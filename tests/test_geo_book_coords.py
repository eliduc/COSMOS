# -*- coding: utf-8 -*-
"""Принцип координат (ночь 05→06.09.2026, общий с CDPM): координаты
станций из книги авиакомпании (лист «Аэропорты», «Широта»/«Долгота»)
первичны; справочник по коду IATA — только при явном классе данных
open/proprietary; synthetic и «класс не указан» — только книга.
Причина: вымышленные коды синтетических наборов совпадают с
настоящими (PLY — Плимут, SVG — Ставангер, GRT — Гуджрат), и «карта
мира» рисовала чужую географию."""
from __future__ import annotations

from tests.test_task_channel import (  # noqa: F401 — фикстуры pytest
    _channel_env, client)
from tests.test_geo_map import _pkg_dict

from webapp import geo

MAP_URL = "/api/task-package/current/map"
POLAR = {"PLY": [68.9, 33.1], "SVG": [64.6, 40.7], "GRT": [67.65, 53.1]}


# ------------------------------------------------------------ резолвер

def test_книга_первична_у_любого_класса():
    for cls in ("synthetic", "open", "proprietary", None):
        r = geo.resolve_for(["PLY", "SVG", "GRT"], data_class=cls,
                            known=POLAR)
        assert r["complete"] is True and r["basis"] == "book", cls
        assert r["coords"]["PLY"] == [68.9, 33.1], cls
        assert r["missing"] == []


def test_синтетический_без_книги_справочник_не_применяется():
    # PLY/SVG/GRT есть в справочнике IATA — но у synthetic он молчит
    r = geo.resolve_for(["PLY", "SVG", "GRT"], data_class="synthetic")
    assert r["coords"] == {} and r["complete"] is False
    assert r["basis"] == "none" and r["missing"] == ["PLY", "SVG", "GRT"]
    assert r["data_class"] == "synthetic"
    # и добор «при необъявленном классе» синтетику не касается
    r2 = geo.resolve_for(["PLY"], data_class="synthetic",
                         fallback_reference=True)
    assert r2["coords"] == {}


def test_класс_не_указан_только_книга_кроме_явного_добора():
    r = geo.resolve_for(["JFK"], data_class=None)
    assert r["coords"] == {} and r["basis"] == "none"
    # наборы без пакета постановки (решение 30.08): добор разрешён явно
    r2 = geo.resolve_for(["JFK"], data_class=None, fallback_reference=True)
    assert "JFK" in r2["coords"] and r2["basis"] == "reference"


def test_открытый_класс_добирает_пробелы_справочником():
    r = geo.resolve_for(["JFK", "PLY"], data_class="open",
                        known={"PLY": POLAR["PLY"]})
    assert r["complete"] is True and r["basis"] == "book+reference"
    assert r["coords"]["PLY"] == [68.9, 33.1]
    assert 40 < r["coords"]["JFK"][0] < 41
    # неизвестный код и у открытого класса честно в missing
    r2 = geo.resolve_for(["JFK", "XXQ"], data_class="proprietary")
    assert r2["complete"] is False and r2["missing"] == ["XXQ"]


def test_известные_из_аэропортов_набора():
    known = geo.known_from_airports([
        {"code": "ply", "latitude": 68.9, "longitude": 33.1},
        {"code": "SVG", "latitude": 0.0, "longitude": 0.0},   # умолчание
        {"code": "GRT", "latitude": "x", "longitude": 53.1},  # мусор
        {"code": "BAD", "latitude": 95.0, "longitude": 10.0},  # граница
        "не словарь", {"code": "", "latitude": 1, "longitude": 1},
    ])
    assert known == {"PLY": [68.9, 33.1]}


# ------------------------------------------------- пакет постановки

def _pkg_with_coords(data_class: str | None) -> dict:
    from webapp import task_package as tp
    pkg = _pkg_dict()
    rows = [{"Код": c, "timeZone": "Europe/Moscow",
             "Широта": str(POLAR[c][0]), "Долгота": str(POLAR[c][1])}
            for c in ("PLY", "SVG", "GRT")]
    # половинка и мусор не становятся координатами
    rows.append({"Код": "XXQ", "timeZone": "", "Широта": "10", "Долгота": ""})
    pkg["clean_data"]["tables"]["D1"]["Аэропорты"] = {
        "columns": ["Код", "timeZone", "Широта", "Долгота"], "rows": rows}
    if data_class is not None:
        pkg["manifest"]["kit"] = {"data_class": data_class}
    assert tp.SHEET_FLIGHTS in pkg["clean_data"]["tables"]["D1"]
    return pkg


def test_координаты_книги_попадают_в_набор_решателя():
    from webapp import task_package as tp
    payload = tp.to_payload(_pkg_with_coords("synthetic"))
    by_code = {a["code"]: a for a in payload["airports"]}
    assert set(by_code) == {"PLY", "SVG", "GRT"}
    assert by_code["PLY"]["latitude"] == 68.9
    assert by_code["PLY"]["longitude"] == 33.1
    assert by_code["PLY"]["timeZone"] == "Europe/Moscow"
    assert by_code["GRT"]["latitude"] == 67.65
    # книга без координат — полей нет (не выдумываем нули)
    plain = tp.to_payload(_pkg_dict())
    assert all("latitude" not in a for a in plain["airports"])


def test_точка_листа_целиком_и_в_границах():
    from webapp import task_package as tp
    assert tp._airport_point({"Широта": "68,9", "Долгота": "33.1"}) \
        == (68.9, 33.1)
    assert tp._airport_point({"latitude": 1.0, "longitude": 2.0}) \
        == (1.0, 2.0)
    assert tp._airport_point({"Широта": "10"}) is None
    assert tp._airport_point({"Широта": "x", "Долгота": "1"}) is None
    assert tp._airport_point({"Широта": "95", "Долгота": "1"}) is None
    assert tp._airport_point({"Широта": "0", "Долгота": "0"}) is None


def _map_for(client, tmp_path, monkeypatch, pkg: dict) -> dict:
    stub = tmp_path / "package.zip"
    stub.write_bytes(b"stub")
    monkeypatch.setenv("CREW_TASK_PACKAGE", str(stub))
    from webapp import app as app_module, task_package
    app_module._TASK_MAP_CACHE.clear()
    monkeypatch.setattr(task_package, "load", lambda path, **kw: pkg)
    r = client.get(MAP_URL)
    assert r.status_code == 200, r.text
    return r.json()


def test_карта_постановки_синтетика_с_координатами_книги(
        client, tmp_path, monkeypatch):
    net = _map_for(client, tmp_path, monkeypatch,
                   _pkg_with_coords("synthetic"))
    g = net["geo"]
    assert g["complete"] is True and g["basis"] == "book"
    assert g["data_class"] == "synthetic"
    assert g["coords"] == POLAR, "полярная сеть книги, не Плимут"


def test_карта_постановки_синтетика_без_координат_только_схема(
        client, tmp_path, monkeypatch):
    pkg = _pkg_dict()
    pkg["manifest"]["kit"] = {"data_class": "synthetic"}
    net = _map_for(client, tmp_path, monkeypatch, pkg)
    g = net["geo"]
    assert g["complete"] is False and g["basis"] == "none"
    assert g["data_class"] == "synthetic"
    assert set(g["missing"]) == {"PLY", "SVG", "GRT"}


def test_карта_постановки_открытый_класс_справочником(
        client, tmp_path, monkeypatch):
    pkg = _pkg_dict()
    pkg["manifest"]["kit"] = {"data_class": "open"}
    net = _map_for(client, tmp_path, monkeypatch, pkg)
    g = net["geo"]
    # PLY/SVG/GRT есть в справочнике IATA → у открытого класса карта
    # мира доступна (это и есть настоящие Плимут/Ставангер/Гуджрат)
    assert g["complete"] is True and g["basis"] == "reference"
