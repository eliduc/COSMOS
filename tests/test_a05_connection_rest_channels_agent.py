"""A05 scalar connection/rest carriers, independent of the duty-only tests.

Oracle: P0-CONTRACTS-v1.13 sections 3.1(6), 3.7 and 12.5. A different
threshold is not execution proof merely because a channel is present.
A stricter scalar needs another applicable HARD rule, not an invented norm.

Real schema-valid landing/2 fixtures and real execution-manifest readers.
Registry coverage is explicit unit-fixture evidence, not stand approval.
Literal channel keys below follow pairing_input/builder, not PARAM_EXECUTION.

Scope is deliberately only unconditional applied + ordinary CLI scalars.
min_turnaround has condition=None (both areas), not a home/outstation collapse.
layover_tiers is [duty_upto, home, off, home_night, off_night, home_trans,
off_trans] per row, NOT one scalar. Explicit --rest-*-long, conditional
floors, tier selection, executors and actual package readiness are unmeasured.
No producer/receiver/manifest verdict is mocked; no service or solver runs.
"""
import copy

import pytest

from cdpm import model as m
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge  # noqa: F401 -- fixture
from vmp import execution_manifest as em


CHANNELS = pytest.mark.parametrize("family,name,relation,expected,applied_key,flag", [
    pytest.param("connection_windows", "max_sit_duration", "max", 240,
                 "day_break_threshold_min", "--sit-max", id="sit-max"),
    pytest.param("connection_windows", "min_turnaround", "min", 45,
                 "min_sit_min", "--sit-min", id="sit-min-global"),
    pytest.param("rest_floors", "min_rest_home", "min", 720,
                 "min_rest_home_min", "--rest-base", id="rest-home"),
    pytest.param("rest_floors", "min_rest_outstation", "min", 600,
                 "min_rest_outstation_min", "--rest-off", id="rest-outstation"),
])
DOORS = pytest.mark.parametrize("door", ["applied", "cli"])


def _case(tmp_path, monkeypatch, family, name, relation, expected):
    doc, rule = typed_rule(tmp_path, monkeypatch, family, {
        name: {"value": expected, "unit": "duration_minutes", "relation": relation,
               "condition": None, "quote": f"{expected} минут"},
    })
    rule.update(kind="HARD", zone="PAIRING",
                statement=f"Норма {name}: {relation} {expected} минут.")
    rule.pop("params", None)
    doc["rules"] = [rule]
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    return doc, rule


def _judge(rules, door, applied_key, flag, actual):
    before = copy.deepcopy(rules)
    if door == "applied":
        gaps = em.thresholds_gaps(rules, {applied_key: actual}, task="pairing")
        rows = None
    else:
        registry = {"rules": [{"rule_id": rule["id"], "verdict": "covered_full",
                               "evidence": "explicit unit fixture; not stand approval"}
                              for rule in rules]}
        result = em.build(rules, registry, "pairing", "feasibility-hard",
                          [flag, str(actual)], {}, None)
        gaps, rows = result["blocking"], result["rules"]
        assert rows[0]["passed"][0]["key"] == flag
        assert rows[0]["passed"][0]["value"] == str(actual)
    assert rules == before, "judgment must not rewrite a formulation rule"
    return gaps, rows


@CHANNELS
@DOORS
@pytest.mark.parametrize("delta", [0, -15, 15], ids=["exact", "below", "above"])
def test_scalar_connection_or_rest_matches_each_actual_carrier(
        tmp_path, monkeypatch, family, name, relation, expected, applied_key, flag, door, delta):
    _doc, rule = _case(tmp_path, monkeypatch, family, name, relation, expected)
    gaps, rows = _judge([rule], door, applied_key, flag, expected + delta)
    assert bool(gaps) is (delta != 0), (gaps, rows)
    if rows is not None:
        assert rows[0]["status"] == ("исполняется" if delta == 0 else "НЕ ИСПОЛНЯЕТСЯ")
    if gaps:
        assert "две правды" in gaps[0]["note"]
        assert str(expected) in gaps[0]["note"]
        assert str(expected + delta) in gaps[0]["note"]


@CHANNELS
@DOORS
def test_stricter_connection_or_rest_needs_an_applicable_hard_norm(
        tmp_path, monkeypatch, family, name, relation, expected, applied_key, flag, door):
    doc, rule = _case(tmp_path, monkeypatch, family, name, relation, expected)
    stricter = expected - 15 if relation == "max" else expected + 15
    other = copy.deepcopy(rule)
    other.update(id="R-EXPLAINS-STRICTER", statement=f"Применимая норма: {stricter} минут.")
    other["template_fit"]["params"][name].update(
        value=stricter, quote=f"{stricter} минут",
        source={"kind": "rule", "rule_id": other["id"]})
    doc["rules"] = [rule, other]
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []

    gaps, rows = _judge(doc["rules"], door, applied_key, flag, stricter)
    assert gaps == [], (gaps, rows)
    if rows is not None:
        assert rows[0]["status"] == rows[1]["status"] == "исполняется"
        assert rows[0]["passed"][0]["explained_by"] == other["id"]
        assert rows[0]["passed"][0]["comparison"] == "ужесточено правилом " + other["id"]

    # The same stronger value from another task is not a pairing explanation.
    other["zone"] = "ROSTERING"
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    gaps, rows = _judge(doc["rules"], door, applied_key, flag, stricter)
    assert any(gap["rule_id"] == rule["id"] for gap in gaps), (gaps, rows)
    if rows is not None:
        assert rows[0]["status"] == "НЕ ИСПОЛНЯЕТСЯ"
