"""Literal answer and selected meaning are distinct at the real worker boundary.

Synthetic in-memory receipts and deterministic callbacks only. This is prompt
and lexical-gate evidence, not acceptance of any model-produced rule patch.
"""
import copy
import json
import re

import pytest

from cdpm import answers as ans
from test_cdpm_answer_choice_context_gpt import (
    SELECTED, UNSELECTED, _capture_prompt, _pending_doc, _reply, offline_only,
)


RAW_LABEL = "ДОСЛОВНЫЙ ОТВЕТ (raw): "


def _literal_raw(prompt):
    lines = [line for line in prompt.splitlines() if line.startswith(RAW_LABEL)]
    assert len(lines) == 1, "each worker item needs one explicit raw JSON line"
    encoded = lines[0][len(RAW_LABEL):]
    raw = json.loads(encoded)
    assert isinstance(raw, str), "raw must remain a JSON string, not a parsed value"
    assert encoded == json.dumps(raw, ensure_ascii=False)
    return raw


@pytest.mark.parametrize("raw,key,note,fmt", [
    ("б", "б", None, None),
    ("б, но только при сохранении {n}", "б", "но только при сохранении {n}", None),
    (SELECTED, "б", None, None),
    ('Свободный ответ: "цитата"\\путь\nстрока {items}\tконец', None, None,
     {"type": "text"}),
    ("Дословный свободный ответ", "б", "отдельная заметка", {"type": "text"}),
    ("Неразрешённый ключ", "в", None, None),
], ids=["letter", "key-note", "full-option-raw", "text-escapes",
        "nonchoice-key-note", "unknown-key"])
def test_raw_json_is_exact_and_does_not_replace_existing_display(raw, key, note, fmt):
    doc = _pending_doc(raw=raw, key=key, note=note, fmt=fmt)
    before = copy.deepcopy(doc)
    prompt = _capture_prompt(doc)
    assert _literal_raw(prompt) == raw
    shown = f"{key} ({note})" if key and note else (key or raw)
    assert f"ОТВЕТ АВИАКОМПАНИИ: {shown}" in prompt
    if fmt is None and key == "б":
        assert "выбранный вариант: " + SELECTED in prompt
    assert UNSELECTED not in prompt
    assert doc == before  # raw/key/note, receipt, edition and rules all unchanged


def test_prompt_names_raw_as_only_quote_source_not_selected_context():
    prompt = _capture_prompt(_pending_doc(raw="б", key="б"))
    instruction = " ".join(prompt.split("Вопросы с ответами (", 1)[0].split())
    assert "unchanged.basis" in instruction
    assert "ДОСЛОВНЫЙ ОТВЕТ (raw)" in instruction
    assert "подстрок" in instruction
    assert "выбранный вариант" in instruction.lower()
    assert re.search(r"не\b.{0,100}цитат", instruction, re.IGNORECASE)
    assert _literal_raw(prompt) == "б"


@pytest.mark.parametrize("qid", ["Q-3a-данные-перечень", "Q-3b-данные-перечень"])
def test_raw_line_keeps_selected_data_scope_context(qid):
    scope = {"rule": "R1", "statement": "Парк ∈ {A320}", "values": ["Ту-204"]}
    doc = _pending_doc(qid, raw="а", key="а", scope=scope)
    options = doc["questions"][0]["answer_format"]["options"]
    prompt = _capture_prompt(doc)
    assert _literal_raw(prompt) == "а"
    assert "выбранный вариант: " + options[0]["text"] in prompt
    assert options[1]["text"] not in prompt
    assert "Точный контракт перечня: " + json.dumps(scope, ensure_ascii=False) in prompt
    assert "Добавьте только перечисленные значения, сохраните прежние" in prompt


@pytest.mark.parametrize("raw,basis,valid", [
    ("б", "б", True),
    ("б", SELECTED, False),
    (SELECTED, SELECTED, True),
])
def test_raw_lexical_gate_is_unchanged(raw, basis, valid):
    doc = _pending_doc(raw=raw, key="б")
    before = copy.deepcopy(doc)
    q = doc["questions"][0]
    row = {"id": "R1", "unchanged": {"basis": basis}}
    why = ans._check_patch(q, {"rules": [row]}, {"R1": doc["rules"][0]})
    assert (why is None) is valid
    if not valid:
        result = ans.apply_worker_patches(doc, lambda _: _reply(q["id"], [row]))
        assert result["worker_applied"] == 0
        assert any("unchanged требует дословную непустую цитату ответа" in reason
                   for rejected in result["worker_rejected"] for reason in rejected["reasons"])
    # A literal match is necessary, not proof that unchanged is substantively valid.
    assert doc == before
