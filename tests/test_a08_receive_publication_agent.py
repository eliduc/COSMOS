"""Actual receiver publication boundaries and recovery, without a live service.

Each case receives a real healthy 1.20 package, then interrupts publication of
a different real 1.21 package at one filesystem replace. Real gates, reader,
readiness and the receipt marker stay in place. This is proof-only coverage,
not a new product defect, protocol activation or external audit.
"""
import hashlib
import json
from pathlib import Path

import pytest

from test_a08_receive_handler_agent import (
    _body, _complete, _request, clean_bridge, handler, real_soft_package,
)


def _send(application, body, revision):
    request, reads = _request(application, body, revision=revision)
    response = _complete(application.api_task_package_receive(request))
    assert reads == [True]
    return response.status_code, _body(response)


def _files(directory):
    return {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}


@pytest.mark.parametrize("boundary,package_written,marker_written", [
    ("package", False, False),
    ("verdict", True, False),
    ("current", True, True),
])
def test_receive_publication_interruption_preserves_previous_current_and_retry(
        tmp_path, monkeypatch, handler, boundary, package_written, marker_written):
    application, channel, _ = handler
    old = real_soft_package(tmp_path / "old", monkeypatch, "1.20")
    old_body = old[-2]
    old_sha = hashlib.sha256(old_body).hexdigest()
    status, old_receipt = _send(application, old_body, "1.20")
    assert status == 200 and old_receipt["verdict"] in {"OK", "MISMATCH"}
    assert not any(c["launch_allowed"] for c in old_receipt["mg"].values())
    inbox = channel.inbox_dir()
    old_files = _files(inbox / old_sha)
    current_path = inbox / channel.CURRENT_NAME
    current_before = current_path.read_bytes()

    new = real_soft_package(tmp_path / "new", monkeypatch, "1.21")
    new_body = new[-2]
    new_sha = hashlib.sha256(new_body).hexdigest()
    assert old_sha != new_sha
    new_dir = inbox / new_sha
    targets = {"package": new_dir / channel.PACKAGE_NAME,
               "verdict": new_dir / channel.VERDICT_NAME,
               "current": current_path}
    target = targets[boundary]
    actual_replace = channel.os.replace
    interrupted = []

    def interrupt_replace(source, destination):
        if Path(destination) == target:
            assert not interrupted, "Exactly one publication operation is interrupted"
            assert current_path.read_bytes() == current_before
            assert _files(inbox / old_sha) == old_files
            interrupted.append(Path(source))
            raise PermissionError(13, "isolated receiver publication boundary", str(target))
        return actual_replace(source, destination)

    with monkeypatch.context() as fault:
        fault.setattr(channel.os, "replace", interrupt_replace)
        with pytest.raises(PermissionError, match="isolated receiver publication boundary"):
            _send(application, new_body, "1.21")
    assert len(interrupted) == 1
    assert current_path.read_bytes() == current_before
    assert _files(inbox / old_sha) == old_files
    assert (new_dir / channel.PACKAGE_NAME).is_file() is package_written
    assert (new_dir / channel.VERDICT_NAME).is_file() is marker_written
    assert not list(inbox.glob("incoming-*.zip"))
    assert [r["sha"] for r in channel.receipts_tail(10)] == [old_sha]
    partial_marker = ((new_dir / channel.VERDICT_NAME).read_bytes()
                      if marker_written else None)

    # Retry uses the real marker/idempotency branch, not a supplied verdict.
    status, retried = _send(application, new_body, "1.21")
    assert status == 200 and retried["verdict"] in {"OK", "MISMATCH"}, retried
    assert retried["sha"] == new_sha
    assert retried["already_accepted"] is marker_written
    assert not any(c["launch_allowed"] for c in retried["mg"].values())
    assert (new_dir / channel.PACKAGE_NAME).read_bytes() == new_body
    if marker_written:
        assert (new_dir / channel.VERDICT_NAME).read_bytes() == partial_marker
    current = json.loads(current_path.read_text(encoding="utf-8"))
    assert current["sha"] == new_sha
    assert Path(current["path"]) == new_dir / channel.PACKAGE_NAME
    assert _files(inbox / old_sha) == old_files
    assert [r["sha"] for r in channel.receipts_tail(10)] == [old_sha, new_sha]
    assert channel.receipts_tail(1)[0]["repeat"] is marker_written
    assert not list(inbox.glob("incoming-*.zip"))
    for parts in (old, new):
        assert parts[5].read_bytes() == parts[6]
        assert parts[-1].read_bytes() == parts[-2]
