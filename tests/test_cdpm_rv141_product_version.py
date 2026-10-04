# -*- coding: utf-8 -*-
"""РВ-141 (слово владельца 04.10.2026): номер версии продукта мелким
шрифтом внизу интерфейса панели — из одной настройки площадки
`CREW_PRODUCT_VERSION`, литерала в коде нет. Не задана — `null`, и
подписи нет (а не выдуманный номер)."""
from pathlib import Path

from test_cdpm_webui import client  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def test_settings_carry_product_version_from_one_setting(client, monkeypatch):
    monkeypatch.delenv("CREW_PRODUCT_VERSION", raising=False)
    assert client.get("/api/settings").json()["product_version"] is None
    monkeypatch.setenv("CREW_PRODUCT_VERSION", "  ")
    assert client.get("/api/settings").json()["product_version"] is None
    monkeypatch.setenv("CREW_PRODUCT_VERSION", " 0.0.0-test ")
    assert client.get("/api/settings").json()["product_version"] \
        == "0.0.0-test"


def test_built_ui_shows_version_from_settings_not_literal():
    """Собранный интерфейс (`cdpm-ui/dist`, его раздаёт панель) читает
    номер из настроек и подписывает «версия …»; номера литералом нет."""
    index = (ROOT / "cdpm-ui" / "dist" / "index.html").read_text(encoding="utf-8")
    bundle = index.split('src="./assets/')[1].split('"')[0]
    js = (ROOT / "cdpm-ui" / "dist" / "assets" / bundle).read_text(encoding="utf-8")
    assert "product_version" in js and "версия ${" in js
    src = (ROOT / "cdpm-ui" / "src" / "components" / "layout" / "Layout.tsx"
           ).read_text(encoding="utf-8")
    assert "1.4." not in src
