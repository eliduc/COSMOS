# -*- coding: utf-8 -*-
"""Структурное поле перечня кодов у определения-перечня (заявка COSMOS
03.09, RAZDELENIE п.5 сообщения): коды с цитатами ставит код из клаузы,
поле едет в форму как machine.codes и возвращается без потерь."""
from cdpm import extract as ex
from cdpm import model as m
from cdpm import vmp_form as vf

CLAUSE = ("Базы приписки экипажей: SVG (Северогорск), PLY (Полярный), GRT "
          "(Гранитный); главная база — PLY.")


def test_codes_from_clause_with_quotes():
    codes = m.codes_from_clause(CLAUSE)
    assert [c["code"] for c in codes] == ["SVG", "PLY", "GRT"]
    assert codes[0]["quote"] == "SVG (Северогорск)"
    assert codes[1]["quote"] == "PLY (Полярный)"
    # Служебные слова формы кодами не считаются; повтор не дублируется.
    assert m.codes_from_clause("Вид DEF; ставки в UTC; PLY и снова PLY") == [
        {"code": "PLY", "quote": "PLY"}]


def test_definition_row_carries_codes_and_form_keeps_them():
    asm = ex._Assembler("т", [{"sid": "S1", "file": "a.docx", "sha256": "0" * 64}])
    asm.add_candidate("S1", {"anchor": "абз.5", "title": "Базы приписки",
                             "statement": "Базы: SVG, PLY, GRT; главная — PLY.",
                             "zone": "BOTH", "kind": "DEF"}, clause_text=CLAUSE)
    rule = asm.doc["rules"][0]
    assert [c["code"] for c in rule["codes"]] == ["SVG", "PLY", "GRT"]
    assert m.validate_schema(asm.doc) == []
    form = vf.to_form(asm.doc)
    row = form["common"]["definitions"][0]
    assert [c["code"] for c in row["machine"]["codes"]] == ["SVG", "PLY", "GRT"]
    back = vf.from_form(form)
    assert back["rules"][0]["codes"] == rule["codes"]
    assert vf.canonical_diff(asm.doc, back) == []
    # Одиночный код — тоже перечень: поле есть ДОКАЗАТЕЛЬНЫЙ слой
    # «какие коды названы клаузой», а не перечень сущностей мира
    # (семантика приёмника, подтверждена COSMOS 03.09). Прежний порог
    # «два и более» молчал о парке из одного типа судов, а набирался у
    # него лишь номером записи документа, то есть по ошибке.
    asm.add_candidate("S1", {"anchor": "абз.6", "title": "Главная база",
                             "statement": "Главная база — PLY.", "zone": "BOTH",
                             "kind": "DEF"}, clause_text="Главная база — PLY.")
    assert [c["code"] for c in asm.doc["rules"][1]["codes"]] == ["PLY"]
    # Клауза без кодов поля не получает.
    asm.add_candidate("S1", {"anchor": "абз.7", "title": "Порядок",
                             "statement": "Планирование ведётся помесячно.",
                             "zone": "BOTH", "kind": "DEF"},
                      clause_text="Планирование ведётся помесячно.")
    assert "codes" not in asm.doc["rules"][2]


def test_номер_записи_документа_кодом_не_становится():
    """Находка приёмника COSMOS 03.09: клауза набора начинается с
    номера записи («VN-003. Базы приписки…»), и номер уезжал в
    перечень кодов — приёмник честно говорил «кода VN-003 в данных
    нет». Судит ПОЛОЖЕНИЕ: номер стоит в начале клаузы и отделён
    точкой. По форме номер записи неотличим от живого кода перечня —
    «КТ-001» в начале это нумерация, а «ЛТ-100» в теле той же клаузы
    остаётся кодом."""
    assert [c["code"] for c in m.codes_from_clause(
        "VN-003. Базы приписки экипажей: SVG (Северогорск), PLY, GRT.")]         == ["SVG", "PLY", "GRT"]
    assert [c["code"] for c in m.codes_from_clause(
        "КТ-001. Перечень: ЧКЭ, ЛТ-100.")] == ["ЧКЭ", "ЛТ-100"]
    # Живые коды парка формой похожи на номера и обязаны уцелеть.
    assert [c["code"] for c in m.codes_from_clause(
        "Парк: Ту-204, B-737, A320.")] == ["Ту-204", "B-737", "A320"]
    # Скобка нумерации — тот же случай, что точка.
    assert [c["code"] for c in m.codes_from_clause(
        "R-12) Список: SVG.")] == ["SVG"]


def test_codes_of_both_alphabets_and_model_designations():
    """Замечание COSMOS 03.09: типы судов пишутся кириллицей и с цифрами
    — код перечня берётся целиком, вместе с дефисной частью."""
    got = m.codes_from_clause("Типы судов парка: Полярис-Р200 (базовый), "
                              "ЛТ-100 (региональный), Ту-204.")
    assert [c["code"] for c in got] == ["Полярис-Р200", "ЛТ-100", "Ту-204"]
    assert got[0]["quote"] == "Полярис-Р200 (базовый)"
    assert [c["code"] for c in m.codes_from_clause("Флот: A320, B737-800, SSJ-100.")] \
        == ["A320", "B737-800", "SSJ-100"]
    # Аббревиатуры кириллицей — коды; обычные слова и величины — нет.
    assert [c["code"] for c in m.codes_from_clause("Роли: КВС и ВП.")] == ["КВС", "ВП"]
    assert m.codes_from_clause("Минимальный отдых между сменами дома — 12:00 "
                               "(720 минут).") == []
    assert m.codes_from_clause("Настоящий документ утверждён приказом; вид DEF.") == []
