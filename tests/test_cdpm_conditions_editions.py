"""A04: automatic conditions scope and its readers share the current edition."""
import copy
import re

import pytest

from cdpm import answers, model as m, question_editions as qe
from vmp import conditions_export as ce


ROOT = "Q-3a-условия-синт-отдых"


@pytest.fixture
def topics(monkeypatch):
    rows = [("условия-синт-отдых", re.compile("отдых"), "Отдых",
             "Каков минимальный отдых?", "Укажите длительность")]
    monkeypatch.setattr(m, "_conditions_topics", lambda customer: rows)
    monkeypatch.setattr(m, "_topic_param_specs", lambda *args: {
        "условия-синт-отдых": {"name": "min_rest_synth",
            "unit": "duration_minutes", "relation": "min", "rule_kind": None}})
    return rows


def rule(i):
    return {"id": f"R-{i}", "kind": "HARD", "zone": "PAIRING",
            "title": "Отдых", "statement": "отдых вне базы",
            "sources": [{"sid": "S1", "anchor": f"п.{i}.1", "quote": "Норма"},
                        {"sid": "S1", "anchor": f"п.{i}.2", "quote": "Дополнение"}]}


def initial(n=1):
    doc = {"meta": {"revision": 1, "customer": "Синтетика",
                    "cdpm_version": "1.0-test", "mode": 1},
           "rules": [rule(i) for i in range(n)],
           "questions": [], "sources": [{"sid": "S1", "file": "rules.md"}]}
    assert m.ensure_conditions_questions(doc) == [ROOT]
    return doc, doc["questions"][0]


def test_first_question_keeps_all_targets_and_all_source_clauses(topics):
    doc, q = initial(8)
    assert set(q["rule_refs"]) == {f"R-{i}" for i in range(8)}
    assert len(q["source_refs"]) == 16


@pytest.mark.parametrize("status", [None, "ожидает обработчика", "применён"])
def test_growth_reissues_without_changing_published_question_or_answer(topics, status):
    doc, q = initial()
    if status:
        q["answer"] = {"raw": "10 часов", "status": status, "revision": 1}
    qe.freeze(q)
    before = copy.deepcopy(q)
    doc["rules"].append(rule(1))
    assert m.ensure_conditions_questions(doc) == [ROOT + "-E2"]
    new = doc["questions"][-1]
    assert q == before and "answer" not in new
    assert new["edition_of"] == ROOT
    assert qe.targets(new) == {"R-0", "R-1"}
    assert len(new["source_refs"]) == 4
    assert m.ensure_conditions_questions(doc) == []
    assert qe.problems(q) + qe.problems(new) == []
    doc["rules"].append(rule(2))
    assert m.ensure_conditions_questions(doc) == [ROOT + "-E3"]


def test_new_source_reissues_but_another_quote_of_same_clause_does_not(topics):
    doc, q = initial()
    qe.freeze(q)
    before = copy.deepcopy(q)
    doc["rules"][0]["sources"][0]["quote"] = "Другая выдержка из того же пункта"
    assert m.ensure_conditions_questions(doc) == []
    assert q == before
    doc["rules"][0]["sources"].append({"sid": "S1", "anchor": "п.новый"})
    assert m.ensure_conditions_questions(doc) == [ROOT + "-E2"]
    assert q == before
    assert doc["questions"][-1]["source_refs"][-1]["anchor"] == "п.новый"


def test_changed_question_form_reissues_and_is_idempotent(topics):
    doc, q = initial()
    qe.freeze(q)
    before = copy.deepcopy(q)
    key, rex, title, text, note = topics[0]
    topics[0] = (key, rex, title, text + " Уточните ночной отдых.", note)
    assert m.ensure_conditions_questions(doc) == [ROOT + "-E2"]
    assert q == before
    assert m.ensure_conditions_questions(doc) == []


def test_draft_growth_updates_in_place_and_no_topic_is_invented(topics):
    doc, q = initial()
    doc["rules"].append(rule(1))
    assert m.ensure_conditions_questions(doc) == []
    assert len(doc["questions"]) == 1 and "edition" not in q
    assert set(q["rule_refs"]) == {"R-0", "R-1"}
    assert m.ensure_conditions_questions({"rules": [], "questions": []}) == []


def test_current_edition_answer_reaches_parameters_not_old_answer(topics):
    doc, q = initial()
    qe.freeze(q)
    q["answer"] = {"raw": "10 часов", "status": "применён", "revision": 1}
    doc["rules"].append(rule(1))
    # Isolate reader from writer: explicit valid E2, not guessed suffix.
    new = qe.expand(doc, q, ["R-1"])
    before = copy.deepcopy(q)
    assert m.apply_topic_value_answers(doc) == []
    assert all("params" not in r for r in doc["rules"])
    assert answers.apply_answers(doc, {new["id"]: "12 часов"})["applied"] == 1
    assert len(m.apply_topic_value_answers(doc)) == 2
    assert [r["params"][0]["value"] for r in doc["rules"]] == [720, 720]
    assert m.apply_topic_value_answers(doc) == []
    assert q == before


@pytest.mark.parametrize("reverse", [False, True])
def test_export_uses_only_current_explicit_edition(topics, reverse):
    doc, q = initial()
    qe.freeze(q)
    q["answer"] = {"raw": "10 часов", "status": "применён", "revision": 1}
    new = qe.expand(doc, q, [], content={"text": q["text"] + " Уточнение."})
    if reverse:
        doc["questions"].reverse()
    assert ce._answer_raw(doc, ROOT) == (None, None)
    new["answer"] = {"raw": "12 часов", "status": "применён", "revision": 2}
    assert ce._answer_raw(doc, ROOT) == ("12 часов", 2)
    assert q["answer"]["raw"] == "10 часов"


def test_invalid_successor_cannot_restore_old_applied_answer(topics):
    doc, q = initial()
    qe.freeze(q)
    q["answer"] = {"raw": "10 часов", "status": "применён", "revision": 1}
    new = qe.expand(doc, q, [], content={"text": q["text"] + " Уточнение."})
    new["text"] += " Подмена после публикации."
    before = copy.deepcopy(doc)
    with pytest.raises(ValueError):
        ce._answer_raw(doc, ROOT)
    with pytest.raises(ValueError):
        m.apply_topic_value_answers(doc)
    assert doc == before


def test_legacy_exact_id_export_still_works_and_suffix_is_not_lineage(topics):
    doc, q = initial()
    q["answer"] = {"raw": "10 часов", "status": "применён", "revision": 1}
    alien = copy.deepcopy(q)
    alien.update(id=ROOT + "-E2", answer={"raw": "99 часов", "status": "применён"})
    doc["questions"].insert(0, alien)
    assert ce._answer_raw(doc, ROOT) == ("10 часов", 1)


def test_invalid_published_source_is_not_repaired_by_automatic_writer(topics):
    doc, q = initial()
    qe.freeze(q)
    q["source_refs"].append({"sid": "S1", "anchor": "подмена"})
    before = copy.deepcopy(doc)
    with pytest.raises(qe.EditionRefusal):
        m.ensure_conditions_questions(doc)
    assert doc == before


def test_all_source_clauses_are_preserved_independently_of_target_count(topics):
    doc, q = initial(3)
    assert len(q["source_refs"]) == 6


def test_answered_legacy_root_is_frozen_before_scope_growth(topics):
    doc, q = initial()
    q["answer"] = {"raw": "10 часов", "status": "применён", "revision": 1}
    before = copy.deepcopy(q)
    doc["rules"].append(rule(1))
    assert m.ensure_conditions_questions(doc) == [ROOT + "-E2"]
    assert {k: v for k, v in q.items() if k != "edition"} == before
    assert qe.targets(q) == {"R-0"}
    assert "answer" not in doc["questions"][-1]


@pytest.mark.parametrize("mutation", ["duplicate_root", "fork", "revision_gap"])
def test_ambiguous_or_broken_lineage_refuses_without_mutation(topics, mutation):
    doc, q = initial()
    qe.freeze(q)
    q["answer"] = {"raw": "10 часов", "status": "применён", "revision": 1}
    new = qe.expand(doc, q, [], content={"text": q["text"] + " Уточнение."})
    if mutation == "duplicate_root":
        doc["questions"].append(copy.deepcopy(q))
    elif mutation == "fork":
        other = copy.deepcopy(new)
        other["id"] += "-другой"
        doc["questions"].append(other)
    else:
        new.pop("edition")
        qe.freeze(new, revision=4)
        assert new["edition"]["revision"] == 4 and qe.problems(new) == []
    before = copy.deepcopy(doc)
    with pytest.raises(qe.EditionRefusal):
        m.ensure_conditions_questions(doc)
    with pytest.raises(qe.EditionRefusal):
        m.apply_topic_value_answers(doc)
    with pytest.raises(ce.ConditionsExportError) as error:
        ce._answer_raw(doc, ROOT)
    assert error.value.gaps[0]["key"] == "question_edition." + ROOT
    assert doc == before


def test_question_whitespace_change_is_not_a_new_normative_edition(topics):
    doc, q = initial()
    qe.freeze(q)
    before = copy.deepcopy(q)
    key, rex, title, text, note = topics[0]
    topics[0] = (key, rex, "  " + title, text + "  ", " " + note + " ")
    assert m.ensure_conditions_questions(doc) == []
    assert q == before


@pytest.mark.parametrize("writer", ["ensure_conditions_questions", "ensure_pairing_clarification_questions"])
def test_neighbor_writers_share_full_scope_and_published_source_growth(topics, monkeypatch, writer):
    monkeypatch.setattr(m, "_clarification_topics", lambda customer: topics)
    ensure = getattr(m, writer)
    doc = {"meta": {"revision": 1}, "rules": [rule(i) for i in range(8)], "questions": []}
    assert ensure(doc) == [ROOT]
    q = doc["questions"][0]
    assert set(q["rule_refs"]) == {f"R-{i}" for i in range(8)}
    assert len(q["source_refs"]) == 16
    qe.freeze(q)
    q["answer"] = {"raw": "10 часов", "status": "применён", "revision": 1}
    before = copy.deepcopy(q)
    doc["rules"].append(rule(8))
    assert ensure(doc) == [ROOT + "-E2"]
    assert q == before
    new = doc["questions"][-1]
    assert len(new["rule_refs"]) == 9 and len(new["source_refs"]) == 18
    assert "answer" not in new and ensure(doc) == []
    doc["rules"][0]["sources"].append({"sid": "S1", "anchor": "п.новый"})
    assert ensure(doc) == [ROOT + "-E3"]


def test_clarification_includes_case_carrier_without_changing_conditions_scope(topics, monkeypatch):
    monkeypatch.setattr(m, "_clarification_topics", lambda customer: topics)
    r = rule(0)
    r["kind"] = "CASE"
    doc = {"rules": [r], "questions": []}
    assert m.ensure_conditions_questions(doc) == []
    assert m.ensure_pairing_clarification_questions(doc) == [ROOT]
    qe.freeze(doc["questions"][0])
    r2 = rule(1)
    r2["kind"] = "CASE"
    doc["rules"].append(r2)
    assert m.ensure_pairing_clarification_questions(doc) == [ROOT + "-E2"]
    assert m.ensure_conditions_questions(doc) == []


def test_completed_parameters_do_not_hide_new_source_of_published_question(topics):
    doc, q = initial()
    qe.freeze(q)
    q["answer"] = {"raw": "10 часов", "status": "применён", "revision": 1}
    assert len(m.apply_topic_value_answers(doc)) == 1
    before = copy.deepcopy(q)
    assert m.ensure_conditions_questions(doc) == []
    doc["rules"][0]["sources"].append({"sid": "S1", "anchor": "п.новый"})
    assert m.ensure_conditions_questions(doc) == [ROOT + "-E2"]
    assert q == before and "answer" not in doc["questions"][-1]
