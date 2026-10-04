"""COR@L 727 column-scale test: 342 rows x 12,618,766 columns airline crew
set-partitioning instance (Linderoth-Lee-Savelsbergh). Published: LP
relaxation 637.25, best-known integer 1108.86 (NOT proven optimal — huge
integrality gap; this is a hard instance).

Goal: test whether our open-source stack loads and solves a set-partitioning
model two orders of magnitude larger (by columns) than GERAD (~thousands),
without branch-and-price. We report, for each solver:
  - LP relaxation value (must match 637.25 -> validates parsing) and time;
  - best integer objective within a time budget and its status.

Loading uses compact CSC arrays (array module) to avoid 12.6M Python
objects. HiGHS is fed via HighsLp (single passModel, no per-var calls).

Format per line: "<cost> <row> <row> ..." (row indices of covered legs).

Usage: python _coral727_scale.py [lp_only|mip] [budget_s]
"""
from __future__ import annotations

import array
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CORAL = Path("C:/work/Sandbox/Testing/TimeFold/coral-data")
FILES = [CORAL / f"727pairings.{n}.txt" for n in range(40, 49)]
PUB_LP = 637.25
PUB_BEST = 1108.86


def parse():
    """Return (n_rows, costs[array d], indptr[array q], indices[array i]).
    Row indices are stored as-is (0-based detected); indptr[c]..indptr[c+1]
    slices indices for column c."""
    costs = array.array("d")
    indices = array.array("i")
    indptr = array.array("q", [0])
    max_row = -1
    min_row = 1 << 30
    t0 = time.monotonic()
    for f in FILES:
        with open(f, "r") as fh:
            for line in fh:
                parts = line.split()
                if not parts:
                    continue
                costs.append(float(parts[0]))
                for tok in parts[1:]:
                    r = int(tok)
                    indices.append(r)
                    if r > max_row:
                        max_row = r
                    if r < min_row:
                        min_row = r
                indptr.append(len(indices))
        print(f"  parsed {f.name}: cols so far {len(costs):,}, "
              f"nnz {len(indices):,} ({time.monotonic()-t0:.0f}s)", flush=True)
    n_rows = max_row - min_row + 1
    if min_row == 1:                       # normalize to 0-based
        for i in range(len(indices)):
            indices[i] -= 1
    print(f"[parse] cols={len(costs):,} rows={n_rows} "
          f"(idx {min_row}..{max_row}) nnz={len(indices):,} "
          f"in {time.monotonic()-t0:.0f}s", flush=True)
    return n_rows, costs, indptr, indices


def build_lp(n_rows, costs, indptr, indices, integer):
    import highspy
    import numpy as np
    lp = highspy.HighsLp()
    n = len(costs)
    lp.num_col_ = n
    lp.num_row_ = n_rows
    lp.col_cost_ = np.frombuffer(costs, dtype=np.float64)
    lp.col_lower_ = np.zeros(n, dtype=np.float64)
    lp.col_upper_ = np.ones(n, dtype=np.float64)
    lp.row_lower_ = np.ones(n_rows, dtype=np.float64)   # exact cover: = 1
    lp.row_upper_ = np.ones(n_rows, dtype=np.float64)
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_ = np.frombuffer(indptr, dtype=np.int64)
    lp.a_matrix_.index_ = np.frombuffer(indices, dtype=np.int32)
    lp.a_matrix_.value_ = np.ones(len(indices), dtype=np.float64)
    if integer:
        lp.integrality_ = np.full(
            n, int(highspy.HighsVarType.kInteger), dtype=np.int32)
    return lp


def run_highs(n_rows, costs, indptr, indices, integer, budget):
    import highspy
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(budget))
    t0 = time.monotonic()
    lp = build_lp(n_rows, costs, indptr, indices, integer)
    h.passModel(lp)
    t_build = time.monotonic() - t0
    t1 = time.monotonic()
    h.run()
    t_solve = time.monotonic() - t1
    obj = h.getObjectiveValue()
    status = str(h.getModelStatus())
    return {"obj": obj, "status": status, "build_s": round(t_build, 1),
            "solve_s": round(t_solve, 1)}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "lp_only"
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 900
    print(f"=== COR@L 727 scale test [{mode}, budget {budget}s] ===",
          flush=True)
    print(f"published: LP={PUB_LP}, best-known integer={PUB_BEST} "
          f"(not proven optimal)", flush=True)

    n_rows, costs, indptr, indices = parse()

    print("\n[HiGHS] LP relaxation ...", flush=True)
    lp_res = run_highs(n_rows, costs, indptr, indices, False, budget)
    lp_ok = abs(lp_res["obj"] - PUB_LP) < 0.5
    print(f"  LP obj={lp_res['obj']:.2f} [{lp_res['status']}] "
          f"build {lp_res['build_s']}s + solve {lp_res['solve_s']}s  "
          f"{'MATCHES 637.25' if lp_ok else 'DIFFERS from 637.25'}",
          flush=True)

    result = {"n_cols": len(costs), "n_rows": n_rows, "nnz": len(indices),
              "published_lp": PUB_LP, "published_best": PUB_BEST,
              "highs_lp": lp_res, "lp_match": lp_ok}

    if mode == "mip":
        print("\n[HiGHS] MIP (integer) ...", flush=True)
        mip_res = run_highs(n_rows, costs, indptr, indices, True, budget)
        gap = (100.0 * (mip_res["obj"] - PUB_LP) / PUB_LP
               if mip_res["obj"] else None)
        print(f"  MIP obj={mip_res['obj']:.2f} [{mip_res['status']}] "
              f"build {mip_res['build_s']}s + solve {mip_res['solve_s']}s  "
              f"vs best-known {PUB_BEST}", flush=True)
        result["highs_mip"] = mip_res

    import json
    import _data_paths; out = _data_paths.results_dir() / "coral727_scale.json"
    out.write_text(json.dumps(result, indent=1))
    print(f"\n[saved] {out.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
