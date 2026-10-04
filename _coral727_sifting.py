"""COR@L 727 LP via SIFTING (column generation over the explicit 12.6M-column
pool) — the on-thesis punchline: monolithic simplex/interior-point/first-order
methods all time out on the raw 342 x 12,618,766 LP, but the industrial
technique that DOES solve it (sifting = column generation over given columns)
is exactly our GERAD approach, just over a pre-enumerated pool instead of a
DFS generator.

Master: HiGHS LP over a small working set of columns + artificial big-M
columns (one per row) guaranteeing feasibility. Pricing: reduced cost of
ALL 12.6M columns computed vectorized in numpy
    rc_j = cost_j - sum_{r in col j} dual_r
via segment sums over the CSC (indptr, indices). Add the most-negative K,
repeat until min reduced cost >= -eps => LP optimum proven (should reach
the published 637.25).

Usage: python _coral727_sifting.py [K_per_iter] [max_iters]
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import highspy

import os
CORAL = Path("C:/work/Sandbox/Testing/TimeFold/coral-data")
NPY = CORAL / os.environ.get("SIFT_NPY", "_parsed")
PUB_LP = float(os.environ.get("SIFT_PUB_LP", "637.25"))
PUB_BEST = float(os.environ.get("SIFT_PUB_BEST", "1108.86"))
TAG = os.environ.get("SIFT_TAG", "coral727")
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def load():
    meta = json.loads((NPY / "meta.json").read_text())
    return (meta["n_rows"],
            np.load(NPY / "costs.npy"),
            np.load(NPY / "indptr.npy"),
            np.load(NPY / "indices.npy"))


def price_all(costs, indptr, indices, duals):
    """Vectorized reduced cost of every column: rc_j = c_j - sum dual[rows_j].
    Uses reduceat over the CSC index segments."""
    contrib = duals[indices]                       # dual for each nonzero
    # segment sum per column via reduceat on indptr[:-1]
    seg = np.add.reduceat(contrib, indptr[:-1])
    # columns with zero nonzeros would misbehave, but every column covers >=1
    return costs - seg


def solve_master(n_rows, cols_cost, cols_rows_flat, cols_indptr, big_m,
                 covering=False):
    """LP over working columns + one artificial per row (cost big_m)."""
    n_work = len(cols_cost)
    n = n_work + n_rows
    lp = highspy.HighsLp()
    lp.num_col_ = n
    lp.num_row_ = n_rows
    cost = np.concatenate([cols_cost, np.full(n_rows, big_m)])
    lp.col_cost_ = cost
    lp.col_lower_ = np.zeros(n)
    lp.col_upper_ = np.full(n, highspy.kHighsInf)
    lp.row_lower_ = np.ones(n_rows)
    lp.row_upper_ = (np.full(n_rows, highspy.kHighsInf) if covering
                     else np.ones(n_rows))
    # build CSC: working columns then identity artificials
    start = np.empty(n + 1, dtype=np.int64)
    start[:n_work + 1] = cols_indptr
    base = cols_indptr[-1]
    for a in range(n_rows):
        start[n_work + a] = base + a
    start[n] = base + n_rows
    index = np.concatenate([cols_rows_flat,
                            np.arange(n_rows, dtype=np.int32)])
    value = np.ones(len(index))
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_ = start
    lp.a_matrix_.index_ = index
    lp.a_matrix_.value_ = value
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.run() if False else None
    h.passModel(lp)
    h.run()
    obj = h.getObjectiveValue()
    sol = h.getSolution()
    duals = np.array(sol.row_dual, dtype=np.float64)
    return obj, duals, str(h.getModelStatus())


def main():
    K = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    max_iters = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    covering = (len(sys.argv) > 3 and sys.argv[3] == "covering")
    n_rows, costs, indptr, indices = load()
    n_cols = len(costs)
    print(f"=== 727 sifting LP [{'covering >=1' if covering else 'partition =1'}]"
          f": {n_rows} rows x {n_cols:,} cols ===", flush=True)
    big_m = float(costs.max() * n_rows + 1)

    # seed working set: cheapest 2*K columns by cost
    order = np.argsort(costs)[: 2 * K]
    work = set(int(i) for i in order)
    t0 = time.monotonic()
    lp_obj = None
    for it in range(max_iters):
        wl = np.fromiter(work, dtype=np.int64)
        # build working CSC
        seg_len = indptr[wl + 1] - indptr[wl]
        w_indptr = np.empty(len(wl) + 1, dtype=np.int64)
        w_indptr[0] = 0
        np.cumsum(seg_len, out=w_indptr[1:])
        w_index = np.empty(int(w_indptr[-1]), dtype=np.int32)
        pos = 0
        for c in wl:
            s, e = indptr[c], indptr[c + 1]
            w_index[pos:pos + (e - s)] = indices[s:e]
            pos += e - s
        w_cost = costs[wl]

        t1 = time.monotonic()
        lp_obj, duals, st = solve_master(n_rows, w_cost, w_index,
                                         w_indptr, big_m, covering)
        t_master = time.monotonic() - t1

        t2 = time.monotonic()
        rc = price_all(costs, indptr, indices, duals)
        min_rc = float(rc.min())
        t_price = time.monotonic() - t2

        neg = np.where(rc < -1e-6)[0]
        print(f"  it {it}: LP {lp_obj:,.2f} [{st}] work {len(work):,} "
              f"min_rc {min_rc:.3f} neg {len(neg):,} "
              f"master {t_master:.1f}s price {t_price:.1f}s", flush=True)
        if len(neg) == 0:
            print(f"  CONVERGED — LP optimum {lp_obj:,.2f} proven", flush=True)
            break
        # add the K most-negative
        worst = neg[np.argsort(rc[neg])[:K]]
        for c in worst:
            work.add(int(c))

    total = time.monotonic() - t0
    match = abs(lp_obj - PUB_LP) < 0.5 if lp_obj else False
    print(f"[sifting] LP {lp_obj:,.2f} vs published {PUB_LP} "
          f"{'MATCH' if match else 'DIFF'} in {total:.0f}s, "
          f"{len(work):,} working columns", flush=True)

    # Validate model against the reliable published INTEGER best-known
    # (1108.86): solve the partition IP over the final working columns.
    if not covering:
        wl = np.fromiter(work, dtype=np.int64)
        seg_len = indptr[wl + 1] - indptr[wl]
        w_indptr = np.empty(len(wl) + 1, dtype=np.int64)
        w_indptr[0] = 0
        np.cumsum(seg_len, out=w_indptr[1:])
        w_index = np.empty(int(w_indptr[-1]), dtype=np.int32)
        pos = 0
        for c in wl:
            s, e = indptr[c], indptr[c + 1]
            w_index[pos:pos + (e - s)] = indices[s:e]
            pos += e - s
        nW = len(wl)
        lp2 = highspy.HighsLp()
        lp2.num_col_, lp2.num_row_ = nW, n_rows
        lp2.col_cost_ = costs[wl]
        lp2.col_lower_ = np.zeros(nW)
        lp2.col_upper_ = np.ones(nW)
        lp2.row_lower_ = np.ones(n_rows)
        lp2.row_upper_ = np.ones(n_rows)
        lp2.a_matrix_.format_ = highspy.MatrixFormat.kColwise
        lp2.a_matrix_.start_ = w_indptr
        lp2.a_matrix_.index_ = w_index
        lp2.a_matrix_.value_ = np.ones(len(w_index))
        lp2.integrality_ = [highspy.HighsVarType.kInteger] * nW
        h = highspy.Highs()
        h.setOptionValue("output_flag", False)
        h.setOptionValue("time_limit", 300.0)
        h.passModel(lp2)
        h.run()
        ip = h.getObjectiveValue()
        print(f"[IP over {nW:,} working cols] {ip:.2f} "
              f"[{h.getModelStatus()}] vs best-known {PUB_BEST}", flush=True)
    import _data_paths; out = _data_paths.results_dir() / f"{TAG}_sifting.json"
    out.write_text(json.dumps({"lp_obj": round(lp_obj, 2), "published": PUB_LP,
                               "match": match, "total_s": round(total, 1),
                               "working_cols": len(work),
                               "iters": it + 1}, indent=1))
    print(f"[saved] {out.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
