"""Onboarding surface: read-only demo endpoints, tour flag, note codes.

The demo endpoints replay canonical final-report artifacts (nothing is
computed on request) and must work for ANY authenticated user — including
a fresh one with an empty workspace. The tour flag lives in the user
catalog with a lazy ALTER TABLE migration for pre-tour databases; note
codes let the frontend translate the API's advisory notes.

The user DB and admin audit log are redirected to a tmp dir — the real
webapp/config is never touched. Exercised in dev auth mode.
"""
import importlib
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient
from _stand_identity import ADMIN_EMAIL


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    from webapp import users, admin_api
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(admin_api, "AUDIT_LOG_PATH", tmp_path / "audit.log")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c, users, app_module


# ------------------------------------------------------------ demo endpoints

def test_demo_pipeline_shape(client):
    c, _, _ = client
    resp = c.get("/api/demo/pipeline")
    assert resp.status_code == 200
    body = resp.json()
    assert body["instance"]["id"] == "gerad-1"
    assert body["input"]["flights_total"] == 1013
    assert body["validation"]["ok"] is True
    assert body["stage1"]["ip_objective"] == 116589
    assert body["stage1"]["lp_bound_valid"] is True
    assert body["gate"]["result_status"] == "OK"
    roster = body["results"]["roster"]
    assert roster["total_rows"] > 1000
    assert len(roster["rows"]) > 0
    assert roster["columns"][0] == "employee"
    artifacts = body["results"]["artifacts"]
    assert isinstance(artifacts, list) and artifacts


def test_demo_comparison_shape(client):
    c, _, _ = client
    resp = c.get("/api/demo/comparison")
    assert resp.status_code == 200
    body = resp.json()
    gerad = body["gerad"]
    assert [e["inst"] for e in gerad] == list(range(1, 8))
    for entry in gerad:
        assert isinstance(entry["gencol"]["gap_pct"], (int, float))
        rostering = entry["rostering"]
        assert rostering["two_stage"] is not None
        assert rostering["direct"] is not None
        assert "unassigned_pct" in rostering["two_stage"]
        assert "unassigned_pct" in rostering["direct"]
    assert body["spp"]["ort"]["optimal"] == 29
    assert body["spp"]["tf"]["feasible"] == 17
    assert body["scale"]["a320"]["bp"]["status"] == "optimal"
    assert body["scale"]["b727"]["baselines_failed"] == 3


def test_demo_visible_to_plain_user(client):
    # Onboarding is the point: a fresh plain user with an empty workspace
    # must still see both demo views.
    c, users, _ = client
    users.add("plain@example.com", "user", by=ADMIN_EMAIL)
    os.environ["CREW_DEV_USER"] = "plain@example.com"
    assert c.get("/api/demo/pipeline").status_code == 200
    assert c.get("/api/demo/comparison").status_code == 200
    os.environ["CREW_DEV_USER"] = ADMIN_EMAIL


# ----------------------------------------------------------------- tour flag

def test_tour_flag_lifecycle(client):
    c, _, _ = client
    assert c.get("/api/me").json()["tour_seen"] is False
    resp = c.post("/api/me/tour-seen")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "tour_seen": True}
    assert c.get("/api/me").json()["tour_seen"] is True


def test_tour_column_migration(tmp_path, monkeypatch):
    """Catalogs created before the tour existed lack tour_seen_at; the
    ALTER TABLE migration in users._connect() adds it on first open."""
    from webapp import users
    db_path = tmp_path / "old-users.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE users (
            email         TEXT PRIMARY KEY,
            role          TEXT NOT NULL CHECK (role IN ('admin', 'user')),
            status        TEXT NOT NULL
                          CHECK (status IN ('active', 'revoked')),
            created_at    TEXT NOT NULL,
            created_by    TEXT NOT NULL,
            last_login_at TEXT,
            login_count   INTEGER NOT NULL DEFAULT 0 CHECK (login_count >= 0),
            jobs_run      INTEGER NOT NULL DEFAULT 0 CHECK (jobs_run >= 0),
            uploads       INTEGER NOT NULL DEFAULT 0 CHECK (uploads >= 0)
        );
    """)
    conn.execute(
        "INSERT INTO users (email, role, status, created_at, created_by,"
        " login_count, jobs_run, uploads)"
        " VALUES ('old@example.com', 'admin', 'active',"
        " '2026-01-01T00:00:00Z', 'bootstrap', 0, 0, 0)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(users, "USERS_DB_PATH", db_path)
    row = users.get_user("old@example.com")
    assert row is not None
    assert row["tour_seen_at"] is None
    users.mark_tour_seen("old@example.com")
    assert users.get_user("old@example.com")["tour_seen_at"] is not None


# ---------------------------------------------------------------- note codes

def test_job_note_code(client, monkeypatch):
    # gerad-1 is seed data: the response must carry the translatable
    # note_code next to the legacy Russian note. start_job is stubbed so
    # no real pipeline subprocess is launched.
    c, _, app_module = client
    monkeypatch.setattr(app_module.jobs, "start_job",
                        lambda *a, **k: "job-test-1")
    resp = c.post("/api/jobs",
                  json={"type": "validate", "instance_id": "gerad-1"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["job_id"] == "job-test-1"
    assert body["note_code"] == "seed_overwrite"
    assert body["note"]  # the human-readable note stays for compatibility

    cfg = c.get("/api/airline-config")
    assert cfg.status_code == 200
    assert cfg.json()["note_code"] == "rules_env"


def test_airline_config_put_rejects_int32_overflow(client):
    # Ревью пакета 2026-07-19 (P1-12): главный писатель Яруса 1 принимал
    # значение > Int32 и отравлял конфиг (все джобы падали до отката).
    # Невалидные значения отвергаются ДО записи — реальный конфиг не трогаем.
    c, _, _app = client
    resp = c.put("/api/airline-config",
                 json={"parameters": {"min_rest_min": 3_000_000_000}})
    assert resp.status_code == 422
    assert "32-bit" in resp.text
    # Огромное целое из JSON (без float-конверсии) — контрактный 422, не 500.
    resp2 = c.put("/api/airline-config",
                  json={"parameters": {"min_rest_min": 10**400}})
    assert resp2.status_code == 422
