"""Explicit invalid relations must not turn into an implicit valid default.

Synthetic dictionaries only; these tests do not run live models or services.
"""
import copy

import pytest

from cdpm import model as m, export_task_package as tp
from test_cdpm_a05_code_projection import rule as code_rule
from test_cdpm_a05_base_map_projection import _rule as map_rule
from test_cdpm_a05_threshold_landing import _floor


def _case(kind):
    if kind in ("code", "code_list"):
        name = "fleet_type" if kind == "code" else "fleet_types"
        return code_rule(name, "Airbus A320"), name
    if kind == "numeric":
        return _floor(), "min_rest_home"
    return map_rule(), "reserve_crews"


@pytest.mark.parametrize("kind", ["code", "code_list", "numeric", "base_map"])
@pytest.mark.parametrize("bad", ["", " ", False, 0, [], {}, "bogus"])
def test_invalid_cell_relation_refuses_without_mutating_input(kind, bad):
    rule, name = _case(kind)
    rule["template_fit"]["params"][name]["relation"] = bad
    before = copy.deepcopy(rule)
    with pytest.raises(tp.TaskPackageError) as error:
        tp.derive_params_from_landing(rule)
    assert f"{rule['id']}/{name}" in error.value.items
    assert rule == before


@pytest.mark.parametrize("kind", ["code", "code_list"])
@pytest.mark.parametrize("bad", ["", " ", False, 0, [], {}, "bogus"])
def test_invalid_legacy_relation_refuses_without_replacement(kind, bad):
    rule, name = _case(kind)
    rule["params"] = [{"name": name, "value": "Airbus A320", "unit": kind,
                       "as_written": "Airbus A320", "relation": bad}]
    before = copy.deepcopy(rule)
    with pytest.raises(tp.TaskPackageError):
        tp.derive_params_from_landing(rule)
    assert rule == before


@pytest.mark.parametrize("kind", ["code", "code_list", "numeric", "base_map"])
@pytest.mark.parametrize("explicit_none", [False, True])
def test_missing_relation_keeps_established_catalog_or_code_default(kind, explicit_none):
    rule, name = _case(kind)
    if explicit_none:
        rule["template_fit"]["params"][name]["relation"] = None
    before = copy.deepcopy(rule)
    params = tp.derive_params_from_landing(rule)
    selected = [p for p in params if p["name"] == name]
    assert selected and {p["relation"] for p in selected} == (
        {"eq"} if kind in ("code", "code_list") else {"min"})
    assert all(m.validate_param(p, rule["kind"]) == [] for p in selected)
    assert rule == before


@pytest.mark.parametrize("relation", ["eq", "min", "max", "window"])
def test_missing_cell_relation_keeps_explicit_legacy_code_relation(relation):
    rule, name = _case("code")
    rule["template_fit"]["params"][name]["relation"] = None
    rule["params"] = [{"name": name, "value": "Airbus A320", "unit": "code",
                       "as_written": "Airbus A320", "relation": relation}]
    p, = tp.derive_params_from_landing(rule)
    assert p["relation"] == relation


@pytest.mark.parametrize("kind", ["numeric", "base_map_whole", "base_map_scalar"])
@pytest.mark.parametrize("bad", ["", False, 0, [], {}, "bogus"])
def test_invalid_legacy_numeric_or_map_relation_cannot_be_erased(kind, bad):
    rule, name = _case("numeric" if kind == "numeric" else "base_map")
    cell = rule["template_fit"]["params"][name]
    previous = {"name": name, "value": copy.deepcopy(cell["value"]),
                "unit": "duration_minutes" if kind == "numeric" else "count",
                "relation": bad, "as_written": cell["quote"]}
    if kind == "base_map_scalar":
        previous.update(value=cell["value"]["SVG"], qualifier="SVG")
    rule["params"] = [previous]
    before = copy.deepcopy(rule)
    with pytest.raises(tp.TaskPackageError) as error:
        tp.derive_params_from_landing(rule)
    assert f"{rule['id']}/{name}" in error.value.items
    assert rule == before


@pytest.mark.parametrize("kind", ["numeric", "base_map_whole", "base_map_scalar"])
@pytest.mark.parametrize("relation", [None, "min"])
def test_legacy_numeric_or_map_missing_and_valid_relation_stay_compatible(kind, relation):
    rule, name = _case("numeric" if kind == "numeric" else "base_map")
    cell = rule["template_fit"]["params"][name]
    previous = {"name": name, "value": copy.deepcopy(cell["value"]),
                "unit": "duration_minutes" if kind == "numeric" else "count",
                "relation": relation, "as_written": cell["quote"]}
    if kind == "base_map_scalar":
        previous.update(value=cell["value"]["SVG"], qualifier="SVG")
    rule["params"] = [previous]
    before = copy.deepcopy(rule)
    got = tp.derive_params_from_landing(rule)
    assert got and all(p["relation"] == "min" for p in got)
    assert rule == before
