"""A/B test for flight-conflict gradient smoothing (#5) on a PINNED S4.

S4's feasibility plateau is 100% flight conflicts. This runs FF+LA N times
on a FIXED S4 instance (saved to _s4_pinned.json so the flat-penalty build
and the proportional-penalty build see identical data) and reports the
final hard score of each run. Compare the two builds' distributions.

Usage:
    python _run_conflict_test.py [budget_s] [n_runs]
    defaults: 120  4
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

from datetime import datetime  # noqa: E402
from collections import defaultdict  # noqa: E402

import httpx  # noqa: E402
from flight_crew_scheduling.benchmark import (  # noqa: E402
    KOTLIN_SOLVER_JAR, _java_exe, _kill_port, _wait_http_ready,
)
from flight_crew_scheduling.demo_data import (  # noqa: E402
    BENCHMARK_DATASETS, generate_demo_data_scaled,
)

PORT = 9099
PIN = HERE / "_s4_pinned.json"


def count_conflicts(solved):
    """Number of overlapping same-employee assignment pairs (weight-independent)."""
    emps = {e["id"]: e for e in solved["employees"]}
    flights = {f["flightNumber"]: f for f in solved["flights"]}

    def eid(a):
        v = a.get("employee")
        return (v.get("id") if isinstance(v, dict) else v) if v is not None else None

    def fl(a):
        v = a.get("flight")
        return flights.get(v["flightNumber"]) if isinstance(v, dict) else flights.get(v)

    by_emp = defaultdict(list)
    for a in solved["flightAssignments"]:
        e = eid(a)
        f = fl(a)
        if e is None or not f:
            continue
        dep = datetime.fromisoformat(f["departureUTCDateTime"])
        arr = datetime.fromisoformat(f["arrivalUTCDateTime"])
        by_emp[e].append((dep, arr))
    n = 0
    for lst in by_emp.values():
        lst.sort()
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                if lst[j][0] >= lst[i][1]:
                    break
                n += 1
    return n


def main():
    budget = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    inst = Path(sys.argv[3]) if len(sys.argv) > 3 else PIN

    if inst.exists():
        payload = json.loads(inst.read_text(encoding="utf-8"))
        print(f"loaded instance {inst.name}", flush=True)
    else:
        ds = BENCHMARK_DATASETS[3]
        schedule = generate_demo_data_scaled(
            seed=ds["seed"], count_flights=ds["count_flights"],
            teams_per_airport=ds["teams_per_airport"])
        payload = schedule.model_dump(by_alias=True, mode="json")
        payload.pop("solverStatus", None)
        PIN.write_text(json.dumps(payload), encoding="utf-8")
        print(f"generated + pinned S4 to {PIN.name}", flush=True)

    print(f"S4: {len(payload['flightAssignments'])} slots, "
          f"{len(payload['employees'])} crew | FF+LA {budget}s × {n} runs\n",
          flush=True)

    _kill_port(PORT)
    time.sleep(1)
    log_f = (HERE / "_conflict_jvm.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [_java_exe(), "-Xms512m", "-Xmx4g", "-XX:+UseG1GC",
         f"-Dquarkus.http.port={PORT}",
         "-Dquarkus.timefold.solver.environment-mode=no-assert",
         "-jar", str(KOTLIN_SOLVER_JAR)],
        stdin=subprocess.DEVNULL, stdout=log_f, stderr=subprocess.STDOUT,
        cwd=str(KOTLIN_SOLVER_JAR.parent.parent))
    finals = []
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
                finals.append((sc, nconf))
                print(f"  run {k}: {sc}  | flight-conflicts={nconf}", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_f.close()
        _kill_port(PORT)

    confs = [c for _, c in finals]
    feas = sum(1 for c in confs if c == 0)
    if confs:
        print(f"\nfeasible (0 conflicts): {feas}/{len(confs)} | "
              f"conflicts best={min(confs)} worst={max(confs)} "
              f"mean={sum(confs)/len(confs):.1f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
