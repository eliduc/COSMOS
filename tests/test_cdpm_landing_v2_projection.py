"""Typed scope survives the shared legacy-consumer projection unchanged."""
import copy

import pytest

from cdpm import family_params as fp, model as m
from cdpm.param_projection import derive_params_from_landing, ParamProjectionError
from template_catalog import family_contract
from test_k01_family_manifest import setup_case
from test_k01_semantic_transition import clean_bridge


def typed_rule(tmp_path, monkeypatch, family, cells):
    doc, cat = setup_case(tmp_path, monkeypatch)
    rule = doc["rules"][-1]
    rule["template_fit"] = {"category": "A", "schema_version": "landing/2",
                            "family": family, "registry_rev": cat.fingerprint(),
                            "family_contract": family_contract.snapshot(cat, family, cells),
                            "params": copy.deepcopy(cells)}
    for cell in rule["template_fit"]["params"].values():
        cell["source"] = {"kind": "rule", "rule_id": rule["id"]}
    return doc, rule


def test_structured_area_becomes_legacy_qualifier_without_changing_landing(tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, "connection_windows", {
        "min_turnaround": {"value": 45, "unit": "duration_minutes", "relation": "min",
                           "condition": {"kind": "area", "value": "база"}, "quote": "45 минут"}})
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    before = copy.deepcopy(rule)
    out = derive_params_from_landing(rule)
    assert out[0]["qualifier"] == "база" and out[0]["value"] == 45
    assert rule == before
    rule["params"] = out
    assert m.reconcile_carriers(rule) == []
    rule["params"][0]["qualifier"] = "вне базы"
    with pytest.raises(ParamProjectionError):
        derive_params_from_landing(rule)


def test_typed_base_map_has_per_base_scope_not_a_global_scalar(tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, "reserve_floors", {
        "reserve_crews": {"value": {"ST1": 1, "ST2": 2}, "unit": "count",
                          "relation": "min", "condition": None, "quote": "ST1:1,ST2:2"}})
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    before = copy.deepcopy(rule)
    out = derive_params_from_landing(rule)
    assert [(p["qualifier"], p["value"]) for p in out] == [("ST1", 1), ("ST2", 2)]
    assert rule == before
    rule["params"] = out
    assert m.reconcile_carriers(rule) == []
    rule["params"][0]["qualifier"] = "ST3"
    with pytest.raises(ParamProjectionError):
        derive_params_from_landing(rule)


def test_typed_code_list_preserves_each_designation(tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, "fleet_exclusions", {
        "fleet_types": {"value": ["Airbus A320", "B737"], "unit": "code_list",
                        "relation": None, "condition": None, "quote": "Airbus A320, B737"}})
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    before = copy.deepcopy(rule)
    out = derive_params_from_landing(rule)
    assert out[0]["value"] == "Airbus A320, B737"
    assert rule == before


def test_typed_designations_reach_data_consumers_without_stringification(tmp_path, monkeypatch):
    from cdpm import data_scope
    labels = ["Airbus A320", "Ту-204", "Élan / 2", "Label, with comma"]
    doc, rule = typed_rule(tmp_path, monkeypatch, "fleet_exclusions", {
        "fleet_types": {"value": labels, "unit": "code_list", "relation": None,
                        "condition": None, "quote": "; ".join(labels)}})
    rule.pop("params", None)
    doc["rules"] = [rule]
    before = copy.deepcopy(doc)
    assert [x["code"] for x in m.parameter_codes(doc)] == labels
    data = {"tables": {"input": {"fleet": {"rows": [{"name": x} for x in labels]}}}}
    assert m.codes_absent_in_data(doc, data) == []
    data["tables"]["input"]["fleet"]["rows"].pop()
    assert [x["code"] for x in m.codes_absent_in_data(doc, data)] == [labels[-1]]
    carriers = data_scope._carriers(rule)
    assert carriers and all(values == set(labels) for _path, values in carriers), carriers
    assert doc == before


@pytest.mark.parametrize("value", [["A320", ""], ["A320", 1], ["A320", "A320"], []])
def test_scope_consumer_refuses_invalid_typed_lists(tmp_path, monkeypatch, value):
    from cdpm import data_scope
    _doc, rule = typed_rule(tmp_path, monkeypatch, "fleet_exclusions", {
        "fleet_types": {"value": value, "unit": "code_list", "relation": None,
                        "condition": None, "quote": "A320"}})
    rule.pop("params", None)
    assert any(values is None for _path, values in data_scope._carriers(rule))


def test_scope_does_not_turn_a_scalar_code_into_a_list():
    from cdpm import data_scope
    rule = {"id": "R-CODE", "params": [{"name": "fleet_type", "unit": "code", "value": ["A320"]}]}
    assert data_scope._carriers(rule) == [("params[0].value", None)]


@pytest.mark.parametrize("stale", [False, True])
def test_typed_list_extension_judges_actual_machine_values(tmp_path, monkeypatch, stale):
    from cdpm import data_scope
    _, old = typed_rule(tmp_path, monkeypatch, "fleet_exclusions", {
        "fleet_types": {"value": ["Airbus A320"], "unit": "code_list", "relation": None,
                        "condition": None, "quote": "Airbus A320"}})
    old.pop("params", None)
    old["statement"] = "Парк ∈ {Airbus A320}"
    evidence = {"rule": old["id"], "statement": old["statement"], "values": ["Ту-204"]}
    q = {"id": "Q-3a-данные-test", "rule_refs": [old["id"]], "data_scope": evidence,
         **data_scope.wording(evidence)}
    new = copy.deepcopy(old)
    new["statement"] = "Парк ∈ {Airbus A320, Ту-204}"
    if not stale:
        new["template_fit"]["params"]["fleet_types"]["value"].append("Ту-204")
    before = copy.deepcopy((q, old, new))
    issues = data_scope.extension_problems(q, {old["id"]: old}, {new["id"]: new})
    assert bool(issues) == stale, issues
    if stale:
        assert any("машинный перечень" in text for text in issues)
    assert (q, old, new) == before


@pytest.mark.parametrize("change", [{"value": "8"}, {"relation": "max"},
                                  {"unit": "штук"}, {"source": {"kind": "rule", "rule_id": "R-OTHER"}}])
def test_projection_refuses_invalid_typed_cells_before_normalizing_them(tmp_path, monkeypatch, change):
    _doc, rule = typed_rule(tmp_path, monkeypatch, "days_off_floor", {
        "min_days_off": {"value": 8, "unit": "count", "relation": "min",
                         "condition": None, "quote": "8"}})
    rule["template_fit"]["params"]["min_days_off"].update(change)
    before = copy.deepcopy(rule)
    with pytest.raises(ParamProjectionError):
        derive_params_from_landing(rule)
    assert rule == before


def test_conditions_use_their_own_catalog_relation_and_writer(tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, "duty_extension_penalty", {
        "duty_extension_cap": {"value": 960, "unit": "duration_minutes", "relation": "max", "condition": None, "quote": "960 минут"},
        "extension_bases": {"value": ["ST1", "ST2"], "unit": "code_list", "relation": "in", "condition": None, "quote": "ST1, ST2"},
        "extension_consent": {"value": True, "unit": "flag", "relation": "yes", "condition": None, "quote": "с согласия"}})
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    replacement = copy.deepcopy(rule["template_fit"]["params"]["extension_bases"])
    replacement.update(value=["ST1"], quote="ST1")
    assert fp.write_cell(rule, "extension_bases", replacement) is None
    before = copy.deepcopy(rule)
    assert {p["name"] for p in derive_params_from_landing(rule)} == {"duty_extension_cap"}
    assert rule == before  # condition cells remain in the canonical landing


@pytest.mark.parametrize("condition", [None, {"kind": "area", "value": "база"},
                                      {"kind": "base_code", "value": "ST2"}])
def test_base_scalar_requires_its_exact_typed_scope(tmp_path, monkeypatch, condition):
    doc, rule = typed_rule(tmp_path, monkeypatch, "reserve_floors", {
        "reserve_crews": {"value": 2, "unit": "count", "relation": "min",
                          "condition": {"kind": "base_code", "value": "ST1"}, "quote": "2"}})
    assert m.validate_template_fit(doc) == []
    out = derive_params_from_landing(rule)
    assert out[0]["qualifier"] == "ST1"
    rule["params"] = out
    rule["template_fit"]["params"]["reserve_crews"]["condition"] = condition
    before = copy.deepcopy(rule)
    with pytest.raises(ParamProjectionError):
        derive_params_from_landing(rule)
    assert rule == before


@pytest.mark.parametrize("condition", [{"kind": "base_code", "value": "ST2"},
                                      {"kind": "area", "value": "база"}])
def test_map_rejects_condition_that_changes_its_key_scope(tmp_path, monkeypatch, condition):
    doc, rule = typed_rule(tmp_path, monkeypatch, "reserve_floors", {
        "reserve_crews": {"value": {"ST1": 2}, "unit": "count", "relation": "min",
                          "condition": {"kind": "base_code", "value": "ST1"}, "quote": "ST1:2"}})
    assert m.validate_template_fit(doc) == []
    assert derive_params_from_landing(rule)[0]["qualifier"] == "ST1"
    rule["template_fit"]["params"]["reserve_crews"]["condition"] = condition
    with pytest.raises(ParamProjectionError):
        derive_params_from_landing(rule)


def test_legacy_projection_must_not_split_an_opaque_designation(tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, "fleet_exclusions", {
        "fleet_types": {"value": ["Airbus A320, special", "B737"], "unit": "code_list",
                        "relation": None, "condition": None, "quote": "Airbus A320, special; B737"}})
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    before = copy.deepcopy(rule)
    with pytest.raises(ParamProjectionError, match="обозначен"):
        derive_params_from_landing(rule)
    assert rule == before
