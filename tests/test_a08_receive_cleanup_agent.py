"""Receiver incoming-temp cleanup must not replace the real primary outcome.

Reuse the registered in-memory handler and modern SOFT package from the existing
A08 handler tests. The only monkeypatched filesystem operation is unlink of
this isolated inbox's incoming-*.zip. Producer gate, receiver, persistence and
returned data are real. The primary-exception control uses an actual directory
blocking package.zip publication, not a mocked judge or store function.
No socket HTTP, complete-family/executor readiness, JVM or full-button claim.
"""
import hashlib
import json
from pathlib import Path

import pytest

from cdpm.gates import gate_task_package as producer_gate
from test_a08_receive_handler_agent import (
    TAIL,
    _body,
    _complete,
    _corrupt,
    _request,
    _rewrite_objective,
    clean_bridge,  # noqa: F401
    handler,  # noqa: F401
    real_soft_package,
)
from webapp import task_package as receiver


REVISION = "1.21"


@pytest.fixture
def package(tmp_path, monkeypatch, handler):
    parts = real_soft_package(tmp_path / "seed", monkeypatch, REVISION)
    source_path, source_bytes, body, path = parts[5], parts[6], parts[-2], parts[-1]
    assert producer_gate.check(path)[0] == []
    parsed = receiver.load(path, run_gate=False)
    assert parsed["manifest"]["spec_version"] == REVISION
    assert parsed["objective_projection"]["judged"] is True
    return source_path, source_bytes, body, path


def _send(application, body):
    request, reads = _request(application, body, revision=REVISION)
    response = _complete(application.api_task_package_receive(request))
    assert reads == [True]
    return response.status_code, _body(response)


def _lock_cleanup(fault, channel):
    real_unlink = Path.unlink
    paths = []
    secondary = PermissionError(13, "isolated incoming cleanup denied")

    def locked(path, *args, **kwargs):
        if (path.parent == channel.inbox_dir()
                and path.name.startswith("incoming-") and path.suffix == ".zip"):
            paths.append(path)
            raise secondary
        return real_unlink(path, *args, **kwargs)

    fault.setattr(Path, "unlink", locked)
    return paths, secondary


def _optional_bytes(path):
    return path.read_bytes() if path.is_file() else None


def _accepted_bytes(channel, sha):
    directory = channel.inbox_dir() / sha
    return {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}


def _assert_source(package):
    source_path, source_bytes, body, path = package
    assert source_path.read_bytes() == source_bytes
    assert path.read_bytes() == body


def test_cleanup_permission_error_preserves_actual_refusal_and_complete_detail(
        tmp_path, monkeypatch, handler, package):
    application, channel, _ = handler
    body = package[2]
    status, healthy = _send(application, body)
    assert status == 200 and healthy["verdict"] in {"OK", "MISMATCH"}, healthy
    assert all(not cell["launch_allowed"] for cell in healthy["mg"].values()), healthy
    sha = hashlib.sha256(body).hexdigest()
    saved = _accepted_bytes(channel, sha)
    current = (channel.inbox_dir() / channel.CURRENT_NAME).read_bytes()
    registry = _optional_bytes(channel.registry_path())

    changed = _rewrite_objective(body, lambda objective: _corrupt(objective, "long-components"))
    bad_path = tmp_path / "bad-objective.zip"
    bad_path.write_bytes(changed)
    assert any("objective-mismatch" in p for p in producer_gate.check(bad_path)[0])
    with pytest.raises(receiver.TaskPackageRefused) as direct:
        receiver.load(bad_path, run_gate=False)
    words = str(direct.value)
    assert direct.value.code == "objective-mismatch" and len(direct.value.problems) == 13
    assert len(words) > 1200 and TAIL in words and TAIL not in words[:1200]

    # The same real handler without the file fault establishes the primary reply.
    status, ordinary = _send(application, changed)
    assert status == 422 and ordinary["verdict"] == "REFUSED", ordinary
    assert ordinary["detail"] == words
    assert not list(channel.inbox_dir().glob("incoming-*.zip"))
    with monkeypatch.context() as fault:
        leftovers, _ = _lock_cleanup(fault, channel)
        status, result = _send(application, changed)
    assert status == 422 and result == ordinary, result
    assert len(leftovers) == 1 and leftovers[0].read_bytes() == changed
    assert not (channel.inbox_dir() / hashlib.sha256(changed).hexdigest()).exists()
    assert _accepted_bytes(channel, sha) == saved
    assert (channel.inbox_dir() / channel.CURRENT_NAME).read_bytes() == current
    assert _optional_bytes(channel.registry_path()) == registry
    assert channel.receipts_tail(3)[-1]["verdict"] == "REFUSED"
    assert bad_path.read_bytes() == changed
    _assert_source(package)


def test_cleanup_permission_error_preserves_healthy_receive_and_repeat_ignores_leftover(
        monkeypatch, handler, package):
    application, channel, _ = handler
    body = package[2]
    sha = hashlib.sha256(body).hexdigest()
    with monkeypatch.context() as fault:
        leftovers, _ = _lock_cleanup(fault, channel)
        status, first = _send(application, body)
    assert status == 200 and first["verdict"] in {"OK", "MISMATCH"}, first
    assert first["already_accepted"] is False and first["sha"] == sha
    assert all(not cell["launch_allowed"] for cell in first["mg"].values()), first
    assert len(leftovers) == 1 and leftovers[0].read_bytes() == body
    saved = _accepted_bytes(channel, sha)
    assert saved[channel.PACKAGE_NAME] == body
    # Poison only the non-authoritative leftover, never the accepted package.
    leftovers[0].write_bytes(b"leftover is deliberately not a ZIP")
    status, repeated = _send(application, body)
    assert status == 200 and repeated["already_accepted"] is True, repeated
    assert repeated["sha"] == sha and repeated["verdict"] == first["verdict"], repeated
    assert _accepted_bytes(channel, sha) == saved
    assert leftovers[0].read_bytes() == b"leftover is deliberately not a ZIP"
    assert list(channel.inbox_dir().glob("incoming-*.zip")) == leftovers
    current = json.loads((channel.inbox_dir() / channel.CURRENT_NAME).read_text(encoding="utf-8"))
    assert current["sha"] == sha and current["repeat"] is True
    assert [row["repeat"] for row in channel.receipts_tail(2)] == [False, True]
    _assert_source(package)


def test_cleanup_permission_error_does_not_mask_actual_publication_exception(
        monkeypatch, handler, package):
    application, channel, _ = handler
    body = package[2]
    sha = hashlib.sha256(body).hexdigest()
    # A real OS publication failure after the actual gate/reader, not a judge
    # replacement: os.replace cannot replace this directory with its ZIP file.
    blocked_destination = channel.inbox_dir() / sha / channel.PACKAGE_NAME
    blocked_destination.mkdir(parents=True)
    with pytest.raises(OSError) as ordinary:
        _send(application, body)
    assert "package.zip" in str(ordinary.value), ordinary.value
    assert not list(channel.inbox_dir().glob("incoming-*.zip"))
    with monkeypatch.context() as fault:
        leftovers, secondary = _lock_cleanup(fault, channel)
        with pytest.raises(OSError) as actual:
            _send(application, body)
    assert actual.value is not secondary, "Temporary-file cleanup hid the real publication exception"
    assert type(actual.value) is type(ordinary.value)
    assert actual.value.errno == ordinary.value.errno
    assert "package.zip" in str(actual.value), actual.value
    assert "isolated incoming cleanup denied" not in str(actual.value)
    assert blocked_destination.is_dir()
    assert not (blocked_destination.parent / channel.VERDICT_NAME).exists()
    assert not (channel.inbox_dir() / channel.CURRENT_NAME).exists()
    assert len(leftovers) == 1 and leftovers[0].read_bytes() == body
    _assert_source(package)
