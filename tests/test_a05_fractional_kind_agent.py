"""A05: distinct fractional values must remain distinct across rule kinds.

Direct numeric-consumer checks on schema-valid pinned SOFT / legacy DEF
records. No ZIP acceptance, executor support, live service or solve is claimed.
The existing cross-kind conflict policy is unchanged; only precision matters.
"""
import copy

import pytest

from cdpm import model as m
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from vmp import formulation_check as fc
from webapp import threshold_gate as tg


NAME = "workload_spread_threshold"


@pytest.fixture
def fractional_pkg(tmp_path, monkeypatch):
    doc, soft = typed_rule(tmp_path, monkeypatch, "balanced_workload", {
        NAME: {"value": 5.25, "unit": "credit_hours", "relation": "max",
               "condition": None, "quote": "5,25 кредитных часа"}})
    soft.update(id="R-SOFT", kind="SOFT", zone="ROSTERING", weight=3,
                statement="Разброс кредитных часов не более 5,25.")
    soft["template_fit"]["params"][NAME]["source"]["rule_id"] = soft["id"]
    soft.pop("params", None)
    other = {"id": "R-DEF", "title": "Определение порога",
             "statement": "Порог разброса кредитных часов 5,75.",
             "kind": "DEF", "zone": "ROSTERING",
             "sources": copy.deepcopy(soft["sources"]),
             "params": [{"name": NAME, "value": 5.75, "unit": "credit_hours",
                         "relation": "max", "as_written": "5,75 кредитных часа"}]}
    doc["rules"], doc["questions"], doc["objectives"] = [soft, other], [], []
    assert m.validate_schema(doc) == []
    assert m.validate_template_fit(doc) == []
    assert m.validate_param(other["params"][0], "DEF") == []
    return {"model": doc, "form": {}}


def test_cross_kind_conflict_preserves_fractional_values(fractional_pkg):
    before = copy.deepcopy(fractional_pkg)
    assert tg.kind_conflicts(fractional_pkg) == [{
        "name": NAME, "qualifier": "", "values": [5.25, 5.75],
        "by_kind": {"DEF": ["R-DEF"], "SOFT": ["R-SOFT"]},
        "rules": ["R-DEF", "R-SOFT"]}]
    assert fractional_pkg == before


def test_formulation_consumer_sees_fractional_cross_kind_conflict(fractional_pkg):
    before = copy.deepcopy(fractional_pkg)
    hit, = fc.check_contradiction(fractional_pkg["model"], fractional_pkg)
    assert hit["class"] == fc.CONTRADICTION and hit["level"] == fc.STOP
    assert hit["values"] == [5.25, 5.75]
    assert hit["rules"] == ["R-DEF", "R-SOFT"]
    assert "5.25" in hit["message"] and "5.75" in hit["message"]
    assert fractional_pkg == before


@pytest.mark.parametrize("change", ["equal", "same_kind", "out", "excluded", "other_scope"])
def test_existing_nonconflict_boundaries_are_preserved(fractional_pkg, change):
    other = fractional_pkg["model"]["rules"][1]
    if change == "equal":
        other["params"][0].update(value=5.25, as_written="5,25 кредитных часа")
    elif change == "same_kind":
        other.update(kind="SOFT", weight=3)
    elif change == "out":
        other["kind"] = "OUT"
    elif change == "excluded":
        other["excluded_until_data"] = {"question": "Q-EXCLUDED", "revision": 1}
    else:
        other["params"][0]["qualifier"] = "отдельная область"
    before = copy.deepcopy(fractional_pkg)
    assert tg.kind_conflicts(fractional_pkg) == []
    assert fractional_pkg == before
