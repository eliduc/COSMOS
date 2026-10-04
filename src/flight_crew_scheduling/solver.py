from typing import Optional

from timefold.solver import SolverFactory, SolverManager, SolutionManager
from timefold.solver.config import (
    Duration,
    ScoreDirectorFactoryConfig,
    SolverConfig,
    TerminationConfig,
)

from .constraints import define_constraints
from .domain import FlightAssignment, FlightCrewSchedule

# Runtime-tunable settings moved to settings.py (audit 2026-07-12, round 6):
# the REST API must read/update them without importing python-timefold.
# Re-exported here so `from .solver import get_settings, update_settings`
# keeps working for existing callers.
from .settings import (  # noqa: F401
    _VALID_ENGINES,
    _settings,
    get_settings,
    on_update_callbacks,
    update_settings,
)

solver_manager: Optional[SolverManager] = None
solution_manager: Optional[SolutionManager] = None


def _build_solver_config() -> SolverConfig:
    time_limit = int(_settings["time_limit_s"] or 120)
    # We deliberately do NOT use `unimproved_spent_limit` — when the user sets
    # an explicit time budget, they want the solver to use it in full (matching
    # the OR-Tools behaviour). A plateau in Local Search is informative but not
    # a reason to stop early; the user can hit the Stop button if they're happy.
    termination = TerminationConfig(
        spent_limit=Duration(seconds=time_limit),
    )
    threshold = _settings.get("soft_threshold")
    if threshold is not None:
        # Timefold score string: "0hard/<soft>soft" - solver stops once current
        # score meets or beats this target (remember soft is negative).
        termination.best_score_limit = f"0hard/{int(threshold)}soft"

    return SolverConfig(
        solution_class=FlightCrewSchedule,
        entity_class_list=[FlightAssignment],
        score_director_factory_config=ScoreDirectorFactoryConfig(
            constraint_provider_function=define_constraints,
        ),
        termination_config=termination,
        # Deterministic search: same demo data + same seed => same trajectory.
        random_seed=0,
        # NB: move_thread_count="AUTO" requires Timefold Enterprise.
        # Community Edition uses single-threaded Local Search.
    )


def _rebuild_managers() -> None:
    global solver_manager, solution_manager
    factory = SolverFactory.create(_build_solver_config())
    solver_manager = SolverManager.create(factory)
    solution_manager = SolutionManager.create(solver_manager)


# Initial build happens eagerly so existing imports still work.
_rebuild_managers()

# Rebuild the managers after every settings update — the time limit and
# soft threshold feed into the SolverConfig termination. (Registered once
# at import; settings.update_settings invokes every registered callback.)
if _rebuild_managers not in on_update_callbacks:
    on_update_callbacks.append(_rebuild_managers)
