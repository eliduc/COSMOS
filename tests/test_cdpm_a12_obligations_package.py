"""A12 evidence serialization using real run snapshots, not semantic approval.

Focused tests exercise the producer's evidence helper. The final cases also
run actual SOFT-only ZIPs, producer gate and receiver with zero HARD rules:
no translation, operational solver or fabricated consistency verdict.
"""
import hashlib
import json

import pytest

from cdpm import export_task_package as ep, obligations as ob
from test_cdpm_a12_obligations_integration import _case, _raw_tree
from test_k01_semantic_transition import clean_bridge  # noqa: F401


def _emit(run, model_bytes=None):
    raw = model_bytes if model_bytes is not None else (run / "extract/model.json").read_bytes()
    files, summary = ep._obligations_evidence(run, raw)
    report = json.loads(files["obligations-report.json"]) if files else None
    return files, summary, report


def test_optional_absence_does_not_invent_empty_success(tmp_path):
    run, _ = _case(tmp_path)
    (run / ob.LEDGER_REL).unlink()
    before = _raw_tree(run)
    assert _emit(run) == ({}, None, None)
    assert _raw_tree(run) == before


def test_exact_authored_bytes_current_gaps_and_deterministic_output(tmp_path):
    run, _ = _case(tmp_path)
    before = _raw_tree(run)
    files, summary, report = _emit(run)
    assert files["obligations-ledger.json"] == before[ob.LEDGER_REL]
    assert report["form"] == "valid" and report["freshness"] == "current"
    assert report["readers"][0]["missing"] == ["O2"]
    assert summary["meaning"] == "not_proven"
    assert summary["independence"] == "declared_only"
    assert report["package_binding"] == {
        "model_sha256": hashlib.sha256(before["extract/model.json"]).hexdigest(),
        "ledger_sha256": hashlib.sha256(before[ob.LEDGER_REL]).hexdigest()}
    assert _emit(run) == (files, summary, report)
    assert _raw_tree(run) == before


@pytest.mark.parametrize("change", ["value", "format"])
def test_projected_model_is_judged_by_its_bytes_not_old_disk_fingerprint(tmp_path, change):
    run, _ = _case(tmp_path)
    before = _raw_tree(run)
    if change == "format":
        emitted = before["extract/model.json"] + b"\n"
    else:
        doc = json.loads(before["extract/model.json"])
        doc["rules"][0]["params"][0]["value"] = 610
        emitted = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    _, summary, report = _emit(run, emitted)
    assert summary["freshness"] == "stale_or_unresolved"
    assert report["readers"][0]["linked"] == 0
    assert report["package_binding"]["model_sha256"] == hashlib.sha256(emitted).hexdigest()
    assert _raw_tree(run) == before


def test_invalid_saved_evidence_is_transferred_as_invalid_not_repaired(tmp_path):
    run, _ = _case(tmp_path)
    raw = b'{"schema_version":"first","schema_version":"second"}'
    (run / ob.LEDGER_REL).write_bytes(raw)
    before = _raw_tree(run)
    files, summary, report = _emit(run)
    assert files["obligations-ledger.json"] == raw
    assert summary["form"] == "unavailable"
    assert summary["meaning"] == "not_proven" and report["readers"] == []
    assert _raw_tree(run) == before


def test_real_ledger_change_during_report_refuses_mixed_evidence(tmp_path, monkeypatch):
    run, _ = _case(tmp_path)
    path = run / ob.LEDGER_REL
    original = ob.run_report
    reached = []

    def concurrent_edit(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result["freshness"] == "current"
        reached.append(result["ledger"]["sha256"])
        path.write_bytes(path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(ob, "run_report", concurrent_edit)
    with pytest.raises(ep.TaskPackageError, match="изменилась") as raised:
        _emit(run)
    assert raised.value.code == "obligation-evidence-unavailable"
    assert len(reached) == 1


def test_report_must_describe_the_exact_freeze_clauses_already_copied_into_zip(tmp_path):
    run, _ = _case(tmp_path)
    model_bytes = (run / "extract/model.json").read_bytes()
    original = (run / "freeze/clauses/S1.json").read_bytes()
    copied = {"freeze/clauses/S1.json": original}
    files, summary = ep._obligations_evidence(run, model_bytes, package_files=copied)
    assert summary["freshness"] == "current" and files
    for invalid in ({}, {"freeze/clauses/S1.json": original + b" "}):
        with pytest.raises(ep.TaskPackageError) as error:
            ep._obligations_evidence(run, model_bytes, package_files=invalid)
        assert error.value.code == "obligation-evidence-unavailable"
    assert (run / "freeze/clauses/S1.json").read_bytes() == original


@pytest.mark.parametrize("damaged", [{}, [], {"files": None}, {"files": [{}]}, {"files": [1]}],
                         ids=["missing-files-original-base", "nonobject-manifest", "null-files",
                              "missing-sid", "nonobject-entry"])
def test_concurrent_freeze_manifest_damage_is_an_addressed_package_refusal(tmp_path, monkeypatch, damaged):
    run, _ = _case(tmp_path)
    model_bytes = (run / "extract/model.json").read_bytes()
    copied = {"freeze/clauses/S1.json": (run / "freeze/clauses/S1.json").read_bytes()}
    files, summary = ep._obligations_evidence(run, model_bytes, package_files=copied)
    assert files and summary["freshness"] == "current"
    authored = (run / ob.LEDGER_REL).read_bytes()
    original = ob.run_report
    reached = []

    def damage_after_real_report(*args, **kwargs):
        report = original(*args, **kwargs)
        assert (report["form"], report["freshness"]) == ("valid", "current"), report
        reached.append(report["input_binding"])
        # Inject an actual competing disk write, not a replacement verdict.
        (run / "freeze/manifest.json").write_bytes(json.dumps(damaged).encode("utf-8"))
        return report

    monkeypatch.setattr(ob, "run_report", damage_after_real_report)
    with pytest.raises(ep.TaskPackageError) as error:
        ep._obligations_evidence(run, model_bytes, package_files=copied)
    assert error.value.code == "obligation-evidence-unavailable"
    assert len(reached) == 1
    assert (run / ob.LEDGER_REL).read_bytes() == authored
    assert (run / "extract/model.json").read_bytes() == model_bytes
    assert (run / "freeze/clauses/S1.json").read_bytes() == copied["freeze/clauses/S1.json"]


@pytest.mark.usefixtures("declared_test_layout")
def test_actual_zip_manifest_gate_and_receiver_preserve_unavailable_evidence(tmp_path, monkeypatch):
    from cdpm.gates import gate_task_package as gate
    from test_cdpm_landing_v2_export import real_soft_package
    from webapp import task_package as receiver
    parts = real_soft_package(tmp_path, monkeypatch, "1.18")
    run, path, original_model, package_path = parts[4], parts[5], parts[6], parts[-1]
    healthy = receiver.load(package_path, run_gate=False)
    assert "obligations" not in healthy["manifest"]
    assert gate.check(package_path)[0] == []
    authored = b'{"schema_version":"x","schema_version":"y"}'
    ledger = run / ob.LEDGER_REL
    ledger.parent.mkdir(exist_ok=True)
    ledger.write_bytes(authored)
    files = ep.build_package(run)
    manifest = json.loads(files["manifest.json"])
    assert manifest["obligations"]["form"] == "unavailable"
    assert manifest["obligations"]["meaning"] == "not_proven"
    for name in ("obligations-ledger.json", "obligations-report.json"):
        row, = [r for r in manifest["files"] if r["name"] == name]
        assert row["sha256"] == hashlib.sha256(files[name]).hexdigest()
        assert row["bytes"] == len(files[name])
    assert files["obligations-ledger.json"] == authored
    report = json.loads(files["obligations-report.json"])
    assert report["package_binding"]["model_sha256"] == hashlib.sha256(files["model.json"]).hexdigest()
    package_path.write_bytes(ep.write_zip(files))
    assert gate.check(package_path)[0] == []
    delivered = receiver.load(package_path, run_gate=False)
    assert delivered["manifest"]["obligations"] == manifest["obligations"]
    assert delivered["model"] == healthy["model"]
    assert path.read_bytes() == original_model and ledger.read_bytes() == authored
    assert ep.build_package(run) == files

    files["obligations-report.json"] += b" "
    package_path.write_bytes(ep.write_zip(files))
    problems, _ = gate.check(package_path)
    assert any("obligations-report.json" in str(p) for p in problems), problems


@pytest.mark.usefixtures("declared_test_layout")
def test_actual_soft_zip_gate_and_receiver_preserve_valid_authored_ledger(tmp_path, monkeypatch):
    import copy
    import zipfile

    from cdpm import freeze, model, smt_verify
    from cdpm.gates import gate_task_package as gate
    from test_cdpm_landing_v2_export import real_soft_package
    from webapp import task_package as receiver

    # Reuse the released-origin catalogue and actual migration fixture. The
    # new source below is entirely local; no external historical ZIP is read.
    seed = real_soft_package(tmp_path / "seed", monkeypatch, "1.18")
    with zipfile.ZipFile(seed[-1]) as archive:
        doc = json.loads(archive.read("model.json"))
    rule, = doc["rules"]
    clause = "Продление смены не более 960 минут для ST1, ST2 только с согласия экипажа."
    assert rule["kind"] == "SOFT" and rule["statement"] == clause
    assert rule["template_fit"]["schema_version"] == "landing/2"
    run = tmp_path / "run-authored-soft"
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
        pytest.fail("A12 SOFT-only evidence must not call a translator or solver")

    consistency = smt_verify.verify(doc, forbidden,
        model_sha256=smt_verify.model_fingerprint(original_model))
    assert consistency["n_rules"] == consistency["n_translated"] == 0, consistency
    smt_verify.write_report(run, consistency)
    before_ledger = ep.build_package(run)
    snapshot = ob.capture_run_snapshot(run, model_bytes=before_ledger["model.json"])
    unit = ob.document_unit("S1", "строка 1")
    item = {"id": "O-EXTENSION", "subject": "экипаж", "modality": "permission",
            "statement": clause, "conditions": [], "exceptions": [], "depends_on": [],
            "sources": [{"unit": unit, "fragments": [
                {"start": 0, "end": len(clause.encode("utf-8")), "quote": clause}]}]}
    # These are two authored fixture records, not two independent experts.
    # The full conditioned statement is linked as one authored component;
    # this is deliberately NOT a semantic-decomposition/meaning oracle.
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
    assert files["model.json"] == before_ledger["model.json"]
    assert files["obligations-ledger.json"] == authored
    report = json.loads(files["obligations-report.json"])
    assert (report["form"], report["freshness"]) == ("valid", "current"), report
    assert report["meaning"] == "not_proven"
    assert report["independence"] == "contradicted_by_declaration"
    assert report["expert_review"] == {"status": "not_reviewed"}
    assert [(r["total"], r["linked"], r["missing"]) for r in report["readers"]] == [
        (1, 1, []), (1, 0, ["O-EXTENSION"])]
    assert report["package_binding"] == {
        "model_sha256": hashlib.sha256(files["model.json"]).hexdigest(),
        "ledger_sha256": hashlib.sha256(authored).hexdigest()}
    manifest = json.loads(files["manifest.json"])
    assert manifest["spec_version"] == "1.18"
    assert manifest["obligations"] == {"ledger": "obligations-ledger.json",
        "report": "obligations-report.json", "form": "valid", "freshness": "current",
        "meaning": "not_proven", "independence": "contradicted_by_declaration"}
    for name in ("obligations-ledger.json", "obligations-report.json", "freeze/clauses/S1.json"):
        row, = [entry for entry in manifest["files"] if entry["name"] == name]
        assert (row["sha256"], row["bytes"]) == (hashlib.sha256(files[name]).hexdigest(), len(files[name]))
    package_path = tmp_path / "authored-soft.zip"
    body = ep.write_zip(files)
    package_path.write_bytes(body)
    problems, _ = gate.check(package_path)
    assert problems == [], problems
    delivered = receiver.load(package_path, run_gate=False)
    assert delivered["manifest"]["obligations"] == manifest["obligations"]
    assert delivered["model"] == json.loads(files["model.json"])
    assert delivered["landing_projection"]["archive_only"] is False
    with zipfile.ZipFile(package_path) as archive:
        assert archive.read("obligations-ledger.json") == authored
        assert archive.read("obligations-report.json") == files["obligations-report.json"]
    # Archive evidence is not executable readiness: this fixture has no book.
    with pytest.raises(receiver.TaskPackageRefused, match="нет очищенных данных"):
        receiver.to_payload(delivered)
    assert _raw_tree(run) == protected
    assert path.read_bytes() == original_model and ledger_path.read_bytes() == authored
    assert ep.build_package(run) == files and package_path.read_bytes() == body
