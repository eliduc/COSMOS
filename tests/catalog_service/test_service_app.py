# -*- coding: utf-8 -*-
"""T1: приложение службы — раздача интерфейса, точки наблюдения, запуск.

Гейт «не вхолостую»: каждый тест называет число рассмотренных объектов
(ручек, ключей запуска, файлов).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import catalog_service.app as app_mod
from catalog_service.__main__ import main
from catalog_service.settings import (DEFAULT_PORT, Settings, SettingsError,
                                      parse_args)
from tests.catalog_service.conftest import detail


def _fake_dist(tmp_path, monkeypatch) -> Path:
    """Собранный интерфейс подменяется папкой теста: механизм раздачи
    судится здесь, настоящая сборка — стражем T12."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        "<title>Каталог шаблонов</title>"
        '<script type="module" src="./assets/app.js"></script>'
        "</head><body></body></html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("export const ok = 1;\n",
                                            encoding="utf-8")
    monkeypatch.setattr(app_mod, "DIST_DIR", dist)
    return dist


def test_root_redirects_to_ui_and_index_is_not_cached(make_service, tmp_path,
                                                      monkeypatch):
    _fake_dist(tmp_path, monkeypatch)
    svc = make_service("owner")
    r = svc.get("/", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/ui/"
    page = svc.get("/ui/")
    assert page.status_code == 200 and "Каталог шаблонов" in page.text
    assert page.headers.get("cache-control") == "no-cache"
    asset = svc.get("/ui/assets/app.js")
    assert asset.status_code == 200
    assert asset.headers.get("cache-control") != "no-cache"
    print("проверено файлов интерфейса: 2 (index.html, assets/app.js)")


def test_root_without_build_refuses_with_words(make_service):
    svc = make_service("owner")
    r = svc.get("/", follow_redirects=False)
    if r.status_code == 302:
        pytest.skip("catalog-ui/dist собран — механизм отказа судится "
                    "подменой DIST_DIR в соседнем тесте")
    assert r.status_code == 503
    words = detail(r)
    assert words["code"] == "ui-not-built"
    assert "npm run build" in words["words"] and "/api" in words["words"]


def test_root_without_build_refuses_with_words_forced(make_service, tmp_path,
                                                      monkeypatch):
    monkeypatch.setattr(app_mod, "DIST_DIR", tmp_path / "нет-сборки")
    svc = make_service("owner")
    r = svc.get("/", follow_redirects=False)
    assert r.status_code == 503 and detail(r)["code"] == "ui-not-built"


def test_healthz_reads_nothing_from_disk(make_service, monkeypatch):
    """Живость не должна зависеть от диска: наблюдатель опрашивает
    `/healthz` постоянно, и чтение разделов на каждый опрос сделало бы
    наблюдение источником нагрузки."""
    svc = make_service("owner")
    reads: list = []
    from template_catalog import reader
    orig_json = reader.read_json_file
    monkeypatch.setattr(reader, "read_json_file",
                        lambda p: (reads.append(str(p)), orig_json(p))[1])
    for name in ("read_text", "read_bytes"):
        orig = getattr(Path, name)

        def counting(self, *a, _orig=orig, _n=name, **kw):
            reads.append(f"{_n}:{self}")
            return _orig(self, *a, **kw)
        monkeypatch.setattr(Path, name, counting)
    r = svc.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert reads == [], f"/healthz читал диск: {reads}"
    print("обращений к диску при /healthz: 0")


def test_readyz_form_and_versionz(make_service):
    svc = make_service("owner")
    r = svc.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert set(body["checks"]) == {"catalog", "ui_built", "work_dir"}
    assert body["mode"] == "edit"
    assert body["catalog"]["errors"] == 0
    cat = svc.catalog()
    assert body["catalog"]["version"] == cat.version()
    assert body["catalog"]["fingerprint"] == cat.fingerprint()
    v = svc.get("/versionz").json()
    assert v["service"] == "catalog_service"
    assert v["service_version"] and v["reader_version"]
    assert v["catalog"]["version"] == cat.version()
    assert v["mode"] == "edit" and v["actor"] == "владелец"
    print("точек наблюдения проверено: 3 (/healthz, /readyz, /versionz)")


def test_readyz_is_red_on_broken_fingerprint(make_service, tmp_path):
    """H02: отпечаток шапки не пересчитан — каталог негоден, и служба
    говорит это кодом, а не текстом при коде 200."""
    from tests.catalog_copies import copy_catalog
    folder = copy_catalog(tmp_path, families={"duty_limits": {"title":
                                                             "иная шапка"}},
                          refresh=False, name="битый")
    svc = make_service("owner", folder=folder)
    r = svc.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["ready"] is False and body["checks"]["catalog"] is False
    assert body["catalog"]["errors"] >= 1
    assert "ошибок" in body["reason"]


def test_unreadable_catalog_gives_503_without_absolute_path(make_service,
                                                            tmp_path):
    from tests.catalog_copies import copy_catalog
    folder = copy_catalog(tmp_path, name="кривой")
    (folder / "families.json").write_text("{ не json", encoding="utf-8")
    svc = make_service("owner", folder=folder)
    r = svc.get("/api/catalog")
    assert r.status_code == 503
    words = detail(r)
    assert words["code"] == "catalog-unavailable"
    assert "families.json" in words["words"]
    assert str(folder) not in words["words"]
    assert str(folder).replace("\\", "/") not in words["words"]
    ready = svc.get("/readyz")
    assert ready.status_code == 503
    assert str(folder) not in json.dumps(ready.json(), ensure_ascii=False)
    print("отказов без абсолютных путей проверено: 2 (/api/catalog, /readyz)")


def test_main_parses_every_key_of_the_contract():
    # Порт-образец — заведомо не порт какой-либо службы пакета: тест
    # судит разбор ключей запуска, в сеть не ходит.
    args = parse_args(["--port", "24680", "--host", "0.0.0.0", "--actor",
                       "analyst", "--read-only", "--catalog", "К",
                       "--work-dir", "Р"])
    assert (args.port, args.host, args.actor, args.read_only, args.catalog,
            args.work_dir) == (24680, "0.0.0.0", "analyst", True, "К", "Р")
    # Умолчание порта берётся ИЗ НАСТРОЕК службы, а не повторяется
    # числом: повтор разошёлся бы с кодом молча.
    assert parse_args([]).host == "127.0.0.1"
    assert parse_args([]).port == DEFAULT_PORT
    print("ключей запуска проверено: 6")


def test_unknown_actor_refuses_startup_with_words(capsys, tmp_path):
    with pytest.raises(SettingsError) as ei:
        Settings.from_env(["--actor", "контролёр"])
    assert "owner" in str(ei.value) and "analyst" in str(ei.value)
    code = main(["--actor", "контролёр", "--work-dir", str(tmp_path)])
    assert code == 2
    assert "не запущена" in capsys.readouterr().out


def test_read_only_wins_over_actor(tmp_path):
    s = Settings(catalog_dir=tmp_path, work_dir=tmp_path / "w",
                 actor="owner", read_only=True)
    assert s.mode == "view" and s.actor == "владелец" and not s.is_owner()
    s2 = Settings(catalog_dir=tmp_path, work_dir=tmp_path / "w")
    assert s2.mode == "view" and s2.actor is None
