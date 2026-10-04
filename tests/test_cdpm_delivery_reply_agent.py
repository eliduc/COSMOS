"""Package delivery receipt identity, offline registered-handler integration.

The ZIP builder, producer gate, publication, sender, delivery writer and run
record remain real. Bad-reply probes inject explicitly synthetic HTTP bodies;
separate healthy/retry probes call the real receiver in memory with temporary
storage. No remote persistence, solver, service or HTTP is run.
The response shape comes from the existing package-channel brief, not the
different catalogue-request receipt/1 protocol.
"""
import hashlib
import io
import json
import shutil
from types import SimpleNamespace
import urllib.error
import zipfile

import pytest

from cdpm import channel, run_record
from test_cdpm_package_publish_agent import publication
from test_k01_semantic_transition import clean_bridge  # noqa: F401 (fixture)
from webapp import task_channel


REAL_SEND = channel.send_task_package


class Reply(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False


@pytest.mark.parametrize("reply_kind,confirmed", [
    ("valid-ok", True), ("valid-mismatch", True),
    ("empty", False), ("foreign-sha", False),
    ("foreign-source", False), ("foreign-spec", False),
    ("foreign-source-repeat", False),
])
def test_application_reply_not_http_success_establishes_package_delivery(
        publication, monkeypatch, reply_kind, confirmed):
    state = publication
    monkeypatch.setenv("CREW_CHANNEL_URL", "http://offline.invalid")
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "isolated-test-token")
    replies = []
    requests = []

    def send_through_real_channel(body, revision, source_run):
        assert state.package.read_bytes() == body
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
        actual_sha = hashlib.sha256(body).hexdigest()
        brief = task_channel._brief(
            "MISMATCH" if reply_kind == "valid-mismatch" else "OK",
            detail="Synthetic transport receipt, not operational acceptance",
            sha=actual_sha, spec_version=manifest["spec_version"],
            source_run=manifest["source_run"],
            mg={task: {"state": cell["state"], "launch_allowed": False}
                for task, cell in manifest["readiness_matrix"]["columns"].items()},
            mismatch=["synthetic readiness mismatch"] if reply_kind == "valid-mismatch" else [])
        if reply_kind == "empty":
            brief = {}
        elif reply_kind == "foreign-sha":
            brief["sha"] = "0" * 64
        elif reply_kind in ("foreign-source", "foreign-source-repeat"):
            brief["source_run"] = "other-artifact-run"
            brief["already_accepted"] = reply_kind == "foreign-source-repeat"
        elif reply_kind == "foreign-spec":
            brief["spec_version"] = "1.999"

        def transport(request, timeout):
            requests.append(request)
            assert request.data == body
            headers = {k.lower(): v for k, v in request.header_items()}
            assert headers["x-package-sha256"] == actual_sha
            assert headers["x-spec-version"] == revision
            assert headers["x-source-run"] == source_run
            replies.append(brief)
            return Reply(json.dumps(brief, ensure_ascii=False).encode("utf-8"))

        return REAL_SEND(body, revision, source_run,
                         _urlopen=transport, _sleep=lambda _seconds: None)

    monkeypatch.setattr(channel, "send_task_package", send_through_real_channel)
    mark = state.invoke()
    assert requests and replies
    assert state.checks and all(not problems for _path, problems in state.checks)
    saved = json.loads((state.out / "delivery.json").read_bytes())
    assert saved == mark
    run = state.out.parent
    record = run_record.build(run)
    deliveries = record["results"]["deliveries"]
    snapshot = {"reply": replies[-1], "saved": saved, "deliveries": deliveries}
    assert mark["sent"] is confirmed, snapshot
    assert state.source.read_bytes() == state.source_before
    assert state.package.is_file()
    actual_sha = hashlib.sha256(state.package.read_bytes()).hexdigest()
    assert mark["sha"] == actual_sha
    if confirmed:
        assert len(requests) == 1
        assert mark["verdict"] == ("MISMATCH" if reply_kind == "valid-mismatch" else "OK")
        assert deliveries == [{"kind": "task-package", "path": "channel/task-package.zip",
                               "sha256": actual_sha}]
    else:
        assert len(requests) == 1, "A deterministic invalid receipt is not a network retry"
        assert "не подтверждена" in mark["detail"]
        assert mark["detail"]
        assert deliveries == []


@pytest.fixture
def memory_receiver(publication, monkeypatch, tmp_path):
    """Actual receive/store/repeat with in-memory transport, no HTTP/auth test."""
    from vmp import catalog_access, catalog_requests

    state = publication
    inbox = tmp_path / "receiver-inbox"
    monkeypatch.setenv("CREW_CHANNEL_URL", "http://offline.invalid")
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "isolated-test-token")
    monkeypatch.setenv("CREW_TASK_INBOX", str(inbox))
    monkeypatch.delenv("CREW_TASK_PACKAGE", raising=False)
    monkeypatch.setenv("CREW_COSMOS_RUNS_DIR", "")
    monkeypatch.setenv("CREW_VMP_REGISTRY", str(tmp_path / "receiver-registry.json"))
    monkeypatch.setenv(catalog_requests.ENV_AUTO, "0")
    monkeypatch.setenv(catalog_requests.ENV_OUTBOX, str(tmp_path / "receiver-outbox"))
    monkeypatch.delenv(catalog_requests.ENV_API_URL, raising=False)
    monkeypatch.delenv(catalog_requests.ENV_TOKEN, raising=False)
    catalog_access.forget()
    receipts, sent_bytes, pauses, request_headers = [], [], [], []
    probe = SimpleNamespace(state=state, inbox=inbox, receipts=receipts,
                            sent_bytes=sent_bytes, pauses=pauses, request_headers=request_headers,
                            lost_first_reply=False)

    def send_through_memory_receiver(body, revision, source_run):
        def transport(request, timeout):
            headers = {k.lower(): v for k, v in request.header_items()}
            request_headers.append(headers)
            sent_bytes.append(request.data)
            status, brief = task_channel.receive(
                request.data, headers["x-package-sha256"],
                headers["x-spec-version"], headers["x-source-run"])
            assert status == 200, brief
            receipts.append(brief)
            if probe.lost_first_reply and len(receipts) == 1:
                raise TimeoutError("reply lost after the actual receiver stored the ZIP")
            return Reply(json.dumps(brief, ensure_ascii=False).encode("utf-8"))

        return REAL_SEND(body, revision, source_run, _urlopen=transport, _sleep=pauses.append)

    monkeypatch.setattr(channel, "send_task_package", send_through_memory_receiver)
    probe.send = send_through_memory_receiver
    try:
        yield probe
    finally:
        catalog_access.forget()


@pytest.mark.parametrize("lost_first_reply", [False, True])
def test_real_memory_receiver_confirms_same_package_after_optional_lost_reply(
        memory_receiver, lost_first_reply):
    probe = memory_receiver
    probe.lost_first_reply = lost_first_reply
    state, inbox = probe.state, probe.inbox
    receipts, sent_bytes, pauses = probe.receipts, probe.sent_bytes, probe.pauses
    mark = state.invoke()
    body = state.package.read_bytes()
    sha = hashlib.sha256(body).hexdigest()
    assert mark["sent"] is True and mark["sha"] == sha
    assert mark["verdict"] == "OK"
    assert mark["attempts"] == (2 if lost_first_reply else 1)
    assert mark["already_accepted"] is lost_first_reply
    assert pauses == ([2.0] if lost_first_reply else [])
    assert sent_bytes == [body] * len(receipts)
    assert receipts[0]["already_accepted"] is False
    assert all(reply["sha"] == sha for reply in receipts)
    assert (inbox / sha / "package.zip").read_bytes() == body
    persisted = json.loads((inbox / sha / "verdict.json").read_bytes())
    assert persisted["sha"] == sha and persisted["already_accepted"] is False
    assert [p.name for p in inbox.iterdir() if p.is_dir()] == [sha]
    assert json.loads((state.out / "delivery.json").read_bytes()) == mark
    assert run_record.build(state.out.parent)["results"]["deliveries"] == [
        {"kind": "task-package", "path": "channel/task-package.zip", "sha256": sha}]
    assert state.source.read_bytes() == state.source_before


def test_replay_child_first_receipt_uses_embedded_parent_identity(memory_receiver):
    """Post-replay disk fixture, real builder/gate/receiver, not a replay-driver test."""
    from cdpm import export_task_package as exporter, replay, run_passport

    probe = memory_receiver
    state = probe.state
    child = state.out.parent
    parent = child.parent / "run-4320"
    shutil.copytree(child, parent)
    parent_status = {"created_at": "2026-09-17T00:00:00Z", "passport": {}}
    (parent / "status.json").write_text(json.dumps(parent_status), encoding="utf-8")
    child_status = {"created_at": "2026-09-17T00:01:00Z", "passport": {"replay": {
        "mode": "replay-journal", "source_run": parent.name, "source_dir": str(parent),
        "source_made_by": run_passport.made_by_of(parent, parent_status)}}}
    (child / "status.json").write_text(json.dumps(child_status), encoding="utf-8")
    parent_model = parent / "extract" / "model.json"
    assert parent_model.read_bytes() == state.source_before == state.source.read_bytes()
    assert replay.source_dir_of(child, child_status) == parent
    assert exporter.package_run_name(child) == parent.name != child.name
    assert not probe.inbox.exists(), "The first reply must not come from cached acceptance"

    mark = state.invoke()
    body = state.package.read_bytes()
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        model = json.loads(archive.read("model.json"))
    assert manifest["spec_version"] == "1.21"
    assert manifest["source_run"] == parent.name
    assert model["rules"] and all(
        rule["template_fit"]["schema_version"] == "landing/2" for rule in model["rules"])
    assert state.checks and all(not problems for _path, problems in state.checks)
    receipt = probe.receipts[0]
    sha = hashlib.sha256(body).hexdigest()
    assert receipt["sha"] == sha and receipt["verdict"] == "OK"
    assert receipt["already_accepted"] is False
    assert receipt["source_run"] == parent.name
    assert probe.request_headers[0]["x-source-run"] == child.name
    assert (probe.inbox / sha / "package.zip").read_bytes() == body
    assert parent_model.read_bytes() == state.source_before == state.source.read_bytes()
    assert mark["sent"] is True, {"mark": mark, "receipt": receipt}
    assert mark["attempts"] == 1
    assert json.loads((state.out / "delivery.json").read_bytes()) == mark
    assert run_record.build(child)["results"]["deliveries"] == [
        {"kind": "task-package", "path": "channel/task-package.zip", "sha256": sha}]


def test_cached_same_sha_receipt_keeps_original_identity_for_a_new_sender_run(memory_receiver):
    probe = memory_receiver
    first = probe.state.invoke()
    assert first["sent"] is True and first["already_accepted"] is False
    body = probe.state.package.read_bytes()
    original = probe.receipts[0]
    assert original["source_run"] == probe.state.out.parent.name
    mark = probe.send(body, original["spec_version"], "different-sender-run")
    cached = probe.receipts[-1]
    assert cached["sha"] == first["sha"] and cached["already_accepted"] is True
    assert cached["source_run"] == original["source_run"] != "different-sender-run"
    assert probe.sent_bytes == [body, body]
    assert (probe.inbox / first["sha"] / "package.zip").read_bytes() == body
    assert mark["sent"] is True, mark
    assert mark["already_accepted"] is True and mark["sha"] == first["sha"]


def test_unreadable_manifest_does_not_raise_before_receiver_refusal(monkeypatch):
    """Sender-only malformed bytes; HTTP refusal is synthetic, not a gate verdict."""
    monkeypatch.setenv("CREW_CHANNEL_URL", "http://offline.invalid")
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "isolated-test-token")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", '{"spec_version":"1.21","source_run":"run-X"}')
        info = archive.getinfo("manifest.json")
        offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    body = bytearray(buffer.getvalue())
    # Invalid DEFLATE block type, with an otherwise readable ZIP directory.
    body[offset] = (body[offset] & ~0b111) | 0b111
    calls = []

    def transport(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, 422, "refused", {},
                                     io.BytesIO(b"unreadable ZIP"))

    mark = REAL_SEND(bytes(body), "1.21", "run-X", _urlopen=transport,
                     _sleep=lambda _seconds: pytest.fail("HTTP refusal must not retry"))
    assert mark["sent"] is False and mark["attempts"] == len(calls) == 1
    assert "HTTP 422" in mark["detail"]


def test_historical_acceptance_with_explicit_missing_bytes_is_not_current_delivery(memory_receiver):
    probe = memory_receiver
    first = probe.state.invoke()
    assert first["sent"] is True
    body = probe.state.package.read_bytes()
    receipt_path = probe.inbox / first["sha"] / "verdict.json"
    original_receipt = receipt_path.read_bytes()
    # Fault injection only in this test's temporary receiver store.
    (probe.inbox / first["sha"] / "package.zip").unlink()
    original = probe.receipts[0]
    mark = probe.send(body, original["spec_version"], original["source_run"])
    historical = probe.receipts[-1]
    assert historical["sha"] == first["sha"] and historical["already_accepted"] is True
    assert historical["run_note_code"] == "package-bytes-missing"
    assert receipt_path.read_bytes() == original_receipt
    assert probe.state.package.read_bytes() == body
    assert mark["sent"] is False, mark
    assert "не подтверждена" in mark["detail"]


@pytest.mark.parametrize("change,confirmed", [
    ("null-legacy-identities", True), ("absent-legacy-identities", True),
    ("present-legacy-identities", True),
    ("foreign-spec", False), ("foreign-source", False),
    ("foreign-sha-repeat", False),
    ("boolean-mismatch", False), ("numeric-repeat", False),
    ("refused-verdict", False), ("json-array", False), ("not-json", False),
])
def test_existing_brief_types_and_optional_identity_are_not_coerced(monkeypatch, change, confirmed):
    """Sender-boundary probe; this synthetic brief is not receiver acceptance."""
    monkeypatch.setenv("CREW_CHANNEL_URL", "http://offline.invalid")
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", "isolated-test-token")
    if "legacy-identities" in change:
        # No embedded identity: optional brief fields are not compared to headers.
        body = b"isolated legacy sender bytes without embedded identity"
    else:
        # A minimal sender-only ZIP, NOT a gate-valid package or receiver verdict.
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("manifest.json", json.dumps({
                "spec_version": "1.20", "source_run": "own-run"}))
        body = buffer.getvalue()
    sha = hashlib.sha256(body).hexdigest()
    reply = task_channel._brief("OK", detail="synthetic sender-only fixture", sha=sha)
    if change == "absent-legacy-identities":
        reply.pop("spec_version")
        reply.pop("source_run")
    elif change == "present-legacy-identities":
        reply.update(spec_version="1.14", source_run="legacy-artifact-run")
    elif change == "foreign-spec":
        reply["spec_version"] = "1.999"
    elif change == "foreign-source":
        reply["source_run"] = "another-run"
    elif change == "foreign-sha-repeat":
        reply.update(sha="0" * 64, already_accepted=True)
    elif change == "boolean-mismatch":
        reply["mismatch"] = False
    elif change == "numeric-repeat":
        reply["repeat"] = 1
    elif change == "refused-verdict":
        reply["verdict"] = "REFUSED"
    elif change == "json-array":
        reply = []
    raw = b"not JSON" if change == "not-json" else json.dumps(reply).encode("utf-8")
    calls = []

    def transport(request, timeout):
        calls.append(request)
        return Reply(raw)

    mark = REAL_SEND(body, "1.20", "own-run", _urlopen=transport,
                     _sleep=lambda _seconds: pytest.fail("deterministic reply must not retry"))
    assert mark["sent"] is confirmed and mark["sha"] == sha, mark
    assert len(calls) == mark["attempts"] == 1
    if not confirmed:
        assert "не подтверждена" in mark["detail"]
