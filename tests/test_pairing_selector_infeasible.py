"""Regression: a flight with NO candidate pairing must yield INFEASIBLE
(audit fix of the dummy-coverage bug)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flight_crew_scheduling.domain import Airport, Flight  # noqa: E402
from flight_crew_scheduling.pairing import Pairing  # noqa: E402
from flight_crew_scheduling.pairing_selector import (  # noqa: E402
    select_pairings_set_partitioning,
)


def test_empty_catalog_with_flights_is_infeasible():
    # Audit 2026-07-12, round 5: empty catalog + non-empty flight list is
    # INFEASIBLE (every flight uncoverable), not an "EMPTY objective=0" run.
    selected, info = select_pairings_set_partitioning(
        ["F1", "F2"], [], time_limit_seconds=5, num_workers=1)
    assert info["status"] == "INFEASIBLE", info
    assert set(info["uncoverable_flights"]) == {"F1", "F2"}
    assert selected == []


def test_empty_flight_list_is_empty_run():
    selected, info = select_pairings_set_partitioning(
        [], [], time_limit_seconds=5, num_workers=1)
    assert info["status"] == "EMPTY"
    assert selected == []


def test_uncoverable_flight_is_infeasible():
    a = Airport(code="AAA", name="AAA", languages=["en"])
    b = Airport(code="BBB", name="BBB", languages=["en"])
    f1 = Flight(flight_number="F1", departure_airport=a,
                departure_utc_date_time="2024-01-01T08:00:00",
                arrival_airport=b,
                arrival_utc_date_time="2024-01-01T10:00:00",
                aircraft_type="A320")
    p = Pairing.from_flights("P1", [f1])
    selected, info = select_pairings_set_partitioning(
        ["F1", "F2_UNCOVERED"], [p], time_limit_seconds=5, num_workers=1)
    assert info["status"] == "INFEASIBLE", info
    assert "F2_UNCOVERED" in info.get("uncoverable_flights", []), info
    assert selected == []
