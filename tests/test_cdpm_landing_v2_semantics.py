"""Typed landing uses the pinned catalog, not guesses from scalar values."""
import copy

import pytest

from cdpm import family_params as fp, model as m
from test_k01_family_manifest import setup_case
from test_k01_semantic_transition import clean_bridge


def typed_case(tmp_path, monkeypatch):
    doc, _cat = setup_case(tmp_path, monkeypatch)
    rule = doc["rules"][-1]
    fit = rule["template_fit"]
    fit["schema_version"] = "landing/2"
    cell = fit["params"]["min_days_off"]
    cell.update(unit="count", relation="min", condition=None,
                source={"kind": "rule", "rule_id": rule["id"]})
    assert m.validate_schema(doc) == []
    assert m.validate_template_fit(doc) == []
    return doc, rule, cell


@pytest.mark.parametrize("change,field", [
    ({"unit": "duration_minutes"}, "unit"), ({"unit": "штук"}, "unit"),
    ({"relation": "max"}, "relation"), ({"relation": None}, "relation"),
    ({"value": "8"}, "value"), ({"value": True}, "value"),
    ({"value": -1}, "value"), ({"value": 1.5}, "value"),
    ({"condition": {"kind": "area", "value": "база"}}, "condition"),
    ({"source": {"kind": "rule", "rule_id": "R-OTHER"}}, "source"),
    ({"source": {"kind": "code", "note": " "}}, "source"),
])
def test_model_rejects_typed_semantics_without_changing_the_pin(tmp_path, monkeypatch, change, field):
    doc, rule, cell = typed_case(tmp_path, monkeypatch)
    pin = copy.deepcopy(rule["template_fit"]["family_contract"])
    cell.update(change)
    before = copy.deepcopy(doc)
    problems = m.validate_template_fit(doc)
    assert any(field in p and "min_days_off" in p for p in problems), problems
    assert doc == before
    assert rule["template_fit"]["family_contract"] == pin


@pytest.mark.parametrize("change", [{"unit": "duration_minutes"}, {"relation": "max"},
    {"relation": None}, {"value": "8"}, {"value": True},
    {"condition": {"kind": "area", "value": "база"}},
    {"source": {"kind": "rule", "rule_id": "R-OTHER"}}])
def test_real_writer_refuses_typed_candidate_atomically(tmp_path, monkeypatch, change):
    _doc, rule, cell = typed_case(tmp_path, monkeypatch)
    replacement = {**cell, **change}
    before = copy.deepcopy(rule)
    problem = fp.write_cell(rule, "min_days_off", replacement)
    assert problem, replacement
    assert rule == before


def test_real_writer_accepts_exact_typed_candidate_and_copies_it(tmp_path, monkeypatch):
    doc, rule, cell = typed_case(tmp_path, monkeypatch)
    pin = copy.deepcopy(rule["template_fit"]["family_contract"])
    replacement = copy.deepcopy(cell)
    replacement.update(value=9, quote="9")
    assert fp.write_cell(rule, "min_days_off", replacement) is None
    replacement["source"]["rule_id"] = "R-OTHER"
    assert rule["template_fit"]["params"]["min_days_off"]["value"] == 9
    assert rule["template_fit"]["params"]["min_days_off"]["source"]["rule_id"] == rule["id"]
    assert rule["template_fit"]["family_contract"] == pin
    assert m.validate_template_fit(doc) == []
