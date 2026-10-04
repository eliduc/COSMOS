"""Day-of-operations replay from BTS on-time data (2026-07-17, gap #17,
phase 2).

Two server pieces on top of the phase-1 fact store (ops_log) and matcher
(plan_fact):

  1. import_bts() — read the SAME BTS on-time CSV the instance was built
     from and lift the FACTUAL fields (departure/arrival delay,
     cancellation, diversion, delay cause) into the fact store, matched on
     the canonical `flightNumber`. The instance's own flight-number set is
     the source of truth: a row is imported only if its reconstructed key
     is a flight of the instance, so nothing foreign leaks in. The key is
     rebuilt EXACTLY as _bts_to_schedule.py builds it:
         "{Reporting_Airline}{Flight_Number}-{local dep HHMM}"
     and rows are additionally pinned to the instance's flight date to
     avoid same-number/same-time collisions across the month.

  2. generate_disruptions() — turn the current facts into the EXISTING
     «Сбой» events (kind "flights" for cancellations, kind "delay" for
     delays past a threshold), so the untouched neighbourhood-rebuild
     machinery (disruption.py / _disruption_repair.py) runs unchanged. It
     is IDEMPOTENT: prior auto-generated events are cleared first, so a
     re-run after more facts arrive never double-counts.

The time scrubber of the replay is a pure UI concern — the deviations
table already carries each flight's scheduled departure, so revealing
"up to time T" is a client-side filter over the already-loaded report; no
server replay state is needed.
"""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from . import disruption, registry

# Default bundled month of BTS on-time data (present on Test; optional).
DEFAULT_BTS_CSV = (registry.REPO_ROOT / "data" / "bts"
                   / "On_Time_Reporting_Carrier_On_Time_Performance_"
                     "(1987_present)_2024_8.csv")

AUTO_TAG = "ops:auto"          # human-readable note on auto-generated events
AUTO_SOURCE = "ops_auto"       # STRUCTURED marker used for idempotent clear
                               # (a note is user-writable; a source field is
                               # set only here — audit 2026-07-17)
DEFAULT_DELAY_THRESHOLD_MIN = 15

_CAUSE_COLS = {
    "CarrierDelay": "carrier", "WeatherDelay": "weather",
    "NASDelay": "nas", "SecurityDelay": "security",
    "LateAircraftDelay": "late_aircraft",
}

csv.field_size_limit(2 ** 24)


class OpsReplayError(ValueError):
    """Invalid replay input; the message is safe to show the user."""


def _fn_key(airline: str, flight_no: str, crs_dep: str) -> str | None:
    """Rebuild the canonical flightNumber from a raw BTS row exactly as the
    converter does: local departure HHMM, zero-padded, 24:00 -> 00:00."""
    airline = str(airline or "").strip()
    flight_no = str(flight_no or "").strip()
    crs = str(crs_dep or "").strip()
    if not airline or not flight_no or not crs.isdigit():
        return None
    val = int(crs)
    hh, mm = (val // 100) % 24, val % 100
    return f"{airline}{flight_no}-{hh:02d}{mm:02d}"


def _num(raw) -> int | None:
    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        return int(round(float(raw)))
    except ValueError:
        return None


def _truthy(raw) -> bool:
    return str(raw or "").strip() not in ("", "0", "0.0", "0.00")


def _cause_of(row: dict) -> str | None:
    """Dominant delay cause (BTS splits delay minutes across five buckets;
    they are populated only for reportable delays)."""
    best, best_min = None, 0
    for col, slug in _CAUSE_COLS.items():
        m = _num(row.get(col)) or 0
        if m > best_min:
            best, best_min = slug, m
    return best


def _fact_from_row(row: dict) -> dict:
    cancelled = _truthy(row.get("Cancelled"))
    return {
        "cancelled": cancelled,
        "diverted": (not cancelled) and _truthy(row.get("Diverted")),
        "dep_delay_min": None if cancelled else _num(row.get("DepDelay")),
        "arr_delay_min": None if cancelled else _num(row.get("ArrDelay")),
        "cause": None if cancelled else _cause_of(row),
        "note": "BTS",
    }


def _carrier_of(valid_fns: set[str]) -> str | None:
    """Leading alpha of any flight number ("DL555-1215" -> "DL")."""
    for fn in valid_fns:
        head = ""
        for ch in fn:
            if ch.isalpha():
                head += ch
            else:
                break
        if head:
            return head
    return None


def import_bts(payload: dict, csv_path: Path | None = None,
               max_scan: int = 6_000_000) -> dict[str, dict]:
    """Stream the BTS CSV and return {flightNumber: fact} for flights that
    belong to this instance (matched by key AND flight date). Pure w.r.t.
    the store — the caller writes the result via ops_log.bulk_upsert."""
    path = Path(csv_path) if csv_path else DEFAULT_BTS_CSV
    if not path.exists():
        raise OpsReplayError(f"BTS data file not found: {path.name}")
    valid_fns = {str(f.get("flightNumber")) for f in payload.get("flights")
                 or [] if f.get("flightNumber")}
    if not valid_fns:
        return {}
    carrier = _carrier_of(valid_fns)
    # Group matched rows BY FlightDate, then pick the date whose facts cover
    # the most instance flights. This picks the true operating day without a
    # UTC/local frame assumption: BTS FlightDate is local, and the same
    # {carrier}{num}-{HHMM} key repeats across days, so the correct day is
    # the one that matches the most of this instance's flights (audit
    # 2026-07-17 — the old modal-UTC-date filter imported the wrong day on
    # >50%-rollover hubs).
    by_date: dict[str, dict[str, dict]] = {}
    scanned = 0
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            scanned += 1
            if scanned > max_scan:
                break
            if carrier and row.get("Reporting_Airline") != carrier:
                continue
            fn = _fn_key(row.get("Reporting_Airline"),
                         row.get("Flight_Number_Reporting_Airline"),
                         row.get("CRSDepTime"))
            if fn is None or fn not in valid_fns:
                continue
            day = by_date.setdefault(str(row.get("FlightDate")), {})
            if fn not in day:                       # first row per fn per day
                day[fn] = _fact_from_row(row)
    if not by_date:
        return {}
    # Pick the date with the most matched flights; on a TIE resolve
    # DETERMINISTICALLY by the earliest date (M05) instead of CSV file order,
    # so the same instance always imports the same factual day.
    best_len = max(len(v) for v in by_date.values())
    best = min(d for d in by_date if len(by_date[d]) == best_len)
    return by_date[best]


def clear_auto_events(inst_id: str) -> int:
    """Remove all AUTO-generated «Сбой» events for an instance by the
    STRUCTURED source marker. Used both before regeneration and when the
    underlying facts are cleared (H12), so a stale disruption cannot outlive
    the fact that produced it. A manually-authored event is never touched."""
    removed = 0
    for ev in disruption.events_for(inst_id):
        if ev.get("source") == AUTO_SOURCE:
            if disruption.remove_event(inst_id, ev.get("id")):
                removed += 1
    return removed


def generate_disruptions(inst_id: str, inst: dict, facts: dict[str, dict],
                         delay_threshold_min: int = DEFAULT_DELAY_THRESHOLD_MIN,
                         by: str | None = None,
                         addr_by_key: dict | None = None) -> dict:
    """Idempotently turn facts into «Сбой» events. Clears prior auto events
    first, then re-creates: one "flights" (cancellation) event per chunk +
    "delay" events grouped by shift. Deviations outside the disruption
    module's own limits are skipped honestly."""
    # threshold below 1 would let a 0-minute "delay" through, which
    # add_delay rejects (shift must be non-zero) — clamp so the run never
    # aborts mid-way after clearing (audit 2026-07-17).
    delay_threshold_min = max(1, int(delay_threshold_min))

    # 1) clear prior auto-generated events by the STRUCTURED source marker,
    # never by the user-writable note (a manual event noted "ops:auto…"
    # must not be deleted — audit 2026-07-17).
    removed = clear_auto_events(inst_id)

    # Трек occurrence-идентичности: события адресуют КОНКРЕТНОЕ вхождение —
    # addr ключа факта (уникальный номер либо flightLegId из
    # ops_log.payload_fact_keys через addr_by_key). Раньше «fn@день»
    # схлопывался к номеру и отмена одной даты порождала событие на обе.
    addr_by_key = addr_by_key or {}
    skipped_unaddressable = 0

    def _addr(key: str):
        nonlocal skipped_unaddressable
        if key in addr_by_key:
            a = addr_by_key[key]
            if a is None:
                skipped_unaddressable += 1
            return a
        return key.split("@", 1)[0]      # легаси-вызовы без карты адресов

    cancelled = sorted({a for a in
                        (_addr(k) for k, f in facts.items()
                         if f.get("cancelled")) if a is not None})
    delays: dict[int, list[str]] = {}
    for key, f in facts.items():
        if f.get("cancelled"):
            continue
        dep = f.get("dep_delay_min")
        # M07: strictly-greater-than-threshold matches plan_fact's on-time
        # predicate (dep<=threshold is on-time), so a 15-min delay no longer
        # both counts as on-time AND spawns a disruption event.
        if not isinstance(dep, int) or dep <= delay_threshold_min:
            continue
        if not (disruption.MIN_SHIFT_MIN <= dep <= disruption.MAX_SHIFT_MIN):
            continue
        addr = _addr(key)
        if addr is not None and addr not in delays.setdefault(dep, []):
            delays[dep].append(addr)

    cap = disruption.MAX_EVENT_FNS
    cancel_events = delay_events = 0
    cancel_flights = 0
    for i in range(0, len(cancelled), cap):
        chunk = cancelled[i:i + cap]
        disruption.add_flights(inst_id, inst, chunk,
                               note=f"{AUTO_TAG} cancel", by=by,
                               source=AUTO_SOURCE)
        cancel_events += 1
        cancel_flights += len(chunk)

    delayed_flights = 0
    for shift, fns in sorted(delays.items()):
        for i in range(0, len(fns), cap):
            chunk = fns[i:i + cap]
            disruption.add_delay(inst_id, inst, chunk, shift,
                                 note=f"{AUTO_TAG} delay", by=by,
                                 source=AUTO_SOURCE)
            delay_events += 1
            delayed_flights += len(chunk)

    return {"cleared": removed,
            "cancelEvents": cancel_events, "cancelledFlights": cancel_flights,
            "delayEvents": delay_events, "delayedFlights": delayed_flights,
            "delayThresholdMin": delay_threshold_min,
            # честный счётчик: факты по неадресуемым вхождениям (повторный
            # номер без flightLegId) пропущены, а не размазаны на оба
            "skippedUnaddressable": skipped_unaddressable}
