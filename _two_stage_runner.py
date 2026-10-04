"""End-to-end two-stage crew scheduling pipeline.

Stage 1 (CP-SAT, OR-Tools):
  * Enumerate all legal pairings from the flight schedule
  * Set-partitioning IP: pick minimum-cost subset covering all flights

Stage 2 (Timefold-Kotlin via REST):
  * Build a synthetic FlightCrewSchedule where flights = selected pairings
  * Solve via /schedules endpoint
  * Get back crew → pairing assignments
  * Expand to crew → individual-flight assignments

Usage:
    python _two_stage_runner.py <dataset_idx> <stage2_timeout_s> [trial_id]

Output:
  * Stage 1 stats (pairings count, CP-SAT obj, time)
  * Stage 2 score (HardSoft, from Timefold)
  * One CSV row appended with engine="two_stage_t<trial>"
"""
from __future__ import annotations

import csv
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

from flight_crew_scheduling import timefold_kotlin_engine as _tf_kotlin  # noqa: E402
from flight_crew_scheduling.benchmark import (  # noqa: E402
    CSV_FIELDS, CSV_PATH, KOTLIN_SOLVER_JAR, _java_exe, _kill_port, _wait_http_ready,
)
from flight_crew_scheduling.demo_data import (  # noqa: E402
    BENCHMARK_DATASETS, generate_demo_data_scaled,
)
from flight_crew_scheduling.pairing_generator import (  # noqa: E402
    enumerate_legal_pairings, summarize_pairings,
)
from flight_crew_scheduling.pairing_selector import (  # noqa: E402
    select_pairings_set_partitioning,
)
from flight_crew_scheduling.pairing_to_schedule import (  # noqa: E402
    build_rostering_schedule, expand_pairing_assignments_to_flight_assignments,
)


def ts():
    return datetime.now().strftime("%H:%M:%S")


def parse_score(score_str):
    if not score_str:
        return None, None
    m = re.match(r"(-?\d+)hard/(-?\d+)soft", str(score_str))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def next_run_index() -> int:
    if not CSV_PATH.exists():
        return 0
    max_idx = -1
    with CSV_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                max_idx = max(max_idx, int(row.get("run_index", "-1")))
            except (ValueError, TypeError):
                pass
    return max_idx + 1


def main():
    if len(sys.argv) < 3:
        print("Usage: python _two_stage_runner.py <dataset_idx> "
              "<stage2_timeout_s> [trial_id]", flush=True)
        return 2

    dataset_idx = int(sys.argv[1])
    stage2_timeout_s = int(sys.argv[2])
    trial_id = sys.argv[3] if len(sys.argv) > 3 else "1"
    dataset = BENCHMARK_DATASETS[dataset_idx]

    print(f"[{ts()}] === TWO-STAGE PIPELINE ===", flush=True)
    print(f"  dataset:         {dataset['label']} (idx={dataset_idx}, "
          f"flights={dataset['count_flights']})", flush=True)
    print(f"  stage 1 timeout: 60s (CP-SAT)", flush=True)
    print(f"  stage 2 timeout: {stage2_timeout_s}s (Timefold)", flush=True)
    print(f"  trial:           {trial_id}", flush=True)

    # --- Generate problem ---------------------------------------------------
    started_at = datetime.now()
    schedule = generate_demo_data_scaled(
        seed=dataset["seed"],
        count_flights=dataset["count_flights"],
        teams_per_airport=dataset["teams_per_airport"],
    )
    n_flights = len(schedule.flights)
    n_crew = len(schedule.employees)
    n_slots = len(schedule.flight_assignments) if schedule.flight_assignments else 0
    print(f"\n[{ts()}] Source: {n_flights} flights, {n_crew} crew, "
          f"{n_slots} crew-slot assignments", flush=True)

    # --- Stage 1A: enumerate pairings ---------------------------------------
    print(f"\n[{ts()}] STAGE 1A: enumerate legal pairings ...", flush=True)
    t1 = time.monotonic()
    pairings = enumerate_legal_pairings(schedule.flights)
    stage1a_elapsed = time.monotonic() - t1
    summary = summarize_pairings(pairings)
    print(f"  {len(pairings)} pairings enumerated in {stage1a_elapsed:.2f}s", flush=True)
    print(f"  legs distribution: {summary.get('by_legs')}", flush=True)
    print(f"  duty range: {summary.get('min_duty_min')}–{summary.get('max_duty_min')} min", flush=True)

    # --- Stage 1B: CP-SAT set-partitioning ----------------------------------
    print(f"\n[{ts()}] STAGE 1B: CP-SAT set-partitioning ...", flush=True)
    flight_numbers = [f.flight_number for f in schedule.flights]
    t1b = time.monotonic()
    selected, info = select_pairings_set_partitioning(
        flight_numbers, pairings, time_limit_seconds=60, num_workers=8)
    stage1b_elapsed = time.monotonic() - t1b
    print(f"  {len(selected)} pairings selected, status={info['status']}, "
          f"obj={info['objective']}, wall={info['wall_time_s']}s", flush=True)
    print(f"  legs in selection: {info.get('legs_distribution')}", flush=True)

    if not selected:
        print(f"\n[{ts()}] CP-SAT failed; aborting", flush=True)
        return 1

    # --- Stage 2: build rostering schedule and solve via Timefold-Kotlin ----
    print(f"\n[{ts()}] STAGE 2 prep: build synthetic schedule ...", flush=True)
    roster_schedule, pairing_by_flight_number = build_rostering_schedule(
        selected, schedule.employees, schedule.airports,
        real_flights=schedule.flights)
    print(f"  synthetic schedule: {len(roster_schedule.flights)} pairing-flights, "
          f"{len(roster_schedule.flight_assignments)} crew-slot assignments", flush=True)

    print(f"\n[{ts()}] STAGE 2: starting Kotlin JAR + solving ...", flush=True)
    _kill_port(9091)
    time.sleep(1)
    log_path = HERE / f"_two_stage_kotlin_{dataset['label'].split()[0]}_t{trial_id}.log"
    log_f = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            _java_exe(),
            "-Xms512m", "-Xmx2g",
            "-XX:+UseG1GC",
            "-jar", str(KOTLIN_SOLVER_JAR),
        ],
        stdin=subprocess.DEVNULL,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        cwd=str(KOTLIN_SOLVER_JAR.parent.parent),
    )

    best = {"score": None, "hard": None, "soft": None}
    try:
        if not _wait_http_ready("http://127.0.0.1:9091/q/openapi", timeout_s=45.0):
            print(f"[{ts()}] ERROR: Kotlin not ready", flush=True)
            return 1

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
            now = time.monotonic()
            if now - last_print_ts[0] >= 60.0:
                print(f"  [+{int(now - t0):5d}s] best={best['score']}", flush=True)
                last_print_ts[0] = now

        last_print_ts[0] = t0
        print(f"[{ts()}] firing Timefold solve, timeout={stage2_timeout_s}s ...", flush=True)
        solved, status_text = _tf_kotlin.solve_with_timefold_kotlin(
            roster_schedule, stage2_timeout_s,
            on_improvement=on_improvement,
        )
        elapsed = time.monotonic() - t0
        on_improvement(solved)
        print(f"\n[{ts()}] STAGE 2 done: {best['score']} after {elapsed:.1f}s "
              f"status={status_text}", flush=True)

        # Expand pairing assignments to real-flight assignments (output)
        expanded = expand_pairing_assignments_to_flight_assignments(
            solved, pairing_by_flight_number, schedule.flights)
        print(f"  expanded to {len(expanded)} crew-flight assignments "
              f"(vs {n_slots} slots in single-stage source)", flush=True)
    finally:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass
        _kill_port(9091)
        try:
            log_f.close()
        except Exception:
            pass

    finished_at = datetime.now()
    total_elapsed = (finished_at - started_at).total_seconds()

    # --- Append CSV row -----------------------------------------------------
    engine_name = f"two_stage_t{trial_id}"
    row = {
        "run_index":          next_run_index(),
        "timeout_s":          stage2_timeout_s,
        "dataset_idx":        dataset_idx,
        "dataset_label":      dataset["label"],
        "count_flights":      dataset["count_flights"],
        "teams_per_airport":  dataset["teams_per_airport"],
        "engine":             engine_name,
        "started_at":         started_at.isoformat(timespec="seconds"),
        "finished_at":        finished_at.isoformat(timespec="seconds"),
        "elapsed_s":          round(total_elapsed, 1),
        "score":              best["score"],
        "hard":               best["hard"],
        "soft":               best["soft"],
        "finish_reason":      "timeout",
        "error":              "",
    }
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writerow(row)
    print(f"\n[{ts()}] CSV row {row['run_index']} appended (engine={engine_name})", flush=True)
    print(f"\n[{ts()}] === TWO-STAGE PIPELINE DONE ===", flush=True)
    print(f"  Stage 1A enumeration: {stage1a_elapsed:.2f}s ({len(pairings)} pairings)", flush=True)
    print(f"  Stage 1B CP-SAT:      {stage1b_elapsed:.2f}s ({len(selected)} selected)", flush=True)
    print(f"  Stage 2 Timefold:     {elapsed:.1f}s ({best['score']})", flush=True)
    print(f"  Total wall-clock:     {total_elapsed:.1f}s", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
