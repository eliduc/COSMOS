"""Stand K-1: actual typed world values must reach the SMT subject text.

Pure construction/validation only: no verify(), translator, solver, export,
HTTP, services, or claimed stand acceptance. Reuses the isolated vocabulary
fixture; its catalogue is not released/approved support. Numeric expectations
come from world.params and the catalogue, never task.expected or a result.
"""
import copy
import json

import pytest

from cdpm import export_task_package as tp, model as m, smt_verify
from template_catalog import landing
from vmp import stand_solver as ss
from test_stand_landing_v2_contract_agent import catalog, rule_of, world_with


HEADING = "Параметры исходного синтетического мира, уточняющие только это правило:"


def _build(catalog, family, world, *, logic=None):
    source_rule = rule_of(catalog, family, world)
    if logic is not None:
        source_rule["logic"] = copy.deepcopy(logic)
    before = copy.deepcopy((world, source_rule))
    doc = ss.model_doc([source_rule], world=world, family=family, catalog=catalog)
    assert (world, source_rule) == before, "construction must not mutate its source"
    row = doc["rules"][0]
    assert row["kind"] == catalog.family(family)["kind"]
    assert row["zone"] == source_rule["zone"]
    assert row["sources"] == [{"sid": "S1", "anchor": "стенд"}]
    assert m.validate_schema(doc) == []
    assert m.validate_template_fit(doc) == []
    assert landing.problems(row, catalog) == []
    return doc, row


def _statement_cells(row):
    statement = row["statement"]
    assert statement.count(HEADING) == 1, statement
    encoded = statement.split(HEADING, 1)[1].strip()
    cells = json.loads(encoded)
    assert encoded == json.dumps(cells, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"))
    assert len({cell["name"] for cell in cells}) == len(cells)
    return {cell["name"]: cell for cell in cells}


@pytest.mark.parametrize("family,values,expected", [
    ("duty_limits", {"max_duty_duration": 780, "max_landings_per_duty": 4,
                     "max_pairing_days": 7},
     {"max_duty_duration": (780, "duration_minutes", "max"),
      "max_landings_per_duty": (4, "count", "max"),
      "max_pairing_days": (7, "count", "max")}),
    ("connection_windows", {"min_turnaround": 45, "max_sit_duration": 960},
     {"min_turnaround": (45, "duration_minutes", "min"),
      "max_sit_duration": (960, "duration_minutes", "max")}),
    ("time_scale", {"base_tz_offset_min": 0},
     {"base_tz_offset_min": (0, "duration_minutes", "eq")}),
    ("rolling_rest", {"weekly_rest_period": 10080, "weekly_rest_required": 2160},
     {"weekly_rest_period": (10080, "duration_minutes", "window"),
      "weekly_rest_required": (2160, "duration_minutes", "min")}),
    ("night_series", {"night_duty_start": 1320, "night_duty_end": 360},
     {"night_duty_start": (1320, "time_of_day_minutes", "min"),
      "night_duty_end": (360, "time_of_day_minutes", "max")}),
])
def test_numeric_world_is_in_actual_smt_subject_with_catalogue_relations(
        catalog, family, values, expected):
    world = world_with(values)
    if family == "duty_limits":
        # The published synthetic task, not its expected outcome, is the input.
        task = next(t for t in catalog.family(family)["stand_tasks"]
                    if t["id"] == "st-duty-1")
        assert task["world"]["params"] == values
        world = copy.deepcopy(task["world"])
    doc, row = _build(catalog, family, world)
    assert catalog.family(family)["semantics"]["text"] in row["statement"]
    subject = smt_verify.subject_of(doc)
    if catalog.family(family)["nature"] == "world_param":
        # K2: the numerical carrier survives, but a coordinate-system datum
        # is not an SMT decision constraint merely because it is HARD.
        assert subject == []
        assert tp.time_declaration_of(doc, None)["base_tz_offset_min"] == 0
        printed = _statement_cells(row)
    else:
        assert subject == [{"id": row["id"], "kind": "HARD",
                            "statement": row["statement"].strip()}]
        printed = _statement_cells(subject[0])
    assert set(printed) == set(expected)
    projected = tp.derive_params_from_landing(row)
    assert {p["name"]: (p["value"], p["unit"], p["relation"])
            for p in projected} == expected
    for name, (value, unit, relation) in expected.items():
        wanted = {"name": name, "value": value, "unit": unit,
                  "relation": relation, "condition": None}
        if unit == "time_of_day_minutes":
            wanted["tz"] = "base-local"
        assert {key: printed[name][key] for key in wanted} == wanted
        typed = row["template_fit"]["params"][name]
        assert all(typed[k] == v for k, v in wanted.items() if k != "name")
    if family == "night_series":
        note = next(p["note"] for p in catalog.family(family)["params"]
                    if p["name"] == "night_duty_end")
        assert "исключительно" in note
        assert printed["night_duty_end"]["note"] == note
    assert all(m.validate_param(p, row["kind"]) == [] for p in projected)


def test_real_world_value_change_changes_the_smt_subject(catalog):
    first, _ = _build(catalog, "duty_limits", world_with({"max_duty_duration": 780}))
    second, _ = _build(catalog, "duty_limits", world_with({"max_duty_duration": 781}))
    assert smt_verify.subject_of(first) != smt_verify.subject_of(second)
    assert smt_verify.subject_fingerprint(first) != smt_verify.subject_fingerprint(second)


def test_absent_or_other_family_values_are_not_invented(catalog):
    world = world_with({"max_duty_duration": 780, "crew_size": 2})
    _, row = _build(catalog, "duty_limits", world)
    assert set(row["template_fit"]["params"]) == {"max_duty_duration"}
    assert set(_statement_cells(row)) == {"max_duty_duration"}


def test_soft_threshold_remains_soft_not_an_smt_hard_bound(catalog):
    doc, row = _build(catalog, "balanced_workload",
                      world_with({"workload_spread_threshold": 5}))
    assert row["kind"] == "SOFT" and row["weight"] == 1
    assert smt_verify.subject_of(doc) == []
    wanted = {
        "name": "workload_spread_threshold", "value": 5, "unit": "credit_hours",
        "relation": "max", "condition": None}
    actual = _statement_cells(row)["workload_spread_threshold"]
    assert {key: actual[key] for key in wanted} == wanted
    assert catalog.family("balanced_workload")["semantics"]["text"] in row["statement"]


@pytest.mark.parametrize("consent,expected", [("yes", True), ("no", False)])
def test_extension_scope_and_consent_are_preserved_not_globalized(catalog, consent, expected):
    world = world_with({"duty_extension_cap": 780, "long_duty_threshold": 720})
    world["conditions"] = {"extension_bases": ["ST1"], "extension_consent": consent}
    doc, row = _build(catalog, "duty_extension_penalty", world)
    cells = _statement_cells(row)
    assert row["kind"] == "SOFT" and smt_verify.subject_of(doc) == []
    assert set(cells) == {"duty_extension_cap", "long_duty_threshold",
                          "extension_bases", "extension_consent"}
    assert (cells["extension_bases"]["value"], cells["extension_bases"]["unit"],
            cells["extension_bases"]["relation"]) == (["ST1"], "code_list", "in")
    assert (cells["extension_consent"]["value"], cells["extension_consent"]["unit"],
            cells["extension_consent"]["relation"]) == (expected, "flag", "yes")
    assert row["template_fit"]["params"]["extension_consent"]["value"] is expected
    assert catalog.family("duty_extension_penalty")["semantics"]["text"] in row["statement"]


def test_existing_logic_and_existing_typed_statement_are_not_rewritten(catalog):
    # Existing real logic grammar from test_cdpm_logic_projection_scope.py.
    logic = {"subject": "duty", "source_params": ["max_consecutive_night_duties"],
             "demand": {"kind": "count_within", "param": "max_consecutive_night_duties",
                        "relation": "max", "as_written": "не более 3"}}
    world = world_with({"max_consecutive_night_duties": 3})
    _, row = _build(catalog, "night_series", world, logic=logic)
    assert row["logic"] == logic
    assert m.validate_logic(row["logic"], row) == []
    row["statement"] = "Уже существующее правило: ночных смен подряд не более 3."
    before = copy.deepcopy(row)
    reread = ss.model_doc([row], world=world, family="night_series", catalog=catalog)
    assert row == before
    assert reread["rules"][0] == before, "existing landing/2 evidence is not regenerated"


def test_crew_composition_retains_its_existing_unconditional_meaning(catalog):
    doc, row = _build(catalog, "crew_composition", world_with({"crew_size": 2}))
    assert "без условия варианта" in row["statement"]
    assert tp.crew_size_sources(doc) == {2: row["id"]}
    wanted = {
        "name": "crew_size", "value": 2, "unit": "count",
        "relation": "eq", "condition": None}
    actual = _statement_cells(row)["crew_size"]
    assert {key: actual[key] for key in wanted} == wanted
