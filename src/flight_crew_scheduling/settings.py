"""Runtime-tunable solver settings shared by the REST API and the engines.

Extracted from solver.py (audit 2026-07-12, round 6, P5): the REST service
must be able to read AND update settings without importing python-timefold,
which is an optional extra that does not install on Python 3.14. Before
this split, the stub fallback in rest_api.py silently ignored updates, so
POST /settings {"engine": "ortools"} returned ok while the engine stayed
unchanged. solver.py re-exports these functions and registers its
manager-rebuild hook in ``on_update_callbacks``.
"""
from typing import Callable, List, Optional

# Valid engine identifiers. "timefold" remains an alias for "timefold_python"
# so existing settings persisted in the UI keep working.
_VALID_ENGINES = ("timefold", "timefold_python", "ortools",
                  "timefold_kotlin", "timefold_kotlin_comprehensive",
                  "solverforge")

_settings: dict = {
    "time_limit_s": 120,
    "soft_threshold": None,   # if set, solver stops as soon as soft >= threshold
    "engine": "timefold",     # "timefold[_python]" | "ortools" | "timefold_kotlin" | "solverforge"
}

# Callbacks invoked after every update_settings() call. When python-timefold
# is installed, solver.py registers its _rebuild_managers here so the
# in-process SolverManager picks up the new time limit / soft threshold.
on_update_callbacks: List[Callable[[], None]] = []


def get_settings() -> dict:
    return dict(_settings)


def update_settings(time_limit_s: Optional[int] = None,
                    soft_threshold: Optional[int] = None,
                    engine: Optional[str] = None) -> dict:
    if time_limit_s is not None:
        _settings["time_limit_s"] = int(time_limit_s)
    _settings["soft_threshold"] = int(soft_threshold) if soft_threshold is not None else None
    if engine in _VALID_ENGINES:
        # Normalise legacy "timefold" alias.
        _settings["engine"] = "timefold_python" if engine == "timefold" else engine
    for callback in on_update_callbacks:
        callback()
    return dict(_settings)
