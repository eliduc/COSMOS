"""Unknown SMT symbols are diagnosed by real Z3, not an identifier regex.

Only translator replies are synthetic.  JSON/form checks, SMT construction,
in-process parsing and solving are real.  These controls do not establish the
cause of any historical run or the quality of an actual model translation.
No service, network, LLM or external solver process is used.
"""

import copy
import json

import pytest

from cdpm import smt_verify as sv


DECLARED = "duty_duration_minutes"
UNKNOWN = "duty_minutes"
DECLS = [f"(declare-fun {DECLARED} () Int)"]


def _doc(lower=600):
    return {"rules": [
        {"id": "R-MAX", "kind": "HARD",
         "statement": "Продолжительность смены не более 720 минут."},
        {"id": "R-MIN", "kind": "HARD",
         "statement": f"Продолжительность смены не менее {lower} минут."},
        {"id": "R-SOFT", "kind": "SOFT",
         "statement": "Предпочитать более короткую смену."},
    ]}


def _asserts(*, upper=None, lower=600, symbol=DECLARED):
    return [
        {"rule": "R-MAX", "smt": upper or f"(<= {symbol} 720)"},
        {"rule": "R-MIN", "smt": f"(>= {symbol} {lower})"},
    ]


def _reply(*, decls=None, upper=None, lower=600, symbol=DECLARED):
    return json.dumps({
        "decls": DECLS if decls is None else decls,
        "asserts": _asserts(upper=upper, lower=lower, symbol=symbol),
    }, ensure_ascii=False)


def _bad_reply():
    return _reply(upper=f"(<= {UNKNOWN} 720)")


class _Replies:
    def __init__(self, *replies):
        self.replies = replies
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        assert len(self.prompts) <= len(self.replies), "unexpected extra call"
        assert "R-MAX |" in prompt and "R-MIN |" in prompt
        assert "R-SOFT" not in prompt
        return self.replies[len(self.prompts) - 1]


def _assert_unknown_constant(message, symbol):
    # The public diagnostic must explain the error in Russian and name the
    # exact unknown symbol; retaining only the English Z3 text is insufficient.
    words = str(message).casefold()
    assert symbol in str(message)
    assert "необъявлен" in words
    assert "констант" in words


def _assert_complete(report, verdict):
    assert report["verdict"] == verdict
    assert report["n_rules"] == report["n_translated"] == 2
    assert report["n_untranslatable"] == 0
    assert report["untranslated"] == report["dropped"] == report["malformed"] == []
    assert [(row["rule"], row["status"]) for row in report["translations"]] == [
        ("R-MAX", "translated"), ("R-MIN", "translated"),
    ]
    if verdict == "sat":
        assert report["core"] == []
    else:
        assert set(report["core"]) == {"R-MAX", "R-MIN"}


def test_actual_z3_names_unknown_constant_in_russian():
    script = sv.build_smt_script(DECLS, _asserts(upper=f"(<= {UNKNOWN} 720)"))
    with pytest.raises(sv.SmtVerifyError) as caught:
        sv._run_z3(script)
    _assert_unknown_constant(caught.value, UNKNOWN)


def test_unknown_symbol_retry_names_problem_and_requests_declared_symbol():
    doc = _doc()
    before = copy.deepcopy(doc)
    llm = _Replies(_bad_reply(), _reply())
    report = sv.verify(doc, llm)
    _assert_complete(report, "sat")
    assert len(llm.prompts) == 2
    assert llm.prompts[1].startswith(llm.prompts[0])
    correction = llm.prompts[1][len(llm.prompts[0]):]
    _assert_unknown_constant(correction, UNKNOWN)
    # Check the requested action, not a whole prescribed sentence.  Referring
    # to the declarations/decls is acceptable without inventing a rename.
    words = correction.casefold()
    assert "использ" in words
    assert "объявлен" in words
    assert "decls" in words or DECLARED in correction
    assert report["translations"][0]["smt"] == f"(<= {DECLARED} 720)"
    assert doc == before


def test_repeated_unknown_symbol_refuses_after_exactly_two_calls():
    doc = _doc()
    before = copy.deepcopy(doc)
    llm = _Replies(_bad_reply(), _bad_reply())
    with pytest.raises(sv.SmtVerifyError) as caught:
        sv.verify(doc, llm)
    _assert_unknown_constant(caught.value, UNKNOWN)
    assert len(llm.prompts) == 2
    assert doc == before


def test_json_failure_then_unknown_symbol_has_no_separate_third_attempt():
    llm = _Replies('{"decls": [', _bad_reply())
    with pytest.raises(sv.SmtVerifyError) as caught:
        sv.verify(_doc(), llm)
    _assert_unknown_constant(caught.value, UNKNOWN)
    assert len(llm.prompts) == 2


@pytest.mark.parametrize("lower,verdict", [(600, "sat"), (750, "unsat")])
def test_healthy_real_z3_verdict_and_rule_core_are_unchanged(lower, verdict):
    doc = _doc(lower)
    before = copy.deepcopy(doc)
    llm = _Replies(_reply(lower=lower))
    report = sv.verify(doc, llm)
    _assert_complete(report, verdict)
    assert len(llm.prompts) == 1
    assert doc == before


LEGAL_NAMES = [
    pytest.param(
        ["(declare-fun |duty duration minutes| () Int)"],
        "(<= |duty duration minutes| 720)", "|duty duration minutes|",
        id="quoted-global-identifier"),
    pytest.param(
        DECLS + ["(define-fun within_limit ((duty_minutes Int)) Bool "
                 "(<= duty_minutes 720))"],
        f"(within_limit {DECLARED})", DECLARED,
        id="define-fun-local-parameter"),
    pytest.param(
        DECLS,
        f"(let ((duty_minutes {DECLARED})) (<= duty_minutes 720))", DECLARED,
        id="let-local-name"),
    pytest.param(
        DECLS,
        f"(let ((duty_minutes {DECLARED})) "
        "(let ((duty_minutes (+ duty_minutes 0))) (<= duty_minutes 720)))",
        DECLARED, id="nested-let-shadowing"),
]


@pytest.mark.parametrize("decls,upper,symbol", LEGAL_NAMES)
@pytest.mark.parametrize("lower,verdict", [(600, "sat"), (750, "unsat")])
def test_z3_legal_quoted_and_local_symbols_keep_real_semantics(
        decls, upper, symbol, lower, verdict):
    # Each expression still expresses the same upper bound, including the
    # shadowing control; these are not constant-true replacements of a rule.
    llm = _Replies(_reply(decls=decls, upper=upper, lower=lower, symbol=symbol))
    report = sv.verify(_doc(lower), llm)
    _assert_complete(report, verdict)
    assert len(llm.prompts) == 1
    assert report["translations"][0]["smt"] == upper


@pytest.mark.parametrize("decls,upper", [
    pytest.param(
        DECLS,
        f"(and (let ((local_minutes {DECLARED})) (<= local_minutes 720)) "
        "(>= local_minutes 0))", id="let-name-does-not-escape"),
    pytest.param(
        DECLS + ["(define-fun within_limit ((local_minutes Int)) Bool "
                 "(<= local_minutes 720))"],
        "(<= local_minutes 720)", id="function-parameter-does-not-escape"),
])
def test_real_z3_refuses_names_outside_their_lexical_scope(decls, upper):
    script = sv.build_smt_script(decls, _asserts(upper=upper))
    with pytest.raises(sv.SmtVerifyError) as caught:
        sv._run_z3(script)
    _assert_unknown_constant(caught.value, "local_minutes")


def test_prompt_has_shared_symbols_and_minutes_without_copyable_duty_alias():
    llm = _Replies(_reply())
    _assert_complete(sv.verify(_doc(), llm), "sat")
    assert len(llm.prompts) == 1
    prompt = llm.prompts[0]
    assert UNKNOWN not in prompt
    words = prompt.casefold()
    assert "словар" in words
    assert "общ" in words or "един" in words
    assert "минут" in words
    assert "decls" in prompt


def _partial_doc(lower=600):
    doc = _doc(lower)
    doc["rules"].append({
        "id": "R-REST", "kind": "HARD",
        "statement": "Порядок согласования отдыха определён внешним документом.",
    })
    return doc


def _partial_reply(*, bad=False, lower=600):
    rows = _asserts(upper=f"(<= {UNKNOWN} 720)" if bad else None, lower=lower)
    rows.append({"rule": "R-REST", "status": "untranslatable",
                 "note": "Внешний порядок согласования не задан формулой."})
    return json.dumps({"decls": DECLS, "asserts": rows}, ensure_ascii=False)


def _assert_partial(report):
    assert report["verdict"] == "none"
    assert report["core"] == []
    assert report["n_rules"] == 3
    assert report["n_translated"] == 2
    assert report["n_untranslatable"] == 1
    assert report["untranslated"] == ["R-REST"]
    assert report["dropped"] == report["malformed"] == []
    assert [(row["rule"], row["status"]) for row in report["translations"]] == [
        ("R-MAX", "translated"), ("R-MIN", "translated"),
        ("R-REST", "untranslatable"),
    ]
    assert report["translations"][2]["note"] == (
        "Внешний порядок согласования не задан формулой.")


def test_partial_translation_unknown_symbol_retries_without_false_completeness():
    doc = _partial_doc()
    before = copy.deepcopy(doc)
    llm = _Replies(_partial_reply(bad=True), _partial_reply())
    report = sv.verify(doc, llm)
    assert len(llm.prompts) == 2
    correction = llm.prompts[1][len(llm.prompts[0]):]
    _assert_unknown_constant(correction, UNKNOWN)
    assert "использ" in correction.casefold()
    _assert_partial(report)
    assert report["translations"][0]["smt"] == f"(<= {DECLARED} 720)"
    assert doc == before


def test_partial_translation_repeated_unknown_symbol_refuses_after_two_calls():
    llm = _Replies(_partial_reply(bad=True), _partial_reply(bad=True))
    with pytest.raises(sv.SmtVerifyError) as caught:
        sv.verify(_partial_doc(), llm)
    _assert_unknown_constant(caught.value, UNKNOWN)
    assert len(llm.prompts) == 2


@pytest.mark.parametrize("lower", [600, 750], ids=["sat-subset", "unsat-subset"])
def test_healthy_partial_subset_never_claims_whole_rule_set_verdict(lower):
    llm = _Replies(_partial_reply(lower=lower))
    _assert_partial(sv.verify(_partial_doc(lower), llm))
    assert len(llm.prompts) == 1


def test_all_untranslatable_does_not_parse_unused_declarations_or_retry():
    # There is no translated subset to parse.  A syntactically invalid unused
    # declaration would make real Z3 fail if this path called the parser; no
    # replacement parser or manufactured solver verdict is installed here.
    reply = json.dumps({
        "decls": ["(declare-fun intentionally_unclosed () Int"],
        "asserts": [
            {"rule": rule, "status": "untranslatable",
             "note": "Переводчик не смог дать формулу."}
            for rule in ("R-MAX", "R-MIN")
        ],
    }, ensure_ascii=False)
    llm = _Replies(reply)
    report = sv.verify(_doc(), llm)
    assert len(llm.prompts) == 1
    assert report["verdict"] == "none" and report["core"] == []
    assert report["n_rules"] == report["n_untranslatable"] == 2
    assert report["n_translated"] == 0
    assert report["untranslated"] == ["R-MAX", "R-MIN"]
    assert report["dropped"] == report["malformed"] == []
