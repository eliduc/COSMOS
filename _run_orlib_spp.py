"""Option-2 benchmark: our Stage-1 engine (CP-SAT) on OR-Library airline
set-partitioning instances against PROVEN optima.

These are the classic Hoffman-Padberg-era instances built from real airline
crew pairing data (Northwest = sppnw*, American = sppaa*): each column is a
candidate pairing with a cost; each row is a flight that must be covered by
EXACTLY one chosen pairing. Optimal values are published (Hoffman & Padberg
1993; Chu & Beasley 1998; several instances cross-checkable via MIPLIB 2017).

CP-SAT is an exact solver: when it returns OPTIMAL, optimality is proven by
the solver itself; the literature optimum is used as an external cross-check.

File format: first line "n_rows n_cols"; then per column: cost n_rows_covered
r1 r2 ... (row indices 1-based; tokens may wrap across lines).

Usage:
    python _run_orlib_spp.py <file-or-dir> [time_limit_s] [csv_out]
Defaults: time_limit 300s, csv final-report/orlib_spp_results.csv
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from ortools.sat.python import cp_model


def parse_spp(path: Path):
    """Token-stream parse (row lists wrap lines in some files)."""
    tokens = path.read_text().split()
    it = iter(tokens)
    n_rows, n_cols = int(next(it)), int(next(it))
    cols = []
    for _ in range(n_cols):
        cost = int(next(it))
        k = int(next(it))
        rows = [int(next(it)) for _ in range(k)]
        cols.append((cost, rows))
    return n_rows, cols


def solve_spp(n_rows: int, cols, time_limit_s: int, workers: int = 8):
    m = cp_model.CpModel()
    x = [m.new_bool_var(f"c{i}") for i in range(len(cols))]
    by_row = [[] for _ in range(n_rows + 1)]
    for i, (_, rows) in enumerate(cols):
        for r in rows:
            by_row[r].append(x[i])
    for r in range(1, n_rows + 1):
        m.add_exactly_one(by_row[r])
    m.minimize(sum(c * xi for (c, _), xi in zip(cols, x)))
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = float(time_limit_s)
    s.parameters.num_search_workers = workers
    s.parameters.random_seed = 0
    t0 = time.monotonic()
    status = s.solve(m)
    elapsed = time.monotonic() - t0
    name = s.status_name(status)
    obj = int(s.objective_value) if name in ("OPTIMAL", "FEASIBLE") else None
    bound = s.best_objective_bound if name in ("OPTIMAL", "FEASIBLE") else None
    return name, obj, bound, elapsed


def upsert_csv_rows(out: Path, fields: list[str], new_rows: list[dict],
                    key: str = "instance") -> None:
    """Latest-run-wins CSV policy (audit 2026-07-12): keep exactly one row
    per `key` value.  Existing rows are loaded, rows with the same key are
    replaced by the new run, and the file is rewritten atomically — a
    re-run updates results instead of appending duplicates."""
    existing: dict[str, dict] = {}
    if out.exists():
        with out.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing[row[key]] = row
    for row in new_rows:
        existing[str(row[key])] = {k: row.get(k) for k in fields}
    tmp = out.with_suffix(out.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(existing.values())
    tmp.replace(out)


def main():
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    out = Path(sys.argv[3]) if len(sys.argv) > 3 else \
        __import__("_data_paths").results_dir() / "orlib_spp_results.csv"
    if target is None:
        print(__doc__)
        return 2

    files = sorted(target.glob("spp*")) if target.is_dir() else [target]
    out.parent.mkdir(exist_ok=True)
    fields = ["instance", "rows", "cols", "status", "objective",
              "bound", "seconds"]
    print(f"{'instance':<12}{'rows':>6}{'cols':>8}{'status':>10}"
          f"{'objective':>12}{'sec':>8}", flush=True)
    for path in files:
        try:
            n_rows, cols = parse_spp(path)
        except Exception as e:  # noqa: BLE001
            print(f"{path.stem:<12} PARSE FAIL: {e}", flush=True)
            continue
        status, obj, bound, sec = solve_spp(n_rows, cols, limit)
        row = {"instance": path.stem, "rows": n_rows, "cols": len(cols),
               "status": status, "objective": obj, "bound": bound,
               "seconds": round(sec, 2)}
        # Rewrite after every instance so partial progress survives a crash.
        upsert_csv_rows(out, fields, [row])
        print(f"{path.stem:<12}{n_rows:>6}{len(cols):>8}{status:>10}"
              f"{str(obj):>12}{sec:>8.2f}", flush=True)
    print(f"\nCSV: {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
