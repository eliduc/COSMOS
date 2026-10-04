"""Optional-import shim for `timefold` symbols used in domain.py.

The Kotlin-driven path (HTTP to Quarkus JAR) does not need a working
JPype/`timefold` Python install — the decorators and PlanningId markers
are only metadata for the Python (JPype-based) solver. When the user's
Python interpreter has no compatible `timefold` wheel (e.g. 3.14 has no
JPype build), import the stubs from here instead. The Pydantic schema
stays identical, JSON (de)serialization works, and the HTTP client to
the Kotlin solver behaves exactly the same.

If `timefold` IS installed, we re-export the real symbols.
"""
from __future__ import annotations

try:
    from timefold.solver import SolverStatus  # type: ignore[import-not-found]
    from timefold.solver.domain import (  # type: ignore[import-not-found]
        planning_entity, planning_solution, PlanningId, PlanningVariable,
        PlanningEntityCollectionProperty, ProblemFactCollectionProperty,
        ValueRangeProvider, PlanningScore,
    )
    from timefold.solver.score import HardSoftScore  # type: ignore[import-not-found]
    HAS_TIMEFOLD = True
except ImportError:
    HAS_TIMEFOLD = False

    # SolverStatus enum stub — only string values matter for JSON round-trip
    from enum import Enum as _Enum

    class SolverStatus(_Enum):  # type: ignore[no-redef]
        NOT_SOLVING = "NOT_SOLVING"
        SOLVING_SCHEDULED = "SOLVING_SCHEDULED"
        SOLVING_ACTIVE = "SOLVING_ACTIVE"

        def __str__(self) -> str:
            return self.value

    # No-op decorators — return the class unchanged
    def planning_entity(cls=None, **_kwargs):  # type: ignore[no-redef]
        if cls is None:
            return lambda c: c
        return cls

    def planning_solution(cls=None, **_kwargs):  # type: ignore[no-redef]
        if cls is None:
            return lambda c: c
        return cls

    # Marker singletons for Annotated[T, PlanningId] etc — Pydantic ignores
    # unknown metadata, so any unique sentinel works.
    class _Marker:
        def __init__(self, name: str) -> None:
            self._name = name

        def __repr__(self) -> str:
            return f"<{self._name} stub>"

    PlanningId = _Marker("PlanningId")  # type: ignore[no-redef]
    PlanningVariable = _Marker("PlanningVariable")  # type: ignore[no-redef]
    PlanningEntityCollectionProperty = _Marker(  # type: ignore[no-redef]
        "PlanningEntityCollectionProperty")
    ProblemFactCollectionProperty = _Marker(  # type: ignore[no-redef]
        "ProblemFactCollectionProperty")
    ValueRangeProvider = _Marker("ValueRangeProvider")  # type: ignore[no-redef]
    PlanningScore = _Marker("PlanningScore")  # type: ignore[no-redef]

    # HardSoftScore stub — we only care about its string repr in JSON;
    # the Kotlin engine returns scores as strings ("0hard/-891244soft")
    # and we never construct one in Python on the Kotlin path.
    class HardSoftScore:  # type: ignore[no-redef]
        def __init__(self, hard: int = 0, soft: int = 0) -> None:
            self.hard = hard
            self.soft = soft

        def __repr__(self) -> str:
            return f"{self.hard}hard/{self.soft}soft"

        def __str__(self) -> str:
            return f"{self.hard}hard/{self.soft}soft"

        @classmethod
        def __get_pydantic_core_schema__(cls, _source_type, _handler):
            """Treat HardSoftScore as a string in Pydantic models — the
            real value comes from the Kotlin solver as text anyway."""
            from pydantic_core import core_schema
            return core_schema.no_info_plain_validator_function(
                lambda v: cls.parse(v) if isinstance(v, str) else v,
                serialization=core_schema.plain_serializer_function_ser_schema(
                    lambda v: str(v) if v is not None else None,
                    return_schema=core_schema.str_schema(),
                ),
            )

        @classmethod
        def of(cls, hard: int, soft: int) -> "HardSoftScore":
            return cls(hard, soft)

        @classmethod
        def parse(cls, s: str) -> "HardSoftScore":
            """Parse strings like "0hard/-891244soft"."""
            if s is None:
                return cls(0, 0)
            txt = str(s).strip()
            try:
                hard_part, soft_part = txt.split("/")
                hard = int(hard_part.replace("hard", "").strip())
                soft = int(soft_part.replace("soft", "").strip())
                return cls(hard, soft)
            except Exception:
                return cls(0, 0)


__all__ = [
    "HAS_TIMEFOLD",
    "SolverStatus",
    "planning_entity",
    "planning_solution",
    "PlanningId",
    "PlanningVariable",
    "PlanningEntityCollectionProperty",
    "ProblemFactCollectionProperty",
    "ValueRangeProvider",
    "PlanningScore",
    "HardSoftScore",
]
