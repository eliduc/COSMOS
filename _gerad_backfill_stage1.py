"""Backfill canonical stage-1 records for inst1-4 (audit 2026-07-12,
round 5, item 6): the canonical file covered only inst5-7; the earlier
aligned-space battery results for inst1-4 lived in legacy files.

Sources (no new computation — a faithful transcription):
  - final-report/gerad_colgen_results_duty.json  (battery rows: LP bound,
    validity, gap, IP objective/status, pool size, wall time)
  - final-report/gerad_colgen_selected_inst{i}.json  (selected catalog:
    pairing count, overcoverage; verified to cover ALL raw legs)
  - data/gerad/payloads/inst{i}_p2.json  (raw legs, coverage check)

Writes final-report/stage1/inst{i}.json for i in 1..4. The merged
gerad_stage1_results.json is rebuilt separately by aggregate_stage1()
(kept out of this script to avoid racing a live completion run).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
import _data_paths
FR = _data_paths.results_dir(create=False)

SPACE = {"bases": ["BASE1", "BASE2", "BASE3"], "pricer": "duty",
         "max_landings": 5, "min_sit_min": 30, "dead_zone": False,
         "ip_model": "set_covering"}


def main() -> int:
    rows = {r["inst"]: r for r in
            json.loads((FR / "gerad_colgen_results_duty.json").read_text())
            if r.get("inst") in (1, 2, 3, 4)}
    inst_dir = FR / "stage1"
    inst_dir.mkdir(exist_ok=True)
    for i in (1, 2, 3, 4):
        r = rows[i]
        sel = json.loads(
            (FR / f"gerad_colgen_selected_inst{i}.json").read_text())
        payload = json.loads(
            (HERE / "data" / "gerad" / "payloads" / f"inst{i}_p2.json")
            .read_text(encoding="utf-8"))
        required = {f["flightNumber"] for f in payload["flights"]}
        covered = {fn for p in sel["selected"] for fn in p["fns"]} & required
        missing = sorted(required - covered)
        if missing:
            raise SystemExit(f"inst{i}: {len(missing)} legs NOT covered by "
                             f"the catalog — backfill must not fake this")
        lp_valid = bool(r.get("lp_bound_valid"))
        rec = {
            "schema": "stage1/v2",
            "label": f"inst{i}",
            "space": SPACE,
            "initial_budget_result": {
                "lp_bound": r.get("lp_bound"),
                "lp_bound_valid": lp_valid,
                "gap_vs_lp_pct": (r.get("gap_vs_lp_pct")
                                  if lp_valid else None),
                "ip_objective": r.get("ip_objective"),
                "ip_status": r.get("ip_status"),
                "selected": len(sel["selected"]),
                "overcovered_legs": sel.get("overcovered_legs"),
                # Uniform schema (audit round 6): always a LIST of leg
                # names, never a count.
                "budget_missing_legs": [],
                "seeded_witness_columns": False,
                "pool_size": r.get("pool_size"),
                "pool_dump": None,
                "pricing_workers": r.get("pricing_workers"),
                "total_s": r.get("total_s"),
            },
            # No missing legs -> nothing to certify, nothing to complete.
            "feasibility_certification": {},
            "completed_result": None,
            "backfilled_from": ["gerad_colgen_results_duty.json",
                                f"gerad_colgen_selected_inst{i}.json"],
        }
        out = inst_dir / f"inst{i}.json"
        out.write_text(json.dumps(rec, indent=1))
        print(f"inst{i}: ip={rec['initial_budget_result']['ip_objective']} "
              f"({rec['initial_budget_result']['ip_status']}), "
              f"coverage {len(covered)}/{len(required)} -> {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
