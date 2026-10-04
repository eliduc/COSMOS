"""Parallel multi-trial runner for one ensemble × one dataset.

Spawns several trials of the same ensemble concurrently — each trial gets
its own port-base so the isolated JVMs don't collide. Useful on the
new 16-physical-core / 128 GB box where the prior sequential n=5 took
~7 hours; with 3 trials concurrent we cut to ~2.5 hours.

Sizing rule of thumb:
  - 16 physical cores / 32 logical
  - Each Timefold-Kotlin solver is single-threaded, so an island ≈ 1 core
  - max_concurrent × islands ≤ 16 (leave 16 logical for OS / GC slack)
  - For I_6wide on 16-core: 2 trials concurrent (12 islands), or 3 (18 — slight oversubscription)
  - For 4-island ensembles: 4 trials concurrent (16 islands) is fine

Usage:
    python _run_multi_trial_parallel.py <ensemble> <dataset_idx> \\
        <total_s> <num_intervals> <n_trials> <max_concurrent>

Examples:
    # 5 trials of I_6wide on S5/1200s, 3 concurrent (18 islands at peak)
    python _run_multi_trial_parallel.py I_6wide 4 1200 8 5 3

    # 5 trials of classic on S5/1200s, 4 concurrent (16 islands at peak)
    python _run_multi_trial_parallel.py classic 4 1200 1 5 4

Each trial appends its row to ``benchmark_results.csv`` with engine name
``timefold_kotlin_iso_<ensemble>_t<trial>``.
"""
from __future__ import annotations

import re
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from _compr_isolated import ENSEMBLES, ports_for_ensemble  # noqa: E402

DRIVER = HERE / "_compr_isolated.py"

# Port spacing between concurrent trials. Each trial uses ports
# [port_base, port_base + n_islands). With spacing 10 we leave 4 spare
# slots between trials so a renegade JVM doesn't accidentally answer
# the next trial's request.
PORT_SPACING = 10


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def run_one_trial(ensemble: str, dataset_idx: int, total_s: int,
                  num_intervals: int, trial_id: int, port_base: int) -> dict:
    """Run a single trial via _compr_isolated.py subprocess.

    Returns a dict with keys: trial, port_base, ok, elapsed, last_lines.
    """
    py = sys.executable
    args = [py, str(DRIVER), ensemble, str(dataset_idx), str(total_s),
            str(num_intervals), str(trial_id), str(port_base)]
    label = f"trial {trial_id} (port_base={port_base})"
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            args, cwd=str(HERE),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            timeout=total_s * 2 + 600,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        return {"trial": trial_id, "port_base": port_base, "ok": False,
                "elapsed": elapsed, "last_lines": ["TIMEOUT"]}
    elapsed = time.monotonic() - t0
    last_lines = proc.stdout.splitlines()[-5:] if proc.stdout else []
    return {"trial": trial_id, "port_base": port_base,
            "ok": proc.returncode == 0, "elapsed": elapsed,
            "last_lines": last_lines, "stdout": proc.stdout}


def parse_score_from_stdout(stdout: str):
    """Look for 'FINAL: <score>' line in trial stdout."""
    if not stdout:
        return None, None
    m = re.search(r"FINAL:\s*(-?\d+)hard/(-?\d+)soft", stdout)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def main():
    if len(sys.argv) < 7:
        print("Usage: python _run_multi_trial_parallel.py <ensemble> "
              "<dataset_idx> <total_s> <num_intervals> <n_trials> "
              "<max_concurrent>", flush=True)
        return 2

    ensemble = sys.argv[1]
    dataset_idx = int(sys.argv[2])
    total_s = int(sys.argv[3])
    num_intervals = int(sys.argv[4])
    n_trials = int(sys.argv[5])
    max_concurrent = int(sys.argv[6])

    if ensemble not in ENSEMBLES:
        print(f"unknown ensemble {ensemble}; valid: {list(ENSEMBLES)}",
              flush=True)
        return 2

    n_islands = len(ENSEMBLES[ensemble])
    print(f"[{ts()}] === Parallel multi-trial: {ensemble} ===", flush=True)
    print(f"  dataset_idx:    {dataset_idx}", flush=True)
    print(f"  total_s:        {total_s}", flush=True)
    print(f"  num_intervals:  {num_intervals} (effective)", flush=True)
    print(f"  n_trials:       {n_trials}", flush=True)
    print(f"  max_concurrent: {max_concurrent}", flush=True)
    print(f"  n_islands:      {n_islands} per trial "
          f"(peak parallelism: {max_concurrent * n_islands} JVMs)", flush=True)

    overall_t0 = time.monotonic()
    results: list[dict] = []

    # Run in waves of max_concurrent. Each trial in a wave gets a distinct
    # port_base so concurrent JVMs don't collide.
    pending_trials = list(range(1, n_trials + 1))
    while pending_trials:
        wave = pending_trials[:max_concurrent]
        pending_trials = pending_trials[max_concurrent:]
        print(f"\n[{ts()}] === wave: trials {wave} (concurrent) ===",
              flush=True)
        with ThreadPoolExecutor(max_workers=len(wave)) as pool:
            futures = {}
            for slot_idx, trial_id in enumerate(wave):
                port_base = 9091 + slot_idx * PORT_SPACING
                print(f"  spawning trial {trial_id} on port_base={port_base} "
                      f"(ports {port_base}..{port_base + n_islands - 1})",
                      flush=True)
                futures[pool.submit(
                    run_one_trial, ensemble, dataset_idx, total_s,
                    num_intervals, trial_id, port_base)] = trial_id
            for fut in as_completed(futures):
                trial_id = futures[fut]
                try:
                    res = fut.result()
                except Exception as e:
                    res = {"trial": trial_id, "ok": False,
                           "last_lines": [f"EXCEPTION: {e}"], "elapsed": 0.0}
                results.append(res)
                hard, soft = parse_score_from_stdout(res.get("stdout", ""))
                score_str = f"{hard}h/{soft}soft" if hard is not None else "?"
                print(f"  [{ts()}] trial {trial_id} done "
                      f"ok={res['ok']} elapsed={res['elapsed']:.1f}s "
                      f"score={score_str}", flush=True)
        # Cooldown between waves (let GC, OS settle)
        if pending_trials:
            print(f"\n[{ts()}] cooldown 60s before next wave ...", flush=True)
            time.sleep(60)

    overall_elapsed = time.monotonic() - overall_t0
    print(f"\n[{ts()}] === DONE in {overall_elapsed:.1f}s "
          f"({overall_elapsed/60:.1f} min) ===", flush=True)

    # Aggregate
    softs, hards = [], []
    for res in sorted(results, key=lambda r: r["trial"]):
        hard, soft = parse_score_from_stdout(res.get("stdout", ""))
        if hard is not None:
            hards.append(hard)
            softs.append(soft)
            print(f"  trial {res['trial']:>2}: {hard}h/{soft}soft "
                  f"({res['elapsed']:.0f}s)", flush=True)
        else:
            print(f"  trial {res['trial']:>2}: FAILED ({res['elapsed']:.0f}s) "
                  f"last={res['last_lines'][-1] if res['last_lines'] else '?'}",
                  flush=True)

    if softs:
        print(f"\n  n trials feasible: {sum(1 for h in hards if h == 0)}/{len(hards)}",
              flush=True)
        print(f"  soft median: {statistics.median(softs)}", flush=True)
        print(f"  soft min/max: {min(softs)}/{max(softs)}", flush=True)
        print(f"  soft range:   {max(softs) - min(softs)}", flush=True)
        if len(softs) >= 2:
            print(f"  soft stdev:   {statistics.stdev(softs):.1f}", flush=True)

    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
