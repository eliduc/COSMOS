"""Stage-2 TDH semantics tests (audit package B): the synthetic payload
and the flight-level expansion must carry only ACTIVE legs, while the
trajectory artifact keeps the full leg list — that pair of invariants is
what makes the TDH reconstruction verifiable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from _gerad_stage2_roster import (  # noqa: E402
    build_synthetic_payload, expand_to_flight_level,
)

PAYLOAD = {
    "airports": [{"code": c, "name": c, "languages": ["en"]}
                 for c in ("BASE1", "AIR1")],
    "employees": [{"id": "E1", "name": "E1", "homeAirport": "BASE1",
                   "skills": ["Captain"], "languages": ["en"]}],
    "flights": [
        {"flightNumber": "L1", "departureAirport": "BASE1",
         "departureUTCDateTime": "2000-01-01T08:00:00",
         "arrivalAirport": "AIR1",
         "arrivalUTCDateTime": "2000-01-01T09:00:00",
         "aircraftType": "727"},
        {"flightNumber": "L2", "departureAirport": "AIR1",
         "departureUTCDateTime": "2000-01-01T10:00:00",
         "arrivalAirport": "BASE1",
         "arrivalUTCDateTime": "2000-01-01T11:00:00",
         "aircraftType": "727"},
    ],
    "flightAssignments": [],
}
# Pairing covers both legs but only L1 is ACTIVE (L2 ridden as TDH).
SELECTED = [{"fns": ["L1", "L2"], "active_fns": ["L1"], "cost": 100}]


def test_synthetic_payload_uses_active_legs_only():
    syn = build_synthetic_payload(PAYLOAD, SELECTED)
    f = syn["flights"][0]
    assert f["legIds"] == ["L1"]
    assert f["blockMinutes"] == 60          # only the active hour
    assert f["departureAirport"] == "BASE1"  # span covers ALL legs
    assert f["arrivalAirport"] == "BASE1"


def test_expansion_assigns_active_legs_only():
    syn = build_synthetic_payload(PAYLOAD, SELECTED)
    solved = {"flights": syn["flights"],
              "flightAssignments": [{"flight": "C_000000",
                                     "employee": "E1"}]}
    expanded = expand_to_flight_level(PAYLOAD, solved)
    fns = [a["flight"] for a in expanded["flightAssignments"]]
    assert fns == ["L1"]                    # TDH leg NOT assigned
    assert expanded["flightAssignments"][0]["employee"] == "E1"


def test_pure_deadhead_pairing_skipped():
    syn = build_synthetic_payload(
        PAYLOAD, [{"fns": ["L1", "L2"], "active_fns": [], "cost": 100}])
    assert syn["flights"] == []
