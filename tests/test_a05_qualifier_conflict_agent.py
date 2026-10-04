"""The existing numeric contradiction oracle must share scope normalization.

The neighbouring fixture supplies a real temporary catalogue and bindings.
The measured rules deliberately use the existing params-only legacy diagnostic
path, not landing/2: that pinned condition enum rejects these whitespace forms.
Schema, template-fit and parameter validators still run; none is disabled.
No product verdict is mocked.
Whitespace equivalence is already required by threshold_gate and its existing
test_пробелы_и_регистр_уточнителя_нормализуются; these cases add no area aliases.
This is a direct Python formulation check, not modern export, archive-package
acceptance or execution permission for a legacy package.
"""
import copy

import pytest

from cdpm import model as m
from cdpm.param_projection import derive_params_from_landing
from test_a05_landing_numeric_neighbors_agent import (
    PARAM,
    _pkg,
    clean_bridge,  # noqa: F401
    isolated_consumer_catalog,  # noqa: F401
    landed,  # noqa: F401
)
from test_threshold_gate import MAPPING, QUALIFIED
from vmp import formulation_check as fc
from webapp import threshold_gate as tg


def _pair(landed, second_area, second_value):
    doc, original = landed
    rules = []
    for rid, area, value in (("R-A", "в базе", 45),
                             ("R-B", second_area, second_value)):
        rule = copy.deepcopy(original)
        rule.update(id=rid, statement=f"Стыковка {area} не менее {value} минут.")
        # Build an explicit legacy diagnostic input, not an invalid typed cell
        # whose immutable enum is bypassed. No release or migration is tested.
        rule.pop("template_fit")
        rule["params"] = [{"name": PARAM, "value": value,
                           "unit": "duration_minutes", "relation": "min",
                           "qualifier": area, "as_written": f"{value} минут"}]
        assert m.validate_param(rule["params"][0], "HARD") == []
        rules.append(rule)
    pkg = _pkg(doc, *rules)
    assert m.validate_schema(pkg["model"]) == []
    assert m.validate_template_fit(pkg["model"]) == []
    assert all("template_fit" not in rule for rule in pkg["model"]["rules"])
    return pkg


def _assert_real_reader_scopes(pkg, areas, values, readings):
    """Read actual legacy parameters and independently observe their targets.

    MAPPING/QUALIFIED are the established consumer test inputs, not a live
    solver report. Matching here proves reader normalization, not execution.
    """
    before = copy.deepcopy(pkg)
    collected = tg.collect_params(pkg)
    assert [(p["qualifier"], p["value"]) for p in collected] == list(zip(areas, values))
    for rule, area, value, reading in zip(pkg["model"]["rules"], areas, values, readings):
        assert m.canon_area_qualifier(area) == (
            "база" if reading == "min_sit_home_min" else "вне базы")
        projected, = derive_params_from_landing(rule)
        assert {key: projected[key] for key in
                ("name", "value", "unit", "relation", "qualifier")} == {
            "name": PARAM, "value": value, "unit": "duration_minutes",
            "relation": "min", "qualifier": area}
        assert m.validate_param(projected, "HARD") == []
        single = copy.deepcopy(pkg)
        single["model"]["rules"] = [copy.deepcopy(rule)]
        compared = tg.compare(single, {reading: value}, profile="DEFAULT",
                              mapping=MAPPING, qualified=QUALIFIED)
        assert len(compared) == 1, compared
        assert compared[0]["reading"] == reading, compared
        assert compared[0]["verdict"] == tg.MATCH, compared
    # Two HARD rows are deliberately not the mixed-kind conflict branch.
    assert tg.kind_conflicts(pkg) == []
    assert pkg == before


@pytest.mark.parametrize("second_area", [
    pytest.param("в базе", id="ordinary-space-control"),
    pytest.param("в  базе", id="repeated-space"),
    pytest.param("В\u00a0базе", id="nbsp-and-case"),
])
def test_equivalent_areas_with_different_hard_numbers_are_a_contradiction(
        landed, second_area):
    pkg = _pair(landed, second_area, 60)
    before = copy.deepcopy(pkg)
    _assert_real_reader_scopes(pkg, ("в базе", second_area), (45, 60),
                               ("min_sit_home_min", "min_sit_home_min"))
    findings = fc.check_contradiction(pkg["model"], pkg)
    assert len(findings) == 1, (
        "Both schema-valid legacy HARD parameters reach the same home threshold, but "
        f"their 45/60 conflict was lost for {second_area!r}: {findings!r}")
    hit = findings[0]
    assert hit["class"] == fc.CONTRADICTION and hit["level"] == fc.STOP, hit
    assert hit["id"] == "R-A" and hit["rules"] == ["R-A", "R-B"], hit
    assert hit["field"] == "params." + PARAM, hit
    assert hit["values"] == [45.0, 60.0], hit
    assert fc.check(pkg)["verdict"] == fc.REJECTED
    assert pkg == before


@pytest.mark.parametrize("second_area", [
    pytest.param("в  базе", id="repeated-space"),
    pytest.param("В\u00a0базе", id="nbsp-and-case"),
])
def test_equivalent_areas_with_equal_numbers_are_not_a_contradiction(
        landed, second_area):
    pkg = _pair(landed, second_area, 45)
    before = copy.deepcopy(pkg)
    _assert_real_reader_scopes(pkg, ("в базе", second_area), (45, 45),
                               ("min_sit_home_min", "min_sit_home_min"))
    assert fc.check_contradiction(pkg["model"], pkg) == []
    assert fc.check(pkg)["verdict"] == fc.ACCEPTED
    assert pkg == before


def test_different_areas_with_different_numbers_remain_separate(landed):
    pkg = _pair(landed, "вне базы", 60)
    before = copy.deepcopy(pkg)
    _assert_real_reader_scopes(pkg, ("в базе", "вне базы"), (45, 60),
                               ("min_sit_home_min", "min_sit_outstation_min"))
    assert fc.check_contradiction(pkg["model"], pkg) == []
    assert fc.check(pkg)["verdict"] == fc.ACCEPTED
    assert pkg == before
