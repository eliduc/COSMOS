"""Отчёт об аномалиях истории — этап Ф6 конвейера формализации
(2026-07-17, дорожная карта №7 документа COSMOS-DATA-FORMALIZATION-RU).

Статистические странности ПОВЕРХ трёх валидаций (схема, ссылки,
семантика): выбросы налёта, дубликаты сотрудников, «мёртвые души»,
смешанные месяцы. Всё детерминированно, без ИИ. Отчёт НИЧЕГО не чинит —
судьбу каждой аномалии решает владелец данных (принцип «громких отказов»:
молчаливые исправления запрещены).
"""
from __future__ import annotations

from datetime import datetime

MAX_ITEMS = 30
# an employee unavailable on >= this share of the horizon days is suspect
UNAVAILABLE_SHARE = 0.8
# IQR-based outlier detection needs a minimally meaningful sample
MIN_SAMPLE_FOR_OUTLIERS = 8


def _dur_hours(f: dict) -> float | None:
    try:
        dep = datetime.fromisoformat(f["departureUTCDateTime"])
        arr = datetime.fromisoformat(f["arrivalUTCDateTime"])
        # subtraction is INSIDE the try: mixing aware+naive raises TypeError
        # (M04) and must degrade to "unknown", not 500 the analyze request.
        h = (arr - dep).total_seconds() / 3600.0
    except (KeyError, TypeError, ValueError):
        return None
    return h if h > 0 else None


def _cap(warnings: list[str]) -> list[str]:
    if len(warnings) > MAX_ITEMS:
        return warnings[:MAX_ITEMS] + [f"… и ещё {len(warnings) - MAX_ITEMS}"]
    return warnings


def report(payload: dict) -> dict:
    """{warnings: [str], stats: {...}} over one canonical payload."""
    warnings: list[str] = []
    employees = payload.get("employees") or []
    flights = payload.get("flights") or []
    assigns = payload.get("flightAssignments") or []

    # -- flights spread over months: mixed exports are a classic intake bug
    months: dict[tuple[int, int], int] = {}
    horizon_days: set[str] = set()
    for f in flights:
        try:
            d = datetime.fromisoformat(f["departureUTCDateTime"])
        except (KeyError, TypeError, ValueError):
            continue
        months[(d.year, d.month)] = months.get((d.year, d.month), 0) + 1
        horizon_days.add(d.date().isoformat())
    if months:
        dom = max(months, key=lambda k: months[k])
        stray = sum(n for m, n in months.items() if m != dom)
        if stray:
            warnings.append(
                f"{stray} рейс(ов) вне основного месяца "
                f"{dom[0]}-{dom[1]:02d} — возможно, смешаны разные выгрузки")

    # -- duplicate employee ids (schema does not enforce uniqueness)
    seen: dict[str, int] = {}
    for e in employees:
        i = str(e.get("id") or "")
        if i:
            seen[i] = seen.get(i, 0) + 1
    dups = sorted(i for i, n in seen.items() if n > 1)
    if dups:
        warnings.append("дубликаты табельных номеров: "
                        + ", ".join(dups[:10]))

    # -- full namesakes on different ids: possible duplicated person
    by_name: dict[str, list[str]] = {}
    for e in employees:
        name = str(e.get("name") or "").strip().lower()
        if name:
            by_name.setdefault(name, []).append(str(e.get("id")))
    for name, ids in sorted(by_name.items()):
        if len(set(ids)) > 1:
            warnings.append(
                f"полные тёзки (возможный дубль сотрудника): «{name}» — "
                + ", ".join(sorted(set(ids))[:5]))

    # -- block hours per assigned employee: outliers + dead souls
    # A flight NUMBER may recur across dates (daily flights); assignments
    # reference the number, so use the MEAN duration of its occurrences
    # rather than an arbitrary last one.
    fh_sum: dict[str, float] = {}
    fh_cnt: dict[str, int] = {}
    for f in flights:
        fn = f.get("flightNumber")
        d = _dur_hours(f)
        if d is not None:
            fh_sum[fn] = fh_sum.get(fn, 0.0) + d
            fh_cnt[fn] = fh_cnt.get(fn, 0) + 1
    fh = {fn: fh_sum[fn] / fh_cnt[fn] for fn in fh_sum}
    hours: dict[str, float] = {}
    for a in assigns:
        emp = a.get("employee")
        h = fh.get(a.get("flight"))
        if emp and h:
            hours[str(emp)] = hours.get(str(emp), 0.0) + h
    ids_all = [str(e.get("id")) for e in employees if e.get("id")]
    idle = sorted(i for i in ids_all if i not in hours)
    if hours and idle:
        warnings.append(
            f"{len(idle)} сотрудник(ов) без единого назначения: "
            + ", ".join(idle[:10])
            + (" …" if len(idle) > 10 else ""))
    vals = sorted(hours.values())
    med = vals[len(vals) // 2] if vals else None
    q1 = vals[len(vals) // 4] if vals else 0.0
    q3 = vals[(3 * len(vals)) // 4] if vals else 0.0
    iqr = q3 - q1
    if len(vals) >= MIN_SAMPLE_FOR_OUTLIERS and med:
        # Normal case: Tukey 1.5·IQR fence. When the middle is FLAT (iqr==0)
        # that fence collapses onto q3 and would flag anything a hair above
        # the median (audit finding), yet a genuine spike must still show —
        # so fall back to a median-relative fence that only clearly-large /
        # clearly-small values cross (a hair above the flat median doesn't).
        if iqr > 0:
            hi = q3 + 1.5 * iqr
            lo = max(0.0, q1 - 1.5 * iqr)
        else:
            hi = med + max(1.0, med)          # > ~2× median
            lo = max(0.0, med - max(1.0, med))
        for emp, h in sorted(hours.items(), key=lambda kv: -kv[1]):
            if h > hi:
                warnings.append(f"выброс налёта: {emp} — {h:.1f} ч "
                                f"при медиане {med:.1f} ч")
        if lo > 0:
            for emp, h in sorted(hours.items(), key=lambda kv: kv[1]):
                if h < lo:
                    warnings.append(f"аномально низкий налёт: {emp} — "
                                    f"{h:.1f} ч при медиане {med:.1f} ч")

    # -- unavailable for most of the horizon: inactive person in the data?
    if len(horizon_days) >= 10:
        for e in employees:
            ud = set(str(d) for d in (e.get("unavailableDays") or []))
            covered = len(ud & horizon_days)
            if covered / len(horizon_days) >= UNAVAILABLE_SHARE:
                warnings.append(
                    f"{e.get('id')}: недоступен {covered} из "
                    f"{len(horizon_days)} дней горизонта — активен ли "
                    "сотрудник в этом месяце?")

    stats = {
        "employees": len(employees),
        "with_assignments": len(hours),
        "idle": len(idle),
        "median_block_h": round(med, 1) if med is not None else None,
        "months": len(months),
        "horizon_days": len(horizon_days),
    }
    return {"warnings": _cap(warnings), "stats": stats}
