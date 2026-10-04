# -*- coding: utf-8 -*-
"""Ручка среды COSMOS `GET /api/run/environment` (договор с CDPM
11.09.2026, волна 15).

Зачем ручка. Голову записи о прогоне чеканит ХОЗЯИН записи — панель CDPM
либо раннер наборов, — и своих моделей COSMOS он не знает: до 11.09.2026
сертификатор и читатель ИДАК в голову не попадали вовсе (находка К5).
Ручка отдаёт ровно то, что COSMOS кладёт фрагментом `cosmos_environment`
карточки записи, но ДО объявления: хозяин спрашивает её перед чеканкой и
кладёт модели COSMOS в `models[]` словом `system: "cosmos"`.

Судится ровно это:

  * форма ответа — схема, площадка, допуск, код, каталог и модели по
    ролям; каждая строка модели называет систему словом словаря записи,
    а поля строки — поля `declaration.models[]`, чтобы хозяин клал их в
    голову как есть;
  * допуск моделей набора: `scope` вне словаря либо не назван —
    `local-only` (наружу ничего не уходит без явного допуска);
  * ручка ЧИТАЮЩАЯ: отвечает БЕЗ текущего пакета (при объявлении пакета
    в COSMOS ещё нет) и в хранилище ничего не пишет;
  * машинный вход: по токену канала без сессии — и fail-closed без него.

Литералов среды в файле нет (сторож tests/test_no_environment_literals):
каталог данных — `tmp_path`, личность — `_stand_identity.ADMIN_EMAIL`,
служб не поднимается.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from _stand_identity import ADMIN_EMAIL

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "tests"))

from vmp import run_record as rr                          # noqa: E402

ADMIN = ADMIN_EMAIL
TOKEN = "test-run-environment-token-2026"
ENVIRONMENT = "/api/run/environment"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    """Каталог данных и носители площадки — свои у каждого теста."""
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data-root"))
    monkeypatch.delenv("CREW_TASK_INBOX", raising=False)
    monkeypatch.delenv("CREW_TASK_PACKAGE", raising=False)
    monkeypatch.delenv("CREW_CHANNEL_TOKEN", raising=False)
    monkeypatch.delenv("CREW_CONTOUR", raising=False)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """TestClient в режиме разработчика (образец tests/test_run_gate)."""
    from webapp import users
    cfg = tmp_path_factory.mktemp("run-environment-auth")
    mp = pytest.MonkeyPatch()
    mp.setenv("CREW_AUTH_MODE", "dev")
    mp.setenv("CREW_DEV_USER", ADMIN)
    mp.delenv("CREW_ACCESS_AUD", raising=False)
    mp.setenv("COSMOS_SKIP_CONFIG_MIGRATION", "1")
    mp.setattr(users, "USERS_DB_PATH", cfg / "users.db")
    users.ensure_db()
    import importlib
    from webapp import app as app_module
    importlib.reload(app_module)
    from fastapi.testclient import TestClient
    try:
        with TestClient(app_module.app) as c:
            yield c
    finally:
        mp.undo()


def test_ручка_называет_среду_косомоса_полями_головы(client):
    """Ответ — схема, площадка, допуск, код, каталог и модели по ролям.
    Пакета не нужно: при объявлении его в COSMOS ещё нет."""
    from webapp import run_gate
    r = client.get(ENVIRONMENT)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["schema"] == run_gate.ENVIRONMENT_SCHEMA
    assert got["contour"] in rr.CONTOURS
    assert got["llm_scope"] == "local-only"
    assert set(got) == {"schema", "contour", "llm_scope", "code", "catalog",
                        "models"}
    assert set(got["catalog"]) >= {"name", "version", "fingerprint", "path"}
    assert "cosmos_commit" in got["code"]
    roles = {m["role"] for m in got["models"]}
    assert roles == {run_gate.ROLE_CERTIFIER, run_gate.ROLE_SOURCE_READER}


def test_строка_модели_ложится_в_голову_как_есть(client):
    """Поля строки — поля `declaration.models[]` словаря записи плюс
    слово системы: хозяин кладёт их в голову, ничего не переименовывая.
    Лишние слова причины (`words`) голова отбрасывает сама."""
    from webapp import run_gate
    rows = client.get(ENVIRONMENT).json()["models"]
    known = set(rr.NESTED_FIELDS["declaration.models[]"])
    for row in rows:
        assert row["system"] == rr.SYSTEM_COSMOS
        assert known >= (set(row) - {"words"}), sorted(row)
    d = rr.normalize_declaration({"models": rows})
    assert [m["role"] for m in d["models"]] == [r["role"] for r in rows]
    assert all(m["system"] == rr.SYSTEM_COSMOS for m in d["models"])
    assert all("words" not in m for m in d["models"])
    assert run_gate.environment_answer()["models"] == rows


def test_допуск_моделей_набора_читается_строго_по_словарю(client):
    """`scope` — допуск моделей НАБОРА: его знает хозяин записи. Вне
    словаря либо не назван — local-only, наружу ничего не уходит без
    явного допуска."""
    from webapp import run_gate
    assert run_gate.environment_scope(None) == "local-only"
    assert run_gate.environment_scope(" Any ") == "local-only"
    assert run_gate.environment_scope("any") == "any"
    assert client.get(ENVIRONMENT, params={"scope": "по-всякому"}
                      ).json()["llm_scope"] == "local-only"
    got = client.get(ENVIRONMENT, params={"scope": "any"}).json()
    assert got["llm_scope"] == "any"
    # Допуск выбирает сертификатора и читателя — роли остаются обе даже
    # тогда, когда модель назвать нельзя: пропажа роли читалась бы
    # «роль не объявлена».
    assert {m["role"] for m in got["models"]} == {
        run_gate.ROLE_CERTIFIER, run_gate.ROLE_SOURCE_READER}


def test_ручка_ничего_не_пишет(client, tmp_path, monkeypatch):
    """Ручка читающая: после ответа в каталоге данных не появляется ни
    одного нового файла — при объявлении писать ещё нечего."""
    root = Path(tmp_path) / "data-root"
    root.mkdir(parents=True, exist_ok=True)
    before = {p for p in root.rglob("*")}
    assert client.get(ENVIRONMENT).status_code == 200
    assert {p for p in root.rglob("*")} == before


def test_машинный_вход_по_токену_канала(client, monkeypatch):
    """Хозяин записи (панель площадки, раннер наборов) спрашивает среду
    без сессии человека — по токену канала; чужой токен и выключенный
    канал — 403 (fail-closed), как у соседних ручек записи."""
    from webapp import app as app_module
    assert app_module._machine_route("GET", ENVIRONMENT)
    monkeypatch.setenv("CREW_CHANNEL_TOKEN", TOKEN)
    assert client.get(ENVIRONMENT,
                      headers={"X-Channel-Token": TOKEN}).status_code == 200
    assert client.get(ENVIRONMENT,
                      headers={"X-Channel-Token": "wrong"}).status_code == 403
    monkeypatch.delenv("CREW_CHANNEL_TOKEN")
    assert client.get(ENVIRONMENT,
                      headers={"X-Channel-Token": TOKEN}).status_code == 403
