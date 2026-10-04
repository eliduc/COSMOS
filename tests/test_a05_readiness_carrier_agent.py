"""A05 consumer wiring: real readiness verifier -> actual threshold comparison.

Typed landing/2, producer readiness, registry derivation/deep-check/drift and
consumer verdicts stay real. Synthetic stand records feed support_table to
manufacture the native support/1 prerequisite for upstream m.support_problems
and producer.readiness_ladder at SPEC 1.20. They are not stand/solver approval:
registry derivation/deep-check judge declared executors, not stand_verified;
rv.verify recomputes the matrix with the producer's ladder and does not
independently validate those stand records. No downstream sensitivity to
empty/failed stand metadata is established here. Literal fixture expectations
below supplement that shared computation, not independently validate all A05.
This is a consumer integration test, not package export or execution proof.
"""
import copy
import json
from pathlib import Path

import pytest

from cdpm import catalog_bridge as cb, export_task_package as producer, model as m
from template_catalog import bindings
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge  # noqa: F401 (fixture)
from vmp import catalog_access, catalog_requests, readiness_verify as rv
from vmp import registry_derive


ROOT = Path(__file__).resolve().parents[1]
SPEC = "1.20"


@pytest.fixture(autouse=True)
def isolated_consumer(monkeypatch, tmp_path):
    catalog_access.forget()
    monkeypatch.setenv(catalog_requests.ENV_AUTO, "0")
    monkeypatch.setenv(catalog_requests.ENV_OUTBOX, str(tmp_path / "queue"))
    monkeypatch.delenv(catalog_requests.ENV_API_URL, raising=False)
    monkeypatch.delenv(catalog_requests.ENV_TOKEN, raising=False)
    yield
    catalog_access.forget()


def _case(tmp_path, monkeypatch, *, explained=False):
    doc, rule = typed_rule(tmp_path, monkeypatch, "duty_limits", {
        "max_duty_duration": {"value": 720, "unit": "duration_minutes",
                              "relation": "max", "condition": None,
                              "quote": "720 минут"}})
    rule.update(kind="HARD", zone="PAIRING", title="Предел смены",
                statement="Рабочая смена не более 720 минут.")
    rule.pop("params", None)
    doc["rules"], doc["questions"], doc["objectives"] = [rule], [], []
    if explained:
        other = copy.deepcopy(rule)
        other.update(id="R-STRICTER", statement="Рабочая смена не более 660 минут.")
        other["template_fit"]["params"]["max_duty_duration"].update(
            value=660, quote="660 минут", source={"kind": "rule", "rule_id": other["id"]})
        doc["rules"].append(other)
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    cat = cb.catalog()
    sources = {}
    for product, folder, env in (
            ("COSMOS", "vmp", "CREW_COSMOS_BINDINGS"),
            ("CDPM", "cdpm", "CDPM_BINDINGS_FILE")):
        raw = json.loads((ROOT / folder / "bindings.json").read_text(encoding="utf-8"))
        raw.update(catalog_version=cat.version(), catalog_fingerprint=cat.fingerprint())
        for row in raw["bindings"]:
            if row["section"] == "families" and row["id"] == "duty_limits":
                # Synthetic input ONLY to the support/1 table built below;
                # not evidence that registry_derive/deep_check enforce stand
                # metadata or that an operational executor was verified.
                row["stand_verified"] = [
                    {"executor_kind": kind, "task": "isolated-readiness-fixture",
                     "result": "passed", "commit": "a" * 40,
                     "at": "2026-09-16T00:00:00Z"}
                    for kind in sorted({e["kind"] for e in row["executors"]
                                        if e["state"] == "есть"})]
        path = tmp_path / (product.lower() + "-bindings.json")
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        sources[product] = path
        monkeypatch.setenv(env, str(path))
    support = bindings.support_table(
        cat, sources, approved_by="isolated fixture",
        approved_how="synthetic upstream CDPM support/1 fixture, not release",
        computed_at="2026-09-16T00:00:00Z")
    (cat.path / "support.json").write_text(
        json.dumps(support, ensure_ascii=False), encoding="utf-8")
    catalog_access.forget()
    assert m.support_problems(doc, SPEC) == []
    clean = {"tables": {"D1": {
        "Рейсы": {"columns": ["id"], "rows": [{"id": "F1"}]},
        "Экипажи": {"columns": ["id"], "rows": [{"id": "E1"}]},
        "Отпуска": {"columns": ["id", "day"], "rows": []},
    }}}
    manifest = {"spec_version": SPEC,
                "readiness_matrix": producer.readiness_ladder(doc, clean, SPEC)}
    registry_path = tmp_path / "registry.json"
    registry = registry_derive.derive_and_write(
        doc, ROOT, registry_path, scope="isolated A05 readiness carrier test")
    # Check the real writer's result; do not hand-issue covered_full records.
    expected_ids = [rule["id"]] + (["R-STRICTER"] if explained else [])
    assert [(row["rule_id"], row["verdict"]) for row in registry["rules"]] == [
        (rid, "covered_full") for rid in expected_ids], registry
    return manifest, doc, clean, registry_path


def _checked(case, *, expected_rules=1, **kwargs):
    manifest, doc, clean, registry_path = case
    # Independent literal: required data sections present, no open questions,
    # no criterion and one (or explicitly two) unblocked HARD pairing rules.
    # cell_match alone compares two calls to the same producer function.
    expected_pairing = {"state": "feasible_soft", "grounds": {
        "questions": {"zone_total": expected_rules, "open_blocking": 0,
                      "open_soft": 0, "strict_interim_answers": 0},
        "criterion": {"defined": False, "source_rule": None},
        "data_declared": {"complete": True, "missing_fields": []}}, "notes": []}
    assert manifest["readiness_matrix"]["columns"]["pairing"] == expected_pairing
    before = copy.deepcopy((manifest, doc, clean, kwargs))
    result = rv.verify(manifest, doc, clean, ROOT, registry_path, **kwargs)
    assert (manifest, doc, clean, kwargs) == before
    assert result["has_matrix"] and result["verdict"] == "OK", result
    cell = result["columns"]["pairing"]
    assert cell["cell_match"], cell
    assert cell["declared_state"] == cell["recomputed_state"] == "feasible_soft", cell
    assert cell["data_actual_missing"] == cell["criteria_tiers"] == []
    assert cell["registry"]["error"] is None, cell
    assert cell["registry"]["gaps"] == 0 and cell["registry"]["drift"] == [], cell
    assert cell["registry"]["gap_rows"] == []
    assert cell["registry"]["rules_in_zone"] == expected_rules, cell
    return cell


@pytest.mark.parametrize("sent", [720, 780, 660])
def test_real_readiness_consumer_checks_typed_applied_value(tmp_path, monkeypatch, sent):
    case = _case(tmp_path, monkeypatch)
    cell = _checked(case, applied={"max_duty_span_min": sent})
    assert cell["launch_allowed"] is (sent == 720), cell
    if sent == 720:
        assert cell["thresholds_missing"] == cell["why_blocked"] == []
    else:
        assert len(cell["thresholds_missing"]) == len(cell["why_blocked"]) == 1, cell
        words = cell["thresholds_missing"][0]
        assert case[1]["rules"][0]["id"] in words
        assert "max_duty_duration" in words and "720" in words and str(sent) in words


def test_real_readiness_accepts_stricter_value_explained_by_second_hard_rule(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch, explained=True)
    cell = _checked(case, expected_rules=2, applied={"max_duty_span_min": 660})
    assert cell["registry"]["rules_in_zone"] == 2
    assert cell["launch_allowed"]
    assert cell["thresholds_missing"] == cell["why_blocked"] == []


def test_absent_applied_is_unjudged_but_empty_applied_reports_missing_threshold(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    # verify explicitly documents None as a service without the P-1 channel.
    omitted = _checked(case)
    explicit_none = _checked(case, applied=None)
    assert omitted == explicit_none
    assert omitted["launch_allowed"]
    assert omitted["thresholds_missing"] == omitted["why_blocked"] == []
    empty = _checked(case, applied={})
    assert not empty["launch_allowed"]
    assert len(empty["thresholds_missing"]) == len(empty["why_blocked"]) == 1
    words = empty["thresholds_missing"][0]
    assert case[1]["rules"][0]["id"] in words and "max_duty_duration" in words
