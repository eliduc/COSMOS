"""Synthetic raw SMT reports must not acquire a verdict by losing JSON fields.

Exercise the real local reader, consistency selection, package report checker,
and replay copy. No translator or solver result is manufactured by execution:
the report fixture is explicit; a sentinel observes required rechecking.
"""
import copy
import json

import pytest

from cdpm import export_task_package as tp, smt_verify as sv
from cdpm.gates import gate_task_package as gate


class RecheckRequired(RuntimeError):
    pass


@pytest.fixture(params=[sv.CANONICAL, sv.PACKAGE])
def subject(tmp_path, request):
    which = request.param
    doc = {"rules": [{"id": "R-A", "kind": "HARD", "statement": "x <= 1"}]}
    released = copy.deepcopy(doc)
    if which == sv.PACKAGE:
        released["rules"][0]["statement"] = "x <= 2"
    canon = json.dumps(doc).encode("utf-8")
    run = tmp_path / "source"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_bytes(canon)
    report = {
        "schema_version": sv.REPORT_SCHEMA, "verdict": "sat",
        "model_sha256": sv.model_fingerprint(canon),
        "subject_sha256": sv.subject_fingerprint(released),
        "translations": [{"rule": "R-A", "status": "translated",
                          "smt": "(<= x 2)" if which == sv.PACKAGE else "(<= x 1)"}],
        "note": "Words NaN Infinity are strings, not numeric evidence",
    }
    cons = {
        "verdict": "sat", "model_sha256": report["model_sha256"],
        "subject_sha256": report["subject_sha256"], "subject": which,
        "report": sv.PACKAGE_REPORT_REL if which == sv.PACKAGE else sv.REPORT_REL,
        "n_rules": 1, "n_translated": 1,
        "translator": {"role": sv.TRANSLATOR_ROLE, "provider": None, "model": None},
    }
    return run, canon, released, report, cons, which


def _damaged(report, damage):
    text = json.dumps(report)
    if damage == "verdict-before":
        text = '{"verdict": "unsat",' + text[1:]
    elif damage == "verdict-after":
        text = text[:-1] + ',"verdict": "unsat"}'
    elif damage == "escaped-model":
        text = '{"model_\\u0073ha256": "' + "0" * 64 + '",' + text[1:]
    elif damage == "subject-before":
        text = '{"subject_sha256": "' + "0" * 64 + '",' + text[1:]
    elif damage == "nested-status":
        text = text.replace('"status": "translated"',
                            '"status": "untranslatable", "status": "translated"')
    else:
        text = text[:-1] + ',"diagnostic": ' + {
            "nan": "NaN", "overflow": "1e9999"}[damage] + "}"
    return text.encode("utf-8")


DAMAGES = ["verdict-before", "verdict-after", "escaped-model",
           "subject-before", "nested-status", "nan", "overflow"]


@pytest.mark.parametrize("damage", DAMAGES)
def test_reader_refuses_ambiguous_report_without_rewriting(subject, damage):
    run, _, _, report, _, which = subject
    raw = _damaged(report, damage)
    path = sv.report_path(run, which)
    path.write_bytes(raw)
    assert sv.read_report(run, which) is None
    assert path.read_bytes() == raw


@pytest.mark.parametrize("damage", DAMAGES)
def test_consistency_requires_new_evidence_not_a_surviving_value(subject, damage, monkeypatch):
    run, canon, released, report, _, which = subject
    raw = _damaged(report, damage)
    path = sv.report_path(run, which)
    path.write_bytes(raw)
    calls = []

    def recheck(actual_run, actual_doc, actual_sha, **kwargs):
        calls.append(kwargs.get("which") or sv.CANONICAL)
        assert actual_run == run and actual_doc == released
        assert actual_sha == report["model_sha256"]
        assert kwargs["subject_sha"] == report["subject_sha256"]
        raise RecheckRequired("Boundary reached; no translator or solver called")

    monkeypatch.setattr(tp, "_run_smt_check", recheck)
    with pytest.raises(RecheckRequired):
        tp.consistency_of(run, canon, released)
    assert calls == [which]
    assert path.read_bytes() == raw


@pytest.mark.parametrize("damage", DAMAGES)
def test_package_report_checker_refuses_ambiguous_raw_evidence(subject, damage):
    _, _, released, report, cons, _ = subject
    raw = _damaged(report, damage)
    problems = gate._check_consistency(
        {"consistency": cons}, {}, {tp.SMT_REPORT_NAME: raw}, released)
    assert problems and any("не читается" in words for words in problems), problems


@pytest.mark.parametrize("damage", DAMAGES)
def test_replay_never_copies_ambiguous_report_as_normalized_evidence(subject, damage):
    run, _, _, report, _, which = subject
    raw = _damaged(report, damage)
    path = sv.report_path(run, which)
    path.write_bytes(raw)
    child = run.parent / "child"
    child.mkdir()
    (child / "status.json").write_text(json.dumps({
        "passport": {"replay": {"source_dir": str(run), "mode": "replay-journal"}}
    }), encoding="utf-8")
    got = tp._report_from_replay_source(
        child, report["model_sha256"], which, report["subject_sha256"])
    assert got is None
    assert not sv.report_path(child, which).exists()
    assert path.read_bytes() == raw


def test_healthy_report_retains_reader_consistency_gate_and_replay(subject, monkeypatch):
    run, canon, released, report, cons, which = subject
    raw = json.dumps(report).encode("utf-8")
    path = sv.report_path(run, which)
    path.write_bytes(raw)

    def no_recheck(*args, **kwargs):
        pytest.fail("An unchanged healthy report must not require a new check")

    monkeypatch.setattr(tp, "_run_smt_check", no_recheck)
    assert sv.read_report(run, which) == report
    assert sv.read_report(run) == report
    assert tp.consistency_of(run, canon, released) == cons
    assert gate._check_consistency(
        {"consistency": cons}, {}, {tp.SMT_REPORT_NAME: raw}, released) == []
    child = run.parent / "child"
    child.mkdir()
    (child / "status.json").write_text(json.dumps({
        "passport": {"replay": {"source_dir": str(run), "mode": "replay-journal"}}
    }), encoding="utf-8")
    assert tp._report_from_replay_source(
        child, report["model_sha256"], which, report["subject_sha256"]) == report
    assert sv.read_report(child, which) == report
    assert path.read_bytes() == raw
