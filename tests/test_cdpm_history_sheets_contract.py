# -*- coding: utf-8 -*-
"""Листы раздела истории (спецификация 1.17) в линии данных.

Живой прогон 05.09.2026 на наборе «Полярис-H0-КИ» (первый набор с листами
«История» и «Утверждённые назначения») дал два дефекта контракта:

1. Лист «Утверждённые назначения» сошёл за плоды планирования (имя несёт
   «назначен»), правило «экипаж — ровно 2 пилота» скомпилировалось по нему
   и объявило БЛОКИРУЮЩЕЕ нарушение на закреплении одного пилота H01→PL101.
   Листы раздела истории — исходное состояние на начало горизонта, а не
   назначения решателя: компилятору не предлагаются, спецификация с таким
   листом отбраковывается, признак «есть плоды планирования» их не видит.
2. Гейт отчёта данных не знал класса «история» и требовал номер строки у
   находки полноты (сотрудник без строки истории) — красный гейт на
   законном отчёте. Класс внесён в перечень; адрес находки — строка
   сотрудника на листе экипажей.
"""
from __future__ import annotations

import copy

import pytest

from cdpm import data_checks as dc
from cdpm import data_report as dr
from cdpm import history_section as hs


def _book():
    return {
        "sources": [{"sid": "D1", "file": "BOOK.xlsx", "sha256": "a" * 64,
                     "rows_seen": 7}],
        "tables": {"D1": {
            "Экипажи": {"columns": ["Табельный номер", "База"],
                        "rows": [{"Табельный номер": "H01", "База": "SVG"},
                                 {"Табельный номер": "H02", "База": "SVG"},
                                 {"Табельный номер": "H03", "База": "SVG"}]},
            "Рейсы": {"columns": ["Рейс", "Дата"],
                      "rows": [{"Рейс": "PL101", "Дата": "04.09.2026"}]},
            "Свойства": {"columns": ["Свойство", "Значение"],
                         "rows": [{"Свойство": "planSince",
                                   "Значение": "04.09.2026 00:00"}]},
            "История": {"columns": ["Табельный номер", "Срез",
                                    "Налёт с начала месяца, мин"],
                        "rows": [{"Табельный номер": "H01",
                                  "Срез": "04.09.2026 00:00",
                                  "Налёт с начала месяца, мин": "600"},
                                 {"Табельный номер": "H02",
                                  "Срез": "04.09.2026 00:00",
                                  "Налёт с начала месяца, мин": "0"}]},
            "Утверждённые назначения": {
                "columns": ["Табельный номер", "Рейс", "Дата"],
                "rows": [{"Табельный номер": "H01", "Рейс": "PL101",
                          "Дата": "04.09.2026"}]},
        }},
    }


def test_history_sheets_are_not_solution_sheets():
    ds = _book()
    assert dc.history_sheet_keys(ds) == {("D1", "История"),
                                        ("D1", "Утверждённые назначения")}
    # «Утверждённые назначения» несёт «назначен», но плодом планирования
    # не является — признак молчит…
    assert dc._has_solution_sheets(ds) is False
    # …а настоящий лист назначений признак видит по-прежнему.
    ds2 = copy.deepcopy(ds)
    ds2["tables"]["D1"]["Назначения экипажа"] = {
        "columns": ["Табельный номер", "Рейс"], "rows": []}
    assert dc._has_solution_sheets(ds2) is True


def test_history_sheets_hidden_from_compiler_schema():
    schema = dc._schema_text(_book())
    assert "D1/Рейсы: колонки Рейс, Дата" in schema
    assert "D1/Утверждённые назначения: лист раздела истории" in schema
    assert "D1/История: лист раздела истории" in schema
    # Колонки листов истории компилятору не показываются — целиться нечем.
    assert "Налёт с начала месяца" not in schema


def test_validate_spec_rejects_history_sheet_as_target():
    ds = _book()
    spec = {"id": "C-R-1-0", "basis": {"rule": "R-1"},
            "template": "счёт-состава-группы",
            "params": {"sheet": "D1/Утверждённые назначения",
                       "group_column": "Рейс", "count": 2},
            "statement": "экипаж — 2 пилота", "unit": "рейс",
            "severity": "БЛОКИРУЮЩЕЕ", "status": "скомпилирована"}
    svod = {"R-1": {"id": "R-1", "kind": "HARD", "statement": "x"}}
    reason = dc.validate_spec(spec, ds, svod)
    assert reason and "раздел истории" in reason
    # Собственные шаблоны истории на своих листах — законны.
    own = {"id": "C-история-полнота-D1", "basis": {"builtin": "история"},
           "template": "история-полнота", "params": {"sheet": "D1/История"},
           "statement": "s", "unit": "сотрудник", "severity": "СЕРЬЁЗНОЕ",
           "status": "скомпилирована"}
    assert dc.validate_spec(own, ds, svod) is None


def test_completeness_finding_addresses_employee_row():
    ds = _book()
    n, findings = hs.run_check("история-полнота", {"sheet": "D1/История"}, ds)
    assert n == 3
    assert [f["target"] for f in findings] == [
        {"sid": "D1", "sheet": "Экипажи", "row": 4}]
    assert "H03" in findings[0]["text"]


def test_report_contract_accepts_history_checks_and_findings():
    """Отчёт данных с проверками «история-…» и находкой полноты проходит
    контракт ДЛ-1 (класс в перечне, адрес — строка листа экипажей)."""
    ds = _book()
    specs = [s for s in dc.builtin_checks(ds) if s["template"].startswith("история-")]
    assert {s["template"] for s in specs} == {
        "история-табельный", "история-значения", "история-срез", "история-полнота",
        "история-закрепления"}
    doc = dc.build_data_report("Полярис", ds, specs)
    assert "история" in dr.CLASSES and "история" in dr.BUILTIN_BASES
    problems = dr.validate_report(doc, {"rules": []})
    assert problems == [], problems
    by_class = {f["class"] for f in doc["findings"]}
    assert by_class == {"история"}
    (f,) = doc["findings"]
    assert f["target"] == {"sid": "D1", "sheet": "Экипажи", "row": 4}
    assert f["severity"] == "СЕРЬЁЗНОЕ"


@pytest.mark.parametrize("name", ["История", "история", "Утверждённые назначения",
                                  "approved_assignments"])
def test_history_aliases_recognised(name):
    ds = {"tables": {"D1": {name: {"columns": [], "rows": []}}}}
    assert dc.history_sheet_keys(ds) == {("D1", name)}
