"""Legal historical expansion through real questions and modern ZIP readers.

Temporary catalogues/support are explicit synthetic fixtures, not releases or
stand approval. The real zero-HARD report invokes no translator or solver.
"""
import copy
import hashlib
import json
import shutil

import pytest
import template_catalog as tc
from template_catalog import family_contract
from cdpm import answers, catalog_bridge as cb, family_manifest, family_params as fp
from cdpm import export_task_package as producer, model as m, smt_verify
from cdpm.gates import gate_task_package as gate
from webapp import task_package as receiver
from test_cdpm_landing_v2_export import real_soft_package
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from test_k06_scope_agent import native_support


FAMILY = "duty_extension_penalty"
PARAM = "long_duty_threshold"


def select_catalogue(path, monkeypatch):
    # Seal only this deliberately edited temporary catalogue fixture.
    head_path = path / "catalog.json"
    head = json.loads(head_path.read_text(encoding="utf-8"))
    head["fingerprint"] = tc.load(path).fingerprint()
    head_path.write_text(json.dumps(head), encoding="utf-8")
    monkeypatch.setenv(cb.ENV_CATALOG, str(path))
    cb.forget()
    cat = tc.load(path)
    assert not [p for p in cat.validate() if p.severity == "error"]
    return cat


def approve_fixture(root, monkeypatch, cat):
    root.mkdir()
    support = native_support(root, monkeypatch, cat, {("families", FAMILY)})
    (cat.path / "support.json").write_text(json.dumps(support), encoding="utf-8")


def package_roundtrip(root, doc, *, ready, pin, revision):
    before = copy.deepcopy(doc)
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    matrix = m.readiness_matrix(doc, revision)
    assert matrix["pairing"]["feasibility-hard"]["ready"]
    assert matrix["pairing"]["feasibility-soft"]["ready"] is ready, matrix
    assert m.support_problems(doc, revision) == []
    source = root / "extract" / "model.json"
    source.parent.mkdir(parents=True)
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    source.write_bytes(raw)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("No translator or solver in a zero-HARD fixture")

    report = smt_verify.verify(doc, forbidden,
        model_sha256=smt_verify.model_fingerprint(raw))
    assert report["n_rules"] == report["n_translated"] == 0
    smt_verify.write_report(root, report)
    body = producer.export_zip(root)
    archive = root / "package.zip"
    archive.write_bytes(body)
    problems, _ = gate.check(archive)
    assert problems == [], problems
    received = receiver.load(archive, run_gate=False)
    assert not received["landing_projection"]["archive_only"]
    assert received["family_projection"]["judged"]
    assert received["model"]["rules"][0]["template_fit"] == doc["rules"][0]["template_fit"]
    assert received["model"]["rules"][0]["template_fit"]["family_contract"] == pin
    declared = received["manifest"]
    assert declared["spec_version"] == revision
    assert ("feasibility-soft" in declared["allowed_modes"]["pairing"]) is ready
    assert declared["families"][FAMILY]["missing"] == ([] if ready else [PARAM])
    # Read the same public snapshot endpoint after each state update. This
    # exercises its actual file loading without starting HTTP or a service.
    from cdpm import webui
    review_source = root.parent / "human" / "run-0042" / "extract" / "model.json"
    review_source.parent.mkdir(parents=True, exist_ok=True)
    review_source.write_bytes(raw)
    response = webui.run_effective_parameters("run-0042")
    assert response.status_code == 200
    view = json.loads(response.body)
    assert view["model_sha256"] == hashlib.sha256(raw).hexdigest()
    reviewed = next(f for f in view["family_review"]["families"] if f["family"] == FAMILY)
    assert reviewed["missing"] == ([] if ready else [PARAM]), reviewed
    shown_params = {p["name"]: p for p in view["rules"][0]["params"]}
    assert shown_params["duty_extension_cap"]["value"] == 960
    expected_threshold = doc["rules"][0]["template_fit"]["params"].get(PARAM)
    if expected_threshold is None:
        assert PARAM not in shown_params
    else:
        assert shown_params[PARAM]["value"] == 720
        assert shown_params[PARAM]["unit"] == "duration_minutes"
        assert shown_params[PARAM]["quote"] == "720 минут"
    if view["family_review"]["catalog"]["version"] != pin["catalog_version"]:
        assert any("расшир" in words for words in reviewed["transitions"]), reviewed
    assert review_source.read_bytes() == raw
    if ready:
        receiver.check_support_execution(received, task="pairing", mode="feasibility-soft")
    assert doc == before and source.read_bytes() == raw and archive.read_bytes() == body
    return received


@pytest.mark.parametrize("revision", ["1.20", "1.21"])
@pytest.mark.parametrize("answer_value,declined", [("720 минут", False), ("Не применяется", True)],
                         ids=["numeric-answer", "unconditional-decline"])
def test_historical_complete_run_expands_and_real_answer_restores_only_missing_parameter(
        tmp_path, monkeypatch, revision, answer_value, declined):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "human"))
    parts = real_soft_package(tmp_path / "seed", monkeypatch, revision)
    old_path, seed_path, seed_bytes = parts[1].path, parts[5], parts[6]
    doc = m.loads_model(seed_bytes)
    families = json.loads((old_path / "families.json").read_text(encoding="utf-8"))
    rec = next(r for r in families["families"] if r["id"] == FAMILY)
    # Both declared parameters remain covered by the union of named sets.
    # The selected cap-only set is initially complete, without the threshold.
    rec["param_sets"] = [
        {"id": "cap", "params": ["duty_extension_cap"], "note": "Synthetic original choice"},
        {"id": "threshold", "params": [PARAM], "note": "Independent alternative"}]
    (old_path / "families.json").write_text(json.dumps(families), encoding="utf-8")
    old = select_catalogue(old_path, monkeypatch)
    fit = doc["rules"][0]["template_fit"]
    fit["registry_rev"] = old.fingerprint()
    fit["family_contract"] = family_contract.snapshot(old, FAMILY, fit["params"])
    # This is a newly constructed typed fixture for the synthetic old release,
    # not the seed's migration event. Keep the original seed bytes untouched.
    doc["meta"].pop("landing_migrations", None)
    pin = copy.deepcopy(fit["family_contract"])
    assert pin["param_set"] == "cap"
    original_cells = copy.deepcopy(fit["params"])
    approve_fixture(tmp_path / "old-support", monkeypatch, old)
    old_received = package_roundtrip(tmp_path / "old-run", doc, ready=True, pin=pin, revision=revision)
    old_doc = copy.deepcopy(doc)

    new_path = tmp_path / "new-catalogue"
    shutil.copytree(old_path, new_path)
    shutil.copytree(old_path, new_path / "releases" / old.version())
    versions_path = new_path / "versions.json"
    versions = json.loads(versions_path.read_text(encoding="utf-8"))
    versions["versions"].append({"version": old.version(), "fingerprint": old.fingerprint(),
        "released_at": "2026-09-16", "released_by": "isolated expansion test",
        "code": "3be8493a26106e9877ae1efac65f1af54a3d27d6", "changes": []})
    versions_path.write_text(json.dumps(versions), encoding="utf-8")
    metadata_path = new_path / "catalog.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["version"] = "0.7.2"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    rec["param_sets"][0]["params"].append(PARAM)
    (new_path / "families.json").write_text(json.dumps(families), encoding="utf-8")
    new = select_catalogue(new_path, monkeypatch)
    assert new.fingerprint() != old.fingerprint()
    approve_fixture(tmp_path / "new-support", monkeypatch, new)

    missing = fp.family_completeness(doc)[FAMILY]
    assert missing["missing"] == [PARAM] and missing["curable"] is True, missing
    assert missing["verdict"] == fp.VERDICT_INCOMPLETE, missing
    assert doc == old_doc
    package_roundtrip(tmp_path / "expanded-run", doc, ready=False, pin=pin, revision=revision)
    created = fp.ensure_family_param_questions(doc)
    assert len(created) == 1, created
    question = next(q for q in doc["questions"] if q["id"] == created[0])
    assert fp.question_target(question) == (FAMILY, PARAM)
    package_roundtrip(tmp_path / "question-run", doc, ready=False, pin=pin, revision=revision)
    result = answers.apply_answers(doc, {created[0]: answer_value})
    assert result["applied"] == 1, result
    fit = doc["rules"][0]["template_fit"]
    assert {key: fit["params"][key] for key in original_cells} == original_cells
    assert fit["family_contract"] == pin
    if declined:
        assert PARAM not in fit["params"]  # Decline never invents a numerical cell.
        assert fp.family_completeness(doc)[FAMILY]["declined"] == [PARAM]
        assert not m.is_open_question(question)
    else:
        cell = fit["params"][PARAM]
        assert cell["value"] == 720 and cell["source"]["kind"] == "answer"
        assert cell["source"]["question"]["id"] == created[0]
        assert cell["source"]["question"]["revision"] == 1
        assert cell["source"]["question"]["fingerprint"]
        assert cell["source"]["version"]["fingerprint"]
    assert family_manifest.project(doc)[FAMILY]["missing"] == []
    final = package_roundtrip(tmp_path / "answered-run", doc, ready=True, pin=pin, revision=revision)
    if declined:
        assert PARAM not in final["model"]["rules"][0]["template_fit"]["params"]
        assert final["manifest"]["families"][FAMILY]["declined"] == [PARAM]
    else:
        assert final["model"]["rules"][0]["template_fit"]["params"][PARAM] == cell
    assert old_received["model"]["rules"][0]["template_fit"]["params"] == original_cells
    assert seed_path.read_bytes() == seed_bytes
