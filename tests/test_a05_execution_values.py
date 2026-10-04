"""A05: the execution manifest must compare the transmitted threshold.

No executor is run. Registry coverage is explicit fixture evidence; the real
manifest judges real typed landing cells and the exact supplied CLI arguments.
This is a unit boundary, not the joint stand or a completed execution proof.
"""
import copy

import pytest

from cdpm import model as m
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge
from vmp import execution_manifest as em


@pytest.mark.parametrize("sent", [720, 780, 660])
def test_typed_duty_limit_compares_actual_cli_value(tmp_path, monkeypatch, sent):
    doc, rule = typed_rule(tmp_path, monkeypatch, "duty_limits", {
        "max_duty_duration": {
            "value": 720, "unit": "duration_minutes", "relation": "max",
            "condition": None, "quote": "720 минут",
        },
    })
    rule.update(kind="HARD", zone="PAIRING", statement="Рабочая смена не более 720 минут.")
    rule.pop("params", None)
    doc["rules"] = [rule]
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    original = copy.deepcopy(rule)
    registry = {"rules": [{"rule_id": rule["id"], "verdict": "covered_full",
                           "evidence": "explicit unit fixture; not stand approval"}]}
    result = em.build([rule], registry, "pairing", "feasibility-hard",
                      ["--duty-span", str(sent)], {}, None)
    row = result["rules"][0]
    assert row["passed"][0]["value"] == str(sent)
    assert rule == original
    if sent == 720:
        assert result["blocking"] == []
        assert row["status"] == "исполняется"
    else:
        # Even a stricter threshold is not automatically authorised:
        # there is no other formulation rule explaining this change.
        assert result["blocking"], result
        assert row["status"] == "НЕ ИСПОЛНЯЕТСЯ"
        assert "две правды" in row["note"]


def _case(tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, "duty_limits", {
        "max_duty_duration": {"value": 720, "unit": "duration_minutes",
                              "relation": "max", "condition": None,
                              "quote": "720 минут"}})
    rule.update(kind="HARD", zone="PAIRING")
    rule.pop("params", None)
    doc["rules"] = [rule]
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    return rule


@pytest.mark.parametrize("door", ["applied", "contract"])
@pytest.mark.parametrize("sent", [720, 780, 660])
def test_value_is_checked_at_each_duty_transport_door(tmp_path, monkeypatch, door, sent):
    rule = _case(tmp_path, monkeypatch)
    if door == "applied":
        gaps = em.thresholds_gaps([rule], {"max_duty_span_min": sent})
    else:
        registry = {"rules": [{"rule_id": rule["id"], "verdict": "covered_full"}]}
        result = em.build([rule], registry, "pairing", "feasibility-hard", [], {},
                          {"work_time_limit_by_tier": [sent]})
        gaps = result["blocking"]
    assert bool(gaps) == (sent != 720), gaps
    if gaps:
        assert "720" in gaps[0]["note"] and str(sent) in gaps[0]["note"]


@pytest.mark.parametrize("change", ["wrong_relation", "foreign_scope", "flag_value"])
def test_cli_presence_cannot_prove_unsupported_semantics(tmp_path, monkeypatch, change):
    rule = _case(tmp_path, monkeypatch)
    cell = rule["template_fit"]["params"]["max_duty_duration"]
    sent = "720"
    if change == "wrong_relation":
        cell["relation"] = "min"
    elif change == "foreign_scope":
        cell["condition"] = {"kind": "area", "value": "база"}
    else:
        sent = True
    # These defensive helper cases are deliberately not schema-valid models:
    # they must not become value proof if a caller bypasses the earlier gate.
    registry = {"rules": [{"rule_id": rule["id"], "verdict": "covered_full"}]}
    result = em.build([rule], registry, "pairing", "feasibility-hard",
                      ["--duty-span", sent], {}, None)
    assert result["blocking"], result


@pytest.mark.parametrize("other_kind,other_zone,covered,expected_ok", [
    ("HARD", "PAIRING", True, True),
    ("SOFT", "PAIRING", True, False),
    ("HARD", "ROSTERING", True, False),
    ("HARD", "PAIRING", False, False),
])
def test_stricter_cli_needs_an_applicable_covered_formulation_rule(
        tmp_path, monkeypatch, other_kind, other_zone, covered, expected_ok):
    rule = _case(tmp_path, monkeypatch)
    other = copy.deepcopy(rule)
    other.update(id="R-STRICTER", kind=other_kind, zone=other_zone)
    cell = other["template_fit"]["params"]["max_duty_duration"]
    cell.update(value=660, quote="660 минут", source={"kind": "rule", "rule_id": other["id"]})
    registry = {"rules": [{"rule_id": rule["id"], "verdict": "covered_full"},
                           {"rule_id": other["id"],
                            "verdict": "covered_full" if covered else "missing"}]}
    result = em.build([rule, other], registry, "pairing", "feasibility-hard",
                      ["--duty-span", "660"], {}, None)
    row = result["rules"][0]
    assert (row["status"] == "исполняется") is expected_ok, row
    if expected_ok:
        assert row["passed"][0]["explained_by"] == other["id"]


def test_applied_aliases_cannot_hide_a_conflicting_value(tmp_path, monkeypatch):
    rule = _case(tmp_path, monkeypatch)
    gaps = em.thresholds_gaps([rule], {"max_duty_span_min": 720,
                                       "max_duty_duration_min": 780})
    assert gaps and "780" in gaps[0]["note"]


def test_soft_duty_parameter_remains_out_of_hard_threshold_judgment(tmp_path, monkeypatch):
    rule = _case(tmp_path, monkeypatch)
    rule["kind"] = "SOFT"
    assert em.thresholds_gaps([rule], {"max_duty_span_min": 780}) == []


@pytest.mark.parametrize("sent", ["720.0", "12:00", "12 часов"])
def test_cli_must_be_an_integer_argument_not_just_a_convertible_quantity(
        tmp_path, monkeypatch, sent):
    # _pairing_builder declares --duty-span with argparse type=int. Quantity
    # normalization of a written norm does not change that transport contract.
    rule = _case(tmp_path, monkeypatch)
    registry = {"rules": [{"rule_id": rule["id"], "verdict": "covered_full"}]}
    healthy = em.build([rule], registry, "pairing", "feasibility-hard",
                       ["--duty-span", "720"], {}, None)
    assert healthy["blocking"] == []
    with pytest.raises(ValueError):
        int(sent)
    result = em.build([rule], registry, "pairing", "feasibility-hard",
                      ["--duty-span", sent], {}, None)
    assert result["blocking"], "a quantity the actual CLI parser rejects is not executable"
    assert result["rules"][0]["status"] == "НЕ ИСПОЛНЯЕТСЯ"
