# -*- coding: utf-8 -*-
"""Движок ростеринга CP-SAT, версия 1 (пакет «выбор движка»,
поручение владельца 2026-08-28, ростеринговая часть).

Прямая модель назначения людей на места цепочек каталога пейринга.
Вход — существующие артефакты (новых форматов нет): каталог этапа 1
(места, коды мест, окна занятости в минутах, требуемый отдых после
цепочки, месячные минуты) и набор данных (сотрудники: роли, коды,
недоступные дни, антипары).

ОБЪЁМ v1 — ЧЕСТНО ОГРАНИЧЕН И ДЕКЛАРИРУЕТСЯ В ОТЧЁТЕ ПОИМЁННО
(engine_scope): судятся — заполнение каждого места подходящим по
ролям/кодам сотрудником; непересечение цепочек одного человека во
времени С НОРМАТИВНЫМ ОТДЫХОМ между ними; недоступные дни; антипары
(два несовместимых человека на одной цепочке); месячный накопитель
налёта. НЕ судятся (остаются движку Timefold, перечень уезжает в
отчёт): визы/гражданство/паспорта, персональные допуски с датами,
серии ночей и недельный отдых, резерв, инструкторские составы,
предпочтения. Незаполненное место — не отказ, а поимённая строка
отчёта (лексикографика «заполнение первое» прослаблениями).
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _cli_console import force_utf8_console

# Инструмент печатает русский отчёт: консоль настраивается САМИМ
# инструментом, а не внешней переменной окружения (страж
# tests/test_cli_console.py, урок аудита P1-15).
force_utf8_console()

SCOPE_JUDGED = (
    "заполнение мест по ролям и кодам мест",
    "непересечение цепочек одного сотрудника во времени",
    "нормативный отдых между цепочками (minRestAfterMin)",
    "недоступные дни (unavailableDays)",
    "антипары (doNotFlyWith)",
    "месячный накопитель налёта (blockMinutesByMonth, предел "
    "аргументом --month-block-cap-min)",
)
SCOPE_NOT_JUDGED = (
    "визы, гражданство, паспорта по направлениям",
    "персональные допуски с датами (qualificationWindows)",
    "серии ночей и недельный отдых",
    "резерв и инструкторские составы",
    "предпочтения сотрудников (мягкие)",
)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _win_days(win) -> set:
    """Календарные дни (UTC) окна занятости [начало, конец] в минутах."""
    out = set()
    d0 = _EPOCH + timedelta(minutes=int(win[0]))
    d1 = _EPOCH + timedelta(minutes=int(win[1]))
    d = d0.date()
    while d <= d1.date():
        out.add(d.isoformat())
        d += timedelta(days=1)
    return out


def build_and_solve(payload: dict, catalog: dict, *,
                    budget_s: int = 300, seed: int = 7,
                    month_block_cap_min: int = 5400) -> dict:
    from ortools.sat.python import cp_model

    emps = payload.get("employees") or []
    recs = catalog.get("selected") or []
    m = cp_model.CpModel()

    codes_of = {}
    for e in emps:
        codes_of[e["id"]] = set(e.get("skills") or ()) \
            | set(e.get("typeRatings") or ())
    unavail = {e["id"]: set(str(d) for d in
                            (e.get("unavailableDays") or ()))
               for e in emps}
    ban_pairs = set()
    for e in emps:
        for other in (e.get("doNotFlyWith") or ()):
            ban_pairs.add(frozenset((e["id"], str(other))))

    # --- допустимость сотрудника на место -----------------------------
    y = {}                      # (ci, si, eid) -> BoolVar
    slot_slack = {}             # (ci, si) -> BoolVar (место пусто)
    ineligible: dict = {}
    for ci, rec in enumerate(recs):
        days = set()
        for w in rec.get("busyWindows") or ():
            days |= _win_days(w)
        need_all = set(rec.get("requiredCodesAll") or ())
        seats = rec.get("seats") or []
        seat_codes = rec.get("seatCodes") or [[]] * len(seats)
        for si, role in enumerate(seats):
            need = set(seat_codes[si] if si < len(seat_codes)
                       else ()) | need_all
            cands = []
            for e in emps:
                eid = e["id"]
                have = codes_of[eid]
                if role not in (e.get("skills") or ()) \
                        and not (need and need <= have):
                    continue
                if need and not need <= have:
                    continue
                if days & unavail[eid]:
                    continue
                v = m.new_bool_var("y_%d_%d_%s" % (ci, si, eid))
                y[(ci, si, eid)] = v
                cands.append(v)
            s = m.new_bool_var("empty_%d_%d" % (ci, si))
            slot_slack[(ci, si)] = s
            m.add(sum(cands) + s == 1)
            if not cands:
                ineligible[(ci, si)] = role

    # --- один человек: без пересечений и с отдыхом --------------------
    def _conflict(a: dict, b: dict) -> bool:
        wa = a.get("busyWindows") or ()
        wb = b.get("busyWindows") or ()
        ra = float(a.get("minRestAfterMin") or 0)
        rb = float(b.get("minRestAfterMin") or 0)
        for w1 in wa:
            for w2 in wb:
                lo1, hi1, lo2, hi2 = (int(w1[0]), int(w1[1]),
                                      int(w2[0]), int(w2[1]))
                if hi1 <= lo2:          # a раньше b: отдых после a
                    if lo2 - hi1 < ra:
                        return True
                elif hi2 <= lo1:        # b раньше a
                    if lo1 - hi2 < rb:
                        return True
                else:
                    return True         # пересечение окон
        return False

    conflicts = []
    for ci in range(len(recs)):
        for cj in range(ci + 1, len(recs)):
            if _conflict(recs[ci], recs[cj]):
                conflicts.append((ci, cj))
    by_emp_chain: dict = {}
    for (ci, si, eid), v in y.items():
        by_emp_chain.setdefault((eid, ci), []).append(v)
    for eid in codes_of:
        for ci, cj in conflicts:
            a = by_emp_chain.get((eid, ci))
            b = by_emp_chain.get((eid, cj))
            if a and b:
                m.add(sum(a) + sum(b) <= 1)
        # человек занимает не больше одного места цепочки
        for ci in range(len(recs)):
            vs = by_emp_chain.get((eid, ci))
            if vs and len(vs) > 1:
                m.add(sum(vs) <= 1)

    # --- антипары на одной цепочке ------------------------------------
    for pair in ban_pairs:
        e1, e2 = sorted(pair)
        for ci in range(len(recs)):
            a = by_emp_chain.get((e1, ci))
            b = by_emp_chain.get((e2, ci))
            if a and b:
                m.add(sum(a) + sum(b) <= 1)

    # --- месячный накопитель налёта -----------------------------------
    months = sorted({mo for rec in recs
                     for mo in (rec.get("blockMinutesByMonth")
                                or {})})
    for eid in codes_of:
        for mo in months:
            terms = []
            for ci, rec in enumerate(recs):
                add = int((rec.get("blockMinutesByMonth")
                           or {}).get(mo) or 0)
                vs = by_emp_chain.get((eid, ci))
                if add and vs:
                    terms.append(add * sum(vs))
            if terms:
                m.add(sum(terms) <= month_block_cap_min)

    # --- цель: заполнение лексикографически первое --------------------
    m.minimize(sum(slot_slack.values()))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(max(1, budget_s))
    solver.parameters.random_seed = seed
    solver.parameters.num_search_workers = 8
    status = solver.solve(m)
    st_name = solver.status_name(status)

    assignments = []
    empty = []
    for (ci, si, eid), v in y.items():
        if solver.value(v):
            assignments.append({
                "chain": recs[ci].get("sourceUnit") or ci,
                "chain_index": ci,
                "seat": (recs[ci].get("seats") or [])[si],
                "employee": eid})
    for (ci, si), s in slot_slack.items():
        if solver.value(s):
            empty.append({
                "chain": recs[ci].get("sourceUnit") or ci,
                "chain_index": ci,
                "seat": (recs[ci].get("seats") or [])[si],
                "reason": ("нет подходящего сотрудника (роль/коды/"
                           "дни)" if (ci, si) in ineligible
                           else "конфликт занятости/накопителей")})
    return {
        "engine": "cpsat-roster-v1",
        "status": st_name,
        "slots_total": len(slot_slack),
        "slots_filled": len(assignments),
        "slots_empty": empty,
        "assignments": assignments,
        "engine_scope": {"judged": list(SCOPE_JUDGED),
                         "not_judged": list(SCOPE_NOT_JUDGED)},
        "month_block_cap_min": month_block_cap_min,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--payload", type=Path, required=True)
    ap.add_argument("--catalog", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--budget-s", type=int, default=300)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--month-block-cap-min", type=int, default=5400,
                    help="предел месячного налёта, мин (носитель — "
                         "правила источника; умолчание 90 ч)")
    a = ap.parse_args(argv)
    payload = json.loads(a.payload.read_text(encoding="utf-8"))
    catalog = json.loads(a.catalog.read_text(encoding="utf-8"))
    res = build_and_solve(payload, catalog, budget_s=a.budget_s,
                          seed=a.seed,
                          month_block_cap_min=a.month_block_cap_min)
    a.out.write_text(json.dumps(res, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    print("движок ростеринга cpsat-v1: %s — мест %d, заполнено %d, "
          "пусто %d (поимённо в отчёте); объём v1 судит %d семей "
          "правил, не судит %d (перечни в engine_scope)"
          % (res["status"], res["slots_total"], res["slots_filled"],
             len(res["slots_empty"]), len(SCOPE_JUDGED),
             len(SCOPE_NOT_JUDGED)), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
