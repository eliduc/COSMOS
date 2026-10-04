"""Synthetic pending-only retry semantics; no workbook or HTTP claim.

The transport reader is outside this probe. It checks the actual answer/worker
sequence after an accepted raw_map, with no provider, solver or persistent state.
"""
import copy

import pytest

from cdpm import answers
from test_cdpm_answer_choice_context_gpt import (
    SELECTED, UNSELECTED, _capture_prompt, _pending_doc, offline_only,
)


@pytest.mark.parametrize("resubmitted_raw", ["б", "а"])
def test_pending_retry_keeps_original_answer_and_receipt(resubmitted_raw):
    doc = _pending_doc(raw="б", key="б")
    doc["meta"] = {"revision": 8}
    q = doc["questions"][0]
    original = copy.deepcopy(doc)
    original_answer = q["answer"]

    def forbidden_matcher(_prompt):
        pytest.fail("already answered question must not be matched again")

    summary = answers.apply_answers(
        doc, {q["id"]: resubmitted_raw}, forbidden_matcher,
        received_at="2099-01-02T00:00:00+00:00", book_sha256="b" * 64,
        revision=9,
    )
    expected = copy.deepcopy(original)
    expected["meta"]["revision"] = 9
    assert doc == expected
    assert q["answer"] is original_answer
    assert summary["received"] == summary["already_answered"] == 1
    assert summary["applied"] == summary["unparsed"] == 0
    assert summary["revision"] == 9 and summary["source_revision"] == 8
    assert q["answer"]["revision"] == 7
    assert q["answer"]["answer_version"]["book_sha256"] == "a" * 64
    assert q["answer"]["answer_version"]["received_at"] == "2099-01-01T00:00:00+00:00"

    # A changed resubmitted raw is ignored too, not new authority. The caller
    # must require exact equality before presenting a workbook as a pure retry.
    prompt = _capture_prompt(doc)
    assert f"ОТВЕТ АВИАКОМПАНИИ: б — выбранный вариант: {SELECTED}" in prompt
    assert UNSELECTED not in prompt
    assert answers.remaining_awaiting(doc) == 1
    assert doc == expected
