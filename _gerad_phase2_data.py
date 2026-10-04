"""Phase 2 data: port of the GERAD G-2014-22 personalization generators
(preferredVacations.cpp and EmployeeLegPreferences.cpp by Kasirzadeh &
Quesnel) onto our instN.json payloads.

Faithful to the C++ logic with two documented deviations:
  * RNG: the originals seed with time(0) (irreproducible by design); we use
    a fixed seed so runs are repeatable.
  * Their vacation generator can emit end day 32 (startDay <= 31-dur+1,
    end = start+dur): we clamp vacation days to January 2000 (1..31).

Semantics (verified against the C++ sources):
  * Vacations: floor(30% of headcount) employees drawn uniformly WITHOUT
    replacement; duration = rand%13 + 2 (2..14); start day uniform in
    1..(31-dur+1); vacation covers start..start+dur inclusive.
  * Preferences: EVERY employee gets floor(10% of his base's legs) legs,
    drawn uniformly without replacement from the legs of the pairings
    assigned to his base in initialSolution.in (deadhead TDH legs excluded).

Output: payloads/instN_p2.json — same payload with employees enriched:
  preferredDaysOff = vacation days (soft, penalty in GERAD2 profile),
  preferredFlights = preferred leg ids (soft bonus in GERAD2 profile).

Usage: python _gerad_phase2_data.py [seed]
"""
from __future__ import annotations

import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
SCRATCH = Path(__file__).resolve().parent / "data" / "gerad"

VACATION_PCT = 30    # params.txt: "% employees choosen : 30"
PREF_PCT = 10        # params.txt: "% airleg choosen : 10"


def parse_initial_solution(path: Path) -> dict[str, list[str]]:
    """legs of the pairings of each base, TDH deadheads excluded."""
    legs_per_base: dict[str, list[str]] = defaultdict(list)
    text = path.read_text(encoding="utf-8", errors="replace")
    for m in re.finditer(r"Pairing\s+\d+\s*:\s*Base\s+(\w+)\s*:\s*([^;]+);",
                         text):
        base, legs_blob = m.group(1), m.group(2)
        for leg in (t.strip() for t in legs_blob.split(",")):
            if leg and not leg.startswith("TDH"):
                legs_per_base[base].append(leg)
    return legs_per_base


def main():
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 42
    summary = []
    for inst in range(1, 8):
        rng = random.Random(seed * 100 + inst)   # per-instance stream
        payload = json.loads(
            (SCRATCH / "payloads" / f"inst{inst}.json").read_text("utf-8"))
        employees = payload["employees"]
        flight_ids = {f["flightNumber"] for f in payload["flights"]}

        inst_dir = SCRATCH / f"inst{inst}" / f"instance{inst}"
        legs_per_base = parse_initial_solution(inst_dir / "initialSolution.in")

        # --- diagnostics: initialSolution legs vs payload flights ----------
        sol_legs = {l for legs in legs_per_base.values() for l in legs}
        unknown = sol_legs - flight_ids
        missing = flight_ids - sol_legs

        # --- vacations (preferredVacations.cpp port) -----------------------
        n_vac = int(len(employees) * VACATION_PCT / 100)
        pool = list(range(len(employees)))
        n_vac_days = 0
        for _ in range(n_vac):
            emp = employees[pool.pop(rng.randrange(len(pool)))]
            dur = rng.randrange(13) + 2                  # 2..14
            start = rng.randrange(31 - dur + 1) + 1      # 1..31-dur+1
            days = [f"2000-01-{d:02d}" for d in range(start,
                                                      min(start + dur, 31) + 1)]
            emp["preferredDaysOff"] = days
            n_vac_days += len(days)

        # --- leg preferences (EmployeeLegPreferences.cpp port) -------------
        n_pref_total = 0
        for emp in employees:
            base = emp.get("homeAirport")
            if isinstance(base, dict):
                base = base.get("code")
            base_legs = [l for l in legs_per_base.get(base, ())
                         if l in flight_ids]
            n_pref = int(len(base_legs) * PREF_PCT / 100.0)
            tmp = list(base_legs)
            chosen = []
            for _ in range(n_pref):
                chosen.append(tmp.pop(rng.randrange(len(tmp))))
            emp["preferredFlights"] = chosen
            n_pref_total += len(chosen)

        out = SCRATCH / "payloads" / f"inst{inst}_p2.json"
        out.write_text(json.dumps(payload), encoding="utf-8")
        row = {"inst": inst, "employees": len(employees),
               "vacation_employees": n_vac, "vacation_days": n_vac_days,
               "preferred_flights_total": n_pref_total,
               "avg_pref_per_emp": round(n_pref_total / len(employees), 1),
               "sol_legs": len(sol_legs),
               "sol_legs_not_in_payload": len(unknown),
               "payload_flights_not_in_solution": len(missing)}
        summary.append(row)
        print(f"inst{inst}: {len(employees)} emp, {n_vac} with vacations "
              f"({n_vac_days} days), {n_pref_total} preferred legs "
              f"(avg {row['avg_pref_per_emp']}/emp); leg-id mismatch: "
              f"{len(unknown)} unknown / {len(missing)} uncovered",
              flush=True)

    import _data_paths
    (_data_paths.results_dir() / "gerad_phase2_data_summary.json").write_text(
        json.dumps({"seed": seed, "vacation_pct": VACATION_PCT,
                    "pref_pct": PREF_PCT, "instances": summary}, indent=1))
    print("saved -> gerad_phase2_data_summary.json", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
