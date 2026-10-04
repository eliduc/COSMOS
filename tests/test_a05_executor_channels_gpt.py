"""Explicit component evidence for provider/Builder threshold channel separation.

The fixture is synthetic and labelled: actual _cos_rules reader and Builder
calendar predicates execute locally, but no run, support or admission is claimed.
No network, service, solver search or subprocess is used by this profile.
"""
import copy
from datetime import datetime, timedelta
import hashlib
import json

import pytest

import _cos_rules
from _pairing_builder import Builder
from webapp import threshold_gate as tg


EXTERNAL = "max_pairing_days"


def fixture(*, offset=0):
    pkg = {"manifest": {"spec_version": "1.20", "source_run": "explicit-component-only"},
           "model": {"rules": [{"id": "R-DAYS", "kind": "HARD", "params": [
               {"name": EXTERNAL, "value": 7, "unit": "count", "relation": "max"}]}]}}
    applied = [{"threshold": EXTERNAL, "value": 7, "fromParams": [EXTERNAL],
                "rules": ["R-DAYS"], "executor": "pairing-builder"}]
    pkg["model"]["rules"].append({"id": "R-OFFSET", "kind": "HARD", "params": [
        {"name": "base_tz_offset_min", "value": offset,
         "unit": "duration_minutes", "relation": "eq"}]})
    applied.append({"threshold": "base_tz_offset_min", "value": offset,
                    "fromParams": ["base_tz_offset_min"], "rules": ["R-OFFSET"],
                    "executor": "kotlin-provider"})
    return pkg, applied


def observe(pkg, *, days=7, offset=0):
    """Capture real reader values, never copy values from applied."""
    raw = json.dumps({"max_pairing_days": days, "base_tz_offset_min": offset},
                     sort_keys=True).encode("utf-8")
    rules = _cos_rules.load_bytes(raw, where="explicit A05 component fixture", strict=True)
    return {"schema": "pairing-builder-thresholds/1",
            "package_identity": tg.builder_package_identity(pkg),
            "contract_bytes": raw, "contract_sha256": hashlib.sha256(raw).hexdigest(),
            "source_keys_read": list(rules.source_keys_read),
            "readings": {EXTERNAL: {"value": rules.max_pairing_days,
                "unit": "count", "relation": "max",
                "semantics": "inclusive-local-calendar-days",
                "base_tz_offset_min": rules.base_tz_offset_min}}}, rules


def compare(pkg, applied, evidence=None, provider=None):
    before = copy.deepcopy((pkg, applied, evidence, provider))
    try:
        # The default is an explicitly labelled protocol fixture observation,
        # not an assertion that this test has run the JVM provider.
        if provider is None:
            provider = {"base_tz_offset_min": pkg["model"]["rules"][1]["params"][0]["value"]}
        return tg.compare(pkg, provider, mapping={EXTERNAL: EXTERNAL,
                          "base_tz_offset_min": "base_tz_offset_min"},
                          applied=applied, builder_readings=evidence)
    finally:
        assert (pkg, applied, evidence) == before[:3]
        if before[3] is not None:
            assert provider == before[3]


def test_external_claim_without_observation_remains_unjudged_not_provider_divergence():
    pkg, applied = fixture()
    rows = compare(pkg, applied)
    external = [r for r in rows if r["reading"] == EXTERNAL]
    assert external and all(row["verdict"] == tg.INCOMPARABLE for row in external)
    assert all(row["solver_value"] is None for row in external)
    assert all(r["verdict"] == tg.MATCH for r in rows if r["reading"] != EXTERNAL)
    assert tg.unjudged_claimed_params(rows, applied) == {EXTERNAL}
    assert applied[0]["value"] == 7


def test_real_reader_and_calendar_predicate_confirm_component_only():
    pkg, applied = fixture()
    evidence, rules = observe(pkg)
    assert rules.max_pairing_days == 7
    builder = object.__new__(Builder)  # No candidate generation or search.
    builder.rules = rules
    start = datetime(2026, 10, 1, 6)
    for days in (7, 8):
        end = start + timedelta(days=days - 1, hours=1)
        chain = [{"t0": start, "t1": end, "work0": start, "work1": end}]
        assert rules.pairing_days(start, end) == days
        assert builder.pairing_days_ok(chain, []) is (days == 7)
        assert builder.pairing_span_ok(chain) is (days == 7)
    rows = compare(pkg, applied, evidence)
    assert rows and all(row["verdict"] == tg.MATCH for row in rows)
    assert tg.unjudged_claimed_params(rows, applied) == set()


def test_calendar_basis_uses_local_dates_not_elapsed_24_hours():
    pkg, applied = fixture(offset=180)
    evidence, rules = observe(pkg, offset=180)
    builder = object.__new__(Builder)
    builder.rules = rules
    start = datetime(2026, 10, 1, 20, 30)
    end = datetime(2026, 10, 7, 21, 30)
    assert (end - start).total_seconds() < 7 * 86400
    assert rules.pairing_days(start, end) == 8
    assert builder.pairing_days_ok([{"t0": start, "t1": end}], []) is False
    assert all(r["verdict"] == tg.MATCH for r in compare(pkg, applied, evidence))


@pytest.mark.parametrize("channel", ["builder", "claim", "both"])
def test_reachable_seven_to_eight_mutations_do_not_match_package(channel):
    pkg, applied = fixture()
    evidence, _ = observe(pkg, days=8 if channel in {"builder", "both"} else 7)
    if channel in {"claim", "both"}:
        applied[0]["value"] = 8
    rows = compare(pkg, applied, evidence)
    assert any(r["verdict"] == tg.DIVERGE for r in rows)
    if channel in {"builder", "both"}:
        assert any(r["solver_value"] == 8 and r["package_values"] == [7]
                   and r["verdict"] == tg.DIVERGE for r in rows)


@pytest.mark.parametrize("mutation", [
    "wrong-package", "wrong-contract-sha", "wrong-contract-bytes", "absent-readkey",
    "duplicate-readkey", "invented-readkey", "wrong-value", "bool-value", "wrong-unit",
    "wrong-relation", "wrong-semantics", "wrong-offset", "bool-offset", "missing-offset",
    "unknown-schema", "unknown-reading", "extra-envelope", "missing-contract", "merely-number",
])
def test_invalid_builder_observations_fail_closed(mutation):
    pkg, applied = fixture()
    evidence, _ = observe(pkg)
    cell = evidence["readings"][EXTERNAL]
    if mutation == "wrong-package":
        evidence["package_identity"] = "0" * 64
    elif mutation == "wrong-contract-sha":
        evidence["contract_sha256"] = "0" * 64
    elif mutation == "wrong-contract-bytes":
        evidence["contract_bytes"] += b" "
    elif mutation == "absent-readkey":
        evidence["source_keys_read"].remove(EXTERNAL)
    elif mutation == "duplicate-readkey":
        evidence["source_keys_read"].append(EXTERNAL)
    elif mutation == "invented-readkey":
        evidence["source_keys_read"].append("fabricated")
    elif mutation == "wrong-value":
        cell["value"] = 8
    elif mutation == "bool-value":
        cell["value"] = True
    elif mutation == "wrong-unit":
        cell["unit"] = "duration_minutes"
    elif mutation == "wrong-relation":
        cell["relation"] = "min"
    elif mutation == "wrong-semantics":
        cell["semantics"] = "elapsed-24-hours"
    elif mutation == "wrong-offset":
        cell["base_tz_offset_min"] = 180
    elif mutation == "bool-offset":
        cell["base_tz_offset_min"] = False
    elif mutation == "missing-offset":
        del cell["base_tz_offset_min"]
    elif mutation == "unknown-schema":
        evidence["schema"] = "pairing-builder-thresholds/2"
    elif mutation == "unknown-reading":
        evidence["readings"]["max_duty_span_min"] = dict(cell)
    elif mutation == "extra-envelope":
        evidence["approved"] = True
    elif mutation == "missing-contract":
        del evidence["contract_bytes"]
    elif mutation == "merely-number":
        evidence["readings"][EXTERNAL] = 7
    with pytest.raises(tg.ThresholdGateError, match="pairing-builder"):
        compare(pkg, applied, evidence)


def test_rehashed_contract_missing_read_key_cannot_claim_reader_observation():
    pkg, applied = fixture()
    evidence, _ = observe(pkg)
    raw = json.dumps({"base_tz_offset_min": 0}).encode()
    evidence["contract_bytes"] = raw
    evidence["contract_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(tg.ThresholdGateError, match="source_keys_read"):
        compare(pkg, applied, evidence)


@pytest.mark.parametrize("part", ["model", "manifest"])
def test_old_observation_cannot_be_reused_with_changed_package(part):
    pkg, applied = fixture()
    evidence, _ = observe(pkg)
    pkg[part]["revision"] = "changed-after-observation"
    with pytest.raises(tg.ThresholdGateError, match="package identity"):
        compare(pkg, applied, evidence)


@pytest.mark.parametrize("evidence_present", [False, True])
def test_provider_spoof_cannot_supply_builder_reading(evidence_present):
    pkg, applied = fixture()
    evidence, _ = observe(pkg)
    with pytest.raises(tg.ThresholdGateError, match="external threshold in provider"):
        compare(pkg, applied, evidence if evidence_present else None, {EXTERNAL: 7})


@pytest.mark.parametrize("executor", [None, True, "", "external", "pairing-builder/2"])
def test_unknown_executor_refuses(executor):
    pkg, applied = fixture()
    applied[0]["executor"] = executor
    with pytest.raises(tg.ThresholdGateError, match="unknown threshold executor"):
        compare(pkg, applied)


def test_conflicting_executor_claims_refuse():
    pkg, applied = fixture()
    applied.append({**applied[0], "executor": "kotlin-provider"})
    with pytest.raises(tg.ThresholdGateError, match="conflicting threshold executors"):
        compare(pkg, applied)


def test_unknown_builder_threshold_does_not_expand_evidence_scope():
    pkg, applied = fixture()
    applied[0]["threshold"] = "max_duty_span_min"
    with pytest.raises(tg.ThresholdGateError, match="unsupported threshold"):
        compare(pkg, applied)


@pytest.mark.parametrize("explicit", [False, True])
def test_old_and_explicit_provider_contracts_keep_their_numeric_guard(explicit):
    pkg = {"model": {"rules": [{"id": "R", "kind": "HARD", "params": [
        {"name": "max_duty_duration", "value": 720, "unit": "duration_minutes", "relation": "max"}]}]}}
    applied = [{"threshold": "max_duty_span_min", "value": 720,
                "fromParams": ["max_duty_duration"], "rules": ["R"]}]
    if explicit:
        applied[0]["executor"] = "kotlin-provider"
    args = {"mapping": {"max_duty_duration": "max_duty_span_min"}, "applied": applied}
    healthy = tg.compare(pkg, {"max_duty_span_min": 720}, **args)
    assert healthy and all(r["verdict"] == tg.MATCH for r in healthy)
    broken = tg.compare(pkg, {"max_duty_span_min": 721}, **args)
    assert any(r["verdict"] == tg.DIVERGE for r in broken)


def test_mixed_provider_failure_remains_visible_beside_healthy_builder():
    pkg, applied = fixture()
    pkg["model"]["rules"].append({"id": "R-DUTY", "kind": "HARD", "params": [
        {"name": "max_duty_duration", "value": 720, "unit": "duration_minutes", "relation": "max"}]})
    applied.append({"threshold": "max_duty_span_min", "value": 720,
                    "fromParams": ["max_duty_duration"], "rules": ["R-DUTY"], "executor": "kotlin-provider"})
    evidence, _ = observe(pkg)
    rows = tg.compare(pkg, {"max_duty_span_min": 721, "base_tz_offset_min": 0},
        mapping={EXTERNAL: EXTERNAL, "max_duty_duration": "max_duty_span_min",
                 "base_tz_offset_min": "base_tz_offset_min"},
        applied=applied, builder_readings=evidence)
    assert all(r["verdict"] == tg.MATCH for r in rows if r["reading"] == EXTERNAL)
    assert any(r["verdict"] == tg.DIVERGE and r["reading"] == "max_duty_span_min" for r in rows)


def test_builder_evidence_requires_declared_external_channel():
    pkg, applied = fixture()
    evidence, _ = observe(pkg)
    del applied[0]["executor"]
    with pytest.raises(tg.ThresholdGateError, match="no declared external executor"):
        compare(pkg, applied, evidence, {"base_tz_offset_min": 0})


def test_rehashed_builder_offset_cannot_change_package_calendar_basis():
    pkg, applied = fixture()
    kwargs = {"mapping": {EXTERNAL: EXTERNAL, "base_tz_offset_min": "base_tz_offset_min"},
              "applied": applied}
    healthy, _ = observe(pkg, offset=0)
    rows = tg.compare(pkg, {"base_tz_offset_min": 0}, builder_readings=healthy, **kwargs)
    assert rows and all(r["verdict"] == tg.MATCH for r in rows)
    # The new contract, digest, measured cell and package digest all agree
    # internally. Only the independent package/provider offset exposes it.
    changed, changed_rules = observe(pkg, offset=180)
    assert changed_rules.base_tz_offset_min == 180
    start, end = datetime(2026, 10, 1, 20, 30), datetime(2026, 10, 7, 21, 30)
    assert changed_rules.pairing_days(start, end) == 8
    assert _cos_rules.load_bytes(healthy["contract_bytes"]).pairing_days(start, end) == 7
    with pytest.raises(tg.ThresholdGateError, match="calendar.*binding"):
        tg.compare(pkg, {"base_tz_offset_min": 0}, builder_readings=changed, **kwargs)


@pytest.mark.parametrize("mutation", ["missing-package-offset", "wrong-package-offset",
    "wrong-unit", "wrong-relation", "condition", "qualifier", "excluded", "soft-only",
    "missing-mapping", "wrong-mapping", "missing-applied", "wrong-applied", "bool-applied",
    "missing-from-param", "missing-rule", "missing-provider", "wrong-provider", "bool-provider"])
def test_calendar_binding_neighbours_fail_closed(mutation):
    pkg, applied = fixture()
    param = pkg["model"]["rules"][1]["params"][0]
    provider = {"base_tz_offset_min": 0}
    mapping = {EXTERNAL: EXTERNAL, "base_tz_offset_min": "base_tz_offset_min"}
    if mutation == "missing-package-offset":
        pkg["model"]["rules"].pop()
    elif mutation == "wrong-package-offset":
        param["value"] = 180
    elif mutation == "wrong-unit":
        param["unit"] = "count"
    elif mutation == "wrong-relation":
        param["relation"] = "max"
    elif mutation == "condition":
        param["condition"] = {"type": "base", "value": "PLY"}
    elif mutation == "qualifier":
        param["qualifier"] = "на базе"
    elif mutation == "excluded":
        pkg["model"]["rules"][1]["excluded_until_data"] = {"question": "Q-OFFSET"}
    elif mutation == "soft-only":
        pkg["model"]["rules"][1]["kind"] = "SOFT"
    elif mutation == "missing-mapping":
        del mapping["base_tz_offset_min"]
    elif mutation == "wrong-mapping":
        mapping["base_tz_offset_min"] = "another-offset"
    elif mutation == "missing-applied":
        applied.pop()
    elif mutation == "wrong-applied":
        applied[1]["value"] = 180
    elif mutation == "bool-applied":
        applied[1]["value"] = False
    elif mutation == "missing-from-param":
        applied[1]["fromParams"] = []
    elif mutation == "missing-rule":
        applied[1]["rules"] = []
    elif mutation == "missing-provider":
        provider.clear()
    elif mutation == "wrong-provider":
        provider["base_tz_offset_min"] = 180
    elif mutation == "bool-provider":
        provider["base_tz_offset_min"] = False
    # Rebind identity to the changed package, so rejection reaches semantics.
    evidence, _ = observe(pkg)
    with pytest.raises(tg.ThresholdGateError, match="calendar.*binding"):
        tg.compare(pkg, provider, mapping=mapping, applied=applied, builder_readings=evidence)


def test_explicit_def_offset_is_a_bound_calendar_not_a_default():
    pkg, applied = fixture()
    pkg["model"]["rules"][1]["kind"] = "DEF"
    evidence, _ = observe(pkg)
    rows = compare(pkg, applied, evidence)
    assert rows and all(r["verdict"] == tg.MATCH for r in rows)


@pytest.mark.parametrize("channel", ["pairing-builder", "kotlin-provider"])
@pytest.mark.parametrize("second_value", [7, 8])
def test_duplicate_same_executor_claims_do_not_hide_numeric_disagreement(channel, second_value):
    pkg, applied = fixture()
    applied[0]["executor"] = channel
    applied.append({**applied[0], "value": second_value})
    if channel == "pairing-builder":
        evidence, _ = observe(pkg)
        rows = compare(pkg, applied, evidence)
    else:
        rows = compare(pkg, applied, provider={EXTERNAL: 7, "base_tz_offset_min": 0})
    assert any(r["verdict"] == tg.DIVERGE for r in rows) is (second_value == 8)
    if second_value == 7:
        assert rows and all(r["verdict"] == tg.MATCH for r in rows)
