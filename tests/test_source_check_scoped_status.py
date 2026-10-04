"""Offline identity regressions; real endpoint functions, no app startup."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from webapp import source_check_status as scs

ROOT = Path(__file__).resolve().parents[1]
CONTEXT = {"task": "rostering", "package_sha": "b" * 64, "cosmos_run": "run-B",
           "workdir": "package-runs/B", "attempt_id": "attempt-B",
           "solver_run_id": "uuid-B", "catalog_sha256": None}


class HttpError(Exception):
    def __init__(self, code, detail):
        self.status_code, self.detail = code, detail


def endpoint(name):
    tree = ast.parse((ROOT / "webapp/app.py").read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    scope = {"__package__": "webapp", "Path": Path, "json": json,
             "Request": object, "HTTPException": HttpError,
             "Response": lambda **kw: kw, "_user": lambda r: None,
             "_user_or_channel": lambda r: None, "_source_check_task": lambda t: t}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), scope)
    return scope[name]


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scs, "current_context", lambda task: dict(CONTEXT, task=task))
    snapdir = tmp_path / "source-check/rostering"
    snapdir.mkdir(parents=True)
    checks = tmp_path / "checks-B.json"
    checks.write_text(json.dumps({"results": {"B": [{"check_id": "B_CHECK"}]}}))
    protocol = tmp_path / "protocol-B.md"
    protocol.write_text("B_PROTOCOL")
    (snapdir / "protocol-last.md").write_text("H0_OLD_PROTOCOL")
    snapshot = {**CONTEXT, "status": "done", "counts": {"ok": 1},
                "checks": str(checks), "protocol": str(protocol)}
    def put(**changes):
        doc = {**snapshot, **changes}
        (snapdir / "last.json").write_text(json.dumps(doc))
        return doc
    put()
    return put


def test_h0_done_is_idle_on_b_and_no_protocol(storage):
    storage(package_sha="a" * 64, cosmos_run="run-H0", solver_run_id="uuid-H0")
    got = endpoint("api_source_check_last")(None, task="rostering")
    assert got["status"] == "idle"
    assert not got.get("rows") and not got.get("counts")
    with pytest.raises(HttpError) as ex:
        endpoint("api_source_check_protocol")(None, task="rostering")
    assert ex.value.status_code == 404


def test_healthy_returns_own_rows_and_own_protocol_not_global(storage):
    got = endpoint("api_source_check_last")(None, task="rostering")
    assert got["status"] == "done" and got["rows"][0]["id"] == "B_CHECK"
    assert got["context_key"] == scs.context_key(CONTEXT)
    protocol = endpoint("api_source_check_protocol")(None, task="rostering",
                                                    context_key=got["context_key"])
    assert protocol["content"] == "B_PROTOCOL"


@pytest.mark.parametrize("field", ["package_sha", "cosmos_run", "workdir", "task",
                                    "attempt_id", "solver_run_id"])
def test_exact_identity_mismatch_refused(storage, field):
    storage(**{field: "other"})
    assert endpoint("api_source_check_last")(None, task="rostering")["status"] == "idle"


@pytest.mark.parametrize("field", ["package_sha", "cosmos_run", "workdir", "task", "solver_run_id"])
def test_missing_binding_not_inferred(storage, field):
    storage(**{field: None})
    assert endpoint("api_source_check_last")(None, task="rostering")["status"] == "idle"


def test_legacy_exact_uuid_is_supported_but_explicit_wrong_attempt_is_not(storage):
    storage(attempt_id=None)
    assert endpoint("api_source_check_last")(None, task="rostering")["status"] == "done"
    storage(attempt_id="another")
    assert endpoint("api_source_check_last")(None, task="rostering")["status"] == "idle"


def test_stale_pinned_key_cannot_download_or_poll_new_selection(storage):
    for name in ("api_source_check_last", "api_source_check_protocol"):
        with pytest.raises(HttpError) as ex:
            endpoint(name)(None, task="rostering", context_key="old-H0-key")
        assert ex.value.status_code == 409


def test_error_never_downloads_previous_global_protocol(storage):
    storage(status="error", error="current B failure")
    with pytest.raises(HttpError) as ex:
        endpoint("api_source_check_protocol")(None, task="rostering")
    assert ex.value.status_code == 404


def test_pairing_hash_and_task_are_not_roster_identity():
    ctx = {**CONTEXT, "task": "pairing", "solver_run_id": None, "catalog_sha256": "cat-B"}
    assert scs.matches(ctx, ctx)
    assert not scs.matches({**ctx, "catalog_sha256": "cat-H0"}, ctx)
    assert not scs.matches(CONTEXT, ctx)


def test_flow_uses_same_selected_snapshot(storage):
    from webapp import flow
    assert flow._source_check_state("rostering")["status"] == "done"
    storage(solver_run_id="uuid-H0")
    assert flow._source_check_state("rostering")["status"] == "idle"


def test_context_resolves_unique_selected_attempt_read_only(tmp_path, monkeypatch):
    from webapp import package_runs as pr
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    wd = tmp_path / "package-runs/B"
    out = wd / "button"
    out.mkdir(parents=True)
    (out / "delivery.json").write_text(json.dumps({"schedule": {"solver_run_id": "uuid-B"}}))
    rec = {"id": "attempt-B", "task": "rostering", "done": True,
           "package_sha": "b" * 64, "run": "run-B", "summary": {"solver_run_id": "uuid-B"}}
    state = {"package_sha": "b" * 64, "attempts": [rec]}
    (wd / "state.json").write_text(json.dumps(state))
    cur = {"sha": "b" * 64, "run": {"run": "run-B", "workdir": "package-runs/B"}}
    before = {p: p.read_bytes() for p in wd.rglob("*") if p.is_file()}
    assert scs.current_context("rostering", cur=cur) == CONTEXT
    assert before == {p: p.read_bytes() for p in wd.rglob("*") if p.is_file()}
    state["attempts"].append(rec)
    (wd / "state.json").write_text(json.dumps(state))
    assert scs.current_context("rostering", cur=cur) is None


def test_start_records_context_for_running_done_and_error():
    source = (ROOT / "webapp/app.py").read_text(encoding="utf-8")
    assert source.count("**(check_context or {})") == 2
    assert "binding.update(check_context or {})" in source
    assert 'body["context_key"] != key' in source
