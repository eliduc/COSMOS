# -*- coding: utf-8 -*-
"""Карта ростера (поручение владельца 2026-09-04): проекция решения
кнопки ростеринга в формат движка NetMap — по дням, маршрут сотрудника
за день как «цепочка», сотрудники без рейсов видны; без решения —
честный отказ ручки."""

import pytest

from vmp.run import roster_map as rmp


def _solution():
    return {
        "assignments": [
            {"employee": "H02", "flight": "PL101|2026-09-04T08:00:00",
             "dep_time": "2026-09-04T08:00:00", "arr_time": "2026-09-04T09:30:00",
             "dep_airport": "PLY", "arr_airport": "SVG", "role": "Captain",
             "base": "PLY", "date": "2026-09-04"},
            {"employee": "H02", "flight": "PL102|2026-09-04T10:20:00",
             "dep_time": "2026-09-04T10:20:00", "arr_time": "2026-09-04T11:50:00",
             "dep_airport": "SVG", "arr_airport": "PLY", "role": "Captain",
             "base": "PLY", "date": "2026-09-04"},
            {"employee": "H14", "flight": "PL201|2026-09-05T13:10:00",
             "dep_time": "2026-09-05T13:10:00", "arr_time": "2026-09-05T15:00:00",
             "dep_airport": "SVG", "arr_airport": "GRT", "role": "FirstOfficer",
             "base": "SVG", "date": "2026-09-05"},
        ],
        "reference_tables": {"D1": {"Экипажи": {
            "columns": ["Табельный номер", "База приписки"],
            "rows": [{"Табельный номер": "H02", "База приписки": "PLY"},
                     {"Табельный номер": "H14", "База приписки": "SVG"},
                     {"Табельный номер": "H20", "База приписки": "GRT"}]}}},
    }


def test_проекция_по_дням_и_маршрут_сотрудника():
    # координаты — из книги авиакомпании (known), справочник по коду у
    # синтетического набора не применяется (06.09.2026)
    book = {"PLY": [69.0, 33.0], "SVG": [64.5, 40.5], "GRT": [68.0, 50.0]}
    d = rmp.build_roster_map(_solution(), known=book, data_class="synthetic")
    assert [n["code"] for n in d["nodes"]] == ["PLY", "SVG", "GRT"]
    assert {n["code"] for n in d["nodes"] if n.get("base")} == {"PLY", "SVG", "GRT"}
    assert [x["date"] for x in d["days"]] == ["2026-09-04", "2026-09-05"]
    d1 = d["days"][0]
    assert d1["arcs"] == [{"a": "PLY", "b": "SVG", "n": 2}]
    assert d1["chains"] == [{"cid": "H02", "legs": [
        {"dep": "PLY", "arr": "SVG", "flight": "PL101|2026-09-04T08:00:00",
         "dep_time": "2026-09-04T08:00:00", "arr_time": "2026-09-04T09:30:00",
         "role": "Captain"},
        {"dep": "SVG", "arr": "PLY", "flight": "PL102|2026-09-04T10:20:00",
         "dep_time": "2026-09-04T10:20:00", "arr_time": "2026-09-04T11:50:00",
         "role": "Captain"}]}]
    assert d1["employees"] == [{"id": "H02", "base": "PLY", "legs": 2,
                                "first": "2026-09-04T08:00:00",
                                "last": "2026-09-04T11:50:00"}]
    # Штат целиком, включая сотрудника без единого рейса (H20).
    assert [e["id"] for e in d["employees"]] == ["H02", "H14", "H20"]
    assert d["employees"][2] == {"id": "H20", "base": "GRT",
                                 "days": {"2026-09-04": 0, "2026-09-05": 0}}
    assert d["geo"]["complete"] is True
    assert d["geo"]["basis"] == "book"
    # сводные дуги за горизонт — верхний уровень карты (рёбра графа)
    assert d["arcs"] and all(set(a) == {"a", "b", "n"} for a in d["arcs"])
    assert sum(a["n"] for a in d["arcs"]) == sum(
        a["n"] for day in d["days"] for a in day["arcs"])


def test_без_координат_книги_синтетический_набор_только_схема():
    # выдуманные коды (PLY/SVG/GRT) совпадают с настоящими IATA — справочник
    # не должен ставить их на чужие города; координат нет — только схема
    d = rmp.build_roster_map(_solution(), known=None, data_class="synthetic")
    assert d["geo"]["coords"] == {}
    assert d["geo"]["complete"] is False
    assert d["geo"]["basis"] == "none"


def test_без_назначений_честный_отказ():
    with pytest.raises(rmp.RosterMapError):
        rmp.build_roster_map({"assignments": []})
