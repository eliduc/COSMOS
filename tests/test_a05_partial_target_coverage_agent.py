"""Every current numeric record needs a threshold verdict, not just its name.

Use a schema-valid pinned base map and its lossless legacy projection. The
consumer report deliberately omits one target: this is a diagnostic input,
not a claim that a live solver emits it or that either base was executed.
No admission gate, verdict, registry approval or solver is mocked.
"""
import copy

import pytest

from cdpm import model as m
from cdpm.param_projection import derive_params_from_landing
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from webapp import threshold_gate as tg


NAME = "reserve_crews"
ST1 = "reserve_crews_min[ST1]"
ST2 = "reserve_crews_min[ST2]"
MAPPING = {NAME: "reserve_crews_min"}


@pytest.fixture(params=["typed", "legacy"])
def scoped_pkg(tmp_path, monkeypatch, request):
    doc, rule = typed_rule(tmp_path, monkeypatch, "reserve_floors", {
        NAME: {"value": {"ST1": 2, "ST2": 1}, "unit": "count",
               "relation": "min", "condition": None, "quote": "ST1:2,ST2:1"}})
    rule.update(id="R-RESERVE", kind="HARD", zone="PAIRING",
                statement="Резерв: ST1:2,ST2:1.")
    rule["template_fit"]["params"][NAME]["source"]["rule_id"] = rule["id"]
    rule.pop("params", None)
    projected = derive_params_from_landing(rule)
    assert [(p["qualifier"], p["value"]) for p in projected] == [("ST1", 2), ("ST2", 1)]
    assert all(m.validate_param(p, "HARD") == [] for p in projected)
    if request.param == "legacy":
        rule.pop("template_fit")
        rule["params"] = projected
    doc["rules"], doc["questions"], doc["objectives"] = [rule], [], []
    assert m.validate_schema(doc) == []
    assert m.validate_template_fit(doc) == []
    return {"model": doc}


def _compare(pkg, qualified, readings=None, ignored=None):
    before = copy.deepcopy(pkg)
    result = tg.compare(pkg, readings if readings is not None else {ST1: 2, ST2: 1},
                        profile="DEFAULT", mapping=MAPPING,
                        qualified=qualified, ignored=ignored)
    assert pkg == before
    return result


def test_one_mapped_base_does_not_hide_another_unmapped_base(scoped_pkg):
    result = _compare(scoped_pkg, {NAME + "|ST1": ST1})
    assert len(result) == 2, result
    known, = [v for v in result if v["reading"] == ST1]
    assert known["verdict"] == tg.MATCH and known["qualifiers"] == ["ST1"]
    missing, = [v for v in result if v["reading"] is None]
    assert missing["verdict"] == tg.INCOMPARABLE, missing
    assert missing["qualifiers"] == ["ST2"] and missing["package_values"] == [1]
    assert missing["rules"] == ["R-RESERVE"] and "не нашёл цели" in missing["note"]


def test_all_mapped_bases_remain_individually_comparable(scoped_pkg):
    result = _compare(scoped_pkg, {NAME + "|ST1": ST1, NAME + "|ST2": ST2})
    assert {(v["reading"], v["verdict"], tuple(v["qualifiers"])) for v in result} == {
        (ST1, tg.MATCH, ("ST1",)), (ST2, tg.MATCH, ("ST2",))}
    assert len(result) == 2


def test_no_targets_reports_both_original_records(scoped_pkg):
    result = _compare(scoped_pkg, {})
    assert len(result) == 1
    assert result[0]["verdict"] == tg.INCOMPARABLE
    assert result[0]["qualifiers"] == ["ST1", "ST2"]
    assert result[0]["package_values"] == [1, 2]


def test_explicitly_ignored_base_is_reported_once(scoped_pkg):
    result = _compare(scoped_pkg, {NAME + "|ST1": ST1}, ignored=[{
        "param": NAME, "rules": ["R-RESERVE"], "qualifiers": ["ST2"],
        "reason": "Потребитель явно не применил ST2."}])
    assert len(result) == 2
    assert sum(v["verdict"] == tg.MATCH for v in result) == 1
    ignored, = [v for v in result if v["verdict"] == tg.INCOMPARABLE]
    assert ignored["qualifiers"] == ["ST2"] and "не применил" in ignored["note"]


def test_known_target_without_reading_is_not_duplicated_as_unmapped(scoped_pkg):
    result = _compare(scoped_pkg, {NAME + "|ST1": ST1, NAME + "|ST2": ST2},
                      readings={ST1: 2})
    assert len(result) == 2
    assert all(v["reading"] is not None for v in result)
    missing, = [v for v in result if v["reading"] == ST2]
    assert missing["verdict"] == tg.INCOMPARABLE
    assert missing["qualifiers"] == ["ST2"] and "не сообщает" in missing["note"]
