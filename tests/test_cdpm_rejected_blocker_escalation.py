# -*- coding: utf-8 -*-
"""An unresolved rejected blocker reaches arbitration, not assumed closure.

Synthetic offline regression for an inherited two-cycle registry. No reader,
LLM, HTTP, solver, or runtime fixture is needed; gate replay is the real gate.
"""
import copy
import json

import pytest

from cdpm import exchange as ex
from cdpm.gates import gate_exchange


def _doc():
    return {
        "meta": {"customer": "SYNTHETIC-REJECTION", "mode": 1,
                 "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "synthetic.md", "sha256": "0" * 64}],
        "rules": [{"id": "R-A", "title": "R-A", "statement": "x <= 1",
                   "zone": "BOTH", "kind": "HARD",
                   "sources": [{"sid": "S1", "anchor": "R-A"}]}],
        "questions": [], "objectives": [],
    }


def _remark(defect="искажение", severity="КРИТИЧЕСКОЕ", rid="A1"):
    return {"id": rid, "severity": severity, "defect_class": defect,
            "target": {"rule": "R-A"},
            "source_refs": [{"sid": "S1", "anchor": "R-A"}],
            "text": "Source fingerprint and actual catalog may disagree",
            "evidence": "Source: catalog fingerprint OLD"}


def _finding(defect="искажение", severity="КРИТИЧЕСКОЕ", fid="R1-A1-001"):
    return {"id": fid, "key": ex.remark_key(_remark(defect, severity)),
            "state": "отклонено", "severity": severity,
            "opened_round": 1, "closed_round": None,
            "text": "Source fingerprint and actual catalog may disagree",
            "basis": "The source admits different readings; ask the owner",
            "partial": False}


def _registry():
    # Different classes at one address are distinct disputes, never aliases.
    return {"schema_version": "registry/2", "cycle": 2, "touch": {}, "findings": [
        _finding(), _finding("выбор чтения", fid="C2-R1-A1-001")]}


def _round(registry, *, round_no=1, cycle=3, remarks=(), answers=()):
    pack = {"round": round_no, "mode": "полный", "remarks": list(remarks)}
    resp = {"answers": list(answers)}
    assert ex.validate_remarks(pack, _doc()) == []
    assert ex.validate_response(resp, pack) == []
    result = ex.apply_round_pure(_doc(), registry, pack, resp, round_no,
                                 cycle=cycle)
    assert result["problems"] == []
    assert result["model"] == _doc()
    assert result["rejections"] == []
    assert result["executed"] == set()
    assert result["tokens"] == []
    return pack, resp, result


@pytest.mark.parametrize("severity", ex.BLOCKING_SEVERITIES)
def test_inherited_rejection_without_repeat_reaches_arbiter(severity):
    registry = _registry()
    for f in registry["findings"]:
        f["severity"] = severity
    before = copy.deepcopy(registry)
    _round(registry)
    findings = registry["findings"]
    assert len(findings) == 2
    assert len({f["key"] for f in findings}) == 2
    for old, f in zip(before["findings"], findings):
        assert f["state"] == "тупик"
        assert f["deadlock_kind"] == "неразрешённое отклонение"
        assert "не установлен" in f["deadlock_reason"]
        assert "повтор" in f["deadlock_reason"]
        # No admission, closure, replacement of grounds, or history invention.
        assert {k: f[k] for k in old if k != "state"} == {
            k: v for k, v in old.items() if k != "state"}
    listed = [copy.deepcopy(f) for f in findings if f["state"] == "тупик"]
    matched = ex.match_listed_deadlocks(registry, listed)
    assert list(matched) == [f["id"] for f in findings]
    assert all(f["state"] in ex.ARBITRABLE_STATES for f in matched.values())
    assert ex.registry_verdict_of(registry)["count"] == 2
    assert ex.decide([{"weighty": (0, 0, 0)}] * 2, 5,
                     registry=registry) == "не закрыто"


@pytest.mark.parametrize("severity", ex.SEVERITIES)
def test_exact_repeat_keeps_existing_repeat_path(severity):
    registry = {"findings": [_finding(severity=severity)]}
    old = copy.deepcopy(registry["findings"][0])
    rm = _remark(severity=severity, rid="A-new")
    _round(registry, remarks=[rm], answers=[{
        "remark": rm["id"], "verdict": "ОТКЛОНЕНО", "basis": "Source OLD"}])
    assert registry["findings"] == [{**old, "state": "тупик",
                                       "deadlock_kind": "повтор"}]


def test_same_round_new_rejection_is_not_escalated():
    registry = {"findings": [_finding()]}
    rm = _remark("выбор чтения", rid="A-new")
    _round(registry, remarks=[rm], answers=[{
        "remark": rm["id"], "verdict": "ОТКЛОНЕНО", "basis": "Source OLD"}])
    old, new = registry["findings"]
    assert old["state"] == "тупик"
    assert new["state"] == "отклонено"
    assert new["id"] == "C3-R1-A-new"
    assert "deadlock_kind" not in new and "deadlock_reason" not in new
    _round(registry, round_no=2)
    assert new["state"] == "тупик"


@pytest.mark.parametrize("state,closed_round", [
    ("закрыто", 2), ("отклонено", 2),
    ("арбитр: обработчик прав", None), ("арбитр: аудитор прав", None),
    ("арбитр: в анкету", None),
])
def test_closed_or_arbitrated_finding_is_not_reopened(state, closed_round):
    f = _finding()
    f.update(state=state, closed_round=closed_round)
    registry = {"findings": [f], "touch": {}}
    before = copy.deepcopy(registry)
    _round(registry)
    assert registry == before
    assert ex.decide([{"weighty": (0, 0, 0)}] * 2, 5,
                     registry=registry) == "успех"


@pytest.mark.parametrize("verdict", ["обработчик прав", "аудитор прав", "в анкету"])
def test_arbitrated_key_suppresses_old_rejected_echo(verdict):
    f = _finding()
    resolved = {**f, "id": "C2-R2-A1-001", "state": "арбитр: " + verdict}
    registry = {"findings": [f, resolved], "touch": {}}
    before = copy.deepcopy(registry)
    _round(registry)
    assert registry == before
    assert ex.registry_blockers(registry) == []


@pytest.mark.parametrize("severity", ["СЕРЬЁЗНОЕ", "РЕДАКЦИОННОЕ"])
def test_nonblocking_rejected_backlog_is_unchanged(severity):
    registry = {"findings": [_finding(severity=severity)], "touch": {}}
    before = copy.deepcopy(registry)
    _round(registry)
    assert registry == before


def test_clean_followup_cycles_are_idempotent():
    registry = _registry()
    _round(registry)
    assert all(f["state"] == "тупик" for f in registry["findings"])
    after = copy.deepcopy(registry)
    for cycle in (3, 4, 5):
        for rnd in (1, 2):
            # JSON boundary mirrors carry-forward; no alias-based illusion.
            registry = json.loads(json.dumps(registry))
            _round(registry, round_no=rnd, cycle=cycle)
            assert registry == after


def _dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def test_two_archived_cycles_keep_separate_dossiers_and_original_evidence(tmp_path):
    registry = _registry()
    expected = {}
    for rev, (defect, f) in enumerate(zip(
            ("искажение", "выбор чтения"), registry["findings"]), 1):
        arch = tmp_path / f"exchange-rev-{rev:03d}"
        rm = _remark(defect, rid=f"A{rev}")
        rm["evidence"] = f"Original source evidence {rev}"
        basis = f"Original rejection grounds {rev}"
        _dump(arch / "registry.json", {"findings": [f]})
        _dump(arch / "rounds" / "round-01" / "remarks.json",
              {"round": 1, "mode": "полный", "remarks": [rm]})
        _dump(arch / "rounds" / "round-01" / "response.json", {"answers": [{
            "remark": rm["id"], "verdict": "ОТКЛОНЕНО", "basis": basis}]})
        expected[f["id"]] = (rm, basis)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    _round(registry)
    for f in registry["findings"]:
        dossier = ex._deadlock_dossier(tmp_path / "exchange", f)
        rm, basis = expected[f["id"]]
        assert dossier["remark"] == rm
        assert dossier["rejections"] == [basis]
        assert dossier["code_rejections"] == []
        assert not dossier["remark_from_ban"]
    assert {p: p.read_bytes() for p in before} == before


def test_existing_gate_replays_escalation_and_rejects_forged_registry(tmp_path):
    out = tmp_path / "exchange"
    registry, history = _registry(), []
    registry["cycle"] = 3
    _dump(out / "registry-start.json", registry)
    _dump(out / "versions" / "model-v00.json", _doc())
    for rnd in (1, 2):
        pack, resp, result = _round(registry, round_no=rnd)
        metrics = {**ex.metrics_of(pack), "round": rnd,
                   "mode": "полный", "patched": False}
        history.append(metrics)
        for name, obj in [("remarks", pack), ("response", resp),
                          ("metrics", metrics),
                          ("patch-rejections", result["rejections"])]:
            _dump(out / "rounds" / f"round-{rnd:02d}" / f"{name}.json", obj)
        _dump(out / "versions" / f"model-v{rnd:02d}.json", result["model"])
    assert all(f["state"] == "тупик" for f in registry["findings"])
    _dump(out / "registry.json", registry)
    summary = {"verdict": ex.decide(history, 5, registry=registry),
               "rounds": 2, "max_rounds": 5, "cycle": 3, "history": history,
               "registry_verdict": ex.registry_verdict_of(registry),
               "deadlocks": registry["findings"]}
    _dump(out / "verdict.json", summary)
    problems, counters = gate_exchange.check(out)
    assert problems == [], problems
    assert counters["findings"] == 2 and counters["rounds"] == 2
    assert summary["verdict"] == "не закрыто"
    for f in registry["findings"]:
        f["state"] = "отклонено"
        f.pop("deadlock_kind")
        f.pop("deadlock_reason")
    _dump(out / "registry.json", registry)
    problems, _ = gate_exchange.check(out)
    assert any("реестр не совпадает с повторной проводкой" in p for p in problems)


def test_arbiter_prompt_does_not_assume_established_defect_or_double_rejection():
    assert "исход спора не установлен" in ex._PROMPT_ARBITER
    assert "не означает признания дефекта" in ex._PROMPT_ARBITER
    assert "обработчик дважды отклонил" not in ex._PROMPT_ARBITER
