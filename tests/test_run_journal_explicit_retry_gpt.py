"""Offline causal coverage of the explicit UI journal-retry budget.

Only urlopen is replaced at the transport boundary. The actual route body
is compiled from app.py without importing the application's startup code;
authentication and current-package discovery are isolated collaborators.
No socket, process, service, LLM or real waiting is permitted. Run with
--noconftest so unrelated solver probes are not collected or imported.
"""
from __future__ import annotations

import ast
import copy
import io
import json
import socket
import subprocess
import sys
import types
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
API = "http://owner.invalid/api/runs/run-test/record"
PACKAGE_SHA = "a" * 64
DECLARATION_SHA = "b" * 64


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Network/process access forbidden in this test")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "isolated-data"))
    monkeypatch.delenv("CREW_CDPM_RECORD_API", raising=False)
    monkeypatch.delenv("CREW_CONTOUR", raising=False)
    monkeypatch.syspath_prepend(str(ROOT))
    from webapp import run_journal

    assert Path(run_journal.__file__).resolve() == ROOT / "webapp/run_journal.py"
    monkeypatch.setattr(run_journal._urlrequest, "urlopen", forbidden)
    return run_journal


@pytest.fixture
def queued(offline, tmp_path):
    journal = offline
    wd = tmp_path / "package-runs" / PACKAGE_SHA
    head = {"record_id": "run-test", "source_run": "run-test",
            "declaration_sha256": DECLARATION_SHA, "record_api": API,
            "owner": "panel"}
    rows = []
    for stage, substage in (("pairing", "A1"), ("source-check", "A1/pairing"),
                           ("rostering", "A2"), ("source-check", "A2/roster")):
        entry = {"stage": stage, "substage": substage,
                 "outcome": "success", "words": "transport fixture",
                 "artifacts": [{"kind": "package", "sha256": PACKAGE_SHA,
                                "path": "task-package.zip"}]}
        rows.append({"key": journal._key(entry), "entry": entry,
                     "at": "2026-09-21T12:00:00+00:00",
                     "results": {"verdicts": {stage: "fixture"}}})
    journal._rewrite(journal.entries_path(wd), rows)
    journal._rewrite(journal.outbox_path(wd), rows)
    return journal, wd, head, rows


class Response:
    def __init__(self, body, status=200):
        self.status = status
        self.raw = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def route(monkeypatch, wd, head, *, missing=False, unauthorized=False):
    """Execute the checked-in endpoint body, never application startup."""
    import webapp
    from webapp import run_gate
    from starlette.responses import JSONResponse

    source = ROOT / "webapp/app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == "api_run_stitch")
    assert len(node.decorator_list) == 1
    decorator = node.decorator_list[0]
    assert isinstance(decorator, ast.Call)
    assert ast.literal_eval(decorator.args[0]) == "/api/run/stitch"
    assert decorator.func.attr == "post"
    node.decorator_list = []

    events = []
    package = types.ModuleType("webapp.package_runs")
    manifest = {"record_id": head["record_id"], "package_sha": PACKAGE_SHA,
                "declaration_sha256": head["declaration_sha256"]}
    package_path = wd / "task-package.zip"

    def resolve():
        events.append("resolve")
        return None if missing else (None, PACKAGE_SHA, package_path, wd, None)

    def read_manifest(path):
        assert path == package_path
        events.append("manifest")
        return manifest

    def checked_head(actual_wd, actual_manifest):
        assert actual_wd == wd and actual_manifest is manifest
        events.append("head")
        return head

    def authenticate(_request):
        events.append("authenticate")
        if unauthorized:
            raise PermissionError("fixture unauthorized")

    package.resolve_current = resolve
    package._package_manifest = read_manifest
    monkeypatch.setitem(sys.modules, "webapp.package_runs", package)
    monkeypatch.setattr(webapp, "package_runs", package, raising=False)
    monkeypatch.setattr(run_gate, "head_for", checked_head)
    namespace = {"__package__": "webapp", "Request": object,
                 "_user_or_channel": authenticate, "JSONResponse": JSONResponse}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"),
         namespace)
    return namespace["api_run_stitch"], events


class SlowAcknowledgingOwner:
    """Models durable append before a slow ACK, without sleep or a server."""
    def __init__(self, required_seconds):
        self.required_seconds = required_seconds
        self.rows = {}
        self.seen = []

    def __call__(self, request, *, timeout):
        assert request.full_url == API + "/append"
        assert request.get_method() == "POST"
        assert request.get_header("Content-type") == "application/json; charset=utf-8"
        body = json.loads(request.data)
        assert body["record_id"] == "run-test"
        assert body["declaration_sha256"] == DECLARATION_SHA
        assert body["schema"] == "cosmos-run-record-append/v1"
        assert body["system"] == "cosmos"
        replaced = 0
        for entry in body["entries"]:
            assert entry["artifacts"][0]["sha256"] == PACKAGE_SHA
            key = (body["system"], entry["stage"], entry["substage"])
            replaced += key in self.rows
            self.rows[key] = entry
        self.seen.append({"timeout": timeout, "bytes": request.data,
                          "replaced": replaced, "body": body})
        if timeout < self.required_seconds:
            raise TimeoutError("simulated ACK exceeds caller budget")
        return Response({"ok": True, "accepted": len(body["entries"]),
                         "replaced": replaced,
                         "record": {"declaration": {"run_id": "run-test"}}})


def test_explicit_ui_retry_can_ack_same_batch_after_automatic_timeout(queued, monkeypatch):
    journal, wd, head, rows = queued
    owner = SlowAcknowledgingOwner(9.4)
    monkeypatch.setattr(journal._urlrequest, "urlopen", owner)
    original_queue = journal.outbox_path(wd).read_bytes()
    original_entries = journal.entries_path(wd).read_bytes()
    original_head = copy.deepcopy(head)
    first = journal.flush_outbox(wd, head)
    assert first["outcome"] == journal.STITCH_WAITING
    assert owner.seen[0]["timeout"] == 5.0
    assert len(owner.rows) == 4, "owner stored before its ACK timed out"
    assert journal.outbox_path(wd).read_bytes() == original_queue

    retry, events = route(monkeypatch, wd, head)
    got = retry(object())
    assert got["ok"] and got["sent"] == 4 and got["waiting"] == 0
    assert events == ["authenticate", "resolve", "manifest", "head"]
    assert [r["timeout"] for r in owner.seen] == [5.0, 30.0]
    assert owner.seen[0]["bytes"] == owner.seen[1]["bytes"]
    assert owner.seen[1]["replaced"] == 4 and len(owner.rows) == 4
    assert head == original_head and journal.entries_path(wd).read_bytes() == original_entries
    assert journal._read_rows(journal.outbox_path(wd)) == []
    receipts = journal._read_rows(journal.receipts_path(wd))
    assert [r["outcome"] for r in receipts] == [journal.STITCH_WAITING, journal.STITCH_SENT]
    assert receipts[-1]["keys"] == [r["key"] for r in rows]
    assert receipts[-1]["accepted"] == 4 and receipts[-1]["replaced"] == 4
    # Subsequent automatic delivery does not inherit a global 30-second override.
    journal._rewrite(journal.outbox_path(wd), rows)
    assert journal.flush_outbox(wd, head)["outcome"] == journal.STITCH_WAITING
    assert owner.seen[-1]["timeout"] == 5.0


@pytest.mark.parametrize("ui_retry, expected_timeout", [(False, 5.0), (True, 30.0)])
def test_healthy_delivery_uses_only_its_fixed_budget(queued, monkeypatch, ui_retry, expected_timeout):
    journal, wd, head, _ = queued
    owner = SlowAcknowledgingOwner(0.1)
    monkeypatch.setattr(journal._urlrequest, "urlopen", owner)
    got = route(monkeypatch, wd, head)[0](object()) if ui_retry else journal.flush_outbox(wd, head)
    assert got["sent"] == 4 and got["waiting"] == 0
    assert owner.seen[0]["timeout"] == expected_timeout


def test_explicit_retry_is_bounded_and_timeout_keeps_queue(queued, monkeypatch):
    journal, wd, head, _ = queued
    owner = SlowAcknowledgingOwner(30.01)
    monkeypatch.setattr(journal._urlrequest, "urlopen", owner)
    before = journal.outbox_path(wd).read_bytes()
    # Request input cannot widen the server-selected budget.
    request = types.SimpleNamespace(query_params={"timeout": "999999"})
    got = route(monkeypatch, wd, head)[0](request)
    assert owner.seen[0]["timeout"] == 30.0
    assert not got["ok"] and got["outcome"] == journal.STITCH_WAITING
    assert got["waiting"] == 4 and got["refused"] == 0
    assert journal.outbox_path(wd).read_bytes() == before


@pytest.mark.parametrize("status, payload, outcome", [
    (409, {"code": "declaration-mismatch", "words": "wrong declaration"}, "refused"),
    (422, {"detail": "invalid entry"}, "refused"),
    (503, {"detail": "busy"}, "waiting"),
    (200, b"not JSON", "waiting"),
    (200, {"ok": False}, "waiting"),
])
def test_retry_preserves_owner_and_receipt_failure_checks(queued, monkeypatch, status, payload, outcome):
    journal, wd, head, _ = queued
    before = journal.outbox_path(wd).read_bytes()
    calls = []

    def answer(request, *, timeout):
        calls.append(timeout)
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "fixture", {},
                                         io.BytesIO(json.dumps(payload).encode()))
        return Response(payload)

    monkeypatch.setattr(journal._urlrequest, "urlopen", answer)
    got = route(monkeypatch, wd, head)[0](object())
    assert calls == [30.0]
    assert not got["ok"] and got["outcome"] == outcome
    assert got["waiting"] == 4 and got["refused"] == (outcome == "refused")
    assert journal.outbox_path(wd).read_bytes() == before
    receipt = journal._read_rows(journal.receipts_path(wd))[-1]
    assert receipt["status"] == status and receipt["ok"] is False


def test_new_unsent_key_is_not_removed_by_successful_retry(queued, monkeypatch):
    journal, wd, head, rows = queued
    owner = SlowAcknowledgingOwner(0.1)
    extra = copy.deepcopy(rows[-1])
    extra["key"]["substage"] = extra["entry"]["substage"] = "A3/roster"

    def answer(request, *, timeout):
        result = owner(request, timeout=timeout)
        journal._append_row(journal.outbox_path(wd), extra)
        return result

    monkeypatch.setattr(journal._urlrequest, "urlopen", answer)
    got = route(monkeypatch, wd, head)[0](object())
    assert got["sent"] == 4
    assert journal._read_rows(journal.outbox_path(wd)) == [extra]
    assert got["stitch"]["waiting"] == 1


@pytest.mark.parametrize("missing, unauthorized", [(True, False), (False, True)])
def test_existing_route_preconditions_do_not_send(queued, monkeypatch, missing, unauthorized):
    journal, wd, head, _ = queued
    before = journal.outbox_path(wd).read_bytes()
    retry, events = route(monkeypatch, wd, head, missing=missing, unauthorized=unauthorized)
    if unauthorized:
        with pytest.raises(PermissionError):
            retry(object())
        assert events == ["authenticate"]
    else:
        assert retry(object()).status_code == 409
        assert events == ["authenticate", "resolve"]
    assert journal.outbox_path(wd).read_bytes() == before
    assert not journal.receipts_path(wd).exists()


@pytest.mark.parametrize("no_api", [False, True])
def test_empty_or_unaddressed_retry_performs_no_transport(queued, monkeypatch, no_api):
    journal, wd, head, _ = queued
    if no_api:
        head["record_api"] = None
    else:
        journal._rewrite(journal.outbox_path(wd), [])
    got = route(monkeypatch, wd, head)[0](object())
    assert got["sent"] == 0 and got["waiting"] == (4 if no_api else 0)
    assert not journal.receipts_path(wd).exists()


def test_background_call_sites_and_read_only_fetch_keep_default_five_seconds(offline, monkeypatch):
    journal = offline
    for relative, name in (("webapp/run_journal.py", "note"),
                           ("webapp/run_gate.py", "current_record")):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        matches = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                   and ((isinstance(n.func, ast.Name) and n.func.id == "flush_outbox")
                        or (isinstance(n.func, ast.Attribute) and n.func.attr == "flush_outbox"))]
        assert len(matches) == 1, (relative, name)
        assert len(matches[0].args) == 2 and matches[0].keywords == []
    seen = []

    def answer(_request, *, timeout):
        seen.append(timeout)
        return Response({"record": {"declaration": {"run_id": "run-test"}}})

    monkeypatch.setattr(journal._urlrequest, "urlopen", answer)
    assert journal.fetch_owner_record(API)["ok"]
    assert seen == [5.0] and journal.TIMEOUT_S == 5.0
