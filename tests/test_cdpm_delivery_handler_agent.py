"""Registered synchronous delivery reader, without HTTP or app startup.

Only temporary paths and channel configuration are supplied. The real run-id
check, file reader, local ZIP identity and retained-brief validator remain in
place. The imported fixture is an identity-only ZIP plus a synthetic lawful
legacy mark, not package admission or proof of an external delivery.

Healthy/foreign-ZIP/refused-brief handler paths already have integral tests;
this supplement covers empty history, missing runs and local envelope fields.
"""
import copy
import json

import pytest
from fastapi import HTTPException

from cdpm import channel, delivery_mark
from test_cdpm_delivery_mark_read_agent import evidence  # noqa: F401


@pytest.fixture
def endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.delenv("CREW_CHANNEL_URL", raising=False)
    monkeypatch.delenv("CREW_CHANNEL_TOKEN", raising=False)
    from cdpm import webui

    routes = [route.endpoint for route in webui.app.routes
              if getattr(route, "path", None) == "/api/runs/{run_id}/channel"
              and "GET" in getattr(route, "methods", set())]
    assert routes == [webui.channel_delivery]
    return routes[0]


def _files(run):
    return {path.relative_to(run).as_posix(): path.read_bytes()
            for path in run.rglob("*") if path.is_file()}


@pytest.mark.parametrize("configured", [False, True])
def test_empty_history_reports_actual_configuration_without_creating_evidence(
        endpoint, tmp_path, monkeypatch, configured):
    run = tmp_path / "run-0042"
    run.mkdir()
    if configured:
        # Input to channel_enabled only: no sender is called by this GET.
        monkeypatch.setenv("CREW_CHANNEL_URL", "http://offline.invalid")
        monkeypatch.setenv("CREW_CHANNEL_TOKEN", "synthetic-config-only")
    assert channel.channel_enabled() is configured
    assert delivery_mark.read_current(run) == {}
    result = endpoint(run.name)
    assert result["run"] == run.name
    assert result["configured"] is configured
    assert result["sent"] is False
    assert "доставок по каналу ещё не было" in result["detail"]
    assert ("канал выключен" in result["detail"]) is (not configured)
    assert list(run.iterdir()) == [], "A read must not create delivery history"


@pytest.mark.parametrize("run_id,status", [("run-9999", 404), ("../run-0042", 400)])
def test_run_identity_is_checked_before_reading_delivery(endpoint, tmp_path, run_id, status):
    before = _files(tmp_path)
    with pytest.raises(HTTPException) as caught:
        endpoint(run_id)
    assert caught.value.status_code == status
    assert run_id in caught.value.detail
    assert _files(tmp_path) == before
    assert not (tmp_path / "run-9999").exists()


@pytest.mark.parametrize("field,foreign", [("run", "run-9999"), ("configured", True)])
def test_saved_extension_cannot_replace_local_run_or_current_configuration(
        endpoint, evidence, field, foreign):
    run, package, receipt, mark = evidence
    assert "source_run" not in mark and "spec_version" not in mark
    assert channel.channel_enabled() is False
    assert delivery_mark.read_current(run) == mark
    healthy_bytes = _files(run)
    assert endpoint(run.name) == {**mark, "run": run.name, "configured": False}
    assert _files(run) == healthy_bytes

    # Unknown stored extension fields remain historical data. Their presence
    # is allowed by the existing reader, not a reason to redefine its schema.
    # Only the handler owns the requested run and live configuration fields.
    mark = {**copy.deepcopy(mark), field: foreign}
    receipt.write_text(json.dumps(mark), encoding="utf-8")
    before = _files(run)
    assert delivery_mark.read_current(run) == mark
    result = endpoint(run.name)
    assert _files(run) == before
    expected = {**mark, "run": run.name, "configured": False}
    assert result == expected, {"actual": result, "expected": expected}
