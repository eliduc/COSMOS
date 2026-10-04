"""Independent machine verification of the A320 branch-and-price
certificate (audit 2026-07-12, round 6).

Reads final-report/coral_a320_bp_certificate.json and the RAW parsed pool
(costs.npy / indptr.npy / indices.npy) and re-checks, without SCIP:
  - every selected column id is a valid pool column;
  - the selected columns cover every row EXACTLY once (set partitioning);
  - the summed pool costs equal the claimed objective;
  - no artificial variables were used.
Exit code 0 only if every check passes.

Usage: python _coral_a320_verify.py [certificate.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def main() -> int:
    cert_p = (Path(sys.argv[1]) if len(sys.argv) > 1
              else HERE / "final-report" / "coral_a320_bp_certificate.json")
    cert = json.loads(cert_p.read_text())
    npy = Path(cert["pool"])
    costs = np.load(npy / "costs.npy")
    indptr = np.load(npy / "indptr.npy")
    indices = np.load(npy / "indices.npy")
    n_rows = cert["rows_total"]
    chosen = cert["selected_columns"]

    ok = True
    if not all(0 <= j < len(costs) for j in chosen):
        print("FAIL: selected column id out of pool range")
        ok = False
    cover = np.zeros(n_rows, dtype=int)
    cost_sum = 0.0
    for j in chosen:
        cover[indices[indptr[j]:indptr[j + 1]]] += 1
        cost_sum += float(costs[j])
    if not (cover == 1).all():
        print(f"FAIL: partition violated — {(cover == 0).sum()} rows "
              f"uncovered, {(cover > 1).sum()} rows covered more than once")
        ok = False
    if cert.get("artificials_in_solution", 1) != 0:
        print("FAIL: artificial variables present in the solution")
        ok = False
    if abs(cost_sum - cert["objective"]) > 1e-4:
        print(f"FAIL: cost sum {cost_sum} != claimed objective "
              f"{cert['objective']}")
        ok = False
    print(f"columns={len(chosen)} rows={n_rows} "
          f"cover==1 everywhere: {(cover == 1).all()} "
          f"cost_sum={cost_sum:.6f} objective={cert['objective']} "
          f"dualbound={cert['dualbound']}")
    print("INDEPENDENT VERIFICATION:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
