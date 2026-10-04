# -*- coding: utf-8 -*-
"""Ядро этапа 2 волны 1 (П-2): мягкие формы в модели, переводе и
парах.

Охраняемое: demand.kind волны законен только у SOFT и только со
ссылочными ролями *_param (числа — через params правила, стиль
count_within/starts_in); перевод в спецификацию детерминирован;
пары — самодостаточные микромиры, гейт пары судит ЕДИНИЦЫ
(spec.expected_units), нарушающий конец даёт ровно ожидание,
чистый — ноль; условие у мягкой формы — честный отказ волны."""
import pytest

from cdpm import data_checks as dc
from cdpm import logic_proof as lp
from cdpm import model as m


def _rule(kind_name, params, demand_extra, rid="R-W-1"):
    dem = {"kind": kind_name, "as_written": "как записано (тест)"}
    dem.update(demand_extra)
    return {
        "id": rid, "kind": "SOFT", "weight": 5,
        "title": "тестовая мягкая форма",
        "statement": "тестовое правило волны",
        "params": [{"name": n, "value": v} for n, v in params],
        "logic": {"subject": "assignment", "condition": [],
                  "demand": dem,
                  "source_params": [n for n, _ in params],
                  "enforce": "required"},
        "sources": [{"sid": "S1", "anchor": "т"}],
    }


CASES = [
    ("штраф-ступенчатый-за-стыковку",
     [("long_sit_threshold", 60), ("long_sit_step", 30)],
     {"threshold_param": "long_sit_threshold",
      "step_param": "long_sit_step"}, 2),
    ("штраф-за-разрыв-смены",
     [("duty_split_threshold", 240)],
     {"threshold_param": "duty_split_threshold"}, 1),
    ("однородность-цепочки-ночь-день",
     [("night_duty_start", 120), ("night_duty_end", 359)],
     {"window_from_param": "night_duty_start",
      "window_to_param": "night_duty_end"}, 1),
    ("мягкий-предел-дней-подряд",
     [("soft_consecutive_days", 10)],
     {"bound_param": "soft_consecutive_days"}, 2),
    ("неудовлетворённая-выполнимая-заявка", [], {}, 1),
]


@pytest.mark.parametrize("kind,params,extra,exp_bad",
                         CASES, ids=[c[0] for c in CASES])
def test_wave_form_end_to_end(kind, params, extra, exp_bad):
    rule = _rule(kind, params, extra)
    assert m.validate_logic(rule["logic"], rule) == []
    spec, why = lp.logic_to_check_spec(rule)
    assert spec is not None, why
    assert spec["template"] == kind
    pair, why2 = lp.generate_pair(rule)
    assert pair is not None, why2
    assert pair["spec"]["expected_units"] == {"bad": exp_bad,
                                              "good": 0}
    assert lp.check_pair(pair, rule["id"]) == []
    _n, bad = dc.run_spec(pair["spec"], pair["dataset_bad"])
    assert len(bad) == exp_bad
    _n, good = dc.run_spec(pair["spec"], pair["dataset_good"])
    assert good == []


def test_wave_kind_illegal_on_hard():
    rule = _rule("штраф-за-разрыв-смены",
                 [("duty_split_threshold", 240)],
                 {"threshold_param": "duty_split_threshold"})
    rule["kind"] = "HARD"
    errs = m.validate_logic(rule["logic"], rule)
    assert any("только у SOFT" in e for e in errs)


def test_wave_condition_refused():
    rule = _rule("штраф-за-разрыв-смены",
                 [("duty_split_threshold", 240)],
                 {"threshold_param": "duty_split_threshold"})
    rule["logic"]["condition"] = [
        {"predicate": "at_base", "as_written": "в базе"}]
    spec, why = lp.logic_to_check_spec(rule)
    assert spec is None
    assert "вне первой волны переводов" in why


def test_merge_soft_params_fail_closed():
    """params_add: имена только из PARAMS, целые ≥ 0, чужое имя и
    переписывание извлечённого — отказ; слияние оставляет след."""
    from cdpm import extract
    rule = {"kind": "SOFT", "params": [
        {"name": "long_sit_threshold", "value": 60}]}
    assert extract.merge_soft_params(rule, None) is None
    err = extract.merge_soft_params(rule, [{"name": "чужое",
                                            "value": 1}])
    assert err and "вне словаря" in err
    err = extract.merge_soft_params(
        rule, [{"name": "long_sit_threshold", "value": 90}])
    assert err and "переписывать нельзя" in err
    err = extract.merge_soft_params(
        rule, [{"name": "long_sit_step", "value": 30}])
    assert err and "без следа as_written" in err
    err = extract.merge_soft_params(
        rule, [{"name": "long_sit_step", "value": 30,
                "as_written": "30 минут", "relation": "шаг"}])
    assert err and "вне перечня" in err
    assert extract.merge_soft_params(
        rule, [{"name": "long_sit_step", "value": 30,
                "as_written": "за каждые полные 30 минут",
                "relation": "eq"},
               {"name": "long_sit_threshold", "value": 60}]) is None
    staged = {p["name"]: p for p in rule["params"]}
    assert set(staged) == {"long_sit_threshold", "long_sit_step"}
    assert staged["long_sit_step"]["as_written"] \
        == "за каждые полные 30 минут"
    assert staged["long_sit_step"]["relation"] == "eq"
    assert "long_sit_step=30" in rule["note"]


def test_wave_role_must_reference_params():
    rule = _rule("мягкий-предел-дней-подряд",
                 [("soft_consecutive_days", 10)],
                 {"bound_param": "нет_такого"})
    rule["logic"]["source_params"] = ["нет_такого"]
    errs = m.validate_logic(rule["logic"], rule)
    assert errs, "ссылка на несуществующий параметр обязана краснить"
