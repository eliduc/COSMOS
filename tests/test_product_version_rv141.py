"""РВ-141: версия продукта внизу интерфейса — номер из одной настройки
площадки (`CREW_PRODUCT_VERSION` пусковика), не литерал в коде.

`/api/me` отдаёт `product_version`; без настройки — None (подписи нет).
Страница несёт место подписи, скрипт заполняет его из `/api/me`.
Пользовательская БД и журнал аудита — во временной папке."""
import importlib
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from _stand_identity import ADMIN_EMAIL

STATIC = Path(__file__).resolve().parents[1] / "webapp" / "static"


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
        yield c


def test_version_from_launcher_setting(client, monkeypatch):
    monkeypatch.setenv("CREW_PRODUCT_VERSION", " 1.4.10 ")
    assert client.get("/api/me").json()["product_version"] == "1.4.10"


def test_no_setting_no_version(client, monkeypatch):
    monkeypatch.delenv("CREW_PRODUCT_VERSION", raising=False)
    assert client.get("/api/me").json()["product_version"] is None
    monkeypatch.setenv("CREW_PRODUCT_VERSION", "   ")
    assert client.get("/api/me").json()["product_version"] is None


def test_page_has_slot_and_no_literal_number():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'id="product-version"' in html
    assert "product_version" in js
    # Номер живёт в настройке площадки, не в коде страницы и скрипта.
    for text in (html, js):
        assert not re.search(r"product-version[^\n]*\d+\.\d+\.\d+", text)
