# -*- coding: utf-8 -*-
"""МЕР-1: экспорт книги операционных данных прогона в формат обмена
COS↔Meridian (dataset) по «Спецификации COS-MERIDIAN v1.17» от
15.06.2026 (передана владельцем 2026-08-21).

ВАЖНО О ПРИРОДЕ ФОРМАТА: спецификация описывает обмен ОПЕРАЦИОННЫМИ
ДАННЫМИ (employees/airports/legs/duties/pairings/groundActivities) и
решением; ПРАВИЛА через неё не передаются — правила живут на стороне
COS «пресетом», в файле есть только ссылка presetId. Поэтому экспорт
Meridian в CDPM — это выгрузка КНИГИ ДАННЫХ прогона, а не свода.

Дисциплина fail-closed: dataset выгружается только ПОЛНЫМ по
обязательным полям спецификации; чего-то нет в книге — честный отказ
с перечнем недостающего (это и есть ответ «какие данные добрать для
передачи в COS»). Ничего не выдумывается: ни стран, ни таймзон, ни
дат рождения.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

FORMAT_VERSION = 1

# Распознавание листов книги данных по названию (русская конвенция
# книг Полярис и заказчика; регистронезависимо, по подстроке).
_SHEET_KINDS = (
    ("legs", ("рейс", "полет", "flight", "leg")),
    ("employees", ("экипаж", "сотруд", "члены", "employee", "crew")),
    ("duties", ("смен", "duty", "дежур")),
    ("airports", ("аэропорт", "airport", "станц")),
)

# Синонимы колонок: целевое поле → варианты заголовков (подстрочно,
# без регистра).
_COLS = {
    "flightNumber": ("номер рейса", "рейс", "flight"),
    "date": ("дата",),
    "depStation": ("вылет", "откуда", "departure"),
    "depTime": ("время вылета",),
    "arrStation": ("прилёт", "прилет", "куда", "arrival"),
    "arrTime": ("время прилёта", "время прилета"),
    "employeeId": ("табельный", "таб", "id", "идентификатор"),
    "base": ("база", "base"),
    "birth": ("дата рождения", "рожден"),
    "sex": ("пол",),
    "citizenship": ("гражданств",),
    "flightTimeLimit": ("лимит налёта", "лимит налета", "сан-норма"),
    "workTimeLimit": ("лимит работы", "рабочее время за"),
    "aircraft": ("тип вс", "вс", "aircraft"),
    "squadron": ("эскадриль", "отряд"),
    "dutyId": ("идентификатор смены", "смена", "duty"),
    "start": ("начало",),
    "end": ("окончание", "конец"),
    "flightTimeCredit": ("налёт смены", "налет смены",
                         "полетное время"),
    "workTimeCredit": ("рабочее время смены", "рабочее время"),
    "iata": ("код", "iata"),
    "country": ("страна",),
    "timezone": ("часовой пояс", "таймзона", "timezone"),
    "category": ("категор",),
    "mvl": ("мвл",),
    "sng": ("снг",),
    "flyByControl": ("провозк",),
}


class MeridianExportError(ValueError):
    """Экспорт не состоялся; недостающее названо перечнем."""

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(
            "книга данных неполна для формата COS-Meridian — "
            f"недостаёт ({len(missing)}): " + "; ".join(missing[:8])
            + ("; …" if len(missing) > 8 else ""))


def _find_sheet(tables: dict, kind_marks: tuple) -> dict | None:
    for sheets in tables.values():
        for name, sheet in sheets.items():
            low = name.lower()
            if any(m in low for m in kind_marks):
                return sheet
    return None


def _col(sheet: dict, field: str) -> str | None:
    for c in sheet.get("columns", []):
        low = str(c).lower()
        if any(m in low for m in _COLS[field]):
            return c
    return None


def _iso(date_val: str, time_val: str = "") -> str | None:
    """Дата (+время) книги → ISO 8601 спецификации; неразборчивое —
    None (в перечень недостающего, не выдумка)."""
    d = str(date_val or "").strip()
    t = str(time_val or "").strip() or "00:00"
    for dfmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            day = datetime.strptime(d.split(" ")[0], dfmt)
            break
        except ValueError:
            continue
    else:
        try:
            day = datetime.fromisoformat(d)
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2}):(\d{2})", t)
    hh, mm = (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    return day.replace(hour=hh % 24, minute=mm).strftime(
        "%Y-%m-%dT%H:%M:%S")


def _carrier_of(flight_number: str) -> str:
    m = re.match(r"^([A-Za-zА-Яа-я0-9]{2})", str(flight_number or ""))
    return m.group(1).upper() if m else ""


def build_dataset(tables: dict, *, preset_id: str,
                  dataset_type: str = "roster",
                  division: str = "FD",
                  description: str = "") -> dict:
    """Книга данных (data-freeze tables) → dataset спецификации.
    Возвращает полный валидный dataset либо поднимает
    MeridianExportError с перечнем недостающего."""
    missing: list[str] = []
    if not (preset_id or "").strip():
        missing.append("presetId — идентификатор пресета правил на "
                       "стороне COS (задаётся при экспорте)")

    legs_sheet = _find_sheet(tables, dict(_SHEET_KINDS)["legs"])
    emp_sheet = _find_sheet(tables, dict(_SHEET_KINDS)["employees"])
    duty_sheet = _find_sheet(tables, dict(_SHEET_KINDS)["duties"])
    apt_sheet = _find_sheet(tables, dict(_SHEET_KINDS)["airports"])

    legs: list[dict] = []
    airports_seen: set[str] = set()
    times: list[str] = []
    if legs_sheet is None:
        missing.append("лист рейсов (legs) — в книге не найден")
    else:
        cols = {f: _col(legs_sheet, f) for f in
                ("flightNumber", "date", "depStation", "depTime",
                 "arrStation", "arrTime")}
        for f in ("flightNumber", "date", "depStation", "arrStation"):
            if not cols[f]:
                missing.append(f"лист рейсов: колонка {f} не "
                               f"распознана")
        if all(cols[f] for f in ("flightNumber", "date",
                                 "depStation", "arrStation")):
            counters: dict[tuple, int] = {}
            for i, row in enumerate(legs_sheet.get("rows", []), 1):
                num = str(row.get(cols["flightNumber"]) or "").strip()
                if not num:
                    continue
                start = _iso(row.get(cols["date"]),
                             row.get(cols["depTime"] or "", ""))
                end = _iso(row.get(cols["date"]),
                           row.get(cols["arrTime"] or "", ""))
                if not start or not end:
                    missing.append(f"лист рейсов, строка {i}: дата/"
                                   f"время не разобраны")
                    continue
                dep = str(row.get(cols["depStation"]) or "").strip()
                arr = str(row.get(cols["arrStation"]) or "").strip()
                key = (num, start[:10])
                counters[key] = counters.get(key, 0) + 1
                n = counters[key]
                day = datetime.fromisoformat(start).strftime(
                    "%d.%m.%Y")
                legs.append({
                    "legId": f"{num}${day}${n}",
                    "flightNumber": num, "legNumber": n,
                    "start": start, "end": end,
                    "departureStation": dep, "arrivalStation": arr,
                    "carrier": _carrier_of(num), "fleet": str(
                        row.get(_col(legs_sheet, "aircraft") or "",
                                "") or "").strip() or "UNKNOWN",
                    "properties": "REGULAR", "serviceType": "J",
                    "deadhead": False, "dutyCrewTypes": {},
                })
                airports_seen |= {dep, arr}
                times += [start, end]

    employees: list[dict] = []
    if emp_sheet is None:
        missing.append("лист экипажей (employees) — в книге не найден")
    else:
        req = ("employeeId", "base", "birth", "sex", "citizenship",
               "flightTimeLimit", "workTimeLimit", "aircraft",
               "squadron")
        cols = {f: _col(emp_sheet, f) for f in req}
        for f in req:
            if not cols[f]:
                missing.append(
                    f"лист экипажей: колонка {f} не распознана — "
                    f"обязательное поле спецификации")
        if all(cols.values()):
            for i, row in enumerate(emp_sheet.get("rows", []), 1):
                eid = str(row.get(cols["employeeId"]) or "").strip()
                if not eid:
                    continue
                birth = _iso(row.get(cols["birth"]))
                if not birth:
                    missing.append(f"лист экипажей, строка {i}: дата "
                                   f"рождения не разобрана")
                    continue
                ac = str(row.get(cols["aircraft"]) or "").strip()
                base = str(row.get(cols["base"]) or "").strip()
                employees.append({
                    "employeeId": eid, "base": base,
                    "birth": birth[:10],
                    "sex": str(row.get(cols["sex"]) or "").strip(),
                    "citizenship": str(
                        row.get(cols["citizenship"]) or "").strip(),
                    "flightTimeLimit": int(
                        row.get(cols["flightTimeLimit"]) or 0),
                    "workTimeLimit": int(
                        row.get(cols["workTimeLimit"]) or 0),
                    "preferredAircraftType": ac,
                    "squadron": str(
                        row.get(cols["squadron"]) or "").strip(),
                    "accumulatedFlightTime": {ac: 0} if ac else {},
                    "fleets": [], "features": [], "extra": [],
                })
                airports_seen.add(base)

    airports: list[dict] = []
    apt_rows = {str(r.get(_col(apt_sheet, "iata") or "", "")
                    or "").strip(): r
                for r in (apt_sheet or {}).get("rows", [])} \
        if apt_sheet and _col(apt_sheet, "iata") else {}
    for code in sorted(a for a in airports_seen if a):
        row = apt_rows.get(code)
        if row is None:
            missing.append(
                f"аэропорт {code}: нет строки в листе аэропортов — "
                f"обязательные поля спецификации (country, timezone, "
                f"category, flyByControl, mvl, sng) взять неоткуда")
            continue
        got = {}
        for f, target in (("country", "country"),
                          ("timezone", "timezone"),
                          ("category", "category"),
                          ("flyByControl", "flyByControl")):
            c = _col(apt_sheet, f)
            v = str(row.get(c) or "").strip() if c else ""
            if not v and f in ("country", "timezone"):
                missing.append(f"аэропорт {code}: пустое обязательное "
                               f"поле {target}")
            got[target] = v
        def _flag(f):
            c = _col(apt_sheet, f)
            return str(row.get(c) or "").strip().lower() in (
                "да", "true", "1", "yes") if c else False
        airports.append({
            "IATA": code, "country": got["country"],
            "timezone": got["timezone"],
            "category": got["category"],
            "flyByControl": got["flyByControl"] or "NONE",
            "mvl": _flag("mvl"), "sng": _flag("sng"),
        })

    duties: list[dict] = []
    if duty_sheet is not None:
        cols = {f: _col(duty_sheet, f) for f in
                ("dutyId", "start", "end", "flightTimeCredit",
                 "workTimeCredit")}
        for f in ("flightTimeCredit", "workTimeCredit"):
            if not cols[f]:
                missing.append(f"лист смен: колонка {f} не распознана "
                               f"— обязательное поле спецификации")
        # Смены без пейрингов спецификации не собрать: pairingId
        # обязателен — фаза А выгружает пустые duties/pairings
        # (законно для расчёта пейрингов: их построит COS).

    if missing:
        raise MeridianExportError(missing)

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "datasetId": str(uuid.uuid4()).upper(),
        "presetId": preset_id.strip(),
        "description": description or "Экспорт CDPM",
        "format": FORMAT_VERSION,
        "type": dataset_type, "division": division,
        "planSince": min(times) if times else now,
        "planUntil": max(times) if times else now,
        "created": now,
        "employees": employees, "airports": airports,
        "legs": legs, "duties": duties, "pairings": [],
        "groundActivities": [],
    }


def export_meridian(tables: dict, **kw) -> str:
    return json.dumps(build_dataset(tables, **kw),
                      ensure_ascii=False, indent=2) + "\n"
