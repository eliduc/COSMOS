"""Real draft.3 request transaction and both clients; transport only in memory.

No socket, service lifespan, release, owner decision or fabricated journal.
The registered handler writes only an isolated catalogue copy. Authentication,
request judgment, writer, journal, receipt construction and clients are real.
This is not HTTP middleware coverage or a live stand.
"""
import hashlib
import copy
import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
from starlette.requests import Request

import template_catalog as tc
from template_catalog import requests as law
from catalog_service.app import create_app
from catalog_service.settings import Settings
from catalog_service.errors import ServiceError
from catalog_service import import_requests as importer
from catalog_service import request_queue
from catalog_service.store import Store
from cdpm import catalog_requests as client
from test_k07_integral_receipt_agent import _Response
from test_k07_submit_integral_agent import _document

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog-integration" / "0.7.0-draft.3"
TOKEN = "isolated-local-k07-test-token"
URL = "http://native-k07.invalid"


def files(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


@pytest.fixture
def transaction(tmp_path, monkeypatch, request):
    original = files(CATALOG)
    copied = tmp_path / "catalog"
    shutil.copytree(CATALOG, copied)
    tc.forget_current()
    app = create_app(Settings(catalog_dir=copied, work_dir=tmp_path / "work",
                              actor="analyst", machine_token=TOKEN,
                              markers_file=tmp_path / "absent-test-markers"))
    matches = [r for r in app.routes if getattr(r, "path", None) == "/api/requests"
               and "POST" in (getattr(r, "methods", None) or set())]
    assert len(matches) == 1
    cat = app.state.store.catalog()
    kind = cat.dictionaries()["executor_kinds"][0]
    assert not any(r["record_id"] == "duty_limits" for r in cat.requests())
    key = law.kind_request_key("families", "duty_limits", [kind])
    sent = {"request_id": law.request_id_of(key), "state": "подана",
            "section": "families", "record_id": "duty_limits",
            "submitted_at": "2026-09-19T20:40:00+00:00", "submitted_by": "CDPM",
            "source": {"kind": "вручную", "document_class": None,
                       "run_class": None, "ref": None}, "draft": None,
            "executor_kinds_requested": [kind],
            "decision": {"by": None, "at": None, "words": None}, "cr_key": None}
    if getattr(request, "param", "executor-kinds") == "draft":
        sent = _document(cat, record_id="native_k07_draft_probe")
    calls = []

    def post(url, headers, raw):
        assert url == URL + "/api/requests"
        req = Request({"type": "http", "method": "POST", "path": "/api/requests",
                       "headers": [(k.lower().encode(), v.encode())
                                   for k, v in headers.items()], "app": app})
        try:
            response = matches[0].endpoint(json.loads(raw), req)
            status, body, media = response.status_code, json.loads(response.body), response.headers["content-type"]
        except ServiceError as exc:
            status, body, media = exc.status, {"detail": exc.detail()}, "application/json"
        calls.append((status, body))
        return status, body, media

    def urlopen(req, *, timeout):
        assert req.get_method() == "POST" and timeout > 0
        status, body, media = post(req.full_url, dict(req.header_items()), req.data)
        if status >= 400:
            raise HTTPError(req.full_url, status, "local handler refusal",
                            {"Content-Type": media}, io.BytesIO(json.dumps(body).encode()))
        return _Response(status, body, media)

    monkeypatch.setenv(client.ENV_SERVICE_URL, URL)
    monkeypatch.setenv(client.ENV_SERVICE_TOKEN, TOKEN)
    before = files(copied)
    yield SimpleNamespace(app=app, sent=sent, post=post, urlopen=urlopen,
                          calls=calls, copied=copied, before=before,
                          identity=(cat.version(), cat.fingerprint()))
    tc.forget_current()
    assert files(CATALOG) == original


@pytest.mark.parametrize("first", ["importer", "cdpm"])
@pytest.mark.parametrize("transaction", ["executor-kinds", "draft"], indirect=True)
def test_actual_submission_and_cross_client_repeat(transaction, tmp_path, first):
    t = transaction
    results = []
    for i, which in enumerate([first, "cdpm" if first == "importer" else "importer"]):
        outbox = tmp_path / f"outbox-{i}"
        outbox.mkdir()
        queued = outbox / (t.sent["request_id"] + ".json")
        queued.write_text(json.dumps(t.sent, ensure_ascii=False), encoding="utf-8")
        raw = queued.read_bytes()
        if which == "importer":
            result = importer.one(queued, api_url=URL, token=TOKEN, post=t.post)
            assert result["accepted"] is True, result
            receipt = result["receipt"]
            assert queued.read_bytes() == raw
        else:
            def no_retry(seconds):
                pytest.fail(f"Unexpected retry: {seconds}")
            result = client.deliver_one(t.sent, tmp_path / f"run-{i}", outbox,
                                        _urlopen=t.urlopen, _sleep=no_retry)
            assert result["outcome"] == "доставлена", result
            persisted = client.read_receipt(tmp_path / f"run-{i}", t.sent["request_id"])
            receipt = persisted["receipt"]
            assert persisted["queue"] == "sent"
            assert (outbox / "sent" / queued.name).read_bytes() == raw
            assert not queued.exists()
        assert receipt["repeat"] is bool(i)
        assert receipt["decision"] == {"by": None, "at": None, "words": None, "version": 0}
        assert receipt["state"] == "подана"
        assert receipt["canon"] == law.draft_canon(t.sent["draft"])
        assert receipt["catalog"] == dict(zip(("version", "fingerprint"), t.identity))
        results.append(receipt)
    assert [s for s, _ in t.calls] == [201, 200]
    assert results[0]["journal_ref"] == results[1]["journal_ref"]
    rows = t.app.state.journal.read()
    assert len(rows) == 1 and rows[0]["op"] == "request-submit"
    assert rows[0]["actor"] == "аналитик"
    cat = t.app.state.store.catalog()
    assert len([r for r in cat.requests() if r["request_id"] == t.sent["request_id"]]) == 1
    after = files(t.copied)
    assert {p for p in set(after) | set(t.before) if after.get(p) != t.before.get(p)} == {"requests.json"}


@pytest.mark.parametrize("fault", ["token", "id"])
def test_bad_submission_is_not_written(transaction, fault):
    t = transaction
    body = dict(t.sent)
    token = TOKEN
    if fault == "token":
        token = "incorrect-local-token"
    else:
        body["request_id"] = "req-" + "0" * 20
    status, result, _ = t.post(URL + "/api/requests",
                              {"X-Catalog-Product": "CDPM", "X-Catalog-Token": token},
                              json.dumps(body).encode())
    assert status == (401 if fault == "token" else 422), result
    assert result["detail"]["code"] == ("bad-token" if fault == "token" else "bad-request-id")
    assert files(t.copied) == t.before
    assert t.app.state.journal.read() == []


@pytest.mark.parametrize("lose_all", [False, True], ids=["retry-in-call", "resume-later"])
@pytest.mark.parametrize("transaction", ["executor-kinds", "draft"], indirect=True)
def test_lost_response_after_real_write_reuses_one_request(transaction, tmp_path, lose_all):
    t = transaction
    outbox, run = tmp_path / "outbox", tmp_path / "run"
    outbox.mkdir()
    queued = outbox / (t.sent["request_id"] + ".json")
    queued.write_text(json.dumps(t.sent, ensure_ascii=False), encoding="utf-8")
    original = queued.read_bytes()
    bodies, pauses = [], []

    def lose_response(req, *, timeout):
        bodies.append(req.data)
        response = t.urlopen(req, timeout=timeout)  # real commit precedes loss
        if lose_all or len(bodies) == 1:
            raise URLError("simulated response loss after persistence")
        return response

    first = client.deliver_one(t.sent, run, outbox, _urlopen=lose_response,
                               _sleep=pauses.append)
    assert len(t.app.state.journal.read()) == 1
    assert len(set(bodies)) == 1
    assert t.calls[0][0] == 201 and all(s == 200 for s, _ in t.calls[1:])
    if lose_all:
        assert first["outcome"] != "доставлена"
        assert first["receipt"] is None and first["queue"] == "outbox"
        assert queued.read_bytes() == original
        assert len(bodies) == len(client.DELIVERY_PAUSES_S)
        tc.forget_current()  # subsequent delivery cannot rely on reader cache
        final = client.deliver_one(t.sent, run, outbox, _urlopen=t.urlopen,
                                   _sleep=pauses.append)
        assert len(final["attempts"]) == len(first["attempts"]) + 1
    else:
        final = first
        assert len(bodies) == 2
    assert final["outcome"] == "доставлена"
    assert final["receipt"]["repeat"] is True
    assert final["receipt"]["decision"]["version"] == 0
    assert final["queue"] == "sent" and not queued.exists()
    assert (outbox / "sent" / queued.name).read_bytes() == original
    assert len(t.app.state.journal.read()) == 1
    assert TOKEN not in json.dumps(final, ensure_ascii=False)
    assert TOKEN not in t.app.state.settings.journal_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("fault,status,kind", [
    ("server", 500, client.KIND_5XX),
    ("token-echo", 401, client.KIND_CHANNEL),
    ("closed", 409, client.KIND_CLOSED),
])
def test_delivery_distinguishes_refusals_and_scrubs_persisted_echo(transaction, tmp_path, fault, status, kind):
    t = transaction
    if fault == "closed":
        assert t.post(URL + "/api/requests",
                      {"X-Catalog-Product": "CDPM", "X-Catalog-Token": TOKEN},
                      json.dumps(t.sent).encode())[0] == 201
        # Explicit simulated owner action solely in this temporary catalogue;
        # no real owner's decision or catalogue adoption is claimed.
        owner = Store(t.app.state.settings.with_(actor="owner"))
        request_queue.decide(owner, t.app.state.journal, t.sent["request_id"],
                             {"decision": "отклонена", "words": "Пробная механика требует пересмотра"})
    outbox, run = tmp_path / "fault-outbox", tmp_path / "fault-run"
    outbox.mkdir()
    queued = outbox / (t.sent["request_id"] + ".json")
    queued.write_text(json.dumps(t.sent, ensure_ascii=False), encoding="utf-8")
    original = queued.read_bytes()
    before = files(t.copied)
    rows = t.app.state.journal.read()
    calls = []

    def transport(req, *, timeout):
        calls.append(req.data)
        if fault == "closed":
            return t.urlopen(req, timeout=timeout)
        # Explicit transport error fixtures; not a claim that the service
        # emitted a server failure or exposed this local synthetic token.
        detail = {"code": "bad-token" if status == 401 else "fixture-server-error",
                  "words": "Test failure echo " + TOKEN,
                  "problems": [{"message": "Nested " + TOKEN}],
                  "extra": {"fixture": [TOKEN]}}
        raise HTTPError(req.full_url, status, "test transport fault",
                        {"Content-Type": "application/json"},
                        io.BytesIO(json.dumps({"detail": detail}).encode()))

    def no_retry(seconds):
        pytest.fail(f"This deterministic refusal must not retry: {seconds}")

    result = client.deliver_one(t.sent, run, outbox, _urlopen=transport, _sleep=no_retry)
    assert len(calls) == 1
    assert result["http"] == status and result["outcome_kind"] == kind
    assert result["outcome"] == (client.OUTCOME_UNKNOWN if status == 500 else client.OUTCOME_REFUSED)
    assert result["receipt"] is None and result["queue"] == "outbox"
    assert queued.read_bytes() == original and not (outbox / "sent").exists()
    assert files(t.copied) == before and t.app.state.journal.read() == rows
    persisted = client.read_receipt(run, t.sent["request_id"])
    assert TOKEN not in client.receipt_path(run, t.sent["request_id"]).read_text(encoding="utf-8")
    assert len(persisted["attempts"]) == 1
    assert set(persisted["attempts"][0]) == {"at", "http", "outcome", "words"}
    if fault == "closed":
        assert result["extra"]["state"] == "отклонена"
        assert result["extra"]["decision"]["words"] == "Пробная механика требует пересмотра"


def test_both_clients_share_schema_and_judge_native_receipt_matrix(transaction):
    from template_catalog import receipts
    t = transaction
    _, good, media = t.post(URL + "/api/requests",
                           {"X-Catalog-Product": "CDPM", "X-Catalog-Token": TOKEN},
                           json.dumps(t.sent).encode())
    repeated = copy.deepcopy(good)
    repeated.update(repeat=True, note="уже подана")
    missing_journal = {k: v for k, v in good.items() if k != "journal_ref"}
    cases = [(201, good, media, True), (200, repeated, media, True),
             (200, good, media, False), (201, repeated, media, False),
             (201, good, "text/html", False), (201, missing_journal, media, False),
             (204, None, media, False), (202, {"note": "принято"}, media, False),
             (500, {"detail": {"code": "fixture", "words": "failure"}}, media, False),
             (201, "<html>login</html>", "text/html", False)]
    for field, value in [("request_id", "req-" + "7" * 20),
                         ("state", "unknown"), ("schema_version", "receipt/2"),
                         ("canon", "0" * 64), ("section", "criteria"),
                         ("record_id", "another_record")]:
        cases.append((201, dict(good, **{field: value}), media, False))
    for status, body, media, accepted in cases:
        mine = client.receipt_problems(status, body, t.sent, media)
        theirs = importer.receipt_problems(status, body, t.sent, media)
        assert mine == theirs and (not mine) is accepted, (status, body, mine, theirs)
    assert len(cases) == 16
    assert client.validate_receipt is importer.validate_receipt is receipts.validate_receipt
    assert (ROOT / "catalog_service/schemas/receipt.schema.json").read_bytes() == \
        (ROOT / "template_catalog/schemas/receipt.schema.json").read_bytes()
