"""CDPM-292: semantic roles must not reinterpret a lawful parameter unit."""
import copy
import pytest
from cdpm import logic_proof as lp, model as m
from test_cdpm_logic_proof import _rule_duration, _rule_count

def _clock_in_duration(name, qualified=False):
    rule = _rule_duration()
    param = rule["params"][0]
    param.update(name=name, value=420, unit="time_of_day_minutes",
                 as_written="07:00", tz="UTC")
    ref = name
    if qualified:
        param["qualifier"] = "SVO"
        ref = {"name": name, "qualifier": "SVO"}
    rule["logic"]["demand"]["param"] = ref
    rule["logic"]["source_params"] = [ref]
    assert m.validate_param(param, "HARD") == []
    return rule

@pytest.mark.parametrize("name", ["earliest_departure", "latest_arrival",
                                  "night_duty_start", "night_duty_end"])
@pytest.mark.parametrize("qualified", [False, True])
@pytest.mark.parametrize("reader", ["validate", "render", "proof"])
def test_clock_parameter_cannot_be_used_as_duration(name, qualified, reader):
    rule = _clock_in_duration(name, qualified)
    before = copy.deepcopy(rule)
    if reader == "validate":
        problem = "; ".join(m.validate_logic(rule["logic"], rule))
    elif reader == "render":
        problem = m.render_logic(rule["logic"], rule)
        assert "не более 7:00" not in problem
    else:
        spec, problem = lp.logic_to_check_spec(rule)
        assert spec is None
    assert "duration_minutes" in problem and "time_of_day_minutes" in problem
    assert name in problem
    assert rule == before

# Expected units are independent of the implementation's role table.
ROLES = [
    ("demand", "duration_within", "param", "duration_minutes"),
    ("demand", "count_within", "param", "count"),
    ("condition", "starts_in", "from_param", "time_of_day_minutes"),
    ("condition", "starts_in", "to_param", "time_of_day_minutes"),
    ("condition", "longer_than", "param", "duration_minutes"),
    ("condition", "shorter_than", "param", "duration_minutes"),
    ("condition", "consecutive", "param", "count"),
    ("demand", "штраф-ступенчатый-за-стыковку", "threshold_param", "duration_minutes"),
    ("demand", "штраф-ступенчатый-за-стыковку", "step_param", "duration_minutes"),
    ("demand", "штраф-за-разрыв-смены", "threshold_param", "duration_minutes"),
    ("demand", "однородность-цепочки-ночь-день", "window_from_param", "time_of_day_minutes"),
    ("demand", "однородность-цепочки-ночь-день", "window_to_param", "time_of_day_minutes"),
    ("demand", "мягкий-предел-дней-подряд", "bound_param", "count"),
]
PARAMS = {"duration_minutes": ("max_duty_duration", 420),
          "time_of_day_minutes": ("earliest_departure", 420),
          "count": ("crew_size", 7)}

@pytest.mark.parametrize("where,kind,arg,expected", ROLES)
@pytest.mark.parametrize("actual", PARAMS)
def test_every_declared_numeric_role_checks_its_unit(where, kind, arg, expected, actual):
    name, value = PARAMS[actual]
    rule = _rule_duration()
    rule["params"] = [{"name": name, "value": value, "unit": actual}]
    atom = {"kind" if where == "demand" else "predicate": kind, arg: name}
    logic = {"demand": atom} if where == "demand" else {"condition": [atom]}
    problems = m.logic_unit_problems(logic, rule)
    assert bool(problems) == (actual != expected)
    if problems:
        assert expected in "; ".join(problems) and actual in "; ".join(problems)

@pytest.mark.parametrize("placement", ["root", "condition"])
def test_rolling_window_needs_count_not_clock(placement):
    rule = _clock_in_duration("earliest_departure")
    window = {"kind": "rolling", "unit": "day", "param": "earliest_departure"}
    logic = ({"window": window} if placement == "root" else
             {"condition": [{"predicate": "in_window", "window": window}]})
    assert "count" in "; ".join(m.logic_unit_problems(logic, rule))

@pytest.mark.parametrize("factory", [_rule_duration, _rule_count])
def test_healthy_legacy_rule_keeps_validation_render_and_proof(factory):
    rule = factory()
    before = copy.deepcopy(rule)
    assert m.validate_logic(rule["logic"], rule) == []
    assert "не может" not in m.render_logic(rule["logic"], rule)
    spec, why = lp.logic_to_check_spec(rule)
    assert spec is not None and not why
    assert rule == before


def test_all_declared_reference_roles_have_a_unit_contract():
    for group, definitions in [("condition", m.LOGIC_PREDICATES),
                               ("demand", m.LOGIC_DEMANDS)]:
        for kind, args in definitions.items():
            refs = {arg for arg in args if arg.endswith("param")}
            assert refs <= set(m._LOGIC_ROLE_UNITS[group].get(kind, {}))


@pytest.mark.parametrize("kind", ["exists_person", "exists_universe", "not_exists"])
def test_optional_existence_count_cannot_take_duration(kind):
    rule = _rule_duration()
    logic = {"demand": {"kind": kind, "param": "max_duty_duration"}}
    assert "count" in "; ".join(m.logic_unit_problems(logic, rule))


def test_package_barrier_is_sensitive_to_the_unit_check(tmp_path, monkeypatch):
    from cdpm import export_task_package as tp
    from test_cdpm_a05_carriers_barrier import _doc_with, _run_dir
    doc = _doc_with(_clock_in_duration("earliest_departure"))
    # Same bytes reach the package path with the role-unit guard removed.
    # This proves the witness is not an unrelated readiness refusal.
    with monkeypatch.context() as disabled:
        disabled.setattr(m, "_logic_param_unit_problems", lambda *_: [])
        assert "model.json" in tp.build_package(_run_dir(tmp_path, doc, "without-guard"))
    with pytest.raises(tp.TaskPackageError) as error:
        tp.build_package(_run_dir(tmp_path, doc, "with-guard"))
    assert "time_of_day_minutes" in str(error.value)
    assert "duration_minutes" in str(error.value)
    assert any("time_of_day_minutes" in problem and "duration_minutes" in problem
               for problem in m.validate_model(doc))


def test_healthy_duration_package_still_builds(tmp_path):
    from cdpm import export_task_package as tp
    from test_cdpm_a05_carriers_barrier import _doc_with, _run_dir
    assert "model.json" in tp.build_package(_run_dir(tmp_path, _doc_with(_rule_duration())))


def test_package_checks_units_again_after_healing(tmp_path, monkeypatch):
    from cdpm import export_task_package as tp
    from test_cdpm_a05_carriers_barrier import _doc_with, _run_dir
    doc = _doc_with(_rule_duration())
    real = tp.heal_for_package
    def corrupt(candidate):
        changed = real(candidate)
        candidate["rules"][-1] = _clock_in_duration("earliest_departure")
        return changed
    monkeypatch.setattr(tp, "heal_for_package", corrupt)
    with pytest.raises(tp.TaskPackageError) as error:
        tp.build_package(_run_dir(tmp_path, doc))
    assert "time_of_day_minutes" in str(error.value)
    assert "duration_minutes" in str(error.value)
