"""Alternative solver backend based on Google OR-Tools CP-SAT.

LEGACY (audit 2026-07-10): этот модуль — весенний прототип и НЕ входит в результатный тракт отчётов (final-report/*). Результаты считаются конвейером Kotlin/Quarkus + OR-Tools/SCIP/HiGHS (_gerad_*.py, _coral_*.py). Известные ограничения см. в аудите; правила здесь старые.

This variant mirrors the full Timefold constraint set (see ``constraints.py``)
as closely as a linear CP-SAT model allows:

    Hard (10)
    --------
    H1.  required_skill            — allowed-employee set per assignment
    H2.  type_rating_required      — idem
    H3.  employee_unavailability   — idem
    H4.  language_qualification    — idem
    H5.  flight_conflict           — pairwise ≤1 on overlapping flights
    H6.  minimum_rest_between_f.   — pairwise forbidden when gap < 10 h
    H7.  max_daily_duty_period     — pairwise: any 2 flights same day whose
                                     span > 14 h cannot both be taken
    H8.  max_weekly_flight_hours   — Σ minutes per (employee, ISO week) ≤ 60 h
    H9.  max_monthly_flight_hours  — Σ minutes per (employee, month)    ≤100 h

    Soft (11)
    ---------
    S1.  positioning_cost           — first flight of an employee's pairing
                                      not from home (500)
    S2.  transfer_between_two_f.    — consecutive flights with arrival ≠ next
                                      departure (800) — detected via "no one
                                      in between"
    S3.  layover_nights_away        — one penalty per (employee, arrival day)
                                      when arrival ≠ home (40)
    S4.  reserve_usage              — any assignment of a reserve crew (300)
    S5.  preferred_day_off_viol.    — assignment on employee's preferred day
                                      off, weighted by seniority (50×w×10)
    S6.  undesired_airport_viol.    — dep or arr in employee.undesired_airports
                                      (20 × w × 5)
    S7.  consecutive_duty_days      — per (employee, ISO week): count days
                                      with a duty > 6 → 500 × (n-6)
    S8.  fair_red_eye_distribution  — (max-min) of red-eye counts (×20)
    S9.  fair_weekend_distribution  — (max-min) of weekend counts (×20)
    S10. balanced_workload          — (max-min) of flight minutes (×1)

    S11. (unused — "first/last not home" was superseded by positioning_cost)

The score returned to the UI is ``0hard/-{objective}soft`` when a feasible
solution is found. Weights mirror Timefold where practical.
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

try:
    from ortools.sat.python import cp_model  # type: ignore
    ORTOOLS_AVAILABLE = True
except Exception:  # pragma: no cover
    cp_model = None  # type: ignore
    ORTOOLS_AVAILABLE = False

from .domain import Employee, Flight, FlightAssignment, FlightCrewSchedule

# -- weight constants (match Timefold where meaningful) ----------------------

MIN_REST_MINUTES = 10 * 60
MAX_DAILY_DUTY_MINUTES = 14 * 60
MAX_WEEKLY_FLIGHT_MINUTES = 60 * 60
MAX_MONTHLY_FLIGHT_MINUTES = 100 * 60
CONSECUTIVE_DUTY_DAYS_LIMIT = 6

POSITIONING_COST = 500
TRANSFER_COST = 800
LAYOVER_COST = 40
RESERVE_COST = 300
PREFERRED_OFF_BASE = 50
UNDESIRED_AIRPORT_BASE = 20
CONSECUTIVE_OVER_COST = 500
FAIR_RED_EYE_COST = 20
FAIR_WEEKEND_COST = 20
BALANCED_WORKLOAD_COST = 1


def _s(v) -> str:
    """Safe string of a JPype-wrapped or plain value."""
    return None if v is None else str(v)


def _flights_overlap(f1: Flight, f2: Flight) -> bool:
    if f1.departure_utc_date_time is None or f2.departure_utc_date_time is None:
        return False
    return (f1.departure_utc_date_time < f2.arrival_utc_date_time
            and f2.departure_utc_date_time < f1.arrival_utc_date_time)


def _rest_gap_min(earlier: Flight, later: Flight) -> int:
    return int((later.departure_utc_date_time - earlier.arrival_utc_date_time).total_seconds() / 60)


def _daily_pair_span_min(f1: Flight, f2: Flight) -> int:
    """Time span from earliest departure to latest arrival of two flights on
    the same calendar day, in minutes."""
    earliest = min(f1.departure_utc_date_time, f2.departure_utc_date_time)
    latest = max(f1.arrival_utc_date_time, f2.arrival_utc_date_time)
    return int((latest - earliest).total_seconds() / 60)


def _seniority_weight(emp: Employee) -> int:
    if emp.seniority is None:
        return 5
    raw = max(1, min(1000, int(emp.seniority)))
    return max(1, 11 - (raw - 1) // 100)


def _employee_speaks_required(emp: Employee, flight: Flight) -> bool:
    langs = flight.get_required_languages() or []
    if not langs:
        return True
    own = set(_s(l) for l in (emp.languages or []))
    return any(_s(l) in own for l in langs)


def _employee_allowed(emp: Employee, assignment: FlightAssignment) -> bool:
    flight = assignment.flight
    if flight is None:
        return False
    if not emp.has_skill(assignment.required_skill):
        return False
    if not emp.is_qualified_for(flight.aircraft_type):
        return False
    if not _employee_speaks_required(emp, flight):
        return False
    dep_date = flight.get_departure_utc_date()
    arr_date = flight.arrival_utc_date_time.date() if flight.arrival_utc_date_time else dep_date
    if not emp.is_available(dep_date, arr_date):
        return False
    return True


def solve_with_ortools(schedule: FlightCrewSchedule, time_limit_s: int = 30,
                       on_improvement=None, should_stop=None) -> Tuple[FlightCrewSchedule, str]:
    """
    Args:
        schedule:        FlightCrewSchedule to mutate in-place.
        time_limit_s:    upper bound on solver runtime; CP-SAT can return
                         earlier if it proves OPTIMAL or INFEASIBLE.
        on_improvement:  optional callback(updated_schedule) invoked on every
                         new best feasible solution. Same role as Timefold's
                         `with_best_solution_consumer`.
        should_stop:     optional callable () -> bool; when it returns True,
                         CP-SAT terminates the search early.

    Wraps `_solve_with_ortools_impl` in a watchdog thread that aborts the run
    if total wall-clock exceeds `time_limit_s × 3 + 30s`. CP-SAT's own
    `max_time_in_seconds` only governs the search phase — model construction
    can run for tens of minutes on big instances and is otherwise
    uninterruptible. The watchdog is the safety net.
    """
    if not ORTOOLS_AVAILABLE:
        raise RuntimeError("ortools not installed in this environment")

    import threading
    deadline_s = max(60, time_limit_s * 3 + 30)
    result_holder: dict = {"schedule": schedule, "status": "ABORTED"}
    error_holder: dict = {"exc": None}

    def _runner():
        try:
            sched, status = _solve_with_ortools_impl(
                schedule, time_limit_s,
                on_improvement=on_improvement,
                should_stop=should_stop,
            )
            result_holder["schedule"] = sched
            result_holder["status"] = status
        except Exception as exc:  # noqa: BLE001
            import sys, traceback
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            error_holder["exc"] = exc

    t = threading.Thread(target=_runner, name="ortools-solve", daemon=True)
    t.start()
    t.join(deadline_s)

    if t.is_alive():
        # Watchdog tripped — most likely model build is dominating.
        # We can't kill the thread (Python doesn't allow), but we can return
        # an error sentinel so the benchmark moves on. The thread keeps
        # consuming CPU in the background until it eventually finishes; the
        # daemon flag will let the interpreter exit anyway.
        schedule.score = "-9999hard/0soft"
        return schedule, f"BUILD_TIMEOUT (>{deadline_s}s)"

    if error_holder["exc"] is not None:
        schedule.score = "-1hard/0soft"
        return schedule, f"ERROR: {error_holder['exc']}"

    return result_holder["schedule"], result_holder["status"]


def _solve_with_ortools_impl(schedule: FlightCrewSchedule, time_limit_s: int,
                             on_improvement=None, should_stop=None) -> Tuple[FlightCrewSchedule, str]:
    employees: List[Employee] = list(schedule.employees or [])
    assignments: List[FlightAssignment] = list(schedule.flight_assignments or [])
    num_e = len(employees)
    num_a = len(assignments)

    allowed_per_a: List[List[int]] = [
        [ei for ei, emp in enumerate(employees) if _employee_allowed(emp, a)]
        for a in assignments
    ]
    # Audit fix (2026-07-12, P0): an assignment with an EMPTY eligible set
    # used to silently drop out of the model, and a schedule that cannot be
    # staffed came back as OPTIMAL 0hard. Fail fast instead.
    uncoverable = [a for a, allowed in zip(assignments, allowed_per_a)
                   if not allowed]
    if uncoverable:
        ids = [getattr(a, "id", "?") for a in uncoverable[:10]]
        schedule.score = f"-{len(uncoverable)}hard/0soft"
        return schedule, (f"INFEASIBLE_NO_ALLOWED_EMPLOYEE "
                          f"({len(uncoverable)} assignments, e.g. {ids})")
    # Symmetrical lookup
    allowed_per_e: List[List[int]] = [[] for _ in range(num_e)]
    for ai, es in enumerate(allowed_per_a):
        for ei in es:
            allowed_per_e[ei].append(ai)

    model = cp_model.CpModel()

    # x[a, e] — decision vars
    x: Dict[Tuple[int, int], cp_model.IntVar] = {}
    for ai in range(num_a):
        for ei in allowed_per_a[ai]:
            x[(ai, ei)] = model.NewBoolVar(f"x_{ai}_{ei}")
        if allowed_per_a[ai]:
            model.Add(sum(x[(ai, ei)] for ei in allowed_per_a[ai]) == 1)

    # ---------- H5. flight_conflict & H6. min_rest & H7. max_daily_duty ----
    # Everything that's pairwise per employee gets emitted in one nested loop.
    for ei in range(num_e):
        cands = sorted(
            allowed_per_e[ei],
            key=lambda ai: assignments[ai].flight.departure_utc_date_time,
        )
        for i in range(len(cands)):
            a1 = assignments[cands[i]]
            for j in range(i + 1, len(cands)):
                a2 = assignments[cands[j]]
                f1, f2 = a1.flight, a2.flight

                # H5 overlap
                if _flights_overlap(f1, f2):
                    model.Add(x[(cands[i], ei)] + x[(cands[j], ei)] <= 1)
                    continue  # already forbidden, no need for more

                # H6 min rest — f1 is earlier by sort
                gap = _rest_gap_min(f1, f2)
                if 0 <= gap < MIN_REST_MINUTES:
                    model.Add(x[(cands[i], ei)] + x[(cands[j], ei)] <= 1)
                    continue

                # H7 daily duty: same calendar day, pair span > 14h
                if f1.departure_utc_date_time.date() == f2.departure_utc_date_time.date():
                    if _daily_pair_span_min(f1, f2) > MAX_DAILY_DUTY_MINUTES:
                        model.Add(x[(cands[i], ei)] + x[(cands[j], ei)] <= 1)

    # ---------- H8. max weekly flight hours ---------------------------------
    # ---------- H9. max monthly flight hours --------------------------------
    for ei, cands in enumerate(allowed_per_e):
        buckets_week: Dict[Tuple[int, int], List[int]] = defaultdict(list)
        buckets_month: Dict[Tuple[int, int], List[int]] = defaultdict(list)
        for ai in cands:
            f = assignments[ai].flight
            iso = f.departure_utc_date_time.isocalendar()
            buckets_week[(iso[0], iso[1])].append(ai)
            ym = (f.departure_utc_date_time.year, f.departure_utc_date_time.month)
            buckets_month[ym].append(ai)
        for week_ais in buckets_week.values():
            model.Add(
                sum(assignments[ai].flight.get_duration_minutes() * x[(ai, ei)]
                    for ai in week_ais) <= MAX_WEEKLY_FLIGHT_MINUTES)
        for month_ais in buckets_month.values():
            model.Add(
                sum(assignments[ai].flight.get_duration_minutes() * x[(ai, ei)]
                    for ai in month_ais) <= MAX_MONTHLY_FLIGHT_MINUTES)

    # ======================================================================
    #                          SOFT PART
    # ======================================================================
    soft_terms = []

    # ---------- S4. reserve_usage -----------------------------------------
    for ai in range(num_a):
        for ei in allowed_per_a[ai]:
            if employees[ei].is_reserve:
                soft_terms.append(RESERVE_COST * x[(ai, ei)])

    # ---------- S5. preferred day off & S6. undesired airports -----------
    for ai in range(num_a):
        flight = assignments[ai].flight
        dep_date = flight.get_departure_utc_date()
        dep_ap = _s(flight.departure_airport)
        arr_ap = _s(flight.arrival_airport)
        for ei in allowed_per_a[ai]:
            emp = employees[ei]
            w = _seniority_weight(emp)
            if emp.preferred_days_off and dep_date in emp.preferred_days_off:
                soft_terms.append(PREFERRED_OFF_BASE * w * 10 * x[(ai, ei)])
            undesired = set(emp.undesired_airports or [])
            if undesired and (dep_ap in undesired or arr_ap in undesired):
                soft_terms.append(UNDESIRED_AIRPORT_BASE * w * 5 * x[(ai, ei)])

    # ---------- S1. positioning cost (first assignment of pairing) ----------
    # is_first[ai, ei] = x[ai, ei] AND no earlier assignment of same employee
    for ei in range(num_e):
        cands = sorted(
            allowed_per_e[ei],
            key=lambda ai: assignments[ai].flight.departure_utc_date_time,
        )
        earlier_so_far: List[int] = []
        emp = employees[ei]
        home = _s(emp.home_airport)
        for ai in cands:
            is_first = model.NewBoolVar(f"first_{ai}_{ei}")
            model.Add(is_first <= x[(ai, ei)])
            for aj in earlier_so_far:
                model.Add(is_first <= 1 - x[(aj, ei)])
            # tight lower bound to force is_first=1 when x[ai]=1 and no earlier assigned
            if earlier_so_far:
                model.Add(is_first >= x[(ai, ei)]
                          - sum(x[(aj, ei)] for aj in earlier_so_far))
            else:
                model.Add(is_first >= x[(ai, ei)])
            # penalise when first flight doesn't depart from home
            dep_ap = _s(assignments[ai].flight.departure_airport)
            if home is not None and dep_ap != home:
                soft_terms.append(POSITIONING_COST * is_first)
            earlier_so_far.append(ai)

    # ---------- S2. transfer_between_two_flights ---------------------------
    # Penalise when two chronologically adjacent flights of the same employee
    # mismatch (flight1.arr ≠ flight2.dep). "Adjacent" means no other flight
    # of this employee is scheduled between them.
    for ei in range(num_e):
        cands = sorted(
            allowed_per_e[ei],
            key=lambda ai: assignments[ai].flight.departure_utc_date_time,
        )
        for i in range(len(cands)):
            ai = cands[i]
            f1 = assignments[ai].flight
            for j in range(i + 1, len(cands)):
                aj = cands[j]
                f2 = assignments[aj].flight
                if _s(f1.arrival_airport) == _s(f2.departure_airport):
                    continue
                between = cands[i + 1:j]
                pair = model.NewBoolVar(f"trn_{ai}_{aj}_{ei}")
                # pair => x_i AND x_j
                model.Add(pair <= x[(ai, ei)])
                model.Add(pair <= x[(aj, ei)])
                # pair => every in-between is 0
                for ak in between:
                    model.Add(pair <= 1 - x[(ak, ei)])
                # tight lower bound to force pair=1 in the matching case
                # pair ≥ x_i + x_j - 1 - Σ x_between
                if between:
                    model.Add(pair >= x[(ai, ei)] + x[(aj, ei)] - 1
                              - sum(x[(ak, ei)] for ak in between))
                else:
                    model.Add(pair >= x[(ai, ei)] + x[(aj, ei)] - 1)
                soft_terms.append(TRANSFER_COST * pair)

    # ---------- S3. layover nights away from home --------------------------
    # Per (employee, arrival date): away=1 if ≥1 flight lands at non-home
    for ei in range(num_e):
        emp = employees[ei]
        home = _s(emp.home_airport)
        per_day: Dict[date, List[int]] = defaultdict(list)
        for ai in allowed_per_e[ei]:
            f = assignments[ai].flight
            if _s(f.arrival_airport) != home:
                per_day[f.arrival_utc_date_time.date()].append(ai)
        for d, ais in per_day.items():
            away = model.NewBoolVar(f"away_{ei}_{d.isoformat()}")
            # away ≥ max x_a (OR)
            for ai in ais:
                model.Add(away >= x[(ai, ei)])
            # away ≤ Σ x_a (tight upper)
            model.Add(away <= sum(x[(ai, ei)] for ai in ais))
            soft_terms.append(LAYOVER_COST * away)

    # ---------- S7. consecutive_duty_days ----------------------------------
    # For each (employee, ISO week): duty_days = Σ day_has_flight; over = max(0, duty_days - 6)
    for ei in range(num_e):
        weeks: Dict[Tuple[int, int], Dict[date, List[int]]] = defaultdict(lambda: defaultdict(list))
        for ai in allowed_per_e[ei]:
            f = assignments[ai].flight
            iso = f.departure_utc_date_time.isocalendar()
            weeks[(iso[0], iso[1])][f.departure_utc_date_time.date()].append(ai)
        for (y, w), days_map in weeks.items():
            day_bools = []
            for d, ais in days_map.items():
                dv = model.NewBoolVar(f"dd_{ei}_{y}_{w}_{d.isoformat()}")
                for ai in ais:
                    model.Add(dv >= x[(ai, ei)])
                model.Add(dv <= sum(x[(ai, ei)] for ai in ais))
                day_bools.append(dv)
            if not day_bools:
                continue
            over = model.NewIntVar(0, len(day_bools), f"over_{ei}_{y}_{w}")
            model.Add(over >= sum(day_bools) - CONSECUTIVE_DUTY_DAYS_LIMIT)
            soft_terms.append(CONSECUTIVE_OVER_COST * over)

    # ---------- S8-S9. fair distributions (range-based approximation) -----
    # Count red-eye and weekend flights per employee; minimise range (max-min)
    def _add_range_fairness(predicate, label: str, weight: int):
        per_emp_counts = []
        for ei in range(num_e):
            matching = [ai for ai in allowed_per_e[ei] if predicate(assignments[ai].flight)]
            if not matching:
                continue
            cnt = model.NewIntVar(0, len(matching), f"{label}_cnt_{ei}")
            model.Add(cnt == sum(x[(ai, ei)] for ai in matching))
            per_emp_counts.append(cnt)
        if len(per_emp_counts) < 2:
            return
        hi = model.NewIntVar(0, len(assignments), f"{label}_hi")
        lo = model.NewIntVar(0, len(assignments), f"{label}_lo")
        for cnt in per_emp_counts:
            model.Add(hi >= cnt)
            model.Add(lo <= cnt)
        span = model.NewIntVar(0, len(assignments), f"{label}_span")
        model.Add(span == hi - lo)
        soft_terms.append(weight * span)

    _add_range_fairness(lambda f: f.is_red_eye(), "redeye", FAIR_RED_EYE_COST)
    _add_range_fairness(lambda f: f.is_weekend_flight(), "weekend", FAIR_WEEKEND_COST)

    # ---------- S10. balanced workload (total flight minutes per employee) -
    # Hard cap per pilot in minutes; matches weekly / monthly hard rules.
    total_max_minutes = MAX_MONTHLY_FLIGHT_MINUTES
    per_emp_mins = []
    for ei in range(num_e):
        matching = allowed_per_e[ei]
        if not matching:
            continue
        mins = model.NewIntVar(0, total_max_minutes, f"mins_{ei}")
        model.Add(mins == sum(assignments[ai].flight.get_duration_minutes() * x[(ai, ei)]
                              for ai in matching))
        per_emp_mins.append(mins)
    if len(per_emp_mins) >= 2:
        hi = model.NewIntVar(0, total_max_minutes, "mins_hi")
        lo = model.NewIntVar(0, total_max_minutes, "mins_lo")
        for m in per_emp_mins:
            model.Add(hi >= m)
            model.Add(lo <= m)
        span = model.NewIntVar(0, total_max_minutes, "mins_span")
        model.Add(span == hi - lo)
        soft_terms.append(BALANCED_WORKLOAD_COST * span)

    # ---------- objective --------------------------------------------------
    if soft_terms:
        model.Minimize(sum(soft_terms))

    # ---------- solve ------------------------------------------------------
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(max(1, time_limit_s))
    # Allow overriding worker count via env var for benchmarking experiments
    # (e.g., comparing 8 vs 12 vs 16 workers on the same hardware).
    _override_workers = int(os.environ.get("ORTOOLS_NUM_WORKERS", "0") or "0")
    # 8 parallel workers — multi-strategy CP-SAT search. This is non-deterministic
    # across runs (workers race), but on this problem size 8x throughput beats
    # single-threaded determinism by a wide margin.
    solver.parameters.num_search_workers = _override_workers if _override_workers > 0 else 8
    solver.parameters.random_seed = 0

    has_objective = bool(soft_terms)
    emp_by_id = {e.id: e for e in employees}

    # SWIG quirk: CpSolverSolutionCallback needs the explicit unbound __init__
    # call rather than `super().__init__()`. Defined inline so we don't have
    # to thread state through a constructor — the closure handles it.
    class _ImprovementListener(cp_model.CpSolverSolutionCallback):
        def __init__(self):
            cp_model.CpSolverSolutionCallback.__init__(self)
            self.solutions_found = 0
            self.best_objective = None  # for multi-worker race filtering

        def on_solution_callback(self):  # noqa: N802
            self.solutions_found += 1
            obj = int(self.ObjectiveValue()) if has_objective else 0
            # In multi-worker mode CP-SAT may occasionally publish a worker's
            # local incumbent that's globally worse. Filter those out so the
            # UI sees a strictly monotone improvement stream.
            if self.best_objective is not None and obj > self.best_objective:
                return
            self.best_objective = obj
            # Materialise the current incumbent into the schedule
            for ai, a in enumerate(assignments):
                a.employee = None
                for ei in allowed_per_a[ai]:
                    if self.Value(x[(ai, ei)]) == 1:
                        a.employee = emp_by_id.get(employees[ei].id)
                        break
            schedule.score = f"0hard/-{obj}soft"
            if on_improvement is not None:
                try:
                    on_improvement(schedule)
                except Exception:
                    pass
            # External stop signal — triggers immediate return from solver
            if should_stop is not None and should_stop():
                self.StopSearch()

    listener = _ImprovementListener()
    # OR-Tools >=9.8 removed SolveWithSolutionCallback in favour of Solve(model, callback).
    status = solver.Solve(model, listener)

    status_map = {
        cp_model.OPTIMAL: "OPTIMAL",
        cp_model.FEASIBLE: "FEASIBLE",
        cp_model.INFEASIBLE: "INFEASIBLE",
        cp_model.MODEL_INVALID: "MODEL_INVALID",
        cp_model.UNKNOWN: "UNKNOWN",
    }
    status_text = status_map.get(status, str(status))

    # Post-solve: always re-pull the BEST solution from the solver when the
    # status is FEASIBLE/OPTIMAL — `solver.ObjectiveValue()` is the global
    # best across all workers, while the streaming listener may have ended on
    # a worker's slightly worse incumbent. If the solver doesn't claim a
    # feasible solution at all, mark every assignment as unassigned.
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for ai, a in enumerate(assignments):
            a.employee = None
            for ei in allowed_per_a[ai]:
                if solver.Value(x[(ai, ei)]) == 1:
                    a.employee = emp_by_id.get(employees[ei].id)
                    break
        soft_val = int(solver.ObjectiveValue()) if has_objective else 0
        schedule.score = f"0hard/-{soft_val}soft"
    elif listener.solutions_found > 0:
        # Solver returned UNKNOWN (e.g. user-stopped) but the listener wrote
        # at least one feasible solution — keep what it last published.
        pass
    else:
        # No feasible solution at all — surface as hard violations
        for a in assignments:
            a.employee = None
        unassigned = len(assignments)
        schedule.score = f"-{unassigned}hard/0soft"

    return schedule, status_text
