"""Access-request flow: a verified-but-unauthorized visitor registers a
request exactly once, the admin is notified through the Telegram module,
and the unauthorized page reflects the request status (COSMOS moderation).

Runs in dev auth mode: CREW_DEV_USER supplies the verified identity, so a
value missing from the catalog exercises exactly the same "forbidden"
path as Cloudflare Access mode (auth.authenticate_request sets
request.state.denied_email in both).
"""
import importlib
import os
import time

import pytest
from fastapi.testclient import TestClient

from webapp import access_requests
from _stand_identity import ADMIN_EMAIL


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    notified = []
    monkeypatch.setattr(app_module.telegram_bot, "notify_access_request",
                        notified.append)
    with TestClient(app_module.app) as client:
        yield client, users, notified


def _as(email):
    os.environ["CREW_DEV_USER"] = email


def _wait_for(predicate, timeout_s=5.0):
    """The Telegram notification runs in a daemon thread — poll briefly."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


# ------------------------------------------------------------------ store

def test_register_is_idempotent(env):
    created1, row1 = access_requests.register("Stranger@Example.COM")
    created2, row2 = access_requests.register("stranger@example.com")
    assert created1 is True and created2 is False
    assert row1["id"] == row2["id"]
    assert row2["status"] == access_requests.STATUS_PENDING
    assert row2["email"] == "stranger@example.com"


def test_set_status_and_question(env):
    _, row = access_requests.register("q@example.com")
    upd = access_requests.set_status(
        row["id"], access_requests.STATUS_INFO_REQUESTED,
        decided_by="telegram-admin", question="Какая авиакомпания?")
    assert upd["status"] == access_requests.STATUS_INFO_REQUESTED
    assert upd["question"] == "Какая авиакомпания?"
    assert upd["decided_by"] == "telegram-admin"
    with pytest.raises(access_requests.RequestNotFoundError):
        access_requests.set_status(99999, access_requests.STATUS_DENIED)


# ------------------------------------------------------------- middleware

def test_unknown_visitor_registers_and_sees_pending(env):
    client, users, notified = env
    _as("visitor@airline.aero")
    try:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/unauthorized.html"
        page = client.get("/unauthorized.html")
        assert page.status_code == 200
        assert "Запрос на доступ отправлен" in page.text
        assert "COSMOS" in page.text
        row = access_requests.get_by_email("visitor@airline.aero")
        assert row is not None and row["status"] == "pending"
        assert _wait_for(lambda: len(notified) == 1)
        assert notified[0]["email"] == "visitor@airline.aero"
        # Repeat visits do not create duplicates and do not re-notify.
        client.get("/unauthorized.html")
        client.get("/api/instances")
        assert len(notified) == 1
    finally:
        _as(ADMIN_EMAIL)


def test_api_403_carries_request_status(env):
    client, users, notified = env
    _as("apiuser@airline.aero")
    try:
        resp = client.get("/api/instances")
        assert resp.status_code == 403
        assert resp.json()["request_status"] == "pending"
    finally:
        _as(ADMIN_EMAIL)


def test_denied_and_info_requested_pages(env):
    client, users, notified = env
    _as("waiting@airline.aero")
    try:
        client.get("/unauthorized.html")
        row = access_requests.get_by_email("waiting@airline.aero")
        access_requests.set_status(
            row["id"], access_requests.STATUS_INFO_REQUESTED,
            question="Кто вы?")
        page = client.get("/unauthorized.html")
        assert "дополнительную информацию" in page.text
        access_requests.set_status(row["id"], access_requests.STATUS_DENIED)
        page = client.get("/unauthorized.html")
        assert "отказано" in page.text
    finally:
        _as(ADMIN_EMAIL)


def test_approved_visitor_gets_in(env):
    client, users, notified = env
    _as("newpilot@airline.aero")
    try:
        client.get("/unauthorized.html")            # registers the request
        row = access_requests.get_by_email("newpilot@airline.aero")
        # What the bot's "approve" handler does: catalog + request status.
        users.add("newpilot@airline.aero", users.ROLE_USER,
                  by="telegram-admin")
        access_requests.set_status(
            row["id"], access_requests.STATUS_APPROVED,
            decided_by="telegram-admin")
        me = client.get("/api/me")
        assert me.status_code == 200
        assert me.json()["email"] == "newpilot@airline.aero"
        assert me.json()["role"] == "user"
        # A signed-in user hitting the unauthorized page bounces home.
        resp = client.get("/unauthorized.html", follow_redirects=False)
        assert resp.status_code == 302 and resp.headers["location"] == "/"
    finally:
        _as(ADMIN_EMAIL)
