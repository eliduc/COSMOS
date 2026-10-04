"""Quick 60s comprehensive solve on S5. Compare LA-island finish to the
60s standalone TF-Kotlin baseline (-600hard/-326800soft).

If the cloned-template fix worked, the LA island should land near -600 hard
at 60s, not stuck at -780+ like the previous comprehensive run.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
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

    log_path = HERE / "_diag_compr_60s.log"
    log_f = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [
            _java_exe(),
            "-Xms2g", "-Xmx8g",
            "-XX:+UseG1GC",
            "-XX:ParallelGCThreads=4",
            "-XX:ConcGCThreads=2",
            "-XX:MaxGCPauseMillis=50",
            "-jar", str(KOTLIN_SOLVER_JAR),
        ],
        stdin=subprocess.DEVNULL,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        cwd=str(KOTLIN_SOLVER_JAR.parent.parent),
    )

    try:
        if not _wait_http_ready("http://127.0.0.1:9091/q/openapi", timeout_s=45.0):
            return 1

        schedule = generate_demo_data_scaled(seed=5, count_flights=56, teams_per_airport=3)
        t0 = [time.monotonic()]

        def on_islands_update(d):
            elapsed = int(time.monotonic() - t0[0])
            print(f"\n[+{elapsed:4d}s] per-island state:", flush=True)
            for acc in ("LA", "SA", "TABU", "GD"):
                print(f"    {acc:5} {d.get(acc, '—'):>30}", flush=True)

        t0[0] = time.monotonic()
        print(f"[diag] firing comprehensive (N=100%) S5 60s ...", flush=True)
        solved, status = _tf_kotlin.solve_with_timefold_kotlin(
            schedule, TIMEOUT_S,
            comprehensive=True,
            phase_a_fraction=1.0,
            on_islands_update=on_islands_update,
            islands_poll_interval_s=15.0,  # poll every 15s for the smoke run
        )
        elapsed = time.monotonic() - t0[0]
        print(f"\n[diag] done in {elapsed:.1f}s; global best = {solved.score}", flush=True)
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
        print(f"[diag] log: {log_path}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
