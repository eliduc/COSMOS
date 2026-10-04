# -*- coding: utf-8 -*-
"""РВ-113 «канон карты» (Stage run-0024/run-0026, вопрос второго чтения
S1-абз-11): суд двух чтений сравнивает ЗНАЧЕНИЯ по смыслу — одно
значение в разной форме (перечень кодов списком и строкой, порядок,
регистр и пробелы кода, скаляр кода против перечня из одного, имя
fleet_type против fleet_types, число строкой) не «два чтения»; реально
разные значения различаются. Один канон — во всех местах сравнения:
fit_signature (суд второго чтения, суд дублей, дубль кандидата),
ставка цели, эквивалентность logic ↔ ручки."""
import copy

import pytest

from cdpm import model as m
from cdpm import second_reading as sr


def _rule(params, rid="R-S1-абз-11", cat="A", fam="fleet_exclusions"):
    return {"id": rid, "kind": "HARD", "zone": "BOTH",
            "title": "Допуск по типу воздушного судна",
            "statement": "Рейс на ВС типа Полярис-Р200 выполняет пилот с допуском.",
            "sources": [{"sid": "S1", "anchor": "абз.11"}],
            "template_fit": {"category": cat, "family": fam,
                             "params": {k: {"value": v, "quote": "q"}
                                        for k, v in params.items()}}}


SAME = [
    ({"fleet_types": ["Полярис-Р200"]}, {"fleet_types": "Полярис-Р200"}),   # run-0026
    ({"fleet_type": "Полярис-Р200"}, {"fleet_types": ["Полярис-Р200"]}),    # run-0024
    ({"fleet_types": ["A", "B"]}, {"fleet_types": ["B", "A"]}),
    ({"fleet_types": "A, B"}, {"fleet_types": ["b", " a "]}),
    ({"fleet_types": [" TYPE   A "]}, {"fleet_types": ["type a"]}),
    ({"fleet_types": ["A", "A", "B"]}, {"fleet_types": "B; A"}),
]
DIFFERENT = [
    ({"fleet_types": ["A"]}, {"fleet_types": ["B"]}),
    ({"fleet_types": ["A"]}, {"fleet_types": ["A", "B"]}),
    ({"fleet_type": "A"}, {"fleet_types": ["A", "B"]}),
    ({"fleet_types": []}, {"fleet_types": ["A"]}),
]


@pytest.mark.parametrize("left,right", SAME)
def test_one_value_in_different_forms_has_one_signature(left, right):
    assert m.fit_signature(_rule(left)) == m.fit_signature(_rule(right))


@pytest.mark.parametrize("left,right", DIFFERENT)
def test_really_different_values_differ(left, right):
    assert m.fit_signature(_rule(left)) != m.fit_signature(_rule(right))


def test_numbers_and_minutes_keep_prior_canon():
    a = {"category": "A", "family": "max_duty", "params": {"max_duty_duration": {"value": 720}}}
    b = {"category": "A", "family": "max_duty", "params": {"max_duty_duration": {"value": "12:00"}}}
    c = {"category": "A", "family": "max_duty", "params": {"max_duty_duration": {"value": "720"}}}
    sig = [m.fit_signature({"template_fit": f}) for f in (a, b, c)]
    assert sig[0] == sig[1] == sig[2]


def test_same_as_pairs_exist_in_catalog():
    fams = m.template_families()
    for (fam, alias), canon in m.PARAM_SAME_AS.items():
        names = set(fams.get(fam) or []) | set(m.family_conditions(fam))
        assert alias in names and canon in names, (fam, alias, canon)


def test_same_as_table_is_explicit_and_small():
    assert m.PARAM_SAME_AS == {("fleet_exclusions", "fleet_type"): "fleet_types"}


def test_second_reading_compare_sees_one_reading():
    """Канон слоя второго чтения: run-0024/run-0026 — одно чтение."""
    for left, right in SAME[:2]:
        assert sr._canon_rule(_rule(left)) == sr._canon_rule(_rule(right))
    for left, right in DIFFERENT[:2]:
        assert sr._canon_rule(_rule(left)) != sr._canon_rule(_rule(right))


def test_duplicate_finder_uses_the_canon():
    a = _rule({"fleet_types": ["Полярис-Р200"]}, rid="R-1")
    b = _rule({"fleet_type": "Полярис-Р200"}, rid="R-2")
    c = _rule({"fleet_types": ["Иной"]}, rid="R-3")
    doc = {"rules": [a, c]}
    assert m.find_duplicate_rule(doc, b) == "R-1"
    assert m.find_duplicate_rule({"rules": [c]}, b) is None


def test_objective_rate_number_and_string_are_one():
    o1 = {"zone": "ROSTERING", "direction": "min", "metric": "m",
          "criterion": {"kind": "k", "rate_per_duty": 100, "per": "за час"}}
    o2 = copy.deepcopy(o1)
    o2["criterion"]["rate_per_duty"] = "100"
    o3 = copy.deepcopy(o1)
    o3["criterion"]["rate_per_duty"] = "120"
    assert sr._canon_objective(o1) == sr._canon_objective(o2)
    assert sr._canon_objective(o1) != sr._canon_objective(o3)


def test_logic_equivalence_lookup_by_canonical_knob_name():
    logic = {"subject": "assignment",
             "condition": [{"predicate": "has_qualification_for",
                            "type_value": "Полярис-Р200", "as_written": "x"}],
             "demand": {"kind": "exists_person", "subject": "employee",
                        "as_written": "y"},
             "source_params": [], "enforce": "required"}
    r = _rule({"fleet_type": "Полярис-Р200"})
    r["logic"] = copy.deepcopy(logic)
    assert m.reconcile_logic_with_template_fit({"rules": [r]})
    assert r["logic"]["enforce"] == "post-check"
    # иное значение типа — эквивалентности нет (GPT-043 не ослаблен)
    r2 = _rule({"fleet_type": "Полярис-Р300"})
    r2["logic"] = copy.deepcopy(logic)
    assert m.reconcile_logic_with_template_fit({"rules": [r2]}) == []
    assert r2["logic"]["enforce"] == "required"


def test_conflicting_cells_of_one_knob_are_not_equivalence_gpt_0020():
    """fleet_type=Р300 и fleet_types=Р200 у одной посадки — конфликт:
    сведения к post-check нет, required остаётся (не перезапись)."""
    logic = {"subject": "assignment",
             "condition": [{"predicate": "has_qualification_for",
                            "type_value": "Полярис-Р200", "as_written": "x"}],
             "demand": {"kind": "exists_person", "subject": "employee",
                        "as_written": "y"},
             "source_params": [], "enforce": "required"}
    for order in (("fleet_type", "fleet_types"), ("fleet_types", "fleet_type")):
        vals = {"fleet_type": "Полярис-Р300", "fleet_types": ["Полярис-Р200"]}
        r = _rule({k: vals[k] for k in order})
        r["logic"] = copy.deepcopy(logic)
        assert m.reconcile_logic_with_template_fit({"rules": [r]}) == []
        assert r["logic"]["enforce"] == "required"
    # те же значения в двух ячейках — одна ручка, эквивалентность есть
    r = _rule({"fleet_type": "Полярис-Р200", "fleet_types": ["Полярис-Р200"]})
    r["logic"] = copy.deepcopy(logic)
    assert m.reconcile_logic_with_template_fit({"rules": [r]})
    assert r["logic"]["enforce"] == "post-check"


def test_conflicting_alias_cells_are_not_absorbed_in_signature():
    """fleet_type=Р300 + fleet_types=Р200 не равно ни одной из ячеек и не
    зависит от порядка; второе чтение видит конфликт целиком."""
    a = _rule({"fleet_type": "Полярис-Р300", "fleet_types": ["Полярис-Р200"]})
    b = _rule({"fleet_types": ["Полярис-Р200"], "fleet_type": "Полярис-Р300"})
    only200 = _rule({"fleet_types": ["Полярис-Р200"]})
    only300 = _rule({"fleet_types": ["Полярис-Р300"]})
    assert m.fit_signature(a) == m.fit_signature(b)
    assert m.fit_signature(a) not in (m.fit_signature(only200),
                                      m.fit_signature(only300))
    ca = sr._canon_rule(a)
    assert ca == sr._canon_rule(b)
    assert ca not in (sr._canon_rule(only200), sr._canon_rule(only300))
    assert set(ca["params"]) == {"fleet_type", "fleet_types"}
    # одинаковые значения двух ячеек — одна запись канона
    same = _rule({"fleet_type": "Полярис-Р200", "fleet_types": ["Полярис-Р200"]})
    assert m.fit_signature(same) == m.fit_signature(only200)
