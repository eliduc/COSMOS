"""SolverForge-Legacy engine for PANORAMA COS.

`solverforge-legacy` is a community fork of Timefold 1.24 with the same JPype
bridge and a 100%-compatible API. We can therefore reuse the *same Pydantic
domain classes and constraint provider* we already use for Timefold-Python —
the JPype interpreter walks the same `@planning_entity` / `@planning_solution`
decorators irrespective of which package they were registered through.

In practice this engine option exists for empirical comparison: SolverForge's
Java core is a fork of Timefold's, frozen at 1.24.0, so behaviour is similar
but search trajectories may diverge slightly from Timefold-Python because of
internal RNG/move-selector tweaks made by the SolverForge community team.

For 2027 we plan to swap this out for the new Rust SolverForge core via an
HTTP sidecar (parallel to `timefold_kotlin_engine.py`); see research notes.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Dict, Optional

from .domain import FlightAssignment, FlightCrewSchedule
from .constraints import define_constraints

LOGGER = logging.getLogger(__name__)

_solver_factory = None
_solver_manager = None
_active_solvers: Dict[str, "object"] = {}  # job_id -> Solver instance, for terminate_early
_lock = threading.Lock()


def _build_factory(time_limit_s: int, soft_threshold: Optional[int]):
    """(Re)build a SolverFactory targeting the given termination config.

    Imported lazily so importing this module never starts the JVM —
    a precondition for the package to remain usable when SolverForge isn't
    available."""
    from solverforge_legacy.solver import SolverFactory
    from solverforge_legacy.solver.config import (
        Duration, ScoreDirectorFactoryConfig, SolverConfig, TerminationConfig,
    )

    termination = TerminationConfig(spent_limit=Duration(seconds=int(max(1, time_limit_s))))
    if soft_threshold is not None:
        termination.best_score_limit = f"0hard/{int(soft_threshold)}soft"

    cfg = SolverConfig(
        solution_class=FlightCrewSchedule,
        entity_class_list=[FlightAssignment],
        score_director_factory_config=ScoreDirectorFactoryConfig(
            constraint_provider_function=define_constraints,
        ),
        termination_config=termination,
        random_seed=0,
    )
    return SolverFactory.create(cfg)


def solve_with_solverforge(
    schedule: FlightCrewSchedule,
    time_limit_s: int = 30,
    soft_threshold: Optional[int] = None,
    on_improvement: Optional[Callable[[FlightCrewSchedule], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> tuple[FlightCrewSchedule, str]:
    """Solve the schedule using SolverForge-Legacy. Mirrors the Timefold-Python
    flow but routes through `solverforge_legacy.solver.SolverFactory`.

    Returns (schedule, status_text).
    """
    try:
        factory = _build_factory(time_limit_s, soft_threshold)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Failed to build SolverForge factory")
        schedule.score = "-1hard/0soft"
        return schedule, f"ERROR: {exc}"

    solver = factory.build_solver()

    # Register so terminate_early(job_id) can find us.
    job_id = _register_solver(solver)

    # SolverForge mirrors Timefold's BestSolutionChangedEvent. We poll the
    # event listener via a callback set up before solving.
    last_score: Optional[str] = None

    def _on_best(updated):
        nonlocal last_score
        score = updated.score
        score_str = str(score) if score is not None else None
        if score_str != last_score:
            last_score = score_str
            schedule.score = score_str
            # Copy assignments: shallow update is enough — the underlying employees
            # are the same instances (shared problem facts).
            for src, dst in zip(updated.flight_assignments or [], schedule.flight_assignments or []):
                dst.employee = src.employee
            if on_improvement is not None:
                try:
                    on_improvement(schedule)
                except Exception:
                    LOGGER.exception("on_improvement consumer raised")

    # SolverForge supports addEventListener pattern just like Timefold's.
    try:
        solver.add_event_listener(lambda event: _on_best(event.new_best_solution))
    except Exception:  # noqa: BLE001
        # If add_event_listener API differs, just live without live updates.
        LOGGER.warning("SolverForge add_event_listener unavailable; updates will be coarse.")

    # Stop-poll thread — SolverForge has terminate_early(); we trigger it when
    # should_stop() returns True.
    stop_flag = {"stopped": False}

    def _watch_stop():
        while not stop_flag["stopped"]:
            if should_stop is not None and should_stop():
                try:
                    solver.terminate_early()
                except Exception:
                    LOGGER.exception("solver.terminate_early raised")
                return
            time.sleep(0.5)

    watcher = threading.Thread(target=_watch_stop, name=f"sf-stop-{job_id[:8]}", daemon=True)
    watcher.start()

    try:
        final = solver.solve(schedule)
        stop_flag["stopped"] = True
        # Republish final into the schedule reference.
        schedule.score = str(final.score) if final.score is not None else schedule.score
        for src, dst in zip(final.flight_assignments or [], schedule.flight_assignments or []):
            dst.employee = src.employee
        status = "STOPPED" if (should_stop is not None and should_stop()) else "FINISHED"
        return schedule, status
    finally:
        stop_flag["stopped"] = True
        _unregister_solver(job_id)


def terminate_early(job_id: str) -> None:
    """Called by rest_api.py when DELETE /schedules/{job_id} comes in.
    SolverForge's worker watches `user_stopped` via the should_stop callback
    we passed in, but this lets us short-circuit the 0.5s polling delay."""
    with _lock:
        solver = _active_solvers.get(job_id)
    if solver is None:
        return
    try:
        solver.terminate_early()
    except Exception:
        LOGGER.exception("terminate_early on solverforge solver raised")


def _register_solver(solver) -> str:
    import uuid
    sid = str(uuid.uuid4())
    with _lock:
        _active_solvers[sid] = solver
    return sid


def _unregister_solver(sid: str) -> None:
    with _lock:
        _active_solvers.pop(sid, None)
