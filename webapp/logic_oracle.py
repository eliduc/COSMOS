# -*- coding: utf-8 -*-
"""Оракул порождённых форм ступени 3 — независимое прочтение на Python.

Правило главы 6 дизайна: два прочтения держат друг друга. Для каждой
формы первой волны здесь живёт счёт по набору и решению, не знающий ни
одной константы решателя и ни одного имени ограничения; приёмка
требует равенства: вклад порождённого ограничения в жёсткий счёт равен
минус-сумме нарушений оракула, умноженной на вес из отчёта прогона.

Форма DAYS_OFF_PER_MONTH_MIN — «выходных в месяц не менее N».
Прочтение (то же, что объявлено решателем в отчёте, слово в слово по
смыслу): выходной — календарные сутки UTC, которых не касается ни одно
назначение сотрудника (даты вылета и прилёта включительно); дни
недоступности — выходные; клетка — сотрудник × ПОЛНЫЙ календарный
месяц горизонта набора (от первого вылета до последнего прилёта);
неполные месяцы вне оценки; сотрудник без назначений в месяце имеет все
дни месяца выходными.
"""

from __future__ import annotations

import calendar
from datetime import date

from webapp.reserve_oracle import busy_days, horizon

READING_DAYS_OFF = ("выходной — сутки UTC без единого назначения сотрудника (даты вылета "
                    "и прилёта включительно); дни недоступности — выходные; клетка — "
                    "сотрудник × полный календарный месяц горизонта; неполные месяцы вне "
                    "оценки")


def _code(ap) -> str:
    code = ap.get("code") if isinstance(ap, dict) else ap
    return str(code or "").strip().upper()


def full_months(days: list[date]) -> list[tuple[int, int]]:
    """(год, месяц) для месяцев, целиком лежащих в перечне суток горизонта."""
    if not days:
        return []
    have = set(days)
    months = sorted({(d.year, d.month) for d in days})
    out = []
    for y, m in months:
        n = calendar.monthrange(y, m)[1]
        if all(date(y, m, k) in have for k in range(1, n + 1)):
            out.append((y, m))
    return out


def partial_months(days: list[date]) -> list[str]:
    full = set(full_months(days))
    return ["%04d-%02d" % (y, m) for (y, m) in sorted({(d.year, d.month) for d in days})
            if (y, m) not in full]


def _history_of(e: dict | None) -> dict:
    """Плоская запись истории сотрудника набора (ревизия 1.17; имена
    HISTORY_FIELDS); {} — истории нет."""
    h = (e or {}).get("history")
    return h if isinstance(h, dict) else {}


def _history_month(e: dict | None) -> tuple[int, int] | None:
    """(год, месяц) среза истории у сотрудника с записью истории."""
    from datetime import datetime
    if not _history_of(e):
        return None
    s = str((e or {}).get("historyAsOf") or "")[:10]
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return (d.year, d.month)


def _history_last_duty_end(e: dict | None):
    """Конец последней смены истории как момент; None — нет."""
    from datetime import datetime
    v = _history_of(e).get("historyLastDutyEnd")
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v)[:19].replace(" ", "T"))
    except ValueError:
        return None


def days_off_per_month(payload: dict, solved: dict, bound: int,
                       base_scope: str | None = None) -> list[dict]:
    """Строки (сотрудник, месяц): занято суток, выходных, норма, нехватка.

    Ревизия 1.17 (решение владельца 05.09.2026): месяц среза истории —
    клетка сотрудника с историей, когда месяц начат до горизонта и
    горизонт доходит до его конца; занятые сутки = отработанные до среза
    (historyMonthDaysWorked, поле carry_days строки) + занятые сутки
    горизонта. Без истории неполный месяц вне оценки — как у решателя."""
    from datetime import date
    days = horizon(payload)
    months = full_months(days)
    busy = busy_days(payload, solved)
    lo, hi = (min(days), max(days)) if days else (None, None)
    rows: list[dict] = []
    for e in payload.get("employees") or []:
        if base_scope and _code(e.get("homeAirport")) != base_scope.upper():
            continue
        emp_id = str(e.get("id"))
        mine = busy.get(emp_id, set())
        cells: list[tuple[tuple[int, int], int]] = [((y, m), 0) for (y, m) in months]
        hm = _history_month(e)
        if hm is not None and lo is not None and hm not in months:
            y, m = hm
            first = date(y, m, 1)
            last = date(y, m, calendar.monthrange(y, m)[1])
            if first < lo and last <= hi:
                cells.append((hm, int(_history_of(e).get("historyMonthDaysWorked") or 0)))
        for (y, m), carry in cells:
            n = calendar.monthrange(y, m)[1]
            used = sum(1 for d in mine if d.year == y and d.month == m)
            off = n - (used + carry)
            row = {"employee": emp_id, "month": "%04d-%02d" % (y, m),
                   "busy_days": used, "days_off": off, "bound": int(bound),
                   "shortfall": max(0, int(bound) - off)}
            if carry:
                row["carry_days"] = carry
            rows.append(row)
    return rows


def shortfall_total(rows: list[dict]) -> int:
    return sum(int(r.get("shortfall") or 0) for r in rows)


def expected_hard(rows: list[dict], weight: int) -> int:
    """Ожидаемый вклад порождённого ограничения в жёсткий счёт: −вес × Σ нехватки."""
    return -int(weight) * shortfall_total(rows)

READING_CONSECUTIVE = ("рабочий день — сутки UTC, которых касается хотя бы одно назначение "
                       "(две смены в одни сутки — один день); серия — подряд идущие рабочие "
                       "сутки; штраф за каждый день сверх границы в каждой серии; серия за "
                       "границами горизонта видна только внутри него")


def consecutive_runs(payload: dict, solved: dict, bound: int,
                     base_scope: str | None = None) -> list[dict]:
    """Строки (сотрудник, серия): начало, дней подряд, граница, превышение."""
    busy = busy_days(payload, solved)
    rows: list[dict] = []
    for e in payload.get("employees") or []:
        if base_scope and _code(e.get("homeAirport")) != base_scope.upper():
            continue
        emp_id = str(e.get("id"))
        days = sorted(busy.get(emp_id, set()))
        run: list = []
        for d in days + [None]:
            if run and (d is None or (d - run[-1]).days != 1):
                rows.append({"employee": emp_id, "start": run[0].isoformat(),
                             "days": len(run), "bound": int(bound),
                             "overage": max(0, len(run) - int(bound))})
                run = []
            if d is not None:
                run.append(d)
    return rows


def overage_total(rows: list[dict]) -> int:
    return sum(int(r.get("overage") or 0) for r in rows)

READING_REST_AFTER = ("смена — нарезка по порогам отдыха прогона (та же, что у решателя); "
                      "продолжительность смены — размах; отдых после — разрыв до первого "
                      "вылета следующей смены; штраф за минуту недобора; отдых после "
                      "последней смены горизонта не судится")


def rest_after_long_duty(payload: dict, solved: dict, long_min: int, rest_min: int,
                         rest_thresholds, base_scope: str | None = None) -> list[dict]:
    """Строки (сотрудник, смена): размах, отдых после, недобор минут.

    `rest_thresholds` — пороги отдыха ПРОГОНА ({"home": N, "outstation": M}):
    нарезка смен обязана совпадать с решателем, иначе оракул подтвердит
    чужую ошибку той же ошибкой.
    """
    from datetime import datetime

    from webapp import solution_export as se
    inp = se.certifier_input(payload, solved, rest_thresholds)
    scoped = None
    if base_scope:
        scoped = {str(e.get("id")) for e in (payload.get("employees") or [])
                  if _code(e.get("homeAirport")) == base_scope.upper()}
    by_emp: dict[str, list[dict]] = {}
    for d in inp.get("duties") or []:
        emp = str(d.get("employee"))
        if scoped is not None and emp not in scoped:
            continue
        by_emp.setdefault(emp, []).append(d)
    rows: list[dict] = []
    for emp in sorted(by_emp):
        ds = sorted(by_emp[emp], key=lambda d: d["start"])
        for d1, d2 in zip(ds, ds[1:]):
            start1 = datetime.fromisoformat(d1["start"])
            end1 = datetime.fromisoformat(d1["end"])
            span = int((end1 - start1).total_seconds() // 60)
            if span <= int(long_min):
                continue
            rest = int((datetime.fromisoformat(d2["start"]) - end1).total_seconds() // 60)
            rows.append({"employee": emp, "duty": d1.get("id"), "span_min": span,
                         "rest_min": rest, "bound_min": int(rest_min),
                         "shortfall_min": max(0, int(rest_min) - rest)})
    return rows


def rest_shortfall_total(rows: list[dict]) -> int:
    return sum(int(r.get("shortfall_min") or 0) for r in rows)

READING_ROLLING = ("отдых — непрерывный промежуток без назначений (края присутствия — отдых); "
                   "окно — N подряд идущих суток UTC; окно нарушено, когда ни один промежуток "
                   "не даёт внутри окна непрерывных W минут; сотрудник без назначений "
                   "выполняет все окна")


def rest_in_rolling_windows(payload: dict, solved: dict, window_days: int, rest_min: int,
                            base_scope: str | None = None) -> list[dict]:
    """Строки (сотрудник): окон рассмотрено, нарушено — прочтение решателя."""
    from datetime import datetime, timedelta

    times = {}
    for f in payload.get("flights") or []:
        key = str(f.get("flightLegId") or f.get("flightNumber"))
        pair = (datetime.fromisoformat(str(f["departureUTCDateTime"]).replace("Z", "")),
                datetime.fromisoformat(str(f["arrivalUTCDateTime"]).replace("Z", "")))
        times[key] = pair
        times.setdefault(str(f.get("flightNumber")), pair)
    busy: dict[str, list] = {}
    for a in solved.get("flightAssignments") or []:
        emp = a.get("employee")
        if not emp:
            continue
        emp_id = str(emp.get("id") if isinstance(emp, dict) else emp)
        flight = a.get("flight")
        leg = str(flight.get("flightLegId") or flight.get("flightNumber"))             if isinstance(flight, dict) else str(flight)
        busy.setdefault(emp_id, []).append(times[leg])
    rows: list[dict] = []
    for e in payload.get("employees") or []:
        if base_scope and _code(e.get("homeAirport")) != base_scope.upper():
            continue
        emp_id = str(e.get("id"))
        ivs = sorted(busy.get(emp_id, []))
        if not ivs:
            rows.append({"employee": emp_id, "windows": 0, "violated": 0})
            continue
        lo = ivs[0][0].date()
        hi = max(arr for _, arr in ivs).date()
        rests = []
        cursor = datetime.combine(lo, datetime.min.time())
        # Ревизия 1.17: конец последней смены истории — начало известного
        # отдыха перед горизонтом; старты окон — с первых полных суток
        # после него (то же прочтение, что у решателя).
        last_end = _history_last_duty_end(e)
        if last_end is not None and last_end < ivs[0][0]:
            cursor = last_end
            end_day = last_end.date()
            first_full = (end_day if last_end == datetime.combine(end_day, datetime.min.time())
                          else end_day + timedelta(days=1))
            if first_full < lo:
                lo = first_full
        for dep, arr in ivs:
            if dep > cursor:
                rests.append((cursor, dep))
            if arr > cursor:
                cursor = arr
        rests.append((cursor, datetime.combine(hi + timedelta(days=1), datetime.min.time())))
        windows = violated = 0
        d = lo
        while d + timedelta(days=window_days - 1) <= hi:
            w_from = datetime.combine(d, datetime.min.time())
            w_to = w_from + timedelta(days=window_days)
            windows += 1
            ok = any((min(r_to, w_to) - max(r_from, w_from)).total_seconds() // 60 >= rest_min
                     for r_from, r_to in rests)
            if not ok:
                violated += 1
            d += timedelta(days=1)
        rows.append({"employee": emp_id, "windows": windows, "violated": violated})
    return rows


def violated_total(rows: list[dict]) -> int:
    return sum(int(r.get("violated") or 0) for r in rows)

READING_NIGHT = ("смена — нарезка по порогам отдыха прогона; ночная — первый вылет смены в окне "
                 "[от, до) времени суток UTC (через полночь законно); подряд — соседние смены "
                 "последовательности сотрудника; штраф за ночную сверх границы в каждой серии")


def night_runs_overage(payload: dict, solved: dict, night_from: int, night_to: int,
                       bound: int, rest_thresholds, base_scope: str | None = None) -> list[dict]:
    """Строки (сотрудник, серия ночных): длина, граница, превышение."""
    from datetime import datetime

    from webapp import solution_export as se
    inp = se.certifier_input(payload, solved, rest_thresholds)
    scoped = None
    if base_scope:
        scoped = {str(e.get("id")) for e in (payload.get("employees") or [])
                  if _code(e.get("homeAirport")) == base_scope.upper()}

    def is_night(d):
        start = datetime.fromisoformat(d["start"])
        m = start.hour * 60 + start.minute
        return (night_from <= m < night_to) if night_from <= night_to             else (m >= night_from or m < night_to)

    by_emp: dict[str, list[dict]] = {}
    for d in inp.get("duties") or []:
        emp = str(d.get("employee"))
        if scoped is not None and emp not in scoped:
            continue
        by_emp.setdefault(emp, []).append(d)
    emp_rec = {str(e.get("id")): e for e in (payload.get("employees") or [])}
    rows: list[dict] = []
    for emp in sorted(by_emp):
        ds = sorted(by_emp[emp], key=lambda d: d["start"])
        run = 0
        start_id = None
        # Ревизия 1.17: серия ночных из истории (historyConsecutiveNightDuties)
        # продолжается первой сменой горизонта, если та ночная (то же
        # прочтение, что у решателя); поле carry строки — перенос.
        carry = 0
        if ds and is_night(ds[0]):
            carry = int(_history_of(emp_rec.get(emp)).get("historyConsecutiveNightDuties") or 0)
        for d in ds + [None]:
            night = d is not None and is_night(d)
            if night:
                if run == 0:
                    start_id = d.get("id")
                    if carry:
                        run = carry
                run += 1
            else:
                if run > 0:
                    row = {"employee": emp, "first_duty": start_id, "nights": run,
                           "bound": int(bound), "overage": max(0, run - int(bound))}
                    if carry:
                        row["carry"] = carry
                        carry = 0
                    rows.append(row)
                run = 0
    return rows


def night_overage_total(rows: list[dict]) -> int:
    return sum(int(r.get("overage") or 0) for r in rows)


# ---- счётчики скользящих окон (решение владельца 05.09.2026) ----

READING_DAYS_OFF_ROLLING = ("выходной — сутки горизонта без назначений сотрудника; окно — N подряд "
                            "идущих суток, старты — сутки горизонта, где окно помещается; нехватка "
                            "— K минус выходных в окне; календарь истории (1.17.2) открывает окна "
                            "через границу горизонта, сутки без записи делают окно несудимым")
READING_NIGHT_ROLLING = ("ночная смена — первый вылет смены в окне [от, до) минут суток; смена "
                         "относится к суткам начала; окно — N подряд идущих суток; превышение — "
                         "ночных в окне минус K; календарь истории (1.17.2) открывает окна через "
                         "границу горизонта, сутки без записи или рабочие без признака ночи делают "
                         "окно несудимым")


def _calendar_of(e: dict | None) -> dict:
    """Посуточный календарь до горизонта сотрудника набора (1.17.2):
    {дата: {kind, night}}; пусто — календаря нет."""
    from datetime import date
    out: dict = {}
    for d in _history_of(e).get("historyCalendar") or []:
        if not isinstance(d, dict):
            continue
        try:
            dt = date.fromisoformat(str(d.get("date") or "")[:10])
        except ValueError:
            continue
        out[dt] = d
    return out


def _calendar_day_off(cal: dict, d) -> int | None:
    h = cal.get(d)
    if h is None:
        return None
    kind = str(h.get("kind") or "unknown")
    if kind in ("day_off", "leave", "sick"):
        return 1
    if kind in ("work", "reserve"):
        return 0
    return None


def _calendar_nights(cal: dict, d) -> int | None:
    h = cal.get(d)
    if h is None:
        return None
    kind = str(h.get("kind") or "unknown")
    if kind == "unknown" or (kind == "work" and h.get("night") is None):
        return None
    return 1 if h.get("night") is True else 0


def _horizon_bounds(payload: dict):
    days = horizon(payload)
    return (min(days), max(days)) if days else (None, None)


def days_off_in_rolling_windows(payload: dict, solved: dict, window_days: int, min_off: int,
                                base_scope: str | None = None) -> list[dict]:
    """Строки (сотрудник): окон, судимых окон, нарушенных, нехватка —
    то же прочтение, что у решателя (LogicForms.daysOffWindowShortfall)."""
    from datetime import timedelta
    lo, hi = _horizon_bounds(payload)
    busy = busy_days(payload, solved)
    rows: list[dict] = []
    for e in payload.get("employees") or []:
        if base_scope and _code(e.get("homeAirport")) != base_scope.upper():
            continue
        emp_id = str(e.get("id"))
        row = {"employee": emp_id, "windows": 0, "judged": 0, "violated": 0, "shortfall": 0}
        mine = busy.get(emp_id, set())
        if lo is None or not mine:
            rows.append(row)
            continue
        cal = _calendar_of(e)
        d = lo - timedelta(days=window_days - 1)
        while d + timedelta(days=window_days - 1) <= hi:
            row["windows"] += 1
            off, known = 0, True
            x = d
            while x < d + timedelta(days=window_days):
                if x < lo:
                    v = _calendar_day_off(cal, x)
                    if v is None:
                        known = False
                        break
                    off += v
                elif x not in mine:
                    off += 1
                x += timedelta(days=1)
            if known:
                row["judged"] += 1
                if off < min_off:
                    row["violated"] += 1
                    row["shortfall"] += min_off - off
            d += timedelta(days=1)
        rows.append(row)
    return rows


def window_shortfall_total(rows: list[dict]) -> int:
    return sum(int(r.get("shortfall") or 0) for r in rows)


def night_duties_in_rolling_windows(payload: dict, solved: dict, window_days: int, max_nights: int,
                                    night_from: int, night_to: int, rest_thresholds,
                                    base_scope: str | None = None) -> list[dict]:
    """Строки (сотрудник): окон, судимых, нарушенных, превышение — то же
    прочтение, что у решателя (LogicForms.nightWindowOverage)."""
    from datetime import datetime, timedelta

    from webapp import solution_export as se
    inp = se.certifier_input(payload, solved, rest_thresholds)

    def is_night(d):
        start = datetime.fromisoformat(d["start"])
        m = start.hour * 60 + start.minute
        return (night_from <= m < night_to) if night_from <= night_to \
            else (m >= night_from or m < night_to)
    nights: dict[str, dict] = {}
    for d in inp.get("duties") or []:
        if is_night(d):
            day = datetime.fromisoformat(d["start"]).date()
            per = nights.setdefault(str(d.get("employee")), {})
            per[day] = per.get(day, 0) + 1
    lo, hi = _horizon_bounds(payload)
    rows: list[dict] = []
    for e in payload.get("employees") or []:
        if base_scope and _code(e.get("homeAirport")) != base_scope.upper():
            continue
        emp_id = str(e.get("id"))
        row = {"employee": emp_id, "windows": 0, "judged": 0, "violated": 0, "overage": 0}
        mine = nights.get(emp_id, {})
        cal = _calendar_of(e)
        if lo is None or (not mine and not cal):
            rows.append(row)
            continue
        d = lo - timedelta(days=window_days - 1)
        while d + timedelta(days=window_days - 1) <= hi:
            row["windows"] += 1
            n, known = 0, True
            x = d
            while x < d + timedelta(days=window_days):
                if x < lo:
                    v = _calendar_nights(cal, x)
                    if v is None:
                        known = False
                        break
                    n += v
                else:
                    n += mine.get(x, 0)
                x += timedelta(days=1)
            if known:
                row["judged"] += 1
                if n > max_nights:
                    row["violated"] += 1
                    row["overage"] += n - max_nights
            d += timedelta(days=1)
        rows.append(row)
    return rows


def window_overage_total(rows: list[dict]) -> int:
    return sum(int(r.get("overage") or 0) for r in rows)
