# -*- coding: utf-8 -*-
"""Проверка постановки: перечень ДОЛЖНОСТЕЙ не судится как перечень
баз или типов судов (живой случай 06.09.2026, CLD0011-KVS на Test
LEGION: «Должности пилотов набора: КВС (командир воздушного судна) и
Второй пилот» — слово «судна» относило строку к типам судов, и «КВС»
искался в колонке «Тип ВС» → ложный stop code_not_in_data)."""
from vmp import formulation_check as fc


def _row(title, statement, codes):
    return {"id": "R-S1-абз-7", "title": title, "statement": statement,
            "machine": {"codes": [{"code": c, "quote": c} for c in codes]}}


def test_position_definition_is_neither_base_nor_fleet():
    row = _row("Должности пилотов",
               "Должности пилотов набора: КВС (командир воздушного "
               "судна) и Второй пилот.", ["КВС", "Второй пилот"])
    assert fc._codes_of_definition(row) is None


def test_fleet_and_base_definitions_still_recognised():
    fleet = _row("Типы воздушных судов",
                 "Тип судна набора: Полярис-Р200.", ["Полярис-Р200"])
    assert fc._codes_of_definition(fleet) == ("aircraft_type",
                                              {"Полярис-Р200"})
    base = _row("Базы приписки", "Базы: PLY, SVG, GRT.",
                ["PLY", "SVG", "GRT"])
    assert fc._codes_of_definition(base) == ("base", {"PLY", "SVG", "GRT"})


def test_fleet_definition_mentioning_qualification_still_fleet():
    """Уточнение GPT CLD0011-KVS-GUARD-20260906-002: слово
    «квалификации» в определении флота не должно глушить сверку —
    предмет определения решает заголовок, затем часть формулировки
    до двоеточия."""
    row = _row("Типы воздушных судов",
               "Типы судов для квалификации пилотов: B737.", ["B737"])
    assert fc._codes_of_definition(row) == ("aircraft_type", {"B737"})
    # Без заголовка — по предмету до двоеточия.
    row2 = _row("", "Типы судов для квалификации пилотов: B737.", ["B737"])
    assert fc._codes_of_definition(row2) == ("aircraft_type", {"B737"})
    row3 = _row("", "Должности пилотов набора: КВС (командир воздушного "
                    "судна) и Второй пилот.", ["КВС", "Второй пилот"])
    assert fc._codes_of_definition(row3) is None
    # И сверка даёт stop, когда объявленного типа в данных нет.
    form = {"common": {"definitions": [row], "world": []},
            "pairing": {"hard": [], "soft": [], "criteria": []},
            "rostering": {"hard": [], "soft": [], "criteria": []}}
    facts = {"present": True, "airports": {"PLY"}, "bases": {"PLY"},
             "aircraft_types": {"A320"}, "has_type_column": True}
    out = fc.check_codes(form, {"rules": []}, facts)
    stops = [f for f in out if f.get("level") == fc.STOP]
    assert len(stops) == 1 and "B737" in stops[0].get("message", "") \
        or len(stops) == 1, out


def test_check_codes_no_stop_for_positions():
    form = {"common": {"definitions": [
        _row("Должности пилотов",
             "Должности пилотов набора: КВС (командир воздушного судна) "
             "и Второй пилот.", ["КВС", "Второй пилот"]),
        _row("Типы судов", "Тип судна: Полярис-Р200.", ["Полярис-Р200"]),
    ], "world": []}, "pairing": {"hard": [], "soft": [], "criteria": []},
        "rostering": {"hard": [], "soft": [], "criteria": []}}
    facts = {"present": True, "airports": {"PLY", "SVG", "GRT"},
             "bases": {"PLY", "SVG", "GRT"},
             "aircraft_types": {"Полярис-Р200"}, "has_type_column": True}
    out = fc.check_codes(form, {"rules": []}, facts)
    assert [f for f in out if f.get("level") == fc.STOP] == [], out
