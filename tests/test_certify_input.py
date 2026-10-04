# -*- coding: utf-8 -*-
"""Сборка входа сертификатора модулем (этап Г, Г-S2, шов Ш-3).

Проверяется контракт модуля, который зовёт оркестратор кнопки: закрытый
перечень полей входа, lastRunLogic внутри, гейт показаний ДО сборки
(вход с неподтверждёнными корзинами не строится), и идентичность
вывода прежнему встроенному коду инструмента (Г-5).
"""

import pytest

from webapp import certify_input as ci
from webapp import solution_export as se


def _flight(leg, dep, arr):
    return {"flightLegId": leg, "flightNumber": leg,
            "departureUTCDateTime": dep, "arrivalUTCDateTime": arr}


def _fixtures():
    flights = [_flight("F1", "2026-03-02T10:00:00", "2026-03-02T12:00:00"),
               _flight("F2", "2026-03-03T10:00:00", "2026-03-03T12:00:00")]
    payload = {"flights": flights,
               "employees": [{"id": "E1", "homeAirport": {"code": "PLY"}}],
               "flightAssignments": [
                   {"id": "s%d" % i, "requiredSkill": "Pilot",
                    "flight": {"flightNumber": f["flightNumber"]},
                    "employee": {"id": "E1"}} for i, f in enumerate(flights)]}
    solved = {"score": "0hard/-5soft",
              "flightAssignments": payload["flightAssignments"]}
    pkg = {"manifest": {"source_run": "run-T", "spec_version": "1.6"},
           "model": {"rules": [
               {"id": "R-7", "kind": "HARD",
                "logic": {"subject": "duty",
                          "demand": {"kind": "duration_within", "relation": "max",
                                     "param": "max_duty_duration", "as_written": "12:00"}}}]}}
    logic = {"blocksSeen": 1, "plans": [],
             "generated": [], "refused": [], "notHard": [],
             "executedElsewhere": [
                 {"rule": "R-7", "state": "ИСПОЛНЯЕТСЯ СУЩЕСТВУЮЩИМ", "enforce": "required",
                  "constraints": ["Max duty period (rest-segmented)"],
                  "considered": 2, "consideredMeasure": "назначений набора",
                  "reason": None, "declarations": []}],
             "ignored": [],
             "registered": ["Max duty period (rest-segmented)"]}
    report = {"lastRun": {
        "rulesProfile": "DEFAULT",
        "mode": "feasibility-soft",
        "profileReadings": {"min_rest_home_min": 480, "min_rest_outstation_min": 480},
        "logic": logic,
        "thresholds": {"applied": []},
    }, "taskPackage": {"fingerprint": "abc123"}}
    return pkg, payload, solved, report, logic


def test_вход_собирается_с_показаниями_внутри_и_закрытым_перечнем_полей():
    pkg, payload, solved, report, logic = _fixtures()
    inp, coverage = ci.build(pkg, payload, solved, report, name="проба")
    # Закрытый перечень полей (единая версия 06.09.2026, W-9/W-10/W-21):
    # штатные плюс горизонт, перечень сотрудников, контекст цены,
    # интервалы истории.
    assert set(inp) == {"assignments", "duties", "lastRunLogic", "solution",
                        "window_horizon", "window_employees", "cost_context",
                        "cost_history_intervals"}
    assert inp["lastRunLogic"] == logic
    assert len(inp["assignments"]) == 2 and len(inp["duties"]) >= 1
    assert isinstance(coverage, dict) and "home" in coverage
    # Идентичность прежнему встроенному коду инструмента (Г-5).
    rest = se.min_rest_from_applied(report)
    cov2: dict = {}
    expected = se.certifier_input(payload, solved, rest, name="проба", coverage=cov2)
    expected["lastRunLogic"] = logic
    assert inp == expected
    assert coverage == cov2


def test_неподтверждённые_показания_не_собираются():
    pkg, payload, solved, report, logic = _fixtures()
    # Исполнитель не зарегистрирован фабрикой — гейт красный.
    report["lastRun"]["logic"]["registered"] = ["Чужое имя"]
    with pytest.raises(ci.CertifyInputError) as e:
        ci.build(pkg, payload, solved, report, name="проба")
    assert "не подтверждены" in str(e.value)
    assert "не зарегистрирован" in str(e.value)


def test_шапка_несёт_трассировку_и_корзины():
    pkg, payload, solved, report, logic = _fixtures()
    inp, coverage = ci.build(pkg, payload, solved, report, name="проба")
    head = ci.head(pkg, payload, solved, report, inp, coverage)
    assert head["package_fingerprint"] == "abc123"
    assert head["source_run"] == "run-T"
    assert head["score"] == "0hard/-5soft"
    assert head["logic_baskets"]["executedElsewhere"] == 1
    assert head["assignments"] == 2
    assert head["rest_places"] == coverage
    # Листы рядом со входом.
    assert ci.quals_sheet(payload)["rows"] == []
    assert ci.reserve_sheet(payload, solved, report)["rows"] == []


def test_отчёт_ночёвок_из_решения_иначе_из_показаний_того_же_прогона():
    """H11-WIRE-1 (рецензия GPT-024, 06.09.2026): решатель хранит отчёт
    RosterLayoverCost в записи прогона и отдаёт его и в решении
    (`run/{id}/result`), и в показаниях (`run/{id}/applied-rules`).
    Вход сертификатора несёт его из решения; решателю прежней сборки без
    поля в решении — из показаний ТОГО ЖЕ runId; нет нигде — поля нет
    (сверка цены честно «несравнимо», H11-R2 не ослабляется)."""
    pkg, payload, solved, report, _ = _fixtures()
    layover = {"version": 2, "contract": "B", "known_events": [], "known_nights": 0}
    # Нет ни в решении, ни в показаниях — поля нет.
    inp, _ = ci.build(pkg, payload, solved, report, name="проба")
    assert "solver_layover_cost" not in inp
    assert ci.solver_layover_report(solved, report) is None
    # Только в показаниях (решатель прежней сборки) — берётся оттуда.
    report_with = dict(report, runId="r-1", layoverCost=layover)
    inp, _ = ci.build(pkg, payload, solved, report_with, name="проба")
    assert inp["solver_layover_cost"] == layover
    # В решении — из решения; одинаковый объект в показаниях не мешает.
    solved_with = dict(solved, layoverCost=layover)
    inp, _ = ci.build(pkg, payload, solved_with, report_with, name="проба")
    assert inp["solver_layover_cost"] == layover
    inp, _ = ci.build(pkg, payload, solved_with, report, name="проба")
    assert inp["solver_layover_cost"] == layover
    # Не словарь — не свидетельство.
    assert ci.solver_layover_report(dict(solved, layoverCost="нет"), report) is None


def test_расхождение_отчёта_ночёвок_решения_и_показаний_отказ():
    """Оба источника есть и расходятся — чужое свидетельство сертификатор
    не получает: вход не строится, причина словами с runId."""
    pkg, payload, solved, report, _ = _fixtures()
    solved_with = dict(solved, layoverCost={"version": 2, "known_nights": 1})
    report_with = dict(report, runId="r-2", layoverCost={"version": 2, "known_nights": 0})
    with pytest.raises(ci.CertifyInputError) as e:
        ci.build(pkg, payload, solved_with, report_with, name="проба")
    assert "расходится" in str(e.value) and "r-2" in str(e.value)
