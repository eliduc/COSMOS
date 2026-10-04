"""Put config (б)'s GERAD roster on the SAME cost scale as config (а)
Stage-1 (pairing selection), so the two become directly comparable.

Config (а) Stage-1 minimizes sum of c_p = int(max(1, duty_minutes -
200*(legs-1))) over an exact partition of all legs into legal chains.
A config (б) roster IS also an exact partition of the same legs — each
employee's chronological flight sequence, segmented into chains. This
script re-solves inst N with config (б) (direct Timefold rostering,
rules=GERAD), then computes the BEST (а)-scale cost of that roster via
per-employee dynamic programming over contiguous segmentations (chain
rules identical to _gerad_colgen.py: <=5 legs, span <=96h, gap >=60min
and not in the [6h,10h) dead zone, multi-leg chains close the loop,
block time <=30h). Single-leg chains are always legal => DP total is
well-defined for any legal roster.

Usage: python _gerad_b_cost_parity.py <inst> [budget_s] [seed]
"""
from __future__ import annotations

import copy
import json
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
from flight_crew_scheduling.benchmark import KOTLIN_SOLVER_JAR  # noqa: E402
from _compr_isolated import IsolatedJvmFleet, ts  # noqa: E402

SCRATCH = Path(__file__).resolve().parent / "data" / "gerad"

MIN_SIT = 60
DEAD_LO, DEAD_HI = 6 * 60, 10 * 60   # illegal gap zone under GERAD profile
MAX_SPAN_MIN = 96 * 60
MAX_BLOCK_MIN = 30 * 60
MAX_LEGS = 5
DISCOUNT = 200


def chain_cost(legs):
    """(а)-scale cost of one chain; None if the chain is not (а)-legal.
    legs = list of (dep_min, arr_min, dep_code, arr_code, dur)."""
    n = len(legs)
    span = legs[-1][1] - legs[0][0]
    if span > MAX_SPAN_MIN:
        return None
    if n > 1:
        if legs[0][2] != legs[-1][3]:
            return None                      # loop closure
        if sum(l[4] for l in legs) > MAX_BLOCK_MIN:
            return None
        for a, b in zip(legs, legs[1:]):
            gap = b[0] - a[1]
            if gap < MIN_SIT or DEAD_LO <= gap < DEAD_HI:
                return None
    return max(1, span - DISCOUNT * (n - 1))


def dp_cost(seq):
    """Min (а)-scale cost of a contiguous segmentation of one employee's
    chronological leg sequence. Single-leg chains guarantee feasibility."""
    n = len(seq)
    dp = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        best = None
        for j in range(i, min(i + MAX_LEGS, n)):
            c = chain_cost(seq[i:j + 1])
            if c is not None:
                t = c + dp[j + 1]
                if best is None or t < best:
                    best = t
        dp[i] = best
    return dp[0]


def main():
    inst = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    seed = int(sys.argv[3]) if len(sys.argv) > 3 else 7

    payload = json.loads(
        (SCRATCH / "payloads" / f"inst{inst}.json").read_text(encoding="utf-8"))
    n_flights = len(payload["flights"])
    print(f"=== config (б) cost-parity: inst{inst} ({n_flights} legs), "
          f"LA seed {seed}, {budget}s, rules=GERAD ===", flush=True)

    t0 = time.monotonic()
    with IsolatedJvmFleet([9098], KOTLIN_SOLVER_JAR, heap_xmx="8g") as fleet:
        timeout = httpx.Timeout(budget * 2 + 120.0)
        with httpx.Client(timeout=timeout) as cli:
            r = cli.post(
                "http://127.0.0.1:9098/schedules/single",
                json=copy.deepcopy(payload),
                params={"acceptor": "LA", "spentLimitSeconds": budget,
                        "randomSeed": seed, "rules": "GERAD"})
            r.raise_for_status()
            sol = r.json()
    solve_s = time.monotonic() - t0
    print(f"[{ts()}] solved: score={sol.get('score')} in {solve_s:.0f}s",
          flush=True)

    out_path = SCRATCH / f"b_solution_inst{inst}.json"
    out_path.write_text(json.dumps(sol), encoding="utf-8")

    flights = {f["flightNumber"]: f for f in sol["flights"]}

    def _rid(v, key):
        return v.get(key) if isinstance(v, dict) else v

    by_emp = defaultdict(list)
    nulls = 0
    for a in sol["flightAssignments"]:
        eid = _rid(a.get("employee"), "id")
        fn = _rid(a.get("flight"), "flightNumber")
        if eid is None:
            nulls += 1
            continue
        f = flights[fn]
        dep = datetime.fromisoformat(f["departureUTCDateTime"])
        arr = datetime.fromisoformat(f["arrivalUTCDateTime"])
        e = datetime(2000, 1, 1)
        by_emp[eid].append((
            int((dep - e).total_seconds() // 60),
            int((arr - e).total_seconds() // 60),
            f["departureAirport"] if not isinstance(f["departureAirport"], dict)
            else f["departureAirport"]["code"],
            f["arrivalAirport"] if not isinstance(f["arrivalAirport"], dict)
            else f["arrivalAirport"]["code"],
        ))
    print(f"  assignments: {n_flights - nulls}/{n_flights} "
          f"({nulls} unassigned), {len(by_emp)} active employees", flush=True)

    total = 0
    chains_stats = defaultdict(int)
    for eid, seq in by_emp.items():
        seq.sort()
        seq = [(d, a, dc, ac, a - d) for d, a, dc, ac in seq]
        total += dp_cost(seq)

    print(f"  (а)-scale cost of the (б) roster (optimal segmentation): "
          f"{total}", flush=True)
    result = {"inst": inst, "score": sol.get("score"), "solve_s": round(solve_s, 1),
              "unassigned": nulls, "active_employees": len(by_emp),
              "a_scale_cost": total, "seed": seed, "budget_s": budget}
    import _data_paths; rp = _data_paths.results_dir() / f"gerad_b_cost_parity_inst{inst}.json"
    rp.write_text(json.dumps(result, indent=1))
    print(f"  saved -> {rp.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
