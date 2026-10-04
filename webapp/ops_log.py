"""Day-of-operations fact journal (2026-07-17, gap item #17).

The stored facts are the OPERATIONAL truth for one instance: what actually
happened to each planned flight — a departure/arrival delay, a
cancellation, a diversion — keyed by the canonical `flightNumber` so the
same key gives both plan (the payload) and fact (here). One fact per
flight; upsert replaces it. In phase 2 the same store is filled in bulk
from a BTS on-time replay; phase 1 also allows manual entry/edit.

Storage discipline mirrors disruption.py: a per-process lock, an atomic
tmp->replace with the Windows read-share retry, and a DEFENSIVE read (a
corrupt byte degrades to "no facts", never 500s the ops view). The store
is gitignored runtime state (webapp/config/ops_log.json).

Convention (industrial): NO fact for a flight means it operated as
scheduled ("no movement message = ran to plan"). Only a recorded fact
marks a deviation. plan_fact.py reads this convention.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import registry

OPS_LOG_PATH = registry.REPO_ROOT / "webapp" / "config" / "ops_log.json"

# BTS-aligned delay causes (Carrier/Weather/NAS/Security/LateAircraft).
CAUSES = ("carrier", "weather", "nas", "security", "late_aircraft")

# Delay clamps: a day-of-ops delay outside this band is a data error, not a
# delay. Lower bound allows modest early departures (negative delay).
MIN_DELAY_MIN = -120
MAX_DELAY_MIN = 24 * 60
MAX_NOTE = 200

_lock = threading.Lock()


class OpsLogError(ValueError):
    """Invalid fact input; the message is safe to show the user."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load() -> dict:
    # A MISSING store is a fresh, empty log. GENUINE corruption (bad JSON /
    # not a dict) FAILS CLOSED (H14): treating it as {} would let the next
    # write silently erase every other instance's facts. But a TRANSIENT
    # OSError (a Windows sharing-violation while a concurrent _save_locked
    # runs its atomic replace, on this RAM-flaky box) is NOT corruption and
    # NOT a data-loss risk on the read path — retry it like the write path
    # does, so a normal GET does not 500 on a perfectly valid store.
    import time
    if not OPS_LOG_PATH.exists():
        return {}
    for attempt in range(5):
        try:
            raw = OPS_LOG_PATH.read_text(encoding="utf-8")
            break
        except OSError:
            if attempt == 4:
                raise OpsLogError(
                    "operational fact store is temporarily unreadable; "
                    "retry — refusing to overwrite it")
            time.sleep(0.05)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OpsLogError(
            f"operational fact store is corrupt ({exc}); refusing to "
            "overwrite it - inspect webapp/config/ops_log.json") from exc
    if not isinstance(data, dict):
        raise OpsLogError("operational fact store is not a JSON object")
    return data


def _replace_with_retry(tmp: Path, dst: Path) -> None:
    import time
    for attempt in range(5):
        try:
            tmp.replace(dst)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)


def _save_locked(data: dict) -> None:
    OPS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OPS_LOG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    _replace_with_retry(tmp, OPS_LOG_PATH)


def facts_for(inst_id: str) -> dict[str, dict]:
    """flightNumber -> fact for this instance (defensive: non-dict rows
    dropped)."""
    rows = _load().get(inst_id) or {}
    if not isinstance(rows, dict):
        return {}
    return {str(k): v for k, v in rows.items() if isinstance(v, dict)}


def _clamp_delay(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        m = int(value)
    except (TypeError, ValueError) as exc:
        raise OpsLogError("delay must be an integer number of minutes") from exc
    return max(MIN_DELAY_MIN, min(MAX_DELAY_MIN, m))


def _strict_bool(value, field: str) -> bool:
    """Strict boolean from untrusted JSON (M06): bool("false") is True in
    Python, so a JSON string "false" would silently cancel a flight. Accept
    only real booleans or explicit true/false-ish tokens."""
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "1", "yes", "y"):
            return True
        if s in ("false", "0", "no", "n", ""):
            return False
    raise OpsLogError(f"{field} must be a boolean, got {value!r}")


def _clean_fact(fields: dict) -> dict:
    """Validate + clamp one fact record from untrusted input."""
    cancelled = _strict_bool(fields.get("cancelled"), "cancelled")
    diverted = _strict_bool(fields.get("diverted"), "diverted")
    dep = _clamp_delay(fields.get("dep_delay_min"))
    arr = _clamp_delay(fields.get("arr_delay_min"))
    cause = fields.get("cause")
    if cause in ("", None):
        cause = None
    elif cause not in CAUSES:
        raise OpsLogError(f"unknown cause {cause!r}")
    # a cancelled flight has no delay figures (it never departed)
    if cancelled:
        dep = arr = None
        diverted = False
    note = str(fields.get("note") or "")[:MAX_NOTE]
    return {"cancelled": cancelled, "diverted": diverted,
            "dep_delay_min": dep, "arr_delay_min": arr,
            "cause": cause, "note": note}


def payload_flight_numbers(payload: dict) -> set[str]:
    return {str(f.get("flightNumber"))
            for f in (payload.get("flights") or [])
            if f.get("flightNumber")}


def _service_day(flight: dict) -> str:
    return str(flight.get("departureUTCDateTime") or "")[:10]


def _fact_key_of(f: dict, counts: dict, day_counts: dict) -> str:
    fn = str(f["flightNumber"])
    if counts[fn] == 1:
        return fn
    day = _service_day(f)
    if day_counts[(fn, day)] == 1:
        return f"{fn}@{day}"
    # повторный номер внутри ОДНОГО дня (ревью пакета): различаем по
    # минуте вылета — 'fn@YYYY-MM-DDTHH:MM'
    return f"{fn}@{str(f.get('departureUTCDateTime') or '')[:16]}"


def _key_counters(payload: dict):
    flights = [f for f in (payload.get("flights") or [])
               if f.get("flightNumber")]
    counts: dict[str, int] = {}
    day_counts: dict = {}
    for f in flights:
        fn = str(f["flightNumber"])
        counts[fn] = counts.get(fn, 0) + 1
        dk = (fn, _service_day(f))
        day_counts[dk] = day_counts.get(dk, 0) + 1
    return flights, counts, day_counts


def payload_fact_keys(payload: dict) -> dict[str, dict]:
    """key -> {'fn', 'day', 'ambiguous'} для каждого планового рейса.

    Аудит-2 (P1-05b): факты хранились по одному flightNumber — один
    cancel-факт гасил ВСЕ календарные occurrence номера. Ключ факта:
    сам номер, пока он уникален в плане (существующие хранилища работают
    без миграции); 'номер@YYYY-MM-DD' при повторном номере;
    'номер@YYYY-MM-DDTHH:MM' при повторном номере внутри одного дня.

    'ambiguous' — ТОЛЬКО внутриминутная коллизия: два вхождения номера
    вылетают в одну и ту же минуту, и даже самый точный ключ их не
    различает (P0-2 аудита документации 2026-07-20: раньше флаг стоял
    у любого повторного номера и никем не потреблялся; теперь он несёт
    точный смысл «ключ неадресуем» и режет ввод фактов в fact_key_for)."""
    flights, counts, day_counts = _key_counters(payload)
    keys: dict[str, dict] = {}
    for f in flights:
        fn = str(f["flightNumber"])
        # addr — адрес вхождения для событий «Сбоя» (трек occurrence-
        # идентичности): уникальный номер либо flightLegId; None, если
        # повтор без материализованной идентичности (не адресуем).
        lid = f.get("flightLegId")
        addr = (fn if counts[fn] == 1
                else (str(lid) if isinstance(lid, str) and lid else None))
        key = _fact_key_of(f, counts, day_counts)
        if key in keys:
            # ревью пакета: два рейса схлопнулись в ОДИН ключ факта
            # (повтор в одну минуту) — addr обязан стать неадресуемым,
            # а не указывать на произвольное вхождение
            keys[key]["addr"] = None
            keys[key]["ambiguous"] = True
            continue
        keys[key] = {"fn": fn, "day": _service_day(f),
                     "ambiguous": False, "addr": addr}
    return keys


def keys_by_flight(payload: dict) -> list[str | None]:
    """Ключ факта для КАЖДОГО планового рейса в порядке payload'а —
    единственный источник соответствия рейс->ключ для plan_fact
    (ревью пакета: повторная inline-деривация в читателях разъехалась бы
    с трёхуровневой схемой). Внутриминутная коллизия (ambiguous-ключ)
    отдаётся как None: один журнальный факт НЕ должен ложиться на оба
    вхождения сразу — обе строки остаются плановыми, а сам факт виден
    счётчиком unaddressableFacts (P0-2)."""
    flights, counts, day_counts = _key_counters(payload)
    keys = payload_fact_keys(payload)
    out: list[str | None] = []
    for f in flights:
        key = _fact_key_of(f, counts, day_counts)
        meta = keys.get(key)
        out.append(None if (meta and meta.get("ambiguous")) else key)
    return out


def fact_key_for(payload: dict, flight_number: str,
                 service_day: str | None = None) -> str:
    """Разрешить ключ факта по номеру (+дате/времени). Неоднозначность —
    громкий отказ, а не молчаливое действие на несколько рейсов сразу."""
    fn = str(flight_number or "")
    keys = payload_fact_keys(payload)
    if fn in keys:                          # номер уникален в плане
        return fn
    mine = [k for k, v in keys.items() if v["fn"] == fn]
    if not mine:
        raise OpsLogError(f"unknown flight {fn!r} for this instance")
    def _addressable(key: str) -> str:
        # P0-2: внутриминутная коллизия — ключ существует, но не различает
        # вхождения; ввод факта по нему запрещён (иначе один факт лёг бы
        # на оба рейса сразу)
        if keys[key].get("ambiguous"):
            raise OpsLogError(
                f"flight {fn!r} has several departures in the same "
                f"minute — the fact cannot be addressed to one flight")
        return key

    if service_day:
        exact = f"{fn}@{service_day}"
        if exact in keys:
            return _addressable(exact)
        # дата без времени при внутридневных повторах: сузить по префиксу
        pref = [k for k in mine if k.startswith(f"{fn}@{service_day}")]
        if len(pref) == 1:
            return _addressable(pref[0])
        if len(pref) > 1:
            raise OpsLogError(
                f"flight {fn!r} has several departures on "
                f"{service_day!r} — pass serviceDay with time "
                f"(YYYY-MM-DDTHH:MM)")
        raise OpsLogError(
            f"unknown flight {fn!r} on {service_day!r} for this instance")
    raise OpsLogError(
        f"flight number {fn!r} operates several times — "
        f"pass serviceDay (YYYY-MM-DD or YYYY-MM-DDTHH:MM)")


def upsert_fact(inst_id: str, valid_fns: set[str], flight_number: str,
                fields: dict, by: str | None = None) -> dict:
    """Create or replace the fact for one flight. `flight_number` must be a
    flight of the instance payload (caller passes the payload's set)."""
    fn = str(flight_number or "")
    if fn not in valid_fns:
        raise OpsLogError(f"unknown flight {fn!r} for this instance")
    fact = _clean_fact(fields)
    fact["updated_at"] = _now()
    fact["by"] = by
    with _lock:
        data = _load()
        data.setdefault(inst_id, {})[fn] = fact
        _save_locked(data)
    return {"flightNumber": fn, **fact}


def bulk_upsert(inst_id: str, facts: dict[str, dict], valid_fns: set[str],
                by: str | None = None) -> int:
    """Replace many facts at once (phase-2 BTS import). Silently skips
    flights not in the instance and invalid rows; returns the count
    written. One lock/one save for the whole batch."""
    cleaned: dict[str, dict] = {}
    for fn, fields in (facts or {}).items():
        fn = str(fn)
        if fn not in valid_fns or not isinstance(fields, dict):
            continue
        try:
            row = _clean_fact(fields)
        except OpsLogError:
            continue
        row["updated_at"] = _now()
        row["by"] = by
        cleaned[fn] = row
    if not cleaned:
        return 0
    with _lock:
        data = _load()
        data.setdefault(inst_id, {}).update(cleaned)
        _save_locked(data)
    return len(cleaned)


def remove_fact(inst_id: str, flight_number: str) -> bool:
    """Delete one flight's fact (reverts it to plan). True if removed."""
    with _lock:
        data = _load()
        rows = data.get(inst_id)
        if not isinstance(rows, dict) or str(flight_number) not in rows:
            return False
        rows.pop(str(flight_number))
        if rows:
            data[inst_id] = rows
        else:
            data.pop(inst_id, None)
        _save_locked(data)
    return True


def clear(inst_id: str) -> int:
    """Drop every fact for an instance (phase-2 replay reset). Returns the
    number removed."""
    with _lock:
        data = _load()
        rows = data.pop(inst_id, None)
        if rows is None:
            return 0
        _save_locked(data)
    return len(rows) if isinstance(rows, dict) else 0
