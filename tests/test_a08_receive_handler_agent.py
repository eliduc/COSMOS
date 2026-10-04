"""A06/A08 receive handler -> actual package gates -> complete refusal words.

P0-CONTRACTS-v1.13 sections 3.3, 3.4 and 3.7 require the direction and
objective-corruption refusals to survive both package doors. This adds the
registered HTTP handler boundary to the existing direct-reader evidence.

Transport seams are explicit: a real Starlette Request reads in-memory bytes,
and run_in_threadpool dispatches synchronously. No judge, package reader,
readiness result, authentication decision or persistence function is mocked.
This is NOT socket HTTP, middleware/routing, browser rendering, executor
readiness, or a stand. The SOFT seed is a valid package but its family is not
complete; acceptance is deliberately not treated as an execution permission.
The seed has no released matching executor bindings. A receipt may therefore
name an unavailable registry; the test preserves that absence, not a fake one.
"""
import hashlib
import json
import zipfile

import pytest
from starlette.requests import Request

from cdpm.gates import gate_task_package as producer_gate
from test_a08_journal_boundaries_agent import _rewrite_objective
from test_cdpm_landing_v2_export import real_soft_package
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from webapp import task_package as receiver


ROUTE = "/api/task-package/receive"
TOKEN = "isolated-a08-handler-token"
TAIL = "zz_a08_handler_tail_after_many_complete_diagnostics"


@pytest.fixture
def handler(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.setenv("CREW_TASK_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("CREW_VMP_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.setenv("CREW_COSMOS_RUNS_DIR", "")
    monkeypatch.setenv("CREW_TASK_PACKAGE", "")
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    from vmp import catalog_requests
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
    return application, task_channel, dispatched


def _request(application, body, *, token=TOKEN, sha=None, revision="1.20"):
    headers = {"content-type": "application/zip", "content-length": str(len(body)),
               "x-package-sha256": sha or hashlib.sha256(body).hexdigest(),
               "x-spec-version": revision}
    if token is not None:
        headers["x-channel-token"] = token
    body_reads = []

    async def receive_body():
        assert not body_reads, "The bounded request body must be consumed once"
        body_reads.append(True)
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({"type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": ROUTE, "raw_path": ROUTE.encode(),
        "query_string": b"", "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        "server": ("offline.invalid", 80), "client": ("offline", 1),
        "app": application.app}, receive=receive_body)
    return request, body_reads


def _complete(coroutine):
    # Both transport awaits finish in memory. Refuse an unexpected suspension
    # instead of starting an event loop (which can allocate sockets on Windows).
    try:
        try:
            suspended = coroutine.send(None)
        except StopIteration as finished:
            return finished.value
        pytest.fail("Unexpected asynchronous transport dependency: " + repr(suspended))
    finally:
        coroutine.close()


def _body(response):
    return json.loads(response.body)


def _corrupt(objective, mutation):
    if mutation == "direction":
        assert objective["pairing"]["direction"] == "minimize"
        objective["pairing"]["direction"] = "maximize"
        return
    components = objective["pairing"]["components"]
    assert components
    for number in range(12):
        components.append({"rule_id": f"a08_handler_extra_{number:02d}_untrusted_component",
                           "weight": 1})
    components.append({"rule_id": TAIL, "weight": 1})


@pytest.mark.parametrize("revision", ["1.20", "1.21"])
@pytest.mark.parametrize("mutation,code", [
    ("direction", "criterion-direction"), ("long-components", "objective-mismatch")])
def test_registered_receive_handler_preserves_real_projection_refusal(
        tmp_path, monkeypatch, handler, mutation, code, revision):
    application, channel, dispatched = handler
    parts = real_soft_package(tmp_path / "seed", monkeypatch, revision)
    source_path, source_bytes, body, path = parts[5], parts[6], parts[-2], parts[-1]
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["spec_version"] == revision, manifest
    assert producer_gate.check(path)[0] == []
    assert receiver.load(path, run_gate=False)["objective_projection"]["judged"] is True

    # Healthy control traverses the same registered handler and actual storage.
    request, body_reads = _request(application, body, revision=revision)
    assert request.headers["x-spec-version"] == revision
    healthy_response = _complete(application.api_task_package_receive(request))
    healthy = _body(healthy_response)
    assert healthy_response.status_code == 200, healthy
    assert healthy["verdict"] in {"OK", "MISMATCH"}, healthy
    assert body_reads == [True] and len(dispatched) == 1
    assert healthy["cosmos_run"] is None and healthy["run_note_code"] == "storage-off"
    assert set(healthy["mg"]) == {"pairing", "rostering"}, healthy
    assert all(not cell["launch_allowed"] for cell in healthy["mg"].values()), healthy
    inbox = channel.inbox_dir()
    accepted_sha = hashlib.sha256(body).hexdigest()
    accepted = inbox / accepted_sha
    before = {p: p.read_bytes() for p in accepted.iterdir() if p.is_file()}
    current_before = (inbox / channel.CURRENT_NAME).read_bytes()
    registry_file = channel.registry_path()
    registry_before = registry_file.read_bytes() if registry_file.is_file() else None
    if registry_before is None:
        assert "вывод реестра исполнителей не удался" in healthy["detail"], healthy
    assert (accepted / channel.PACKAGE_NAME).read_bytes() == body

    changed = _rewrite_objective(body, lambda value: _corrupt(value, mutation))
    bad_path = tmp_path / "altered-objective.zip"
    bad_path.write_bytes(changed)
    gate_problems, _ = producer_gate.check(bad_path)
    assert any(code in words for words in gate_problems), gate_problems
    with pytest.raises(receiver.TaskPackageRefused) as refused:
        receiver.load(bad_path, run_gate=False)
    assert refused.value.code == code, str(refused.value)
    words = str(refused.value)
    if mutation == "long-components":
        assert len(words) > 1200 and TAIL in words and TAIL not in words[:1200]
        assert len(refused.value.problems) == 13, refused.value.problems

    request, body_reads = _request(application, changed, revision=revision)
    assert request.headers["x-spec-version"] == revision
    response = _complete(application.api_task_package_receive(request))
    result = _body(response)
    assert response.status_code == 422, result
    assert result["verdict"] == "REFUSED", result
    assert result["detail"] == words, result
    assert body_reads == [True] and len(dispatched) == 2
    assert result["sha"] == hashlib.sha256(changed).hexdigest()
    assert not (inbox / result["sha"]).exists()
    assert not list(inbox.glob("incoming-*.zip"))
    assert {p: p.read_bytes() for p in accepted.iterdir() if p.is_file()} == before
    assert (inbox / channel.CURRENT_NAME).read_bytes() == current_before
    registry_after = registry_file.read_bytes() if registry_file.is_file() else None
    assert registry_after == registry_before
    journal = [json.loads(line) for line in (inbox / channel.RECEIPTS_NAME)
               .read_text(encoding="utf-8").splitlines()]
    assert len(journal) == 2
    assert journal[-1]["verdict"] == "REFUSED" and journal[-1]["sha"] == result["sha"]
    assert source_path.read_bytes() == source_bytes and path.read_bytes() == body


@pytest.mark.parametrize("token", [None, "wrong-token"])
def test_registered_receive_handler_denies_token_before_body_and_validation(handler, token):
    from fastapi import HTTPException
    application, channel, dispatched = handler
    request, body_reads = _request(application, b"not-a-zip", token=token)
    with pytest.raises(HTTPException) as refused:
        _complete(application.api_task_package_receive(request))
    assert refused.value.status_code == 403
    assert refused.value.detail == "доступ запрещён"
    assert not body_reads and not dispatched
    assert not channel.inbox_dir().exists()


def test_registered_receive_handler_checks_sha_before_package_validation(handler):
    application, channel, dispatched = handler
    request, body_reads = _request(application, b"not-a-zip", sha="0" * 64)
    response = _complete(application.api_task_package_receive(request))
    result = _body(response)
    assert response.status_code == 422 and result["verdict"] == "REFUSED", result
    assert "sha не совпал" in result["detail"]
    assert body_reads == [True] and len(dispatched) == 1
    assert not channel.inbox_dir().exists()
