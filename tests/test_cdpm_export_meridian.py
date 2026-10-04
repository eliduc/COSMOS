# -*- coding: utf-8 -*-
"""МЕР-1: экспорт книги данных в dataset COS-Meridian (спецификация
v1.17 от 15.06.2026).

Охраняемое: полная книга даёт валидный dataset (составные ключи
legId «номер$дата$N», ISO-даты, обязательные поля объектов, окно
планирования из данных); неполная — честный отказ с перечнем
недостающего (ничего не выдумывается: ни стран, ни таймзон); без
presetId — отказ (правила живут пресетом на стороне COS)."""
import json

import pytest

from cdpm import export_meridian as em


def _tables(drop_country=False):
    apt_cols = ["Код", "Страна", "Часовой пояс", "Категория",
                "Провозка", "МВЛ", "СНГ"]
    apt_rows = [
        {"Код": "PLY", "Страна": "RU", "Часовой пояс": "RU 1",
         "Категория": "B", "Провозка": "NONE", "МВЛ": "да",
         "СНГ": "нет"},
        {"Код": "SVG", "Страна": "RU", "Часовой пояс": "RU 2",
         "Категория": "B", "Провозка": "NONE", "МВЛ": "да",
         "СНГ": "нет"},
    ]
    if drop_country:
        apt_rows[1]["Страна"] = ""
    return {"D1": {
        "Рейсы": {
            "columns": ["Номер рейса", "Дата", "Вылет",
                        "Время вылета", "Прилёт", "Время прилёта",
                        "Тип ВС"],
            "rows": [
                {"Номер рейса": "PL101", "Дата": "21.11.2026",
                 "Вылет": "PLY", "Время вылета": "08:30",
                 "Прилёт": "SVG", "Время прилёта": "10:05",
                 "Тип ВС": "A320"},
                {"Номер рейса": "PL101", "Дата": "21.11.2026",
                 "Вылет": "SVG", "Время вылета": "11:00",
                 "Прилёт": "PLY", "Время прилёта": "12:35",
                 "Тип ВС": "A320"},
            ]},
        "Экипажи": {
            "columns": ["Табельный", "База", "Дата рождения", "Пол",
                        "Гражданство", "Лимит налёта (мин)",
                        "Лимит работы (мин)", "Тип ВС", "Эскадрилья"],
            "rows": [
                {"Табельный": "10840", "База": "PLY",
                 "Дата рождения": "1982-10-28", "Пол": "M",
                 "Гражданство": "RU", "Лимит налёта (мин)": 4800,
                 "Лимит работы (мин)": 8160, "Тип ВС": "A320",
                 "Эскадрилья": "PL$LO$S1"},
            ]},
        "Аэропорты": {"columns": apt_cols, "rows": apt_rows},
    }}


def test_full_book_yields_valid_dataset():
    got = json.loads(em.export_meridian(
        _tables(), preset_id="7dea9793-45ba-457f-b1a3-cb218ebdfad3",
        dataset_type="pairing", division="FD"))
    assert got["format"] == 1
    assert got["type"] == "pairing" and got["division"] == "FD"
    assert got["presetId"].startswith("7dea9793")
    # Составной ключ и нумерация участков одного рейса в один день.
    ids = [x["legId"] for x in got["legs"]]
    assert ids == ["PL101$21.11.2026$1", "PL101$21.11.2026$2"]
    assert got["legs"][0]["start"] == "2026-11-21T08:30:00"
    assert got["legs"][0]["carrier"] == "PL"
    assert got["legs"][0]["serviceType"] == "J"
    # Окно планирования — из данных.
    assert got["planSince"] == "2026-11-21T08:30:00"
    assert got["planUntil"] == "2026-11-21T12:35:00"
    emp = got["employees"][0]
    assert emp["birth"] == "1982-10-28"
    assert emp["citizenship"] == "RU"
    assert emp["squadron"] == "PL$LO$S1"
    apts = {a["IATA"]: a for a in got["airports"]}
    assert set(apts) == {"PLY", "SVG"}
    assert apts["PLY"]["timezone"] == "RU 1"
    assert apts["PLY"]["mvl"] is True and apts["PLY"]["sng"] is False
    assert got["pairings"] == [] and got["groundActivities"] == []


def test_incomplete_book_refused_with_named_gaps():
    with pytest.raises(em.MeridianExportError) as e:
        em.export_meridian(_tables(drop_country=True),
                           preset_id="x")
    assert "SVG" in str(e.value) and "country" in str(e.value)

    tables = _tables()
    del tables["D1"]["Аэропорты"]
    with pytest.raises(em.MeridianExportError) as e:
        em.export_meridian(tables, preset_id="x")
    assert "аэропорт" in str(e.value)

    with pytest.raises(em.MeridianExportError) as e:
        em.export_meridian(_tables(), preset_id="  ")
    assert "presetId" in str(e.value)
