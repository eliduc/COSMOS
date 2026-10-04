# -*- coding: utf-8 -*-
"""Ревизия 1.17.1 (05.09.2026, по ревью дельты раската №8).

1. Канон колонок листа «Утверждённые назначения»: «Рейс» и «Дата» под
   каноническими ключами независимо от регистра и синонимов заголовков;
   прочие колонки — дословно.
2. Проверка линии данных «история-закрепления»: табельный в «Экипажах»,
   «Рейс» и «Дата» читаются, рейс есть в листе «Рейсы» на эту дату —
   иначе БЛОКИРУЮЩЕЕ до пакета (потребитель отказал бы всему нажатию).
3. Строка листа «История» без единого значения — запись без истории:
   блоки null (не словари из null), провенанс строки сохраняется,
   в coverage.with_history не считается.
"""
from __future__ import annotations

import copy

from cdpm import data_checks as dc
from cdpm import history_section as hs


def _book(approved_columns=("Табельный номер", "Номер рейса", "Дата вылета", "Примечание"),
          approved_rows=None, history_rows=None):
    approved_rows = approved_rows if approved_rows is not None else [
        {"Табельный номер": "H01", "Номер рейса": "PL101",
         "Дата вылета": "04.09.2026", "Примечание": "по заявке"}]
    history_rows = history_rows if history_rows is not None else [
        {"Табельный номер": "H01", "Срез": "04.09.2026 00:00",
         "Налёт с начала месяца, мин": "600"},
        {"Табельный номер": "H02", "Срез": "", "Налёт с начала месяца, мин": ""},
    ]
    return {
        "sources": [{"sid": "D1", "file": "BOOK.xlsx", "sha256": "a" * 64, "rows_seen": 9}],
        "tables": {"D1": {
            "Экипажи": {"columns": ["Табельный номер", "База"],
                        "rows": [{"Табельный номер": "H01", "База": "SVG"},
                                 {"Табельный номер": "H02", "База": "SVG"},
                                 {"Табельный номер": "H03", "База": "SVG"}]},
            "Рейсы": {"columns": ["Номер рейса", "Дата", "Вылет"],
                      "rows": [{"Номер рейса": "PL101", "Дата": "04.09.2026", "Вылет": "SVG"},
                               {"Номер рейса": "PL102", "Дата": "05.09.2026", "Вылет": "PLY"}]},
            "Свойства": {"columns": ["Свойство", "Значение"],
                         "rows": [{"Свойство": "planSince", "Значение": "04.09.2026 00:00"}]},
            "История": {"columns": ["Табельный номер", "Срез", "Налёт с начала месяца, мин"],
                        "rows": history_rows},
            "Утверждённые назначения": {"columns": list(approved_columns), "rows": approved_rows},
        }},
    }


def test_approved_assignment_keys_are_canonical():
    sec = hs.history_section(_book())
    h01 = next(e for e in sec["employees"] if e["employee_id"] == "H01")
    (item,) = h01["approved_assignments"]
    assert item["Рейс"] == "PL101" and item["Дата"] == "04.09.2026"
    assert "Номер рейса" not in item and "Дата вылета" not in item
    # Прочие колонки — дословно, провенанс — строка листа.
    assert item["Примечание"] == "по заявке"
    assert item["provenance"] == {"sid": "D1", "sheet": "Утверждённые назначения", "row": 2}
    assert hs.approved_key("РЕЙС") == "Рейс" and hs.approved_key("date") == "Дата"
    assert hs.approved_key("Примечание") == "Примечание"


def test_empty_history_row_is_record_without_history():
    sec = hs.history_section(_book())
    h02 = next(e for e in sec["employees"] if e["employee_id"] == "H02")
    for block in ("month_to_date", "rolling", "last_duty", "series", "location"):
        assert h02[block] is None, block
    # Строка была — провенанс сохранён; в покрытие не считается.
    assert h02["provenance"] == {"sid": "D1", "sheet": "История", "row": 3}
    assert h02["as_of"] == "2026-09-04T00:00"
    assert sec["coverage"] == {"employees_total": 3, "with_history": 1, "kind": "partial"}
    h03 = next(e for e in sec["employees"] if e["employee_id"] == "H03")
    assert h03["provenance"] is None and h03["month_to_date"] is None


def test_pins_check_is_compiled_only_with_the_sheet():
    specs = hs.check_specs(_book())
    pins = [s for s in specs if s["template"] == "история-закрепления"]
    assert len(pins) == 1
    assert pins[0]["params"] == {"sheet": "D1/Утверждённые назначения"}
    assert pins[0]["severity"] == "БЛОКИРУЮЩЕЕ"
    book = _book()
    del book["tables"]["D1"]["Утверждённые назначения"]
    assert not [s for s in hs.check_specs(book) if s["template"] == "история-закрепления"]


def test_pins_check_green_on_canonical_book():
    ds = _book()
    n, findings = hs.run_check("история-закрепления", {"sheet": "D1/Утверждённые назначения"}, ds)
    assert (n, findings) == (1, [])


def test_pins_check_catches_every_refusal_reason():
    ds = _book(approved_rows=[
        {"Табельный номер": "H01", "Номер рейса": "PL101", "Дата вылета": "04.09.2026", "Примечание": ""},
        {"Табельный номер": "H09", "Номер рейса": "PL101", "Дата вылета": "04.09.2026", "Примечание": ""},
        {"Табельный номер": "H02", "Номер рейса": "", "Дата вылета": "04.09.2026", "Примечание": ""},
        {"Табельный номер": "H02", "Номер рейса": "PL102", "Дата вылета": "видимо завтра", "Примечание": ""},
        {"Табельный номер": "H03", "Номер рейса": "PL102", "Дата вылета": "04.09.2026", "Примечание": ""},
        {"Табельный номер": "H03", "Номер рейса": "PL999", "Дата вылета": "04.09.2026", "Примечание": ""},
    ])
    n, findings = hs.run_check("история-закрепления", {"sheet": "D1/Утверждённые назначения"}, ds)
    assert n == 6
    texts = [f["text"] for f in findings]
    rows = [f["target"]["row"] for f in findings]
    assert rows == [3, 4, 5, 6, 7]
    assert "не найден в листе «Экипажи»" in texts[0]
    assert "без читаемых «Рейс» и «Дата»" in texts[1] and "без читаемых" in texts[2]
    assert "нет в листе «Рейсы» на 2026-09-04" in texts[3]   # PL102 летает 05.09
    assert "нет в листе «Рейсы» на 2026-09-04" in texts[4]   # PL999 не существует
    # Без листа «Рейсы» сверить нельзя — честная находка, не молчание.
    ds2 = _book()
    del ds2["tables"]["D1"]["Рейсы"]
    _n, f2 = hs.run_check("история-закрепления", {"sheet": "D1/Утверждённые назначения"}, ds2)
    assert len(f2) == 1 and "листа «Рейсы»" in f2[0]["text"]


def test_pins_check_runs_through_data_line_and_report_contract():
    """Спецификация исполняется штатным диспетчером run_spec, отчёт
    проходит контракт (класс «история», адрес — строка листа закреплений)."""
    from cdpm import data_report as dr
    ds = _book(approved_rows=[
        {"Табельный номер": "H01", "Номер рейса": "PL777", "Дата вылета": "04.09.2026", "Примечание": ""}])
    specs = [s for s in dc.builtin_checks(ds) if s["template"] == "история-закрепления"]
    assert len(specs) == 1
    assert dc.validate_spec(specs[0], ds, {}) is None
    doc = dc.build_data_report("Полярис", ds, specs)
    assert dr.validate_report(doc, {"rules": []}) == []
    (f,) = doc["findings"]
    assert f["severity"] == "БЛОКИРУЮЩЕЕ" and f["class"] == "история"
    assert f["target"] == {"sid": "D1", "sheet": "Утверждённые назначения", "row": 2,
                           "column": "Номер рейса"}


def test_history_section_is_stable_under_header_case():
    a = hs.history_section(_book())
    b = hs.history_section(_book(approved_columns=("Табельный номер", "РЕЙС", "дата", "Примечание"),
                                 approved_rows=[{"Табельный номер": "H01", "РЕЙС": "PL101",
                                                 "дата": "04.09.2026", "Примечание": "по заявке"}]))
    ia = copy.deepcopy(a["employees"][0]["approved_assignments"][0])
    ib = copy.deepcopy(b["employees"][0]["approved_assignments"][0])
    assert ia == ib
