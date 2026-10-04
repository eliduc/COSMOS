"""Typed numeric consumer regressions adjacent to the A05 landing-only seam.

These are direct Python unit checks, not package-gate or execution claims.
The existing helper creates a real temporary synthetic catalogue and a pinned
landing/2 cell; no registry/support approval, verdict, HTTP or solver is mocked.
Oracles preserve the existing numeric checks' meaning, including explicit
exclusion, separate areas, duplicate legacy records and warning-only horizons.
"""
import copy
import json
from pathlib import Path

import pytest

from cdpm import catalog_bridge as cb, model as m
from cdpm.param_projection import ParamProjectionError
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from vmp import catalog_access, formulation_check as fc
from webapp import threshold_gate as tg


PARAM = "min_turnaround"


@pytest.fixture(autouse=True)
def isolated_consumer_catalog():
    catalog_access.forget()
    yield
    catalog_access.forget()


@pytest.fixture
def landed(tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, "connection_windows", {
        PARAM: {"value": 45, "unit": "duration_minutes", "relation": "min",
                "condition": {"kind": "area", "value": "база"},
                "quote": "45 минут"}})
    rule.update(id="R-A", kind="HARD", zone="PAIRING", title="Стыковка",
                statement="Стыковка в базе не менее 45 минут.")
    rule["template_fit"]["params"][PARAM]["source"]["rule_id"] = rule["id"]
    rule.pop("params", None)
    doc["rules"], doc["questions"], doc["objectives"] = [rule], [], []
    _checked_model(doc)
    # fc.check also reads the actual violation-unit registry. Bind that
    # reader to this catalogue without stubbing its projection or verdict.
    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / "vmp" / "bindings.json").read_text(encoding="utf-8"))
    cat = cb.catalog()
    raw.update(catalog_version=cat.version(), catalog_fingerprint=cat.fingerprint())
    bindings_path = tmp_path / "numeric-consumer-bindings.json"
    bindings_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CREW_COSMOS_BINDINGS", str(bindings_path))
    catalog_access.forget()
    return doc, rule


def _checked_model(doc):
    assert m.validate_schema(doc) == []
    assert m.validate_template_fit(doc) == []
    return doc


def _other(rule, *, value=60, kind="HARD", area="база", excluded=False):
    other = copy.deepcopy(rule)
    other.update(id="R-B", kind=kind,
                 statement=f"Стыковка {area} не менее {value} минут.")
    cell = other["template_fit"]["params"][PARAM]
    cell.update(value=value, quote=f"{value} минут",
                condition={"kind": "area", "value": area},
                source={"kind": "rule", "rule_id": "R-B"})
    if kind == "SOFT":
        other["weight"] = 3
    elif kind in {"OUT", "DATA"}:
        # Current landing permits only HARD/SOFT. Keep the legacy OUT/DATA
        # controls legal instead of bypassing that validator for the test.
        other.pop("template_fit")
        other["params"] = [{"name": PARAM, "value": value,
                            "unit": "duration_minutes", "relation": "min",
                            "qualifier": area, "as_written": f"{value} минут"}]
    if excluded:
        # Existing consumer exclusion contract; no claim that a real answer
        # lifecycle or a complete package was reconstructed by this fixture.
        other["excluded_until_data"] = {"question": "Q-EXCLUDED", "revision": 1}
    return other


def _pkg(doc, *rules):
    model = copy.deepcopy(doc)
    model["rules"] = list(rules)
    _checked_model(model)
    return {"model": model, "form": {}}


def test_kind_conflicts_see_landing_values_without_merging_distinct_scopes(landed):
    doc, rule = landed
    # Equal values, the same kind, OUT, an explicit exclusion and distinct
    # areas are the existing negative controls, not new conflict semantics.
    for change in ({"kind": "SOFT", "value": 45}, {"kind": "HARD"},
                   {"kind": "OUT"}, {"kind": "SOFT", "excluded": True},
                   {"kind": "SOFT", "area": "вне базы"}):
        pkg = _pkg(doc, rule, _other(rule, **change))
        before = copy.deepcopy(pkg)
        assert tg.kind_conflicts(pkg) == [], change
        assert pkg == before
    pkg = _pkg(doc, rule, _other(rule, kind="SOFT"))
    before = copy.deepcopy(pkg)
    assert tg.kind_conflicts(pkg) == [{
        "name": PARAM, "qualifier": "база", "values": [45, 60],
        "by_kind": {"HARD": ["R-A"], "SOFT": ["R-B"]},
        "rules": ["R-A", "R-B"]}]
    verdict = fc.check(pkg)
    assert verdict["verdict"] == fc.REJECTED
    hit, = [f for f in verdict["findings"] if f["class"] == fc.CONTRADICTION]
    assert hit["level"] == fc.STOP and hit["rules"] == ["R-A", "R-B"]
    assert hit["values"] == [45, 60]
    assert pkg == before


def test_excluded_landing_records_remain_visible_but_not_applied(landed):
    doc, rule = landed
    active = _pkg(doc, rule)
    assert tg.excluded_params(active) == []
    for change, expected_kind in (({"kind": "OUT"}, "OUT"),
                                  ({"kind": "DATA"}, "DATA"),
                                  ({"kind": "SOFT"}, "SOFT"),
                                  ({"excluded": True}, "excluded_until_data")):
        pkg = _pkg(doc, _other(rule, **change))
        before = copy.deepcopy(pkg)
        assert tg.collect_params(pkg) == [], change
        row, = tg.excluded_params(pkg)
        assert {k: row[k] for k in ("name", "value", "unit", "relation",
                                    "qualifier", "rule", "kind")} == {
            "name": PARAM, "value": 60, "unit": "duration_minutes",
            "relation": "min", "qualifier": "база", "rule": "R-B",
            "kind": expected_kind}
        assert pkg == before


def test_contradiction_uses_typed_numbers_and_preserves_first_rule_order(landed):
    doc, rule = landed
    for change in ({"value": 45}, {"area": "вне базы"}, {"excluded": True}):
        pkg = _pkg(doc, rule, _other(rule, **change))
        before = copy.deepcopy(pkg)
        assert fc.check_contradiction(pkg["model"], pkg) == [], change
        assert fc.check(pkg)["verdict"] == fc.ACCEPTED
        assert pkg == before
    pkg = _pkg(doc, rule, _other(rule))
    before = copy.deepcopy(pkg)
    hit, = fc.check_contradiction(pkg["model"], pkg)
    assert hit["class"] == fc.CONTRADICTION and hit["level"] == fc.STOP
    assert hit["id"] == "R-A" and hit["rules"] == ["R-A", "R-B"]
    assert hit["values"] == [45.0, 60.0]
    assert PARAM in hit["field"] and "база" in hit["message"]
    assert fc.check(pkg)["verdict"] == fc.REJECTED
    assert pkg == before


def test_degenerate_typed_duration_keeps_warning_and_horizon_boundary(
        tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, "duty_limits", {
        "max_duty_duration": {"value": 720, "unit": "duration_minutes",
                              "relation": "max", "condition": None,
                              "quote": "720 минут"}})
    rule.update(kind="HARD", zone="PAIRING", statement="Смена не более 720 минут.")
    rule.pop("params", None)
    doc["rules"], doc["questions"], doc["objectives"] = [rule], [], []
    _checked_model(doc)
    before = copy.deepcopy(doc)
    for horizon, warning in ((721, False), (720, True), (719, True)):
        hits = fc.check_degenerate({}, doc, {"present": True, "horizon_min": horizon})
        assert len(hits) == int(warning), (horizon, hits)
        if warning:
            assert hits[0]["class"] == fc.DEGENERATE and hits[0]["level"] == fc.WARN
            assert hits[0]["id"] == rule["id"]
            assert "max_duty_duration" in hits[0]["field"]
            assert "720" in hits[0]["message"] and str(horizon) in hits[0]["message"]
    assert fc.check_degenerate({}, doc, {"present": False}) == []
    assert doc == before
    excluded = copy.deepcopy(doc)
    excluded["rules"][0]["excluded_until_data"] = {"question": "Q-EXCLUDED", "revision": 1}
    assert fc.check_degenerate({}, excluded, {"present": True, "horizon_min": 719}) == []


@pytest.mark.parametrize("typed", [False, True])
def test_matching_duplicate_legacy_records_and_their_traces_are_not_collapsed(landed, typed):
    doc, rule = landed
    rule = copy.deepcopy(rule)
    if not typed:
        rule.pop("template_fit")
    rule["params"] = [
        {"name": PARAM, "value": 45, "unit": "duration_minutes", "relation": "min",
         "qualifier": "база", "as_written": quote}
        for quote in ("45 минут", "45 мин")]
    assert m.carrier_problems({"rules": [rule]}) == []
    pkg = _pkg(doc, rule)
    before = copy.deepcopy(pkg)
    assert tg.collect_params(pkg) == [dict(p, rule="R-A", zone="PAIRING")
                                      for p in rule["params"]]
    assert pkg == before
    if typed:
        rule["excluded_until_data"] = {"question": "Q-EXCLUDED", "revision": 1}
        excluded_kind = "excluded_until_data"
    else:
        rule["kind"] = "OUT"
        excluded_kind = "OUT"
    pkg = _pkg(doc, rule)
    before = copy.deepcopy(pkg)
    assert tg.excluded_params(pkg) == [dict(p, rule="R-A", kind=excluded_kind)
                                       for p in rule["params"]]
    assert pkg == before


@pytest.mark.parametrize("reader", ["collect_params", "kind_conflicts", "excluded_params"])
def test_divergent_legacy_number_is_refused_without_rewriting_either_carrier(landed, reader):
    doc, rule = landed
    rule = copy.deepcopy(rule)
    if reader == "excluded_params":
        rule["excluded_until_data"] = {"question": "Q-EXCLUDED", "revision": 1}
    rule["params"] = [{"name": PARAM, "value": 60, "unit": "duration_minutes",
                       "relation": "min", "qualifier": "база", "as_written": "60 минут"}]
    assert m.carrier_problems({"rules": [rule]})
    # Deliberately contradictory carriers, not a legal package: the direct
    # readers must fail closed even when called without the earlier gate.
    pkg = {"model": {"rules": [rule]}}
    before = copy.deepcopy(pkg)
    with pytest.raises((tg.ThresholdGateError, ParamProjectionError)) as error:
        getattr(tg, reader)(pkg)
    assert "R-A" in str(error.value) and PARAM in str(error.value)
    assert pkg == before
