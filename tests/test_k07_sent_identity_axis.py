"""K07 saved-file identity: internal consistency is not equality to sent.

Both complete requests pass the current request schema and reader rules.
The real receipt writer builds the nested carrier and its outer canon together.
Only the foreign case disagrees with the source request in the real sent file.
No service, HTTP, process, solver, release or verdict substitution is involved.
Seeded receipt-file/2 history tests migration reads, not service admission.
The same two cases can run on d67: the negative must fail on quarantine,
not on a new function signature, malformed schema or inconsistent nesting.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

import template_catalog as tc
from catalog_service import request_queue
from cdpm import catalog_requests as client
from template_catalog import requests as law
from template_catalog.receipts import validate_receipt
from template_catalog.validate import schema_messages, schema_path_for_version
from tests.test_k07_submit_integral_agent import _document


ROOT = Path(__file__).resolve().parents[1]
AT = "2026-09-16T06:00:00+00:00"
STATEMENT = "Ограничение длительности последовательности работ"


def _request_is_valid(cat, request):
    # Require real jsonschema, not the reader's unavailable-schema fallback.
    schema = json.loads(schema_path_for_version(cat.version()).read_text(
        encoding="utf-8"))
    schema["$ref"] = "#/$defs/request"
    jsonschema.Draft202012Validator.check_schema(schema)
    problems = schema_messages(request, schema, "request")
    assert problems == [], problems
    # This is the same request-addressed reader judgment used by _judge().
    problems = request_queue.catalog_with(cat, [request]).validate(
        forbidden_terms=("ФРЕГЕНОЛЁТ",))
    assert not [p for p in problems if p.code == "S06"], problems
    errors = [p for p in request_queue._mine(problems, request["request_id"])
              if p.severity == "error"]
    assert errors == [], errors


def _snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("foreign", [False, True], ids=[
    "healthy-same-draft", "same-id-consistent-foreign-draft"])
def test_saved_receipt_is_compared_to_the_actual_sent_file(tmp_path, foreign):
    cat = tc.load(ROOT / "catalog")  # Read-only source; never modified.
    sent = _document(cat, "k07_sent_identity_axis")
    # CDPM's key is about the source statement, not the generated draft/title.
    key = law.request_key(sent["section"], sent["draft"]["kind"],
                          sent["draft"]["zone"], STATEMENT)
    sent["request_id"] = rid = law.request_id_of(key)
    _request_is_valid(cat, sent)
    returned = copy.deepcopy(sent)
    if foreign:
        returned["draft"]["title"] = "Другой вариант пробной семьи"
        assert returned["draft"]["title"] != sent["draft"]["title"]
    _request_is_valid(cat, returned)
    assert law.request_id_of(law.request_key(
        returned["section"], returned["draft"]["kind"],
        returned["draft"]["zone"], STATEMENT)) == rid

    body = request_queue.receipt(
        cat, returned, journal_at=AT, journal_actor="владелец",
        words=request_queue.accepted_words(rid, "CDPM"))
    assert body["request_id"] == sent["request_id"]
    assert body["request"] == returned
    assert body["canon"] == law.draft_canon(returned["draft"])
    # Preconditions BEFORE the target: neither schema nor nesting may reject.
    assert validate_receipt(body) == []
    assert client.nested_request_problems(body) == []
    assert validate_receipt(body, sent=returned) == []
    identity_problems = validate_receipt(body, sent=sent)
    if foreign:
        assert body["canon"] != law.draft_canon(sent["draft"])
        assert len(identity_problems) == 1, identity_problems
        assert "канон черновика" in identity_problems[0], identity_problems
    else:
        assert identity_problems == [], identity_problems

    run, outbox = tmp_path / "run", tmp_path / "outbox"
    row = client.index_row(key, rid, client.COMPOSED_SUBMITTED, None,
                           client.DEFAULT_DOCUMENT_CLASS)
    client.write_index(run, {"form": 1, "rows": {row["key"]: row}})
    paths = client._outbox_paths(outbox, rid)
    paths["sent"].parent.mkdir(parents=True)
    paths["sent"].write_bytes(client._dump(sent))
    saved = client._blank_receipt(rid, "http://k07-saved.invalid/api/requests")
    client._seal(saved, client.KIND_DELIVERED, http=201, body=body, at=AT)
    saved["queue"] = "sent"
    client.write_receipt(run, saved)
    assert client.read_receipt(run, rid) == saved
    assert client._queue_record(run, rid, outbox) == sent
    # Both revisions accept this body without the source comparison. This
    # assertion prevents an unrelated malformed-body rejection from hiding it.
    assert client.receipt_confirmed(client.read_receipt(run, rid), rid) is not None

    before = _snapshot(tmp_path)
    expected = [rid] if foreign else []
    first = client.quarantined(run, outbox)
    assert client.quarantined(run, outbox) == first
    displayed = client.states(run, cat, outbox)[0]
    assert _snapshot(tmp_path) == before, "reads must not rewrite history or queue"
    assert first == expected, "only the sent comparison distinguishes these cases"
    assert displayed["delivered"] is (not foreign)
    assert displayed["transport_state"] == (
        client.TRANSPORT_UNKNOWN if foreign else client.TRANSPORT_DELIVERED)
    if foreign:
        assert client.UNCONFIRMED_WORDS in displayed["state"]
