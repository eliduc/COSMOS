"""Selected choice meaning reaches the real worker boundary without new authority.

Synthetic in-memory documents and deterministic callbacks only. No workbook,
provider, service, solver or product-state write is used by these tests.
"""
import copy
import json
import socket
import subprocess

import pytest

from cdpm import answer_versions, answers as ans, data_scope, question_editions


SELECTED = 'Сохранить выбранное чтение; точные данные {"marker": "{items}"}.'
UNSELECTED = "Отвергнутое чтение — UNSELECTED_OPTION_SENTINEL."
CHOICES = [{"key": "а", "text": UNSELECTED},
           {"key": "б", "text": SELECTED}]


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("network/process/provider execution is forbidden")

    monkeypatch.setattr(socket, "create_connection", refused)
    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket.socket, "connect_ex", refused)
    monkeypatch.setattr(subprocess, "Popen", refused)
    monkeypatch.setattr(ans.llm_mod, "channel", refused)


def _pending_doc(qid="Q-2-арб-C2-R1-A1-001-E2", *, raw="б", key="б",
                 note=None, fmt=None, freeze=True, scope=None):
    rule = {"id": "R1", "title": "Синтетическое исходное требование",
            "statement": "Сохраняется исходная норма.", "kind": "HARD",
            "zone": "ROSTERING", "sources": [], "blockers": [qid]}
    question = {"id": qid, "title": "Выбор чтения",
                "text": "Какое чтение следует применить?",
                "rule_refs": [rule["id"]], "source_refs": [],
                "answer_format": copy.deepcopy(fmt if fmt is not None else
                                                {"type": "choice", "options": CHOICES})}
    if scope is not None:
        question["data_scope"] = copy.deepcopy(scope)
        question.update(data_scope.wording(scope))
    if freeze:
        question_editions.freeze(question)
    answer = {"status": "ожидает обработчика", "raw": raw, "revision": 7}
    if key is not None:
        answer["key"] = key
    if note is not None:
        answer["note"] = note
    # Genuine receipt SHAPE, deliberately synthetic identity; no source book is read.
    answer_versions.stamp(answer, None, answer_versions.context(
        received_at="2099-01-01T00:00:00+00:00", book_sha256="a" * 64))
    question["answer"] = answer
    doc = {"rules": [rule], "questions": [question]}
    assert question_editions.application_problems(doc, question) == []
    assert answer_versions.problems(answer) == []
    return doc


class _PromptCaptured(Exception):
    pass


def _capture_prompt(doc):
    before = copy.deepcopy(doc)
    calls = []

    def fake_worker(prompt):
        calls.append(prompt)
        raise _PromptCaptured  # stop before accepting/validating any returned patch

    with pytest.raises(_PromptCaptured):
        ans.apply_worker_patches(doc, fake_worker)
    assert len(calls) == 1
    # Includes raw, key, note, receipt/fingerprint, edition and all rule fields.
    assert doc == before
    return calls[0]


@pytest.mark.parametrize("qid", [
    "Q-1-противоречие", "Q-2-чтение", "Q-2-арб-C2-R1-A1-001-E2",
    "Q-2-fit-синтетика", "Q-3a-чтение", "Q-3b-чтение",
])
def test_selected_b_text_reaches_non_data_choice_worker(qid):
    prompt = _capture_prompt(_pending_doc(qid))
    assert prompt.count(SELECTED) == 1
    assert UNSELECTED not in prompt
    assert "ОТВЕТ АВИАКОМПАНИИ: б" in prompt


def test_selected_text_keeps_note_and_literal_braces_without_mutating_receipt():
    note = "но только при сохранении условий {n} и {PARAM_DICT}"
    doc = _pending_doc(raw="б, " + note, note=note)
    prompt = _capture_prompt(doc)
    assert f"ОТВЕТ АВИАКОМПАНИИ: б ({note})" in prompt
    assert prompt.count(SELECTED) == 1
    assert UNSELECTED not in prompt


def test_key_from_full_option_raw_still_has_selected_meaning():
    prompt = _capture_prompt(_pending_doc(raw=SELECTED, key="б"))
    assert prompt.count(SELECTED) == 1
    assert UNSELECTED not in prompt


@pytest.mark.parametrize("qid", ["Q-3a-данные-перечень", "Q-3b-данные-перечень"])
def test_data_scope_choice_keeps_exact_existing_extension_context(qid):
    scope = {"rule": "R1", "statement": "Парк ∈ {A320}", "values": ["Ту-204"]}
    doc = _pending_doc(qid, raw="а", key="а", scope=scope)
    options = doc["questions"][0]["answer_format"]["options"]
    prompt = _capture_prompt(doc)
    assert prompt.count(options[0]["text"]) == 1
    assert options[1]["text"] not in prompt
    assert "Точный контракт перечня: " + json.dumps(scope, ensure_ascii=False) in prompt
    assert "Добавьте только перечисленные значения, сохраните прежние" in prompt


@pytest.mark.parametrize("key", [None, "б"])
def test_nonchoice_keeps_raw_or_key_note_display_without_inventing_option(key):
    raw = 'Точный свободный ответ {"n": "{items}"}'
    note = "сохранить без переосмысления" if key else None
    doc = _pending_doc(raw=raw, key=key, note=note,
                       fmt={"type": "text", "options": copy.deepcopy(CHOICES)})
    prompt = _capture_prompt(doc)
    shown = f"б ({note})" if key else raw
    assert f"ОТВЕТ АВИАКОМПАНИИ: {shown}\n" in prompt
    assert SELECTED not in prompt and UNSELECTED not in prompt


@pytest.mark.parametrize("key", [None, "в", "b", "Б"])
def test_absent_or_nonexact_key_does_not_invent_a_selected_option(key):
    doc = _pending_doc(raw="неподставляемый дословный ответ", key=key)
    prompt = _capture_prompt(doc)
    assert SELECTED not in prompt and UNSELECTED not in prompt
    assert "выбранный вариант:" not in prompt


@pytest.mark.parametrize("options", [
    None, [], {}, "не список", [None, "не объект"],
    [{"key": "б"}], [{"key": "б", "text": None}],
    [{"key": "б", "text": 123}], [{"key": "б", "text": {"value": "not text"}}],
    [{"key": "б", "text": SELECTED}, {"key": "б", "text": UNSELECTED}],
])
def test_malformed_legacy_options_do_not_supply_guessed_context(options):
    # Legacy in-memory shape: this probes formatting, not document admissibility.
    # No frozen edition is fabricated from unreadable option entries.
    doc = _pending_doc(fmt={"type": "choice", "options": options}, freeze=False)
    prompt = _capture_prompt(doc)
    assert SELECTED not in prompt and UNSELECTED not in prompt
    assert "выбранный вариант:" not in prompt


def _reply(qid, rows):
    return json.dumps({"patches": [{"question": qid, "change": "синтетика", "rules": rows}]}, ensure_ascii=False)


def test_choice_context_does_not_allow_export_solver_field():
    doc = _pending_doc()
    before = copy.deepcopy(doc)
    q = doc["questions"][0]
    patched = copy.deepcopy(doc["rules"][0])
    patched["export_solver"] = True
    result = ans.apply_worker_patches(doc, lambda _: _reply(q["id"], [patched]))
    assert result["worker_applied"] == 0
    assert any("export_solver" in why and "вне схемы" in why
               for failure in result["worker_rejected"] for why in failure["reasons"])
    assert doc == before


def test_selected_option_text_is_not_unchanged_answer_quote_authority():
    doc = _pending_doc(raw="б", key="б")
    before = copy.deepcopy(doc)
    q = doc["questions"][0]
    row = {"id": "R1", "unchanged": {"basis": SELECTED}}
    result = ans.apply_worker_patches(doc, lambda _: _reply(q["id"], [row]))
    assert result["worker_applied"] == 0
    assert any("unchanged требует дословную непустую цитату ответа" in why
               for failure in result["worker_rejected"] for why in failure["reasons"])
    assert doc == before
    # Existing lexical quote gate still accepts only the literal raw here; it
    # does not thereby prove that an unchanged result would be substantively valid.
    raw_basis = {"id": "R1", "unchanged": {"basis": q["answer"]["raw"]}}
    assert ans._check_patch(q, {"rules": [raw_basis]}, {"R1": doc["rules"][0]}) is None
    assert doc == before
