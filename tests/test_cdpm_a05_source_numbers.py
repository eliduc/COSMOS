"""CDPM-304: source identifiers/fractions are not extracted magnitudes.

Exercise the public batch writer, not just its lexical helper. The credit
fixture tests the writer directly (empty landing), not a schema-valid producer.
"""
import copy

import pytest

from cdpm import extract, family_params as fp
from test_cdpm_a05_extract_landing import _count_ready, NAME


def _source(statement, unit):
    rule = _count_ready()
    rule["statement"] = statement
    name = NAME
    if unit == "credit_hours":
        name = "workload_spread_threshold"
        rule["template_fit"].update(family="balanced_workload", params={})
    return rule, name


@pytest.mark.parametrize("statement,quote,value", [
    ("Пункт 13/3.1 требует не более 5 смен подряд.", "13", 13),
    ("Приложение 7 к разделу 2: не более 9 смен.", "7", 7),
    ("Приложение 7 к разделу 2: не более 9 смен.", "2", 2),
    ("Не более 1/3 нормы.", "1", 1),
    ("Не более 1 / 3 нормы.", "3", 3),
    ("См. п. 7: не более 5 смен.", "7", 7),
    ("Таблица № 7: не более 5 смен.", "7", 7),
    ("Раздел 7.2: не более 5 смен.", "7", 7),
    ("Ссылка № 7 определяет не более 5 смен.", "7", 7),
    ("Глава 7 устанавливает не более 5 смен.", "7", 7),
])
@pytest.mark.parametrize("unit", ["count", "credit_hours"])
def test_source_number_is_not_a_parameter_value(unit, statement, quote, value):
    rule, name = _source(statement, unit)
    before = copy.deepcopy(rule)
    problem = extract.merge_soft_params(rule, [{"name": name, "value": value,
                            "relation": "max", "as_written": quote}])
    assert problem
    assert rule == before


@pytest.mark.parametrize("statement,quote,value", [
    ("Пункт 13/3.1 требует не более 5 смен подряд.", "5", 5),
    ("Приложение 7 к разделу 2: не более 9 смен.", "9", 9),
    ("Экипаж — 2 пилота.", "2", 2),
    ("Число дней подряд — не более 3.", "не более 3", 3),
    ("5 смен подряд.", "5 смен", 5),
    ("Пункт 5: не более 5 смен.", "5 смен", 5),
    ("По пункту 7 — не более 5 смен.", "5", 5),
])
def test_real_bare_value_next_to_a_subject_or_reference_is_preserved(
        statement, quote, value, monkeypatch):
    rule, name = _source(statement, "count")
    calls = []
    real = fp.write_cell

    def spy(target, param, cell):
        calls.append(param)
        return real(target, param, cell)

    monkeypatch.setattr(fp, "write_cell", spy)
    assert extract.merge_soft_params(rule, [{"name": name, "value": value,
                            "relation": "max", "as_written": quote}]) is None
    assert calls == [name]
    cell = rule["template_fit"]["params"][name]
    assert (cell["value"], cell["quote"]) == (value, quote)


@pytest.mark.parametrize("statement,quote", [
    ("Разброс кредитных часов — не более 5.", "5"),
    ("Пункт 7: разброс кредитных часов — не более 5.", "не более 5"),
    ("Пункт 5: разброс кредитных часов — не более 5.", "не более 5"),
])
def test_healthy_bare_credit_magnitude_is_preserved(statement, quote):
    rule, name = _source(statement, "credit_hours")
    assert extract.merge_soft_params(rule, [{"name": name, "value": 5,
                            "relation": "max", "as_written": quote}]) is None
    assert rule["template_fit"]["params"][name]["value"] == 5


def test_source_number_late_in_batch_does_not_leave_the_good_first_cell():
    rule, name = _source("Смена не более 13 часов. Пункт 7 ограничивает число смен.", "count")
    rule["template_fit"]["params"] = {}
    before = copy.deepcopy(rule)
    assert extract.merge_soft_params(rule, [
        {"name": "max_duty_duration", "value": 780, "relation": "max", "as_written": "13 часов"},
        {"name": name, "value": 7, "relation": "max", "as_written": "7"},
    ])
    assert rule == before


@pytest.mark.parametrize("quote,value", [
    ("3 суток", 4320), ("13 часов", 780), ("10:00", 600),
    ("13,5 часа", 810), ("10 часов 30 минут", 630),
])
def test_whole_duration_keeps_its_existing_interpretation(quote, value):
    rule, _ = _source("Смена не более " + quote, "count")
    rule["template_fit"]["params"] = {
        NAME: {"value": 3, "quote": "число дней подряд — 3", "relation": "max"}}
    assert extract.merge_soft_params(rule, [{"name": "max_duty_duration", "value": value,
                            "relation": "max", "as_written": quote}]) is None
    assert rule["template_fit"]["params"]["max_duty_duration"]["value"] == value


@pytest.mark.parametrize("statement,quote,value", [
    ("Смена не более 10 часов 30 минут", "10 часов", 600),
    ("Смена не более 13 часов", "13", 13),
    ("Допустимо 10-12 смен", "12", 12),
    ("Допустимо 1⁄3 нормы", "1", 1),
    ("Допустимо 1∕3 нормы", "3", 3),
])
def test_other_numeric_fragments_stay_rejected(statement, quote, value):
    rule, name = _source(statement, "count")
    before = copy.deepcopy(rule)
    assert extract.merge_soft_params(rule, [{"name": name, "value": value,
                            "relation": "max", "as_written": quote}])
    assert rule == before
