"""Multi-trial benchmark for statistically reliable comparison.

Runs 4 variants × N trials each on S5/1200s with:
  * **process isolation** (each ensemble island = its own JVM via _compr_isolated.py)
  * **interleaved randomized order** (block-based: all variants once per block,
    in random within-block order; 5 blocks total) — amortizes any thermal /
    time-of-day drift across variants
  * **60s cooldown** between trials (CPU thermal recovery)
  * **120s cooldown** between blocks
  * each trial starts with FRESH JVMs (kill+restart) so no JIT/GC leakage

Variants tested:
  1. **single-LA**: 1 JVM, /schedules endpoint = pure single Late Acceptance baseline
  2. **classic-iso**: 4 isolated JVMs (LA, SA, TABU, GD), num_intervals=1 (no migration)
  3. **I-iso**: 4 isolated JVMs (LA, DLA, SA, SCHC), 5 intervals × 240s with migration
  4. **I_8-iso**: 4 isolated JVMs (same acceptors), 8 intervals × 150s with migration

Total wall-clock: 20 trials × ~20 min + 16×60s + 4×120s cooldowns ≈ 7 hours.

Each trial writes a row to ``benchmark_results.csv`` with engine name
``timefold_kotlin_iso_<variant>_t<trial>`` (or ``timefold_kotlin_t<trial>``
for single-LA). Aggregate stats (median, IQR) computed at end.
"""
from __future__ import annotations

import csv
import random
import re
import statistics
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

from flight_crew_scheduling.benchmark import CSV_PATH, _kill_port  # noqa: E402

# ---------------------------------------------- config
DATASET_IDX = 4   # S5
TOTAL_S = 1200
TRIALS_PER_VARIANT = 5
COOLDOWN_BETWEEN_TRIALS = 60
COOLDOWN_BETWEEN_BLOCKS = 120
SHUFFLE_SEED = 42

# (label, driver_script, base_args, engine_pattern_for_csv_match)
VARIANTS = [
    ("single-LA",   "_standalone_regular.py", [str(DATASET_IDX), str(TOTAL_S)],
                    "timefold_kotlin_t{trial}"),
    ("classic-iso", "_compr_isolated.py",     ["classic", str(DATASET_IDX), str(TOTAL_S), "1"],
                    "timefold_kotlin_iso_classic_t{trial}"),
    ("I-iso",       "_compr_isolated.py",     ["I", str(DATASET_IDX), str(TOTAL_S), "5"],
                    "timefold_kotlin_iso_I_t{trial}"),
    ("I_8-iso",     "_compr_isolated.py",     ["I_8", str(DATASET_IDX), str(TOTAL_S), "8"],
                    "timefold_kotlin_iso_I_8_t{trial}"),
]


def ts():
    return datetime.now().strftime("%H:%M:%S")


def cleanup_ports():
    for port in (9091, 9092, 9093, 9094):
        _kill_port(port)


def run_one_trial(variant_label, script, base_args, trial_id):
    py = sys.executable
    args = list(base_args) + [str(trial_id)]
    print(f"\n>>> [{ts()}] {variant_label} trial {trial_id} starting "
          f"(args: {script} {' '.join(args)})", flush=True)
    cleanup_ports()
    time.sleep(2)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [py, str(HERE / script)] + args,
            cwd=str(HERE),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            timeout=TOTAL_S * 2 + 600,
        )
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        print(f"<<< [{ts()}] {variant_label} trial {trial_id} TIMEOUT after "
              f"{elapsed:.1f}s", flush=True)
        cleanup_ports()
        return False
    elapsed = time.monotonic() - t0
    # Echo last few lines for visibility
    for line in proc.stdout.splitlines()[-3:]:
        print("    " + line, flush=True)
    print(f"<<< [{ts()}] {variant_label} trial {trial_id} done rc={proc.returncode} "
          f"elapsed={elapsed:.1f}s", flush=True)
    cleanup_ports()
    return proc.returncode == 0


def parse_csv_results():
    """Return dict: variant_label → list of (hard, soft) tuples."""
    if not CSV_PATH.exists():
        return {}
    by_variant = {}
    with CSV_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            engine = row.get("engine", "")
            for label, _, _, pattern in VARIANTS:
                # build a generic pattern
                regex_pattern = pattern.format(trial=r"\d+")
                if re.fullmatch(regex_pattern, engine):
                    try:
                        h = int(row.get("hard") or 0)
                        s = int(row.get("soft") or 0)
                        by_variant.setdefault(label, []).append((h, s))
                    except (ValueError, TypeError):
                        pass
                    break
    return by_variant


def print_aggregate():
    print(f"\n========= [{ts()}] AGGREGATE STATS (so far) =========", flush=True)
    by_variant = parse_csv_results()
    for label, _, _, _ in VARIANTS:
        results = by_variant.get(label, [])
        if not results:
            print(f"  {label:14}: (no trials yet)", flush=True)
            continue
        hards = [r[0] for r in results]
        softs = [r[1] for r in results]
        h_med = statistics.median(hards)
        s_med = statistics.median(softs)
        feasible_count = sum(1 for h in hards if h == 0)
        print(f"  {label:14}: n={len(results)}, feasible={feasible_count}/{len(results)}, "
              f"hard median={h_med}, soft median={s_med}", flush=True)
        if len(results) >= 2:
            print(f"      hard min/max: {min(hards)}/{max(hards)}, "
                  f"soft min/max: {min(softs)}/{max(softs)}", flush=True)


def main():
    random.seed(SHUFFLE_SEED)

    print(f"[{ts()}] === Multi-trial benchmark ===", flush=True)
    print(f"  variants: {[v[0] for v in VARIANTS]}", flush=True)
    print(f"  trials per variant: {TRIALS_PER_VARIANT}", flush=True)
    print(f"  total trials: {TRIALS_PER_VARIANT * len(VARIANTS)}", flush=True)
    print(f"  dataset: idx={DATASET_IDX} (S5)", flush=True)
    print(f"  total budget per trial: {TOTAL_S}s", flush=True)
    print(f"  cooldown between trials: {COOLDOWN_BETWEEN_TRIALS}s", flush=True)
    print(f"  cooldown between blocks: {COOLDOWN_BETWEEN_BLOCKS}s", flush=True)
    estimated_min = (TRIALS_PER_VARIANT * len(VARIANTS) * (TOTAL_S + 30)
                     + (TRIALS_PER_VARIANT * len(VARIANTS) - TRIALS_PER_VARIANT) * COOLDOWN_BETWEEN_TRIALS
                     + (TRIALS_PER_VARIANT - 1) * COOLDOWN_BETWEEN_BLOCKS) / 60
    print(f"  estimated wall-clock: ~{estimated_min:.0f} min", flush=True)

    overall_t0 = time.monotonic()
    block_count = 0

    for trial in range(1, TRIALS_PER_VARIANT + 1):
        block_count += 1
        # Random order within block
        order = list(range(len(VARIANTS)))
        random.shuffle(order)
        order_labels = [VARIANTS[i][0] for i in order]
        print(f"\n========= BLOCK {block_count}/{TRIALS_PER_VARIANT} "
              f"(trial {trial}) order: {order_labels} =========", flush=True)

        for i, idx in enumerate(order):
            label, script, base_args, _ = VARIANTS[idx]
            run_one_trial(label, script, base_args, trial)
            # Cooldown between trials (within block)
            if not (i == len(order) - 1 and trial == TRIALS_PER_VARIANT):
                cooldown = (COOLDOWN_BETWEEN_BLOCKS if i == len(order) - 1
                            else COOLDOWN_BETWEEN_TRIALS)
                print(f"  [{ts()}] cooling down {cooldown}s ...", flush=True)
                time.sleep(cooldown)

        # End-of-block aggregate
        print_aggregate()

    total_elapsed = time.monotonic() - overall_t0
    print(f"\n[{ts()}] === Multi-trial done; total {total_elapsed/60:.1f} min ===",
          flush=True)
    print_aggregate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
