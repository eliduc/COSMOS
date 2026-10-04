# -*- coding: utf-8 -*-
"""Затравка цепочками (этап Г, Г-Д2): построитель линий и раздача.

Держит контракт webapp/chain_seed.py: линии непрерывны по аэропортам и
времени с зазором стыковки, построение детерминировано, раздача чтит
навык и базу, затравка не трогает исходную полезную нагрузку, пороги
приходят только из пакета постановки либо явными аргументами.

Пороги в тестах — СИНТЕТИКА (не числа какого-либо заказчика), подобраны
под топологию тестовых сетей: зазоры 90/120 мин внутри [60, 360),
размахи смен 210/240 <= 600.
"""

import pytest

from webapp import chain_seed as cs

_THR = dict(sit_min=60, max_duty_min=600, rest_min=540, max_block_min=6000)


def _f(num, dep, dep_t, arr, arr_t):
    return {"flightNumber": num, "flightLegId": num,
            "departureAirport": dep, "departureUTCDateTime": dep_t,
            "arrivalAirport": arr, "arrivalUTCDateTime": arr_t}


def test_линии_непрерывны_и_чтят_зазор_стыковки():
    flights = [
        _f("F1", "AAA", "2026-03-01T06:00:00", "BBB", "2026-03-01T07:00:00"),
        # зазор 90 мин >= 60 — продолжает линию F1
        _f("F2", "BBB", "2026-03-01T08:30:00", "AAA", "2026-03-01T09:30:00"),
        # вылет из BBB через 30 мин после посадки F1 — линии F1 не по зубам,
        # открывается вторая линия
        _f("F3", "BBB", "2026-03-01T07:30:00", "CCC", "2026-03-01T08:30:00"),
    ]
    lines = cs.build_lines(flights, **_THR)
    assert cs.continuity_defects(lines) == 0
    assert sorted(len(l) for l in lines) == [1, 2]
    two = next(l for l in lines if len(l) == 2)
    assert [f["flightNumber"] for f in two] == ["F1", "F2"]


def test_линии_берут_раньше_всех_освободившийся_конец():
    flights = [
        _f("F1", "AAA", "2026-03-01T06:00:00", "BBB", "2026-03-01T07:00:00"),
        _f("F2", "AAA", "2026-03-01T06:30:00", "BBB", "2026-03-01T07:30:00"),
        _f("F3", "BBB", "2026-03-01T09:00:00", "AAA", "2026-03-01T10:00:00"),
    ]
    lines = cs.build_lines(flights, **_THR)
    assert cs.continuity_defects(lines) == 0
    first = next(l for l in lines if l[0]["flightNumber"] == "F1")
    # F3 продолжает линию F1: её конец освободился раньше (07:00 < 07:30)
    assert [f["flightNumber"] for f in first] == ["F1", "F3"]


def test_построение_детерминировано():
    flights = [
        _f("F%d" % i, "AAA" if i % 2 else "BBB", "2026-03-01T%02d:00:00" % (6 + i),
           "BBB" if i % 2 else "AAA", "2026-03-01T%02d:30:00" % (6 + i))
        for i in range(6)
    ]
    a = cs.build_lines(flights, **_THR)
    b = cs.build_lines(list(reversed(flights)), **_THR)
    assert [[f["flightNumber"] for f in l] for l in a] == \
           [[f["flightNumber"] for f in l] for l in b]


def test_раздача_чтит_базу_и_оставляет_резерв():
    lines = [
        [_f("F1", "AAA", "2026-03-01T06:00:00", "BBB", "2026-03-01T07:00:00"),
         _f("F2", "BBB", "2026-03-01T08:30:00", "AAA", "2026-03-01T09:30:00")],
        [_f("F3", "BBB", "2026-03-01T07:30:00", "CCC", "2026-03-01T08:30:00")],
    ]
    employees = [
        {"id": "E-B", "homeAirport": "BBB", "skills": ["Captain"]},
        {"id": "E-A", "homeAirport": "AAA", "skills": ["Captain"]},
        {"id": "E-X", "homeAirport": "CCC", "skills": ["Captain"]},
    ]
    picked = cs.assign_lines(lines, employees)
    # длинная линия стартует из AAA — её берёт сотрудник базы AAA
    assert picked[0] == "E-A" and picked[1] == "E-B"
    assert len(picked) == 2  # третий сотрудник остаётся свободным (резерв)


def test_затравка_заполняет_кресла_по_навыку_и_не_трогает_исходник():
    flights = [
        _f("F1", "AAA", "2026-03-01T06:00:00", "BBB", "2026-03-01T07:00:00"),
        _f("F2", "BBB", "2026-03-01T08:30:00", "AAA", "2026-03-01T09:30:00"),
    ]
    payload = {
        "flights": flights,
        "employees": [{"id": "CAP", "homeAirport": "AAA", "skills": ["Captain"]},
                      {"id": "FO", "homeAirport": "AAA", "skills": ["FirstOfficer"]}],
        "flightAssignments": [
            {"id": "s%d-%d" % (i, k), "flight": f["flightNumber"],
             "indexInFlight": k, "requiredSkill": skill}
            for i, f in enumerate(flights)
            for k, skill in enumerate(("Captain", "FirstOfficer"))],
    }
    seeded, summary = cs.seed(payload, **_THR)
    assert summary["seats_seeded"] == 4 and summary["seats_total"] == 4
    for a in seeded["flightAssignments"]:
        assert a["employee"] == ("CAP" if a["requiredSkill"] == "Captain" else "FO")
    # исходная полезная нагрузка не тронута
    assert all("employee" not in a for a in payload["flightAssignments"])


def test_самопроверка_ловит_разрыв():
    broken = [[
        _f("F1", "AAA", "2026-03-01T06:00:00", "BBB", "2026-03-01T07:00:00"),
        _f("F2", "CCC", "2026-03-01T08:30:00", "AAA", "2026-03-01T09:30:00"),
    ]]
    assert cs.continuity_defects(broken) == 1


# ── Пороги из пакета постановки (числа ниже — синтетика теста) ──────────

def _param(name, value, unit="duration_minutes", relation="min",
           qualifier=None):
    return {"name": name, "value": value, "unit": unit,
            "relation": relation, "qualifier": qualifier}


def _pkg(*rules):
    return {"model": {"rules": [
        {"id": "R%d" % i, "kind": "HARD", "params": list(ps)}
        for i, ps in enumerate(rules, 1)]}}


def _full_pkg():
    return _pkg(
        [_param("min_turnaround", 35, qualifier="в базе"),
         _param("min_turnaround", 50, qualifier="вне базы")],
        [_param("max_duty_duration", 615, relation="max")],
        [_param("min_rest_home", 705), _param("min_rest_outstation", 585)],
        [_param("max_monthly_block", 5100, relation="max")],
    )


def test_пороги_из_пакета_сводятся_строже_правил():
    thr = cs.thresholds_from_package(_full_pkg())
    # стыковка — строжайшая из двух областей (max), отдых — строжайший
    # из домашнего и внебазового, пределы смены и налёта — min
    assert thr == {"sit_min": 50, "max_duty_min": 615,
                   "rest_min": 705, "max_block_min": 5100}
    colgen = cs.colgen_thresholds_from_package(_full_pkg())
    assert colgen == {"sit_min": 50, "max_duty_min": 615,
                      "rest_base_min": 705, "rest_out_min": 585,
                      "max_block_min": 5100}


def test_пороги_из_пакета_отказ_без_подстановки_соседней_области():
    # без min_rest_home — отказ с именем, а НЕ внебазовые 585 на обе области
    pkg = _pkg(
        [_param("min_turnaround", 35)],
        [_param("max_duty_duration", 615, relation="max")],
        [_param("min_rest_outstation", 585)],
        [_param("max_monthly_block", 5100, relation="max")],
    )
    with pytest.raises(RuntimeError, match="min_rest_home"):
        cs.thresholds_from_package(pkg)


def test_пороги_из_пакета_отказ_на_чужой_единице():
    pkg = _full_pkg()
    pkg["model"]["rules"][0]["params"][0]["unit"] = "count"
    with pytest.raises(RuntimeError, match="min_turnaround"):
        cs.thresholds_from_package(pkg)
