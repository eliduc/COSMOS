"""COR@L 727 (342 rows x 12,618,766 cols) — exact-solver scale test across
ALL open-source exact engines in our stack, one engine per process.

Why only exact engines: the columns are PRE-GENERATED, so this is the
column-SELECTION task (our 'OR-T' half). Timefold (metaheuristic) and
column generation are not applicable to a pre-enumerated set-partitioning
benchmark — CG has nothing to generate, and TF on 12.6M selection vars is
the OR-Library-SPP finding (task 1) at 400x scale. So we compare the exact
engines: HiGHS, OR-Tools GLOP (LP), OR-Tools CP-SAT (MIP), SCIP (MIP).

Published: LP relaxation 637.25, best-known integer 1108.86 (a HARD
instance — the best-known is not proven optimal).

Subcommands:
  parse                 parse the 9 text files -> cached .npy arrays (once)
  highs_lp | highs_mip  HiGHS via matrix passModel (efficient at scale)
  glop                  OR-Tools GLOP LP relaxation (per-var Python API)
  cpsat  [budget]       OR-Tools CP-SAT MIP
  scip   [budget]       OR-Tools SCIP MIP
Each writes a line into final-report/coral727_engines.json.

Usage: python _coral727_engines.py <subcommand> [budget_s]
"""
from __future__ import annotations

import array
import json
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import os
CORAL = Path("C:/work/Sandbox/Testing/TimeFold/coral-data")
FILES = [CORAL / f"727pairings.{n}.txt" for n in range(40, 49)]
NPY = CORAL / os.environ.get("ENG_NPY", "_parsed")
PUB_LP = float(os.environ.get("ENG_PUB_LP", "637.25"))
PUB_BEST = float(os.environ.get("ENG_PUB_BEST", "1108.86"))
_TAG = os.environ.get("ENG_TAG", "coral727")
import _data_paths
RESULT = _data_paths.results_dir(create=False) / f"{_TAG}_engines.json"


def do_parse():
    import numpy as np
    costs = array.array("d")
    indices = array.array("i")
    indptr = array.array("q", [0])
    max_row = -1
    min_row = 1 << 30
    t0 = time.monotonic()
    for f in FILES:
        with open(f) as fh:
            for line in fh:
                p = line.split()
                if not p:
                    continue
                costs.append(float(p[0]))
                for tok in p[1:]:
                    r = int(tok)
                    indices.append(r)
                    if r > max_row:
                        max_row = r
                    if r < min_row:
                        min_row = r
                indptr.append(len(indices))
    idx = np.frombuffer(indices, dtype=np.int32).copy()
    if min_row == 1:
        idx -= 1
    n_rows = max_row - min_row + 1
    NPY.mkdir(exist_ok=True)
    np.save(NPY / "costs.npy", np.frombuffer(costs, dtype=np.float64).copy())
    np.save(NPY / "indptr.npy", np.frombuffer(indptr, dtype=np.int64).copy())
    np.save(NPY / "indices.npy", idx)
    (NPY / "meta.json").write_text(json.dumps(
        {"n_rows": int(n_rows), "n_cols": len(costs), "nnz": len(idx)}))
    print(f"[parse] cols={len(costs):,} rows={n_rows} nnz={len(idx):,} "
          f"in {time.monotonic()-t0:.0f}s -> cached", flush=True)


def load():
    import numpy as np
    meta = json.loads((NPY / "meta.json").read_text())
    return (meta["n_rows"],
            np.load(NPY / "costs.npy"),
            np.load(NPY / "indptr.npy"),
            np.load(NPY / "indices.npy"))


def save_result(row):
    try:
        prior = json.loads(RESULT.read_text())
    except Exception:
        prior = []
    prior = [r for r in prior if r.get("engine") != row["engine"]] + [row]
    RESULT.write_text(json.dumps(prior, indent=1))
    print(f"[saved] {RESULT.name}: {row['engine']}", flush=True)


def run_highs(integer, budget, method="default"):
    import highspy
    import numpy as np
    n_rows, costs, indptr, indices = load()
    n = len(costs)
    lp = highspy.HighsLp()
    lp.num_col_, lp.num_row_ = n, n_rows
    lp.col_cost_ = costs
    lp.col_lower_ = np.zeros(n)
    lp.col_upper_ = np.ones(n)
    lp.row_lower_ = np.ones(n_rows)
    lp.row_upper_ = np.ones(n_rows)
    lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
    lp.a_matrix_.start_ = indptr
    lp.a_matrix_.index_ = indices
    lp.a_matrix_.value_ = np.ones(len(indices))
    if integer:
        lp.integrality_ = [highspy.HighsVarType.kInteger] * n
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("time_limit", float(budget))
    if method in ("ipm", "pdlp"):
        h.setOptionValue("solver", method)
        if method == "ipm":
            h.setOptionValue("run_crossover", "off")
    t0 = time.monotonic()
    h.passModel(lp)
    tb = time.monotonic() - t0
    t1 = time.monotonic()
    h.run()
    ts = time.monotonic() - t1
    tag = {"default": "", "ipm": "-IPM", "pdlp": "-PDLP"}[method]
    eng = ("HiGHS-MIP" if integer else "HiGHS-LP") + tag
    obj = h.getObjectiveValue()
    save_result({"engine": eng, "obj": round(obj, 2),
                 "status": str(h.getModelStatus()),
                 "build_s": round(tb, 1), "solve_s": round(ts, 1),
                 "ref": PUB_BEST if integer else PUB_LP})
    print(f"[{eng}] obj={obj:.2f} [{h.getModelStatus()}] "
          f"build {tb:.0f}s solve {ts:.0f}s", flush=True)


def run_ortools(kind, budget):
    from ortools.linear_solver import pywraplp
    n_rows, costs, indptr, indices = load()
    n = len(costs)
    backend = {"glop": "GLOP", "scip": "SCIP"}[kind]
    s = pywraplp.Solver.CreateSolver(backend)
    if s is None:
        save_result({"engine": backend, "error": "solver-unavailable"})
        return
    t0 = time.monotonic()
    if kind == "glop":
        xs = [s.NumVar(0.0, 1.0, "") for _ in range(n)]
    else:
        xs = [s.IntVar(0, 1, "") for _ in range(n)]
    # constraints: sum of cols covering row r == 1
    cons = [s.Constraint(1.0, 1.0) for _ in range(n_rows)]
    ind = indices
    for c in range(n):
        for k in range(indptr[c], indptr[c + 1]):
            cons[ind[k]].SetCoefficient(xs[c], 1.0)
    obj = s.Objective()
    for c in range(n):
        obj.SetCoefficient(xs[c], float(costs[c]))
    obj.SetMinimization()
    tb = time.monotonic() - t0
    s.SetTimeLimit(int(budget * 1000))
    t1 = time.monotonic()
    st = s.Solve()
    ts = time.monotonic() - t1
    names = {pywraplp.Solver.OPTIMAL: "OPTIMAL",
             pywraplp.Solver.FEASIBLE: "FEASIBLE",
             pywraplp.Solver.INFEASIBLE: "INFEASIBLE",
             pywraplp.Solver.ABNORMAL: "ABNORMAL",
             pywraplp.Solver.NOT_SOLVED: "NOT_SOLVED"}
    eng = "OR-Tools-GLOP" if kind == "glop" else "OR-Tools-SCIP"
    val = s.Objective().Value() if st in (pywraplp.Solver.OPTIMAL,
                                          pywraplp.Solver.FEASIBLE) else None
    save_result({"engine": eng, "obj": round(val, 2) if val else None,
                 "status": names.get(st, str(st)),
                 "build_s": round(tb, 1), "solve_s": round(ts, 1),
                 "ref": PUB_LP if kind == "glop" else PUB_BEST})
    print(f"[{eng}] obj={val} [{names.get(st)}] build {tb:.0f}s solve {ts:.0f}s",
          flush=True)


def run_cpsat(budget):
    from ortools.sat.python import cp_model
    n_rows, costs, indptr, indices = load()
    n = len(costs)
    t0 = time.monotonic()
    m = cp_model.CpModel()
    x = [m.NewBoolVar("") for _ in range(n)]
    rows = [[] for _ in range(n_rows)]
    for c in range(n):
        for k in range(indptr[c], indptr[c + 1]):
            rows[indices[k]].append(x[c])
    for r in range(n_rows):
        m.AddExactlyOne(rows[r])
    m.Minimize(sum(int(costs[c]) * x[c] for c in range(n)))
    tb = time.monotonic() - t0
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(budget)
    solver.parameters.num_search_workers = 8
    t1 = time.monotonic()
    st = solver.Solve(m)
    ts = time.monotonic() - t1
    names = {cp_model.OPTIMAL: "OPTIMAL", cp_model.FEASIBLE: "FEASIBLE",
             cp_model.INFEASIBLE: "INFEASIBLE", cp_model.UNKNOWN: "UNKNOWN"}
    val = int(solver.ObjectiveValue()) if st in (cp_model.OPTIMAL,
                                                 cp_model.FEASIBLE) else None
    save_result({"engine": "OR-Tools-CP-SAT", "obj": val,
                 "status": names.get(st, str(st)),
                 "build_s": round(tb, 1), "solve_s": round(ts, 1),
                 "ref": PUB_BEST})
    print(f"[CP-SAT] obj={val} [{names.get(st)}] build {tb:.0f}s solve {ts:.0f}s",
          flush=True)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "parse"
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 900
    if cmd == "parse":
        do_parse()
    elif cmd == "highs_lp":
        run_highs(False, budget)
    elif cmd == "highs_lp_ipm":
        run_highs(False, budget, "ipm")
    elif cmd == "highs_lp_pdlp":
        run_highs(False, budget, "pdlp")
    elif cmd == "highs_mip":
        run_highs(True, budget)
    elif cmd == "glop":
        run_ortools("glop", budget)
    elif cmd == "scip":
        run_ortools("scip", budget)
    elif cmd == "cpsat":
        run_cpsat(budget)
    else:
        print("unknown:", cmd)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
