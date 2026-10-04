"""COSMOS 3293a633: actual worker conflict reaches all question surfaces."""
import copy

import pytest

from cdpm import answer_feedback, answers, anketa_xlsx, family_params as fp
from cdpm import model as m, render
from test_cdpm_edition_revision_integration import _expanded_answer, _pending_reply, _q, OTHER, PARAM
from test_cdpm_family_params import QID


def worker_conflict_document(tmp_path, *, conflict=True):
    doc, ids, _ = _expanded_answer(tmp_path)
    first = next(r for r in doc["rules"] if r["id"] == ids[0])
    first["template_fit"]["params"][OTHER]["quote"] = "поздняя правка дома"
    rid = answers.open_revision(doc, QID + "-E2")["created"]
    assert answers.apply_answers(doc, {rid: "540 минут"}, None)["partial"] == 1
    reply = _pending_reply(doc, rid)
    if conflict:
        pending = answers._affected_rules(doc, _q(doc, rid))[0]
        pending["template_fit"]["params"][PARAM] = {
            "value": 660, "quote": "позднее согласовано 660 минут"}
    result = answers.apply_worker_patches(doc, lambda prompt: reply)
    assert result["worker_applied"] == (0 if conflict else 1)
    assert bool(result["worker_rejected"]) is conflict
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    return doc, rid


@pytest.mark.parametrize("surface", ["anketa", "svod", "xlsx"])
def test_worker_conflict_reason_and_action_are_visible(tmp_path, surface):
    doc, rid = worker_conflict_document(tmp_path)
    q = _q(doc, rid)
    before = copy.deepcopy(q)
    if surface == "xlsx":
        row = anketa_xlsx.question_row(q, {"S1": "synthetic.md"}, doc=doc)
        text = row[1]
        assert row[5] is None
    else:
        text = render.render_anketa(doc) if surface == "anketa" else render.render_svod(doc)
    for reason in q["answer"]["conflicts"]:
        assert reason in text
    assert q["answer"]["processing_note"] in text
    assert "540 минут" in text
    assert q == before


def test_healthy_continuation_does_not_add_conflict_advice(tmp_path):
    doc, rid = worker_conflict_document(tmp_path, conflict=False)
    q = _q(doc, rid)
    assert q["answer"]["status"] == "применён"
    assert answer_feedback.lines(q, doc=doc) == []
    assert "Причина конфликта:" not in render.render_svod(doc)
