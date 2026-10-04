"""API-гейты новых эндпоинтов формализации (аудит-находка «нет тестов на
/extract, delta-эндпоинты, roster-compare»): роли, владение, маппинг
ошибок. LLM/солвер не вызываются (или мокаются)."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from _stand_identity import ADMIN_EMAIL, ADMIN_SLUG, LEGACY_UPLOAD_ID

ADMIN = ADMIN_EMAIL
USER = "someone@example.com"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    from webapp import (users, registry, rule_packs, schedule_deltas,
                        raw_zone, llm_backend)
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(registry, "OWNERSHIP_PATH", tmp_path / "own.json")
    # P0-03: короткий grandfathered-слаг личности, как на реальном стенде.
    (tmp_path / "own.json").write_text(json.dumps(
        {LEGACY_UPLOAD_ID: ADMIN}), encoding="utf-8")
    monkeypatch.setattr(registry, "LLM_SCOPE_PATH", tmp_path / "scope.json")
    monkeypatch.setattr(rule_packs, "PACKS_PATH", tmp_path / "packs.json")
    monkeypatch.setattr(schedule_deltas, "JOURNAL_PATH",
                        tmp_path / "deltas.json")
    monkeypatch.setattr(raw_zone, "RAW_DIR", tmp_path / "raw")
    # extractor must never hit a real backend
    monkeypatch.setattr(llm_backend, "complete",
                        lambda *a, **k: {"ok": True, "text": "[]",
                                         "backend": k.get("backend"),
                                         "model": "m"})
    users.ensure_db()
    users.add(USER, "user", by=ADMIN)
    from webapp import app as app_module
    importlib.reload(app_module)
    # committed uploads land in the REAL data/uploads (payload_path must be
    # repo-relative for instance_payload_path); track and remove ours.
    updir = registry.UPLOADS_DIR / ADMIN_SLUG
    before = set(updir.glob("*.json")) if updir.exists() else set()
    try:
        with TestClient(app_module.app) as c:
            yield c, monkeypatch
    finally:
        if updir.exists():
            for p in updir.glob("*.json"):
                if p not in before:
                    p.unlink(missing_ok=True)


def _upload(c, name="delta-api"):
    """Commit a tiny upload instance via the import endpoints; returns id."""
    flights = ("flightnumber;origin;destination;departure;arrival\n"
               "SU100;SVO;LED;2026-03-01T08:00:00;2026-03-01T09:20:00\n"
               "SU101;LED;SVO;2026-03-02T10:30:00;2026-03-02T11:50:00\n")
    crew = "id,name,base,skills\nP1,One,SVO,Captain\n"
    from webapp import llm_backend
    # a canonical mapping plan so the heuristic isn't needed
    r = c.post("/api/import/analyze", json={
        "scope": "local-only",
        "files": [{"name": "f.csv", "text": flights},
                  {"name": "c.csv", "text": crew}]})
    assert r.status_code == 200, r.text
    tok = r.json()["token"]
    r2 = c.post("/api/import/commit", json={"token": tok, "name": name})
    assert r2.status_code == 200, r2.text
    return r2.json()["id"]


# --------------------------------------------------------- rule extractor

def test_extract_admin_only_and_maps_errors(client):
    c, monkeypatch = client
    pid = c.post("/api/rule-packs", json={"name": "P"}).json()["id"]
    # bad sensitivity -> 422
    r = c.post(f"/api/rule-packs/{pid}/extract",
               json={"text": "Статья 1. Отдых не менее 10 часов.",
                     "sensitivity": "secret", "document": "Док"})
    assert r.status_code == 422
    # confidential + cloud -> ScopeViolation -> 400
    r2 = c.post(f"/api/rule-packs/{pid}/extract",
                json={"text": "Статья 1. Отдых не менее 10 часов подряд.",
                      "sensitivity": "confidential", "document": "Договор",
                      "backend": "cloud"})
    assert r2.status_code == 400
    # valid confidential (local) -> 200, empty candidates from stub
    r3 = c.post(f"/api/rule-packs/{pid}/extract",
                json={"text": "Статья 1. Отдых не менее 10 часов подряд.",
                      "sensitivity": "confidential", "document": "Договор"})
    assert r3.status_code == 200 and r3.json()["added"] == 0
    # non-admin blocked
    monkeypatch.setenv("CREW_DEV_USER", USER)
    assert c.post(f"/api/rule-packs/{pid}/extract",
                  json={"text": "x" * 50, "sensitivity": "public",
                        "document": "Д"}).status_code == 403


# ---------------------------------------------------------------- deltas

def test_delta_endpoints_owner_gated_and_journaled(client):
    c, monkeypatch = client
    inst_id = _upload(c)
    # preview (no apply) does not journal
    r = c.post(f"/api/instances/{inst_id}/deltas/schedule",
               json={"text": "CNL\nSU100/01MAR26"})
    assert r.status_code == 200 and r.json()["applied"] is False
    assert c.get(f"/api/instances/{inst_id}/deltas").json()["items"] == []
    # apply journals
    r2 = c.post(f"/api/instances/{inst_id}/deltas/schedule",
                json={"text": "CNL\nSU100/01MAR26", "apply": True})
    assert r2.status_code == 200 and r2.json()["applied"] is True
    assert len(c.get(f"/api/instances/{inst_id}/deltas").json()["items"]) == 1
    # unparseable delta -> 422
    r3 = c.post(f"/api/instances/{inst_id}/deltas/schedule",
                json={"text": "GARBAGE\nnope"})
    assert r3.status_code == 422
    # crew delta present-field validation
    r4 = c.post(f"/api/instances/{inst_id}/deltas/crew", json={})
    assert r4.status_code == 422
    # foreign user cannot see the instance -> 404 (not 403)
    monkeypatch.setenv("CREW_DEV_USER", USER)
    assert c.get(f"/api/instances/{inst_id}/deltas").status_code == 404
    assert c.post(f"/api/instances/{inst_id}/deltas/schedule",
                  json={"text": "CNL\nSU100/01MAR26"}).status_code == 404


def test_delta_crew_size_limit(client):
    c, monkeypatch = client
    inst_id = _upload(c, name="delta-big")
    from webapp import schedule_deltas
    monkeypatch.setattr(schedule_deltas, "MAX_CREW_BYTES", 100)
    big = "id,name\n" + "\n".join(f"P{i},Name{i}" for i in range(200))
    r = c.post(f"/api/instances/{inst_id}/deltas/crew",
               json={"text": big})
    assert r.status_code == 413


# --------------------------------------------------------- roster compare

def test_roster_compare_owner_gated(client):
    c, monkeypatch = client
    inst_id = _upload(c, name="rcmp-api")
    # no stage-2 roster -> ours unavailable, historical empty, 200
    r = c.get(f"/api/roster-compare/{inst_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["ours"]["available"] is False
    act = c.get(f"/api/roster-compare/{inst_id}/act.md")
    assert act.status_code == 200
    assert "Диагностическое сравнение ростеров" in act.text   # H08 retitle
    # foreign instance -> 404
    monkeypatch.setenv("CREW_DEV_USER", USER)
    assert c.get(f"/api/roster-compare/{inst_id}").status_code == 404
    assert c.get(f"/api/roster-compare/nope/act.md").status_code == 404
