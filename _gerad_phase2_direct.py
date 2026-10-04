"""Phase 2, config «прямая» (direct Timefold rostering) under the GERAD2
personalized profile: vacations (soft 1000), preferred flights (reward
100), uncovered legs (soft 10000) on top of core GERAD legality.

Per instance: fresh isolated JVM, POST instN_p2.json with rules=GERAD2,
save the full solution (for later uniform re-scoring) and compute the
personalization quality metrics that Kasirzadeh's thesis reports:
  - preference hits: assignments of a leg the crew member asked for
    (and the max attainable = each leg counted if ANY employee prefers it)
  - vacation violations: assignments touching a requested vacation day
  - coverage: unassigned slots

Usage: python _gerad_phase2_direct.py [inst_from] [inst_to] [budget_s] [seed]
"""
from __future__ import annotations

import copy
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx  # noqa: E402
from flight_crew_scheduling.benchmark import KOTLIN_SOLVER_JAR  # noqa: E402
from _compr_isolated import IsolatedJvmFleet, ts  # noqa: E402

SCRATCH = Path(__file__).resolve().parent / "data" / "gerad"
import os  # noqa: E402
PORT = int(os.environ.get("PHASE2_PORT", "9097"))


def _rid(v, key):
    return v.get(key) if isinstance(v, dict) else v


def personalization_metrics(sol: dict) -> dict:
    flights = {f["flightNumber"]: f for f in sol["flights"]}
    emp_by_id = {}
    for e in sol["employees"]:
        emp_by_id[e["id"]] = e
    pref_of = {eid: set(e.get("preferredFlights") or ())
               for eid, e in emp_by_id.items()}
    vac_of = {eid: set(e.get("preferredDaysOff") or ())
              for eid, e in emp_by_id.items()}

    unassigned = 0
    pref_hits = 0
    vac_hits = 0
    for a in sol["flightAssignments"]:
        eid = _rid(a.get("employee"), "id")
        fn = _rid(a.get("flight"), "flightNumber")
        if eid is None:
            unassigned += 1
            continue
        if fn in pref_of.get(eid, ()):
            pref_hits += 1
        f = flights[fn]
        dep_d = f["departureUTCDateTime"][:10]
        arr_d = f["arrivalUTCDateTime"][:10]
        vacs = vac_of.get(eid, ())
        if dep_d in vacs or arr_d in vacs:
            vac_hits += 1

    # Attainability ceiling: a leg counts if at least one employee prefers
    # it (an upper bound — qualification/овerlap may make some infeasible).
    prefed_legs = set()
    for s in pref_of.values():
        prefed_legs |= s
    return {"unassigned": unassigned, "pref_hits": pref_hits,
            "pref_attainable_legs": len(prefed_legs & set(flights)),
            "vacation_violations": vac_hits}


def main():
    lo = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    hi = int(sys.argv[2]) if len(sys.argv) > 2 else 7
    budget = int(sys.argv[3]) if len(sys.argv) > 3 else 600
    seed = int(sys.argv[4]) if len(sys.argv) > 4 else 7

    import _data_paths; results_path = _data_paths.results_dir() / "gerad_phase2_direct.json"
    n_not_ok = 0
    for inst in range(lo, hi + 1):
        payload = json.loads((SCRATCH / "payloads" / f"inst{inst}_p2.json")
                             .read_text(encoding="utf-8"))
        n = len(payload["flights"])
        print(f"=== phase2 direct (GERAD2): inst{inst} ({n} legs), "
              f"LA seed {seed}, {budget}s ===", flush=True)
        t0 = time.monotonic()
        with IsolatedJvmFleet([PORT], KOTLIN_SOLVER_JAR, heap_xmx="8g") as fl:
            with httpx.Client(timeout=budget * 2 + 120.0) as cli:
                r = cli.post(f"http://127.0.0.1:{PORT}/schedules/single",
                             json=copy.deepcopy(payload),
                             params={"acceptor": "LA",
                                     "spentLimitSeconds": budget,
                                     "randomSeed": seed, "rules": "GERAD2"})
                r.raise_for_status()
                sol = r.json()
        wall = time.monotonic() - t0
        (SCRATCH / f"p2_direct_solution_inst{inst}.json").write_text(
            json.dumps(sol), encoding="utf-8")
        m = personalization_metrics(sol)
        # Result gate (audit 2026-07-12, round 6): the direct pipeline had
        # no result_status at all. Same contract as stage 2: a roster with
        # hard violations, unassigned slots or coverage holes is a
        # diagnostic, not a result. The direct config has no
        # proven-impossible allowance (it is not catalog-limited) and no
        # pairing-trajectory artifact (its true-rules violations are
        # measured by _gerad_true_rules_check.py on the same file).
        from _coverage_validator import check as coverage_check
        from _gerad_stage2_roster import gate_result_status
        cov = coverage_check(payload, sol)
        result_status = gate_result_status(
            sol.get("score"), m["unassigned"], cov, set())
        n_not_ok += (result_status != "OK")
        row = {"inst": inst, "legs": n, "score": sol.get("score"),
               "result_status": result_status,
               "missing_legs": cov["missing"],
               "wall_s": round(wall, 1), "seed": seed, "budget_s": budget,
               **m}
        print(f"  score={row['score']}  [{result_status}]  "
              f"pref_hits={m['pref_hits']}/"
              f"{m['pref_attainable_legs']} attainable  "
              f"vacation_violations={m['vacation_violations']}  "
              f"unassigned={m['unassigned']}  ({wall:.0f}s)", flush=True)

        try:
            prior = json.loads(results_path.read_text())
        except Exception:
            prior = []
        prior = [x for x in prior if x.get("inst") != inst] + [row]
        tmp = results_path.with_suffix(f".tmp{inst}")
        tmp.write_text(json.dumps(prior, indent=1))
        tmp.replace(results_path)
        print(f"  saved -> {results_path.name}", flush=True)
    # Round-6 audit: any non-OK instance -> nonzero exit code.
    return min(n_not_ok, 125)


if __name__ == "__main__":
    sys.exit(main())
