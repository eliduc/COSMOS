"""Rebinding an in-scope soft row is not a new question edition."""
import copy

import pytest

from cdpm import model as m, question_editions as qe
from test_cdpm_goal_editions import _goal


def test_in_scope_soft_row_rebinds_without_an_identical_edition():
    doc, q = _goal()
    row = next(r for r in doc["rules"] if r["id"] == "R-S")
    row.pop("blockers")
    old = copy.deepcopy(q)
    old_ids = [x["id"] for x in doc["questions"]]
    assert m.ensure_goal_question(doc, "3a") == q["id"]
    assert q == old and [x["id"] for x in doc["questions"]] == old_ids
    assert row["blockers"] == [q["id"]]
    stable = copy.deepcopy(doc)
    assert m.ensure_goal_question(doc, "3a") == q["id"]
    assert doc == stable
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_queued_answer_keeps_dedicated_weights_path_and_saved_answer():
    doc, q = _goal()
    row = next(r for r in doc["rules"] if r["id"] == "R-S")
    row.pop("blockers")
    q["answer"] = {"raw": "Уточняем веса", "status": "ожидает обработчика", "revision": 1}
    old = copy.deepcopy(q)
    qid = m.ensure_goal_question(doc, "3a")
    assert qid == "Q-3a-GOAL-W"
    assert q == old and row["blockers"] == [qid]
    assert len([x for x in doc["questions"] if x["id"] == qid]) == 1


def test_in_scope_rebind_with_new_normative_source_still_reissues():
    doc, q = _goal()
    row = next(r for r in doc["rules"] if r["id"] == "R-S")
    row.pop("blockers")
    source = {"sid": "S1", "anchor": "п.новая-норма"}
    row["sources"].append(source)
    old = copy.deepcopy(q)
    qid = m.ensure_goal_question(doc, "3a")
    assert qid == "Q-3a-GOAL-E2" and q == old
    new = next(x for x in doc["questions"] if x["id"] == qid)
    assert source in new["source_refs"] and row["blockers"] == [qid]
    assert qe.lineage_root(doc, new) == q["id"]


def test_identical_explicit_edition_is_still_refused_atomically():
    doc, q = _goal()
    before = copy.deepcopy(doc)
    with pytest.raises(qe.EditionRefusal, match="неизменённый вопрос"):
        qe.expand(doc, q, q["rule_refs"], content={"text": q["text"]})
    assert doc == before
