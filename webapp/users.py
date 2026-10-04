"""SQLite user catalog for the pipeline web app (webapp/config/users.db).

The app's OWN authorization layer on top of Cloudflare Access: Access
proves mailbox control, this catalog decides whether that mailbox may use
the app and with which role. One row per email:

    users(email PK lowercase, role admin|user, status active|revoked,
          created_at, created_by, last_login_at, login_count,
          jobs_run, uploads, tour_seen_at)

First-start admins come from the CREW_SUPER_ADMIN environment variable
(comma-separated addresses; empty/unset means NO seeded admins) and are
seeded exactly once, when the database file is first created.
Protections: the LAST active admin can never be deleted, demoted or
revoked (otherwise nobody could manage the catalog).

Thread-safety follows the reference implementation: every operation opens
its own connection (SQLite connections must not cross threads), mutations
run inside BEGIN IMMEDIATE transactions, and a busy timeout handles
concurrent writers. USERS_DB_PATH is a module variable on purpose — tests
monkeypatch it to a tmp directory instead of touching the real catalog.

House rules: loud failures — malformed emails, unknown users and
last-admin violations raise typed exceptions, never silently no-op.
"""
from __future__ import annotations

import os
import re
import sqlite3
import unicodedata
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .registry import REPO_ROOT

CONFIG_DIR = REPO_ROOT / "webapp" / "config"
USERS_DB_PATH: Path = CONFIG_DIR / "users.db"

ROLE_ADMIN = "admin"
ROLE_USER = "user"

# Domain allow-list (2026-07-16). An Access-authenticated identity that
# matches one of these but has NO catalog row yet is auto-provisioned as
# an active user with the given role on first login (see authorize()).
# An explicit revoke always wins — only a MISSING row is provisioned, never
# a revoked one — so an admin can still eject a domain member permanently.
# Kept in code (not a gitignored config) on purpose: a DOMAIN policy should
# deploy atomically to all three contours and live in the reviewed git
# history, never drift between machines. Personal ADDRESSES are deployment
# data, not policy — they come from the environment (see the env parsers
# below DEFAULT_ADMIN_EMAILS / ALLOWLIST_EMAILS), never from code.
ALLOWLIST_DOMAINS = {
    "ramax.ru": ROLE_ADMIN,
    "r-labs.ru": ROLE_ADMIN,
    "integrotechnologies.ru": ROLE_ADMIN,
}
ALLOWLIST_BY = "allow-list"
ROLES = (ROLE_ADMIN, ROLE_USER)
STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"
STATUSES = (STATUS_ACTIVE, STATUS_REVOKED)
STAT_FIELDS = ("jobs_run", "uploads")

_BUSY_TIMEOUT_MS = 5_000
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

_LOCAL_PART = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}")
_DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    email         TEXT PRIMARY KEY,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'user')),
    status        TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
    created_at    TEXT NOT NULL,
    created_by    TEXT NOT NULL,
    last_login_at TEXT,
    login_count   INTEGER NOT NULL DEFAULT 0 CHECK (login_count >= 0),
    jobs_run      INTEGER NOT NULL DEFAULT 0 CHECK (jobs_run >= 0),
    uploads       INTEGER NOT NULL DEFAULT 0 CHECK (uploads >= 0),
    tour_seen_at  TEXT
);
"""


class UsersError(RuntimeError):
    """Base class for expected user-catalog failures."""


class UserNotFoundError(UsersError):
    """The requested catalog user does not exist."""


class UserExistsError(UsersError):
    """The email is already present in the catalog."""


class LastAdminError(UsersError):
    """The mutation would remove/disable the final active administrator."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime(_TS_FMT)


def normalize_email(value: object) -> str:
    """Canonical lowercase form of a conventional email; raises ValueError.

    Deliberately conservative (reference access_control.py grammar): one
    local part, one FQDN-ish domain, no whitespace/control characters, so
    visually equivalent entries cannot create separate catalog rows.
    """
    if not isinstance(value, str):
        raise ValueError("email must be a string")
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized or len(normalized) > 254:
        raise ValueError("email must contain 1 to 254 characters")
    if any(ch.isspace() or ord(ch) < 33 or ord(ch) == 127
           for ch in normalized):
        raise ValueError("email contains whitespace or control characters")
    local, separator, domain = normalized.rpartition("@")
    if not separator or not local or not domain or "@" in local:
        raise ValueError("email must contain one local part and one domain")
    if _LOCAL_PART.fullmatch(local) is None:
        raise ValueError("email local part is not supported")
    if local.startswith(".") or local.endswith(".") or ".." in local:
        raise ValueError("email local part contains an invalid dot sequence")
    domain = domain.lower()
    if len(domain) > 253 or "." not in domain:
        raise ValueError("email domain must be a fully qualified domain")
    if any(_DOMAIN_LABEL.fullmatch(label) is None
           for label in domain.split(".")):
        raise ValueError("email domain contains an invalid label")
    return f"{local.lower()}@{domain}"


# --------------------------------------------- deployment data (environment)

def _admins_from_env() -> tuple[str, ...]:
    """Администраторы первого запуска — из CREW_SUPER_ADMIN (адреса через
    запятую; пишет установщик в пусковой файл). Значения — только из
    данных развёртывания: переменной нет или она пуста → пустой кортеж,
    админов-умолчаний НЕТ (вход возможен лишь по allow-list); кривой
    адрес → ValueError при старте, не тихий пропуск."""
    raw = os.environ.get("CREW_SUPER_ADMIN", "")
    return tuple(normalize_email(part)
                 for part in raw.split(",") if part.strip())


def _allowlist_emails_from_env() -> dict[str, str]:
    """Поимённый allow-list — из CREW_ALLOWLIST_EMAILS (записи
    «email:role» через запятую). Пусто/нет переменной → пустой словарь;
    запись без роли, с неизвестной ролью или с кривым адресом →
    ValueError при старте (кривые данные — отказ, не догадка)."""
    raw = os.environ.get("CREW_ALLOWLIST_EMAILS", "")
    result: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        email, sep, role = part.rpartition(":")
        if not sep or not email.strip():
            raise ValueError(
                f"CREW_ALLOWLIST_EMAILS: запись {part!r} не в формате "
                f"email:role")
        role = role.strip()
        if role not in (ROLE_ADMIN, ROLE_USER):
            raise ValueError(
                f"CREW_ALLOWLIST_EMAILS: роль {role!r} не из "
                f"{(ROLE_ADMIN, ROLE_USER)} (запись {part!r})")
        result[normalize_email(email.strip())] = role
    return result


DEFAULT_ADMIN_EMAILS = _admins_from_env()
ALLOWLIST_EMAILS = _allowlist_emails_from_env()


# ------------------------------------------------------------- connections

def _connect() -> sqlite3.Connection:
    """Open a fresh connection; create schema + seed defaults on first open."""
    USERS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(USERS_DB_PATH),
                           timeout=_BUSY_TIMEOUT_MS / 1000.0,
                           isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    fresh = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone() is None
    conn.executescript(_SCHEMA)
    # Migration for catalogs created before the onboarding tour existed.
    # Two threads may both see the column missing (this check is not in a
    # write transaction); the loser's ALTER fails with "duplicate column
    # name" — benign, swallow exactly that.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "tour_seen_at" not in cols:
        try:
            conn.execute("ALTER TABLE users ADD COLUMN tour_seen_at TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    if fresh:
        now = _now_iso()
        for email in DEFAULT_ADMIN_EMAILS:
            # OR IGNORE: two threads may both see a fresh database.
            conn.execute(
                "INSERT OR IGNORE INTO users"
                " (email, role, status, created_at, created_by)"
                " VALUES (?, ?, ?, ?, 'bootstrap')",
                (normalize_email(email), ROLE_ADMIN, STATUS_ACTIVE, now))
    return conn


@contextmanager
def _write_txn() -> Iterator[sqlite3.Connection]:
    with closing(_connect()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()


def ensure_db() -> None:
    """Create the catalog (and seed the default admins) if missing."""
    with closing(_connect()):
        pass


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "email": row["email"],
        "role": row["role"],
        "status": row["status"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "last_login_at": row["last_login_at"],
        "login_count": int(row["login_count"]),
        "jobs_run": int(row["jobs_run"]),
        "uploads": int(row["uploads"]),
        "tour_seen_at": row["tour_seen_at"],
    }


def _get_row(conn: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE email = ?",
                        (email,)).fetchone()


def _active_admin_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) AS n FROM users"
        " WHERE role = 'admin' AND status = 'active'").fetchone()["n"])


def _is_last_active_admin(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    return (row["role"] == ROLE_ADMIN and row["status"] == STATUS_ACTIVE
            and _active_admin_count(conn) <= 1)


# --------------------------------------------------------------- operations

def _allowlisted_role(canonical: str) -> str | None:
    """Role for an allow-listed identity (exact email first, then domain),
    or None. `canonical` is already normalize_email'd (lowercased)."""
    if canonical in ALLOWLIST_EMAILS:
        return ALLOWLIST_EMAILS[canonical]
    return ALLOWLIST_DOMAINS.get(canonical.rpartition("@")[2])


def authorize(email: str) -> dict[str, str] | None:
    """Identity check on every request: active catalog user or None.

    An identity that matches the domain/email allow-list but has NO catalog
    row yet is auto-provisioned as an active user on first login (a real
    row, so it shows in the admin catalog and record_login works). An
    explicit revoke is never overridden: only a MISSING row is provisioned,
    a revoked one still returns None."""
    canonical = normalize_email(email)
    with closing(_connect()) as conn:
        row = _get_row(conn, canonical)
    if row is not None:
        if row["status"] != STATUS_ACTIVE:
            return None                       # revoked beats the allow-list
        return {"email": row["email"], "role": row["role"]}
    # no row — auto-provision iff allow-listed
    role = _allowlisted_role(canonical)
    if role is None:
        return None
    try:
        created = add(canonical, role, by=ALLOWLIST_BY)
        return {"email": created["email"], "role": created["role"]}
    except UserExistsError:                    # raced a concurrent first login
        with closing(_connect()) as conn:
            row = _get_row(conn, canonical)
        if row is None or row["status"] != STATUS_ACTIVE:
            return None
        return {"email": row["email"], "role": row["role"]}


def get_user(email: str) -> dict[str, Any] | None:
    canonical = normalize_email(email)
    with closing(_connect()) as conn:
        row = _get_row(conn, canonical)
    return None if row is None else _row_to_dict(row)


def add(email: str, role: str, by: str) -> dict[str, Any]:
    """Add one active user; UserExistsError on duplicates (any status)."""
    canonical = normalize_email(email)
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")
    with _write_txn() as conn:
        if _get_row(conn, canonical) is not None:
            raise UserExistsError(f"user already exists: {canonical}")
        conn.execute(
            "INSERT INTO users (email, role, status, created_at, created_by)"
            " VALUES (?, ?, ?, ?, ?)",
            (canonical, role, STATUS_ACTIVE, _now_iso(), str(by)))
        return _row_to_dict(_get_row(conn, canonical))


def set_role(email: str, role: str) -> dict[str, Any]:
    """Change a user's role; demoting the last active admin is refused."""
    canonical = normalize_email(email)
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")
    with _write_txn() as conn:
        row = _get_row(conn, canonical)
        if row is None:
            raise UserNotFoundError(f"no such user: {canonical}")
        if role != ROLE_ADMIN and _is_last_active_admin(conn, row):
            raise LastAdminError(
                f"cannot demote {canonical}: last active admin")
        conn.execute("UPDATE users SET role = ? WHERE email = ?",
                     (role, canonical))
        return _row_to_dict(_get_row(conn, canonical))


def set_status(email: str, status: str) -> dict[str, Any]:
    """Activate/revoke a user; revoking the last active admin is refused."""
    canonical = normalize_email(email)
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    with _write_txn() as conn:
        row = _get_row(conn, canonical)
        if row is None:
            raise UserNotFoundError(f"no such user: {canonical}")
        if status == STATUS_REVOKED and _is_last_active_admin(conn, row):
            raise LastAdminError(
                f"cannot revoke {canonical}: last active admin")
        conn.execute("UPDATE users SET status = ? WHERE email = ?",
                     (status, canonical))
        return _row_to_dict(_get_row(conn, canonical))


def delete(email: str) -> None:
    """Delete a user; deleting the last active admin is refused."""
    canonical = normalize_email(email)
    with _write_txn() as conn:
        row = _get_row(conn, canonical)
        if row is None:
            raise UserNotFoundError(f"no such user: {canonical}")
        if _is_last_active_admin(conn, row):
            raise LastAdminError(
                f"cannot delete {canonical}: last active admin")
        conn.execute("DELETE FROM users WHERE email = ?", (canonical,))


def list_all() -> list[dict[str, Any]]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY email").fetchall()
    return [_row_to_dict(row) for row in rows]


def record_login(email: str) -> None:
    """Bump login_count and last_login_at for an existing user."""
    canonical = normalize_email(email)
    with _write_txn() as conn:
        cur = conn.execute(
            "UPDATE users SET login_count = login_count + 1,"
            " last_login_at = ? WHERE email = ?",
            (_now_iso(), canonical))
        if cur.rowcount == 0:
            raise UserNotFoundError(f"no such user: {canonical}")


def bump_stat(email: str, field: str) -> None:
    """Increment one whitelisted per-user counter (jobs_run | uploads)."""
    if field not in STAT_FIELDS:
        raise ValueError(f"field must be one of {STAT_FIELDS}, got {field!r}")
    canonical = normalize_email(email)
    with _write_txn() as conn:
        cur = conn.execute(
            f"UPDATE users SET {field} = {field} + 1 WHERE email = ?",
            (canonical,))
        if cur.rowcount == 0:
            raise UserNotFoundError(f"no such user: {canonical}")


def mark_tour_seen(email: str) -> None:
    """Record that the user finished (or skipped) the onboarding tour."""
    canonical = normalize_email(email)
    with _write_txn() as conn:
        cur = conn.execute(
            "UPDATE users SET tour_seen_at = ? WHERE email = ?",
            (_now_iso(), canonical))
        if cur.rowcount == 0:
            raise UserNotFoundError(f"no such user: {canonical}")


def stats_summary() -> dict[str, Any]:
    """Aggregate totals for the admin dashboard."""
    with closing(_connect()) as conn:
        totals = conn.execute(
            "SELECT COUNT(*) AS users_total,"
            " COALESCE(SUM(status = 'active'), 0) AS users_active,"
            " COALESCE(SUM(status = 'active' AND role = 'admin'), 0)"
            "   AS admins_active,"
            " COALESCE(SUM(login_count), 0) AS logins_total,"
            " COALESCE(SUM(jobs_run), 0) AS jobs_total,"
            " COALESCE(SUM(uploads), 0) AS uploads_total"
            " FROM users").fetchone()
        rows = conn.execute(
            "SELECT email, jobs_run, uploads FROM users ORDER BY email"
        ).fetchall()
    return {
        "users_total": int(totals["users_total"]),
        "users_active": int(totals["users_active"]),
        "admins_active": int(totals["admins_active"]),
        "logins_total": int(totals["logins_total"]),
        "jobs_total": int(totals["jobs_total"]),
        "uploads_total": int(totals["uploads_total"]),
        "jobs_by_user": {r["email"]: int(r["jobs_run"]) for r in rows},
        "uploads_by_user": {r["email"]: int(r["uploads"]) for r in rows},
    }
