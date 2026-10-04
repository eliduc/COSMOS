"""A04 GOAL: scope writer, answer routing and readiness share edition identity."""
import copy
import json

import pytest

from cdpm import answers, model as m, question_editions as qe
from test_cdpm_objectives import _base_doc


def _goal(soft=True):
    doc = _base_doc()
    if soft:
        doc["rules"].append({"id": "R-S", "title": "Ночёвки вне базы",
            "statement": "penalty(overnight)", "zone": "PAIRING", "kind": "SOFT",
            "sources": [{"sid": "S1", "anchor": "п.2"}]})
    qid = m.ensure_goal_question(doc, "3a")
    q = next(x for x in doc["questions"] if x["id"] == qid)
    qe.freeze(q)
    return doc, q


def _extra(doc, rid):
    other = copy.deepcopy(next(x for x in doc["rules"] if x["id"] == "R-S"))
    other.update(id=rid, sources=[{"sid": "S1", "anchor": "п." + rid}])
    other.pop("blockers", None)
    doc["rules"].append(other)
    return other


def test_goal_scope_growth_reissues_and_moves_all_live_blockers():
    doc, q = _goal()
    old = copy.deepcopy(q)
    other = _extra(doc, "R-S2")
    qid = m.ensure_goal_question(doc, "3a")
    assert qid == "Q-3a-GOAL-E2"
    new = next(x for x in doc["questions"] if x["id"] == qid)
    assert q == old
    assert new["edition_of"] == q["id"]
    assert set(new["rule_refs"]) == {"R-S", "R-S2"}
    assert other["sources"][0] in new["source_refs"]
    for r in doc["rules"]:
        if r["kind"] == "SOFT":
            assert r["blockers"] == [qid]
    assert m.ensure_goal_question(doc, "3a") == qid
    assert m.validate_model(doc) == []
    _extra(doc, "R-S3")
    assert m.ensure_goal_question(doc, "3a") == "Q-3a-GOAL-E3"
    assert qe.problems(q) + qe.problems(new) == []


def test_goal_new_normative_source_reissues_without_new_soft_candidate():
    doc, q = _goal()
    old = copy.deepcopy(q)
    extra = {"sid": "S1", "anchor": "п.9"}
    next(x for x in doc["rules"] if x["id"] == "R-S")["sources"].append(extra)
    qid = m.ensure_goal_question(doc, "3a")
    assert qid == "Q-3a-GOAL-E2"
    assert q == old
    assert extra in next(x for x in doc["questions"] if x["id"] == qid)["source_refs"]
    assert m.ensure_goal_question(doc, "3a") == qid


@pytest.mark.parametrize("qid,want", [
    ("Q-3a-GOAL-E2", True), ("Q-3b-GOAL-E12", True),
    ("Q-3a-GOAL-T-2-E3", True), ("Q-3a-GOAL-W-E2", False),
    ("Q-3a-GOAL-E1", False), ("Q-3a-GOAL-EXTRA", False),
])
def test_goal_edition_routing_does_not_capture_weight_or_arbitrary_ids(qid, want):
    assert m.is_goal_family(qid) is want


def test_goal_edition_does_not_block_feasibility_and_latest_decline_is_seen():
    doc, q = _goal(soft=False)
    new = qe.expand(doc, q, [], content={"text": q["text"] + " Уточнение."})
    assert m.readiness(doc)["state"] == "в"
    assert m.readiness_matrix(doc)["pairing"]["feasibility-hard"]["ready"]
    new["answer"] = {"raw": "любое допустимое решение", "status": "применён",
                     "revision": 2, "goal_decline": True}
    assert m.readiness_matrix(doc)["pairing"]["optimization"]["state"] == "отказ подтверждён"
    assert m.ensure_criterion_question(doc) == []


def test_goal_e2_real_answer_uses_goal_worker_and_preserves_old_question():
    doc, q = _goal()
    old = copy.deepcopy(q)
    _extra(doc, "R-S2")
    # Isolate routing from the writer: the separate writer test covers this transfer.
    new = qe.expand(doc, q, ["R-S2"])
    for r in doc["rules"]:
        if r["kind"] == "SOFT":
            r["blockers"] = [new["id"]]
    assert answers.apply_answers(doc, {new["id"]: "Оптимизация не нужна; веса 10"})["applied"] == 0
    def llm(prompt):
        assert "Q-3a-GOAL-E2" in prompt
        assert "decline" in prompt
        return json.dumps({"decline": True, "objectives": [], "weights": [
            {"rule": rid, "weight": 10, "as_written": "10", "statement": "penalty = 10"}
            for rid in ("R-S", "R-S2")]})
    assert answers.apply_worker_patches(doc, llm) == {"worker_applied": 1, "worker_rejected": []}
    assert q == old
    assert new["answer"]["status"] == "применён"
    assert m.readiness_matrix(doc)["pairing"]["optimization"]["state"] == "отказ подтверждён"
    assert all(not r.get("blockers") for r in doc["rules"])


def test_new_normative_clause_after_applied_goal_requires_fresh_confirmation():
    doc, q = _goal(soft=False)
    q["answer"] = {"raw": "минимизировать стоимость", "status": "применён",
                   "revision": 1, "goal_decline": False}
    doc["objectives"] = [{"id": "O-1", "title": "Стоимость",
        "statement": "minimize cost", "zone": "PAIRING", "direction": "minimize",
        "metric": "total_pairing_cost", "sources": [{"sid": "S1", "anchor": "п.1"}]}]
    assert m.readiness_matrix(doc)["pairing"]["optimization"]["ready"]
    old = copy.deepcopy(q)
    doc["rules"][0]["sources"].append({"sid": "S1", "anchor": "п.9"})
    qid = m.ensure_goal_question(doc, "3a")
    assert qid == "Q-3a-GOAL-E2"
    assert q == old
    assert m.ensure_goal_question(doc, "3a") == qid
    matrix = m.readiness_matrix(doc)["pairing"]
    assert matrix["feasibility-hard"]["ready"]
    assert not matrix["optimization"]["ready"]
    assert qid in matrix["optimization"]["note"]


def test_latest_goal_selection_is_not_list_order_and_rejects_fork():
    doc, q = _goal(soft=False)
    new = qe.expand(doc, q, [], content={"text": q["text"] + " Уточнение."})
    doc["questions"].reverse()
    assert qe.latest(doc, q) is new
    fork = copy.deepcopy(new)
    fork["id"] += "-other"
    doc["questions"].append(fork)
    with pytest.raises(qe.EditionRefusal, match="разветвлена"):
        m.ensure_goal_question(doc, "3a")
    doc["objectives"] = [{"id": "O-1", "title": "Стоимость",
        "statement": "minimize cost", "zone": "PAIRING", "direction": "minimize",
        "metric": "total_pairing_cost", "sources": [{"sid": "S1", "anchor": "п.1"}]}]
    assert not m.readiness_matrix(doc)["pairing"]["optimization"]["ready"]


def test_new_source_does_not_reuse_previously_applied_soft_weight():
    doc, q = _goal()
    q["answer"] = {"raw": "любое допустимое; вес 10", "status": "применён",
                   "revision": 1, "goal_decline": True}
    soft = next(r for r in doc["rules"] if r["id"] == "R-S")
    soft.update(weight=10, statement="penalty = 10")
    soft.pop("blockers")
    old = copy.deepcopy(q)
    soft["sources"].append({"sid": "S1", "anchor": "п.9"})
    qid = m.ensure_goal_question(doc, "3a")
    assert qid == "Q-3a-GOAL-E2"
    assert q == old and soft["weight"] == 10
    assert soft["blockers"] == [qid]
    assert not m.readiness_matrix(doc)["pairing"]["feasibility-soft"]["ready"]
    assert m.ensure_goal_question(doc, "3a") == qid
    answers.apply_answers(doc, {qid: "оптимизация не нужна; вес 20"})
    def llm(prompt):
        assert qid in prompt and "R-S" in prompt
        return json.dumps({"decline": True, "objectives": [], "weights": [
            {"rule": "R-S", "weight": 20, "as_written": "20", "statement": "penalty = 20"}]})
    assert answers.apply_worker_patches(doc, llm) == {"worker_applied": 1, "worker_rejected": []}
    assert soft["weight"] == 20 and not soft.get("blockers")
    assert q == old
    assert m.readiness_matrix(doc)["pairing"]["feasibility-soft"]["ready"]


def test_superseded_pending_goal_does_not_call_worker_or_apply_old_answer():
    doc, q = _goal(soft=False)
    answers.apply_answers(doc, {q["id"]: "любое допустимое решение"})
    qe.expand(doc, q, [], content={"text": q["text"] + " Новые условия."})
    before = copy.deepcopy(doc)
    def forbidden_llm(prompt):
        pytest.fail("superseded answer must be refused before invoking a worker")
    result = answers.apply_worker_patches(doc, forbidden_llm)
    # A retired queue entry is history, not a fresh worker failure. The
    # application guard still refuses it explicitly if addressed directly.
    assert result == {"worker_applied": 0, "worker_rejected": []}
    assert "переиздан" in qe.application_problems(doc, q)[0]
    assert answers.remaining_awaiting(doc) == 0
    assert doc == before


def test_goal_reissued_during_worker_call_is_not_committed():
    doc, q = _goal(soft=False)
    answers.apply_answers(doc, {q["id"]: "любое допустимое решение"})
    old = copy.deepcopy(q)
    def llm(prompt):
        qe.expand(doc, q, [], content={"text": q["text"] + " Новые условия."})
        return json.dumps({"decline": True, "objectives": [], "weights": []})
    result = answers.apply_worker_patches(doc, llm)
    assert result["worker_applied"] == 0
    assert "переиздан" in result["worker_rejected"][0]["reasons"][0]
    assert q == old and len(doc["questions"]) == 2


def test_late_soft_after_published_applied_goal_keeps_weights_path():
    doc, q = _goal()
    q["answer"] = {"raw": "любое допустимое; вес 10", "status": "применён",
                   "revision": 1, "goal_decline": True}
    soft = next(r for r in doc["rules"] if r["id"] == "R-S")
    soft.update(weight=10, statement="penalty = 10")
    soft.pop("blockers")
    old = copy.deepcopy(q)
    late = _extra(doc, "R-LATE")
    late.pop("weight")
    late["statement"] = "penalty(overnight)"
    assert m.ensure_goal_question(doc, "3a") == "Q-3a-GOAL-W"
    assert late["blockers"] == ["Q-3a-GOAL-W"]
    assert not soft.get("blockers") and q == old


def test_goal_worker_refuses_foreign_blocker_without_invoking_llm():
    doc, q = _goal()
    answers.apply_answers(doc, {q["id"]: "оптимизация не нужна; вес 10"})
    foreign = _extra(doc, "R-FOREIGN")
    foreign["blockers"] = [q["id"]]
    before = copy.deepcopy(doc)
    def forbidden_llm(prompt):
        pytest.fail("foreign scope must be rejected before invoking a worker")
    result = answers.apply_worker_patches(doc, forbidden_llm)
    assert result["worker_applied"] == 0
    assert "вне области" in result["worker_rejected"][0]["reasons"][0]
    assert doc == before
