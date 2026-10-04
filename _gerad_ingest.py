"""GERAD/Kasirzadeh instance ingestion → our FlightCrewSchedule payload.

Parses the 7 anonymized Northwest monthly instances (G-2014-22 data sets)
into the JSON shape our Kotlin solver consumes. Milestone 1 of benchmark ①:
faithful data ingestion + validation against the paper's published counts.

Mapping decisions (documented for the report):
  * These are single-position pilot instances → 1 crew slot per leg,
    requiredSkill "Captain"; aircraftType = instance fleet tag (727, DC9...)
    with every employee rated for it.
  * Employees are taken from solution_0 (real EMPxxx ids with home bases) —
    the same crew set GENCOL's reference schedules use.
  * Anonymized naive datetimes (Jan 2000) are treated as UTC.
  * Vacations/preassignments from the reference solution are NOT applied at
    ingestion (kept for the rules-transcription phase; open question list).

Usage:
    python _gerad_ingest.py            # all 7 instances
    python _gerad_ingest.py 1          # just instance 1
Outputs: <scratch>/kasirzadeh/payloads/instN.json + validation table.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRATCH = Path(__file__).resolve().parent / "data" / "gerad"
OUT = SCRATCH / "payloads"

# Paper's published counts for validation (G-2014-22 / EURO JTL 2017)
EXPECTED = {
    1: ("727", 1013, 33), 2: ("DC9", 1500, 34), 3: ("D94", 1855, 47),
    4: ("D95", 5613, 145), 5: ("757", 5743, 247), 6: ("319", 5886, 223),
    7: ("320", 7766, 305),
}


def find_root(n: int) -> Path:
    base = SCRATCH / f"inst{n}"
    inner = list(base.glob("instance*"))
    return inner[0] if inner else base


def parse_days(root: Path):
    flights = []
    for f in sorted(root.glob("day_*.csv"),
                    key=lambda p: int(re.search(r"day_(\d+)", p.name).group(1))):
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 7:
                continue
            leg, dep_ap, dep_d, dep_t, arr_ap, arr_d, arr_t = parts[:7]
            flights.append({
                "flightNumber": leg,
                "departureAirport": dep_ap,
                "departureUTCDateTime": f"{dep_d}T{dep_t}:00",
                "arrivalAirport": arr_ap,
                "arrivalUTCDateTime": f"{arr_d}T{arr_t}:00",
            })
    return flights


def parse_bases(root: Path):
    bases = {}
    airports = set()
    for line in (root / "listOfBases.csv").read_text().splitlines()[1:]:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[0]:
            continue
        ap, status, n = parts[0], parts[1], parts[2]
        airports.add(ap)
        if status == "1":
            bases[ap] = int(n)
    return bases, airports


def parse_employees(root: Path):
    """EMPxxx (BASEy) pairs from solution_0 — the reference crew set."""
    txt = (root / "solution_0").read_text(encoding="utf-8", errors="replace")
    emps = {}
    for m in re.finditer(r"schedule\s+\d+\s+(EMP\w+)\s+\((\w+)\)", txt):
        emps[m.group(1)] = m.group(2)
    return emps


def build_payload(n: int):
    fleet, exp_legs, exp_crew = EXPECTED[n]
    root = find_root(n)
    flights = parse_days(root)
    bases, airports = parse_bases(root)
    emps = parse_employees(root)

    for fl in flights:
        airports.add(fl["departureAirport"])
        airports.add(fl["arrivalAirport"])

    airport_objs = [{"code": a, "name": a, "latitude": 0.0, "longitude": 0.0,
                     "taxiTimeInMinutes": {}, "languages": ["en"]}
                    for a in sorted(airports)]
    flight_objs = [{**fl,
                    "departureAirport": fl["departureAirport"],
                    "arrivalAirport": fl["arrivalAirport"],
                    "aircraftType": fleet} for fl in flights]
    # Embedded airport refs: our Kotlin uses @JsonIdentityInfo — first
    # occurrence full object, later plain code string. Simplest robust form:
    # give flights full airport dicts every time is NOT valid for identity;
    # use string refs against the airports list order → send full objects in
    # airports[], and STRING codes inside flights (Jackson resolves by id).
    # solution_0 lists only crew WITH schedules; the authoritative headcount
    # is listOfBases.csv (matches the paper's totals). Top up each base with
    # synthetic ids for employees that have no reference schedule.
    per_base_have = {}
    for base in emps.values():
        per_base_have[base] = per_base_have.get(base, 0) + 1
    for base, want in bases.items():
        for k in range(want - per_base_have.get(base, 0)):
            emps[f"EMPX_{base}_{k+1}"] = base

    emp_objs = [{"id": eid, "name": eid, "homeAirport": base,
                 "skills": ["Captain"], "languages": ["en"],
                 "unavailableDays": [], "typeRatings": [fleet],
                 "seniority": 500, "isReserve": False,
                 "preferredDaysOff": [], "undesiredAirports": []}
                for eid, base in sorted(emps.items())]
    assigns = [{"id": str(i + 1), "flight": fl["flightNumber"],
                "indexInFlight": 0, "requiredSkill": "Captain",
                "employee": None}
               for i, fl in enumerate(flight_objs)]

    payload = {"airports": airport_objs, "employees": emp_objs,
               "flights": flight_objs, "flightAssignments": assigns,
               "score": None}
    ok_legs = len(flight_objs) == exp_legs
    ok_crew = len(emp_objs) == exp_crew
    return payload, {
        "inst": n, "fleet": fleet,
        "legs": len(flight_objs), "exp_legs": exp_legs, "ok_legs": ok_legs,
        "crew": len(emp_objs), "exp_crew": exp_crew, "ok_crew": ok_crew,
        "bases": bases, "airports": len(airport_objs),
    }


def main():
    only = int(sys.argv[1]) if len(sys.argv) > 1 else None
    OUT.mkdir(exist_ok=True)
    print(f"{'inst':>4} {'fleet':>6} {'legs':>6} {'exp':>6} {'ok':>4} "
          f"{'crew':>5} {'exp':>4} {'ok':>4} {'airports':>9}  bases", flush=True)
    all_ok = True
    for n in ([only] if only else range(1, 8)):
        payload, v = build_payload(n)
        (OUT / f"inst{n}.json").write_text(json.dumps(payload),
                                           encoding="utf-8")
        all_ok &= v["ok_legs"] and v["ok_crew"]
        print(f"{v['inst']:>4} {v['fleet']:>6} {v['legs']:>6} "
              f"{v['exp_legs']:>6} {str(v['ok_legs']):>4} {v['crew']:>5} "
              f"{v['exp_crew']:>4} {str(v['ok_crew']):>4} "
              f"{v['airports']:>9}  {v['bases']}", flush=True)
    print(f"\npayloads: {OUT}", flush=True)
    print("VALIDATION:", "ALL OK" if all_ok else "MISMATCHES — investigate",
          flush=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
