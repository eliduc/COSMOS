"""Finite wire-contract tests, not native measurements or support evidence.

These in-memory observations never run a JVM, approve a catalogue, or grant
a task mode. Real native inputs, measured provider readings and their identity
census must still be supplied through vmp.stand_components.load_evidence.
"""
import copy

import pytest

from template_catalog.stand_contracts import component_expectation_problem
from vmp.stand_components import Evidence


def reading(param, threshold, unit, relation, value, *, converted=None,
            value_unit=None):
    resolved = value if converted is None else converted
    return {"param": param, "threshold": threshold, "unit": unit,
            "relation": relation, "input_value": copy.deepcopy(value),
            "value_unit": value_unit or unit, "value": copy.deepcopy(resolved),
            "provider_value": copy.deepcopy(resolved)}


READINGS = {
    "balanced_workload": [reading("workload_spread_threshold",
        "workload_spread_threshold_min", "credit_hours", "max", 5,
        converted=300, value_unit="duration_minutes")],
    "crew_composition": [reading("crew_size", "crew_size", "count", "eq", 2)],
    "fleet_exclusions": [reading("fleet_types", "fleet_types", "code_list", None,
        ["T2"])],
    "reserve_floors": [reading("reserve_crews", "reserve_crews_min", "count", "min",
        {"ST1": 1})],
    "rest_floors": [reading("min_rest_home", "min_rest_home_min",
        "duration_minutes", "min", 660), reading("min_rest_outstation",
        "min_rest_outstation_min", "duration_minutes", "min", 700)],
    "time_scale": [reading("base_tz_offset_min", "base_tz_offset_min",
        "duration_minutes", "eq", 0)],
}
REFUSAL = {"profile": "typed-knob-refusal/1",
           "param": "base_tz_offset_min", "unit": "duration_minutes",
           "relation": "eq", "input_value": 60, "refused": True,
           "reason": "nonzero_scale_offset"}


def profile(family):
    return {"profile": "typed-knob-readings/1", "readings": copy.deepcopy(READINGS[family]),
            "refusals": []}


def problem(value):
    return component_expectation_problem("kotlin_knob", value)


@pytest.mark.parametrize("family", READINGS)
def test_closed_healthy_profile_and_input_are_preserved(family):
    value = profile(family)
    before = copy.deepcopy(value)
    assert problem(value) is None
    assert value == before


def test_original_duty_wire_remains_valid_without_new_envelope():
    legacy = {"param": "max_duty_duration", "threshold": "max_duty_span_min",
              "unit": "duration_minutes", "relation": "max", "value": 780,
              "provider_value": 780}
    assert problem(legacy) is None
    for field in ("value", "provider_value"):
        damaged = {**legacy, field: True}
        assert problem(damaged)


@pytest.mark.parametrize("field", ["profile", "readings", "refusals"])
def test_missing_profile_fields_are_not_optional(field):
    value = profile("crew_composition")
    del value[field]
    assert problem(value)


@pytest.mark.parametrize("damage", ["extra", "unknown", "empty", "not_list", "duplicate",
                                   "mixed", "not_object", "unknown_param"])
def test_profile_inventory_is_closed(damage):
    value = profile("crew_composition")
    if damage == "extra": value["passed"] = True
    elif damage == "unknown": value["profile"] = "anything/1"
    elif damage == "empty": value["readings"] = []
    elif damage == "not_list": value["readings"] = {}
    elif damage == "duplicate": value["readings"] *= 2
    elif damage == "mixed": value["readings"] += copy.deepcopy(READINGS["fleet_exclusions"])
    elif damage == "not_object": value["readings"] = [None]
    else: value["readings"][0]["param"] = "invented_knob"
    assert problem(value)


@pytest.mark.parametrize("field", list(READINGS["crew_composition"][0]))
def test_every_typed_reading_field_is_required(field):
    value = profile("crew_composition")
    del value["readings"][0][field]
    assert problem(value)


@pytest.mark.parametrize("field,value", [("threshold", "min_rest_home_min"),
    ("unit", "duration_minutes"), ("relation", "max"), ("value_unit", "credit_hours"),
    ("param", None), ("param", []), ("value", 3), ("provider_value", 3),
    ("input_value", True), ("value", 2.0), ("provider_value", "2"),
    ("passed", True)])
def test_identity_units_types_and_provider_consumption_are_not_inferred(field, value):
    document = profile("crew_composition")
    document["readings"][0][field] = value
    assert problem(document)


@pytest.mark.parametrize("bad", [True, 1.5, "2", None, 0, -1, 2147483648])
def test_integer_knob_range_and_type_are_exact(bad):
    value = profile("crew_composition")
    for field in ("input_value", "value", "provider_value"):
        value["readings"][0][field] = bad
    assert problem(value)


def test_workload_conversion_is_explicit_and_zero_is_not_missing():
    value = profile("balanced_workload")
    row = value["readings"][0]
    row.update(input_value=0, value=0, provider_value=0)
    assert problem(value) is None
    row.update(input_value=5, value=5, provider_value=5)
    assert problem(value), "5 credit hours must not be measured as 5 minutes"
    row.update(value=300, provider_value=300, value_unit="credit_hours")
    assert problem(value), "a conversion without the resolved unit is ambiguous"
    row.update(value_unit="duration_minutes", input_value=35791395,
               value=2147483700, provider_value=2147483700)
    assert problem(value), "native integer overflow is not a valid measurement"


@pytest.mark.parametrize("field", ["input_value", "value", "provider_value"])
@pytest.mark.parametrize("bad", [1, [], [""], [" T2"], ["T2", "T2"],
                                [False], ["T2", "T1"]])
def test_fleet_is_exact_canonical_codes_not_a_count_or_coerced_list(field, bad):
    value = profile("fleet_exclusions")
    value["readings"][0][field] = bad
    assert problem(value)


def test_fleet_same_count_different_labels_is_refused():
    value = profile("fleet_exclusions")
    value["readings"][0]["provider_value"] = ["T1"]
    assert problem(value)
    value["readings"][0]["provider_value"] = ["T2"]
    assert problem(value) is None


@pytest.mark.parametrize("field", ["input_value", "value", "provider_value"])
@pytest.mark.parametrize("bad", [1, {}, {"ST1": True}, {"ST1": "1"}, {"ST1": 0},
                                {"ST1": -1}, {"": 1}, {" ST1": 1}, {"ST2": 1}])
def test_reserve_requires_each_exact_base_and_positive_native_count(field, bad):
    value = profile("reserve_floors")
    value["readings"][0][field] = bad
    assert problem(value)


def test_multiple_reserve_bases_are_not_summed_or_lost():
    value = profile("reserve_floors")
    row = value["readings"][0]
    for key in ("input_value", "value", "provider_value"):
        row[key] = {"ST1": 1, "ST2": 2}
    assert problem(value) is None
    row["provider_value"] = {"ST1": 3}
    assert problem(value)


def test_reserve_equal_relation_is_explicitly_supported_but_maximum_is_not():
    value = profile("reserve_floors")
    value["readings"][0]["relation"] = "eq"
    assert problem(value) is None
    value["readings"][0]["relation"] = "max"
    assert problem(value)


@pytest.mark.parametrize("damage", ["missing_home", "missing_outstation", "duplicate_home",
                                   "swapped_threshold", "copied_provider"])
def test_rest_profile_proves_both_distinct_readings(damage):
    value = profile("rest_floors")
    if damage == "missing_home": value["readings"].pop(0)
    elif damage == "missing_outstation": value["readings"].pop()
    elif damage == "duplicate_home": value["readings"][1] = copy.deepcopy(value["readings"][0])
    elif damage == "swapped_threshold": value["readings"][1]["threshold"] = "min_rest_home_min"
    else: value["readings"][1]["provider_value"] = 660
    assert problem(value)


def test_time_scale_positive_profile_accepts_only_the_zero_reading():
    value = profile("time_scale")
    assert problem(value) is None
    for key in ("input_value", "value", "provider_value"):
        value["readings"][0][key] = 60
    assert problem(value)


def test_separately_bound_nonzero_refusal_is_a_valid_negative_observation():
    value = copy.deepcopy(REFUSAL)
    assert problem(value) is None
    assert value == REFUSAL


@pytest.mark.parametrize("field,replacement", [
    ("profile", "unknown/1"), ("param", "crew_size"), ("unit", "count"),
    ("relation", "max"), ("input_value", 0), ("input_value", True),
    ("input_value", 60.0), ("input_value", "60"), ("input_value", -60),
    ("refused", False), ("refused", 1), ("reason", "any failure"),
    ("approved", True), ("readings", []),
])
def test_nonzero_refusal_is_exact_typed_and_cannot_hide_unrelated_failure(field, replacement):
    value = copy.deepcopy(REFUSAL)
    value[field] = replacement
    assert problem(value)


@pytest.mark.parametrize("field", list(REFUSAL))
def test_refusal_fields_cannot_be_omitted(field):
    value = copy.deepcopy(REFUSAL)
    del value[field]
    assert problem(value)


@pytest.mark.parametrize("family", ["crew_composition", "time_scale"])
def test_refusal_cannot_be_attached_to_another_inputs_reading(family):
    value = profile(family)
    value["refusals"] = [copy.deepcopy(REFUSAL)]
    assert problem(value)
    value["refusals"] = {}
    assert problem(value)


def test_typed_knob_does_not_approve_score_or_execution_kind():
    for value in (profile("crew_composition"), REFUSAL):
        assert component_expectation_problem("kotlin_constraint", value)
        assert component_expectation_problem("kotlin_form", value)


@pytest.mark.parametrize("family", READINGS)
def test_existing_evidence_judge_compares_exact_observation_to_declared_oracle(family):
    expected = profile(family)
    world = {"form": "schedule", "params": {"protocol_only": True}}
    key = ("families/" + family, "protocol-only")
    source = {"world": copy.deepcopy(world), "expected": {"kotlin_knob": copy.deepcopy(expected)}}
    got = copy.deepcopy(expected)
    evidence = Evidence({}, {key: source}, {(*key, "kotlin_knob"): got})
    assert evidence.judge("kotlin_knob", expected, *key, world) == expected
    # Internally valid but different readings still fail the independent
    # declared-oracle comparison, including equal-count code-list substitution.
    row = got["readings"][0]
    if family == "fleet_exclusions":
        for name in ("input_value", "value", "provider_value"): row[name] = ["T1"]
    elif family == "reserve_floors":
        for name in ("input_value", "value", "provider_value"): row[name] = {"ST2": 1}
    elif family == "time_scale": row["provider_value"] = 1
    else:
        row["input_value"] += 1
        amount = 60 if family == "balanced_workload" else 1
        row["value"] += amount
        row["provider_value"] += amount
    with pytest.raises(ValueError):
        evidence.judge("kotlin_knob", expected, *key, world)


def test_positive_and_negative_time_scale_observations_cannot_cross_task_identity():
    record = "families/time_scale"
    positive, negative = profile("time_scale"), copy.deepcopy(REFUSAL)
    zero_world = {"params": {"base_tz_offset_min": 0}}
    nonzero_world = {"params": {"base_tz_offset_min": 60}}
    zero_key, nonzero_key = (record, "st-scale-0"), (record, "st-scale-nonzero-1")
    declared = {zero_key: {"world": zero_world, "expected": {"kotlin_knob": positive}},
                nonzero_key: {"world": nonzero_world, "expected": {"kotlin_knob": negative}}}
    observed = {(*zero_key, "kotlin_knob"): positive,
                (*nonzero_key, "kotlin_knob"): negative}
    evidence = Evidence({}, declared, observed)
    assert evidence.judge("kotlin_knob", positive, *zero_key, zero_world) == positive
    assert evidence.judge("kotlin_knob", negative, *nonzero_key, nonzero_world) == negative
    with pytest.raises(ValueError, match="world changed"):
        evidence.judge("kotlin_knob", negative, *nonzero_key, zero_world)
    observed[(*nonzero_key, "kotlin_knob")] = positive
    with pytest.raises(ValueError, match="differs"):
        evidence.judge("kotlin_knob", negative, *nonzero_key, nonzero_world)
    observed[(*zero_key, "kotlin_knob")] = negative
    with pytest.raises(ValueError, match="differs"):
        evidence.judge("kotlin_knob", positive, *zero_key, zero_world)
