"""CDPM-409: a code decision is not a received answer on round-trip."""
import copy

from cdpm import answers, family_params as fp, model as m
from test_cdpm_edition_revision_integration import _q, _sync_reload
from test_cdpm_k04_revision_barrier import (
    _doc_after_conflicting_revision, PARAM, REV_Q,
)
from test_cdpm_question_editions import _doc


def test_repeated_carrier_disappearance_keeps_published_code_identity(tmp_path):
    doc, carrier = _doc_after_conflicting_revision()
    rid = carrier["id"]
    original = copy.deepcopy(carrier)
    doc = _sync_reload(doc, tmp_path / "run")
    history = []
    active_id = REV_Q
    for cycle in range(3):
        row = next(r for r in doc["rules"] if r["id"] == rid)
        row.clear()
        row.update({"id": rid, "title": "Отменено", "statement": "Требование отменено",
                    "kind": "OUT", "zone": "ROSTERING", "note": "отменено человеком",
                    "sources": copy.deepcopy(original["sources"])})
        doc = _sync_reload(doc, tmp_path / "run")
        closed = copy.deepcopy(_q(doc, active_id))
        assert closed["answer"]["closed_by"] == "code"
        assert "answer_version" not in closed["answer"]
        assert "parsed" not in closed["answer"]
        history.append(closed)
        row = next(r for r in doc["rules"] if r["id"] == rid)
        returned = copy.deepcopy(original)
        returned["template_fit"]["params"].pop(PARAM)
        returned["blockers"] = [qid for qid in returned.get("blockers", []) if qid != REV_Q]
        row.clear()
        row.update(returned)
        created = fp.ensure_family_param_questions(doc)
        assert len(created) == 1
        active_id = created[0]
        doc = _sync_reload(doc, tmp_path / "run")
        assert m.is_open_question(_q(doc, active_id))
        for previous in history:
            assert _q(doc, previous["id"]) == previous


def test_ordinary_code_decision_is_identical_after_publication_and_read(tmp_path):
    doc, question = _doc()
    m.close_question_by_code(doc, question, raw="Больше не требуется",
                             note="Все относящиеся к вопросу строки сняты")
    assert question["answer"]["closed_by"] == "code"
    assert "close_reason" not in question["answer"]
    assert "answer_version" not in question["answer"]
    m.publish_model(doc, tmp_path / "model.json")
    assert m.load_model(tmp_path / "model.json") == doc


def test_human_text_looking_like_code_is_still_a_received_answer(tmp_path):
    doc, question = _doc()
    raw = "снят кодом: параметр семьи задан сводом"
    answers.apply_answers(doc, {question["id"]: raw}, None)
    assert question["answer"]["raw"] == raw
    assert "closed_by" not in question["answer"]
    assert question["answer"]["answer_version"]["received_at"] is not None
    m.publish_model(doc, tmp_path / "model.json")
    assert m.load_model(tmp_path / "model.json") == doc
