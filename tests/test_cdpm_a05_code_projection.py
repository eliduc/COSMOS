"""A05: nonnumeric designations must survive current-format projection.

The legacy code contract already represents exact labels with relation=eq
(test_cdpm_code_params). Catalog null means no numeric bound, not an empty
legacy relation; explicit carrier relations must still be preserved/compared.
"""
import copy
import json

import pytest

from cdpm import model as m, family_params as fp, export_task_package as tp
from cdpm.gates import gate_task_package as gate
from vmp import execution_manifest as execution, formulation_check as fc
from test_cdpm_task_package import _ready_doc, _run_dir


def rule(name="fleet_types", value="Airbus A320, ATR 72-600", **extra):
    quote = "Типы ВС: Airbus A320, ATR 72-600."
    return {"id": "R-CODES", "title": "Типы ВС", "statement": quote,
            "kind": "HARD", "zone": "BOTH",
            "sources": [{"sid": "S1", "anchor": "п.1"}],
            "template_fit": {"category": "A", "family": "fleet_exclusions",
                "registry_rev": m.registry_fingerprint(),
                "params": {name: {"value": value, "quote": quote, **extra}}}}


@pytest.mark.parametrize("name,value,expected", [
    ("fleet_type", "Airbus A320", "Airbus A320"),
    ("fleet_type", ["Airbus A320"], "Airbus A320"),
    ("fleet_types", "Airbus A320, ATR 72-600", "ATR 72-600, Airbus A320"),
    ("fleet_types", ["Airbus A320", "ATR 72-600"], "ATR 72-600, Airbus A320"),
])
@pytest.mark.parametrize("previous", [False, True])
def test_code_projection_preserves_complete_labels_and_trace(name, value, expected, previous):
    r = rule(name, value)
    spec = fp._param_spec("fleet_exclusions", name)
    assert spec["relation"] is None and spec["unit"] in ("code", "code_list")
    if previous:
        r["params"] = [{"name": name, "value": expected, "unit": spec["unit"],
                        "as_written": r["statement"], "relation": "eq"}]
    before = copy.deepcopy(r)
    p, = tp.derive_params_from_landing(r)
    assert p == {"name": name, "value": expected, "unit": spec["unit"],
                 "relation": "eq", "as_written": r["statement"]}
    assert m.validate_param(p, "HARD") == []
    candidate = copy.deepcopy(r)
    candidate["params"] = [p]
    assert m.reconcile_carriers(candidate) == []
    assert tp.derive_params_from_landing(candidate) == [p]
    assert r == before


@pytest.mark.parametrize("relation", ["eq", "min", "max", "window"])
@pytest.mark.parametrize("source", ["legacy", "cell", "both"])
def test_explicit_code_relation_is_not_replaced(relation, source):
    r = rule("fleet_type", "Airbus A320")
    if source in ("legacy", "both"):
        r["params"] = [{"name": "fleet_type", "value": "Airbus A320", "unit": "code",
                        "as_written": "Airbus A320", "relation": relation}]
    if source in ("cell", "both"):
        r["template_fit"]["params"]["fleet_type"]["relation"] = relation
    before = copy.deepcopy(r)
    p, = tp.derive_params_from_landing(r)
    assert p["relation"] == relation
    assert r == before


@pytest.mark.parametrize("changes", [
    {"value": {"Airbus A320": 1}}, {"value": []}, {"value": ["Airbus A320", ""]},
    {"value": ["Airbus A320", True]}, {"value": "Airbus A320,,"},
    {"value": 320}, {"unit": "count"}, {"condition": {"base": "SVO"}},
    {"quote": "не содержит обозначений"},
])
def test_malformed_or_unrepresentable_code_projection_refuses(changes):
    r = rule(**changes)
    before = copy.deepcopy(r)
    with pytest.raises(tp.TaskPackageError) as error:
        tp.derive_params_from_landing(r)
    assert error.value.items == ["R-CODES/fleet_types"]
    assert r == before


@pytest.mark.parametrize("difference", ["value", "relation", "trace"])
def test_legacy_disagreement_or_missing_trace_cannot_be_hidden(difference):
    r = rule("fleet_type", "Airbus A320", relation="eq")
    p = {"name": "fleet_type", "value": "Airbus A320", "unit": "code",
         "relation": "eq", "as_written": "Airbus A320"}
    p[difference if difference != "trace" else "as_written"] = {
        "value": "ATR 72-600", "relation": "min", "trace": "не тот источник"}[difference]
    r["params"] = [p]
    before = copy.deepcopy(r)
    with pytest.raises(tp.TaskPackageError):
        tp.derive_params_from_landing(r)
    assert r == before


def test_missing_numeric_relation_is_not_invented(monkeypatch):
    from test_cdpm_a05_threshold_landing import _floor
    original = fp._param_spec
    def without_relation(family, name):
        return {**original(family, name), "relation": None}
    monkeypatch.setattr(fp, "_param_spec", without_relation)
    with pytest.raises(tp.TaskPackageError, match="relation"):
        tp.derive_params_from_landing(_floor())


@pytest.mark.parametrize("structured", [False, True])
def test_real_package_keeps_codes_and_reaches_existing_consumer(tmp_path, structured):
    doc = _ready_doc()
    value = ["Airbus A320", "ATR 72-600"] if structured else "Airbus A320, ATR 72-600"
    doc["rules"].append(rule(value=value))
    run = _run_dir(tmp_path, doc)
    original = (run / "extract/model.json").read_bytes()
    files = tp.build_package(run)
    emitted_doc = json.loads(files["model.json"])
    emitted = next(r for r in emitted_doc["rules"] if r["id"] == "R-CODES")
    p, = emitted["params"]
    assert p["value"] == "ATR 72-600, Airbus A320" and p["relation"] == "eq"
    assert m.validate_schema(emitted_doc) == [] and m.validate_model(emitted_doc) == []
    assert (run / "extract/model.json").read_bytes() == original
    actual = execution.rule_params(emitted)[0]
    assert actual["value"] == p["value"] and actual["relation"] == "eq"
    facts = {"present": True, "aircraft_types": {"Airbus A320", "ATR 72-600"},
             "has_type_column": True, "airports": set(), "bases": set()}
    assert fc.check_codes({}, {"rules": [emitted]}, facts) == []
    facts["aircraft_types"].remove("ATR 72-600")
    hits = fc.check_codes({}, {"rules": [emitted]}, facts)
    assert len(hits) == 1 and hits[0]["codes"] == ["ATR 72-600"] and hits[0]["level"] == fc.STOP
    package = tmp_path / "codes.zip"
    package.write_bytes(tp.write_zip(files))
    assert gate.check(package)[0] == []


@pytest.mark.parametrize("bad", [False, 0, [], {}, True, "bogus"])
@pytest.mark.parametrize("source", ["legacy", "cell"])
def test_invalid_explicit_relation_is_not_defaulted_to_eq(bad, source):
    r = rule("fleet_type", "Airbus A320")
    if source == "cell":
        r["template_fit"]["params"]["fleet_type"]["relation"] = bad
    else:
        r["params"] = [{"name": "fleet_type", "value": "Airbus A320", "unit": "code",
                        "relation": bad, "as_written": "Airbus A320"}]
    with pytest.raises(tp.TaskPackageError):
        tp.derive_params_from_landing(r)


@pytest.mark.parametrize("unit", ["code", "code_list", "count", "credit_hours"])
@pytest.mark.parametrize("value", ["Airbus A320", " ", 0, 1.5, -1, True, None, []])
def test_schema_enforces_value_type_by_unit(unit, value):
    doc = _ready_doc()
    r = rule("fleet_type", "Airbus A320")
    r.pop("template_fit")
    r["params"] = [{"name": "fleet_type", "unit": unit, "value": value,
                    "relation": "eq", "as_written": "Airbus A320"}]
    doc["rules"].append(r)
    code = unit in ("code", "code_list")
    valid = isinstance(value, str) and bool(value.strip()) if code else (
        type(value) in (int, float) and value >= 0 and
        (unit != "count" or type(value) is int))
    assert bool(m.validate_schema(doc)) is not valid
