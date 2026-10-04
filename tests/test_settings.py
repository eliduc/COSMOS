"""Super-admin Settings: identity gate, config store, per-use-case model
routing, API-key handling (never echoed), live model discovery, and the
contour-preserving mask policy. (2026-07-18)"""
import importlib
import json

import pytest
from fastapi.testclient import TestClient
from _stand_identity import ADMIN_EMAIL

SUPER = ADMIN_EMAIL
OTHER = "someone@example.com"


@pytest.fixture()
def st(tmp_path, monkeypatch):
    """settings module with its two files redirected to a temp dir and its
    mtime cache reset."""
    from webapp import settings
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "app_settings.json")
    monkeypatch.setattr(settings, "API_KEY_PATH", tmp_path / "claude_api_key")
    monkeypatch.setattr(settings, "_cache", None)
    monkeypatch.setattr(settings, "_cache_mtime", None)
    monkeypatch.delenv("CREW_ANTHROPIC_API_KEY", raising=False)
    return settings


# ------------------------------------------------------------- identity gate

def test_super_admin_is_email_not_role(st):
    assert st.is_super_admin({"email": SUPER, "role": "admin"})
    assert st.is_super_admin({"email": SUPER.upper(), "role": "user"})
    # the token-mode operator (holds the deployment bearer) counts too
    assert st.is_super_admin({"email": "operator@token.local", "role": "admin"})
    # a mere admin is NOT the super admin
    assert not st.is_super_admin({"email": OTHER, "role": "admin"})
    assert not st.is_super_admin({"email": "", "role": "admin"})
    assert not st.is_super_admin(None)


def test_super_admin_env_override(tmp_path, monkeypatch):
    """Пакет «умолчания-в-данные» (2026-08-29): супер-админ — ПЕРВЫЙ адрес
    CREW_SUPER_ADMIN (список через запятую); умолчания в коде нет —
    пусто/нет переменной → None, супер-функций нет ни у кого, кроме
    оператора token-режима."""
    from webapp import settings
    monkeypatch.setenv("CREW_SUPER_ADMIN", "boss@corp.example")
    importlib.reload(settings)
    try:
        assert settings.is_super_admin({"email": "boss@corp.example"})
        assert not settings.is_super_admin({"email": SUPER})
        # список: супер-админ — первый адрес, остальные — нет
        monkeypatch.setenv("CREW_SUPER_ADMIN",
                           " Boss@Corp.example , second@corp.example ")
        importlib.reload(settings)
        assert settings.SUPER_ADMIN_EMAIL == "boss@corp.example"
        assert not settings.is_super_admin({"email": "second@corp.example"})
        # пусто → супер-админа нет вовсе; оператор token-режима остаётся
        monkeypatch.delenv("CREW_SUPER_ADMIN", raising=False)
        importlib.reload(settings)
        assert settings.SUPER_ADMIN_EMAIL is None
        assert not settings.is_super_admin({"email": SUPER})
        assert settings.is_super_admin({"email": "operator@token.local"})
    finally:
        # вернуть env СЕССИИ (задан в conftest) до финального reload —
        # иначе None утёк бы в остальные тесты модуля
        monkeypatch.undo()
        importlib.reload(settings)


# --------------------------------------------------------------- config store

def test_save_and_model_for_cloud_only(st):
    st.save({"models": {"ask_ai": "claude-opus-4-8", "coding": "claude-fable-5"}})
    # cloud backend => the chosen model; local backend => None (uses Ollama)
    assert st.model_for("ask_ai", "cloud") == "claude-opus-4-8"
    assert st.model_for("ask_ai", "local") is None
    assert st.model_for("text", "cloud") is None          # unset use case
    assert st.model_for(None, "cloud") is None
    assert st.cli_model_args("coding") == ["--model", "claude-fable-5"]
    assert st.cli_model_args("text") == []                # no model set here


def test_агент_реализации_без_настройки_идёт_на_opus(st):
    """Слово владельца 09.09.2026: агент реализации (назначение «coding»)
    = claude-opus-5 на всех контурах. Пустая настройка площадки — не
    «модель по умолчанию CLI» без имени, а умолчание кода; настройка
    площадки главнее умолчания; у прочих назначений умолчания нет."""
    assert st.cli_model("coding") == "claude-opus-5"
    assert st.cli_model_args("coding") == ["--model", "claude-opus-5"]
    assert st.cli_model("text") is None and st.cli_model_args("text") == []
    st.save({"models": {"coding": "claude-fable-5"}})
    assert st.cli_model("coding") == "claude-fable-5"
    st.save({"models": {"coding": None}})
    assert st.cli_model_args("coding") == ["--model", "claude-opus-5"]


def test_save_validation_rejects_junk(st):
    with pytest.raises(ValueError):
        st.save({"models": {"ask_ai": "bad model id!!"}})
    with pytest.raises(ValueError):
        st.save({"models": {"nope": "claude-opus-4-8"}})
    with pytest.raises(ValueError):
        st.save({"default_backend": "gpt"})
    with pytest.raises(ValueError):
        st.save({"mask_samples": "off"})                  # not an allowed mode


def test_save_clears_with_null(st):
    st.save({"models": {"ask_ai": "claude-opus-4-8"}})
    st.save({"models": {"ask_ai": None}})
    assert st.model_for("ask_ai", "cloud") is None


def test_backend_and_local_overrides(st):
    st.save({"default_backend": "cloud", "ollama_model": "qwen-x",
             "ollama_url": "http://127.0.0.1:11434"})
    assert st.get_default_backend() == "cloud"
    assert st.get_ollama_model() == "qwen-x"
    assert st.get_ollama_url() == "http://127.0.0.1:11434"


# --------------------------------------------------------------- api key I/O

def test_api_key_roundtrip_and_never_returns_raw(st):
    assert not st.has_api_key()
    assert st.api_key_source() == "none"
    st.set_api_key("sk-ant-api03-SECRETVALUE1234")
    assert st.has_api_key()
    assert st.api_key_source() == "file"
    assert st.get_api_key() == "sk-ant-api03-SECRETVALUE1234"
    # the hint never reveals the whole key
    hint = st.api_key_hint()
    assert hint == "…1234" and "SECRET" not in hint
    st.clear_api_key()
    assert not st.has_api_key()


def test_api_key_rejects_non_anthropic(st):
    with pytest.raises(ValueError):
        st.set_api_key("not-a-key")
    with pytest.raises(ValueError):
        st.set_api_key("")


def test_api_key_env_fallback(st, monkeypatch):
    monkeypatch.setenv("CREW_ANTHROPIC_API_KEY", "sk-ant-envfallback9999")
    assert st.has_api_key()
    assert st.api_key_source() == "env"
    assert st.get_api_key().endswith("9999")


# --------------------------------------------- llm_backend routing + discovery

def test_complete_cloud_routes_to_api_when_key_and_model(st, monkeypatch):
    from webapp import llm_backend
    st.set_api_key("sk-ant-api03-KEY")
    st.save({"models": {"ask_ai": "claude-opus-4-8"}})
    calls = {}
    monkeypatch.setattr(llm_backend, "_api_generate",
                        lambda p, **k: calls.update(api=k) or "API")
    monkeypatch.setattr(llm_backend, "_cli_generate",
                        lambda *a, **k: "CLI")
    res = llm_backend.complete("q", backend="cloud", usage="ask_ai")
    assert res["ok"] and res["text"] == "API"
    assert res["model"] == "claude-opus-4-8"
    assert calls["api"]["model"] == "claude-opus-4-8"


def test_complete_cloud_falls_back_to_cli_without_key(st, monkeypatch):
    from webapp import llm_backend
    st.save({"models": {"ask_ai": "claude-opus-4-8"}})   # model set, no key
    monkeypatch.setattr(llm_backend, "_cli_generate",
                        lambda prompt, timeout_s, **k: "CLI:" + str(k.get("model")))
    res = llm_backend.complete("q", backend="cloud", usage="ask_ai")
    # no key -> CLI path, but the chosen model is still passed as --model
    assert res["ok"] and res["text"] == "CLI:claude-opus-4-8"


def test_complete_local_ignores_usage_model(st, monkeypatch):
    from webapp import llm_backend
    st.save({"models": {"ask_ai": "claude-opus-4-8"}})
    seen = {}
    monkeypatch.setattr(llm_backend, "_ollama_generate",
                        lambda p, t, **k: seen.update(k) or "local")
    res = llm_backend.complete("q", backend="local", usage="ask_ai")
    assert res["ok"] and res["text"] == "local"
    assert "model" not in seen          # local never gets a Claude model


def test_list_models_filters_to_claude(st, monkeypatch):
    from webapp import llm_backend

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"data": [
                {"id": "claude-opus-4-8", "display_name": "Opus 4.8",
                 "created_at": "2026-05-01"},
                {"id": "claude-fable-5", "display_name": "Fable 5",
                 "created_at": "2026-06-01"},
                {"id": "gpt-4o", "display_name": "GPT", "created_at": "2026-01"},
            ]}

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    models = llm_backend.list_models("sk-ant-x")
    ids = [m["id"] for m in models]
    assert ids == ["claude-fable-5", "claude-opus-4-8"]   # newest first, no gpt


def test_mask_policy_keeps_cloud_masked(st):
    from webapp import data_import
    # cloud is masked under every policy (contour floor)
    for pol in (None, "cloud", "always"):
        if pol:
            st.save({"mask_samples": pol})
        assert data_import._mask_samples_for("cloud") is True
    # local side follows the policy
    st.save({"mask_samples": "always"})
    assert data_import._mask_samples_for("local") is True
    st.save({"mask_samples": "cloud"})
    assert data_import._mask_samples_for("local") is False


# ----------------------------------------------------- API gate (dev auth)

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", SUPER)
    from webapp import users, settings
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(settings, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(settings, "SETTINGS_PATH", tmp_path / "app_settings.json")
    monkeypatch.setattr(settings, "API_KEY_PATH", tmp_path / "claude_api_key")
    monkeypatch.setattr(settings, "_cache", None)
    monkeypatch.setattr(settings, "_cache_mtime", None)
    users.ensure_db()
    users.add(OTHER, "admin", by=SUPER)   # a real admin, but NOT the super admin
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c, monkeypatch


def test_super_admin_can_read_and_write_settings(client):
    c, _ = client
    r = c.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["is_super"] is True
    assert body["api_key"]["configured"] is False
    assert {u["key"] for u in body["usages"]} >= {"coding", "text", "ask_ai"}
    # write a model choice
    r = c.put("/api/settings", json={"models": {"ask_ai": "claude-opus-4-8"}})
    assert r.status_code == 200
    assert r.json()["models"]["ask_ai"] == "claude-opus-4-8"


def test_api_key_endpoint_never_echoes_the_key(client):
    c, _ = client
    r = c.post("/api/settings/api-key", json={"key": "sk-ant-api03-TOPSECRET42"})
    assert r.status_code == 200
    assert "TOPSECRET" not in r.text            # only status + hint returned
    assert r.json()["configured"] is True
    # GET never returns the raw key either
    assert "TOPSECRET" not in c.get("/api/settings").text
    r = c.delete("/api/settings/api-key")
    assert r.status_code == 200 and r.json()["configured"] is False


def test_api_key_endpoint_rejects_bad_key(client):
    c, _ = client
    r = c.post("/api/settings/api-key", json={"key": "nope"})
    assert r.status_code == 422


def test_non_super_admin_is_forbidden(client):
    c, monkeypatch = client
    # re-point the dev identity to a non-super admin and reload the app
    monkeypatch.setenv("CREW_DEV_USER", OTHER)
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c2:
        assert c2.get("/api/settings").status_code == 403
        assert c2.put("/api/settings", json={}).status_code == 403
        assert c2.get("/api/me").json().get("is_super") is False
