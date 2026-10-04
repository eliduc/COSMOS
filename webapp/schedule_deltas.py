"""Приём дельт: изменения расписания (подмножество ASM/SSM) и
инкрементальные кадровые выгрузки (2026-07-17, дорожная карта №6 документа
COSMOS-DATA-FORMALIZATION-RU, этап Ф9).

Дельты расписания. Полная грамматика сообщений Type B (IATA AHM 785) в
v1 не реализуется — поддерживается ЧЕСТНО ОПИСАННОЕ ПОДМНОЖЕСТВО с
громкими отказами на всё непонятое:

  * блоки разделяются пустой строкой; строки "ASM"/"SSM"/"UTC" в начале
    блока игнорируются (совместимость с реальными шапками);
  * строка действия: NEW | CNL | TIM | EQT;
  * строка рейса: "SU100/01MAR26" (2-значный год опционален — тогда год
    берётся из расписания инстанса);
  * NEW: строка сегмента "SVO LED 0800 1000 [A320]";
  * TIM: строка "0830 1030" — новые времена вылета/прилёта UTC (прилёт
    раньше вылета = прилёт на следующий день);
  * EQT: строка "A321".

Семантика применения к канону: CNL удаляет рейс И его назначения (журнал
хранит историю), TIM/EQT правят поля рейса, NEW добавляет рейс (без
слотов — непокрытие честно всплывёт в валидации покрытия). Совпадение
рейса — по flightNumber И дате вылета (ежедневные рейсы различимы).

Кадровые инкременты: CSV/JSON с КАНОНИЧЕСКИМИ именами колонок
(id, name, homeAirport, skills, ...). Диф по id: added / updated /
unchanged; сотрудники, пропавшие из выгрузки, НЕ удаляются молча — они
возвращаются списком missing_in_feed на решение человека.

Дисциплина: применение двухшаговое (preview по умолчанию, apply=true —
запись); после применения payload проходит validate_payload — невалидный
результат НЕ сохраняется; повторная дельта с тем же отпечатком SHA-256
отклоняется (идемпотентность источника — урок BTS-импорта); каждый приём
пишется в журнал webapp/config/schedule_deltas.json.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone

from . import registry

JOURNAL_PATH = (registry.REPO_ROOT / "webapp" / "config"
                / "schedule_deltas.json")

_lock = threading.Lock()

# One lock per instance: a delta APPLY reads the payload, mutates it and
# writes it back — two concurrent applies to the same instance would each
# start from the pre-delta payload and the second write would clobber the
# first, yet both would journal "applied". Serialize the read-modify-write
# per instance (the SHA idempotency check lives inside it too).
_inst_locks: dict[str, threading.Lock] = {}
_inst_locks_guard = threading.Lock()


def _instance_lock(inst_id: str) -> threading.Lock:
    with _inst_locks_guard:
        lk = _inst_locks.get(inst_id)
        if lk is None:
            lk = _inst_locks[inst_id] = threading.Lock()
        return lk

ACTIONS = ("NEW", "CNL", "TIM", "EQT")
_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}

_FLIGHT_RE = re.compile(r"^([A-Z0-9]{2,8})/(\d{2})([A-Z]{3})(\d{2})?$")
_TIME_RE = re.compile(r"^(\d{4})\s+(\d{4})$")
_SEG_RE = re.compile(r"^([A-Z]{3})\s+([A-Z]{3})\s+(\d{4})\s+(\d{4})"
                     r"(?:\s+(\S{2,8}))?$")

MAX_TEXT_CHARS = 100_000
MAX_CREW_BYTES = 10 * 1024 * 1024

# Canonical employee columns the crew feed may carry (id is the key).
CREW_FIELDS = ("id", "name", "homeAirport", "skills", "languages",
               "unavailableDays", "typeRatings", "seniority", "isReserve",
               "preferredDaysOff", "undesiredAirports", "preferredFlights")
CREW_LIST_FIELDS = {"skills", "languages", "unavailableDays", "typeRatings",
                    "preferredDaysOff", "undesiredAirports",
                    "preferredFlights"}


class DeltaError(ValueError):
    """User-facing delta failure; loud, never silently 'fixed'."""


# ------------------------------------------------------------------ journal

def _load_journal() -> dict:
    try:
        raw = json.loads(JOURNAL_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"delta journal unreadable: {exc}") from exc


def _append_journal(inst_id: str, entry: dict) -> None:
    with _lock:
        data = _load_journal()
        data.setdefault(inst_id, []).append(entry)
        JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = JOURNAL_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),  # census-ok: журнал модуля, не набор
                       encoding="utf-8")
        tmp.replace(JOURNAL_PATH)


def journal_for(inst_id: str) -> list[dict]:
    return list(_load_journal().get(inst_id) or [])


def _seen_sha(inst_id: str, sha: str) -> bool:
    return any(e.get("sha256") == sha for e in journal_for(inst_id))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------- ASM parsing

def _schedule_years(payload: dict) -> set[int]:
    years = set()
    for f in payload.get("flights") or []:
        v = f.get("departureUTCDateTime")
        if isinstance(v, str) and len(v) >= 4 and v[:4].isdigit():
            years.add(int(v[:4]))
    return years


def parse_asm(text: str, payload: dict) -> list[dict]:
    """Text (supported subset) -> action list. Every unparsed line is a
    loud error — a half-understood delta must not be applied."""
    if len(text) > MAX_TEXT_CHARS:
        raise DeltaError("текст дельты слишком велик")
    # A 2-digit year is optional in the flight line; without it the year is
    # taken from the schedule, but ONLY when the schedule spans a single
    # year — otherwise the mapping is ambiguous (a January delta against a
    # December..January schedule could land a year off) and we demand it.
    sched_years = _schedule_years(payload)
    year_default = min(sched_years) if len(sched_years) == 1 else None
    blocks: list[list[str]] = [[]]
    for line in text.splitlines():
        line = line.strip()
        if not line:
            if blocks[-1]:
                blocks.append([])
            continue
        blocks[-1].append(line)
    actions = []
    errors = []
    for bi, block in enumerate(b for b in blocks if b):
        lines = [ln for ln in block
                 if ln.upper() not in ("ASM", "SSM", "UTC", "LT")]
        if not lines:
            continue
        act = lines[0].upper()
        if act not in ACTIONS:
            errors.append(f"блок {bi + 1}: неизвестное действие "
                          f"{lines[0]!r} (поддержаны: {', '.join(ACTIONS)})")
            continue
        if len(lines) < 2:
            errors.append(f"блок {bi + 1}: нет строки рейса")
            continue
        m = _FLIGHT_RE.match(lines[1].upper())
        if not m:
            errors.append(f"блок {bi + 1}: строка рейса {lines[1]!r} не "
                          "разобралась (ожидается SU100/01MAR26)")
            continue
        fn, day, mon, yy = m.groups()
        if mon not in _MONTHS:
            errors.append(f"блок {bi + 1}: неизвестный месяц {mon!r}")
            continue
        if yy:
            year = 2000 + int(yy)
        elif year_default is not None:
            year = year_default
        else:
            errors.append(f"блок {bi + 1}: год не указан, а расписание "
                          "охватывает несколько лет — укажите год "
                          "(например SU100/01MAR26)")
            continue
        try:
            date = datetime(year, _MONTHS[mon], int(day)).date()
        except ValueError as exc:
            errors.append(f"блок {bi + 1}: некорректная дата: {exc}")
            continue
        a: dict = {"action": act, "flightNumber": fn,
                   "date": date.isoformat()}
        rest = lines[2:]
        if act == "CNL":
            if rest:
                errors.append(f"блок {bi + 1}: CNL не ожидает "
                              "дополнительных строк")
                continue
        elif act == "TIM":
            if len(rest) != 1 or not _TIME_RE.match(rest[0]):
                errors.append(f"блок {bi + 1}: TIM ожидает строку "
                              "'HHMM HHMM'")
                continue
            dep, arr = _TIME_RE.match(rest[0]).groups()
            if not (_valid_hhmm(dep) and _valid_hhmm(arr)):
                errors.append(f"блок {bi + 1}: недопустимое время "
                              f"(HH<24, MM<60): {dep} {arr}")
                continue
            a["dep_hhmm"], a["arr_hhmm"] = dep, arr
        elif act == "EQT":
            if len(rest) != 1 or not rest[0]:
                errors.append(f"блок {bi + 1}: EQT ожидает строку с "
                              "типом ВС")
                continue
            a["aircraftType"] = rest[0]
        elif act == "NEW":
            if len(rest) != 1 or not _SEG_RE.match(rest[0].upper()):
                errors.append(f"блок {bi + 1}: NEW ожидает сегмент "
                              "'SVO LED 0800 1000 [A320]'")
                continue
            dep_ap, arr_ap, dep_t, arr_t, ac = _SEG_RE.match(
                rest[0].upper()).groups()
            if not (_valid_hhmm(dep_t) and _valid_hhmm(arr_t)):
                errors.append(f"блок {bi + 1}: недопустимое время "
                              f"(HH<24, MM<60): {dep_t} {arr_t}")
                continue
            a.update({"departureAirport": dep_ap, "arrivalAirport": arr_ap,
                      "dep_hhmm": dep_t, "arr_hhmm": arr_t,
                      "aircraftType": ac})
        actions.append(a)
    if errors:
        raise DeltaError("; ".join(errors))
    if not actions:
        raise DeltaError("в тексте не найдено ни одного действия")
    return actions


def _valid_hhmm(hhmm: str) -> bool:
    return (len(hhmm) == 4 and hhmm.isdigit()
            and int(hhmm[:2]) < 24 and int(hhmm[2:]) < 60)


def _hhmm_iso(date_iso: str, hhmm: str) -> str:
    return f"{date_iso}T{hhmm[:2]}:{hhmm[2:]}:00"


def apply_schedule(payload: dict, actions: list[dict]) -> tuple[dict, dict]:
    """(new_payload, summary). Idempotent per action: cancelling an absent
    flight / re-adding an existing one is counted as skipped, not an error."""
    out = copy.deepcopy(payload)
    flights = out.setdefault("flights", [])
    summary = {"cancelled": 0, "retimed": 0, "equipment": 0, "created": 0,
               "skipped": [], "assignments_removed": 0,
               "seats_created": 0, "leg_ids_stamped": 0}

    def find(fn: str, date_iso: str):
        for i, f in enumerate(flights):
            if (f.get("flightNumber") == fn
                    and str(f.get("departureUTCDateTime") or "")
                    .startswith(date_iso)):
                return i
        return None

    for a in actions:
        fn, date_iso = a["flightNumber"], a["date"]
        idx = find(fn, date_iso)
        if a["action"] == "CNL":
            if idx is None:
                summary["skipped"].append(f"CNL {fn}/{date_iso}: рейса нет")
                continue
            cancelled_leg = flights[idx].get("flightLegId")
            flights.pop(idx)
            # Slot removal handles MIXED materialization honestly (review
            # 2026-07-18, findings 2/6/10/14): a slot WITH flightLegId is
            # removed precisely (== the cancelled leg's id); a slot WITHOUT
            # it falls back to the legacy conservative number rule (kept
            # until no other dated flight carries the number) — so importer
            # payloads whose repeated-number slots could not be stamped are
            # never stranded referencing a vanished flight.
            still = any(f.get("flightNumber") == fn for f in flights)

            def _keep(s):
                slid = s.get("flightLegId")
                if slid and cancelled_leg:
                    return slid != cancelled_leg
                if s.get("flight") == fn:
                    return still
                return True

            before = len(out.get("flightAssignments") or [])
            out["flightAssignments"] = [
                s for s in out.get("flightAssignments") or [] if _keep(s)]
            summary["assignments_removed"] += before - len(
                out["flightAssignments"])
            summary["cancelled"] += 1
        elif a["action"] == "TIM":
            if idx is None:
                summary["skipped"].append(f"TIM {fn}/{date_iso}: рейса нет")
                continue
            f = flights[idx]
            dep = _hhmm_iso(date_iso, a["dep_hhmm"])
            arr = _hhmm_iso(date_iso, a["arr_hhmm"])
            if arr <= dep:                       # прилёт на следующий день
                nxt = (datetime.fromisoformat(date_iso)
                       + timedelta(days=1)).date().isoformat()
                arr = _hhmm_iso(nxt, a["arr_hhmm"])
            f["departureUTCDateTime"], f["arrivalUTCDateTime"] = dep, arr
            summary["retimed"] += 1
        elif a["action"] == "EQT":
            if idx is None:
                summary["skipped"].append(f"EQT {fn}/{date_iso}: рейса нет")
                continue
            flights[idx]["aircraftType"] = a["aircraftType"]
            summary["equipment"] += 1
        elif a["action"] == "NEW":
            if idx is not None:
                summary["skipped"].append(
                    f"NEW {fn}/{date_iso}: рейс уже существует")
                continue
            dep = _hhmm_iso(date_iso, a["dep_hhmm"])
            arr = _hhmm_iso(date_iso, a["arr_hhmm"])
            if arr <= dep:
                nxt = (datetime.fromisoformat(date_iso)
                       + timedelta(days=1)).date().isoformat()
                arr = _hhmm_iso(nxt, a["arr_hhmm"])
            rec = {"flightNumber": fn,
                   "departureAirport": a["departureAirport"],
                   "departureUTCDateTime": dep,
                   "arrivalAirport": a["arrivalAirport"],
                   "arrivalUTCDateTime": arr}
            if a.get("aircraftType"):
                rec["aircraftType"] = a["aircraftType"]
            # P0-3 (аудит документации 2026-07-20): NEW обязан создавать и
            # МЕСТА ЭКИПАЖА — рейс без flightAssignments невидим спросу
            # сертификата покрытия, и старое решение оставалось «полностью
            # покрытым» при фактически необеспеченном рейсе. Шаблон мест и
            # дисциплина идентичностей — в _new_flight_seats.
            occurrences = [f for f in flights
                           if isinstance(f, dict)
                           and f.get("flightNumber") == fn]
            lid, seats, stamped = _new_flight_seats(
                out, flights, occurrences, fn, dep)
            if lid:
                rec["flightLegId"] = lid
            elif not occurrences:
                # уникальный номер: прежняя фаза-3 конвенция — штамп только
                # в полностью материализованном payload, коллизия формулы =
                # молчаливый пропуск (id — идентичность, не формула)
                if flights and all(isinstance(f, dict)
                                   and f.get("flightLegId")
                                   for f in flights):
                    cand = f"{fn}|{dep}"
                    if cand not in {f.get("flightLegId") for f in flights}:
                        rec["flightLegId"] = cand
                        for s in seats:
                            s["flightLegId"] = cand
            flights.append(rec)
            out.setdefault("flightAssignments", []).extend(seats)
            summary["created"] += 1
            summary["seats_created"] += len(seats)
            summary["leg_ids_stamped"] += stamped
    return out, summary


def _new_flight_seats(out: dict, flights: list, occurrences: list,
                      fn: str, dep: str) -> tuple[str | None, list, int]:
    """Места экипажа и идентичности для NEW-рейса (P0-3).

    Возврат: (flightLegId нового рейса или None, список новых слотов,
    сколько СУЩЕСТВУЮЩИХ записей доштамповано flightLegId).

    Дисциплина идентичностей: если номер становится ПОВТОРНЫМ, штамповка
    обязательна — иначе экземпляр станет rekey-блокированным на границе
    солвера; неоднозначные случаи — громкий отказ, не молчаливая порча."""
    slots_all = out.get("flightAssignments") or []
    use_csid = any(isinstance(s, dict) and s.get("crewSlotId")
                   for s in slots_all)

    def _slots_of(occ):
        olid = occ.get("flightLegId")
        if olid:
            return [s for s in slots_all
                    if isinstance(s, dict) and s.get("flightLegId") == olid]
        # без legId ссылка однозначна только пока номер уникален
        return [s for s in slots_all
                if isinstance(s, dict) and s.get("flight") == fn
                and not s.get("flightLegId")]

    def _template_of(slist):
        return tuple(str(s.get("requiredSkill") or "")
                     for s in sorted(
                         slist, key=lambda s: (s.get("indexInFlight") is None,
                                               s.get("indexInFlight"))))

    stamped = 0
    new_lid: str | None = None
    if occurrences:
        # номер станет повторным — обязательные идентичности
        if "@" in fn:
            raise DeltaError(
                f"NEW {fn}: символ «@» в номере рейса зарезервирован для "
                f"внутренних имён — повторный номер с «@» не поддерживается")
        missing = [o for o in occurrences if not o.get("flightLegId")]
        if len(occurrences) >= 2 and missing:
            raise DeltaError(
                f"NEW {fn}: номер уже повторяется без материализованного "
                f"flightLegId — сначала материализуйте идентичности "
                f"(повторный импорт), затем применяйте дельту")
        existing_lids = {f.get("flightLegId") for f in flights
                         if isinstance(f, dict) and f.get("flightLegId")}
        if len(occurrences) == 1 and missing:
            # номер был уникален: доштамповать существующее вхождение и его
            # слоты той же формулой (однозначный случай; счётчик виден в
            # предпросмотре и журнале)
            occ = occurrences[0]
            olid = f"{fn}|{occ.get('departureUTCDateTime')}"
            if olid in existing_lids:
                raise DeltaError(
                    f"NEW {fn}: синтетический flightLegId {olid!r} для "
                    f"существующего вхождения совпадает с авторским — "
                    f"материализуйте идентичности импортом")
            occ["flightLegId"] = olid
            stamped += 1
            for s in slots_all:
                if (isinstance(s, dict) and s.get("flight") == fn
                        and not s.get("flightLegId")):
                    s["flightLegId"] = olid
                    stamped += 1
            existing_lids.add(olid)
        new_lid = f"{fn}|{dep}"
        if new_lid in existing_lids:
            raise DeltaError(
                f"NEW {fn}: синтетический flightLegId {new_lid!r} нового "
                f"вхождения совпадает с существующим — конфликт идентичности")
        # шаблон мест — по вхождениям этого номера; расхождение = отказ
        templates = {_template_of(_slots_of(o)) for o in occurrences}
        templates.discard(tuple())
        if len(templates) > 1:
            raise DeltaError(
                f"NEW {fn}: места вхождений номера различаются — образец "
                f"состава неоднозначен")
        template = next(iter(templates), None)
    else:
        template = None

    if template is None:
        # новый (или бес-слотный) номер: доминирующий паттерн экземпляра.
        # Ревью P0 (2026-07-20): слоты БЕЗ flightLegId, чей номер повторяется
        # среди flights, исключаются из голосования — группа по номеру была
        # бы конкатенацией всех вхождений (шаблон-монстр на десятки мест),
        # а не образцом состава одного рейса.
        fn_counts: dict = {}
        for f in flights:
            if isinstance(f, dict) and isinstance(f.get("flightNumber"),
                                                  str):
                fn_counts[f["flightNumber"]] = fn_counts.get(
                    f["flightNumber"], 0) + 1
        by_leg: dict = {}
        for s in slots_all:
            if not isinstance(s, dict):
                continue
            key = s.get("flightLegId")
            if not key:
                sfn = s.get("flight")
                if sfn is None or fn_counts.get(sfn, 0) > 1:
                    continue
                key = sfn
            by_leg.setdefault(key, []).append(s)
        patterns: dict[tuple, int] = {}
        for slist in by_leg.values():
            patterns[_template_of(slist)] = patterns.get(
                _template_of(slist), 0) + 1
        patterns.pop(tuple(), None)
        if not patterns:
            raise DeltaError(
                f"NEW {fn}: в экземпляре нет ни одного места экипажа — "
                f"состав нового рейса синтезировать не из чего; загрузите "
                f"данные с flightAssignments")
        # тай-брейк: чаще; при равенстве — БОЛЬШЕ мест (перекрытие честнее
        # непокрытия); затем лексикографически (детерминизм)
        template = sorted(patterns.items(),
                          key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))[0][0]

    existing_ids = {s.get("id") for s in slots_all if isinstance(s, dict)}
    base = new_lid or f"{fn}|{dep}"
    seats = []
    for i, skill in enumerate(template):
        sid = f"{base}#{i}"
        while sid in existing_ids:
            sid = sid + "+"
        existing_ids.add(sid)
        seat = {"id": sid, "flight": fn, "indexInFlight": i,
                "requiredSkill": skill}
        if new_lid:
            seat["flightLegId"] = new_lid
        if use_csid:
            seat["crewSlotId"] = sid
        seats.append(seat)
    return new_lid, seats, stamped


# ------------------------------------------------------------ crew deltas

def _split_list(v: str) -> list[str]:
    return [s.strip() for s in re.split(r"[;|]", v) if s.strip()]


def _coerce_crew_field(h: str, v) -> object | None:
    """One raw value -> its canonical shape. Lists arrive already-typed
    from JSON (kept as-is) or as a delimited string from CSV (split)."""
    if h in CREW_LIST_FIELDS:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return _split_list(str(v))
    s = str(v).strip()
    if s == "":
        return None
    if h == "seniority":
        try:
            return float(s)
        except ValueError:
            raise DeltaError(f"seniority {s!r} — не число")
    if h == "isReserve":
        if isinstance(v, bool):
            return v
        return s.lower() in ("1", "true", "yes", "да")
    return s


def _crew_from_json(raw: bytes) -> list[dict] | None:
    """JSON path: a top-level list of employee records (or a dict holding
    one such list). Returns None if the bytes are not that shape — the
    caller falls back to CSV. List fields stay lists (no stringification)."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    records = None
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list) and v]
        if len(lists) == 1:
            records = lists[0]
    if not records or not all(isinstance(r, dict) for r in records):
        return None
    recs = []
    for r in records:
        unknown = [k for k in r if k not in CREW_FIELDS]
        if unknown:
            raise DeltaError(
                "кадровая дельта требует КАНОНИЧЕСКИХ полей; неизвестны: "
                + ", ".join(unknown[:8]))
        rec: dict = {}
        for k, v in r.items():
            cv = _coerce_crew_field(k, v)
            if cv is not None:
                rec[k] = cv
        if rec.get("id"):
            recs.append(rec)
    return recs


def parse_crew_feed(name: str, raw: bytes) -> list[dict]:
    """CSV (canonical headers) or JSON list of employee records. JSON list
    fields are preserved as lists; CSV list fields are split on ; or |."""
    lower = name.lower()
    if lower.endswith(".json") or raw.lstrip()[:1] in (b"{", b"["):
        recs = _crew_from_json(raw)
        if recs is not None:
            if not recs:
                raise DeltaError("в кадровой дельте нет ни одной записи с id")
            return recs
    from . import data_import
    parsed = data_import.parse_file(name, raw)
    if parsed["kind"] == "canonical":
        raise DeltaError("это полный канонический payload — используйте "
                         "обычную загрузку, а не дельту")
    headers = parsed["headers"]
    unknown = [h for h in headers if h not in CREW_FIELDS]
    if unknown:
        raise DeltaError(
            "кадровая дельта требует КАНОНИЧЕСКИХ имён колонок; неизвестны: "
            + ", ".join(unknown[:8])
            + " — для произвольных выгрузок используйте LLM-импорт")
    if "id" not in headers:
        raise DeltaError("в кадровой дельте нет колонки id")
    recs = []
    for row in parsed["rows"]:
        rec = {}
        for i, h in enumerate(headers):
            cv = _coerce_crew_field(h, row[i] if i < len(row) else "")
            if cv is not None:
                rec[h] = cv
        if rec.get("id"):
            recs.append(rec)
    if not recs:
        raise DeltaError("в кадровой дельте нет ни одной записи с id")
    return recs


def crew_diff(payload: dict, records: list[dict]) -> dict:
    by_id = {str(e.get("id")): e for e in payload.get("employees") or []}
    added, updated, unchanged = [], [], []
    for r in records:
        eid = str(r["id"])
        cur = by_id.get(eid)
        if cur is None:
            added.append(eid)
            continue
        changed = {k: v for k, v in r.items()
                   if k != "id" and cur.get(k) != v}
        (updated if changed else unchanged).append(
            {"id": eid, "fields": sorted(changed)} if changed else eid)
    feed_ids = {str(r["id"]) for r in records}
    missing = sorted(i for i in by_id if i not in feed_ids)
    return {"added": added, "updated": updated, "unchanged": unchanged,
            "missing_in_feed": missing}


def apply_crew(payload: dict, records: list[dict]) -> tuple[dict, dict]:
    """Add/update only; NOTHING is deleted (missing crews are the human's
    decision — «мёртвые души» не удаляются молча)."""
    out = copy.deepcopy(payload)
    emps = out.setdefault("employees", [])
    by_id = {str(e.get("id")): e for e in emps}
    diff = crew_diff(payload, records)
    for r in records:
        eid = str(r["id"])
        cur = by_id.get(eid)
        if cur is None:
            rec = {"homeAirport": None, "skills": []}
            rec.update(r)
            emps.append(rec)
        else:
            cur.update({k: v for k, v in r.items() if k != "id"})
    return out, diff


# ------------------------------------------------------------- application

def _persist(inst: dict, payload: dict) -> dict:
    """validate -> write; an invalid post-delta payload is NEVER saved."""
    import time
    # Правка времени рейса оператором — путь записи набора наравне с
    # загрузкой, поэтому канонизация обязана пройти и здесь (внешний аудит
    # R9, P0-03). Запись идёт через временный файл с повторами, поэтому
    # общая граница `registry.write_payload` не подходит — берём её
    # содержательную половину.
    registry.canonicalize_for_store(payload, "schedule_delta")
    validation = registry.validate_payload(payload)
    if not validation.get("ok"):
        raise DeltaError("после дельты payload невалиден: "
                         + "; ".join(validation.get("errors")[:5]))
    path = registry.instance_payload_path(inst)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    # tmp.replace can hit WinError 5 transiently on Windows (a scanner/indexer
    # holding the target for a moment) — retry like ops_log/_evolution_bench.
    for attempt in range(5):
        try:
            tmp.replace(path)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)
    # Windows mtime granularity (~15.6 ms): a second delta persisted within
    # the same timer tick would otherwise be read back STALE from the
    # mtime-keyed cache — invalidate explicitly (lost-update race).
    registry.invalidate_payload_cache(path)
    return validation


def receive_schedule(inst: dict, text: str, by: str,
                     apply: bool = False) -> dict:
    if inst.get("source") != "upload":
        raise DeltaError("дельты применимы только к загруженным наборам "
                         "(сид-данные — эталонные)")
    text = str(text or "")
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    def run():
        if apply and _seen_sha(inst["id"], sha):
            raise DeltaError("эта дельта уже была применена (идентичный "
                             "отпечаток SHA-256)")
        payload = registry.load_payload(registry.instance_payload_path(inst))
        actions = parse_asm(text, payload)
        new_payload, summary = apply_schedule(payload, actions)
        out = {"kind": "schedule", "actions": len(actions),
               "summary": summary, "sha256": sha, "applied": False}
        if apply:
            _persist(inst, new_payload)
            _append_journal(inst["id"], {
                "id": "d" + secrets.token_hex(4), "at": _now(), "by": by,
                "kind": "schedule", "sha256": sha,
                "actions": len(actions), "summary": summary})
            out["applied"] = True
        return out

    if not apply:
        return run()
    with _instance_lock(inst["id"]):
        return run()


def receive_crew(inst: dict, name: str, raw: bytes, by: str,
                 apply: bool = False) -> dict:
    if inst.get("source") != "upload":
        raise DeltaError("дельты применимы только к загруженным наборам "
                         "(сид-данные — эталонные)")
    sha = hashlib.sha256(raw).hexdigest()

    def run():
        if apply and _seen_sha(inst["id"], sha):
            raise DeltaError("эта дельта уже была применена (идентичный "
                             "отпечаток SHA-256)")
        records = parse_crew_feed(name, raw)
        payload = registry.load_payload(registry.instance_payload_path(inst))
        new_payload, diff = apply_crew(payload, records)
        out = {"kind": "crew", "records": len(records), "diff": diff,
               "sha256": sha, "applied": False}
        if apply:
            _persist(inst, new_payload)
            _append_journal(inst["id"], {
                "id": "d" + secrets.token_hex(4), "at": _now(), "by": by,
                "kind": "crew", "sha256": sha, "records": len(records),
                "summary": {"added": len(diff["added"]),
                            "updated": len(diff["updated"]),
                            "missing_in_feed": len(diff["missing_in_feed"])}})
            out["applied"] = True
        return out

    if not apply:
        return run()
    with _instance_lock(inst["id"]):
        return run()
