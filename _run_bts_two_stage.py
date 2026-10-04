"""Run the two-stage pipeline on a BTS-derived FlightCrewSchedule.

Loads a schedule that was prepared by ``_bts_to_schedule.py`` and feeds
it through:
  Stage 1A: enumerate legal pairings
  Stage 1B: CP-SAT set-partitioning
  Stage 2:  Timefold-Kotlin rostering on the synthetic flight schedule

Usage:
    python _run_bts_two_stage.py <schedule_json> <stage2_timeout_s> [trial_id]

Example:
    python _run_bts_two_stage.py data/bts_schedules/DL_ATL_2024-08-15.json 600 1
"""
from __future__ import annotations

import json
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
    KOTLIN_SOLVER_JAR, _java_exe, _kill_port, _wait_http_ready,
)
from flight_crew_scheduling.domain import FlightCrewSchedule  # noqa: E402
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


def load_schedule(json_path: Path) -> FlightCrewSchedule:
    """Load a FlightCrewSchedule from JSON.

    Foreign-key fields (Airport on Flight, Flight on FlightAssignment, ...)
    are stored as plain ID strings in the JSON. Pydantic's ``BeforeValidator``
    looks them up in ``info.context``. We do a two-pass load:

      Pass 1: parse airports / employees / flights (these have no FK refs
      that need other lists yet — ``departure_airport`` resolves Airport
      from the airports we just parsed).

      Pass 2: parse flight_assignments with full context.
    """
    from flight_crew_scheduling.domain import (
        Airport, Employee, Flight, FlightAssignment,
    )

    with json_path.open(encoding="utf-8") as f:
        payload = json.load(f)

    airports = [Airport.model_validate(a) for a in payload["airports"]]
    airports_by_code = {a.code: a for a in airports}

    employees = [
        Employee.model_validate(e, context={"airports": airports_by_code})
        for e in payload["employees"]
    ]
    employees_by_id = {e.id: e for e in employees}

    flights = [
        Flight.model_validate(f, context={"airports": airports_by_code})
        for f in payload["flights"]
    ]
    flights_by_number = {f.flight_number: f for f in flights}

    flight_assignments = [
        FlightAssignment.model_validate(
            fa,
            context={
                "airports": airports_by_code,
                "employees": employees_by_id,
                "flights": flights_by_number,
            },
        )
        for fa in payload["flightAssignments"]
    ]

    return FlightCrewSchedule(
        airports=airports,
        employees=employees,
        flights=flights,
        flight_assignments=flight_assignments,
    )


def main():
    if len(sys.argv) < 3:
        print("Usage: python _run_bts_two_stage.py <schedule_json> "
              "<stage2_timeout_s> [trial_id]", flush=True)
        return 2

    json_path = Path(sys.argv[1]).resolve()
    stage2_timeout_s = int(sys.argv[2])
    trial_id = sys.argv[3] if len(sys.argv) > 3 else "1"

    print(f"[{ts()}] === BTS TWO-STAGE PIPELINE ===", flush=True)
    print(f"  schedule:        {json_path.name}", flush=True)
    print(f"  stage 1 timeout: 60s (CP-SAT)", flush=True)
    print(f"  stage 2 timeout: {stage2_timeout_s}s (Timefold)", flush=True)
    print(f"  trial:           {trial_id}", flush=True)

    started_at = datetime.now()
    schedule = load_schedule(json_path)
    n_flights = len(schedule.flights)
    n_crew = len(schedule.employees)
    n_slots = len(schedule.flight_assignments) if schedule.flight_assignments else 0
    n_airports = len(schedule.airports)
    print(f"\n[{ts()}] Source: {n_flights} flights, {n_crew} crew, "
          f"{n_slots} crew-slot assignments, {n_airports} airports", flush=True)

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
    # num_workers=1 → reproducible pairing set (see _run_bts_compare.py
    # note: 8-worker CP-SAT picks a random equally-optimal set, which
    # swings the Stage-2 difficulty floor and confounds comparisons).
    selected, info = select_pairings_set_partitioning(
        flight_numbers, pairings, time_limit_seconds=120, num_workers=1)
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
    log_path = HERE / f"_bts_kotlin_{json_path.stem}_t{trial_id}.log"
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
    elapsed = 0.0
    status_text = "unknown"
    expanded = []
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
              f"(vs {n_slots} slots in source)", flush=True)
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

    print(f"\n[{ts()}] === BTS TWO-STAGE PIPELINE DONE ===", flush=True)
    print(f"  Stage 1A enumeration: {stage1a_elapsed:.2f}s ({len(pairings)} pairings)", flush=True)
    print(f"  Stage 1B CP-SAT:      {stage1b_elapsed:.2f}s ({len(selected)} selected)", flush=True)
    print(f"  Stage 2 Timefold:     {elapsed:.1f}s ({best['score']})", flush=True)
    print(f"  Total wall-clock:     {total_elapsed:.1f}s", flush=True)
    print(f"  Output assignments:   {len(expanded)}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
