"""C01/C02 phase-2 review hardening (2026-07-19) — three LOW findings closed.

Threat model: a HAND-EDITED or EXTERNAL solution.json (never the honest
`expand_to_flight_level` output, which copies requiredSkill from the demand
faithfully). "No solver state is trusted": the validators must derive the
demand from the payload, not believe the solution's own claims.

  1. coverage: the seat's requiredSkill is read from the PAYLOAD demand, never
     from the solution row — a relabelled/nulled skill cannot forge coverage.
  2. coverage: one physical crew member cannot fill two seats of the same leg;
     the repeat is a duplicate, not a second fill.
  3. linkage: a duplicate (flight, indexInFlight) seat key must raise, not
     collapse under last-write-wins (a collusive row order could exploit it).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _coverage_validator import check  # noqa: E402
from _gerad_trajectory_check import certify_artifact_linkage  # noqa: E402


def _payload_2seat():
    """One leg L0 with a Captain seat (idx0) and a FirstOfficer seat (idx1)."""
    return {
        "flights": [{"flightNumber": "L0"}],
        "employees": [
            {"id": "CAP", "skills": ["Captain"]},
            {"id": "CAP2", "skills": ["Captain"]},
            {"id": "FO", "skills": ["FirstOfficer"]},
            {"id": "BOTH", "skills": ["Captain", "FirstOfficer"]}],
        "flightAssignments": [
            {"id": "1", "flight": "L0", "indexInFlight": 0,
             "requiredSkill": "Captain"},
            {"id": "2", "flight": "L0", "indexInFlight": 1,
             "requiredSkill": "FirstOfficer"}]}


def _sol(rows):
    return {"flightAssignments": rows}


# --------------------------------------------------------- finding 1 (skills)

def test_honest_two_seat_assignment_is_covered():
    # Control: the RIGHT crew in the right seats still certifies (the hardening
    # must not over-reject).
    r = check(_payload_2seat(), _sol([
        {"flight": "L0", "indexInFlight": 0, "requiredSkill": "Captain",
         "employee": "CAP"},
        {"flight": "L0", "indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": "FO"}]))
    assert r["fully_covered"] and r["missing"] == 0
    assert r["skill_mismatches"] == 0 and r["duplicates"] == 0


def test_relabelled_seat_skill_cannot_forge_coverage():
    # F1a: the solution RELABELS the FirstOfficer seat as 'Captain' and fills
    # both seats with Captain-only crew. The seat's required skill is the
    # PAYLOAD's (idx1 = FirstOfficer), so the FO seat stays uncovered — a
    # solution with both seats 'Captain' on a Captain+FO leg is NOT covered.
    r = check(_payload_2seat(), _sol([
        {"flight": "L0", "indexInFlight": 0, "requiredSkill": "Captain",
         "employee": "CAP"},
        {"flight": "L0", "indexInFlight": 1, "requiredSkill": "Captain",
         "employee": "CAP2"}]))
    assert not r["fully_covered"]
    assert r["missing_slots"] == 1 and r["skill_mismatches"] == 1


def test_falsy_solution_skill_does_not_bypass_check():
    # F1b: nulling the solution row's requiredSkill used to skip the skill
    # gate (`check_skill and rskill and ...`). The check now reads the payload,
    # so a Captain-only crew on the (payload) FirstOfficer seat is caught.
    for forged in (None, "", 0):
        r = check(_payload_2seat(), _sol([
            {"flight": "L0", "indexInFlight": 0, "requiredSkill": "Captain",
             "employee": "CAP"},
            {"flight": "L0", "indexInFlight": 1, "requiredSkill": forged,
             "employee": "CAP2"}]))
        assert not r["fully_covered"], forged
        assert r["skill_mismatches"] == 1, forged


# ------------------------------------------------------ finding 2 (dedup)

def test_one_employee_cannot_fill_both_seats_of_a_leg():
    # F2: a dual-skilled employee seated as BOTH the Captain and the
    # FirstOfficer of one leg is one physical body, not two fills.
    r = check(_payload_2seat(), _sol([
        {"flight": "L0", "indexInFlight": 0, "requiredSkill": "Captain",
         "employee": "BOTH"},
        {"flight": "L0", "indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": "BOTH"}]))
    assert not r["fully_covered"]
    assert r["duplicates"] == 1 and r["missing_slots"] == 1


def test_distinct_dual_skilled_crew_still_cover_the_leg():
    # Control: two DISTINCT dual-skilled crew legitimately cover both seats.
    r = check(_payload_2seat(), _sol([
        {"flight": "L0", "indexInFlight": 0, "requiredSkill": "Captain",
         "employee": "BOTH"},
        {"flight": "L0", "indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": "FO"}]))
    assert r["fully_covered"] and r["duplicates"] == 0


# ----------------------------------------- finding 3 (duplicate seat key)

def _linkage_fixtures():
    catalog = {"selected": [
        {"fns": ["L1", "L2"], "active_fns": ["L1", "L2"], "cost": 100},
        {"fns": ["L2", "L3"], "active_fns": ["L3"], "cost": 80}]}
    payload = {"flights": [{"flightNumber": f} for f in ("L1", "L2", "L3")]}
    traj = {"pairings": [
        {"pid": "C_000000", "employee": "E1",
         "fns": ["L1", "L2"], "active_fns": ["L1", "L2"]},
        {"pid": "C_000001", "employee": "E2",
         "fns": ["L2", "L3"], "active_fns": ["L3"]}]}
    return traj, catalog, payload


def test_duplicate_seat_key_raises_even_when_last_writer_matches():
    # F3: the genuine trajectory owns (L2,0)->E1. A tampered solution seats a
    # GHOST E2 on L2 FIRST, then the genuine E1 LAST. Last-write-wins would
    # leave E1 (matching the trajectory) and the old mismatch check saw
    # nothing — the double-seating was hidden by a collusive row order. The
    # duplicate seat key must now raise regardless of order.
    traj, catalog, payload = _linkage_fixtures()
    solution = {"flightAssignments": [
        {"flight": "L1", "employee": "E1"},
        {"flight": "L2", "employee": "E2"},   # ghost writer FIRST
        {"flight": "L2", "employee": "E1"},   # genuine writer LAST (LWW picks)
        {"flight": "L3", "employee": "E2"}]}
    with pytest.raises(ValueError, match="twice"):
        certify_artifact_linkage(traj, catalog, payload, solution)


def test_duplicate_seat_key_raises_in_the_other_order_too():
    # The order that the pre-existing mismatch check DID catch must keep
    # raising (now via the duplicate-key guard) — order independence.
    traj, catalog, payload = _linkage_fixtures()
    solution = {"flightAssignments": [
        {"flight": "L1", "employee": "E1"},
        {"flight": "L2", "employee": "E1"},
        {"flight": "L3", "employee": "E2"},
        {"flight": "L2", "employee": "E2"}]}   # duplicate LAST
    with pytest.raises(ValueError, match="twice"):
        certify_artifact_linkage(traj, catalog, payload, solution)


def test_genuine_solution_without_duplicate_keys_still_certifies():
    # Control: the honest single-writer-per-seat solution must still pass.
    traj, catalog, payload = _linkage_fixtures()
    solution = {"flightAssignments": [
        {"flight": "L1", "employee": "E1"},
        {"flight": "L2", "employee": "E1"},
        {"flight": "L3", "employee": "E2"}]}
    certify_artifact_linkage(traj, catalog, payload, solution)  # no raise
