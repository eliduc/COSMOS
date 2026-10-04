from copy import deepcopy
import pytest

from vmp.run.cost_comparison import compare_roster_cost
from vmp import penalty_reconcile as pr


def records():
    scope = {"version": 2, "scope": "known", "night_definition": "calendar_date_transition_at_station",
             "timezone": "input_timestamp_calendar", "duty_semantics": "normative_duty_identity",
             "scope_start": "2026-09-04T00:00", "scope_end": "2026-10-01T00:00"}
    independent = {**scope, "task": "rostering", "employees": ["H01", "H02"], "known_nights": 1,
                   "known_events": [{"employee": "H01", "night": "2026-09-04", "airport": "SVG", "row": 5}],
                   "unknown_count": 1, "unknown_events": [{"employee": "H01", "reason": "terminal_continuation_unknown",
                       "start": "2026-09-05T09:00", "end": "2026-10-01T00:00", "station": "PLY"}],
                   "complete": False, "certainty": "partial"}
    solver = deepcopy(independent)
    solver["known_events"] = [{"employee": "H01", "date": "2026-09-04", "station": "SVG", "from": "seat123"}]
    solver["scope_end"] = "2026-10-01T00:00:00"
    return independent, solver


def test_scope_and_event_identity_ignore_row_not_physical_location():
    independent, solver = records()
    assert compare_roster_cost(independent, solver)["verdict"] == "OK"
    solver["known_events"][0]["station"] = "GRT"
    result = compare_roster_cost(independent, solver)
    assert result["verdict"] == "DIVERGE" and not result["known_events_match"]


def test_count_equality_does_not_hide_shortened_horizon_or_unknown_gap():
    independent, solver = records()
    solver["scope_start"] = "2026-09-05T00:00"
    assert not compare_roster_cost(independent, solver)["same_scope"]
    _, solver = records()
    solver["unknown_events"][0]["end"] = "2026-09-06T00:00"
    assert not compare_roster_cost(independent, solver)["uncertainty_match"]


def test_missing_solver_evidence_and_duplicate_event_fail_closed():
    independent, solver = records()
    assert compare_roster_cost(independent, None)["verdict"] == "INCOMPARABLE"
    solver["known_events"] *= 2
    solver["known_nights"] = 2
    assert compare_roster_cost(independent, solver)["verdict"] == "INCOMPARABLE"


def test_matching_partial_evidence_is_not_a_full_price():
    independent, solver = records()
    independent["solver_comparison"] = compare_roster_cost(independent, solver)
    result = pr.reconcile(None, {}, {"constraints": {}, "soft": 0}, {"cost_assessment": independent})
    assert result["known_subtotal_verdict"] == pr.OK
    assert result["cost_scope_comparison"]["verdict"] == "OK"
    # Политика P-417 (единая версия 06.09.2026, план §3.10.3; GPT-013/014):
    # сошедшаяся известная часть при неизвестном остатке — «сошлось» на
    # области учёта (cost=partial), не «несравнимо»; полная стоимость при
    # этом не подтверждена. Авторское ожидание INCOMPARABLE адаптировано.
    assert result["verdict"] == pr.OK and not result["full_cost_confirmed"]
    assert result["price_complete"] is False and not result["night_cost_confirmed"]


def test_complete_count_cannot_override_scope_mismatch():
    independent, solver = records()
    independent.update(complete=True, certainty="complete_in_declared_scope", unknown_count=0, unknown_events=[])
    solver.update(complete=True, certainty="complete_in_declared_scope", unknown_count=0, unknown_events=[])
    solver["timezone"] = "other"
    independent["solver_comparison"] = compare_roster_cost(independent, solver)
    result = pr.reconcile(None, {}, {"constraints": {}, "soft": 0}, {"cost_assessment": independent})
    assert result["verdict"] == pr.DIVERGE and not result["full_cost_confirmed"]
    assert result["arithmetic_subtotal_verdict"] == pr.OK
    assert result["known_subtotal_verdict"] == pr.DIVERGE


def test_omitted_idle_employee_is_a_scope_mismatch_even_with_no_event():
    independent, solver = records()
    solver["employees"] = ["H01"]
    assert compare_roster_cost(independent, solver)["verdict"] == "DIVERGE"


def test_certainty_label_must_agree_with_addressed_unknowns():
    independent, solver = records()
    solver["complete"] = True
    solver["certainty"] = "complete_in_declared_scope"
    assert compare_roster_cost(independent, solver)["verdict"] == "INCOMPARABLE"


def overflow_record():
    independent, solver = records()
    solver["known_subtotal"] = None
    solver["night_cost_confirmed"] = False
    solver["unknown_events"].append({"employee": None, "station": None,
        "reason": "known_cost_overflow", "start": solver["scope_start"], "end": solver["scope_end"]})
    solver["unknown_count"] += 1
    return independent, solver


def test_numeric_unavailability_is_not_physical_divergence_or_acceptance():
    independent, solver = overflow_record()
    comparison = compare_roster_cost(independent, solver)
    assert comparison["verdict"] == "INCOMPARABLE"
    assert comparison["same_scope"] and comparison["known_events_match"]
    assert comparison["uncertainty_match"]
    assert comparison["reasons"] == ["solver_known_cost_overflow"]
    independent["solver_comparison"] = comparison
    result = pr.reconcile(None, {}, {"constraints": {}, "soft": 0}, {"cost_assessment": independent})
    assert result["verdict"] == pr.INCOMPARABLE
    assert result["known_subtotal_verdict"] == pr.INCOMPARABLE
    assert not result["night_cost_confirmed"] and not result["full_cost_confirmed"]


@pytest.mark.parametrize("damage", ["event", "scope", "physical_unknown"])
def test_numeric_overflow_cannot_hide_physical_disagreement(damage):
    independent, solver = overflow_record()
    if damage == "event": solver["known_events"][0]["station"] = "OTHER"
    elif damage == "scope": solver["employees"] = ["H01"]
    else: solver["unknown_events"][0]["station"] = "OTHER"
    assert compare_roster_cost(independent, solver)["verdict"] == "DIVERGE"


@pytest.mark.parametrize("damage", ["subtotal", "confirmed", "address", "range", "duplicate"])
def test_malformed_numeric_overflow_is_invalid_evidence(damage):
    independent, solver = overflow_record()
    if damage == "subtotal": solver["known_subtotal"] = -2
    elif damage == "confirmed": solver["night_cost_confirmed"] = True
    elif damage == "address": solver["unknown_events"][-1]["employee"] = "H01"
    elif damage == "range": solver["unknown_events"][-1]["end"] = "2027-01-01"
    else:
        solver["unknown_events"].append(deepcopy(solver["unknown_events"][-1]))
        solver["unknown_count"] += 1
    comparison = compare_roster_cost(independent, solver)
    assert comparison["verdict"] == "INCOMPARABLE"
    assert "invalid_cost_evidence" in comparison["reasons"]
