"""Current-edition readers: pure fixtures, no app startup or live channels.

Run with --noconftest -p no:cacheprovider.  The webui reader is the exact
undecorated AST function, not an import of the application and its startup.
"""
import ast
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading

import pytest

from cdpm import exchange as ex, measure, model as m, principles, question_editions as qe
from cdpm.gates import gate_answers


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_offline(monkeypatch, tmp_path):
    """Only fixture writes; accidental network/process/thread starts fail."""
    def deny(*_args, **_kwargs):
        raise AssertionError("offline fixture forbids external execution")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket.socket, "bind", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(subprocess, "Popen", deny)
    monkeypatch.setattr(os, "system", deny)
    monkeypatch.setattr(threading.Thread, "start", deny)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(ROOT / "catalog"))
    for key in ("CREW_COSMOS_BINDINGS", "CREW_CDPM_BINDINGS", "CREW_BINDINGS_SEED",
                "CDPM_SETTINGS_FILE", "CDPM_AGENTS_DEFAULT", "CREW_TASK_INBOX",
                "CDPM_CATALOG_SERVICE_URL", "CDPM_CATALOG_SERVICE_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CREW_CDPM_RUNS_DIR", str(tmp_path / "runs"))
    active = [True]

    def in_fixture(value):
        return isinstance(value, (str, bytes, os.PathLike)) and Path(
            os.fsdecode(value)).resolve().is_relative_to(tmp_path.resolve())

    def audit(event, args):
        if not active[0]:
            return
        if event.startswith("socket.") and event != "socket.gethostname":
            deny()
        if event in ("subprocess.Popen", "os.system", "ctypes.dlopen"):
            deny()
        if event == "open":
            path, mode, flags = args
            writing = (isinstance(mode, str) and any(x in mode for x in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing and not in_fixture(path):
                raise AssertionError("write outside fixture: " + str(path))
        if event in ("os.mkdir", "os.remove", "os.rmdir", "os.rename"):
            paths = args[:2] if event == "os.rename" else args[:1]
            if any(not in_fixture(x) for x in paths):
                raise AssertionError("filesystem mutation outside fixture")

    sys.addaudithook(audit)
    try:
        yield
    finally:
        active[0] = False


def document(answered=True):
    """Same causal structure as run-0052; invented text/IDs, no tenant data."""
    old = {"id": "Q-2-trace", "part": "2", "title": "Выбор источника",
           "text": "Какой источник применяется?", "rule_refs": ["R-1"],
           "source_refs": [{"sid": "S1", "anchor": "a1"}],
           "answer_format": {"type": "choice", "options": [
               {"key": "а", "text": "Действующий источник"},
               {"key": "б", "text": "Иной источник"}]}}
    doc = {"meta": {"customer": "вымышленный стенд", "mode": 1,
                    "cdpm_version": "test", "revision": 2},
           "sources": [{"sid": "S1", "file": "fixture.txt", "sha256": "0" * 64}],
           "rules": [{"id": "R-1", "title": "Определение базы", "kind": "DEF",
                      "zone": "BOTH", "statement": "База приписки — AAA.",
                      "sources": [{"sid": "S1", "anchor": "a1"}]}],
           "objectives": [], "questions": [old]}
    qe.freeze(old)
    current = qe.expand(doc, old, [], content={"source_refs": [
        {"sid": "S1", "anchor": "a1"}, {"sid": "S1", "anchor": "a2"}]})
    if answered:
        current["answer"] = {"raw": "а", "key": "а", "status": "применён", "revision": 2}
        doc["rules"][0].pop("blockers", None)
    return doc, old, current


def read_webui(doc, tmp_path):
    file = ROOT / "cdpm" / "webui.py"
    tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
    function = next(x for x in tree.body if isinstance(x, ast.FunctionDef)
                    and x.name == "_readiness_of")
    assert not function.decorator_list
    ns = {"Path": Path, "m_mod": m}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(file), "exec"), ns)
    extract = tmp_path / "extract"
    extract.mkdir()
    path = extract / "model.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    before = path.read_bytes()
    got = ns["_readiness_of"](tmp_path)
    assert path.read_bytes() == before
    return got


def finding(old):
    return {"id": "R1-A1-001", "arbiter_question": old["id"],
            "deadlock_kind": ex.CODE_DEADLOCK_KIND, "code_rejection": "запрещено",
            "arbiter_unenforced": "требуется решение владельца"}


def test_valid_chain_keeps_history_but_closes_active_readers(tmp_path):
    doc, old, current = document()
    before = copy.deepcopy(doc)
    assert qe.is_superseded(doc, old)
    assert m.open_questions(doc) == []
    got = read_webui(doc, tmp_path)
    assert got["pairing_ready"] and got["rostering_ready"]
    assert (got["vmp_open_questions"], got["vmp_questions_total"], got["vmp_state"]) == (0, 1, "ВМП")
    assert doc == before and old.get("answer") is None and current["answer"]["status"] == "применён"


def test_principles_counts_current_not_archival_forks():
    doc, _, _ = document()
    got = principles.summary(doc)
    assert (got["P2"]["open_forks"], got["P2"]["forks_total"], got["P2"]["ok"]) == (0, 1, True)


def test_actual_audit_prompt_has_no_retired_open_question():
    doc, old, _ = document()
    listing = ex._open_questions_listing(doc)
    prompt = ex._PROMPT_AUDIT.format(sid="S1", n=1, clauses="[a2] источник",
        rules="R-1", questions=listing, objectives="", uncovered="", unconsumed_values="",
        rejected="", pending="", closed="", fit_listing="")
    assert old["id"] not in prompt
    assert listing == "(открытых вопросов нет)"


def test_actual_open_successor_stays_in_audit_and_readiness():
    doc, old, current = document(answered=False)
    listing = ex._open_questions_listing(doc)
    assert f"[{old['id']}]" not in listing
    assert f"[{current['id']}]" in listing
    assert m.open_questions(doc) == [current]
    assert not m.readiness(doc)["pairing_ready"]
    assert not m.readiness_matrix(doc)["pairing"]["feasibility-hard"]["ready"]


@pytest.mark.parametrize("answer_status", ["не разобран", "ожидает обработчика", "применён частично", "конфликт пересмотра"])
def test_principles_preserves_non_applied_current_statuses(answer_status):
    doc, _, current = document()
    current["answer"]["status"] = answer_status
    got = principles.summary(doc)["P2"]
    assert got["open_forks"] == 1 and not got["ok"]


@pytest.mark.parametrize("answered", [False, True])
def test_reading_shield_tracks_current_edition(answered):
    doc, _, current = document(answered)
    rm = {"defect_class": "выбор чтения", "target": {"rule": "R-1"},
          "source_refs": [{"sid": "S1", "anchor": "a1"}]}
    got = ex.awaiting_decision_reason(doc, rm)
    assert got is None if answered else current["id"] in got
    rm["defect_class"] = "пропуск"
    assert ex.awaiting_decision_reason(doc, rm) is None


@pytest.mark.parametrize("answered", [False, True])
def test_owner_shield_resolves_root_to_current_edition(answered):
    doc, old, current = document(answered)
    before = copy.deepcopy(doc)
    got = ex.awaits_owner_answer(finding(old), None, [doc])
    assert got == (None if answered else current["id"])
    assert doc == before


def test_fresh_answer_does_not_fall_back_to_older_open_snapshot():
    fresh, old, _ = document()
    stale, _, _ = document(answered=False)
    assert ex.code_lock_owner_question(finding(old), [fresh, stale]) is None


def test_owner_shield_does_not_create_code_lock_evidence():
    doc, old, _ = document(answered=False)
    f = finding(old)
    f.pop("code_rejection")
    assert ex.awaits_owner_answer(f, None, [doc]) is None
    assert ex.awaits_owner_answer(f, {"code_rejections": [{"terminal": True}]}, [doc])


def corrupt(doc, old, current, kind):
    if kind == "fingerprint":
        current["edition"]["fingerprint"] = "0" * 12
    elif kind == "snapshot":
        current["text"] += " подмена"
    elif kind == "fork":
        other = copy.deepcopy(current)
        other["id"] = "Q-2-other"
        other.pop("edition")
        qe.freeze(other, revision=2)
        doc["questions"].append(other)
    elif kind == "duplicate_id":
        doc["questions"].append(copy.deepcopy(current))
    elif kind == "cycle":
        old["edition_of"] = current["id"]
    elif kind == "revision_gap":
        current.pop("edition")
        qe.freeze(current, revision=3)
    elif kind == "scope_loss":
        current["rule_refs"] = []
        current.pop("edition")
        qe.freeze(current, revision=2)
    elif kind == "missing_snapshot":
        current.pop("edition")
    else:
        raise AssertionError(kind)


INVALID = ("fingerprint", "snapshot", "fork", "duplicate_id", "cycle", "revision_gap", "scope_loss", "missing_snapshot")


@pytest.mark.parametrize("kind", INVALID)
def test_invalid_successor_cannot_hide_old_open_question(kind):
    doc, old, current = document()
    corrupt(doc, old, current, kind)
    before = copy.deepcopy(doc)
    assert not qe.is_superseded(doc, old)
    assert old in m.open_questions(doc)
    assert f"[{old['id']}]" in ex._open_questions_listing(doc)
    assert m.validate_model(doc)
    assert doc == before


@pytest.mark.parametrize("kind", INVALID)
def test_invalid_chain_cannot_clear_owner_shield_even_with_old_answer(kind):
    doc, old, current = document()
    old["answer"] = copy.deepcopy(current["answer"])
    corrupt(doc, old, current, kind)
    assert ex.code_lock_owner_question(finding(old), [doc]) == old["id"]


@pytest.mark.parametrize("mutation", ["suffix_only", "dangling_parent"])
def test_unrelated_e2_id_or_dangling_parent_is_not_supersession(mutation):
    doc, old, current = document()
    if mutation == "suffix_only":
        current.pop("edition_of")
    else:
        current["edition_of"] = "missing"
    assert old in m.open_questions(doc)
    assert not qe.is_superseded(doc, old)
    assert m.validate_model(doc)


@pytest.mark.parametrize("answered", [False, True])
def test_legacy_no_edition_behavior_is_unchanged(answered):
    doc, old, current = document(answered)
    old.pop("edition")
    if answered:
        old["answer"] = current["answer"]
    doc["questions"] = [old]
    assert m.open_questions(doc) == ([] if answered else [old])


def test_missing_current_code_resolution_cannot_hide_open_question():
    doc, _, current = document(answered=False)
    current["code_resolutions"] = []
    assert current in m.open_questions(doc)
    assert principles.summary(doc)["P2"]["open_forks"] == 1


@pytest.mark.parametrize("answered", [False, True])
def test_measure_needs_current_open_question_for_normative_carrier(answered):
    doc, _, current = document(answered)
    doc["rules"] = []  # Deliberate uncovered normative carrier, not a valid VMP claim.
    clauses = [{"sid": "S1", "anchor": "a1", "text": "Смена не более 720 минут."}]
    before = copy.deepcopy(doc)
    got = measure.measure(doc, clauses)
    assert got["clauses"][0]["questions_open"] == ([] if answered else [current["id"]])
    assert got["counts"]["normative_without_rows"] == (1 if answered else 0)
    assert doc == before


def test_answers_gate_counter_uses_active_open_but_keeps_validation_census(tmp_path):
    doc, _, _ = document()
    model_path, summary_path = tmp_path / "model.json", tmp_path / "summary.json"
    model_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    summary_path.write_text(json.dumps({"revision": 2, "applied": 1}), encoding="utf-8")
    before = model_path.read_bytes()
    problems, counters = gate_answers.check(model_path, summary_path)
    assert problems == []
    assert counters["open"] == 0 and counters["questions"] == 2
    assert counters["applied"] == 1
    assert model_path.read_bytes() == before


def test_old_edition_patch_remains_refused_and_current_patch_is_healthy():
    doc, old, current = document(answered=False)
    before = copy.deepcopy(doc)
    patch = {"question_id": old["id"], "add_source_refs": [{"sid": "S1", "anchor": "a3"}]}
    problems = ex._apply_question_patch(doc, patch, {("S1", "a3")})
    assert any("уже переиздан" in problem for problem in problems)
    assert doc == before
    patch["question_id"] = current["id"]
    assert ex._apply_question_patch(doc, patch, {("S1", "a3")}) == []
    assert doc["questions"][:2] == before["questions"]
    assert qe.latest(doc, old)["edition"]["revision"] == 3


@pytest.mark.parametrize("answered", [False, True])
def test_three_editions_resolve_by_lineage_not_list_order(answered):
    doc, old, second = document()
    third = qe.expand(doc, second, [], content={"source_refs": [
        {"sid": "S1", "anchor": "a1"}, {"sid": "S1", "anchor": "a2"},
        {"sid": "S1", "anchor": "a3"}]})
    if answered:
        third["answer"] = copy.deepcopy(second["answer"])
    doc["questions"].reverse()
    before = copy.deepcopy(doc)
    assert m.current_questions(doc, "2") == [third]
    assert m.current_questions(doc, "3b") == []
    assert m.open_questions(doc, "2") == ([] if answered else [third])
    assert ex.code_lock_owner_question(finding(old), [doc]) == (
        None if answered else third["id"])
    assert doc == before


def test_direct_leaf_reference_cannot_clear_shield_with_duplicate_ancestor():
    doc, old, current = document()
    doc["questions"].append(copy.deepcopy(old))
    assert ex.code_lock_owner_question(finding(current), [doc]) == current["id"]
