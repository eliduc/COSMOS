"""A04 minor: distinguish historical answer notes from current code decisions."""
import copy

import pytest

from cdpm import answers, model as m, question_resolutions as qr
from test_cdpm_edition_scope_display import shown
from test_cdpm_missing_data_editions import initial, ran, _positive_ground


HISTORY = "Историческая заметка к прежнему ответу (не текущее состояние): "


@pytest.mark.parametrize("answer", ["а", "б"])
@pytest.mark.parametrize("ground", ["present", "coverage_gap", "executor"])
@pytest.mark.parametrize("surface", ["md", "xlsx"])
def test_closed_answer_note_is_explicitly_historical(tmp_path, answer, ground, surface):
    doc, q, root = initial()
    answers.apply_answers(doc, {root: answer})
    prior = copy.deepcopy(q["answer"])
    assert prior["note"]
    m.ensure_missing_data_questions(doc, _positive_ground(doc, ground))
    assert qr.complete(q) and not qr.problems(doc, q)
    snapshot = copy.deepcopy(doc)
    text = shown(doc, q, surface, tmp_path)
    assert HISTORY + prior["note"] in text
    assert "снят кодом" in text
    assert qr.notes(q, doc=doc)[0] in text
    assert q["answer"] == prior and doc == snapshot
    display = qr.display_answer(q, doc=doc)
    assert display["key"] == prior["key"] and display["raw"] == prior["raw"]
    assert display["revision"] == prior["revision"]
    assert display["note"] == HISTORY + prior["note"]


@pytest.mark.parametrize("answer", ["а", "б"])
@pytest.mark.parametrize("retired", [False, True])
def test_incomplete_resolution_keeps_current_answer_note(answer, retired):
    doc, q, root = initial("R-A", "R-B")
    answers.apply_answers(doc, {root: answer})
    if retired:
        m.ensure_missing_data_questions(doc, ran("R-A"))
    assert not qr.complete(q)
    snapshot = copy.deepcopy(doc)
    assert qr.display_answer(q, doc=doc) == q["answer"]
    assert doc == snapshot


@pytest.mark.parametrize("answer", ["а", "б"])
@pytest.mark.parametrize("damage", ["shape", "live_blocker", "changed_statement"])
def test_invalid_resolution_cannot_relabel_current_note_as_history(answer, damage):
    doc, q, root = initial()
    answers.apply_answers(doc, {root: answer})
    m.ensure_missing_data_questions(doc, ran("R-D"))
    if damage == "shape":
        q["code_resolutions"][0]["revision"] = 0
    elif damage == "live_blocker":
        doc["rules"][0]["blockers"] = [root]
    else:
        doc["rules"][0]["statement"] += " — изменённая норма"
    assert qr.problems(doc, q)
    snapshot = copy.deepcopy(doc)
    assert qr.display_answer(q, doc=doc) == q["answer"]
    assert doc == snapshot


@pytest.mark.parametrize("answer", ["а", "б"])
def test_without_current_model_the_note_is_not_relabelled(answer):
    doc, q, root = initial()
    answers.apply_answers(doc, {root: answer})
    m.ensure_missing_data_questions(doc, ran("R-D"))
    assert qr.display_answer(q)["note"] == q["answer"]["note"]
