# -*- coding: utf-8 -*-
"""РВ-141 (слово владельца 04.10.2026): номер версии продукта мелким
шрифтом внизу экрана каталога — из одной настройки площадки
`CREW_PRODUCT_VERSION`, литерала в коде нет. Не задана — `null`, и
подписи нет."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_session_carries_product_version_from_one_setting(service, monkeypatch):
    monkeypatch.delenv("CREW_PRODUCT_VERSION", raising=False)
    assert service.get("/api/session").json()["product_version"] is None
    monkeypatch.setenv("CREW_PRODUCT_VERSION", "  ")
    assert service.get("/api/session").json()["product_version"] is None
    monkeypatch.setenv("CREW_PRODUCT_VERSION", " 0.0.0-test ")
    assert service.get("/api/session").json()["product_version"] \
        == "0.0.0-test"


def test_built_ui_shows_version_from_session_not_literal():
    """Собранный интерфейс (`catalog-ui/dist`, его раздаёт служба) читает
    номер из сессии и подписывает словами словаря; литерала номера нет."""
    dist = ROOT / "catalog-ui" / "dist"
    index = (dist / "index.html").read_text(encoding="utf-8")
    bundle = index.split('src="./assets/')[1].split('"')[0]
    js = (dist / "assets" / bundle).read_text(encoding="utf-8")
    assert "product_version" in js and "версия {version}" in js
    src = (ROOT / "catalog-ui" / "src" / "components" / "layout" / "Layout.tsx"
           ).read_text(encoding="utf-8")
    assert "1.4." not in src
