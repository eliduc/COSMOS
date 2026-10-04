"""Audit 2026-07-12, round 6: mutation tests for the exact artifact
certificate — deleting/zeroing active_fns, dropping pairings, swapping
employees or adding ghost assignments must all raise, not pass."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _gerad_trajectory_check import certify_artifact_linkage  # noqa: E402


def _fixtures():
    catalog = {"selected": [
        {"fns": ["L1", "L2"], "active_fns": ["L1", "L2"], "cost": 100},
        {"fns": ["L2", "L3"], "active_fns": ["L3"], "cost": 80},
    ]}
    payload = {"flights": [{"flightNumber": f} for f in ("L1", "L2", "L3")]}
    traj = {"pairings": [
        {"pid": "C_000000", "employee": "E1",
         "fns": ["L1", "L2"], "active_fns": ["L1", "L2"]},
        {"pid": "C_000001", "employee": "E2",
         "fns": ["L2", "L3"], "active_fns": ["L3"]},
    ]}
    solution = {"flightAssignments": [
        {"flight": "L1", "employee": "E1"},
        {"flight": "L2", "employee": "E1"},
        {"flight": "L3", "employee": "E2"},
    ]}
    return traj, catalog, payload, solution


def test_genuine_artifacts_pass():
    certify_artifact_linkage(*_fixtures())


def test_zeroed_active_fns_raises():
    traj, catalog, payload, solution = _fixtures()
    m = copy.deepcopy(traj)
    for p in m["pairings"]:
        p["active_fns"] = []
    with pytest.raises(ValueError):
        certify_artifact_linkage(m, catalog, payload, solution)


def test_dropped_pairing_raises():
    traj, catalog, payload, solution = _fixtures()
    m = copy.deepcopy(traj)
    m["pairings"] = m["pairings"][:1]
    with pytest.raises(ValueError):
        certify_artifact_linkage(m, catalog, payload, solution)


def test_extra_pairing_raises():
    traj, catalog, payload, solution = _fixtures()
    m = copy.deepcopy(traj)
    m["pairings"].append({"pid": "C_000099", "employee": "E1",
                          "fns": ["L1"], "active_fns": ["L1"]})
    with pytest.raises(ValueError):
        certify_artifact_linkage(m, catalog, payload, solution)


def test_swapped_fns_raises():
    traj, catalog, payload, solution = _fixtures()
    m = copy.deepcopy(traj)
    m["pairings"][1]["fns"] = ["L3"]          # dropped the TDH leg
    with pytest.raises(ValueError):
        certify_artifact_linkage(m, catalog, payload, solution)


def test_swapped_employee_raises():
    traj, catalog, payload, solution = _fixtures()
    m = copy.deepcopy(traj)
    m["pairings"][0]["employee"] = "E9"
    with pytest.raises(ValueError):
        certify_artifact_linkage(m, catalog, payload, solution)


def test_double_active_ownership_raises():
    traj, catalog, payload, solution = _fixtures()
    cat = copy.deepcopy(catalog)
    m = copy.deepcopy(traj)
    cat["selected"][1]["active_fns"] = ["L2", "L3"]
    m["pairings"][1]["active_fns"] = ["L2", "L3"]
    with pytest.raises(ValueError):
        certify_artifact_linkage(m, cat, payload, solution)


def test_unassigned_pairing_with_ghost_solution_assignment_raises():
    traj, catalog, payload, solution = _fixtures()
    m = copy.deepcopy(traj)
    m["pairings"][1]["employee"] = None       # pairing unassigned...
    with pytest.raises(ValueError):           # ...but L3 still assigned
        certify_artifact_linkage(m, catalog, payload, solution)


def test_solution_assignment_outside_active_coverage_raises():
    traj, catalog, payload, solution = _fixtures()
    p = copy.deepcopy(payload)
    p["flights"].append({"flightNumber": "L4"})
    s = copy.deepcopy(solution)
    s["flightAssignments"].append({"flight": "L4", "employee": "E1"})
    with pytest.raises(ValueError):
        certify_artifact_linkage(traj, catalog, p, s)


def test_conflicting_duplicate_assignment_raises():
    traj, catalog, payload, solution = _fixtures()
    s = copy.deepcopy(solution)
    s["flightAssignments"].append({"flight": "L2", "employee": "E2"})
    with pytest.raises(ValueError):
        certify_artifact_linkage(traj, catalog, payload, s)


def test_missing_solution_raises():
    traj, catalog, payload, _ = _fixtures()
    with pytest.raises(ValueError):
        certify_artifact_linkage(traj, catalog, payload, None)


def test_empty_trajectory_raises():
    _, catalog, payload, solution = _fixtures()
    with pytest.raises(ValueError):
        certify_artifact_linkage({"pairings": []}, catalog, payload,
                                 solution)


# --- многоместные цепочки: нумерация спроса против нумерации цепочки ------

def _multiseat():
    """Цепочка с экипажем ШИРЕ спроса участка — ровно тот случай, на
    котором сертификат был невычислим для набора заказчика.

    Экипаж цепочки — поролевой максимум по её участкам: три капитана,
    места 0..2. Участок L2 требует всего двух капитанов, поэтому разворот
    выпускает два ряда с номерами СПРОСА 0 и 1, а третий член экипажа
    едет сверхштатно. Прежняя сверка требовала совпадения номеров мест и
    объявляла артефакты рассинхронизированными."""
    catalog = {"selected": [
        {"fns": ["L1", "L2"], "active_fns": ["L1", "L2"], "cost": 10}]}
    payload = {"flights": [{"flightNumber": f} for f in ("L1", "L2")],
               "flightAssignments": [
                   {"flight": "L1", "indexInFlight": 0,
                    "requiredSkill": "Captain"},
                   {"flight": "L1", "indexInFlight": 1,
                    "requiredSkill": "Captain"},
                   {"flight": "L1", "indexInFlight": 2,
                    "requiredSkill": "Captain"},
                   {"flight": "L2", "indexInFlight": 0,
                    "requiredSkill": "Captain"},
                   {"flight": "L2", "indexInFlight": 1,
                    "requiredSkill": "Captain"}]}
    traj = {"pairings": [
        {"pid": "C_000000", "fns": ["L1", "L2"], "active_fns": ["L1", "L2"],
         "crew": [{"indexInFlight": 0, "requiredSkill": "Captain",
                   "employee": "E1"},
                  {"indexInFlight": 1, "requiredSkill": "Captain",
                   "employee": "E2"},
                  {"indexInFlight": 2, "requiredSkill": "Captain",
                   "employee": "E3"}]}]}
    solution = {"flightAssignments": [
        {"flight": "L1", "indexInFlight": 0, "requiredSkill": "Captain",
         "employee": "E1"},
        {"flight": "L1", "indexInFlight": 1, "requiredSkill": "Captain",
         "employee": "E2"},
        {"flight": "L1", "indexInFlight": 2, "requiredSkill": "Captain",
         "employee": "E3"},
        {"flight": "L2", "indexInFlight": 0, "requiredSkill": "Captain",
         "employee": "E1"},
        {"flight": "L2", "indexInFlight": 1, "requiredSkill": "Captain",
         "employee": "E2"}]}
    return traj, catalog, payload, solution


def test_wide_crew_over_narrow_leg_certifies():
    """Главная проверка: широкий экипаж на узком участке — не дефект."""
    certify_artifact_linkage(*_multiseat())


def test_seat_swap_within_one_rank_is_not_a_defect():
    """Места одного ранга взаимозаменяемы — перестановка не расхождение."""
    traj, catalog, payload, solution = _multiseat()
    s = copy.deepcopy(solution)
    s["flightAssignments"][3]["employee"] = "E2"
    s["flightAssignments"][4]["employee"] = "E1"
    certify_artifact_linkage(traj, catalog, payload, s)


def test_outsider_on_a_leg_still_raises():
    """А вот чужой человек на участке обязан ловиться по-прежнему."""
    traj, catalog, payload, solution = _multiseat()
    s = copy.deepcopy(solution)
    s["flightAssignments"][0]["employee"] = "E9"
    with pytest.raises(ValueError, match="вне экипажа"):
        certify_artifact_linkage(traj, catalog, payload, s)


def test_undermanned_leg_still_raises():
    """Недобор мест — тоже расхождение."""
    traj, catalog, payload, solution = _multiseat()
    s = copy.deepcopy(solution)
    s["flightAssignments"] = s["flightAssignments"][:3]
    with pytest.raises(ValueError, match="посажено"):
        certify_artifact_linkage(traj, catalog, payload, s)


def test_overmanned_leg_still_raises():
    traj, catalog, payload, solution = _multiseat()
    s = copy.deepcopy(solution)
    s["flightAssignments"].append(
        {"flight": "L2", "indexInFlight": 2, "requiredSkill": "Captain",
         "employee": "E3"})
    with pytest.raises(ValueError, match="посажено"):
        certify_artifact_linkage(traj, catalog, payload, s)
