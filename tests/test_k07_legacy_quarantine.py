"""P0 section 4.3: persisted sent metadata is not an application receipt.

Real file readers/quarantine, temporary form:1 index and outbox only. The
positive body comes from the actual receipt writer with an explicit synthetic
catalogue fixture. No service, HTTP, process, model, solver or verdict stub.
The two negative cases expose the disputed historical-reading boundary;
they do not silently rewrite the accepted compatibility contract.
"""
import copy
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest
import template_catalog as tc

from cdpm import catalog_requests as client
from template_catalog.receipts import validate_receipt
from test_k07_integral_receipt_agent import (
    JSON_TYPE, _Response, _produced, _request)


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


def _saved(tmp_path, form):
    sent = _request("draft")
    rid = sent["request_id"]
    run, outbox = tmp_path / "run", tmp_path / "outbox"
    row = client.index_row("synthetic-existing-index-key", rid,
                           client.COMPOSED_SUBMITTED, None,
                           client.DEFAULT_DOCUMENT_CLASS)
    client.write_index(run, {"form": 1, "rows": {row["key"]: row}})
    paths = client._outbox_paths(outbox, rid)
    paths["sent"].parent.mkdir(parents=True)
    paths["sent"].write_bytes(client._dump(sent))
    historical = {"request_id": rid, "delivered": True, "http": 201,
                  "state": "подана", "queue": "sent", "note": None,
                  "at": "2026-09-12T12:00:00+02:00"}
    if form != "legacy":
        body = (_produced(tmp_path, sent) if form == "modern-valid" else
                {"schema_version": "receipt/1", "request_id": rid,
                 "state": "подана", "note": None})
        assert (validate_receipt(body, sent=sent) == []) is (form == "modern-valid")
        historical.update(schema_version="receipt-file/2", outcome="доставлена",
                          receipt=body, attempts=[])
    client.write_receipt(run, historical)
    return SimpleNamespace(sent=sent, rid=rid, run=run, outbox=outbox,
                           historical=historical, paths=paths)


@pytest.mark.parametrize("form", ["legacy", "modern-incomplete", "modern-valid"])
def test_sent_without_application_receipt_stays_quarantined_on_repeated_reads(
        tmp_path, form):
    s = _saved(tmp_path, form)
    run, outbox, rid, historical = s.run, s.outbox, s.rid, s.historical
    before = _snapshot(tmp_path)
    first = client.quarantined(run, outbox)
    second = client.quarantined(run, outbox)
    assert _snapshot(tmp_path) == before, "read-only migration must not rewrite history/index"
    assert client.read_index(run)["form"] == 1
    assert first == second
    assert first == ([] if form == "modern-valid" else [rid]), (form, first)
    confirmed = client.receipt_confirmed(copy.deepcopy(historical), rid)
    assert (confirmed is not None) is (form == "modern-valid")


@pytest.mark.parametrize("form", ["legacy", "modern-incomplete", "modern-valid"])
def test_display_does_not_reuse_unproved_delivery_words(tmp_path, form):
    s = _saved(tmp_path, form)
    s.historical["state_words"] = "подана в службу каталога"
    client.write_receipt(s.run, s.historical)
    before = _snapshot(tmp_path)
    cat = tc.load(Path(__file__).resolve().parents[1] / "catalog")
    row = client.states(s.run, cat, s.outbox)[0]
    assert row["delivered"] is (form == "modern-valid")
    assert row["transport_state"] == (
        client.TRANSPORT_DELIVERED if form == "modern-valid"
        else client.TRANSPORT_UNKNOWN)
    if form != "modern-valid":
        assert client.UNCONFIRMED_WORDS in row["state"]
    assert _snapshot(tmp_path) == before


@pytest.mark.parametrize("form", ["legacy", "modern-incomplete", "modern-valid"])
@pytest.mark.parametrize("path", ["delivery-retention", "recheck"])
def test_timeout_keeps_only_schema_proven_historical_delivery(
        tmp_path, monkeypatch, form, path):
    s = _saved(tmp_path, form)
    original_index = client.index_path(s.run).read_bytes()
    original_sent = s.paths["sent"].read_bytes()
    if path == "delivery-retention":
        result = client._blank_receipt(s.rid, "http://k07.invalid", s.historical)
        client._seal(result, client.KIND_TIMEOUT, words="synthetic transport timeout")
        client._keep_prior_delivery(result, s.historical)
    else:
        monkeypatch.setenv(client.ENV_SERVICE_URL, "http://k07.invalid")
        calls = []

        def unavailable(request, *, timeout):
            assert request.get_method() == "GET"
            calls.append(request.full_url)
            raise URLError("synthetic transport timeout")

        result = client.recheck_one(s.rid, s.run, s.outbox, _urlopen=unavailable)
        again = client.recheck_one(s.rid, s.run, s.outbox, _urlopen=unavailable)
        assert len(calls) == 2, "recheck reads, never submits or retries a write"
        assert again["attempts"] == result["attempts"], "same check is idempotent"
    assert client.index_path(s.run).read_bytes() == original_index
    assert s.paths["sent"].read_bytes() == original_sent
    assert client.last_outcome(result) == client.OUTCOME_UNKNOWN
    assert (result["outcome"] == client.OUTCOME_DELIVERED) is (form == "modern-valid")
    if form == "modern-valid":
        assert result["receipt"] == s.historical["receipt"]
    else:
        assert result["receipt"] is None


@pytest.mark.parametrize("repeat", [False, True])
def test_read_only_recheck_removes_legacy_quarantine_only_with_real_receipt(
        tmp_path, monkeypatch, repeat):
    s = _saved(tmp_path, "legacy")
    body = _produced(tmp_path, s.sent, repeat=repeat)
    monkeypatch.setenv(client.ENV_SERVICE_URL, "http://k07.invalid")
    original_index = client.index_path(s.run).read_bytes()
    original_sent = s.paths["sent"].read_bytes()
    calls = []

    def get(request, *, timeout):
        assert request.get_method() == "GET"
        assert request.data is None
        calls.append(request.full_url)
        return _Response(200, body, JSON_TYPE)

    result = client.recheck_one(s.rid, s.run, s.outbox, _urlopen=get)
    again = client.recheck_one(s.rid, s.run, s.outbox, _urlopen=get)
    assert len(calls) == 2
    assert result["receipt"] == body
    assert again["attempts"] == result["attempts"]
    assert client.quarantined(s.run, s.outbox) == []
    proof = client.receipt_confirmed(again, s.rid, sent=s.sent)
    assert proof["state"] == ("принята" if repeat else "подана")
    assert proof["at"] == body["issued_at"]
    assert client.index_path(s.run).read_bytes() == original_index
    assert s.paths["sent"].read_bytes() == original_sent


@pytest.mark.parametrize("field", ["section", "record_id", "canon", "request_id"])
def test_stored_body_must_match_original_request_before_retention(
        tmp_path, field):
    s = _saved(tmp_path, "modern-valid")
    body = s.historical["receipt"]
    # Remove the optional nested carrier so identity, not its consistency,
    # rejects an otherwise schema-valid foreign body.
    body.pop("request")
    body[field] = {
        "section": "criteria", "record_id": "another_record",
        "canon": "b" * 64, "request_id": "req-" + "0" * 20,
    }[field]
    assert validate_receipt(body) == []
    assert validate_receipt(body, sent=s.sent)
    client.write_receipt(s.run, s.historical)
    assert client.quarantined(s.run, s.outbox) == [s.rid]
    result = client._blank_receipt(s.rid, None)
    client._seal(result, client.KIND_TIMEOUT, words="timeout")
    client._keep_prior_delivery(result, s.historical, sent=s.sent)
    assert result["outcome"] == client.OUTCOME_UNKNOWN
    assert result["receipt"] is None


@pytest.mark.parametrize("form", ["legacy", "modern-incomplete", "modern-valid"])
def test_retry_timeout_moves_live_file_only_for_previously_proven_delivery(
        tmp_path, monkeypatch, form):
    s = _saved(tmp_path, form)
    s.paths["sent"].replace(s.paths["live"])
    before = s.paths["live"].read_bytes()
    index = client.index_path(s.run).read_bytes()
    monkeypatch.setenv(client.ENV_SERVICE_URL, "http://k07.invalid")
    calls, pauses = [], []

    def unavailable(request, *, timeout):
        assert request.get_method() == "POST"
        assert request.data == before
        calls.append(request.full_url)
        raise URLError("synthetic transport timeout")

    result = client.deliver_one(
        s.sent, s.run, s.outbox, _urlopen=unavailable, _sleep=pauses.append)
    assert len(calls) == len(client.DELIVERY_PAUSES_S)
    assert client.last_outcome(result) == client.OUTCOME_UNKNOWN
    assert (result["outcome"] == client.OUTCOME_DELIVERED) is (form == "modern-valid")
    remaining = s.paths["sent"] if form == "modern-valid" else s.paths["live"]
    assert remaining.read_bytes() == before
    assert s.paths["live"].exists() is (form != "modern-valid")
    assert s.paths["sent"].exists() is (form == "modern-valid")
    assert client.index_path(s.run).read_bytes() == index


@pytest.mark.parametrize("reply_form", ["receipt", "card"])
@pytest.mark.parametrize("form", ["modern-valid", "legacy"])
def test_recheck_without_source_file_rejects_foreign_identity(
        tmp_path, monkeypatch, reply_form, form):
    s = _saved(tmp_path, form)
    s.paths["sent"].unlink()  # Only this test's temporary source copy is removed.
    foreign = _request("executor-kinds")
    assert foreign["request_id"] != s.rid
    body = (_produced(tmp_path / "foreign", foreign)
            if reply_form == "receipt" else foreign)
    monkeypatch.setenv(client.ENV_SERVICE_URL, "http://k07.invalid")
    calls = []

    def get(request, *, timeout):
        assert request.get_method() == "GET"
        assert request.full_url.endswith(s.rid)
        calls.append(request.full_url)
        return _Response(200, body, JSON_TYPE)

    result = client.recheck_one(s.rid, s.run, s.outbox, _urlopen=get)
    assert len(calls) == 1
    assert client.last_outcome(result) == client.OUTCOME_UNKNOWN
    assert not result.get("known"), "a foreign card is not knowledge of this key"
    if form == "modern-valid":
        assert result["receipt"] == s.historical["receipt"]
        assert client.receipt_confirmed(result, s.rid) is not None
    else:
        assert result["receipt"] is None
        assert client.receipt_confirmed(result, s.rid) is None


@pytest.mark.parametrize("path", ["recheck", "deliver"])
def test_timeout_preserves_delivery_but_marks_last_known_decision_stale(
        tmp_path, monkeypatch, path):
    s = _saved(tmp_path, "modern-valid")
    body = _produced(tmp_path / "decision", s.sent, repeat=True)
    s.historical["receipt"] = body
    client.write_receipt(s.run, s.historical)
    cat = tc.load(Path(__file__).resolve().parents[1] / "catalog")
    before = client.states(s.run, cat, s.outbox)[0]
    assert before["delivered"] and not before["stale"]
    assert before["stale_reason"] is None
    assert before["decision"]["words"] == body["decision"]["words"]
    original_index = client.index_path(s.run).read_bytes()
    original_sent = s.paths["sent"].read_bytes()
    monkeypatch.setenv(client.ENV_SERVICE_URL, "http://k07.invalid")
    calls, pauses = [], []

    def unavailable(request, *, timeout):
        assert request.get_method() == ("GET" if path == "recheck" else "POST")
        assert request.data == (None if path == "recheck" else original_sent)
        calls.append(request.full_url)
        raise URLError("synthetic transport timeout")

    if path == "recheck":
        result = client.recheck_one(s.rid, s.run, s.outbox, _urlopen=unavailable)
        assert len(calls) == 1 and pauses == []
    else:
        result = client.deliver_one(s.sent, s.run, s.outbox,
                                   _urlopen=unavailable, _sleep=pauses.append)
        assert len(calls) == len(client.DELIVERY_PAUSES_S)
        assert pauses == [p for p in client.DELIVERY_PAUSES_S if p]
    assert client.read_receipt(s.run, s.rid) == result
    assert client.last_outcome(result) == client.OUTCOME_UNKNOWN
    assert result["receipt"] == body
    after = client.states(s.run, cat, s.outbox)[0]
    assert after["delivered"]
    assert after["decision"] == before["decision"]
    assert after["stale"], "connection failure leaves a last-known, not fresh decision"
    assert after["stale_reason"] == client.STALE_LAST_CHECK
    assert client.quarantined(s.run, s.outbox) == []

    fresh = _produced(tmp_path / "recovery", s.sent, repeat=True)
    recovery_calls = []

    def recovered(request, *, timeout):
        assert request.get_method() == "GET" and request.data is None
        assert request.full_url.endswith(s.rid)
        recovery_calls.append(request.full_url)
        return _Response(200, fresh, JSON_TYPE)

    recovered_receipt = client.recheck_one(
        s.rid, s.run, s.outbox, _urlopen=recovered)
    assert len(recovery_calls) == 1
    assert client.read_receipt(s.run, s.rid) == recovered_receipt
    assert recovered_receipt["receipt"] == fresh
    assert client.last_outcome(recovered_receipt) == client.OUTCOME_DELIVERED
    current = client.states(s.run, cat, s.outbox)[0]
    assert current["delivered"] and current["stale"] is False
    assert current["stale_reason"] is None
    assert current["decision"] == before["decision"]
    assert client.index_path(s.run).read_bytes() == original_index
    assert s.paths["sent"].read_bytes() == original_sent
