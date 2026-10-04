"""Regression test (audit P0, 2026-07-12): a schedule containing an
assignment with an EMPTY eligible-employee set must come back INFEASIBLE,
not OPTIMAL 0hard with a silently dropped assignment."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flight_crew_scheduling.domain import (  # noqa: E402
    Airport, Employee, Flight, FlightAssignment, FlightCrewSchedule,
)
from flight_crew_scheduling.ortools_engine import solve_with_ortools  # noqa: E402


def _tiny_schedule():
    a = Airport(code="AAA", name="AAA", languages=["en"])
    b = Airport(code="BBB", name="BBB", languages=["en"])
    f = Flight(
        flight_number="FL1",
        departure_airport=a,
        departure_utc_date_time="2024-01-01T08:00:00",
        arrival_airport=b,
        arrival_utc_date_time="2024-01-01T10:00:00",
        aircraft_type="A320",
    )
    # The only employee lacks the required skill => empty eligible set.
    emp = Employee(id="E1", name="E1", home_airport=a,
                   skills=["FlightAttendant"], languages=["en"],
                   type_ratings=["A320"])
    fa = FlightAssignment(id="1", flight=f, index_in_flight=0,
                          required_skill="Captain")
    return FlightCrewSchedule(airports=[a, b], employees=[emp],
                              flights=[f], flight_assignments=[fa])


def test_empty_eligible_set_is_infeasible():
    schedule, status = solve_with_ortools(_tiny_schedule(), time_limit_s=5)
    assert "INFEASIBLE_NO_ALLOWED_EMPLOYEE" in status, status
    assert not str(schedule.score).startswith("0hard"), schedule.score


if __name__ == "__main__":
    test_empty_eligible_set_is_infeasible()
    print("OK: empty eligible set -> INFEASIBLE_NO_ALLOWED_EMPLOYEE")
