"""A08 native draft.3/spec1.20 package -> real sender -> receive -> repeat.

Only transport is in memory: urllib is adapted to the registered handler,
and its threadpool dispatch runs inline. Gates, projection, readiness,
storage, receipt construction and sender receipt validation are real.
The original duty stand has a genuine model-bound local Z3 proof; unsigned
support remains unsigned and does NOT grant launch permission. No ASGI
lifespan, network, JVM, service, catalogue release or fake approval runs.
"""
import hashlib
import io
import json
from types import SimpleNamespace
import urllib.error
import zipfile

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from cdpm import channel as sender
from cdpm import smt_verify
from cdpm.gates import gate_task_package as producer_gate
from test_a06_native_stand_proof_agent import prepared, prepare_world  # noqa: F401
from test_a08_journal_boundaries_agent import _rewrite_objective
from test_a08_receive_handler_agent import _complete
from vmp import catalog_requests, stand_solver
from webapp import task_package as receiver


ROUTE = "/api/task-package/receive"
URL = "http://a08-native.invalid"
TOKEN = "isolated-native-receive-token"


@pytest.fixture
def native_channel(prepared, tmp_path, monkeypatch):
    # Dependency order is intentional: prepared clears channel environment.
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "channel-data"))
    monkeypatch.setenv("CREW_TASK_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("CREW_VMP_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.setenv("CREW_COSMOS_RUNS_DIR", "")
    monkeypatch.setenv("CREW_TASK_PACKAGE", "")
    monkeypatch.setenv("CREW_CHANNEL_URL", URL)
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    monkeypatch.setenv(catalog_requests.ENV_AUTO, "0")
    monkeypatch.setenv(catalog_requests.ENV_OUTBOX, str(tmp_path / "catalog-queue"))
    monkeypatch.delenv(catalog_requests.ENV_API_URL, raising=False)
    monkeypatch.delenv(catalog_requests.ENV_TOKEN, raising=False)
    from webapp import app as application, task_channel
    import starlette.concurrency

    dispatched = []

    async def inline_dispatch(fn, *args, **kwargs):
        assert fn is task_channel.receive
        dispatched.append((args, kwargs))
        return fn(*args, **kwargs)

    monkeypatch.setattr(starlette.concurrency, "run_in_threadpool", inline_dispatch)
    matches = [route for route in application.app.routes
               if getattr(route, "path", None) == ROUTE
               and "POST" in (getattr(route, "methods", None) or set())]
    assert len(matches) == 1
    assert matches[0].endpoint is application.api_task_package_receive
    return SimpleNamespace(catalog=prepared, application=application,
                           channel=task_channel, dispatched=dispatched,
                           tmp=tmp_path)


def _package(context, task_id):
    base = context.tmp / task_id
    world, rules, doc, run, raw, report = prepare_world(context.catalog, base, task_id)
    proof_bytes = smt_verify.report_path(run).read_bytes()
    body = stand_solver.package_bytes(
        world, rules, stamp=stand_solver.stamp_of(context.catalog), base=base,
        catalog=context.catalog, family="duty_limits", spec_version="1.20")
    path = context.tmp / (task_id + ".zip")
    path.write_bytes(body)
    assert producer_gate.check(path)[0] == []
    loaded = receiver.load(path, run_gate=False)
    manifest = loaded["manifest"]
    assert manifest["spec_version"] == "1.20"
    assert manifest["consistency"]["subject_sha256"] == report["subject_sha256"]
    assert manifest["consistency"]["verdict"] == "sat"
    assert not manifest.get("stand_only")
    assert loaded["objective_projection"]["judged"] is True
    assert {(rule["kind"], rule["zone"]) for rule in loaded["model"]["rules"]} == {("HARD", "PAIRING")}
    assert all(cell["approved"] is None for cell in manifest["support"]["cells"])
    with pytest.raises(receiver.TaskPackageRefused) as refusal:
        receiver.check_support_execution(loaded, task="pairing", mode="feasibility-hard")
    assert refusal.value.code == "support-missing"
    # Same unsigned artifact, different task: the only HARD obligation is
    # PAIRING. The real support gate confirms why the ROSTERING column differs.
    receiver.check_support_execution(loaded, task="rostering", mode="feasibility-hard")
    assert (run / "extract/model.json").read_bytes() == raw
    assert smt_verify.report_path(run).read_bytes() == proof_bytes
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert json.loads(archive.read("smt-report.json")) == report
    return body, manifest, run, raw, proof_bytes


class MemoryResponse:
    def __init__(self, response):
        self.status = response.status_code
        self.headers = dict(response.headers)
        self.raw = response.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.raw


def _transport(context, fault=None):
    exchanges = []

    def urlopen(request, *, timeout):
        assert request.full_url == URL + ROUTE
        assert request.get_method() == "POST" and timeout > 0
        raw = request.data
        headers = {key.lower(): value for key, value in request.header_items()}
        assert headers["content-type"] == "application/zip"
        assert headers["x-package-sha256"] == hashlib.sha256(raw).hexdigest()
        assert headers["x-spec-version"] == "1.20"
        assert headers["x-channel-token"] == TOKEN
        if fault == "sha":
            headers["x-package-sha256"] = "0" * 64
        elif fault == "token":
            headers["x-channel-token"] = "wrong-native-test-token"
        headers["content-length"] = str(len(raw))
        reads = []

        async def receive_body():
            assert not reads, "body is consumed once"
            reads.append(True)
            return {"type": "http.request", "body": raw, "more_body": False}

        req = Request({"type": "http", "http_version": "1.1", "method": "POST",
            "scheme": "http", "path": ROUTE, "raw_path": ROUTE.encode(),
            "query_string": b"", "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
            "server": ("a08-native.invalid", 80), "client": ("offline", 1),
            "app": context.application.app}, receive=receive_body)
        try:
            response = _complete(context.application.api_task_package_receive(req))
        except HTTPException as error:
            # Only HTTP transport representation, not an authentication verdict.
            response = JSONResponse(status_code=error.status_code,
                                    content={"detail": error.detail}, headers=error.headers)
        exchanges.append((response.status_code, json.loads(response.body), reads))
        if response.status_code >= 400:
            raise urllib.error.HTTPError(request.full_url, response.status_code,
                                         "handler refusal", dict(response.headers),
                                         io.BytesIO(response.body))
        return MemoryResponse(response)

    return urlopen, exchanges


def _send(context, body, manifest, fault=None):
    transport, exchanges = _transport(context, fault)

    def no_sleep(seconds):
        pytest.fail("deterministic receive must not retry: " + str(seconds))

    mark = sender.send_task_package(body, manifest["spec_version"], manifest["source_run"],
                                    _urlopen=transport, _sleep=no_sleep)
    assert len(exchanges) == 1 and mark["attempts"] == 1
    return mark, exchanges[0]


def _accepted(mark, exchange, sha, repeat):
    status, receipt, reads = exchange
    assert status == 200 and reads == [True]
    assert receipt["verdict"] in {"OK", "MISMATCH"}, receipt
    assert mark["sent"] is True and mark["sha"] == sha, mark
    assert mark["already_accepted"] is repeat
    assert receipt["sha"] == sha and receipt["already_accepted"] is repeat
    assert mark["mg"] == receipt["mg"]
    assert set(receipt["mg"]) == {"pairing", "rostering"}
    # The original HARD rule belongs to PAIRING, not the other task column.
    # Receiving this artifact must not grant permission for that execution.
    assert receipt["mg"]["pairing"]["launch_allowed"] is False
    assert receipt["cosmos_run"] is None and receipt["run_note_code"] == "storage-off"


@pytest.mark.parametrize("task_id", ["st-duty-1", "st-duty-3"])
def test_native_package_sender_registered_receiver_receipt_and_exact_repeat(native_channel, task_id):
    context = native_channel
    body, manifest, run, raw, proof = _package(context, task_id)
    sha = hashlib.sha256(body).hexdigest()
    mark, first = _send(context, body, manifest)
    _accepted(mark, first, sha, False)
    assert first[1]["spec_version"] == manifest["spec_version"]
    assert first[1]["source_run"] == manifest["source_run"]
    box = context.channel.inbox_dir()
    accepted = box / sha
    stored = {p.name: p.read_bytes() for p in accepted.iterdir() if p.is_file()}
    assert stored[context.channel.PACKAGE_NAME] == body
    assert receiver.load(accepted / context.channel.PACKAGE_NAME, run_gate=False)["objective_projection"]["judged"]
    mark, second = _send(context, body, manifest)
    _accepted(mark, second, sha, True)
    assert {p.name: p.read_bytes() for p in accepted.iterdir() if p.is_file()} == stored
    current = json.loads((box / context.channel.CURRENT_NAME).read_bytes())
    assert current["sha"] == sha and current["repeat"] is True
    rows = context.channel.receipts_tail(10)
    assert [(row["sha"], row["repeat"]) for row in rows] == [(sha, False), (sha, True)]
    assert len(context.dispatched) == 2 and not list(box.glob("incoming-*.zip"))
    assert (run / "extract/model.json").read_bytes() == raw
    assert smt_verify.report_path(run).read_bytes() == proof


@pytest.mark.parametrize("fault", ["sha", "token", "projection"])
def test_native_delivery_corruption_does_not_replace_accepted_package(native_channel, fault):
    context = native_channel
    body, manifest, run, raw, proof = _package(context, "st-duty-1")
    sha = hashlib.sha256(body).hexdigest()
    _accepted(*_send(context, body, manifest), sha, False)
    box = context.channel.inbox_dir()
    accepted = box / sha
    before = {p.name: p.read_bytes() for p in accepted.iterdir() if p.is_file()}
    current = (box / context.channel.CURRENT_NAME).read_bytes()
    registry = context.channel.registry_path()
    registry_before = registry.read_bytes() if registry.is_file() else None
    sent_body = body
    if fault == "projection":
        def corrupt(objective):
            assert objective["pairing"]["direction"] == "minimize"
            objective["pairing"]["direction"] = "maximize"
        sent_body = _rewrite_objective(body, corrupt)
        bad_path = context.tmp / "projection-corrupt.zip"
        bad_path.write_bytes(sent_body)
        assert any("criterion-direction" in problem for problem in producer_gate.check(bad_path)[0])
        with pytest.raises(receiver.TaskPackageRefused) as refusal:
            receiver.load(bad_path, run_gate=False)
        assert refusal.value.code == "criterion-direction"
    mark, (status, receipt, reads) = _send(context, sent_body, manifest, fault)
    assert mark["sent"] is False and mark["sha"] == hashlib.sha256(sent_body).hexdigest()
    assert status == (403 if fault == "token" else 422)
    assert reads == ([] if fault == "token" else [True])
    assert "HTTP " + str(status) in mark["detail"]
    if fault == "projection":
        assert receipt["verdict"] == "REFUSED"
        # The channel carries complete human refusal words, not the internal
        # TaskPackageRefused.code field; compare the actual reader's words.
        assert receipt["detail"] == str(refusal.value)
        assert "pairing.direction" in mark["detail"] and "maximize" in mark["detail"]
        assert not (box / mark["sha"]).exists()
    assert {p.name: p.read_bytes() for p in accepted.iterdir() if p.is_file()} == before
    assert (box / context.channel.CURRENT_NAME).read_bytes() == current
    assert (registry.read_bytes() if registry.is_file() else None) == registry_before
    assert not list(box.glob("incoming-*.zip"))
    rows = context.channel.receipts_tail(10)
    assert len(rows) == (2 if fault == "projection" else 1)
    if fault == "projection":
        assert rows[-1]["verdict"] == "REFUSED" and rows[-1]["sha"] == mark["sha"]
    assert (run / "extract/model.json").read_bytes() == raw
    assert smt_verify.report_path(run).read_bytes() == proof
