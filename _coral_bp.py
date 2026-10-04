"""Branch-and-price for COR@L set-partitioning instances via PySCIPOpt's
pricer plugin, over the EXPLICIT column pool (8.1M / 12.6M columns).

SCIP runs the branch-and-bound tree, bounding and branching; our Pricer
generates negative-reduced-cost columns from the pool at each node
(vectorized reduced cost over the whole pool). This is real
branch-and-price (Vance et al. 1997) — the technique needed to PROVE the
integer optimum that monolithic solvers and price-then-branch cannot reach
(A320: LP 940.65, proven integer optimum 1078).

Master: 190/342 set-partitioning constraints (=1). One continuous
artificial per row (big cost) keeps the LP feasible, so only reduced-cost
pricing fires. Real columns priced in as binary (mode bp) or continuous
(mode lp, to validate the pricer reproduces the LP relaxation).

Usage: python _coral_bp.py <lp|bp> [time_limit_s] [K_per_round]
  env: BP_NPY (default _parsed_a320), BP_PUB_LP, BP_PUB_BEST, BP_TAG
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from pyscipopt import Model, Pricer, SCIP_RESULT, SCIP_PARAMSETTING

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CORAL = Path("C:/work/Sandbox/Testing/TimeFold/coral-data")
NPY = CORAL / os.environ.get("BP_NPY", "_parsed_a320")
PUB_LP = float(os.environ.get("BP_PUB_LP", "940.65"))
PUB_BEST = float(os.environ.get("BP_PUB_BEST", "1078"))
TAG = os.environ.get("BP_TAG", "coral_a320")
BIG = 1.0e5


class PoolPricer(Pricer):
    def pricerinit(self):
        self.tcons = [self.model.getTransformedCons(c) for c in self.cons]
        self.center = None      # dual stability center (Neame smoothing)

    def _rc(self, duals, base):
        contrib = duals[self.indices]
        seg = np.add.reduceat(contrib, self.indptr[:-1])
        rc = base - seg
        rc[self.added] = np.inf
        return rc

    def _add(self, cols):
        m = self.model
        for j in cols:
            j = int(j)
            rows = self.indices[self.indptr[j]:self.indptr[j + 1]]
            v = m.addVar(vtype=self.vtype, obj=float(self.costs[j]),
                         lb=0.0, ub=1.0, pricedVar=True)
            for r in rows:
                m.addConsCoeff(self.tcons[int(r)], v, 1.0)
            self.added[j] = True
            # Machine certificate (audit round 6): remember which pool
            # column each priced variable represents, so the incumbent's
            # selected columns can be dumped and re-verified externally.
            self.varmap.append((v, j))

    def _price(self, farkas):
        m = self.model
        self.rounds += 1
        if farkas:
            duals = np.fromiter(
                (m.getDualfarkasLinear(c) for c in self.tcons),
                dtype=np.float64, count=len(self.tcons))
            neg = np.where(self._rc(duals, np.zeros(len(self.costs))) < -1e-6)[0]
            if len(neg):
                self._add(neg[np.argsort(self._rc(duals, np.zeros(len(self.costs)))[neg])[: self.K]])
            return {"result": SCIP_RESULT.SUCCESS}

        # reduced-cost pricing, optionally with Neame dual smoothing.
        duals = np.fromiter(
            (m.getDualsolLinear(c) for c in self.tcons),
            dtype=np.float64, count=len(self.tcons))
        if self.alpha > 0.0:
            if self.center is None:
                self.center = duals.copy()
            smoothed = self.alpha * self.center + (1.0 - self.alpha) * duals
            rc_s = self._rc(smoothed, self.costs)
            neg_s = np.where(rc_s < -1e-6)[0]
            if len(neg_s):                       # smoothed step: add & recenter
                self._add(neg_s[np.argsort(rc_s[neg_s])[: self.K]])
                self.center = smoothed
                return {"result": SCIP_RESULT.SUCCESS}
            # misprice: fall back to ACTUAL duals (correctness of termination)
            self.center = duals.copy()
        rc = self._rc(duals, self.costs)
        neg = np.where(rc < -1e-6)[0]
        if len(neg):
            self._add(neg[np.argsort(rc[neg])[: self.K]])
        return {"result": SCIP_RESULT.SUCCESS}

    def pricerredcost(self):
        return self._price(False)

    def pricerfarkas(self):
        return self._price(True)


def load():
    meta = json.loads((NPY / "meta.json").read_text())
    return (meta["n_rows"],
            np.load(NPY / "costs.npy"),
            np.load(NPY / "indptr.npy"),
            np.load(NPY / "indices.npy"))


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "lp"
    tl = int(sys.argv[2]) if len(sys.argv) > 2 else 1800
    K = int(sys.argv[3]) if len(sys.argv) > 3 else 3000
    alpha = float(sys.argv[4]) if len(sys.argv) > 4 else (
        0.5 if mode == "bp2" else 0.0)   # bp2 = van Rossum smoothing + aggressive heur

    n_rows, costs, indptr, indices = load()
    print(f"=== branch-and-price [{mode}] {TAG}: {n_rows} rows x "
          f"{len(costs):,} cols, TL {tl}s ===", flush=True)

    m = Model(f"bp_{TAG}")
    m.setPresolve(SCIP_PARAMSETTING.OFF)
    m.setSeparating(SCIP_PARAMSETTING.OFF)
    m.setBoolParam("misc/allowstrongdualreds", False)
    m.setBoolParam("misc/allowweakdualreds", False)
    m.setRealParam("limits/time", float(tl))
    aggr = os.environ.get("BP_AGGR", "0") == "1"
    if mode != "lp" and aggr:
        # van Rossum insight probe: emphasise SCIP's built-in primal
        # heuristics (NB: unlike the paper's diving, SCIP heuristics do not
        # re-price columns inside dives — ablation showed this matters).
        m.setHeuristics(SCIP_PARAMSETTING.AGGRESSIVE)

    # artificials (one per row) + partition constraints (=1)
    arts = [m.addVar(vtype="C", obj=BIG, lb=0.0, ub=None, name=f"a{r}")
            for r in range(n_rows)]
    cons = [m.addCons(arts[r] == 1.0, name=f"c{r}", modifiable=True,
                      separate=False)
            for r in range(n_rows)]

    pricer = PoolPricer()
    pricer.cons = cons
    pricer.costs = costs
    pricer.indptr = indptr
    pricer.indices = indices
    pricer.added = np.zeros(len(costs), dtype=bool)
    pricer.K = K
    pricer.vtype = "C" if mode == "lp" else "B"
    pricer.rounds = 0
    pricer.alpha = alpha
    pricer.varmap = []
    m.includePricer(pricer, "poolpricer", "explicit-pool column pricer")

    # Raw solver log for the machine certificate (audit round 6).
    log_p = None
    if os.environ.get("BP_LOG"):
        import _data_paths; log_p = _data_paths.results_dir() \
            / f"{TAG}_bp_{mode}_scip.log"
        m.setLogfile(str(log_p))

    t0 = time.monotonic()
    m.optimize()
    dt = time.monotonic() - t0

    status = m.getStatus()
    obj = m.getObjVal() if m.getNSols() > 0 else None
    dual = m.getDualbound()
    gap = (100.0 * (obj - dual) / obj) if obj else None
    print(f"[{mode}] status={status} obj={obj} dualbound={dual:.2f} "
          f"gap={gap:.2f}% pricing_rounds={pricer.rounds} in {dt:.0f}s",
          flush=True)
    ref = PUB_LP if mode == "lp" else PUB_BEST
    if obj is not None:
        print(f"  vs published {'LP' if mode=='lp' else 'optimum'} {ref}: "
              f"{'MATCH' if abs(obj-ref)<0.5 else f'{obj-ref:+.2f}'}",
              flush=True)

    import _data_paths; out = _data_paths.results_dir() / f"{TAG}_bp.json"
    prior = json.loads(out.read_text()) if out.exists() else []
    key = f"{mode}|a{alpha}|h{'aggr' if aggr else 'def'}"
    prior = [r for r in prior if r.get("key") != key] + [{
        "key": key, "alpha": alpha, "aggressive_heur": aggr,
        "mode": mode, "status": str(status),
        "obj": round(obj, 2) if obj else None,
        "dualbound": round(dual, 2), "gap_pct": round(gap, 2) if gap else None,
        "pricing_rounds": pricer.rounds, "cols_added": int(pricer.added.sum()),
        "time_s": round(dt, 1), "published": ref}]
    out.write_text(json.dumps(prior, indent=1))
    print(f"[saved] {out.name}", flush=True)

    # --- machine certificate of the incumbent (audit round 6) --------------
    # Dump the selected pool columns and self-check coverage/cost; the
    # INDEPENDENT re-verification is _coral_a320_verify.py (pure numpy, no
    # SCIP) over this file + the raw parsed pool.
    if mode != "lp" and m.getNSols() > 0:
        sol = m.getBestSol()
        art_usage = sum(1 for a in arts if m.getSolVal(sol, a) > 1e-6)
        chosen = sorted(int(j) for v, j in pricer.varmap
                        if m.getSolVal(sol, v) > 0.5)
        cover = np.zeros(n_rows, dtype=int)
        cost_sum = 0.0
        for j in chosen:
            cover[indices[indptr[j]:indptr[j + 1]]] += 1
            cost_sum += float(costs[j])
        cert = {
            "tag": TAG, "mode": mode, "key": key,
            "status": str(status), "objective": obj,
            "dualbound": round(dual, 6), "gap_pct": gap,
            "artificials_in_solution": art_usage,
            "selected_columns": chosen,
            "selected_cost_sum": round(cost_sum, 6),
            "rows_total": int(n_rows),
            "rows_covered_exactly_once": int((cover == 1).sum()),
            "partition_ok": bool((cover == 1).all() and art_usage == 0),
            "cost_matches_objective": (obj is not None
                                       and abs(cost_sum - obj) < 1e-4),
            "scip_log": (log_p.name if log_p else None),
            "pool": str(NPY),
        }
        import _data_paths; cp = _data_paths.results_dir() \
            / f"{TAG}_bp_certificate.json"
        cp.write_text(json.dumps(cert, indent=1))
        print(f"[certificate] {cp.name}: {len(chosen)} columns, "
              f"partition_ok={cert['partition_ok']}, "
              f"cost_sum={cost_sum:.2f} vs obj={obj}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
