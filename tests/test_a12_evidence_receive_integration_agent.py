"""A12 authored evidence across the actual modern receive-handler boundary.

Composition of the existing SOFT package/A12 freeze fixtures and registered
in-memory handler. No judge or storage verdict is mocked. The temporary
catalogue has no released executor proof and the source has no data book.
There are zero HARD rules: neither translation nor SMT/operational solving
is permitted. This is not socket HTTP, middleware or independent expertise.

The contract here is evidence preservation, unchanged mode declarations and
the existing member-checksum refusal, NOT semantic re-auditing by COSMOS.
"""
import copy
import hashlib
import json
import zipfile

import pytest

from cdpm import export_task_package as ep, freeze, model, obligations as ob, smt_verify
from cdpm.gates import gate_task_package as gate
from test_a08_receive_handler_agent import handler, _body, _complete, _request
from test_cdpm_a12_obligations_integration import _raw_tree
from test_cdpm_landing_v2_export import real_soft_package
from test_k01_semantic_transition import clean_bridge  # noqa: F401 (fixture)
from webapp import task_package as receiver


def _authored_files(tmp_path, monkeypatch, revision):
    seed = real_soft_package(tmp_path / "seed", monkeypatch, revision)
    with zipfile.ZipFile(seed[-1]) as archive:
        doc = json.loads(archive.read("model.json"))
    rule, = doc["rules"]
    clause = "Продление смены не более 960 минут для ST1, ST2 только с согласия экипажа."
    assert rule["kind"] == "SOFT" and rule["statement"] == clause
    assert rule["template_fit"]["schema_version"] == "landing/2"
    run = tmp_path / "run-a12-receive"
    source = run / "src" / "synthetic-soft.md"
    source.parent.mkdir(parents=True)
    source.write_text(clause + "\n", encoding="utf-8")
    frozen = freeze.freeze([source], run / "freeze")
    assert frozen["files"][0]["clause_count"] == 1
    doc["sources"] = [{key: frozen["files"][0][key] for key in ("sid", "file", "sha256")}]
    rule["sources"] = [{"sid": "S1", "anchor": "строка 1"}]
    assert model.validate_schema(doc) == model.validate_template_fit(doc) == []
    assert model.validate_fit_quotes(doc, {("S1", "строка 1"): clause}) == []
    path = run / "extract" / "model.json"
    path.parent.mkdir()
    original_model = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    path.write_bytes(original_model)

    def forbidden(*_args, **_kwargs):
        pytest.fail("SOFT-only A12 evidence cannot call a translator or solver")

    consistency = smt_verify.verify(doc, forbidden,
        model_sha256=smt_verify.model_fingerprint(original_model))
    assert consistency["n_rules"] == consistency["n_translated"] == 0
    smt_verify.write_report(run, consistency)
    without_evidence = ep.build_package(run)
    snapshot = ob.capture_run_snapshot(run, model_bytes=without_evidence["model.json"])
    unit = ob.document_unit("S1", "строка 1")
    item = {"id": "O-EXTENSION", "subject": "экипаж", "modality": "permission",
            "statement": clause, "conditions": [], "exceptions": [], "depends_on": [],
            "sources": [{"unit": unit, "fragments": [
                {"start": 0, "end": len(clause.encode("utf-8")), "quote": clause}]}]}
    # Authored declarations intentionally contradict independence. They are
    # neither actual independent readings nor a semantic decomposition oracle.
    ledger = {"schema_version": ob.SCHEMA, "snapshot": snapshot,
              "scope": {"included": [unit], "excluded": []},
              "readers": [ob.seal_reader({
                  "id": rid, "actor": "same synthetic test author", "method": "fixture", "version": "1",
                  "input_sha256": ob.fingerprint(snapshot),
                  "exposure": {"other_reader": True, "model": True},
                  "reviewed_units": [unit], "obligations": [copy.deepcopy(item)]})
                  for rid in ("LEFT", "RIGHT")],
              "links": [{"reader": "LEFT", "obligation": "O-EXTENSION",
                  "model_sha256": snapshot["model_sha256"], "status": "declared",
                  "author": "fixture author", "reason": "explicit authored correspondence",
                  "targets": [{"collection": "rules", "row_id": rule["id"],
                               "pointer": "/statement", "component": "statement"}]}],
              "comparisons": [], "expert_review": None}
    authored = (json.dumps(ledger, ensure_ascii=False, indent=2) + "\n  ").encode("utf-8")
    ledger_path = run / ob.LEDGER_REL
    ledger_path.parent.mkdir(exist_ok=True)
    ledger_path.write_bytes(authored)
    protected = _raw_tree(run)
    files = ep.build_package(run)
    assert files["model.json"] == without_evidence["model.json"]
    assert files["obligations-ledger.json"] == authored
    report = json.loads(files["obligations-report.json"])
    assert (report["form"], report["freshness"]) == ("valid", "current"), report
    assert report["meaning"] == "not_proven"
    assert report["independence"] == "contradicted_by_declaration"
    assert report["expert_review"] == {"status": "not_reviewed"}
    assert [(row["total"], row["linked"], row["missing"]) for row in report["readers"]] == [
        (1, 1, []), (1, 0, ["O-EXTENSION"])]
    assert report["package_binding"] == {
        "model_sha256": hashlib.sha256(files["model.json"]).hexdigest(),
        "ledger_sha256": hashlib.sha256(authored).hexdigest()}
    declared = json.loads(files["manifest.json"])
    assert declared["spec_version"] == revision
    assert declared["obligations"] == {"ledger": "obligations-ledger.json",
        "report": "obligations-report.json", "form": "valid", "freshness": "current",
        "meaning": "not_proven", "independence": "contradicted_by_declaration"}
    # A12 evidence neither upgrades nor newly blocks the existing modes.
    assert declared["allowed_modes"] == json.loads(without_evidence["manifest.json"])["allowed_modes"]
    assert _raw_tree(run) == protected
    assert seed[5].read_bytes() == seed[6] and seed[-1].read_bytes() == seed[-2]
    return run, files, protected


@pytest.mark.usefixtures("declared_test_layout")
@pytest.mark.parametrize("revision", ["1.20", "1.21"])
def test_authored_evidence_survives_registered_receive_without_promoting_readiness(
        tmp_path, monkeypatch, handler, revision):
    application, channel, dispatched = handler
    run, files, protected = _authored_files(tmp_path, monkeypatch, revision)
    body = ep.write_zip(files)
    package_path = tmp_path / "authored-evidence.zip"
    package_path.write_bytes(body)
    problems, _ = gate.check(package_path)
    assert problems == [], problems
    loaded = receiver.load(package_path, run_gate=False)
    assert loaded["manifest"]["spec_version"] == revision
    assert loaded["landing_projection"]["archive_only"] is False
    assert loaded["objective_projection"]["judged"] is True
    assert loaded["manifest"]["obligations"] == json.loads(files["manifest.json"])["obligations"]

    request, body_reads = _request(application, body, revision=revision)
    assert request.headers["x-spec-version"] == revision
    response = _complete(application.api_task_package_receive(request))
    healthy = _body(response)
    assert response.status_code == 200 and healthy["verdict"] in {"OK", "MISMATCH"}, healthy
    assert body_reads == [True] and len(dispatched) == 1
    assert healthy["spec_version"] == revision
    assert healthy["cosmos_run"] is None and healthy["run_note_code"] == "storage-off"
    assert set(healthy["mg"]) == {"pairing", "rostering"}, healthy
    assert all(not cell["launch_allowed"] for cell in healthy["mg"].values()), healthy
    inbox = channel.inbox_dir()
    accepted = inbox / hashlib.sha256(body).hexdigest()
    accepted_path = accepted / channel.PACKAGE_NAME
    assert accepted_path.read_bytes() == body
    delivered = receiver.load(accepted_path, run_gate=False)
    assert delivered["model"] == loaded["model"]
    assert delivered["manifest"] == loaded["manifest"]
    with zipfile.ZipFile(accepted_path) as archive:
        for name in ("obligations-ledger.json", "obligations-report.json", "freeze/clauses/S1.json"):
            assert archive.read(name) == files[name]
        retained = json.loads(archive.read("obligations-report.json"))
    assert retained["meaning"] == "not_proven"
    assert retained["independence"] == "contradicted_by_declaration"
    assert retained["expert_review"] == {"status": "not_reviewed"}
    with pytest.raises(receiver.TaskPackageRefused, match="нет очищенных данных"):
        receiver.to_payload(delivered)
    accepted_before = _raw_tree(accepted)
    current_before = (inbox / channel.CURRENT_NAME).read_bytes()
    registry = channel.registry_path()
    registry_before = registry.read_bytes() if registry.is_file() else None

    # Change only the report bytes. Its content remains valid JSON, but its
    # exact member hash no longer matches the original manifest declaration.
    damaged = dict(files)
    damaged["obligations-report.json"] += b" "
    changed = ep.write_zip(damaged)
    bad_path = tmp_path / "changed-evidence.zip"
    bad_path.write_bytes(changed)
    problems, _ = gate.check(bad_path)
    assert problems == ["obligations-report.json: sha256 не совпадает с манифестом"], problems
    with pytest.raises(receiver.TaskPackageRefused) as direct_refusal:
        receiver.load(bad_path, run_gate=False)
    assert "контрольная сумма obligations-report.json не совпала" in str(direct_refusal.value)
    request, body_reads = _request(application, changed, revision=revision)
    response = _complete(application.api_task_package_receive(request))
    refused = _body(response)
    assert response.status_code == 422 and refused["verdict"] == "REFUSED", refused
    assert refused["detail"] == str(direct_refusal.value)
    assert body_reads == [True] and len(dispatched) == 2
    assert refused["sha"] == hashlib.sha256(changed).hexdigest()
    assert not (inbox / refused["sha"]).exists()
    assert not list(inbox.glob("incoming-*.zip"))
    assert _raw_tree(accepted) == accepted_before
    assert (inbox / channel.CURRENT_NAME).read_bytes() == current_before
    assert (registry.read_bytes() if registry.is_file() else None) == registry_before
    assert _raw_tree(run) == protected
    assert package_path.read_bytes() == body and bad_path.read_bytes() == changed
