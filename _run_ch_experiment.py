"""Construction-heuristic experiment: does the CH lever pierce S4's
feasibility plateau?

Isolates ONE variable. Single solver, single acceptor (LA), same dataset,
same budget — vary ONLY the construction heuristic via /schedules/single?ch=.
For each CH we record:
  * post-CH hard score   (from the JVM log "Construction Heuristic phase ...
    ended: best score (...)") — how good the STARTING roster is
  * final hard/soft score (the endpoint return) — where LS ends up

FF (First Fit, our current default) vs FFD (First Fit Decreasing, hardest
slots first) is the headline comparison; WFD and CI are extra angles.

Usage:
    python _run_ch_experiment.py [dataset_idx] [budget_s] [ch1,ch2,...]
    defaults: 3 (S4)  300  FF,FFD,WFD,CI
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx  # noqa: E402

from flight_crew_scheduling.benchmark import (  # noqa: E402
    KOTLIN_SOLVER_JAR, _java_exe, _kill_port, _wait_http_ready,
)
from flight_crew_scheduling.demo_data import (  # noqa: E402
    BENCHMARK_DATASETS, generate_demo_data_scaled,
)

PORT = 9099


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def parse_score(s):
    m = re.match(r"(-?\d+)hard/(-?\d+)soft", str(s))
    return (int(m.group(1)), int(m.group(2))) if m else (None, None)


def post_ch_hard_from_log(log_path: Path):
    """Last 'Construction Heuristic phase ... ended: best score (Xhard/Ysoft)'."""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    matches = re.findall(
        r"Construction Heuristic phase.*?ended.*?best score \((-?\d+)hard/(-?\d+)soft\)",
        text)
    if not matches:
        return None
    h, s = matches[-1]
    return int(h), int(s)


def main():
    dataset_idx = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    budget_s = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    chs = (sys.argv[3].split(",") if len(sys.argv) > 3
           else ["FF", "FFD", "WFD", "CI"])

    ds = BENCHMARK_DATASETS[dataset_idx]
    print(f"[{ts()}] === CH EXPERIMENT ===", flush=True)
    print(f"  dataset: idx={dataset_idx} ({ds['label']}, "
          f"{ds['count_flights']} flights)", flush=True)
    print(f"  budget:  {budget_s}s per CH", flush=True)
    print(f"  CHs:     {chs}", flush=True)

    schedule = generate_demo_data_scaled(
        seed=ds["seed"], count_flights=ds["count_flights"],
        teams_per_airport=ds["teams_per_airport"])
    payload = schedule.model_dump(by_alias=True, mode="json")
    payload.pop("solverStatus", None)
    print(f"  entities: {len(schedule.flight_assignments)} crew-slots, "
          f"{len(schedule.employees)} crew", flush=True)

    results = []
    for ch in chs:
        log_path = HERE / f"_ch_jvm_{ch}.log"
        _kill_port(PORT)
        time.sleep(1)
        log_f = log_path.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            [_java_exe(), "-Xms512m", "-Xmx4g", "-XX:+UseG1GC",
             f"-Dquarkus.http.port={PORT}",
             "-Dquarkus.timefold.solver.environment-mode=no-assert",
             "-jar", str(KOTLIN_SOLVER_JAR)],
            stdin=subprocess.DEVNULL, stdout=log_f, stderr=subprocess.STDOUT,
            cwd=str(KOTLIN_SOLVER_JAR.parent.parent))
        try:
            if not _wait_http_ready(f"http://127.0.0.1:{PORT}/q/openapi", timeout_s=60):
                raise RuntimeError("JVM not ready")
            print(f"\n[{ts()}] --- ch={ch} solving {budget_s}s ---", flush=True)
            t0 = time.monotonic()
            try:
                with httpx.Client(timeout=budget_s * 2 + 120) as cli:
                    r = cli.post(
                        f"http://127.0.0.1:{PORT}/schedules/single",
                        json=payload,
                        params={"acceptor": "LA", "ch": ch,
                                "spentLimitSeconds": budget_s})
                    r.raise_for_status()
                    solved = r.json()
                elapsed = time.monotonic() - t0
                fh, fs = parse_score(solved.get("score"))
            except Exception as e:  # noqa: BLE001
                elapsed = time.monotonic() - t0
                fh, fs = None, None
                print(f"  [{ts()}] ch={ch} FAILED: {str(e)[:120]}", flush=True)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            log_f.close()
            _kill_port(PORT)

        post_ch = post_ch_hard_from_log(log_path)
        post_ch_str = (f"{post_ch[0]}hard/{post_ch[1]}soft"
                       if post_ch else "?")
        results.append((ch, post_ch, (fh, fs), elapsed))
        print(f"  [{ts()}] ch={ch}: post-CH={post_ch_str} -> "
              f"final={fh}hard/{fs}soft ({elapsed:.0f}s)", flush=True)

    print(f"\n[{ts()}] === SUMMARY (dataset {ds['label']}, {budget_s}s) ===",
          flush=True)
    print(f"{'CH':<5} {'post-CH hard':>13} {'final hard':>11} {'final soft':>12} {'feasible?':>10}",
          flush=True)
    for ch, post_ch, (fh, fs), _ in results:
        pch = post_ch[0] if post_ch else None
        feas = "YES" if fh == 0 else "no"
        print(f"{ch:<5} {str(pch):>13} {str(fh):>11} {str(fs):>12} {feas:>10}",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
