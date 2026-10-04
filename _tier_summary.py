"""Print one-line ``per-tier`` summary of the running benchmark.

Used by the chat monitor: when the cursor crosses the boundary between
timeout tiers, we run this to print a compact table of best-soft-per-cell.
"""
import csv
import sys
from collections import defaultdict
from pathlib import Path

CSV = Path(__file__).resolve().parent / "benchmark_results.csv"
ENGINES = ["timefold_python", "ortools", "timefold_kotlin",
           "timefold_kotlin_comprehensive", "solverforge"]
ELABELS = {
    "timefold_python":               "TF-Py",
    "ortools":                       "OR-Tools",
    "timefold_kotlin":               "TF-Kot",
    "timefold_kotlin_comprehensive": "TF-Kot+4",
    "solverforge":                   "SForge",
}
DATASETS = [
    "S1 (tiny)", "S2 (small)", "S3 (small-med)",
    "S4 (medium)", "S5 (medium-lg)", "S6 (large)",
]


def fmt(r):
    if r is None:
        return "—"
    h, s = r["hard"], r["soft"]
    if r["finish_reason"] == "watchdog":
        return "wdog"
    if h == "0":
        return f"{int(s)}"
    if h == "" or s == "":
        return "—"
    return f"{h}h"


def main():
    if not CSV.exists():
        print("(no CSV yet)")
        return

    with CSV.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    by_t = defaultdict(list)
    for r in rows:
        by_t[int(r["timeout_s"])].append(r)
    if not by_t:
        return

    # Index for lookup
    M = {(int(r["timeout_s"]), r["dataset_label"], r["engine"]): r for r in rows}

    completed_tiers = []
    for t in sorted(by_t):
        cells = by_t[t]
        # tier complete if all 24 cells (4 engines × 6 datasets) exist for
        # the engines we currently care about — historical CSV may still
        # carry rows from retired engines (e.g. timefold_java) so we filter.
        keys = {(c["dataset_label"], c["engine"]) for c in cells
                 if c["engine"] in ENGINES}
        if len(keys) == len(ENGINES) * len(DATASETS):
            completed_tiers.append(t)

    if not completed_tiers:
        print("(no tier complete yet)")
        return

    for t in completed_tiers:
        print(f"\n=== Tier {t}s ===")
        head = f"{'dataset':<14}"
        for e in ENGINES:
            head += f" {ELABELS[e]:>10}"
        print(head)
        print("-" * len(head))
        for ds in DATASETS:
            row = f"{ds:<14}"
            for e in ENGINES:
                row += f" {fmt(M.get((t, ds, e))):>10}"
            print(row)


if __name__ == "__main__":
    main()
