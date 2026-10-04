"""Multi-trial variance study of three methods on a PINNED BTS day.

Why this exists: even with the synthetic schedule pinned (num_workers=1),
the solver result still varies run-to-run because termination is by wall
clock — the same deterministic-trajectory solver does a different number
of steps per run and freezes at a different point. On the Atlanta day we
saw the SAME schedule yield -623k (SA won) on one run and -991k (LA won)
on another. So a single run can't rank methods; we need repeats.

Design (mirrors the validated S5 multi-trial methodology, RESULTS §5):
  * Stage 1 runs ONCE — deterministic pairing set, reused by every trial.
  * Three methods compared:
      all_at_once        — single planner on the full roster (single-stage)
      trips_no_exchange  — single planner on the trip-bundled problem
      trips_with_exchange— 4-planner ensemble with draft exchange (I_8)
  * Block-randomized order: each block runs all three methods once in a
    shuffled order; N_TRIALS blocks. Amortizes any thermal/time-of-day
    drift across methods.
  * Sequential only — NO concurrent runs, so cross-run memory/cache
    contention can't inflate the variance we're trying to measure.
  * 30s cooldown between methods, 60s between blocks.

Each run appends a tagged row to data/bts_multitrial_results.csv.
Aggregate stats (median / min / max / range / stdev, per FA-slot) print
at the end.

Usage:
    python _run_bts_multitrial.py [budget_s] [n_trials]
    (defaults: 1200 5)
"""
from __future__ import annotations

import csv
import random
import statistics
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

from flight_crew_scheduling.pairing_generator import enumerate_legal_pairings  # noqa: E402
from flight_crew_scheduling.pairing_selector import select_pairings_set_partitioning  # noqa: E402
from flight_crew_scheduling.pairing_to_schedule import build_rostering_schedule  # noqa: E402
from _run_bts_two_stage import load_schedule, ts  # noqa: E402
from _run_bts_compare import _solve_timefold, _solve_iso  # noqa: E402

SCHEDULE_JSON = HERE / "data" / "bts_schedules" / "DL_ATL_2024-08-15.json"
CSV_PATH = HERE / "data" / "bts_multitrial_results.csv"
CSV_FIELDS = [
    "timestamp", "method", "trial", "budget_s", "elapsed_s",
    "score", "hard", "soft", "n_slots", "soft_per_slot",
    "first_feasible_at_s", "status",
]

SHUFFLE_SEED = 42
COOLDOWN_METHOD_S = 30
COOLDOWN_BLOCK_S = 60


def main():
    budget_s = int(sys.argv[1]) if len(sys.argv) > 1 else 1200
    n_trials = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    print(f"[{ts()}] === BTS MULTI-TRIAL VARIANCE STUDY ===", flush=True)
    print(f"  schedule:  {SCHEDULE_JSON.name}", flush=True)
    print(f"  budget:    {budget_s}s per run", flush=True)
    print(f"  trials:    {n_trials} per method (3 methods → {n_trials*3} runs)", flush=True)
    est_min = n_trials * 3 * (budget_s + COOLDOWN_METHOD_S) / 60
    print(f"  est. wall-clock: ~{est_min:.0f} min", flush=True)

    schedule = load_schedule(SCHEDULE_JSON)
    print(f"\n[{ts()}] Source: {len(schedule.flights)} flights, "
          f"{len(schedule.employees)} crew, "
          f"{len(schedule.flight_assignments)} crew-slots", flush=True)

    # --- Stage 1 ONCE — pinned (num_workers=1 → reproducible) ---------------
    print(f"\n[{ts()}] STAGE 1A: enumerate pairings ...", flush=True)
    t1 = time.monotonic()
    pairings = enumerate_legal_pairings(schedule.flights)
    print(f"  {len(pairings)} pairings in {time.monotonic()-t1:.1f}s", flush=True)

    print(f"[{ts()}] STAGE 1B: CP-SAT set-partitioning (num_workers=1, pinned) ...",
          flush=True)
    flight_numbers = [f.flight_number for f in schedule.flights]
    t1b = time.monotonic()
    selected, info = select_pairings_set_partitioning(
        flight_numbers, pairings, time_limit_seconds=120, num_workers=1)
    print(f"  {len(selected)} pairings selected ({info['status']}) "
          f"in {time.monotonic()-t1b:.1f}s", flush=True)
    if not selected:
        print("CP-SAT failed; aborting", flush=True)
        return 1

    synthetic, _ = build_rostering_schedule(
        selected, schedule.employees, schedule.airports,
        real_flights=schedule.flights)
    print(f"  synthetic (PINNED): {len(synthetic.flights)} pairings, "
          f"{len(synthetic.flight_assignments)} crew-slots", flush=True)

    full_slots = len(schedule.flight_assignments)
    synth_slots = len(synthetic.flight_assignments)

    # --- method registry ----------------------------------------------------
    # Each entry: (key, runner(label) -> (best, elapsed, status), n_slots)
    def run_all_at_once(label):
        return _solve_timefold(schedule, budget_s, None, label)

    def run_trips_no_exchange(label):
        return _solve_timefold(synthetic, budget_s, None, label)

    def run_trips_with_exchange(label):
        return _solve_iso(synthetic, budget_s, "I_8", label, num_intervals=8)

    METHODS = {
        "all_at_once":         (run_all_at_once,        full_slots),
        "trips_no_exchange":   (run_trips_no_exchange,  synth_slots),
        "trips_with_exchange": (run_trips_with_exchange, synth_slots),
    }

    # CSV header
    if not CSV_PATH.exists():
        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()

    rng = random.Random(SHUFFLE_SEED)
    results = []  # list of dict rows

    for trial in range(1, n_trials + 1):
        order = list(METHODS.keys())
        rng.shuffle(order)
        print(f"\n========== [{ts()}] BLOCK {trial}/{n_trials} "
              f"order={order} ==========", flush=True)

        for i, method in enumerate(order):
            runner, n_slots = METHODS[method]
            label = f"mt_{method}_t{trial}"
            print(f"\n----- [{ts()}] {method} trial {trial} -----", flush=True)
            try:
                best, elapsed, status = runner(label)
            except Exception as e:  # noqa: BLE001
                best = {"score": None, "hard": None, "soft": None,
                        "first_feas_at_s": None}
                elapsed = 0.0
                status = f"ERROR: {e}"

            soft = best.get("soft")
            sps = round(soft / n_slots, 2) if soft is not None else None
            row = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "method": method,
                "trial": trial,
                "budget_s": budget_s,
                "elapsed_s": round(elapsed, 1),
                "score": best.get("score"),
                "hard": best.get("hard"),
                "soft": soft,
                "n_slots": n_slots,
                "soft_per_slot": sps,
                "first_feasible_at_s": best.get("first_feas_at_s"),
                "status": status,
            }
            results.append(row)
            with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)
            print(f"  [{ts()}] {method} t{trial}: {row['score']} "
                  f"({sps}/slot) first_feas={row['first_feasible_at_s']}s "
                  f"elapsed={elapsed:.0f}s", flush=True)

            if not (trial == n_trials and i == len(order) - 1):
                print(f"  [{ts()}] cooldown {COOLDOWN_METHOD_S}s ...", flush=True)
                time.sleep(COOLDOWN_METHOD_S)

        if trial < n_trials:
            print(f"\n[{ts()}] block cooldown {COOLDOWN_BLOCK_S}s ...", flush=True)
            time.sleep(COOLDOWN_BLOCK_S)

    # --- aggregate ----------------------------------------------------------
    print(f"\n\n========== [{ts()}] AGGREGATE (n={n_trials}) ==========", flush=True)
    print(f"{'method':<22} {'n_feas':>6} {'median/slot':>12} "
          f"{'best/slot':>10} {'worst/slot':>11} {'range':>8} {'stdev':>8}",
          flush=True)
    for method in METHODS:
        rows = [r for r in results if r["method"] == method
                and r["soft_per_slot"] is not None and r["hard"] == 0]
        if not rows:
            print(f"{method:<22} {'0':>6}  (no feasible trials)", flush=True)
            continue
        sps_vals = [r["soft_per_slot"] for r in rows]
        med = statistics.median(sps_vals)
        best = max(sps_vals)   # least negative = best
        worst = min(sps_vals)
        rng_v = best - worst
        sd = statistics.stdev(sps_vals) if len(sps_vals) >= 2 else 0.0
        print(f"{method:<22} {len(rows):>6} {med:>12.1f} {best:>10.1f} "
              f"{worst:>11.1f} {rng_v:>8.1f} {sd:>8.1f}", flush=True)

    print(f"\n[{ts()}] CSV: {CSV_PATH}", flush=True)
    print(f"[{ts()}] DONE.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
