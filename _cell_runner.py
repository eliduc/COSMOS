"""Single-shot benchmark cell runner.

Spawned by `benchmark.py` as a fresh subprocess per cell so each solver
invocation gets a pristine JVM (for JPype-based engines) and a pristine
process (for OR-Tools, even though it's native — keeps the design uniform).

Usage:
    python _cell_runner.py < params.json   # JSON params on stdin
    # JSON result on stdout (single line); diagnostics on stderr

Input JSON shape::

    {
      "engine":            "timefold_python" | "ortools" | "solverforge",
      "seed":              int,
      "count_flights":     int,
      "teams_per_airport": int,
      "time_limit_s":      int,
      "soft_threshold":    int | null
    }

Output JSON shape::

    {
      "score":         "0hard/-Nsoft" | null,
      "hard":          int | null,
      "soft":          int | null,
      "finish_reason": "timeout" | "error",
      "error":         str | null
    }
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Optional


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))


def _parse_score(score_str: Optional[str]):
    if not score_str:
        return None, None
    m = re.match(r"(-?\d+)hard/(-?\d+)soft", str(score_str))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


class _BestTracker:
    def __init__(self):
        self.best_tuple = None
        self.best_score_str = None

    def update_from_str(self, score_str):
        if not score_str:
            return
        h, s = _parse_score(score_str)
        if h is None:
            return
        cand = (h, s)
        if self.best_tuple is None or cand > self.best_tuple:
            self.best_tuple = cand
            self.best_score_str = score_str

    def update_from_schedule(self, schedule):
        try:
            score = schedule.score
        except Exception:
            return
        self.update_from_str(str(score) if score is not None else None)

    def result(self):
        if self.best_tuple is None:
            return None, None, None
        return self.best_score_str, self.best_tuple[0], self.best_tuple[1]


def _run_timefold_python(schedule, time_limit_s, soft_threshold, tracker):
    """In-process Timefold-Python via JPype. Fresh JVM in this subprocess."""
    from flight_crew_scheduling import solver as _sm
    from flight_crew_scheduling.solver import update_settings

    update_settings(time_limit_s=time_limit_s, soft_threshold=soft_threshold)

    job_id = f"cell-tfp-{int(time.time() * 1000)}"
    done = threading.Event()

    def _on_best(s):
        tracker.update_from_schedule(s)

    def _on_final(s):
        tracker.update_from_schedule(s)
        done.set()

    (_sm.solver_manager.solve_builder()
        .with_problem_id(job_id)
        .with_problem(schedule)
        .with_best_solution_consumer(_on_best)
        .with_final_best_solution_consumer(_on_final)
        .run())

    deadline = time.monotonic() + time_limit_s + 30
    while not done.is_set() and time.monotonic() < deadline:
        done.wait(timeout=0.5)


def _run_ortools(schedule, time_limit_s, soft_threshold, tracker):
    from flight_crew_scheduling import ortools_engine
    solved, _ = ortools_engine.solve_with_ortools(
        schedule, time_limit_s,
        on_improvement=tracker.update_from_schedule,
    )
    tracker.update_from_schedule(solved)


def _run_solverforge(schedule, time_limit_s, soft_threshold, tracker):
    from flight_crew_scheduling import solverforge_engine as _sf
    solved, _ = _sf.solve_with_solverforge(
        schedule, time_limit_s,
        soft_threshold=soft_threshold,
        on_improvement=tracker.update_from_schedule,
    )
    tracker.update_from_schedule(solved)


def main():
    raw = sys.stdin.read()
    params = json.loads(raw)

    engine = params["engine"]
    seed = int(params["seed"])
    count_flights = int(params["count_flights"])
    teams_per_airport = int(params["teams_per_airport"])
    time_limit_s = int(params["time_limit_s"])
    soft_threshold = params.get("soft_threshold")
    if soft_threshold is not None:
        soft_threshold = int(soft_threshold)

    print(f"cell-runner: engine={engine} seed={seed} flights={count_flights} "
          f"teams={teams_per_airport} t={time_limit_s}s",
          file=sys.stderr, flush=True)

    tracker = _BestTracker()
    error = None
    finish_reason = "timeout"

    try:
        from flight_crew_scheduling.demo_data import generate_demo_data_scaled
        schedule = generate_demo_data_scaled(
            seed=seed,
            count_flights=count_flights,
            teams_per_airport=teams_per_airport,
        )

        if engine == "timefold_python":
            _run_timefold_python(schedule, time_limit_s, soft_threshold, tracker)
        elif engine == "ortools":
            _run_ortools(schedule, time_limit_s, soft_threshold, tracker)
        elif engine == "solverforge":
            _run_solverforge(schedule, time_limit_s, soft_threshold, tracker)
        else:
            raise ValueError(f"engine {engine} not supported by cell-runner")
    except Exception as exc:  # noqa: BLE001
        finish_reason = "error"
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc(file=sys.stderr)

    score_str, hard, soft = tracker.result()
    out = {
        "score": score_str,
        "hard": hard,
        "soft": soft,
        "finish_reason": finish_reason,
        "error": error,
    }
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
