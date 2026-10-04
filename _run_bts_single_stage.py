"""Run single-stage Timefold (no pairing decomposition) on a BTS schedule.

For comparison vs. ``_run_bts_two_stage.py``. Feeds the realistic
schedule directly to Timefold-Kotlin without first selecting pairings.

Usage:
    python _run_bts_single_stage.py <schedule_json> <timeout_s> [trial_id]
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
from _run_bts_two_stage import load_schedule, parse_score, ts  # noqa: E402


def main():
    if len(sys.argv) < 3:
        print("Usage: python _run_bts_single_stage.py <schedule_json> "
              "<timeout_s> [trial_id]", flush=True)
        return 2

    json_path = Path(sys.argv[1]).resolve()
    timeout_s = int(sys.argv[2])
    trial_id = sys.argv[3] if len(sys.argv) > 3 else "1"

    print(f"[{ts()}] === BTS SINGLE-STAGE PIPELINE ===", flush=True)
    print(f"  schedule: {json_path.name}", flush=True)
    print(f"  timeout:  {timeout_s}s (Timefold)", flush=True)
    print(f"  trial:    {trial_id}", flush=True)

    started_at = datetime.now()
    schedule = load_schedule(json_path)
    n_flights = len(schedule.flights)
    n_crew = len(schedule.employees)
    n_slots = len(schedule.flight_assignments)
    print(f"\n[{ts()}] Source: {n_flights} flights, {n_crew} crew, "
          f"{n_slots} crew-slot assignments", flush=True)

    print(f"\n[{ts()}] starting Kotlin JAR + solving ...", flush=True)
    _kill_port(9091)
    time.sleep(1)
    log_path = HERE / f"_bts_single_kotlin_{json_path.stem}_t{trial_id}.log"
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
        print(f"[{ts()}] firing Timefold solve, timeout={timeout_s}s ...", flush=True)
        solved, status_text = _tf_kotlin.solve_with_timefold_kotlin(
            schedule, timeout_s,
            on_improvement=on_improvement,
        )
        elapsed = time.monotonic() - t0
        on_improvement(solved)
        print(f"\n[{ts()}] DONE: {best['score']} after {elapsed:.1f}s "
              f"status={status_text}", flush=True)
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
    print(f"\n[{ts()}] === BTS SINGLE-STAGE DONE ===", flush=True)
    print(f"  Best score: {best['score']}", flush=True)
    print(f"  Solve time: {elapsed:.1f}s", flush=True)
    print(f"  Total wall: {total_elapsed:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
