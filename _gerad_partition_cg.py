"""Set-PARTITIONING column generation (==1 master, free-sign duals) vs our
set-COVERING master, to get a rigorous LP lower bound and re-measure the
structure gap. Motivated by the inst4 finding: partition-LP over the
covering pool == integer optimum, so the covering relaxation was the sole
source of the apparent 10.9% gap.

Validity: the pricing pruning bound in _gerad_colgen.price_pass stays valid
with free-sign duals (max_dual bounds the dual contribution of any
completion regardless of sign). Single-leg seeds keep the ==1 master
feasible throughout. Convergence with no negative reduced cost => the
reported bound is a rigorous full-space set-partitioning LP lower bound.

Base-anchored mode (audit fix, 2026-07-10): --bases restricts pairings to
start (and close) at a crew base — the GENCOL reference semantics (0
anchoring violations over 6160 reference pairings). Single-leg seeds do
not exist then; the ==1 master stays feasible via artificial columns at
big-M cost. Residual artificial usage marks uncoverable legs honestly.

Usage: python _gerad_partition_cg.py <inst> [--ip-budget S]
         [--bases BASE1,BASE2,BASE3] [--max-legs N] [--out FILE]
"""
from __future__ import annotations

import argparse
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

import _gerad_colgen as gc  # noqa: E402
import _gerad_gap_experiment as ge  # noqa: E402
from ortools.linear_solver import pywraplp  # noqa: E402


class PartitionMaster:
    def __init__(self, legs, artificial_cost: float | None = None):
        self.s = pywraplp.Solver.CreateSolver("GLOP")
        self.obj = self.s.Objective()
        self.obj.SetMinimization()
        self.cons = {l.fn: self.s.Constraint(1.0, 1.0, l.fn) for l in legs}
        self.inf = self.s.infinity()
        self._arts = []
        if artificial_cost is not None:
            for l in legs:
                a = self.s.NumVar(0.0, self.inf, f"art_{l.fn}")
                self.obj.SetCoefficient(a, float(artificial_cost))
                self.cons[l.fn].SetCoefficient(a, 1.0)
                self._arts.append((l.fn, a))

    def add(self, cost, fns):
        v = self.s.NumVar(0.0, self.inf, "")
        self.obj.SetCoefficient(v, float(cost))
        for fn in fns:
            self.cons[fn].SetCoefficient(v, 1.0)

    def solve(self):
        st = self.s.Solve()
        if st != pywraplp.Solver.OPTIMAL:
            return None, None
        return self.obj.Value(), {fn: c.dual_value()
                                  for fn, c in self.cons.items()}

    def artificial_usage(self):
        return [(fn, a.solution_value()) for fn, a in self._arts
                if a.solution_value() > 1e-6]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inst", type=int)
    ap.add_argument("--ip-budget", type=int, default=900)
    ap.add_argument("--bases", default=None,
                    help="comma-separated base codes (base-anchored space)")
    ap.add_argument("--max-legs", type=int, default=None)
    ap.add_argument("--price-cap", type=float, default=300.0)
    ap.add_argument("--mp", type=int, default=0,
                    help="parallel pricing workers (0/1 = single-threaded)")
    ap.add_argument("--out", default=None,
                    help="results file (default: gerad_partition_cg.json, "
                         "or gerad_partition_cg_base.json in --bases mode)")
    args = ap.parse_args()
    inst = args.inst
    bases = set(args.bases.split(",")) if args.bases else None
    if args.max_legs:
        gc.MAX_LEGS = args.max_legs

    executor = None
    if args.mp and args.mp > 1:
        from concurrent.futures import ProcessPoolExecutor
        import _gerad_pricing_mp as pmp
        executor = ProcessPoolExecutor(
            max_workers=args.mp, initializer=pmp.init_worker,
            initargs=(inst, None, "gerad", False, args.max_legs))
        print(f"  pricing: {args.mp} parallel workers", flush=True)

    legs, _, _wm = gc.load_legs(inst)
    fn_list = [l.fn for l in legs]
    by_dep, min_dur = ge.build_by_dep(legs)

    pool = {}
    if bases:
        starts = [l for l in legs if l.dep in bases]
        m = PartitionMaster(legs, artificial_cost=gc.ARTIFICIAL_COST)
    else:
        starts = None
        for l in legs:
            pool[(l.fn,)] = gc.pairing_cost(l.arr_min - l.dep_min, 1)
        m = PartitionMaster(legs)
    seen = set(pool)
    for fns, c in pool.items():
        m.add(c, fns)

    base_note = (f", base-anchored {sorted(bases)}, max {gc.MAX_LEGS} legs"
                 if bases else "")
    print(f"=== partition CG inst{inst} ({len(legs)} legs, "
          f"sit>={gc.MIN_GROUND_TIME_MIN}min, "
          f"dead_zone={gc.DEAD_ZONE_ON}{base_note}) ===", flush=True)
    if bases:
        print(f"  starts: {len(starts)}/{len(legs)} legs depart from a base",
              flush=True)
    t0 = time.monotonic()
    lp_obj = None
    converged = False
    for it in range(300):
        lp_obj, duals = m.solve()
        if lp_obj is None:
            print("  LP not optimal — abort", flush=True)
            break
        for l in legs:
            l.dual = duals[l.fn]
        if executor is not None:
            import _gerad_pricing_mp as pmp
            cols, exhausted = pmp.price_pass_mp(
                executor, legs, starts if starts is not None else legs,
                seen, 1e-4, 20000,
                deadline=time.monotonic() + args.price_cap,
                workers=args.mp)
        else:
            cols, exhausted = gc.price_pass(legs, by_dep, min_dur, seen,
                                            1e-4, 20000,
                                            deadline=time.monotonic()
                                            + args.price_cap,
                                            starts=starts)
        if not cols:
            if exhausted:
                converged = True
            print(f"  it {it}: LP {lp_obj:,.1f}  +0 cols  "
                  f"{'CONVERGED (valid bound)' if exhausted else 'capped'}",
                  flush=True)
            break
        for cost, fns in cols:
            pool[fns] = cost
            seen.add(fns)
            m.add(cost, fns)
        if it % 5 == 0 or len(cols) < 50:
            print(f"  it {it}: LP {lp_obj:,.1f}  +{len(cols)} cols  "
                  f"pool {len(pool):,}", flush=True)
    t_cg = time.monotonic() - t0
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)

    art_used = m.artificial_usage() if bases else []
    if bases:
        if art_used:
            print(f"  !! {len(art_used)} legs on artificial slack "
                  f"(uncoverable from bases with max {gc.MAX_LEGS} legs): "
                  f"{[fn for fn, _ in art_used[:10]]}"
                  f"{'...' if len(art_used) > 10 else ''}", flush=True)
        else:
            print("  artificial slack fully driven out", flush=True)

    ip_obj, st = ge.solve_ip(fn_list, pool, args.ip_budget)
    gap = (100.0 * (ip_obj - lp_obj) / lp_obj
           if ip_obj is not None and lp_obj else None)
    # Covering bounds from the standard runner — old (any-start) space only.
    cover = (None if bases else
             {1: 99424.0, 2: 96623.0, 3: 131729.0, 4: 289555.0,
              5: 806601.0, 6: 631681.0, 7: 1066298.0}.get(inst))
    print(f"[partition CG] LP bound "
          f"{lp_obj:,.0f} ({'valid' if converged and not art_used else 'RESTRICTED'}) "
          f"in {t_cg:.0f}s" if lp_obj is not None else "[partition CG] no LP",
          flush=True)
    if cover:
        print(f"[covering bound was] {cover:,.0f}", flush=True)
    print(f"[IP] {ip_obj} [{st}]" + (f"  gap vs partition-LP = {gap:.2f}%"
                                     if gap is not None else ""), flush=True)
    if cover and ip_obj is not None:
        print(f"  (old gap vs covering-LP was "
              f"{100*(ip_obj-cover)/cover:.2f}%)", flush=True)

    out = {"inst": inst,
           "partition_lp": round(lp_obj, 1) if lp_obj is not None else None,
           "partition_lp_valid": converged and not art_used,
           "cg_s": round(t_cg, 1),
           "pool": len(pool), "ip": ip_obj, "ip_status": st,
           "gap_vs_partition_lp_pct": round(gap, 2) if gap is not None else None,
           "covering_lp": cover,
           "gap_vs_covering_pct": (round(100*(ip_obj-cover)/cover, 2)
                                   if cover and ip_obj is not None else None),
           "bases": sorted(bases) if bases else None,
           "max_legs": gc.MAX_LEGS,
           "min_sit_min": gc.MIN_GROUND_TIME_MIN,
           "dead_zone": gc.DEAD_ZONE_ON,
           "uncoverable_legs": [fn for fn, _ in art_used] if bases else None}
    p = Path(args.out) if args.out else (
        HERE / "final-report" /
        ("gerad_partition_cg_base.json" if bases
         else "gerad_partition_cg.json"))
    # Atomic, crash/concurrency-tolerant save (same pattern as the runner).
    for attempt in range(5):
        try:
            prior = json.loads(p.read_text()) if p.exists() else []
        except (json.JSONDecodeError, OSError):
            prior = []
        prior = [r for r in prior if r.get("inst") != inst] + [out]
        tmp = p.with_suffix(f".tmp{inst}")
        tmp.write_text(json.dumps(prior, indent=1))
        try:
            tmp.replace(p)
            break
        except OSError:
            time.sleep(0.5 * (attempt + 1))
    print(f"[saved] {p.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
