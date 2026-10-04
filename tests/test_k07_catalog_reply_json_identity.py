"""K07 raw JSON identity at all catalogue-client response readers.

Store/Journal/importer are real and temporary. The transport seam returns
bytes in memory; no HTTP, application, server or synthetic judge is used.
Session/refusal shapes are explicit protocol controls, not live-service proof.
"""
import io
import json
from urllib.error import HTTPError

import pytest

from catalog_service import request_queue
from cdpm import catalog_requests as client
from test_k07_import_reply_proof_agent import imported  # noqa: F401
from test_k07_submit_integral_agent import (
    API_URL, JSON_TYPE, _tree_bytes, submission,  # noqa: F401
)


class RawResponse(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Type": JSON_TYPE}


def _raw(body, damage=None):
    text = json.dumps(body, ensure_ascii=False)
    if damage == "request-id":
        text = '{"request_id":"foreign",' + text[1:]
    elif damage == "nested-state":
        assert '"request": {' in text
        text = text.replace('"request": {', '"request": {"state":"foreign",', 1)
    elif damage == "overflow":
        text = text[:-1] + ',"diagnostic":1e9999}'
    return text.encode("utf-8")


@pytest.mark.parametrize("damage", [None, "outcome", "nested-id", "infinity"])
def test_stored_own_receipt_cannot_choose_a_duplicate_over_older_import_proof(tmp_path, imported, damage):
    s = imported
    own = client._blank_receipt(s["rid"], API_URL)
    client._seal(own, client.KIND_DELIVERED, http=201, body=s["reply"]["receipt"])
    path = client.write_receipt(s["run"], own)
    text = json.dumps(own, ensure_ascii=False)
    if damage == "outcome":
        text = '{"outcome":"неизвестен",' + text[1:]
    elif damage == "nested-id":
        assert '"receipt": {' in text
        text = text.replace('"receipt": {', '"receipt": {"request_id":"foreign",', 1)
    elif damage == "infinity":
        text = text[:-1] + ',"diagnostic":Infinity}'
    raw = text.encode("utf-8")
    path.write_bytes(raw)
    before = _tree_bytes(tmp_path)
    read = client.read_receipt(s["run"], s["rid"])
    got, = client.states(s["run"], s["cat"], s["outbox"])
    assert got["delivered"] is (damage is None), (damage, got)
    assert (read is None) is (damage is not None)
    assert path.read_bytes() == raw and _tree_bytes(tmp_path) == before


@pytest.mark.parametrize("operation", ["deliver", "recheck"])
@pytest.mark.parametrize("damage", [None, "request-id", "nested-state", "overflow"])
def test_service_receipt_bytes_are_strict_before_real_confirmation(tmp_path, monkeypatch, imported, operation, damage):
    s = imported
    monkeypatch.setenv(client.ENV_SERVICE_URL, API_URL)
    # Remove the earlier import proof so it cannot legitimately preserve an
    # already-proven delivery and conceal which bytes this call accepted.
    s["paths"]["reply"].unlink()
    original = s["paths"]["live"].read_bytes()
    index = client.index_path(s["run"]).read_bytes()
    service_before = _tree_bytes(s["service"].folder)
    journal_before = s["service"].settings.journal_path.read_bytes()
    calls, payloads = [], []

    def transport(request, *, timeout):
        calls.append(request.get_method())
        assert timeout > 0
        if operation == "deliver":
            assert request.full_url == API_URL + client.SERVICE_REQUESTS_PATH
            assert json.loads(request.data) == s["sent"]
            body, status = request_queue.submit(s["service"].store, s["service"].journal,
                                                json.loads(request.data), product="CDPM")
            assert status == 200 and body["repeat"] is True
        else:
            assert request.full_url == API_URL + client.SERVICE_REQUEST_PATH + s["rid"]
            # Exact application body previously produced by real submit;
            # a GET seam does not perform another write/submit operation.
            body, status = s["reply"]["receipt"], 200
        raw = _raw(body, damage)
        payloads.append(raw)
        return RawResponse(raw, status)

    def no_sleep(_):
        pytest.fail("Deterministic malformed JSON must not trigger retry")

    if operation == "deliver":
        result = client.deliver_one(s["sent"], s["run"], s["outbox"],
                                    _urlopen=transport, _sleep=no_sleep)
    else:
        result = client.recheck_one(s["rid"], s["run"], s["outbox"], _urlopen=transport)
    proof = client.receipt_confirmed(result, s["rid"], sent=s["sent"])
    assert (proof is not None) is (damage is None), (operation, damage, result)
    assert result["outcome"] == (client.OUTCOME_DELIVERED if damage is None else client.OUTCOME_UNKNOWN)
    assert calls == (["POST"] if operation == "deliver" else ["GET"])
    assert len(payloads) == 1
    if damage is not None:
        assert result["receipt"] is None and result["known"] is None
        assert s["paths"]["live"].read_bytes() == original
        assert not s["paths"]["sent"].exists()
    elif operation == "deliver":
        assert not s["paths"]["live"].exists()
        assert s["paths"]["sent"].read_bytes() == original
    else:
        assert s["paths"]["live"].read_bytes() == original
    assert client.read_receipt(s["run"], s["rid"]) == result
    assert client.index_path(s["run"]).read_bytes() == index
    assert _tree_bytes(s["service"].folder) == service_before
    assert s["service"].settings.journal_path.read_bytes() == journal_before


@pytest.mark.parametrize("duplicate", [False, True])
def test_dry_run_fallback_does_not_choose_one_of_two_known_values(tmp_path, monkeypatch, imported, duplicate):
    s = imported
    monkeypatch.setenv(client.ENV_SERVICE_URL, API_URL)
    s["paths"]["reply"].unlink()
    before = _tree_bytes(s["service"].folder)
    journal_before = s["service"].settings.journal_path.read_bytes()
    original = s["paths"]["live"].read_bytes()
    calls = []

    def transport(request, *, timeout):
        calls.append(request.get_method())
        if request.get_method() == "GET":
            raise HTTPError(request.full_url, 404, "No legacy card endpoint",
                            {"Content-Type": JSON_TYPE}, io.BytesIO(b'{"detail":"Not Found"}'))
        assert request.full_url == API_URL + client.SERVICE_DRY_RUN_PATH
        assert json.loads(request.data) == s["sent"]
        body = request_queue.dry_run(s["service"].store, json.loads(request.data), product="CDPM")
        assert body["known"]["request_id"] == s["rid"]
        text = json.dumps(body, ensure_ascii=False)
        if duplicate:
            text = '{"known":null,' + text[1:]
        return RawResponse(text.encode("utf-8"))

    result = client.recheck_one(s["rid"], s["run"], s["outbox"], _urlopen=transport)
    assert calls == ["GET", "POST"]
    assert client.receipt_confirmed(result, s["rid"], sent=s["sent"]) is None
    assert result["outcome"] == client.OUTCOME_UNKNOWN
    if duplicate:
        assert result["known"] is None, result
    else:
        assert result["known"]["state"] == "подана", result
    assert s["paths"]["live"].read_bytes() == original
    assert _tree_bytes(s["service"].folder) == before
    assert s["service"].settings.journal_path.read_bytes() == journal_before


@pytest.mark.parametrize("duplicate", [False, True])
def test_session_precheck_cannot_report_checked_from_ambiguous_fingerprint(monkeypatch, duplicate):
    monkeypatch.setenv(client.ENV_SERVICE_URL, API_URL)
    monkeypatch.setenv(client.ENV_SERVICE_TOKEN, "synthetic-test-token")
    fingerprint = "a" * 64
    raw = ('{"catalog":{"fingerprint":"' + fingerprint + '"}}').encode()
    if duplicate:
        raw = raw.replace(b'{"fingerprint":', b'{"fingerprint":"foreign","fingerprint":')
    calls = []

    def transport(request, *, timeout):
        calls.append(request.full_url)
        return RawResponse(raw)

    result = client.session_precheck(_urlopen=transport, _stamp={"fingerprint": fingerprint})
    assert calls == [API_URL + client.SERVICE_SESSION_PATH]
    # Preserve the existing skip-with-words policy, not a new availability gate.
    assert result["ok"] is True
    assert result["checked"] is (not duplicate), result
    assert result["service"] == (None if duplicate else fingerprint)
    if duplicate:
        assert "пропущена" in result["words"]


@pytest.mark.parametrize("duplicate", [False, True])
def test_refusal_does_not_turn_ambiguous_machine_code_into_selected_outcome(duplicate):
    detail = {"code": "request-exists", "words": 'Words NaN and "state" remain text',
              "problems": [{"where": "draft", "message": "explicit problem"}],
              "extra": {"existing": "synthetic-existing"}}
    text = json.dumps({"detail": detail}, ensure_ascii=False)
    if duplicate:
        text = text.replace('"detail": {', '"detail": {"code":"write-busy",', 1)
    result = client.parse_refusal(text, 409)
    if duplicate:
        assert result["code"] is None and result["problems"] == [] and result["extra"] == {}, result
        assert result["words"] == text[:400]
    else:
        assert result == detail
