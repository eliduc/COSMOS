"""HTTP proxy to the Kotlin Timefold Quarkus service in `crew-demo/kotlin-solver`.

The Kotlin service runs the Timefold Solver 2.0.0 core (latest stable, native
JVM) and exposes it as a microservice on port 9091. The benchmark and UI
both reach Timefold via this proxy — Python's ``data_sets`` cache stays the
single source of truth that the UI polls.

We POST the JSON shape we already serialize from Python (camelCase via
`to_camel` alias_generator), poll the Kotlin service at 1 Hz, and republish
the latest schedule into the Python ``data_sets`` cache.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

import httpx

from .domain import FlightCrewSchedule

LOGGER = logging.getLogger(__name__)

# Kotlin service listens on 9091 (python on 8765 — both outside Windows
# Hyper-V's 8030–8129 reserved range).
KOTLIN_BASE_URL = "http://127.0.0.1:9091"

POLL_INTERVAL_S = 1.0


def is_running() -> bool:
    """Cheap liveness probe for the Kotlin service."""
    try:
        with httpx.Client(timeout=2.0) as client:
            r = client.get(f"{KOTLIN_BASE_URL}/q/openapi")
        return r.status_code in (200, 204)
    except Exception:
        return False


def _to_kotlin_schedule(schedule: FlightCrewSchedule) -> Dict[str, Any]:
    """Serialize a Pydantic FlightCrewSchedule into the JSON shape the Kotlin
    service expects. Jackson with @JsonIdentityInfo for shared facts."""
    payload = schedule.model_dump(by_alias=True, mode="json")
    payload.pop("solverStatus", None)
    return payload


def solve_with_timefold_kotlin(
    schedule: FlightCrewSchedule,
    time_limit_s: int = 30,
    soft_threshold: Optional[int] = None,
    on_improvement: Optional[Callable[[FlightCrewSchedule], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
    comprehensive: bool = False,
    phase_a_fraction: float = 1.0,
    on_islands_update: Optional[Callable[[Dict[str, str]], None]] = None,
    islands_poll_interval_s: float = 60.0,
    ensemble: Optional[str] = None,
) -> tuple[FlightCrewSchedule, str]:
    """Drive the Kotlin service to solve `schedule`.

    Mutates `schedule` in place each time the Kotlin service reports a better
    solution; after the run the schedule.score is the best Kotlin knows about.
    Returns ``(schedule, status_text)`` where status_text is "OPTIMAL" |
    "FEASIBLE" | "TIMEOUT" | "STOPPED" | "ERROR: ...".

    When ``comprehensive=True`` the Kotlin service runs four solvers in
    parallel — Late Acceptance, Simulated Annealing, Tabu Search and Great
    Deluge — and returns the best score across all of them. The
    ``phase_a_fraction`` parameter is reserved for the future Phase-A/B
    split; for now the Kotlin side ignores anything < 1.0 and runs all four
    to the end.
    """
    payload = _to_kotlin_schedule(schedule)
    params: Dict[str, Any] = {"spentLimitSeconds": int(max(1, time_limit_s))}
    if soft_threshold is not None:
        params["softThreshold"] = int(soft_threshold)
    if comprehensive:
        params["phaseAFraction"] = float(phase_a_fraction)
        if ensemble is not None:
            params["ensemble"] = str(ensemble)

    endpoint = "/schedules/comprehensive" if comprehensive else "/schedules"
    label = "Kotlin-comprehensive" if comprehensive else "Kotlin"

    try:
        # Payload size sanity log — single-stage on full BTS pushes this
        # into multi-MB range, useful when debugging Quarkus body limits.
        try:
            import json as _json
            _payload_bytes = len(_json.dumps(payload).encode("utf-8"))
            LOGGER.info("%s POST %s payload size: %.2f MB",
                        label, endpoint, _payload_bytes / (1024 * 1024))
        except Exception:
            pass

        with httpx.Client(timeout=60.0) as client:
            r = client.post(f"{KOTLIN_BASE_URL}{endpoint}",
                            json=payload, params=params)
            if r.status_code >= 400:
                # Capture body BEFORE raise_for_status so we can see
                # Jackson/Quarkus error messages.
                body_excerpt = (r.text[:2000] if r.text else "(empty)")
                LOGGER.error("%s POST %s -> %d, body: %s",
                             label, endpoint, r.status_code, body_excerpt)
                print(f"[{label}] POST {endpoint} -> {r.status_code}\n"
                      f"  body: {body_excerpt}", flush=True)
            r.raise_for_status()
            kotlin_job_id = r.text.strip()
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("%s POST %s failed", label, endpoint)
        # Audit fix (2026-07-12, round 6): do NOT fabricate a "-1hard/0soft"
        # score on transport failure — leave schedule.score untouched so the
        # caller can tell "no result" from "bad result". The "ERROR: ..."
        # status text is the failure signal.
        return schedule, f"ERROR: {exc}"

    LOGGER.info("%s solver started: job=%s", label, kotlin_job_id)

    last_score: Optional[str] = None
    deadline = time.monotonic() + time_limit_s + 30
    last_islands_poll = time.monotonic()

    try:
        with httpx.Client(timeout=10.0) as client:
            while time.monotonic() < deadline:
                if should_stop is not None and should_stop():
                    try:
                        client.delete(f"{KOTLIN_BASE_URL}/schedules/{kotlin_job_id}")
                    except Exception:
                        LOGGER.exception("Kotlin DELETE failed")

                try:
                    r = client.get(f"{KOTLIN_BASE_URL}/schedules/{kotlin_job_id}")
                except Exception:
                    LOGGER.exception("Kotlin poll failed; will retry")
                    time.sleep(POLL_INTERVAL_S)
                    continue

                if r.status_code != 200:
                    LOGGER.warning("Kotlin GET %s -> %s", kotlin_job_id, r.status_code)
                    time.sleep(POLL_INTERVAL_S)
                    continue

                body = r.json()
                solver_status = body.get("solverStatus")
                score = body.get("score")

                if score and score != last_score:
                    _apply_kotlin_solution(schedule, body)
                    last_score = score
                    if on_improvement is not None:
                        try:
                            on_improvement(schedule)
                        except Exception:
                            LOGGER.exception("on_improvement consumer raised")

                # Periodic per-island polling for comprehensive runs.
                if comprehensive and on_islands_update is not None:
                    now = time.monotonic()
                    if now - last_islands_poll >= islands_poll_interval_s:
                        try:
                            r2 = client.get(
                                f"{KOTLIN_BASE_URL}/schedules/{kotlin_job_id}/islands"
                            )
                            if r2.status_code == 200:
                                on_islands_update(r2.json())
                        except Exception:
                            LOGGER.exception("Kotlin /islands poll failed")
                        last_islands_poll = now

                if solver_status == "NOT_SOLVING":
                    final_score = score or "0hard/0soft"
                    schedule.score = final_score
                    # One last per-island fetch so the caller sees the
                    # final state of all four acceptors, not the snapshot
                    # from the previous tick.
                    if comprehensive and on_islands_update is not None:
                        try:
                            r2 = client.get(
                                f"{KOTLIN_BASE_URL}/schedules/{kotlin_job_id}/islands"
                            )
                            if r2.status_code == 200:
                                on_islands_update(r2.json())
                        except Exception:
                            LOGGER.exception("Kotlin /islands final fetch failed")
                    if should_stop is not None and should_stop():
                        return schedule, "STOPPED"
                    return schedule, "TIMEOUT"

                time.sleep(POLL_INTERVAL_S)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Kotlin polling loop crashed")
        return schedule, f"ERROR: {exc}"

    try:
        with httpx.Client(timeout=5.0) as client:
            client.delete(f"{KOTLIN_BASE_URL}/schedules/{kotlin_job_id}")
    except Exception:
        pass
    return schedule, "TIMEOUT"


def _apply_kotlin_solution(target: FlightCrewSchedule, body: Dict[str, Any]) -> None:
    """Pull employee assignments and score from a Kotlin response body into the
    in-memory Pydantic schedule."""
    score = body.get("score")
    if score is not None:
        target.score = score

    kotlin_assignments = body.get("flightAssignments") or []
    if not kotlin_assignments:
        return

    by_id = {a.id: a for a in (target.flight_assignments or [])}
    emp_by_id = {e.id: e for e in (target.employees or [])}

    for ja in kotlin_assignments:
        a = by_id.get(ja.get("id"))
        if a is None:
            continue
        emp_field = ja.get("employee")
        if isinstance(emp_field, str):
            a.employee = emp_by_id.get(emp_field)
        elif isinstance(emp_field, dict):
            a.employee = emp_by_id.get(emp_field.get("id"))
        else:
            a.employee = None
