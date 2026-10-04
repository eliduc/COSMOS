"""P0 sections 3.6 (line 4), 3.7: interrupted package publication and retry.

The registered synchronous handler runs with an in-memory Starlette Request.
Exporter, gate, disabled-channel response and file-replace retry stay real.
Fault injection touches only stored ZIP bytes or the low-level file operation;
no gate verdict is supplied. This is not HTTP, delivery or executor readiness.
The SOFT seed is a modern-format package, not a complete executable family.
"""
import hashlib
import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import zipfile

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from cdpm import channel, export_task_package as exporter, file_replace
from cdpm.gates import gate_task_package as gate
from test_cdpm_landing_v2_export import real_soft_package
from test_k01_semantic_transition import clean_bridge  # noqa: F401


@pytest.fixture
def publication(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    monkeypatch.setenv("CDPM_RUNS_DIR", str(root))
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CREW_CHANNEL_URL", raising=False)
    monkeypatch.delenv("CREW_CHANNEL_TOKEN", raising=False)
    from cdpm import catalog_requests
    monkeypatch.setenv(catalog_requests.ENV_AUTO, "0")
    monkeypatch.setenv(catalog_requests.ENV_OUTBOX, str(tmp_path / "catalog-outbox"))
    monkeypatch.delenv(catalog_requests.ENV_SERVICE_URL, raising=False)
    monkeypatch.delenv(catalog_requests.ENV_SERVICE_TOKEN, raising=False)
    parts = real_soft_package(tmp_path / "seed", monkeypatch, "1.20")
    run = root / "run-4321"
    shutil.copytree(parts[4], run)
    from cdpm import webui
    routes = [route for route in webui.app.routes
              if getattr(route, "path", None) == "/api/runs/{run_id}/export/{fmt}"
              and "GET" in (getattr(route, "methods", None) or set())]
    assert len(routes) == 1 and routes[0].endpoint is webui.export_for_solver
    request = Request({"type": "http", "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": "/api/runs/run-4321/export/task-package-send",
        "root_path": "", "query_string": b"", "headers": [],
        "server": ("offline.invalid", 80), "client": ("offline", 1), "app": webui.app})
    calls = []
    real_send = channel.send_task_package

    def disabled_send(body, revision, source_run):
        # Real channel configuration is disabled; never replace it with a receipt.
        assert not channel.channel_enabled()
        assert (run / "channel" / "task-package.zip").read_bytes() == body
        calls.append((body, revision, source_run))
        return real_send(body, revision, source_run)

    monkeypatch.setattr(channel, "send_task_package", disabled_send)

    def invoke():
        return webui.export_for_solver(run.name, "task-package-send", request)

    old_mark = invoke()
    assert old_mark["sent"] is False and old_mark["attempts"] == 0, old_mark
    out = run / "channel"
    package = out / "task-package.zip"
    assert gate.check(package)[0] == []
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    assert set(before) == {"task-package.zip", "delivery.json"}
    source = run / "extract" / "model.json"
    source_before = source.read_bytes()
    calls.clear()
    # A distinct, genuine next export; no production specification activation.
    monkeypatch.setattr(exporter, "SPEC_VERSION", "1.21")
    checks = []
    real_check = gate.check

    def observed_check(path, **kwargs):
        result = real_check(path, **kwargs)
        checks.append((Path(path), list(result[0])))
        return result

    monkeypatch.setattr(gate, "check", observed_check)
    return SimpleNamespace(invoke=invoke, out=out, package=package, before=before,
        source=source, source_before=source_before, calls=calls, checks=checks)


def _preserved(state):
    assert {p.name: p.read_bytes() for p in state.out.iterdir()} == state.before, (
        "A refused publication must preserve the previous ZIP and delivery mark, "
        "and remove only its own staging file")
    assert not state.calls, "A failed publication must never enter channel send"
    assert state.source.read_bytes() == state.source_before


def _successful_retry(state, *, retained_staging=()):
    mark = state.invoke()
    assert mark["sent"] is False and mark["attempts"] == 0, mark
    assert len(state.calls) == 1 and state.calls[0][1:] == ("1.21", "run-4321")
    body = state.package.read_bytes()
    assert body == state.calls[0][0] and body != state.before["task-package.zip"]
    assert mark["sha"] == hashlib.sha256(body).hexdigest()
    assert json.loads((state.out / "delivery.json").read_bytes()) == mark
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        assert json.loads(archive.read("manifest.json"))["spec_version"] == "1.21"
    assert gate.check(state.package)[0] == []
    assert {p.name for p in state.out.iterdir()} == (
        set(state.before) | {p.name for p in retained_staging})
    assert state.source.read_bytes() == state.source_before


def _damage_checksums(body):
    # Five independent actual gate errors, with otherwise unchanged members.
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    assert len(manifest["files"]) >= 5
    for entry in manifest["files"][:5]:
        entry["sha256"] = "0" * 64
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    return exporter.write_zip(files)


def _faulty_write(monkeypatch, state, *, partial):
    real_write = Path.write_bytes
    paths = []

    def write(path, body):
        if path.parent == state.out:
            paths.append(path)
            if partial:
                real_write(path, body[:37])
                raise OSError("isolated partial ZIP write")
            return real_write(path, _damage_checksums(body))
        return real_write(path, body)

    monkeypatch.setattr(Path, "write_bytes", write)
    return paths


def test_real_handler_healthy_publish_and_retry(publication):
    _successful_retry(publication)


def test_partial_write_preserves_previous_package_and_delivery_then_retries(
        publication, monkeypatch):
    state = publication
    with monkeypatch.context() as fault:
        paths = _faulty_write(fault, state, partial=True)
        with pytest.raises((OSError, HTTPException)) as refused:
            state.invoke()
    assert len(paths) == 1
    _preserved(state)
    assert isinstance(refused.value, HTTPException) and refused.value.status_code == 409
    assert "isolated partial ZIP write" in str(refused.value.detail)
    assert not state.checks, "Partial bytes must not reach the package gate"
    _successful_retry(state)
    assert paths[0] != state.package and state.checks[0][0] != paths[0]


def test_real_gate_refusal_preserves_previous_package_and_delivery_then_retries(
        publication, monkeypatch):
    state = publication
    with monkeypatch.context() as fault:
        paths = _faulty_write(fault, state, partial=False)
        with pytest.raises(HTTPException) as refused:
            state.invoke()
    assert refused.value.status_code == 409
    assert len(paths) == len(state.checks) == 1 and len(state.checks[0][1]) >= 5
    _preserved(state)
    _successful_retry(state)
    assert state.checks[0][0] != state.checks[1][0], "Each attempt owns unique staging"


def test_real_gate_refusal_exposes_all_problem_words(publication, monkeypatch):
    state = publication
    with monkeypatch.context() as fault:
        _faulty_write(fault, state, partial=False)
        with pytest.raises(HTTPException) as refused:
            state.invoke()
    assert refused.value.status_code == 409
    assert len(state.checks) == 1
    problems = state.checks[0][1]
    assert len(problems) >= 5, problems
    assert refused.value.detail == "пакет не прошёл гейт перед отправкой: " + "; ".join(problems)
    _preserved(state)


def test_cleanup_failure_preserves_primary_gate_refusal_and_retry_ignores_stale_zip(
        publication, monkeypatch):
    state = publication
    real_unlink = Path.unlink
    cleanup = []

    def locked_cleanup(path, *args, **kwargs):
        if path.parent == state.out and path.name.startswith(".task-package-"):
            cleanup.append(path)
            raise PermissionError(13, "isolated staging cleanup refusal", str(path))
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as fault:
        _faulty_write(fault, state, partial=False)
        fault.setattr(Path, "unlink", locked_cleanup)
        with pytest.raises(HTTPException) as refused:
            state.invoke()
    assert refused.value.status_code == 409
    assert len(state.checks) == len(cleanup) == 1
    stale, problems = state.checks[0]
    assert cleanup == [stale] and len(problems) >= 5
    assert refused.value.detail == "пакет не прошёл гейт перед отправкой: " + "; ".join(problems)
    assert {name: (state.out / name).read_bytes() for name in state.before} == state.before
    assert not state.calls
    stale_bytes = stale.read_bytes()
    real_open = io.open

    def forbid_stale_read(file, *args, **kwargs):
        if isinstance(file, (str, Path)):
            assert Path(file) != stale, "Retry must not consume an abandoned staging ZIP"
        return real_open(file, *args, **kwargs)

    with monkeypatch.context() as retry:
        retry.setattr(io, "open", forbid_stale_read)
        _successful_retry(state, retained_staging=(stale,))
    assert state.checks[1][0] != stale
    assert stale.read_bytes() == stale_bytes


@pytest.mark.parametrize("transient", [False, True])
def test_actual_replace_retry_preserves_previous_bytes_until_publication(
        publication, monkeypatch, transient):
    state = publication
    real_replace = file_replace.replace_file
    attempts = []

    def interrupted_replace(src, dst):
        if Path(dst) == state.package:
            assert state.package.read_bytes() == state.before["task-package.zip"]
            assert (state.out / "delivery.json").read_bytes() == state.before["delivery.json"]
            assert not state.calls and state.checks[-1] == (Path(src), [])
            attempts.append(Path(src))
            if not transient or len(attempts) == 1:
                raise PermissionError(13, "isolated reader holds previous ZIP", str(dst))
        return real_replace(src, dst)

    with monkeypatch.context() as fault:
        fault.setattr(file_replace, "replace_file", interrupted_replace)
        if not transient:
            fault.setattr(file_replace, "REPLACE_WINDOW_S", 0)
            with pytest.raises((OSError, HTTPException)) as refused:
                state.invoke()
        else:
            _successful_retry(state)
    if transient:
        assert len(attempts) == 2 and attempts[0] == attempts[1]
    else:
        assert len(attempts) == 1
        _preserved(state)
        assert isinstance(refused.value, HTTPException) and refused.value.status_code == 409
        assert "isolated reader holds previous ZIP" in str(refused.value.detail)
        _successful_retry(state)
