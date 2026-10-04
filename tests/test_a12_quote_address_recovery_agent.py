"""A12 obligation-readings/2: approved exact-byte address recovery contract.

Only completion text is supplied by the reviewed in-memory Channel; actual
orchestration, parsing, exact input capture, evaluation and sealing are used.
The supplied fragment has no address evidence: the product must derive it.
Correct declared coordinates are authoritative even at a second occurrence.
Wrong integer coordinates recover only from a unique exact byte occurrence;
ambiguous occurrences permit one clarification, never a guessed nearest match.
No source text normalization, product judge replacement, live call or file IO.
This proves machine carriers, not semantic completeness or reader independence.
"""
import copy
import hashlib
import json

import pytest

from cdpm import obligations as ob
from test_a12_obligation_readers import Channel, core, run
from test_cdpm_a12_obligations import _fixture


QUOTE = "Допуск нужен."
UNICODE_TEXT = "🙂\r\nДопуск нужен.\r\nКонец."
REPEATED_TEXT = "Допуск нужен. / Допуск нужен."
CRLF_TEXT = "X\r\nДопуск нужен.\r\nY"

# Literal byte positions, not results produced by a quote-search implementation.
RECOVERY_CASES = (
    ("unicode_character_positions", UNICODE_TEXT,
     {"start": 3, "end": 16, "quote": QUOTE},
     {"start": 6, "end": 30, "quote": QUOTE}),
    ("unicode_shifted_byte_positions", UNICODE_TEXT,
     {"start": 7, "end": 30, "quote": QUOTE},
     {"start": 6, "end": 30, "quote": QUOTE}),
    ("crlf_unique_exact_fragment", CRLF_TEXT,
     {"start": 0, "end": 26, "quote": QUOTE + "\r\n"},
     {"start": 3, "end": 29, "quote": QUOTE + "\r\n"}),
    ("negative_integer", UNICODE_TEXT,
     {"start": -5, "end": -1, "quote": QUOTE},
     {"start": 6, "end": 30, "quote": QUOTE}),
    ("reversed_integer", UNICODE_TEXT,
     {"start": 30, "end": 6, "quote": QUOTE},
     {"start": 6, "end": 30, "quote": QUOTE}),
    ("out_of_bounds_integer", UNICODE_TEXT,
     {"start": 600, "end": 900, "quote": QUOTE},
     {"start": 6, "end": 30, "quote": QUOTE}),
    ("empty_integer_range", UNICODE_TEXT,
     {"start": 6, "end": 6, "quote": QUOTE},
     {"start": 6, "end": 30, "quote": QUOTE}),
)


def _case_inputs_channels(text, fragment, mutate_reply=None, *, sequences=None):
    """Exact synthetic source + canned replies; no address repair in fixtures."""
    _, inputs = _fixture()
    inputs["documents"][0]["body"] = text.encode("utf-8")
    inputs["documents"][0]["clauses"][0]["text"] = text
    raw_by_reader = {"LEFT": [], "RIGHT": []}

    def transform(reader):
        def complete(value, prompt):
            item = value["obligations"][0]
            item["statement"] = "Допуск нужен"
            fragments = fragment if isinstance(fragment, list) else [fragment]
            if sequences and reader in sequences:
                attempts = sequences[reader]
                fragments = attempts[min(len(raw_by_reader[reader]), len(attempts) - 1)]
            item["sources"][0]["fragments"] = copy.deepcopy(fragments)
            if mutate_reply is not None:
                mutate_reply(value)
            # Preserve whitespace and Cyrillic in the evidence; do not rebuild
            # the raw reply from the normalized candidate when checking its SHA.
            raw = "\r\n " + json.dumps(value, ensure_ascii=False, indent=2) + "\r\n"
            raw_by_reader[reader].append(raw)
            return raw
        return complete

    left = Channel("claude-fixture", transform("LEFT"))
    right = Channel("gpt-fixture", transform("RIGHT"))
    return inputs, left, right, raw_by_reader


def _addressed(text, declared, resolved=None):
    """Literal expected evidence; no source search or recovery in the oracle."""
    resolved = declared if resolved is None else resolved
    old = {key: declared[key] for key in ("start", "end")}
    new = {key: resolved[key] for key in ("start", "end")}
    return {**resolved, "address": {
        "origin": "declared" if old == new else "recovered",
        "unit_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "declared": old, "resolved": new,
        "method": "declared_exact" if old == new else "unique_exact_utf8",
    }}


def _assert_exact_result(result, inputs, expected_fragments, raw_by_reader):
    """Assert the real final carrier and every original raw call, including retry."""
    ledger, evidence = result.ledger, result.evidence
    report = ob.evaluate(ledger, **inputs)
    assert report["form"] == "valid", report
    assert report["freshness"] == "current", report
    assert report["meaning"] == "not_proven"
    assert report["independence"] == "declared_only"
    assert report["expert_review"] == {"status": "not_reviewed"}
    assert ledger["links"] == [] and ledger["expert_review"] is None
    assert len(evidence["calls"]) == sum(map(len, raw_by_reader.values()))
    unit = ledger["snapshot"]["units"][0]
    source = inputs["documents"][0]["clauses"][0]["text"].encode("utf-8")
    assert unit["text_sha256"] == hashlib.sha256(source).hexdigest()
    for reader in ledger["readers"]:
        assert reader["version"] == "obligation-readings/2"
        fragments = (expected_fragments[reader["id"]] if isinstance(expected_fragments, dict)
                     else expected_fragments)
        ref = reader["obligations"][0]["sources"][0]
        assert ref == {"unit": unit["id"], "fragments": fragments}
        calls = [c for c in evidence["calls"] if c["reader"] == reader["id"]]
        assert reader["execution"] == calls
        assert len(calls) == len(raw_by_reader[reader["id"]])
        for call, raw in zip(calls, raw_by_reader[reader["id"]]):
            assert call["raw_reply"] == raw
            assert call["reply_sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
            assert call["prompt_sha256"] == hashlib.sha256(call["prompt"].encode("utf-8")).hexdigest()
            assert json.loads(call["prompt"])["input"]["text"].encode("utf-8") == source
            assert "address" not in json.loads(raw)["obligations"][0]["sources"][0]["fragments"][0]
        assert reader["exposure"] == {"other_reader": False, "model": False}
        assert reader["result_sha256"] == ob.seal_reader(reader)["result_sha256"]


@pytest.mark.parametrize("text,fragment", [
    pytest.param(UNICODE_TEXT, {"start": 6, "end": 30, "quote": QUOTE}, id="unicode_exact"),
    pytest.param(REPEATED_TEXT, {"start": 27, "end": 51, "quote": QUOTE}, id="second_duplicate_exact"),
    pytest.param(CRLF_TEXT, {"start": 3, "end": 29, "quote": QUOTE + "\r\n"}, id="crlf_exact"),
    pytest.param("aaa", {"start": 1, "end": 3, "quote": "aa"}, id="overlapping_second_exact"),
])
def test_valid_declared_range_is_preserved_with_raw_reply_evidence(text, fragment):
    inputs, left, right, raw = _case_inputs_channels(text, fragment)
    before = copy.deepcopy(inputs)
    result, _, _ = run(inputs, left, right)
    _assert_exact_result(result, inputs, [_addressed(text, fragment)], raw)
    assert len(left.prompts) == len(right.prompts) == 1
    assert inputs == before


@pytest.mark.parametrize("name,text,declared,resolved", RECOVERY_CASES,
                         ids=[case[0] for case in RECOVERY_CASES])
def test_unique_exact_utf8_match_recovers_address_without_changing_the_raw_reply(name, text, declared, resolved):
    inputs, left, right, raw = _case_inputs_channels(text, declared)
    before = copy.deepcopy(inputs)
    result, _, _ = run(inputs, left, right)
    _assert_exact_result(result, inputs, [_addressed(text, declared, resolved)], raw)
    assert len(left.prompts) == len(right.prompts) == 1
    assert inputs == before


@pytest.mark.parametrize("text,fragment", [
    pytest.param(UNICODE_TEXT, {"start": 6, "end": 30, "quote": "Допуск нужен!"}, id="changed_quote"),
    pytest.param("e\u0301", {"start": 0, "end": 2, "quote": "é"}, id="no_unicode_normalization"),
    pytest.param(CRLF_TEXT, {"start": 3, "end": 28, "quote": QUOTE + "\n"}, id="no_crlf_normalization"),
])
def test_absent_exact_quote_cannot_return_a_candidate(text, fragment):
    inputs, left, right, _ = _case_inputs_channels(text, fragment)
    before = copy.deepcopy(inputs)
    with pytest.raises(core().ObligationReadError):
        run(inputs, left, right)
    assert len(left.prompts) == 1 and right.prompts == []
    assert inputs == before


@pytest.mark.parametrize("text,fragment", [
    pytest.param(REPEATED_TEXT, {"start": 1, "end": 25, "quote": QUOTE}, id="two_matches_wrong_range"),
    pytest.param("aaa", {"start": 0, "end": 1, "quote": "aa"}, id="overlapping_matches_wrong_range"),
])
def test_still_ambiguous_after_one_clarification_is_refused(text, fragment):
    inputs, left, right, raw = _case_inputs_channels(text, fragment)
    before = copy.deepcopy(inputs)
    with pytest.raises(core().ObligationReadError):
        run(inputs, left, right)
    assert len(left.prompts) == 2 and right.prompts == []
    retry = json.loads(left.prompts[1])
    assert retry["mode"] == "address_clarification"
    assert retry["input"] == json.loads(left.prompts[0])["input"]
    assert retry["previous_reply"] == raw["LEFT"][0]
    assert retry["error"]
    assert inputs == before


def test_clarification_selects_valid_second_occurrence_and_retains_both_raw_calls():
    wrong = {"start": 1, "end": 25, "quote": QUOTE}
    second = {"start": 27, "end": 51, "quote": QUOTE}
    inputs, left, right, raw = _case_inputs_channels(REPEATED_TEXT, second,
        sequences={"LEFT": [[wrong], [second]]})
    before = copy.deepcopy(inputs)
    result, _, _ = run(inputs, left, right)
    _assert_exact_result(result, inputs, [_addressed(REPEATED_TEXT, second)], raw)
    assert len(left.prompts) == 2 and len(right.prompts) == 1
    retry = json.loads(left.prompts[1])
    assert retry["mode"] == "address_clarification"
    assert retry["previous_reply"] == raw["LEFT"][0]
    assert "previous_reply" not in json.loads(right.prompts[0])
    assert inputs == before


def test_mixed_fragments_and_origins_do_not_change_left_right_payload_comparison():
    exact = {"start": 6, "end": 30, "quote": QUOTE}
    wrong = {"start": 3, "end": 16, "quote": QUOTE}
    tail = {"start": 32, "end": 43, "quote": "Конец."}
    inputs, left, right, raw = _case_inputs_channels(UNICODE_TEXT, [exact, tail],
        sequences={"LEFT": [[wrong, tail]]})
    before = copy.deepcopy(inputs)
    result, _, _ = run(inputs, left, right)
    _assert_exact_result(result, inputs, {
        "LEFT": [_addressed(UNICODE_TEXT, wrong, exact), _addressed(UNICODE_TEXT, tail)],
        "RIGHT": [_addressed(UNICODE_TEXT, exact), _addressed(UNICODE_TEXT, tail)],
    }, raw)
    comparisons = result.ledger["comparisons"]
    assert len(comparisons) == 1
    assert comparisons[0]["reason"] == "canonical payload bytes identical; meaning not verified"
    assert len(result.evidence["comparison"]["identical"]) == 1
    assert result.evidence["comparison"]["open"] == {"LEFT": [], "RIGHT": []}
    compared = ob.evaluate(result.ledger, **inputs)["comparisons"][0]
    assert compared["identical_payload"] is True and compared["different_parts"] == []
    assert len(left.prompts) == len(right.prompts) == 1
    assert inputs == before


@pytest.mark.parametrize("duplicate", ["fragment", "obligation"])
def test_different_address_origins_cannot_hide_a_duplicate(duplicate):
    exact = {"start": 6, "end": 30, "quote": QUOTE}
    wrong = {"start": 3, "end": 16, "quote": QUOTE}

    def duplicate_reply(value):
        first = value["obligations"][0]
        if duplicate == "fragment":
            first["sources"][0]["fragments"].append(copy.deepcopy(wrong))
        else:
            second = copy.deepcopy(first)
            second["id"] = "O2"
            second["sources"][0]["fragments"] = [copy.deepcopy(wrong)]
            value["obligations"].append(second)

    inputs, left, right, _ = _case_inputs_channels(UNICODE_TEXT, exact, duplicate_reply)
    with pytest.raises(core().ObligationReadError, match="duplicate"):
        run(inputs, left, right)
    assert len(left.prompts) == 1 and right.prompts == []


@pytest.mark.parametrize("field,bad_value", [
    ("start", False), ("start", 6.0), ("end", "30"), ("end", None),
    ("missing_start", None), ("missing_end", None),
])
def test_unique_quote_does_not_excuse_noninteger_or_missing_range(field, bad_value):
    fragment = {"start": 6, "end": 30, "quote": QUOTE}
    if field.startswith("missing_"):
        fragment.pop(field.removeprefix("missing_"))
    else:
        fragment[field] = bad_value
    inputs, left, right, _ = _case_inputs_channels(UNICODE_TEXT, fragment)
    with pytest.raises(core().ObligationReadError):
        run(inputs, left, right)
    assert len(left.prompts) == 1 and right.prompts == []


@pytest.mark.parametrize("location", ["reply", "source_reference"])
def test_exact_quote_cannot_be_rebound_from_a_foreign_unit(location):
    def foreign(value):
        target = value if location == "reply" else value["obligations"][0]["sources"][0]
        target["unit"] = "document:foreign"

    inputs, left, right, _ = _case_inputs_channels(
        UNICODE_TEXT, {"start": 6, "end": 30, "quote": QUOTE}, foreign)
    with pytest.raises(core().ObligationReadError):
        run(inputs, left, right)
    assert len(left.prompts) == 1 and right.prompts == []
