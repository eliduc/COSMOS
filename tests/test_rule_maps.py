"""Общий читатель секции rule_maps пакета постановки (ревизия 1.14).

Закон потребителя: пакет С подсекцией — значения из ПАКЕТА; нет секции
или подсекции — None (легаси-ветка вызывающего, его зашитые таблицы);
кривая форма — отказ словами типом ошибки вызывающего. Судится
НАЛИЧИЕ ПОДСЕКЦИИ: сборщик копирует из носителя только имеющиеся.

Все карты здесь синтетические: данных заказчика в тестах нет и быть
не может (условие МУ-07).
"""
from __future__ import annotations

import pytest

from webapp import rule_maps as rm


def _manifest(**sections) -> dict:
    return {"spec_version": "1.14", "rule_maps": sections}


# ------------------------------------------------- легаси-ветка (None)

def test_нет_секции_все_читатели_дают_none():
    for reader in (rm.crew_size_rules, rm.zone_executors,
                   rm.acceptance_rules):
        assert reader({}) is None
        assert reader({"spec_version": "1.13"}) is None


def test_нет_подсекции_судится_по_подсекции_а_не_по_секции():
    """Сборщик копирует только имеющиеся в носителе подсекции — их
    состав от пакета к пакету законно разный. Секция с одной
    подсекцией не должна ломать читателей остальных."""
    man = _manifest(crew_size_rules=["R-СИНТ-1"])
    assert rm.crew_size_rules(man) == ("R-СИНТ-1",)
    assert rm.zone_executors(man) is None
    assert rm.acceptance_rules(man) is None


@pytest.mark.parametrize("section", [{}, [], "строка", 7])
def test_секция_пуста_или_не_объект_отказ(section):
    man = {"rule_maps": section}
    with pytest.raises(rm.RuleMapsError) as e:
        rm.crew_size_rules(man)
    assert "rule_maps" in str(e.value)


def test_тип_ошибки_вызывающего():
    """Отказ идёт законом вызывающего: параметр error задаёт тип."""
    from webapp.task_package import TaskPackageRefused
    man = _manifest(crew_size_rules=[])
    with pytest.raises(TaskPackageRefused):
        rm.crew_size_rules(man, error=TaskPackageRefused)
    with pytest.raises(rm.RuleMapsError):
        rm.crew_size_rules(man)


# --------------------------------------------------- crew_size_rules

def test_crew_size_rules_годная_форма():
    got = rm.crew_size_rules(
        _manifest(crew_size_rules=["R-СИНТ-1", " R-СИНТ-2 "]))
    assert got == ("R-СИНТ-1", "R-СИНТ-2"), "строки обрезаются по краям"


@pytest.mark.parametrize("bad", [
    "не список", {}, {"R-СИНТ-1": 1}, [], [7], [""], ["   "],
    ["R-СИНТ-1", None],
])
def test_crew_size_rules_кривая_форма_отказ(bad):
    with pytest.raises(rm.RuleMapsError) as e:
        rm.crew_size_rules(_manifest(crew_size_rules=bad))
    assert "crew_size_rules" in str(e.value)


# ---------------------------------------------------- zone_executors

def test_zone_executors_годная_форма():
    got = rm.zone_executors(_manifest(zone_executors={
        "R-СИНТ-зона-1": {"constraint": "Synthetic nights", "literal": 40},
        "R-СИНТ-зона-2": {"constraint": "Synthetic nights", "literal": 40},
    }))
    assert got == {
        "R-СИНТ-зона-1": {"constraint": "Synthetic nights", "literal": 40},
        "R-СИНТ-зона-2": {"constraint": "Synthetic nights", "literal": 40},
    }


@pytest.mark.parametrize("bad", [
    "не объект", [], {},
    {"R-СИНТ": "не запись"},
    {"R-СИНТ": {"literal": 40}},                      # нет constraint
    {"R-СИНТ": {"constraint": "", "literal": 40}},
    {"R-СИНТ": {"constraint": "C"}},                  # нет literal
    {"R-СИНТ": {"constraint": "C", "literal": 0}},
    {"R-СИНТ": {"constraint": "C", "literal": -40}},
    {"R-СИНТ": {"constraint": "C", "literal": "40"}},
    {"R-СИНТ": {"constraint": "C", "literal": 40.5}},
    {"R-СИНТ": {"constraint": "C", "literal": True}},
    {"": {"constraint": "C", "literal": 40}},
])
def test_zone_executors_кривая_форма_отказ(bad):
    with pytest.raises(rm.RuleMapsError) as e:
        rm.zone_executors(_manifest(zone_executors=bad))
    assert "zone_executors" in str(e.value)


# -------------------------------------------------- acceptance_rules

def _acc(**over) -> dict:
    base = {"days_off": ["R-СИНТ-В1", "R-СИНТ-В2"],
            "consecutive": "R-СИНТ-П",
            "rest": "R-СИНТ-О",
            "rolling": "R-СИНТ-С",
            "night": "R-СИНТ-Н"}
    base.update(over)
    return base


def test_acceptance_rules_годная_форма():
    got = rm.acceptance_rules(_manifest(acceptance_rules=_acc()))
    assert got == {"days_off": ("R-СИНТ-В1", "R-СИНТ-В2"),
                   "consecutive": "R-СИНТ-П", "rest": "R-СИНТ-О",
                   "rolling": "R-СИНТ-С", "night": "R-СИНТ-Н"}


@pytest.mark.parametrize("bad,fragment", [
    ("не объект", "acceptance_rules"),
    ([], "acceptance_rules"),
    (_acc(days_off="R-СИНТ"), "days_off"),
    (_acc(days_off=[]), "days_off"),
    (_acc(days_off=[""]), "days_off"),
    (_acc(days_off=[7]), "days_off"),
    (_acc(consecutive=""), "consecutive"),
    (_acc(night=7), "night"),
    (_acc(chuzhoy="R-СИНТ"), "неизвестные ключи"),
    ({"days_off": ["R-СИНТ"]}, "нет ключей"),
])
def test_acceptance_rules_кривая_форма_отказ(bad, fragment):
    with pytest.raises(rm.RuleMapsError) as e:
        rm.acceptance_rules(_manifest(acceptance_rules=bad))
    assert fragment in str(e.value)


def test_живая_форма_полного_пакета_читается():
    """Секция живого пакета run-0047 (формы значений — те же, id
    здесь синтетические): все три читателя питона согласны."""
    man = _manifest(
        crew_size_rules=["R-СИНТ-числ-1", "R-СИНТ-числ-2"],
        zone_executors={"R-СИНТ-ночи": {
            "constraint": "Layover nights away from home", "literal": 40}},
        rule_to_constraint={"R-СИНТ-ночи":
                            "Layover nights away from home"},
        objective_to_constraint={"O-СИНТ-1": "Credit hours spread"},
        acceptance_rules=_acc())
    assert rm.crew_size_rules(man) == ("R-СИНТ-числ-1", "R-СИНТ-числ-2")
    assert rm.zone_executors(man)["R-СИНТ-ночи"]["literal"] == 40
    assert rm.acceptance_rules(man)["days_off"] == ("R-СИНТ-В1",
                                                    "R-СИНТ-В2")
