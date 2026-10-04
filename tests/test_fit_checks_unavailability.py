# -*- coding: utf-8 -*-
"""Семья crew_unavailability (каталог 0.6.0, набор Н0-С): исполнитель
сертификата ростера кодом по исходному листу «Отпуска».

Первый проход Н0-С через COSMOS (17.09.2026, пакет 236f5235): решатель
исполнил «Employee unavailable», сверка с ИДАК прошла 14/0, а сертификат
остался «не удостоверено: непроверено 1 правил (VN-027), проверок не
скомпилировано 1» — семьи не было в диспетчере проверок по посадке, и
правило ушло компилятору, у которого нет формы «интервал двух колонок».

Стенды: чистое решение — проверка прогнана по всем назначениям без
находок и правило компилятору не подаётся; назначение в день отпуска
(по вылету, по прилёту, на границе интервала) — находка со строкой
назначения и строкой листа «Отпуска»; нечитаемые или неоднозначные
исходные данные — вся проверка «не прогнана» с причиной (fail-closed);
пустой лист «Отпуска» — законное «никто не недоступен»; сквозной
сертификат выдан на чистом решении и не выдан при нарушении.
"""
from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from vmp.run import roster_certify as rc          # noqa: E402
from vmp.run import fit_checks as fc              # noqa: E402

RID = "R-S2-лист-«VMP-H0-2»-строка-5"


def _rule():
    return {"id": RID, "title": "Отпуск",
            "statement": "Член экипажа не может быть назначен на рейс в дни своего отпуска.",
            "zone": "ROSTERING", "kind": "HARD",
            "template_fit": {"category": "A", "family": "crew_unavailability",
                             "registry_rev": "7dc0626d", "params": {}},
            "sources": [{"sid": "S2", "anchor": "лист «VMP-H0-2», строка 5"}]}


def _assignment(emp, flight, dep, arr):
    return {"employee": emp, "flight": f"{flight}|{dep}", "dep_time": dep,
            "arr_time": arr, "dep_airport": "PLY", "arr_airport": "SVG",
            "duty_id": f"{emp}/D1", "base": "PLY", "role": "Captain"}


CLEAN = [_assignment("H02", "PL301", "2026-09-04T06:00:00", "2026-09-04T07:30:00"),
         _assignment("H06", "PL301", "2026-09-05T06:00:00", "2026-09-05T07:30:00")]
VACATION = [{"Табельный номер": "H02", "С": "2026-09-05", "По": "2026-09-06"}]


def _source(rows=None):
    return {"D1": {"Отпуска": {"columns": ["Табельный номер", "С", "По"],
                               "rows": deepcopy(VACATION) if rows is None else rows}}}


def _dataset(assignments=None, rows=None):
    solution = {"solution": "стенд отпусков",
                "assignments": deepcopy(assignments if assignments is not None else CLEAN)}
    return rc.build_solution_dataset(solution, _source(rows))


def test_clean_roster_is_checked_by_code_and_never_reaches_the_compiler():
    rule, data = _rule(), _dataset()
    before = deepcopy(data)
    specs, checks, findings, remaining = fc.fit_code_checks({"rules": [rule]}, data)
    assert not specs and not findings and remaining["rules"] == []
    check = checks[0]
    assert check["id"] == f"C-{RID}-посадка" and check["compiled_by"] == fc.COMPILED_BY
    assert check["status"] == "прогнана" and check["rows_checked"] == 2
    assert check["unavailable_employees"] == 1
    by_emp = {a["employee"]: a for a in check["unavailability_assessments"]}
    assert by_emp["H02"]["unavailability_rows"] == [2] and by_emp["H02"]["violating_rows"] == []
    assert by_emp["H06"]["unavailability_rows"] == []
    assert data == before


def test_assignment_in_vacation_day_is_a_violation_with_both_rows_named():
    rule = _rule()
    variants = {
        "по вылету": _assignment("H02", "PL301", "2026-09-05T06:00:00", "2026-09-05T07:30:00"),
        "по прилёту": _assignment("H02", "PL201", "2026-09-04T23:00:00", "2026-09-05T00:30:00"),
        "граница «По»": _assignment("H02", "PL301", "2026-09-06T06:00:00", "2026-09-06T07:30:00"),
        "граница «С»": _assignment("H02", "PL301", "2026-09-05T00:00:00", "2026-09-05T01:00:00"),
    }
    for name, bad in variants.items():
        data = _dataset([CLEAN[1], bad])
        check, findings = fc._crew_unavailability(RID, rule, data, fc._Counter())
        assert check["status"] == "прогнана" and check["rows_checked"] == 2, name
        assert len(findings) == 1, name
        f = findings[0]
        assert f["rule_refs"] == [RID] and f["target"] == {"sid": "SOL", "sheet": "Назначения", "row": 3}, name
        assert "H02" in f["text"] and "недоступный день" in f["text"], name
        assert "D1/Отпуска!2" in f["evidence"], name


def test_day_after_vacation_and_other_employee_are_clean():
    rule = _rule()
    data = _dataset([_assignment("H02", "PL301", "2026-09-07T06:00:00", "2026-09-07T07:30:00"),
                     _assignment("H06", "PL301", "2026-09-06T06:00:00", "2026-09-06T07:30:00")])
    check, findings = fc._crew_unavailability(RID, rule, data, fc._Counter())
    assert check["status"] == "прогнана" and not findings


def test_deadhead_does_not_exempt_from_unavailability():
    rule = _rule()
    bad = dict(_assignment("H02", "PL301", "2026-09-05T06:00:00", "2026-09-05T07:30:00"),
               deadhead=True)
    data = _dataset([CLEAN[1], bad])
    check, findings = fc._crew_unavailability(RID, rule, data, fc._Counter())
    assert check["status"] == "прогнана" and len(findings) == 1


def test_empty_vacation_sheet_means_nobody_unavailable():
    rule = _rule()
    data = _dataset(rows=[])
    check, findings = fc._crew_unavailability(RID, rule, data, fc._Counter())
    assert check["status"] == "прогнана" and check["rows_checked"] == 2
    assert check["unavailable_employees"] == 0 and not findings


def test_unreadable_or_ambiguous_source_fails_closed_without_findings():
    rule = _rule()
    bad = _assignment("H02", "PL301", "2026-09-05T06:00:00", "2026-09-05T07:30:00")
    original = _dataset([CLEAN[1], bad])
    mutations = ("bad-date", "reversed", "empty-employee", "missing-sheet",
                 "wrong-schema", "two-sources", "bad-assignment-time")
    for mutation in mutations:
        data = deepcopy(original)
        if mutation == "bad-date":
            data["tables"]["D1"]["Отпуска"]["rows"][0]["По"] = "не дата"
        elif mutation == "reversed":
            data["tables"]["D1"]["Отпуска"]["rows"][0]["С"] = "2026-09-07"
        elif mutation == "empty-employee":
            data["tables"]["D1"]["Отпуска"]["rows"][0]["Табельный номер"] = ""
        elif mutation == "missing-sheet":
            del data["tables"]["D1"]["Отпуска"]
        elif mutation == "wrong-schema":
            data["tables"]["D1"]["Отпуска"]["columns"] = ["Табельный номер", "С"]
        elif mutation == "two-sources":
            data["tables"]["D2"] = deepcopy(data["tables"]["D1"])
        else:
            data["tables"]["SOL"]["Назначения"]["rows"][1]["Время вылета"] = "вчера"
        check, findings = fc._crew_unavailability(RID, rule, data, fc._Counter())
        assert check["status"] == "не прогнана" and check["rows_checked"] == 0, mutation
        assert check["reason"] and not findings, mutation
    # Правило берётся кодом и без исходного листа: компилятору оно не
    # подаётся, а сертификат видит честную «не прогнана» с причиной.
    data = deepcopy(original)
    del data["tables"]["D1"]["Отпуска"]
    _specs, checks, _findings, remaining = fc.fit_code_checks({"rules": [rule]}, data)
    assert remaining["rules"] == [] and checks[0]["status"] == "не прогнана"
    assert "Отпуска" in checks[0]["reason"]


PROMPTS: list[str] = []


def _compiler(prompt):
    PROMPTS.append(prompt)
    return json.dumps({"checks": [], "skipped": []}, ensure_ascii=False)


def _svod():
    return {"meta": {"customer": "Полярис-H0-C", "mode": 1, "cdpm_version": "0.1"},
            "sources": [{"sid": "S2", "file": "ID-H0C-VMP-2.xlsx", "sha256": "0" * 64}],
            "rules": [_rule()], "questions": [], "objectives": []}


def _solution(bad: bool):
    rows = [CLEAN[1], (_assignment("H02", "PL301", "2026-09-05T06:00:00", "2026-09-05T07:30:00")
                       if bad else CLEAN[0])]
    duties = [{"employee": a["employee"], "id": a["duty_id"], "start": a["dep_time"],
               "end": a["arr_time"], "base": "PLY", "last_arr_airport": "SVG"} for a in rows]
    return {"solution": "стенд отпусков", "assignments": deepcopy(rows), "duties": duties,
            "reference_tables": _source(),
            "window_horizon": {"start_inclusive": "2026-09-04T00:00",
                               "end_exclusive": "2026-09-07T00:00"},
            "window_employees": [{"employee": e, "base": "PLY"} for e in ("H02", "H06")],
            "history_policy": {"coverage_kind": "none", "assume_zero_when_missing": True,
                               "user_decision": {"question": "Q-3b-история-нет",
                                                 "decision": "continue_zero"}}}


def test_certificate_is_issued_on_clean_roster_and_withheld_on_violation():
    for bad in (False, True):
        PROMPTS.clear()
        report = rc.certify(_svod(), _solution(bad), _compiler)
        assert all(RID not in p for p in PROMPTS), "правило семьи отпусков ушло компилятору"
        cert = report["certificate"]
        checks = {c["id"]: c for c in report["checks"]}
        assert checks[f"C-{RID}-посадка"]["status"] == "прогнана"
        assert cert.get("checks_not_compiled", 0) == 0
        assert RID not in (cert.get("rules_uncovered") or [])
        if bad:
            assert cert["certified"] is False and cert["violated_rules"] == [RID]
            assert any(f["rule_refs"] == [RID] for f in report["findings"])
        else:
            assert cert["certified"] is True and not cert["violated_rules"]
