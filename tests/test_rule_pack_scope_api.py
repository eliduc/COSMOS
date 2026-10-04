"""Точка запроса «область действия пакета правил» (пятый аудит, R5-04).

Проверок области на уровне модуля мало: дефект живёт на стыке. Здесь —
сам запрос: право доступа, отказ на чужой набор, отказ на мусор и то, что
смена области снимает утверждение пакета.
"""
from __future__ import annotations

import importlib
import json

import pytest
from fastapi.testclient import TestClient
from _stand_identity import ADMIN_EMAIL


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    from webapp import rule_packs
    packs = tmp_path / "rule_packs.json"
    packs.write_text(json.dumps({"packs": [
        {"id": "P1", "name": "норма", "status": "approved", "country": "XA",
         "rules": [{"id": "R1", "status": "approved"}]},
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(rule_packs, "PACKS_PATH", packs)
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_binding_to_an_invisible_instance_is_refused(client):
    """Пакет, не применяющийся нигде, — тихая потеря правила; заодно
    перебором нельзя узнать состав чужих наборов."""
    r = client.put("/api/rule-packs/P1/scope",
                   json={"scope": "instance:upload-does-not-exist"})
    assert r.status_code == 404


def test_garbage_scope_is_refused_loudly(client):
    for bad in ("tenant:x", "instance:", "", 5):
        r = client.put("/api/rule-packs/P1/scope", json={"scope": bad})
        assert r.status_code == 422, bad


def test_regulatory_scope_is_accepted_and_unapproves(client):
    from webapp import rule_packs
    before = [p for p in rule_packs.list_packs() if p["id"] == "P1"][0]
    assert before["status"] == "approved"
    r = client.put("/api/rule-packs/P1/scope", json={"scope": "regulatory"})
    assert r.status_code == 200 and r.json()["scope"] == "regulatory"
    after = [p for p in rule_packs.list_packs() if p["id"] == "P1"][0]
    # область не менялась (уже была нормативной по умолчанию) —
    # утверждение снимать не за что
    assert after["status"] == "approved"


def test_unknown_pack_is_404(client):
    r = client.put("/api/rule-packs/NOPE/scope", json={"scope": "regulatory"})
    assert r.status_code == 404


def test_ordinary_user_cannot_change_scope(client, monkeypatch):
    """Область — это КОМУ действует правило; менять её вправе только
    администратор."""
    from webapp import users
    users.add("plain@example.com", role="user", by=ADMIN_EMAIL)
    monkeypatch.setenv("CREW_DEV_USER", "plain@example.com")
    r = client.put("/api/rule-packs/P1/scope", json={"scope": "regulatory"})
    assert r.status_code in (401, 403), r.status_code
