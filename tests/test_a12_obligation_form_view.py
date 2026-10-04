"""Direct real form handlers with the existing synthetic A12 disk carrier.

This tests access to declared, hash-bound correspondence, not semantic
coverage or independent expert approval. No HTTP, LLM, solver or verdict
substitution; canonical form and saved run evidence must stay unchanged.
"""
import hashlib
import json
from html import escape
from pathlib import Path

import pytest

from cdpm import model, obligations as ob, vmp_form as vf
from test_cdpm_a12_obligations import _json, _reseal
from test_cdpm_a12_obligations_integration import _case, _raw_tree


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path))
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "settings.json"))
    run, ledger = _case(tmp_path)
    from cdpm import webui

    def no_channel(*args, **kwargs):
        pytest.fail("Reading the form must never construct an LLM channel")

    monkeypatch.setattr(webui, "_channel_for", no_channel)
    return webui, run, ledger


def _get(local):
    webui, run, _ = local
    matches = [route.endpoint for route in webui.app.routes
               if getattr(route, "path", None) == "/api/runs/{run_id}/form"
               and "GET" in getattr(route, "methods", set())]
    assert matches == [webui.run_form]
    return json.loads(matches[0](run.name).body)


def test_existing_legal_link_is_accessible_at_its_exact_form_field(local):
    _, run, _ = local
    before = _raw_tree(run)
    report = ob.run_report(run)
    assert report["form"] == "valid" and report["freshness"] == "current", report
    assert report["reverse_links"] == [{
        "collection": "rules", "row_id": "R-LIMIT", "pointer": "/params/0/value",
        "reader": "LEFT", "obligation": "O1", "component": "statement"}]
    response = _get(local)
    assert "obligations" in response, "A legal saved link has no form-side evidence view"
    view = response["obligations"]
    assert view["status"] == "current", view
    assert view["model_sha256"] == hashlib.sha256(before["extract/model.json"]).hexdigest()
    assert view["fields"] == [{
        "collection": "rules", "row_id": "R-LIMIT", "pointer": "/params/0/value",
        "value": 600, "links": [{"reader": "LEFT", "obligation": "O1",
            "component": "statement", "status": "linked",
            "correspondence": "declared_not_semantically_verified"}]}]
    assert view["meaning"] == "not_proven"
    assert view["independence"] == "declared_only"
    assert view["expert_review"] == {"status": "not_reviewed"}
    assert _raw_tree(run) == before


def test_canonical_form_and_roundtrip_checks_are_unchanged(local):
    webui, run, _ = local
    before = _raw_tree(run)
    doc = model.loads_model(before["extract/model.json"])
    expected = vf.to_form(doc)
    response = _get(local)
    assert response["form"] == expected
    assert "obligations" not in response["form"]
    assert response["problems"] == vf.validate_form(expected, strict=True)
    assert response["lost"] == vf.canonical_diff(doc, vf.from_form(expected))
    assert response["lossless"] is (not response["lost"])
    page = webui.run_form_md(run.name).body.decode()
    assert "Часть 1. Пейринг" in page and "Часть 2. Ростеринг" in page
    assert _raw_tree(run) == before


def test_human_form_shows_exact_field_value_disposition_and_a12_link(local):
    webui, run, _ = local
    before = _raw_tree(run)
    response = webui.run_form_md(run.name)
    page = response.body.decode()
    assert response.headers["cache-control"] == "no-store"
    for text in ("obligation-field-evidence", "Обратные ссылки обязательств у полей ВМП",
                 "R-LIMIT", "/params/0/value", "600", "LEFT", "O1", "statement",
                 "linked", "declared_not_semantically_verified", "not_proven",
                 "declared_only", "not_reviewed", "не семантическим покрытием"):
        assert text in page, text
    assert f"href='/runs/{run.name}/obligations'" in page
    assert _raw_tree(run) == before


@pytest.mark.parametrize("status", ["disputed", "open_question"])
def test_current_disputed_or_open_link_is_not_presented_as_linked(local, status):
    webui, run, ledger = local
    ledger["links"][0]["status"] = status
    (run / ob.LEDGER_REL).write_bytes(_json(ledger))
    before = _raw_tree(run)
    view = _get(local)["obligations"]
    assert view["status"] == "current", view
    assert len(view["fields"]) == 1
    assert view["fields"][0]["links"][0]["status"] == status
    page = webui.run_form_md(run.name).body.decode()
    assert status in page and "not_proven" in page
    assert _raw_tree(run) == before


@pytest.mark.parametrize("damage,expected", [
    ("missing", "not_recorded"), ("invalid_json", "unavailable"),
    ("invalid_form", "unavailable"), ("model_revision", "stale_or_unresolved")])
def test_missing_invalid_or_stale_evidence_is_not_an_empty_green_form(local, damage, expected):
    webui, run, ledger = local
    path = run / ob.LEDGER_REL
    if damage == "missing":
        path.unlink()
    elif damage == "invalid_json":
        path.write_bytes(b'{"schema_version":"x","schema_version":"y"}')
    elif damage == "invalid_form":
        ledger["links"][0]["targets"][0]["pointer"] = "/no-such-field"
        path.write_bytes(_json(ledger))
    else:
        model_path = run / "extract" / "model.json"
        revised = model.loads_model(model_path.read_bytes())
        revised["rules"][0]["params"][0]["value"] = 610
        model.dump_model(revised, model_path)
    before = _raw_tree(run)
    view = _get(local)["obligations"]
    assert view["status"] == expected, view
    assert view["fields"] == [] and view["meaning"] == "not_proven"
    assert view["issues"], view
    page = webui.run_form_md(run.name).body.decode()
    assert expected in page
    assert "Это не подтверждение отсутствия обязательств" in page
    assert _raw_tree(run) == before


def test_form_and_backlinks_use_the_same_raw_model_snapshot(local, monkeypatch):
    webui, run, _ = local
    path = run / "extract" / "model.json"
    original_raw = path.read_bytes()
    original_doc = model.loads_model(original_raw)
    newer = model.loads_model(original_raw)
    newer["rules"][0]["params"][0]["value"] = 610
    newer_raw = _json(newer)
    real_read = webui._file_replace.read_bytes_shared
    reads = []

    def replace_after_read(actual):
        raw = real_read(actual)
        if Path(actual) == path:
            reads.append(raw)
            path.write_bytes(newer_raw)
        return raw

    # A filesystem transport race only; model/report/projection judges stay real.
    monkeypatch.setattr(webui._file_replace, "read_bytes_shared", replace_after_read)
    view = _get(local)
    assert reads == [original_raw], "The form must not assemble different model snapshots"
    assert view["form"] == vf.to_form(original_doc)
    assert view["obligations"]["model_sha256"] == hashlib.sha256(original_raw).hexdigest()
    assert view["obligations"]["fields"][0]["value"] == 600
    assert path.read_bytes() == newer_raw


def test_new_field_view_escapes_imported_values_and_reader_ids(local):
    webui, run, ledger = local
    attack = '<img src=x onerror="not-executed"> & <script>not-executed</script>'
    path = run / "extract" / "model.json"
    doc = model.loads_model(path.read_bytes())
    doc["rules"][0]["statement"] = attack
    model.dump_model(doc, path)
    # Explicit synthetic authorship of a new bound fixture, not product rebinding.
    ledger["snapshot"] = ob.capture_run_snapshot(run)
    for reader in ledger["readers"]:
        reader["input_sha256"] = ob.fingerprint(ledger["snapshot"])
    ledger["readers"][0]["id"] = attack
    _reseal(ledger)
    link = ledger["links"][0]
    link["reader"] = attack
    link["model_sha256"] = ledger["snapshot"]["model_sha256"]
    link["targets"][0]["pointer"] = "/statement"
    (run / ob.LEDGER_REL).write_bytes(_json(ledger))
    before = _raw_tree(run)
    view = _get(local)["obligations"]
    assert view["status"] == "current", view
    assert view["fields"][0]["value"] == attack
    assert view["fields"][0]["links"][0]["reader"] == attack
    page = webui.run_form_md(run.name).body.decode()
    # Only the new A12 HTML is in this change, not the legacy Markdown renderer.
    section = page.split("<section id='obligation-field-evidence'>", 1)[1]
    assert attack not in section and "<script>" not in section and "<img src=x" not in section
    assert escape(attack, quote=True) in section
    assert _raw_tree(run) == before


def test_model_read_oserror_is_addressed_without_publication(local, monkeypatch):
    from fastapi import HTTPException
    webui, run, _ = local
    before = _raw_tree(run)

    def fail(actual):
        assert Path(actual) == run / "extract" / "model.json"
        raise PermissionError("synthetic model read denied")

    monkeypatch.setattr(webui._file_replace, "read_bytes_shared", fail)
    for endpoint in (webui.run_form, webui.run_form_md):
        with pytest.raises(HTTPException) as refused:
            endpoint(run.name)
        assert refused.value.status_code == 503
        assert "synthetic model read denied" in refused.value.detail
    assert _raw_tree(run) == before
