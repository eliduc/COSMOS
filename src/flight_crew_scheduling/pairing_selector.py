"""Stage 1B: CP-SAT set-partitioning to select an optimal subset of pairings.

Given a list of candidate Pairings (from ``pairing_generator``), select a
minimum-cost subset such that **every flight is covered by exactly one
pairing**. This is the classical airline-industry **Crew Pairing Problem**
formulated as set-partitioning IP::

    minimize    sum(c_p × x_p)
    subject to  sum_{p covers flight f}(x_p) == 1   for all flights f
                x_p ∈ {0, 1}

Cost ``c_p`` is a heuristic — we use a discount for multi-flight pairings
to incentivize the solver to combine flights into trips (= more efficient
crew utilization, less paperwork, fewer hand-offs)::

    c_p = duty_minutes - DISCOUNT_PER_EXTRA_LEG × (num_legs - 1)

The CP-SAT model has |pairings| Boolean variables and |flights|
equality constraints. For our datasets it solves in <1 sec.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .pairing import Pairing

# Cost discount applied to multi-flight pairings (per extra leg beyond 1).
# Higher discount → solver prefers longer pairings.
DISCOUNT_PER_EXTRA_LEG = 200   # arbitrary units (compared to duty minutes)


def select_pairings_set_partitioning(
    flight_numbers: List[str],
    candidates: List[Pairing],
    time_limit_seconds: int = 60,
    num_workers: int = 8,
) -> Tuple[List[Pairing], dict]:
    """Solve the set-partitioning IP and return chosen pairings.

    Returns:
        (selected_pairings, info) where info contains solver stats:
            status, objective, wall_time_s, num_vars, num_constraints
    """
    from ortools.sat.python import cp_model

    if not flight_numbers:
        return [], {"status": "EMPTY", "objective": 0, "wall_time_s": 0.0}

    # Audit fix (2026-07-12, round 5): an EMPTY candidate catalog with a
    # non-empty flight list is infeasible, not an "EMPTY objective=0" run.
    if not candidates:
        return [], {"status": "INFEASIBLE", "objective": None,
                    "wall_time_s": 0.0, "num_vars": 0,
                    "num_constraints": len(flight_numbers),
                    "uncoverable_flights": list(flight_numbers)}

    model = cp_model.CpModel()

    # Decision variables: x_p in {0,1} for each pairing
    x = [model.NewBoolVar(f"x_{p.id}") for p in candidates]

    # Cost coefficients (smaller = preferred). Multiply by big factor to
    # convert to integers (CP-SAT IP wants integer objective).
    costs = []
    for p in candidates:
        c = p.duty_minutes - DISCOUNT_PER_EXTRA_LEG * (len(p.flight_numbers) - 1)
        costs.append(int(max(1, c)))   # ensure positive

    # Objective: minimize total cost
    model.Minimize(sum(c * v for c, v in zip(costs, x)))

    # For each flight, exactly one chosen pairing must cover it
    flight_to_pairings: dict[str, List[int]] = {fn: [] for fn in flight_numbers}
    for i, p in enumerate(candidates):
        for fn in p.flight_numbers:
            if fn in flight_to_pairings:
                flight_to_pairings[fn].append(i)

    # Audit fix (2026-07-10): a flight covered by NO candidate pairing makes
    # the partitioning infeasible by definition. The previous code added a
    # free dummy boolean forced to 1 — which kept the model SATISFIABLE and
    # could report OPTIMAL while silently leaving flights uncovered.
    uncoverable = [fn for fn, idxs in flight_to_pairings.items() if not idxs]
    if uncoverable:
        return [], {"status": "INFEASIBLE", "objective": None,
                    "wall_time_s": 0.0, "num_vars": len(candidates),
                    "num_constraints": len(flight_numbers),
                    "uncoverable_flights": uncoverable}

    for fn, indices in flight_to_pairings.items():
        model.Add(sum(x[i] for i in indices) == 1)

    # Solve
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_seconds)
    solver.parameters.num_search_workers = num_workers
    solver.parameters.random_seed = 0

    status = solver.Solve(model)
    status_str = solver.StatusName(status)

    info = {
        "status": status_str,
        "objective": int(solver.ObjectiveValue()) if status in
            (cp_model.OPTIMAL, cp_model.FEASIBLE) else None,
        "wall_time_s": round(solver.WallTime(), 2),
        "num_vars": len(candidates),
        "num_constraints": len(flight_numbers),
    }

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return [], info

    selected = [candidates[i] for i in range(len(candidates))
                if solver.Value(x[i]) == 1]
    info["num_selected"] = len(selected)
    info["legs_distribution"] = _legs_dist(selected)
    return selected, info


def _legs_dist(pairings: List[Pairing]) -> dict:
    from collections import Counter
    cnt = Counter(len(p.flight_numbers) for p in pairings)
    return dict(sorted(cnt.items()))
