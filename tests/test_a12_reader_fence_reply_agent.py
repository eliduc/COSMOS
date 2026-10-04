"""A12 full outer-fence handling with synthetic, injected reader replies.

Real parser/orchestration/carrier checks; no live models or customer artifacts.
Fence tolerance is not JSON substring extraction or semantic completeness.
"""
import copy
import hashlib
import json

import pytest

from test_a12_obligation_readers import Channel, _fixture, core, ob, reply, run


SOURCE = "Тестовый экипаж обязан иметь действующий допуск.".encode("utf-8")
UNIT = {"id": "document:SYNTHETIC:one", "text_sha256": hashlib.sha256(SOURCE).hexdigest()}


def _payload():
    value = reply(json.dumps({"input": {
        "unit": UNIT["id"], "text": SOURCE.decode("utf-8")}}, ensure_ascii=False))
    value["obligations"][0]["statement"] = SOURCE.decode("utf-8")
    return value


def _body(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def _wrap(body, form):
    if form == "json":
        return "```json\n" + body + "\n```"
    if form == "bare-fence":
        return "```\n" + body + "\n```"
    if form == "json-crlf":
        return " \t\r\n```json\r\n" + body.replace("\n", "\r\n") + "\r\n```\r\n \t"
    if form == "bare-crlf":
        return "\r\n \t```\r\n" + body.replace("\n", "\r\n") + "\r\n```\t \r\n"
    assert form == "plain"
    return " \n" + body + "\n\t "


@pytest.mark.parametrize("form", ["plain", "json", "bare-fence", "json-crlf", "bare-crlf"])
def test_only_complete_outer_fence_is_accepted(form):
    value = _payload()
    raw = _wrap(_body(value), form)
    parsed = core()._parse_reply(raw, UNIT, SOURCE)
    expected = copy.deepcopy(value)
    fragment = expected["obligations"][0]["sources"][0]["fragments"][0]
    fragment["address"] = {"origin": "declared", "unit_text_sha256": UNIT["text_sha256"],
                           "declared": {"start": 0, "end": len(SOURCE)},
                           "resolved": {"start": 0, "end": len(SOURCE)},
                           "method": "declared_exact"}
    assert parsed == expected


@pytest.mark.parametrize("case", [
    "prose-before", "prose-after", "prose-inside", "duplicate-top", "duplicate-nested",
    "wrong-language", "unclosed", "multiple-fences", "inline-open", "inline-close",
    "long-close", "wrong-unit", "wrong-quote",
])
def test_fence_does_not_allow_substring_recovery_or_relax_strict_json(case):
    value = _payload()
    body = _body(value)
    if case == "prose-before": raw = "Here is the answer:\n" + _wrap(body, "json")
    elif case == "prose-after": raw = _wrap(body, "json") + "\nAdditional explanation."
    elif case == "prose-inside": raw = "```json\nHere is JSON:\n" + body + "\n```"
    elif case == "duplicate-top":
        raw = _wrap(body.replace('"unit":', '"unit": "document:SYNTHETIC:one", "unit":', 1), "json")
    elif case == "duplicate-nested":
        raw = _wrap(body.replace('"id": "O1"', '"id": "O1", "id": "O1"', 1), "bare-fence")
    elif case == "wrong-language": raw = "```python\n" + body + "\n```"
    elif case == "unclosed": raw = "```json\n" + body
    elif case == "multiple-fences": raw = _wrap(body, "json") + "\n" + _wrap(body, "json")
    elif case == "inline-open": raw = "```json " + body + "\n```"
    elif case == "inline-close": raw = "```json\n" + body + "```"
    elif case == "long-close": raw = "```json\n" + body + "\n````"
    elif case == "wrong-unit":
        value["unit"] = "document:foreign"
        raw = _wrap(_body(value), "json")
    else:
        assert case == "wrong-quote"
        value["obligations"][0]["sources"][0]["fragments"][0]["quote"] += "!"
        raw = _wrap(_body(value), "json")
    with pytest.raises(core().ObligationReadError) as error:
        core()._parse_reply(raw, UNIT, SOURCE)
    assert UNIT["id"] in str(error.value)


@pytest.mark.parametrize("left_form,right_form", [
    ("json", "bare-fence"), ("json-crlf", "bare-crlf"), ("plain", "json"),
])
def test_both_readers_finish_and_preserve_exact_raw_fence_evidence(left_form, right_form):
    _, inputs = _fixture()
    before = copy.deepcopy(inputs)
    supplied = {"LEFT": [], "RIGHT": []}

    def transformed(reader, form):
        def transform(value, _prompt):
            raw = _wrap(_body(value), form)
            supplied[reader].append(raw)
            return raw
        return transform

    left = Channel("claude-fixture", transformed("LEFT", left_form))
    right = Channel("gpt-fixture", transformed("RIGHT", right_form))
    result, _, _ = run(inputs, left, right)
    assert inputs == before
    ledger, evidence = result.ledger, result.evidence
    assert [r["id"] for r in ledger["readers"]] == ["LEFT", "RIGHT"]
    unit_ids = [unit["id"] for unit in ledger["snapshot"]["units"]]
    assert len(left.prompts) == len(right.prompts) == len(unit_ids) == 1
    assert len(evidence["calls"]) == 2
    for reader in ledger["readers"]:
        assert reader["reviewed_units"] == unit_ids
        assert len(reader["obligations"]) == 1
        calls = [call for call in evidence["calls"] if call["reader"] == reader["id"]]
        assert [call["raw_reply"] for call in calls] == supplied[reader["id"]]
        assert reader["execution"] == calls
        for call in calls:
            assert call["reply_sha256"] == hashlib.sha256(call["raw_reply"].encode("utf-8")).hexdigest()
            assert call["prompt_sha256"] == hashlib.sha256(call["prompt"].encode("utf-8")).hexdigest()
    report = ob.evaluate(ledger, **inputs)
    assert report["form"] == "valid" and report["freshness"] == "current"
    assert [r["total"] for r in report["readers"]] == [1, 1]
    assert report["meaning"] == evidence["meaning"] == "not_proven"
    assert report["independence"] == evidence["independence"] == "declared_only"
    assert ledger["expert_review"] is None and ledger["links"] == []


@pytest.mark.parametrize("side", ["LEFT", "RIGHT"])
def test_prose_wrapped_fence_on_either_side_cannot_return_a_candidate(side):
    _, inputs = _fixture()
    before = copy.deepcopy(inputs)

    def bad(value, _prompt):
        return "Explanation before JSON:\n" + _wrap(_body(value), "json")

    left = Channel("claude-fixture", bad if side == "LEFT" else None)
    right = Channel("gpt-fixture", bad if side == "RIGHT" else None)
    with pytest.raises(core().ObligationReadError):
        run(inputs, left, right)
    assert len(left.prompts) == 1
    assert len(right.prompts) == (1 if side == "RIGHT" else 0)
    assert inputs == before
