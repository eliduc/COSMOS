"""Real Store/Journal/submit -> both consumers, with transport in memory.

No TestClient, application, endpoint, socket, child process or verdict stub.
The catalog is a temporary base-catalog copy, without candidate/executor
materialization or releases. Reloading Store/reader is not a process restart.
Token/header authorization before submit and resource/startup locks remain
outside this test; submit's role check, judge, writer and journal are real.
"""
from __future__ import annotations

import copy
import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

import template_catalog as tc
from catalog_service import import_requests as importer
from catalog_service import markers, request_queue
from catalog_service.errors import ServiceError
from catalog_service.journal import Journal
from catalog_service.settings import Settings
from catalog_service.store import Store
from cdpm import catalog_requests as client
from template_catalog import requests as law
from tests.catalog_copies import copy_catalog


API_URL = "http://k07-submit.invalid"
JSON_TYPE = "application/json; charset=utf-8"
CONSUMERS = pytest.mark.parametrize("consumer", ["importer", "cdpm"])


def _tree_bytes(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in folder.rglob("*") if p.is_file()
            and "__pycache__" not in p.parts}


def _document(cat, record_id="probe_k07_submit"):
    families = cat.families_full()
    sample = next((r for r in families
                   if isinstance(r.get("subject_words"), list)), families[0])
    draft = {k: copy.deepcopy(v) for k, v in sample.items()
             if k not in ("status", "history", "stand_tasks")}
    draft.update(id=record_id, title="Пробная семья заявки", params=[],
                 param_sets=[], conditions=[], complements=[],
                 subject_words=None, rule_form=None)
    key = law.manual_key("families", record_id, law.draft_canon(draft))
    return {
        "request_id": law.request_id_of(key), "state": "подана",
        "section": "families", "record_id": record_id,
        "submitted_at": "2026-09-16T06:00:00+00:00", "submitted_by": "CDPM",
        "source": {"kind": "вручную", "document_class": None,
                   "run_class": None, "ref": None},
        "draft": draft, "executor_kinds_requested": None,
        "decision": {"by": None, "at": None, "words": None}, "cr_key": None,
    }


@pytest.fixture()
def submission(tmp_path):
    # No parity tool, candidate materialization, binding builder or release.
    folder = copy_catalog(tmp_path, name="catalog-copy", requests=False,
                          releases=False, candidates=False, bindings=False,
                          version="0.2.0-draft.1")
    marker_file = tmp_path / "markers.json"
    marker_file.write_text(json.dumps({"checks": [
        {"name": "synthetic marker", "kind": "token", "values": ["ФРЕГЕНОЛЁТ"]}
    ]}, ensure_ascii=False), encoding="utf-8")
    settings = Settings(
        catalog_dir=folder, work_dir=tmp_path / "work", actor="owner",
        markers_file=marker_file, machine_token="synthetic-k07-token",
        cosmos_bindings=tmp_path / "unused-cosmos-bindings.json",
        cdpm_bindings=tmp_path / "unused-cdpm-bindings.json")
    assert markers.terms(settings), "the actual judge must have marker checks"
    tc.forget_current()
    store = Store(settings)
    journal = Journal(settings.journal_path, enabled=settings.is_edit())
    doc = _document(store.catalog())
    assert request_queue.get(store.catalog().requests(), doc["request_id"]) is None
    try:
        yield SimpleNamespace(store=store, journal=journal, settings=settings,
                              folder=folder, doc=doc)
    finally:
        store.release_catalog_lock()
        tc.forget_current()


class _Response:
    def __init__(self, status, body):
        self.status = status
        self.headers = {"Content-Type": JSON_TYPE}
        self.raw = json.dumps(body, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.raw


def _exchange(consumer, root, monkeypatch, store, journal, sent):
    """Only adapt transport; every status/body/error is produced by submit."""
    outbox = root / "outbox"
    outbox.mkdir(parents=True)
    queued = outbox / f"{sent['request_id']}.json"
    queued.write_text(json.dumps(sent, ensure_ascii=False), encoding="utf-8")
    original = queued.read_bytes()
    calls = []

    def submit(raw):
        doc = json.loads(raw)
        assert doc == sent
        try:
            body, status = request_queue.submit(store, journal, doc,
                                                product="CDPM")
            error = None
        except ServiceError as exc:
            body, status, error = {"detail": exc.detail()}, exc.status, exc
        calls.append(SimpleNamespace(body=body, status=status, error=error))
        return calls[-1]

    def post(url, headers, raw):
        assert url == API_URL + "/api/requests"
        assert headers["X-Catalog-Product"] == "CDPM"
        got = submit(raw)
        return got.status, got.body, JSON_TYPE

    def urlopen(request, *, timeout):
        assert request.full_url == API_URL + "/api/requests"
        assert request.get_method() == "POST" and timeout > 0
        got = submit(request.data)
        if got.error is not None:
            raise HTTPError(
                request.full_url, got.status, got.error.words,
                {"Content-Type": JSON_TYPE},
                io.BytesIO(json.dumps(got.body, ensure_ascii=False).encode("utf-8")))
        return _Response(got.status, got.body)

    def no_sleep(seconds):
        raise AssertionError(f"deterministic submit must not retry: {seconds}")

    if consumer == "importer":
        result = importer.one(queued, api_url=API_URL, token=None, post=post)
    else:
        monkeypatch.setenv(client.ENV_SERVICE_URL, API_URL)
        monkeypatch.setenv(client.ENV_SERVICE_TOKEN, "synthetic-k07-token")
        result = client.deliver_one(sent, root / "run", outbox,
                                    _urlopen=urlopen, _sleep=no_sleep)
        assert client.read_receipt(root / "run", sent["request_id"]) == result
    assert len(calls) == 1, "the real producer was called exactly once"
    return SimpleNamespace(consumer=consumer, result=result, service=calls[0],
                           queued=queued, original=original, outbox=outbox)


def _accepted(got, status, sent):
    assert got.service.error is None and got.service.status == status
    body = got.service.body
    assert body["request_id"] == sent["request_id"]
    assert body["repeat"] is (status == 200)
    assert body["note"] == ("уже подана" if status == 200 else None)
    assert body["state"] == "подана"
    assert got.result["receipt"] == body
    if got.consumer == "importer":
        assert got.result["accepted"] is True
        assert got.result["catalog_request_id"] == sent["request_id"]
        assert got.queued.read_bytes() == got.original
    else:
        assert got.result["outcome"] == "доставлена"
        assert got.result["queue"] == "sent" and not got.queued.exists()
        assert (got.outbox / "sent" / got.queued.name).read_bytes() == got.original


def _refused(got, status, code):
    assert isinstance(got.service.error, ServiceError)
    assert (got.service.status, got.service.error.code) == (status, code)
    assert got.result["receipt"] is None
    assert got.queued.read_bytes() == got.original
    assert not (got.outbox / "sent" / got.queued.name).exists()
    if got.consumer == "importer":
        assert got.result["accepted"] is False
        assert got.result["catalog_request_id"] is None
        assert code in got.result["words"]
    else:
        assert got.result["outcome"] == "отказ"
        assert got.result["refusal"] == code
        assert got.result["http"] == status
        assert got.result["queue"] == "outbox"


@CONSUMERS
def test_submit_writes_one_request_and_real_receipt_reaches_consumer(
        submission, tmp_path, monkeypatch, consumer):
    s = submission
    before = _tree_bytes(s.folder)
    old_count = len(s.store.catalog().requests())
    fingerprint = s.store.catalog().fingerprint()
    got = _exchange(consumer, tmp_path / "client", monkeypatch,
                    s.store, s.journal, s.doc)
    _accepted(got, 201, s.doc)
    after = _tree_bytes(s.folder)
    assert {name for name in before.keys() | after.keys()
            if before.get(name) != after.get(name)} == {"requests.json"}
    assert len(s.store.catalog().requests()) == old_count + 1
    assert request_queue.find(s.store.catalog().requests(), s.doc["request_id"]) \
        == got.service.body["request"]
    assert s.store.catalog().fingerprint() == fingerprint
    rows = s.journal.read()
    assert len(rows) == 1 and s.doc["request_id"] in rows[0]["words"]
    assert got.service.body["journal_ref"] == {
        "op": "request-submit", "at": rows[0]["at"], "actor": rows[0]["actor"]}


@CONSUMERS
def test_repeat_after_store_and_reader_reload_does_not_write_again(
        submission, tmp_path, monkeypatch, consumer):
    s = submission
    first = _exchange(consumer, tmp_path / "first", monkeypatch,
                      s.store, s.journal, s.doc)
    _accepted(first, 201, s.doc)
    before = _tree_bytes(s.folder)
    journal_bytes = s.settings.journal_path.read_bytes()
    s.store.forget()
    # Fresh objects and disk read, explicitly NOT a process restart.
    reloaded, journal = Store(s.settings), Journal(s.settings.journal_path)
    assert request_queue.find(reloaded.catalog().requests(), s.doc["request_id"])
    again = _exchange(consumer, tmp_path / "repeat", monkeypatch,
                      reloaded, journal, copy.deepcopy(s.doc))
    _accepted(again, 200, s.doc)
    assert again.service.body["journal_ref"] == first.service.body["journal_ref"]
    assert _tree_bytes(s.folder) == before
    assert s.settings.journal_path.read_bytes() == journal_bytes
    assert len(journal.read()) == 1


@CONSUMERS
def test_same_id_other_draft_is_real_409_and_no_storage_change(
        submission, tmp_path, monkeypatch, consumer):
    s = submission
    first = _exchange(consumer, tmp_path / "first", monkeypatch,
                      s.store, s.journal, s.doc)
    _accepted(first, 201, s.doc)
    before, journal_bytes = _tree_bytes(s.folder), s.settings.journal_path.read_bytes()
    changed = copy.deepcopy(s.doc)
    changed["draft"]["title"] = "Иной механизм"
    got = _exchange(consumer, tmp_path / "conflict", monkeypatch,
                    s.store, s.journal, changed)
    _refused(got, 409, "request-exists")
    assert got.service.error.extra["existing"] == s.doc["request_id"]
    if consumer == "cdpm":
        assert got.result["outcome_kind"] == "409 request-exists"
    assert _tree_bytes(s.folder) == before
    assert s.settings.journal_path.read_bytes() == journal_bytes


@CONSUMERS
def test_real_draft_judge_refuses_422_without_writing_request_or_journal(
        submission, tmp_path, monkeypatch, consumer):
    s = submission
    first = _exchange(consumer, tmp_path / "first", monkeypatch,
                      s.store, s.journal, s.doc)
    _accepted(first, 201, s.doc)
    bad = _document(s.store.catalog(), "probe_k07_invalid")
    assert bad["request_id"] != s.doc["request_id"]
    del bad["draft"]["title"]
    before, journal_bytes = _tree_bytes(s.folder), s.settings.journal_path.read_bytes()
    got = _exchange(consumer, tmp_path / "invalid", monkeypatch,
                    s.store, s.journal, bad)
    _refused(got, 422, "invalid-request")
    assert any(p.where.startswith(f"requests/{bad['request_id']}/draft")
               and "title" in p.message for p in got.service.error.problems)
    if consumer == "cdpm":
        assert got.result["outcome_kind"] == "422/400"
    assert _tree_bytes(s.folder) == before
    assert s.settings.journal_path.read_bytes() == journal_bytes
    assert request_queue.get(s.store.catalog().requests(), bad["request_id"]) is None
