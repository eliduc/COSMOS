"""A12 actual reader orchestration with injected channels only; no live model.

Expected counts and extracted statements are fixture constants. Carrier and
provenance verdicts are real; only prompt -> raw JSON is a controlled seam.
"""
import copy
import importlib
import json

import pytest

from cdpm import llm, obligations as ob
from test_cdpm_a12_obligations import CLAUSE, _fixture, _answer_fixture


def core():
    return importlib.import_module("cdpm.obligation_readers")


def reply(prompt):
    unit = json.loads(prompt)["input"]
    return {"unit": unit["unit"], "obligations": [{
        "id": "O1", "subject": "экипаж", "modality": "requirement",
        "statement": "Предел смены 600 минут", "conditions": [], "exceptions": [],
        "sources": [{"unit": unit["unit"], "fragments": [
            {"start": 0, "end": len(unit["text"].encode("utf-8")), "quote": unit["text"]}]}],
        "depends_on": []}]}


class Channel:
    def __init__(self, model, transform=None):
        self.prompts = []
        self.transform = transform
        llm.stamp_identity(self, provider="synthetic", model=model, transport="test")

    def __call__(self, prompt):
        self.prompts.append(prompt)
        result = reply(prompt)
        if self.transform:
            result = self.transform(result, prompt)
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)


def run(inputs, left=None, right=None, **kwargs):
    left = left or Channel("claude-fixture")
    right = right or Channel("gpt-fixture")
    result = core().read_independently(inputs=inputs, resolve_channel=lambda role, unit:
        left if role == "extractor" else right, **kwargs)
    return result, left, right


def test_actual_two_readings_bind_exact_inputs_and_keep_results_separate():
    _, inputs = _fixture()
    before = copy.deepcopy(inputs)
    result, left, right = run(inputs)
    ledger = result.ledger
    report = ob.evaluate(ledger, **inputs)
    assert report["form"] == "valid" and report["freshness"] == "current"
    assert [row["total"] for row in report["readers"]] == [1, 1]
    assert [row["linked"] for row in report["readers"]] == [0, 0]
    assert report["meaning"] == "not_proven"
    assert report["independence"] == "declared_only"
    assert ledger["links"] == [] and ledger["expert_review"] is None
    assert len(ledger["comparisons"]) == 1
    assert ledger["comparisons"][0]["reason"] == "canonical payload bytes identical; meaning not verified"
    assert len(left.prompts) == len(right.prompts) == 1
    assert left.prompts[0] != right.prompts[0]
    for channel in (left, right):
        prompt = json.loads(channel.prompts[0])
        assert prompt["input"]["text"] == CLAUSE
        assert "model" not in prompt and "readers" not in prompt
        assert "Q-OPEN" not in channel.prompts[0]
    assert inputs == before
    assert isinstance(result.ledger_bytes, bytes) and isinstance(result.evidence_bytes, bytes)
    ledger["readers"][0]["obligations"].clear()
    assert len(result.ledger["readers"][0]["obligations"]) == 1
    assert len(result.evidence["calls"]) == 2
    assert [c["channel_identity"] for c in result.evidence["calls"]] == [
        llm.channel_identity(left), llm.channel_identity(right)]


def test_second_reader_never_sees_first_result_or_model_and_differences_stay_open():
    _, inputs = _fixture()
    first = Channel("claude-fixture", lambda value, prompt: {
        **value, "obligations": [{**value["obligations"][0], "statement": "FIRST_RESULT_SECRET"}]})
    result, _, right = run(inputs, left=first)
    assert "FIRST_RESULT_SECRET" not in right.prompts[0]
    assert result.ledger["comparisons"] == []
    comparison = result.evidence["comparison"]
    assert comparison["identical"] == []
    assert len(comparison["open"]["LEFT"]) == len(comparison["open"]["RIGHT"]) == 1


@pytest.mark.parametrize("empty_kind", ["all", "mixed"])
def test_explicit_non_normative_unit_is_reviewed_but_not_proof_of_completeness(empty_kind):
    _, inputs = _fixture()
    inputs["documents"][0]["clauses"].append({"anchor": "heading", "text": "Заголовок"})
    if empty_kind == "all":
        inputs["documents"][0]["clauses"] = inputs["documents"][0]["clauses"][1:]

    def response(value, prompt):
        if json.loads(prompt)["input"]["text"] == "Заголовок":
            return {"unit": value["unit"], "obligations": [], "none_found": True,
                    "reason": "Heading only; no obligation identified by this reader"}
        return value

    result, _, _ = run(inputs, Channel("claude-fixture", response), Channel("gpt-fixture", response))
    report = ob.evaluate(result.ledger, **inputs)
    assert report["form"] == "valid" and report["meaning"] == "not_proven"
    assert [r["total"] for r in report["readers"]] == ([0, 0] if empty_kind == "all" else [1, 1])
    assert all(not r["unreviewed_units"] for r in report["readers"])
    assert result.ledger["scope"]["excluded"] == []
    assert len([c for c in result.evidence["calls"] if c["none_found"]]) == 2


@pytest.mark.parametrize("case", ["unknown_unit", "missing_unit", "unknown_field", "duplicate_id",
    "duplicate_payload", "missing_source", "foreign_source", "wrong_quote",
    "bool_offset", "unknown_dependency", "empty_unexplained", "false_none", "empty_reason",
    "duplicate_json_key", "nonfinite", "prose_wrapper", "empty_reply"])
def test_bad_raw_reader_result_is_refused_not_an_empty_success(case):
    _, inputs = _fixture()

    def damage(value, prompt):
        item = value["obligations"][0]
        fragment = item["sources"][0]["fragments"][0]
        if case == "unknown_unit": value["unit"] = "document:foreign"
        elif case == "missing_unit": value.pop("unit")
        elif case == "unknown_field": value["approved"] = True
        elif case == "duplicate_id": value["obligations"].append(copy.deepcopy(item))
        elif case == "duplicate_payload": value["obligations"].append({**copy.deepcopy(item), "id": "O2"})
        elif case == "missing_source": item["sources"] = []
        elif case == "foreign_source": item["sources"][0]["unit"] = "document:foreign"
        elif case == "wrong_quote": fragment["quote"] += "!"
        elif case == "bool_offset": fragment["start"] = False
        elif case == "unknown_dependency": item["depends_on"] = ["absent"]
        elif case in ("empty_unexplained", "false_none", "empty_reason"):
            value["obligations"] = []
            if case != "empty_unexplained":
                value.update(none_found=(case != "false_none"), reason="" if case == "empty_reason" else "heading")
        elif case == "duplicate_json_key": return '{"unit":"x","unit":"y","obligations":[]}'
        elif case == "nonfinite": return json.dumps(value)[:-1] + ',"x":NaN}'
        elif case == "prose_wrapper": return "Here is JSON: " + json.dumps(value)
        elif case == "empty_reply": return ""
        return value

    with pytest.raises(core().ObligationReadError):
        run(inputs, left=Channel("claude-fixture", damage))


@pytest.mark.parametrize("kind", ["same_family", "unknown", "malformed"])
def test_identity_refusal_precedes_all_model_calls(kind):
    _, inputs = _fixture()
    left = Channel("claude-first")
    right = Channel("claude-second" if kind == "same_family" else "gpt-second")
    if kind == "unknown": right.identity = ""
    if kind == "malformed": right.identity = '{"model":"gpt-a","model":"claude-b"}'
    with pytest.raises(core().ObligationReadError, match="identity|family"):
        run(inputs, left, right)
    assert left.prompts == right.prompts == []


def test_input_change_during_read_cannot_produce_a_current_ledger():
    _, inputs = _fixture()
    def changed(value, prompt):
        inputs["documents"][0]["body"] += b"changed"
        return value
    with pytest.raises(core().ObligationReadError, match="input.*changed"):
        run(inputs, left=Channel("claude-first", changed))


def test_channel_identity_change_after_call_is_not_saved_as_the_old_channel():
    _, inputs = _fixture()
    left = Channel("claude-first")
    def changed(value, prompt):
        llm.stamp_identity(left, model="gpt-changed")
        return value
    left.transform = changed
    with pytest.raises(core().ObligationReadError, match="identity.*changed"):
        run(inputs, left=left)


def test_answer_prompt_uses_exact_selected_version_and_byte_identity():
    _, inputs = _answer_fixture()
    result, left, _ = run(inputs)
    answer = next(json.loads(p)["input"] for p in left.prompts if json.loads(p)["input"]["kind"] == "answer")
    assert answer["text"] == "Можно назначать только при действующем допуске"
    assert answer["source"] == inputs["answer_sources"][0]
    assert ob.evaluate(result.ledger, **inputs)["freshness"] == "current"
    damaged = copy.deepcopy(inputs)
    damaged["answer_sources"][0]["version"]["fingerprint"] = "f" * 12
    with pytest.raises(core().ObligationReadError):
        run(damaged)


def test_checkpoint_abort_and_channel_failure_never_return_a_candidate():
    _, inputs = _fixture()
    left, right = Channel("claude-first"), Channel("gpt-second")
    def stop():
        raise RuntimeError("caller checkpoint stopped")
    with pytest.raises(RuntimeError, match="checkpoint stopped"):
        run(inputs, left, right, checkpoint=stop)
    assert left.prompts == right.prompts == []
    def fail(value, prompt):
        raise TimeoutError("transport unavailable")
    with pytest.raises(core().ObligationReadError, match="transport unavailable"):
        run(inputs, left=Channel("claude-first", fail))


@pytest.mark.parametrize("model", ["8b", "namespace/", ":tag"])
def test_empty_family_cannot_claim_independent_identity(model):
    _, inputs = _fixture()
    left, right = Channel(model), Channel("gpt-second")
    with pytest.raises(core().ObligationReadError, match="identity"):
        run(inputs, left, right)
    assert left.prompts == right.prompts == []


@pytest.mark.parametrize("side", ["LEFT", "RIGHT"])
def test_caller_cancellation_raised_inside_channel_propagates_unchanged(side):
    _, inputs = _fixture()
    class CancelReading(RuntimeError):
        pass
    reason = CancelReading("caller cancelled")
    def cancel(value, prompt):
        raise reason
    left = Channel("claude-first", cancel if side == "LEFT" else None)
    right = Channel("gpt-second", cancel if side == "RIGHT" else None)
    with pytest.raises(CancelReading) as exc:
        run(inputs, left, right, cancellation_errors=(CancelReading,))
    assert exc.value is reason
    assert len(left.prompts) == 1
    assert len(right.prompts) == (side == "RIGHT")


def test_late_unit_policy_refusal_happens_before_either_reader_is_called():
    _, inputs = _fixture()
    inputs["documents"][0]["clauses"].append({"anchor": "second", "text": "Вторая клауза"})
    left, right = Channel("claude-first"), Channel("gpt-second")
    resolved = []

    def resolve(role, unit):
        resolved.append((role, unit["id"]))
        if len(resolved) == 4:
            raise PermissionError("late unit access policy refused")
        return left if role == "extractor" else right

    with pytest.raises(PermissionError, match="late unit access policy refused"):
        core().read_independently(inputs=inputs, resolve_channel=resolve)
    assert len(resolved) == 4
    assert left.prompts == right.prompts == []


def test_two_obligations_at_one_anchor_keep_conditions_exceptions_and_dependency():
    _, inputs = _fixture()

    def atomic_pair(value, prompt):
        first = value["obligations"][0]
        first["conditions"] = [{"id": "C1", "text": "Только для заданной базы"}]
        first["exceptions"] = [{"id": "E1", "text": "Кроме явно указанной аварийной ситуации"}]
        second = {**copy.deepcopy(first), "id": "O2", "statement": "Необходим действующий допуск",
                  "conditions": [], "exceptions": [], "depends_on": ["O1"]}
        value["obligations"].append(second)
        return value

    result, _, _ = run(inputs, Channel("qwen3.6:8b", atomic_pair), Channel("llama3:8b", atomic_pair))
    report = ob.evaluate(result.ledger, **inputs)
    assert report["form"] == "valid"
    assert [r["total"] for r in report["readers"]] == [2, 2]
    assert [r["linked"] for r in report["readers"]] == [0, 0]
    assert report["meaning"] == "not_proven"
    assert report["expert_review"]["status"] == "not_reviewed"
    assert len(result.ledger["comparisons"]) == 2
    for reader in result.ledger["readers"]:
        first, second = reader["obligations"]
        assert first["conditions"] == [{"id": "C1", "text": "Только для заданной базы"}]
        assert first["exceptions"] == [{"id": "E1", "text": "Кроме явно указанной аварийной ситуации"}]
        assert second["depends_on"] == [first["id"]]
        assert first["sources"] == second["sources"]
        assert len(reader["reviewed_units"]) == 1
        assert reader["result_sha256"] == ob.seal_reader(reader)["result_sha256"]
        assert json.loads(reader["execution"][0]["raw_reply"])["obligations"][1]["depends_on"] == ["O1"]
