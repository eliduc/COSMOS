"""Activity journal (webapp/activity_log.py) + its HTTP surface: the nav
beacon, the access-mode logout beacon and the admin-only journal view.

The journal files are redirected to a tmp dir — the real webapp/config is
never touched. HTTP is exercised in dev auth mode (same pattern as
test_users_admin). The per-email nav throttle is disabled by the fixtures
(set to 0) except in the test that specifically exercises it."""
import importlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from _stand_identity import ADMIN_EMAIL

REPO_ROOT = Path(__file__).resolve().parents[1]


def _reset(mod, tmp_path, throttle=0.0):
    mod.ACTIVITY_PATH = tmp_path / "activity.jsonl"
    mod._auth_count = None
    mod._nav_count = None
    mod._nav_last = {}
    mod._seq = 0
    mod.NAV_MIN_INTERVAL_S = throttle


@pytest.fixture()
def act(tmp_path):
    from webapp import activity_log
    _reset(activity_log, tmp_path)
    return activity_log


# ------------------------------------------------------------------ unit

def test_record_and_recent_roundtrip(act):
    act.record("User@Example.COM", "login", "access")
    act.record("user@example.com", "nav", "board")
    act.record("user@example.com", "logout", "access")
    rows = act.recent()
    assert [r["event"] for r in rows] == ["logout", "nav", "login"]  # newest first
    assert rows[0]["email"] == "user@example.com"                    # lowered
    assert rows[1]["detail"] == "board"
    assert all(r["ts"] for r in rows)


def test_auth_and_nav_go_to_separate_files(act):
    act.record("a@x", "login", "access")
    act.record("a@x", "nav", "board")
    auth = act.ACTIVITY_PATH.read_text(encoding="utf-8")
    nav = act._nav_path().read_text(encoding="utf-8")
    assert "login" in auth and "nav" not in auth
    assert "nav" in nav and "login" not in nav


def test_filters(act):
    act.record("a@x", "login", "access")
    act.record("b@x", "nav", "demo")
    act.record("a@x", "nav", "board")
    assert [r["email"] for r in act.recent(email="a@x")] == ["a@x", "a@x"]
    assert [r["detail"] for r in act.recent(event="nav")] == ["board", "demo"]
    assert act.recent(email="a@x", event="login")[0]["detail"] == "access"
    assert act.recent(email="nobody@x") == []


def test_unknown_event_and_clamps(act):
    act.record("a@x", "hack", "boom")          # ignored: unknown event
    act.record("a@x", "nav", "x" * 500)        # detail clamped
    rows = act.recent()
    assert len(rows) == 1
    assert len(rows[0]["detail"]) == act.MAX_DETAIL


def test_corrupt_line_skipped(act):
    act.record("a@x", "nav", "board")
    with act.ACTIVITY_PATH.open("a", encoding="utf-8") as fh:
        fh.write("{broken json\n")             # in the AUTH file
    act.record("a@x", "nav", "demo")
    assert [r["detail"] for r in act.recent(event="nav")] == ["demo", "board"]


def test_nav_flood_never_evicts_auth_events(act, tmp_path):
    """The serious audit finding (2026-07-16): a nav flood must NOT be able
    to push another user's login/logout out of the journal."""
    _reset(act, tmp_path, throttle=0.0)
    act.NAV_MAX_LINES = 10
    act.NAV_TRIM_TO = 5
    act.record("victim@x", "login", "access")
    act.record("victim@x", "logout", "access")
    for i in range(50):                        # attacker floods nav
        act.record("attacker@x", "nav", "board")
    # nav file rotated to its tail, but the auth journal is untouched
    logins = act.recent(email="victim@x")
    assert {r["event"] for r in logins} == {"login", "logout"}
    nav_lines = act._nav_path().read_text(encoding="utf-8").splitlines()
    assert len(nav_lines) <= 10                 # nav rotated on its own


def test_throttle_drops_rapid_same_user_nav(act, tmp_path):
    _reset(act, tmp_path, throttle=60.0)       # 1 nav per email per minute
    act.record("a@x", "nav", "board")
    act.record("a@x", "nav", "demo")           # dropped (same user, <60 s)
    act.record("b@x", "nav", "board")          # different user — kept
    navs = act.recent(event="nav")
    assert [(r["email"], r["detail"]) for r in navs] == \
        [("b@x", "board"), ("a@x", "board")]


def test_nav_rotation_keeps_tail(act, tmp_path):
    _reset(act, tmp_path, throttle=0.0)
    act.NAV_MAX_LINES = 10
    act.NAV_TRIM_TO = 5
    for i in range(12):
        act.record("a@x", "nav", "board")
    lines = act._nav_path().read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 10


def test_recording_never_raises(act, monkeypatch):
    """A broken journal path must not break the request path."""
    bad = act.ACTIVITY_PATH / "cannot-be-a-dir" / "x.jsonl"
    act.ACTIVITY_PATH = bad
    act._auth_count = None
    bad.parent.parent.write_text("block", encoding="utf-8")
    act.record("a@x", "login", "access")       # must not raise


# ------------------------------------------------------------------ HTTP

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    from webapp import users, activity_log
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    _reset(activity_log, tmp_path)
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c, users, activity_log


def test_nav_beacon_records_and_admin_reads(client):
    c, _, _ = client
    assert c.post("/api/activity/nav", json={"tab": "board"}).status_code == 200
    assert c.post("/api/activity/nav", json={"tab": "demo"}).status_code == 200
    resp = c.get("/api/activity")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert [e["detail"] for e in events if e["event"] == "nav"] == \
        ["demo", "board"]
    assert events[0]["email"] == ADMIN_EMAIL


def test_nav_beacon_rejects_unknown_tab(client):
    c, _, _ = client
    # only known leaf tab ids are accepted
    assert c.post("/api/activity/nav", json={"tab": "board"}).status_code == 200
    assert c.post("/api/activity/nav", json={"tab": ""}).status_code == 400
    assert c.post("/api/activity/nav",
                  json={"tab": "боард"}).status_code == 400     # Cyrillic
    assert c.post("/api/activity/nav",
                  json={"tab": "constructor"}).status_code == 400  # prototype
    assert c.post("/api/activity/nav",
                  json={"tab": "nope"}).status_code == 400       # unknown ascii
    assert c.post("/api/activity/nav", json={"tab": 7}).status_code == 400


def test_logout_beacon_records(client):
    c, _, act = client
    assert c.post("/api/activity/logout").status_code == 200
    rows = act.recent(event="logout")
    assert rows and rows[0]["email"] == ADMIN_EMAIL
    assert rows[0]["detail"] == "dev"


def test_journal_is_admin_only(client):
    c, users, _ = client
    users.add("plain.users@example.com", "user", by=ADMIN_EMAIL)
    os.environ["CREW_DEV_USER"] = "plain.users@example.com"
    try:
        # the beacon is allowed for every authenticated user...
        assert c.post("/api/activity/nav",
                      json={"tab": "board"}).status_code == 200
        # ...but the journal view is not
        assert c.get("/api/activity").status_code == 403
    finally:
        os.environ["CREW_DEV_USER"] = ADMIN_EMAIL


def test_journal_filters_via_http(client):
    c, _, _ = client
    c.post("/api/activity/nav", json={"tab": "board"})
    c.post("/api/activity/logout")
    only_nav = c.get("/api/activity?event=nav").json()["events"]
    assert {e["event"] for e in only_nav} == {"nav"}
    none = c.get("/api/activity?email=nobody@x").json()["events"]
    assert none == []
