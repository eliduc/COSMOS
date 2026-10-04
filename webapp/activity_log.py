"""Activity journal: who logged in / out and which screens they visited.

Storage is TWO append-only JSONL files (both gitignored runtime stores):

  * webapp/config/activity.jsonl      — "login" / "logout" (durable, rare;
    generous rotation cap).
  * webapp/config/activity_nav.jsonl  — "nav" screen switches (high volume;
    its own, tighter rotation).

Splitting the stores is a SECURITY property, not a tidiness one: nav events
arrive from an endpoint open to every authenticated user, so a nav flood
must never be able to evict another user's login/logout history (audit
2026-07-16, serious). Two files + a per-email nav throttle make the
auth-event journal untouchable by nav volume.

One line per event:

    {"ts": "2026-07-16T22:41:03+00:00", "email": "user@x", "event": "nav",
     "detail": "board"}

House rules mirror the other stores: recording NEVER raises into the
request path, reads are defensive (a corrupt line is skipped), writes are
locked and each file is size-capped. Under pytest the DEFAULT paths are
inert (tests that want the journal monkeypatch ACTIVITY_PATH to a tmp dir,
which moves the derived nav path with it). ACTIVITY_PATH / the caps are
module variables on purpose — tests monkeypatch them.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .registry import REPO_ROOT

ACTIVITY_PATH: Path = REPO_ROOT / "webapp" / "config" / "activity.jsonl"
_DEFAULT_PATH: Path = ACTIVITY_PATH

EVENTS = ("login", "logout", "nav")
_AUTH_EVENTS = frozenset({"login", "logout"})
MAX_DETAIL = 64

# login/logout are rare — keep a long history; nav is chatty — cap tighter.
AUTH_MAX_LINES = 50000
AUTH_TRIM_TO = 25000
NAV_MAX_LINES = 20000
NAV_TRIM_TO = 10000

# Per-email server-side throttle for nav (mirrors the client dedup, but the
# server never trusts the client): at most one nav row per email per window.
NAV_MIN_INTERVAL_S = 1.0
_NAV_LAST_MAX = 10000              # cap the throttle map; clear if exceeded

_lock = threading.Lock()
_auth_count: int | None = None     # lazy; maintained under _lock
_nav_count: int | None = None
_nav_last: dict[str, float] = {}
_seq = 0                           # per-run tiebreaker for equal timestamps
                                   # (ts is second-precision; within one
                                   # second, and across the two files, seq
                                   # preserves true insertion order)

# Back-compat alias: the original single-file counter name some tests reset.
_line_count: int | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _nav_path() -> Path:
    """Nav store derived from ACTIVITY_PATH so a single monkeypatch (tests)
    or a single config move relocates BOTH files together."""
    return ACTIVITY_PATH.with_name(ACTIVITY_PATH.stem + "_nav"
                                   + ACTIVITY_PATH.suffix)


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def record(email: str, event: str, detail: str = "") -> None:
    """Append one event. Swallows every I/O error by design: the journal
    is an observer, never a participant, of the request path."""
    global _auth_count, _nav_count, _seq
    if event not in EVENTS:
        return
    # Test hygiene: suites that exercise /api/login through the real app
    # must not pollute the REAL journal (the deploy gate runs pytest on the
    # live checkouts). Only the untouched default path is guarded.
    if "PYTEST_CURRENT_TEST" in os.environ and ACTIVITY_PATH == _DEFAULT_PATH:
        return
    email = str(email or "")[:120].lower()
    try:
        with _lock:
            _seq += 1
            row = {"ts": _now(), "email": email, "event": event,
                   "detail": str(detail or "")[:MAX_DETAIL], "seq": _seq}
            line = json.dumps(row, ensure_ascii=False) + "\n"
            if event in _AUTH_EVENTS:
                path, is_nav = ACTIVITY_PATH, False
            else:
                # per-email throttle: drop nav bursts from the same user so a
                # flood cannot balloon the nav file (auth file is untouched
                # regardless — different file).
                now = time.monotonic()
                last = _nav_last.get(email)
                if last is not None and (now - last) < NAV_MIN_INTERVAL_S:
                    return
                if len(_nav_last) > _NAV_LAST_MAX:
                    _nav_last.clear()
                _nav_last[email] = now
                path, is_nav = _nav_path(), True
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            if is_nav:
                if _nav_count is None:
                    _nav_count = _count_lines(path)
                _nav_count += 1
                if _nav_count > NAV_MAX_LINES:
                    _nav_count = _rotate(path, NAV_TRIM_TO)
            else:
                if _auth_count is None:
                    _auth_count = _count_lines(path)
                _auth_count += 1
                if _auth_count > AUTH_MAX_LINES:
                    _auth_count = _rotate(path, AUTH_TRIM_TO)
    except OSError:
        pass


def _rotate(path: Path, trim_to: int) -> int:
    """Keep the newest trim_to lines of `path`. Runs under _lock; atomic
    replace with the Windows read-share retry used by the other stores.
    Returns the new line count."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines(True)
    except OSError:
        return 0
    tail = lines[-trim_to:]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("".join(tail), encoding="utf-8")
    for attempt in range(5):
        try:
            tmp.replace(path)
            break
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05)
    return len(tail)


def _read_rows(path: Path) -> list[dict]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def recent(limit: int = 200, email: str | None = None,
           event: str | None = None) -> list[dict]:
    """Newest-first slice of the merged journal (auth + nav files),
    optionally filtered by exact email (case-insensitive) and/or event
    kind. Corrupt lines skipped."""
    limit = max(1, min(int(limit), 1000))
    email_f = (email or "").strip().lower() or None
    event_f = event if event in EVENTS else None
    if event_f in _AUTH_EVENTS:
        rows = _read_rows(ACTIVITY_PATH)
    elif event_f == "nav":
        rows = _read_rows(_nav_path())
    else:
        rows = _read_rows(ACTIVITY_PATH) + _read_rows(_nav_path())
    # (ts, seq): ts orders across seconds/restarts, seq preserves true
    # insertion order within a second and across the two merged files.
    rows.sort(key=lambda r: (str(r.get("ts", "")),
                             r.get("seq", 0) if isinstance(r.get("seq"), int)
                             else 0),
              reverse=True)
    out: list[dict] = []
    for row in rows:
        if len(out) >= limit:
            break
        if email_f and str(row.get("email", "")).lower() != email_f:
            continue
        if event_f and row.get("event") != event_f:
            continue
        out.append({"ts": str(row.get("ts", "")),
                    "email": str(row.get("email", "")),
                    "event": str(row.get("event", "")),
                    "detail": str(row.get("detail", ""))})
    return out
