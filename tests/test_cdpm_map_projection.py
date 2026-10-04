# -*- coding: utf-8 -*-
"""Карта сети и персонал (ревизия 05.09.2026, слово владельца с
репетиции): проекция книги данных несёт сводку сети и персонала —
рейсы и сотрудники по станциям, горизонт и флот, должности и допуски,
разрез база × должность. Проекция детерминирована: никакой модели,
только замороженная книга."""
import pytest

from cdpm import map_projection as mp


def _book():
    return {"D1": {
        "Рейсы": {
            "columns": ["Номер рейса", "Дата", "Вылет", "Время вылета",
                        "Прилёт", "Время прилёта", "Тип ВС"],
            "rows": [
                {"Номер рейса": "PL101", "Дата": "04.09.2026",
                 "Вылет": "PLY", "Прилёт": "SVG", "Тип ВС": "A320"},
                {"Номер рейса": "PL102", "Дата": "04.09.2026",
                 "Вылет": "SVG", "Прилёт": "PLY", "Тип ВС": "A320"},
                {"Номер рейса": "PL201", "Дата": "05.09.2026",
                 "Вылет": "PLY", "Прилёт": "GRT", "Тип ВС": "B737"},
                {"Номер рейса": "PL202", "Дата": "05.09.2026",
                 "Вылет": "GRT", "Прилёт": "PLY", "Тип ВС": "B737"},
                # строка без станций — не рейс, в счёт не идёт
                {"Номер рейса": "", "Дата": "", "Вылет": "",
                 "Прилёт": "", "Тип ВС": ""},
            ]},
        "Экипажи": {
            "columns": ["Табельный номер", "Фамилия И.О.", "База",
                        "Должность"],
            "rows": [
                {"Табельный номер": "P02", "Фамилия И.О.": "Иванов И.И.",
                 "База": "PLY", "Должность": "КВС"},
                {"Табельный номер": "P01", "Фамилия И.О.": "Петров П.П.",
                 "База": "PLY", "Должность": "ВП"},
                {"Табельный номер": "G01", "Фамилия И.О.": "Сидоров С.С.",
                 "База": "GRT", "Должность": "КВС"},
                {"Табельный номер": "X01", "Фамилия И.О.": "Без базы",
                 "База": "", "Должность": ""},
            ]},
        "Допуски": {
            "columns": ["Табельный номер", "Тип ВС", "Действует до"],
            "rows": [
                {"Табельный номер": "P02", "Тип ВС": "A320",
                 "Действует до": "31.12.2026"},
                {"Табельный номер": "P02", "Тип ВС": "B737",
                 "Действует до": "31.12.2026"},
                {"Табельный номер": "P02", "Тип ВС": "A320",
                 "Действует до": "31.12.2027"},   # дубль типа — один раз
                {"Табельный номер": "G01", "Тип ВС": "B737",
                 "Действует до": "31.12.2026"},
            ]},
        "Аэропорты": {
            "columns": ["Код", "timeZone"],
            "rows": [{"Код": "PLY", "timeZone": "Europe/Moscow"}]},
    }}


def test_projection_carries_network_summary_and_crew(monkeypatch):
    from webapp import geo
    monkeypatch.setattr(geo, "resolve", lambda codes: {
        "coords": {}, "missing": list(codes), "complete": False})
    # Открытый класс — справочник применяется (подменён), книга без
    # координат: карта мира честно недоступна.
    got = mp.build_map_projection(_book(), data_class="open")

    # Сеть: станции в порядке появления, базы помечены, рейсы по станции.
    assert [n["code"] for n in got["nodes"]] == ["PLY", "SVG", "GRT"]
    by = {n["code"]: n for n in got["nodes"]}
    assert by["PLY"].get("base") is True and by["GRT"].get("base") is True
    assert "base" not in by["SVG"]
    assert by["PLY"]["flights"] == 4 and by["SVG"]["flights"] == 2
    assert by["PLY"]["employees"] == 2 and by["SVG"]["employees"] == 0
    # Рёбра: ненаправленные пары со счётом и числом в сутки.
    assert got["arcs"] == [
        {"a": "GRT", "b": "PLY", "n": 2, "per_day": 1.0},
        {"a": "PLY", "b": "SVG", "n": 2, "per_day": 1.0}]
    # Сводка: рейсы, горизонт по различным датам, флот.
    s = got["summary"]
    assert s["flights"] == 4 and s["stations"] == 3 and s["bases"] == 2
    assert s["employees"] == 4 and s["days"] == 2
    assert s["date_from"] == "2026-09-04" and s["date_to"] == "2026-09-05"
    assert s["fleet"] == {"A320": 2, "B737": 2}
    # Персонал: порядок (база, табельный), должность, допуски без дублей.
    assert [e["id"] for e in got["employees"]] == ["X01", "G01", "P01", "P02"]
    p02 = next(e for e in got["employees"] if e["id"] == "P02")
    assert p02["position"] == "КВС" and p02["quals"] == ["A320", "B737"]
    assert p02["name"] == "Иванов И.И."
    assert next(e for e in got["employees"] if e["id"] == "P01")["quals"] == []
    # Разрез база × должность: сотрудник без базы в разрез не входит,
    # без должности — под своим именем.
    assert got["by_base"] == {"GRT": {"КВС": 1}, "PLY": {"ВП": 1, "КВС": 1}}
    assert got["geo"]["coords"] == {}
    assert got["geo"]["missing"] == ["PLY", "SVG", "GRT"]
    assert got["geo"]["complete"] is False
    assert got["geo"]["source"] == mp.GEO_SOURCE_REFERENCE


def test_projection_without_optional_sheets_and_columns(monkeypatch):
    """Книга только с рейсами без дат и типов ВС: сводка честно без
    горизонта и флота, персонал пуст, рёбра без per_day."""
    from webapp import geo
    monkeypatch.setattr(geo, "resolve", lambda codes: {
        "coords": {}, "missing": [], "complete": False})
    got = mp.build_map_projection({"D1": {"Рейсы": {
        "columns": ["Вылет", "Прилёт"],
        "rows": [{"Вылет": "AAA", "Прилёт": "BBB"}]}}})
    assert got["arcs"] == [{"a": "AAA", "b": "BBB", "n": 1}]
    assert got["summary"]["days"] is None
    assert got["summary"]["date_from"] is None and got["summary"]["fleet"] == {}
    assert got["employees"] == [] and got["by_base"] == {}
    assert got["nodes"][0] == {"code": "AAA", "flights": 1, "employees": 0}


def test_projection_refuses_without_legs_sheet():
    with pytest.raises(mp.MapProjectionError, match="лист рейсов"):
        mp.build_map_projection({"D1": {"Экипажи": {
            "columns": ["Табельный номер"], "rows": []}}})


def test_synthetic_class_ignores_reference_coordinates(monkeypatch):
    """Живой случай 05.09: вымышленные PLY/SVG/GRT «нашлись» в
    справочнике как Плимут, Ставангер и Гуджрат. У синтетического
    класса справочник не применяется — карта мира только по
    координатам книги."""
    from webapp import geo
    calls = []

    def fake_resolve(codes):
        calls.append(list(codes))
        return {"coords": {c: [1.0, 2.0] for c in codes}, "missing": [],
                "complete": True}
    monkeypatch.setattr(geo, "resolve", fake_resolve)
    book = _book()
    got = mp.build_map_projection(book, data_class="synthetic")
    assert calls == [], "справочник не должен вызываться для синтетики"
    assert got["geo"]["complete"] is False
    assert got["geo"]["missing"] == ["PLY", "SVG", "GRT"]
    assert got["geo"]["source"] == mp.GEO_SOURCE_BOOK
    assert "синтетический" in got["geo"]["note"]
    # Книга с координатами — карта мира доступна и у синтетики.
    book["D1"]["Аэропорты"] = {
        "columns": ["Код", "timeZone", "Широта", "Долгота"],
        "rows": [{"Код": "PLY", "Широта": "69.0", "Долгота": "33.1"},
                 {"Код": "SVG", "Широта": "64,55", "Долгота": "40.7"},
                 {"Код": "GRT", "Широта": "67.5", "Долгота": "64.0"}]}
    got = mp.build_map_projection(book, data_class="synthetic")
    assert got["geo"]["complete"] is True and got["geo"]["missing"] == []
    assert got["geo"]["coords"]["SVG"] == [64.55, 40.7]
    assert calls == []
    # Открытый/проприетарный класс: справочник применяется, книга сильнее.
    got = mp.build_map_projection(book, data_class="open")
    assert calls and got["geo"]["source"] == mp.GEO_SOURCE_REFERENCE
    assert got["geo"]["coords"]["PLY"] == [69.0, 33.1]


def test_book_coords_reject_garbage():
    tables = {"D1": {"Аэропорты": {
        "columns": ["Код", "Широта", "Долгота"],
        "rows": [{"Код": "AAA", "Широта": "91", "Долгота": "0"},
                 {"Код": "BBB", "Широта": "x", "Долгота": "0"},
                 {"Код": "", "Широта": "1", "Долгота": "1"},
                 {"Код": "ccc", "Широта": "-10.5", "Долгота": "179.9"}]}}}
    assert mp.book_coords(tables) == {"CCC": [-10.5, 179.9]}
    assert mp.book_coords({"D1": {"Аэропорты": {
        "columns": ["Код", "timeZone"], "rows": [{"Код": "PLY"}]}}}) == {}


def test_unclassified_kit_uses_book_coordinates_only(monkeypatch):
    """Класс «не указан» — как синтетика: справочник по коду не
    применяется (непомеченный комплект нельзя считать настоящим)."""
    from webapp import geo
    monkeypatch.setattr(geo, "resolve",
                        lambda codes: (_ for _ in ()).throw(AssertionError(
                            "справочник вызван для комплекта без класса")))
    got = mp.build_map_projection(_book(), data_class=None)
    assert got["geo"]["complete"] is False
    assert got["geo"]["source"] == mp.GEO_SOURCE_BOOK
    assert "не указан" in got["geo"]["note"]


def test_codes_normalised_and_days_deduplicated(monkeypatch):
    """Коды станций в одном регистре везде (узлы, рёбра, базы,
    координаты); сутки горизонта — различные календарные даты, а не
    различные написания одной даты (ревью 05.09)."""
    from webapp import geo
    monkeypatch.setattr(geo, "resolve", lambda codes: {
        "coords": {}, "missing": list(codes), "complete": False})
    tables = {"D1": {
        "Рейсы": {"columns": ["Дата", "Вылет", "Прилёт"], "rows": [
            {"Дата": "04.09.2026", "Вылет": "ply", "Прилёт": "SVG"},
            {"Дата": "2026-09-04", "Вылет": "Svg", "Прилёт": "PLY"},
            {"Дата": "04.09.2026 10:00", "Вылет": "PLY", "Прилёт": "svg"},
            {"Дата": "05.09.2026", "Вылет": "PLY", "Прилёт": "SVG"}]},
        "Экипажи": {"columns": ["Табельный номер", "База"], "rows": [
            {"Табельный номер": "E1", "База": "ply"}]},
        "Аэропорты": {"columns": ["Код ИКАО", "Код", "Широта", "Долгота"],
                      "rows": [{"Код ИКАО": "XXXX", "Код": "ply",
                                "Широта": "69", "Долгота": "33"},
                               {"Код ИКАО": "YYYY", "Код": "svg",
                                "Широта": "64", "Долгота": "40"}]},
    }}
    got = mp.build_map_projection(tables, data_class="synthetic")
    assert [n["code"] for n in got["nodes"]] == ["PLY", "SVG"]
    assert got["nodes"][0]["base"] is True and got["nodes"][0]["employees"] == 1
    assert got["arcs"] == [{"a": "PLY", "b": "SVG", "n": 4, "per_day": 2.0}]
    assert got["summary"]["days"] == 2
    assert got["summary"]["date_from"] == "2026-09-04"
    assert got["by_base"] == {"PLY": {mp.NO_POSITION: 1}}
    # Колонка «Код» выбрана точным совпадением, не «Код ИКАО».
    assert got["geo"]["coords"] == {"PLY": [69.0, 33.0], "SVG": [64.0, 40.0]}
    assert got["geo"]["complete"] is True


def test_column_marks_prefer_exact_then_prefix():
    # Точное совпадение сильнее начала заголовка; подстрока «роль» в
    # «Контроль» не считается.
    sheet = {"columns": ["Контроль", "Должность в экипаже"]}
    assert mp._col_by_marks(sheet, mp._POSITION_MARKS) == "Должность в экипаже"
    assert mp._col_by_marks({"columns": ["Контроль", "Роль"]},
                            mp._POSITION_MARKS) == "Роль"
    assert mp._col_by_marks({"columns": ["Контроль"]},
                            mp._POSITION_MARKS) is None
    assert mp._col_by_marks({"columns": ["Платформа", "Широта, °"]},
                            mp._LAT_MARKS) == "Широта, °"
