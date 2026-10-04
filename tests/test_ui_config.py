# -*- coding: utf-8 -*-
"""Язык интерфейса по умолчанию: /ui-config.js.

Зачем. Выбор языка живёт в браузере, но при первом заходе браузеру не из
чего выбирать — прежде жёстко брался русский. Установщик спрашивает язык
первым вопросом и кладёт его в CREW_DEFAULT_LANG; приложение отдаёт его
страницам маленьким сценарием /ui-config.js, который подключается до
i18n.js. Страница входа публична — сценарий обязан отвечать и без
какой-либо личности.
"""
import re

import pytest
from starlette.testclient import TestClient

from webapp import app as app_module


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("COSMOS_SKIP_CONFIG_MIGRATION", "1")
    with TestClient(app_module.app) as c:
        yield c


def _lang_of(body: str) -> str:
    m = re.search(r'window\.CREW_DEFAULT_LANG = "([a-z]{2})";', body)
    assert m, "сценарий не несёт назначения языка: %r" % body[:120]
    return m.group(1)


def test_default_lang_follows_environment(client, monkeypatch):
    monkeypatch.setenv("CREW_DEFAULT_LANG", "uz")
    r = client.get("/ui-config.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert _lang_of(r.text) == "uz"


def test_default_lang_without_setting_is_russian(client, monkeypatch):
    """Существующие контуры ничего не задают — их умолчание не меняется."""
    monkeypatch.delenv("CREW_DEFAULT_LANG", raising=False)
    r = client.get("/ui-config.js")
    assert r.status_code == 200
    assert _lang_of(r.text) == "ru"


def test_unknown_lang_falls_back_to_russian(client, monkeypatch):
    """Язык — не та настройка, из-за которой приложение вправе сломаться:
    мусор в переменной тихо даёт прежнее умолчание."""
    monkeypatch.setenv("CREW_DEFAULT_LANG", "tlh")
    r = client.get("/ui-config.js")
    assert _lang_of(r.text) == "ru"


def test_ui_config_is_public(client, monkeypatch):
    """Сценарий нужен странице входа ДО аутентификации."""
    assert "/ui-config.js" in app_module.PUBLIC_PATHS


def test_every_ui_lang_is_a_real_i18n_lang():
    """Перечень языков точки — зеркало LANGS из i18n.js: разъехавшиеся
    списки означали бы язык, который нельзя назначить (или наоборот)."""
    js = (app_module.STATIC_DIR / "i18n.js").read_text(encoding="utf-8")
    m = re.search(r'var LANGS = \[([^\]]+)\]', js)
    assert m, "в i18n.js не найден перечень LANGS"
    js_langs = set(re.findall(r'"([a-z]{2})"', m.group(1)))
    assert set(app_module._UI_LANGS) == js_langs


def test_pages_load_ui_config_before_i18n():
    """Сценарий обязан подключаться ДО i18n.js — иначе умолчание опоздает."""
    index = (app_module.STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert index.find("ui-config.js") != -1, "index.html не подключает сценарий"
    assert index.find("ui-config.js") < index.find('src="i18n.js"')
    login = (app_module.STATIC_DIR / "login.html").read_text(encoding="utf-8")
    assert "/ui-config.js" in login, "страница входа не подключает сценарий"
