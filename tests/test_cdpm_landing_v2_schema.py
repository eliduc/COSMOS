"""Explicit landing/2 shape, not writer/migration/executor acceptance.

The current legacy shape stays a distinct branch. No catalog release,
runtime service, model call or file migration is performed here.
"""
import copy

import pytest

from cdpm import family_contract, model as m


def document():
    fit = {"schema_version": "landing/2", "category": "A",
           "family": "rest_floors", "registry_rev": m.registry_fingerprint(),
           "family_contract": family_contract.snapshot("rest_floors", ["min_rest_home"]),
           "params": {"min_rest_home": {
               "value": 720, "unit": "duration_minutes", "relation": "min",
               "condition": None, "quote": "12 часов",
               "source": {"kind": "rule", "rule_id": "R-REST"}}}}
    return {"meta": {"customer": "Тест", "mode": 1, "cdpm_version": "0.1"},
            "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
            "rules": [{"id": "R-REST", "title": "Отдых",
                       "statement": "дома не менее 12 часов", "kind": "HARD",
                       "zone": "BOTH", "sources": [{"sid": "S1", "anchor": "п.1"}],
                       "template_fit": fit}], "questions": [], "objectives": []}


def healthy():
    doc = document()
    assert m.validate_schema(doc) == []
    fit = doc["rules"][0]["template_fit"]
    return doc, fit, fit["params"]["min_rest_home"]


def test_typed_shape_is_read_without_mutation():
    doc = document()
    before = copy.deepcopy(doc)
    assert m.validate_schema(doc) == []
    assert doc == before


@pytest.mark.parametrize("field", ["value", "unit", "relation", "condition", "quote", "source"])
def test_each_typed_cell_field_is_required(field):
    doc, _fit, cell = healthy()
    del cell[field]
    problems = m.validate_schema(doc)
    assert problems and any(field in p for p in problems)


@pytest.mark.parametrize("value", ["landing/3", "", None, 2])
def test_unknown_version_is_not_a_legacy_fallback(value):
    doc, fit, _cell = healthy()
    fit["schema_version"] = value
    assert any("schema_version" in p for p in m.validate_schema(doc))


def test_typed_machine_landing_requires_the_origin_contract():
    doc, fit, _cell = healthy()
    del fit["family_contract"]
    assert any("family_contract" in p for p in m.validate_schema(doc))


@pytest.mark.parametrize("condition", ["база", {}, {"kind": "area"},
                                        {"value": "база"},
                                        {"kind": "area", "value": "база", "extra": 1}])
def test_typed_condition_is_structured_or_null(condition):
    doc, _fit, cell = healthy()
    cell["condition"] = condition
    assert any("condition" in p for p in m.validate_schema(doc))


def test_structured_condition_and_nullable_relation_have_shapes():
    # Catalog-specific kind/unit/relation equality belongs to the semantic
    # validator, not a duplicated vocabulary in the JSON schema.
    doc, _fit, cell = healthy()
    cell.update(condition={"kind": "area", "value": "база"}, relation=None)
    assert m.validate_schema(doc) == []


@pytest.mark.parametrize("value,valid", [(["A320", "B737"], True), ([], False),
    (["A320", "A320"], False), (["A320", 7], False), ([""], False)])
def test_code_list_shape_is_a_nonempty_unique_string_array(value, valid):
    doc, _fit, cell = healthy()
    cell.update(value=value, unit="code_list", relation=None)
    assert (not m.validate_schema(doc)) is valid


def test_time_of_day_needs_timezone():
    doc, _fit, cell = healthy()
    cell.update(value=720, unit="time_of_day_minutes")
    assert any("tz" in p for p in m.validate_schema(doc))
    cell["tz"] = "UTC"
    assert m.validate_schema(doc) == []
    cell["unit"] = "duration_minutes"
    assert m.validate_schema(doc)


@pytest.mark.parametrize("source", [{"kind": "rule"}, {"kind": "code"},
    {"kind": "answer"}, {"kind": "answer", "question": {"id": "Q", "fingerprint": "f"},
     "version": {"fingerprint": "v"}, "effect": {"rule": "R-REST", "field": "x"}, "root": "Q"}])
def test_typed_source_requires_its_kind_specific_identity(source):
    doc, _fit, cell = healthy()
    cell["source"] = source
    assert any("source" in p for p in m.validate_schema(doc))


@pytest.mark.parametrize("version", [None, "landing/1"])
def test_legacy_shape_keeps_its_old_rules(version):
    doc = document()
    fit = doc["rules"][0]["template_fit"]
    if version is None:
        fit.pop("schema_version")
    else:
        fit["schema_version"] = version
    fit.pop("family_contract")
    fit["params"] = {"min_rest_home": {"value": 720, "quote": "12 часов"}}
    assert m.validate_schema(doc) == []
    cell = fit["params"]["min_rest_home"]
    for change in ({"condition": None}, {"condition": {"kind": "area", "value": "база"}},
                   {"relation": None}, {"value": ["A320"]}):
        candidate = copy.deepcopy(doc)
        candidate["rules"][0]["template_fit"]["params"]["min_rest_home"].update(change)
        assert m.validate_schema(candidate), change
    assert cell == {"value": 720, "quote": "12 часов"}
