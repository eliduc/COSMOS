"""Real component export judges; no native-world migration or support approval.

The complete synthetic draft.4 unit input is copied, never changed on disk.
Numeric expectations are literal independent probes, not exporter-derived.
"""
import copy
import json
from pathlib import Path

import pytest

from _conditions import Conditions
from vmp import conditions_export as ce, stand_adapters as sa, stand_tasks as st


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CREW_VMP_DETECTORS", raising=False)
    path = Path(__file__).resolve().parents[1] / "catalog-integration/0.7.0-draft.4/families.json"
    families = json.loads(path.read_text(encoding="utf-8"))["families"]
    family = next(r for r in families if r["id"] == "duty_limits")
    task = next(t for t in family["stand_tasks"] if t["id"] == "st-export-worktime-exact-1")
    return copy.deepcopy(task["world"])


def judge(world, expected, family):
    return st._kind_outcome("export_form", {"world": world, "expected": expected,
        "family": family, "section": "families", "task_id": "st-rest-fleet-unit"})


def actual_reader(world):
    inputs = world["export_form_input"]
    payload, _ = sa.world_to_builder(world)
    return Conditions(ce.build_conditions(inputs["model"], inputs["forms"],
                                         inputs["manifest"], payload))


@pytest.fixture
def rest(world):
    world["export_form_input"]["forms"]["conditional_floors"] = [{
        "threshold": "min_rest_outstation_min", "value_min": 960,
        "condition": {"kind": "tz_diff_hours", "relation": "min", "value": 4},
        "rule": "R-TRANS", "as_written": "Explicit synthetic 16-hour conditional floor"}]
    return world


def rest_expected(work=600, on_base=False, night=False, trans=True, value=960):
    return {"symbol": "required_layover", "probes": [{"work_min": work,
        "on_base": on_base, "night": night, "trans": trans, "value_min": value}]}


@pytest.mark.parametrize("work,on_base,trans,value", [
    (0, True, False, 720), (600, True, False, 720), (720, True, False, 720),
    (750, True, False, 750), (0, False, False, 600), (600, False, False, 600),
    (630, False, False, 630), (600, False, True, 960), (600, True, True, 720),
    (1020, False, True, 1020), (1020, True, False, 1020),
])
@pytest.mark.parametrize("night", [False, True])
def test_required_layover_real_export_reader_probes(rest, work, on_base, trans, value, night):
    expected = rest_expected(work, on_base, night, trans, value)
    before = copy.deepcopy(rest)
    out = judge(rest, expected, "rest_floors")
    assert out.result == "passed", out.note
    assert out.got == expected
    assert rest == before


@pytest.mark.parametrize("damage", ["lower_conditional", "wrong_branch", "lower_extended_top"])
def test_rest_reachable_numeric_mutation_fails_original_oracle(rest, damage):
    expected = rest_expected(work=1020, value=1020) if damage == "lower_extended_top" else rest_expected()
    assert judge(rest, expected, "rest_floors").result == "passed"
    inputs = rest["export_form_input"]
    if damage == "lower_conditional":
        inputs["forms"]["conditional_floors"][0]["value_min"] = 900
    elif damage == "wrong_branch":
        inputs["forms"]["conditional_floors"][0]["threshold"] = "min_rest_home_min"
    else:
        q = next(q for q in inputs["model"]["questions"] if q["id"] == "Q-3a-условия-прил6")
        q["answer"]["raw"] = "T1 = класс 2; класс 2 = 13:00."
    reader = actual_reader(rest)  # mutation reaches an actual successful exporter/reader
    probe = expected["probes"][0]
    assert reader.required_layover(probe["work_min"], probe["on_base"],
                                   probe["night"], probe["trans"]) != probe["value_min"]
    out = judge(rest, expected, "rest_floors")
    assert out.result == "failed", out.note
    assert out.got is not None, "Must be an observed mismatch, not an input refusal"


@pytest.mark.parametrize("damage,key", [("wrong_unit", "rest_base_by_work_h"),
    ("wrong_relation", "rest_base_by_work_h"), ("excluded", "rest_base_by_work_h"),
    ("incompatible_home_floor", "b12_rest_base_upto_12h")])
def test_rest_semantic_refusal_is_failure_not_support_evidence(rest, damage, key):
    assert judge(rest, rest_expected(), "rest_floors").result == "passed"
    rule = next(r for r in rest["export_form_input"]["model"]["rules"] if r["id"] == "R-REST-H")
    if damage == "wrong_unit": rule["params"][0]["unit"] = "count"
    elif damage == "wrong_relation": rule["params"][0]["relation"] = "max"
    elif damage == "excluded": rule["excluded_until_data"] = {"question": "Q-unit", "revision": 1}
    else: rule["params"][0]["value"] = 660
    with pytest.raises(ce.ConditionsExportError) as error:
        actual_reader(rest)
    assert key in {g["key"] for g in error.value.gaps}
    out = judge(rest, rest_expected(), "rest_floors")
    assert out.result == "failed", out.note
    assert key in out.note


@pytest.mark.parametrize("field,value", [("work_min", -1), ("work_min", True),
    ("work_min", 600.0), ("work_min", "600"), ("on_base", 1), ("on_base", "false"),
    ("night", 0), ("night", None), ("trans", 1), ("trans", "true"),
    ("value_min", 0), ("value_min", -1), ("value_min", True), ("value_min", 960.0)])
def test_rest_probe_invalid_types_cannot_pass(rest, field, value):
    expected = rest_expected()
    expected["probes"][0][field] = value
    assert judge(rest, expected, "rest_floors").result == "failed"


@pytest.mark.parametrize("damage", ["empty", "null", "not_list", "missing_key",
    "extra_key", "not_object", "missing_probes", "extra_expected"])
def test_rest_closed_shape(rest, damage):
    expected = rest_expected()
    if damage == "empty": expected["probes"] = []
    elif damage == "null": expected["probes"] = None
    elif damage == "not_list": expected["probes"] = {}
    elif damage == "missing_key": del expected["probes"][0]["night"]
    elif damage == "extra_key": expected["probes"][0]["other"] = 1
    elif damage == "not_object": expected["probes"][0] = []
    elif damage == "missing_probes": del expected["probes"]
    else: expected["other"] = None
    assert judge(rest, expected, "rest_floors").result == "failed"


@pytest.fixture
def fleet(world):
    world["flights"].append({"id": "F2", "from": "ST2", "to": "ST1", "fleet_type": "T2",
        "dep": "2025-10-02T12:00:00", "arr": "2025-10-02T14:00:00"})
    world["export_form_input"]["model"]["rules"].append({"id": "R-EXCL", "kind": "DATA",
        "statement": "Synthetic explicit type exclusion for T2", "params": []})
    world["export_form_input"]["forms"]["fleet_dh_marked"] = {
        "types": ["T2"], "action": "deadhead", "semantics": "apply_by_type", "rule": "R-EXCL"}
    return world


def fleet_expected(fleets=None):
    return {"symbol": "auto_plan_excluded_fleets", "fleets": ["T2"] if fleets is None else fleets}


@pytest.mark.parametrize("mode,want", [("apply", ["T2"]), ("description", []), ("absent", []),
    ("canonical_case", ["T2"]), ("two_types", ["T1", "T2"])])
def test_fleet_real_export_reader_explicit_semantics(fleet, mode, want):
    forms = fleet["export_form_input"]["forms"]
    if mode == "description": forms["fleet_dh_marked"]["semantics"] = "master_data_marking_description"
    elif mode == "absent": del forms["fleet_dh_marked"]
    elif mode == "canonical_case": forms["fleet_dh_marked"]["types"] = ["t2"]
    elif mode == "two_types": forms["fleet_dh_marked"]["types"] = ["T2", "T1"]
    before = copy.deepcopy(fleet)
    out = judge(fleet, fleet_expected(want), "fleet_exclusions")
    assert out.result == "passed", out.note
    assert out.got == fleet_expected(want)
    reader = actual_reader(fleet)
    assert reader.auto_plan_excluded_fleets() == frozenset(want)
    for code in ("T1", "T2"):
        assert reader.crew_min(code) == 2
        assert reader.crew_std(code) == 2
    assert fleet == before


@pytest.mark.parametrize("damage", ["wrong_type", "description", "remove_form"])
def test_fleet_reachable_mutation_fails_original_oracle(fleet, damage):
    expected = fleet_expected()
    assert judge(fleet, expected, "fleet_exclusions").result == "passed"
    forms = fleet["export_form_input"]["forms"]
    if damage == "wrong_type": forms["fleet_dh_marked"]["types"] = ["T1"]
    elif damage == "description": forms["fleet_dh_marked"]["semantics"] = "master_data_marking_description"
    else: del forms["fleet_dh_marked"]
    assert actual_reader(fleet).auto_plan_excluded_fleets() != frozenset(["T2"])
    out = judge(fleet, expected, "fleet_exclusions")
    assert out.result == "failed", out.note
    assert out.got is not None


@pytest.mark.parametrize("fleets", [None, "T2", {}, [""], ["  "], [1], [True], [None],
    [["T2"]], ["T2", "T2"]])
def test_fleet_expected_list_is_strict(fleet, fleets):
    assert judge(fleet, {"symbol": "auto_plan_excluded_fleets", "fleets": fleets},
                 "fleet_exclusions").result == "failed"


@pytest.mark.parametrize("damage", ["missing", "extra", "wrong_symbol"])
def test_fleet_expected_shape_is_closed(fleet, damage):
    expected = fleet_expected()
    if damage == "missing": del expected["fleets"]
    elif damage == "extra": expected["probes"] = []
    else: expected["symbol"] = "invented"
    assert judge(fleet, expected, "fleet_exclusions").result == "failed"


@pytest.mark.parametrize("family,symbol", [("rest_floors", "required_layover"),
    ("fleet_exclusions", "auto_plan_excluded_fleets")])
def test_new_judges_do_not_turn_missing_input_into_pass(world, family, symbol):
    expected = rest_expected() if symbol == "required_layover" else fleet_expected([])
    del world["export_form_input"]
    out = judge(world, expected, family)
    assert out.result == "failed", out.note
    assert "export_form_input" in out.note
