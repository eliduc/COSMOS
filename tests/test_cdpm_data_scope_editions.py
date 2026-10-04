"""A04 data findings retain the issued choices and reach the worker honestly."""
import copy
import json

import pytest

from cdpm import answers, model as m, question_editions as qe
from test_cdpm_params import _doc, _rule


def report(*values, alias=False):
    return {"checks": [{"id": "C", "template": "перечень-значений",
                        "basis": {"rule": "[R-D]" if alias else "R-D"},
                        "status": "прогнана"}],
            "findings": [{"check": "C", "text": f"значение «{v}» вне перечня"}
                         for v in values]}


def initial(zone="PAIRING"):
    doc = _doc(_rule("R-D", kind="DATA", zone=zone, statement="База ∈ {OLD}"))
    qid = m.ensure_data_scope_questions(doc, report("AAA"))[0]
    return doc, doc["questions"][0], qid


@pytest.mark.parametrize("zone", ["PAIRING", "ROSTERING", "BOTH"])
@pytest.mark.parametrize("status", [None, "ожидает обработчика", "применён"])
def test_new_values_reissue_choices_without_reusing_old_answer(zone, status):
    doc, q, root = initial(zone)
    qe.freeze(q)
    if status:
        q["answer"] = {"raw": "а", "key": "а", "status": status, "revision": 1}
    before = copy.deepcopy(q)
    assert m.ensure_data_scope_questions(doc, report("AAA", "BBB")) == [root + "-E2"]
    new = qe.current_for_root(doc, root)
    assert q == before and "answer" not in new
    assert "BBB" in new["text"]
    assert all("BBB" in o["text"] for o in new["answer_format"]["options"])
    assert not doc["rules"][0].get("blockers")
    assert m.ensure_data_scope_questions(doc, report("BBB", "AAA", "BBB")) == []
    assert m.validate_model(doc) == []
    answers.apply_answers(doc, {root: "б"})
    assert q == before and "answer" not in new


@pytest.mark.parametrize("reverse", [False, True])
def test_alias_findings_are_merged_before_question_deduplication(reverse):
    doc = _doc(_rule("R-D", kind="DATA", statement="База ∈ {OLD}"))
    first, second = report("AAA"), report("BBB", alias=True)
    second["checks"][0]["id"] = "C2"
    second["findings"][0]["check"] = "C2"
    merged = {k: first[k] + second[k] for k in first}
    if reverse:
        merged["checks"].reverse()
        merged["findings"].reverse()
    assert len(m.ensure_data_scope_questions(doc, merged)) == 1
    assert "AAA, BBB" in doc["questions"][0]["text"]


def test_draft_refresh_and_answered_legacy_freeze():
    doc, q, root = initial()
    assert m.ensure_data_scope_questions(doc, report("BBB")) == []
    assert len(doc["questions"]) == 1 and "BBB" in q["text"]
    q["answer"] = {"raw": "а", "key": "а", "status": "применён", "revision": 1}
    before = copy.deepcopy(q)
    assert m.ensure_data_scope_questions(doc, report("CCC")) == [root + "-E2"]
    assert {k: v for k, v in q.items() if k != "edition"} == before


def test_all_sources_retained_and_new_clause_reissues_but_quote_does_not():
    doc = _doc(_rule("R-D", kind="DATA", statement="База ∈ {OLD}",
                    sources=[{"sid": "S1", "anchor": f"п.{i}"} for i in range(5)]))
    root = m.ensure_data_scope_questions(doc, report("AAA"))[0]
    q = doc["questions"][0]
    assert len(q["source_refs"]) == 5
    qe.freeze(q)
    before = copy.deepcopy(q)
    doc["rules"][0]["sources"][0]["quote"] = "другая выдержка того же пункта"
    assert m.ensure_data_scope_questions(doc, report("AAA")) == []
    doc["rules"][0]["sources"].append({"sid": "S1", "anchor": "п.новый"})
    assert m.ensure_data_scope_questions(doc, report("AAA")) == [root + "-E2"]
    assert q == before and len(doc["questions"][-1]["source_refs"]) == 6


def test_changed_published_snapshot_is_not_silently_ignored():
    doc, q, root = initial()
    qe.freeze(q)
    q["answer_format"]["options"][0]["text"] = "подмена"
    before = copy.deepcopy(doc)
    with pytest.raises(qe.EditionRefusal):
        m.ensure_data_scope_questions(doc, report("BBB"))
    assert doc == before


@pytest.mark.parametrize("key", ["а", "б"])
def test_choice_waits_for_effect_and_worker_receives_selected_meaning(key):
    doc, q, root = initial()
    qe.freeze(q)
    statement = doc["rules"][0]["statement"]
    result = answers.apply_answers(doc, {root: key})
    assert result["applied"] == 0 and result["awaiting"] == 1
    assert q["answer"]["status"] == "ожидает обработчика"
    assert doc["rules"][0]["blockers"] == [root]


    selected = next(o["text"] for o in q["answer_format"]["options"] if o["key"] == key)
    calls = []
    def worker(prompt):
        calls.append(prompt)
        assert selected in prompt
        return json.dumps({"patches": []})
    result = answers.apply_worker_patches(doc, worker)
    assert len(calls) == 1 and result["worker_applied"] == 0
    assert q["answer"]["status"] == "ожидает обработчика"
    assert doc["rules"][0]["statement"] == statement
    assert doc["rules"][0]["blockers"] == [root]


def test_inapplicable_does_not_close_a_data_scope_choice():
    doc, q, root = initial()
    qe.freeze(q)
    rules_before = copy.deepcopy(doc["rules"])
    result = answers.apply_answers(doc, {root: "неприменимо"})
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert q["answer"]["status"] == "не разобран"
    assert doc["rules"] == rules_before


@pytest.mark.parametrize("change", ["none", "whitespace", "note", "content"])
def test_worker_needs_content_effect_not_just_removing_blocker(change):
    doc, q, root = initial()
    qe.freeze(q)
    answers.apply_answers(doc, {root: "а"})
    before = copy.deepcopy(doc["rules"])
    patched = copy.deepcopy(doc["rules"][0])
    patched.pop("blockers", None)
    changes_rule = change == "content"
    if change == "whitespace":
        patched["statement"] += "  "
    if change == "note":
        patched["note"] = "ответ учтён"
    if changes_rule:
        patched["statement"] = "База ∈ {OLD, AAA}"
    result = answers.apply_worker_patches(doc, lambda _: json.dumps({
        "patches": [{"question": root, "rules": [patched]}]}))
    assert result["worker_applied"] == int(changes_rule)
    if changes_rule:
        assert q["answer"]["status"] == "применён"
        assert doc["rules"][0]["statement"] == "База ∈ {OLD, AAA}"
        assert not doc["rules"][0].get("blockers")
        assert any(e["field"] == "statement" for e in q["answer"]["effects"])
    else:
        assert doc["rules"] == before and q["answer"]["status"] == "ожидает обработчика"


@pytest.mark.parametrize("surface", ["md", "xlsx"])
def test_new_values_and_choices_reach_delivered_questionnaire(tmp_path, surface):
    from test_cdpm_edition_scope_display import shown
    doc, q, root = initial()
    qe.freeze(q)
    q["answer"] = {"raw": "а", "key": "а", "status": "применён", "revision": 1}
    before = copy.deepcopy(q)
    assert m.ensure_data_scope_questions(doc, report("AAA", "BBB")) == [root + "-E2"]
    new = qe.current_for_root(doc, root)
    text = shown(doc, new, surface, tmp_path)
    assert "добавить AAA, BBB" in text and "значениями AAA, BBB" in text
    assert "ответ «а» применён к изданию 1" in text
    assert q == before and "answer" not in new
