"""K01/K06 independent barriers through actual modern ZIP boundaries.

The catalogue, typed landing, model readiness, exporter, ZIP gate and receiver
are real. Native support approvals are explicit temporary synthetic evidence,
not a stand result. A SOFT-only source uses a real zero-HARD consistency report;
no translator, solver, network, service, release or execution is invoked.
"""
import copy
import json
import zipfile

import pytest

from cdpm import answers, export_task_package as producer, family_manifest, model as m
from cdpm import family_params as fp
from cdpm import smt_verify
from cdpm.gates import gate_task_package as gate
from test_cdpm_landing_v2_export import real_soft_package
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from test_k06_scope_agent import native_support
from webapp import task_package as receiver


FAMILY = "duty_extension_penalty"
PARAM = "long_duty_threshold"


@pytest.mark.parametrize("complete,answer", [(False, False), (True, False), (True, True)],
                         ids=["missing", "rule-value", "question-answer"])
@pytest.mark.parametrize("supported", [False, True], ids=["unsupported", "supported"])
@pytest.mark.parametrize("revision", ["1.20", "1.21"])
def test_parameter_and_support_barriers_cannot_mask_or_supply_each_other(
        tmp_path, monkeypatch, complete, answer, supported, revision):
    parts = real_soft_package(tmp_path / "seed", monkeypatch, revision)
    cat, seed_path, seed_bytes, pin = parts[1], parts[5], parts[6], parts[8]
    doc = m.loads_model(seed_bytes)
    rule = doc["rules"][0]
    fit = rule["template_fit"]
    assert fit["schema_version"] == "landing/2"
    assert fit["family_contract"] == pin
    assert PARAM not in fit["params"]
    open_question_doc = None
    if answer:
        created = fp.ensure_family_param_questions(doc)
        assert len(created) == 1, created
        question = next(q for q in doc["questions"] if q["id"] == created[0])
        assert fp.question_target(question) == (FAMILY, PARAM)
        open_question_doc = copy.deepcopy(doc)
        assert m.validate_schema(open_question_doc) == []
        assert m.validate_template_fit(open_question_doc) == []
        result = answers.apply_answers(doc, {created[0]: "720 минут"})
        assert result["applied"] == 1, result
        rule = doc["rules"][0]
        fit = rule["template_fit"]
        cell = fit["params"][PARAM]
        assert cell["value"] == 720 and cell["unit"] == "duration_minutes"
        assert cell["source"]["kind"] == "answer"
        assert cell["source"]["question"]["id"] == created[0]
        assert cell["source"]["question"]["revision"] == 1
        assert cell["source"]["question"]["fingerprint"]
        assert cell["source"]["version"]["fingerprint"]
        assert cell["source"]["effect"] == {"rule": PARAM, "field": "value"}
        assert cell["quote"] == "720 минут"
        assert fit["family_contract"] == pin
    elif complete:
        rule["statement"] += " Мягкий порог смены — не более 720 минут."
        fit["params"][PARAM] = {
            "value": 720, "unit": "duration_minutes", "relation": "max",
            "condition": None, "quote": "720 минут",
            "source": {"kind": "rule", "rule_id": rule["id"]}}
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    clauses = {(s["sid"], s["anchor"]): rule["statement"] for s in rule["sources"]}
    assert m.validate_fit_quotes(doc, clauses) == []
    if not answer:
        assert doc["questions"] == []
    assert not rule.get("blockers")
    records = {("families", FAMILY)}
    support = native_support(tmp_path, monkeypatch, cat, records)
    if not supported:
        for cell in support["cells"]:
            if (cell["section"], cell["record_id"]) in records:
                cell["approved"] = None
    (cat.path / "support.json").write_text(
        json.dumps(support, ensure_ascii=False), encoding="utf-8")

    before = copy.deepcopy(doc)
    family = family_manifest.project(doc)[FAMILY]
    assert family["missing"] == ([] if complete else [PARAM]), family
    assert family["verdict"] == ("полна" if complete else "неполна"), family
    support_problems = m.support_problems(doc, revision)
    assert bool(support_problems) is (not supported), support_problems
    assert all(p["code"] == "support-missing" for p in support_problems)
    if open_question_doc is not None:
        # Same live support as the answered document; only the real answer
        # changes. An open question is not a supplied parameter, even with
        # healthy support. Keep this before/after oracle independent of the
        # producer's declared readiness and preserve the original pin.
        pending_before = copy.deepcopy(open_question_doc)
        pending_family = family_manifest.project(open_question_doc)[FAMILY]
        assert pending_family["missing"] == [PARAM], pending_family
        assert pending_family["verdict"] == "неполна", pending_family
        pending_ready = m.readiness_matrix(open_question_doc, revision)["pairing"]
        assert pending_ready["feasibility-hard"]["ready"], pending_ready
        assert not pending_ready["feasibility-soft"]["ready"], pending_ready
        assert open_question_doc["rules"][0]["template_fit"]["family_contract"] == pin
        assert open_question_doc == pending_before
    ready = m.readiness_matrix(doc, revision)["pairing"]
    soft_ready = complete and supported
    assert ready["feasibility-hard"]["ready"], ready
    assert ready["feasibility-soft"]["ready"] is soft_ready, ready
    modes = producer.allowed_modes(doc, revision)[0]
    assert "feasibility-hard" in modes["pairing"]
    assert ("feasibility-soft" in modes["pairing"]) is soft_ready
    assert doc == before

    run = tmp_path / "joint-run"
    source = run / "extract" / "model.json"
    source.parent.mkdir(parents=True)
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    source.write_bytes(raw)

    def forbidden(*_args, **_kwargs):
        pytest.fail("The zero-HARD fixture cannot invoke a translator or solver")

    report = smt_verify.verify(doc, forbidden,
        model_sha256=smt_verify.model_fingerprint(raw))
    assert report["n_rules"] == report["n_translated"] == 0
    smt_verify.write_report(run, report)
    body = producer.export_zip(run)
    path = tmp_path / "joint.zip"
    path.write_bytes(body)
    problems, _ = gate.check(path)
    assert problems == [], problems
    received = receiver.load(path, run_gate=False)
    assert received["landing_projection"]["archive_only"] is False
    assert received["family_projection"]["judged"] is True
    declared = received["manifest"]
    assert declared["spec_version"] == revision
    assert declared["families"][FAMILY]["missing"] == ([] if complete else [PARAM])
    assert "feasibility-hard" in declared["allowed_modes"]["pairing"]
    assert ("feasibility-soft" in declared["allowed_modes"]["pairing"]) is soft_ready
    assert received["model"]["rules"][0]["template_fit"]["family_contract"] == pin
    assert received["model"]["rules"][0]["template_fit"] == fit
    receiver.check_support_execution(received, task="pairing", mode="feasibility-hard")
    if soft_ready:
        receiver.check_support_execution(received, task="pairing", mode="feasibility-soft")

    if not soft_ready:
        # Keep honest family/support snapshots and original member checksums.
        # Lie only about permission, so each boundary must replay its own law.
        with zipfile.ZipFile(path) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        forged = json.loads(files["manifest.json"])
        forged["allowed_modes"]["pairing"].append("feasibility-soft")
        files["manifest.json"] = json.dumps(forged, ensure_ascii=False).encode("utf-8")
        bad = tmp_path / "forged-permission.zip"
        bad.write_bytes(producer.write_zip(files))
        expected = "family-incomplete" if not complete else "support-missing"
        problems, _ = gate.check(bad)
        assert any(expected in p for p in problems), problems
        # K01 replays permissions during load. K06 intentionally separates
        # reading an honest support snapshot from authorizing a chosen mode
        # (support_manifest's contract; button calls the execution check).
        # Skipping the subprocess producer gate must not skip that final
        # execution refusal. Do not demand archive/read rejection instead.
        with pytest.raises(receiver.TaskPackageRefused) as caught:
            loaded = receiver.load(bad, run_gate=False)
            assert complete, "K01 must already refuse the missing-family claim"
            receiver.check_support_execution(loaded, task="pairing", mode="feasibility-soft")
        assert caught.value.code == expected, str(caught.value)
        assert FAMILY in str(caught.value)
    assert source.read_bytes() == raw
    assert seed_path.read_bytes() == seed_bytes
    assert path.read_bytes() == body
