"""K07: actual receipt producer -> both delivery consumers, without HTTP.

The only substituted boundary is transport.  The producer's catalog identity
is synthetic; receipt(), _known(), Journal, the common receipt validator,
CDPM deliver_one() and the importer's one() are real.  No service, catalog
release, submission/adoption transaction or live endpoint is exercised.

The negative expectations implement P0 v1.13 section 4.1 and the existing
CDPM K07C-F1/N1 law.  They protect the integrated media-type/nested-request
checks; they must not be skipped or inverted.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from catalog_service import import_requests as importer
from catalog_service import request_queue
from catalog_service.journal import Journal
from cdpm import catalog_requests as client
from template_catalog import requests as request_law
from template_catalog.receipts import validate_receipt


API_URL = "http://k07-receipt.invalid"
JSON_TYPE = "application/json; charset=utf-8"


def _request(kind):
    record_id = "k07_integral_receipt_probe"
    # Only receipt identity/canon is in scope: this is not a draft-schema test.
    draft = {"id": record_id, "title": "Synthetic receipt identity"}
    kinds = None
    if kind == "executor-kinds":
        draft, kinds = None, ["kotlin_constraint"]
        key = request_law.kind_request_key("families", record_id, kinds)
    else:
        key = request_law.manual_key(
            "families", record_id, request_law.draft_canon(draft))
    return {
        "request_id": request_law.request_id_of(key),
        "state": "подана", "section": "families", "record_id": record_id,
        "submitted_at": "2026-09-16T06:00:00+00:00", "submitted_by": "CDPM",
        "source": {"kind": "вручную", "document_class": None,
                   "run_class": None, "ref": None},
        "draft": draft, "executor_kinds_requested": kinds,
        "decision": {"by": None, "at": None, "words": None}, "cr_key": None,
    }


def _produced(tmp_path, sent, *, repeat=False):
    """Use real receipt construction and the real repeat/journal read path."""
    catalog = SimpleNamespace(version=lambda: "0.5.0",
                              fingerprint=lambda: "a" * 64)
    journal = Journal(tmp_path / "producer" / "actions.jsonl")
    journal_row = journal.append(
        actor="владелец", op="request-submit",
        target=f"{sent['section']}/{sent['record_id']}",
        words=f"заявка {sent['request_id']} подана")
    known = copy.deepcopy(sent)
    if repeat:
        known["state"] = "принята"
        known["decision"] = {
            "by": "владелец", "at": "2026-09-16T06:01:00+00:00",
            "words": "Synthetic accepted decision",
        }
        body = request_queue._known(catalog, journal, known, sent)
    else:
        body = request_queue.receipt(
            catalog, known, journal_at=journal_row["at"],
            journal_actor=journal_row["actor"],
            words=request_queue.accepted_words(sent["request_id"], "CDPM"))
    # This is an independent contract expectation, not a mocked judge.
    assert body["request"] == known
    assert body["repeat"] is repeat
    assert body["note"] == ("уже подана" if repeat else None)
    assert body["state"] == ("принята" if repeat else "подана")
    assert body["decision"]["version"] == (1 if repeat else 0)
    assert body["journal_ref"] == {
        "op": "request-submit", "at": journal_row["at"],
        "actor": journal_row["actor"],
    }
    assert len(journal.read()) == 1, "repeat must not create another admission"
    assert validate_receipt(body, sent=sent) == []
    return body


class _Response:
    """An in-memory transport response; no socket or ASGI application."""

    def __init__(self, status, body, content_type):
        self.status = status
        self.headers = ({} if content_type is None
                        else {"Content-Type": content_type})
        self.raw = json.dumps(body, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.raw


def _consume(tmp_path, monkeypatch, sent, body, *, status=201,
             content_type=JSON_TYPE):
    """The same producer body goes through each actual consumer entry point."""
    monkeypatch.setenv(client.ENV_SERVICE_URL, API_URL)
    monkeypatch.setenv(client.ENV_SERVICE_TOKEN, "synthetic-k07-token")
    outbox = tmp_path / "outbox"
    outbox.mkdir(parents=True)
    queued = outbox / f"{sent['request_id']}.json"
    queued.write_text(json.dumps(sent, ensure_ascii=False), encoding="utf-8")
    original = queued.read_bytes()
    calls = []

    def post(url, headers, raw):
        assert url == API_URL + "/api/requests"
        assert headers["X-Catalog-Product"] == "CDPM"
        assert json.loads(raw) == sent
        calls.append("importer")
        return status, copy.deepcopy(body), content_type

    def urlopen(request, *, timeout):
        assert request.full_url == API_URL + "/api/requests"
        assert request.get_method() == "POST"
        assert json.loads(request.data) == sent
        assert timeout > 0
        calls.append("cdpm")
        return _Response(status, body, content_type)

    def no_sleep(seconds):
        raise AssertionError(f"deterministic receipt must not retry: {seconds}")

    imported = importer.one(queued, api_url=API_URL, token=None, post=post)
    delivered = client.deliver_one(
        sent, tmp_path / "run", outbox, _urlopen=urlopen, _sleep=no_sleep)
    assert calls == ["importer", "cdpm"], "no live transport or hidden retry"
    persisted = client.read_receipt(tmp_path / "run", sent["request_id"])
    assert persisted["schema_version"] == "receipt-file/2"
    assert len(persisted["attempts"]) == 1
    assert persisted["attempts"][0]["http"] == status
    assert importer.receipt_problems(status, body, sent, content_type) == \
        client.receipt_problems(status, body, sent, content_type)
    return SimpleNamespace(imported=imported, delivered=delivered,
                           persisted=persisted, queued=queued,
                           original=original, outbox=outbox)


@pytest.mark.parametrize("kind", ["draft", "executor-kinds"])
@pytest.mark.parametrize("repeat", [False, True], ids=["first-201", "repeat-200"])
def test_actual_producer_receipt_is_accepted_by_both_consumers(
        tmp_path, monkeypatch, kind, repeat):
    sent = _request(kind)
    body = _produced(tmp_path, sent, repeat=repeat)
    if kind == "executor-kinds":
        assert body["canon"] is None, "an executor-kind request has no draft"
    result = _consume(tmp_path, monkeypatch, sent, body,
                      status=200 if repeat else 201)
    assert result.imported["accepted"] is True, result.imported["words"]
    assert result.imported["catalog_request_id"] == sent["request_id"]
    assert result.imported["receipt"] == body
    assert result.delivered["outcome"] == "доставлена"
    assert result.persisted["receipt"] == body
    assert result.persisted["queue"] == "sent"
    assert not result.queued.exists()
    sent_file = result.outbox / "sent" / result.queued.name
    assert sent_file.read_bytes() == result.original


@pytest.mark.parametrize("content_type", [None, "application/jsonp",
                                          "application/json-bogus"],
                         ids=["missing", "jsonp", "json-bogus"])
def test_both_consumers_reject_non_json_media_on_an_actual_receipt(
        tmp_path, monkeypatch, content_type):
    sent = _request("draft")
    body = _produced(tmp_path, sent)
    result = _consume(tmp_path, monkeypatch, sent, body,
                      content_type=content_type)
    assert result.delivered["outcome"] == "неизвестен"
    assert result.persisted["receipt"] is None
    assert result.queued.read_bytes() == result.original
    assert not (result.outbox / "sent" / result.queued.name).exists()
    assert result.imported["accepted"] is False, (
        "importer accepted a receipt without exact application/json: "
        f"{content_type!r}; {result.imported}")
    assert result.imported["receipt"] is None
    assert "повтор тем же номером" in result.imported["words"]


@pytest.mark.parametrize("fault", [
    "foreign-id", "other-draft", "other-state", "section", "record_id",
    "by", "at", "words", "version",
])
def test_both_consumers_reject_a_mixed_nested_request(
        tmp_path, monkeypatch, fault):
    sent = _request("draft")
    body = _produced(tmp_path, sent, repeat=True)
    if fault == "foreign-id":
        body["request"]["request_id"] = "req-" + "0" * 20
        assert body["request"]["request_id"] != sent["request_id"]
    elif fault == "other-draft":
        body["request"]["draft"]["title"] = "A different synthetic draft"
    elif fault == "other-state":
        body["request"]["state"] = "отклонена"
    elif fault in ("section", "record_id"):
        body["request"][fault] = "criteria" if fault == "section" else "other"
    else:
        body["request"]["decision"][fault] = 2 if fault == "version" else "other"
    # The outer receipt still has the exact sent identity and valid schema.
    # Only the relation to its nested carrier is damaged (K07C-F1).
    assert validate_receipt(body, sent=sent) == []
    if fault in ("by", "at", "words"):
        label = {"by": "автор решения", "at": "время решения",
                 "words": "слова решения"}[fault]
        problems = client.nested_request_problems(body)
        assert len(problems) == 1 and problems[0].startswith(label), problems
        assert repr(body["request"]["decision"][fault]) in problems[0]
        assert repr(body["decision"][fault]) in problems[0]
        assert importer.receipt_problems(200, body, sent, JSON_TYPE) == (
            client.receipt_problems(200, body, sent, JSON_TYPE))
    result = _consume(tmp_path, monkeypatch, sent, body, status=200)
    assert result.delivered["outcome"] == "неизвестен"
    assert result.persisted["receipt"] is None
    assert result.queued.read_bytes() == result.original
    assert not (result.outbox / "sent" / result.queued.name).exists()
    assert result.imported["accepted"] is False, (
        f"importer accepted contradicting nested request ({fault}): "
        f"{result.imported}")
    assert result.imported["receipt"] is None
    assert "повтор тем же номером" in result.imported["words"]


@pytest.mark.parametrize("content_type", ["application/json",
                                          " Application/JSON ; charset=UTF-8"])
def test_exact_json_media_keeps_valid_case_and_parameters(
        tmp_path, monkeypatch, content_type):
    sent = _request("draft")
    body = _produced(tmp_path, sent)
    result = _consume(tmp_path, monkeypatch, sent, body,
                      content_type=content_type)
    assert result.imported["accepted"] is True
    assert result.delivered["outcome"] == "доставлена"


@pytest.mark.parametrize("outer_version, inner_version, accepted", [
    (1, None, True),          # Catalog 0.5: absent version is not version zero.
    (1, "", True),
    (1, True, True),          # Booleans are not numeric versions in CDPM.
    (1, "1", True),
    (1, 2, False),
    (2**53, 2**53, True),
    (2**53, 2**53 + 1, False),
])
def test_nested_numeric_version_uses_the_existing_cdpm_law(
        tmp_path, outer_version, inner_version, accepted):
    sent = _request("draft")
    body = _produced(tmp_path, sent, repeat=True)
    body["decision"]["version"] = outer_version
    if inner_version is not None:
        body["request"]["decision"]["version"] = inner_version
    mine = importer.receipt_problems(200, body, sent, JSON_TYPE)
    cdpm = client.receipt_problems(200, body, sent, JSON_TYPE)
    assert mine == cdpm
    assert (not mine) is accepted


@pytest.mark.parametrize("nested", ["absent", "null", "normalized-decision"])
def test_optional_nested_carrier_follows_the_existing_cdpm_law(tmp_path, nested):
    sent = _request("draft")
    body = _produced(tmp_path, sent, repeat=True)
    if nested == "absent":
        body.pop("request")
    elif nested == "null":
        body["request"] = None
    else:
        for field in ("by", "at", "words"):
            body["request"]["decision"][field] = \
                " " + body["request"]["decision"][field] + " "
    mine = importer.receipt_problems(200, body, sent, JSON_TYPE)
    assert mine == client.receipt_problems(200, body, sent, JSON_TYPE)
    assert (not mine) is (nested != "null")
