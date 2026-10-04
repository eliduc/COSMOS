# -*- coding: utf-8 -*-
"""Нормы резерва судятся КОДОМ по сводке «Резерв» решения, не моделью.

Проход 21 финальной серии H0 (03.09): компилятор-модель выдал для
правила резерва спецификацию «диапазон без min и без max», её
отбраковали, и правило осталось непокрытым — при той же сводке, что в
соседних проходах. Что код проверяет точно, код и проверяет."""
from cdpm import data_checks as dc
from vmp.run import roster_certify as rc

SOL = rc.SOLUTION_SID


def _rule(rid="R-RES", fam="reserve_floors"):
    return {"id": rid, "kind": "HARD", "zone": "ROSTERING", "title": rid,
            "statement": "Ежедневно резерв: PLY ≥ 2 экипажа",
            "sources": [{"sid": "S1", "anchor": rid}],
            "template_fit": {"category": "A", "family": fam,
                             "registry_rev": "x" * 12,
                             "params": {"reserve_crews": {
                                 "value": "PLY:2", "quote": "PLY ≥ 2"}}}}


def _dataset(shortfalls):
    # Значения листов линии данных — строки (так их пишет _rows).
    cols = ["База", "Дата", "Экипажей", "Норма", "Нехватка"]
    rows = [dict(zip(cols, ("PLY", f"2026-09-0{i + 4}", "2", "2", str(s))))
            for i, s in enumerate(shortfalls)]
    return {"sources": [{"sid": SOL, "file": "р", "sha256": "0" * 64,
                         "rows_seen": len(rows)}],
            "tables": {SOL: {"Резерв": {"columns": cols, "rows": rows}}}}


def test_спецификация_резерва_строится_кодом_и_прогоняется():
    svod = {"rules": [_rule(), _rule("R-OTHER", "rest_floors")]}
    specs, rest = rc.reserve_code_checks(svod, _dataset([0, 0, 0]))
    assert [s["basis"]["rule"] for s in specs] == ["R-RES"]
    assert specs[0]["compiled_by"] == "код"
    assert [r["id"] for r in rest["rules"]] == ["R-OTHER"]
    n, findings = dc.run_spec(specs[0], _dataset([0, 0, 0]))
    assert n == 3 and findings == []


def test_нехватка_резерва_даёт_находку():
    specs, _rest = rc.reserve_code_checks({"rules": [_rule()]}, _dataset([0, 1, 0]))
    n, findings = dc.run_spec(specs[0], _dataset([0, 1, 0]))
    assert n == 3 and len(findings) == 1
    assert findings[0]["target"]["row"] == 3


def test_без_сводки_резерва_код_не_вмешивается():
    svod = {"rules": [_rule()]}
    specs, rest = rc.reserve_code_checks(svod, {"tables": {SOL: {}}})
    assert specs == [] and rest is svod
