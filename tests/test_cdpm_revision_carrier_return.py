"""K04 disappearance/return through publication, not a constructed end state."""
import copy

import pytest

from cdpm import answers, family_params as fp, model as m, question_editions as qe
from test_cdpm_edition_revision_integration import _q, _sync_reload
from test_cdpm_k04_revision_barrier import (
    _doc_after_conflicting_revision, PARAM, OTHER_PARAM, ROOT_Q, REV_Q,
)


def _return_once(doc, tmp_path, rid, revision_id):
    row = next(r for r in doc["rules"] if r["id"] == rid)
    original = copy.deepcopy(row)
    doc = _sync_reload(doc, tmp_path / "run")
    row = next(r for r in doc["rules"] if r["id"] == rid)
    assert m.is_open_question(_q(doc, revision_id))
    row.clear()
    row.update({"id": rid, "title": "Отменено", "statement": "Требование отменено",
                "kind": "OUT", "zone": "ROSTERING", "note": "отменено человеком",
                "sources": copy.deepcopy(original["sources"])})
    doc = _sync_reload(doc, tmp_path / "run")
    closed = copy.deepcopy(_q(doc, revision_id))
    assert closed["answer"]["closed_by"] == "code"
    assert closed["answer"]["close_reason"] == m.CODE_CLOSE_OUTCOME
    assert m.pending_revision_facts(doc) == []
    row = next(r for r in doc["rules"] if r["id"] == rid)
    returned = copy.deepcopy(original)
    returned["template_fit"]["params"].pop(PARAM)
    returned["blockers"] = [qid for qid in returned.get("blockers", []) if qid != revision_id]
    row.clear()
    row.update(returned)
    created = fp.ensure_family_param_questions(doc)
    assert len(created) == 1
    new = _q(doc, created[0])
    assert new["id"] not in (ROOT_Q, revision_id)
    assert new["edition_of"] == closed["revision_of"]
    assert revision_id in new["text"]
    assert fp.question_target(new) == ("rest_floors", PARAM)
    assert m.is_open_question(new) and "answer" not in new
    assert _q(doc, revision_id) == closed
    assert PARAM not in row["template_fit"]["params"]
    assert new["id"] in row["blockers"]
    assert fp.ensure_family_param_questions(doc) == []
    doc = _sync_reload(doc, tmp_path / "run")
    assert _q(doc, revision_id) == closed
    assert answers.apply_answers(doc, {new["id"]: "540 минут"}, None)["applied"] == 1
    row = next(r for r in doc["rules"] if r["id"] == rid)
    assert row["template_fit"]["params"][PARAM]["value"] == 540
    assert row["template_fit"]["params"][PARAM]["source"]["question"]["id"] == new["id"]
    assert row["template_fit"]["params"][OTHER_PARAM] == original["template_fit"]["params"][OTHER_PARAM]
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    return doc, new["id"], closed


def test_code_closed_revision_reissues_when_its_carrier_returns(tmp_path):
    doc, row = _doc_after_conflicting_revision()
    _return_once(doc, tmp_path, row["id"], REV_Q)


def test_repeated_returns_follow_latest_edition_not_first_closed_revision(tmp_path):
    doc, row = _doc_after_conflicting_revision()
    rid, revision_id = row["id"], REV_Q
    history = []
    for cycle in range(3):
        doc, active_id, closed = _return_once(doc, tmp_path, rid, revision_id)
        history.append(closed)
        for old in history:
            assert _q(doc, old["id"]) == old
        if cycle < 2:
            row = next(r for r in doc["rules"] if r["id"] == rid)
            row["template_fit"]["params"][OTHER_PARAM]["quote"] = f"новая правка соседа {cycle}"
            revision_id = answers.open_revision(doc, active_id)["created"]


def test_active_revision_still_cannot_be_rebased_as_an_edition(tmp_path):
    doc, _ = _doc_after_conflicting_revision()
    doc = _sync_reload(doc, tmp_path / "run")
    q = _q(doc, REV_Q)
    before = copy.deepcopy(doc)
    with pytest.raises(qe.EditionRefusal, match="снимка ячеек"):
        qe.expand(doc, q, q["rule_refs"], content={"text": "Иной текст"})
    assert doc == before
