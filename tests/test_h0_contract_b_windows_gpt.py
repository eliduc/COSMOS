# -*- coding: utf-8 -*-
"""Контракт Б: обязательные окна не сужаются отсутствием истории или
назначений (тесты GPT второго дерева, перенос по смыслу в единую версию
06.09.2026, строки C-25..C-34).

Стенды сами называют ожидаемый исход; счёт решателя оракулом не служит.
Случаи, судящие контракт отчёта данных (validate_report / гейт
сертификата с третьим статусом и подделкой покрытия) — девять тестов
GPT (перенесены как есть при интеграции C+P 06.09.2026) против
контракта CDPM в main (cdpm/data_report: CHECK_STATUSES с «частично
прогнана», COVERAGE_GRANULARITIES, _window_coverage_problems,
_continuous_coverage_problems; cdpm/gates/gate_certify).
"""
from __future__ import annotations

import copy
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cdpm import data_report as dr
from cdpm.gates import gate_certify
from vmp.run import logic_checks as lc

RULE = {"id": "R-W", "kind": "HARD", "statement": "Контроль окон"}
PARTIAL = "частично прогнана"  # литерал контракта CHECK_STATUSES cdpm main (GPT-026: оракул не следует за кодом)


def _assignment(day, employee="E1", start="06:00", end="20:00"):
    return {lc.EMP: employee, lc.BASE: "PLY", "Время вылета": f"{day}T{start}",
            "Время прилёта": f"{day}T{end}", "Смена": f"{employee}/{day}"}


def _duty(day, employee="E1", start="05:00", end="20:00"):
    return {lc.EMP: employee, lc.BASE: "PLY", "Начало": f"{day}T{start}",
            "Конец": f"{day}T{end}", "Идентификатор": f"{employee}/{day}"}


def _calendar(employee="E1", kind="work", night=False):
    return [{lc.EMP: employee, "Дата": (date(2026, 8, 29) + timedelta(days=k)).isoformat(),
             "Вид": kind, "Ночная": night} for k in range(6)]


def _dataset(assignments=None, duties=None, history=None, calendar=None,
             start="2026-09-04", end="2026-09-12", employees=None):
    if assignments is None:
        assignments = [_assignment(f"2026-09-{k:02d}") for k in range(4, 12)]
    if duties is None:
        duties = [_duty(f"2026-09-{k:02d}") for k in range(4, 12)]
    columns = {
        "Назначения": [lc.EMP, lc.BASE, "Время вылета", "Время прилёта", "Смена"],
        "Смены": [lc.EMP, lc.BASE, "Начало", "Конец", "Идентификатор"],
        "История": [lc.EMP, "Есть история", "Срез", "Конец последней смены",
                    "Ночных смен подряд", "Рабочих дней подряд", "Рабочих дней с начала месяца"],
        "Календарь истории": [lc.EMP, "Дата", "Вид", "Ночная"],
    }
    rows = {"Назначения": assignments, "Смены": duties,
            "История": history or [], "Календарь истории": calendar or []}
    return {
        "tables": {"SOL": {name: {"columns": columns[name], "rows": values}
                           for name, values in rows.items()}},
        "window_horizon": {"start_inclusive": start, "end_exclusive": end,
                           "source": "explicit fixture run horizon"},
        "window_employees": employees or [{"employee": "E1", "base": "PLY"}],
    }


def _history(**extra):
    return [{lc.EMP: "E1", "Есть история": "да", "Срез": "2026-09-04T00:00", **extra}]


def _run(dataset, form=lc.FORM_ROLLING, bound=2160, days=7, **extra):
    plan = {"form": form, "bound": bound, "condBound": days,
            "baseScope": "PLY", "nightFrom": 1320, "nightTo": 360, **extra}
    return lc.run_plan("R-W", RULE, plan, dataset, lc._Counter())


def _coverage(check, expected, evaluated, unknown):
    cov = check["window_coverage"]
    assert (cov["expected"], cov["evaluated"], cov["unknown"]) == (expected, evaluated, unknown)
    assert expected == evaluated + unknown == len(cov["windows"])
    assert len({(w["employee"], w["start_inclusive"], w["end_exclusive"])
                for w in cov["windows"]}) == expected
    assert all(w["reason"] for w in cov["windows"])
    assert sum(w["status"] == "unknown" for w in cov["windows"]) == unknown
    # производные поля main — от того же покрытия
    assert check["windows_judged"] == evaluated and check["windows_unjudged"] == unknown
    return cov


def test_continuous_rest_keeps_historical_domain_and_known_violations():
    check, findings = _run(_dataset())
    cov = _coverage(check, 5, 3, 2)
    assert check["status"] == PARTIAL
    assert cov["granularity"] == "continuous_critical_cells"
    assert cov["expected_unit"] == "critical_cells"
    assert cov["windows"][0]["start_inclusive"] == "2026-08-28T00:00:00"
    assert cov["windows"][0]["end_exclusive"] == "2026-09-04T00:00:00"
    assert cov["windows"][-1]["end_exclusive"] == "2026-09-12T00:00:00"
    assert len(findings) == 3
    assert all(w["occupancy_complete"] for w in cov["windows"] if w.get("outcome") == "violation")


def test_last_duty_end_does_not_fill_the_prior_week():
    data = _dataset(history=_history(**{"Конец последней смены": "2026-09-03T20:00"}))
    check, findings = _run(data)
    cov = _coverage(check, 5, 3, 2)
    assert len(findings) == 3
    assert cov["employee_proofs"]["E1"]["fully_known_window_end_spans"][0]["start_inclusive"] == "2026-09-10T20:00:00"
    assert "только после него" in check["window_coverage"]["windows"][0]["reason"]


def test_exact_midnight_last_end_moves_the_known_domain_not_the_whole_history():
    data = _dataset(history=_history(**{"Конец последней смены": "2026-09-03T00:00"}))
    check, findings = _run(data)
    cov = _coverage(check, 5, 3, 2)
    assert len(findings) == 3
    assert cov["employee_proofs"]["E1"]["fully_known_window_end_spans"][0]["start_inclusive"] == "2026-09-10T00:00:00"


def test_sufficient_known_rest_proves_pass_without_claiming_full_history():
    data = _dataset(assignments=[], duties=[], history=_history(**{
        "Конец последней смены": "2026-09-02T00:00"}))
    check, findings = _run(data)
    cov = _coverage(check, 5, 5, 0)
    assert check["status"] == "прогнана" and findings == []
    assert cov["windows"][0]["occupancy_complete"] is False
    assert "неизвестная часть окна не нужна" in cov["windows"][0]["reason"]


def test_unaligned_history_cut_does_not_extend_known_rest():
    history = _history(**{"Конец последней смены": "2026-09-02T00:00", "Срез": "2026-09-03T00:00"})
    check, _ = _run(_dataset(assignments=[], duties=[], history=history))
    cov = _coverage(check, 7, 5, 2)
    assert cov["employee_proofs"]["E1"]["passing_window_end_spans"][0]["start_inclusive"] == "2026-09-05T12:00:00"


@pytest.mark.parametrize("kind", ["leave", "sick", "work", "reserve", "unknown"])
def test_calendar_label_is_not_automatically_continuous_rest(kind):
    check, findings = _run(_dataset(calendar=_calendar(kind=kind)))
    _coverage(check, 5, 3, 2)
    assert len(findings) == 3


def test_explicit_day_off_calendar_can_prove_continuous_rest():
    check, findings = _run(_dataset(calendar=_calendar(kind="day_off")))
    cov = _coverage(check, 7, 7, 0)
    assert cov["windows"][0]["outcome"] == "pass"
    assert len(findings) == 2  # нарушенный открытый интервал плюс его конечная точка


def test_employee_with_late_or_no_assignment_still_gets_every_expected_window():
    data = _dataset(assignments=[_assignment("2026-09-10")], duties=[_duty("2026-09-10")],
                    employees=[{"employee": "E1", "base": "PLY"},
                               {"employee": "E2", "base": "PLY"}])
    check, _ = _run(data)
    cov = _coverage(check, 14, 10, 4)
    assert {w["employee"] for w in cov["windows"]} == {"E1", "E2"}
    assert all(sum(w["employee"] == emp for w in cov["windows"]) == 7 for emp in ("E1", "E2"))


def test_continue_zero_policy_closes_missing_records_by_named_assumption():
    """Политика main (1.17.2): сотрудник без записи при continue_zero
    свободен до горизонта по допущению — ячейки через границу судятся и
    так и называются; запись с недостаточной историей остаётся unknown."""
    data = _dataset(assignments=[], duties=[])
    data["history_policy"] = {"assume_zero_when_missing": True,
                              "user_decision": {"question": "Q-3b", "decision": "continue_zero"}}
    check, findings = _run(data)
    cov = _coverage(check, 3, 3, 0)
    assert check["status"] == "прогнана" and findings == []
    assert check["windows_assumed_employees"] == 1 and "continue_zero" in check["windows_assumed_basis"]
    assert all(w.get("assumed") for w in cov["windows"])
    with_record = _dataset(assignments=[], duties=[], history=_history())
    with_record["history_policy"] = data["history_policy"]
    check2, _ = _run(with_record)
    assert check2["window_coverage"]["unknown"] > 0 and "windows_assumed_employees" not in check2


@pytest.mark.parametrize("form,bound", [(lc.FORM_DAYS_OFF_ROLLING, 2),
                                      (lc.FORM_NIGHT_ROLLING, 2)])
def test_counter_windows_require_daily_history_even_when_employee_history_exists(form, bound):
    check, findings = _run(_dataset(history=_history()), form, bound)
    _coverage(check, 8, 2, 6)
    assert check["status"] == PARTIAL
    assert len(findings) == 2


def test_exact_missing_calendar_date_is_preserved_for_every_affected_window():
    cal = [r for r in _calendar() if r["Дата"] != "2026-08-31"]
    check, findings = _run(_dataset(calendar=cal), lc.FORM_DAYS_OFF_ROLLING, 2)
    cov = _coverage(check, 8, 5, 3)
    assert len(findings) == 5
    assert all("2026-08-31" in w["reason"] for w in cov["windows"] if w["status"] == "unknown")


@pytest.mark.parametrize("kind,night", [("reserve", None), ("work", None),
                                      ("not_a_kind", False), ("day_off", True)])
def test_missing_or_conflicting_night_calendar_field_is_not_zero(kind, night):
    check, _ = _run(_dataset(calendar=_calendar(kind=kind, night=night)), lc.FORM_NIGHT_ROLLING, 10)
    _coverage(check, 8, 2, 6)


def test_night_windows_are_not_skipped_for_employee_without_night_duties():
    check, findings = _run(_dataset(assignments=[], duties=[]), lc.FORM_NIGHT_ROLLING, 2)
    _coverage(check, 8, 2, 6)
    assert findings == []


def test_conflicting_duplicate_calendar_day_stays_unknown():
    cal = _calendar()
    cal.extend([dict(cal[0], **{"Вид": "day_off"}), dict(cal[0])])
    check, _ = _run(_dataset(calendar=cal), lc.FORM_DAYS_OFF_ROLLING, 2)
    _coverage(check, 8, 7, 1)


def test_month_window_missing_prefix_is_not_an_implicit_zero():
    data = _dataset(assignments=[], duties=[], end="2026-10-01",
                    history=_history())
    check, findings = _run(data, lc.FORM_DAYS_OFF, 8)
    cov = _coverage(check, 1, 0, 1)
    assert findings == [] and cov["granularity"] == "windows"
    assert cov["expected_unit"] == "windows" and cov["window_kind"] == "calendar_month"
    assert cov["windows"][0]["start_inclusive"] == "2026-09-01"
    assert cov["windows"][0]["end_exclusive"] == "2026-10-01"


@pytest.mark.parametrize("worked,off,violations", [(0, 3, 0), (2, 1, 0), (3, 0, 1)])
def test_exact_full_month_prefix_aggregate_is_sufficient(worked, off, violations):
    assignments = [_assignment(f"2026-09-{day:02d}") for day in range(4, 31)]
    data = _dataset(assignments=assignments, duties=[], end="2026-10-01",
                    history=_history(**{"Рабочих дней с начала месяца": worked}))
    check, findings = _run(data, lc.FORM_DAYS_OFF, 1)
    cov = _coverage(check, 1, 1, 0)
    proof = cov["windows"][0]["history_proof"]
    assert proof == {"kind": "exact_month_prefix_aggregate", "start_inclusive": "2026-09-01",
                     "end_exclusive": "2026-09-04", "days_worked": worked, "days_off": off}
    assert cov["windows"][0]["observed_count"] == off
    assert len(findings) == violations


@pytest.mark.parametrize("worked,cut", [(4, "2026-09-04T00:00"),
                                      (None, "2026-09-04T00:00"),
                                      (0, "2026-09-03T00:00"),
                                      (0, "2026-09-04T00:00:01")])
def test_month_prefix_aggregate_must_be_valid_and_exactly_aligned(worked, cut):
    data = _dataset(assignments=[], duties=[], end="2026-10-01",
                    history=_history(**{"Рабочих дней с начала месяца": worked, "Срез": cut}))
    check, findings = _run(data, lc.FORM_DAYS_OFF, 1)
    _coverage(check, 1, 0, 1)
    assert findings == []


def test_month_aggregate_conflicting_with_known_calendar_is_unknown():
    data = _dataset(assignments=[], duties=[], end="2026-10-01",
                    history=_history(**{"Рабочих дней с начала месяца": 3}),
                    calendar=[{lc.EMP: "E1", "Дата": "2026-09-03", "Вид": "day_off", "Ночная": False}])
    check, _ = _run(data, lc.FORM_DAYS_OFF, 1)
    cov = _coverage(check, 1, 0, 1)
    assert "противоречит" in cov["windows"][0]["reason"]


def test_month_calendar_is_counted_exactly_and_incomplete_trailing_month_is_not_added():
    cal = [{lc.EMP: "E1", "Дата": f"2026-09-0{day}", "Вид": kind, "Ночная": False}
           for day, kind in [(1, "work"), (2, "day_off"), (3, "work")]]
    data = _dataset(assignments=[], duties=[], end="2026-10-03", calendar=cal)
    check, findings = _run(data, lc.FORM_DAYS_OFF, 29)
    cov = _coverage(check, 1, 1, 0)
    assert cov["windows"][0]["observed_count"] == 28
    assert len(findings) == 1


@pytest.mark.parametrize("form", lc.FORMS)
def test_no_declared_horizon_is_inferred_from_assignments_and_named(form):
    """C-27b, вариант (б) плана: без объявленного window_horizon горизонт
    выводится по назначениям решения и назван source = inferred —
    частичная диагностика, а не «не прогнана» и не молчание."""
    data = _dataset()
    del data["window_horizon"]
    check, _findings = _run(data, form, 2)
    assert check["status"] != "не прогнана"
    cov = check["window_coverage"]
    assert cov["horizon"]["source"] == lc.HORIZON_INFERRED and check["horizon_inferred"] is True
    assert cov["horizon"]["start_inclusive"] == "2026-09-04"
    assert cov["horizon"]["end_exclusive"] == "2026-09-12"
    # без назначений с читаемыми временами и без горизонта — не прогнана
    empty = _dataset(assignments=[], duties=[])
    del empty["window_horizon"]
    check2, findings2 = _run(empty, form, 2)
    assert check2["status"] == "не прогнана" and findings2 == []
    assert "горизонт" in check2["reason"]


@pytest.mark.parametrize("start,end", [("bad", "2026-09-12"),
                                      ("2026-09-04T12:00", "2026-09-12"),
                                      ("2026-09-12", "2026-09-04")])
def test_invalid_horizon_is_not_run(start, end):
    check, findings = _run(_dataset(start=start, end=end))
    assert check["status"] == "не прогнана" and findings == []


@pytest.mark.parametrize("form", lc.FORMS)
def test_missing_threshold_is_not_zero(form):
    check, findings = _run(_dataset(), form, None)
    assert check["status"] == "не прогнана" and findings == []


@pytest.mark.parametrize("form", [lc.FORM_NIGHT, lc.FORM_CONSECUTIVE])
def test_missing_series_carry_is_unknown_but_known_lower_bound_violation_survives(form):
    check, findings = _run(_dataset(history=_history()), form, 2)
    _coverage(check, 1, 0, 1)
    assert check["status"] == PARTIAL and len(findings) == 1


@pytest.mark.parametrize("form,field", [(lc.FORM_NIGHT, "Ночных смен подряд"),
                                      (lc.FORM_CONSECUTIVE, "Рабочих дней подряд")])
def test_explicit_zero_series_carry_is_known(form, field):
    check, findings = _run(_dataset(history=_history(**{field: 0})), form, 9)
    _coverage(check, 1, 1, 0)
    assert check["status"] == "прогнана" and findings == []


def test_bad_employee_intervals_do_not_remove_other_employees_proven_violations():
    data = _dataset()
    data["tables"]["SOL"]["Назначения"]["rows"].append(_assignment("bad-date", employee="E2"))
    check, findings = _run(data)
    cov = _coverage(check, 8, 3, 5)
    assert len(findings) == 3 and all("«E1»" in f["evidence"] for f in findings)
    assert all(w["status"] == "unknown" for w in cov["windows"] if w["employee"] == "E2")


def test_continuous_sweep_finds_intraday_violation_that_daily_endpoints_miss():
    first = dict(_assignment("2026-09-04", start="00:00", end="01:00"), **{"Смена": "E1/A"})
    second = dict(_assignment("2026-09-04", start="18:00", end="19:00"), **{"Смена": "E1/B"})
    duties = [{lc.EMP: "E1", lc.BASE: "PLY", "Идентификатор": "E1/A",
               "Начало": "2026-09-03T18:00", "Конец": "2026-09-04T06:00"},
              {lc.EMP: "E1", lc.BASE: "PLY", "Идентификатор": "E1/B",
               "Начало": "2026-09-04T18:00", "Конец": "2026-09-05T00:00"}]
    data = _dataset(assignments=[first, second], duties=duties, end="2026-09-05",
                    history=_history(**{"Конец последней смены": "2026-09-03T06:00"}))
    check, findings = _run(data, bound=720, days=1)
    cov = _coverage(check, 7, 7, 0)
    assert cov["windows"][0]["outcome"] == cov["windows"][-1]["outcome"] == "pass"
    violations = [w for w in cov["windows"] if w["outcome"] == "violation"]
    assert len(violations) == len(findings) == 1
    assert violations[0]["ending_domain"] == {
        "start": "2026-09-04T06:00:00", "end": "2026-09-04T18:00:00",
        "start_closed": False, "end_closed": False, "kind": "open_interval"}
    assert violations[0]["occupancy_basis"] == "normative_duties"
    # По одним рейсам промежуток 17 часов — норма ложно выдержана.
    assert findings[0]["target"]["sheet"] == "Смены"


def test_continuous_sweep_retains_singleton_equality_when_required_equals_window():
    lo, hi = datetime(2026, 9, 4), datetime(2026, 9, 5)
    width = required = timedelta(hours=12)
    cells, proof = lc._continuous_rest_cells(lo, hi, width, required,
        [(lo - timedelta(days=1), hi)], [(lo, lo + timedelta(hours=12))])
    assert len(cells) == 5
    passes = [cell for cell in cells if cell[3]]
    assert passes == [(lo + required, lo + required, True, True, True)]
    assert proof["passing_window_end_spans"] == [{
        "start_inclusive": "2026-09-04T12:00:00", "end_inclusive": "2026-09-04T12:00:00"}]


def test_continuous_union_preserves_touching_boundaries_and_one_second_gaps():
    lo, hi = datetime(2026, 9, 4), datetime(2026, 9, 5)
    known = [(lo - timedelta(days=1), hi)]
    rest_a = (lo - timedelta(hours=12), lo)
    rest_b = (lo, lo + timedelta(hours=12))
    cells, _ = lc._continuous_rest_cells(lo, hi, timedelta(days=1), timedelta(hours=12), known, [rest_a, rest_b])
    assert len(cells) == 3 and all(cell[3] for cell in cells)
    shifted = tuple(t + timedelta(seconds=1) for t in rest_b)
    cells, _ = lc._continuous_rest_cells(lo, hi, timedelta(days=1), timedelta(hours=12), known, [rest_a, shifted])
    failed = [c for c in cells if not c[3] and c[4]]
    assert len(failed) == 1
    assert failed[0][0] == lo + timedelta(hours=12)
    assert failed[0][1] == lo + timedelta(hours=12, seconds=1)
    assert failed[0][2] is False


def test_window_moments_do_not_silently_truncate_seconds_or_history_cuts():
    assert lc._moment("2026-09-04T00:00:01").second == 1
    assert lc._moment("2026-09-04T02:00:01+02:00") == datetime(2026, 9, 4, 0, 0, 1)
    assert lc._moment("2026-09-04T00:00junk") is None
    # формы книг — те же, что у cdpm.data_checks.parse_moment
    assert lc._moment("04.09.2026 00:00") == datetime(2026, 9, 4)
    assert lc._moment("04.09.2026") == datetime(2026, 9, 4)
    assert lc._moment("2026-09-04 00:00") == datetime(2026, 9, 4)
    assert lc._num(True) is None and lc._num(float("nan")) is None and lc._num(float("inf")) is None
    assert lc._whole(2.5) is None and lc._whole(-1) is None and lc._whole("3") == 3
    data = _dataset(assignments=[], duties=[], history=_history(**{
        "Конец последней смены": "2026-09-02T00:00", "Срез": "2026-09-04T00:00:01"}))
    check, _ = _run(data)
    assert check["window_coverage"]["unknown"] > 0


def test_missing_normative_duties_cannot_certify_but_proven_flight_lower_bound_survives():
    check, findings = _run(_dataset(duties=[]))
    cov = _coverage(check, 5, 0, 5)
    assert check["status"] == PARTIAL
    assert len(findings) == 3
    assert all(w.get("lower_bound_violation") is True for w in cov["windows"] if w.get("outcome") == "violation")


def test_missing_duty_mapping_does_not_turn_night_counts_into_zero():
    check, findings = _run(_dataset(duties=[]), lc.FORM_NIGHT_ROLLING, 10)
    _coverage(check, 8, 0, 8)
    assert findings == []


def test_arrival_after_horizon_keeps_legal_departure_and_full_duty_interval():
    import copy
    assignment = dict(_assignment("2026-09-04", start="23:30"), **{"Время прилёта": "2026-09-05T01:10"})
    duty = dict(_duty("2026-09-04", start="23:00"), **{"Конец": "2026-09-05T01:40"})
    data = _dataset(assignments=[assignment], duties=[duty], end="2026-09-05", history=_history(**{
        "Конец последней смены": "2026-09-02T00:00", "Рабочих дней подряд": 0}))
    original = copy.deepcopy(data)
    check, findings = _run(data)
    _coverage(check, 3, 3, 0)
    assert findings == [] and check["status"] == "прогнана"
    for form, bound in [(lc.FORM_DAYS_OFF_ROLLING, 1), (lc.FORM_NIGHT_ROLLING, 0)]:
        check, findings = _run(data, form, bound, days=1)
        _coverage(check, 1, 1, 0)
        assert len(findings) == 1
    assert data == original


@pytest.mark.parametrize("remove", ["Идентификатор", "Смена"])
def test_missing_duty_identity_is_incomplete_not_a_flight_gap_pass(remove):
    data = _dataset()
    table = "Смены" if remove == "Идентификатор" else "Назначения"
    data["tables"]["SOL"][table]["rows"][0].pop(remove)
    check, _ = _run(data)
    assert check["status"] == PARTIAL
    assert check["window_coverage"]["evaluated"] == 0
    assert check["window_coverage"]["employee_proofs"]["E1"]["occupancy_basis"] == "assignment_projection_incomplete"


# ---------- контракт отчёта данных и гейт сертификата (тесты GPT, как есть) --

def _report(check, findings, certified=False):
    report = {"customer": "contract-B-fixture",
              "sources": [{"sid": "SOL", "file": "solution.json", "sha256": "0" * 64, "rows_seen": 8}],
              "checks": [check], "findings": findings}
    report["counters"] = dr.metrics_of(report)
    n_run = int(check["status"] == "прогнана")
    report["certificate"] = {"certified": certified, "findings": len(findings),
        "violated_rules": ["R-W"] if findings else [], "checks_run": n_run,
        "checks_not_run": 1 - n_run, "rules_considered": 1}
    return report


def test_partial_check_with_known_findings_remains_valid_report_and_honest_certificate():
    check, findings = _run(_dataset())
    report = _report(check, findings)
    assert dr.validate_report(report, {"rules": [RULE]}) == []
    assert gate_certify.check({"rules": [RULE]}, report)[0] == []
    assert report["counters"]["checks_run"] == 0


def test_partial_check_without_findings_cannot_be_certified_even_in_nonstrict_mode():
    check, findings = _run(_dataset(assignments=[], duties=[]), lc.FORM_NIGHT_ROLLING, 10)
    report = _report(check, findings, certified=True)
    assert any("certified" in p for p in gate_certify.check({"rules": [RULE]}, report)[0])


def test_forged_fully_run_status_with_unknown_windows_is_rejected():
    check, findings = _run(_dataset(assignments=[], duties=[]), lc.FORM_NIGHT_ROLLING, 10)
    check["status"] = "прогнана"
    report = _report(check, findings, certified=True)
    problems = gate_certify.check({"rules": [RULE]}, report)[0]
    assert any("certified" in p for p in problems)
    assert any("неизвестные окна" in p for p in problems)


def test_full_window_proof_with_no_assignment_rows_is_a_valid_report():
    check, findings = _run(_dataset(assignments=[], duties=[], history=_history(**{
        "Конец последней смены": "2026-09-02T00:00"})))
    report = _report(check, findings, certified=True)
    assert check["rows_checked"] == 0
    assert gate_certify.check({"rules": [RULE]}, report)[0] == []


@pytest.mark.parametrize("mutate", [
    lambda cov: cov.update(expected=cov["expected"] + 1),
    lambda cov: cov.update(unknown=0),
    lambda cov: cov["windows"][0].update(reason=""),
    lambda cov: cov["windows"][0].update(end_exclusive="2020-01-01"),
    lambda cov: cov["windows"].append(copy.deepcopy(cov["windows"][0])),
])
def test_window_evidence_tampering_is_rejected(mutate):
    check, findings = _run(_dataset())
    mutate(check["window_coverage"])
    problems = dr.validate_report(_report(check, findings), {"rules": [RULE]})
    assert any("window_coverage" in p or "частичная" in p for p in problems)


@pytest.mark.parametrize("kind", ["point", "open_interval"])
def test_removing_continuous_cell_and_reconciling_counts_is_still_rejected(kind):
    check, findings = _run(_dataset(assignments=[], duties=[]))
    cov = check["window_coverage"]
    removed = next(w for w in cov["windows"] if w["ending_domain"]["kind"] == kind)
    cov["windows"].remove(removed)
    cov["expected"] -= 1
    cov["unknown" if removed["status"] == "unknown" else "evaluated"] -= 1
    report = _report(check, findings)
    assert any("критические ячейки" in p for p in dr.validate_report(report, {"rules": [RULE]}))


def test_removing_all_unknown_cells_cannot_forge_continuous_positive_certificate():
    check, findings = _run(_dataset(assignments=[], duties=[]))
    cov = check["window_coverage"]
    cov["windows"] = [w for w in cov["windows"] if w["status"] == "evaluated"]
    cov.update(expected=len(cov["windows"]), evaluated=len(cov["windows"]), unknown=0)
    check["status"] = "прогнана"
    report = _report(check, findings, certified=True)
    assert any("критические ячейки" in p for p in gate_certify.check({"rules": [RULE]}, report)[0])


def test_forged_passing_span_is_recomputed_from_rest_intervals():
    check, findings = _run(_dataset(assignments=[], duties=[]))
    check["window_coverage"]["employee_proofs"]["E1"]["passing_window_end_spans"][0]["start_inclusive"] = "2026-09-04T00:00:00"
    assert any("объединения окон" in p for p in dr.validate_report(_report(check, findings), {"rules": [RULE]}))
