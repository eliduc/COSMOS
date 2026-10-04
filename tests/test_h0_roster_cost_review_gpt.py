"""Independent regression witnesses for contract-B roster cost scope edges."""
from copy import deepcopy

from vmp.run.cost_scope import assess_roster_cost
from vmp.run.roster_certify import build_solution_dataset


START, END = "2026-09-04T00:00", "2026-09-06T00:00"


def assignment(flight, a, b, departure="PLY", arrival="SVG", duty=None):
    return {"employee": "E", "flight": flight, "dep_time": a, "arr_time": b,
            "dep_airport": departure, "arr_airport": arrival, "base": "PLY",
            "duty_id": duty or flight}


def interval(a, b, station="PLY", employee="E"):
    return {"employee": employee, "start": a, "end": b, "station": station}


def dataset(rows=(), intervals=(), *, start=START, end=END, people=None, boundaries=None):
    solution = {
        "solution": "cost review witness", "assignments": list(rows),
        "window_horizon": {"start_inclusive": start, "end_exclusive": end},
        "window_employees": people if people is not None else [{"employee": "E", "base": "PLY"}],
        "cost_history_intervals": list(intervals),
        "cost_context": {"task": "rostering", "boundaries": boundaries or {}},
    }
    if rows:
        return build_solution_dataset(solution)
    # The assessor accepts a pure idle universe; the public certificate
    # boundary still correctly rejects an entirely empty solved schedule.
    return {"tables": {"SOL": {"Назначения": {"columns": [], "rows": []}}},
            **{key: value for key, value in solution.items()
               if key not in ("solution", "assignments")}}


def reasons(result):
    return {x["reason"] for x in result["unknown_events"]}


def test_home_departure_does_not_prove_the_missing_initial_days():
    result = assess_roster_cost(dataset([
        assignment("F", "2026-09-05T23:00", END, arrival="PLY")]))
    assert result["known_nights"] == 0 and not result["complete"]
    assert result["unknown_events"] == [{
        "employee": "E", "reason": "initial_boundary_location_interval_unknown",
        "start": "2026-09-04T00:00:00", "end": "2026-09-05T23:00:00", "station": "PLY"}]


def test_last_minute_location_snapshot_does_not_close_initial_horizon():
    result = assess_roster_cost(dataset([
        assignment("F", "2026-09-05T23:00", END, arrival="PLY")],
        [interval("2026-09-05T22:59", "2026-09-05T23:00")]))
    assert result["unknown_events"][0]["end"] == "2026-09-05T22:59:00"
    assert not result["complete"]


def test_touching_same_station_intervals_union_to_full_initial_coverage():
    result = assess_roster_cost(dataset([
        assignment("F", "2026-09-05T23:00", END, departure="SVG", arrival="PLY")],
        [interval(START, "2026-09-05T00:00", "SVG"),
         interval("2026-09-05T00:00", "2026-09-05T23:00", "SVG")]))
    assert result["complete"] and result["known_nights"] == 1
    assert result["known_events"][0]["night"] == "2026-09-04"
    assert result["known_events"][0]["row"] == 2
    assert result["known_events"][0]["sheet"] == "История местоположений"
    assert result["known_events"][0]["source"] == "explicit_history_union"
    assert result["known_events"][0]["evidence_rows"] == [2, 3]


def test_duplicate_and_overlapping_location_evidence_is_counted_once():
    base = [interval(START, END, "SVG"), interval(START, END, "SVG"),
            interval("2026-09-04T12:00", "2026-09-05T12:00", "SVG")]
    result = assess_roster_cost(dataset(intervals=base))
    assert result["complete"] and result["known_nights"] == 2


def test_idle_employee_without_location_is_not_zero_cost():
    result = assess_roster_cost(dataset())
    assert result["known_nights"] == 0 and not result["complete"]
    assert reasons(result) == {"idle_employee_location_unknown"}
    assert result["unknown_events"][0]["end"] == "2026-09-06T00:00:00"


def test_idle_employee_full_home_interval_has_known_zero_nights():
    result = assess_roster_cost(dataset(intervals=[interval(START, END)]))
    assert result["complete"] and result["known_nights"] == 0


def test_missing_home_is_unknown_even_with_full_location_and_no_assignments():
    result = assess_roster_cost(dataset(intervals=[interval(START, END, "SVG")],
                                       people=[{"employee": "E"}]))
    assert reasons(result) == {"home_base_unknown"}
    assert not result["complete"] and result["known_nights"] == 0


def test_conflicting_location_evidence_is_order_independent_and_never_known():
    intervals = [interval(START, END, "SVG"), interval(START, END, "GRT")]
    a = assess_roster_cost(dataset(intervals=intervals))
    b = assess_roster_cost(dataset(intervals=list(reversed(intervals))))
    assert a == b and a["known_nights"] == 0
    assert reasons(a) == {"location_evidence_conflict"}


def test_gap_in_union_is_addressed_instead_of_imputed_zero():
    result = assess_roster_cost(dataset(intervals=[
        interval(START, "2026-09-04T12:00"), interval("2026-09-05T12:00", END)]))
    assert result["unknown_events"] == [{
        "employee": "E", "reason": "idle_employee_location_unknown",
        "start": "2026-09-04T12:00:00", "end": "2026-09-05T12:00:00", "station": None}]


def test_same_normative_duty_midnight_connection_is_not_a_layover():
    rows = [assignment("A", "2026-09-04T22:00", "2026-09-04T23:30", duty="D"),
            assignment("B", "2026-09-05T00:30", "2026-09-05T01:30", "SVG", "PLY", duty="D")]
    result = assess_roster_cost(dataset(rows, start=rows[0]["dep_time"], end=rows[-1]["arr_time"]))
    assert result["complete"] and result["known_nights"] == 0


def test_missing_normative_identity_is_not_guessed_from_a_midnight_gap():
    rows = [assignment("A", "2026-09-04T18:00", "2026-09-04T19:00"),
            assignment("B", "2026-09-05T08:00", "2026-09-05T09:00", "SVG", "PLY")]
    for row in rows:
        row.pop("duty_id")
    result = assess_roster_cost(dataset(rows, start=rows[0]["dep_time"], end=rows[-1]["arr_time"]))
    assert result["known_nights"] == 0
    assert reasons(result) == {"normative_duty_identity_unknown"}


def test_one_night_across_two_duties_is_not_double_counted_by_history():
    rows = [assignment("A", "2026-09-04T18:00", "2026-09-04T19:00"),
            assignment("B", "2026-09-05T08:00", "2026-09-05T09:00", "SVG", "PLY")]
    gap = interval(rows[0]["arr_time"], rows[1]["dep_time"], "SVG")
    result = assess_roster_cost(dataset(rows, [gap, deepcopy(gap)],
                                       start=rows[0]["dep_time"], end=rows[-1]["arr_time"]))
    assert result["complete"] and result["known_nights"] == 1


def test_short_closed_scope_cannot_claim_a_larger_declared_horizon_complete():
    row = assignment("F", START, "2026-09-04T01:00")
    result = assess_roster_cost(dataset([row], boundaries={
        "E": {"mode": "closed_scope", "at": row["arr_time"], "reason": "observed segment ends"}}))
    assert not result["complete"] and result["scope_end"] == "2026-09-06T00:00:00"
    assert result["unknown_events"][0]["reason"] == "terminal_continuation_unknown"
    assert result["unknown_events"][0]["end"] == result["scope_end"]


def test_explicit_terminal_union_can_establish_rest_without_automatic_charge():
    row = assignment("F", START, "2026-09-04T01:00")
    result = assess_roster_cost(dataset([row], [
        interval(row["arr_time"], "2026-09-05T00:00", "SVG"),
        interval("2026-09-05T00:00", END, "SVG")]))
    assert result["complete"] and result["known_nights"] == 2


def test_known_boundary_location_must_match_observed_airport():
    row = assignment("F", "2026-09-05T23:00", END, departure="PLY", arrival="PLY")
    result = assess_roster_cost(dataset([row], [interval(START, row["dep_time"], "SVG")]))
    assert result["known_nights"] == 0 and "discontinuous_timeline" in reasons(result)
