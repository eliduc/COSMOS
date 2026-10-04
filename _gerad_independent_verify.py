"""Independent verification of the 7 GERAD structure optima, using solver
engines DIFFERENT from the CP-SAT that produced them.

Part 1 (this script): re-solve the set-partitioning IP over each instance's
CG column pool with SCIP (branch-and-cut, bundled in OR-Tools — a different
engine class than CP-SAT's lazy-clause-generation) and, if available,
HiGHS. Agreement with our partition-CG optima = independent confirmation of
the INTEGER-solve half of the proof.

Part 2 (bound certification, --certify): re-enumerate ALL legal pairings
(duty-fixed) and, at the converged set-partitioning duals, compute the
minimum reduced cost over the whole space via a code path independent of
the CG pricer's DFS pruning. min reduced cost >= -eps certifies the LP
lower bound (hence the 0% gap) without trusting our pricer.

Usage:
  python _gerad_independent_verify.py            # Part 1, all instances
  python _gerad_independent_verify.py --certify N   # Part 2 for instance N
"""
from __future__ import annotations

import json
import sys
import time
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import _gerad_colgen as gc  # noqa: E402
import _gerad_gap_experiment as ge  # noqa: E402
import _gerad_partition_cg as pc  # noqa: E402

KNOWN = {1: 99943, 2: 97696, 3: 134539, 4: 320736,
         5: 825756, 6: 645003, 7: 1077874}


def solve_ip_scip(fn_list, pool, budget):
    from ortools.linear_solver import pywraplp
    s = pywraplp.Solver.CreateSolver("SCIP")
    if s is None:
        return None, "SCIP-unavailable"
    s.SetTimeLimit(int(budget * 1000))
    pk = list(pool.keys())
    x = [s.IntVar(0, 1, "") for _ in pk]
    s.Minimize(s.Sum(int(pool[p]) * v for p, v in zip(pk, x)))
    cover = {fn: [] for fn in fn_list}
    for i, p in enumerate(pk):
        for fn in p:
            cover[fn].append(i)
    for fn, idxs in cover.items():
        s.Add(s.Sum(x[i] for i in idxs) == 1)
    st = s.Solve()
    names = {pywraplp.Solver.OPTIMAL: "OPTIMAL",
             pywraplp.Solver.FEASIBLE: "FEASIBLE"}
    if st in names:
        return int(round(s.Objective().Value())), names[st]
    return None, f"status{st}"


def solve_ip_highs(fn_list, pool, budget):
    try:
        import highspy
    except ImportError:
        return None, "highspy-not-installed"
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(budget))
    pk = list(pool.keys())
    idx = {fn: [] for fn in fn_list}
    xs = []
    for j, p in enumerate(pk):
        v = h.addBinary()
        xs.append(v)
        for fn in p:
            idx[fn].append(v)
    for fn, vs in idx.items():
        h.addConstr(sum(vs) == 1)
    h.minimize(sum(int(pool[p]) * v for p, v in zip(pk, xs)))
    info = h.getInfo()
    obj = h.getObjectiveValue()
    return int(round(obj)), str(h.getModelStatus())


def part1(budget=1200):
    rows = []
    for inst in range(1, 8):
        legs, _, _wm = gc.load_legs(inst)
        fn_list = [l.fn for l in legs]
        by_dep, min_dur = ge.build_by_dep(legs)
        pool, _, _, _, _ = ge.converge_cg(legs, by_dep, min_dur)
        scip, sst = solve_ip_scip(fn_list, pool, budget)
        highs, hst = solve_ip_highs(fn_list, pool, budget)
        known = KNOWN[inst]
        ok_scip = (scip == known)
        ok_highs = (highs == known) if highs is not None else None
        print(f"inst{inst}: known={known:,} | SCIP={scip} [{sst}] "
              f"{'OK' if ok_scip else 'MISMATCH'} | HiGHS={highs} [{hst}] "
              f"{'OK' if ok_highs else ('n/a' if ok_highs is None else 'MISMATCH')}",
              flush=True)
        rows.append({"inst": inst, "known": known, "pool": len(pool),
                     "scip": scip, "scip_status": sst, "scip_match": ok_scip,
                     "highs": highs, "highs_status": hst,
                     "highs_match": ok_highs})
    out = HERE / "final-report" / "gerad_independent_verify.json"
    out.write_text(json.dumps(rows, indent=1))
    n_scip = sum(1 for r in rows if r["scip_match"])
    n_highs = sum(1 for r in rows if r["highs_match"])
    print(f"\nSCIP confirmed {n_scip}/7 ; HiGHS confirmed {n_highs}/7", flush=True)
    print(f"saved -> {out.name}", flush=True)


def certify(inst):
    """Independent LP-bound certification: min reduced cost over ALL legal
    pairings at the converged partition duals, via a fresh enumeration path
    (not the CG pricer). Returns the minimum; >= -eps proves the bound."""
    legs, _, _wm = gc.load_legs(inst)
    by_dep, min_dur = ge.build_by_dep(legs)
    # Converge partition CG to get duals + bound (reuse partition master).
    m = pc.PartitionMaster(legs)
    pool = {}
    for l in legs:
        pool[(l.fn,)] = gc.pairing_cost(l.arr_min - l.dep_min, 1)
        m.add(pool[(l.fn,)], (l.fn,))
    seen = set(pool)
    lp_obj = duals = None
    for _ in range(300):
        lp_obj, duals = m.solve()
        for l in legs:
            l.dual = duals[l.fn]
        cols, ex = gc.price_pass(legs, by_dep, min_dur, seen, 1e-4, 20000,
                                 deadline=time.monotonic() + 300)
        if not cols and ex:
            break
        for c, fns in cols:
            pool[fns] = c
            seen.add(fns)
            m.add(c, fns)
    print(f"[inst{inst}] converged partition LP = {lp_obj:,.1f}", flush=True)

    # INDEPENDENT full enumeration (duty-fixed), tracking min reduced cost.
    dep_sorted = {}
    grouped = defaultdict(list)
    for l in legs:
        grouped[l.dep].append(l)
    for code, ls in grouped.items():
        ls.sort(key=lambda x: x.dep_min)
        dep_sorted[code] = ([x.dep_min for x in ls], ls)
    dual = {l.fn: l.dual for l in legs}
    best = [float("inf")]
    cnt = [0]
    t0 = time.monotonic()

    def ext(chain, dsum, duty_start):
        first, last = chain[0], chain[-1]
        n = len(chain)
        if n >= 2 and first.dep == last.arr:
            cost = gc.pairing_cost(last.arr_min - first.dep_min, n)
            rc = cost - dsum
            if rc < best[0]:
                best[0] = rc
            cnt[0] += 1
            if cnt[0] % 5_000_000 == 0:
                print(f"  {cnt[0]:,} pairings, min rc so far {best[0]:.1f}, "
                      f"{time.monotonic()-t0:.0f}s", flush=True)
        if n >= gc.MAX_LEGS:
            return
        times, cands = dep_sorted.get(last.arr, (None, None))
        if not cands:
            return
        latest = first.dep_min + gc.MAX_PAIRING_MIN
        for i in range(bisect_left(times, last.arr_min + gc.MIN_GROUND_TIME_MIN),
                       len(cands)):
            nxt = cands[i]
            if nxt.dep_min > latest:
                break
            gap = nxt.dep_min - last.arr_min
            if gc.DAY_BREAK_THRESHOLD_MIN <= gap < gc.MIN_OVERNIGHT_REST_MIN:
                continue
            if nxt.idx in [c.idx for c in chain]:
                continue
            if nxt.ac != first.ac:
                continue
            if nxt.arr_min - first.dep_min > gc.MAX_PAIRING_MIN:
                continue
            nds = nxt.dep_min if gap >= gc.MIN_OVERNIGHT_REST_MIN else duty_start
            if nxt.arr_min - nds > gc.MAX_DUTY_SPAN_MIN:
                continue
            ext(chain + [nxt], dsum + dual[nxt.fn], nds)

    for l in legs:
        # single-leg reduced cost too
        rc = gc.pairing_cost(l.arr_min - l.dep_min, 1) - dual[l.fn]
        if rc < best[0]:
            best[0] = rc
        ext([l], dual[l.fn], l.dep_min)
    dt = time.monotonic() - t0
    print(f"[inst{inst}] enumerated {cnt[0]:,} multi-leg pairings in {dt:.0f}s",
          flush=True)
    print(f"[inst{inst}] MIN reduced cost over ALL pairings = {best[0]:.3f}",
          flush=True)
    certified = best[0] >= -1e-4
    print(f"[inst{inst}] bound {lp_obj:,.0f} "
          f"{'CERTIFIED (min rc >= 0)' if certified else 'NOT certified'}",
          flush=True)
    out = HERE / "final-report" / "gerad_bound_certification.json"
    prior = json.loads(out.read_text()) if out.exists() else []
    prior = [r for r in prior if r.get("inst") != inst] + [{
        "inst": inst, "partition_lp": round(lp_obj, 1),
        "min_reduced_cost": round(best[0], 3), "certified": certified,
        "pairings_enumerated": cnt[0], "enum_s": round(dt, 1)}]
    out.write_text(json.dumps(prior, indent=1))
    print(f"saved -> {out.name}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--certify":
        certify(int(sys.argv[2]))
    else:
        part1()
