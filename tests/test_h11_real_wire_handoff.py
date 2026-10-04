"""Reviewer-only deterministic witness for the frozen 52e5b83 H11 handoff.

No endpoint, solver, model, or service is called. Shapes match
FlightCrewSchedulingResource.packagedRunResult/packagedRunApplied:
the result is FlightCrewSchedule, while layoverCost belongs to RunRecord
and is exposed in the separate applied-rules response.
"""
from webapp import certify_input as ci
from vmp.run import roster_certify as rc


def test_run_scoped_applied_layover_report_reaches_independent_comparison():
    horizon = {"start_inclusive": "2026-09-04T10:00:00",
               "end_exclusive": "2026-09-04T12:00:00",
               "source": "declared_window_horizon"}
    flight = {"flightNumber": "F1", "flightLegId": "F1",
              "departureUTCDateTime": horizon["start_inclusive"],
              "arrivalUTCDateTime": horizon["end_exclusive"],
              "departureAirport": {"code": "PLY"},
              "arrivalAirport": {"code": "SVG"}}
    employee = {"id": "E1", "homeAirport": {"code": "PLY"}}
    seat = {"id": "S1", "crewSlotId": "S1", "flight": flight,
            "employee": employee, "requiredSkill": "Pilot", "deadhead": False}
    payload = {"flights": [flight], "employees": [employee],
               "flightAssignments": [seat], "window_horizon": horizon}
    # /result returns FlightCrewSchedule: it has no layoverCost property.
    solved = {"score": "0hard/0soft", "flightAssignments": [seat],
              "employees": [employee], "window_horizon": horizon}
    # RosterLayoverCost.report for the exact two-hour horizon: no gaps,
    # no midnight, no unassigned seats, hence complete zero night events.
    layover = {
        "version": 2, "contract": "B", "task": "rostering",
        "scope": "actual_home_known_assignment_gaps_and_explicit_history_intervals",
        "timezone": "input_timestamp_calendar",
        "scope_start": horizon["start_inclusive"],
        "scope_end": horizon["end_exclusive"],
        "scope_source": "declared_window_horizon",
        "night_definition": "calendar_date_transition_at_station",
        "duty_semantics": "normative_duty_identity", "employees": ["E1"],
        "complete": True, "certainty": "complete_in_declared_scope",
        "full_cost_confirmed": False, "night_scope_cost_confirmed": True,
        "known_subtotal_is_full_objective": False, "known_nights": 0,
        "known_calendar_nights": {}, "known_subtotal": 0, "known_events": [],
        "unassigned_required_slots": 0, "unknown_count": 0, "unknown_events": []}
    applied = {"runId": "WIRE-RUN", "state": "готов", "layoverCost": layover,
               "lastRun": {"rulesProfile": "DEFAULT", "logic": {"blocksSeen": 0},
                           "profileReadings": {"min_rest_home_min": 480,
                                               "min_rest_outstation_min": 480}}}
    pkg = {"model": {"rules": []}}
    inp, _ = ci.build(pkg, payload, solved, applied, name="run-scoped H11 wire")
    dataset = rc.build_solution_dataset(inp)
    svod = {"rules": [{"id": "R-L", "kind": "SOFT",
                       "template_fit": {"category": "A", "family": "layover_penalty"}}]}
    rc.measure_soft_rules(svod, dataset)
    print("WIRE_COMPARISON", dataset["cost_assessment"]["solver_comparison"])
    assert inp.get("solver_layover_cost") == applied["layoverCost"], (
        "The genuine /applied-rules layoverCost was dropped: exporter only "
        "reads /result.layoverCost, a property absent from FlightCrewSchedule")
    assert dataset["cost_assessment"]["solver_comparison"]["verdict"] == "OK"
