"""A05 count thresholds across applied, CLI and scalar contract channels.

Real schema-valid landing/2 fixtures and execution-manifest readers only.
covered_full is explicit unit fixture evidence, never execution/stand approval.
The pairing-days CLI assertion checks count * 1440 transmission, not whether
the calendar-day executor is correct. No executor, service or process runs.
Expectations below are literals, not derived from PARAM_EXECUTION or a judge.
"""
import copy

import pytest

from cdpm import model as m
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge  # noqa: F401 -- fixture
from vmp import execution_manifest as em


PARAMETERS = pytest.mark.parametrize("name,expected,flag,cli_scale", [
    pytest.param("max_landings_per_duty", 4, "--max-segs", 1, id="landings"),
    pytest.param("max_pairing_days", 3, "--pairing-span", 1440, id="pairing-days"),
])
DOORS = pytest.mark.parametrize("door", ["applied", "cli", "contract"])


def _case(tmp_path, monkeypatch, name, expected):
    doc, rule = typed_rule(tmp_path, monkeypatch, "duty_limits", {
        name: {"value": expected, "unit": "count", "relation": "max",
               "condition": None, "quote": str(expected)},
    })
    rule.update(kind="HARD", zone="PAIRING",
                statement=f"Предел счётчика {name}: {expected}.")
    rule.pop("params", None)
    doc["rules"] = [rule]  # No second HARD rule can explain a stricter value.
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    return doc, rule


def _registry(rule):
    return {"rules": [{"rule_id": rule["id"], "verdict": "covered_full",
                       "evidence": "explicit unit fixture; not stand approval"}]}


def _wire(door, value, cli_scale):
    if door != "cli" or isinstance(value, bool):
        return value  # Do not hide a bool by coercing it into a number/string.
    return str(value * cli_scale)


def _judge(rule, name, door, flag, actual, *, args=None):
    before = copy.deepcopy(rule)
    if door == "applied":
        gaps = em.thresholds_gaps([rule], {name: actual})
        row = None
    else:
        bargs = (args if args is not None else [flag, actual]) if door == "cli" else []
        contract = {name: actual} if door == "contract" else None
        result = em.build([rule], _registry(rule), "pairing", "feasibility-hard",
                          bargs, {}, contract)
        assert len(result["rules"]) == 1
        gaps, row = result["blocking"], result["rules"][0]
    assert rule == before, "execution judgment must not rewrite the norm"
    return gaps, row


def _expect(gaps, row, accepted):
    assert bool(gaps) is (not accepted), (gaps, row)
    if row is not None:
        assert row["status"] == ("исполняется" if accepted else "НЕ ИСПОЛНЯЕТСЯ"), row


@PARAMETERS
@DOORS
@pytest.mark.parametrize("delta", [0, 1, -1], ids=["exact", "weaker", "stricter"])
def test_count_threshold_matches_the_value_at_each_channel(
        tmp_path, monkeypatch, name, expected, flag, cli_scale, door, delta):
    _doc, rule = _case(tmp_path, monkeypatch, name, expected)
    actual = _wire(door, expected + delta, cli_scale)
    gaps, row = _judge(rule, name, door, flag, actual)
    _expect(gaps, row, delta == 0)
    if row is not None:
        assert row["passed"][0]["value"] == actual
    if gaps:
        assert "две правды" in gaps[0]["note"], gaps


@PARAMETERS
@DOORS
@pytest.mark.parametrize("bad", ["bool", "fraction"])
def test_count_channel_rejects_bool_and_fraction(
        tmp_path, monkeypatch, name, expected, flag, cli_scale, door, bad):
    # True == 1 in Python: the rejection must be typed, not an accidental
    # mismatch against 3 or 4. The landing cell remains a healthy integer.
    expected = 1 if bad == "bool" else expected
    _doc, rule = _case(tmp_path, monkeypatch, name, expected)
    healthy = _wire(door, expected, cli_scale)
    _expect(*_judge(rule, name, door, flag, healthy), accepted=True)
    actual = _wire(door, True if bad == "bool" else expected + 0.5, cli_scale)
    _expect(*_judge(rule, name, door, flag, actual), accepted=False)


@PARAMETERS
@pytest.mark.parametrize("change", ["relation", "condition"])
def test_cli_presence_does_not_prove_wrong_relation_or_scope(
        tmp_path, monkeypatch, name, expected, flag, cli_scale, change):
    doc, rule = _case(tmp_path, monkeypatch, name, expected)
    actual = _wire("cli", expected, cli_scale)
    _expect(*_judge(rule, name, "cli", flag, actual), accepted=True)
    cell = rule["template_fit"]["params"][name]
    if change == "relation":
        cell["relation"] = "min"
    else:
        cell["condition"] = {"kind": "area", "value": "база"}
    # Structural landing/2 remains valid. Its altered semantics cannot become
    # scalar proof even if a caller bypasses the earlier template-fit gate.
    assert m.validate_schema(doc) == []
    _expect(*_judge(rule, name, "cli", flag, actual), accepted=False)


@PARAMETERS
@pytest.mark.parametrize("last_exact", [True, False], ids=["last-exact", "last-wrong"])
def test_last_cli_occurrence_is_the_value_judged(
        tmp_path, monkeypatch, name, expected, flag, cli_scale, last_exact):
    _doc, rule = _case(tmp_path, monkeypatch, name, expected)
    exact = _wire("cli", expected, cli_scale)
    wrong = _wire("cli", expected + 1, cli_scale)
    first, last = (wrong, exact) if last_exact else (exact, wrong)
    gaps, row = _judge(rule, name, "cli", flag, last,
                       args=[flag, first, flag, last])
    assert row["passed"][0]["value"] == last
    _expect(gaps, row, last_exact)


@PARAMETERS
@DOORS
def test_stricter_count_requires_applicable_hard_rule(
        tmp_path, monkeypatch, name, expected, flag, cli_scale, door):
    _doc, rule = _case(tmp_path, monkeypatch, name, expected)
    lower = copy.deepcopy(rule)
    lower["id"] = "independent-lower-count"
    lower["template_fit"]["params"][name]["value"] = expected - 1
    actual = _wire(door, expected - 1, cli_scale)

    def judge():
        rules = [rule, lower]
        if door == "applied":
            return em.thresholds_gaps(rules, {name: actual})
        registry = {"rules": [*_registry(rule)["rules"], *_registry(lower)["rules"]]}
        return em.build(rules, registry, "pairing", "feasibility-hard",
                        [flag, actual] if door == "cli" else [], {},
                        {name: actual} if door == "contract" else None)["blocking"]

    assert judge() == [], "a covered global HARD rule explains the stricter value"
    lower["zone"] = "ROSTERING"
    assert any(g["rule_id"] == rule["id"] for g in judge()), "other task cannot explain it"
