"""Existing area aliases must agree in the legacy numeric diagnostic readers.

The alias is already declared by model.canon_area_qualifier and by the existing
threshold test's QUALIFIED map: both 'база' and 'в базе' name the home threshold.
Use the reviewed params-only fixture, not an expanded landing/2 condition enum.
The temporary catalogue/bindings are supplied by the existing fixture; no
verdict, projection or validator is mocked. No package/execution claim is made.
"""
import copy

import pytest

from cdpm import model as m
from cdpm.param_projection import derive_params_from_landing
from test_a05_qualifier_conflict_agent import (
    PARAM,
    _assert_real_reader_scopes,
    _pair,
    clean_bridge,  # noqa: F401
    isolated_consumer_catalog,  # noqa: F401
    landed,  # noqa: F401
)
from vmp import formulation_check as fc
from webapp import threshold_gate as tg


def _validate(pkg):
    assert m.validate_schema(pkg["model"]) == []
    assert m.validate_template_fit(pkg["model"]) == []
    for rule in pkg["model"]["rules"]:
        assert "template_fit" not in rule
        for param in rule["params"]:
            assert m.validate_param(param, rule["kind"]) == [], (rule, param)


def _scoped_pair(landed, *, kind="HARD", value=60, area="база", excluded=False):
    pkg = _pair(landed, area, value)
    assert PARAM in m.AREA_PARAM_NAMES
    # Observe the actual numeric reader before changing kind/exclusion. The
    # mapping is the established test input, not evidence of a live solver run.
    reading = "min_sit_outstation_min" if area == "вне базы" else "min_sit_home_min"
    _assert_real_reader_scopes(pkg, ("в базе", area), (45, value),
                               ("min_sit_home_min", reading))
    other = pkg["model"]["rules"][1]
    other["kind"] = kind
    if kind == "SOFT":
        other["weight"] = 3
    if excluded:
        other["excluded_until_data"] = {"question": "Q-EXCLUDED", "revision": 1}
    _validate(pkg)
    return pkg


@pytest.mark.parametrize("kind", ["HARD", "SOFT"], ids=["hard-hard", "mixed-kind"])
def test_existing_area_aliases_with_different_numbers_are_a_contradiction(landed, kind):
    pkg = _scoped_pair(landed, kind=kind)
    before = copy.deepcopy(pkg)
    assert m.canon_area_qualifier("база") == m.canon_area_qualifier("в базе") == "база"
    conflicts = tg.kind_conflicts(pkg)
    if kind == "SOFT":
        assert len(conflicts) == 1, (
            "Existing area aliases must not hide a HARD/SOFT 45/60 conflict", conflicts)
        conflict = conflicts[0]
        assert conflict["name"] == PARAM, conflict
        assert m.canon_area_qualifier(conflict["qualifier"]) == "база", conflict
        assert conflict["values"] == [45, 60], conflict
        assert conflict["by_kind"] == {"HARD": ["R-A"], "SOFT": ["R-B"]}, conflict
        assert conflict["rules"] == ["R-A", "R-B"], conflict
    else:
        assert conflicts == []  # Same-kind conflict belongs to formulation_check.
    findings = fc.check_contradiction(pkg["model"], pkg)
    assert len(findings) == 1, (
        "Schema-valid aliases for the same area lost a 45/60 contradiction", findings)
    hit = findings[0]
    assert hit["class"] == fc.CONTRADICTION and hit["level"] == fc.STOP, hit
    assert hit["id"] == "R-A" and hit["rules"] == ["R-A", "R-B"], hit
    assert hit["field"] == "params." + PARAM and hit["values"] == [45, 60], hit
    assert fc.check(pkg)["verdict"] == fc.REJECTED
    assert pkg == before


@pytest.mark.parametrize("change", [
    pytest.param({"value": 45}, id="hard-equal"),
    pytest.param({"kind": "SOFT", "value": 45}, id="mixed-equal"),
    pytest.param({"area": "вне базы"}, id="hard-different-area"),
    pytest.param({"kind": "SOFT", "area": "вне базы"}, id="mixed-different-area"),
    pytest.param({"kind": "OUT"}, id="withdrawn"),
    pytest.param({"excluded": True}, id="hard-excluded-until-data"),
    pytest.param({"kind": "SOFT", "excluded": True}, id="soft-excluded-until-data"),
])
def test_equal_separate_withdrawn_and_excluded_requirements_are_not_conflicts(landed, change):
    pkg = _scoped_pair(landed, **change)
    before = copy.deepcopy(pkg)
    assert tg.kind_conflicts(pkg) == [], change
    assert fc.check_contradiction(pkg["model"], pkg) == [], change
    assert fc.check(pkg)["verdict"] == fc.ACCEPTED, change
    assert pkg == before


@pytest.mark.parametrize("kind", ["HARD", "SOFT"], ids=["hard-hard", "mixed-kind"])
def test_non_area_qualifiers_are_not_reinterpreted_as_area_aliases(landed, kind):
    pkg = _pair(landed, "база", 60)
    # model.py explicitly distinguishes rest by parameter name, not the area
    # qualifier vocabulary. Its legacy free qualifier remains literal here;
    # accepting a diagnostic record is not proving executor support for it.
    name = "min_rest_home"
    assert name in m.PARAMS and name not in m.AREA_PARAM_NAMES
    for rule in pkg["model"]["rules"]:
        rule["params"][0]["name"] = name
        rule["title"] = "Отдых"
        rule["statement"] = "Отдых с уточнителем из отдельной области диагностического входа."
    other = pkg["model"]["rules"][1]
    other["kind"] = kind
    if kind == "SOFT":
        other["weight"] = 3
    _validate(pkg)
    before = copy.deepcopy(pkg)
    for rule in pkg["model"]["rules"]:
        assert derive_params_from_landing(rule) == rule["params"]
        assert tg.projected_params(rule, numeric_only=True) == rule["params"]
    assert tg.kind_conflicts(pkg) == []
    assert fc.check_contradiction(pkg["model"], pkg) == []
    assert fc.check(pkg)["verdict"] == fc.ACCEPTED
    assert pkg == before
