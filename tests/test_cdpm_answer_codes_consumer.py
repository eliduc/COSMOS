"""CDPM-224/226: validate the fleet value the consumer actually receives.

Synthetic, in-memory pipeline only; no solver, live server or customer book.
No mutation or automatic reconciliation of conflicting provenance is allowed.
"""
import copy

import pytest

from cdpm import answers, model
from cdpm import export_task_package as package
from vmp import execution_manifest as execution
from vmp import formulation_check as fc


def _facts(has_column=True):
    return {"present": True, "airports": {"SVO"}, "bases": {"SVO"},
            "aircraft_types": {"A320", "B738"},
            "has_type_column": has_column}


def _rule(name, value, source):
    rule = {"id": "R-FLEET", "kind": "HARD", "zone": "BOTH"}
    if source == "params":
        rule["params"] = [{"name": name, "value": value, "as_written": "q"}]
    else:
        cell = {"value": value, "quote": "q"} if source == "cell" else value
        rule["template_fit"] = {"category": "A", "family": "fleet_exclusions",
                                "params": {name: cell}}
    return rule


@pytest.mark.parametrize("name", ["fleet_types", "fleet_type"])
@pytest.mark.parametrize("source", ["params", "cell", "scalar"])
@pytest.mark.parametrize("value,missing", [
    ("A320", []), ("A320, B738", []), (["A320", "B738"], []),
    ("ZZZZ", ["ZZZZ"]), ("A320, ZZZZ", ["ZZZZ"]),
    (["A320", "ZZZZ"], ["ZZZZ"]),
])
def test_same_effective_codes_are_checked_for_each_carrier(name, source,
                                                         value, missing):
    rule = _rule(name, value, source)
    before = copy.deepcopy(rule)
    hits = fc.check_codes({}, {"rules": [rule]}, _facts())
    assert rule == before
    if not missing:
        assert hits == []
        return
    assert len(hits) == 1 and hits[0]["level"] == fc.STOP
    assert hits[0]["codes"] == missing and hits[0]["id"] == "R-FLEET"
    prefix = "params" if source == "params" else "template_fit.params"
    assert hits[0]["field"] == f"{prefix}.{name}"


@pytest.mark.parametrize("source", ["params", "cell", "scalar"])
def test_missing_type_column_warns_instead_of_silent_success_or_stop(source):
    rule = _rule("fleet_types", "A320, ZZZZ", source)
    hits = fc.check_codes({}, {"rules": [rule]}, _facts(False))
    assert len(hits) == 1 and hits[0]["level"] == fc.WARN
    assert hits[0]["codes"] == ["A320", "ZZZZ"]
    assert "Тип ВС" in hits[0]["message"]
    no_data = fc.check_codes({}, {"rules": [rule]}, {"present": False})
    assert len(no_data) == 1 and no_data[0]["level"] == fc.WARN


@pytest.mark.parametrize("legacy,current,missing", [
    (["A320"], "A320, ZZZZ", ["ZZZZ"]),
    (["ZZZZ"], "A320", []),
    (["A320"], "A320", []),
])
def test_effective_precedence_matches_execution_without_rewriting_history(
        legacy, current, missing):
    rule = _rule("fleet_types", current, "cell")
    rule["template_fit"]["registry_rev"] = model.registry_fingerprint()
    rule["params"] = [{"name": "fleet_types", "value": legacy,
                       "unit": "code_list", "relation": "eq",
                       "as_written": "исходный текст", "qualifier": "база XXX"}]
    before = copy.deepcopy(rule)
    effective = execution.rule_params(rule)[0]
    assert effective["value"] == current
    hits = fc.check_codes({}, {"rules": [rule]}, _facts())
    fleet = [h for h in hits if h["kind"] == "aircraft_type"]
    assert [h["codes"] for h in fleet] == ([missing] if missing else [])
    assert any(h["field"] == "params.fleet_types.qualifier" for h in hits)
    assert rule == before
    if legacy != [current]:
        assert model.reconcile_carriers(rule)  # A05 still holds both carriers.


@pytest.mark.parametrize("raw,has_column,level", [
    ("A320, ZZZZ", True, fc.STOP), ("ZZZZ", True, fc.STOP),
    ("A320, B738", True, None), ("A320", True, None),
    ("A320, ZZZZ", False, fc.WARN),
])
def test_question_answer_package_effective_value_consumer(raw, has_column, level):
    from test_cdpm_k02_number_grammar import _prepared_fleet, QID_FLEET
    doc, rule, question = _prepared_fleet()
    result = answers.apply_answers(doc, {QID_FLEET: raw})
    assert result["applied"] == 1 and not model.is_open_question(question)
    assert not rule.get("params") and not rule.get("codes")
    package.enrich_rules_for_package(doc)
    package.heal_for_package(doc)
    rule = next(r for r in doc["rules"] if r["id"] == rule["id"])
    effective = next(p for p in execution.rule_params(rule)
                     if p["name"] == "fleet_types")
    assert effective["value"] == raw
    assert effective["quote"] == raw and effective["from"] == "template_fit"
    hits = fc.check_codes({}, {"rules": [rule]}, _facts(has_column))
    assert len(hits) == (0 if level is None else 1)
    if level is not None:
        assert hits[0]["level"] == level
        assert "ZZZZ" in hits[0]["codes"]
    # Public consumer verdict: facts are derived from normalized data,
    # not the injected facts helper above. No live button/solver is run.
    columns = ["Вылет", "Прилёт"] + (["Тип ВС"] if has_column else [])
    rows = [{"Вылет": "SVO", "Прилёт": "SVO",
             **({"Тип ВС": code} if has_column else {})}
            for code in ("A320", "B738")]
    data = {"tables": {"synthetic": {"Рейсы": {
        "columns": columns, "rows": rows}}}}
    verdict = fc.check({"form": {}, "model": {"rules": [rule]},
                        "clean_data": data})
    assert verdict["verdict"] == (fc.REJECTED if level == fc.STOP
                                   else fc.ACCEPTED)
    assert verdict["stops"] == int(level == fc.STOP)
    assert verdict["warnings"] == int(level == fc.WARN)


@pytest.mark.parametrize("raw", ["Airbus A320", "Base SVO", "ATR 72-600"])
def test_closed_cdpm217_is_not_reopened_by_consumer_fix(raw):
    from test_cdpm_k02_number_grammar import _prepared_fleet, QID_FLEET
    doc, rule, question = _prepared_fleet()
    result = answers.apply_answers(doc, {QID_FLEET: raw})
    assert result["applied"] == 0 and model.is_open_question(question)
    assert QID_FLEET in rule["blockers"]
    assert not execution.rule_params(rule)


def test_checker_uses_the_execution_reader(monkeypatch):
    rule = _rule("fleet_types", "A320", "cell")
    calls = []
    def effective(actual):
        assert actual is rule
        calls.append(actual)
        return [{"name": "fleet_types", "value": "ZZZZ",
                 "from": "template_fit"}]
    monkeypatch.setattr(execution, "rule_params", effective)
    hits = fc.check_codes({}, {"rules": [rule]}, _facts())
    assert len(calls) == 1 and hits[0]["codes"] == ["ZZZZ"]
