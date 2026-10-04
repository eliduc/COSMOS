# -*- coding: utf-8 -*-
"""Параметры-коды (решение владельца 03.09): шаблон обезличен, а
обозначение справочника авиакомпании (тип судна, база, роль) приходит
значением параметра из документа и обязано встречаться в данных."""
import pytest

from cdpm import model as m


def _param(name, value, unit, as_written, relation="eq"):
    return {"name": name, "value": value, "unit": unit,
            "as_written": as_written, "relation": relation}


def test_code_units_declared_and_list_canonicalized():
    assert "code" in m.PARAM_UNITS and "code_list" in m.PARAM_UNITS
    assert m.CODE_UNITS == ("code", "code_list")
    assert m.split_code_list("A320,  B737-800 ,") == ["A320", "B737-800"]
    assert m.canon_code_list("B737-800, A320, A320") == "A320, B737-800"
    assert m.canon_code_list("Полярис-Р200") == "Полярис-Р200"


def test_code_parameter_validation(monkeypatch):
    """Код — строка из документа: число, перечень в единице «код» и
    обозначение, которого нет в следе, отвергаются."""
    monkeypatch.setitem(m.PARAMS, "fleet_type", "code")
    monkeypatch.setitem(m.PARAMS, "fleet_types", "code_list")
    ok = m.validate_param(_param("fleet_type", "Полярис-Р200", "code", "тип судна Полярис-Р200"), "HARD")
    assert ok == [], ok
    num = m.validate_param(_param("fleet_type", 200, "code", "Полярис-Р200"), "HARD")
    assert any("непустая строка" in p for p in num)
    many = m.validate_param(_param("fleet_type", "A320, B737", "code",
                                   "A320, B737"), "HARD")
    assert any("ОДНО обозначение" in p for p in many)
    unsorted_ = m.validate_param(_param("fleet_types", "B737, A320", "code_list",
                                        "парк: B737, A320"), "HARD")
    assert any("не канонизирован" in p for p in unsorted_)
    alien = m.validate_param(_param("fleet_type", "ЛТ-100", "code",
                                    "тип судна Полярис-Р200"), "HARD")
    assert any("нет в следе" in p for p in alien)


def test_codes_absent_in_data_finds_alien_designation():
    doc = {"rules": [
        {"id": "VN-100", "kind": "DEF",
         "codes": [{"code": "Полярис-Р200", "quote": "Полярис-Р200"},
                   {"code": "ЛТ-100", "quote": "ЛТ-100"}]},
        {"id": "VN-101", "kind": "HARD",
         "params": [_param("fleet_type", "Полярис-Р200", "code",
                           "Полярис-Р200")]}]}
    dataset = {"tables": {"D1": {"Рейсы": {"columns": ["Тип ВС"],
                                           "rows": [{"Тип ВС": "Полярис-Р200"}]}}}}
    absent = m.codes_absent_in_data(doc, dataset)
    assert [x["code"] for x in absent] == ["ЛТ-100"]
    assert absent[0]["rule"] == "VN-100"
    # Все обозначения в данных — находок нет.
    dataset["tables"]["D1"]["Рейсы"]["rows"].append({"Тип ВС": "ЛТ-100"})
    assert m.codes_absent_in_data(doc, dataset) == []
    # Параметр-код тоже считается источником обозначения.
    got = {(x["rule"], x["param"], x["code"]) for x in m.parameter_codes(doc)}
    assert ("VN-101", "fleet_type", "Полярис-Р200") in got


@pytest.mark.parametrize("value,expected", [
    ("Полярис-Р200", ["Полярис-Р200"]),
    ("A320, B737-800", ["A320", "B737-800"]),
    ("", []),
])
def test_split_code_list_shapes(value, expected):
    assert m.split_code_list(value) == expected
