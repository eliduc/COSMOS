"""Stored delivery -> registered record/tree/golden handlers, without HTTP.

The input is an identity-only ZIP and the historical retained projection of
the actual receiver's ``_brief``. This is NOT an accepted-package fixture,
external delivery, solver result, or production golden approval. The existing
human-only/completed-run rule is exercised with a synthetic local signature
and a temporary contour setting (run_tree_panel: sections 12.2 / owner 6.3).
No delivery reader, record builder, or golden verdict is replaced.
"""
from __future__ import annotations

import hashlib
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from cdpm import run_record as rr
from cdpm import run_tree_panel as rtp
from tests.test_cdpm_run_tree_panel import OWNER, _delivery, _run, _write
from webapp import task_channel


AT = "2026-09-17T00:00:00+00:00"
WORDS = "Synthetic local confirmation of the stored-delivery projection"


def _endpoint(webui, path, method):
    matches = [route.endpoint for route in webui.app.routes
               if getattr(route, "path", None) == path
               and method in getattr(route, "methods", set())]
    assert len(matches) == 1, (path, method, matches)
    return matches[0]


def _record(webui, run):
    return _endpoint(webui, "/api/runs/{run_id}/record", "GET")(run.name)


def _tree(webui, run):
    return _endpoint(webui, "/api/runs/{run_id}/tree", "GET")(run.name)


def _refresh(webui, run):
    return _endpoint(webui, "/api/runs/{run_id}/record/refresh", "POST")(run.name)


def _golden(webui, run):
    request = Request({
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": f"/api/runs/{run.name}/golden",
        "raw_path": f"/api/runs/{run.name}/golden".encode(),
        "query_string": b"", "headers": [], "app": webui.app,
    })
    return _endpoint(webui, "/api/runs/{run_id}/golden", "POST")(
        run.name, body={"by": OWNER, "words": WORDS}, request=request)


@pytest.fixture()
def local_run(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    monkeypatch.setenv("CDPM_RUNS_DIR", str(runs))
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "settings.json"))
    # This is only the existing golden eligibility input in an isolated test.
    monkeypatch.setenv("CREW_CONTOUR", "prod")
    for key in ("CREW_CHANNEL_URL", "CREW_CHANNEL_TOKEN", "CREW_CDPM_RECORD_API",
                "CDPM_CATALOG_SERVICE_URL", "CREW_CATALOG_URL", "CREW_SOLVER_URL"):
        monkeypatch.delenv(key, raising=False)
    from cdpm import webui

    run = _run(runs, "run-0042", at=AT, done=False)
    _delivery(run, AT)
    return webui, run


def _store_historical_brief(run, verdict):
    package = run / "channel" / "task-package.zip"
    sha = hashlib.sha256(package.read_bytes()).hexdigest()
    brief = task_channel._brief(
        verdict, detail="Stored receiver brief input; not an external delivery",
        sha=sha, mismatch=["fixture mismatch"] if verdict == "MISMATCH" else [])
    # The pre-existing sender retained these brief fields, but not source_run
    # or spec_version. Their absence must not invalidate a historical mark.
    mark = {"sent": True, "sha": sha, "attempts": 1, "at": AT}
    for key in ("verdict", "mg", "mismatch", "already_accepted", "detail"):
        mark[key] = brief[key]
    _write(run / "channel" / "delivery.json", mark)
    assert "source_run" not in mark and "spec_version" not in mark
    return mark


@pytest.mark.parametrize("verdict", ["OK", "MISMATCH"])
def test_historical_actual_brief_keeps_completed_record_and_human_golden(
        local_run, verdict):
    webui, run = local_run
    mark = _store_historical_brief(run, verdict)
    protected = [run / "declaration.json", run / "channel" / "task-package.zip",
                 run / "channel" / "delivery.json"]
    before = {path: path.read_bytes() for path in protected}

    assert _refresh(webui, run) == {"ok": True, "run": run.name}
    record = _record(webui, run)
    assert record["results"]["state"] == "завершён", record["results"]
    assert record["results"]["deliveries"] == [{
        "kind": "task-package", "path": "channel/task-package.zip",
        "sha256": mark["sha"]}]
    assert _tree(webui, run)["golden_action"]["allowed"] is True

    confirmed = _golden(webui, run)
    assert confirmed["results"]["golden"]["confirmed"] is True
    assert confirmed["results"]["golden"]["by"] == OWNER
    assert confirmed["results"]["golden"]["words"] == WORDS
    _refresh(webui, run)
    assert _record(webui, run)["results"]["golden"]["confirmed"] is True
    tree = _tree(webui, run)
    assert tree["node"]["state"] == "завершён"
    assert tree["golden"]["run_id"] == run.name
    assert tree["golden_action"]["confirmed"] is True
    assert {path: path.read_bytes() for path in protected} == before


@pytest.mark.parametrize("damage", ["foreign_zip", "bare_sent", "refused_brief"])
def test_current_delivery_refusal_blocks_golden_despite_old_completed_record(
        local_run, damage):
    webui, run = local_run
    mark = _store_historical_brief(run, "OK")
    _refresh(webui, run)
    assert _record(webui, run)["results"]["state"] == "завершён"
    assert _tree(webui, run)["golden_action"]["allowed"] is True

    if damage == "foreign_zip":
        # A different, still readable ZIP: test exact byte identity, not format.
        import zipfile
        with zipfile.ZipFile(run / "channel" / "task-package.zip", "w") as archive:
            archive.writestr("foreign.txt", "different artifact")
    elif damage == "bare_sent":
        _write(run / "channel" / "delivery.json",
               {"sent": True, "sha": mark["sha"], "verdict": "OK"})
    else:
        _write(run / "channel" / "delivery.json", {**mark, "verdict": "REFUSED"})

    # The persisted record/index still remember completion. Read/golden must
    # consult current delivery before any explicit refresh repairs that cache.
    cached = json.loads((run / rr.RECORD_FILE).read_text(encoding="utf-8"))
    assert cached["results"]["state"] == "завершён"
    record = _record(webui, run)
    assert record["results"]["state"] != "завершён", (damage, record["results"])
    assert record["results"]["deliveries"] == [], (damage, record["results"])
    action = _tree(webui, run)["golden_action"]
    assert action["allowed"] is False, (damage, action)
    assert action["code"] == rtp.GOLDEN_NOT_DONE, (damage, action)
    with pytest.raises(HTTPException) as denied:
        _golden(webui, run)
    assert denied.value.status_code == 409, (damage, denied.value.detail)
    assert "завершённый" in denied.value.detail, (damage, denied.value.detail)
    assert _record(webui, run)["results"]["golden"]["confirmed"] is False

    _refresh(webui, run)
    _endpoint(webui, "/api/run-tree", "GET")(rebuild=1)
    tree = _tree(webui, run)
    assert tree["node"]["state"] != "завершён", (damage, tree["node"])
    assert tree["golden"] is None
    assert tree["golden_action"]["allowed"] is False
