"""GERAD benchmark ①, config (а) — Stage-1 probe: pairing enumeration +
CP-SAT set-partitioning on the monthly instances, under GERAD connection
rules.

This is the half of config (а) directly comparable to GENCOL's published
pairing-phase effort (their I4 took >1 day per column-generation
iteration). We time: DFS enumeration of legal pairings (their connection
rules: sit >=60 min, rest >=10 h, base return, <=4 days) and CP-SAT
selection to proven optimality.

Fidelity notes: our DFS caps pairings at 5 LEGS total (their rule is 5
landings PER DUTY, up to 4 duties => up to ~20 legs) — our pairings are a
legal SUBSET of theirs; single-leg fallback keeps cover guaranteed.
Rostering over selected pairings is the next step (pairing-aware
constraint surface required — see BTS synthetic-distortion lesson).

Usage: python _gerad_two_stage.py <inst 1..7> [cpsat_time_limit_s]
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import flight_crew_scheduling.pairing_generator as pg  # noqa: E402
from flight_crew_scheduling.pairing_selector import (  # noqa: E402
    select_pairings_set_partitioning,
)
from flight_crew_scheduling.domain import Airport, Flight  # noqa: E402

SCRATCH = Path(__file__).resolve().parent / "data" / "gerad"


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    tl = int(sys.argv[2]) if len(sys.argv) > 2 else 300

    payload = json.loads(
        (SCRATCH / "payloads" / f"inst{n}.json").read_text(encoding="utf-8"))
    print(f"=== GERAD config (a) Stage-1 probe: inst{n} "
          f"({len(payload['flights'])} legs) ===", flush=True)

    # GERAD connection rules into our DFS generator
    pg.MIN_GROUND_TIME_MIN = 60          # rule 4 (same)
    pg.MIN_OVERNIGHT_REST_MIN = 10 * 60  # rule 5: min rest 10 h (was 8 h)
    pg.DAY_BREAK_THRESHOLD_MIN = 6 * 60  # max sit proxy (duty-span bounded)
    pg.MAX_PAIRING_HOURS = 96            # rule 2: 4 days (same)
    pg.MAX_LEGS = 5                      # fidelity note: per-pairing cap

    airports = {a["code"]: Airport(code=a["code"], name=a["code"],
                                   languages=["en"])
                for a in payload["airports"]}
    flights = [Flight(
        flight_number=f["flightNumber"],
        departure_airport=airports[f["departureAirport"]],
        departure_utc_date_time=f["departureUTCDateTime"],
        arrival_airport=airports[f["arrivalAirport"]],
        arrival_utc_date_time=f["arrivalUTCDateTime"],
        aircraft_type=f["aircraftType"],
    ) for f in payload["flights"]]

    t0 = time.monotonic()
    pairings = pg.enumerate_legal_pairings(flights)
    t_enum = time.monotonic() - t0
    legs_hist = Counter(len(p.flight_numbers) for p in pairings)
    print(f"  enumeration: {len(pairings)} pairings in {t_enum:.1f}s "
          f"legs={dict(sorted(legs_hist.items()))}", flush=True)

    t1 = time.monotonic()
    flight_numbers = [f.flight_number for f in flights]
    selected, info = select_pairings_set_partitioning(
        flight_numbers, pairings, time_limit_seconds=tl, num_workers=1)
    t_sel = time.monotonic() - t1
    sel_hist = Counter(len(p.flight_numbers) for p in selected)
    print(f"  CP-SAT: {len(selected)} selected ({info['status']}) "
          f"in {t_sel:.1f}s  legs={dict(sorted(sel_hist.items()))} "
          f"obj={info.get('objective')}", flush=True)

    out = {"inst": n, "legs": len(flights),
           "pairings_enumerated": len(pairings), "enum_s": round(t_enum, 1),
           "selected": len(selected), "cpsat_status": info["status"],
           "cpsat_s": round(t_sel, 1),
           "legs_distribution": dict(sorted(sel_hist.items()))}
    import _data_paths; p = _data_paths.results_dir() / "gerad_config_a_stage1.json"
    prior = json.loads(p.read_text()) if p.exists() else []
    prior = [r for r in prior if r.get("inst") != n] + [out]
    p.write_text(json.dumps(prior, indent=1))
    print(f"  saved -> {p.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
