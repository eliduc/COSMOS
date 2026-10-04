"""Regressions for CDPM339 and COSMOS's accepted 900de043 residuals."""
import copy
import json

import pytest

from cdpm import answers, model as m, question_editions as qe
from test_cdpm_data_scope_editions import initial, report
from test_cdpm_edition_scope_display import shown


def pending(key="а"):
    doc, q, root = initial()
    qe.freeze(q)
    answers.apply_answers(doc, {root: key})
    return doc, q, root


def patch(doc, q, **changes):
    rule = copy.deepcopy(doc["rules"][0])
    rule.pop("blockers", None)
    rule.update(changes)
    return answers.apply_worker_patches(doc, lambda _: json.dumps({
        "patches": [{"question": q["id"], "rules": [rule]}]}))


@pytest.mark.parametrize("statement", ["БАЗА ∈ {OLD}", "база ∈ {old}",
                                       "База ∈ {OLD}.", "База ∈ {OLD} —",
                                       "База ∈ {OLD, AAA}"])
def test_cosmetic_case_is_not_an_effect_but_extension_is(statement):
    doc, q, root = pending()
    before = copy.deepcopy(doc["rules"])
    applied = statement == "База ∈ {OLD, AAA}"
    result = patch(doc, q, statement=statement)
    assert result["worker_applied"] == int(applied)
    if not applied:
        assert doc["rules"] == before
        assert q["answer"]["status"] == "ожидает обработчика"
        assert m.ensure_data_scope_questions(doc, report("AAA")) == []
        assert q["answer"]["status"] == "ожидает обработчика"
    assert m.validate_schema(doc) + m.validate_model(doc) == []


@pytest.mark.parametrize("change", [{}, {"kind": "OUT"},
                                    {"statement": "База ∈ {OLD}; записи с AAA исключены"},
                                    {"statement": "База ∈ {OLD, AAA}"}])
def test_exclude_records_never_means_remove_or_rewrite_rule(change):
    doc, q, root = pending("б")
    before = copy.deepcopy(doc["rules"])
    result = patch(doc, q, **change)
    assert result["worker_applied"] == 0
    assert doc["rules"] == before
    assert q["answer"]["raw"] == "б" and q["answer"]["status"] == "ожидает обработчика"
    reason = "; ".join(result["worker_rejected"][0]["reasons"])
    assert "запис" in reason and "повторите проверку данных" in reason
    assert m.validate_schema(doc) + m.validate_model(doc) == []


@pytest.mark.parametrize("surface", ["md", "xlsx"])
@pytest.mark.parametrize("raw", ["неприменимо", "проверьте всё ещё раз"])
def test_unparsed_advice_reaches_actual_delivered_questionnaire(tmp_path, surface, raw):
    doc, q, root = initial()
    qe.freeze(q)
    answers.apply_answers(doc, {root: raw})
    assert q["answer"]["status"] == "не разобран"
    reason = q["answer"]["note"]
    snapshot = copy.deepcopy(doc)
    text = shown(doc, q, surface, tmp_path)
    assert reason in text and raw in text
    assert doc == snapshot


@pytest.mark.parametrize("surface", ["md", "xlsx"])
@pytest.mark.parametrize("key", ["а", "б"])
def test_worker_refusal_reaches_actual_delivered_questionnaire(tmp_path, surface, key):
    doc, q, root = pending(key)
    result = patch(doc, q)
    reason = "; ".join(result["worker_rejected"][0]["reasons"])
    snapshot = copy.deepcopy(doc)
    text = shown(doc, q, surface, tmp_path)
    assert reason in text
    assert "ожидает обработчика" in text
    assert doc == snapshot


def test_success_clears_processing_reason_without_changing_airline_note(tmp_path):
    doc, q, root = pending()
    patch(doc, q)
    assert q["answer"].get("processing_note")
    raw, note = q["answer"]["raw"], q["answer"].get("note")
    assert patch(doc, q, statement="База ∈ {OLD, AAA}")["worker_applied"] == 1
    assert "processing_note" not in q["answer"]
    assert q["answer"]["raw"] == raw and q["answer"].get("note") == note
    assert "выбор по данным не внесён" not in shown(doc, q, "md", tmp_path)


def test_reissued_answer_keeps_previous_processing_record_immutable():
    doc, q, root = pending()
    patch(doc, q)
    assert q["answer"].get("processing_note")
    old = copy.deepcopy(q)
    m.ensure_data_scope_questions(doc, report("AAA", "BBB"))
    answers.apply_worker_patches(doc, lambda _: pytest.fail("old edition must not be sent"))
    assert q == old


@pytest.mark.parametrize("surface", ["md", "xlsx"])
def test_exclusion_limit_is_visible_before_any_worker_call(tmp_path, surface):
    doc, q, root = pending("б")
    text = shown(doc, q, surface, tmp_path)
    assert "повторите проверку данных" in text
    assert "правило и блокировка сохранены" in text
    assert q["answer"]["status"] == "ожидает обработчика"
    assert doc["rules"][0]["blockers"] == [root]


def test_empty_worker_response_keeps_exclusion_specific_advice():
    doc, q, root = pending("б")
    before = copy.deepcopy(doc["rules"])
    result = answers.apply_worker_patches(doc, lambda _: '{"patches": []}')
    assert result["worker_applied"] == 0 and doc["rules"] == before
    assert "повторите проверку данных" in q["answer"]["processing_note"]
    assert m.validate_schema(doc) + m.validate_model(doc) == []


@pytest.mark.parametrize("statement", ["База ∈ {OLD}", "База ∈ {OLD, AAA}"])
def test_extension_choice_cannot_remove_rule_even_with_changed_statement(statement):
    doc, q, root = pending("а")
    before = copy.deepcopy(doc["rules"])
    result = patch(doc, q, kind="OUT", statement=statement)
    assert result["worker_applied"] == 0
    assert doc["rules"] == before
    assert q["answer"]["status"] == "ожидает обработчика"
    assert "вид правила" in q["answer"]["processing_note"]
