# -*- coding: utf-8 -*-
"""Линия данных: выгрузка JSON (запрос одной из линий).

Форма: объект «имя листа → массив плоских записей» либо массив
записей верхним уровнем (один лист «Данные»). Охраняемые контракты:
колонки — объединение ключей в порядке первого появления,
недостающие ключи — пустые ячейки (как в xlsx); значения — только
скаляры, вложенный контейнер — честный отказ С ПУТЁМ; повторяющийся
ключ — отказ; числа/истина/ложь/null — строками по правилам ячеек
(15.0 → «15», null → пусто)."""
import json

import pytest

from cdpm import data_freeze as df


def _write(tmp_path, data, name="VYGRUZKA.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False),
                 encoding="utf-8")
    return p


def test_json_sheet_map_columns_and_cells(tmp_path):
    p = _write(tmp_path, {
        "Экипаж": [
            {"Табельный": "001", "База": "HUB", "Налёт": 75.0},
            {"Табельный": "002", "Отпуск": True, "Прим": None},
        ],
        "Пустой": [],
    })
    sheets = df._read_json(p)
    ek = sheets["Экипаж"]
    # Колонки — объединение ключей в порядке первого появления.
    assert ek["columns"] == ["Табельный", "База", "Налёт",
                             "Отпуск", "Прим"]
    assert ek["rows"][0]["Налёт"] == "75"          # 75.0 → «75»
    assert ek["rows"][1]["База"] == ""             # недостающий ключ
    assert ek["rows"][1]["Отпуск"] == "true"
    assert ek["rows"][1]["Прим"] == ""             # null → пусто
    assert "Пустой" not in sheets                  # пустой лист опущен


def test_json_top_level_array_becomes_one_sheet(tmp_path):
    p = _write(tmp_path, [{"Рейс": "XX-731"}, {"Рейс": "XX-732"}])
    sheets = df._read_json(p)
    assert list(sheets) == ["Данные"]
    assert [r["Рейс"] for r in sheets["Данные"]["rows"]] \
        == ["XX-731", "XX-732"]


def test_json_nested_value_refused_with_path(tmp_path):
    p = _write(tmp_path, {"Экипаж": [
        {"Табельный": "001", "Допуски": ["A320"]}]})
    with pytest.raises(df.DataFreezeError) as e:
        df._read_json(p)
    msg = str(e.value)
    assert "вложенное значение" in msg
    assert "'Экипаж'" in msg and "'Допуски'" in msg


def test_json_duplicate_key_refused(tmp_path):
    p = tmp_path / "DUP.json"
    p.write_text('{"Лист": [{"a": 1, "a": 2}]}', encoding="utf-8")
    with pytest.raises(df.DataFreezeError) as e:
        df._read_json(p)
    assert "повторяющийся ключ" in str(e.value)


def test_json_wrong_shapes_refused(tmp_path):
    with pytest.raises(df.DataFreezeError):
        df._read_json(_write(tmp_path, "строка", name="S.json"))
    with pytest.raises(df.DataFreezeError):
        df._read_json(_write(tmp_path, {"Лист": {"не": "массив"}},
                             name="M.json"))
    with pytest.raises(df.DataFreezeError):
        df._read_json(_write(tmp_path, {"Лист": ["не объект"]},
                             name="R.json"))


def test_json_in_freeze_data_kit(tmp_path):
    p = _write(tmp_path, {"Экипаж": [{"Табельный": "001"}]},
               name="AK-DATA.json")
    out = tmp_path / "kit"
    counters = df.freeze_data([p], out)
    dataset = json.loads(
        (out / "dataset.json").read_text(encoding="utf-8"))
    src = dataset["sources"][0]
    assert src["file"] == "AK-DATA.json"
    tables = dataset["tables"][src["sid"]]
    assert tables["Экипаж"]["rows"] == [{"Табельный": "001"}]
    assert counters["files"] == 1
