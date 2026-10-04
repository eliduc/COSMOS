"""Synthetic book + source freeze through real package/measure/receiver/button.

No execution verdict is mocked. The fixture's one DATA equality is translated
explicitly and checked by local Z3, as documented by real_soft_package.
The service-health and remote coverage seams are explicit; no service, builder,
JVM, customer run or operational solver is started. Stopping at an unconfigured
builder is evidence of reaching that boundary, NOT successful execution.
"""
import json
from pathlib import Path

import pytest

from cdpm import export_task_package as producer
from cdpm.gates import gate_task_package as gate
from test_cdpm_landing_v2_export import real_soft_package
from test_k01_semantic_transition import clean_bridge
from test_k06_button_boundary import NoExecution
from vmp.run import button
from webapp import task_package as receiver


def source_freeze(run, doc):
    """Explicit local source-document fixture, not a customer freeze/release."""
    grouped = {}
    for rule in doc["rules"]:
        source = rule["sources"][0]
        text = rule["statement"]
        if rule.get("kind") == "SOFT":
            text += " Вес штрафа " + str(rule["weight"]) + "."
        grouped.setdefault(source["sid"], {}).setdefault(source["anchor"], []).append(text)
    freeze = run / "freeze"
    (freeze / "clauses").mkdir(parents=True)
    files = []
    for sid, anchors in grouped.items():
        clauses = [{"anchor": anchor, "text": "\n".join(texts)}
                   for anchor, texts in anchors.items()]
        body = {"sid": sid, "file": "synthetic-source.md", "sha256": "0" * 64,
                "clauses": clauses}
        path = freeze / "clauses" / (sid + ".json")
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        files.append({"sid": sid})
    (freeze / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    return freeze


@pytest.mark.parametrize("changed_source", [False, True])
def test_real_source_measurement_reaches_builder_boundary_without_fabricated_pass(
        tmp_path, monkeypatch, changed_source):
    parts = real_soft_package(tmp_path, monkeypatch, "1.20", with_data=True)
    doc, cat, run, model_path, package_path = parts[0], parts[1], parts[4], parts[5], parts[-1]
    original_model = model_path.read_bytes()
    freeze = source_freeze(run, doc)
    original_clauses = {p.name: p.read_bytes() for p in (freeze / "clauses").glob("*.json")}

    # Real, isolated bindings point at the genuine temporary catalogue.
    raw = json.loads((Path(__file__).resolve().parents[1] / "vmp" / "bindings.json").read_text(encoding="utf-8"))
    raw.update(catalog_version=cat.version(), catalog_fingerprint=cat.fingerprint())
    live = tmp_path / "integral-bindings.json"
    live.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CREW_COSMOS_BINDINGS", str(live))

    body = producer.export_zip(run)
    package_path.write_bytes(body)
    problems, _ = gate.check(package_path)
    assert problems == [], problems
    pkg = receiver.load(package_path, run_gate=False)
    assert pkg["manifest"]["measure"]["available"] is True
    assert pkg["manifest"]["measure"]["ok"] is True
    assert pkg["manifest"]["coverage"]["values_without_addressee"] == 0
    assert pkg["manifest"]["coverage"]["normative_without_rows"] == 0
    payload = receiver.to_payload(pkg)
    assert len(payload["employees"]) == len(payload["flights"]) == 1

    if changed_source:
        path = next((freeze / "clauses").glob("*.json"))
        prior = path.read_text(encoding="utf-8")
        assert "960" in prior
        path.write_text(prior.replace("960", "961"), encoding="utf-8")
        with pytest.raises(producer.TaskPackageError):
            producer.export_zip(run)
    else:
        delivery = button.orchestrate(run, NoExecution(), task="pairing",
            mode="feasibility-hard", package_path=package_path,
            coverage_fn=lambda *_: None)
        assert delivery["detail"] == "построитель не сконфигурирован у кнопки", delivery
        assert "ortools-построителем" in delivery["verdict"], delivery
        for step in ("Ш-2м", "Ш-2и"):
            assert any(row["step"] == step and row["status"] == "пройден"
                       for row in delivery["journal"]), delivery["journal"]
        assert {p.name: p.read_bytes() for p in (freeze / "clauses").glob("*.json")} == original_clauses
    assert model_path.read_bytes() == original_model


@pytest.mark.parametrize("approved", [False, True])
def test_modern_support_gap_does_not_become_a_customer_missing_data_question(
        tmp_path, monkeypatch, approved):
    """F2 remains distinct from execution permission under native support/1."""
    from cdpm import model as m
    from test_k06_scope_agent import healthy_model, native_support, publish, FAMILY

    doc, cat = healthy_model(tmp_path, monkeypatch)
    records = {("families", FAMILY)}
    support = native_support(tmp_path, monkeypatch, cat, records)
    if not approved:
        for cell in support["cells"]:
            if (cell["section"], cell["record_id"]) == ("families", FAMILY):
                cell["approved"] = None
    publish(cat, doc, support, records)
    monkeypatch.setattr(producer, "SPEC_VERSION", "1.20")
    rule = doc["rules"][0]
    report = {"checks": [{"id": "C-SCALE", "status": "не скомпилирована",
        "reason": "нет листа «Смены»", "basis": {"rule": rule["id"]}}], "findings": []}
    assert m.ensure_missing_data_questions(doc, report) == []
    assert doc["questions"] == [] and not rule.get("blockers")
    assert report["judged_on_solution"][0]["rule"] == rule["id"]
    problems = m.support_problems(doc, "1.20")
    assert bool(problems) is (not approved)
    if not approved:
        assert all(p["code"] == "support-missing" for p in problems)
    for task in ("pairing", "rostering"):
        assert m.readiness_matrix(doc, "1.20")[task]["feasibility-hard"]["ready"] is approved
