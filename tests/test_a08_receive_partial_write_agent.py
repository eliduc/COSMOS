"""A failed incoming ZIP write must clean its partial staging bytes.

The package and subsequent retry use real gates/reader/receipt persistence.
Only writing this isolated inbox's incoming file is interrupted, after actual
partial bytes have been written. No execution readiness or service is claimed.
"""
import hashlib
from pathlib import Path

import pytest

from test_a08_receive_cleanup_agent import (
    _assert_source, _send, clean_bridge, handler, package,
)


def test_partial_incoming_write_is_cleaned_and_real_retry_can_receive(
        monkeypatch, handler, package):
    application, channel, _ = handler
    body = package[2]
    sha = hashlib.sha256(body).hexdigest()
    inbox = channel.inbox_dir()
    actual_write = Path.write_bytes
    interrupted = []
    primary = OSError(28, "isolated partial incoming write")

    def partial_write(path, data):
        if path.parent == inbox and path.name.startswith("incoming-"):
            assert data == body and not interrupted
            actual_write(path, data[:64])
            interrupted.append(path)
            raise primary
        return actual_write(path, data)

    with monkeypatch.context() as fault:
        fault.setattr(Path, "write_bytes", partial_write)
        with pytest.raises(OSError) as raised:
            _send(application, body)
    assert raised.value is primary
    assert len(interrupted) == 1
    assert not interrupted[0].exists(), "Failed incoming write leaked partial staging ZIP"
    assert not (inbox / sha).exists()
    assert not (inbox / channel.CURRENT_NAME).exists()
    assert channel.receipts_tail() == []
    _assert_source(package)

    status, receipt = _send(application, body)
    assert status == 200 and receipt["already_accepted"] is False, receipt
    assert receipt["sha"] == sha
    assert not any(c["launch_allowed"] for c in receipt["mg"].values())
    assert (inbox / sha / channel.PACKAGE_NAME).read_bytes() == body
    assert not list(inbox.glob("incoming-*.zip"))
    assert len(channel.receipts_tail()) == 1
    _assert_source(package)
