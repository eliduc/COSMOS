"""Reconsidered history is not an open fork; unresolved/broken chains still are.

Pure synthetic model transitions, real answer/worker code, deterministic replies.
No application startup, network, native solver, workbook or persisted model.
"""
import copy
import json
import os
import socket
import subprocess
import threading

import pytest

from cdpm import answer_versions, answers, model as m, principles, question_editions as qe
from test_cdpm_current_question_readers_gpt import document


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("external execution forbidden in principles fixture")
    for name in ("connect", "connect_ex", "bind"):
        monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(subprocess, "Popen", denied)
    monkeypatch.setattr(os, "system", denied)
    monkeypatch.setattr(threading.Thread, "start", denied)
    monkeypatch.setattr(answers.llm_mod, "channel", denied)


def completed_chain(depth=1):
    doc, _, root = document()
    answer_versions.stamp(root["answer"], None, answer_versions.context(
        received_at="2099-01-01T00:00:00+00:00", book_sha256="a" * 64))
    root["answer"]["effects"] = []
    current = root
    for _ in range(depth):
        opened = answers.open_revision(doc, current["id"])
        current = next(q for q in doc["questions"] if q["id"] == opened["created"])
        qe.freeze(current)
        report = answers.apply_answers(doc, {current["id"]: "а"},
            received_at="2099-01-02T00:00:00+00:00", book_sha256="b" * 64)
        assert report["awaiting"] == 1
        reply = json.dumps({"patches": [{"question": current["id"], "change": "Подтверждение",
            "rules": [{"id": "R-1", "unchanged": {"basis": "а"}}]}]}, ensure_ascii=False)
        worker = answers.apply_worker_patches(doc, lambda _prompt: reply)
        assert worker["worker_applied"] == 1 and not worker["worker_rejected"]
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    return doc, root, current


def summary_without_mutation(doc):
    before = copy.deepcopy(doc)
    result = principles.summary(doc)
    assert doc == before
    return result["P2"]


@pytest.mark.parametrize("depth", [1, 2])
@pytest.mark.parametrize("reverse", [False, True])
def test_applied_reconsideration_closes_only_historical_forks(depth, reverse):
    doc, root, current = completed_chain(depth)
    if reverse:
        doc["questions"].reverse()
    before = copy.deepcopy(doc)
    assert root["answer"]["status"] == "пересмотрен"
    assert current["answer"]["status"] == "применён"
    assert m.open_questions(doc) == []
    got = summary_without_mutation(doc)
    assert (got["open_forks"], got["forks_total"], got["ok"]) == (0, depth + 1, True)
    assert doc == before  # raw, receipt, edition, history and rule provenance retained


@pytest.mark.parametrize("status", [None, "ожидает обработчика", "не разобран",
                                    m.PARTIAL_ANSWER_STATUS, m.REVISION_CONFLICT_STATUS])
def test_unfinished_replacement_does_not_hide_unresolved_history(status):
    doc, _, current = completed_chain()
    if status is None:
        current.pop("answer")
    else:
        current["answer"]["status"] = status
    got = summary_without_mutation(doc)
    assert got["open_forks"] == 2 and not got["ok"]


@pytest.mark.parametrize("damage", ["missing_link", "missing_target", "wrong_root",
    "duplicate_target", "self_cycle", "two_cycle", "changed_edition", "wrong_scope",
    "old_blocker", "new_blocker", "old_conditional"])
def test_broken_reconsideration_cannot_make_history_look_resolved(damage):
    doc, root, current = completed_chain()
    if damage == "missing_link":
        root["answer"].pop("revised_by")
    elif damage == "missing_target":
        root["answer"]["revised_by"] = "Q-missing"
    elif damage == "wrong_root":
        current["revision_of"] = "Q-foreign"
    elif damage == "duplicate_target":
        doc["questions"].append(copy.deepcopy(current))
    elif damage == "self_cycle":
        root["answer"]["revised_by"] = root["id"]
    elif damage == "two_cycle":
        current["answer"].update(status="пересмотрен", revised_by=root["id"])
    elif damage == "changed_edition":
        current["text"] += " Подмена."
    elif damage == "wrong_scope":
        current["rule_refs"] = []
        current.pop("edition")
        qe.freeze(current)
    elif damage in ("old_blocker", "new_blocker"):
        doc["rules"][0]["blockers"] = [(root if damage == "old_blocker" else current)["id"]]
    else:
        doc["rules"][0]["conditional"] = {"question": root["id"]}
    before = copy.deepcopy(doc)
    assert principles._open(root, doc=doc)
    got = summary_without_mutation(doc)
    assert got["open_forks"] >= 1 and not got["ok"]
    assert doc == before


def test_orphan_without_document_stays_visible():
    _, root, _ = completed_chain()
    assert principles._open(root)


@pytest.mark.parametrize("damage", ["answer_string", "answer_list", "link_list", "root_list", "root_self_link"])
def test_malformed_history_is_not_hidden_or_crashed(damage):
    doc, root, _ = completed_chain()
    if damage == "answer_string":
        root["answer"] = "не объект"
    elif damage == "answer_list":
        root["answer"] = ["не объект"]
    elif damage == "link_list":
        root["answer"]["revised_by"] = ["не ID"]
    elif damage == "root_list":
        root["revision_of"] = ["не ID"]
    else:
        root["revision_of"] = root["id"]
    before = copy.deepcopy(doc)
    assert principles._open(root, doc=doc)
    assert doc == before


def test_completed_history_does_not_hide_an_unrelated_open_question():
    doc, _, _ = completed_chain()
    foreign = {"id": "Q-2-live", "part": "2", "title": "Новый выбор",
               "text": "Как поступить?", "rule_refs": ["R-1"],
               "source_refs": [], "answer_format": {"type": "text"}}
    qe.freeze(foreign)
    doc["questions"].append(foreign)
    got = summary_without_mutation(doc)
    assert got["open_forks"] == 1 and not got["ok"]


@pytest.mark.parametrize("answer", [["не объект"], "не объект"])
def test_malformed_successor_answer_keeps_history_visible(answer):
    doc, root, current = completed_chain()
    current["answer"] = answer
    before = copy.deepcopy(doc)
    assert principles._open(root, doc=doc)
    got = summary_without_mutation(doc)
    assert got["open_forks"] == 2 and not got["ok"]
    assert doc == before


@pytest.mark.parametrize("status", [None, "применён", "ожидает обработчика"])
@pytest.mark.parametrize("depth", [1, 2])
def test_same_root_branch_cannot_certify_completed_history(status, depth):
    doc, root, current = completed_chain(depth)
    branch = copy.deepcopy(current)
    branch["id"] += "-fork"
    branch.pop("edition")
    if status is None:
        branch.pop("answer")
    else:
        branch["answer"]["status"] = status
    qe.freeze(branch)
    doc["questions"].append(branch)
    before = copy.deepcopy(doc)
    for q in doc["questions"]:
        if (q.get("answer") or {}).get("status") == "пересмотрен":
            assert principles._open(q, doc=doc)
    got = summary_without_mutation(doc)
    assert got["open_forks"] == depth + (status != "применён")
    assert not got["ok"] and doc == before


@pytest.mark.parametrize("field", ["questions", "rules"])
@pytest.mark.parametrize("value", [None, {}, "не список", 17])
def test_nonlist_model_collection_cannot_certify_history(field, value):
    doc, root, _ = completed_chain()
    doc[field] = value
    before = copy.deepcopy(doc)
    # Only the history predicate accepts these arbitrary JSON shapes; the
    # complete principles panel still requires a schema-valid document.
    assert principles._open(root, doc=doc)
    assert doc == before


@pytest.mark.parametrize("value", [[], ["не объект"], "не объект", 17,
                                   {"question": []}, {"question": {}}])
def test_malformed_conditional_cannot_certify_history(value):
    doc, root, _ = completed_chain()
    doc["rules"][0]["conditional"] = value
    before = copy.deepcopy(doc)
    assert principles._open(root, doc=doc)
    assert doc == before
