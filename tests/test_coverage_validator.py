"""Coverage validator unit tests (audit package B)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _coverage_validator import check  # noqa: E402

PAYLOAD = {"flights": [{"flightNumber": f"L{i}"} for i in range(3)]}


def _sol(assignments):
    return {"flightAssignments": assignments}


def test_full_coverage():
    r = check(PAYLOAD, _sol([{"flight": f"L{i}", "employee": "E1"}
                             for i in range(3)]))
    assert r["fully_covered"] and r["missing"] == 0


def test_slot_honest_single_seat_isomorphic():
    # C01 phase 1: a payload WITH explicit one-seat-per-leg demand behaves like
    # the legacy set predicate — each leg's single Captain slot filled -> OK.
    payload = {"flights": [{"flightNumber": "L0"}, {"flightNumber": "L1"}],
               "flightAssignments": [
                   {"flight": "L0", "indexInFlight": 0, "requiredSkill": "Captain"},
                   {"flight": "L1", "indexInFlight": 0, "requiredSkill": "Captain"}]}
    r = check(payload, _sol([{"flight": "L0", "employee": "E1"},
                             {"flight": "L1", "employee": "E1"}]))
    assert r["fully_covered"] and r["missing_slots"] == 0 and r["required"] == 2


def test_single_seat_nonzero_index_still_covered():
    # Regression (phase-1 review, 2026-07-18): coverage counts SEATS PER LEG,
    # not (leg, indexInFlight) slots. The two-stage expander hardcodes
    # indexInFlight:0 on every regenerated supply seat, so a legitimately
    # covered single-seat upload whose sole seat carries a NONZERO index
    # (1-based numbering / captain-only export) must NOT flip to not-covered.
    payload = {"flights": [{"flightNumber": "F1"}],
               "flightAssignments": [
                   {"flight": "F1", "indexInFlight": 1,
                    "requiredSkill": "Captain"}]}
    # supply keys the same leg with index 0 (as the expander emits it)
    r = check(payload, _sol([{"flight": "F1", "indexInFlight": 0,
                              "employee": "E1"}]))
    assert r["fully_covered"] and r["missing_slots"] == 0
    assert r["missing_legs"] == [] and r["required"] == 1


def test_slot_honest_multi_seat_underfill_not_covered():
    # C01: a Captain+FirstOfficer leg whose roster fills ONLY the Captain is
    # NOT fully covered (the old flightNumber-set predicate wrongly said OK).
    payload = {"flights": [{"flightNumber": "L0"}],
               "flightAssignments": [
                   {"flight": "L0", "indexInFlight": 0, "requiredSkill": "Captain"},
                   {"flight": "L0", "indexInFlight": 1,
                    "requiredSkill": "FirstOfficer"}]}
    r = check(payload, _sol([{"flight": "L0", "indexInFlight": 0,
                              "employee": "E1"}]))
    assert not r["fully_covered"]
    assert r["missing_slots"] == 1 and r["missing_legs"] == ["L0"]
    # both seats filled -> covered
    r2 = check(payload, _sol([
        {"flight": "L0", "indexInFlight": 0, "employee": "E1"},
        {"flight": "L0", "indexInFlight": 1, "employee": "E2"}]))
    assert r2["fully_covered"] and r2["missing_slots"] == 0


def test_unknown_employee_rejected_when_pool_present():
    # M12: when the payload lists employees, an assignment to an id NOT in the
    # pool (a coordinated E999 tamper) is NOT coverage.
    payload = {"flights": [{"flightNumber": "L0"}],
               "employees": [{"id": "E1"}, {"id": "E2"}]}
    r = check(payload, _sol([{"flight": "L0", "employee": "E1"}]))
    assert r["fully_covered"] and r["unknown_employees"] == 0
    r = check(payload, _sol([{"flight": "L0", "employee": "E999"}]))
    assert not r["fully_covered"] and r["unknown_employees"] == 1


def test_employee_fk_skipped_when_no_pool():
    # M12 guard: a payload WITHOUT an employees list cannot be FK-checked and
    # must not false-fail (some GERAD academic instances carry no pool).
    r = check(PAYLOAD, _sol([{"flight": f"L{i}", "employee": "E1"}
                             for i in range(3)]))
    assert r["fully_covered"] and r["unknown_employees"] == 0


def test_missing_leg_detected():
    r = check(PAYLOAD, _sol([{"flight": "L0", "employee": "E1"},
                             {"flight": "L1", "employee": "E1"}]))
    assert not r["fully_covered"]
    assert r["missing_legs"] == ["L2"]


def test_unassigned_slot_is_not_coverage():
    r = check(PAYLOAD, _sol([{"flight": "L0", "employee": "E1"},
                             {"flight": "L1", "employee": "E1"},
                             {"flight": "L2", "employee": None}]))
    assert not r["fully_covered"]
    assert r["missing_legs"] == ["L2"]
    assert r["unassigned_slots"] == 1


def test_duplicate_and_unknown():
    r = check(PAYLOAD, _sol([{"flight": "L0", "employee": "E1"},
                             {"flight": "L0", "employee": "E2"},
                             {"flight": "L1", "employee": "E1"},
                             {"flight": "L2", "employee": "E1"},
                             {"flight": "GHOST", "employee": "E1"}]))
    assert r["duplicates"] == 1
    assert r["unknown_flights"] == 1
    assert not r["fully_covered"]
