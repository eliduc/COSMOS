"""CDPM301/302: projection follows references, not unrelated clock cells.

Certifies parameter reading, not equivalence of arbitrary text to logic.
No live models or solver process.
"""
import copy
import pytest
from cdpm import model as m, logic_proof as lp, param_projection as projection

COUNT = "max_consecutive_night_duties"
CLOCK = "night_duty_start"


def _rule(clock=True, tz=None):
    cells = {COUNT: {"value": 3, "unit": "count", "relation": "max", "quote": "3"}}
    if clock:
        cells[CLOCK] = {"value": 1320, "unit": "time_of_day_minutes", "relation": "eq", "quote": "22:00"}
        if tz is not None:
            cells[CLOCK]["tz"] = tz
    return {"id": "R-SCOPE", "title": "Проверка ссылок", "statement": "Число смен — 3. Начало ночи — 22:00.",
            "kind": "HARD", "zone": "ROSTERING", "sources": [{"sid": "S1", "anchor": "п.1"}],
            "template_fit": {"family": "night_series", "category": "A",
                             "registry_rev": m.registry_fingerprint(), "params": cells},
            "logic": {"subject": "duty", "source_params": [COUNT],
                      "demand": {"kind": "count_within", "param": COUNT,
                                 "relation": "max", "as_written": "не более 3"}}}


@pytest.mark.parametrize("qualified", [False, True])
def test_unreferenced_clock_does_not_block_count_validation_or_retelling(qualified):
    rule = _rule()
    if qualified:
        rule["template_fit"]["params"][COUNT]["condition"] = "SVO"
        ref = {"name": COUNT, "qualifier": "SVO"}
        rule["logic"]["demand"]["param"] = ref
        rule["logic"]["source_params"] = [ref]
    before = copy.deepcopy(rule)
    assert m.validate_logic(rule["logic"], rule) == []
    assert "не более 3" in m.render_logic(rule["logic"], rule)
    entry = lp._param_entry(rule, rule["logic"]["demand"]["param"])
    assert entry["value"] == 3
    assert rule == before


def test_full_projection_still_refuses_missing_clock_zone():
    with pytest.raises(projection.ParamProjectionError, match="tz"):
        projection.derive_params_from_landing(_rule())


def test_reference_in_actual_atom_cannot_be_hidden_by_source_params():
    rule = _rule()
    rule["logic"]["condition"] = [{"predicate": "starts_in", "from_param": CLOCK,
              "to_param": CLOCK, "as_written": "в 22:00"}]
    assert "tz" in "; ".join(m.validate_logic(rule["logic"], rule))
    assert "tz" in m.render_logic(rule["logic"], rule)


def test_declared_but_unused_clock_does_not_escape_validation():
    rule = _rule()
    rule["logic"]["source_params"].append(CLOCK)
    assert m.validate_logic(rule["logic"], rule)


def test_other_clock_carrier_divergence_is_not_hidden_by_scoping():
    rule = _rule()
    rule["params"] = [{"name": CLOCK, "value": 60, "unit": "time_of_day_minutes",
                       "tz": "UTC", "relation": "eq", "as_written": "01:00"}]
    assert "носител" in "; ".join(m.validate_logic(rule["logic"], rule))
    assert lp._param_entry(rule, COUNT) is None


def test_logic_argument_not_attached_to_rule_defines_its_own_scope():
    rule = _rule()
    logic = copy.deepcopy(rule["logic"])
    rule["logic"]["source_params"] = [CLOCK]
    rule["logic"]["demand"]["param"] = CLOCK
    assert m.validate_logic(logic, rule) == []
    assert "не более 3" in m.render_logic(logic, rule)


@pytest.mark.parametrize("bad", [None, [], "broken"])
def test_corrupt_landing_container_is_never_ignored(bad):
    rule = _rule()
    rule["template_fit"]["params"] = bad
    assert m.validate_logic(rule["logic"], rule)


@pytest.mark.parametrize("zone", ["UTC", "base-local", "acclimatized"])
def test_explicit_clock_zone_is_schema_legal_and_preserved_in_projection(zone):
    rule = _rule(tz=zone)
    doc = {"meta": {"customer": "T", "mode": 1, "cdpm_version": "0.1", "revision": 1},
           "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
           "rules": [rule], "questions": [], "objectives": []}
    assert m.validate_schema(doc) == []
    values = {p["name"]: p for p in projection.derive_params_from_landing(rule)}
    assert values[CLOCK]["tz"] == zone
    assert values[CLOCK]["value"] == 1320


def test_clock_retelling_preserves_two_digit_hour():
    rule = _rule(tz="UTC")
    rule["template_fit"]["params"][CLOCK].update(value=420, quote="07:00")
    rule["logic"]["condition"] = [{"predicate": "starts_in", "from_param": CLOCK,
              "to_param": CLOCK, "as_written": "в 07:00"}]
    rule["logic"]["source_params"].append(CLOCK)
    assert m.validate_logic(rule["logic"], rule) == []
    assert "[07:00–07:00]" in m.render_logic(rule["logic"], rule)


@pytest.mark.parametrize("legacy_zone", ["base-local", None])
def test_explicit_landing_zone_cannot_overwrite_a_different_legacy_zone(legacy_zone):
    rule = _rule(tz="UTC")
    legacy = {"name": CLOCK, "value": 1320, "unit": "time_of_day_minutes",
              "relation": "eq", "as_written": "22:00"}
    if legacy_zone is not None:
        legacy["tz"] = legacy_zone
    rule["params"] = [legacy]
    before = copy.deepcopy(rule)
    assert "tz" in "; ".join(m.reconcile_carriers(rule))
    with pytest.raises(projection.ParamProjectionError, match="tz"):
        projection.derive_params_from_landing(rule)
    assert rule == before


def test_old_cell_without_zone_keeps_explicit_legacy_zone():
    rule = _rule()
    rule["params"] = [{"name": CLOCK, "value": 1320, "unit": "time_of_day_minutes",
                       "tz": "UTC", "relation": "eq", "as_written": "22:00"}]
    assert m.reconcile_carriers(rule) == []
    values = {p["name"]: p for p in projection.derive_params_from_landing(rule)}
    assert values[CLOCK]["tz"] == "UTC"


@pytest.mark.parametrize("name,zone", [(COUNT, "UTC"), (CLOCK, "Mars"), (CLOCK, None)])
def test_shared_writer_refuses_inapplicable_or_invalid_explicit_zone(name, zone):
    from cdpm import family_params as fp
    rule = _rule()
    before = copy.deepcopy(rule)
    cell = {**rule["template_fit"]["params"][name], "tz": zone}
    assert "tz" in fp.write_cell(rule, name, cell)
    assert rule == before


@pytest.mark.parametrize("zone", ["UTC", "base-local", "acclimatized"])
def test_shared_writer_keeps_explicit_zone_and_copies_the_cell(zone):
    from cdpm import family_params as fp
    rule = _rule(clock=False)
    cell = {"value": 420, "unit": "time_of_day_minutes", "relation": "eq",
            "quote": "07:00", "tz": zone}
    assert fp.write_cell(rule, CLOCK, cell) is None
    assert rule["template_fit"]["params"][CLOCK] == cell
    cell["tz"] = "changed-by-caller"
    values = {p["name"]: p for p in projection.derive_params_from_landing(rule)}
    assert values[CLOCK]["tz"] == zone


@pytest.mark.parametrize("change", [{"tz": "Mars"}, {"tz": None},
                                     {"unit": "count"}, {"unit": None}])
def test_schema_refuses_invalid_or_inapplicable_explicit_zone(change):
    rule = _rule(tz="UTC")
    rule["template_fit"]["params"][CLOCK].update(change)
    doc = {"meta": {"customer": "T", "mode": 1, "cdpm_version": "0.1", "revision": 1},
           "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
           "rules": [rule], "questions": [], "objectives": []}
    assert m.validate_schema(doc)


def test_legacy_timezone_healing_cannot_hide_explicit_carrier_disagreement():
    rule = _rule(tz="base-local")
    rule["statement"] = "Начало ночи в поясе акклиматизации — 22:00."
    rule["params"] = [{"name": CLOCK, "value": 1320, "unit": "time_of_day_minutes",
                       "tz": "base-local", "relation": "eq", "as_written": "22:00"}]
    doc = {"rules": [rule]}
    assert m.reconcile_carriers(rule) == []
    assert m.restore_night_window_tz(doc)
    assert rule["params"][0]["tz"] == "acclimatized"
    # The old healer does not own a new explicit landing zone. A package must
    # refuse this disagreement, not quietly choose either timezone.
    assert rule["template_fit"]["params"][CLOCK]["tz"] == "base-local"
    with pytest.raises(projection.ParamProjectionError, match="tz"):
        projection.derive_params_from_landing(rule)
