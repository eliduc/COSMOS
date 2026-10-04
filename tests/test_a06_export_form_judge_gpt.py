"""Real offline export_form judge; no support certification or native-world edits."""
import copy

import pytest

from test_a06_export_form_proof_gpt import _complete_unit_inputs
from vmp import stand_tasks as st


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CREW_VMP_DETECTORS", raising=False)
    model, forms, manifest, _ = _complete_unit_inputs()
    world = {
        "form": "schedule", "params": {}, "history": [], "horizon": None,
        "crews": [{"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T38"]}],
        "flights": [{"id": "F1", "from": "ST1", "to": "ST2", "fleet_type": "T38",
                     "dep": "2025-10-02T08:00:00", "arr": "2025-10-02T10:00:00"}],
        "export_form_input": {"model": model, "forms": forms, "manifest": manifest},
    }
    probes = [{"minute": minute, "landings": landings, "acclimatized": accl,
               "fleet": "T38", "value_min": value}
              for minute, landings, accl, value in (
                  (359, 1, True, 690), (360, 1, True, 780),
                  (899, 3, True, 750), (900, 3, True, 720),
                  (1020, 4, True, 660), (1439, 5, True, 540),
                  (360, 1, False, 690))]
    return {"world": world, "expected": {"symbol": "worktime_exact", "probes": probes},
            "family": "duty_limits", "section": "families", "task_id": "st-export-unit"}


def judge(ctx):
    return st._kind_outcome("export_form", ctx)


def test_real_export_form_judge_matches_independent_probes(ctx):
    before = copy.deepcopy(ctx)
    result = judge(ctx)
    assert result.result == "passed", result.note
    assert result.got == ctx["expected"]
    assert ctx == before


def test_native_missing_explicit_input_is_failure_not_skip_or_pass(ctx):
    del ctx["world"]["export_form_input"]
    result = judge(ctx)
    assert result.result == "failed", result.note
    assert "export_form_input" in result.note


@pytest.mark.parametrize("field,value", [
    ("minute", True), ("minute", -1), ("minute", 1440), ("minute", 360.0),
    ("landings", False), ("landings", 0), ("landings", 1.0),
    ("acclimatized", 1), ("acclimatized", "true"),
    ("fleet", ""), ("fleet", "  "), ("fleet", 1),
    ("value_min", True), ("value_min", 0), ("value_min", 780.0),
])
def test_probe_types_cannot_create_vacuous_pass(ctx, field, value):
    ctx["expected"]["probes"][0][field] = value
    result = judge(ctx)
    assert result.result == "failed", result.note
    assert "probes" in result.note


@pytest.mark.parametrize("damage", ["empty", "null", "unknown_symbol", "extra_expected",
                                    "missing_probe_key", "extra_probe_key", "list_item"])
def test_expected_contract_is_closed(ctx, damage):
    expected = ctx["expected"]
    if damage == "empty": expected["probes"] = []
    elif damage == "null": expected["probes"] = None
    elif damage == "unknown_symbol": expected["symbol"] = "invented"
    elif damage == "extra_expected": expected["other"] = None
    elif damage == "missing_probe_key": del expected["probes"][0]["value_min"]
    elif damage == "extra_probe_key": expected["probes"][0]["extra"] = None
    else: expected["probes"][0] = None
    result = judge(ctx)
    assert result.result == "failed", result.note


@pytest.mark.parametrize("damage", ["input_payload", "missing_forms", "wrong_model_type"])
def test_export_input_is_closed_and_never_supplies_payload(ctx, damage):
    inputs = ctx["world"]["export_form_input"]
    if damage == "input_payload": inputs["payload"] = {"employees": [{"homeAirport": "FAKE"}]}
    elif damage == "missing_forms": del inputs["forms"]
    else: inputs["model"] = []
    result = judge(ctx)
    assert result.result == "failed", result.note
    assert "export_form_input" in result.note


def test_world_payload_really_reaches_exporter(ctx):
    assert judge(ctx).result == "passed"
    ctx["world"]["crews"] = []
    result = judge(ctx)
    assert result.result == "failed", result.note
    assert "base" in result.note


def test_changed_input_changes_measured_result_not_expected(ctx):
    assert judge(ctx).result == "passed"
    rows = ctx["world"]["export_form_input"]["forms"]["matrices"][0]["rows"]
    row = next(r for r in rows if r["start_from_min"] == 360 and r["sectors_from"] == 1)
    row["value_min"] = 779
    changed = judge(ctx)
    assert changed.result == "failed", changed.note
    assert changed.got["probes"][1]["value_min"] == 779
    assert ctx["expected"]["probes"][1]["value_min"] == 780
    # Demonstrate reachability through the actual reader, not a generic refusal.
    ctx["expected"]["probes"][1]["value_min"] = 779
    assert judge(ctx).result == "passed"


def test_real_exporter_refusal_is_never_positive_evidence(ctx):
    assert judge(ctx).result == "passed"
    ctx["world"]["export_form_input"]["model"]["questions"] = []
    result = judge(ctx)
    assert result.result == "failed", result.note
    assert "ConditionsExportError" in result.note


def test_exporter_and_reader_are_bound_into_freshness():
    assert "export_form" in st.PURE_KINDS
    assert "vmp/conditions_export.py" in st.CODE_FILES
    assert "_conditions.py" in st.CODE_FILES
    actual = st.code_fingerprints()
    assert len(actual["vmp/conditions_export.py"]) == 64
    assert len(actual["_conditions.py"]) == 64
