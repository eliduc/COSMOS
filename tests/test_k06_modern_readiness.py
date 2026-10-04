"""Future support/1 readiness, real temporary evidence, no release/solver."""
import json
from pathlib import Path

import pytest

from cdpm import model as m, export_task_package as producer
from cdpm import catalog_bridge as cb
from cdpm.gates import gate_task_package as gate
from template_catalog import bindings
from test_k06_support_execution import executable
from test_k06_support_manifest import FAMILY
from test_k01_semantic_transition import clean_bridge


def test_modern_criterion_approval_blocks_only_optimization_and_preserves_archive(tmp_path, monkeypatch):
    pkg, support, _cell, path, _live = executable(
        tmp_path, monkeypatch, criterion="min_person_days", task="pairing")
    doc = pkg["model"]
    doc["rules"], doc["questions"] = [], []
    # The metric references the real catalogue criterion; its prose is also
    # a valid producer fallback, so export is not stopped for lack of a goal.
    doc["objectives"][0].update(title="Минимум человеко-дней",
                               statement="Минимум человеко-дней — 3 условных единицы")
    monkeypatch.setattr(producer, "SPEC_VERSION", "1.20")
    assert m.readiness_matrix(doc)["pairing"]["optimization"]["ready"]
    assert "optimization" in producer.allowed_modes(doc)[0]["pairing"]
    chosen = next(c for c in support["cells"] if
        (c["section"], c["record_id"], c["kind"], c["product"]) ==
        ("criteria", "min_person_days", "builder_method", "COSMOS"))
    assert chosen["approved"] and chosen["state"] == "есть"
    chosen["approved"] = None
    path.write_text(json.dumps(support, ensure_ascii=False), encoding="utf-8")
    matrix = m.readiness_matrix(doc)
    assert not matrix["pairing"]["optimization"]["ready"]
    assert matrix["pairing"]["feasibility-soft"]["ready"]
    assert matrix["pairing"]["feasibility-hard"]["ready"]
    assert m.readiness(doc)["pairing_ready"]  # basic feasibility, not optimization
    assert "optimization" not in producer.allowed_modes(doc)[0]["pairing"]
    assert "optimization" in producer.allowed_modes(doc, spec_version="1.18")[0]["pairing"]
    problems = m.readiness_barriers(doc)
    assert any(p["code"] == "support-missing" and p["item"] == "criteria/min_person_days"
               for p in problems)
    assert m.barrier_affects(next(p for p in problems if p["item"] == "criteria/min_person_days")) == {
        ("pairing", "optimization")}


@pytest.mark.parametrize("rule_kind", ["HARD", "SOFT"])
def test_modern_family_uses_both_products_and_preserves_partial_modes(tmp_path, monkeypatch, rule_kind):
    pkg, _support, _cell, path, cosmos_live = executable(tmp_path, monkeypatch)
    doc, cat = pkg["model"], cb.catalog()
    doc["objectives"], doc["questions"] = [], []
    doc["rules"][0].update(kind=rule_kind, weight=1)
    sources = {"COSMOS": cosmos_live, "CDPM": tmp_path / "cdpm-bindings.json"}
    cdpm = json.loads((Path(__file__).resolve().parents[1] / "cdpm" / "bindings.json").read_text(encoding="utf-8"))
    cdpm.update(catalog_version=cat.version(), catalog_fingerprint=cat.fingerprint())
    cdpm["bindings"] = [r for r in cdpm["bindings"] if
                        r["section"] == "families" and r["id"] == FAMILY]
    sources["CDPM"].write_text(json.dumps(cdpm, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CDPM_BINDINGS_FILE", str(sources["CDPM"]))
    for live in sources.values():
        raw = json.loads(live.read_text(encoding="utf-8"))
        for row in raw["bindings"]:
            # All stand evidence here is explicitly synthetic local test data.
            row["stand_verified"] = [{"executor_kind": kind, "task": "isolated-fixture",
                "result": "passed", "commit": "a" * 40, "at": "2026-09-16T00:00:00Z"}
                for kind in sorted({e["kind"] for e in row["executors"] if e["state"] == "есть"})]
        live.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    support = bindings.support_table(cat, sources, approved_by="isolated fixture",
        approved_how="synthetic test, not release", computed_at="2026-09-16T00:00:00Z")
    path.write_text(json.dumps(support, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(producer, "SPEC_VERSION", "1.20")
    assert m.support_problems(doc) == []
    assert m.readiness_matrix(doc)["rostering"]["feasibility-soft"]["ready"]
    chosen = next(c for c in support["cells"] if
        (c["section"], c["record_id"], c["kind"], c["product"]) ==
        ("families", FAMILY, "check_template", "CDPM"))
    assert chosen["state"] == "есть" and chosen["approved"]
    chosen["approved"] = None
    path.write_text(json.dumps(support, ensure_ascii=False), encoding="utf-8")
    problems = m.support_problems(doc)
    assert len(problems) == 1 and problems[0]["item"] == "families/" + FAMILY
    assert problems[0]["kind"] == "check_template"
    matrix = m.readiness_matrix(doc)
    assert matrix["pairing"]["feasibility-hard"]["ready"]
    assert not matrix["rostering"]["feasibility-soft"]["ready"]
    assert matrix["rostering"]["feasibility-hard"]["ready"] is (rule_kind == "SOFT")
    manifest = {"spec_version": "1.20", "allowed_modes": {"rostering": ["feasibility-hard"]}}
    assert bool(gate._check_barriers(doc, manifest, {})) is (rule_kind == "HARD")
    assert m.support_problems(doc, "1.18") == []
