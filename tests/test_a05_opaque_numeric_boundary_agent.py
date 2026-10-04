"""AFTER-only A05 reader boundary: opaque labels are not numeric stops.

Real typed landing/catalogue validation and real threshold/formulation readers.
Temporary catalogue copies only; no package acceptance, full formulation,
external solver, service or release is claimed. The shared legacy projection
still refuses opaque labels containing commas; this reporting view must not
inherit that unrelated serialization limit or hide a real carrier conflict.
"""
import copy

import pytest
import template_catalog as tc

from cdpm import catalog_bridge as cb, model as m
from cdpm.param_projection import ParamProjectionError, derive_params_from_landing
from template_catalog import landing
from vmp import catalog_access as ca, formulation_check as fc
from webapp import threshold_gate as tg
from test_cdpm_landing_v2_projection import typed_rule


NAME = "fleet_types"
OPAQUE = ["Airbus A320, special", "B737"]
FACTS = {"present": True, "horizon_min": 1440}


@pytest.fixture(autouse=True)
def isolated_catalogue(monkeypatch, tmp_path):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    cb.forget()
    ca.forget()
    tc.forget_current()
    yield
    cb.forget()
    ca.forget()
    tc.forget_current()


def _fleet(tmp_path, monkeypatch, labels=None):
    labels = OPAQUE if labels is None else labels
    doc, rule = typed_rule(tmp_path, monkeypatch, "fleet_exclusions", {
        NAME: {"value": list(labels), "unit": "code_list", "relation": None,
               "condition": None, "quote": "; ".join(labels)}})
    rule.pop("params", None)
    rule["kind"] = "HARD"
    doc["rules"] = [rule]
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    assert landing.problems(rule, cb.catalog()) == []
    return doc, rule


def _no_numeric_findings(doc):
    pkg = {"model": doc}
    assert fc.check_contradiction(doc, pkg) == []
    assert fc.check_degenerate({}, doc, FACTS) == []
    assert tg.kind_conflicts(pkg) == []


def test_valid_opaque_landing_is_visible_incomparable_not_numeric_stop(
        tmp_path, monkeypatch):
    doc, rule = _fleet(tmp_path, monkeypatch)
    before = copy.deepcopy(doc)
    # Healthy native labels are genuinely unrepresentable by the legacy writer.
    with pytest.raises(ParamProjectionError, match="обозначен"):
        derive_params_from_landing(rule)
    _no_numeric_findings(doc)

    rows = tg.collect_params({"model": doc})
    assert len(rows) == 1 and rows[0]["name"] == NAME
    assert rows[0]["value"] == ["Airbus A320, special", "B737"]
    assert rows[0]["unit"] == "code_list"
    assert rows[0]["condition"] is None
    assert rows[0]["source"] == rule["template_fit"]["params"][NAME]["source"]
    verdicts = tg.compare({"model": doc}, {"opaque_observation": 1},
                          mapping={NAME: "opaque_observation"})
    assert len(verdicts) == 1, verdicts
    assert verdicts[0]["verdict"] == tg.INCOMPARABLE, verdicts
    assert verdicts[0]["params"] == [NAME]
    assert verdicts[0]["rules"] == [rule["id"]]
    assert "единицы" in verdicts[0]["note"], verdicts
    rows[0]["value"].append("not the source")
    rows[0]["source"]["rule_id"] = "not the source"
    assert doc == before


def test_excluded_until_data_opaque_landing_remains_a_visible_copy(
        tmp_path, monkeypatch):
    doc, rule = _fleet(tmp_path, monkeypatch)
    rule["excluded_until_data"] = {"question": "Q-NO-DATA", "revision": 1}
    assert m.validate_schema(doc) == []
    before = copy.deepcopy(doc)
    _no_numeric_findings(doc)
    assert tg.collect_params({"model": doc}) == []
    rows = tg.excluded_params({"model": doc})
    assert len(rows) == 1
    assert rows[0]["kind"] == "excluded_until_data"
    assert rows[0]["rule"] == rule["id"]
    assert rows[0]["name"] == NAME and rows[0]["value"] == OPAQUE
    assert rows[0]["condition"] is None
    assert NAME in tg.report([], rows) and "вне счёта" in tg.report([], rows)
    rows[0]["value"].append("not the source")
    assert doc == before


@pytest.mark.parametrize("typed", [False, True], ids=["legacy-only", "typed-and-legacy"])
def test_duplicate_code_records_keep_multiplicity_trace_and_legal_scope(
        tmp_path, monkeypatch, typed):
    # These labels are legacy-representable: the comma is a delimiter here.
    doc, rule = _fleet(tmp_path, monkeypatch, ["Airbus A320", "B737"])
    rule["params"] = [{
        "name": NAME, "value": "Airbus A320, B737", "unit": "code_list",
        "relation": "eq", "as_written": quote}
        for quote in ("Airbus A320, B737", "Парк Airbus A320, B737")]
    if not typed:
        del rule["template_fit"]
        for record, qualifier in zip(rule["params"], ("ST1", "ST2")):
            record["qualifier"] = qualifier
    assert m.validate_schema(doc) == []
    assert m.reconcile_carriers(rule) == []
    before = copy.deepcopy(doc)
    rows = tg.projected_params(rule)
    assert len(rows) == 2
    assert [r["as_written"] for r in rows] == [p["as_written"] for p in rule["params"]]
    if typed:
        assert all(r["value"] == ["Airbus A320", "B737"] for r in rows)
        assert all("condition" in r and r["condition"] is None for r in rows)
    else:
        assert rows == rule["params"]
        assert [r["qualifier"] for r in rows] == ["ST1", "ST2"]
    _no_numeric_findings(doc)
    rows[0]["as_written"] = "not the source"
    assert doc == before


def test_opaque_reader_does_not_hide_disagreeing_legacy_carrier(tmp_path, monkeypatch):
    doc, rule = _fleet(tmp_path, monkeypatch, ["Airbus A320", "B737"])
    rule["params"] = [{"name": NAME, "value": "Airbus A320", "unit": "code_list",
                       "relation": "eq", "as_written": "Airbus A320"}]
    assert m.validate_schema(doc) == []
    assert m.reconcile_carriers(rule), "the negative input must actually disagree"
    before = copy.deepcopy(doc)
    with pytest.raises(tg.ThresholdGateError):
        tg.collect_params({"model": doc})
    findings = fc.check_contradiction(doc)
    assert findings and all(f["level"] == fc.STOP for f in findings), findings
    assert doc == before


def test_missing_catalogue_becomes_explicit_stop_after_healthy_read(
        tmp_path, monkeypatch):
    doc, rule = _fleet(tmp_path, monkeypatch)
    assert tg.collect_params({"model": doc})[0]["value"] == OPAQUE
    _no_numeric_findings(doc)
    before = copy.deepcopy(doc)
    missing = tmp_path / "missing-catalogue"
    assert not missing.exists()
    monkeypatch.setenv(cb.ENV_CATALOG, str(missing))
    cb.forget()
    ca.forget()
    tc.forget_current()
    with pytest.raises(tg.ThresholdGateError, match="каталог") as refused:
        tg.collect_params({"model": doc})
    assert rule["id"] in str(refused.value)
    for findings in (fc.check_contradiction(doc), fc.check_degenerate({}, doc, FACTS)):
        assert len(findings) == 1, findings
        assert findings[0]["level"] == fc.STOP, findings
        assert findings[0]["id"] == rule["id"]
        assert "каталог" in findings[0]["message"]
    assert doc == before
