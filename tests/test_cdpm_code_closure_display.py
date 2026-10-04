"""COSMOS31e8: code closures must not read as accepted airline answers."""
import copy

import pytest

from cdpm import answers, anketa_xlsx as ax, model as m, render
from test_cdpm_question_editions import _doc


@pytest.mark.parametrize("key", [None, "неприменимо"])
def test_code_closure_status_and_reason_are_visible_without_mutation(key):
    doc, question = _doc()
    m.close_question_by_code(doc, question, raw="снято кодом: правило отменено",
                             note="Носитель отменён подтверждённой правкой", key=key)
    before = copy.deepcopy(doc)
    text = render.render_anketa(doc)
    assert "## Принятые ответы и решения кода" in text
    row = next(line for line in text.splitlines() if line.startswith("| " + question["id"] + " —"))
    assert "| снят кодом |" in row
    assert "Носитель отменён подтверждённой правкой" in row
    xrow = next(r for r in ax.answered_rows(doc) if r[0] == question["id"])
    assert xrow[3] == "снят кодом"
    assert "Носитель отменён подтверждённой правкой" in xrow[2]
    assert doc == before


def test_human_code_looking_text_is_not_relabelled_as_code():
    doc, question = _doc()
    answers.apply_answers(doc, {question["id"]: "снят кодом: правило отменено"}, None)
    before = copy.deepcopy(doc)
    # Whatever processing status the genuine received answer has, display
    # cannot classify its author from these words.
    from cdpm import question_resolutions
    assert question_resolutions.display_answer(question, doc=doc)["status"] == question["answer"]["status"]
    assert "решения кода" not in render.render_anketa(doc)
    assert doc == before
