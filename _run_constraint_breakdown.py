"""Diagnose WHICH hard constraints dominate a feasibility plateau.

Timefold's SolutionManager.analyze() is an Enterprise-only feature, so we
compute the per-constraint hard breakdown in PYTHON from the solved
schedule the Kotlin endpoint returns. Re-implements the 9 hard constraints
from FlightCrewSchedulingConstraintProvider.kt (weights kept in sync).

Usage:
    python _run_constraint_breakdown.py [dataset_idx] [budget_s]
    defaults: 3 (S4)  120
"""
from __future__ import annotations

import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
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
from flight_crew_scheduling.demo_data import (  # noqa: E402
    BENCHMARK_DATASETS, generate_demo_data_scaled,
)

PORT = 9099
# Weights mirror FlightCrewSchedulingConstraintProvider.kt
MIN_REST_MIN = 10 * 60
MAX_DAILY_DUTY_MIN = 14 * 60
MAX_MONTHLY_MIN = 100 * 60
MAX_WEEKLY_MIN = 60 * 60


def _dt(s):
    return datetime.fromisoformat(s) if s else None


def _ref(v):
    """A reference field may be an id string or an embedded dict."""
    if isinstance(v, dict):
        return v
    return v  # id string


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    ds = BENCHMARK_DATASETS[idx]
    print(f"=== constraint breakdown: {ds['label']} ({ds['count_flights']} flights), "
          f"FF+LA {budget}s ===", flush=True)

    pin = HERE / "_s4_pinned.json"
    if idx == 3 and pin.exists():
        import json as _json
        payload = _json.loads(pin.read_text(encoding="utf-8"))
        print(f"  (loaded pinned {pin.name})", flush=True)
    else:
        schedule = generate_demo_data_scaled(
            seed=ds["seed"], count_flights=ds["count_flights"],
            teams_per_airport=ds["teams_per_airport"])
        payload = schedule.model_dump(by_alias=True, mode="json")
        payload.pop("solverStatus", None)

    _kill_port(PORT)
    time.sleep(1)
    log_f = (HERE / "_breakdown_jvm.log").open("w", encoding="utf-8")
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
            print(f"solving FF+LA {budget}s ...", flush=True)
            r = cli.post(f"http://127.0.0.1:{PORT}/schedules/single",
                         json=payload,
                         params={"acceptor": "LA", "ch": "FF",
                                 "spentLimitSeconds": budget})
            r.raise_for_status()
            solved = r.json()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_f.close()
        _kill_port(PORT)

    print(f"solved score: {solved.get('score')}\n", flush=True)

    # --- resolve references --------------------------------------------------
    emps = {e["id"]: e for e in solved["employees"]}
    flights = {f["flightNumber"]: f for f in solved["flights"]}

    def emp_of(a):
        v = a.get("employee")
        if v is None:
            return None
        return emps.get(v["id"]) if isinstance(v, dict) else emps.get(v)

    def flight_of(a):
        v = a.get("flight")
        return flights.get(v["flightNumber"]) if isinstance(v, dict) else flights.get(v)

    assigns = []
    for a in solved["flightAssignments"]:
        e = emp_of(a)
        f = flight_of(a)
        assigns.append({
            "id": a["id"], "skill": a.get("requiredSkill", ""),
            "emp": e, "flight": f,
            "dep": _dt(f.get("departureUTCDateTime")) if f else None,
            "arr": _dt(f.get("arrivalUTCDateTime")) if f else None,
        })

    # --- compute hard violations --------------------------------------------
    res = defaultdict(lambda: {"count": 0, "hard": 0})

    def dur(a):
        return int((a["arr"] - a["dep"]).total_seconds() // 60) if a["dep"] and a["arr"] else 0

    for a in assigns:
        e = a["emp"]
        if e is None:
            continue
        f = a["flight"]
        # required skill
        if a["skill"] not in (e.get("skills") or []):
            res["Required skill (100)"]["count"] += 1
            res["Required skill (100)"]["hard"] += 100
        # type rating
        at = f.get("aircraftType") if f else None
        if at and at not in (e.get("typeRatings") or []):
            res["Type rating (50)"]["count"] += 1
            res["Type rating (50)"]["hard"] += 50
        # language
        reqlangs = set((f.get("departureAirport") or {}).get("languages", []) if isinstance(f.get("departureAirport"), dict) else [])
        reqlangs |= set((f.get("arrivalAirport") or {}).get("languages", []) if isinstance(f.get("arrivalAirport"), dict) else [])
        if reqlangs and not (reqlangs & set(e.get("languages") or [])):
            res["Language (2)"]["count"] += 1
            res["Language (2)"]["hard"] += 2

    # flight conflict: same emp, overlapping
    by_emp = defaultdict(list)
    for a in assigns:
        if a["emp"] and a["dep"] and a["arr"]:
            by_emp[a["emp"]["id"]].append(a)
    for eid, lst in by_emp.items():
        lst.sort(key=lambda x: x["dep"])
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                if lst[j]["dep"] >= lst[i]["arr"]:
                    break
                res["Flight conflict (10/pair)"]["count"] += 1
                res["Flight conflict (10/pair)"]["hard"] += 10
        # min rest between consecutive
        for i in range(len(lst) - 1):
            gap = int((lst[i + 1]["dep"] - lst[i]["arr"]).total_seconds() // 60)
            if 0 <= gap < MIN_REST_MIN:
                short = max(1, MIN_REST_MIN - gap)
                res["Min rest (5x shortfall)"]["count"] += 1
                res["Min rest (5x shortfall)"]["hard"] += 5 * short
        # max daily duty
        byday = defaultdict(list)
        for a in lst:
            byday[a["dep"].date()].append(a)
        for d, day in byday.items():
            if len(day) >= 2:
                span = int((max(x["arr"] for x in day) - min(x["dep"] for x in day)).total_seconds() // 60)
                if span > MAX_DAILY_DUTY_MIN:
                    res["Max daily duty (5x excess)"]["count"] += 1
                    res["Max daily duty (5x excess)"]["hard"] += 5 * (span - MAX_DAILY_DUTY_MIN)
        # monthly / weekly hours
        bymonth = defaultdict(int)
        byweek = defaultdict(int)
        for a in lst:
            bymonth[(a["dep"].year, a["dep"].month)] += dur(a)
            iso = a["dep"].isocalendar()
            byweek[(iso[0], iso[1])] += dur(a)
        for k, tot in bymonth.items():
            if tot > MAX_MONTHLY_MIN:
                res["Max monthly hours (3x excess)"]["count"] += 1
                res["Max monthly hours (3x excess)"]["hard"] += 3 * (tot - MAX_MONTHLY_MIN)
        for k, tot in byweek.items():
            if tot > MAX_WEEKLY_MIN:
                res["Max weekly hours (2x excess)"]["count"] += 1
                res["Max weekly hours (2x excess)"]["hard"] += 2 * (tot - MAX_WEEKLY_MIN)

    print("=== PER-CONSTRAINT HARD BREAKDOWN (Python, weights from .kt) ===", flush=True)
    print(f"{'constraint':<34}{'matches':>9}{'hard pts':>12}", flush=True)
    total = 0
    for name, d in sorted(res.items(), key=lambda kv: -kv[1]["hard"]):
        print(f"{name:<34}{d['count']:>9}{d['hard']:>12}", flush=True)
        total += d["hard"]
    print(f"{'TOTAL':<34}{'':>9}{total:>12}  (= {total} hard pts ≈ score hard)",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
