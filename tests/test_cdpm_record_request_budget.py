"""CDPM231: a busy record must not stall/replay an already completed action.

Only temporary files, an in-process HTTP client, fake failures and held readers.
"""
import contextvars
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time

import pytest
from fastapi.testclient import TestClient

from cdpm import file_replace as fr, run_record as rr, webui
from test_cdpm_run_record_file_replace import _run


def _panel(tmp_path, monkeypatch):
    run = _run(tmp_path)
    rr.refresh(run)
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path))
    monkeypatch.setattr(webui, "_rebuild_run_tree", lambda *a: None)
    return run, TestClient(webui.app)


def _busy_record(monkeypatch):
    windows = []
    actual = fr.replace_with_retry
    def replace(src, dst, **kwargs):
        if dst.name == rr.RECORD_FILE:
            windows.append(kwargs.get("window_s"))
            raise PermissionError(13, "file held by foreign reader")
        return actual(src, dst, **kwargs)
    monkeypatch.setattr(fr, "replace_with_retry", replace)
    return windows


def test_real_append_succeeds_once_and_reports_record_only_warning(tmp_path,
                                                                  monkeypatch):
    run, client = _panel(tmp_path, monkeypatch)
    head = rr.record_head(run)
    windows = _busy_record(monkeypatch)
    response = client.post(f"/api/runs/{run.name}/record/append", json={
        "schema": rr.APPEND_SCHEMA, "record_id": head["run_id"],
        "declaration_sha256": head["declaration_sha256"], "system": "cosmos",
        "entries": [], "results": {"state": "идёт"}})
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True  # action already happened
    assert windows and all(w is not None and 0 <= w <= 0.25 for w in windows)
    assert response.headers["x-cdpm-record-warning"] == "record-refresh-busy"
    assert response.headers["x-cdpm-record-runs"] == run.name
    assert len((run / "journal/record-appends.jsonl").read_text(
        encoding="utf-8").splitlines()) == 1
    assert not list(run.glob("*.tmp"))


def test_existing_healthy_append_has_no_warning(tmp_path, monkeypatch):
    run, client = _panel(tmp_path, monkeypatch)
    head = rr.record_head(run)
    response = client.post(f"/api/runs/{run.name}/record/append", json={
        "schema": rr.APPEND_SCHEMA, "record_id": head["run_id"],
        "declaration_sha256": head["declaration_sha256"], "system": "cosmos",
        "entries": [], "results": {"state": "идёт"}})
    assert response.status_code == 200
    assert "x-cdpm-record-warning" not in response.headers


def test_budget_is_shared_by_writes_and_reset_after_exception(tmp_path, monkeypatch):
    seen = []
    def replace(src, dst, *, window_s=None):
        seen.append(window_s)
        raise PermissionError(13, "busy")
    monkeypatch.setattr(fr, "replace_with_retry", replace)
    with rr.request_write_budget(0.02) as warnings:
        with pytest.raises(rr.RecordWriteBusy):
            rr._write_json(tmp_path / rr.RECORD_FILE, {})
        time.sleep(0.03)
        with pytest.raises(rr.RecordWriteBusy):
            rr._write_json(tmp_path / rr.RECORD_FILE, {})
        assert warnings
    with pytest.raises(PermissionError) as error:
        rr._write_json(tmp_path / rr.RECORD_FILE, {})
    assert not isinstance(error.value, rr.RecordWriteBusy)
    assert 0 <= seen[0] <= 0.02 and seen[1] == 0 and seen[2] is None
    assert not list(tmp_path.glob("*.tmp"))


def test_background_thread_drops_even_an_inherited_request_budget(tmp_path,
                                                                 monkeypatch):
    run = _run(tmp_path)
    done = threading.Event()
    seen = []
    def replace(src, dst, *, window_s=None):
        seen.append(window_s)
        fr.replace_file(src, dst)
    monkeypatch.setattr(fr, "replace_with_retry", replace)
    real_thread = threading.Thread
    # Simulate a runtime which inherits ContextVars into new threads.
    def inherited_thread(*, target, daemon):
        ctx = contextvars.copy_context()
        return real_thread(target=lambda: ctx.run(target), daemon=daemon)
    monkeypatch.setattr(webui.threading, "Thread", inherited_thread)
    def work():
        try:
            rr._write_json(run / rr.RECORD_FILE, {"background": True})
        finally:
            done.set()
    with rr.request_write_budget(0) as warnings:
        webui._start_stage_thread(run, work)
        assert done.wait(3)
        assert warnings == []
    assert seen == [None]


def test_refresh_only_endpoint_can_be_retried_without_repeating_action(tmp_path,
                                                                    monkeypatch):
    run, client = _panel(tmp_path, monkeypatch)
    with monkeypatch.context() as busy:
        windows = _busy_record(busy)
        response = client.post(f"/api/runs/{run.name}/record/refresh")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "record-refresh-busy"
        assert "занят" in response.json()["detail"]["error"]
        assert windows and windows[0] <= 0.25
    response = client.post(f"/api/runs/{run.name}/record/refresh")
    assert response.status_code == 200 and response.json()["ok"] is True
    assert not (run / "journal/record-appends.jsonl").exists()
    assert rr.on_disk(run) is not None


@pytest.mark.skipif(not fr._WIN, reason="Windows ordinary reader holds replacement")
def test_actual_foreign_reader_has_short_request_wait_but_default_background(
        tmp_path, monkeypatch):
    run, client = _panel(tmp_path, monkeypatch)
    t0 = time.monotonic()
    with open(run / rr.RECORD_FILE, "rb"):
        response = client.post(f"/api/runs/{run.name}/record/refresh")
    assert response.status_code == 503
    assert time.monotonic() - t0 < 2
    # The shared helper default is NOT changed by the request policy.
    assert fr.REPLACE_WINDOW_S == 10.0
    assert not list(run.glob("*.tmp"))


def test_browser_warning_api_without_network():
    node = shutil.which("node")
    assert node, "Node is required for this UI regression profile"
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, "--test", "src/record-warning.test.mjs"],
                            cwd=root / "cdpm-ui", capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_mandatory_declaration_is_not_a_record_only_retry(tmp_path, monkeypatch):
    windows = []
    def replace(src, dst, *, window_s=None):
        windows.append(window_s)
        raise PermissionError(13, "declaration held")
    monkeypatch.setattr(fr, "replace_with_retry", replace)
    with rr.request_write_budget(0) as warnings:
        with pytest.raises(PermissionError) as error:
            rr._write_json(tmp_path / rr.DECLARATION_FILE, {})
        assert not isinstance(error.value, rr.RecordWriteBusy)
        assert windows == [None] and warnings == []
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(not fr._WIN, reason="Windows ordinary reader holds replacement")
def test_real_background_waits_past_request_window_and_then_succeeds(tmp_path,
                                                                   monkeypatch):
    path = tmp_path / rr.RECORD_FILE
    rr._write_json(path, {"generation": 1})
    entered = threading.Event()
    done = threading.Event()
    errors = []
    actual = fr.replace_with_retry
    def observed(src, dst, **kwargs):
        entered.set()
        return actual(src, dst, **kwargs)
    monkeypatch.setattr(fr, "replace_with_retry", observed)
    def writer():
        try:
            rr._write_json(path, {"generation": 2})
        except Exception as error:
            errors.append(error)
        finally:
            done.set()
    worker = threading.Thread(target=writer, daemon=True)
    try:
        with open(path, "rb"):
            worker.start()
            assert entered.wait(2)
            assert not done.wait(rr.REQUEST_WRITE_WINDOW_S + 0.2)
    finally:
        worker.join(timeout=3)
    assert done.is_set() and not errors, errors
    assert rr._read_json(path) == {"generation": 2}
    assert not list(tmp_path.glob("*.tmp"))
