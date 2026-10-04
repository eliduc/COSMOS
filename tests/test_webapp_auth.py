"""Authentication tests for the pipeline web app (webapp.auth + middleware).

All credential/session files live in a tmp directory: webapp.auth exposes
AUTH_CONFIG_PATH / SESSIONS_PATH as module variables exactly so tests can
monkeypatch them and never touch the real webapp/config.

No skips: a missing/broken backend is a red result.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

TEST_TOKEN = "pytest-fixed-token-abc123"


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


@pytest.fixture()
def auth_mod(tmp_path, monkeypatch):
    """webapp.auth with config redirected to tmp and a known token seeded."""
    try:
        from webapp import auth
    except Exception as exc:  # noqa: BLE001 — report the real import error
        pytest.fail(f"webapp.auth is not importable: {exc!r}")
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps({
        "token_sha256": _sha256(TEST_TOKEN),
        "created_at": "2026-07-13T00:00:00Z",
    }), encoding="utf-8")
    monkeypatch.setattr(auth, "AUTH_CONFIG_PATH", auth_file)
    monkeypatch.setattr(auth, "SESSIONS_PATH", tmp_path / "sessions.json")
    return auth


@pytest.fixture()
def client(auth_mod):
    try:
        from webapp.app import app
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"webapp.app is not importable: {exc!r}")
    from fastapi.testclient import TestClient
    with TestClient(app) as c:
        yield c


def _login(client, token):
    return client.post("/api/login", json={"token": token})


# --- middleware: everything requires a session ------------------------------

def test_api_without_cookie_is_401(client):
    resp = client.get("/api/instances")
    assert resp.status_code == 401


def test_all_api_routes_locked_without_cookie(client):
    # A sample across the API surface: nothing but /api/login is public.
    for method, path in [("get", "/api/jobs"), ("get", "/api/schema"),
                         ("get", "/api/airline-config"),
                         ("post", "/api/import/ssim"),
                         ("post", "/api/import/meridian"),
                         ("get", "/api/results/gerad-1"),
                         ("get", "/api/artifacts/gerad_stage1_results.json")]:
        resp = getattr(client, method)(path)
        assert resp.status_code == 401, f"{method} {path}: {resp.status_code}"


def test_page_without_cookie_redirects_to_login(client):
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


def test_login_page_and_stylesheet_are_public(client):
    page = client.get("/login")
    assert page.status_code == 200
    assert "login-form" in page.text
    css = client.get("/style.css")
    assert css.status_code == 200


# --- login ------------------------------------------------------------------

def test_login_wrong_token_401(client):
    resp = _login(client, "definitely-not-the-token")
    assert resp.status_code == 401
    # And no session cookie was handed out.
    assert client.get("/api/instances").status_code == 401


def test_login_missing_token_field_401(client):
    resp = client.post("/api/login", json={})
    assert resp.status_code == 401


def test_login_right_token_gives_working_session(client, auth_mod):
    resp = _login(client, TEST_TOKEN)
    assert resp.status_code == 200
    set_cookie = resp.headers.get("set-cookie", "")
    assert auth_mod.COOKIE_NAME in set_cookie
    assert "httponly" in set_cookie.lower()
    # Subsequent API calls pass (TestClient keeps the cookie jar).
    assert client.get("/api/instances").status_code == 200
    assert client.get("/api/airline-config").status_code == 200
    # Session persisted server-side, not just in the cookie.
    sessions = json.loads(
        auth_mod.SESSIONS_PATH.read_text(encoding="utf-8"))["sessions"]
    assert len(sessions) == 1


def test_cookie_value_is_not_the_credential(client, auth_mod):
    resp = _login(client, TEST_TOKEN)
    assert resp.status_code == 200
    sid = client.cookies.get(auth_mod.COOKIE_NAME)
    assert sid
    assert sid != TEST_TOKEN
    assert sid != _sha256(TEST_TOKEN)


# --- logout -----------------------------------------------------------------

def test_logout_kills_the_session(client, auth_mod):
    assert _login(client, TEST_TOKEN).status_code == 200
    sid = client.cookies.get(auth_mod.COOKIE_NAME)
    assert client.get("/api/instances").status_code == 200
    assert client.post("/api/logout").status_code == 200
    # The session id is revoked server-side: even replaying the old cookie
    # value must fail, not just clearing the browser cookie.
    client.cookies.set(auth_mod.COOKIE_NAME, sid)
    assert client.get("/api/instances").status_code == 401


# --- first-start token generation --------------------------------------------

def test_first_start_generates_and_prints_token_once(tmp_path, monkeypatch,
                                                     capsys):
    from webapp import auth
    auth_file = tmp_path / "fresh" / "auth.json"
    monkeypatch.setattr(auth, "AUTH_CONFIG_PATH", auth_file)
    monkeypatch.setattr(auth, "SESSIONS_PATH", tmp_path / "fresh" / "sessions.json")

    auth.ensure_auth_config()
    out = capsys.readouterr().out
    assert "ACCESS TOKEN" in out
    # The plaintext token is on its own line; 24 urlsafe chars.
    token_lines = [ln.strip() for ln in out.splitlines()
                   if ln.strip() and " " not in ln.strip()
                   and "=" not in ln.strip()]
    assert len(token_lines) == 1, f"expected exactly one token line: {out!r}"
    token = token_lines[0]
    assert len(token) == 24

    stored = json.loads(auth_file.read_text(encoding="utf-8"))
    assert stored["token_sha256"] == _sha256(token)
    assert token not in auth_file.read_text(encoding="utf-8")  # never plaintext
    assert auth.verify_token(token) is True
    assert auth.verify_token("wrong") is False

    # Second start: no-op, token NOT printed again.
    auth.ensure_auth_config()
    assert "ACCESS TOKEN" not in capsys.readouterr().out


def test_malformed_auth_config_fails_loudly(tmp_path, monkeypatch):
    from webapp import auth
    bad = tmp_path / "auth.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(auth, "AUTH_CONFIG_PATH", bad)
    with pytest.raises(RuntimeError):
        auth.verify_token("anything")


# --- session expiry -----------------------------------------------------------

def test_sessions_older_than_30_days_are_pruned(client, auth_mod):
    assert _login(client, TEST_TOKEN).status_code == 200
    assert client.get("/api/instances").status_code == 200
    # Backdate the stored session past the TTL.
    data = json.loads(auth_mod.SESSIONS_PATH.read_text(encoding="utf-8"))
    old = (datetime.now(timezone.utc)
           - timedelta(days=auth_mod.SESSION_TTL_DAYS, minutes=1))
    for meta in data["sessions"].values():
        meta["created_at"] = old.strftime("%Y-%m-%dT%H:%M:%SZ")
    auth_mod.SESSIONS_PATH.write_text(json.dumps(data), encoding="utf-8")
    assert client.get("/api/instances").status_code == 401
