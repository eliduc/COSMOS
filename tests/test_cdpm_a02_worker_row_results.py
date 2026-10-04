"""A02 causal probe: every affected row needs its own worker result."""
import copy
import json

import pytest

from cdpm import answers, model as m
from cdpm.gates import gate_answers
from test_cdpm_task_package import _ready_doc


def _pending():
    doc = _ready_doc()
    original = doc["rules"][0]
    second = copy.deepcopy(original)
    second.update(id="R-H2", title="Второе жёсткое правило")
    doc["rules"].append(second)
    q = {"id": "Q-3b-ROW-RESULTS", "part": "3b", "title": "Проверить две строки",
         "text": "Уточните каждое правило", "answer_format": {"type": "registry"},
         "rule_refs": ["R-H", "R-H2"],
         "source_refs": copy.deepcopy(original["sources"]),
         "answer": {"raw": "Первое правило подтверждено. Второе ещё уточняется.",
                    "status": "ожидает обработчика", "revision": 2}}
    doc["questions"].append(q)
    for r in (original, second):
        r["blockers"] = [q["id"]]
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    return doc, q


def _worker(q, rows):
    patches = []
    for row in rows:
        patch = copy.deepcopy(row)
        patch.pop("blockers", None)
        patch["note"] = "Правило подтверждено ответом"
        patches.append(patch)
    return lambda prompt: json.dumps({"patches": [
        {"question": q["id"], "change": "Строки подтверждены", "rules": patches}
    ]}, ensure_ascii=False)


def test_worker_partial_result_keeps_unhandled_row_blocked():
    doc, q = _pending()
    first = doc["rules"][0]
    second = doc["rules"][-1]
    before_second = copy.deepcopy(second)
    result = answers.apply_worker_patches(doc, _worker(q, [first]))
    assert result["worker_rejected"] == []
    assert q["answer"]["status"] == "применён частично"
    assert q["id"] not in first.get("blockers", [])
    assert second == before_second
    assert m.is_open_question(q, doc=doc)
    assert result["worker_applied"] == 0 and result["worker_partial"] == 1


def test_worker_complete_result_remains_applied():
    doc, q = _pending()
    result = answers.apply_worker_patches(
        doc, _worker(q, [doc["rules"][0], doc["rules"][-1]]))
    assert result["worker_rejected"] == []
    assert q["answer"]["status"] == "применён"
    assert all(q["id"] not in r.get("blockers", []) for r in doc["rules"])


def _result_reply(q, rows):
    return lambda prompt: json.dumps({"patches": [
        {"question": q["id"], "change": "Результаты по строкам", "rules": rows}
    ]}, ensure_ascii=False)


def test_second_worker_pass_sees_only_unhandled_row_and_completes_answer():
    doc, q = _pending()
    first, second = doc["rules"][0], doc["rules"][-1]
    answers.apply_worker_patches(doc, _worker(q, [first]))
    frozen_first = copy.deepcopy(first)
    assert answers._affected_rules(doc, q) == [second]
    result = answers.apply_worker_patches(doc, _worker(q, [second]))
    assert result["worker_rejected"] == [] and result["worker_applied"] == 1
    assert q["answer"]["status"] == "применён" and not m.is_open_question(q)
    assert first == frozen_first
    assert q["id"] not in second.get("blockers", [])


def test_quoted_unchanged_is_a_result_and_preserves_rule_fields():
    doc, q = _pending()
    first, second = doc["rules"][0], doc["rules"][-1]
    before = copy.deepcopy(second)
    before.pop("blockers")
    patched = json.loads(_worker(q, [first])(None))["patches"][0]["rules"][0]
    q["answer"]["raw"] = "Первое правило подтверждено. Второе правило без изменений."
    basis = "Второе правило без изменений."
    result = answers.apply_worker_patches(doc, _result_reply(q, [patched,
        {"id": second["id"], "unchanged": {"basis": basis}}]))
    assert result["worker_rejected"] == [] and result["worker_applied"] == 1
    assert second == before
    assert q["answer"]["worker_unchanged"] == {second["id"]: basis}
    assert m.validate_schema(doc) + m.validate_model(doc) == []


@pytest.mark.parametrize("unchanged", [{}, {"basis": ""}, {"basis": " "},
    {"basis": "не было в ответе"}, {"basis": 7}, {"basis": "Первое правило", "extra": 1}])
def test_unchanged_requires_exact_nonempty_quote_and_refusal_is_atomic(unchanged):
    doc, q = _pending()
    before = copy.deepcopy(doc)
    result = answers.apply_worker_patches(doc, _result_reply(q, [
        {"id": "R-H", "unchanged": unchanged}]))
    assert result["worker_rejected"] and result["worker_applied"] == 0
    assert doc == before


@pytest.mark.parametrize("row", [None, [], {"id": []},
    {"id": "R-H", "unchanged": {"basis": "Первое правило"}, "kind": "OUT"}])
def test_malformed_row_or_unchanged_combined_with_changes_refuses(row):
    doc, q = _pending()
    before = copy.deepcopy(doc)
    result = answers.apply_worker_patches(doc, _result_reply(q, [row]))
    assert result["worker_rejected"] and doc == before


def test_partial_answer_keeps_gate_red_and_can_be_rendered(tmp_path):
    from cdpm import render
    doc, q = _pending()
    answers.apply_worker_patches(doc, _worker(q, [doc["rules"][0]]))
    model = tmp_path / "model.json"
    summary = tmp_path / "summary.json"
    model.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    summary.write_text(json.dumps({"revision": 2}), encoding="utf-8")
    issues, counts = gate_answers.check(model, summary)
    assert any(q["id"] in p and "частично" in p for p in issues)
    assert counts["partial"] == 1 and counts["open"] >= 1
    assert q["id"] in render.render_anketa(doc)


def test_new_text_cannot_replace_already_partially_applied_raw_or_effects():
    doc, q = _pending()
    answers.apply_worker_patches(doc, _worker(q, [doc["rules"][0]]))
    before = copy.deepcopy(q["answer"])
    result = answers.apply_answers(doc, {q["id"]: "Другой ответ"})
    assert result["already_answered"] == 1
    assert any(x["question"] == q["id"] and "частично" in x["reason"]
               and "пересмотр" in x["reason"] for x in result["refused"])
    assert q["answer"] == before


def test_partial_answer_can_be_explicitly_revised_with_effect_rollback():
    doc, q = _pending()
    first = doc["rules"][0]
    before = copy.deepcopy(first)
    answers.apply_worker_patches(doc, _worker(q, [first]))
    revision = answers.open_revision(doc, q["id"])
    assert q["answer"]["status"] == "пересмотрен"
    assert revision["created"] in first.get("blockers", [])
    assert revision["restored"] and revision["effects_reverted"] > 0
    assert first["statement"] == before["statement"]
    assert "note" not in first


def test_patch_preserves_blockers_of_another_question():
    doc, q = _pending()
    other = copy.deepcopy(q)
    other.update(id="Q-3b-OTHER", title="Другой вопрос", text="Другое уточнение")
    other.pop("answer")
    doc["questions"].append(other)
    first = doc["rules"][0]
    first["blockers"].append(other["id"])
    result = answers.apply_worker_patches(doc, _worker(q, [first]))
    assert result["worker_rejected"] == []
    assert first["blockers"] == [other["id"]]


def test_partial_published_edition_retries_only_its_unhandled_target():
    from cdpm import question_editions
    doc, q = _pending()
    question_editions.freeze(q)
    frozen_edition = copy.deepcopy(q["edition"])
    first, second = doc["rules"][0], doc["rules"][-1]
    answers.apply_worker_patches(doc, _worker(q, [first]))
    assert q["answer"]["status"] == "применён частично"
    assert answers._affected_rules(doc, q) == [second]
    result = answers.apply_worker_patches(doc, _worker(q, [second]))
    assert result["worker_rejected"] == []
    assert q["edition"] == frozen_edition and q["answer"]["status"] == "применён"


def test_retry_cannot_modify_an_already_handled_row():
    doc, q = _pending()
    first = doc["rules"][0]
    answers.apply_worker_patches(doc, _worker(q, [first]))
    before = copy.deepcopy(doc)
    result = answers.apply_worker_patches(doc, _worker(q, [first]))
    assert result["worker_rejected"] and doc == before


def test_persisted_unchanged_quote_is_checked_again_by_model():
    doc, q = _pending()
    result = answers.apply_worker_patches(doc, _result_reply(q, [
        {"id": "R-H", "unchanged": {"basis": "Первое правило подтверждено."}}]))
    assert result["worker_rejected"] == []
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    q["answer"]["worker_unchanged"]["R-H"] = "Придуманный ответ"
    assert any("unchanged" in p for p in m.validate_model(doc))


def test_empty_worker_result_does_not_finish_partial_answer():
    doc, q = _pending()
    answers.apply_worker_patches(doc, _worker(q, [doc["rules"][0]]))
    before = copy.deepcopy(doc)
    result = answers.apply_worker_patches(doc, _result_reply(q, []))
    assert result["worker_rejected"] and doc == before


@pytest.mark.parametrize("patches", [None, {}, [None], [{"question": []}]])
def test_malformed_patch_collection_refuses_without_crash(patches):
    doc, q = _pending()
    before = copy.deepcopy(doc)
    result = answers.apply_worker_patches(
        doc, lambda prompt: json.dumps({"patches": patches}))
    assert result["worker_rejected"] and doc == before


@pytest.mark.parametrize("field,bad", [("title", 7), ("statement", True),
    ("title", ["title"]), ("statement", {"statement": "text"})])
def test_nontext_title_or_statement_refuses_without_crash(field, bad):
    doc, q = _pending()
    before = copy.deepcopy(doc)
    patch = json.loads(_worker(q, [doc["rules"][0]])(None))["patches"][0]["rules"][0]
    patch[field] = bad
    result = answers.apply_worker_patches(doc, _result_reply(q, [patch]))
    assert result["worker_rejected"] and doc == before


def test_duplicate_question_patches_are_not_resolved_by_last_wins():
    doc, q = _pending()
    before = copy.deepcopy(doc)
    patch = json.loads(_worker(q, [doc["rules"][0]])(None))["patches"][0]
    result = answers.apply_worker_patches(
        doc, lambda prompt: json.dumps({"patches": [patch, patch]}, ensure_ascii=False))
    assert result["worker_rejected"] and doc == before


def test_refused_book_does_not_hide_how_to_revise_partial_answer():
    doc, q = _pending()
    answers.apply_worker_patches(doc, _worker(q, [doc["rules"][0]]))
    before = copy.deepcopy(q["answer"])
    result = answers.apply_answers(doc, {}, refused={q["id"]: {
        "raw": "Другой ответ", "reason": "чужая ревизия книги"}})
    assert result["already_answered"] == 1 and q["answer"] == before
    assert any("пересмотр" in r["reason"] and "частично" in r["reason"]
               for r in result["refused"])


def test_full_worker_result_preserves_existing_summary_shape():
    doc, q = _pending()
    assert answers.apply_worker_patches(doc, _worker(q, [
        doc["rules"][0], doc["rules"][-1]])) == {
        "worker_applied": 1, "worker_rejected": []}


def test_partial_answer_roundtrips_publication_and_questionnaire(tmp_path):
    from cdpm import anketa_xlsx
    from cdpm.gates import gate_anketa_xlsx
    doc, q = _pending()
    answers.apply_worker_patches(doc, _worker(q, [doc["rules"][0]]))
    model = tmp_path / "model.json"
    m.publish_model(doc, model)
    loaded = m.load_model(model)
    retained = next(x for x in loaded["questions"] if x["id"] == q["id"])
    assert retained["answer"]["status"] == "применён частично"
    assert [r["id"] for r in answers._affected_rules(loaded, retained)] == ["R-H2"]
    book = tmp_path / "anketa.xlsx"
    anketa_xlsx.render_anketa_xlsx(loaded, book)
    problems, _ = gate_anketa_xlsx.check_workbook(loaded, book)
    assert problems == []


def test_unpublished_legacy_scope_includes_already_blocked_row_before_freezing(tmp_path):
    doc, q = _pending()
    q["answer"]["raw"] = "Первое правило подтверждено. Второе правило без изменений."
    q["rule_refs"] = ["R-H"]
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    result = answers.apply_worker_patches(doc, _result_reply(q, [
        {"id": "R-H", "unchanged": {"basis": "Первое правило подтверждено."}},
        {"id": "R-H2", "unchanged": {"basis": "Второе правило без изменений."}}]))
    assert result == {"worker_applied": 1, "worker_rejected": []}
    assert set(q["rule_refs"]) == {"R-H", "R-H2"}
    path = tmp_path / "model.json"
    m.publish_model(doc, path)
    loaded = m.load_model(path)
    assert m.validate_schema(loaded) + m.validate_model(loaded) == []


def test_published_scope_cannot_expand_to_an_outside_blocked_row():
    from cdpm import question_editions
    doc, q = _pending()
    q["answer"]["raw"] = "Первое правило подтверждено. Второе правило без изменений."
    q["rule_refs"] = ["R-H"]
    question_editions.freeze(q)
    before = copy.deepcopy(doc)
    result = answers.apply_worker_patches(doc, _result_reply(q, [
        {"id": "R-H2", "unchanged": {"basis": "Второе правило без изменений."}}]))
    assert result["worker_rejected"] and doc == before
