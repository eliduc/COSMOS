"""A05 base-map projection: preserve each base, reject lossy or malformed input.

Synthetic rules and temporary packages only; shared deterministic SMT stub.
"""
import copy
import json
from decimal import Decimal

import pytest

from cdpm import family_params as fp, model as m, export_task_package as tp
from cdpm.gates import gate_task_package as gate
from test_cdpm_task_package import _ready_doc, _run_dir

NAME = "reserve_crews"
QUOTE = "Ежедневно резерв: PLY ≥ 2 экипажа, SVG ≥ 1, GRT ≥ 0."
VALUES = {"PLY": 2, "SVG": 1, "GRT": 0}


def _record(base, value, **extra):
    return {"name": NAME, "value": value, "unit": "count", "relation": "min",
            "qualifier": base, "as_written": QUOTE, **extra}


def _rule(value=None, legacy=None, **cell_changes):
    cell = {"value": copy.deepcopy(VALUES if value is None else value),
            "quote": QUOTE, **cell_changes}
    rule = {"id": "R-RES", "title": "Резерв на базах", "statement": QUOTE,
            "zone": "ROSTERING", "kind": "HARD",
            "sources": [{"sid": "S1", "anchor": "п.1"}],
            "template_fit": {"category": "A", "family": "reserve_floors",
                             "registry_rev": m.registry_fingerprint(),
                             "params": {NAME: cell}}}
    if legacy is not None:
        rule["params"] = copy.deepcopy(legacy)
    return rule


@pytest.mark.parametrize("legacy", [
    None,
    [_record("PLY", 2)],
    [_record("SVG", 1), _record("GRT", 0), _record("PLY", 2)],
    [{"name": NAME, "value": VALUES, "unit": "count",
      "relation": "min", "as_written": QUOTE}],
])
def test_every_base_is_projected_once_without_mutating_source(legacy):
    rule = _rule(legacy=legacy)
    before = copy.deepcopy(rule)
    got = tp.derive_params_from_landing(rule)
    assert {(p["qualifier"], p["value"]) for p in got} == {
        ("PLY", 2), ("SVG", 1), ("GRT", 0)}
    assert len(got) == 3
    assert all(m.validate_param(p, "HARD") == [] for p in got)
    assert all(p["as_written"] == QUOTE for p in got)
    assert m.reconcile_carriers(dict(rule, params=got)) == []
    assert rule == before
    got[0]["value"] = 999
    assert rule == before


def test_partial_old_records_preserve_their_trace_and_unrelated_parameters():
    old = _record("PLY", 2, as_written="PLY ≥ 2 экипажа")
    other = {"name": "crew_size", "value": 2, "unit": "count",
             "relation": "eq", "as_written": "2"}
    rule = _rule(legacy=[old, other])
    got = tp.derive_params_from_landing(rule)
    assert old in got and other in got
    assert {(p["qualifier"], p["value"]) for p in got if p["name"] == NAME} == {
        ("PLY", 2), ("SVG", 1), ("GRT", 0)}


@pytest.mark.parametrize("raw", [
    {}, {"PLY": True}, {"PLY": "2"}, {"PLY": 1.5},
    {"PLY": float("inf")}, {"PLY": float("nan")},
    {"PLY": Decimal("sNaN")}, {"": 2},
    {"PLY": 2, " PLY ": 2}, {"PLY": 1, " PLY ": 2},
])
def test_base_map_rejects_invalid_shapes_and_trim_collisions(raw):
    assert fp.base_map(raw) is None


def test_base_map_keeps_distinct_codes_and_exact_whole_numbers():
    assert fp.base_map({" PLY ": 2.0, "ply": Decimal("3"), "PLУ": 4}) == {
        "PLY": Decimal(2), "ply": Decimal(3), "PLУ": Decimal(4)}


def test_collision_cannot_hide_a_disagreeing_carrier():
    rule = _rule(value={"PLY": 1, " PLY ": 2}, legacy=[_record("PLY", 2)])
    assert m.reconcile_carriers(rule)
    with pytest.raises(tp.TaskPackageError):
        tp.derive_params_from_landing(rule)


@pytest.mark.parametrize("value", [{}, {"PLY": 1.5}, {"PLY": -1},
                                    {"PLY": True}, {"PLY": float("inf")}])
def test_identical_old_structures_cannot_bypass_projection_validation(value):
    rule = _rule(value=value, legacy=[{
        "name": NAME, "value": value, "unit": "count",
        "relation": "min", "as_written": QUOTE}])
    before = copy.deepcopy(rule)
    with pytest.raises(tp.TaskPackageError) as error:
        tp.derive_params_from_landing(rule)
    assert error.value.items == ["R-RES/reserve_crews"]
    assert rule == before


@pytest.mark.parametrize("change", [
    {"unit": "duration_minutes"}, {"unit": "ч"},
    {"relation": "window"}, {"tz": "base-local"},
    {"condition": None}, {"condition": "вне базы"},
    {"condition": {"kind": "base_code", "value": "PLY"}},
])
def test_unrepresentable_map_metadata_is_not_discarded(change):
    # Current-format compatibility only: a base qualifier cannot also carry
    # an independent scope condition. landing/2 migration remains separate.
    rule = _rule(**change)
    with pytest.raises(tp.TaskPackageError):
        tp.derive_params_from_landing(rule)


@pytest.mark.parametrize("legacy", [[_record("PLY", 3)], [_record("XXX", 2)]])
def test_projection_refuses_original_value_or_base_conflict(legacy):
    rule = _rule(legacy=legacy)
    before = copy.deepcopy(rule)
    with pytest.raises(tp.TaskPackageError) as error:
        tp.derive_params_from_landing(rule)
    assert error.value.code == tp.PARAM_CARRIERS_DIVERGE
    assert rule == before


def test_logic_reader_gets_all_bases_not_just_the_old_subset():
    rule = _rule(legacy=[_record("PLY", 2)])
    got, problems = m.logic_param_records(rule, selected_names={NAME})
    assert problems == []
    assert {(p["qualifier"], p["value"]) for p in got} == {
        ("PLY", 2), ("SVG", 1), ("GRT", 0)}


@pytest.mark.parametrize("legacy", [None, [_record("PLY", 2)],
    [{"name": NAME, "value": VALUES, "unit": "count", "relation": "min", "as_written": QUOTE}]])
def test_package_and_gate_preserve_complete_base_map(tmp_path, legacy):
    doc = _ready_doc()
    doc["rules"].append(_rule(legacy=legacy))
    run = _run_dir(tmp_path, doc)
    original = (run / "extract/model.json").read_bytes()
    files = tp.build_package(run)
    packaged = json.loads(files["model.json"])
    emitted = next(r for r in packaged["rules"] if r["id"] == "R-RES")
    assert {(p["qualifier"], p["value"]) for p in emitted["params"]} == {
        ("PLY", 2), ("SVG", 1), ("GRT", 0)}
    assert emitted["template_fit"]["params"][NAME]["value"] == VALUES
    assert m.validate_schema(packaged) == [] and m.validate_model(packaged) == []
    path = tmp_path / "base-map.zip"
    path.write_bytes(tp.write_zip(files))
    assert gate.check(path)[0] == []
    assert tp.build_package(run) == files
    assert (run / "extract/model.json").read_bytes() == original


@pytest.mark.parametrize("legacy", [None, [_record("PLY", 2)]])
def test_existing_packed_format_projects_all_bases_and_keeps_its_exact_source(tmp_path, legacy):
    raw = "GRT:0,PLY:2,SVG:1"
    rule = _rule(value=raw, legacy=legacy)
    before = copy.deepcopy(rule)
    got = tp.derive_params_from_landing(rule)
    assert {(p["qualifier"], p["value"]) for p in got} == {
        ("PLY", 2), ("SVG", 1), ("GRT", 0)}
    assert rule == before
    doc = _ready_doc()
    doc["rules"].append(rule)
    files = tp.build_package(_run_dir(tmp_path, doc))
    emitted = next(r for r in json.loads(files["model.json"])["rules"] if r["id"] == "R-RES")
    assert emitted["template_fit"]["params"][NAME]["value"] == raw
    assert {(p["qualifier"], p["value"]) for p in emitted["params"]} == {
        ("PLY", 2), ("SVG", 1), ("GRT", 0)}
    path = tmp_path / "packed-base-map.zip"
    path.write_bytes(tp.write_zip(files))
    assert gate.check(path)[0] == []


@pytest.mark.parametrize("raw", ["PLY:2,PLY:2", "PLY:1,PLY:2", "PLY:2,",
    "PLY:1.5", "PLY:-1", "PLY:True", "PLY:2, SVG:1", "PLY:2:3"])
def test_bad_packed_strings_are_not_partially_read(raw):
    with pytest.raises(tp.TaskPackageError):
        tp.derive_params_from_landing(_rule(value=raw))


@pytest.mark.parametrize("base", ["ply", "PLУ", "TOOLONG", "P-L"])
def test_projection_does_not_emit_a_base_current_consumer_cannot_distinguish(base):
    with pytest.raises(tp.TaskPackageError):
        tp.derive_params_from_landing(_rule(value={base: 2}))


def test_map_cannot_be_assigned_to_a_non_base_parameter():
    rule = _rule()
    rule["template_fit"].update(family="duty_limits", params={
        "max_pairing_days": {"value": {"PLY": 2}, "quote": "2"}})
    assert m.validate_template_fit({"rules": [rule]})


def test_duplicate_old_base_records_are_not_collapsed():
    with pytest.raises(tp.TaskPackageError):
        tp.derive_params_from_landing(_rule(legacy=[_record("PLY", 2), _record("PLY", 2)]))
