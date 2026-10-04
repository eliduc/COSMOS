"""Audit round 6 (P5, 2026-07-12): REST fallback management.

On Python 3.14 python-timefold is absent; the settings endpoints must still
persist updates (the old stub fallback ignored them, so the engine could
never be switched to OR-Tools), both status endpoints must agree, and a
Kotlin-proxy transport failure must not fabricate a score.

Runs WITHOUT python-timefold — everything here goes through the
timefold-free settings module and the _timefold_compat stubs.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from flight_crew_scheduling import rest_api  # noqa: E402
from flight_crew_scheduling import settings as fcs_settings  # noqa: E402
from flight_crew_scheduling import timefold_kotlin_engine as tk_engine  # noqa: E402
from flight_crew_scheduling.demo_data import generate_demo_data  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_settings():
    """Settings are module-level state shared with the app — snapshot and
    restore around every test so order does not matter."""
    saved = dict(fcs_settings._settings)
    saved_callbacks = list(fcs_settings.on_update_callbacks)
    yield
    fcs_settings._settings.clear()
    fcs_settings._settings.update(saved)
    fcs_settings.on_update_callbacks[:] = saved_callbacks


# ------------------------------------------------------------ settings module


def test_update_engine_reflected_in_get_settings():
    out = fcs_settings.update_settings(engine="ortools")
    assert out["engine"] == "ortools"
    assert fcs_settings.get_settings()["engine"] == "ortools"


def test_invalid_engine_is_ignored():
    before = fcs_settings.get_settings()["engine"]
    out = fcs_settings.update_settings(engine="definitely-not-an-engine")
    assert out["engine"] == before


def test_timefold_alias_normalized():
    out = fcs_settings.update_settings(engine="timefold")
    assert out["engine"] == "timefold_python"


def test_time_limit_int_coerced():
    out = fcs_settings.update_settings(time_limit_s="99")
    assert out["time_limit_s"] == 99
    assert isinstance(out["time_limit_s"], int)


def test_on_update_callback_invoked():
    calls = []
    fcs_settings.on_update_callbacks.append(lambda: calls.append(1))
    fcs_settings.update_settings(time_limit_s=7)
    assert calls == [1]


# ------------------------------------------------------------ REST /settings


client = TestClient(rest_api.app)


def test_get_settings_reports_python_timefold_availability():
    r = client.get("/settings")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["python_timefold"], bool)
    assert body["python_timefold"] == rest_api.PYTHON_TIMEFOLD_AVAILABLE


def test_post_settings_actually_persists():
    r = client.post("/settings", json={"engine": "ortools", "time_limit_s": 99})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    body = client.get("/settings").json()
    assert body["engine"] == "ortools"
    assert body["time_limit_s"] == 99


# ------------------------------------------------------- status consistency


def _make_external_entry(job_id: str) -> dict:
    now = datetime.now()
    entry = {
        "schedule": generate_demo_data(),
        "created_at": now,
        "started_at": now,
        "final_at": None,
        "time_limit_s": 5,
        "soft_threshold": None,
        "engine": "timefold_kotlin",
        "user_stopped": False,
        "exception": None,
    }
    rest_api.data_sets[job_id] = entry
    return entry


def test_status_endpoints_agree_for_external_engine():
    job_id = "test-status-consistency"
    entry = _make_external_entry(job_id)
    try:
        # While the worker runs (final_at is None) both endpoints must say
        # SOLVING_ACTIVE — before the fix /status read the stored
        # schedule.solver_status, which external engines never update.
        full = client.get(f"/schedules/{job_id}").json()
        short = client.get(f"/schedules/{job_id}/status").json()
        assert full["solverStatus"] == "SOLVING_ACTIVE"
        assert short["solver_status"] == "SOLVING_ACTIVE"

        entry["final_at"] = datetime.now()
        full = client.get(f"/schedules/{job_id}").json()
        short = client.get(f"/schedules/{job_id}/status").json()
        assert full["solverStatus"] == "NOT_SOLVING"
        assert short["solver_status"] == "NOT_SOLVING"
    finally:
        rest_api.data_sets.pop(job_id, None)


def test_summary_exposes_error_state():
    job_id = "test-summary-error"
    entry = _make_external_entry(job_id)
    try:
        entry["exception"] = "ERROR: connection refused"
        entry["final_at"] = datetime.now()
        summary = client.get(f"/schedules/{job_id}").json()["summary"]
        assert summary["solveState"] == "ERROR"
        assert summary["finishReason"] == "error"
    finally:
        rest_api.data_sets.pop(job_id, None)


# ---------------------------------------------------- kotlin transport error


def test_transport_failure_does_not_fake_score(monkeypatch):
    class _BoomClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def post(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

        def get(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(tk_engine.httpx, "Client", _BoomClient)

    schedule = generate_demo_data()
    assert schedule.score is None
    solved, status = tk_engine.solve_with_timefold_kotlin(schedule, time_limit_s=1)
    assert status.startswith("ERROR")
    # The old code stamped a fabricated "-1hard/0soft" here.
    assert solved.score is None
