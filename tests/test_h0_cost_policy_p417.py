# -*- coding: utf-8 -*-
"""Политика P-417 штрафной сверки (единая версия 06.09.2026, план
§3.10.3; строки H11-7, GPT-013/014).

Охраняемое: расхождение области или событий с решателем — красное даже
при равных числах; отсутствующая запись решателя — «несравнимо», не
зелёный; известная часть при неизвестном остатке — «сошлось» на области
учёта (cost=partial) без автоматического certified=False; полное
совпадение одних ночёвок подтверждает только член цены «ночёвки»
(night_cost_confirmed), full_cost_confirmed требует всех членов; прежний
путь без объявленной области вердикт не понижает и полноты не
подтверждает."""
from copy import deepcopy

import pytest

from vmp import penalty_reconcile as pr
from vmp.run import verdicts as vd


def _assessment(complete=True):
    scope = {"version": 2, "contract": "B", "task": "rostering", "scope": "known",
             "night_definition": "calendar_date_transition_at_station",
             "timezone": "input_timestamp_calendar",
             "duty_semantics": "normative_duty_identity",
             "scope_start": "2026-09-04T00:00:00", "scope_end": "2026-10-01T00:00:00",
             "employees": ["H01"], "known_nights": 1,
             "known_events": [{"employee": "H01", "night": "2026-09-04",
                               "airport": "GRT", "row": 2}]}
    if complete:
        scope.update(unknown_count=0, unknown_events=[], complete=True,
                     certainty="complete_in_declared_scope")
    else:
        scope.update(unknown_count=1, complete=False, certainty="partial",
                     unknown_events=[{"employee": "H01", "reason": "terminal_continuation_unknown",
                                      "start": "2026-09-05T09:00:00",
                                      "end": "2026-10-01T00:00:00", "station": "PLY"}])
    scope["solver_comparison"] = {"verdict": "OK", "same_scope": True,
                                  "known_events_match": True, "uncertainty_match": True,
                                  "reasons": []}
    return scope


def _row(rid, fam, coefficient, unit):
    return {"id": rid, "penalty": {"coefficient": coefficient, "violation_unit": unit},
            "template": {"family": fam}}


def _inputs(rows, *, breakdown_soft=None):
    """Форма + отчёт решателя + разбор счёта + тело сертификата, у которых
    числовая сверка сходится по каждой строке (одно совпадение × вес)."""
    form = {"rostering": {"soft": [_row(*r) for r in rows]}}
    weight_rules = {r[0]: f"C-{r[0]}" for r in rows}
    applied = {f"C-{r[0]}": r[2] for r in rows}
    constraints = {f"C-{r[0]}": {"matches": 1, "soft": -r[2]} for r in rows}
    soft_total = -sum(r[2] for r in rows) if breakdown_soft is None else breakdown_soft
    last_run = {"appliedWeights": applied, "weightRules": weight_rules}
    breakdown = {"soft": soft_total, "constraints": constraints}
    certificate = {
        "checks": [{"id": f"C-{r[0]}-мера", "basis": {"rule": r[0]}, "status": "прогнана"}
                   for r in rows],
        "findings": [{"check": f"C-{r[0]}-мера", "rule_refs": [r[0]]} for r in rows],
    }
    return form, last_run, breakdown, certificate


NIGHT = ("R-L", "layover_penalty", 3, "ночь")
EXTENSION = ("R-E", "duty_extension_penalty", 2, "смена")


def test_night_parity_alone_confirms_only_the_night_member():
    form, last_run, breakdown, cert = _inputs([NIGHT, EXTENSION])
    cert["certificate"] = {"cost_assessment": _assessment(complete=True)}
    got = pr.reconcile(form, last_run, breakdown, cert)
    assert got["verdict"] == pr.OK
    assert got["arithmetic_subtotal_verdict"] == got["known_subtotal_verdict"] == pr.OK
    assert got["cost_scope_comparison"]["verdict"] == "OK"
    assert got["price_complete"] is True and got["night_cost_confirmed"] is True
    assert got["price_scope"] == "nights"
    # Продление смены сверено только числом, областью событий не покрыто
    # — полная стоимость не подтверждена (GPT-014: старый флаг
    # «complete and OK» не переносится).
    assert got["price_members_uncovered"] == ["R-E"]
    assert got["full_cost_confirmed"] is False
    # H11-R1 (GPT-022): пользовательская стоимость «подтверждена» —
    # только при full_cost_confirmed; полнота одних ночёвок при
    # непокрытом члене цены — partial с показанием, допустимость
    # (certified) от partial не отзывается.
    assert vd._cost_of({"penalty_reconciliation": got}) == "partial"
    res = vd.result_of_certificate({"penalty_reconciliation": got, "checks_run": 1,
                                    "penalty_reconciliation_clean": True})
    assert res["cost"] == "partial" and res["feasibility"] == "confirmed"
    assert "ночёвки подтверждены" in res["cost_note"] and "R-E" in res["cost_note"]
    assert "полная стоимость не подтверждена" in res["cost_note"]
    line = vd.result_line(res)
    assert "ночёвки подтверждены" in line and "R-E" in line
    assert vd.certified_of({"penalty_reconciliation_clean": True}, res) is True


def test_full_cost_confirmed_is_the_only_road_to_confirmed_cost():
    """H11-R1: «подтверждена» ⇔ full_cost_confirmed; показания partial
    называют, что подтверждено (ночёвки) и какие члены не покрыты."""
    form, last_run, breakdown, cert = _inputs([NIGHT])
    cert["cost_assessment"] = _assessment(complete=True)
    got = pr.reconcile(form, last_run, breakdown, cert)
    assert got["full_cost_confirmed"] is True
    assert vd._cost_of({"penalty_reconciliation": got}) == "confirmed"
    assert vd.cost_note_of({"penalty_reconciliation": got}) is None
    # Та же запись с отозванным флагом полного покрытия — partial, не
    # «подтверждена»: полнота ночёвок сама по себе всей цены не доказывает.
    forged = dict(got, full_cost_confirmed=False, price_members_uncovered=["R-X"])
    assert vd._cost_of({"penalty_reconciliation": forged}) == "partial"
    assert "R-X" in vd.cost_note_of({"penalty_reconciliation": forged})
    # Полнота заявлена без сверки событий — тоже partial, словами.
    bare = dict(got, full_cost_confirmed=False, night_cost_confirmed=False,
                price_members_uncovered=[])
    assert vd._cost_of({"penalty_reconciliation": bare}) == "partial"
    assert "независимой сверки событий" in vd.cost_note_of({"penalty_reconciliation": bare})


@pytest.mark.parametrize("removal", ["pop", "none"])
def test_removed_solver_comparison_on_declared_scope_is_incomparable(removal):
    """H11-R2 (GPT-022): у ростерной оценки с объявленной областью
    отсутствующая сверка с записью решателя — отсутствующее обязательное
    свидетельство: «несравнимо» / not_checked, не «сошлось с пометкой»."""
    form, last_run, breakdown, cert = _inputs([NIGHT])
    scope = _assessment(complete=True)
    if removal == "pop":
        scope.pop("solver_comparison")
    else:
        scope["solver_comparison"] = None
    cert["cost_assessment"] = scope
    got = pr.reconcile(form, last_run, breakdown, cert)
    assert got["arithmetic_subtotal_verdict"] == pr.OK
    assert got["verdict"] == got["known_subtotal_verdict"] == pr.INCOMPARABLE
    assert got["cost_scope_comparison"] is None
    assert got["night_cost_confirmed"] is False and got["full_cost_confirmed"] is False
    assert got["price_scope"] is None
    assert "обязательное свидетельство отсутствует" in got["cost_scope_note"]
    assert "отсутствует" in got["cost_scope_note"]
    assert not pr.is_clean(got)
    assert vd._cost_of({"penalty_reconciliation": got}) == "not_checked"
    # Числовое расхождение сильнее: остаётся «разошлось».
    _f, _l, bad_breakdown, _c = _inputs([NIGHT], breakdown_soft=-99)
    assert pr.reconcile(form, last_run, bad_breakdown, cert)["verdict"] == pr.DIVERGE


@pytest.mark.parametrize("broken", ["OK", 7, {"same_scope": True, "reasons": []},
                                    {"verdict": "GREEN"}])
def test_corrupted_solver_comparison_on_declared_scope_is_incomparable(broken):
    """H11-R2: повреждённая сверка (не объект / без вердикта / вердикт
    вне словаря) — то же отсутствующее свидетельство."""
    form, last_run, breakdown, cert = _inputs([NIGHT])
    scope = _assessment(complete=True)
    scope["solver_comparison"] = broken
    cert["cost_assessment"] = scope
    got = pr.reconcile(form, last_run, breakdown, cert)
    assert got["verdict"] == got["known_subtotal_verdict"] == pr.INCOMPARABLE
    assert got["cost_scope_comparison"] is None
    assert got["night_cost_confirmed"] is False and got["full_cost_confirmed"] is False
    assert "обязательное свидетельство отсутствует" in got["cost_scope_note"]
    assert "повреждена" in got["cost_scope_note"]
    assert vd._cost_of({"penalty_reconciliation": got}) == "not_checked"


def test_missing_comparison_without_declared_scope_keeps_legacy_diagnostic():
    """H11-R2, граница: голое заявление полноты без границ области и
    прежний путь по «Сменам» — не объявленная область; их показание
    «сверка не проводилась» сохраняется (test_penalty_reconcile), но
    полноту оно не подтверждает."""
    form, last_run, breakdown, cert = _inputs([NIGHT])
    cert["cost_assessment"] = {"task": "rostering", "complete": True}
    got = pr.reconcile(form, last_run, breakdown, cert)
    assert got["verdict"] == pr.OK and "не проводилась" in got["cost_scope_note"]
    assert got["full_cost_confirmed"] is False
    assert vd._cost_of({"penalty_reconciliation": got}) == "partial"


def test_full_cost_confirmed_when_every_price_member_is_a_night_member():
    form, last_run, breakdown, cert = _inputs([NIGHT])
    cert["cost_assessment"] = _assessment(complete=True)
    got = pr.reconcile(form, last_run, breakdown, cert)
    assert got["verdict"] == pr.OK and got["full_cost_confirmed"] is True
    assert got["price_members_uncovered"] == []
    # Целевая по другому ограничению — ещё один член цены вне области событий.
    last_run2 = dict(last_run, objective={"id": "OBJ", "constraint": "C-OBJ", "weight": 1})
    breakdown2 = deepcopy(breakdown)
    breakdown2["constraints"]["C-OBJ"] = {"matches": 4, "soft": -4}
    breakdown2["soft"] -= 4
    last_run2["effectiveWeights"] = {"C-OBJ": 1}
    got2 = pr.reconcile(form, last_run2, breakdown2, cert)
    assert got2["verdict"] == pr.OK and got2["night_cost_confirmed"] is True
    assert got2["price_members_uncovered"] == ["objective:OBJ"]
    assert got2["full_cost_confirmed"] is False


def test_scope_or_event_divergence_is_red_even_with_equal_numbers():
    form, last_run, breakdown, cert = _inputs([NIGHT])
    scope = _assessment(complete=True)
    scope["solver_comparison"] = {"verdict": "DIVERGE", "same_scope": True,
                                  "known_events_match": False, "uncertainty_match": True,
                                  "reasons": ["known_event_identity_mismatch"]}
    cert["cost_assessment"] = scope
    got = pr.reconcile(form, last_run, breakdown, cert)
    assert got["arithmetic_subtotal_verdict"] == pr.OK
    assert got["verdict"] == got["known_subtotal_verdict"] == pr.DIVERGE
    assert not got["full_cost_confirmed"] and not got["night_cost_confirmed"]
    assert "known_event_identity_mismatch" in got["cost_scope_note"]
    assert not pr.is_clean(got)
    assert vd._cost_of({"penalty_reconciliation": got}) == "mismatch"


def test_missing_solver_record_is_incomparable_not_green():
    form, last_run, breakdown, cert = _inputs([NIGHT])
    scope = _assessment(complete=True)
    scope["solver_comparison"] = {"verdict": "INCOMPARABLE", "same_scope": False,
                                  "known_events_match": False, "uncertainty_match": False,
                                  "reasons": ["solver_layover_cost_missing"]}
    cert["cost_assessment"] = scope
    got = pr.reconcile(form, last_run, breakdown, cert)
    assert got["arithmetic_subtotal_verdict"] == pr.OK
    assert got["verdict"] == pr.INCOMPARABLE and not got["full_cost_confirmed"]
    assert "solver_layover_cost_missing" in got["cost_scope_note"]
    assert vd._cost_of({"penalty_reconciliation": got}) == "not_checked"
    # Числовое расхождение остаётся расхождением, а не «несравнимо».
    _f, _l, bad_breakdown, _c = _inputs([NIGHT], breakdown_soft=-99)
    got2 = pr.reconcile(form, last_run, bad_breakdown, cert)
    assert got2["verdict"] == pr.DIVERGE


def test_partial_known_part_keeps_ok_verdict_and_partial_cost():
    form, last_run, breakdown, cert = _inputs([NIGHT])
    cert["cost_assessment"] = _assessment(complete=False)
    got = pr.reconcile(form, last_run, breakdown, cert)
    assert got["verdict"] == got["known_subtotal_verdict"] == pr.OK
    assert got["price_complete"] is False and got["full_cost_confirmed"] is False
    assert got["night_cost_confirmed"] is False and got["price_scope"] is None
    assert "остаток области учёта неизвестен" in got["cost_scope_note"]
    assert pr.is_clean(got)
    # Стоимость — «подтверждена на области учёта», не «подтверждена».
    assert vd._cost_of({"penalty_reconciliation": got}) == "partial"


def test_legacy_scope_without_declared_horizon_is_not_downgraded_and_not_full():
    form, last_run, breakdown, cert = _inputs([NIGHT])
    cert["cost_assessment"] = {
        "version": 2, "contract": "B", "task": "rostering", "source": "legacy_duties_sheet",
        "known_nights": 1, "known_events": [], "unknown_count": 2,
        "unknown_events": [{"employee": None, "reason": "declared_accounting_horizon_unknown"},
                           {"employee": None, "reason": "employee_universe_unknown"}],
        "complete": False, "certainty": "partial", "full_cost_confirmed": False,
        "solver_comparison": None, "note": "область учёта не объявлена"}
    got = pr.reconcile(form, last_run, breakdown, cert)
    assert got["verdict"] == pr.OK and got["cost_scope_comparison"] is None
    assert got["full_cost_confirmed"] is False and got["price_complete"] is False
    assert "не проводилась" in got["cost_scope_note"]
    assert vd._cost_of({"penalty_reconciliation": got}) == "partial"


def test_pairing_assessment_and_absent_assessment_leave_verdict_alone():
    form, last_run, breakdown, cert = _inputs([NIGHT])
    got = pr.reconcile(form, last_run, breakdown, cert)
    assert got["verdict"] == pr.OK and got["cost_assessment"] is None
    assert got["full_cost_confirmed"] is False and got["price_complete"] is None
    assert vd._cost_of({"penalty_reconciliation": got}) == "confirmed"
    cert["cost_assessment"] = {"task": "pairing", "complete": False,
                               "solver_comparison": {"verdict": "DIVERGE"}}
    got2 = pr.reconcile(form, last_run, breakdown, cert)
    assert got2["verdict"] == pr.OK and got2["cost_scope_comparison"] is None
    assert "pairing" in got2["cost_scope_note"]
