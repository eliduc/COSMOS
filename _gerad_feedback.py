"""Phase 3, experiment 2: the feedback loop («а-штрих», after Gomes &
Gualda 2015) — make the structure-selection step "see" the rostering
outcome.

Loop (per instance):
  0. build the column pool once via CG (imported from _gerad_colgen,
     duty-fixed rules), select with BASE costs, roster via GERAD2P,
     measure (preference hits, vacation violations, structure cost).
  k. adjust per-pairing costs from the LAST roster:
       miss_p = |legs of p| - |legs of p the assigned employee prefers|
       vac_p  = 1 if the assigned employee's vacation overlaps p
       c'_p   = c_p + LAMBDA_MISS * miss_p + LAMBDA_VAC * vac_p
     re-select over the SAME pool (CP-SAT), re-roster, re-measure.
  Metric: does preference satisfaction of the two-stage pipeline move
  toward the direct config's level, and at what structure-cost price.

Usage: python _gerad_feedback.py <inst> [iters] [ip_tl] [roster_s] [seed]
"""
from __future__ import annotations

import copy
import json
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
from flight_crew_scheduling.benchmark import KOTLIN_SOLVER_JAR  # noqa: E402
from flight_crew_scheduling.pairing import Pairing  # noqa: E402
from flight_crew_scheduling.pairing_selector import (  # noqa: E402
    select_pairings_set_partitioning,
)
from _compr_isolated import IsolatedJvmFleet  # noqa: E402
import _gerad_colgen as gc  # noqa: E402  (duty-fixed rules by default)
from _gerad_stage2_roster import (  # noqa: E402
    SCRATCH, build_synthetic_payload, expand_to_flight_level,
)
from _gerad_phase2_direct import personalization_metrics, _rid  # noqa: E402
from _gerad_structure_eval import evaluate as structure_eval  # noqa: E402

PORT = 9093
LAMBDA_MISS = 40
LAMBDA_VAC = 500


def build_pool(inst: int):
    legs, flight_by_fn, _wm = gc.load_legs(inst)
    by_dep = {}
    from collections import defaultdict
    grouped = defaultdict(list)
    for leg in legs:
        grouped[leg.dep].append(leg)
    for code, ls in grouped.items():
        ls.sort(key=lambda l: l.dep_min)
        by_dep[code] = ([l.dep_min for l in ls], ls)
    min_dur = min(l.dur for l in legs)

    pool = {}
    for leg in legs:
        pool[(leg.fn,)] = gc.pairing_cost(leg.arr_min - leg.dep_min, 1)
    seen = set(pool)
    rmp = gc.RestrictedMaster(legs)
    for fns, cost in pool.items():
        rmp.add_column(cost, fns)
    for it in range(200):
        lp_obj, duals = rmp.solve()
        for leg in legs:
            leg.dual = duals[leg.fn]
        cols, exhausted = gc.price_pass(
            legs, by_dep, min_dur, seen, 1e-4, 20000,
            deadline=time.monotonic() + 300)
        if not cols and exhausted:
            break
        for cost, fns in cols:
            pool[fns] = cost
            seen.add(fns)
            rmp.add_column(cost, fns)
    print(f"  pool: {len(pool):,} columns, LP bound {lp_obj:,.0f}",
        flush=True)
    return legs, flight_by_fn, pool, lp_obj


def main():
    inst = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    iters = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    ip_tl = int(sys.argv[3]) if len(sys.argv) > 3 else 600
    roster_s = int(sys.argv[4]) if len(sys.argv) > 4 else 600
    seed = int(sys.argv[5]) if len(sys.argv) > 5 else 7

    payload = json.loads((SCRATCH / "payloads" / f"inst{inst}_p2.json")
                         .read_text(encoding="utf-8"))
    pref_of = {e["id"]: set(e.get("preferredFlights") or ())
               for e in payload["employees"]}
    vac_of = {e["id"]: set(e.get("preferredDaysOff") or ())
              for e in payload["employees"]}
    fn_list = [f["flightNumber"] for f in payload["flights"]]

    print(f"=== feedback loop (а'): inst{inst}, {iters} feedback iters, "
          f"IP {ip_tl}s, roster {roster_s}s, seed {seed} ===", flush=True)
    legs, flight_by_fn, pool, lp_bound = build_pool(inst)

    adjust = {}          # fns -> penalty from the last roster
    history = []
    for k in range(iters + 1):
        costs = {fns: c + adjust.get(fns, 0) for fns, c in pool.items()}
        pairings = [Pairing.from_flights(f"C_{i:06d}",
                                         [flight_by_fn[fn] for fn in fns])
                    for i, fns in enumerate(costs)]
        # selection minimizes ADJUSTED costs: selector recomputes its own
        # base cost, so shift duty_minutes is not an option — instead we
        # pass adjusted costs by rebuilding the selector inline:
        selected, info = select_with_costs(fn_list, pairings, costs, ip_tl)
        sel_fns = [tuple(p.flight_numbers) for p in selected]
        base_obj = sum(pool[f] for f in sel_fns)

        syn = build_synthetic_payload(
            payload, [{"fns": list(f)} for f in sel_fns])
        with IsolatedJvmFleet([PORT], KOTLIN_SOLVER_JAR, heap_xmx="8g") as fl:
            with httpx.Client(timeout=roster_s * 2 + 120.0) as cli:
                r = cli.post(f"http://127.0.0.1:{PORT}/schedules/single",
                             json=copy.deepcopy(syn),
                             params={"acceptor": "LA",
                                     "spentLimitSeconds": roster_s,
                                     "randomSeed": seed,
                                     "rules": "GERAD2P"})
                r.raise_for_status()
                solved = r.json()
        expanded = expand_to_flight_level(payload, solved)
        out_p = SCRATCH / f"p3_feedback_inst{inst}_iter{k}.json"
        out_p.write_text(json.dumps(expanded), encoding="utf-8")
        m = personalization_metrics(expanded)
        struct = structure_eval(out_p)
        row = {"iter": k, "ip_status": info["status"],
               "structure_cost_base": base_obj,
               "structure_cost_dp": struct,
               "pref_hits": m["pref_hits"],
               "vacation_violations": m["vacation_violations"],
               "unassigned": m["unassigned"]}
        history.append(row)
        print(f"  iter {k}: sel={len(selected)} [{info['status']}] "
              f"base_struct={base_obj:,} dp_struct={struct:,} "
              f"pref={m['pref_hits']} vac={m['vacation_violations']} "
              f"unassigned={m['unassigned']}", flush=True)

        # ---- feedback for the next round -------------------------------
        emp_of_pairing = {}
        for a in solved["flightAssignments"]:
            pid = _rid(a.get("flight"), "flightNumber")
            emp_of_pairing[pid] = _rid(a.get("employee"), "id")
        adjust = {}
        for i, fns in enumerate(sel_fns):
            eid = emp_of_pairing.get(f"C_{i:06d}")
            if eid is None:
                continue
            miss = len(fns) - sum(1 for fn in fns if fn in pref_of[eid])
            days = set()
            for fn in fns:
                f = next(fl for fl in payload["flights"]
                         if fl["flightNumber"] == fn)
                days.add(f["departureUTCDateTime"][:10])
                days.add(f["arrivalUTCDateTime"][:10])
            vac = 1 if days & vac_of[eid] else 0
            adjust[fns] = LAMBDA_MISS * miss + LAMBDA_VAC * vac

    res = {"inst": inst, "lp_bound": round(lp_bound, 1),
           "lambda_miss": LAMBDA_MISS, "lambda_vac": LAMBDA_VAC,
           "seed": seed, "ip_tl": ip_tl, "roster_s": roster_s,
           "history": history}
    rp = HERE / "final-report" / f"gerad_phase3_feedback_inst{inst}.json"
    rp.write_text(json.dumps(res, indent=1))
    print(f"  saved -> {rp.name}", flush=True)
    return 0


def select_with_costs(fn_list, pairings, costs, tl):
    """CP-SAT set partitioning with EXPLICIT per-pairing costs."""
    from ortools.sat.python import cp_model
    model = cp_model.CpModel()
    x = [model.NewBoolVar(f"x{i}") for i in range(len(pairings))]
    model.Minimize(sum(int(costs[tuple(p.flight_numbers)]) * v
                       for p, v in zip(pairings, x)))
    cover = {fn: [] for fn in fn_list}
    for i, p in enumerate(pairings):
        for fn in p.flight_numbers:
            cover[fn].append(i)
    for fn, idxs in cover.items():
        model.Add(sum(x[i] for i in idxs) == 1)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(tl)
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status = solver.Solve(model)
    sel = [pairings[i] for i in range(len(pairings))
           if solver.Value(x[i]) == 1]
    return sel, {"status": solver.StatusName(status)}


if __name__ == "__main__":
    sys.exit(main())
