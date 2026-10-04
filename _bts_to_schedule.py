"""Convert BTS On-Time Performance CSV to a FlightCrewSchedule.

Filters BTS data to a specific airline/origin/date and produces a
Pydantic schedule with synthetic crew sized to plausibly cover the
operations.

Usage:
    python _bts_to_schedule.py <bts_csv> <carrier> <origin_iata> <date_yyyy_mm_dd>

Example:
    python _bts_to_schedule.py path/to/ontime.csv DL ATL 2024-08-15

Writes the schedule as JSON to ``data/bts_schedules/<carrier>_<origin>_<date>.json``.
"""
from __future__ import annotations

import _data_paths
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

csv.field_size_limit(2**24)

# IATA → IANA timezone via the airportsdata package (~7900 airports).
# Every conversion is DST-aware through zoneinfo; airports that cannot
# be resolved are collected and reported as a hard error — no silent
# fallback to a default offset.
import airportsdata
from zoneinfo import ZoneInfo

_AIRPORTS_IATA = airportsdata.load("IATA")
_TZ_CACHE: Dict[str, ZoneInfo] = {}
UNRESOLVED_AIRPORTS: set = set()


def _tz_for(iata: str) -> "ZoneInfo | None":
    """Resolve an IATA code to its IANA timezone. Returns None (and
    records the code in UNRESOLVED_AIRPORTS) when the airport or its
    timezone is unknown — callers must treat that as a fatal condition.
    """
    if iata in _TZ_CACHE:
        return _TZ_CACHE[iata]
    entry = _AIRPORTS_IATA.get(iata)
    tz_name = entry.get("tz") if entry else None
    if not tz_name:
        UNRESOLVED_AIRPORTS.add(iata)
        return None
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        UNRESOLVED_AIRPORTS.add(iata)
        return None
    _TZ_CACHE[iata] = tz
    return tz


def _hhmm_to_time(s: str):
    """Parse 'HHMM' or 'HMM' from BTS into a time object."""
    if not s or not s.strip():
        return None
    s = s.zfill(4)
    try:
        hh, mm = int(s[:2]), int(s[2:])
        # BTS uses 2400 to mean next day midnight; treat as 0:00 + 1 day
        if hh == 24:
            hh = 0
            return time(hh, mm), True   # rollover flag
        return time(hh, mm), False
    except ValueError:
        return None, False


def _local_to_utc(local_naive: datetime, tz: ZoneInfo) -> datetime:
    """Convert a naive local datetime to a naive UTC datetime, DST-aware."""
    return (local_naive.replace(tzinfo=tz)
            .astimezone(ZoneInfo("UTC"))
            .replace(tzinfo=None))


def _parse_elapsed_min(raw: str) -> "int | None":
    """CRSElapsedTime comes as e.g. '245.00' minutes; empty → None."""
    if raw is None or not str(raw).strip():
        return None
    try:
        return int(round(float(raw)))
    except ValueError:
        return None


def _aircraft_type_from_tail(tail: str) -> str:
    """Approximate Delta fleet typing from tail prefix.

    Delta convention (rough):
      - N1xxxx, N6xxxx, N7xxxx → 757/767/A330 (long-haul)
      - N9xxxx, N3xxxx → A220/A319/A320/A321 (Airbus narrowbody)
      - Otherwise → B737/717 (Boeing narrowbody)

    We collapse to 3 types: BOEING, AIRBUS, WIDEBODY.
    """
    if not tail:
        return "BOEING_NARROW"
    t = tail.upper().lstrip("N")
    if t.startswith("9") or t.startswith("3"):
        return "AIRBUS_NARROW"
    if t.startswith(("1", "6", "7")):
        return "WIDEBODY"
    return "BOEING_NARROW"


def filter_bts(csv_path: Path, carrier: str, hub: str, date_str: str) -> List[dict]:
    """Read BTS CSV and return rows for ``carrier`` on ``date_str`` whose
    Origin OR Dest is ``hub``.

    We want both legs of any ATL-touching pairing so the pairing
    generator can chain (e.g. ATL→LAX outbound + LAX→ATL return). A
    single-direction filter (Origin=ATL only) gives no return legs and
    forces every pairing to be a singleton.
    """
    out = []
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("Reporting_Airline") == carrier
                and row.get("FlightDate") == date_str
                and (row.get("Origin") == hub or row.get("Dest") == hub)):
                out.append(row)
    return out


def build_flights(bts_rows: List[dict]) -> Tuple[list, dict, dict]:
    """Convert BTS rows to Flight + Airport objects.

    Local CRS times are converted to UTC via IANA timezones (DST-aware).
    The arrival day is not guessed from HHMM comparisons: we try day
    offsets 0/+1/-1 for the arrival and keep the one whose UTC duration
    matches CRSElapsedTime within 1 minute. Rows where no offset matches
    are kept with the closest positive-duration candidate but recorded
    in the returned check stats — never silently fudged.

    Returns (flights, airports_by_code, check_stats).
    """
    from flight_crew_scheduling.domain import Airport, Flight

    airports_by_code: Dict[str, Airport] = {}
    flights = []
    check = {
        "flights_total": 0,
        "matched_exact": 0,
        "mismatched": [],
        "max_abs_diff_min": 0,
    }

    def get_airport(code: str) -> Airport:
        if code not in airports_by_code:
            airports_by_code[code] = Airport(
                code=code,
                name=code,   # IATA is fine as a name for our PoC
                languages=_iata_languages_guess(code),
            )
        return airports_by_code[code]

    for i, row in enumerate(bts_rows, start=1):
        flight_no = row.get("Flight_Number_Reporting_Airline", str(i))
        origin = row["Origin"]
        dest = row["Dest"]
        flight_date = datetime.strptime(row["FlightDate"], "%Y-%m-%d").date()
        crs_dep = row.get("CRSDepTime", "")
        crs_arr = row.get("CRSArrTime", "")
        if not crs_dep or not crs_arr:
            continue
        dep_t = _hhmm_to_time(crs_dep)
        arr_t = _hhmm_to_time(crs_arr)
        if dep_t[0] is None or arr_t[0] is None:
            continue

        origin_tz = _tz_for(origin)
        dest_tz = _tz_for(dest)
        if origin_tz is None or dest_tz is None:
            # Codes already recorded in UNRESOLVED_AIRPORTS; main() aborts.
            continue

        dep_local = datetime.combine(flight_date, dep_t[0])
        if dep_t[1]:   # 2400 rollover
            dep_local += timedelta(days=1)
        dep_utc = _local_to_utc(dep_local, origin_tz)

        # Arrival day disambiguation: the BTS row only carries the local
        # HHMM. Build candidates on flight_date + rollover + {0,+1,-1}
        # days and let CRSElapsedTime arbitrate.
        arr_base = datetime.combine(flight_date, arr_t[0])
        if arr_t[1]:   # 2400 rollover
            arr_base += timedelta(days=1)
        crs_elapsed = _parse_elapsed_min(row.get("CRSElapsedTime"))

        candidates = []   # (abs_diff_min or None, duration_min, arr_utc)
        for day_offset in (0, 1, -1):
            arr_utc_c = _local_to_utc(arr_base + timedelta(days=day_offset),
                                      dest_tz)
            duration_min = (arr_utc_c - dep_utc).total_seconds() / 60.0
            diff = (abs(duration_min - crs_elapsed)
                    if crs_elapsed is not None else None)
            candidates.append((diff, duration_min, arr_utc_c))

        check["flights_total"] += 1
        exact = [c for c in candidates if c[0] is not None and c[0] <= 1.0]
        if exact:
            _, duration_min, arr_utc = min(exact, key=lambda c: c[0])
            check["matched_exact"] += 1
            if crs_elapsed is not None:
                check["max_abs_diff_min"] = max(
                    check["max_abs_diff_min"],
                    int(round(abs(duration_min - crs_elapsed))))
        else:
            # Genuinely inconsistent BTS row (or missing CRSElapsedTime):
            # keep the closest candidate with a positive duration and
            # record the discrepancy instead of fudging the times.
            positive = [c for c in candidates if c[1] > 0]
            pool = positive or candidates
            if crs_elapsed is not None:
                diff_min, duration_min, arr_utc = min(pool, key=lambda c: c[0])
                check["max_abs_diff_min"] = max(check["max_abs_diff_min"],
                                                int(round(diff_min)))
            else:
                diff_min, duration_min, arr_utc = min(pool, key=lambda c: c[1])
            check["mismatched"].append({
                "flight": f"{row.get('Reporting_Airline', '')}{flight_no} "
                          f"{origin}-{dest} {row['FlightDate']}",
                "computed_min": int(round(duration_min)),
                "crs_elapsed_min": crs_elapsed,
            })

        tail = row.get("Tail_Number", "")
        aircraft = _aircraft_type_from_tail(tail)

        # BTS reuses the same flight_number for multiple legs on the same
        # day (e.g. DL555 = ATL→LAX morning + LAX→ATL evening). Jackson's
        # @JsonIdentityInfo on the Kotlin Flight class requires a unique
        # flightNumber per scope, so we suffix with the local-departure
        # HHMM to make it unique per-leg while staying readable.
        dep_hhmm = dep_local.strftime("%H%M")
        unique_flight_no = f"{row['Reporting_Airline']}{flight_no}-{dep_hhmm}"

        flights.append(Flight(
            flight_number=unique_flight_no,
            departure_airport=get_airport(origin),
            departure_utc_date_time=dep_utc,
            arrival_airport=get_airport(dest),
            arrival_utc_date_time=arr_utc,
            aircraft_type=aircraft,
        ))
    return flights, airports_by_code, check


def _iata_country_guess(code: str) -> str:
    """Trivial heuristic — for our PoC we just label US airports US.
    Not authoritative; affects only the Airport.country field for
    constraint computations that use it (none in our 21-constraint set).
    """
    international = {"LHR", "CDG", "FCO", "AMS", "FRA", "ZRH", "MUC",
                      "MAD", "BCN", "DUB", "BRU", "VIE",
                      "NRT", "ICN", "PEK", "HKG", "SIN", "SYD"}
    if code in international:
        return "INT"
    return "US"


def _iata_languages_guess(code: str) -> List[str]:
    """Languages spoken at the airport (heuristic).

    Used by H4 (language qualification) constraint. We assign English
    everywhere + a local language for international airports.
    """
    base = ["en"]
    locales = {
        "CDG": "fr", "LHR": "en", "FRA": "de", "MUC": "de", "ZRH": "de",
        "MAD": "es", "BCN": "es", "FCO": "it", "AMS": "nl",
        "NRT": "ja", "ICN": "ko", "PEK": "zh", "HKG": "zh",
    }
    if code in locales:
        base.append(locales[code])
    return base


def synthesize_crew(flights: list, airports_by_code: dict, hub_iata: str,
                     seed: int = 42) -> list:
    """Generate synthetic crew sized to plausibly cover ``flights``.

    Sizing rules (heuristic for Delta@ATL):
      * pilots ≈ 30% of flights (50/50 CPT/FO)
      * cabin  ≈ 55% of flights (30/70 senior CA / CA)

    Type-rating distribution is **demand-weighted** — drawn from the
    actual aircraft-type distribution in ``flights`` rather than fixed
    fleet ratios. This avoids structural infeasibility (e.g. 60% of
    flights are A-narrow but only 25% of crew are A-narrow rated). Each
    crew member gets ``type_count`` distinct ratings sampled by demand.

    Crew base: 70% hub_iata, 30% other top destinations.
    """
    from flight_crew_scheduling.domain import Employee

    rng = random.Random(seed)
    flight_count = len(flights)
    aircraft_dist = Counter(f.aircraft_type for f in flights)
    print(f"  flight aircraft distribution: {dict(aircraft_dist)}", flush=True)

    # Sizing: each flight needs 1 CPT + 1 FO + 1 senior CA + 2 CA (long-haul
    # adds +1 FO and +1 CA). One crew member serves at most one pairing
    # per day, so we must have AT LEAST as many of each rank as we have
    # pairings. We don't know the pairing count until Stage 1B runs, so
    # we generously oversize off the flight count: pairings ≤ flights.
    # For each rank we target ~1.3× the flight count → ample slack for
    # the optimizer to pick favorable assignments.
    n_captains = max(150, int(flight_count * 1.3))
    n_fos = max(150, int(flight_count * 1.4))   # +long-haul augmentation
    n_senior_ca = max(150, int(flight_count * 1.3))
    n_ca = max(300, int(flight_count * 2.7))    # 2 per flight + long-haul +1
    n_pilots = n_captains + n_fos
    n_cabin = n_senior_ca + n_ca

    print(f"  synthesizing crew: {n_pilots} pilots ({n_captains} CPT + {n_fos} FO), "
          f"{n_cabin} cabin ({n_senior_ca} senior + {n_ca} CA)", flush=True)

    # Bases: hub heavily weighted (~54%) + top 6 destinations
    dest_counts = Counter(f.arrival_airport.code for f in flights)
    other_bases = [code for code, _ in dest_counts.most_common(6) if code != hub_iata]
    bases = [hub_iata] * 7 + other_bases

    # Type-rating "weights" — exactly the schedule's aircraft mix. Each
    # entry's frequency in this list = its sampling probability.
    type_pool: List[str] = []
    for ac, cnt in aircraft_dist.items():
        type_pool.extend([ac] * max(1, cnt))

    def sample_unique_types(k: int) -> List[str]:
        """Sample k distinct ratings from the demand-weighted pool."""
        result: List[str] = []
        attempts = 0
        while len(result) < k and attempts < 50:
            cand = rng.choice(type_pool)
            if cand not in result:
                result.append(cand)
            attempts += 1
        # Fallback: if the pool has fewer distinct types than k, just
        # take everything we have (k=2 with 3 types in pool: always 2).
        return result

    employees = []
    counter = 1

    def add_employee(rank: str, type_count: int = 1):
        nonlocal counter
        base = airports_by_code.get(rng.choice(bases))
        if base is None:
            base = airports_by_code.get(hub_iata)
        type_ratings = sample_unique_types(type_count)
        languages = ["en"]
        if rng.random() < 0.25:
            languages.append(rng.choice(["es", "fr", "de", "it", "nl", "ja", "zh"]))
        employees.append(Employee(
            id=str(counter),
            name=f"E{counter:05d}",
            home_airport=base,
            skills=[rank],
            type_ratings=type_ratings,
            languages=languages,
            unavailable_days=[],
        ))
        counter += 1

    for _ in range(n_captains):
        add_employee("Captain", type_count=2)
    for _ in range(n_fos):
        add_employee("First officer", type_count=2)
    for _ in range(n_senior_ca):
        add_employee("Senior cabin attendant", type_count=3)
    for _ in range(n_ca):
        add_employee("Cabin attendant", type_count=3)

    return employees


def main():
    if len(sys.argv) < 5:
        print("Usage: python _bts_to_schedule.py <bts_csv> <carrier> "
              "<origin_iata> <date_yyyy_mm_dd>", flush=True)
        return 2

    csv_path = Path(sys.argv[1])
    carrier = sys.argv[2]
    origin = sys.argv[3]
    date_str = sys.argv[4]

    print(f"=== BTS → FlightCrewSchedule ===", flush=True)
    print(f"  csv:     {csv_path.name}", flush=True)
    print(f"  carrier: {carrier}", flush=True)
    print(f"  origin:  {origin}", flush=True)
    print(f"  date:    {date_str}", flush=True)

    rows = filter_bts(csv_path, carrier, origin, date_str)
    print(f"  matched: {len(rows)} flights", flush=True)
    if not rows:
        return 1

    flights, airports_by_code, check = build_flights(rows)
    print(f"  built {len(flights)} Flight objects, {len(airports_by_code)} airports",
          flush=True)

    # Timezone validation artifact — written even on failure so the
    # audit trail shows exactly what was (or was not) resolved.
    check_payload = {
        "airports_total": len(airports_by_code) + len(UNRESOLVED_AIRPORTS),
        "airports_resolved": len(airports_by_code),
        "airports_unresolved": sorted(UNRESOLVED_AIRPORTS),
        "flights_total": check["flights_total"],
        "matched_exact": check["matched_exact"],
        "mismatched": check["mismatched"],
        "max_abs_diff_min": check["max_abs_diff_min"],
    }
    check_dir = _data_paths.results_dir()
    check_dir.mkdir(parents=True, exist_ok=True)
    check_path = check_dir / "bts_conversion_check.json"
    with check_path.open("w", encoding="utf-8") as f:
        json.dump(check_payload, f, indent=1)
    print(f"  tz check: {check['matched_exact']}/{check['flights_total']} flights "
          f"match CRSElapsedTime exactly, {len(check['mismatched'])} mismatched, "
          f"max_abs_diff={check['max_abs_diff_min']} min → {check_path.name}",
          flush=True)

    if UNRESOLVED_AIRPORTS:
        print(f"  ERROR: {len(UNRESOLVED_AIRPORTS)} airport(s) missing from "
              f"airportsdata, no fallback allowed: "
              f"{', '.join(sorted(UNRESOLVED_AIRPORTS))}", flush=True)
        return 3

    employees = synthesize_crew(flights, airports_by_code, origin)

    # Build full schedule
    from flight_crew_scheduling.domain import FlightCrewSchedule
    from flight_crew_scheduling.demo_data import generate_flight_assignments
    flight_assignments = generate_flight_assignments(flights)

    schedule = FlightCrewSchedule(
        airports=list(airports_by_code.values()),
        employees=employees,
        flights=flights,
        flight_assignments=flight_assignments,
    )

    out_dir = HERE / "data" / "bts_schedules"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{carrier}_{origin}_{date_str}.json"
    payload = schedule.model_dump(by_alias=True, mode="json")

    # C01/C02 phase 3: materialize first-class identities as a dict
    # post-process (the Pydantic domain stays untouched — adding fields there
    # would ripple into the demo generator and Kotlin serialization). Wire
    # flightNumbers are already leg-unique (HHMM suffix above), so the fn ->
    # leg-id map is unambiguous. NOTE: this only affects FUTURE converter
    # runs — the stored seed files on disk are not regenerated by phase 3.
    leg_of_fn = {}
    for fl in payload.get("flights", []):
        fl["flightLegId"] = (f"{fl['flightNumber']}|"
                             f"{fl['departureUTCDateTime']}")
        leg_of_fn[fl["flightNumber"]] = fl["flightLegId"]
    for a in payload.get("flightAssignments", []):
        a.setdefault("crewSlotId", a.get("id"))
        fn = a.get("flight")
        fn = fn.get("flightNumber") if isinstance(fn, dict) else fn
        if fn in leg_of_fn:
            a["flightLegId"] = leg_of_fn[fn]

    # Diff summary vs a previous run of this converter, if one exists.
    if out_path.exists():
        try:
            with out_path.open(encoding="utf-8") as f:
                old_flights = {fl["flightNumber"]: fl
                               for fl in json.load(f).get("flights", [])}
            dep_changed = arr_changed = added = 0
            for fl in payload["flights"]:
                old = old_flights.pop(fl["flightNumber"], None)
                if old is None:
                    added += 1
                    continue
                if old["departureUTCDateTime"] != fl["departureUTCDateTime"]:
                    dep_changed += 1
                if old["arrivalUTCDateTime"] != fl["arrivalUTCDateTime"]:
                    arr_changed += 1
            print(f"  diff vs previous file: {dep_changed} departures changed, "
                  f"{arr_changed} arrivals changed, {added} added, "
                  f"{len(old_flights)} removed", flush=True)
        except (json.JSONDecodeError, KeyError) as exc:
            print(f"  diff vs previous file skipped ({exc})", flush=True)

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"  saved: {out_path}", flush=True)
    print(f"  schedule: {len(schedule.flights)}f, {len(schedule.employees)}e, "
          f"{len(schedule.flight_assignments)} crew-slots", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
