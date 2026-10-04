"""K07: importer reply files must carry proof, not just accepted=True.

Actual Store/Journal/submit -> importer.run -> persisted reply -> CDPM readers.
Only transport is in memory. The local consumer catalogue deliberately has
not received the service's requests.json, so it cannot mask missing proof.
Synthetic temporary catalogue only; no HTTP/server, solver or verdict stub.
"""
import copy
import json
import shutil
from urllib.error import URLError

import pytest
import template_catalog as tc

from catalog_service import import_requests as importer, request_queue
from cdpm import catalog_requests as client
from template_catalog.receipts import validate_receipt
from test_k07_submit_integral_agent import (
    API_URL, JSON_TYPE, _document, _tree_bytes, submission,  # noqa: F401
)


@pytest.fixture()
def imported(tmp_path, submission):
    s = submission
    local_catalog = tmp_path / "client-catalog"
    shutil.copytree(s.folder, local_catalog)
    cat = tc.load(local_catalog)
    sent = copy.deepcopy(s.doc)
    rid = sent["request_id"]
    assert not any(r.get("request_id") == rid for r in cat.requests())
    run, outbox = tmp_path / "run", tmp_path / "outbox"
    row = client.index_row("synthetic-import-key", rid,
                           client.COMPOSED_SUBMITTED, None,
                           client.DEFAULT_DOCUMENT_CLASS)
    client.write_index(run, {"form": 1, "rows": {"R-IMPORT": row}})
    local_request = client.run_requests_dir(run) / f"{rid}.json"
    local_request.write_bytes(client._dump(sent))
    paths = client._outbox_paths(outbox, rid)
    outbox.mkdir()
    paths["live"].write_bytes(client._dump(sent))
    original = paths["live"].read_bytes()
    calls = []

    def post(url, headers, raw):
        assert url == API_URL + "/api/requests"
        assert headers["X-Catalog-Product"] == "CDPM"
        assert json.loads(raw) == sent
        body, status = request_queue.submit(s.store, s.journal, json.loads(raw),
                                            product="CDPM")
        calls.append(status)
        return status, body, JSON_TYPE

    result = importer.run(outbox, api_url=API_URL, token=None, reply=True, post=post)
    assert calls == [201]
    assert result["accepted"] == 1 and result["refused"] == 0
    reply = json.loads(paths["reply"].read_text(encoding="utf-8"))
    assert reply["accepted"] is True and reply["status"] == 201
    assert validate_receipt(reply["receipt"], sent=sent) == []
    assert client.nested_request_problems(reply["receipt"]) == []
    assert paths["live"].read_bytes() == original
    assert not client.receipt_path(run, rid).exists()
    return {"service": s, "run": run, "outbox": outbox, "paths": paths,
            "cat": cat, "sent": sent, "rid": rid, "reply": reply,
            "calls": calls, "post": post}


@pytest.mark.parametrize("case, delivered", [
    ("healthy", True), ("legacy-no-body", False), ("foreign-body", False),
])
def test_import_reply_delivery_requires_its_own_application_body(
        tmp_path, imported, case, delivered):
    s = imported
    reply = copy.deepcopy(s["reply"])
    if case == "legacy-no-body":
        reply.pop("receipt")
    elif case == "foreign-body":
        # The foreign receipt is genuinely produced and internally valid.
        # Only its association with this source request is wrong.
        service = s["service"]
        foreign = _document(service.store.catalog(), "probe_import_foreign")
        body, status = request_queue.submit(
            service.store, service.journal, foreign, product="CDPM")
        assert status == 201 and foreign["request_id"] != s["rid"]
        assert validate_receipt(body) == client.nested_request_problems(body) == []
        assert validate_receipt(body, sent=s["sent"])
        reply["receipt"] = body
    s["paths"]["reply"].write_bytes(client._dump(reply))
    # Historical layout after the file was collected/imported elsewhere.
    # Preparing it is explicit; states/quarantined must never move files.
    s["paths"]["sent"].parent.mkdir()
    s["paths"]["live"].replace(s["paths"]["sent"])
    before = _tree_bytes(tmp_path)
    first = client.states(s["run"], s["cat"], s["outbox"])
    second = client.states(s["run"], s["cat"], s["outbox"])
    assert first == second and len(first) == 1
    assert _tree_bytes(tmp_path) == before
    got = first[0]
    assert got["delivered"] is delivered, got
    assert got["transport_state"] == (
        client.TRANSPORT_DELIVERED if delivered else client.TRANSPORT_UNKNOWN)
    if not delivered:
        assert client.UNCONFIRMED_WORDS in got["state"], got
        assert client.quarantined(s["run"], s["outbox"]) == [s["rid"]]


def test_real_import_reply_lifts_sent_quarantine_without_rewriting_files(
        tmp_path, imported):
    s = imported
    # A repeat importer pass has no submission or catalogue/journal growth.
    before = _tree_bytes(tmp_path)
    again = importer.run(s["outbox"], api_url=API_URL, token=None,
                         reply=True, post=s["post"])
    assert again["sent"] == 0 and s["calls"] == [201]
    assert _tree_bytes(tmp_path) == before
    s["paths"]["sent"].parent.mkdir()
    s["paths"]["live"].replace(s["paths"]["sent"])
    before = _tree_bytes(tmp_path)
    first = client.quarantined(s["run"], s["outbox"])
    second = client.quarantined(s["run"], s["outbox"])
    assert first == second == [], "real receipt proof must not remain quarantined"
    assert client.states(s["run"], s["cat"], s["outbox"])[0]["delivered"] is True
    assert _tree_bytes(tmp_path) == before
    assert client.read_index(s["run"])["form"] == 1


@pytest.mark.parametrize("damage", [None, "accepted", "escaped-accepted", "nested-state", "nan", "overflow"])
def test_raw_import_reply_must_be_unambiguous_before_it_can_confirm_delivery(tmp_path, imported, damage):
    s = imported
    path = s["paths"]["reply"]
    text = json.dumps(s["reply"], ensure_ascii=False)
    if damage == "accepted":
        text = '{"accepted": false,' + text[1:]
    elif damage == "escaped-accepted":
        text = '{"acce\\u0070ted": false,' + text[1:]
    elif damage == "nested-state":
        assert '"receipt": {' in text
        text = text.replace('"receipt": {', '"receipt": {"state": "чужое",', 1)
    elif damage in {"nan", "overflow"}:
        text = text[:-1] + ',"diagnostic":' + ("NaN" if damage == "nan" else "1e9999") + "}"
    raw = text.encode("utf-8")
    path.write_bytes(raw)
    s["paths"]["sent"].parent.mkdir()
    s["paths"]["live"].replace(s["paths"]["sent"])
    before = _tree_bytes(tmp_path)
    got, = client.states(s["run"], s["cat"], s["outbox"])
    assert got["delivered"] is (damage is None), (damage, got)
    assert got["transport_state"] == (client.TRANSPORT_DELIVERED if damage is None
                                        else client.TRANSPORT_UNKNOWN), got
    assert client.quarantined(s["run"], s["outbox"]) == ([] if damage is None else [s["rid"]])
    assert path.read_bytes() == raw and _tree_bytes(tmp_path) == before
    if damage is not None:
        assert client._read_json(path) is None
        assert client.UNCONFIRMED_WORDS in got["state"]


@pytest.mark.parametrize("status", [200, 204])
def test_import_reply_status_must_agree_with_the_valid_body(tmp_path, imported, status):
    s = imported
    reply = copy.deepcopy(s["reply"])
    assert validate_receipt(reply["receipt"], sent=s["sent"]) == []
    assert reply["receipt"]["repeat"] is False and reply["receipt"]["note"] is None
    reply["status"] = status
    s["paths"]["reply"].write_bytes(client._dump(reply))
    s["paths"]["sent"].parent.mkdir()
    s["paths"]["live"].replace(s["paths"]["sent"])
    before = _tree_bytes(tmp_path)
    got = client.states(s["run"], s["cat"], s["outbox"])[0]
    assert not got["delivered"] and got["transport_state"] == client.TRANSPORT_UNKNOWN
    assert client.UNCONFIRMED_WORDS in got["state"]
    assert client.quarantined(s["run"], s["outbox"]) == [s["rid"]]
    assert _tree_bytes(tmp_path) == before


@pytest.mark.parametrize("own_form", ["unreadable", "invalid-body", "explicit-refusal"])
def test_own_channel_receipt_has_priority_over_older_import_success(
        tmp_path, imported, own_form):
    s = imported
    own = client._blank_receipt(s["rid"], API_URL)
    if own_form == "explicit-refusal":
        client._seal(own, client.KIND_CHANNEL, http=403,
                     words="synthetic later channel refusal",
                     refusal={"code": "machine-channel-off", "words": "channel off"})
    else:
        client._seal(own, client.KIND_DELIVERED, http=201, body={"state": "подана"})
    path = client.write_receipt(s["run"], own)
    if own_form == "unreadable":
        path.write_bytes(b"{broken historical JSON")
    s["paths"]["sent"].parent.mkdir()
    s["paths"]["live"].replace(s["paths"]["sent"])
    before = _tree_bytes(tmp_path)
    got = client.states(s["run"], s["cat"], s["outbox"])[0]
    assert not got["delivered"]
    assert got["transport_state"] == (client.TRANSPORT_REFUSED
        if own_form == "explicit-refusal" else client.TRANSPORT_UNKNOWN)
    if own_form == "explicit-refusal":
        assert got["delivery"]["refusal"] == "machine-channel-off"
    assert client.quarantined(s["run"], s["outbox"]) == [s["rid"]]
    assert _tree_bytes(tmp_path) == before


@pytest.mark.parametrize("operation", ["deliver", "recheck"])
def test_imported_delivery_survives_failed_later_check_as_stale(
        tmp_path, monkeypatch, imported, operation):
    s = imported
    monkeypatch.setenv(client.ENV_SERVICE_URL, API_URL)
    original = s["paths"]["live"].read_bytes()
    index = client.index_path(s["run"]).read_bytes()
    reply = s["paths"]["reply"].read_bytes()
    calls = []

    def unavailable(request, *, timeout):
        calls.append(request.get_method())
        raise URLError("synthetic offline transport timeout")

    if operation == "deliver":
        result = client.deliver_one(s["sent"], s["run"], s["outbox"],
                                    _urlopen=unavailable, _sleep=lambda _: None)
        assert calls == ["POST"] * len(client.DELIVERY_PAUSES_S)
    else:
        result = client.recheck_one(s["rid"], s["run"], s["outbox"], _urlopen=unavailable)
        assert calls == ["GET"]
    assert client.last_outcome(result) == client.OUTCOME_UNKNOWN
    assert client.receipt_confirmed(result, s["rid"], sent=s["sent"]) is not None
    assert result["receipt"] == s["reply"]["receipt"]
    got = client.states(s["run"], s["cat"], s["outbox"])[0]
    assert got["delivered"] and got["remote_state"] == "подана"
    assert got["stale"] and got["stale_reason"] == client.STALE_LAST_CHECK
    remaining = s["paths"]["sent"] if operation == "deliver" else s["paths"]["live"]
    assert remaining.read_bytes() == original
    assert s["paths"]["reply"].read_bytes() == reply
    assert client.index_path(s["run"]).read_bytes() == index


@pytest.mark.parametrize("has_proof", [True, False])
def test_withdraw_is_blocked_without_claiming_unproven_import_delivery(
        tmp_path, imported, has_proof):
    s = imported
    if not has_proof:
        reply = copy.deepcopy(s["reply"])
        reply.pop("receipt")
        s["paths"]["reply"].write_bytes(client._dump(reply))
    before = _tree_bytes(tmp_path)
    got = client.withdraw(s["rid"], s["run"], s["outbox"], s["cat"])
    assert got["ok"] is False, "uncertain delivery must not permit withdrawal"
    assert _tree_bytes(tmp_path) == before
    if has_proof:
        assert "уже принята" in got["words"]
    else:
        assert client.UNCONFIRMED_WORDS in got["words"] and "сверк" in got["words"]
        assert "уже принята" not in got["words"]
