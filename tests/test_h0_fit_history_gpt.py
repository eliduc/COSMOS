# -*- coding: utf-8 -*-
"""Независимые ожидаемые исходы для границ истории H0 у посадок
(тесты GPT второго дерева, перенос по смыслу в единую версию 06.09.2026,
строки C-21..C-23): полы отдыха и налёт за месяц."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vmp.run import fit_checks as fc
from vmp.run import logic_checks as lc
from vmp.run import verdicts

EMP = "Табельный номер"
BASE = "База приписки"
RULE = {"id": "R-H", "kind": "HARD", "statement": "Явная норма истории"}
PARTIAL = lc.partial_status()


def _assignment(start="2026-09-04T06:00", end="2026-09-04T08:00", employee="E1", **extra):
    return {EMP: employee, "Время вылета": start, "Время прилёта": end, **extra}


def _duty(start="2026-09-04T06:00", end="2026-09-04T08:00", employee="E1", **extra):
    return {EMP: employee, "Начало": start, "Конец": end, BASE: "PLY",
            "Место отдыха": "PLY", "Идентификатор": f"{employee}/{start}", **extra}


def _history(employee="E1", **extra):
    return {EMP: employee, "Есть история": "да", "Срез": "2026-09-04T00:00",
            "Конец последней смены": "2026-09-03T17:30", "Место последней смены": "PLY",
            "Налёт с начала месяца, мин": 150, **extra}


def _data(assignments=None, duties=None, history=None, employees=None,
          start="2026-09-04", end="2026-10-01"):
    assignments = [_assignment()] if assignments is None else assignments
    duties = [_duty()] if duties is None else duties
    history = [] if history is None else history
    columns = {
        "Назначения": [EMP, "Время вылета", "Время прилёта", "Перегон"],
        "Смены": [EMP, "Начало", "Конец", BASE, "Место отдыха", "Идентификатор"],
        "История": [EMP, "Есть история", "Срез", "Конец последней смены",
                    "Место последней смены", "Налёт с начала месяца, мин"],
    }
    rows = {"Назначения": assignments, "Смены": duties, "История": history}
    return {"tables": {"SOL": {name: {"rows": values, "columns": columns[name]}
                                for name, values in rows.items()}},
            "window_horizon": {"start_inclusive": start, "end_exclusive": end},
            "window_employees": employees or [{"employee": "E1", "base": "PLY"}]}


def _rest(data, kind="home", bound=720):
    return fc._rest_floor("R-H", RULE, data, fc._Counter(), kind, bound)


def _month(data, bound=300):
    return fc._monthly_block("R-H", RULE, data, fc._Counter(), bound)


def _coverage(check, counts):
    cov = check["window_coverage"]
    assert tuple(cov[k] for k in ("expected", "evaluated", "unknown")) == counts
    assert cov["expected"] == cov["evaluated"] + cov["unknown"] == len(cov["windows"])
    assert all(w["reason"] for w in cov["windows"])
    assert check["windows_unjudged"] == cov["unknown"]
    return cov["windows"]


@pytest.mark.parametrize("patch", [
    {"Конец последней смены": None}, {"Конец последней смены": "bad"},
    {"Срез": "2026-09-03T00:00"}, {"Срез": "2026-09-04T00:00:01"},
    {"Место последней смены": None}, {"Есть история": "нет"},
    {"Конец последней смены": "2026-09-04T01:00"},
])
def test_rest_missing_stale_or_unreadable_history_is_addressed_unknown(patch):
    check, findings = _rest(_data(history=[_history(**patch)]))
    assert check["status"] == PARTIAL and not findings
    win = _coverage(check, (1, 0, 1))[0]
    assert win["employee"] == "E1"
    assert win["rest_unknown"] == 1 and win["rest_expected"] == 1
    assert win["rest_assessments"][0]["duty_row"] == 2


def test_rest_absent_history_is_not_a_zero_initial_boundary():
    check, findings = _rest(_data())
    _coverage(check, (1, 0, 1))
    assert not findings


def test_rest_absent_history_with_continue_zero_is_a_named_assumption():
    """Политика main (1.17.2): без записи при continue_zero первый отдых
    судится по названному допущению; строка «Есть история» = нет — то же
    отсутствие записи (C-22, ревью группы C: не проблема сотрудника, а
    предмет политики); дубль строк — неоднозначная история, unknown при
    любом решении."""
    data = _data()
    data["history_policy"] = {"assume_zero_when_missing": True,
                              "user_decision": {"question": "Q-3b", "decision": "continue_zero"}}
    check, findings = _rest(data)
    win = _coverage(check, (1, 1, 0))[0]
    assert check["status"] == "прогнана" and not findings
    assert win["rest_assessments"][0]["assumed"] is True and win["assumed"] is True
    assert check["windows_assumed_employees"] == 1
    data = _data(history=[_history(**{"Есть история": "нет"})])
    data["history_policy"] = {"assume_zero_when_missing": True}
    check, _ = _rest(data)
    win = _coverage(check, (1, 1, 0))[0]
    assert win["assumed"] is True and check["windows_assumed_employees"] == 1
    data = _data(history=[_history(), _history()])
    data["history_policy"] = {"assume_zero_when_missing": True}
    check, _ = _rest(data)
    assert check["window_coverage"]["unknown"] == 1
    assert "несколько строк" in check["window_coverage"]["windows"][0]["reason"]


@pytest.mark.parametrize("run", [_rest, _month])
def test_history_absent_row_is_no_record_judged_by_policy(run):
    """C-22: единственная строка «Есть история» ≠ да — записи нет: без
    решения по истории — unknown с причиной «нет записи сотрудника в
    истории (решения по истории нет)», при continue_zero — названное
    допущение; ни то ни другое не становится нулевым остатком молча."""
    data = _data(history=[_history(**{"Есть история": "нет"})])
    check, findings = run(data)
    win = _coverage(check, (1, 0, 1))[0]
    assert not findings
    assert "нет записи сотрудника в истории (решения по истории нет)" in win["reason"]
    assert "явно не предоставлена" not in win["reason"]
    data = _data(history=[_history(**{"Есть история": "нет"})])
    data["history_policy"] = {"assume_zero_when_missing": True,
                              "user_decision": {"question": "Q-3b", "decision": "continue_zero"}}
    check, findings = run(data)
    win = _coverage(check, (1, 1, 0))[0]
    assert win["assumed"] is True and "continue_zero" in win["assumed_basis"]
    assert check["windows_assumed_employees"] == 1 and not findings
    # при continue_partial — записи нет и допущения нет: unknown с решением в причине
    data = _data(history=[_history(**{"Есть история": "нет"})])
    data["history_policy"] = {"assume_zero_when_missing": False,
                              "user_decision": {"question": "Q-3b", "decision": "continue_partial"}}
    check, _ = run(data)
    win = _coverage(check, (1, 0, 1))[0]
    assert "решение пользователя: continue_partial" in win["reason"]


def test_rest_known_history_recomputes_an_addressed_violation():
    check, findings = _rest(_data(history=[_history(**{"Конец последней смены": "2026-09-03T22:00"})]))
    win = _coverage(check, (1, 1, 0))[0]
    assert win["rest_assessments"][0]["observed_minutes"] == 480
    assert len(findings) == 1 and "история" in findings[0]["text"]
    assert findings[0]["target"]["row"] == 2 and findings[0]["rule_refs"] == ["R-H"]


def test_rest_preserves_seconds_at_the_minimum_boundary():
    data = _data(duties=[_duty("2026-09-04T11:59:00", "2026-09-04T13:00")],
                 history=[_history(**{"Конец последней смены": "2026-09-03T23:59:30"})])
    check, findings = _rest(data)
    win = _coverage(check, (1, 1, 0))[0]
    assert win["rest_assessments"][0]["observed_minutes"] == 719.5
    assert len(findings) == 1


def test_rest_preserves_known_later_violation_with_unknown_initial_history():
    data = _data(duties=[_duty(), _duty("2026-09-04T12:00", "2026-09-04T14:00")])
    check, findings = _rest(data)
    win = _coverage(check, (1, 0, 1))[0]
    assert (win["rest_expected"], win["rest_evaluated"], win["rest_unknown"]) == (2, 1, 1)
    assert win["outcome"] == "violation" and len(findings) == 1


def test_rest_other_kind_is_explicitly_inapplicable_not_missing():
    check, findings = _rest(_data(history=[_history()]), kind="outstation", bound=600)
    win = _coverage(check, (1, 1, 0))[0]
    assert win["rest_assessments"][0]["outcome"] == "not_applicable"
    assert not findings


def test_rest_idle_employee_is_in_coverage_without_invented_history_requirement():
    check, findings = _rest(_data(history=[_history()], employees=[
        {"employee": "E1", "base": "PLY"}, {"employee": "E2", "base": "PLY"}]))
    windows = _coverage(check, (2, 2, 0))
    idle = next(w for w in windows if w["employee"] == "E2")
    assert idle["rest_expected"] == 0 and idle["outcome"] == "pass"
    assert not findings


def test_rest_missing_duties_for_assigned_employee_is_unknown():
    check, findings = _rest(_data(duties=[]))
    win = _coverage(check, (1, 0, 1))[0]
    assert "нет разметки" in win["reason"] and not findings


@pytest.mark.parametrize("patch", [
    {"Налёт с начала месяца, мин": None}, {"Налёт с начала месяца, мин": "bad"},
    {"Налёт с начала месяца, мин": -1}, {"Налёт с начала месяца, мин": float("nan")},
    {"Налёт с начала месяца, мин": float("inf")}, {"Налёт с начала месяца, мин": True},
    {"Налёт с начала месяца, мин": 4321}, {"Срез": "2026-09-03T00:00"},
    {"Срез": "2026-09-04T00:00:01"}, {"Есть история": "нет"},
])
def test_month_missing_or_unanchored_prefix_is_not_zero(patch):
    check, findings = _month(_data(history=[_history(**patch)]))
    win = _coverage(check, (1, 0, 1))[0]
    assert check["status"] == PARTIAL and not findings
    assert win["known_subtotal_minutes"] == 120 and not win["total_complete"]
    assert win["start_inclusive"] == "2026-09-01" and win["end_exclusive"] == "2026-10-01"


def test_month_prefix_exact_zero_is_known_not_missing():
    check, findings = _month(_data(history=[_history(**{"Налёт с начала месяца, мин": 0})]))
    win = _coverage(check, (1, 1, 0))[0]
    assert win["history_proof"]["block_minutes"] == 0
    assert win["known_subtotal_minutes"] == 120 and not findings


def test_month_prefix_without_record_under_continue_zero_is_a_named_assumption():
    data = _data()
    data["history_policy"] = {"assume_zero_when_missing": True,
                              "user_decision": {"question": "Q-3b", "decision": "continue_zero"}}
    check, findings = _month(data)
    win = _coverage(check, (1, 1, 0))[0]
    assert win["history_proof"]["kind"] == "assumed_zero_prefix" and win["assumed"] is True
    assert win["known_subtotal_minutes"] == 120 and not findings
    assert check["windows_assumed_employees"] == 1


def test_month_exact_prefix_can_prove_violation():
    check, findings = _month(_data(history=[_history(**{"Налёт с начала месяца, мин": 200})]))
    win = _coverage(check, (1, 1, 0))[0]
    assert win["known_subtotal_minutes"] == 320 and len(findings) == 1


def test_month_known_subtotal_violation_survives_unknown_history():
    data = _data(assignments=[_assignment(end="2026-09-04T12:00")])
    check, findings = _month(data)
    win = _coverage(check, (1, 0, 1))[0]
    assert win["lower_bound_violation"] and win["outcome"] == "violation"
    assert len(findings) == 1 and "полный итог неизвестен" in findings[0]["text"]
    cert = {"checks_run": 0, "findings": len(findings), "window_coverage": check["window_coverage"]}
    assert verdicts.feasibility_of(cert) == "violated"


def test_month_idle_employee_prefix_is_checked_and_can_violate():
    check, findings = _month(_data(assignments=[], duties=[], history=[
        _history(**{"Налёт с начала месяца, мин": 400})]))
    win = _coverage(check, (1, 1, 0))[0]
    assert win["known_subtotal_minutes"] == 400 and len(findings) == 1
    assert findings[0]["target"]["sheet"] == "История"


def test_month_idle_employee_without_prefix_is_unknown():
    check, findings = _month(_data(assignments=[], duties=[]))
    _coverage(check, (1, 0, 1))
    assert not findings


def test_month_start_first_day_has_proven_empty_prefix_without_history():
    check, findings = _month(_data(start="2026-09-01"))
    win = _coverage(check, (1, 1, 0))[0]
    assert win["total_complete"] and "history_proof" not in win and not findings


def test_month_partial_final_month_has_unknown_suffix_not_implicit_zero():
    check, findings = _month(_data(end="2026-10-10", history=[_history()]))
    windows = _coverage(check, (2, 1, 1))
    assert windows[1]["start_inclusive"] == "2026-10-01"
    assert "остаток календарного месяца" in windows[1]["reason"]
    assert not findings


def test_month_legal_arrival_beyond_horizon_retains_full_flight_without_new_month():
    data = _data(assignments=[_assignment("2026-09-30T23:30", "2026-10-01T01:10")],
                 history=[_history(**{"Налёт с начала месяца, мин": 0})])
    check, findings = _month(data)
    win = _coverage(check, (1, 1, 0))[0]
    assert win["known_subtotal_minutes"] == 100 and not findings


def test_month_deadhead_does_not_contribute_block_time():
    data = _data(assignments=[_assignment(end="2026-09-04T20:00", **{"Перегон": "да"})],
                 history=[_history(**{"Налёт с начала месяца, мин": 0})])
    check, findings = _month(data)
    assert _coverage(check, (1, 1, 0))[0]["known_subtotal_minutes"] == 0 and not findings


@pytest.mark.parametrize("run", [_rest, _month])
def test_history_duplicates_are_unknown_not_last_row_wins(run):
    data = _data(history=[_history(), _history()])
    check, findings = run(data)
    win = _coverage(check, (1, 0, 1))[0]
    assert "несколько строк" in win["reason"] and not findings


@pytest.mark.parametrize("run", [_rest, _month])
def test_missing_declared_horizon_is_inferred_from_the_solution_and_named(run):
    """C-27b, вариант (б): без объявленного горизонта он выводится по
    назначениям и назван inferred; сам горизонт назначением не выдаётся
    за объявленный — это частичная диагностика, не pass: причина
    названа в проверке (сертификат относит правило к покрытым частично)."""
    data = _data(history=[_history()])
    del data["window_horizon"]
    check, _findings = run(data)
    assert check["status"] != "не прогнана"
    assert check["window_coverage"]["horizon"]["source"] == lc.HORIZON_INFERRED
    assert check["horizon_inferred"] is True
    assert check["window_coverage"]["horizon"]["start_inclusive"] == "2026-09-04"
    assert "частичная диагностика" in check["reason"]
    assert lc.HORIZON_INFERRED_REASON in check["reason"]


def test_independent_families_do_not_mutate_source_dataset():
    data = _data(history=[_history()])
    before = copy.deepcopy(data)
    _rest(data)
    _month(data)
    assert data == before
