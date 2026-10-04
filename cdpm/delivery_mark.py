"""Local delivery evidence bound to the currently published package bytes.

This checks the saved accepted-brief shape and local artifact identity, not
another package/receiver gate. Old senders discarded remote SHA/identity;
that lost evidence cannot be reconstructed here. Reading never rewrites
history. ZIP and mark publication are not a single transaction: an interrupted
publication must be projected as unconfirmed.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from cdpm import channel, file_replace


def _unconfirmed(reason: str) -> dict:
    # Do not carry a previous package's SHA, verdict, mg or cosmos_run into
    # the current result. The complete historical receipt remains on disk.
    return {"sent": False, "detail": "текущая доставка не подтверждена: " + reason}


def read_current(run_dir: Path) -> dict:
    """Return a matching mark, or explicit uncertainty without stale results."""
    directory = Path(run_dir) / "channel"
    package = directory / "task-package.zip"
    try:
        mark = channel._loads_receipt(file_replace.read_bytes_shared(
            directory / "delivery.json"))
    except FileNotFoundError:
        return (_unconfirmed("квитанция текущего ZIP отсутствует")
                if package.exists() else {})
    except (OSError, ValueError):
        return _unconfirmed("квитанция не читается")
    if not isinstance(mark, dict) or type(mark.get("sent")) is not bool:
        return _unconfirmed("квитанция повреждена")
    try:
        actual_sha = hashlib.sha256(file_replace.read_bytes_shared(package)).hexdigest()
    except OSError:
        return _unconfirmed("текущий ZIP отсутствует или не читается")
    if mark.get("sha") != actual_sha:
        return _unconfirmed("квитанция от другого ZIP")
    if mark["sent"] is True:
        # Validate only the surviving brief projection. Its SHA is local;
        # absent remote identities are NOT inferred from today's ZIP/headers.
        problem = channel._receipt_problem(mark, actual_sha, {})
        if problem:
            return _unconfirmed("сохранённая квитанция: " + problem)
    return mark


def write_current(run_dir: Path, mark: dict) -> None:
    """Publish one complete mark atomically; preserve old bytes on failure."""
    directory = Path(run_dir) / "channel"
    body = json.dumps(mark, ensure_ascii=False, indent=1)
    directory.mkdir(parents=True, exist_ok=True)
    staging = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".delivery-", suffix=".json",
                dir=directory, delete=False) as temporary:
            staging = Path(temporary.name)
        staging.write_text(body, encoding="utf-8")
        file_replace.replace_with_retry(staging, directory / "delivery.json")
    finally:
        if staging is not None:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                # A unique abandoned staging file is never read as evidence.
                # Cleanup must not hide the primary write/replace exception.
                pass
