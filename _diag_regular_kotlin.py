"""Diagnostic: 60-second standalone solve via the REGULAR /schedules endpoint
on the new JAR. Captures move evaluation speed to compare with comprehensive
LA-island throughput.

Goal: prove or disprove that comprehensive's LA island under-performs
standalone TF-Kotlin LA. Also captures the environment-mode line so we can
see whether the regular endpoint runs the same env-mode as comprehensive.
"""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

from flight_crew_scheduling import timefold_kotlin_engine as _tf_kotlin  # noqa: E402
from flight_crew_scheduling.benchmark import (  # noqa: E402
    KOTLIN_SOLVER_JAR, _java_exe, _kill_port, _wait_http_ready,
)
from flight_crew_scheduling.demo_data import generate_demo_data_scaled  # noqa: E402

TIMEOUT_S = 60


def main():
    print("[diag] starting fresh JVM ...", flush=True)
    _kill_port(9091)
    time.sleep(1)

    log_path = HERE / "_diag_regular_kotlin.log"
    log_f = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [_java_exe(), "-Xms256m", "-Xmx2g", "-jar", str(KOTLIN_SOLVER_JAR)],
        stdin=subprocess.DEVNULL,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        cwd=str(KOTLIN_SOLVER_JAR.parent.parent),
    )

    try:
        if not _wait_http_ready("http://127.0.0.1:9091/q/openapi", timeout_s=45.0):
            print("[diag] ERROR: ready timeout", flush=True)
            return 1

        # Use S5 to match the comprehensive run — same difficulty
        schedule = generate_demo_data_scaled(seed=5, count_flights=56, teams_per_airport=3)

        t0 = time.monotonic()
        print(f"[diag] firing REGULAR (single LA) solve, time_limit_s={TIMEOUT_S} ...", flush=True)
        solved, status = _tf_kotlin.solve_with_timefold_kotlin(
            schedule, TIMEOUT_S,
            comprehensive=False,
        )
        elapsed = time.monotonic() - t0
        print(f"[diag] done in {elapsed:.1f}s status={status}", flush=True)
        print(f"[diag] FINAL score = {solved.score}", flush=True)
        return 0
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
        print(f"[diag] JVM stopped. Log: {log_path}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
