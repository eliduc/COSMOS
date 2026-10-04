"""Strict export-form stand oracle; no approval or catalogue mutation."""
import copy
import importlib

import pytest

cv = importlib.import_module("template_catalog.validate")


def expected():
    return {"symbol": "worktime_exact", "probes": [
        {"minute": 360, "landings": 1, "acclimatized": True,
         "fleet": "T38", "value_min": 780}]}


def problems(exp, *, declared=True):
    ctx = cv._Ctx({"stand_task_kinds": ["export_form"] if declared else ["builder_method"],
                   "world_forms": {"schedule": {"fields": []}}}, [])
    record = {"executor_kinds": ["export_form"], "stand_tasks": [{
        "id": "explicit-export-unit", "world": {"form": "schedule"},
        "expected": {"export_form": exp}}]}
    before = copy.deepcopy(record)
    cv._stand_tasks_check(ctx, "families/duty_limits", record, "executor_kinds")
    assert record == before
    return ctx.problems


def test_explicit_healthy_oracle_and_old_dictionary_remains_closed():
    assert not problems(expected())
    assert any(p.code == "X05" for p in problems(expected(), declared=False))


@pytest.mark.parametrize("damage", ["empty", "none", "unknown_symbol", "missing_symbol",
    "extra", "empty_probes", "null_probes", "not_list", "empty_probe", "extra_probe",
    "minute_bool", "minute_fraction", "minute_negative", "minute_end", "landings_bool",
    "landings_zero", "acclimatized_number", "fleet_empty", "fleet_spaces",
    "value_bool", "value_fraction", "value_zero", "value_inf"])
def test_malformed_export_oracle_is_never_accepted(damage):
    exp = expected()
    if damage == "empty": exp = {}
    elif damage == "none": exp = None
    elif damage == "unknown_symbol": exp["symbol"] = "pretend"
    elif damage == "missing_symbol": exp.pop("symbol")
    elif damage == "extra": exp["passed"] = True
    elif damage == "empty_probes": exp["probes"] = []
    elif damage == "null_probes": exp["probes"] = None
    elif damage == "not_list": exp["probes"] = {}
    elif damage == "empty_probe": exp["probes"] = [{}]
    elif damage == "extra_probe": exp["probes"][0]["passed"] = True
    else:
        key, value = {"minute_bool": ("minute", True), "minute_fraction": ("minute", 360.5),
            "minute_negative": ("minute", -1), "minute_end": ("minute", 1440),
            "landings_bool": ("landings", True), "landings_zero": ("landings", 0),
            "acclimatized_number": ("acclimatized", 1), "fleet_empty": ("fleet", ""),
            "fleet_spaces": ("fleet", "  "), "value_bool": ("value_min", True),
            "value_fraction": ("value_min", 780.5), "value_zero": ("value_min", 0),
            "value_inf": ("value_min", float("inf"))}[damage]
        exp["probes"][0][key] = value
    assert any(p.code == "X05" for p in problems(exp))
