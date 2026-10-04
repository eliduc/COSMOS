"""Stand-world conditions retain meaning at the certificate boundary.

Pure adapter calls only: no solver, HTTP, LLM, catalogue mutation or verdict stub.
"""
import copy

import pytest

from vmp import stand_adapters as sa


@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False), ("yes", True), ("no", False),
    ("true", True), ("false", False), ("да", True), ("нет", False),
    (" YES ", True), (" FALSE ", False),
])
def test_extension_rule_emits_boolean_not_truthy_text(value, expected):
    world = {"params": {"duty_extension_cap": 960},
             "conditions": {"extension_consent": value,
                            "extension_bases": ["ST1", "ST2"]}}
    before = copy.deepcopy(world)
    rule = sa._rule_of(world, "duty_extension_penalty", {}, ["duty_extension_cap"])
    params = rule["template_fit"]["params"]
    assert params["extension_consent"]["value"] is expected
    assert params["extension_bases"]["value"] == ["ST1", "ST2"]
    assert params["duty_extension_cap"]["value"] == 960
    assert world == before


@pytest.mark.parametrize("value", [None, 0, 1, [], {}, "", "maybe", "false-ish"])
def test_ambiguous_consent_is_refused_without_guessing(value):
    world = {"conditions": {"extension_consent": value}}
    with pytest.raises(sa.StandWorldError, match="extension_consent"):
        sa._rule_of(world, "duty_extension_penalty", {}, [])


def test_absent_consent_is_not_synthesized_into_rule():
    rule = sa._rule_of({"params": {"duty_extension_cap": 960}},
                       "duty_extension_penalty", {}, ["duty_extension_cap"])
    assert "extension_consent" not in rule["template_fit"]["params"]


@pytest.mark.parametrize("value,expected", [
    (True, True), (False, False), ("yes", True), ("no", False),
    ("false", False), ("нет", False), ("да", True),
])
def test_actual_solution_and_rule_agree_on_consent(value, expected):
    world = {"form": "schedule",
             "crews": [{"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T1"]}],
             "flights": [{"id": "F1", "from": "ST1", "to": "ST2",
                          "dep": "2026-10-01T06:00", "arr": "2026-10-01T08:00",
                          "fleet_type": "T1"}],
             "params": {"crew_size": 1},
             "conditions": {"extension_consent": value}}
    original = copy.deepcopy(world)
    solution = sa.world_to_solution(world)
    assert len(solution["duties"]) == 1
    assert solution["duties"][0].get("extension_consent", False) is expected
    rule = sa._rule_of(world, "duty_extension_penalty", {}, [])
    assert rule["template_fit"]["params"]["extension_consent"]["value"] is expected
    assert world == original


@pytest.mark.parametrize("conditions", [[], "yes", 1])
def test_conditions_must_be_an_object(conditions):
    with pytest.raises(sa.StandWorldError, match="conditions"):
        sa._rule_of({"conditions": conditions}, "duty_extension_penalty", {}, [])
