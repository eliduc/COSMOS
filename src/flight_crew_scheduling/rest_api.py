"""LEGACY (audit 2026-07-10): этот модуль — весенний прототип и НЕ входит в результатный тракт отчётов (final-report/*). Результаты считаются конвейером Kotlin/Quarkus + OR-Tools/SCIP/HiGHS (_gerad_*.py, _coral_*.py). Известные ограничения см. в аудите; правила здесь старые."""
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import Annotated, Final, List, Optional
from uuid import uuid4
from datetime import datetime
from .domain import *
from .score_analysis import *
import threading
from pydantic import BaseModel as _BaseModel
from .demo_data import generate_demo_data
# Audit fix (2026-07-12, round 6, P5): settings live in the timefold-free
# settings module, so GET/POST /settings work (and actually persist!) even
# when python-timefold is absent. The old stub fallback silently ignored
# updates, so the user could never switch the engine to OR-Tools.
from .settings import get_settings, update_settings
# Audit fix (2026-07-12, round 5): the legacy Python-Timefold engine is an
# optional extra and does not install on Python 3.14 — the REST service
# must still start (Kotlin/OR-Tools engines are the default pipeline).
# Endpoints that require Python-Timefold return 501 when it is absent.
try:
    from . import solver as _solver_module
    PYTHON_TIMEFOLD_AVAILABLE = True
except ModuleNotFoundError:
    _solver_module = None
    PYTHON_TIMEFOLD_AVAILABLE = False

from . import ortools_engine as _ortools_engine
from . import timefold_kotlin_engine as _timefold_kotlin_engine
try:
    from . import solverforge_engine as _solverforge_engine
except ModuleNotFoundError:
    _solverforge_engine = None
from . import benchmark as _benchmark


def _require_python_timefold():
    if not PYTHON_TIMEFOLD_AVAILABLE:
        raise HTTPException(
            status_code=501,
            detail="python-timefold engine is not installed (optional "
                   "extra 'timefold-legacy'); use the Kotlin or OR-Tools "
                   "engine")

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

app = FastAPI(docs_url='/q/swagger-ui')


@app.middleware("http")
async def _disable_static_cache(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith((".js", ".css", ".html", ".svg")):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

MAX_JOBS_CACHE_SIZE: Final[int] = 2
data_sets: Dict[str, dict] = {}


@app.get("/demo-data")
async def get_demo_data():
    return generate_demo_data()


async def setup_context(request: Request) -> FlightCrewSchedule:
    json = await request.json()

    airports_dict = {
        airport['code']: Airport.model_validate(airport) for airport in json.get('airports', [])
    }

    # Preprocess flights to replace airport codes with Airport objects
    for flight in json.get('flights', []):
        if isinstance(flight.get('departureAirport'), str):
            flight['departureAirport'] = airports_dict.get(flight['departureAirport'], None)
        if isinstance(flight.get('arrivalAirport'), str):
            flight['arrivalAirport'] = airports_dict.get(flight['arrivalAirport'], None)

    # Preprocess employees to replace airport codes with Airport objects
    for employee in json.get('employees', []):
        if isinstance(employee.get('homeAirport'), str):
            airport_code = employee['homeAirport']
            employee['homeAirport'] = airports_dict.get(airport_code, None)

    return FlightCrewSchedule.model_validate(json,
                                         context={
                                             'airports': airports_dict,
                                             'employees': {
                                                 employee['id']: Employee.model_validate(employee) for
                                                 employee in json.get('employees', [])
                                             },
                                             'flights': {
                                                 flight['flightNumber']: Flight.model_validate(flight) for
                                                 flight in json.get('flights', [])
                                             },
                                         })


def clean_jobs():
    """
    The method retains only the records of the last MAX_JOBS_CACHE_SIZE completed jobs by removing the oldest ones.
    """
    global data_sets
    if len(data_sets) <= MAX_JOBS_CACHE_SIZE:
        return

    # Audit fix (2026-07-12, round 5): a job has a schedule from the moment
    # it is created — "has schedule" is NOT "completed". Only jobs with a
    # final_at stamp may be evicted, so an active solve can no longer be
    # deleted by two newer submissions.
    completed_jobs = [
        (job_id, job_data)
        for job_id, job_data in data_sets.items()
        if job_data.get("final_at") is not None
    ]

    completed_jobs.sort(key=lambda job: job[1]["created_at"])

    for job_id, _ in completed_jobs[:len(completed_jobs) - MAX_JOBS_CACHE_SIZE]:
        del data_sets[job_id]


def update_flight_crew_schedule(problem_id: str, flight_crew_schedule: FlightCrewSchedule):
    global data_sets
    entry = data_sets.get(problem_id)
    if entry is not None:
        entry["schedule"] = flight_crew_schedule


def mark_final_schedule(problem_id: str, flight_crew_schedule: FlightCrewSchedule):
    global data_sets
    entry = data_sets.get(problem_id)
    if entry is None:
        return
    entry["schedule"] = flight_crew_schedule
    entry["final_at"] = datetime.now()


import re as _re


def _score_to_pair(score) -> tuple[int | None, int | None]:
    if score is None:
        return None, None
    m = _re.match(r"(-?\d+)hard/(-?\d+)soft", str(score))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _compute_summary(entry: dict) -> dict | None:
    final_at = entry.get("final_at")
    if final_at is None:
        return None
    started_at = entry.get("started_at", entry.get("created_at"))
    elapsed = (final_at - started_at).total_seconds()
    schedule: FlightCrewSchedule = entry["schedule"]
    hard, soft = _score_to_pair(schedule.score)

    reason = "optimum"
    if entry.get("user_stopped"):
        reason = "manual"
    else:
        threshold = entry.get("soft_threshold")
        time_limit = entry.get("time_limit_s") or 0
        if threshold is not None and soft is not None and soft >= threshold:
            reason = "threshold"
        elif elapsed + 0.8 >= time_limit:
            reason = "timeout"

    assignments = schedule.flight_assignments or []
    assigned = [a for a in assignments if a.employee is not None]
    unique_emp = {a.employee.id for a in assigned}

    summary = {
        "finishReason": reason,
        "elapsedSeconds": round(elapsed, 1),
        "timeLimitSeconds": entry.get("time_limit_s"),
        "softThreshold": entry.get("soft_threshold"),
        "hard": hard,
        "soft": soft,
        "assignedCount": len(assigned),
        "totalAssignments": len(assignments),
        "employeesInvolved": len(unique_emp),
    }
    # Audit fix (2026-07-12, round 6): a failed run must not look like a
    # successful one — engines report transport/solve failures either via a
    # raised exception or an "ERROR: ..." status text; both end up in
    # entry["exception"] and are surfaced to the UI here.
    if entry.get("exception"):
        summary["solveState"] = "ERROR"
        summary["finishReason"] = "error"
    return summary


def _make_callbacks(job_id: str):
    """Shared on_improvement / should_stop closures for engines that don't
    drive Timefold's solver_manager directly (OR-Tools, Timefold-Kotlin, SolverForge).
    """
    def _on_improvement(updated):
        update_flight_crew_schedule(job_id, updated)

    def _should_stop():
        entry = data_sets.get(job_id)
        return bool(entry and entry.get("user_stopped"))

    return _on_improvement, _should_stop


def _run_ortools_job(job_id: str, schedule: FlightCrewSchedule, time_limit_s: int) -> None:
    """Background worker that drives the CP-SAT solver and streams every
    improvement back into ``data_sets[job_id]`` so the UI polling can see
    the score drop in real time."""
    on_improvement, should_stop = _make_callbacks(job_id)
    try:
        solved, status_text = _ortools_engine.solve_with_ortools(
            schedule, time_limit_s,
            on_improvement=on_improvement,
            should_stop=should_stop,
        )
        entry = data_sets.get(job_id)
        if entry is None:
            return
        entry["schedule"] = solved
        entry["ortools_status"] = status_text
        # Audit fix (2026-07-12, round 6): the engine reports failures as an
        # "ERROR: ..." status text without raising — record it so the summary
        # shows solveState ERROR instead of pretending success.
        if status_text.startswith("ERROR"):
            entry["exception"] = status_text
        mark_final_schedule(job_id, solved)
    except Exception as exc:  # noqa: BLE001
        entry = data_sets.get(job_id)
        if entry is not None:
            entry["exception"] = str(exc)
            entry["final_at"] = datetime.now()
        raise


def _run_timefold_kotlin_job(job_id: str, schedule: FlightCrewSchedule,
                              time_limit_s: int, soft_threshold: Optional[int],
                              comprehensive: bool = False) -> None:
    """Background worker that proxies to the Kotlin Quarkus service on :9091.

    If ``comprehensive=True`` the Kotlin side runs four acceptors in parallel
    (Late Acceptance, Simulated Annealing, Tabu Search, Great Deluge) and
    reports the global best. Wall-clock budget is unchanged.
    """
    on_improvement, should_stop = _make_callbacks(job_id)
    try:
        solved, status_text = _timefold_kotlin_engine.solve_with_timefold_kotlin(
            schedule, time_limit_s,
            soft_threshold=soft_threshold,
            on_improvement=on_improvement,
            should_stop=should_stop,
            comprehensive=comprehensive,
            phase_a_fraction=1.0,
        )
        entry = data_sets.get(job_id)
        if entry is None:
            return
        entry["schedule"] = solved
        entry["engine_status"] = status_text
        # Audit fix (2026-07-12, round 6): a transport failure to the Kotlin
        # service comes back as an "ERROR: ..." status text, not an exception
        # — record it so the summary shows solveState ERROR instead of
        # pretending success.
        if status_text.startswith("ERROR"):
            entry["exception"] = status_text
        mark_final_schedule(job_id, solved)
    except Exception as exc:  # noqa: BLE001
        entry = data_sets.get(job_id)
        if entry is not None:
            entry["exception"] = str(exc)
            entry["final_at"] = datetime.now()
        raise


def _run_solverforge_job(job_id: str, schedule: FlightCrewSchedule,
                          time_limit_s: int, soft_threshold: Optional[int]) -> None:
    """Background worker that drives solverforge-legacy (JPype clone of
    Timefold 1.24)."""
    on_improvement, should_stop = _make_callbacks(job_id)
    try:
        if _solverforge_engine is None:
            raise HTTPException(status_code=501, detail="solverforge engine requires the optional python-timefold stack")
        solved, status_text = _solverforge_engine.solve_with_solverforge(
            schedule, time_limit_s,
            soft_threshold=soft_threshold,
            on_improvement=on_improvement,
            should_stop=should_stop,
        )
        entry = data_sets.get(job_id)
        if entry is None:
            return
        entry["schedule"] = solved
        entry["engine_status"] = status_text
        # Audit fix (2026-07-12, round 6): same "ERROR: ..." status-text
        # convention as the OR-Tools and Kotlin runners above.
        if status_text.startswith("ERROR"):
            entry["exception"] = status_text
        mark_final_schedule(job_id, solved)
    except Exception as exc:  # noqa: BLE001
        entry = data_sets.get(job_id)
        if entry is not None:
            entry["exception"] = str(exc)
            entry["final_at"] = datetime.now()
        raise


@app.post("/schedules")
async def solve_schedule(flight_crew_schedule: Annotated[FlightCrewSchedule, Depends(setup_context)]) -> str:
    job_id = str(uuid4())
    settings = get_settings()
    now = datetime.now()
    engine = settings.get("engine") or "timefold"
    data_sets[job_id] = {
        "schedule": flight_crew_schedule,
        "created_at": now,
        "started_at": now,
        "final_at": None,
        "time_limit_s": settings.get("time_limit_s"),
        "soft_threshold": settings.get("soft_threshold"),
        "engine": engine,
        "user_stopped": False,
        "exception": None,
    }

    time_limit = int(settings.get("time_limit_s") or 30)
    soft_threshold = settings.get("soft_threshold")

    if engine == "ortools":
        # CP-SAT runs in its own thread and streams improvements through the
        # solution callback we wired up in `ortools_engine`.
        threading.Thread(
            target=_run_ortools_job,
            args=(job_id, flight_crew_schedule, time_limit),
            name=f"ortools-{job_id[:8]}", daemon=True,
        ).start()
    elif engine in ("timefold_kotlin", "timefold_kotlin_comprehensive"):
        # The Kotlin Quarkus service on :9091 does the actual solving; this
        # thread just polls and republishes incumbents into our cache. The
        # comprehensive variant flips a flag in the proxy that selects the
        # /schedules/comprehensive endpoint on the server.
        comprehensive = (engine == "timefold_kotlin_comprehensive")
        threading.Thread(
            target=_run_timefold_kotlin_job,
            args=(job_id, flight_crew_schedule, time_limit, soft_threshold, comprehensive),
            name=f"timefold-kotlin-{job_id[:8]}", daemon=True,
        ).start()
    elif engine == "solverforge":
        # solverforge-legacy is a JPype clone of Timefold 1.24, so we run
        # it via the same in-process thread pattern as Timefold-Python.
        threading.Thread(
            target=_run_solverforge_job,
            args=(job_id, flight_crew_schedule, time_limit, soft_threshold),
            name=f"solverforge-{job_id[:8]}", daemon=True,
        ).start()
    else:
        # Default: in-process Timefold-Python (engine == "timefold" or
        # "timefold_python"). Streams best-solution updates as the solver
        # searches.
        _require_python_timefold()
        (_solver_module.solver_manager.solve_builder()
            .with_problem_id(job_id)
            .with_problem(flight_crew_schedule)
            .with_best_solution_consumer(lambda s: update_flight_crew_schedule(job_id, s))
            .with_final_best_solution_consumer(lambda s: mark_final_schedule(job_id, s))
            .run())

    clean_jobs()
    return job_id


def _derive_status(entry: dict, problem_id: str) -> SolverStatus:
    """Single source of truth for a job's solver status (audit 2026-07-12,
    round 6): GET /schedules/{id} and GET /schedules/{id}/status must agree.
    External engines (ortools, timefold_kotlin*, solverforge) don't drive
    the in-process Timefold solver_manager, so we synthesize SOLVING_ACTIVE
    until the worker thread sets `final_at`."""
    engine = entry.get("engine", "timefold")
    if engine in ("ortools", "timefold_kotlin", "timefold_kotlin_comprehensive",
                   "solverforge"):
        return SolverStatus.NOT_SOLVING if entry.get("final_at") is not None \
            else SolverStatus.SOLVING_ACTIVE
    _require_python_timefold()
    return _solver_module.solver_manager.get_solver_status(problem_id)


@app.get("/schedules/{problem_id}")
async def get_flight_crew_schedule(problem_id: str):
    # Audit fix 2026-07-10: unknown job id -> HTTP 404, not KeyError/500.
    if problem_id not in data_sets:
        raise HTTPException(status_code=404,
                            detail=f"unknown schedule id: {problem_id}")
    entry = data_sets[problem_id]
    schedule: FlightCrewSchedule = entry["schedule"]
    engine = entry.get("engine", "timefold")
    status = _derive_status(entry, problem_id)
    body = schedule.model_copy(update={"solver_status": status}).model_dump(by_alias=True)
    summary = _compute_summary(entry)
    if summary is not None:
        summary["engine"] = engine
        engine_status = entry.get("engine_status") or entry.get("ortools_status")
        if engine_status:
            summary["engineStatus"] = engine_status
        body["summary"] = summary
    return body


@app.get("/schedules/{job_id}/status")
async def get_schedule_status(job_id: str) -> dict:
    # Audit fix (2026-07-12): unknown job must be 404, not KeyError/500.
    if job_id not in data_sets:
        raise HTTPException(status_code=404,
                            detail=f"No schedule found with id {job_id}")
    # Audit fix (2026-07-12, round 6): use the same status derivation as
    # GET /schedules/{id}. The stored schedule.solver_status is never
    # updated by the external-engine workers, so it always read NOT_SOLVING
    # here even while GET /schedules/{id} reported SOLVING_ACTIVE.
    return {"solver_status": _derive_status(data_sets[job_id], job_id)}


@app.put("/schedules/analyze")
async def analyze_timetable(flight_crew_schedule: Annotated[FlightCrewSchedule, Depends(setup_context)]) -> dict:
    return {'constraints': [ConstraintAnalysisDTO(
        name=constraint.constraint_name,
        weight=constraint.weight,
        score=constraint.score,
        matches=[
            MatchAnalysisDTO(
                name=match.constraint_ref.constraint_name,
                score=match.score,
                justification=str(match.justification) if match.justification is not None else None,
            )
            for match in constraint.matches
        ]
    ) for constraint in (_require_python_timefold() or _solver_module.solution_manager.analyze(flight_crew_schedule).constraint_analyses)]}


@app.delete("/schedules/{problem_id}")
async def stop_solving(problem_id: str) -> None:
    entry = data_sets.get(problem_id)
    if entry is not None:
        entry["user_stopped"] = True

    engine = (entry or {}).get("engine", "timefold")
    if engine == "ortools":
        # The CP-SAT solution callback polls `user_stopped` and calls
        # `StopSearch()` — nothing further to do here.
        return
    if engine in ("timefold_kotlin", "timefold_kotlin_comprehensive"):
        # The polling thread in timefold_kotlin_engine sees `user_stopped`
        # and issues the DELETE to the Kotlin service itself; the Kotlin
        # service in turn calls terminateEarly on every comprehensive
        # island.
        return
    if engine == "solverforge":
        if _solverforge_engine is not None:
            _solverforge_engine.terminate_early(problem_id)
        return
    # Default: in-process Timefold-Python.
    _require_python_timefold()
    _solver_module.solver_manager.terminate_early(problem_id)


class _SettingsPayload(_BaseModel):
    time_limit_s: Optional[int] = None
    soft_threshold: Optional[int] = None
    engine: Optional[str] = None


@app.get("/settings")
async def read_settings() -> dict:
    body = get_settings()
    # Let the UI know whether the in-process Python-Timefold engine can be
    # selected at all (optional extra, absent on Python 3.14).
    body["python_timefold"] = PYTHON_TIMEFOLD_AVAILABLE
    return body


@app.post("/settings")
async def write_settings(payload: _SettingsPayload) -> dict:
    new_state = update_settings(
        time_limit_s=payload.time_limit_s,
        soft_threshold=payload.soft_threshold,
        engine=payload.engine,
    )
    return {"ok": True, "settings": new_state}


# =========================================================== benchmark


class _BenchmarkStartPayload(_BaseModel):
    timeouts: Optional[List[int]] = None   # override the default grid
    engines: Optional[List[str]] = None    # subset of the 5 engines
    append: Optional[bool] = False         # keep existing CSV; add new rows


@app.post("/benchmark/start")
async def benchmark_start(payload: Optional[_BenchmarkStartPayload] = None) -> dict:
    if payload is None:
        return _benchmark.start()
    return _benchmark.start(
        timeouts=payload.timeouts,
        engines=payload.engines,
        append=bool(payload.append),
    )


@app.post("/benchmark/resume")
async def benchmark_resume() -> dict:
    return _benchmark.resume()


@app.delete("/benchmark/stop")
async def benchmark_stop() -> dict:
    return _benchmark.stop()


@app.get("/benchmark/status")
async def benchmark_status() -> dict:
    return _benchmark.status()


@app.get("/benchmark/results.csv")
async def benchmark_csv():
    """Return the raw CSV file for download / pandas."""
    from fastapi.responses import FileResponse
    if not _benchmark.CSV_PATH.exists():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="No benchmark CSV yet")
    return FileResponse(str(_benchmark.CSV_PATH), media_type="text/csv",
                        filename="benchmark_results.csv")


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
