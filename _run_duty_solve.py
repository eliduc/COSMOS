"""RULES v2 finale: fresh all_at_once solve under duty-period-aware rules.

With the 10h-between-any-flights rule replaced by turnaround/duty-rest
rules, the direct solver CAN now chain legs into duty days — the very
thing that gave the pairing roster its 33% soft advantage. Question: does
direct assignment exploit chaining and close the gap?

Prints: score + completeness, gap-structure stats (how much chaining the
roster actually uses), hard breakdown (should be 0), and the comparison
against the pairing roster's RULES-v2 numbers.

Usage: python _run_duty_solve.py [budget_s] [seed]   (default 1200 11)
"""
from __future__ import annotations

import json
import sys
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
from flight_crew_scheduling.benchmark import KOTLIN_SOLVER_JAR  # noqa: E402
from _run_bts_two_stage import load_schedule, ts  # noqa: E402
from _compr_isolated import IsolatedJvmFleet  # noqa: E402
from _run_rescoring import (  # noqa: E402
    SCHEDULE_JSON, count_nulls, hard_breakdown, solve_single, _ref_id,
)

# RULES v2 numbers of the pairing roster (scored moments ago)
PAIRING_V2 = "-248225hard/-1092293soft"


def gap_stats(payload):
    """Distribution of adjacent same-employee gaps + duty-day leg counts."""
    flights = {f["flightNumber"]: f for f in payload["flights"]}
    by_emp = defaultdict(list)
    for a in payload["flightAssignments"]:
        eid = _ref_id(a.get("employee"), "id")
        fn = _ref_id(a.get("flight"), "flightNumber")
        if eid is None or fn not in flights:
            continue
        f = flights[fn]
        by_emp[eid].append((
            datetime.fromisoformat(f["departureUTCDateTime"]),
            datetime.fromisoformat(f["arrivalUTCDateTime"])))
    buckets = {"<60m (illegal)": 0, "60m-6h (sit)": 0,
               "6-8h (dead zone)": 0, ">=8h (rest)": 0}
    legs_per_day = defaultdict(int)
    for eid, lst in by_emp.items():
        lst.sort()
        for i in range(len(lst) - 1):
            gap = (lst[i + 1][0] - lst[i][1]).total_seconds() / 60
            if gap < 60:
                buckets["<60m (illegal)"] += 1
            elif gap < 360:
                buckets["60m-6h (sit)"] += 1
            elif gap < 480:
                buckets["6-8h (dead zone)"] += 1
            else:
                buckets[">=8h (rest)"] += 1
        byday = defaultdict(int)
        for dep, _ in lst:
            byday[dep.date()] += 1
        for n in byday.values():
            legs_per_day[n] += 1
    return buckets, dict(sorted(legs_per_day.items()))


def main():
    budget = int(sys.argv[1]) if len(sys.argv) > 1 else 1200
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 11

    print(f"[{ts()}] === RULES v2: fresh all_at_once solve "
          f"(LA, seed {seed}, {budget}s) ===", flush=True)
    schedule = load_schedule(SCHEDULE_JSON)
    payload = schedule.model_dump(by_alias=True, mode="json")
    payload.pop("solverStatus", None)

    solved = solve_single(payload, seed, budget)
    nulls = count_nulls(solved)
    print(f"  score: {solved.get('score')}  (unassigned: {nulls})", flush=True)
    (HERE / "_duty_full_solved.json").write_text(
        json.dumps(solved), encoding="utf-8")

    print(f"\n[{ts()}] gap structure of the NEW direct roster:", flush=True)
    buckets, legs = gap_stats(solved)
    for k, v in buckets.items():
        print(f"  {k:<18} {v:>6}", flush=True)
    print(f"  legs per crew-duty-day: {legs}", flush=True)

    print(f"\n[{ts()}] hard breakdown (mirror, should be ~0):", flush=True)
    res = hard_breakdown(solved)
    if not res:
        print("  (no hard violations)", flush=True)
    for name, d in sorted(res.items(), key=lambda kv: -kv[1]["hard"]):
        print(f"  {name:<36} n={d['n']:>5} hard={d['hard']:>9}", flush=True)

    print(f"\n[{ts()}] === RULES v2 COMPARISON (single scale) ===", flush=True)
    print(f"  all_at_once (fresh, rules v2): {solved.get('score')}", flush=True)
    print(f"  pairing roster (expanded):     {PAIRING_V2}", flush=True)
    print(f"[{ts()}] DONE.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
