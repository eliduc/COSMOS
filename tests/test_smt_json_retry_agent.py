"""JSON repair shares the existing one-retry SMT budget.

Only the translator input is deterministic here.  JSON parsing, translation
classification and in-process Z3 remain real; no channel, service or solver
process is started.  These tests do not exercise the stand-only policy.
"""

import copy
import json

import pytest

from cdpm import smt_verify as sv


def _doc():
    return {"rules": [
        {"id": "R-LOW", "kind": "HARD", "statement": "x >= 2"},
        {"id": "R-HIGH", "kind": "HARD", "statement": "x <= 7"},
        {"id": "R-SOFT", "kind": "SOFT", "statement": "minimize x"},
    ]}


def _reply(*, first_smt="(>= x 2)", malformed=False):
    rows = [{"rule": "R-LOW", "smt": first_smt},
            {"rule": "R-HIGH", "smt": "(<= x 7)"}]
    if malformed:
        rows.append(None)
    return json.dumps({"decls": ["(declare-fun x () Int)"], "asserts": rows})


class _Replies:
    def __init__(self, *replies):
        self.replies = replies
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        assert len(self.prompts) <= len(self.replies), "unexpected extra LLM call"
        assert "R-LOW | x >= 2" in prompt
        assert "R-HIGH | x <= 7" in prompt
        assert "R-SOFT" not in prompt
        reply = self.replies[len(self.prompts) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply


BAD_JSON = ("no JSON object", '{"decls": [', '{"decls": invalid}')


def _assert_complete_sat(report):
    assert report["verdict"] == "sat"
    assert report["core"] == []
    assert report["n_rules"] == report["n_translated"] == 2
    assert report["n_untranslatable"] == 0
    assert report["untranslated"] == report["dropped"] == report["malformed"] == []
    assert [(row["rule"], row["status"], row["smt"])
            for row in report["translations"]] == [
        ("R-LOW", "translated", "(>= x 2)"),
        ("R-HIGH", "translated", "(<= x 7)"),
    ]


@pytest.mark.parametrize("bad_json", BAD_JSON,
                         ids=["no-object", "truncated", "invalid-json"])
def test_json_first_gets_one_retry_and_real_sat(bad_json):
    doc = _doc()
    before = copy.deepcopy(doc)
    llm = _Replies(bad_json, _reply())
    report = sv.verify(doc, llm)
    assert len(llm.prompts) == 2
    assert llm.prompts[1].startswith(llm.prompts[0])
    assert "JSON" in llm.prompts[1][len(llm.prompts[0]):]
    _assert_complete_sat(report)
    assert doc == before


@pytest.mark.parametrize("bad_json", BAD_JSON,
                         ids=["no-object", "truncated", "invalid-json"])
def test_json_twice_exhausts_total_two_calls_and_refuses(bad_json):
    llm = _Replies(bad_json, bad_json)
    with pytest.raises(sv.SmtVerifyError, match="JSON"):
        sv.verify(_doc(), llm)
    assert len(llm.prompts) == 2


def test_z3_invalid_then_json_does_not_start_another_retry_budget():
    # Undeclared symbol: real Z3 rejection, not a mocked parser verdict.
    llm = _Replies(_reply(first_smt="(>= undeclared_x 2)"), BAD_JSON[1])
    with pytest.raises(sv.SmtVerifyError, match="JSON"):
        sv.verify(_doc(), llm)
    assert len(llm.prompts) == 2
    assert "Z3" in llm.prompts[1][len(llm.prompts[0]):]


def test_malformed_form_then_json_does_not_start_another_retry_budget():
    llm = _Replies(_reply(malformed=True), BAD_JSON[2])
    with pytest.raises(sv.SmtVerifyError, match="JSON"):
        sv.verify(_doc(), llm)
    assert len(llm.prompts) == 2
    assert "asserts[2]" in llm.prompts[1][len(llm.prompts[0]):]


@pytest.mark.parametrize("after_form_error", [False, True],
                         ids=["first-call", "after-form-retry"])
def test_channel_runtime_error_is_not_reinterpreted_as_json_repair(after_form_error):
    error = RuntimeError("translator transport is unavailable")
    replies = ((_reply(malformed=True), error) if after_form_error else (error,))
    llm = _Replies(*replies)
    with pytest.raises(RuntimeError) as caught:
        sv.verify(_doc(), llm)
    assert caught.value is error
    assert len(llm.prompts) == len(replies)


def test_healthy_translation_unchanged_and_uses_one_call():
    doc = _doc()
    before = copy.deepcopy(doc)
    llm = _Replies(_reply())
    report = sv.verify(doc, llm)
    assert len(llm.prompts) == 1
    _assert_complete_sat(report)
    assert doc == before
