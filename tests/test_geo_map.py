# -*- coding: utf-8 -*-
"""Пакет «Карта на гео-подложке» (2026-08-30): резолвер координат
(webapp/geo.py) и карта сети постановки
GET /api/task-package/current/map.

Оснастка клиента/канала переиспользована из tests/test_task_channel
(тот же dev-клиент, та же изоляция каталога данных)."""
from __future__ import annotations

import pytest

from tests.test_task_channel import (  # noqa: F401 — фикстуры pytest
    _channel_env, client)

from webapp import geo

MAP_URL = "/api/task-package/current/map"


# ------------------------------------------------------------ резолвер

def test_резолвер_полный_и_с_пробелами():
    full = geo.resolve(["JFK", "LHR"])
    assert full["complete"] is True
    assert set(full["coords"]) == {"JFK", "LHR"}
    lat, lon = full["coords"]["JFK"]
    assert 40 < lat < 41 and -74.5 < lon < -73
    part = geo.resolve(["JFK", "XXQ"])
    assert part["complete"] is False
    assert part["missing"] == ["XXQ"]
    assert "JFK" in part["coords"]


def test_резолвер_исторический_код_из_дополнений():
    # PBI (данные BTS): в свежем OurAirports аэропорт сменил IATA —
    # исторический код живёт в airports-extra.json (только пробелы,
    # основной справочник при конфликте сильнее).
    r = geo.resolve(["PBI"])
    assert r["complete"] is True
    assert 26 < r["coords"]["PBI"][0] < 27


def test_резолвер_пустой_список_честно_не_готов():
    r = geo.resolve([])
    assert r["complete"] is False and r["coords"] == {}
    # пустые/дублирующиеся коды не шумят
    r2 = geo.resolve(["", "jfk", "JFK", None])
    assert list(r2["coords"]) == ["JFK"]


# ------------------------------------------------- карта сети постановки

def _pkg_dict() -> dict:
    """Минимальный pkg формата task_package.load (образец —
    test_task_package.test_срок_допуска_не_указан_окно_открытое):
    три рейса двумя парами станций, один транспортный, две базы."""
    from webapp import task_package as tp
    flights = [
        {"Номер рейса": "PL1", "Дата": "2026-03-02", "Вылет": "PLY",
         "Время вылета": "10:00", "Прилёт": "SVG",
         "Время прилёта": "12:00"},
        {"Номер рейса": "PL2", "Дата": "2026-03-02", "Вылет": "SVG",
         "Время вылета": "14:00", "Прилёт": "PLY",
         "Время прилёта": "16:00"},
        {"Номер рейса": "PL3", "Дата": "2026-03-03", "Вылет": "PLY",
         "Время вылета": "09:00", "Прилёт": "GRT",
         "Время прилёта": "11:00"},
    ]
    return {
        "manifest": {"crew_size": {"value": 2, "rule": "R-TEST"},
                     "has_data": True, "source_run": "run-test",
                     "customer": "Т"},
        "model": {"rules": []},
        "clean_data": {"tables": {"D1": {
            tp.SHEET_FLIGHTS: {
                "columns": ["Номер рейса", "Дата", "Вылет",
                            "Время вылета", "Прилёт", "Время прилёта"],
                "rows": flights},
            tp.SHEET_CREW: {
                "columns": ["Табельный номер", "База", "Должность"],
                "rows": [{"Табельный номер": "E1", "База": "PLY",
                          "Должность": "командир"},
                         {"Табельный номер": "E2", "База": "GRT",
                          "Должность": "второй пилот"}]},
        }}},
    }


def test_карта_постановки_из_текущего_пакета(client, tmp_path,
                                             monkeypatch):
    stub = tmp_path / "package.zip"
    stub.write_bytes(b"stub")
    monkeypatch.setenv("CREW_TASK_PACKAGE", str(stub))
    from webapp import app as app_module, task_package
    app_module._TASK_MAP_CACHE.clear()
    monkeypatch.setattr(task_package, "load",
                        lambda path, **kw: _pkg_dict())
    r = client.get(MAP_URL)
    assert r.status_code == 200, r.text
    net = r.json()
    codes = {n["code"] for n in net["nodes"]}
    assert codes == {"PLY", "SVG", "GRT"}
    assert {n["code"] for n in net["nodes"] if n["base"]} \
        == {"PLY", "GRT"}
    arcs = {(a["a"], a["b"]): a["n"] for a in net["arcs"]}
    assert arcs == {("PLY", "SVG"): 2, ("GRT", "PLY"): 1}, (
        "пара агрегируется без направления")
    assert set(net["geo"]) == {"coords", "missing", "complete",
                               "basis", "data_class"}
    # класс данных не объявлен, координат в книге нет → у карты мира
    # нечего показывать (принцип координат 05.09.2026: справочник по
    # коду — только при явном классе open/proprietary)
    assert net["geo"]["complete"] is False
    assert net["geo"]["basis"] == "none"
    assert net["source_run"] == "run-test"
    ppl = net["employees"]
    assert [p["id"] for p in ppl] == ["E2", "E1"], (
        "сортировка по базе, затем по табельному")
    assert ppl[1]["base"] == "PLY" and ppl[1]["roles"] == ["Captain"]
    assert set(ppl[0]) == {"id", "name", "base", "roles", "off_days"}
    # кэш: повторный вызов не перегоняет load (подменяем на бомбу)
    monkeypatch.setattr(task_package, "load",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("cache miss")))
    assert client.get(MAP_URL).status_code == 200


def test_карта_без_пакета_404(client):
    assert client.get(MAP_URL).status_code == 404
