"""K2 subject classification, not a stand/SMT execution or package acceptance.

Uses real typed stand construction and the existing isolated vocabulary fixture.
No translator, Z3, HTTP, JVM, service, or verdict mocks are invoked here.
The catalogue's proven world_param nature is distinct from descriptive HARD.
"""
import copy
import json
import shutil

import pytest

from cdpm import catalog_bridge as cb, export_task_package as tp, model as m, smt_verify
import template_catalog as tc
from template_catalog import family_contract, landing, semantics
from template_catalog.reader import SECTION_FILES
from vmp import stand_solver as ss
from test_stand_landing_v2_contract_agent import catalog, rule_of, world_with


def _document(catalog, offset=0, *, include_duty=False):
    values = {"base_tz_offset_min": offset}
    families = ["time_scale"]
    if include_duty:
        values["max_duty_duration"] = 720
        families.append("duty_limits")
    world = world_with(values)
    rules = [rule_of(catalog, family, world) for family in families]
    original = copy.deepcopy((world, rules))
    doc = ss.model_doc(rules, world=world, catalog=catalog)
    assert (world, rules) == original
    assert m.validate_schema(doc) == []
    assert m.validate_template_fit(doc) == []
    assert all(landing.problems(row, catalog) == [] for row in doc["rules"])
    return doc


def _subject_entry(rule):
    return {"id": str(rule.get("id")), "kind": "HARD",
            "statement": rule["statement"].strip()}


@pytest.mark.parametrize("offset,scale", [(0, "base-local"), (180, "shifted")])
def test_proven_world_parameter_keeps_its_carrier_but_is_not_a_constraint(
        catalog, offset, scale):
    doc = _document(catalog, offset, include_duty=True)
    world_rule, duty = doc["rules"]
    before = copy.deepcopy(doc)
    fit = world_rule["template_fit"]
    pin = fit["family_contract"]
    reference = family_contract.read(pin, catalog, family="time_scale",
                                     registry_rev=fit["registry_rev"])
    assert reference["verdict"] == semantics.COMPATIBLE
    assert reference["issues"] == []
    assert reference["origin"] == pin
    origin = semantics.resolve_release(catalog,
        fingerprint=pin["catalog_fingerprint"], version=pin["catalog_version"])
    assert origin.family(pin["family_id"])["nature"] == "world_param"
    assert world_rule["kind"] == "HARD"
    assert fit["params"]["base_tz_offset_min"]["value"] == offset
    assert tp.time_declaration_of(doc, None) == {
        "time_scale": scale, "base_tz_offset_min": offset,
        "source_rule": "R-STAND-time_scale"}
    assert duty["kind"] == "HARD"
    assert duty["template_fit"]["params"]["max_duty_duration"]["value"] == 720
    assert smt_verify.subject_of(doc) == [_subject_entry(duty)]
    assert doc == before, "classification is read-only; the world carrier survives"


@pytest.mark.parametrize("damage", [
    "missing-pin", "null-pin", "empty-pin", "missing-record-fingerprint",
    "wrong-record-fingerprint", "foreign-family-pin", "foreign-catalog-pin",
    "registry-mismatch", "unknown-schema", "unknown-category", "wrong-unit",
    "boolean-value", "empty-params", "missing-params", "untyped-cell",
    "missing-family", "missing-revision",
])
def test_unproven_world_parameter_must_not_disappear_from_subject(catalog, damage):
    doc = _document(catalog, include_duty=True)
    row, duty = doc["rules"]
    fit = row["template_fit"]
    pin = fit["family_contract"]
    if damage == "missing-pin":
        del fit["family_contract"]
    elif damage == "null-pin":
        fit["family_contract"] = None
    elif damage == "empty-pin":
        fit["family_contract"] = {}
    elif damage == "missing-record-fingerprint":
        del pin["record_fingerprint"]
    elif damage == "wrong-record-fingerprint":
        pin["record_fingerprint"] = "0" * 64
    elif damage == "foreign-family-pin":
        fit["family_contract"] = copy.deepcopy(duty["template_fit"]["family_contract"])
    elif damage == "foreign-catalog-pin":
        pin["catalog_fingerprint"] = fit["registry_rev"] = "0" * 64
    elif damage == "registry-mismatch":
        fit["registry_rev"] = "0" * 64
    elif damage == "unknown-schema":
        fit["schema_version"] = "landing/999"
    elif damage == "unknown-category":
        fit["category"] = "UNKNOWN"
    elif damage == "wrong-unit":
        fit["params"]["base_tz_offset_min"]["unit"] = "count"
    elif damage == "boolean-value":
        fit["params"]["base_tz_offset_min"]["value"] = False
    elif damage == "empty-params":
        fit["params"] = {}
    elif damage == "missing-params":
        del fit["params"]
    elif damage == "untyped-cell":
        fit["params"]["base_tz_offset_min"] = 0
    elif damage == "missing-family":
        del fit["family"]
    elif damage == "missing-revision":
        del fit["registry_rev"]
    else:
        raise AssertionError(damage)
    before = copy.deepcopy(doc)
    # Malformed input is not claimed admissible: it must retain the obligation
    # for downstream validators, never gain the world-parameter exemption.
    assert smt_verify.subject_of(doc) == [_subject_entry(duty), _subject_entry(row)]
    assert doc == before


@pytest.mark.parametrize("schema", [None, "landing/1"])
def test_legacy_family_name_alone_does_not_exempt_hard_rule(catalog, schema):
    doc = _document(catalog)
    row = doc["rules"][0]
    fit = row["template_fit"]
    del fit["family_contract"]
    if schema is None:
        del fit["schema_version"]
    else:
        fit["schema_version"] = schema
    before = copy.deepcopy(doc)
    assert smt_verify.subject_of(doc) == [_subject_entry(row)]
    assert doc == before


def test_rule_nature_claim_cannot_hide_an_ordinary_hard_constraint(catalog):
    doc = _document(catalog, include_duty=True)
    duty = doc["rules"][1]
    duty["nature"] = "world_param"
    doc["rules"] = [duty]
    before = copy.deepcopy(doc)
    assert catalog.family("duty_limits")["nature"] != "world_param"
    assert smt_verify.subject_of(doc) == [_subject_entry(duty)]
    assert doc == before


@pytest.mark.parametrize("identity", ["missing", "empty", "null"])
def test_nameless_hard_world_carrier_is_not_hidden(catalog, identity):
    doc = _document(catalog)
    row = doc["rules"][0]
    if identity == "missing":
        del row["id"]
    else:
        row["id"] = "" if identity == "empty" else None
    before = copy.deepcopy(doc)
    assert smt_verify.subject_of(doc) == [_subject_entry(row)]
    assert smt_verify.nameless_hard_positions(doc) == [0]
    assert doc == before


@pytest.mark.parametrize("pending", ["Q-world-R1", None])
def test_pending_cell_is_not_proven_even_when_the_value_is_present(catalog, pending):
    doc = _document(catalog)
    row = doc["rules"][0]
    row["template_fit"]["params"]["base_tz_offset_min"]["pending_revision"] = pending
    before = copy.deepcopy(doc)
    assert smt_verify.subject_of(doc) == [_subject_entry(row)]
    assert doc == before


@pytest.mark.parametrize("fit", [None, [], "time_scale"])
def test_non_object_fit_cannot_claim_an_exemption(catalog, fit):
    doc = _document(catalog)
    row = doc["rules"][0]
    row["template_fit"] = fit
    row["nature"] = "world_param"
    before = copy.deepcopy(doc)
    assert smt_verify.subject_of(doc) == [_subject_entry(row)]
    assert doc == before


def test_unavailable_catalog_retains_the_hard_obligation(catalog, tmp_path, monkeypatch):
    doc = _document(catalog)
    row = doc["rules"][0]
    before = copy.deepcopy(doc)
    missing = tmp_path / "catalogue-not-present"
    assert not missing.exists()
    monkeypatch.setenv(cb.ENV_CATALOG, str(missing))
    cb.forget()
    with pytest.raises(cb.CatalogRequired):
        cb.catalog()
    assert smt_verify.subject_of(doc) == [_subject_entry(row)]
    assert doc == before


def _synthetic_nature(folder, nature, version):
    """Edit only pytest's private catalogue body, never publish/approve it."""
    path = folder / "families.json"
    families = json.loads(path.read_text(encoding="utf-8"))
    record = next(row for row in families["families"] if row["id"] == "time_scale")
    record["nature"] = nature
    record["nature_confirmed_by"] = "certifier_registry" if nature == "world_param" else "none"
    path.write_text(json.dumps(families, ensure_ascii=False), encoding="utf-8")
    header_path = folder / "catalog.json"
    header = json.loads(header_path.read_text(encoding="utf-8"))
    header["version"] = version
    header["fingerprint"] = tc.load(folder).fingerprint()
    header_path.write_text(json.dumps(header, ensure_ascii=False), encoding="utf-8")
    cb.forget()
    return cb.catalog()


@pytest.mark.parametrize("origin_nature,current_nature", [
    ("world_param", "schedule"), ("schedule", "world_param"),
])
def test_resolved_origin_nature_not_current_label_controls_subject(
        catalog, origin_nature, current_nature):
    # Counterfactual catalogues isolate classification, not a real migration or
    # approval. Both bodies are read and validated by the real catalogue code.
    original = _synthetic_nature(catalog.path, origin_nature, catalog.version())
    doc = _document(original)
    row = doc["rules"][0]
    before = copy.deepcopy(doc)
    archived = original.path / "releases" / original.version()
    archived.mkdir(parents=True)
    for filename in SECTION_FILES:
        shutil.copyfile(original.path / filename, archived / filename)
    current = _synthetic_nature(original.path, current_nature, "0.7.1")
    fit = row["template_fit"]
    pin = fit["family_contract"]
    reference = family_contract.read(pin, current, family=fit["family"],
                                     registry_rev=fit["registry_rev"])
    assert reference["verdict"] == semantics.COMPATIBLE
    assert reference["issues"] == []
    resolved = semantics.resolve_release(current, fingerprint=pin["catalog_fingerprint"],
                                         version=pin["catalog_version"])
    assert resolved.family("time_scale")["nature"] == origin_nature
    assert current.family("time_scale")["nature"] == current_nature
    assert landing.problems(row, current) == []
    expected = [] if origin_nature == "world_param" else [_subject_entry(row)]
    assert smt_verify.subject_of(doc) == expected
    assert doc == before
