# -*- coding: utf-8 -*-
"""Движок ростеринга CP-SAT v1: ядро жёстких правил на мини-мирах.

Каждый тест закрывает СВОЁ правило объёма v1; честность объёма —
отдельным тестом (перечни judged/not_judged в отчёте, пустые места
поимённо с причиной, а не отказ).
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import _roster_cpsat as rc  # noqa: E402

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _mins(iso: str) -> int:
    dt = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
    return int((dt - _EPOCH).total_seconds() // 60)


def _emp(eid, skills=("КВС", "ВП"), **over):
    e = {"id": eid, "homeAirport": "BASE", "skills": list(skills)}
    e.update(over)
    return e


def _chain(name, t0, t1, rest=600, seats=("КВС", "ВП"), month=None,
           block=0):
    return {"sourceUnit": name,
            "busyWindows": [[_mins(t0), _mins(t1)]],
            "minRestAfterMin": rest,
            "seats": list(seats),
            "seatCodes": [[s] for s in seats],
            "blockMinutesByMonth": ({month: block} if month else {}),
            "requiredCodesAll": []}


def _solve(emps, chains, **kw):
    return rc.build_and_solve({"employees": emps},
                              {"selected": chains}, budget_s=10, **kw)


def test_непересечение_и_отдых_между_цепочками():
    """Один человек не берёт две цепочки, разделённые отдыхом меньше
    нормативного; людей на все места не хватает — пустые места
    называются поимённо, движок не падает."""
    emps = [_emp("E1"), _emp("E2")]
    chains = [
        _chain("c1", "2026-08-01T08:00:00", "2026-08-01T18:00:00",
               rest=600),
        # зазор 400 мин < отдыха 600 — конфликт
        _chain("c2", "2026-08-02T00:40:00", "2026-08-02T10:00:00"),
    ]
    res = _solve(emps, chains)
    per_emp = {}
    for a in res["assignments"]:
        per_emp.setdefault(a["employee"], set()).add(a["chain"])
    for eid, cs in per_emp.items():
        assert len(cs) <= 1, (eid, cs)
    assert res["slots_filled"] == 2
    assert len(res["slots_empty"]) == 2
    for row in res["slots_empty"]:
        assert row["reason"]


def test_достаточный_отдых_разрешает_обе_цепочки():
    emps = [_emp("E1"), _emp("E2")]
    chains = [
        _chain("c1", "2026-08-01T08:00:00", "2026-08-01T18:00:00",
               rest=600),
        _chain("c2", "2026-08-02T06:00:00", "2026-08-02T16:00:00"),
    ]
    res = _solve(emps, chains)
    assert res["slots_filled"] == 4 and not res["slots_empty"]


def test_недоступные_дни_исключают_сотрудника():
    emps = [_emp("E1", unavailableDays=["2026-08-01"]), _emp("E2")]
    chains = [_chain("c1", "2026-08-01T08:00:00",
                     "2026-08-01T18:00:00")]
    res = _solve(emps, chains)
    who = {a["employee"] for a in res["assignments"]}
    assert "E1" not in who
    assert res["slots_filled"] == 1 and len(res["slots_empty"]) == 1


def test_антипара_не_летит_одной_цепочкой():
    emps = [_emp("E1", doNotFlyWith=["E2"]), _emp("E2")]
    chains = [_chain("c1", "2026-08-01T08:00:00",
                     "2026-08-01T18:00:00")]
    res = _solve(emps, chains)
    who = {a["employee"] for a in res["assignments"]}
    assert len(who) == 1, who


def test_месячный_накопитель_налёта():
    emps = [_emp("E1")]
    chains = [
        _chain("c1", "2026-08-01T08:00:00", "2026-08-01T18:00:00",
               rest=60, seats=("КВС",), month="2026-08", block=3000),
        _chain("c2", "2026-08-03T08:00:00", "2026-08-03T18:00:00",
               seats=("КВС",), month="2026-08", block=3000),
    ]
    res = _solve(emps, chains, month_block_cap_min=5400)
    assert res["slots_filled"] == 1, res
    res2 = _solve(emps, chains, month_block_cap_min=6100)
    assert res2["slots_filled"] == 2, res2


def test_роль_места_обязательна():
    emps = [_emp("E2", skills=("ВП",))]
    chains = [_chain("c1", "2026-08-01T08:00:00",
                     "2026-08-01T18:00:00", seats=("КВС",))]
    res = _solve(emps, chains)
    assert res["slots_filled"] == 0
    assert res["slots_empty"][0]["reason"].startswith("нет подходящего")


def test_объём_движка_декларируется_честно():
    res = _solve([_emp("E1")], [])
    scope = res["engine_scope"]
    assert scope["judged"] and scope["not_judged"]
    assert any("виз" in x for x in scope["not_judged"])
    assert res["engine"] == "cpsat-roster-v1"
