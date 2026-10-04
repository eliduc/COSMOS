"""Smoke-test for the new /schedules/comprehensive endpoint on the
Kotlin solver. Spawns a fresh prod-JAR JVM, runs N=100% comprehensive
solve on dataset S1 for `time_limit_s` seconds, prints the best score
and which acceptors were active, then kills the JVM.

Usage:
    python _smoke_comprehensive.py [time_limit_s]
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

from flight_crew_scheduling import timefold_kotlin_engine as _tf_kotlin  # noqa: E402
from flight_crew_scheduling.demo_data import generate_demo_data_scaled  # noqa: E402
from flight_crew_scheduling.benchmark import (  # noqa: E402
    KOTLIN_SOLVER_JAR, _java_exe, _kill_port, _wait_http_ready,
)


def main():
    time_limit_s = int(sys.argv[1]) if len(sys.argv) > 1 else 30

    print(f"[smoke] starting Kotlin prod-JAR ({KOTLIN_SOLVER_JAR.name}) ...", flush=True)
    _kill_port(9091)
    time.sleep(1)

    log_path = HERE / "_smoke_kotlin.log"
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
            print("[smoke] ERROR: Kotlin JAR did not become ready in 45s")
            return 1

        print("[smoke] Kotlin ready on :9091; building S1 (tiny) dataset ...", flush=True)
        # S1 from BENCHMARK_DATASETS: tiny, ~14 flights, fast to solve.
        schedule = generate_demo_data_scaled(seed=1, count_flights=14, teams_per_airport=2)

        improvements = []

        def _on_improvement(s):
            improvements.append((time.monotonic(), str(s.score)))
            print(f"  [+{improvements[-1][0] - t0:6.1f}s] best={improvements[-1][1]}",
                  flush=True)

        t0 = time.monotonic()
        print(f"[smoke] firing comprehensive solve, time_limit_s={time_limit_s} ...",
              flush=True)
        solved, status = _tf_kotlin.solve_with_timefold_kotlin(
            schedule, time_limit_s,
            on_improvement=_on_improvement,
            comprehensive=True,
            phase_a_fraction=1.0,
        )
        elapsed = time.monotonic() - t0
        print(f"\n[smoke] done in {elapsed:.1f}s status={status}")
        print(f"[smoke] FINAL score = {solved.score}")
        print(f"[smoke] improvements observed: {len(improvements)}")
        return 0
    finally:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:  # noqa: BLE001
            pass
        _kill_port(9091)
        try:
            log_f.close()
        except Exception:  # noqa: BLE001
            pass
        print("[smoke] Kotlin JVM stopped.  log:", log_path)


if __name__ == "__main__":
    sys.exit(main())
