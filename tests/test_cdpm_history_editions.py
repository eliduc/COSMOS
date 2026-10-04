"""A04: published history questions, synthetic data, no services."""
import copy

from cdpm import answers, history_questions as hq, history_section as hs
from cdpm import model as m, question_editions as qe
from test_cdpm_history_1_17_2 import (
    _doc_with_history_rule, _book, H01_JOURNAL, Q_BOUNDARY,
)


def _published():
    doc = _doc_with_history_rule()
    assert hq.ensure_history_questions(doc, _book()) == [Q_BOUNDARY]
    q = next(x for x in doc["questions"] if x["id"] == Q_BOUNDARY)
    qe.freeze(q)
    return doc, q


def _latest(doc):
    superseded = {q.get("edition_of") for q in doc["questions"]}
    return next(q for q in doc["questions"] if hs.is_history_question(q["id"])
                and q["id"] not in superseded)


def test_coverage_change_preserves_answer_and_refreshes_option_meaning():
    doc, q = _published()
    assert answers.apply_answers(doc, {q["id"]: "б"})["applied"] == 1
    before = copy.deepcopy(q)
    book = _book(journal_rows=H01_JOURNAL)
    assert hq.ensure_history_questions(doc, book) == [Q_BOUNDARY + "-E2"]
    new = _latest(doc)
    assert q == before
    assert new["history"]["coverage"] == "partial"
    assert "частичным" in new["answer_format"]["options"][1]["text"]
    assert not hs.history_decision_of(doc)
    assert hs.history_section(book, doc)["user_decision"] is None
    assert answers.apply_answers(doc, {new["id"]: "б"})["applied"] == 1
    assert hs.history_section(book, doc)["user_decision"] == {
        "question": new["id"], "key": "б", "decision": "continue_partial"}
    assert hq.ensure_history_questions(doc, book) == []
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_scope_growth_needs_own_answer_even_at_same_coverage():
    doc, q = _published()
    answers.apply_answers(doc, {q["id"]: "б"})
    before = copy.deepcopy(q)
    rule = copy.deepcopy(next(r for r in doc["rules"] if r["id"] == "R-H-REST"))
    rule["id"] = "R-H-EXTRA"
    doc["rules"].append(rule)
    assert hq.ensure_history_questions(doc, _book()) == [Q_BOUNDARY + "-E2"]
    new = _latest(doc)
    assert q == before
    assert new["edition"]["targets"] == ["R-H-EXTRA", "R-H-REST"]
    assert "answer" not in new
    assert hs.history_decision_of(doc) is None
    assert hq.ensure_history_questions(doc, _book()) == []


def test_full_history_and_return_do_not_restore_old_zero_decision():
    doc, q = _published()
    answers.apply_answers(doc, {q["id"]: "б"})
    before = copy.deepcopy(q)
    full = _book(history_rows=[{"Табельный номер": eid,
        "Налёт с начала месяца, мин": "60"} for eid in ("H01", "H02", "H03")])
    assert hs.history_section(full, doc)["coverage"]["kind"] == "full"
    hq.ensure_history_questions(doc, full)
    closed = copy.deepcopy(_latest(doc))
    assert q == before
    assert closed["answer"]["raw"] == hq.CODE_CLOSED_RAW
    assert not m.is_open_question(closed)
    assert hq.ensure_history_questions(doc, full) == []
    assert hq.ensure_history_questions(doc, _book()) == [Q_BOUNDARY + "-E3"]
    assert next(x for x in doc["questions"] if x["id"] == closed["id"]) == closed
    assert q == before
    assert m.is_open_question(_latest(doc))
    assert hs.history_decision_of(doc) is None
    assert hq.ensure_history_questions(doc, _book()) == []
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_stale_open_edition_is_refused_without_changing_it():
    doc, q = _published()
    before = copy.deepcopy(q)
    new = qe.expand(doc, q, [doc["rules"][0]["id"]])
    result = answers.apply_answers(doc, {q["id"]: "б"})
    assert result["applied"] == 0
    assert result["received"] == 1
    assert "переиздан" in result["refused"][0]["reason"]
    assert q == before
    assert "answer" not in new


def test_text_change_same_coverage_is_reissued_and_not_repeated():
    doc, q = _published()
    before = copy.deepcopy(q)
    book = _book(crew=("H01", "H02", "H03", "H04"))
    assert hq.ensure_history_questions(doc, book) == [Q_BOUNDARY + "-E2"]
    assert q == before
    assert "4 сотрудников" in _latest(doc)["text"]
    assert hq.ensure_history_questions(doc, book) == []


def test_no_history_rules_retires_without_erasing_human_answer():
    doc, q = _published()
    answers.apply_answers(doc, {q["id"]: "б"})
    before = copy.deepcopy(q)
    next(r for r in doc["rules"] if r["id"] == "R-H-REST")["kind"] = "OUT"
    hq.ensure_history_questions(doc, _book())
    assert q == before
    assert hs.history_decision_of(doc) is None
    assert not m.is_open_question(_latest(doc))
    assert hq.ensure_history_questions(doc, _book()) == []


def test_history_metadata_tampering_cannot_change_decision_meaning():
    doc, q = _published()
    answers.apply_answers(doc, {q["id"]: "б"})
    original = qe.fingerprint(q)
    assert hs.history_decision_of(doc)["decision"] == "continue_zero"
    q["history"]["coverage"] = "partial"
    assert any("history" in p for p in qe.problems(q))
    assert hs.history_decision_of(doc) is None
    other = copy.deepcopy(q)
    other.pop("edition")
    qe.freeze(other)
    assert qe.fingerprint(other) != original


def test_superseded_unanswered_question_does_not_hold_readiness():
    doc = _doc_with_history_rule()
    base = m.readiness(doc)
    matrix = m.readiness_matrix(doc)
    hq.ensure_history_questions(doc, _book())
    q = next(x for x in doc["questions"] if x["id"] == Q_BOUNDARY)
    qe.freeze(q)
    hq.ensure_history_questions(doc, _book(crew=("H01", "H02")))
    new = _latest(doc)
    assert new["id"] != q["id"] and "answer" not in q
    answers.apply_answers(doc, {new["id"]: "б"})
    assert m.readiness(doc) == base
    assert m.readiness_matrix(doc) == matrix


def test_old_answer_is_named_in_both_publications():
    from cdpm import anketa_xlsx as ax, render
    doc, q = _published()
    answers.apply_answers(doc, {q["id"]: "б"})
    hq.ensure_history_questions(doc, _book(journal_rows=H01_JOURNAL))
    note = "ответ «б» применён к изданию 1"
    assert note in render.render_anketa(doc)
    rows = [row for _, rows in ax.sheet_plan(doc) for row in rows]
    assert note in next(row[4] for row in rows if row[0] == _latest(doc)["id"])
