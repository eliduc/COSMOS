"""Convert an IATA SSIM chapter-7 schedule file into the canonical payload.

Standalone import adapter (layer 1). Reads a fixed-width SSIM file, parses
TYPE 3 records (flight leg records) only, expands them over a date range,
synthesizes crew from an explicit --bases list (SSIM carries no crew), and
writes a payload that validates against
``webapp/schema/canonical_payload.schema.json``.

House rules honoured here:
  * loud failures — malformed type-3 records are collected and FAIL the
    run (nonzero exit); no silent fixes;
  * unknown / non-type-3 record types are skipped with a per-type count
    reported in the conversion check artifact;
  * the adapter never invents crew bases — --bases is REQUIRED;
  * a conversion check artifact (house pattern, cf.
    final-report/bts_conversion_check.json) is always written, even on
    failure, and any sanity violation is listed and exits nonzero;
  * the output payload is validated against the canonical JSON Schema
    before it is considered done — self-invalid output is a hard error.

Usage:
    python _ssim_to_payload.py <file.ssim> --bases ATL:20,LAX:15
        [--from YYYY-MM-DD] [--to YYYY-MM-DD]
        [--out payload.json] [--check conversion_check.json]

Defaults: --from/--to = the file's full period of operation;
--out = <input>_payload.json next to the input;
--check = <input>_conversion_check.json next to the input.

Column reference — IATA Standard Schedules Information Manual (SSIM),
Chapter 7, record type 3 (flight leg record), fixed width 200 characters.
1-based columns exactly as in the standard; the 0-based Python slices used
below are these columns minus one:

  col 1        record type ('3')
  col 2        operational suffix
  cols 3-5     airline designator
  cols 6-9     flight number
  cols 10-11   itinerary variation identifier
  cols 12-13   leg sequence number
  col 14       service type
  cols 15-21   period of operation, from date (DDMMMYY)
  cols 22-28   period of operation, to date (DDMMMYY)
  cols 29-35   days of operation (position d holds digit d; 1 = Monday)
  col 36       frequency rate
  cols 37-39   departure station (IATA)
  cols 40-43   scheduled time of passenger departure (HHMM, local)
  cols 44-47   scheduled time of aircraft departure (HHMM, local)
  cols 48-52   UTC time variation, departure (sign + HHMM, e.g. -0400)
  cols 53-54   passenger terminal, departure
  cols 55-57   arrival station (IATA)
  cols 58-61   scheduled time of aircraft arrival (HHMM, local)
  cols 62-65   scheduled time of passenger arrival (HHMM, local)
  cols 66-70   UTC time variation, arrival (sign + HHMM)
  cols 71-72   passenger terminal, arrival
  cols 73-75   aircraft type (IATA code)
  cols 76-192  booking / meal / joint-operation / configuration fields
               (not used by this adapter)
  col 193      departure date variation (blank / '0'-'9'; 'A' = -1 day)
  col 194      arrival date variation   (blank / '0'-'9'; 'A' = -1 day)
  cols 195-200 record serial number

This adapter uses the PASSENGER departure/arrival times (cols 40-43 and
62-65). Two-digit years are interpreted as 20YY (SSIM files in circulation
today are all 20YY; a 19YY schedule is out of scope for this pipeline).
Times are local; UTC = local - time variation. Blank-tolerant parsing is
the single deliberate liberality: every field is stripped and short lines
are space-padded to 200 characters before slicing — nothing else is
guessed or repaired.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "webapp" / "schema" / "canonical_payload.schema.json"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

RECORD_LENGTH = 200
KNOWN_NON_LEG_TYPES = {"1", "2", "4", "5"}   # header/carrier/segment/trailer
OFFSET_MIN = timedelta(hours=-12)
OFFSET_MAX = timedelta(hours=14)
DURATION_MAX = timedelta(hours=20)

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


class RecordError(ValueError):
    """A malformed field inside a type-3 record (collected, then fatal)."""


# ---------------------------------------------------------------- field parsers

def _parse_ddmmmyy(raw: str, what: str) -> date:
    s = raw.strip().upper()
    if len(s) != 7:
        raise RecordError(f"{what}: expected DDMMMYY, got {raw!r}")
    day_s, mon_s, yy_s = s[0:2], s[2:5], s[5:7]
    if mon_s not in _MONTHS:
        raise RecordError(f"{what}: unknown month {mon_s!r} in {raw!r}")
    if not (day_s.isdigit() and yy_s.isdigit()):
        raise RecordError(f"{what}: non-numeric day/year in {raw!r}")
    try:
        return date(2000 + int(yy_s), _MONTHS[mon_s], int(day_s))
    except ValueError as exc:
        raise RecordError(f"{what}: invalid date {raw!r} ({exc})") from exc


def _parse_hhmm(raw: str, what: str) -> time:
    s = raw.strip()
    if len(s) != 4 or not s.isdigit():
        raise RecordError(f"{what}: expected HHMM, got {raw!r}")
    hh, mm = int(s[0:2]), int(s[2:4])
    if hh > 23 or mm > 59:
        raise RecordError(f"{what}: out-of-range time {raw!r}")
    return time(hh, mm)


def _parse_offset(raw: str, what: str) -> timedelta:
    """Parse an SSIM time variation: sign + HHMM, e.g. '+0100', '-0430'."""
    s = raw.strip()
    if len(s) != 5 or s[0] not in "+-" or not s[1:].isdigit():
        raise RecordError(f"{what}: expected sHHMM UTC offset, got {raw!r}")
    hh, mm = int(s[1:3]), int(s[3:5])
    if mm > 59:
        raise RecordError(f"{what}: invalid minutes in UTC offset {raw!r}")
    delta = timedelta(hours=hh, minutes=mm)
    return -delta if s[0] == "-" else delta


def _parse_days(raw: str) -> Set[int]:
    """Days of operation: 7 positions; position d (1=Monday) holds the
    digit d when the flight operates that weekday, blank or '0' otherwise."""
    field = raw.ljust(7)
    days: Set[int] = set()
    for i, ch in enumerate(field, start=1):
        if ch == str(i):
            days.add(i)
        elif ch not in (" ", "0"):
            raise RecordError(
                f"days of operation: position {i} must be blank, '0' or "
                f"'{i}', got {ch!r} in {raw!r}")
    if not days:
        raise RecordError(f"days of operation: no operating days in {raw!r}")
    return days


def _parse_date_variation(ch: str, what: str) -> Optional[int]:
    """Date variation: blank = absent, '0'-'9' = +N days, 'A' = -1 day."""
    if ch.strip() == "":
        return None
    if ch.isdigit():
        return int(ch)
    if ch.upper() == "A":
        return -1
    raise RecordError(f"{what} date variation: expected blank/0-9/'A', got {ch!r}")


# ---------------------------------------------------------------- type-3 parsing

@dataclass
class Leg:
    """One parsed SSIM type-3 flight leg record (pre-expansion)."""
    line_no: int
    airline: str
    flight_number: str
    itinerary_variation: int
    leg_sequence: int
    service_type: str
    period_from: date
    period_to: date
    days_of_operation: Set[int]
    departure_station: str
    departure_time_local: time
    departure_utc_offset: timedelta
    arrival_station: str
    arrival_time_local: time
    arrival_utc_offset: timedelta
    aircraft_type: str
    departure_date_variation: Optional[int]
    arrival_date_variation: Optional[int]


def parse_type3(line: str, line_no: int) -> Leg:
    """Parse one type-3 record. Raises RecordError on any malformed field.

    The line is space-padded to 200 chars first (blank-tolerance); every
    slice below is the 1-based SSIM column range minus one (see module
    docstring for the column reference).
    """
    rec = line.ljust(RECORD_LENGTH)

    airline = rec[2:5].strip()          # cols 3-5
    if not airline or not airline.isalnum():
        raise RecordError(f"airline designator: got {rec[2:5]!r}")

    flight_raw = rec[5:9].strip()       # cols 6-9
    if not flight_raw.isdigit():
        raise RecordError(f"flight number: expected digits, got {rec[5:9]!r}")
    flight_number = str(int(flight_raw))

    ivi_raw = rec[9:11].strip()         # cols 10-11
    if not ivi_raw.isdigit():
        raise RecordError(
            f"itinerary variation identifier: got {rec[9:11]!r}")

    leg_raw = rec[11:13].strip()        # cols 12-13
    if not leg_raw.isdigit():
        raise RecordError(f"leg sequence number: got {rec[11:13]!r}")

    service_type = rec[13].strip()      # col 14

    period_from = _parse_ddmmmyy(rec[14:21], "period-from")   # cols 15-21
    period_to = _parse_ddmmmyy(rec[21:28], "period-to")       # cols 22-28
    if period_from > period_to:
        raise RecordError(
            f"period of operation: from {period_from} after to {period_to}")

    days = _parse_days(rec[28:35])      # cols 29-35

    dep_station = rec[36:39].strip()    # cols 37-39
    if len(dep_station) != 3 or not dep_station.isalnum():
        raise RecordError(f"departure station: got {rec[36:39]!r}")
    dep_time = _parse_hhmm(rec[39:43], "passenger departure time")  # 40-43
    dep_offset = _parse_offset(rec[47:52], "departure")             # 48-52

    arr_station = rec[54:57].strip()    # cols 55-57
    if len(arr_station) != 3 or not arr_station.isalnum():
        raise RecordError(f"arrival station: got {rec[54:57]!r}")
    arr_time = _parse_hhmm(rec[61:65], "passenger arrival time")    # 62-65
    arr_offset = _parse_offset(rec[65:70], "arrival")               # 66-70

    aircraft_type = rec[72:75].strip()  # cols 73-75
    if not aircraft_type:
        raise RecordError(f"aircraft type: got {rec[72:75]!r}")

    dep_var = _parse_date_variation(rec[192], "departure")  # col 193
    arr_var = _parse_date_variation(rec[193], "arrival")    # col 194

    return Leg(
        line_no=line_no,
        airline=airline,
        flight_number=flight_number,
        itinerary_variation=int(ivi_raw),
        leg_sequence=int(leg_raw),
        service_type=service_type,
        period_from=period_from,
        period_to=period_to,
        days_of_operation=days,
        departure_station=dep_station,
        departure_time_local=dep_time,
        departure_utc_offset=dep_offset,
        arrival_station=arr_station,
        arrival_time_local=arr_time,
        arrival_utc_offset=arr_offset,
        aircraft_type=aircraft_type,
        departure_date_variation=dep_var,
        arrival_date_variation=arr_var,
    )


def parse_ssim(path: Path) -> Tuple[List[Leg], Counter, List[dict], int]:
    """Read the file; return (legs, skipped_by_type, errors, records_total).

    Non-type-3 record types (known or unknown) are skipped and counted by
    their record-type character; malformed type-3 records go into
    ``errors`` and make the run fail in main().
    """
    legs: List[Leg] = []
    skipped: Counter = Counter()
    errors: List[dict] = []
    total = 0
    with path.open(encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            line = raw.rstrip("\r\n")
            total += 1
            if not line.strip():
                skipped["blank"] += 1
                continue
            record_type = line[0]
            if record_type != "3":
                skipped[record_type] += 1
                continue
            try:
                legs.append(parse_type3(line, line_no))
            except RecordError as exc:
                errors.append({"line": line_no, "error": str(exc)})
    return legs, skipped, errors, total


# ---------------------------------------------------------------- expansion

def _daterange(d_from: date, d_to: date):
    d = d_from
    while d <= d_to:
        yield d
        d += timedelta(days=1)


def expand_legs(legs: List[Leg], range_from: date, range_to: date) -> List[dict]:
    """Expand each leg over its operating days intersected with the range.

    Flight id: <airline><number>-<legseq>-<operating date>, where the
    operating date is the schedule date from the period of operation
    (BEFORE any date variation — the variations shift the actual local
    departure/arrival relative to that date, per SSIM).
    """
    flights: List[dict] = []
    for leg in legs:
        lo = max(leg.period_from, range_from)
        hi = min(leg.period_to, range_to)
        for op_date in _daterange(lo, hi):
            if op_date.isoweekday() not in leg.days_of_operation:
                continue

            dep_date = op_date + timedelta(
                days=leg.departure_date_variation or 0)
            dep_local = datetime.combine(dep_date, leg.departure_time_local)
            dep_utc = dep_local - leg.departure_utc_offset

            if leg.arrival_date_variation is not None:
                arr_date = op_date + timedelta(days=leg.arrival_date_variation)
            else:
                # No date-variation field: infer — an arrival earlier than
                # the departure on the local clock means next day.
                arr_date = dep_date
                if leg.arrival_time_local < leg.departure_time_local:
                    arr_date += timedelta(days=1)
            arr_local = datetime.combine(arr_date, leg.arrival_time_local)
            arr_utc = arr_local - leg.arrival_utc_offset

            flights.append({
                "id": (f"{leg.airline}{leg.flight_number}"
                       f"-{leg.leg_sequence}-{op_date.isoformat()}"),
                "leg": leg,
                "departureUTC": dep_utc,
                "arrivalUTC": arr_utc,
            })
    return flights


# ---------------------------------------------------------------- payload

def _fmt(dt: datetime) -> str:
    """Naive-UTC datetime format used across the pipeline's payloads."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def build_payload(flights: List[dict], bases: List[Tuple[str, int]],
                  source_name: str, check_path_str: str) -> dict:
    airports = sorted(
        {f["leg"].departure_station for f in flights}
        | {f["leg"].arrival_station for f in flights})
    aircraft_types = sorted({f["leg"].aircraft_type for f in flights})

    employees = []
    for base, count in bases:
        for i in range(1, count + 1):
            employees.append({
                "id": f"{base}-CPT-{i:03d}",
                "name": f"Captain {base} {i}",
                "homeAirport": base,
                "skills": ["Captain"],
                "typeRatings": aircraft_types,
                "languages": ["en"],
                "unavailableDays": [],
            })

    flight_objs = []
    assignments = []
    for idx, f in enumerate(flights, start=1):
        leg = f["leg"]
        # C01/C02 phase 3: first-class identities (f['id'] is already
        # leg-unique per date, so fn|dep is doubly unique).
        leg_id = f"{f['id']}|{_fmt(f['departureUTC'])}"
        flight_objs.append({
            "flightNumber": f["id"],
            "flightLegId": leg_id,
            "departureAirport": leg.departure_station,
            "departureUTCDateTime": _fmt(f["departureUTC"]),
            "arrivalAirport": leg.arrival_station,
            "arrivalUTCDateTime": _fmt(f["arrivalUTC"]),
            "aircraftType": leg.aircraft_type,
        })
        assignments.append({
            "id": str(idx),
            "crewSlotId": str(idx),
            "flight": f["id"],
            "flightLegId": leg_id,
            "indexInFlight": 0,
            "requiredSkill": "Captain",
            "employee": None,
        })

    return {
        "formatVersion": "1",
        "provenance": {
            "adapter": "ssim",
            "source": source_name,
            "convertedAt": datetime.now(timezone.utc)
                                   .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "conversionCheck": check_path_str,
        },
        "airports": [{"code": c, "name": c} for c in airports],
        "employees": employees,
        "flights": flight_objs,
        "flightAssignments": assignments,
    }


# ---------------------------------------------------------------- sanity checks

def run_sanity_checks(legs: List[Leg], flights: List[dict]) -> dict:
    """UTC-offset range, positive-and-bounded duration, unique flight ids."""
    offset_violations = []
    for leg in legs:
        for what, off in (("departure", leg.departure_utc_offset),
                          ("arrival", leg.arrival_utc_offset)):
            if not (OFFSET_MIN <= off <= OFFSET_MAX):
                offset_violations.append({
                    "line": leg.line_no,
                    "flight": f"{leg.airline}{leg.flight_number}",
                    "field": what,
                    "offset_min": int(off.total_seconds() // 60),
                })

    duration_violations = []
    for f in flights:
        duration = f["arrivalUTC"] - f["departureUTC"]
        if not (timedelta(0) < duration <= DURATION_MAX):
            duration_violations.append({
                "flight": f["id"],
                "duration_min": int(duration.total_seconds() // 60),
            })

    seen: Set[str] = set()
    duplicates: List[str] = []
    for f in flights:
        if f["id"] in seen:
            duplicates.append(f["id"])
        seen.add(f["id"])

    return {
        "offset_violations": offset_violations,
        "duration_violations": duration_violations,
        "duplicate_flight_ids": duplicates,
    }


# ---------------------------------------------------------------- CLI

def _parse_bases(raw: str) -> List[Tuple[str, int]]:
    bases: List[Tuple[str, int]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise argparse.ArgumentTypeError(
                f"--bases entry {part!r}: expected AIRPORT:COUNT")
        code, _, count_s = part.partition(":")
        code = code.strip().upper()
        if len(code) != 3 or not code.isalnum():
            raise argparse.ArgumentTypeError(
                f"--bases entry {part!r}: {code!r} is not an IATA code")
        if not count_s.strip().isdigit() or int(count_s) <= 0:
            raise argparse.ArgumentTypeError(
                f"--bases entry {part!r}: count must be a positive integer")
        bases.append((code, int(count_s)))
    if not bases:
        raise argparse.ArgumentTypeError("--bases: no valid entries")
    return bases


def _parse_iso_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {raw!r}") from exc


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="SSIM chapter-7 -> canonical payload adapter")
    parser.add_argument("ssim_file", type=Path)
    parser.add_argument("--bases", type=_parse_bases, required=True,
                        help="crew bases, e.g. ATL:20,LAX:15 (REQUIRED — "
                             "the adapter never invents bases)")
    parser.add_argument("--from", dest="date_from", type=_parse_iso_date,
                        help="expansion range start (default: file period)")
    parser.add_argument("--to", dest="date_to", type=_parse_iso_date,
                        help="expansion range end (default: file period)")
    parser.add_argument("--out", type=Path,
                        help="payload output path "
                             "(default: <input>_payload.json)")
    parser.add_argument("--check", type=Path,
                        help="conversion check artifact path "
                             "(default: <input>_conversion_check.json)")
    args = parser.parse_args(argv)

    ssim_path: Path = args.ssim_file
    if not ssim_path.is_file():
        print(f"ERROR: SSIM file not found: {ssim_path}", flush=True)
        return 2
    out_path: Path = args.out or ssim_path.with_name(
        ssim_path.stem + "_payload.json")
    check_path: Path = args.check or ssim_path.with_name(
        ssim_path.stem + "_conversion_check.json")

    print("=== SSIM -> canonical payload ===", flush=True)
    print(f"  input:  {ssim_path}", flush=True)

    legs, skipped, errors, total = parse_ssim(ssim_path)

    # Expansion range: CLI override or the file's full period of operation.
    if legs:
        file_from = min(leg.period_from for leg in legs)
        file_to = max(leg.period_to for leg in legs)
    else:
        file_from = file_to = None
    range_from = args.date_from or file_from
    range_to = args.date_to or file_to

    flights: List[dict] = []
    if legs and not errors and range_from and range_to:
        flights = expand_legs(legs, range_from, range_to)

    sanity = run_sanity_checks(legs, flights)

    # Conversion check artifact — written even on failure (audit trail).
    try:
        check_rel = str(check_path.resolve().relative_to(HERE)).replace("\\", "/")
    except ValueError:
        check_rel = str(check_path)
    check_payload = {
        "source": ssim_path.name,
        "generatedAt": datetime.now(timezone.utc)
                               .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "records_total": total,
        "records_parsed_type3": len(legs),
        "records_skipped_by_type": dict(sorted(skipped.items())),
        "malformed_type3": errors,
        "expansion_from": range_from.isoformat() if range_from else None,
        "expansion_to": range_to.isoformat() if range_to else None,
        "flights_expanded": len(flights),
        "airports_total": len({leg.departure_station for leg in legs}
                              | {leg.arrival_station for leg in legs}),
        "airports_resolved": len({f["leg"].departure_station for f in flights}
                                 | {f["leg"].arrival_station for f in flights}),
        "airports": sorted({f["leg"].departure_station for f in flights}
                           | {f["leg"].arrival_station for f in flights}),
        **sanity,
        "ok": (not errors
               and not sanity["offset_violations"]
               and not sanity["duration_violations"]
               and not sanity["duplicate_flight_ids"]
               and len(flights) > 0),
    }
    check_path.parent.mkdir(parents=True, exist_ok=True)
    with check_path.open("w", encoding="utf-8") as f:
        json.dump(check_payload, f, indent=1)
    print(f"  records: {total} total, {len(legs)} type-3 parsed, "
          f"skipped by type {dict(sorted(skipped.items()))}", flush=True)
    print(f"  check:   {check_path}", flush=True)

    if errors:
        print(f"  ERROR: {len(errors)} malformed type-3 record(s) — "
              f"failing loudly, no payload written:", flush=True)
        for e in errors:
            print(f"    line {e['line']}: {e['error']}", flush=True)
        return 1
    if not legs:
        print("  ERROR: no type-3 records in file", flush=True)
        return 1
    if not flights:
        print(f"  ERROR: no flights expanded in range "
              f"{range_from}..{range_to}", flush=True)
        return 3
    if (sanity["offset_violations"] or sanity["duration_violations"]
            or sanity["duplicate_flight_ids"]):
        print("  ERROR: sanity violations (see check artifact):", flush=True)
        for v in sanity["offset_violations"]:
            print(f"    offset out of [-12:00,+14:00]: {v}", flush=True)
        for v in sanity["duration_violations"]:
            print(f"    duration out of (0, 20h]: {v}", flush=True)
        for v in sanity["duplicate_flight_ids"]:
            print(f"    duplicate flight id: {v}", flush=True)
        return 3

    # Bases must reference airports that actually occur in the schedule —
    # anything else would fail the webapp's referential checks downstream.
    airports_seen = {f["leg"].departure_station for f in flights} \
        | {f["leg"].arrival_station for f in flights}
    unknown_bases = [b for b, _ in args.bases if b not in airports_seen]
    if unknown_bases:
        print(f"  ERROR: base airport(s) not present in the schedule: "
              f"{', '.join(unknown_bases)} (airports seen: "
              f"{', '.join(sorted(airports_seen))})", flush=True)
        return 2

    payload = build_payload(flights, args.bases, ssim_path.name, check_rel)

    # Self-validation against the canonical JSON Schema — loud failure.
    import jsonschema
    with SCHEMA_PATH.open(encoding="utf-8") as f:
        schema = json.load(f)
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as exc:
        print(f"  ERROR: adapter output does not validate against "
              f"{SCHEMA_PATH.name}: {exc.message}", flush=True)
        return 4

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"  payload: {out_path}", flush=True)
    print(f"  {len(payload['flights'])} flights, "
          f"{len(payload['employees'])} employees, "
          f"{len(payload['flightAssignments'])} crew slots, "
          f"{len(payload['airports'])} airports", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
