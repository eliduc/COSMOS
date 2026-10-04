"""Three independent carriers: package, declaration, provider policy snapshot.

Focused reader fragments, not a complete package/run or a release assertion.
"""
import copy

import pytest

from webapp import threshold_gate as tg


def evidence():
    base = {"id": "BASE", "kind": "HARD", "params": [{
        "name": "max_duty_duration", "value": 720,
        "unit": "duration_minutes", "relation": "max"}]}
    extension = {"id": "EXT", "kind": "SOFT", "template_fit": {
        "category": "A", "family": "duty_extension_penalty", "params": {
            "duty_extension_cap": {"value": 780, "unit": "duration_minutes", "quote": "780 минут"},
            "extension_bases": {"value": ["A"], "unit": "code_list", "quote": "A"},
            "extension_consent": {"value": True, "unit": "flag", "quote": "с согласия"}}}}
    policy = {"rule": "EXT", "cap_min": 780, "extension_bases": ["A"],
              "extension_consent": True}
    pkg = {"model": {"rules": [base, extension]}}
    applied = [{"threshold": "max_duty_span_min", "value": 720,
                "fromParams": ["max_duty_duration"], "rules": ["BASE", "EXT"],
                "dutyExtensions": [copy.deepcopy(policy)]}]
    actual = {"max_duty_span_min": {
        "base_min": 720, "policies": [copy.deepcopy(policy)],
        "base_source": "employee.homeAirport.code",
        "consent_match": "employee+exact_duty_start_end_payload_scale"}}
    return pkg, applied, actual


def compare(pkg, applied, actual):
    before = copy.deepcopy((pkg, applied, actual))
    result = tg.compare(pkg, {"max_duty_span_min": 720}, applied=applied,
        mapping={"max_duty_duration": "max_duty_span_min"}, policy_readings=actual)
    assert (pkg, applied, actual) == before
    return [r for r in result if r["params"] == ["duty_extension_cap"]]


def test_addressed_extension_preserves_base_reading_and_exact_source_policy():
    row, = compare(*evidence())
    assert row["verdict"] == tg.MATCH and row["rules"] == ["EXT"]


def test_missing_policy_snapshot_cannot_be_replaced_by_applied():
    pkg, applied, _ = evidence()
    row, = compare(pkg, applied, None)
    assert row["verdict"] == tg.DIVERGE


@pytest.mark.parametrize(("key", "value"), [
    ("cap_min", 900), ("extension_bases", None),
    ("extension_bases", ["B"]), ("extension_consent", False), ("rule", "OTHER")])
def test_equal_applied_and_provider_cannot_override_package(key, value):
    pkg, applied, actual = evidence()
    applied[0]["dutyExtensions"][0][key] = value
    actual["max_duty_span_min"]["policies"][0][key] = value
    row, = compare(pkg, applied, actual)
    assert row["verdict"] == tg.DIVERGE


@pytest.mark.parametrize(("key", "value"), [
    ("base_min", 780), ("base_source", "rule.declaration"),
    ("consent_match", "employee_without_duty_bounds")])
def test_actual_binding_mechanism_is_part_of_the_evidence(key, value):
    pkg, applied, actual = evidence()
    actual["max_duty_span_min"][key] = value
    row, = compare(pkg, applied, actual)
    assert row["verdict"] == tg.DIVERGE


def test_declared_policy_drift_is_detected_when_actual_and_package_agree():
    pkg, applied, actual = evidence()
    applied[0]["dutyExtensions"][0]["extension_bases"] = ["B"]
    assert compare(pkg, applied, actual)[0]["verdict"] == tg.DIVERGE


@pytest.mark.parametrize("value", [None, "false", 0])
def test_unknown_or_nonboolean_source_condition_refuses(value):
    pkg, applied, actual = evidence()
    pkg["model"]["rules"][1]["template_fit"]["params"]["extension_consent"]["value"] = value
    with pytest.raises(tg.ThresholdGateError):
        compare(pkg, applied, actual)


def test_optional_absence_is_not_the_same_as_a_present_null_condition():
    pkg, applied, actual = evidence()
    del pkg["model"]["rules"][1]["template_fit"]["params"]["extension_consent"]
    applied[0]["dutyExtensions"][0]["extension_consent"] = False
    actual["max_duty_span_min"]["policies"][0]["extension_consent"] = False
    assert compare(pkg, applied, actual)[0]["verdict"] == tg.MATCH


def test_cancelled_extension_cannot_survive_as_provider_policy():
    pkg, applied, actual = evidence()
    pkg["model"]["rules"][1]["kind"] = "OUT"
    assert compare(pkg, applied, actual)[0]["verdict"] == tg.DIVERGE


def test_several_disjoint_policies_remain_separate():
    pkg, applied, actual = evidence()
    other = copy.deepcopy(pkg["model"]["rules"][1])
    other["id"] = "EXT-B"
    other["template_fit"]["params"]["extension_bases"]["value"] = ["B"]
    other["template_fit"]["params"]["extension_bases"]["quote"] = "B"
    other["template_fit"]["params"]["duty_extension_cap"]["value"] = 840
    other["template_fit"]["params"]["duty_extension_cap"]["quote"] = "840 минут"
    pkg["model"]["rules"].append(other)
    p = {"rule": "EXT-B", "cap_min": 840, "extension_bases": ["B"], "extension_consent": True}
    applied[0]["dutyExtensions"].append(copy.deepcopy(p))
    actual["max_duty_span_min"]["policies"].insert(0, copy.deepcopy(p))
    row, = compare(pkg, applied, actual)
    assert row["verdict"] == tg.MATCH and row["rules"] == ["EXT", "EXT-B"]
