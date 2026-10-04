# -*- coding: utf-8 -*-
"""Детерминированные проверки сертификата из посадки правил (решение
владельца 05.09.2026, пункт 3 после аудита).

Стенды с известным ответом: посаженные правила семей crew_composition,
duty_limits, rest_floors, connection_windows компилятору-модели не
подаются вовсе; на решении с заложенными нарушениями каждое из них
находится кодом с доказательством по строкам; на чистом решении —
сертификат выдан, гейт сертификата CDPM зелёный в обоих случаях.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from vmp.run import roster_certify as rc          # noqa: E402
from vmp.run import fit_checks as fc              # noqa: E402
from cdpm.gates import gate_certify               # noqa: E402


def _rule(rid, family, params, zone="ROSTERING"):
    return {"id": rid, "title": rid, "statement": "правило " + rid,
            "zone": zone, "kind": "HARD",
            "template_fit": {"category": "A", "family": family,
                             "params": {k: {"value": v} for k, v in params.items()}},
            "sources": [{"sid": "S1", "anchor": "п.1"}]}


def _svod():
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
        "rules": [
            _rule("R-CREW", "crew_composition", {"crew_size": 2}, "BOTH"),
            _rule("R-DUTY", "duty_limits", {"max_duty_duration": 720}, "BOTH"),
            _rule("R-LAND", "duty_limits", {"max_landings_per_duty": 1}),
            _rule("R-DAYS", "duty_limits", {"max_pairing_days": 2}, "PAIRING"),
            _rule("R-REST-H", "rest_floors", {"min_rest_home": 720}),
            _rule("R-REST-O", "rest_floors", {"min_rest_outstation": 600}),
            _rule("R-SIT", "connection_windows", {"min_turnaround": 45}, "BOTH"),
        ],
        "questions": [], "objectives": [],
    }


def _solution(bad: bool):
    sit_dep = "2026-01-15T08:30" if bad else "2026-01-15T09:00"   # стыковка 30 / 60 мин
    e1_next = "2026-01-15T20:00" if bad else "2026-01-16T06:00"   # отдых дома 570 / 1170 мин
    e2_next = "2026-01-15T19:00" if bad else "2026-01-16T06:00"   # отдых вне базы 510 / 1170 мин
    # вторые смены короче предела 720 мин в обоих вариантах (9 ч / 3 ч)
    e1_end = "2026-01-16T05:00" if bad else "2026-01-16T09:00"
    e2_end = "2026-01-16T04:00" if bad else "2026-01-16T09:00"
    chain_end = "2026-01-17T10:00" if bad else "2026-01-16T10:00"  # 3 / 2 суток
    assignments = []
    for emp in ("E1", "E2"):
        assignments += [
            {"employee": emp, "flight": "PL101", "dep_time": "2026-01-15T06:00",
             "arr_time": "2026-01-15T08:00", "dep_airport": "PLY",
             "arr_airport": "SVG", "duty_id": f"{emp}/D1", "base": "PLY"},
            {"employee": emp, "flight": "PL102", "dep_time": sit_dep,
             "arr_time": "2026-01-15T10:30", "dep_airport": "SVG",
             "arr_airport": "PLY" if emp == "E1" else "SVG",
             "duty_id": f"{emp}/D1", "base": "PLY"}]
    duties = [
        {"employee": "E1", "id": "E1/D1", "start": "2026-01-15T06:00",
         "end": "2026-01-15T10:30", "base": "PLY", "last_arr_airport": "PLY"},
        {"employee": "E1", "id": "E1/D2", "start": e1_next,
         "end": e1_end, "base": "PLY", "last_arr_airport": "PLY"},
        {"employee": "E2", "id": "E2/D1", "start": "2026-01-15T06:00",
         "end": "2026-01-15T10:30", "base": "PLY", "last_arr_airport": "SVG"},
        {"employee": "E2", "id": "E2/D2", "start": e2_next,
         "end": e2_end, "base": "PLY", "last_arr_airport": "PLY"},
    ]
    pairings = [{"employee": "E1", "id": "P1", "start": "2026-01-15T06:00",
                 "end": chain_end}]
    # Строгая сертификация отдыха включает начальную границу (C-39):
    # стенд несёт объявленный горизонт и известную предыдущую смену
    # (обе первые смены — отдых 750 мин), а не выводит их из назначений.
    history = [{"employee": emp, "has_history": True,
                "as_of": "2026-01-15T00:00",
                "last_duty_end": "2026-01-14T17:30",
                "last_duty_station": "PLY"} for emp in ("E1", "E2")]
    return {"solution": "стенд посадки", "assignments": assignments,
            "duties": duties, "pairings": pairings, "history": history,
            "window_horizon": {"start_inclusive": "2026-01-15T00:00",
                               "end_exclusive": "2026-01-18T00:00"},
            "window_employees": [{"employee": emp, "base": "PLY"}
                                 for emp in ("E1", "E2")]}


def _zero_policy_solution(bad: bool):
    """Тот же стенд без истории, но с решением continue_zero (main
    1.17.2): первый отдых судится по названному допущению."""
    sol = _solution(bad)
    sol.pop("history")
    sol["history_policy"] = {"coverage_kind": "none", "assume_zero_when_missing": True,
                             "user_decision": {"question": "Q-3b-история-нет",
                                               "decision": "continue_zero"}}
    return sol


PROMPTS: list[str] = []


def _fleet_fixture():
    """Независимые исходные строки для проверки допусков КН0 (C-40)."""
    rule = _rule("R-S1-абз-11", "fleet_exclusions", {"fleet_types": "Полярис-Р200"}, "BOTH")
    source = {"D1": {
        "Рейсы": {"columns": ["Номер рейса", "Дата", "Время вылета", "Тип ВС"],
                  "rows": [{"Номер рейса": "PL101", "Дата": "2026-09-04",
                            "Время вылета": "08:00", "Тип ВС": "Полярис-Р200"}]},
        "Допуски": {"columns": ["Табельный номер", "Тип ВС", "Действует с", "Действует до"],
                    "rows": [{"Табельный номер": "E1", "Тип ВС": "Полярис-Р200",
                              "Действует с": "2026-09-04", "Действует до": "2026-09-04"}]}}}
    solution = {"solution": "КН0-fleet-fixture", "assignments": [{
        "employee": "E1", "flight": "PL101|2026-09-04T08:00:00",
        "dep_time": "2026-09-04T08:00:00", "arr_time": "2026-09-04T09:30:00"}]}
    return rule, rc.build_solution_dataset(solution, source)


def test_source_fleet_validity_inclusive_endpoints_and_no_input_mutation():
    from copy import deepcopy
    rule, dataset = _fleet_fixture()
    before = deepcopy(dataset)
    specs, checks, findings, remaining = fc.fit_code_checks({"rules": [rule]}, dataset)
    assert not specs and not findings and not remaining["rules"]
    assert checks[0]["status"] == "прогнана" and checks[0]["rows_checked"] == 1
    assert checks[0]["qualification_assessments"][0]["valid_qualification_rows"] == [2]
    assert dataset == before


def test_source_fleet_missing_expired_future_wrong_type_and_gap_are_violations():
    variants = [[], [{"Действует с": "2026-09-01", "Действует до": "2026-09-03"}],
                [{"Действует с": "2026-09-05", "Действует до": "2027-01-01"}],
                [{"Тип ВС": "другой тип"}],
                [{"Действует с": "2026-09-01", "Действует до": "2026-09-03"},
                 {"Действует с": "2026-09-05", "Действует до": "2026-09-07"}]]
    for patches in variants:
        rule, data = _fleet_fixture()
        table = data["tables"]["D1"]["Допуски"]
        row = table["rows"][0]
        table["rows"] = [dict(row, **patch) for patch in patches]
        check, findings = fc._fleet_qualification(rule["id"], rule, data, fc._Counter())
        assert check["status"] == "прогнана", check
        assert len(findings) == 1
        assert findings[0]["rule_refs"] == ["R-S1-абз-11"]
        assert findings[0]["target"]["row"] == 2


def test_source_fleet_unknown_data_fails_closed_instead_of_becoming_open():
    from copy import deepcopy
    rule, original = _fleet_fixture()
    for mutation in ("bad-date", "reversed", "duplicate-flight", "missing-source", "forged-identity"):
        data = deepcopy(original)
        if mutation == "bad-date":
            data["tables"]["D1"]["Допуски"]["rows"][0]["Действует до"] = "not a date"
        elif mutation == "reversed":
            data["tables"]["D1"]["Допуски"]["rows"][0]["Действует с"] = "2026-09-05"
        elif mutation == "duplicate-flight":
            rows = data["tables"]["D1"]["Рейсы"]["rows"]
            rows.append(dict(rows[0]))
        elif mutation == "missing-source":
            del data["tables"]["D1"]
        else:
            data["tables"]["SOL"]["Назначения"]["rows"][0]["Рейс"] = "PL101|2026-09-03T08:00:00"
        check, findings = fc._fleet_qualification(rule["id"], rule, data, fc._Counter())
        assert check["status"] == "не прогнана" and check["rows_checked"] == 0
        assert check["reason"] and not findings
    # Без исходных листов нужной схемы правило не берётся кодом — путь
    # main (лист «Допуски» решения и компилятор), а не пустая «не прогнана».
    data = deepcopy(original)
    del data["tables"]["D1"]
    _specs, checks, _findings, remaining = fc.fit_code_checks({"rules": [rule]}, data)
    assert checks == [] and [r["id"] for r in remaining["rules"]] == [rule["id"]]


def test_source_fleet_open_bounds_source_precedence_and_passenger_scope():
    rule, data = _fleet_fixture()
    row = data["tables"]["D1"]["Допуски"]["rows"][0]
    row.update({"Действует с": "", "Действует до": ""})
    assert not fc._fleet_qualification(rule["id"], rule, data, fc._Counter())[1]
    data["tables"]["SOL"]["Допуски"] = data["tables"]["D1"]["Допуски"]
    data["tables"]["D1"]["Допуски"] = {"columns": ["Табельный номер", "Тип ВС"], "rows": []}
    assert len(fc._fleet_qualification(rule["id"], rule, data, fc._Counter())[1]) == 1
    assignments = data["tables"]["SOL"]["Назначения"]
    assignments["columns"].append("Перегон")
    assignments["rows"][0]["Перегон"] = "да"
    check, findings = fc._fleet_qualification(rule["id"], rule, data, fc._Counter())
    assert check["status"] == "прогнана" and not findings
    assert check["qualification_assessments"][0]["applicable"] is False


def _compiler(prompt):
    PROMPTS.append(prompt)
    return json.dumps({"checks": [], "skipped": []}, ensure_ascii=False)


def test_fitted_rules_never_reach_the_compiler_and_violations_are_found_by_code():
    PROMPTS.clear()
    sol = _solution(bad=True)
    # посадок за смену: у E1/D1 два рабочих назначения при пределе 1
    report = rc.certify(_svod(), sol, _compiler)
    v = report["certificate"]
    for p in PROMPTS:
        for rid in ("R-CREW", "R-DUTY", "R-LAND", "R-DAYS", "R-REST-H",
                    "R-REST-O", "R-SIT"):
            assert rid not in p, "посаженное правило ушло компилятору: " + rid
    assert v["certified"] is False
    assert v["violated_rules"] == ["R-DAYS", "R-LAND", "R-REST-H", "R-REST-O", "R-SIT"]
    by_rule = {}
    for f in report["findings"]:
        by_rule.setdefault(f["rule_refs"][0], []).append(f)
    assert "570" in by_rule["R-REST-H"][0]["text"]
    assert "510" in by_rule["R-REST-O"][0]["text"]
    assert "30" in by_rule["R-SIT"][0]["text"] and len(by_rule["R-SIT"]) == 2
    assert "3 суток" in by_rule["R-DAYS"][0]["text"]
    assert "2 больше предела 1" in by_rule["R-LAND"][0]["text"]
    checks = {c["id"]: c for c in report["checks"]}
    assert all(checks[f"C-{rid}-посадка"]["status"] == "прогнана"
               for rid in ("R-CREW", "R-DUTY", "R-LAND", "R-DAYS", "R-REST-H",
                           "R-REST-O", "R-SIT"))
    assert checks["C-R-REST-H-посадка"]["compiled_by"] == fc.COMPILED_BY
    # спецификации словаря (состав, предел смены) идут через отчёт
    # контракта — их запись несёт единицу семьи
    assert checks["C-R-CREW-посадка"]["unit"] == "рейс"
    assert checks["C-R-DUTY-посадка"]["unit"] == "смена"
    assert gate_certify.check(_svod(), report)[0] == []


def test_clean_solution_is_certified_by_fitted_checks_alone():
    PROMPTS.clear()
    report = rc.certify(_svod(), _solution(bad=False), _compiler)
    v = report["certificate"]
    # посадок: у E1/D1 два назначения при пределе 1 — единственное
    # оставшееся нарушение убираем повышением предела
    assert v["violated_rules"] == ["R-LAND"]
    svod = _svod()
    for r in svod["rules"]:
        if r["id"] == "R-LAND":
            r["template_fit"]["params"]["max_landings_per_duty"] = {"value": 2}
    report = rc.certify(svod, _solution(bad=False), _compiler)
    v = report["certificate"]
    assert v["certified"] is True, v
    assert v["checks_run"] == 7 and v["findings"] == 0
    assert v["window_coverage"]["unknown"] == 0
    assert gate_certify.check(svod, report)[0] == []
    rest = next(c for c in report["checks"] if c["id"] == "C-R-REST-H-посадка")
    first = rest["window_coverage"]["windows"][0]["rest_assessments"][0]
    assert first["previous_source"] == "История" and first["observed_minutes"] == 750
    # Без истории и без решения по ней первый отдых неизвестен — не ноль:
    # сертификат допустимости не выдаётся, нарушений нет.
    no_history = _solution(bad=False)
    no_history.pop("history")
    rep2 = rc.certify(svod, no_history, _compiler)
    assert rep2["certificate"]["certified"] is False
    assert rep2["certificate"]["findings"] == 0
    assert rep2["certificate"]["result"]["feasibility"] == "not_established"
    assert set(rep2["certificate"]["rules_partially_covered"]) == {"R-REST-H", "R-REST-O"}
    # С решением continue_zero (main 1.17.2) первый отдых судится по
    # названному допущению — сертификат выдан, допущение названо.
    rep3 = rc.certify(svod, _zero_policy_solution(bad=False), _compiler)
    assert rep3["certificate"]["certified"] is True, rep3["certificate"]
    rest3 = next(c for c in rep3["checks"] if c["id"] == "C-R-REST-H-посадка")
    assert rest3["windows_assumed_employees"] == 2
    assert "continue_zero" in rest3["windows_assumed_basis"]
    assert rest3["window_coverage"]["windows"][0]["rest_assessments"][0]["assumed"] is True
    assert gate_certify.check(svod, rep3)[0] == []


def test_missing_sheet_is_an_honest_not_run_not_a_pass():
    sol = _solution(bad=False)
    sol.pop("pairings")
    report = rc.certify(_svod(), sol, _compiler)
    days = next(c for c in report["checks"] if c["id"] == "C-R-DAYS-посадка")
    assert days["status"] == "не прогнана" and "Цепочки" in days["reason"]
    assert report["certificate"]["certified"] is False
