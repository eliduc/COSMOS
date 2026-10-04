# -*- coding: utf-8 -*-
"""Внешний аудит 04.09.2026, A5 — закрепления в единой модели ресурсов.

До A5 закреплённые цепочки жили отдельным списком «данностей»: рейс
закреплённой цепочки не выполнял ни один свободный кандидат, поэтому
свободная цепочка с перегоном на таком рейсе отсеивалась как «перегон на
невыполняемом рейсе» (а в модели принудительно обнулялась); лимиты мест
на рейсе, перевозок в день и ёмкость баз строились только по свободным
кандидатам — перегоны и дни закреплённых цепочек из них не вычитались.

Стенды с известным ответом:
  1. перегон свободной цепочки на рейсе закреплённой — законен: участок,
     достижимый только этим перегоном, покрывается, а не становится
     ложной сиротой;
  2. предел мест на рейсе уменьшается на перегоны закреплённых цепочек
     (группа появляется даже при одном свободном кандидате); перебор
     закреплениями — честная запись переполнения;
  3. ёмкость базы уменьшается на календарные дни закреплённых цепочек:
     при запасе свободная цепочка входит, без запаса — не входит.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

_pb = pytest.importorskip("_pairing_builder")
from tests.test_pairing_builder import _args, _leg, _payload   # noqa: E402


def _pin(*fns_groups, unit="locked:1") -> dict:
    fns = [f for g in fns_groups for f in g]
    return {"selected": [{"fns": fns, "active_fns": fns,
                          "duty_fns": [list(g) for g in fns_groups],
                          "sourceUnit": unit}]}


def _world_dh_on_pinned() -> dict:
    # Закреплённая командировка: L1 BASE→AAA 1-го, L2 AAA→BASE 2-го.
    # Свободный участок F1 AAA→BASE 1-го в 12:00 достижим ТОЛЬКО
    # перегоном рейсом L1 (в AAA больше ничего не летит).
    return _payload([
        _leg("L1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("L2", "AAA", "BASE", "2026-08-02T10:00:00", 120),
        _leg("F1", "AAA", "BASE", "2026-08-01T12:00:00", 120)])


def test_deadhead_on_pinned_flight_is_legal_and_covers_the_orphan():
    catalog, report = _pb.build(
        _world_dh_on_pinned(),
        _args(rest_off=600, sit_max=300, w_dh=0, dh_options=2),
        pin_catalog=_pin(["L1"], ["L2"]))
    assert report["pinned_chains"] == 1
    assert report["dropped_ghost_dh_chains"] == 0
    assert report["uncovered_legs"] == 0, report.get("uncovered_reasons")
    free = [u for u in catalog["selected"] if not u.get("pinned")]
    assert len(free) == 1
    assert free[0]["active_fns"] == ["F1"]
    assert "L1" in free[0]["fns"]                    # перегон рейсом L1
    assert report["fixed_resources"]["working_legs"] == 2
    assert report["fixed_resources"]["dh"] == 0
    assert report.get("pin_resource_overflow", []) == []


def test_without_pins_the_same_world_is_covered_by_free_chains():
    """Контроль стенда: без закреплений участки покрываются свободным
    перебором — сирота выше была ложной, а не свойством данных."""
    catalog, report = _pb.build(
        _world_dh_on_pinned(),
        _args(rest_off=600, sit_max=300, w_dh=0, dh_options=2))
    assert report["uncovered_legs"] == 0


def test_dh_limit_per_flight_counts_pinned_deadheads():
    b = _pb.Builder(_world_dh_on_pinned(), _args())
    b.rules = SimpleNamespace(max_dh_per_flight=1, max_dh_per_day=None,
                              dh_limit_counts="all", base_tz_offset_min=0)
    cands = [{"legs": ["F1"], "dh": ["L1"], "duties": []}]
    # без закреплений: один кандидат в пределе 1 — группы нет
    assert b.dh_limit_groups(cands, [0]) == []
    # перегон закреплённой цепочки занял место: предел для свободных 0
    assert b.dh_limit_groups(cands, [0], fixed_dh=["L1"]) == [([0], 0)]
    assert b.report.get("pin_resource_overflow", []) == []
    # два закреплённых перегона при пределе 1 — переполнение словами
    b.dh_limit_groups(cands, [0], fixed_dh=["L1", "L1"])
    over = b.report["pin_resource_overflow"]
    assert over and over[0]["kind"] == "max_dh_per_flight" \
        and over[0]["flight"] == "L1" and over[0]["pinned"] == 2 \
        and over[0]["cap"] == 1


def _world_base_cap() -> dict:
    return _payload([
        # закреплённая командировка на два календарных дня с BASE
        _leg("L1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("L2", "AAA", "BASE", "2026-08-02T10:00:00", 120),
        # свободная однодневная цепочка с той же базы
        _leg("F1", "BASE", "BBB", "2026-08-01T07:00:00", 120),
        _leg("F2", "BBB", "BASE", "2026-08-01T11:00:00", 120)])


def test_base_day_cap_is_reduced_by_pinned_chain_days():
    common = dict(rest_off=600, sit_max=300, w_dh=0)
    # ёмкость 3 дня: закрепление берёт 2, свободной хватает 1
    cat, rep = _pb.build(_world_base_cap(),
                         _args(base_day_cap="BASE=3", **common),
                         pin_catalog=_pin(["L1"], ["L2"]))
    assert rep["fixed_resources"]["base_days"] == {"BASE": 2}
    assert rep["uncovered_legs"] == 0
    assert rep["base_day_cap_constraints"] == 1
    # ёмкость 2 дня: всё съело закрепление — свободная цепочка не входит
    cat2, rep2 = _pb.build(_world_base_cap(),
                           _args(base_day_cap="BASE=2", **common),
                           pin_catalog=_pin(["L1"], ["L2"]))
    assert rep2["uncovered_legs"] == 2 or cat2["coverage"]["verdict"] != "OK"
    assert rep2.get("pin_resource_overflow", []) == []
    # ёмкость 1 день: закрепление само её превышает — честная запись
    cat3, rep3 = _pb.build(_world_base_cap(),
                           _args(base_day_cap="BASE=1", **common),
                           pin_catalog=_pin(["L1"], ["L2"]))
    over = rep3["pin_resource_overflow"]
    assert over and over[0]["kind"] == "base_day_cap" \
        and over[0]["base"] == "BASE" and over[0]["pinned"] == 2
    assert cat3["coverage"]["verdict"] != "OK"
