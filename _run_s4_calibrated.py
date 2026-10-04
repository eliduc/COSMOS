"""Confirmatory test: is S4's plateau caused by type-rating SCARCITY?

Takes the pinned S4 instance, gives EVERY employee ALL aircraft-type
ratings (removes the type-rating supply constraint entirely), and re-solves
single-LA. If the type-rating violations vanish and it approaches
feasibility (only flight conflicts left), that proves the blocker was
structural type-rating scarcity — fixable by crew calibration, not by any
search method.

Usage:  python _run_s4_calibrated.py [budget_s] [n_runs]
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx  # noqa: E402
from flight_crew_scheduling.benchmark import (  # noqa: E402
    KOTLIN_SOLVER_JAR, _java_exe, _kill_port, _wait_http_ready,
)
from _run_conflict_test import count_conflicts  # noqa: E402

PORT = 9099
PIN = HERE / "_s4_pinned.json"


def main():
    budget = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    payload = json.loads(PIN.read_text(encoding="utf-8"))
    all_types = sorted({f.get("aircraftType") for f in payload["flights"]
                        if f.get("aircraftType")})
    for e in payload["employees"]:
        e["typeRatings"] = list(all_types)
    print(f"CALIBRATED S4: every crew rated for all types {all_types}", flush=True)
    print(f"  {len(payload['flightAssignments'])} slots, "
          f"{len(payload['employees'])} crew | single-LA {budget}s × {n}\n",
          flush=True)

    _kill_port(PORT)
    time.sleep(1)
    log_f = (HERE / "_calib_jvm.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [_java_exe(), "-Xms512m", "-Xmx4g", "-XX:+UseG1GC",
         f"-Dquarkus.http.port={PORT}",
         "-Dquarkus.timefold.solver.environment-mode=no-assert",
         "-jar", str(KOTLIN_SOLVER_JAR)],
        stdin=subprocess.DEVNULL, stdout=log_f, stderr=subprocess.STDOUT,
        cwd=str(KOTLIN_SOLVER_JAR.parent.parent))
    try:
        if not _wait_http_ready(f"http://127.0.0.1:{PORT}/q/openapi", timeout_s=60):
            raise RuntimeError("JVM not ready")
        with httpx.Client(timeout=budget * 2 + 120) as cli:
            for k in range(1, n + 1):
                r = cli.post(f"http://127.0.0.1:{PORT}/schedules/single",
                             json=payload,
                             params={"acceptor": "LA", "ch": "FF",
                                     "spentLimitSeconds": budget})
                r.raise_for_status()
                solved = r.json()
                sc = solved.get("score")
                nconf = count_conflicts(solved)
                feas = "FEASIBLE ✓" if str(sc).startswith("0hard") else ""
                print(f"  run {k}: {sc}  conflicts={nconf}  {feas}", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_f.close()
        _kill_port(PORT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
