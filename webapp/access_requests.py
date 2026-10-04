"""Access-request queue for COSMOS (stored in the users.db SQLite file).

When a visitor passes Cloudflare's email OTP but is not in the app's own
user catalog, the middleware registers an access request here and (once,
on creation) notifies the admin through the Telegram bot. The admin's
decision — approve / deny / request more information — is recorded here
and mirrored to the catalog (users.add) by the bot handlers.

One row per email (UNIQUE): a repeat visit by the same address never
creates a second row and never re-notifies. Statuses:

    pending         registered, waiting for the admin's decision
    info_requested  the admin emailed the visitor a question
    approved        added to the catalog (kept for audit/history)
    denied          refused; the visitor sees the refusal page

Connections/transactions reuse the users.py helpers so the table lives in
the same database file and tests that monkeypatch users.USERS_DB_PATH
automatically redirect this store too. Loud failures, as everywhere.
"""
from __future__ import annotations

from typing import Any

from . import users

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"
STATUS_INFO_REQUESTED = "info_requested"
STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_DENIED,
            STATUS_INFO_REQUESTED)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS access_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL CHECK (status IN
                ('pending', 'approved', 'denied', 'info_requested')),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    question    TEXT,
    decided_by  TEXT
);
"""


class RequestNotFoundError(users.UsersError):
    """The referenced access request does not exist."""


def _ensure_table(conn) -> None:
    conn.executescript(_SCHEMA)


def _row_to_dict(row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "email": row["email"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "question": row["question"],
        "decided_by": row["decided_by"],
    }


def register(email: str) -> tuple[bool, dict[str, Any]]:
    """Idempotent registration of an access request for a VERIFIED email.

    Returns (created, row). created is True only when a brand-new row was
    inserted — that is the caller's cue to notify the admin exactly once.
    An existing row (any status) is returned as-is: a denied visitor does
    not get to re-trigger notifications by revisiting.
    """
    canonical = users.normalize_email(email)
    now = users._now_iso()
    with users._write_txn() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM access_requests WHERE email = ?",
            (canonical,)).fetchone()
        if row is not None:
            return False, _row_to_dict(row)
        conn.execute(
            "INSERT INTO access_requests"
            " (email, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (canonical, STATUS_PENDING, now, now))
        row = conn.execute(
            "SELECT * FROM access_requests WHERE email = ?",
            (canonical,)).fetchone()
        return True, _row_to_dict(row)


def get_by_id(req_id: int) -> dict[str, Any]:
    with users._write_txn() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM access_requests WHERE id = ?",
            (int(req_id),)).fetchone()
    if row is None:
        raise RequestNotFoundError(f"no such access request: id={req_id}")
    return _row_to_dict(row)


def get_by_email(email: str) -> dict[str, Any] | None:
    canonical = users.normalize_email(email)
    with users._write_txn() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM access_requests WHERE email = ?",
            (canonical,)).fetchone()
    return None if row is None else _row_to_dict(row)


def set_status(req_id: int, status: str, decided_by: str | None = None,
               question: str | None = None) -> dict[str, Any]:
    """Record the admin's decision (or question) on a request."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    with users._write_txn() as conn:
        _ensure_table(conn)
        row = conn.execute(
            "SELECT * FROM access_requests WHERE id = ?",
            (int(req_id),)).fetchone()
        if row is None:
            raise RequestNotFoundError(f"no such access request: id={req_id}")
        conn.execute(
            "UPDATE access_requests SET status = ?, updated_at = ?,"
            " decided_by = COALESCE(?, decided_by),"
            " question = COALESCE(?, question)"
            " WHERE id = ?",
            (status, users._now_iso(), decided_by, question, int(req_id)))
        row = conn.execute(
            "SELECT * FROM access_requests WHERE id = ?",
            (int(req_id),)).fetchone()
        return _row_to_dict(row)


def list_all() -> list[dict[str, Any]]:
    with users._write_txn() as conn:
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT * FROM access_requests ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]
