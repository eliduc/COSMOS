"""Worker prompt data is not a second format string (no provider calls)."""
import copy
import json
import socket
import subprocess

import pytest

from cdpm import answers as ans
from cdpm import model as m
from cdpm import question_editions


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("network/process execution is forbidden in this test")

    monkeypatch.setattr(socket, "create_connection", refused)
    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket.socket, "connect_ex", refused)
    monkeypatch.setattr(subprocess, "Popen", refused)


def _pending_doc(raw="подтверждаю исходное требование", *, key=None, note=None):
    qid = "Q-2-prompt-format"
    rule = {"id": "R1", "title": "Исходное требование",
            "statement": "Сохраняется исходное требование.",
            "kind": "HARD", "zone": "ROSTERING", "sources": [],
            "blockers": [qid]}
    question = {"id": qid, "title": "Уточнение исходного требования",
                "text": "Как применяется исходное требование?",
                "rule_refs": [rule["id"]], "source_refs": [],
                "answer_format": {"type": "text"}}
    question_editions.freeze(question)
    question["answer"] = {"status": "ожидает обработчика", "raw": raw}
    if key is not None:
        question["answer"]["key"] = key
    if note is not None:
        question["answer"]["note"] = note
    doc = {"rules": [rule], "questions": [question]}
    assert question_editions.application_problems(doc, question) == []
    return doc


class _PromptCaptured(Exception):
    pass


def _capture_prompt(doc):
    before = copy.deepcopy(doc)
    calls = []

    def fake_worker(prompt):
        calls.append(prompt)
        # Stop at the real worker boundary, before any returned patch exists.
        raise _PromptCaptured

    with pytest.raises(_PromptCaptured):
        ans.apply_worker_patches(doc, fake_worker)
    assert len(calls) == 1
    assert doc == before
    return calls[0]


def test_real_dictionary_json_reaches_worker_verbatim():
    listing = m.machine_dictionary_listing()
    assert '{"AAA": 1, "BBB": 2}' in listing
    assert 'condition={"kind":"base_code","value":"AAA"}' in listing
    prompt = _capture_prompt(_pending_doc())
    assert prompt.count(listing) == 1
    assert prompt.count("Вопросы с ответами (1):") == 1
    assert '{"patches": [{' in prompt
    assert '{{"patches": [{{' not in prompt


@pytest.mark.parametrize("listing", [
    '{"AAA": 1, "BBB": 2}',
    '{"nested": {"value": 1}, "list": [{"value": 2}]}',
    "literal {n} and {items} and {PARAM_DICT}",
    "literal {foreign_placeholder}",
    "literal {items.__class__} and {n!r:>10}",
    "literal {{escaped}} and {{{n}}}",
    "literal unmatched {",
    "literal unmatched }",
])
def test_dictionary_braces_are_literal_data(monkeypatch, listing):
    monkeypatch.setattr(m, "machine_dictionary_listing", lambda: listing)
    prompt = _capture_prompt(_pending_doc())
    assert prompt.count(listing) == 1
    assert prompt.count("Вопросы с ответами (1):") == 1


@pytest.mark.parametrize("with_key", [False, True])
def test_answer_json_and_placeholders_remain_literal(monkeypatch, with_key):
    literal = ('{"AAA": 1} {n} {items} {PARAM_DICT} '
               '{foreign} {{escaped}}')
    monkeypatch.setattr(m, "machine_dictionary_listing", lambda: "Словарь без скобок")
    doc = _pending_doc(literal, key="а" if with_key else None,
                       note=literal if with_key else None)
    prompt = _capture_prompt(doc)
    shown = f"а ({literal})" if with_key else literal
    assert prompt.count(f"ОТВЕТ АВИАКОМПАНИИ: {shown}\n") == 1
    assert prompt.count("Вопросы с ответами (1):") == 1


def test_template_interpolation_occurs_once_not_inside_arguments(monkeypatch):
    template = "{{template}}|D={PARAM_DICT}|N={n}|I={items}|{{end}}"
    listing = '{"AAA":1}|{n}|{items}|{PARAM_DICT}|{foreign}|{{escaped}}'
    answer = '{"BBB":2}|{n}|{items}|{foreign}|{{answer}}'
    monkeypatch.setattr(ans, "_PROMPT_WORKER", template)
    monkeypatch.setattr(m, "machine_dictionary_listing", lambda: listing)
    doc = _pending_doc(answer)
    question = doc["questions"][0]
    item = (f"{question['id']} | {question['title']}\n"
            f"Вопрос: {question['text']}\n"
            f"ДОСЛОВНЫЙ ОТВЕТ (raw): {json.dumps(answer, ensure_ascii=False)}\n"
            f"ОТВЕТ АВИАКОМПАНИИ: {answer}\n"
            "Затронутые строки: " + json.dumps(doc["rules"], ensure_ascii=False))
    assert _capture_prompt(doc) == (
        "{template}|D=" + listing + "|N=1|I=" + item + "|{end}")


def test_empty_patch_response_remains_rejected_without_mutation(monkeypatch):
    listing = '{"AAA": 1, "BBB": 2}'
    monkeypatch.setattr(m, "machine_dictionary_listing", lambda: listing)
    doc = _pending_doc()
    before = copy.deepcopy(doc)
    calls = []

    def fake_worker(prompt):
        calls.append(prompt)
        return '{"patches": []}'

    result = ans.apply_worker_patches(doc, fake_worker)
    assert result["worker_applied"] == 0
    assert result["worker_rejected"] == [{
        "question": doc["questions"][0]["id"],
        "reasons": ["обработчик не дал патча по вопросу"],
    }]
    assert len(calls) == 1
    assert listing in calls[0]
    assert doc == before
