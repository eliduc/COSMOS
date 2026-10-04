"""Current-format A05 package projection: source stays intact, disagreement refuses.

Only synthetic dictionaries/temp packages; package tests use the existing
deterministic translator stub plus local SMT, not live LLM/JVM or a stand.
"""
import copy
import json

import pytest

from cdpm import answers, family_params as fp, export_task_package as tp, model as m
from cdpm.gates import gate_task_package as gate
from test_cdpm_a05_threshold_landing import _floor, PARAM
from test_cdpm_task_package import _ready_doc, _run_dir


@pytest.mark.parametrize("value,unit", [(600, None), (10, "ч"), ("600", "duration_minutes")])
def test_projection_uses_family_units_and_does_not_mutate_source(value, unit):
    rule = _floor()
    rule["template_fit"]["params"][PARAM].update(value=value)
    if unit:
        rule["template_fit"]["params"][PARAM]["unit"] = unit
    before = copy.deepcopy(rule)
    projected = tp.derive_params_from_landing(rule)
    p = next(p for p in projected if p["name"] == PARAM)
    assert p == {"name": PARAM, "value": 600, "unit": "duration_minutes",
                 "relation": "min", "as_written": "10 часов"}
    assert m.validate_param(p, "HARD") == []
    assert rule == before
    projected[0]["value"] = 999
    assert rule == before


def test_projection_keeps_original_conflict_visible_and_source_intact():
    rule = _floor()
    rule["params"] = [{"name": PARAM, "value": 900, "unit": "duration_minutes",
                       "relation": "min", "as_written": "15 часов"}]
    before = copy.deepcopy(rule)
    with pytest.raises(tp.TaskPackageError) as error:
        tp.derive_params_from_landing(rule)
    assert error.value.code == tp.PARAM_CARRIERS_DIVERGE
    assert error.value.items == [f"R-TZ/{PARAM}"]
    assert rule == before


def test_projection_preserves_qualifier_multiplicity_and_unlanded_parameters():
    rule = _floor()
    old = [{"name": PARAM, "value": 600, "unit": "duration_minutes",
            "relation": "min", "as_written": "10 часов", "qualifier": q}
           for q in ("A", "B")]
    other = {"name": "max_pairing_days", "value": 3, "unit": "count",
             "relation": "max", "as_written": "3 суток"}
    rule["params"] = old + [other]
    before = copy.deepcopy(rule)
    projected = tp.derive_params_from_landing(rule)
    assert [p for p in projected if p["name"] == PARAM] == old
    assert other in projected
    assert rule == before


@pytest.mark.parametrize("condition", ["вне базы", "только для базы SVO"])
def test_projection_carries_string_condition_without_broadening(condition):
    rule = _floor()
    rule["template_fit"]["params"][PARAM]["condition"] = condition
    projected = tp.derive_params_from_landing(rule)
    p = next(p for p in projected if p["name"] == PARAM)
    assert p["qualifier"] == condition
    candidate = copy.deepcopy(rule)
    candidate["params"] = projected
    assert m.reconcile_carriers(candidate) == []


@pytest.mark.parametrize("change", [{"unit": "парсек"}, {"value": True},
                                    {"condition": {"kind": "area", "value": "вне базы"}}])
def test_unrepresentable_projection_refuses_with_address(change):
    rule = _floor()
    rule["template_fit"]["params"][PARAM].update(change)
    before = copy.deepcopy(rule)
    with pytest.raises(tp.TaskPackageError) as error:
        tp.derive_params_from_landing(rule)
    assert error.value.items == [f"R-TZ/{PARAM}"]
    assert rule == before


def test_non_landed_rule_is_not_migrated_or_mutated():
    rule = {"id": "R-OLD", "params": [{"name": "max_pairing_days", "value": 3,
            "unit": "count", "relation": "max", "as_written": "3 суток"}]}
    before = copy.deepcopy(rule)
    assert tp.derive_params_from_landing(rule) == rule["params"]
    assert rule == before


@pytest.mark.parametrize("trace", [None, ""])
def test_missing_legacy_trace_is_derived_only_from_the_agreeing_cell(trace):
    rule = _floor()
    rule["params"] = [{"name": PARAM, "value": 600, "as_written": trace}]
    rule["template_fit"]["params"][PARAM]["quote"] = "Минимальный отдых — 10 часов."
    before = copy.deepcopy(rule)
    p, = [p for p in tp.derive_params_from_landing(rule) if p["name"] == PARAM]
    assert p["as_written"] == "10 часов"
    assert m.validate_param(p, "HARD") == []
    assert rule == before


def test_existing_incorrect_legacy_trace_is_not_replaced_by_a_convenient_quote():
    rule = _floor()
    rule["params"] = [{"name": PARAM, "value": 600, "unit": "duration_minutes",
                       "relation": "min", "as_written": "15 часов"}]
    before = copy.deepcopy(rule)
    with pytest.raises(tp.TaskPackageError):
        tp.derive_params_from_landing(rule)
    assert rule == before


def test_real_package_projects_landing_and_keeps_canonical_input(tmp_path):
    doc = _ready_doc()
    rule = _floor()
    doc["rules"].append(rule)
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []
    run = _run_dir(tmp_path, doc)
    original = (run / "extract/model.json").read_bytes()
    files = tp.build_package(run)
    packaged = json.loads(files["model.json"])
    emitted = next(r for r in packaged["rules"] if r["id"] == "R-TZ")
    p = next(p for p in emitted.get("params", []) if p["name"] == PARAM)
    assert p["value"] == 600 and p["unit"] == "duration_minutes"
    assert m.validate_schema(packaged) == [] and m.validate_model(packaged) == []
    path = tmp_path / "package.zip"
    path.write_bytes(tp.write_zip(files))
    assert gate.check(path)[0] == []
    assert tp.build_package(run) == files
    assert (run / "extract/model.json").read_bytes() == original


@pytest.mark.parametrize("raw,value,trace", [("Минимальный отдых — 10 часов.", 600, "10 часов"),
                                           ("10,5 часов", 630, "10,5 часов"),
                                           ("0,1 часа", 6, "0,1 часа")])
def test_answer_quote_projects_exact_numeric_fragment_without_losing_full_quote(tmp_path, raw, value, trace):
    doc = _ready_doc()
    rule = _floor()
    del rule["template_fit"]["params"][PARAM]
    doc["rules"].append(rule)
    qid = fp.ensure_family_param_questions(doc)[0]
    assert answers.apply_answers(doc, {qid: raw})["applied"] == 1
    quote = rule["template_fit"]["params"][PARAM]["quote"]
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []
    files = tp.build_package(_run_dir(tmp_path, doc))
    emitted = next(r for r in json.loads(files["model.json"])["rules"] if r["id"] == "R-TZ")
    p = next(p for p in emitted["params"] if p["name"] == PARAM)
    assert p["value"] == value
    assert p["as_written"] == trace and trace in quote
    assert emitted["template_fit"]["params"][PARAM]["quote"] == quote
    assert m.validate_param(p, "HARD") == []
    path = tmp_path / "answered.zip"
    path.write_bytes(tp.write_zip(files))
    assert gate.check(path)[0] == []


@pytest.mark.parametrize("quote", ["Минимальный отдых 15 часов", "Отдых 10 часов и 12 часов"])
def test_projection_does_not_pick_a_convenient_fragment_of_bad_quote(quote):
    rule = _floor()
    rule["template_fit"]["params"][PARAM]["quote"] = quote
    before = copy.deepcopy(rule)
    with pytest.raises(tp.TaskPackageError):
        tp.derive_params_from_landing(rule)
    assert rule == before


def test_new_reserve_unscoped_scalar_cannot_become_global_count():
    rule = {"id": "R-RES", "kind": "HARD", "template_fit": {
        "category": "A", "family": "reserve_floors", "params": {
            "reserve_crews": {"value": 2, "quote": "2 экипажа по базам"}}}}
    before = copy.deepcopy(rule)
    with pytest.raises(tp.TaskPackageError) as error:
        tp.derive_params_from_landing(rule)
    assert error.value.items == ["R-RES/reserve_crews"]
    assert rule == before


@pytest.mark.parametrize("raw,value", [("10,5 часов", 630), ("0,1 часа", 6), ("-0 ч 30 мин", -30)])
def test_legacy_literal_reader_is_exact_and_never_reads_prose(raw, value):
    assert m.canon_minutes(raw) == value
    assert m.canon_minutes("Предел " + raw) is None
    if value >= 0:
        p = {"name": PARAM, "value": value, "unit": "duration_minutes",
             "relation": "min", "as_written": raw}
        assert m.validate_param(p, "HARD") == []
        p["value"] += 1
        assert any("не сходится" in x for x in m.validate_param(p, "HARD"))
