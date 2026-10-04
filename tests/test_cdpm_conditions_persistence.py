"""Actual conditions endpoint persists changes even when no question is added."""
import copy

from fastapi.testclient import TestClient

from cdpm import answers, model as m, webui
from test_cdpm_edition_revision_integration import _q, _sync_reload
from test_cdpm_k04_revision_barrier import (
    _doc_after_conflicting_revision, PARAM, REV_Q,
)


def _prepared_run(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "settings.json"))
    run = tmp_path / "runs" / "run-9412"
    doc, row = _doc_after_conflicting_revision()
    rid = row["id"]
    doc = _sync_reload(doc, run)
    client = TestClient(webui.app)
    for _ in range(2):
        response = client.post("/api/runs/run-9412/conditions-questions")
        assert response.status_code == 200, response.text
    doc = m.load_model(run / "extract/model.json")
    assert m.is_open_question(_q(doc, REV_Q))
    return doc, rid, run, client


def test_conditions_persists_code_closure_without_creating_questions(tmp_path, monkeypatch):
    doc, rid, run, client = _prepared_run(tmp_path, monkeypatch)
    row = next(r for r in doc["rules"] if r["id"] == rid)
    original = copy.deepcopy(row)
    row.clear()
    row.update({"id": rid, "title": "Отменено", "statement": "Требование отменено",
                "kind": "OUT", "zone": "ROSTERING", "note": "отменено человеком",
                "sources": original["sources"]})
    m.derive_all_rule_traits(doc, "тест: законная отмена носителя")
    m.publish_model(doc, run / "extract/model.json")
    assert m.revision_carriers(doc, _q(doc, REV_Q)) == []
    assert m.is_open_question(_q(doc, REV_Q))

    response = client.post("/api/runs/run-9412/conditions-questions")
    assert response.status_code == 200, response.text
    assert response.json()["created"] == []
    after = m.load_model(run / "extract/model.json")
    closed = _q(after, REV_Q)
    assert closed.get("answer", {}).get("closed_by") == "code"
    assert closed["answer"]["close_reason"] == m.CODE_CLOSE_OUTCOME
    assert not m.is_open_question(closed)
    assert m.pending_revision_facts(after) == []
    assert m.validate_schema(after) + m.validate_model(after) == []

    history = copy.deepcopy(closed)
    returned = copy.deepcopy(original)
    returned["statement"] += " Новая редакция нормы после возврата."
    returned["template_fit"]["params"].pop(PARAM)
    returned["blockers"] = [q for q in returned.get("blockers", []) if q != REV_Q]
    row = next(r for r in after["rules"] if r["id"] == rid)
    row.clear()
    row.update(returned)
    m.publish_model(after, run / "extract/model.json")
    response = client.post("/api/runs/run-9412/conditions-questions")
    assert response.status_code == 200, response.text
    assert len(response.json()["created"]) == 1
    fresh_id = response.json()["created"][0]
    after = m.load_model(run / "extract/model.json")
    fresh = _q(after, fresh_id)
    assert fresh_id == history["revision_of"] + "-E2"
    assert "Новая редакция нормы после возврата." in fresh["text"]
    assert REV_Q in fresh["text"] and m.is_open_question(fresh)
    assert _q(after, REV_Q) == history
    row = next(r for r in after["rules"] if r["id"] == rid)
    assert PARAM not in row["template_fit"]["params"]
    assert fresh_id in row["blockers"]
    assert answers.apply_answers(after, {fresh_id: "540 минут"}, None)["applied"] == 1
    m.publish_model(after, run / "extract/model.json")
    response = client.post("/api/runs/run-9412/conditions-questions")
    assert response.status_code == 200, response.text
    after = m.load_model(run / "extract/model.json")
    cell = next(r for r in after["rules"] if r["id"] == rid)["template_fit"]["params"][PARAM]
    assert cell["value"] == 540 and cell["source"]["question"]["id"] == fresh_id
    assert _q(after, REV_Q) == history
    assert m.validate_schema(after) + m.validate_model(after) == []


def test_conditions_without_change_keeps_open_revision_and_model_bytes(tmp_path, monkeypatch):
    doc, rid, run, client = _prepared_run(tmp_path, monkeypatch)
    before = (run / "extract/model.json").read_bytes()
    response = client.post("/api/runs/run-9412/conditions-questions")
    assert response.status_code == 200, response.text
    assert response.json()["created"] == []
    assert (run / "extract/model.json").read_bytes() == before
    after = m.load_model(run / "extract/model.json")
    assert _q(after, REV_Q) == _q(doc, REV_Q)
    row = next(r for r in after["rules"] if r["id"] == rid)
    assert row["template_fit"]["params"][PARAM]["value"] == 600
    assert row["template_fit"]["params"][PARAM]["pending_revision"] == REV_Q


def test_conditions_persists_restored_value_without_creating_questions(tmp_path, monkeypatch):
    doc, rid, run, client = _prepared_run(tmp_path, monkeypatch)
    assert answers.apply_answers(doc, {REV_Q: "540 минут"}, None)["applied"] == 1
    row = next(r for r in doc["rules"] if r["id"] == rid)
    expected = copy.deepcopy(row["template_fit"]["params"].pop(PARAM))
    m.publish_model(doc, run / "extract/model.json")
    question = copy.deepcopy(_q(doc, REV_Q))
    response = client.post("/api/runs/run-9412/conditions-questions")
    assert response.status_code == 200, response.text
    assert response.json()["created"] == []
    after = m.load_model(run / "extract/model.json")
    row = next(r for r in after["rules"] if r["id"] == rid)
    assert row["template_fit"]["params"].get(PARAM) == expected
    assert _q(after, REV_Q) == question
    assert m.validate_schema(after) + m.validate_model(after) == []

    before = (run / "extract/model.json").read_bytes()
    response = client.post("/api/runs/run-9412/conditions-questions")
    assert response.status_code == 200 and response.json()["created"] == []
    assert (run / "extract/model.json").read_bytes() == before
