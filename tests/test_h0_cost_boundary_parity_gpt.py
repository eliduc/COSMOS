"""Cold-0 boundary witness: crossing a horizon is not two overlapping duties.

Authoring host does not execute these tests; run them on Legion.
"""
from copy import deepcopy

from vmp.run.cost_comparison import compare_roster_cost
from vmp.run.cost_scope import assess_roster_cost
from vmp.run.roster_certify import build_solution_dataset


def _assignment(flight, start, end, departure="PLY", arrival="SVG"):
    return {"employee": "E", "flight": flight, "dep_time": start, "arr_time": end,
            "dep_airport": departure, "arr_airport": arrival, "base": "PLY", "duty_id": flight}


def _solution(rows, start, end, slots=()):
    return {"solution": "boundary parity witness", "assignments": rows,
            "window_horizon": {"start_inclusive": start, "end_exclusive": end},
            "window_employees": [{"employee": "E", "base": "PLY"}],
            "cost_context": {"task": "rostering", "unassigned_required_slots": list(slots)}}


def test_arrival_after_horizon_keeps_initial_unknown_and_all_150_unassigned_slots():
    slots = [{"crewSlotId": f"U-{i}", "flight": "U", "start": "2026-09-30T10:00",
              "end": "2026-09-30T12:00"} for i in range(150)]
    solution = _solution([_assignment("F", "2026-09-30T23:30", "2026-10-01T01:10")],
                         "2026-09-30T00:00", "2026-10-01T00:00", slots)
    result = assess_roster_cost(build_solution_dataset(solution))
    assert result["unknown_count"] == 151 and result["known_nights"] == 0
    unknowns = result["unknown_events"]
    assert sum(row["reason"] == "unassigned_slot" for row in unknowns) == 150
    assert {row["reason"] for row in unknowns} == {
        "unassigned_slot", "initial_boundary_location_interval_unknown"}
    assert result["scope_end"] == "2026-10-01T00:00:00"
    assert solution["assignments"][0]["arr_time"] == "2026-10-01T01:10"
    assert result["complete"] is False and result["certainty"] == "partial"


def test_departure_before_horizon_has_no_initial_overlap():
    solution = _solution([_assignment("F", "2026-09-30T23:30", "2026-10-01T01:10")],
                         "2026-10-01T00:00", "2026-10-01T01:10")
    result = assess_roster_cost(build_solution_dataset(solution))
    assert result["known_nights"] == 0 and result["unknown_events"] == []
    assert solution["assignments"][0]["dep_time"] == "2026-09-30T23:30"


def test_actual_assignment_overlap_remains_explicit():
    solution = _solution([
        _assignment("A", "2026-09-30T10:00", "2026-09-30T12:00"),
        _assignment("B", "2026-09-30T11:00", "2026-09-30T13:00", "SVG", "PLY"),
    ], "2026-09-30T10:00", "2026-09-30T13:00")
    result = assess_roster_cost(build_solution_dataset(solution))
    assert result["unknown_events"] == [{"employee": "E", "reason": "overlapping_assignments",
        "start": "2026-09-30T12:00:00", "end": "2026-09-30T11:00:00", "station": "SVG"}]
    assert result["complete"] is False


def test_comparator_still_rejects_a_spurious_boundary_overlap():
    solution = _solution([_assignment("F", "2026-09-30T23:30", "2026-10-01T01:10")],
                         "2026-09-30T00:00", "2026-10-01T00:00")
    independent = assess_roster_cost(build_solution_dataset(solution))
    solver = deepcopy(independent)
    assert compare_roster_cost(independent, solver)["verdict"] == "OK"
    solver["unknown_events"].append({"employee": "E", "reason": "overlapping_assignments",
        "start": "2026-10-01T01:10", "end": "2026-10-01T00:00", "station": "SVG"})
    solver["unknown_count"] += 1
    comparison = compare_roster_cost(independent, solver)
    assert comparison["verdict"] == "DIVERGE" and comparison["known_events_match"]
    assert comparison["same_scope"] and not comparison["uncertainty_match"]
    assert comparison["reasons"] == ["unknown_event_scope_mismatch"]
