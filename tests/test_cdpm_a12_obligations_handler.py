"""Real registered local A12 handlers, without HTTP/ASGI/server startup.

Synthetic source -> real freeze/model -> real obligations adapter and writer.
Only environment paths and an addressed replace failure are supplied. No
report, reader independence, model verdict or expert approval is fabricated.
This checks server-rendered content, not browser/JavaScript execution.
"""
import copy
import hashlib
import json

import pytest
from fastapi import HTTPException

from cdpm import obligations as ob
from test_cdpm_a12_obligations import _json, _reseal
from test_cdpm_a12_obligations_integration import _case, _raw_tree


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "settings.json"))
    run, ledger = _case(tmp_path)
    from cdpm import webui
    return webui, run, ledger


def _endpoint(webui, suffix, method):
    path = "/api/runs/{run_id}/obligations" + suffix
    matches = [route.endpoint for route in webui.app.routes
               if getattr(route, "path", None) == path and method in getattr(route, "methods", set())]
    assert len(matches) == 1, (path, method)
    return matches[0]


def _get(local):
    webui, run, _ = local
    return _endpoint(webui, "", "GET")(run.name)


def _put(local, text, expected):
    webui, run, _ = local
    return _endpoint(webui, "", "PUT")(run.name, {"text": text, "expected_sha256": expected})


def test_read_export_and_page_show_real_gaps_without_approval(local):
    webui, run, ledger = local
    before = _raw_tree(run)
    view = _get(local)
    assert view["report"] == ob.run_report(run)
    assert view["report"]["freshness"] == "current"
    assert view["report"]["readers"][0]["missing"] == ["O2"]
    assert view["report"]["readers"][0]["linked"] == 1
    assert view["report"]["meaning"] == "not_proven"
    assert view["report"]["expert_review"] == {"status": "not_reviewed"}
    assert view["sha256"] == hashlib.sha256(before[ob.LEDGER_REL]).hexdigest()
    assert json.loads(view["text"]) == ledger
    download = _endpoint(webui, "", "GET")(run.name, download=1)
    assert download.body == before[ob.LEDGER_REL]
    assert download.headers["content-type"] == "application/json"
    assert download.headers["cache-control"] == "no-store"
    page_route = [route.endpoint for route in webui.app.routes
                  if getattr(route, "path", None) == "/runs/{run_id}/obligations"]
    assert page_route == [webui.obligations_page]
    page = page_route[0](run.name).body.decode()
    for text in ("O1", "O2", "conditions", "exceptions", "R-LIMIT", "missing_components",
                 "not_proven", "не являются приёмкой", "Пересчитать отпечатки", "expected_sha256"):
        assert text in page
    assert 'href="/runs/${r.run}/obligations"' in webui.legacy_page()
    assert _raw_tree(run) == before


def test_new_ledger_is_explicit_and_template_never_fabricates_readers(local):
    webui, run, ledger = local
    (run / ob.LEDGER_REL).unlink()
    before = _raw_tree(run)
    view = _get(local)
    assert view["report"]["form"] == "not_recorded"
    assert view["sha256"] is None and view["text"] is None
    assert view["template"]["schema_version"] == "obligation-ledger/1"
    assert view["template"]["readers"] == []
    assert view["template"]["expert_review"] is None
    assert view["template"]["snapshot"] == ob.capture_run_snapshot(run)
    assert _raw_tree(run) == before
    with pytest.raises(HTTPException) as missing:
        _endpoint(webui, "", "GET")(run.name, download=1)
    assert missing.value.status_code == 404
    with pytest.raises(HTTPException) as draft:
        _put(local, json.dumps(view["template"]), None)
    assert draft.value.status_code == 422
    assert not (run / ob.LEDGER_REL).exists()

    raw = _json(ledger).decode() + "\n  "
    saved = _put(local, raw, None)
    assert saved["text"] == raw
    assert (run / ob.LEDGER_REL).read_bytes() == raw.encode()
    assert saved["report"]["readers"][0]["missing"] == ["O2"]
    assert saved["report"]["meaning"] == "not_proven"
    assert all((run / name).read_bytes() == value for name, value in before.items())


def test_prepare_seals_only_authored_readers_and_save_checks_previous_sha(local):
    webui, run, ledger = local
    old = _get(local)
    authored = copy.deepcopy(ledger)
    authored["readers"][0]["actor"] = "Changed explicit local author"
    raw = _json(authored).decode()
    before = _raw_tree(run)
    with pytest.raises(HTTPException) as unsealed:
        _put(local, raw, old["sha256"])
    assert unsealed.value.status_code == 422
    prepared = _endpoint(webui, "/prepare", "POST")(run.name, {"text": raw})
    assert _raw_tree(run) == before
    expected = copy.deepcopy(authored)
    _reseal(expected)
    assert json.loads(prepared["text"]) == expected
    assert prepared["report"]["meaning"] == "not_proven"
    assert prepared["report"]["expert_review"] == {"status": "not_reviewed"}
    assert expected["snapshot"] == ledger["snapshot"]
    saved = _put(local, prepared["text"], old["sha256"])
    assert saved["sha256"] != old["sha256"]
    assert saved["report"]["freshness"] == "current"
    with pytest.raises(HTTPException) as concurrent:
        _put(local, old["text"], old["sha256"])
    assert concurrent.value.status_code == 409
    assert _get(local)["text"] == prepared["text"]
    assert all((run / name).read_bytes() == value for name, value in before.items()
               if name != ob.LEDGER_REL)


@pytest.mark.parametrize("damage", ["duplicate_key", "nonfinite", "scalar", "bad_version", "bad_link"])
def test_strict_import_refuses_without_overwriting_saved_evidence(local, damage):
    _, run, ledger = local
    before = _raw_tree(run)
    if damage == "duplicate_key":
        raw = '{"schema_version":"wrong",' + _json(ledger).decode()[1:]
    elif damage == "nonfinite":
        raw = '{"unexamined":NaN,' + _json(ledger).decode()[1:]
    elif damage == "scalar":
        raw = "true"
    else:
        candidate = copy.deepcopy(ledger)
        if damage == "bad_version":
            candidate["schema_version"] = "obligation-ledger/999"
        else:
            candidate["links"][0]["targets"][0]["pointer"] = "/not-a-field"
        raw = _json(candidate).decode()
    with pytest.raises(HTTPException) as refused:
        _put(local, raw, _get(local)["sha256"])
    assert refused.value.status_code == 422
    assert _raw_tree(run) == before


def test_stale_bytes_read_as_stale_and_import_cannot_rebind_old_reading(local):
    webui, run, _ = local
    old = _get(local)
    path = run / "extract" / "model.json"
    path.write_bytes(path.read_bytes() + b" ")
    before = _raw_tree(run)
    view = _get(local)
    assert view["text"] == old["text"]
    assert view["report"]["freshness"] == "stale_or_unresolved"
    assert view["report"]["readers"][0]["linked"] == 0
    for endpoint, body in [(_endpoint(webui, "/prepare", "POST"), {"text": old["text"]}),
                           (_endpoint(webui, "", "PUT"), {"text": old["text"], "expected_sha256": old["sha256"]})]:
        with pytest.raises(HTTPException) as refused:
            endpoint(run.name, body)
        assert refused.value.status_code == 409
    assert _raw_tree(run) == before


def test_atomic_replace_failure_preserves_previous_version_and_retry(local, monkeypatch):
    webui, run, _ = local
    old = _get(local)
    wanted = old["text"] + "\n "
    original = webui._file_replace.replace_with_retry
    def fail(src, dst):
        assert dst == (run / ob.LEDGER_REL).resolve()
        assert src.read_bytes() == wanted.encode()
        raise PermissionError("synthetic replace refusal")
    monkeypatch.setattr(webui._file_replace, "replace_with_retry", fail)
    with pytest.raises(HTTPException) as refused:
        _put(local, wanted, old["sha256"])
    assert refused.value.status_code == 503
    assert "synthetic replace refusal" in refused.value.detail
    assert _get(local)["text"] == old["text"]
    assert list((run / "exchange").glob(".obligations-*")) == []
    monkeypatch.setattr(webui._file_replace, "replace_with_retry", original)
    assert _put(local, wanted, old["sha256"])["text"] == wanted


@pytest.mark.parametrize("run_id,status", [("../run-0012", 400), ("run-9999", 404)])
def test_actual_run_guard_applies_before_all_obligation_actions(local, run_id, status):
    webui, run, _ = local
    before = _raw_tree(run)
    for endpoint, args in [(_endpoint(webui, "", "GET"), (run_id,)),
                           (_endpoint(webui, "/prepare", "POST"), (run_id, {"text": "{}"})),
                           (_endpoint(webui, "", "PUT"), (run_id, {})),
                           (webui.obligations_page, (run_id,))]:
        with pytest.raises(HTTPException) as refused:
            endpoint(*args)
        assert refused.value.status_code == status
    assert _raw_tree(run) == before


def test_server_rendered_imported_claims_are_escaped(local):
    webui, run, ledger = local
    attack = '</textarea><script>alert("not executable")</script>'
    ledger["readers"][0]["obligations"][0]["statement"] = attack
    _reseal(ledger)
    _put(local, _json(ledger).decode(), _get(local)["sha256"])
    page = webui.obligations_page(run.name).body.decode()
    assert attack not in page
    assert "&lt;/textarea&gt;&lt;script&gt;" in page
    assert _get(local)["report"]["meaning"] == "not_proven"


def test_prepare_and_save_do_not_refresh_expert_review_after_authored_edit(local):
    webui, run, ledger = local
    ledger["expert_review"] = {
        "reviewer": "fixture owner", "decision": "accepted", "reason": "reviewed original content",
        "snapshot_sha256": ob.fingerprint(ledger["snapshot"]),
        "scope_sha256": ob.fingerprint(ledger["scope"]),
        "reviewed_content_sha256": ob.fingerprint({k: v for k, v in ledger.items() if k != "expert_review"}),
        "issues": []}
    saved = _put(local, _json(ledger).decode(), _get(local)["sha256"])
    assert saved["report"]["expert_review"]["status"] == "recorded", saved
    old_review = copy.deepcopy(ledger["expert_review"])
    ledger["readers"][0]["obligations"][0]["statement"] = "Different authored statement"
    prepared = _endpoint(webui, "/prepare", "POST")(run.name, {"text": _json(ledger).decode()})
    assert json.loads(prepared["text"])["expert_review"] == old_review
    assert prepared["report"]["expert_review"]["status"] == "stale", prepared
    changed = _put(local, prepared["text"], saved["sha256"])
    assert changed["report"]["expert_review"]["status"] == "stale", changed
    assert changed["report"]["meaning"] == "not_proven"


def _actual_symlink(link, target, *, directory=False):
    """Real filesystem alias only; an unavailable OS capability is not proof."""
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if getattr(exc, "winerror", None) in {50, 1314}:
            pytest.skip(f"Actual symlink capability unavailable: {exc}")
        raise


def _actual_directory_alias(link, target):
    # Junctions are real directory redirections available without the Windows
    # symlink privilege. No shell/child process or mocked path resolution.
    import os
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
        assert link.is_junction()
    else:
        _actual_symlink(link, target, directory=True)


@pytest.mark.parametrize("target_name", ["extract/model.json", "freeze/manifest.json", "src/synthetic-rules.md"])
def test_writer_refuses_actual_ledger_symlink_without_replacing_source_or_model(local, target_name):
    _, run, ledger = local
    path, target = run / ob.LEDGER_REL, run / target_name
    before = target.read_bytes()
    path.unlink()
    _actual_symlink(path, target)
    refused = None
    try:
        _put(local, _json(ledger).decode(), hashlib.sha256(before).hexdigest())
    except HTTPException as exc:
        refused = exc
    assert target.read_bytes() == before, f"Ledger writer replaced protected target {target_name}"
    assert refused is not None and refused.status_code == 400, refused
    assert path.is_symlink()


def test_writer_refuses_actual_exchange_directory_alias(local):
    _, run, ledger = local
    exchange = run / "exchange"
    (run / ob.LEDGER_REL).unlink()
    exchange.rmdir()
    target = run / "another-directory"
    target.mkdir()
    existing = target / "obligations.json"
    original = b"Do not overwrite another directory's file"
    existing.write_bytes(original)
    _actual_directory_alias(exchange, target)
    refused = None
    try:
        _put(local, _json(ledger).decode(), hashlib.sha256(original).hexdigest())
    except HTTPException as exc:
        refused = exc
    assert existing.read_bytes() == original, "Ledger writer followed a redirected exchange directory"
    assert refused is not None and refused.status_code == 400, refused
