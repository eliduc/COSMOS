# -*- coding: utf-8 -*-
"""Лестница наборов Л0–Л3 против измерителя (open-bench/ladder).

Ступени строятся из выдуманного мира и несут независимую таблицу
ожиданий; тест закрепляет калибровку измерителя: служебная клауза не
становится нормой, норма не теряется, ожидаемые величины находятся.
Изменение эвристик нормативности или разбора величин обязано ронять
этот тест, а не обнаруживаться на живом прогоне.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
LADDER = HERE / "open-bench" / "ladder"


def _mod(name: str):
    spec = importlib.util.spec_from_file_location(name, LADDER / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def steps(tmp_path_factory):
    build = _mod("build_ladder")
    out = tmp_path_factory.mktemp("ladder")
    build.main(["--out", str(out)])
    return out


@pytest.mark.parametrize("step", ["Л0", "Л1", "Л2", "Л3", "Л4", "Л5"])
def test_ступень_проходит_измерителем(steps, step):
    ev = _mod("evaluate_ladder")
    rep = ev.evaluate(steps / step)
    c = rep["counts"]
    assert c["false_normative"] == 0, rep["false_normative"]
    assert c["false_service"] == 0, rep["false_service"]
    assert c["values_missed"] == 0, rep["missed_values"]
    assert c["unclassified_tokens"] == 0
    assert c["normativity_ok"] == c["clauses"]
    # Баланс на эталонном своде ступени: у безошибочного разбора
    # структурные классы нулевые, а весь остаток закрыт вопросом.
    b = rep.get("balance")
    if b and b["rows"]:
        assert b["clean"], b["problems"]
        assert b["rows_on_nonnormative"] == 0
        assert b["fields_without_carrier"] == 0
        assert b["unconsumed_without_addressee"] == 0, b


def test_шум_ступеней_растёт_а_смысл_один(steps):
    """Ступени несут ОДИН и тот же смысл: число нормативных записей от
    шума не меняется, растёт только служебная часть."""
    import json
    norm = {}
    for step in ("Л0", "Л1", "Л2", "Л3"):
        doc = json.loads((steps / step / "expectations.json")
                         .read_text(encoding="utf-8"))
        norm[step] = sum(1 for e in doc["expect"] if e["normative"])
        assert doc["expect"], step
    assert norm["Л0"] == norm["Л1"] == norm["Л2"] == 8
    # Л3 добавляет противоречие, правило без данных, предписание
    # настоящим временем и норму со ссылкой в хвосте (чистая отсылка
    # нормой не считается — она порождает вопрос).
    assert norm["Л3"] == 12


def test_ловушки_калибровки_найденные_лестницей():
    """Три класса, найденные ступенями Л2–Л3 (2026-09-02): словарь по
    подстроке, запрет без словарного слова, отсылка к документу."""
    from cdpm import measure as ms
    assert ms.normativity("Продолжение таблицы 4")["normative"] is False
    assert ms.normativity(
        "Экипаж с истёкшим допуском к полётам не назначается на рейс."
    )["normative"] is True
    ref = ms.normativity(
        "Нормы отдыха при пересечении часовых поясов установлены "
        "Приложением 5.")
    assert ref["normative"] is False and ref["reference"] is True
    # Норма со словарным словом и величиной остаётся нормой.
    assert ms.normativity(
        "Полётная смена не должна превышать 11:30.")["normative"] is True


def test_носителем_табличной_нормы_служит_строка_с_шапкой():
    """Ловушка ступени Л4 (2026-09-03): в таблице модальность живёт в
    шапке колонки, величина — в клетке, предмет — в соседней. Клетка
    нормы не несёт, строка с шапкой — несёт, а сама шапка не несёт
    никогда, как бы ни была сформулирована."""
    from cdpm import measure as ms
    cells = {
        "S1|табл.1, строка 1, кол 1": {
            "sid": "S1", "anchor": "табл.1, строка 1, кол 1",
            "text": "Показатель"},
        "S1|табл.1, строка 1, кол 2": {
            "sid": "S1", "anchor": "табл.1, строка 1, кол 2",
            "text": "Предельное значение (не более)"},
        "S1|табл.1, строка 2, кол 1": {
            "sid": "S1", "anchor": "табл.1, строка 2, кол 1",
            "text": "Полётная смена"},
        "S1|табл.1, строка 2, кол 2": {
            "sid": "S1", "anchor": "табл.1, строка 2, кол 2",
            "text": "11:30"},
    }
    of, text, members, roles = ms.compose_carriers(cells)
    row = "S1|табл.1, строка 2"
    head = "S1|табл.1, строка 1"
    assert of["S1|табл.1, строка 2, кол 2"] == row
    assert len(members[row]) == 2 and roles.get(head) == "header"
    # Клетка сама по себе нормы не несёт, носитель — несёт.
    assert ms.normativity("11:30")["normative"] is False
    assert ms.normativity(text[row])["normative"] is True
    # Шапка не норма, хотя модальное слово в ней есть.
    assert ms.normativity(text[head], roles[head])["normative"] is False


def test_язык_записи_не_меняет_нормативности():
    """Ловушка ступени Л5 (2026-09-03): руководства многих перевозчиков
    ведутся на английском. Норма остаётся нормой, служебный текст —
    служебным; словари объявляются по языкам, тест нормативности один."""
    from cdpm import measure as ms
    assert ms.normativity(
        "The flight duty period shall not exceed 11:30.")["normative"]
    assert ms.normativity(
        "Crew members must be given at least 11 hours of rest at the home "
        "base.")["normative"]
    assert ms.normativity(
        "A penalty of 8 points applies for each night away from the home "
        "base.")["normative"]
    assert ms.normativity(
        "In-flight rest time is counted towards the flight duty period."
    )["signs"]["предписание настоящим временем"]
    for service in ("Crew Planning Manual", "Table of Contents",
                    "Chapter 3. Flight Time Limitations", "Page 4 of 12",
                    "Director of Flight Operations",
                    "Continued on next page"):
        assert ms.normativity(service)["normative"] is False, service
    # Единица величины читается по-английски наравне с русской.
    kinds = {v.text for v in ms.iter_values(
        "The minimum connection time between flights is 35 minutes.")}
    assert any("35 minutes" == t for t in kinds), kinds


def test_адрес_клетки_читается_и_из_якоря_и_из_текста():
    """Способ записи адреса задаёт читатель источника, смысл — нет
    (инвариант И-6): координата в якоре и координата в начале текста
    дают одного и того же носителя."""
    from cdpm import measure as ms
    assert ms.cell_address("табл.2, строка 4, кол 3") == ("табл.2", 4, 3)
    assert ms.cell_address("", "[табл.2, строка 4, кол 3] 11:30") \
        == ("табл.2", 4, 3)
    # Вложенная таблица: носителем служит самая глубокая строка.
    assert ms.cell_address(
        "табл.2, строка 4, кол 3, вл.табл.1, строка 2, кол 1"
    ) == ("табл.2, строка 4, кол 3, вл.табл.1", 2, 1)
    # Строка листа книги — уже носитель, колонки в адресе нет.
    assert ms.cell_address("лист «Реестр», строка 7") \
        == ("лист «Реестр»", 7, None)
    assert ms.cell_address("п.4.2") is None
