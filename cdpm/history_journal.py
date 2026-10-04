# -*- coding: utf-8 -*-
"""Журнал фактических назначений → производный раздел истории (1.17.2).

Лист «Журнал назначений» книги операционных данных: одна строка — одна
смена или один день без работы сотрудника до среза (начала горизонта).
Из него код детерминированно считает те же остатки, что лист «История»
даёт готовыми: последняя смена, суммы месяца среза, серии, дни с выходного,
окна (минуты и счётчики), отдых до горизонта — с провенансом на строки
журнала. Принцип 1.17 сохраняется: неизвестное не превращается в ноль —
пустая ячейка даёт null, сотрудник без строк журнала — запись без истории.

Решение владельца 05.09.2026: если данных фактических назначений хватает —
брать их; частично — предупредить и предложить дополнить либо продолжить;
нет — предупредить и предложить ввести либо продолжить с нулями.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from cdpm.data_checks import parse_moment, parse_num

JOURNAL_SHEETS = ("журнал назначений", "журнал", "факт назначений",
                  "journal", "assignments_journal", "assignments journal")
#: Заголовок колонки (нижний регистр) → ключ записи журнала.
JOURNAL_COLUMN_CANON = {
    "табельный номер": "employee_id", "employee_id": "employee_id", "табельный": "employee_id",
    "начало": "start", "start": "start",
    "конец": "end", "end": "end",
    "станция начала": "dep", "станция": "dep", "откуда": "dep", "dep": "dep",
    "станция конца": "arr", "куда": "arr", "arr": "arr",
    "вид": "kind", "тип": "kind", "kind": "kind",
    "налёт, мин": "block_min", "налет, мин": "block_min", "налёт": "block_min",
    "налет": "block_min", "block_min": "block_min",
    "лётное время, мин": "flight_time_min", "летное время, мин": "flight_time_min",
    "лётное, мин": "flight_time_min", "летное, мин": "flight_time_min",
    "flight_time_min": "flight_time_min",
    "посадок": "landings", "landings": "landings",
    "ночная": "night", "night": "night",
}
REQUIRED_KEYS = ("employee_id", "start", "end", "kind")
#: Закрытый словарь вида строки: рабочие (работа, резерв) и дни без работы.
KIND_CANON = {"работа": "work", "смена": "work", "рейс": "work", "полёт": "work",
              "полет": "work", "work": "work", "duty": "work", "flight": "work",
              "резерв": "standby", "standby": "standby", "sby": "standby",
              "выходной": "day_off", "day_off": "day_off", "dayoff": "day_off",
              "отпуск": "leave", "leave": "leave", "vacation": "leave",
              "болезнь": "sick", "больничный": "sick", "sick": "sick"}
DUTY_KINDS = ("work", "standby")
OFF_KINDS = ("day_off", "leave", "sick")
_YES = ("да", "yes", "true", "1", "ночная")
_NO = ("нет", "no", "false", "0", "дневная")
#: Канонические окна в сутках (плюс окна из параметров правил пакета).
CANON_WINDOWS = (7, 14, 28)
ISO = "%Y-%m-%dT%H:%M"


def _cell(row: dict, col) -> str:
    v = row.get(col) if col is not None else None
    return "" if v is None else str(v).strip()


def column_map(columns: list) -> tuple[dict, list]:
    """(канонический ключ → заголовок колонки, неопознанные заголовки)."""
    keys: dict[str, str] = {}
    unknown: list = []
    for col in columns or []:
        key = JOURNAL_COLUMN_CANON.get(str(col).strip().lower())
        if key and key not in keys:
            keys[key] = col
        elif key is None:
            unknown.append(col)
    return keys, unknown


def missing_columns(columns: list) -> list[str]:
    keys, _unknown = column_map(columns)
    return [k for k in REQUIRED_KEYS if k not in keys]


def parse_entry(row: dict, keys: dict, night_window: tuple[int, int] | None
                ) -> tuple[dict | None, list[str]]:
    """Строка журнала → запись {employee_id, start, end (datetime), dep, arr,
    kind, block_min, flight_time_min, landings, night}; проблемы — словами с
    именем колонки (для находки линии данных)."""
    problems: list[str] = []
    eid = _cell(row, keys.get("employee_id"))
    if not eid:
        problems.append(f"«{keys.get('employee_id', 'Табельный номер')}»: табельный номер пуст")
    start = parse_moment(_cell(row, keys.get("start")))
    end = parse_moment(_cell(row, keys.get("end")))
    if start is None:
        problems.append(f"«{keys.get('start', 'Начало')}»: дата-время не читается "
                        f"«{_cell(row, keys.get('start'))}»")
    if end is None:
        problems.append(f"«{keys.get('end', 'Конец')}»: дата-время не читается "
                        f"«{_cell(row, keys.get('end'))}»")
    if start is not None and end is not None and end < start:
        problems.append(f"«{keys.get('end', 'Конец')}»: конец раньше начала")
    raw_kind = _cell(row, keys.get("kind")).lower()
    kind = KIND_CANON.get(raw_kind)
    if kind is None:
        problems.append(f"«{keys.get('kind', 'Вид')}»: вид «{raw_kind}» вне словаря "
                        f"(работа | резерв | выходной | отпуск | болезнь)")
    nums: dict[str, int | None] = {}
    for key in ("block_min", "flight_time_min", "landings"):
        raw = _cell(row, keys.get(key))
        if raw == "" or keys.get(key) is None:
            nums[key] = None
            continue
        n = parse_num(raw)
        if n is None or n < 0 or int(n) != n:
            problems.append(f"«{keys[key]}»: ожидалось целое неотрицательное число, "
                            f"получено «{raw}»")
            nums[key] = None
        else:
            nums[key] = int(n)
    night: bool | None = None
    raw_night = _cell(row, keys.get("night")).lower()
    if raw_night in _YES:
        night = True
    elif raw_night in _NO:
        night = False
    elif raw_night:
        problems.append(f"«{keys.get('night', 'Ночная')}»: ожидалось «да»/«нет», "
                        f"получено «{raw_night}»")
    elif start is not None and night_window is not None and kind == "work":
        # Пустая «Ночная» — по минуте НАЧАЛА смены в окне ночи параметров
        # пакета (то же чтение «первый вылет», что у решателя).
        night = _in_window(start.hour * 60 + start.minute, night_window)
    if problems:
        return None, problems
    return {"employee_id": eid, "start": start, "end": end,
            "dep": _cell(row, keys.get("dep")) or None,
            "arr": _cell(row, keys.get("arr")) or None,
            "kind": kind, "night": night, **nums}, []


def _in_window(minute: int, window: tuple[int, int]) -> bool:
    a, b = window
    if a == b:
        return False
    if a < b:
        return a <= minute < b
    return minute >= a or minute < b


def _dates_touched(e: dict, upto: datetime) -> set:
    """Календарные даты, задетые записью (обрезка по срезу): запись через
    полночь задевает обе даты; конец ровно в полночь новую дату не задевает."""
    start, end = e["start"], min(e["end"], upto)
    if end <= start:
        return {start.date()}
    last = (end - timedelta(minutes=1)).date()
    d, out = start.date(), set()
    while d <= last:
        out.add(d)
        d += timedelta(days=1)
    return out


def _clip_minutes(e: dict, lo: datetime, hi: datetime) -> int:
    a, b = max(e["start"], lo), min(e["end"], hi)
    return max(0, int((b - a).total_seconds() // 60))


def derive_record(entries: list[dict], as_of: datetime, windows: list[int],
                  calendar_days: int | None = None) -> dict:
    """Запись истории сотрудника из его строк журнала (только Конец ≤ срез;
    строки, начинающиеся на срезе и позже, — план, не факт, игнорируются).
    `calendar_days` — глубина посуточного календаря (1.17.2): по нему
    считаются и счётчики окон (unknown-сутки не считаются никуда)."""
    hist = sorted((e for e in entries if e["start"] < as_of), key=lambda e: (e["start"], e["end"]))
    duties = [e for e in hist if e["kind"] in DUTY_KINDS]
    rec: dict = {"month_to_date": None, "rolling": None, "last_duty": None,
                 "series": None, "location": None, "rest_before_horizon_min": None,
                 "calendar": None}
    if not hist:
        return rec
    days = int(calendar_days or max(list(windows) + [28]))
    rec["calendar"] = derive_calendar(hist, as_of, days)
    # Последняя смена и местоположение.
    if duties:
        last = max(duties, key=lambda e: (min(e["end"], as_of), e["start"]))
        end = min(last["end"], as_of)
        rec["last_duty"] = {"end": end.strftime(ISO), "end_station": last["arr"],
                            "work_min": int((end - last["start"]).total_seconds() // 60),
                            "span_min": int((end - last["start"]).total_seconds() // 60),
                            "night": last["night"]}
        rec["rest_before_horizon_min"] = int((as_of - end).total_seconds() // 60)
    last_any = max(hist, key=lambda e: (min(e["end"], as_of), e["start"]))
    rec["location"] = last_any["arr"] or last_any["dep"]
    # Месяц среза.
    month_start = as_of.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    in_month = [e for e in duties if e["end"] > month_start]
    touched_month: set = set()
    for e in in_month:
        touched_month |= {d for d in _dates_touched(e, as_of) if d >= month_start.date()}

    def _sum(items, key):
        """Сумма известных значений; хоть одно неизвестное — сумма неизвестна
        (null, не ноль); без строк работы — известный ноль."""
        if not items:
            return 0
        if any(e.get(key) is None for e in items):
            return None
        return sum(e[key] for e in items)
    works_month = [e for e in in_month if e["kind"] == "work"]
    rec["month_to_date"] = {
        "block_min": _sum(works_month, "block_min"),
        "work_min": sum(_clip_minutes(e, month_start, as_of) for e in in_month),
        "flight_time_min": _sum(works_month, "flight_time_min"),
        "days_worked": len(touched_month),
        "landings": _sum(works_month, "landings"),
    }
    # Серии: рабочие дни — даты, задетые работой/резервом; дни без работы —
    # остальное (и явные выходной/отпуск/болезнь).
    duty_days: set = set()
    for e in duties:
        duty_days |= _dates_touched(e, as_of)
    day_before = (as_of - timedelta(minutes=1)).date()
    run, d = 0, day_before
    while d in duty_days:
        run += 1
        d -= timedelta(days=1)
    consecutive_nights: int | None = 0
    for e in sorted((e for e in duties if e["kind"] == "work"),
                    key=lambda e: e["end"], reverse=True):
        if e["night"] is True:
            consecutive_nights += 1
        elif e["night"] is None:
            consecutive_nights = None      # признак неизвестен — серия неизвестна
            break
        else:
            break
    earliest = min(e["start"] for e in hist).date()
    d, since_off = day_before, 0
    while d in duty_days and d >= earliest:
        since_off += 1
        d -= timedelta(days=1)
    rec["series"] = {"consecutive_duty_days": run,
                     "consecutive_night_duties": consecutive_nights,
                     "days_since_day_off": since_off}
    # Окна: минуты — по строкам с обрезкой по краям окна; счётчики суток —
    # из посуточного календаря (unknown не считается ни работой, ни выходным).
    rolling: dict = {}
    for n in sorted(set(int(w) for w in windows if int(w) > 0)):
        lo = as_of - timedelta(days=n)
        in_win = [e for e in duties if e["end"] > lo]
        rolling[f"last_{n}d_work_min"] = sum(_clip_minutes(e, lo, as_of) for e in in_win)
        works = [e for e in in_win if e["kind"] == "work"]
        rolling[f"last_{n}d_block_min"] = _sum(works, "block_min")
        rolling[f"last_{n}d_flight_time_min"] = _sum(works, "flight_time_min")
        rolling.update(counters_from_calendar(rec["calendar"], n))
    rec["rolling"] = rolling
    return rec


#: Виды суток календаря истории (1.17.2, предложение COSMOS 05.09): известные
#: сутки — по строкам журнала; сутки без строк — unknown (НЕ выходной).
CALENDAR_KIND = {"work": "work", "standby": "reserve", "day_off": "day_off",
                 "leave": "leave", "sick": "sick"}
_KIND_RANK = {"work": 5, "reserve": 4, "sick": 3, "leave": 2, "day_off": 1}


def derive_calendar(entries: list[dict], as_of: datetime, days: int) -> list[dict]:
    """Посуточный календарь за `days` суток до среза: [{date, kind, night}] по
    датам as_of−days … as_of−1. Сутки, задетые сменой, — work; резервом —
    reserve; явные выходной/отпуск/болезнь — day_off/leave/sick; при
    нескольких строках на дату берётся «занятость» (work > reserve > sick >
    leave > day_off); night — истина, если в эти сутки НАЧАЛАСЬ ночная смена
    работы; сутки без строк — unknown."""
    start_day = (as_of - timedelta(days=days)).date()
    last_day = (as_of - timedelta(minutes=1)).date()
    by_date: dict = {}
    for e in entries:
        if e["start"] >= as_of:
            continue
        kind = CALENDAR_KIND.get(e["kind"])
        if kind is None:
            continue
        for d in _dates_touched(e, as_of):
            if d < start_day or d > last_day:
                continue
            cur = by_date.get(d)
            if cur is None or _KIND_RANK[kind] > _KIND_RANK[cur["kind"]]:
                by_date[d] = {"kind": kind, "night": cur["night"] if cur else None}
            if e["kind"] == "work" and e["start"].date() == d and e["night"] is not None:
                by_date[d]["night"] = bool(by_date[d].get("night")) or e["night"]
    out: list[dict] = []
    d = start_day
    while d <= last_day:
        cell = by_date.get(d)
        if cell is None:
            out.append({"date": d.isoformat(), "kind": "unknown", "night": None})
        else:
            night = cell["night"] if cell["kind"] == "work" else (False if cell["kind"] != "unknown" else None)
            out.append({"date": d.isoformat(), "kind": cell["kind"], "night": night})
        d += timedelta(days=1)
    return out


def counters_from_calendar(calendar: list[dict], n: int) -> dict:
    """Счётчики окна из календаря: последние n суток; unknown не считается
    ни рабочим, ни выходным."""
    tail = calendar[-n:] if n < len(calendar) else calendar
    duty = sum(1 for c in tail if c["kind"] in ("work", "reserve"))
    nights = sum(1 for c in tail if c["kind"] == "work" and c.get("night") is True)
    off = sum(1 for c in tail if c["kind"] in ("day_off", "leave", "sick"))
    unknown = sum(1 for c in tail if c["kind"] == "unknown")
    return {f"last_{n}d_duty_days": duty, f"last_{n}d_night_duties": nights,
            f"last_{n}d_days_off": off, f"last_{n}d_unknown_days": unknown}


def depth_days(entries: list[dict], as_of: datetime) -> int | None:
    """Глубина журнала в сутках от самой ранней строки до среза (None — строк
    до среза нет). Считается ПО СОТРУДНИКУ: полнота по глубине — у каждого."""
    past = [e for e in entries if e["start"] < as_of]
    if not past:
        return None
    return (as_of.date() - min(e["start"] for e in past).date()).days


def horizon_row_problems(entries_rows: list[tuple[int, dict]], as_of: datetime) -> list[dict]:
    """Строки журнала относительно среза (проверка «история-журнал-срез»):
    строка, начатая до среза и оконченная после него, — противоречие данных
    (БЛОКИРУЮЩЕЕ: журнал — факт до горизонта); строка, начатая на срезе или
    позже, — план, не факт (СЕРЬЁЗНОЕ: в раздел не попадает)."""
    out: list[dict] = []
    for i, e in entries_rows:
        if e["start"] < as_of < e["end"]:
            out.append({"row": i, "severity": "БЛОКИРУЮЩЕЕ",
                        "text": f"строка журнала пересекает срез {as_of.strftime(ISO)}: "
                                f"конец {e['end'].strftime(ISO)} позже среза — факт до "
                                f"горизонта не может продолжаться после него"})
        elif e["start"] >= as_of:
            out.append({"row": i, "severity": "СЕРЬЁЗНОЕ",
                        "text": f"строка журнала начинается на срезе или позже "
                                f"({e['start'].strftime(ISO)}) — это план, не факт; в раздел "
                                f"истории не попадает"})
    return out


# ── проверки линии данных (шаблоны «история-журнал-…») ──

def check_specs(dataset: dict) -> list[dict]:
    from cdpm.history_section import find_sheet
    tables = dataset.get("tables") or {}
    found = find_sheet(tables, JOURNAL_SHEETS)
    if not found:
        return []
    sid, name, _table = found
    ref = f"{sid}/{name}"
    return [
        {"id": f"C-история-журнал-колонки-{sid}", "basis": {"builtin": "история"},
         "template": "история-журнал-колонки", "params": {"sheet": ref},
         "statement": f"Лист «{name}» ({sid}) несёт обязательные колонки журнала: "
                      f"«Табельный номер», «Начало», «Конец», «Вид»",
         "unit": "лист", "severity": "БЛОКИРУЮЩЕЕ", "status": "скомпилирована"},
        {"id": f"C-история-журнал-табельный-{sid}", "basis": {"builtin": "история"},
         "template": "история-журнал-табельный", "params": {"sheet": ref},
         "statement": f"Табельный номер каждой строки листа «{name}» ({sid}) есть "
                      f"в листе «Экипажи»",
         "unit": "строка", "severity": "БЛОКИРУЮЩЕЕ", "status": "скомпилирована"},
        {"id": f"C-история-журнал-значения-{sid}", "basis": {"builtin": "история"},
         "template": "история-журнал-значения", "params": {"sheet": ref},
         "statement": f"Значения листа «{name}» ({sid}) читаются: «Начало» и «Конец» — "
                      f"дата-время канона, конец не раньше начала, «Вид» из словаря, "
                      f"числа целые неотрицательные, «Ночная» — да/нет",
         "unit": "строка", "severity": "БЛОКИРУЮЩЕЕ", "status": "скомпилирована"},
        {"id": f"C-история-журнал-срез-{sid}", "basis": {"builtin": "история"},
         "template": "история-журнал-срез", "params": {"sheet": ref},
         "statement": f"Строка листа «{name}» ({sid}), начатая до среза (начала "
                      f"горизонта), оканчивается не позже среза — журнал есть факт до "
                      f"горизонта",
         "unit": "строка", "severity": "БЛОКИРУЮЩЕЕ", "status": "скомпилирована"},
        {"id": f"C-история-журнал-план-{sid}", "basis": {"builtin": "история"},
         "template": "история-журнал-план", "params": {"sheet": ref},
         "statement": f"Строки листа «{name}» ({sid}), начатые на срезе или позже, — план, "
                      f"не факт: в раздел истории не попадают (показание)",
         "unit": "строка", "severity": "СЕРЬЁЗНОЕ", "status": "скомпилирована"},
    ]


def run_check(template: str, params: dict, dataset: dict) -> tuple[int, list[dict]]:
    from unicode_data import confusable_hint
    from cdpm.history_section import employee_ids
    tables = dataset.get("tables") or {}
    sid, name = str(params["sheet"]).split("/", 1)
    table = (tables.get(sid) or {}).get(name) or {}
    rows = table.get("rows") or []
    columns = table.get("columns") or []
    keys, _unknown = column_map(columns)
    out: list[dict] = []

    def tgt(i, col=None):
        t = {"sid": sid, "sheet": name, "row": i}
        if col:
            t["column"] = col
        return t

    if template == "история-журнал-колонки":
        missing = missing_columns(columns)
        if missing:
            out.append({"target": tgt(1), "text": f"нет обязательных колонок журнала: "
                        f"{', '.join(missing)}",
                        "evidence": f"{sid}!{name}: колонки {', '.join(str(c) for c in columns)}"})
        return 1, out
    if template == "история-журнал-табельный":
        known = set(employee_ids(tables))
        for i, row in enumerate(rows, 2):
            eid = _cell(row, keys.get("employee_id"))
            if not eid:
                out.append({"target": tgt(i, keys.get("employee_id")),
                            "text": "табельный номер пуст", "evidence": f"{sid}!{name} строка {i}"})
            elif known and eid not in known:
                out.append({"target": tgt(i, keys.get("employee_id")),
                            "text": f"табельный номер «{eid}» отсутствует в листе «Экипажи»"
                                    + confusable_hint(eid, known),
                            "evidence": f"{sid}!{name} строка {i}: «{eid}»"})
        return len(rows), out
    if template == "история-журнал-значения":
        if missing_columns(columns):
            return len(rows), out          # колонки судит своя проверка
        for i, row in enumerate(rows, 2):
            _entry, problems = parse_entry(row, keys, None)
            for p in problems:
                col = p.split("»", 1)[0].lstrip("«")
                out.append({"target": tgt(i, col), "text": p,
                            "evidence": f"{sid}!{name} строка {i}"})
        return len(rows), out
    if template in ("история-журнал-срез", "история-журнал-план"):
        from cdpm.history_section import plan_since
        as_of_iso = plan_since(tables)
        if missing_columns(columns) or not as_of_iso:
            return len(rows), out          # без среза судить нечего; срез — своя проверка
        as_of = datetime.strptime(as_of_iso, ISO)
        want = "БЛОКИРУЮЩЕЕ" if template == "история-журнал-срез" else "СЕРЬЁЗНОЕ"
        entries_rows = []
        for i, row in enumerate(rows, 2):
            entry, problems = parse_entry(row, keys, None)
            if entry is not None:
                entries_rows.append((i, entry))
        for p in horizon_row_problems(entries_rows, as_of):
            if p["severity"] == want:
                out.append({"target": tgt(p["row"], keys.get("end") if want == "БЛОКИРУЮЩЕЕ"
                                          else keys.get("start")),
                            "text": p["text"], "evidence": f"{sid}!{name} строка {p['row']}"})
        return len(rows), out
    raise ValueError(f"неизвестный шаблон журнала {template!r}")
