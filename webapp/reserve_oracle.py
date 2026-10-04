# -*- coding: utf-8 -*-
"""Оракул суточного резерва по базам — независимое прочтение нормы
суточного резерва свода.

Решатель исполняет норму резерва ограничением (Reserve crews) и отдаёт
число нарушений суммой в счёте. Здесь то же прочтение реализовано
отдельно, на Python, по набору и решению, — чтобы две реализации
держали друг друга: на каждом эталоне hard решателя обязан равняться
−(сумма нехваток оракула) при нуле прочих жёстких, и это проверяется
живьём (tools/most_v_mutation.py), а таблица (база, сутки) уходит
сертификатору листом «Резерв».

Прочтение (то же, что заявлено решателем в отчёте применённого):
- экипаж в резерве — по одному СВОБОДНОМУ сотруднику базы на каждую
  роль состава мест набора; экипажей = наименьшее по ролям;
- свободен в сутки — ни одно назначение не касается суток (дата вылета
  ≤ сутки ≤ дата прилёта, UTC) и сутки не в unavailableDays;
- сутки горизонта — календарные даты UTC от первого вылета до
  последнего прилёта набора;
- сотрудник без роли состава не считается; база без людей даёт
  нехватку, равную норме, на каждые сутки.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta


class ReserveOracleError(RuntimeError):
    """Счёт невозможен — с названной причиной, а не молча."""


def _dt(value) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except (TypeError, ValueError) as e:
        raise ReserveOracleError("нечитаемая метка времени %r: %s" % (value, e)) from e


def _code(ap) -> str:
    code = ap.get("code") if isinstance(ap, dict) else ap
    return str(code or "").strip().upper()


def horizon(payload: dict) -> list[date]:
    """Календарные даты UTC от первого вылета до последнего прилёта."""
    flights = payload.get("flights") or []
    if not flights:
        raise ReserveOracleError("в наборе нет рейсов: горизонт суток не определён")
    lo = min(_dt(f["departureUTCDateTime"]).date() for f in flights)
    # Рейс относится к горизонту по вылету: прилёт после конца горизонта не
    # создаёт лишних суток резерва (то же чтение, что у решателя; единый
    # план 05.09, день 1).
    hi = max(_dt(f["departureUTCDateTime"]).date() for f in flights)
    out, d = [], lo
    while d <= hi:
        out.append(d)
        d += timedelta(days=1)
    return out


def composition_roles(payload: dict) -> list[str]:
    """Роли состава — различные requiredSkill мест набора (и незанятых тоже)."""
    return sorted({str(a.get("requiredSkill") or "") for a in (payload.get("flightAssignments") or [])} - {""})


def busy_days(payload: dict, solved: dict) -> dict[str, set[date]]:
    """Сотрудник -> сутки, которых касается хотя бы одно его назначение."""
    by_leg = {}
    for f in payload.get("flights") or []:
        key = str(f.get("flightLegId") or f.get("flightNumber"))
        by_leg[key] = (_dt(f["departureUTCDateTime"]).date(), _dt(f["arrivalUTCDateTime"]).date())
        by_leg.setdefault(str(f.get("flightNumber")), by_leg[key])
    out: dict[str, set[date]] = {}
    for a in solved.get("flightAssignments") or []:
        emp = a.get("employee")
        if not emp:
            continue
        emp_id = str(emp.get("id") if isinstance(emp, dict) else emp)
        flight = a.get("flight")
        leg = str(flight.get("flightLegId") or flight.get("flightNumber")) if isinstance(flight, dict) else str(flight)
        if leg not in by_leg:
            raise ReserveOracleError("назначение ссылается на рейс %r, которого нет в наборе" % (leg,))
        lo, hi = by_leg[leg]
        d = lo
        while d <= hi:
            out.setdefault(emp_id, set()).add(d)
            d += timedelta(days=1)
    return out


def reserve_table(payload: dict, solved: dict, norms: dict[str, int]) -> list[dict]:
    """Таблица (база, сутки): свободно по ролям, экипажей, норма, нехватка."""
    if not norms:
        return []
    roles = composition_roles(payload)
    if not roles:
        raise ReserveOracleError("в наборе нет мест: роли состава не определены")
    days = horizon(payload)
    busy = busy_days(payload, solved)
    rows: list[dict] = []
    for base in sorted(norms):
        norm = int(norms[base])
        people = [e for e in (payload.get("employees") or []) if _code(e.get("homeAirport")) == base]
        for d in days:
            free = {}
            for role in roles:
                n = 0
                for e in people:
                    if role not in (e.get("skills") or []):
                        continue
                    if d.isoformat() in {str(x)[:10] for x in (e.get("unavailableDays") or [])}:
                        continue
                    if d in busy.get(str(e.get("id")), set()):
                        continue
                    n += 1
                free[role] = n
            crews = min(free.values()) if free else 0
            rows.append({"base": base, "day": d.isoformat(), "free": free,
                         "crews": crews, "norm": norm, "shortfall": max(0, norm - crews),
                         "employees_of_base": len(people)})
    return rows


def total_shortfall(rows: list[dict]) -> int:
    return sum(int(r["shortfall"]) for r in rows)


def norms_from_applied(report: dict) -> dict[str, int]:
    """Нормы резерва из отчёта применённого: thresholds.applied «reserve_crews_min[КОД]»."""
    out: dict[str, int] = {}
    thr = ((report or {}).get("lastRun") or {}).get("thresholds") or {}
    for a in thr.get("applied") or []:
        t = str(a.get("threshold") or "")
        if t.startswith("reserve_crews_min[") and t.endswith("]"):
            out[t[len("reserve_crews_min["):-1]] = int(a.get("value"))
    return out


def unassigned_seats(solved: dict) -> int:
    return sum(1 for a in (solved.get("flightAssignments") or []) if not a.get("employee"))


def overlapping_pairs(payload: dict, solved: dict) -> int:
    """Пары назначений одного человека с пересекающимися рейсами.

    Гейт приёмки: при весе нехватки 1 решатель мог бы «купить» резерв
    двойной посадкой на одновременные рейсы (Flight conflict 1 = 1);
    ноль таких пар доказывает, что покупки не было.
    """
    times = {}
    for f in payload.get("flights") or []:
        key = str(f.get("flightLegId") or f.get("flightNumber"))
        times[key] = (_dt(f["departureUTCDateTime"]), _dt(f["arrivalUTCDateTime"]))
        times.setdefault(str(f.get("flightNumber")), times[key])
    by_emp: dict[str, list] = {}
    for a in solved.get("flightAssignments") or []:
        emp = a.get("employee")
        if not emp:
            continue
        emp_id = str(emp.get("id") if isinstance(emp, dict) else emp)
        flight = a.get("flight")
        leg = str(flight.get("flightLegId") or flight.get("flightNumber")) if isinstance(flight, dict) else str(flight)
        by_emp.setdefault(emp_id, []).append(times[leg])
    n = 0
    for legs in by_emp.values():
        for i in range(len(legs)):
            for j in range(i + 1, len(legs)):
                a, b = legs[i], legs[j]
                if a[0] < b[1] and b[0] < a[1]:
                    n += 1
    return n
