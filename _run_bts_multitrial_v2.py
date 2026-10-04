"""CLEAN multi-trial v2 on the pinned BTS day — fixes the two harness flaws
found in the session audit.

Flaw 1 (contamination): the old harness passed the SAME Pydantic schedule
object into every trial, and solve_with_timefold_kotlin mutates it in place —
so trials 2..5 were warm-started from the previous trial's solution (smoking
gun: first_feasible=2.0s on a 6824-slot cold problem, and monotonically
improving soft across "independent" trials). v2 works with raw JSON dicts
only, deep-copies the payload per run, and never touches the mutating
engine. Every run boots fresh JVM(s).

Flaw 2 (seed monoculture): every solver inherited random-seed=0, so repeated
trials replayed one deterministic trajectory (measuring timer jitter, not
solver variance) and ensemble islands were undiversified. v2 gives every
trial, island and interval its own randomSeed via the new endpoint param.

Methods (all through POST /schedules/single — identical solve path):
  all_at_once         — LA on the FULL schedule, 1 JVM, full budget
  trips_no_exchange   — LA on the pinned synthetic (pairing) schedule
  trips_with_exchange — 4 islands (LA/DLA/SA/SCHC), 8 intervals, best-ever
                        coordinator, per-island+per-interval seeds

Design: Stage 1 once (num_workers=1 → pinned pairing set); block-randomized
method order per trial; strictly sequential runs; cooldowns. first_feasible
is NOT tracked — the synchronous endpoint has no polling, and the audit
showed it was incomparable between method types anyway.

NOTE on reading results: per-slot values are comparable WITHIN a method
across trials (variance!); across single-stage vs two-stage the per-slot
normalization carries the known caveat (a pairing-slot ≈ 2.25 real flights).

Usage:
    python _run_bts_multitrial_v2.py [budget_s] [n_trials]
    defaults: 1200 5
"""
from __future__ import annotations

import copy
import csv
import json
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

from flight_crew_scheduling.benchmark import KOTLIN_SOLVER_JAR  # noqa: E402
from flight_crew_scheduling.pairing_generator import enumerate_legal_pairings  # noqa: E402
from flight_crew_scheduling.pairing_selector import (  # noqa: E402
    select_pairings_set_partitioning,
)
from flight_crew_scheduling.pairing_to_schedule import build_rostering_schedule  # noqa: E402
from _run_bts_two_stage import load_schedule, ts  # noqa: E402
from _compr_isolated import IsolatedJvmFleet, parse_score  # noqa: E402

SCHEDULE_JSON = HERE / "data" / "bts_schedules" / "DL_ATL_2024-08-15.json"
CSV_PATH = HERE / "data" / "bts_multitrial2_results.csv"
CSV_FIELDS = [
    "run_id", "timestamp", "method", "trial", "seed", "budget_s", "elapsed_s",
    "score", "hard", "soft", "n_slots", "soft_per_slot", "status",
]
# run_id (launch timestamp) distinguishes rows of separate launches sharing
# one CSV — so an aborted attempt can't silently mix with a clean re-run
# (audit finding). Exchange rows record the BASE seed; per-request seeds are
# base + 100*interval_k + island_idx.

ACCEPTORS = ["LA", "DLA", "SA", "SCHC"]
SHUFFLE_SEED = 42
COOLDOWN_METHOD_S = 30
COOLDOWN_BLOCK_S = 60


def run_single_cold(payload: dict, seed: int, budget: int) -> str | None:
    """One cold LA solve on a fresh JVM. Deep-copied payload, explicit seed."""
    with IsolatedJvmFleet([9091], KOTLIN_SOLVER_JAR, heap_xmx="8g") as fleet:
        res = fleet.solve_parallel(
            copy.deepcopy(payload), ["LA"], budget, seeds=[seed])
    return res[0].get("score") if res and res[0] else None


def run_exchange_cold(payload: dict, base_seed: int, budget: int) -> str | None:
    """Cold 4-island ensemble with migration; best-ever coordinator.

    Seeds vary per island AND per interval (base + 100*k + island_idx), so
    intervals restarted from the global best explore NEW trajectories
    instead of replaying the previous interval (the old harness re-used
    seed 0 everywhere, making intervals 2+ largely wasted compute).
    """
    n_int = max(1, min(8, budget // 150))
    interval_s = budget // n_int
    best_p = None          # (hard, soft) tuple for comparisons
    best_dict = None       # full schedule dict of the best-ever solution
    with IsolatedJvmFleet([9091, 9092, 9093, 9094], KOTLIN_SOLVER_JAR,
                          heap_xmx="6g") as fleet:
        current = copy.deepcopy(payload)
        for k in range(1, n_int + 1):
            seeds = [base_seed + 100 * k + i for i in range(len(ACCEPTORS))]
            try:
                results = fleet.solve_parallel(current, ACCEPTORS, interval_s,
                                               seeds=seeds)
            except Exception as e:  # noqa: BLE001
                # One flaky island late in a 20-min run must not discard the
                # best-ever accumulated so far (audit finding). Log, stop
                # migrating, return best-so-far.
                print(f"      interval {k}/{n_int} FAILED "
                      f"({str(e)[:120]}); returning best-so-far", flush=True)
                break
            for r in results:
                if not r:
                    continue
                p = parse_score(r.get("score"))
                if p and (best_p is None or p > best_p):
                    best_p, best_dict = p, r
            print(f"      interval {k}/{n_int}: best-ever="
                  f"{best_p[0]}hard/{best_p[1]}soft" if best_p else
                  f"      interval {k}/{n_int}: no parseable result",
                  flush=True)
            if best_dict is not None:
                current = best_dict   # warm-start next interval from best-ever
    return (f"{best_p[0]}hard/{best_p[1]}soft") if best_p else None


def main():
    budget = int(sys.argv[1]) if len(sys.argv) > 1 else 1200
    n_trials = int(sys.argv[2]) if len(sys.argv) > 2 else 5

    # Quarkus's HTTP idle timeout defaults to 30 min; a synchronous solve
    # longer than that gets its connection dropped mid-run (audit finding).
    if budget > 1500:
        print(f"budget {budget}s exceeds the safe synchronous-request limit "
              f"(Quarkus idle timeout 30 min). Use <=1500s or raise "
              f"quarkus.http.idle-timeout.", flush=True)
        return 2

    run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
    print(f"[{ts()}] === BTS MULTI-TRIAL v2 (clean: cold starts + seeds) ===",
          flush=True)
    print(f"  run_id:  {run_id}", flush=True)
    print(f"  budget:  {budget}s per run", flush=True)
    print(f"  trials:  {n_trials} × 3 methods = {n_trials*3} runs", flush=True)
    est_h = (n_trials * 3 * (budget + 45 + COOLDOWN_METHOD_S)) / 3600
    print(f"  est. wall-clock: ~{est_h:.1f} h", flush=True)

    schedule = load_schedule(SCHEDULE_JSON)
    print(f"\n[{ts()}] Source: {len(schedule.flights)} flights, "
          f"{len(schedule.employees)} crew, "
          f"{len(schedule.flight_assignments)} crew-slots", flush=True)

    # ---- Stage 1 ONCE, pinned (num_workers=1) ------------------------------
    print(f"[{ts()}] STAGE 1A: enumerate pairings ...", flush=True)
    pairings = enumerate_legal_pairings(schedule.flights)
    print(f"  {len(pairings)} pairings", flush=True)
    print(f"[{ts()}] STAGE 1B: CP-SAT set-partitioning (num_workers=1) ...",
          flush=True)
    flight_numbers = [f.flight_number for f in schedule.flights]
    selected, info = select_pairings_set_partitioning(
        flight_numbers, pairings, time_limit_seconds=120, num_workers=1)
    print(f"  {len(selected)} pairings selected ({info['status']})", flush=True)
    if not selected:
        print("CP-SAT failed; aborting", flush=True)
        return 1
    synthetic, _ = build_rostering_schedule(
        selected, schedule.employees, schedule.airports,
        real_flights=schedule.flights)

    # ---- freeze both problems as raw dicts (no Pydantic beyond this point) --
    payload_full = schedule.model_dump(by_alias=True, mode="json")
    payload_full.pop("solverStatus", None)
    payload_synth = synthetic.model_dump(by_alias=True, mode="json")
    payload_synth.pop("solverStatus", None)
    n_full = len(payload_full["flightAssignments"])
    n_synth = len(payload_synth["flightAssignments"])
    print(f"  PINNED: full={n_full} slots, synthetic={n_synth} slots "
          f"({len(payload_synth['flights'])} pairings)\n", flush=True)

    # method -> (runner(trial) -> (score, seed_used), n_slots)
    def m_all(t):
        seed = 10 * t + 1
        return run_single_cold(payload_full, seed, budget), seed

    def m_trips(t):
        seed = 10 * t + 2
        return run_single_cold(payload_synth, seed, budget), seed

    def m_exch(t):
        seed = 1000 * t
        return run_exchange_cold(payload_synth, seed, budget), seed

    METHODS = {
        "all_at_once": (m_all, n_full),
        "trips_no_exchange": (m_trips, n_synth),
        "trips_with_exchange": (m_exch, n_synth),
    }

    if not CSV_PATH.exists():
        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()

    rng = random.Random(SHUFFLE_SEED)
    rows = []
    for trial in range(1, n_trials + 1):
        order = list(METHODS.keys())
        rng.shuffle(order)
        print(f"\n========== [{ts()}] BLOCK {trial}/{n_trials} "
              f"order={order} ==========", flush=True)
        for i, method in enumerate(order):
            runner, n_slots = METHODS[method]
            print(f"\n----- [{ts()}] {method} trial {trial} (cold) -----",
                  flush=True)
            t0 = time.monotonic()
            try:
                score, seed = runner(trial)
                status = "OK"
            except Exception as e:  # noqa: BLE001
                score, seed = None, None
                status = f"ERROR: {str(e)[:150]}"
            elapsed = time.monotonic() - t0
            p = parse_score(score) if score else None
            hard, soft = (p if p else (None, None))
            sps = round(soft / n_slots, 2) if soft is not None else None
            row = {
                "run_id": run_id,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "method": method, "trial": trial, "seed": seed,
                "budget_s": budget, "elapsed_s": round(elapsed, 1),
                "score": score, "hard": hard, "soft": soft,
                "n_slots": n_slots, "soft_per_slot": sps, "status": status,
            }
            rows.append(row)
            with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)
            print(f"  [{ts()}] {method} t{trial} seed={seed}: {score} "
                  f"({sps}/slot) elapsed={elapsed:.0f}s [{status}]", flush=True)
            if not (trial == n_trials and i == len(order) - 1):
                time.sleep(COOLDOWN_METHOD_S)
        if trial < n_trials:
            print(f"[{ts()}] block cooldown {COOLDOWN_BLOCK_S}s ...", flush=True)
            time.sleep(COOLDOWN_BLOCK_S)

    # ---- aggregate ----------------------------------------------------------
    print(f"\n\n========== [{ts()}] AGGREGATE (cold, seeded, n={n_trials}) "
          f"==========", flush=True)
    print(f"{'method':<22} {'n_feas':>6} {'median/slot':>12} {'best/slot':>10} "
          f"{'worst/slot':>11} {'range':>8} {'stdev':>8}", flush=True)
    for method in METHODS:
        vals = [r["soft_per_slot"] for r in rows
                if r["method"] == method and r["hard"] == 0
                and r["soft_per_slot"] is not None]
        if not vals:
            print(f"{method:<22} {'0':>6}  (no feasible trials)", flush=True)
            continue
        med = statistics.median(vals)
        sd = statistics.stdev(vals) if len(vals) >= 2 else 0.0
        print(f"{method:<22} {len(vals):>6} {med:>12.1f} {max(vals):>10.1f} "
              f"{min(vals):>11.1f} {max(vals)-min(vals):>8.1f} {sd:>8.1f}",
              flush=True)
    print(f"\n[{ts()}] CSV: {CSV_PATH}", flush=True)
    print(f"[{ts()}] DONE.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
