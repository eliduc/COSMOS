"""Authentication for the pipeline web app: three modes via CREW_AUTH_MODE.

  * "access" — production behind Cloudflare Access: identity is the email
    of a strictly re-verified Cf-Access-Jwt-Assertion / CF_Authorization
    JWT (see access_jwt.py), authorized against the app's own user
    catalog (users.py) on EVERY request. Verification results are cached
    in-process for 60 s by token hash. Default when CREW_ACCESS_AUD is set.
  * "token" — the legacy shared-credential login kept for the PROART LAN
    stage: one shared operator token; only its sha256 hash is stored in
    webapp/config/auth.json (created on first server start, plaintext
    printed EXACTLY once); POST /api/login exchanges it for a random
    session id in an HttpOnly cookie, persisted in sessions.json and
    pruned after SESSION_TTL_DAYS. Default when CREW_ACCESS_AUD is unset.
  * "dev" — tests only: identity comes from the CREW_DEV_USER env var
    (still authorized against the catalog); refuses to activate unless
    CREW_AUTH_MODE=dev is set explicitly.

AUTH_CONFIG_PATH / SESSIONS_PATH are module variables on purpose: tests
monkeypatch them to a tmp directory instead of touching the real config.

House rules: loud failures — an auth config that exists but cannot be
parsed raises instead of silently locking everyone out or (worse) letting
anyone in; an unknown CREW_AUTH_MODE raises instead of guessing.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .registry import REPO_ROOT

CONFIG_DIR = REPO_ROOT / "webapp" / "config"
AUTH_CONFIG_PATH: Path = CONFIG_DIR / "auth.json"
SESSIONS_PATH: Path = CONFIG_DIR / "sessions.json"

COOKIE_NAME = "crew_session"
SESSION_TTL_DAYS = 30
_TOKEN_BYTES = 18          # secrets.token_urlsafe(18) -> 24 urlsafe chars

_lock = threading.Lock()

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime(_TS_FMT)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ credential

def ensure_auth_config() -> None:
    """First-start credential bootstrap.

    If AUTH_CONFIG_PATH is missing: generate a random 24-char urlsafe
    token, store ONLY its sha256 hash + created_at, and print the
    plaintext token to the server console once (the operator reads it
    from the log). No-op when the file already exists.
    """
    if AUTH_CONFIG_PATH.exists():
        return
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    AUTH_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    AUTH_CONFIG_PATH.write_text(json.dumps({
        "token_sha256": _hash_token(token),
        "created_at": _iso(_now()),
    }, indent=1), encoding="utf-8")
    sep = "=" * 66
    print(sep, flush=True)
    print("  WEBAPP ACCESS TOKEN (shown once; only its sha256 is stored):",
          flush=True)
    print(f"      {token}", flush=True)
    print(f"  To reset: delete {AUTH_CONFIG_PATH} and restart the server.",
          flush=True)
    print(sep, flush=True)


def verify_token(token: str) -> bool:
    """Constant-time check of the presented token against the stored hash.

    Missing auth.json -> False (no credential exists, nothing can match).
    An EXISTING but unreadable/malformed auth.json is a config error and
    raises (loud failure), never a silent 401.
    """
    if not AUTH_CONFIG_PATH.exists():
        return False
    try:
        stored = json.loads(AUTH_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"auth config {AUTH_CONFIG_PATH} exists but is unreadable/"
            f"malformed: {exc}") from exc
    expected = stored.get("token_sha256") if isinstance(stored, dict) else None
    if not isinstance(expected, str) or not expected:
        raise RuntimeError(
            f"auth config {AUTH_CONFIG_PATH}: 'token_sha256' missing")
    return hmac.compare_digest(_hash_token(token), expected)


# -------------------------------------------------------------------- sessions

def _load_sessions() -> dict:
    """sessions.json -> {session_id: {"created_at": iso}}; absent file = {}."""
    try:
        raw = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    sessions = raw.get("sessions") if isinstance(raw, dict) else None
    return sessions if isinstance(sessions, dict) else {}


def _save_sessions(sessions: dict) -> None:
    SESSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS_PATH.write_text(json.dumps({"sessions": sessions}, indent=1),
                             encoding="utf-8")


def _prune(sessions: dict) -> dict:
    """Drop sessions older than SESSION_TTL_DAYS (and unparsable rows)."""
    cutoff = _now() - timedelta(days=SESSION_TTL_DAYS)
    kept: dict = {}
    for sid, meta in sessions.items():
        try:
            created = datetime.strptime(
                meta["created_at"], _TS_FMT).replace(tzinfo=timezone.utc)
        except (KeyError, TypeError, ValueError):
            continue
        if created >= cutoff:
            kept[sid] = meta
    return kept


def create_session() -> str:
    """Mint a random session id, persist it, return it (cookie value)."""
    sid = secrets.token_urlsafe(32)
    with _lock:
        sessions = _prune(_load_sessions())
        sessions[sid] = {"created_at": _iso(_now())}
        _save_sessions(sessions)
    return sid


def session_valid(sid: str | None) -> bool:
    if not sid:
        return False
    with _lock:
        return sid in _prune(_load_sessions())


def destroy_session(sid: str | None) -> None:
    if not sid:
        return
    with _lock:
        sessions = _prune(_load_sessions())
        sessions.pop(sid, None)
        _save_sessions(sessions)


# ------------------------------------------------------------------ auth modes

MODE_ACCESS = "access"
MODE_TOKEN = "token"
MODE_DEV = "dev"
_KNOWN_MODES = (MODE_ACCESS, MODE_TOKEN, MODE_DEV)

# In token mode the shared credential has no per-user identity; the single
# operator gets a synthetic admin identity so request.state.user is uniform.
TOKEN_MODE_IDENTITY = {"email": "operator@token.local", "role": "admin"}

# Access-mode JWT verification cache: sha256(token) -> (monotonic deadline,
# verified email). 60 s keeps per-request crypto+network cost near zero while
# catalog authorization still runs on EVERY request (revocation is instant).
JWT_VERIFY_TTL_S = 60.0
_JWT_CACHE_MAX = 1024

_access_lock = threading.Lock()
_verifier = None
_verifier_env: tuple[str, str] | None = None
_jwt_cache: dict[str, tuple[float, str]] = {}
# Token hashes whose successful verification was already counted as a login
# (value = the JWT exp as wall-clock seconds, used for pruning).
_login_seen: dict[str, float] = {}


def get_auth_mode() -> str:
    """Resolve the auth mode from the environment (evaluated per request).

    Explicit CREW_AUTH_MODE always wins (unknown value raises — loud).
    Without it: access mode when CREW_ACCESS_AUD is set, else token mode.
    Dev mode NEVER activates implicitly.
    """
    raw = os.environ.get("CREW_AUTH_MODE", "").strip().lower()
    if raw:
        if raw not in _KNOWN_MODES:
            raise RuntimeError(
                f"CREW_AUTH_MODE={raw!r} is not one of {_KNOWN_MODES}")
        return raw
    if os.environ.get("CREW_ACCESS_AUD", "").strip():
        return MODE_ACCESS
    return MODE_TOKEN


def get_verifier():
    """Process-wide AccessVerifier, rebuilt if the env config changes."""
    from . import access_jwt
    global _verifier, _verifier_env
    env = (os.environ.get("CREW_ACCESS_TEAM", "").strip(),
           os.environ.get("CREW_ACCESS_AUD", "").strip())
    with _access_lock:
        if _verifier is None or _verifier_env != env:
            _verifier = access_jwt.verifier_from_env()
            _verifier_env = env
        return _verifier


def _access_email(token: object) -> tuple[str | None, bool]:
    """Verify an Access JWT (with the 60 s cache); -> (email, first_login).

    first_login is True the first time this process successfully verifies
    this exact token — used to bump the catalog login counter once per
    Access session instead of once per request.
    """
    from . import access_jwt
    if (not isinstance(token, str) or not token
            or len(token) > access_jwt.MAX_JWT_LENGTH):
        return None, False
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = time.monotonic()
    with _access_lock:
        cached = _jwt_cache.get(token_hash)
    if cached is not None and cached[0] > now:
        return cached[1], False
    try:
        identity = get_verifier().verify(token)
    except access_jwt.AccessJwtError:
        return None, False
    wall_now = time.time()
    with _access_lock:
        if len(_jwt_cache) >= _JWT_CACHE_MAX:
            _jwt_cache.clear()
        _jwt_cache[token_hash] = (now + JWT_VERIFY_TTL_S, identity.email)
        for stale_hash, exp in list(_login_seen.items()):
            if exp < wall_now:
                del _login_seen[stale_hash]
        first_login = token_hash not in _login_seen
        # exp + skew: keep the marker for the token's whole validity.
        _login_seen[token_hash] = float(identity.expires_at) + 3600.0
    return identity.email, first_login


def authenticate_request(request) -> tuple[dict | None, str]:
    """Resolve the request identity for the current auth mode.

    Returns (user, reason): user is {"email", "role"} on success (reason
    "ok"); otherwise user is None and reason is "unauthenticated" (no/bad
    credential -> 401 for APIs) or "forbidden" (verified identity that the
    catalog does not authorize -> 403 for APIs).
    """
    from . import users
    mode = get_auth_mode()

    if mode == MODE_TOKEN:
        if session_valid(request.cookies.get(COOKIE_NAME)):
            return dict(TOKEN_MODE_IDENTITY), "ok"
        return None, "unauthenticated"

    if mode == MODE_DEV:
        dev_email = os.environ.get("CREW_DEV_USER", "").strip()
        if not dev_email:
            return None, "unauthenticated"
        user = users.authorize(dev_email)
        if user is None:
            # The verified-but-unauthorized email, for the access-request
            # flow (same contract as access mode below).
            request.state.denied_email = dev_email
            return None, "forbidden"
        return user, "ok"

    # access mode: the JWT travels in the CF_Authorization cookie (browser)
    # or the Cf-Access-Jwt-Assertion header (service/API clients).
    token = (request.headers.get("cf-access-jwt-assertion")
             or request.cookies.get("CF_Authorization"))
    if not token:
        return None, "unauthenticated"
    email, first_login = _access_email(token)
    if email is None:
        return None, "unauthenticated"
    user = users.authorize(email)
    if user is None:
        # STRICTLY verified email (Cloudflare OTP passed) that the catalog
        # does not authorize — exposed for the access-request flow so the
        # middleware can register the request and notify the admin.
        request.state.denied_email = email
        return None, "forbidden"
    if first_login:
        users.record_login(email)
        from . import activity_log
        activity_log.record(email, "login", "access")
    return user, "ok"


def access_logout_url() -> str:
    """App-local Cloudflare Access logout path (served by CF on the app
    hostname; clears the CF_Authorization cookie and the Access session)."""
    return "/cdn-cgi/access/logout"
