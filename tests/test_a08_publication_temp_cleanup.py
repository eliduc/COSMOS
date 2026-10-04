"""Publication temporaries must not accumulate after failure and recovery.

The three handler cases reuse the existing real publication/retry assertions
and add only the missing temporary-file invariant. JSON unit cases isolate
write/replace and secondary cleanup faults, not a judge or receipt verdict.
"""
import hashlib
from pathlib import Path

import pytest

from test_a08_receive_publication_agent import (
    clean_bridge, handler,
    test_receive_publication_interruption_preserves_previous_current_and_retry
    as _publication_recovery,
)
from test_a08_receive_cleanup_agent import _assert_source, _send, package
from webapp import task_channel as channel


@pytest.mark.parametrize("boundary,package_written,marker_written", [
    ("package", False, False),
    ("verdict", True, False),
    ("current", True, True),
])
def test_publication_recovery_removes_its_abandoned_temp(
        tmp_path, monkeypatch, handler, boundary, package_written, marker_written):
    _publication_recovery(tmp_path, monkeypatch, handler, boundary,
                          package_written, marker_written)
    assert not list(handler[1].inbox_dir().rglob("*.tmp")), (
        "Successful retry left a failed publication temporary behind")


@pytest.mark.parametrize("operation", ["write", "replace"])
@pytest.mark.parametrize("cleanup_denied", [False, True])
def test_json_publication_preserves_primary_error_and_previous_file(
        tmp_path, monkeypatch, operation, cleanup_denied, caplog):
    destination = tmp_path / "current.json"
    destination.write_text('{"previous": true}', encoding="utf-8")
    previous = destination.read_bytes()
    primary = OSError(28, "isolated publication failure")
    real_write, real_replace, real_unlink = (
        Path.write_text, channel.os.replace, Path.unlink)
    attempted = []

    def write(path, text, *args, **kwargs):
        if path.parent == tmp_path and path.name.endswith(".tmp"):
            attempted.append(path)
            if operation == "write":
                real_write(path, text[:5], *args, **kwargs)
                raise primary
        return real_write(path, text, *args, **kwargs)

    def replace(source, target):
        if Path(target) == destination and operation == "replace":
            raise primary
        return real_replace(source, target)

    def unlink(path, *args, **kwargs):
        if cleanup_denied and path in attempted:
            raise PermissionError(13, "isolated cleanup refusal")
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(Path, "write_text", write)
        fault.setattr(channel.os, "replace", replace)
        fault.setattr(Path, "unlink", unlink)
        with pytest.raises(OSError) as raised:
            channel._write_json_atomic(destination, {"new": True})
    assert raised.value is primary
    assert destination.read_bytes() == previous
    assert len(attempted) == 1
    assert attempted[0].exists() is cleanup_denied
    if cleanup_denied:
        assert "Could not remove" in caplog.text
        assert str(attempted[0]) in caplog.text
    channel._write_json_atomic(destination, {"new": True})
    assert '"new": true' in destination.read_text(encoding="utf-8")
    # A denied cleanup is observable, not a promise to remove a locked file.
    if cleanup_denied:
        attempted[0].unlink()
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("cleanup_denied", [False, True])
def test_partial_package_publication_keeps_primary_and_allows_real_retry(
        monkeypatch, handler, package, cleanup_denied, caplog):
    application, receiver, _ = handler
    body = package[2]
    sha = hashlib.sha256(body).hexdigest()
    directory = receiver.inbox_dir() / sha
    real_write, real_unlink = Path.write_bytes, Path.unlink
    primary = OSError(28, "isolated partial package publication")
    attempted = []

    def write(path, data):
        if (path.parent == directory and path.name.startswith("package.zip.")
                and path.suffix == ".tmp"):
            assert not attempted and data == body
            attempted.append(path)
            real_write(path, data[:64])
            raise primary
        return real_write(path, data)

    def unlink(path, *args, **kwargs):
        if cleanup_denied and path in attempted:
            raise PermissionError(13, "isolated package cleanup refusal")
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(Path, "write_bytes", write)
        fault.setattr(Path, "unlink", unlink)
        with pytest.raises(OSError) as raised:
            _send(application, body)
    assert raised.value is primary
    assert len(attempted) == 1
    assert attempted[0].exists() is cleanup_denied
    assert not (directory / receiver.PACKAGE_NAME).exists()
    assert not (directory / receiver.VERDICT_NAME).exists()
    assert not (receiver.inbox_dir() / receiver.CURRENT_NAME).exists()
    assert receiver.receipts_tail() == []
    if cleanup_denied:
        assert str(attempted[0]) in caplog.text
        attempted[0].unlink()
    status, receipt = _send(application, body)
    assert status == 200 and receipt["sha"] == sha
    assert receipt["already_accepted"] is False
    assert not any(c["launch_allowed"] for c in receipt["mg"].values())
    assert (directory / receiver.PACKAGE_NAME).read_bytes() == body
    assert len(receiver.receipts_tail()) == 1
    assert not list(receiver.inbox_dir().rglob("*.tmp"))
    assert not list(receiver.inbox_dir().glob("incoming-*.zip"))
    _assert_source(package)
