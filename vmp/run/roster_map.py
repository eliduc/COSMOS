# -*- coding: utf-8 -*-
"""Карта ростера (поручение владельца 2026-09-04): кто из сотрудников на
каком рейсе, по дням — в формате общего движка карты сети NetMap
(webapp/static/net-map.js), тем же движком, что карта постановки.

Источник — решение кнопки ростеринга (button/solution.json →
assignments: сотрудник, рейс, аэропорты и время вылета/прилёта, роль,
база, дата). Никакой модели: детерминированная проекция.

Формат:
  {
    "nodes": [{code, base?}],                — все станции решения; базы
    "geo": {coords, missing, complete},       — координаты (webapp.geo)
    "employees": [{id, base, days: {date: n}}],
    "days": [{
        "date": "2026-09-04",
        "arcs": [{a, b, n}],                  — рёбра дня, n — назначений
        "chains": [{cid, legs: [{dep, arr, flight, dep_time, arr_time,
                                 role}]}],    — маршрут сотрудника за день
                                               (cid = табельный номер)
        "employees": [{id, base, legs: n, first, last}]
    }]
  }
"""
from __future__ import annotations


class RosterMapError(ValueError):
    """Карта ростера не построилась; недостающее названо словами."""


def build_roster_map(solution: dict, *, known: dict | None = None,
                     data_class: str | None = None) -> dict:
    assignments = solution.get("assignments") if isinstance(solution, dict) else None
    if not isinstance(assignments, list) or not assignments:
        raise RosterMapError("в решении ростеринга нет назначений (assignments) — "
                             "карте ростера не из чего строиться")
    codes: list[str] = []
    bases: set = set()
    emp_base: dict = {}
    by_day: dict = {}
    for a in assignments:
        if not isinstance(a, dict):
            continue
        emp = str(a.get("employee") or "").strip()
        dep = str(a.get("dep_airport") or "").strip()
        arr = str(a.get("arr_airport") or "").strip()
        if not emp or not dep or not arr:
            continue
        date = str(a.get("date") or str(a.get("dep_time") or "")[:10])
        for c in (dep, arr):
            if c not in codes:
                codes.append(c)
        base = str(a.get("base") or "").strip()
        if base:
            bases.add(base)
            emp_base.setdefault(emp, base)
            if base not in codes:
                codes.append(base)
        day = by_day.setdefault(date, {"arcs": {}, "legs": {}})
        key = (dep, arr) if dep <= arr else (arr, dep)
        day["arcs"][key] = day["arcs"].get(key, 0) + 1
        day["legs"].setdefault(emp, []).append({
            "dep": dep, "arr": arr, "flight": str(a.get("flight") or ""),
            "dep_time": str(a.get("dep_time") or ""),
            "arr_time": str(a.get("arr_time") or ""),
            "role": str(a.get("role") or "")})
    # Сотрудники без назначений — из справочника решения (штат): в карте
    # они видны как «без рейсов», это часть картины ростера.
    ref = (solution.get("reference_tables") or {}) if isinstance(solution, dict) else {}
    for _sid, book in ref.items():
        crew = (book or {}).get("Экипажи") if isinstance(book, dict) else None
        for row in (crew or {}).get("rows") or []:
            emp = str(row.get("Табельный номер") or "").strip()
            if emp and emp not in emp_base:
                b = str(row.get("База приписки") or row.get("База") or "").strip()
                emp_base[emp] = b
                if b:
                    bases.add(b)
                    if b not in codes:
                        codes.append(b)
    # Координаты — по принципу класса данных, как у карты постановки
    # (ночь 05→06.09.2026): книга авиакомпании (лист «Аэропорты» →
    # airports набора) первична; справочник по коду IATA — только при
    # явном классе open/proprietary. Прежний geo.resolve(codes) брал
    # справочник всегда, и выдуманные коды синтетического набора вставали
    # на настоящие города (PLY в США, SVG в Норвегии, GRT в Пакистане) —
    # замечание владельца на демо 06.09.2026.
    from webapp import geo
    resolved = geo.resolve_for(codes, data_class=data_class, known=known)
    days = []
    for date in sorted(by_day):
        d = by_day[date]
        chains = []
        emps = []
        for emp in sorted(d["legs"]):
            legs = sorted(d["legs"][emp], key=lambda x: x["dep_time"])
            chains.append({"cid": emp, "legs": legs})
            emps.append({"id": emp, "base": emp_base.get(emp, ""), "legs": len(legs),
                         "first": legs[0]["dep_time"], "last": legs[-1]["arr_time"]})
        days.append({
            "date": date,
            "arcs": [{"a": a, "b": b, "n": n} for (a, b), n in sorted(d["arcs"].items())],
            "chains": chains,
            "employees": emps,
        })
    employees = [{"id": emp, "base": emp_base.get(emp, ""),
                  "days": {dt: len(by_day[dt]["legs"].get(emp) or []) for dt in sorted(by_day)}}
                 for emp in sorted(emp_base)]
    # Сводные дуги за весь горизонт — верхний уровень карты (общий движок
    # NetMap рисует net.arcs; дуги по дням остаются в days): без них карта
    # ростера показывала станции без маршрутов (замечание владельца на
    # демо 06.09.2026).
    total: dict = {}
    for d in days:
        for arc in d["arcs"]:
            rec = total.setdefault((arc["a"], arc["b"]),
                                   {"a": arc["a"], "b": arc["b"], "n": 0})
            rec["n"] += int(arc["n"] or 0)
    return {
        "nodes": [{"code": c, **({"base": True} if c in bases else {})} for c in codes],
        "arcs": [total[k] for k in sorted(total)],
        "geo": {"coords": resolved.get("coords") or {},
                "missing": resolved.get("missing") or [],
                "complete": bool(resolved.get("complete")),
                "basis": resolved.get("basis"),
                "data_class": resolved.get("data_class")},
        "employees": employees,
        "days": days,
    }
