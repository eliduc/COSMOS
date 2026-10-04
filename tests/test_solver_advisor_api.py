"""API tests for the solver-advisor endpoints (advise, llm-scope, admin KB).

Dev auth as the primary admin; the KB config paths (skill dir, llm_scope
map) and the audit log are redirected to tmp so the real webapp/config is
never touched. The LLM is always stubbed — no subprocess/HTTP is spawned.
"""
import importlib

import pytest
from fastapi.testclient import TestClient
from _stand_identity import ADMIN_EMAIL


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    from webapp import admin_api, llm_backend, registry, solver_advisor, users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(admin_api, "AUDIT_LOG_PATH", tmp_path / "audit.log")
    monkeypatch.setattr(registry, "LLM_SCOPE_PATH", tmp_path / "llm_scope.json")
    d = tmp_path / "solver_advisor"
    monkeypatch.setattr(solver_advisor, "SKILL_DIR", d)
    monkeypatch.setattr(solver_advisor, "SKILL_PATH", d / "skill.md")
    monkeypatch.setattr(solver_advisor, "META_PATH", d / "meta.json")
    monkeypatch.setattr(solver_advisor, "HISTORY_DIR", d / "history")
    # never touch the real (multi-MB) seed payloads or a real backend
    monkeypatch.setattr(solver_advisor, "load_payload",
                        lambda path: {"airports": [{"code": "ATL"}],
                                      "employees": [{"homeAirport": "ATL"}],
                                      "flights": []})
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c, monkeypatch, llm_backend, solver_advisor


# ------------------------------------------------------------------ advise

def test_advise_returns_llm_recommendation(client):
    c, monkeypatch, llm_backend, _ = client
    reply = ('{"stage1":{"engine":"colgen","pricer":"duty","profile":"gerad",'
             '"notes":"n"},"stage2":{"engine":"TF","notes":""},"split":"seq",'
             '"params":{"mp":8},"warnings":["w"],"rationale":"r"}')
    monkeypatch.setattr(llm_backend, "complete",
                        lambda *a, **k: {"ok": True, "text": reply,
                                         "backend": k["backend"], "model": "m"})
    r = c.post("/api/instances/gerad-1/advise", json={"job_type": "complete"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "llm"
    assert body["recommendation"]["stage1"]["pricer"] == "duty"
    assert body["recommendation"]["params"] == {"mp": 8}
    # descriptor is non-PII (aggregates only)
    assert "n_airports" in body["descriptor"]


def test_advise_falls_back_on_llm_failure(client):
    c, monkeypatch, llm_backend, _ = client
    monkeypatch.setattr(llm_backend, "complete",
                        lambda *a, **k: {"ok": False, "error": "down",
                                         "backend": k["backend"], "model": "m"})
    r = c.post("/api/instances/gerad-1/advise", json={"job_type": "stage1"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "fallback" and body["llm_error"] == "down"
    assert body["recommendation"]["params"].get("seed") == 7


def test_advise_cloud_on_local_only_is_400(client):
    c, _monkeypatch, _llm, _ = client
    # mark the seed dataset local-only, then ask for cloud -> refused
    c.put("/api/instances/gerad-1/llm-scope", json={"scope": "local-only"})
    r = c.post("/api/instances/gerad-1/advise",
               json={"job_type": "complete", "backend": "cloud"})
    assert r.status_code == 400
    assert "local-only" in r.json()["detail"]


def test_advise_unknown_job_type_is_400(client):
    c, monkeypatch, llm_backend, _ = client
    monkeypatch.setattr(llm_backend, "complete",
                        lambda *a, **k: {"ok": False, "error": "x",
                                         "backend": "local", "model": "m"})
    r = c.post("/api/instances/gerad-1/advise", json={"job_type": "bogus"})
    assert r.status_code == 400


def test_advise_missing_instance_is_404(client):
    c, *_ = client
    r = c.post("/api/instances/upload-nope/advise", json={"job_type": "stage1"})
    assert r.status_code == 404


# --------------------------------------------------------------- llm-scope

def test_put_llm_scope_roundtrip(client):
    c, *_ = client
    r = c.put("/api/instances/gerad-1/llm-scope", json={"scope": "local-only"})
    assert r.status_code == 200 and r.json()["llm_scope"] == "local-only"
    inst = c.get("/api/instances/gerad-1").json()
    assert inst["llm_scope"] == "local-only"


def test_put_llm_scope_invalid_is_422(client):
    c, *_ = client
    r = c.put("/api/instances/gerad-1/llm-scope", json={"scope": "public"})
    assert r.status_code == 422


# ---------------------------------------------------- admin KB endpoints

def test_admin_skill_seed(client):
    c, *_ = client
    r = c.get("/api/solver-advisor/skill")
    assert r.status_code == 200
    body = r.json()
    assert body["meta"]["version"] == 0 and body["meta"]["source"] == "seed"


def test_admin_build_triggers_and_busy(client):
    c, monkeypatch, _llm, solver_advisor = client
    calls = {"n": 0}

    def fake_start(backend):
        calls["n"] += 1
        return {"running": True, "backend": backend, "already_running": False}

    monkeypatch.setattr(solver_advisor, "start_skill_build", fake_start)
    r = c.post("/api/solver-advisor/build", json={"backend": "local"})
    assert r.status_code == 200 and calls["n"] == 1

    monkeypatch.setattr(solver_advisor, "start_skill_build",
                        lambda backend: {"already_running": True})
    r2 = c.post("/api/solver-advisor/build", json={"backend": "cloud"})
    assert r2.status_code == 409


def test_admin_backends_probe(client):
    c, monkeypatch, llm_backend, _ = client
    monkeypatch.setattr(llm_backend, "available",
                        lambda: {"local": False, "cloud": True,
                                 "local_model": "llama3.1", "default": "local"})
    r = c.get("/api/solver-advisor/backends")
    assert r.status_code == 200 and r.json()["cloud"] is True
