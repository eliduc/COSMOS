"""C01/C02 phase 2 — co-located multi-crew stage-2 path (deterministic).

Exercises the whole Python pipeline WITHOUT the JVM by simulating the Kotlin
GERAD2P solve output: build the synthetic per-role slots, "assign" employees,
expand to per-(leg,seat) assignments, then check coverage and the full
trajectory certificate. Single-crew isomorphism is guarded by the mandatory
`_coverage_validator.py --gerad` and `_gerad_trajectory_check.py` gates.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _gerad_stage2_roster as s2  # noqa: E402
from _coverage_validator import check as coverage_check  # noqa: E402
from _gerad_trajectory_check import (  # noqa: E402
    certify_artifact_linkage, check_inst)

# One round-trip pairing L1(BASE->DEST) + L2(DEST->BASE), each leg a uniform
# Captain+FirstOfficer seat pair; a Captain-skilled and an FO-skilled crew.
CATALOG = {"selected": [{"fns": ["L1", "L2"],
                         "active_fns": ["L1", "L2"], "cost": 1}]}


def _payload():
    return {
        "airports": [{"code": "BASE"}, {"code": "DEST"}],
        "employees": [
            {"id": "E1", "homeAirport": "BASE", "skills": ["Captain"]},
            {"id": "E2", "homeAirport": "BASE", "skills": ["FirstOfficer"]}],
        "flights": [
            {"flightNumber": "L1", "departureAirport": "BASE",
             "departureUTCDateTime": "2026-03-01T08:00:00",
             "arrivalAirport": "DEST",
             "arrivalUTCDateTime": "2026-03-01T10:00:00",
             "aircraftType": "A320"},
            {"flightNumber": "L2", "departureAirport": "DEST",
             "departureUTCDateTime": "2026-03-01T12:00:00",
             "arrivalAirport": "BASE",
             "arrivalUTCDateTime": "2026-03-01T14:00:00",
             "aircraftType": "A320"}],
        "flightAssignments": [
            {"id": "1", "flight": "L1", "indexInFlight": 0,
             "requiredSkill": "Captain"},
            {"id": "2", "flight": "L1", "indexInFlight": 1,
             "requiredSkill": "FirstOfficer"},
            {"id": "3", "flight": "L2", "indexInFlight": 0,
             "requiredSkill": "Captain"},
            {"id": "4", "flight": "L2", "indexInFlight": 1,
             "requiredSkill": "FirstOfficer"}]}


def _solve(syn, assign):
    """Simulate the Kotlin solve: fill each synthetic slot (matched by its
    (indexInFlight, requiredSkill)) with the given employee id."""
    solved = json.loads(json.dumps(syn))
    want = {(idx, skill): eid for idx, skill, eid in assign}
    for a in solved["flightAssignments"]:
        a["employee"] = want.get((a["indexInFlight"], a["requiredSkill"]))
    return solved


def test_build_synthetic_two_slots_per_pairing():
    syn = s2.build_synthetic_payload(_payload(), CATALOG["selected"])
    assert len(syn["flights"]) == 1
    slots = syn["flightAssignments"]
    assert len(slots) == 2
    assert {(s["indexInFlight"], s["requiredSkill"]) for s in slots} == \
        {(0, "Captain"), (1, "FirstOfficer")}
    # ids unique per role slot (Kotlin @PlanningId)
    assert len({s["id"] for s in slots}) == 2


def test_expand_two_seats_per_leg_and_coverage():
    p = _payload()
    syn = s2.build_synthetic_payload(p, CATALOG["selected"])
    solved = _solve(syn, [(0, "Captain", "E1"), (1, "FirstOfficer", "E2")])
    expanded = s2.expand_to_flight_level(p, solved)
    assert len(expanded["flightAssignments"]) == 4        # 2 legs x 2 seats
    cov = coverage_check(p, expanded)
    assert cov["fully_covered"] and cov["missing"] == 0
    assert cov["skill_mismatches"] == 0 and cov["required"] == 2


def test_wrong_skill_crew_is_not_coverage():
    # Captain seat filled by the FO-only crew and vice-versa -> every seat
    # skill-mismatched -> no leg covered.
    p = _payload()
    syn = s2.build_synthetic_payload(p, CATALOG["selected"])
    solved = _solve(syn, [(0, "Captain", "E2"), (1, "FirstOfficer", "E1")])
    expanded = s2.expand_to_flight_level(p, solved)
    cov = coverage_check(p, expanded)
    assert not cov["fully_covered"]
    assert cov["skill_mismatches"] == 4 and cov["missing"] == 2


def test_partial_multicrew_leaves_seat_open():
    # FO seat unassigned -> each leg has one open seat -> not fully covered.
    p = _payload()
    syn = s2.build_synthetic_payload(p, CATALOG["selected"])
    solved = _solve(syn, [(0, "Captain", "E1"), (1, "FirstOfficer", None)])
    expanded = s2.expand_to_flight_level(p, solved)
    cov = coverage_check(p, expanded)
    assert not cov["fully_covered"]
    assert cov["missing_slots"] == 2 and cov["unassigned_slots"] == 2


def _traj(solved):
    crew = s2._crew_of_pairing(solved)["C_000000"]
    return {"pairings": [{
        "pid": "C_000000", "employee": crew[0][2],
        "crew": [{"indexInFlight": i, "requiredSkill": s, "employee": e}
                 for i, s, e in crew],
        "fns": ["L1", "L2"], "active_fns": ["L1", "L2"]}]}


def test_trajectory_linkage_and_physics_multicrew(tmp_path):
    p = _payload()
    syn = s2.build_synthetic_payload(p, CATALOG["selected"])
    solved = _solve(syn, [(0, "Captain", "E1"), (1, "FirstOfficer", "E2")])
    expanded = s2.expand_to_flight_level(p, solved)
    traj = _traj(solved)
    # linkage: both seats link to their own crew, no mismatch/ghost/extra
    certify_artifact_linkage(traj, CATALOG, p, expanded)
    # full per-employee trajectory: BOTH crew validated, round trip legal
    tp, sp, cp, pp = (tmp_path / n for n in
                      ("traj.json", "sol.json", "cat.json", "pay.json"))
    tp.write_text(json.dumps(traj))
    sp.write_text(json.dumps(expanded))
    cp.write_text(json.dumps(CATALOG))
    pp.write_text(json.dumps(p))
    r = check_inst("mc", traj_path=tp, sol_path=sp,
                   catalog_path=cp, payload_path=pp)
    assert r["violations_total"] == 0
    assert r["employees"] == 2         # Captain AND FO each got a trajectory


def test_expand_stamps_leg_identity_and_coverage_joins():
    # Phase 3: a payload with materialized flightLegId must yield expanded
    # rows carrying the SAME leg identity — otherwise the coverage join
    # diverges (demand keys on flightLegId, supply falls back to fn).
    p = _payload()
    for f in p["flights"]:
        f["flightLegId"] = f"{f['flightNumber']}|{f['departureUTCDateTime']}"
    for a in p["flightAssignments"]:
        a["flightLegId"] = next(
            f["flightLegId"] for f in p["flights"]
            if f["flightNumber"] == a["flight"])
        a["crewSlotId"] = f"s{a['id']}"
    syn = s2.build_synthetic_payload(p, CATALOG["selected"])
    solved = _solve(syn, [(0, "Captain", "E1"), (1, "FirstOfficer", "E2")])
    expanded = s2.expand_to_flight_level(p, solved)
    for row in expanded["flightAssignments"]:
        assert row["flightLegId"].startswith(row["flight"] + "|")
        assert row["crewSlotId"] == (
            f"{row['flightLegId']}#{row['indexInFlight']}")
    cov = coverage_check(p, expanded)
    assert cov["fully_covered"] and cov["missing"] == 0
    assert cov["required"] == 2 and cov["skill_mismatches"] == 0


def test_expand_mirrors_demand_namespace_not_flights():
    # Ревью (находки 3/15): рейсы материализованы, СЛОТЫ спроса — нет.
    # Supply обязан зеркалить спрос (без штампа), иначе полный ростер
    # читается как 100% непокрытый.
    p = _payload()
    for f in p["flights"]:
        f["flightLegId"] = f"{f['flightNumber']}|{f['departureUTCDateTime']}"
    # flightAssignments намеренно БЕЗ flightLegId
    syn = s2.build_synthetic_payload(p, CATALOG["selected"])
    solved = _solve(syn, [(0, "Captain", "E1"), (1, "FirstOfficer", "E2")])
    expanded = s2.expand_to_flight_level(p, solved)
    assert all("flightLegId" not in row
               for row in expanded["flightAssignments"])
    cov = coverage_check(p, expanded)
    assert cov["fully_covered"] and cov["missing"] == 0


def test_legacy_scalar_artifact_still_certifies(tmp_path):
    # A pre-phase-2 artifact (scalar `employee`, no `crew`) must still link and
    # validate — the certificate reads it as a single (0,'Captain') slot.
    p = _payload()
    # single-Captain-only variant of the payload/solution
    p["flightAssignments"] = [a for a in p["flightAssignments"]
                              if a["requiredSkill"] == "Captain"]
    p["employees"] = [{"id": "E1", "homeAirport": "BASE",
                       "skills": ["Captain"]}]
    expanded = {"airports": p["airports"], "employees": p["employees"],
                "flights": p["flights"], "flightAssignments": [
                    {"id": "1", "flight": "L1", "indexInFlight": 0,
                     "requiredSkill": "Captain", "employee": "E1"},
                    {"id": "2", "flight": "L2", "indexInFlight": 0,
                     "requiredSkill": "Captain", "employee": "E1"}]}
    traj = {"pairings": [{"pid": "C_000000", "employee": "E1",
                          "fns": ["L1", "L2"], "active_fns": ["L1", "L2"]}]}
    certify_artifact_linkage(traj, CATALOG, p, expanded)   # no raise
    cov = coverage_check(p, expanded)
    assert cov["fully_covered"] and cov["required"] == 2
