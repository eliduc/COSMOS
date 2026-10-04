"""The «Прогоны» Panel dashboard streams all-tenant data over a websocket;
the admin gate must cover that socket, not only the HTTP route (audit
2026-07-15)."""
import asyncio

import pytest

from webapp import app as app_module
from webapp import users


class _Recorder:
    def __init__(self, connect=True):
        self._connect = connect
        self.sent = []

    async def receive(self):
        return {"type": "websocket.connect"}

    async def send(self, message):
        self.sent.append(message)


def _run(coro):
    return asyncio.run(coro)


def _guard():
    downstream_called = {"v": False}

    async def downstream(scope, receive, send):
        downstream_called["v"] = True

    guard = app_module.RunsDashboardWSGuard(downstream)
    return guard, downstream_called


def test_ws_guard_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(app_module.auth, "authenticate_request",
                        lambda conn: ({"email": "u@x", "role": "user"}, "ok"))
    guard, called = _guard()
    rec = _Recorder()
    scope = {"type": "websocket", "path": "/runs-dashboard/ws", "headers": []}
    _run(guard(scope, rec.receive, rec.send))
    assert not called["v"]                       # never reached the dashboard
    assert any(m.get("type") == "websocket.close" for m in rec.sent)


def test_ws_guard_rejects_unauthenticated(monkeypatch):
    monkeypatch.setattr(app_module.auth, "authenticate_request",
                        lambda conn: (None, "unauthenticated"))
    guard, called = _guard()
    rec = _Recorder()
    scope = {"type": "websocket", "path": "/runs-dashboard/ws", "headers": []}
    _run(guard(scope, rec.receive, rec.send))
    assert not called["v"]
    assert any(m.get("type") == "websocket.close" for m in rec.sent)


def test_ws_guard_allows_admin(monkeypatch):
    monkeypatch.setattr(app_module.auth, "authenticate_request",
                        lambda conn: ({"email": "a@x",
                                       "role": users.ROLE_ADMIN}, "ok"))
    guard, called = _guard()
    rec = _Recorder()
    scope = {"type": "websocket", "path": "/runs-dashboard/ws", "headers": []}
    _run(guard(scope, rec.receive, rec.send))
    assert called["v"]                           # passed through to Panel
    assert not rec.sent                          # no close frame


def test_ws_guard_ignores_other_paths(monkeypatch):
    called_auth = {"v": False}

    def _auth(conn):
        called_auth["v"] = True
        return (None, "x")

    monkeypatch.setattr(app_module.auth, "authenticate_request", _auth)
    guard, called = _guard()
    rec = _Recorder()
    scope = {"type": "websocket", "path": "/api/ws", "headers": []}
    _run(guard(scope, rec.receive, rec.send))
    assert called["v"]                           # unrelated ws passes through
    assert not called_auth["v"]                  # guard didn't even auth it


def test_ws_guard_passes_http_through(monkeypatch):
    guard, called = _guard()
    rec = _Recorder()
    scope = {"type": "http", "path": "/runs-dashboard", "headers": []}
    _run(guard(scope, rec.receive, rec.send))
    assert called["v"]                           # http gated elsewhere
