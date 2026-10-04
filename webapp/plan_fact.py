"""Plan-vs-fact for the day of operations (2026-07-17, gap item #17).

Pure functions over (canonical payload = PLAN, ops_log facts = FACT),
matched on `flightNumber`. No I/O here — app.py loads the payload and the
facts and passes them in, which keeps every KPI trivially testable.

Convention (see ops_log.py): a flight with NO fact operated as scheduled.
Only a recorded fact marks a deviation. So partial fact coverage still
yields honest KPIs — with a `reported` coverage figure alongside so the
reader knows how much of the day is actually observed.

Deviation status per flight:
  cancelled | diverted | delayed (dep or arr beyond OTP threshold) |
  ontime (fact present, within threshold) | scheduled (no fact yet).

KPIs follow what ops-control dashboards show: completion factor, on-time
performance (OTP within 15 min, departure and arrival), average delay,
delay-cause split, and plan/operated block hours.
"""
from __future__ import annotations

from datetime import datetime

OTP_THRESHOLD_MIN = 15         # industry standard on-time window

_STATUS_ORDER = {"cancelled": 0, "diverted": 1, "delayed": 2,
                 "ontime": 3, "scheduled": 4}


def _parse(dt: str):
    try:
        return datetime.fromisoformat(str(dt))
    except (TypeError, ValueError):
        return None


def _block_min(flight: dict) -> int | None:
    dep = _parse(flight.get("departureUTCDateTime"))
    arr = _parse(flight.get("arrivalUTCDateTime"))
    if dep is None or arr is None:
        return None
    mins = int((arr - dep).total_seconds() // 60)
    return mins if mins >= 0 else None


def _operating_anchor(payload: dict):
    """Midnight (UTC) of the EARLIEST departure — a fixed reference so the
    scrubber orders flights by the true instant. Using the earliest instant
    (not the modal UTC date) keeps every dayMin >= 0 even for hubs where
    most departures cross UTC midnight (audit 2026-07-17)."""
    deps = [d for d in (_parse(f.get("departureUTCDateTime"))
                        for f in payload.get("flights") or []) if d]
    if not deps:
        return None
    first = min(deps)
    return first.replace(hour=0, minute=0, second=0, microsecond=0)


def _status(fact: dict | None) -> str:
    if fact is None:
        return "scheduled"
    if fact.get("cancelled"):
        return "cancelled"
    if fact.get("diverted"):
        return "diverted"
    dep = fact.get("dep_delay_min")
    arr = fact.get("arr_delay_min")
    late = ((isinstance(dep, int) and dep > OTP_THRESHOLD_MIN)
            or (isinstance(arr, int) and arr > OTP_THRESHOLD_MIN))
    return "delayed" if late else "ontime"


def deviations(payload: dict, facts: dict[str, dict]) -> list[dict]:
    """One row per plan flight, newest disruption first (cancelled, then
    diverted, delayed, ontime, scheduled), then by scheduled departure.
    Each row carries `dayMin` = minutes since the operating-day anchor, so
    the client scrubber reveals flights in true chronological order
    (including UTC-rollover late flights)."""
    anchor = _operating_anchor(payload)
    # Аудит-2 (P1-05b): факт ищется по ключу рейса (номер / номер@день /
    # номер@день-время) — единственный источник соответствия — ops_log.
    from . import ops_log as _ol
    flight_keys = _ol.keys_by_flight(payload)
    rows = []
    for f, key in zip(payload.get("flights") or [], flight_keys):
        fn = str(f.get("flightNumber"))
        day = str(f.get("departureUTCDateTime") or "")[:10]
        fact = facts.get(key)
        status = _status(fact)
        dep = _parse(f.get("departureUTCDateTime"))
        day_min = int((dep - anchor).total_seconds() // 60) \
            if (dep is not None and anchor is not None) else 0
        rows.append({
            "flightNumber": fn,
            "serviceDay": day,
            # Ключ факта и адрес вхождения — наружу (волна 2). Оба уже
            # были в области видимости и просто не выводились, из-за чего
            # каждому следующему потребителю (контур буфера стыковки —
            # первый на очереди) пришлось бы заново реализовывать
            # трёхуровневое правило адресации: номер / номер@день /
            # номер@день-время. Вторая реализация того же правила
            # разъехалась бы с первой — вопрос только когда.
            # key = None означает НЕАДРЕСУЕМЫЙ рейс (несколько вылетов в
            # одну минуту), и это не то же самое, что «факта нет».
            "factKey": key,
            "flightLegId": f.get("flightLegId"),
            "departureAirport": f.get("departureAirport"),
            "arrivalAirport": f.get("arrivalAirport"),
            "schedDep": f.get("departureUTCDateTime"),
            "dayMin": max(0, day_min),
            "status": status,
            "depDelayMin": (fact or {}).get("dep_delay_min"),
            "arrDelayMin": (fact or {}).get("arr_delay_min"),
            "cause": (fact or {}).get("cause"),
            "note": (fact or {}).get("note") or "",
        })
    rows.sort(key=lambda r: (_STATUS_ORDER.get(r["status"], 9),
                             str(r["schedDep"])))
    return rows


def _pct(num, den):
    if not den:
        return None
    return round(num / den * 100.0, 1)


def summary(payload: dict, facts: dict[str, dict]) -> dict:
    """Operational KPI record for one instance's plan against its facts."""
    flights = payload.get("flights") or []
    scheduled = len(flights)
    if scheduled == 0:
        return {"available": False, "reason": "no_flights"}

    cancelled = diverted = delayed = 0
    reported = 0
    dep_ontime = arr_ontime = 0
    dep_delays: list[int] = []
    arr_delays: list[int] = []
    total_delay = 0
    cause_count = {c: 0 for c in ("carrier", "weather", "nas", "security",
                                  "late_aircraft")}
    cause_min = {c: 0 for c in cause_count}
    plan_block = 0
    operated_block = 0

    from . import ops_log as _ol
    flight_keys = _ol.keys_by_flight(payload)
    for f, _key in zip(flights, flight_keys):
        fn = str(f.get("flightNumber"))
        fact = facts.get(_key)
        bmin = _block_min(f) or 0
        plan_block += bmin
        status = _status(fact)
        if fact is not None:
            reported += 1
        if status == "cancelled":
            cancelled += 1
            continue                       # a cancelled flight has no block
        operated_block += bmin
        is_diverted = status == "diverted"
        if is_diverted:
            diverted += 1
        dep = (fact or {}).get("dep_delay_min")
        arr = (fact or {}).get("arr_delay_min")
        dep = dep if isinstance(dep, int) else 0    # no fact -> 0 (on plan)
        arr = arr if isinstance(arr, int) else 0
        if dep <= OTP_THRESHOLD_MIN:
            dep_ontime += 1
        # a diverted flight did NOT arrive on time at its destination — never
        # count it in the arrival-OTP numerator (audit 2026-07-17)
        if arr <= OTP_THRESHOLD_MIN and not is_diverted:
            arr_ontime += 1
        if status == "delayed":
            delayed += 1
            if dep > 0:
                dep_delays.append(dep)
            if arr > 0:
                arr_delays.append(arr)
            total_delay += max(dep, arr, 0)
            cause = (fact or {}).get("cause")
            if cause in cause_count:
                cause_count[cause] += 1
                cause_min[cause] += max(dep, arr, 0)

    operated = scheduled - cancelled

    def _avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else None

    # H11: with ZERO observed facts these are UNOBSERVED, not perfect — report
    # N/A (None) so an empty operating day cannot read as 100% completion/OTP.
    # (coveragePct/reported already give the watermark for partial coverage,
    # where the documented "no movement = ran to plan" convention still holds.)
    observed = reported > 0
    # P0-2: легаси-факты под внутриминутно-коллизионным ключом больше не
    # ложатся на обе строки — они игнорируются и честно считаются
    from . import ops_log as _ol2
    _keys_meta = _ol2.payload_fact_keys(payload)
    _amb = {k for k, v in _keys_meta.items() if v.get("ambiguous")}
    unaddressable = sum(1 for k in facts if k in _amb)
    return {
        "available": True,
        "unaddressableFacts": unaddressable,
        "scheduled": scheduled,
        "operated": operated,
        "cancelled": cancelled,
        "diverted": diverted,
        "delayed": delayed,
        "affected": cancelled + diverted + delayed,
        "reported": reported,
        "coveragePct": _pct(reported, scheduled),
        "completionFactorPct": _pct(operated, scheduled) if observed else None,
        "otpDep15Pct": _pct(dep_ontime, operated) if observed else None,
        "otpArr15Pct": _pct(arr_ontime, operated) if observed else None,
        "avgDepDelayMin": _avg(dep_delays),
        "avgArrDelayMin": _avg(arr_delays),
        "totalDelayMin": total_delay,
        "causeCounts": cause_count,
        "causeMinutes": cause_min,
        "planBlockHours": round(plan_block / 60.0, 1),
        "operatedBlockHours": round(operated_block / 60.0, 1),
    }


def report(payload: dict, facts: dict[str, dict]) -> dict:
    """Combined deviations table + KPI summary — the ops view payload."""
    return {"summary": summary(payload, facts),
            "deviations": deviations(payload, facts)}
