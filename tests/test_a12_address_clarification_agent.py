"""Adversarial A12 /2 address clarification through the public reader API.

Only injected prompt/reply channels are doubles. Parser, provenance, sealing,
input guards and evaluation are real. Synthetic evidence is not independent
model reading or proof of semantic completeness; no live channel is created.
"""
import copy
import hashlib
import json

import pytest

from cdpm import llm, obligations as ob
from test_a12_obligation_readers import core
from test_cdpm_a12_obligations import _fixture


TEXT = "Преамбула. Первый допуск обязателен; второй допуск проверяется. Отдельное правило."
QUOTE = "допуск"
FIXED = "Отдельное правило."


def _inputs(two_units=False):
    _, inputs = _fixture()
    texts = ["UNIT_ONE_PRIVATE. " + TEXT]
    if two_units:
        texts.append("UNIT_TWO_PRIVATE. " + TEXT)
    inputs["documents"][0].update(
        body="\n".join(texts).encode("utf-8"),
        clauses=[{"anchor": "§" + str(i + 1), "text": text}
                 for i, text in enumerate(texts)])
    return inputs


def _fragment(text, quote, *, second=False):
    source, needle = text.encode("utf-8"), quote.encode("utf-8")
    start = source.index(needle)
    if second:
        start = source.index(needle, start + 1)
    return {"start": start, "end": start + len(needle), "quote": quote}


def _initial(data):
    unit, text = data["input"]["unit"], data["input"]["text"]
    first = {"id": "O1", "subject": "экипаж", "modality": "requirement",
             "statement": data["reader"] + "_PRIVATE_STATEMENT: требуется допуск",
             "conditions": [{"id": "C1", "text": "При выполнении рейса"},
                            {"id": "C2", "text": "При действующем плане"}],
             "exceptions": [{"id": "E1", "text": "Кроме учебного рейса"},
                            {"id": "E2", "text": "Кроме указанного исключения"}],
             "sources": [{"unit": unit, "fragments": [
                 {"start": 0, "end": len(QUOTE.encode("utf-8")), "quote": QUOTE},
                 _fragment(text, FIXED)]}], "depends_on": []}
    second = {"id": "O2", "subject": "экипаж", "modality": "requirement",
              "statement": "Отдельное условие после допуска", "conditions": [],
              "exceptions": [], "sources": [{"unit": unit, "fragments": [
                  _fragment(text, FIXED)]}], "depends_on": ["O1"]}
    return {"unit": unit, "obligations": [first, second]}


def _correct(value, text, style="same"):
    fragment = (_fragment(text, QUOTE, second=True) if style == "same"
                else _fragment(text, "второй допуск проверяется"))
    value["obligations"][0]["sources"][0]["fragments"][0] = fragment


class Scripted:
    def __init__(self, reader, *, ambiguous=True, style="same", damage=None,
                 first_damage=None, repeat=False, after=None):
        self.reader, self.ambiguous, self.style = reader, ambiguous, style
        self.damage, self.first_damage = damage, first_damage
        self.repeat, self.after = repeat, after
        self.prompts, self.raws, self.originals = [], [], {}
        llm.stamp_identity(self, provider="synthetic",
                           model="claude-fixture" if reader == "LEFT" else "gpt-fixture",
                           transport="test")

    def __call__(self, prompt):
        self.prompts.append(prompt)
        data = json.loads(prompt)
        unit, text = data["input"]["unit"], data["input"]["text"]
        if data.get("mode") == "address_clarification":
            value = copy.deepcopy(self.originals[unit])
            if not self.repeat:
                _correct(value, text, self.style)
            if self.damage:
                self.damage(value, data)
        else:
            value = _initial(data)
            if not self.ambiguous:
                _correct(value, text)
            if self.first_damage:
                self.first_damage(value, data)
            self.originals[unit] = copy.deepcopy(value)
        raw = json.dumps(value, ensure_ascii=False, indent=2)
        if data.get("mode") != "address_clarification":
            raw = " \r\n```json\r\n" + raw + "\r\n```\r\n"
        if self.after:
            self.after(self, data)
        self.raws.append(raw)
        return raw


def _run(inputs, left, right, **kwargs):
    return core().read_independently(inputs=inputs, resolve_channel=lambda role, _unit:
        left if role == "extractor" else right, **kwargs)


def _calls(evidence, channels):
    assert evidence["schema_version"] == "obligation-readings/2"
    calls = evidence["calls"]
    for reader, channel in channels.items():
        own = [call for call in calls if call["reader"] == reader]
        assert [call["prompt"] for call in own] == channel.prompts
        returned = [call for call in own if isinstance(call.get("raw_reply"), str)]
        assert [call["raw_reply"] for call in returned] == channel.raws
        for call in own:
            assert call["attempt"] in (1, 2)
            assert call["purpose"] == ("reading" if call["attempt"] == 1 else "address_clarification")
            assert call["status"] in ("valid", "ambiguous", "invalid", "call_failed")
            assert call["prompt_sha256"] == hashlib.sha256(call["prompt"].encode("utf-8")).hexdigest()
            if isinstance(call.get("raw_reply"), str):
                assert call["reply_sha256"] == hashlib.sha256(call["raw_reply"].encode("utf-8")).hexdigest()
    return calls


def _failed(error, left, right):
    evidence = error.evidence
    assert evidence["candidate"] is False
    assert evidence["max_address_clarifications_per_unit"] == 1
    return _calls(evidence, {"LEFT": left, "RIGHT": right})


@pytest.mark.parametrize("style", ["same", "expand"])
def test_clarification_is_once_per_exact_unit_and_reader_and_prompt_is_private(style):
    inputs = _inputs(two_units=True)
    before = copy.deepcopy(inputs)
    left, right = Scripted("LEFT", style=style), Scripted("RIGHT", style=style)
    result = _run(inputs, left, right)
    ledger, evidence = result.ledger, result.evidence
    calls = _calls(evidence, {"LEFT": left, "RIGHT": right})
    assert len(calls) == 8 and inputs == before
    for reader, channel in (("LEFT", left), ("RIGHT", right)):
        own = [call for call in calls if call["reader"] == reader]
        assert [call["status"] for call in own] == ["ambiguous", "valid"] * 2
        for unit in {call["unit"] for call in own}:
            attempts = [call for call in own if call["unit"] == unit]
            assert [call["attempt"] for call in attempts] == [1, 2]
            initial, clarification = [json.loads(call["prompt"]) for call in attempts]
            assert set(clarification) == {"reader", "input", "instructions", "mode", "previous_reply", "error"}
            assert clarification["mode"] == "address_clarification"
            assert clarification["reader"] == reader and clarification["input"] == initial["input"]
            assert clarification["previous_reply"] == attempts[0]["raw_reply"]
            assert clarification["error"] and clarification["instructions"]
            other_reader = "RIGHT" if reader == "LEFT" else "LEFT"
            assert other_reader + "_PRIVATE_STATEMENT" not in attempts[1]["prompt"]
            other_unit_marker = ("UNIT_TWO_PRIVATE" if "UNIT_ONE_PRIVATE" in initial["input"]["text"]
                                 else "UNIT_ONE_PRIVATE")
            assert other_unit_marker not in attempts[1]["prompt"]
            assert "Q-OPEN" not in attempts[1]["prompt"]
        saved = next(r for r in ledger["readers"] if r["id"] == reader)
        assert saved["version"] == "obligation-readings/2" and saved["execution"] == own
        assert len(saved["reviewed_units"]) == 2 and len(saved["obligations"]) == 4
        for unit, initial in channel.originals.items():
            rows = [r for r in saved["obligations"] if r["id"].startswith(unit + "/")]
            assert [r["id"] for r in rows] == [unit + "/O1", unit + "/O2"]
            for old, new in zip(initial["obligations"], rows):
                for field in ("subject", "statement", "modality", "conditions", "exceptions"):
                    assert new[field] == old[field]
                assert new["depends_on"] == [unit + "/" + dep for dep in old["depends_on"]]
            unchanged = rows[0]["sources"][0]["fragments"][1]
            assert {k: unchanged[k] for k in ("start", "end", "quote")} == initial["obligations"][0]["sources"][0]["fragments"][1]
    report = ob.evaluate(ledger, **inputs)
    assert report["form"] == "valid" and report["freshness"] == "current"
    assert report["meaning"] == "not_proven" and ledger["expert_review"] is None


def _damage(case, value, data):
    first, second = value["obligations"]
    text = data["input"]["text"]
    if case == "ids": first["id"], second["depends_on"] = "NEW", ["NEW"]
    elif case == "order": value["obligations"].reverse()
    elif case == "count": value["obligations"].pop()
    elif case == "subject": first["subject"] = "другой субъект"
    elif case == "statement": first["statement"] += " новое правило"
    elif case == "modality": first["modality"] = "permission"
    elif case == "conditions": first["conditions"][0]["text"] += " изменено"
    elif case == "condition-order": first["conditions"].reverse()
    elif case == "exceptions": first["exceptions"][0]["text"] += " изменено"
    elif case == "exception-order": first["exceptions"].reverse()
    elif case == "dependencies": second["depends_on"] = []
    elif case == "foreign-source": first["sources"][0]["unit"] = "document:foreign"
    elif case == "fragment-count": first["sources"][0]["fragments"].append(_fragment(text, "Преамбула."))
    elif case == "fixed-fragment": first["sources"][0]["fragments"][1] = _fragment(text, "Преамбула.")
    elif case == "other-quote": first["sources"][0]["fragments"][0] = _fragment(text, "Преамбула.")
    elif case == "shorter-quote": first["sources"][0]["fragments"][0] = _fragment(text, "доп", second=True)
    else: raise AssertionError(case)


@pytest.mark.parametrize("case", ["ids", "order", "count", "subject", "statement", "modality",
    "conditions", "condition-order", "exceptions", "exception-order", "dependencies",
    "foreign-source", "fragment-count", "fixed-fragment", "other-quote", "shorter-quote"])
def test_address_retry_cannot_change_semantics_or_unaffected_fragments(case):
    inputs = _inputs()
    left = Scripted("LEFT", damage=lambda value, data: _damage(case, value, data))
    right = Scripted("RIGHT", ambiguous=False)
    with pytest.raises(core().ObligationReadError) as error:
        _run(inputs, left, right)
    assert len(left.prompts) == 2 and right.prompts == []
    assert [call["status"] for call in _failed(error.value, left, right)] == ["ambiguous", "invalid"]


def test_ambiguity_exhaustion_preserves_both_attempts_and_never_calls_a_third_time():
    left, right = Scripted("LEFT", repeat=True), Scripted("RIGHT", ambiguous=False)
    with pytest.raises(core().ObligationReadError) as error:
        _run(_inputs(), left, right)
    assert len(left.prompts) == 2 and right.prompts == []
    assert [call["status"] for call in _failed(error.value, left, right)] == ["ambiguous", "ambiguous"]


@pytest.mark.parametrize("case", ["zero-match", "top-structure", "later-structure"])
def test_non_address_failure_does_not_spend_a_clarification(case):
    def damage(value, _data):
        if case == "zero-match":
            value["obligations"][0]["sources"][0]["fragments"][0]["quote"] = "ABSENT_SOURCE_TEXT"
        elif case == "top-structure": value["approved"] = True
        else: value["obligations"][1]["depends_on"] = ["MISSING"]
    left, right = Scripted("LEFT", first_damage=damage), Scripted("RIGHT", ambiguous=False)
    with pytest.raises(core().ObligationReadError) as error:
        _run(_inputs(), left, right)
    assert len(left.prompts) == 1 and right.prompts == []
    assert [call["status"] for call in _failed(error.value, left, right)] == ["invalid"]


@pytest.mark.parametrize("guard", ["identity", "input"])
@pytest.mark.parametrize("when", ["before-retry", "after-retry"])
def test_original_guards_surround_the_clarification(guard, when):
    inputs = _inputs()
    left, right = Scripted("LEFT"), Scripted("RIGHT", ambiguous=False)
    ticks_after_first = 0

    def change():
        if guard == "identity": llm.stamp_identity(left, model="claude-changed-fixture")
        else: inputs["documents"][0]["body"] += b"changed"

    def checkpoint():
        nonlocal ticks_after_first
        if when == "before-retry" and len(left.prompts) == 1:
            ticks_after_first += 1
            # First tick is after attempt 1; second is before attempt 2.
            if ticks_after_first == 2: change()

    if when == "after-retry":
        left.after = lambda _channel, data: change() if data.get("mode") == "address_clarification" else None
    with pytest.raises(core().ObligationReadError) as error:
        _run(inputs, left, right, checkpoint=checkpoint)
    assert len(left.prompts) == (1 if when == "before-retry" else 2)
    assert right.prompts == []
    calls = _failed(error.value, left, right)
    assert calls[0]["status"] == "ambiguous"
    if when == "after-retry": assert calls[-1]["status"] == "invalid"


@pytest.mark.parametrize("when", ["before-retry", "after-retry"])
def test_checkpoint_cancellation_propagates_unchanged_around_retry(when):
    left, right = Scripted("LEFT"), Scripted("RIGHT", ambiguous=False)
    reason = RuntimeError("checkpoint cancelled clarification")
    ticks_after_first = 0

    def checkpoint():
        nonlocal ticks_after_first
        if len(left.prompts) == 1:
            ticks_after_first += 1
            if when == "before-retry" and ticks_after_first == 2: raise reason
        if when == "after-retry" and len(left.prompts) == 2: raise reason

    with pytest.raises(RuntimeError) as error:
        _run(_inputs(), left, right, checkpoint=checkpoint)
    assert error.value is reason
    assert len(left.prompts) == (1 if when == "before-retry" else 2) and right.prompts == []


def test_transport_failure_on_retry_is_recorded_without_a_candidate():
    def fail(_channel, data):
        if data.get("mode") == "address_clarification": raise TimeoutError("retry transport unavailable")
    left, right = Scripted("LEFT", after=fail), Scripted("RIGHT", ambiguous=False)
    with pytest.raises(core().ObligationReadError) as error:
        _run(_inputs(), left, right)
    assert len(left.prompts) == 2 and right.prompts == []
    assert [call["status"] for call in _failed(error.value, left, right)] == ["ambiguous", "call_failed"]


def test_caller_channel_cancellation_on_retry_is_not_wrapped():
    class CancelReading(RuntimeError):
        pass
    reason = CancelReading("caller cancelled retry")
    def fail(_channel, data):
        if data.get("mode") == "address_clarification": raise reason
    left, right = Scripted("LEFT", after=fail), Scripted("RIGHT", ambiguous=False)
    with pytest.raises(CancelReading) as error:
        _run(_inputs(), left, right, cancellation_errors=(CancelReading,))
    assert error.value is reason and len(left.prompts) == 2 and right.prompts == []
