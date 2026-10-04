"""K2: actual stand landing/2 -> compatible params, without export or solve.

Uses the existing isolated vocabulary/catalogue fixture, not approved support.
The source is the synthetic structured world, never a solver's expected result.
Existing typed traces remain evidence: they must not be silently repaired.
"""
import copy

import pytest

from cdpm import export_task_package as tp, model as m
from template_catalog import landing
from vmp import stand_solver as ss
from test_stand_landing_v2_contract_agent import catalog, rule_of, world_with


CASES = [
    ("connection_windows", {"min_turnaround": 45, "max_sit_duration": 960},
     {"min_turnaround": (45, "duration_minutes", "min"),
      "max_sit_duration": (960, "duration_minutes", "max")}),
    ("duty_limits", {"max_duty_duration": 780},
     {"max_duty_duration": (780, "duration_minutes", "max")}),
    ("duty_extension_penalty", {"duty_extension_cap": 780, "long_duty_threshold": 720},
     {"duty_extension_cap": (780, "duration_minutes", "max"),
      "long_duty_threshold": (720, "duration_minutes", "max")}),
    ("night_series", {"night_duty_start": 1320, "night_duty_end": 360},
     {"night_duty_start": (1320, "time_of_day_minutes", "min"),
      "night_duty_end": (360, "time_of_day_minutes", "max")}),
    ("rolling_rest", {"weekly_rest_period": 10080, "weekly_rest_required": 2160},
     {"weekly_rest_period": (10080, "duration_minutes", "window"),
      "weekly_rest_required": (2160, "duration_minutes", "min")}),
    ("span_accumulators", {"max_weekly_block": 300, "max_monthly_block": 960},
     {"max_weekly_block": (300, "duration_minutes", "max"),
      "max_monthly_block": (960, "duration_minutes", "max")}),
]


def _model(catalog, family, values):
    world = world_with(values)
    rule = rule_of(catalog, family, world)
    original = copy.deepcopy((rule, world))
    doc = ss.model_doc([rule], world=world, family=family)
    assert (rule, world) == original
    typed = doc["rules"][0]
    assert typed["template_fit"]["schema_version"] == "landing/2"
    assert landing.problems(typed, catalog) == []
    assert m.validate_schema(doc) == []
    assert m.validate_template_fit(doc) == []
    return world, doc


@pytest.mark.parametrize("family,values,expected", CASES,
                         ids=[case[0] for case in CASES])
def test_generated_time_trace_reaches_actual_projection(catalog, family, values, expected):
    world, doc = _model(catalog, family, values)
    typed = doc["rules"][0]
    original = copy.deepcopy((world, doc))
    try:
        projected = tp.derive_params_from_landing(typed)
    finally:
        assert (world, doc) == original
    assert {p["name"]: (p["value"], p["unit"], p["relation"])
            for p in projected} == expected
    for p in projected:
        assert m.validate_param(p, typed["kind"]) == [], p
        assert m.canon_minutes(p["as_written"]) == p["value"], p
        cell = typed["template_fit"]["params"][p["name"]]
        assert cell["value"] == values[p["name"]]
        assert cell["source"] == {
            "kind": "code", "note": f"vmp.stand_adapters: synthetic world.params.{p['name']}"}
        if p["unit"] == "time_of_day_minutes":
            assert p["tz"] == cell["tz"] == "base-local"


@pytest.mark.parametrize("family,name,value,quote", [
    ("connection_windows", "min_turnaround", 45, "45 минут"),
    ("rolling_rest", "weekly_rest_period", 10080, "10080 мин"),
    ("night_series", "night_duty_end", 360, "06:00"),
])
def test_existing_typed_source_and_explicit_trace_are_preserved(
        catalog, family, name, value, quote):
    world, doc = _model(catalog, family, {name: value})
    typed = doc["rules"][0]
    cell = typed["template_fit"]["params"][name]
    cell["quote"] = quote
    cell["source"] = {"kind": "code", "note": "existing synthetic explicit time source"}
    before = copy.deepcopy((typed, world))
    reread = ss.model_doc([typed], world=world, family=family)
    assert reread["rules"][0]["template_fit"] == typed["template_fit"]
    projected = tp.derive_params_from_landing(reread["rules"][0])
    assert len(projected) == 1
    p = projected[0]
    assert (p["name"], p["value"], p["as_written"]) == (name, value, quote)
    assert m.validate_param(p, typed["kind"]) == []
    assert (typed, world) == before


@pytest.mark.parametrize("quote,words", [
    ("45", "нет явной единицы времени"),
    ("46 минут", "канонизация не сходится"),
])
def test_existing_damaged_trace_is_not_repaired_from_typed_unit(catalog, quote, words):
    world, doc = _model(catalog, "connection_windows", {"min_turnaround": 45})
    typed = doc["rules"][0]
    typed["template_fit"]["params"]["min_turnaround"]["quote"] = quote
    before = copy.deepcopy((typed, world))
    reread = ss.model_doc([typed], world=world, family="connection_windows")
    assert reread["rules"][0]["template_fit"] == typed["template_fit"]
    assert landing.problems(reread["rules"][0], catalog) == []
    with pytest.raises(tp.TaskPackageError, match=words):
        tp.derive_params_from_landing(reread["rules"][0])
    assert (typed, world) == before


@pytest.mark.parametrize("value", [45.5, True], ids=["fraction", "boolean"])
def test_noncanonical_time_value_is_refused_not_formatted(catalog, value):
    world = world_with({"min_turnaround": value})
    rule = rule_of(catalog, "connection_windows", world)
    before = copy.deepcopy((rule, world))
    with pytest.raises(ss.StandSolverError, match="value"):
        ss.model_doc([rule], world=world, family="connection_windows")
    assert (rule, world) == before


def test_count_trace_does_not_acquire_time_units(catalog):
    world, doc = _model(catalog, "crew_composition", {"crew_size": 2})
    before = copy.deepcopy((world, doc))
    projected = tp.derive_params_from_landing(doc["rules"][0])
    assert [(p["name"], p["value"], p["unit"], p["as_written"])
            for p in projected] == [("crew_size", 2, "count", "2")]
    assert m.validate_param(projected[0], doc["rules"][0]["kind"]) == []
    assert (world, doc) == before
