"""A12 /2 address evidence at real handler and persisted-carrier boundaries.

Only synthetic sources and canned completion text. The existing reading-case
fixture retains real freeze/model/source resolution, channels, identity, call
journal, stage ownership and registered handlers. Its platform/passport metadata
seams remain disclosed in that fixture; no reader or carrier verdict is mocked.
These tests neither publish a candidate nor establish semantic/expert acceptance.
"""
import copy
import hashlib
import json

import pytest
from fastapi import HTTPException

from cdpm import obligations as ob
from test_a12_obligation_readers import core, run
from test_a12_obligation_reading_handlers import _protected, _read, _reading_case
from test_a12_quote_address_recovery_agent import QUOTE, UNICODE_TEXT, _case_inputs_channels
from test_cdpm_a12_obligations import _fixture


def _completion_fragments(case, monkeypatch, *, ambiguous):
    """Decorate only the fixture's fake transport response, not its caller."""
    from webapp import llm_backend
    original = llm_backend.complete
    replies = []

    def transport(prompt, **kwargs):
        response = original(prompt, **kwargs)
        value = json.loads(response["text"])
        unit = json.loads(prompt)["input"]
        length = len(unit["text"].encode("utf-8"))
        if ambiguous:
            # A12 has no minimum quote length: this exact fragment really
            # repeats in the existing synthetic clause. No source is changed
            # to manufacture ambiguity, and no semantic claim is made for it.
            assert unit["text"].count("а") > 1
        for item in value["obligations"]:
            item["sources"][0]["fragments"] = [{
                "start": 0 if ambiguous else 1,
                "end": 1 if ambiguous else length + 17,
                "quote": "а" if ambiguous else unit["text"],
            }]
        raw = "\r\n " + json.dumps(value, ensure_ascii=False, indent=2) + "\r\n"
        replies.append(raw)
        return {**response, "text": raw}

    monkeypatch.setattr(llm_backend, "complete", transport)
    return replies


def _assert_calls(calls, raw_replies):
    assert len(calls) == len(raw_replies)
    for call, raw in zip(calls, raw_replies):
        assert call["raw_reply"] == raw
        assert call["reply_sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
        assert call["prompt_sha256"] == hashlib.sha256(call["prompt"].encode("utf-8")).hexdigest()
        assert call["channel_identity"]


def _assert_released(case):
    status = case["webui"]._read_status(case["run"])
    assert status["state"] == "извлечено"
    assert "obligations_reading_token" not in status
    assert status["obligations_reading"]["saved"] is False


def test_registered_handler_exposes_recovered_address_without_saving(tmp_path, monkeypatch):
    case = _reading_case(tmp_path, monkeypatch)
    replies = _completion_fragments(case, monkeypatch, ambiguous=False)
    before = _protected(case["run"])
    result = _read(case)
    assert result["saved"] is False
    assert result["expected_sha256"] == hashlib.sha256(before[ob.LEDGER_REL]).hexdigest()
    report = result["report"]
    assert report["form"] == "valid" and report["freshness"] == "current", report
    assert report["meaning"] == "not_proven" and report["independence"] == "declared_only"
    assert report["expert_review"] == {"status": "not_reviewed"}
    candidate = json.loads(result["text"])
    assert candidate["links"] == [] and candidate["expert_review"] is None
    _assert_calls(result["evidence"]["calls"], replies)
    assert len(case["calls"]) == case["webui"].call_journal.count(case["run"]) == 2
    for reader in candidate["readers"]:
        call = next(c for c in result["evidence"]["calls"] if c["reader"] == reader["id"])
        assert reader["execution"] == [call]
        text = json.loads(call["prompt"])["input"]["text"]
        length = len(text.encode("utf-8"))
        for item in reader["obligations"]:
            assert item["sources"][0]["fragments"] == [{
                "start": 0, "end": length, "quote": text,
                "address": {"origin": "recovered", "method": "unique_exact_utf8",
                            "unit_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            "declared": {"start": 1, "end": length + 17},
                            "resolved": {"start": 0, "end": length}},
            }]
    assert _protected(case["run"]) == before
    _assert_released(case)


def test_handler_422_retains_both_ambiguous_replies_and_releases_stage(tmp_path, monkeypatch):
    case = _reading_case(tmp_path, monkeypatch)
    replies = _completion_fragments(case, monkeypatch, ambiguous=True)
    before = _protected(case["run"])
    with pytest.raises(HTTPException) as caught:
        _read(case)
    assert caught.value.status_code == 422
    detail = caught.value.detail
    assert isinstance(detail, dict) and "ambiguous" in detail["message"]
    evidence = detail["evidence"]
    assert evidence["candidate"] is False
    assert evidence["schema_version"] == "obligation-readings/2"
    assert evidence["max_address_clarifications_per_unit"] == 1
    calls = evidence["calls"]
    _assert_calls(calls, replies)
    assert len(case["calls"]) == len(calls) == 2  # No third call or second reader.
    assert [call["reader"] for call in calls] == ["LEFT", "LEFT"]
    assert [call["attempt"] for call in calls] == [1, 2]
    assert [call["status"] for call in calls] == ["ambiguous", "ambiguous"]
    retry = json.loads(calls[1]["prompt"])
    assert retry["mode"] == "address_clarification" and retry["previous_reply"] == replies[0]
    assert case["webui"].call_journal.count(case["run"]) == 2
    assert _protected(case["run"]) == before
    _assert_released(case)


@pytest.mark.parametrize("field,reason", [
    ("unit_text_sha256", "another input text"),
    ("origin", "unknown address origin"),
    ("method", "origin/method mismatch"),
    ("declared", "valid declared range was marked recovered"),
    ("resolved", "address evidence differs from fragment"),
])
def test_resealed_address_damage_is_invalid_not_automatically_repaired(field, reason):
    fragment = {"start": 3, "end": 16, "quote": QUOTE}
    inputs, left, right, _ = _case_inputs_channels(UNICODE_TEXT, fragment)
    result, _, _ = run(inputs, left, right)
    healthy = result.ledger
    assert ob.evaluate(healthy, **inputs)["form"] == "valid"
    damaged = copy.deepcopy(healthy)
    address = damaged["readers"][0]["obligations"][0]["sources"][0]["fragments"][0]["address"]
    if field == "unit_text_sha256":
        address[field] = "0" * 64
    elif field == "origin":
        address[field] = "guessed"
    elif field == "method":
        address[field] = "nearest_match"
    elif field == "declared":
        address[field] = {"start": 6, "end": 30}
    else:
        address[field] = {"start": 6, "end": 31}
    # Re-sealing removes the accidental-edit alarm from the causal path; the
    # real address-evidence validator must still reject the false declaration.
    damaged["readers"][0] = ob.seal_reader(damaged["readers"][0])
    before = copy.deepcopy(damaged)
    report = ob.evaluate(damaged, **inputs)
    assert report["form"] == "invalid", report
    assert any(reason in issue.get("detail", "") for issue in report["issues"]), report
    assert report["readers"] == [] and report["meaning"] == "not_proven"
    assert damaged == before and result.ledger == healthy


@pytest.mark.parametrize("reader_index", [0, 1])
@pytest.mark.parametrize("declared", [True, False])
def test_v2_reader_cannot_drop_fragment_address_after_resealing(reader_index, declared):
    fragment = {"start": 6 if declared else 3,
                "end": 30 if declared else 16, "quote": QUOTE}
    inputs, left, right, _ = _case_inputs_channels(UNICODE_TEXT, fragment)
    result, _, _ = run(inputs, left, right)
    healthy = result.ledger
    assert ob.evaluate(healthy, **inputs)["form"] == "valid"
    damaged = copy.deepcopy(healthy)
    reader = damaged["readers"][reader_index]
    assert reader["version"] == "obligation-readings/2"
    address = reader["obligations"][0]["sources"][0]["fragments"][0].pop("address")
    assert address["origin"] == ("declared" if declared else "recovered")
    damaged["readers"][reader_index] = ob.seal_reader(reader)
    before = copy.deepcopy(damaged)
    report = ob.evaluate(damaged, **inputs)
    assert report["form"] == "invalid", report
    assert any("address evidence required for obligation-readings/2" in issue.get("detail", "")
               for issue in report["issues"]), report
    assert report["readers"] == [] and report["meaning"] == "not_proven"
    assert damaged == before and result.ledger == healthy


def test_historical_v1_machine_reader_without_address_remains_valid():
    inputs, left, right, _ = _case_inputs_channels(
        UNICODE_TEXT, {"start": 6, "end": 30, "quote": QUOTE})
    result, _, _ = run(inputs, left, right)
    historical = copy.deepcopy(result.ledger)
    for index, reader in enumerate(historical["readers"]):
        reader["version"] = "obligation-readings/1"
        assert reader["method"] == "isolated exact-unit reading"
        for item in reader["obligations"]:
            for ref in item["sources"]:
                for fragment in ref["fragments"]:
                    fragment.pop("address")
        historical["readers"][index] = ob.seal_reader(reader)
    before = copy.deepcopy(historical)
    report = ob.evaluate(historical, **inputs)
    assert report["form"] == "valid" and report["freshness"] == "current", report
    assert historical == before


def test_historical_authored_ledger_without_address_stays_valid_and_unmodified():
    ledger, inputs = _fixture()
    before = copy.deepcopy(ledger)
    assert all("address" not in fragment for reader in ledger["readers"]
               for item in reader["obligations"] for ref in item["sources"]
               for fragment in ref["fragments"])
    report = ob.evaluate(ledger, **inputs)
    assert report["form"] == "valid" and report["freshness"] == "current", report
    assert report["meaning"] == "not_proven"
    assert ledger == before  # Reading is not a migration of historical claims.


def test_unpaired_surrogate_reply_returns_structured_error_without_candidate():
    inputs, left, right, _ = _case_inputs_channels(
        UNICODE_TEXT, {"start": 6, "end": 30, "quote": QUOTE})
    before = copy.deepcopy(inputs)
    left.transform = lambda value, prompt: "\ud800"
    with pytest.raises(core().ObligationReadError, match="not UTF-8") as caught:
        run(inputs, left, right)
    evidence = caught.value.evidence
    assert evidence["candidate"] is False
    assert len(evidence["calls"]) == len(left.prompts) == 1 and right.prompts == []
    call = evidence["calls"][0]
    assert call["status"] == "invalid"
    assert call["raw_reply"] is None and call["reply_sha256"] is None
    assert call["invalid_unicode_reply_repr"] == ascii("\ud800")
    assert caught.value.evidence_bytes.decode("utf-8")
    assert inputs == before
