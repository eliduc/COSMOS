"""K11: real fit-check readers on strict landing/2 and legacy inputs.

Temporary catalogue only; no model, JVM, service, external gate or socket.
The catalogue and projections are real, not acceptance mocks. Expected
qualification outcomes come from explicit source-book rows.
"""
import copy
import hashlib
from pathlib import Path

import pytest

from cdpm import catalog_bridge as cb
from template_catalog import family_contract, landing
from vmp.run import fit_checks as fc
from webapp.threshold_gate import projected_params
from test_k03_subject_vocabulary import selected_catalogue
from test_audit_fit_checks import _fleet_fixture, _rule, _solution, _svod
from vmp.run.roster_certify import build_solution_dataset


@pytest.fixture
def catalogue(tmp_path, monkeypatch):
    cb.forget()
    cat = selected_catalogue(tmp_path, monkeypatch)
    yield cat
    cb.forget()


def typed(cat, family, values, *, quotes=None):
    rule = _rule("R-K11", family, values)
    definitions = {p["name"]: p for p in cat.family(family)["params"]}
    cells = {}
    for name, value in values.items():
        definition = definitions[name]
        unit = definition["unit"]
        quote = ((quotes or {}).get(name) or
                 ("; ".join(value) if isinstance(value, list) else
                  f"{value} минут" if unit == "duration_minutes" else str(value)))
        cells[name] = {"value": copy.deepcopy(value), "unit": unit,
                       "relation": definition.get("relation"),
                       "condition": None, "quote": quote,
                       "source": {"kind": "rule", "rule_id": rule["id"]}}
        if unit == "time_of_day_minutes":
            cells[name]["tz"] = "base-local"
    rule["template_fit"] = {
        "schema_version": "landing/2", "category": "A", "family": family,
        "registry_rev": cat.fingerprint(),
        "family_contract": family_contract.snapshot(cat, family, cells),
        "params": cells}
    assert landing.problems(rule, cat) == []
    # Independently establish that the fixture reaches the existing strict
    # consumer projection before testing the fit-check reader.
    projected_params(rule)
    return rule


def fleet_rule(cat, labels=None):
    return typed(cat, "fleet_exclusions", {
        "fleet_types": ["Полярис-Р200"] if labels is None else labels})


@pytest.mark.parametrize("qualified", [True, False])
def test_actual_cosmos_k11_rule_with_its_original_pinned_catalogue(monkeypatch, qualified):
    # Exact rule object read from COSMOS run-0001-extract/model.json,
    # SHA256 F2CACD402614F49A7A4C98EF4AD87C84E0133170E8DC1DDC967736FCB8C78768.
    # The unrelated source dataset below is controlled, NOT a live run replay.
    rule = {"id": "R-S1-абз-11", "title": "Допуск по типу воздушного судна",
            "statement": "Рейс на воздушном судне типа Полярис-Р200 выполняет пилот, "
                         "у которого допуск на этот тип действует на дату рейса.",
            "zone": "BOTH", "kind": "HARD", "sources": [{"sid": "S1", "anchor": "абз.11"}],
            "template_fit": {"category": "A", "family": "fleet_exclusions", "params": {
                "fleet_types": {"value": ["Полярис-Р200"],
                                "quote": "на воздушном судне типа Полярис-Р200",
                                "unit": "code_list", "relation": None, "condition": None,
                                "source": {"kind": "rule", "rule_id": "R-S1-абз-11"}}},
                "registry_rev": "07d68c15c6fb8c66d44b5cd5bc466e632a59e20db807d78a91772145010f9357",
                "family_contract": {"family_id": "fleet_exclusions",
                    "catalog_version": "0.7.0-draft.2",
                    "catalog_fingerprint": "07d68c15c6fb8c66d44b5cd5bc466e632a59e20db807d78a91772145010f9357",
                    "record_fingerprint": "8ad10bcd8ac587dc5da8710cf5cc03b7debada104fa0a1bf8726bce0a5058f72",
                    "param_set": "several"}, "schema_version": "landing/2"}}
    source = Path(__file__).resolve().parents[1] / "catalog-integration/0.7.0-draft.2"
    before_files = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in source.rglob("*") if p.is_file()}
    monkeypatch.setenv(cb.ENV_CATALOG, str(source))
    cb.forget()
    try:
        assert cb.catalog().fingerprint() == rule["template_fit"]["registry_rev"]
        assert landing.problems(rule, cb.catalog()) == []
        _, dataset = _fleet_fixture()
        if not qualified:
            dataset["tables"]["D1"]["Допуски"]["rows"] = []
        before = copy.deepcopy(rule)
        check, findings = fc._fleet_qualification(rule["id"], rule, dataset, fc._Counter())
        assert check["status"] == "прогнана", check
        assert check["params"]["fleet_types"] == ["Полярис-Р200"]
        assert len(findings) == (0 if qualified else 1)
        assert rule == before
    finally:
        cb.forget()
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.rglob("*") if p.is_file()} == before_files


@pytest.mark.parametrize("qualified", [True, False])
def test_typed_fleet_reaches_actual_source_check(catalogue, qualified):
    rule = fleet_rule(catalogue)
    _, dataset = _fleet_fixture()
    if not qualified:
        dataset["tables"]["D1"]["Допуски"]["rows"] = []
    before = copy.deepcopy((rule, dataset))
    specs, checks, findings, remaining = fc.fit_code_checks({"rules": [rule]}, dataset)
    assert specs == [] and remaining["rules"] == []
    assert checks[0]["status"] == "прогнана", checks
    assert checks[0]["params"]["fleet_types"] == ["Полярис-Р200"]
    assessment = checks[0]["qualification_assessments"][0]
    assert assessment["aircraft_type"] == "Полярис-Р200"
    assert assessment["applicable"] is True
    assert assessment["valid_qualification_rows"] == ([2] if qualified else [])
    assert len(findings) == (0 if qualified else 1)
    if findings:
        assert findings[0]["target"]["row"] == 2
        assert findings[0]["rule_refs"] == [rule["id"]]
    assert (rule, dataset) == before


def test_typed_fleet_preserves_opaque_comma_label(catalogue):
    label = "Полярис, Р200"
    rule = fleet_rule(catalogue, [label])
    _, dataset = _fleet_fixture()
    for name in ("Рейсы", "Допуски"):
        dataset["tables"]["D1"][name]["rows"][0]["Тип ВС"] = label
    check, findings = fc._fleet_qualification(rule["id"], rule, dataset, fc._Counter())
    assert check["status"] == "прогнана", check
    assert check["params"]["fleet_types"] == [label]
    assert check["qualification_assessments"][0]["applicable"] is True
    assert not findings


@pytest.mark.parametrize("mutation", [
    "empty", "duplicate", "not-text", "blank", "string-instead-of-list",
    "unit", "source", "quote", "registry", "record-pin", "future-schema",
    "carrier", "condition",
])
def test_bad_typed_fleet_is_not_run_and_never_uses_legacy_fallback(catalogue, mutation):
    rule = fleet_rule(catalogue)
    fit = rule["template_fit"]
    cell = fit["params"]["fleet_types"]
    if mutation in {"empty", "duplicate", "not-text", "blank", "string-instead-of-list"}:
        cell["value"] = {"empty": [], "duplicate": ["Полярис-Р200", "Полярис-Р200"],
                         "not-text": [123], "blank": [" "],
                         "string-instead-of-list": "Полярис-Р200"}[mutation]
    elif mutation == "unit":
        cell["unit"] = "count"
    elif mutation == "source":
        cell["source"]["rule_id"] = "R-FOREIGN"
    elif mutation == "quote":
        cell["quote"] = ""
    elif mutation == "registry":
        fit["registry_rev"] = "0" * 64
    elif mutation == "record-pin":
        fit["family_contract"]["record_fingerprint"] = "0" * 64
    elif mutation == "future-schema":
        fit["schema_version"] = "landing/99"
    elif mutation == "carrier":
        rule["params"] = [{"name": "fleet_types", "unit": "code_list", "relation": "eq",
                           "value": "Ту-204", "as_written": "Ту-204"}]
    else:
        cell["condition"] = {"kind": "area", "value": "база"}
    _, dataset = _fleet_fixture()
    before = copy.deepcopy(rule)
    check, findings = fc._fleet_qualification(rule["id"], rule, dataset, fc._Counter())
    assert check["status"] == "не прогнана", check
    assert check["rows_checked"] == 0 and check["reason"] and not findings
    assert rule == before


def test_legacy_fleet_healthy_and_conflicting_carrier(catalogue):
    rule, dataset = _fleet_fixture()
    check, findings = fc._fleet_qualification(rule["id"], rule, dataset, fc._Counter())
    assert check["status"] == "прогнана" and not findings
    rule["params"] = [{"name": "fleet_types", "unit": "code_list", "relation": "eq",
                       "value": "Ту-204", "as_written": "Ту-204"}]
    check, findings = fc._fleet_qualification(rule["id"], rule, dataset, fc._Counter())
    assert check["status"] == "не прогнана" and not findings
    assert "носител" in check["reason"]


NUMERIC = [
    ("crew_composition", {"crew_size": 2}),
    ("duty_limits", {"max_duty_duration": 720}),
    ("duty_limits", {"max_landings_per_duty": 2}),
    ("duty_limits", {"max_pairing_days": 2}),
    ("rest_floors", {"min_rest_home": 720}),
    ("rest_floors", {"min_rest_outstation": 600}),
    ("connection_windows", {"min_turnaround": 45}),
    ("span_accumulators", {"max_monthly_block": 6000}),
    ("time_scale", {"base_tz_offset_min": 180}),
    ("night_series", {"max_consecutive_night_duties": 3,
                       "night_duty_start": 1320, "night_duty_end": 360}),
    ("rolling_rest", {"weekly_rest_required": 2520, "weekly_rest_period": 10080}),
    ("days_off_floor", {"min_days_off": 8}),
    ("duty_series_limit", {"max_duty_series_length": 5, "post_series_days_off": 2}),
]


@pytest.mark.parametrize("family,values", NUMERIC, ids=[f"{f}-{next(iter(v))}" for f, v in NUMERIC])
def test_all_numeric_fit_readers_keep_healthy_values_and_reject_bad_provenance(
        catalogue, family, values):
    quotes = {"night_duty_start": "22:00", "night_duty_end": "06:00"}
    rule = typed(catalogue, family, values, quotes=quotes)
    before = copy.deepcopy(rule)
    for name, expected in values.items():
        assert fc.fit_param(rule, name) == expected
    assert rule == before
    first = next(iter(values))
    rule["template_fit"]["params"][first]["source"]["rule_id"] = "R-OTHER"
    # A neighbour's broken provenance invalidates the pinned landing too.
    for name in values:
        assert fc.fit_param(rule, name) is None
    if family != "time_scale":
        specs, checks, findings, rest = fc.fit_code_checks({"rules": [rule]}, {})
        assert not specs and not findings and not rest["rules"]
        assert len(checks) == 1 and checks[0]["status"] == "не прогнана"
        assert checks[0]["reason"]


def test_typed_numeric_families_reach_same_actual_checks_as_legacy(catalogue):
    legacy = _svod()
    modern = copy.deepcopy(legacy)
    for index, old in enumerate(legacy["rules"]):
        family = old["template_fit"]["family"]
        values = {name: cell["value"] for name, cell in old["template_fit"]["params"].items()}
        new = typed(catalogue, family, values)
        new["id"] = old["id"]
        new["statement"] = old["statement"]
        for cell in new["template_fit"]["params"].values():
            cell["source"]["rule_id"] = old["id"]
        modern["rules"][index] = new
    dataset = build_solution_dataset(_solution(bad=True))
    old_specs, old_checks, old_findings, _ = fc.fit_code_checks(legacy, dataset)
    specs, checks, findings, rest = fc.fit_code_checks(modern, dataset)
    assert specs == old_specs and checks == old_checks and findings == old_findings
    assert not rest["rules"]
    assert {f["rule_refs"][0] for f in findings} == {
        "R-LAND", "R-DAYS", "R-REST-H", "R-REST-O", "R-SIT"}


def test_typed_numeric_carrier_conflict_never_uses_raw_landing(catalogue):
    rule = typed(catalogue, "crew_composition", {"crew_size": 2})
    rule["params"] = [{"name": "crew_size", "value": 3, "unit": "count",
                       "relation": "eq", "as_written": "3"}]
    assert fc.fit_param(rule, "crew_size") is None
    specs, checks, findings, rest = fc.fit_code_checks({"rules": [rule]}, {})
    assert not specs and not findings and not rest["rules"]
    assert checks[0]["status"] == "не прогнана" and "носител" in checks[0]["reason"]


def test_qualified_numeric_value_is_not_silently_made_global(catalogue):
    rule = typed(catalogue, "connection_windows", {"min_turnaround": 45})
    rule["template_fit"]["params"]["min_turnaround"]["condition"] = {
        "kind": "area", "value": "база"}
    assert landing.problems(rule, catalogue) == []
    assert fc.fit_param(rule, "min_turnaround") is None


@pytest.mark.parametrize("family,values,expected", [
    ("night_series", {"max_consecutive_night_duties": 3,
                       "night_duty_start": 1320, "night_duty_end": 360},
     {"form": "CONSECUTIVE_NIGHT_DUTIES_MAX", "bound": 3, "nightFrom": 1140, "nightTo": 180}),
    ("rolling_rest", {"weekly_rest_required": 2520, "weekly_rest_period": 10080},
     {"form": "REST_IN_ROLLING_WINDOW", "bound": 2520, "condBound": 7}),
    ("days_off_floor", {"min_days_off": 8},
     {"form": "DAYS_OFF_PER_MONTH_MIN", "bound": 8}),
    ("duty_series_limit", {"max_duty_series_length": 5, "post_series_days_off": 2},
     {"form": "DUTY_SERIES_LIMIT", "bound": 2, "condBound": 5}),
])
def test_history_form_plans_use_verified_typed_parameters(catalogue, family, values, expected):
    from vmp.run import logic_checks
    rule = typed(catalogue, family, values,
                 quotes={"night_duty_start": "22:00", "night_duty_end": "06:00"})
    plan = logic_checks.plan_from_fit(family, rule, base_tz_offset_min=180)
    assert {key: plan[key] for key in expected} == expected


def test_invalid_present_time_scale_never_falls_back_to_zero(catalogue):
    night = typed(catalogue, "night_series", {
        "max_consecutive_night_duties": 3, "night_duty_start": 1320, "night_duty_end": 360},
        quotes={"night_duty_start": "22:00", "night_duty_end": "06:00"})
    clock = typed(catalogue, "time_scale", {"base_tz_offset_min": 180})
    clock["id"] = "R-CLOCK"
    clock["template_fit"]["params"]["base_tz_offset_min"]["source"]["rule_id"] = "R-FOREIGN"
    specs, checks, findings, _ = fc.fit_code_checks({"rules": [night, clock]}, {})
    assert not specs and not findings
    check = next(c for c in checks if c["basis"]["rule"] == night["id"])
    assert check["status"] == "не прогнана"
    assert "шкала времени" in check["reason"] and "source" in check["reason"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "45", [45]])
def test_legacy_numeric_non_numbers_are_not_thresholds(value):
    rule = _rule("R-LEGACY", "connection_windows", {"min_turnaround": value})
    assert fc.fit_param(rule, "min_turnaround") is None


@pytest.mark.parametrize("bad_pin", [False, True])
def test_parameterless_unavailability_keeps_its_pin_boundary(monkeypatch, bad_pin):
    from test_fit_checks_unavailability import _dataset
    # This family was added after the synthetic legacy-catalogue fixture;
    # use its real integration record, never invent a replacement family.
    source = Path(__file__).resolve().parents[1] / "catalog-integration/0.7.0-draft.2"
    monkeypatch.setenv(cb.ENV_CATALOG, str(source))
    cb.forget()
    try:
        rule = typed(cb.catalog(), "crew_unavailability", {})
        if bad_pin:
            rule["template_fit"]["registry_rev"] = "0" * 64
        check, findings = fc._crew_unavailability(rule["id"], rule, _dataset(), fc._Counter())
        assert not findings
        if bad_pin:
            assert check["status"] == "не прогнана" and check["rows_checked"] == 0
            assert "landing-contract" in check["reason"]
        else:
            assert check["status"] == "прогнана" and check["rows_checked"] == 2
    finally:
        cb.forget()
