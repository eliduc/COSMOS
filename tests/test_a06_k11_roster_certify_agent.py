"""K11 neighbouring certificate readers: real code, isolated catalogues.

The list reader for extension_bases already accepts a valid typed list;
these cases preserve that healthy behaviour and expose validation bypasses.
No projection, catalogue validator, certifier mechanism or verdict is mocked.
No LLM, JVM, service or operational solver is invoked.
"""
from copy import deepcopy

import pytest

from vmp.run import roster_certify as rc
from test_cdpm_landing_v2_projection import typed_rule, clean_bridge  # noqa: F401
from test_run_certify_caps import _rule, _spec, _dataset


def _typed(tmp_path, monkeypatch, family, name, value, unit, relation, quote, kind="HARD"):
    _doc, rule = typed_rule(tmp_path, monkeypatch, family, {
        name: {"value": value, "unit": unit, "relation": relation,
               "condition": None, "quote": quote}})
    rule["kind"] = kind
    rule.pop("params", None)
    return rule


def _cap(tmp_path, monkeypatch):
    rule = _typed(tmp_path, monkeypatch, "duty_extension_penalty",
                  "duty_extension_cap", 780, "duration_minutes", "max", "780 минут", "SOFT")
    rule["template_fit"]["params"]["extension_bases"] = {
        "value": ["ST1"], "unit": "code_list", "relation": "in", "condition": None,
        "quote": "ST1", "source": {"kind": "rule", "rule_id": rule["id"]}}
    # The family pin selects all carried cells, including the condition.
    from cdpm import catalog_bridge
    from template_catalog import family_contract
    fit = rule["template_fit"]
    fit["family_contract"] = family_contract.snapshot(
        catalog_bridge.catalog(), "duty_extension_penalty", fit["params"])
    return rule


def _judge_cap(cap):
    base = _rule("R-LIM", "duty_limits", {"max_duty_duration": 720})
    dataset = _dataset(("E1", "ST1", "2026-03-02T06:00", "2026-03-02T18:40"),
                       ("E2", "ST2", "2026-03-02T06:00", "2026-03-02T18:40"))
    return rc.apply_extension_caps({"rules": [base, cap]}, [_spec("R-LIM", 12)], dataset)


@pytest.mark.parametrize("form", ["typed-list", "legacy-list", "legacy-string"])
def test_actual_cap_reader_preserves_typed_list_and_legacy_scope(tmp_path, monkeypatch, form):
    if form == "typed-list":
        cap = _cap(tmp_path, monkeypatch)
    else:
        cap = _rule("R-EXT", "duty_extension_penalty", {
            "duty_extension_cap": 780,
            "extension_bases": ["ST1"] if form == "legacy-list" else "ST1"}, kind="SOFT")
    before = deepcopy(cap)
    kept, _notes, checks, findings = _judge_cap(cap)
    assert kept == [] and checks[0]["status"] == "прогнана"
    assert checks[0]["params"]["conditions"] == {"extension_bases": ["ST1"]}
    assert [f["target"]["row"] for f in findings] == [3]
    assert "базов" in findings[0]["text"]
    assert cap == before


@pytest.mark.parametrize("change", ["pin", "source", "string", "duplicates", "carrier-conflict"])
def test_invalid_typed_cap_cannot_raise_the_hard_limit(tmp_path, monkeypatch, change):
    cap = _cap(tmp_path, monkeypatch)
    fit = cap["template_fit"]
    if change == "pin":
        fit["family_contract"]["record_fingerprint"] = "0" * 64
    elif change == "source":
        fit["params"]["extension_bases"]["source"]["rule_id"] = "OTHER"
    elif change == "string":
        fit["params"]["extension_bases"]["value"] = "ST1"
    elif change == "duplicates":
        fit["params"]["extension_bases"]["value"] = ["ST1", "ST1"]
    else:
        cap["params"] = [{"name": "duty_extension_cap", "value": 900,
                           "unit": "duration_minutes", "relation": "max", "as_written": "900 минут"}]
    before = deepcopy(cap)
    _kept, _notes, checks, findings = _judge_cap(cap)
    assert checks[0]["status"] == "не прогнана", checks
    assert cap["id"] in checks[0]["reason"]
    assert findings == []
    assert cap == before


def _spread_dataset():
    columns = ["Табельный номер", "Время вылета", "Время прилёта"]
    return {"tables": {"SOL": {"Назначения": {"columns": columns, "rows": [
        dict(zip(columns, ("E1", "2026-03-02T06:00", "2026-03-02T10:00"))),
        dict(zip(columns, ("E2", "2026-03-02T06:00", "2026-03-02T06:00")))
    ]}}}}


@pytest.mark.parametrize("invalid", [False, True])
def test_numeric_typed_workload_is_not_list_stringified_or_defaulted(tmp_path, monkeypatch, invalid):
    rule = _typed(tmp_path, monkeypatch, "balanced_workload", "workload_spread_threshold",
                  1, "credit_hours", "max", "1", "SOFT")
    if invalid:
        rule["template_fit"]["params"]["workload_spread_threshold"]["value"] = [1]
    before = deepcopy(rule)
    checks, findings = rc.measure_soft_rules({"rules": [rule]}, _spread_dataset())
    assert findings == []
    if invalid:
        assert checks[0]["status"] == "не прогнана", checks
        assert "workload_spread_threshold" in checks[0]["reason"]
        assert "measure" not in checks[0]
    else:
        assert checks[0]["status"] == "прогнана"
        assert checks[0]["measure"]["value"] == 1  # floor(sqrt(8) - 1)
        assert checks[0]["measure"]["threshold_hours"] == 1
    assert rule == before


@pytest.mark.parametrize("invalid", [False, True])
def test_world_parameter_typed_pin_checked_before_exempting_rule(tmp_path, monkeypatch, invalid):
    rule = _typed(tmp_path, monkeypatch, "time_scale", "base_tz_offset_min",
                  180, "duration_minutes", "eq", "180 минут")
    if invalid:
        rule["template_fit"]["family_contract"]["record_fingerprint"] = "0" * 64
    before = deepcopy(rule)
    if invalid:
        with pytest.raises(rc.CertifyError, match=rule["id"]):
            rc.world_param_checks({"rules": [rule]})
    else:
        checks, remaining = rc.world_param_checks({"rules": [rule]})
        assert remaining["rules"] == []
        assert checks[0]["status"] == rc.WORLD_PARAM_STATUS
        assert checks[0]["params"]["base_tz_offset_min"] == 180
    assert rule == before


@pytest.mark.parametrize("invalid", [False, True])
def test_composition_typed_pin_checked_before_replacing_compiler_count(tmp_path, monkeypatch, invalid):
    rule = _typed(tmp_path, monkeypatch, "crew_composition", "crew_size", 2, "count", "eq", "2")
    if invalid:
        rule["template_fit"]["family_contract"]["record_fingerprint"] = "0" * 64
    before = deepcopy(rule)
    spec = {"template": "счёт-состава-группы", "basis": {"rule": rule["id"]},
            "params": {"sheet": "SOL/Назначения", "count": 7}}
    if invalid:
        with pytest.raises(rc.CertifyError, match=rule["id"]):
            rc.normalize_composition_specs({"rules": [rule]}, [spec])
    else:
        rc.normalize_composition_specs({"rules": [rule]}, [spec])
        assert spec["params"]["count"] == 2
    assert rule == before


@pytest.mark.parametrize("invalid", [False, True])
def test_soft_extension_cannot_use_an_untrusted_typed_hard_base(tmp_path, monkeypatch, invalid):
    base = _typed(tmp_path, monkeypatch, "duty_limits", "max_duty_duration",
                  720, "duration_minutes", "max", "720 минут")
    if invalid:
        base["template_fit"]["family_contract"]["record_fingerprint"] = "0" * 64
    extension = _rule("R-EXT", "duty_extension_penalty", {"duty_extension_cap": 780}, kind="SOFT")
    svod = {"rules": [base, extension]}
    before = deepcopy(svod)
    dataset = _dataset(("E1", "ST1", "2026-03-02T06:00", "2026-03-02T18:40"))
    checks, findings = rc.measure_soft_rules(svod, dataset)
    if invalid:
        assert checks[0]["status"] == "не прогнана"
        assert base["id"] in checks[0]["reason"]
        assert findings == []
    else:
        assert checks[0]["status"] == "прогнана"
        assert len(findings) == 1
        assert "760 мин > 720" in findings[0]["text"]
    assert svod == before


@pytest.mark.parametrize("legacy_carrier", [False, True])
def test_legal_typed_crew_variant_is_not_a_global_composition_count(tmp_path, monkeypatch, legacy_carrier):
    from webapp.threshold_gate import projected_params
    rule = _typed(tmp_path, monkeypatch, "crew_composition", "crew_size", 3, "count", "eq", "3")
    rule["template_fit"]["params"]["crew_size"]["condition"] = {
        "kind": "crew_variant", "value": "расширенный"}
    projected = projected_params(rule)
    assert len(projected) == 1
    assert projected[0]["qualifier"] == "расширенный"
    if legacy_carrier:
        rule["params"] = deepcopy(projected)
    before = deepcopy(rule)
    spec = {"template": "счёт-состава-группы", "basis": {"rule": rule["id"]},
            "params": {"sheet": "SOL/Назначения", "count": 2}}
    old_spec = deepcopy(spec)
    with pytest.raises(rc.CertifyError, match="crew_size.*услов"):
        rc.normalize_composition_specs({"rules": [rule]}, [spec])
    assert spec == old_spec  # reject before any compiler-output mutation
    assert rule == before


@pytest.mark.parametrize("target", ["world", "workload", "hard-base"])
def test_catalogue_unqualified_typed_numbers_reject_foreign_condition(tmp_path, monkeypatch, target):
    from webapp.threshold_gate import ThresholdGateError, projected_params
    args = {
        "world": ("time_scale", "base_tz_offset_min", 180, "duration_minutes", "eq", "180 минут", "HARD"),
        "workload": ("balanced_workload", "workload_spread_threshold", 1, "credit_hours", "max", "1", "SOFT"),
        "hard-base": ("duty_limits", "max_duty_duration", 720, "duration_minutes", "max", "720 минут", "HARD"),
    }[target]
    rule = _typed(tmp_path, monkeypatch, *args)
    rule["template_fit"]["params"][args[1]]["condition"] = {"kind": "area", "value": "база"}
    before = deepcopy(rule)
    with pytest.raises(ThresholdGateError, match="condition"):
        projected_params(rule)  # existing pin's qualifier is null, not area
    if target == "world":
        with pytest.raises(rc.CertifyError, match="condition"):
            rc.world_param_checks({"rules": [rule]})
    elif target == "workload":
        checks, findings = rc.measure_soft_rules({"rules": [rule]}, _spread_dataset())
        assert checks[0]["status"] == "не прогнана" and findings == []
        assert "condition" in checks[0]["reason"]
    else:
        extension = _rule("R-EXT", "duty_extension_penalty", {"duty_extension_cap": 780}, kind="SOFT")
        checks, findings = rc.measure_soft_rules({"rules": [rule, extension]},
            _dataset(("E1", "ST1", "2026-03-02T06:00", "2026-03-02T18:40")))
        assert checks[0]["status"] == "не прогнана" and findings == []
        assert "condition" in checks[0]["reason"]
    assert rule == before
