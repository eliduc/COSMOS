"""A05 contract values through the real source-rules producer.

Typed schema-valid formulation, real contract construction and real manifest
judgment. Registry coverage is a unit fixture, not executor/stand approval.
The duty selector and the opposite rest side must not become this rule's
threshold. Nonuniform selected tiers remain explicitly uncheckable until
their scope mapping is proved; this file does not claim that support.
"""
import copy

import pytest

from cdpm import model as m
from test_a05_connection_rest_channels_agent import _case
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from vmp import execution_manifest as em, pairing_input


SIDES = pytest.mark.parametrize("name,expected,columns", [
    ("min_rest_home", 720, (1, 3, 5)),
    ("min_rest_outstation", 600, (2, 4, 6)),
])


def _contract():
    result = pairing_input.source_rules_contract(None, {
        "min_rest_home_min": 720,
        "min_rest_outstation_min": 600,
        "max_duty_span_min": 540,
    })
    assert result["layover_tiers"] == [[1440, 720, 600, 720, 600, 720, 600]]
    assert result["work_time_limit_by_tier"] == [540]
    return result


def _judge(rules, contract, args=()):
    before = copy.deepcopy((rules, contract))
    registry = {"rules": [{"rule_id": r["id"], "verdict": "covered_full"}
                           for r in rules]}
    result = em.build(rules, registry, "pairing", "feasibility-hard",
                      list(args), {}, contract)
    assert (rules, contract) == before
    return result


@SIDES
@pytest.mark.parametrize("delta", [0, -15, 15])
def test_real_rest_contract_compares_only_the_selected_side(
        tmp_path, monkeypatch, name, expected, columns, delta):
    _doc, rule = _case(tmp_path, monkeypatch, "rest_floors", name, "min", expected)
    contract = _contract()
    for column in columns:
        contract["layover_tiers"][0][column] += delta
    result = _judge([rule], contract)
    assert bool(result["blocking"]) is (delta != 0), result
    passed = result["rules"][0]["passed"][0]
    assert passed["key"] == "source-rules.json:layover_tiers"
    if delta:
        assert "две правды" in result["blocking"][0]["note"]
        assert str(expected) in result["blocking"][0]["note"]
        assert str(expected + delta) in result["blocking"][0]["note"]
    else:
        assert passed["comparison"] == "совпало"


@SIDES
@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("row_index", [0, 1])
def test_each_reachable_rest_cell_cannot_hide_behind_a_uniform_neighbor(
        tmp_path, monkeypatch, name, expected, columns, index, row_index):
    _doc, rule = _case(tmp_path, monkeypatch, "rest_floors", name, "min", expected)
    contract = _contract()
    if row_index:
        contract["layover_tiers"].append([2880, 720, 600, 720, 600, 720, 600])
    assert _judge([rule], contract)["blocking"] == []
    contract["layover_tiers"][row_index][columns[index]] = expected - 15
    result = _judge([rule], contract)
    assert result["blocking"]
    assert "не проверяемо" in result["blocking"][0]["note"]
    assert result["rules"][0]["status"] == "НЕ ИСПОЛНЯЕТСЯ"


@SIDES
@pytest.mark.parametrize("malformed", [[], {"A": 720}, [[1440, 720, 600]],
                                      [[1440, True, True, True, True, True, True]]])
def test_bad_rest_contract_shape_or_value_is_not_execution_proof(
        tmp_path, monkeypatch, name, expected, columns, malformed):
    _doc, rule = _case(tmp_path, monkeypatch, "rest_floors", name, "min", expected)
    contract = _contract()
    assert _judge([rule], contract)["blocking"] == []
    contract["layover_tiers"] = malformed
    result = _judge([rule], contract)
    assert result["blocking"]
    assert result["rules"][0]["status"] == "НЕ ИСПОЛНЯЕТСЯ"


@SIDES
@pytest.mark.parametrize("bad_door", ["cli", "contract"])
def test_matching_rest_carrier_cannot_hide_the_other_carrier_mismatch(
        tmp_path, monkeypatch, name, expected, columns, bad_door):
    _doc, rule = _case(tmp_path, monkeypatch, "rest_floors", name, "min", expected)
    contract = _contract()
    flag = "--rest-base" if name == "min_rest_home" else "--rest-off"
    args = [flag, str(expected)]
    assert _judge([rule], contract, args)["blocking"] == []
    if bad_door == "cli":
        args[1] = str(expected + 15)
    else:
        for column in columns:
            contract["layover_tiers"][0][column] += 15
    result = _judge([rule], contract, args)
    assert result["blocking"]
    assert len(result["rules"][0]["passed"]) == 2


@SIDES
def test_stricter_rest_table_needs_a_covered_rule_with_the_same_scope(
        tmp_path, monkeypatch, name, expected, columns):
    doc, rule = _case(tmp_path, monkeypatch, "rest_floors", name, "min", expected)
    other = copy.deepcopy(rule)
    other["id"] = "R-REST-STRICTER"
    other["template_fit"]["params"][name].update(
        value=expected + 15, quote=f"{expected + 15} минут",
        source={"kind": "rule", "rule_id": other["id"]})
    doc["rules"] = [rule, other]
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    contract = _contract()
    for column in columns:
        contract["layover_tiers"][0][column] += 15
    assert _judge([rule], contract)["blocking"]
    result = _judge(doc["rules"], contract)
    assert result["blocking"] == []
    assert result["rules"][0]["passed"][0]["explained_by"] == other["id"]


@pytest.mark.parametrize("delta", [0, -15, 15])
def test_real_turnaround_contract_compares_the_resulting_scalar(
        tmp_path, monkeypatch, delta):
    _doc, rule = _case(tmp_path, monkeypatch, "connection_windows",
                       "min_turnaround", "min", 45)
    contract = pairing_input.source_rules_contract(None, {"min_sit_min": 45 + delta})
    assert contract["min_sit_min"] == 45 + delta
    result = _judge([rule], contract)
    assert bool(result["blocking"]) is (delta != 0), result
