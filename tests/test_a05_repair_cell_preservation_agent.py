"""Focused repair compatibility controls, not live/auditor acceptance.

The saved answer is an explicit historical fixture verified by the actual
provenance reader; constructing it is not a claim to replay answer submission.
"""
import copy

import pytest

from cdpm import catalog_bridge as cb, family_params as fp, model as m, provenance, repair
from test_a05_repair_carrier_neighbors_agent import _duty_doc, DUTY_CLAUSE, DUTY_NAME
from test_a05_extracted_relation_contract_agent import catalog
from test_k01_semantic_transition import pair, clean_bridge


def _old_pinned_duty(tmp_path, monkeypatch, source):
    _unused, old, new = pair(tmp_path, monkeypatch, lambda docs: None)
    monkeypatch.setenv(cb.ENV_CATALOG, str(old.path))
    cb.forget()
    doc = _duty_doc()
    row = doc["rules"][0]
    assert row["template_fit"]["family_contract"]["catalog_version"] == "0.7.0"
    if source == "answer":
        name = "max_duty_duration"
        q = fp._new_question(doc, "duty_limits", name, [row], set())
        assert q is not None
        q["answer"] = {"raw": "8 часов", "status": "применён", "revision": 1}
        doc["questions"].append(q)
        cell = row["template_fit"]["params"][name]
        cell.update(answer=q["id"], source=provenance.answer_source(q, name, "8 часов"))
        assert provenance.cell_problems(doc, row["id"], "duty_limits", name, cell) == []
    monkeypatch.setenv(cb.ENV_CATALOG, str(new.path))
    cb.forget()
    assert cb.catalog().version() == "0.7.1"
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    assert m.validate_fit_quotes(doc, {("S1", "п.1"): DUTY_CLAUSE}) == []
    return doc


@pytest.mark.parametrize("source", ["rule", "answer"])
@pytest.mark.parametrize("value", [4, 5])
def test_missing_cell_preserves_older_pin_and_saved_neighbor(tmp_path, monkeypatch, source, value):
    doc = _old_pinned_duty(tmp_path, monkeypatch, source)
    row = doc["rules"][0]
    before = copy.deepcopy(doc)
    error = repair._apply(row, False, f"param:{DUTY_NAME}",
                          {"value": value, "quote": "4 посадок"}, DUTY_CLAUSE, doc)
    if value != 4:
        assert isinstance(error, str) and error
        assert doc == before
        return
    assert error is None, error
    fit = row["template_fit"]
    previous = before["rules"][0]["template_fit"]
    assert fit["family_contract"] == previous["family_contract"]
    assert fit["registry_rev"] == previous["registry_rev"]
    assert fit["params"]["max_duty_duration"] == previous["params"]["max_duty_duration"]
    assert doc["questions"] == before["questions"]
    assert fit["params"][DUTY_NAME] == {
        "value": 4, "quote": "4 посадок", "unit": "count", "relation": "max",
        "condition": None, "source": {"kind": "rule", "rule_id": row["id"]}}
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    assert m.validate_fit_quotes(doc, {("S1", "п.1"): DUTY_CLAUSE}) == []


@pytest.mark.parametrize("case", ["unknown-family", "wrong-parameter", "unknown-slot", "no-fit"])
def test_invalid_direct_target_refuses_in_words_without_mutation(catalog, case):
    # Explicit malformed receiver input, not a claimed scheduler slot.
    row = {"id": "R-LEGACY", "kind": "HARD",
           "sources": [{"sid": "S1", "anchor": "п.1"}],
           "template_fit": {"category": "A", "family": "rest_floors"}}
    slot = "param:min_rest_outstation"
    if case == "unknown-family":
        row["template_fit"]["family"] = "not_a_declared_family"
    elif case == "wrong-parameter":
        slot = "param:not_a_declared_parameter"
    elif case == "unknown-slot":
        slot = "not_a_declared_slot"
    else:
        row.pop("template_fit")
    before = copy.deepcopy(row)
    error = repair._apply(row, False, slot, {"value": 600, "quote": "10 часов"}, "Отдых 10 часов.")
    assert isinstance(error, str) and error
    assert row == before


@pytest.mark.parametrize("value", [600, 601])
def test_legacy_missing_cell_stays_legacy_and_rejects_unproved_value(catalog, value):
    row = {"id": "R-LEGACY", "kind": "HARD",
           "sources": [{"sid": "S1", "anchor": "п.1"}],
           "template_fit": {"category": "A", "family": "rest_floors"}}
    before = copy.deepcopy(row)
    error = repair._apply(row, False, "param:min_rest_outstation",
                          {"value": value, "quote": "10 часов"}, "Отдых 10 часов.")
    if value != 600:
        assert isinstance(error, str) and error and row == before
        return
    assert error is None, error
    assert row["template_fit"] == {
        **before["template_fit"], "params": {"min_rest_outstation": {"value": 600, "quote": "10 часов"}}}


@pytest.mark.parametrize("value", [480, 540])
def test_direct_receiver_does_not_overwrite_a_filled_cell(catalog, value):
    doc = _duty_doc()
    row = doc["rules"][0]
    before = copy.deepcopy(doc)
    error = repair._apply(row, False, "param:max_duty_duration",
                          {"value": value, "quote": "8 часов"}, DUTY_CLAUSE, doc)
    assert isinstance(error, str) and "уже заполнен" in error
    assert doc == before
