"""Compare all available solvers on the BTS schedule.

Runs Stage 1 (pairing enumeration + CP-SAT set-partitioning) ONCE, then
for each requested algorithm runs Stage 2 (rostering) on the synthetic
schedule with the same time budget.

For ``single_stage`` algorithms, runs Timefold/OR-Tools directly on the
full BTS schedule without pairing decomposition.

Usage:
    python _run_bts_compare.py <schedule_json> <budget_s> <alg1,alg2,...>

Algorithms:
    ts_classic            — Two-stage, Timefold-Kotlin LA (single acceptor)
    ts_compr_E            — Two-stage, comprehensive E (4 acceptors, 1 JVM)
    ts_compr_HI           — Two-stage, comprehensive HI (hybrid)
    ts_iso_I_8            — Two-stage, I-iso 8 (4 JVMs, 8-interval islands)
    ts_iso_classic        — Two-stage, classic-iso (4 JVMs, 4 acceptors no migration)
    ts_ortools            — Two-stage, OR-Tools CP-SAT on synthetic schedule
    ss_classic            — Single-stage, classic Timefold-Kotlin LA on full schedule
    ss_ortools            — Single-stage, OR-Tools on full schedule (likely OOM)

Output: appended to ``data/bts_compare_results.csv``
"""
from __future__ import annotations

import csv
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx  # noqa: E402

from flight_crew_scheduling import timefold_kotlin_engine as _tf_kotlin  # noqa: E402
from flight_crew_scheduling.benchmark import (  # noqa: E402
    KOTLIN_SOLVER_JAR, _java_exe, _kill_port, _wait_http_ready,
)
from flight_crew_scheduling.pairing_generator import enumerate_legal_pairings  # noqa: E402
from flight_crew_scheduling.pairing_selector import (  # noqa: E402
    select_pairings_set_partitioning,
)
from flight_crew_scheduling.pairing_to_schedule import build_rostering_schedule  # noqa: E402
from _run_bts_two_stage import load_schedule, parse_score, ts  # noqa: E402

CSV_PATH = HERE / "data" / "bts_compare_results.csv"
CSV_FIELDS = [
    "timestamp", "schedule", "stage_mode", "algorithm",
    "budget_s", "elapsed_s", "score", "hard", "soft",
    "status", "first_feasible_at_s",
]


# ---------- engine adapters ------------------------------------------------

def _start_jar(port: int, log_path: Path):
    log_f = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            _java_exe(),
            "-Xms1g", "-Xmx8g",
            "-XX:+UseG1GC",
            f"-Dquarkus.http.port={port}",
            # Surface Jackson/RESTEasy errors that Quarkus normally eats —
            # we hit a 400-empty-body on single-stage and need to see
            # the underlying deserialization exception.
            '-Dquarkus.log.category."org.jboss.resteasy.reactive".level=DEBUG',
            '-Dquarkus.log.category."com.fasterxml.jackson".level=DEBUG',
            '-Dquarkus.log.category."io.quarkus.resteasy".level=DEBUG',
            # 10MB Quarkus default is fine for BTS at 3.4MB but bump
            # so future bigger schedules don't need a rebuild.
            "-Dquarkus.http.limits.max-body-size=64M",
            # Quarkus Timefold default is PHASE_ASSERT (slow debug mode).
            # Forcing REPRODUCIBLE on the JVM command line gives
            # production-grade throughput while keeping deterministic
            # trajectories. Saves rebuild for already-shipped JARs.
            "-Dquarkus.timefold.solver.environment-mode=no-assert",
            "-jar", str(KOTLIN_SOLVER_JAR),
        ],
        stdin=subprocess.DEVNULL,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        cwd=str(KOTLIN_SOLVER_JAR.parent.parent),
    )
    if not _wait_http_ready(f"http://127.0.0.1:{port}/q/openapi", timeout_s=60.0):
        proc.terminate()
        log_f.close()
        raise RuntimeError(f"JVM on port {port} did not become ready in 60s")
    return proc, log_f


def _stop_jar(proc, log_f, port: int):
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass
    _kill_port(port)
    try:
        log_f.close()
    except Exception:
        pass


def _solve_timefold(schedule, budget_s: int, ensemble: Optional[str], log_label: str):
    """Run Timefold-Kotlin on a single port (9091).

    ensemble=None     → classic LA
    ensemble="E"|"HI" → comprehensive in-JVM ensemble
    """
    log_path = HERE / f"_bts_cmp_{log_label}.log"
    _kill_port(9091)
    time.sleep(1)
    proc, log_f = _start_jar(9091, log_path)

    best = {"score": None, "hard": None, "soft": None, "first_feas_at_s": None}
    elapsed = 0.0
    status_text = "unknown"
    try:
        last_print_ts = [time.monotonic()]
        t0 = time.monotonic()

        def on_improvement(s):
            sc = str(s.score) if s.score is not None else None
            if not sc:
                return
            h, soft = parse_score(sc)
            if h is None:
                return
            cur = (best["hard"], best["soft"]) if best["hard"] is not None else None
            cand = (h, soft)
            if cur is None or cand > cur:
                best["hard"] = h
                best["soft"] = soft
                best["score"] = sc
                if h == 0 and best["first_feas_at_s"] is None:
                    best["first_feas_at_s"] = round(time.monotonic() - t0, 1)
            now = time.monotonic()
            if now - last_print_ts[0] >= 60.0:
                print(f"  [+{int(now - t0):5d}s] best={best['score']}", flush=True)
                last_print_ts[0] = now

        last_print_ts[0] = t0
        if ensemble:
            print(f"[{ts()}] firing comprehensive ensemble={ensemble} budget={budget_s}s ...",
                  flush=True)
            solved, status_text = _tf_kotlin.solve_with_timefold_kotlin(
                schedule, budget_s,
                comprehensive=True,
                ensemble=ensemble,
                on_improvement=on_improvement,
            )
        else:
            print(f"[{ts()}] firing classic LA budget={budget_s}s ...", flush=True)
            solved, status_text = _tf_kotlin.solve_with_timefold_kotlin(
                schedule, budget_s,
                on_improvement=on_improvement,
            )
        elapsed = time.monotonic() - t0
        on_improvement(solved)
    finally:
        _stop_jar(proc, log_f, 9091)
    return best, elapsed, status_text


def _solve_iso(schedule, budget_s: int, ensemble_label: str, log_label: str,
               num_intervals: int = 8):
    """Run process-isolated 4-JVM ensemble with interval-based island migration.

    For ``num_intervals`` rounds: solve in parallel on 4 JVMs for
    ``budget_s/num_intervals`` seconds each, pick best of 4, feed it as
    warm-start to all 4 for next round.

    ensemble_label one of: "I", "I_8", "classic", etc. (ENSEMBLES registry).
    """
    from _compr_isolated import (
        IsolatedJvmFleet, ENSEMBLES, ports_for_ensemble, compute_intervals,
    )

    if ensemble_label not in ENSEMBLES:
        raise ValueError(f"Unknown iso ensemble: {ensemble_label}")
    acceptors = ENSEMBLES[ensemble_label]
    ports = ports_for_ensemble(ensemble_label)
    num_intervals, interval_s = compute_intervals(budget_s, num_intervals)

    print(f"[{ts()}] iso fleet boot: ports={ports}, acceptors={acceptors}, "
          f"intervals={num_intervals} × {interval_s}s ...", flush=True)
    schedule_dict = schedule.model_dump(by_alias=True, mode="json")
    schedule_dict.pop("solverStatus", None)

    # `best` tracks the highest-quality result EVER seen across all
    # intervals. `best_schedule_dict` is the schedule that achieved it —
    # we feed it as the warm-start input to every island in every
    # subsequent interval.
    #
    # **Migration-degradation fix:** the previous implementation set
    # ``current = max(this_interval_results)``. If acceptors like SA/TABU
    # in interval k>1 perturbed the input and didn't have time to recover,
    # they returned a worse-than-globalBest result; that worse result then
    # seeded interval k+1 — and the cycle compounded. Symptom on BTS
    # 1200s/8 intervals: globalBest fixed at interval 1's best
    # (-368 030), every later interval drifted down (-589, -823, -983)
    # without ever recovering. Now we always seed from the true global
    # best, matching the Kotlin-side runIslandMigrationGeneric semantics.
    best = {"score": None, "hard": None, "soft": None, "first_feas_at_s": None}
    best_schedule_dict = schedule_dict   # initial: the raw problem
    t0 = time.monotonic()

    def update_best(sc, res_dict):
        nonlocal best_schedule_dict
        if sc is None:
            return
        parsed = parse_score(sc)
        if not parsed:
            return
        h, soft = parsed
        cur = (best["hard"], best["soft"]) if best["hard"] is not None else None
        cand = (h, soft)
        if cur is None or cand > cur:
            best["hard"] = h
            best["soft"] = soft
            best["score"] = sc
            best_schedule_dict = res_dict
            if h == 0 and best["first_feas_at_s"] is None:
                best["first_feas_at_s"] = round(time.monotonic() - t0, 1)

    with IsolatedJvmFleet(ports, KOTLIN_SOLVER_JAR, heap_xmx="6g") as fleet:
        for k in range(1, num_intervals + 1):
            print(f"  [{ts()}] interval {k}/{num_intervals} (best={best['score']}) ...",
                  flush=True)
            # CH+LS chain: FIRST_FIT's default REINITIALIZE_VARIABLE filter
            # leaves already-assigned employees untouched and only fills
            # remaining null entities. So globalBest is preserved across
            # intervals; CH just patches any nulls (legitimate when there's
            # no qualified crew for an assignment).
            results = fleet.solve_parallel(
                best_schedule_dict, acceptors, interval_s,
            )
            scored = []
            for acc, res in zip(acceptors, results):
                sc = res.get("score")
                parsed = parse_score(sc)
                scored.append((acc, sc, parsed, res))
                update_best(sc, res)
            interval_winner = max(scored, key=lambda x: x[2] or (-10**9, -10**9))
            print(f"  [{ts()}] interval {k}/{num_intervals} done; "
                  f"interval_winner={interval_winner[0]} score={interval_winner[1]}; "
                  f"globalBest={best['score']}",
                  flush=True)
    elapsed = time.monotonic() - t0
    return best, elapsed, "TIMEOUT"


def _solve_ortools(schedule, budget_s: int, log_label: str):
    """Run OR-Tools CP-SAT on the schedule directly."""
    from flight_crew_scheduling import ortools_engine
    print(f"[{ts()}] firing OR-Tools CP-SAT budget={budget_s}s ...", flush=True)
    t0 = time.monotonic()
    best = {"score": None, "hard": None, "soft": None, "first_feas_at_s": None}
    try:
        solved, status_text = ortools_engine.solve_with_ortools(
            schedule, budget_s,
        )
        elapsed = time.monotonic() - t0
        sc = str(solved.score) if solved.score is not None else None
        if sc:
            parsed = parse_score(sc)
            if parsed:
                h, soft = parsed
                best["hard"] = h
                best["soft"] = soft
                best["score"] = sc
                if h == 0:
                    best["first_feas_at_s"] = elapsed
    except Exception as e:
        elapsed = time.monotonic() - t0
        return best, elapsed, f"ERROR: {e}"
    return best, elapsed, status_text


# ---------- main loop ------------------------------------------------------

def main():
    if len(sys.argv) < 4:
        print("Usage: python _run_bts_compare.py <schedule_json> <budget_s> "
              "<alg1,alg2,...>", flush=True)
        return 2

    json_path = Path(sys.argv[1]).resolve()
    budget_s = int(sys.argv[2])
    algs = [a.strip() for a in sys.argv[3].split(",") if a.strip()]

    print(f"[{ts()}] === BTS COMPARE ===", flush=True)
    print(f"  schedule:   {json_path.name}", flush=True)
    print(f"  budget:     {budget_s}s per algorithm", flush=True)
    print(f"  algorithms: {algs}", flush=True)

    schedule = load_schedule(json_path)
    n_flights = len(schedule.flights)
    n_crew = len(schedule.employees)
    n_slots = len(schedule.flight_assignments)
    print(f"\n[{ts()}] Source: {n_flights} flights, {n_crew} crew, "
          f"{n_slots} crew-slot assignments", flush=True)

    # Stage 1: enumerate + CP-SAT (only needed for two-stage variants)
    needs_two_stage = any(a.startswith("ts_") for a in algs)
    synthetic = None
    if needs_two_stage:
        print(f"\n[{ts()}] STAGE 1A: enumerate pairings ...", flush=True)
        t1 = time.monotonic()
        pairings = enumerate_legal_pairings(schedule.flights)
        print(f"  {len(pairings)} pairings in {time.monotonic()-t1:.1f}s", flush=True)

        print(f"[{ts()}] STAGE 1B: CP-SAT set-partitioning ...", flush=True)
        t1b = time.monotonic()
        flight_numbers = [f.flight_number for f in schedule.flights]
        # num_workers=1 for a REPRODUCIBLE pairing set. With 8 workers,
        # CP-SAT races and returns whichever of the many equally-optimal
        # set-partitions it hits first — and different pairing sets yield
        # synthetic rosters with very different difficulty floors (we
        # measured the same algorithm land anywhere from -425k to -990k
        # purely from this Stage-1 variance). Pinning to 1 worker makes
        # method comparisons apples-to-apples; the production default
        # stays 8 for speed.
        selected, info = select_pairings_set_partitioning(
            flight_numbers, pairings, time_limit_seconds=120, num_workers=1)
        print(f"  {len(selected)} pairings selected ({info['status']}) "
              f"in {time.monotonic()-t1b:.1f}s", flush=True)
        if not selected:
            print(f"[{ts()}] CP-SAT failed; aborting", flush=True)
            return 1

        synthetic, _ = build_rostering_schedule(
            selected, schedule.employees, schedule.airports,
        real_flights=schedule.flights)
        print(f"  synthetic: {len(synthetic.flights)} pairings, "
              f"{len(synthetic.flight_assignments)} crew-slots", flush=True)

    # CSV header (create file if absent)
    if not CSV_PATH.exists():
        CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writeheader()

    results = []
    for i, alg in enumerate(algs):
        print(f"\n========= [{ts()}] [{i+1}/{len(algs)}] {alg} =========",
              flush=True)
        t0 = time.monotonic()

        try:
            if alg == "ts_classic":
                best, elapsed, status = _solve_timefold(synthetic, budget_s, None, alg)
                stage_mode = "two_stage"
            elif alg == "ts_compr_E":
                best, elapsed, status = _solve_timefold(synthetic, budget_s, "E", alg)
                stage_mode = "two_stage"
            elif alg == "ts_compr_HI":
                best, elapsed, status = _solve_timefold(synthetic, budget_s, "HI", alg)
                stage_mode = "two_stage"
            elif alg.startswith("ts_iso_"):
                ens = alg[len("ts_iso_"):]
                best, elapsed, status = _solve_iso(synthetic, budget_s, ens, alg)
                stage_mode = "two_stage"
            elif alg == "ts_ortools":
                best, elapsed, status = _solve_ortools(synthetic, budget_s, alg)
                stage_mode = "two_stage"
            elif alg == "ss_classic":
                best, elapsed, status = _solve_timefold(schedule, budget_s, None, alg)
                stage_mode = "single_stage"
            elif alg == "ss_compr_E":
                best, elapsed, status = _solve_timefold(schedule, budget_s, "E", alg)
                stage_mode = "single_stage"
            elif alg == "ss_ortools":
                best, elapsed, status = _solve_ortools(schedule, budget_s, alg)
                stage_mode = "single_stage"
            else:
                print(f"  unknown algorithm: {alg}, skipping", flush=True)
                continue
        except Exception as e:
            elapsed = time.monotonic() - t0
            best = {"score": None, "hard": None, "soft": None, "first_feas_at_s": None}
            status = f"ERROR: {e}"
            stage_mode = "two_stage" if alg.startswith("ts_") else "single_stage"

        row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "schedule": json_path.stem,
            "stage_mode": stage_mode,
            "algorithm": alg,
            "budget_s": budget_s,
            "elapsed_s": round(elapsed, 1),
            "score": best.get("score"),
            "hard": best.get("hard"),
            "soft": best.get("soft"),
            "status": status,
            "first_feasible_at_s": best.get("first_feas_at_s"),
        }
        results.append(row)
        with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=CSV_FIELDS).writerow(row)
        print(f"  [{ts()}] {alg} DONE: {row['score']} "
              f"(first feas @ {row['first_feasible_at_s']}s) elapsed={elapsed:.1f}s",
              flush=True)

        # Cooldown between algorithms (let GC, JIT, OS settle)
        if i < len(algs) - 1:
            cool = 30
            print(f"  [{ts()}] cooling {cool}s ...", flush=True)
            time.sleep(cool)

    print(f"\n[{ts()}] === SUMMARY ===", flush=True)
    print(f"{'algorithm':<18} {'mode':<14} {'score':<22} "
          f"{'first_feas':>10} {'elapsed':>8}", flush=True)
    for r in results:
        print(f"{r['algorithm']:<18} {r['stage_mode']:<14} {str(r['score']):<22} "
              f"{str(r.get('first_feasible_at_s')):>10} {r['elapsed_s']:>8}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
