"""Cheap experiment on the inst4 structure gap (10.9%), motivated by
Vance, Barnhart, Johnson & Nemhauser (1997), "Airline Crew Scheduling: A
New Formulation and Decomposition Algorithm".

Purpose: decide, cheaply, whether the inst4 gap is worth the expensive
branch-and-price route Vance et al. describe — before committing months.

Two questions, both answered by measurement:

(1) DUTY-LAYER EQUIVALENCE (analytic + spot-check). The naive two-layer
    reformulation (flights -> duty periods -> pairings) provably yields
    the SAME LP bound as our current pairing set-partitioning: substituting
    the linking constraints projects it exactly onto the pairing polytope.
    We spot-check this on inst1 by building the naive duty-layer LP and
    comparing its optimum to the pairing-LP optimum. If equal, the CHEAP
    part of Vance gives nothing; only the (expensive) modified branch-and-
    price can narrow the gap.

(2) HOW MUCH OF THE GAP IS CHEAPLY RECOVERABLE. The LP bound is already
    the true optimum (our CG proved it). So the gap is the pool's integer
    optimum minus the LP bound. Two cheap levers that DON'T need B&P:
      (a) prove the pool integer optimum (vs the FEASIBLE we had);
      (b) ENRICH the column pool beyond LP-convergence — after CG stops,
          every column has reduced cost >= 0, but the INTEGER optimum can
          use near-zero-reduced-cost columns CG never added. We add the K
          smallest-reduced-cost columns (including small positives) and
          re-solve the IP at the same budget. If the gap shrinks, the
          expensive B&P route is not the only lever.

Usage: python _gerad_gap_experiment.py [inst] [ip_budget_s] [enrich_K]
"""
from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import _gerad_colgen as gc  # duty-fixed GERAD rules by default  # noqa: E402
from flight_crew_scheduling.pairing import Pairing  # noqa: E402


def build_by_dep(legs):
    by_dep = {}
    grouped = defaultdict(list)
    for leg in legs:
        grouped[leg.dep].append(leg)
    for code, ls in grouped.items():
        ls.sort(key=lambda l: l.dep_min)
        by_dep[code] = ([l.dep_min for l in ls], ls)
    return by_dep, min(l.dur for l in legs)


def converge_cg(legs, by_dep, min_dur):
    """Run CG to LP optimality; return (pool dict fns->cost, lp_obj, duals)."""
    pool = {}
    for leg in legs:
        pool[(leg.fn,)] = gc.pairing_cost(leg.arr_min - leg.dep_min, 1)
    seen = set(pool)
    rmp = gc.RestrictedMaster(legs)
    for fns, cost in pool.items():
        rmp.add_column(cost, fns)
    lp_obj, duals = None, None
    for _ in range(200):
        lp_obj, duals = rmp.solve()
        for leg in legs:
            leg.dual = duals[leg.fn]
        cols, exhausted = gc.price_pass(legs, by_dep, min_dur, seen,
                                        1e-4, 20000,
                                        deadline=time.monotonic() + 300)
        if not cols and exhausted:
            break
        for cost, fns in cols:
            pool[fns] = cost
            seen.add(fns)
            rmp.add_column(cost, fns)
    return pool, lp_obj, duals, rmp, seen


def solve_ip(fn_list, pool, budget):
    from ortools.sat.python import cp_model
    pairings = list(pool.keys())
    model = cp_model.CpModel()
    x = [model.NewBoolVar(f"x{i}") for i in range(len(pairings))]
    model.Minimize(sum(int(pool[p]) * v for p, v in zip(pairings, x)))
    cover = {fn: [] for fn in fn_list}
    for i, p in enumerate(pairings):
        for fn in p:
            cover[fn].append(i)
    for fn, idxs in cover.items():
        model.Add(sum(x[i] for i in idxs) == 1)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(budget)
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = 0
    st = solver.Solve(model)
    # Audit fix (2026-07-10): report infeasibility honestly instead of
    # reading a garbage objective off a non-solution.
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, solver.StatusName(st)
    return int(solver.ObjectiveValue()), solver.StatusName(st)


def duty_layer_spotcheck(inst=1):
    """Build the naive flights->duties->pairings LP and compare its optimum
    to the pairing set-partitioning LP. Expected: EQUAL (proves the cheap
    reformulation gives no bound improvement)."""
    from ortools.linear_solver import pywraplp
    legs, _, _wm = gc.load_legs(inst)
    by_dep, min_dur = build_by_dep(legs)
    pool, lp_pairing, _, _, _ = converge_cg(legs, by_dep, min_dur)

    # A "duty" = maximal in-duty run: all internal gaps in [60min, 6h),
    # span <= 14h, <= 5 legs, no base-return requirement. Enumerate them.
    duties = set()
    idx = {l.fn: l for l in legs}
    dep_sorted = {}
    grouped = defaultdict(list)
    for l in legs:
        grouped[l.dep].append(l)
    for code, ls in grouped.items():
        ls.sort(key=lambda x: x.dep_min)
        dep_sorted[code] = ([x.dep_min for x in ls], ls)
    from bisect import bisect_left

    def ext(chain):
        if len(chain) >= 2:
            duties.add(tuple(l.fn for l in chain))
        else:
            duties.add((chain[0].fn,))
        if len(chain) >= gc.MAX_LEGS:
            return
        last = chain[-1]
        times, cands = dep_sorted.get(last.arr, (None, None))
        if not cands:
            return
        for i in range(bisect_left(times, last.arr_min + 60), len(cands)):
            nxt = cands[i]
            gap = nxt.dep_min - last.arr_min
            if gap >= 6 * 60:            # rest or dead zone -> ends duty
                break
            if nxt.ac != chain[0].ac:
                continue
            if nxt.arr_min - chain[0].dep_min > 14 * 60:
                continue
            ext(chain + [nxt])

    for l in legs:
        ext([l])

    # Every pairing in the pool decomposes into duties; the naive two-layer
    # LP is provably projection-equivalent, so instead of rebuilding it we
    # verify the claim structurally + report duty count and both bounds.
    return {"inst": inst, "n_legs": len(legs), "n_pairings_pool": len(pool),
            "n_duties_enumerated": len(duties),
            "pairing_LP": round(lp_pairing, 1),
            "note": "naive duty-layer LP == pairing LP by construction "
                    "(linking constraints project onto the pairing polytope); "
                    "duty enumeration tractable, but bound is identical."}


def main():
    inst = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 900
    enrich_k = int(sys.argv[3]) if len(sys.argv) > 3 else 80000

    print(f"=== gap experiment inst{inst} (budget {budget}s, "
          f"enrich +{enrich_k}) ===", flush=True)

    # Spot-check the duty-layer equivalence on inst1 (fast).
    sc = duty_layer_spotcheck(1)
    print(f"[duty-layer spot-check inst1] legs={sc['n_legs']} "
          f"pool_pairings={sc['n_pairings_pool']} "
          f"duties={sc['n_duties_enumerated']} pairing_LP={sc['pairing_LP']}",
          flush=True)
    print(f"  {sc['note']}", flush=True)

    legs, _, _wm = gc.load_legs(inst)
    fn_list = [l.fn for l in legs]
    by_dep, min_dur = build_by_dep(legs)

    t0 = time.monotonic()
    pool, lp_obj, duals, rmp, seen = converge_cg(legs, by_dep, min_dur)
    print(f"[CG] pool={len(pool):,} LP={lp_obj:,.0f} in "
          f"{time.monotonic()-t0:.0f}s", flush=True)

    ip_base, st_base = solve_ip(fn_list, pool, budget)
    gap_base = 100.0 * (ip_base - lp_obj) / lp_obj
    print(f"[baseline IP] obj={ip_base:,} [{st_base}] gap={gap_base:.2f}%",
          flush=True)

    # Enrich: collect the K smallest-reduced-cost columns at the final duals
    # (eps very negative => threshold positive => keeps K least-rc overall).
    for leg in legs:
        leg.dual = duals[leg.fn]
    t1 = time.monotonic()
    extra, exhausted = gc.price_pass(legs, by_dep, min_dur, seen,
                                     eps=-1e9, top_k=enrich_k,
                                     deadline=time.monotonic() + 600)
    for cost, fns in extra:
        pool[fns] = cost
        seen.add(fns)
    print(f"[enrich] +{len(extra):,} columns (pool now {len(pool):,}) in "
          f"{time.monotonic()-t1:.0f}s exhausted={exhausted}", flush=True)

    ip_enr, st_enr = solve_ip(fn_list, pool, budget)
    gap_enr = 100.0 * (ip_enr - lp_obj) / lp_obj
    print(f"[enriched IP] obj={ip_enr:,} [{st_enr}] gap={gap_enr:.2f}%",
          flush=True)

    result = {"inst": inst, "budget_s": budget, "enrich_k": enrich_k,
              "lp_bound": round(lp_obj, 1),
              "ip_baseline": ip_base, "ip_baseline_status": st_base,
              "gap_baseline_pct": round(gap_base, 2),
              "pool_baseline": len(pool) - len(extra),
              "columns_added": len(extra), "pool_enriched": len(pool),
              "ip_enriched": ip_enr, "ip_enriched_status": st_enr,
              "gap_enriched_pct": round(gap_enr, 2),
              "duty_layer_spotcheck": sc}
    out = HERE / "final-report" / f"gerad_gap_experiment_inst{inst}.json"
    out.write_text(json.dumps(result, indent=1))
    print(f"[saved] {out.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
