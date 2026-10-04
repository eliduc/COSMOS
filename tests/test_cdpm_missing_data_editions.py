"""A04: missing-data reports cannot rewrite or silently reuse published answers."""
import copy

import pytest

from cdpm import answers, model as m, question_editions as qe, question_resolutions as qr
from test_cdpm_params import _doc, _rule


def missing(*ids, reason="нет листа «Допуски»"):
    return {"checks": [
        {"id": f"C-{rid}", "status": "не прогнана", "reason": reason,
         "basis": {"rule": rid}} for rid in ids], "findings": []}


def ran(*ids):
    return {"checks": [
        {"id": f"C-{rid}", "status": "прогнана", "basis": {"rule": rid}}
        for rid in ids], "findings": []}


def initial(*ids, zone="PAIRING"):
    ids = ids or ("R-D",)
    doc = _doc(*[_rule(rid, kind="DATA", zone=zone,
                      statement="Для рейса требуется действующий допуск") for rid in ids])
    qid = m.ensure_missing_data_questions(doc, missing(*ids))[0]
    q = next(q for q in doc["questions"] if q["id"] == qid)
    qe.freeze(q)
    return doc, q, qid


def assert_only_import_revision_changed(doc, before):
    # apply_answers owns one document revision per import, including refusals.
    assert doc["meta"]["revision"] == before["meta"].get("revision", 1) + 1
    actual = copy.deepcopy(doc)
    if "revision" in before["meta"]:
        actual["meta"]["revision"] = before["meta"]["revision"]
    else:
        actual["meta"].pop("revision")
    assert actual == before


@pytest.mark.parametrize("zone", ["PAIRING", "ROSTERING", "BOTH"])
@pytest.mark.parametrize("answer", [None, "а", "б"])
def test_late_carrier_reissues_without_reusing_answer(zone, answer):
    doc, q, root = initial("R-Z", zone=zone)
    if answer:
        answers.apply_answers(doc, {root: answer})
    old = copy.deepcopy(q)
    extra = _rule("R-A", kind="DATA", zone=zone, statement=doc["rules"][0]["statement"])
    doc["rules"].append(extra)
    assert m.ensure_missing_data_questions(doc, missing("R-Z", "R-A")) == [root + "-E2"]
    new = qe.current_for_root(doc, root)
    assert q == old and new["rule_refs"] == ["R-A", "R-Z"] and "answer" not in new
    assert new["id"] in extra.get("blockers", []) and "excluded_until_data" not in extra
    assert m.ensure_missing_data_questions(doc, missing("R-A", "R-Z")) == []
    before = copy.deepcopy(doc)
    answers.apply_answers(doc, {root: "а"})
    assert_only_import_revision_changed(doc, before)
    assert m.validate_schema(doc) + m.validate_model(doc) == []


@pytest.mark.parametrize("change", ["reason", "statement", "source"])
def test_full_context_change_reissues(change):
    reason = "нет колонки «" + "я" * 180 + "А»"
    statement = "Условие " + "я" * 180 + " старое"
    doc = _doc(_rule("R-D", kind="DATA", zone="PAIRING", statement=statement))
    root = m.ensure_missing_data_questions(doc, missing("R-D", reason=reason))[0]
    q = doc["questions"][0]
    qe.freeze(q)
    answers.apply_answers(doc, {root: "б"})
    old = copy.deepcopy(q)
    if change == "reason":
        reason = reason[:-2] + "Б»"
    elif change == "statement":
        doc["rules"][0]["statement"] = statement.replace("старое", "новое")
    else:
        doc["rules"][0]["sources"].append({"sid": "S1", "anchor": "п.2"})
    assert m.ensure_missing_data_questions(doc, missing("R-D", reason=reason)) == [root + "-E2"]
    new = qe.current_for_root(doc, root)
    assert q == old and "answer" not in new
    assert reason in new["text"] and doc["rules"][0]["statement"] in new["text"]
    assert len(new["source_refs"]) == (2 if change == "source" else 1)
    assert m.ensure_missing_data_questions(doc, missing("R-D", reason=reason)) == []


@pytest.mark.parametrize("answer", [None, "а", "б"])
def test_positive_report_retires_question_without_changing_answer(answer):
    doc, q, root = initial()
    if answer:
        answers.apply_answers(doc, {root: answer})
    old_answer, old_edition = copy.deepcopy(q.get("answer")), copy.deepcopy(q["edition"])
    m.ensure_missing_data_questions(doc, ran("R-D"))
    assert qr.complete(q)
    assert q.get("answer") == old_answer and q["edition"] == old_edition
    assert not doc["rules"][0].get("excluded_until_data")
    assert root not in doc["rules"][0].get("blockers", [])
    before = copy.deepcopy(doc)
    answers.apply_answers(doc, {root: "а"})
    assert_only_import_revision_changed(doc, before)
    assert m.validate_schema(doc) + m.validate_model(doc) == []


@pytest.mark.parametrize("answer", [None, "а", "б"])
def test_partial_report_only_retires_proven_target(answer):
    doc, q, root = initial("R-A", "R-B")
    if answer:
        answers.apply_answers(doc, {root: answer})
    old_answer = copy.deepcopy(q.get("answer"))
    m.ensure_missing_data_questions(doc, ran("R-A"))
    assert qr.resolved_targets(q) == {"R-A"} and not qr.complete(q)
    assert q.get("answer") == old_answer
    if answer is None:
        answers.apply_answers(doc, {root: "а"})
    a, b = doc["rules"]
    assert not a.get("excluded_until_data")
    if answer != "б":
        assert (b.get("excluded_until_data") or {}).get("question") == root
    else:
        assert root in b.get("blockers", [])


@pytest.mark.parametrize("report", [
    {"checks": [], "findings": []},
    {"checks": [{"id": "C-D", "status": "не скомпилирована",
                 "basis": {"rule": "R-D"}, "reason": "строка блокирована вопросом"}]},
    {"checks": [
        {"id": "C-1", "status": "прогнана", "basis": {"rule": "R-D"}},
        {"id": "C-2", "status": "не прогнана", "basis": {"rule": "R-D"},
         "reason": "нет листа «Допуски»"}]},
])
def test_no_positive_evidence_does_not_clear_exclusion(report):
    doc, q, root = initial()
    answers.apply_answers(doc, {root: "а"})
    m.ensure_missing_data_questions(doc, copy.deepcopy(report))
    assert doc["rules"][0]["excluded_until_data"]["question"] == root
    assert not qr.complete(q)


def test_caveated_permission_is_not_unconditional_exclusion():
    doc, q, root = initial()
    result = answers.apply_answers(doc, {root: "а, но только для дальних рейсов"})
    assert result["applied"] == 0
    assert q["answer"]["status"] == "ожидает обработчика"
    assert not doc["rules"][0].get("excluded_until_data")
    assert root in doc["rules"][0].get("blockers", [])


@pytest.mark.parametrize("answer", [None, "а", "б"])
def test_coverage_withdrawal_keeps_airline_history(answer):
    doc, q, root = initial()
    if answer:
        answers.apply_answers(doc, {root: answer})
    old = copy.deepcopy(q.get("answer"))
    rule = doc["rules"][0]
    rule.update(kind="HARD", template_fit={
        "category": "C", "registry_rev": "x" * 12, "gap": "нет механики"})
    m.ensure_missing_data_questions(doc, missing("R-D"))
    assert qr.complete(q) and q.get("answer") == old
    assert root not in rule.get("blockers", []) and "excluded_until_data" not in rule
    assert m.validate_schema(doc) + m.validate_model(doc) == []


@pytest.mark.parametrize("answer", [None, "а", "б"])
@pytest.mark.parametrize("reason", ["нет листа «Допуски»", "нет колонки «База»"])
def test_data_returns_then_disappears_requires_a_new_edition(answer, reason):
    doc, q, root = initial()
    if answer:
        answers.apply_answers(doc, {root: answer})
    m.ensure_missing_data_questions(doc, ran("R-D"))
    old = copy.deepcopy(q)
    assert m.ensure_missing_data_questions(doc, missing("R-D", reason=reason)) == [root + "-E2"]
    new = qe.current_for_root(doc, root)
    assert q == old and "answer" not in new and not qr.complete(new)
    assert new["id"] in doc["rules"][0].get("blockers", [])
    assert "excluded_until_data" not in doc["rules"][0]
    assert reason in new["text"]
    assert m.ensure_missing_data_questions(doc, missing("R-D", reason=reason)) == []
    assert m.validate_schema(doc) + m.validate_model(doc) == []

@pytest.mark.parametrize("surface", ["md", "xlsx"])
@pytest.mark.parametrize("answer", ["а", "б"])
def test_permission_effect_and_retirement_are_visible_in_the_questionnaire(tmp_path, surface, answer):
    from test_cdpm_edition_scope_display import shown
    doc, q, root = initial()
    answers.apply_answers(doc, {root: answer})
    expected = "исключены из расчёта до получения данных: R-D" if answer == "а" else "ждём данных"
    assert expected in shown(doc, q, surface, tmp_path)
    prior = copy.deepcopy(q["answer"])
    m.ensure_missing_data_questions(doc, ran("R-D"))
    text = shown(doc, q, surface, tmp_path)
    assert "данные поступили" in text and "снят кодом" in text
    assert q["answer"] == prior


@pytest.mark.parametrize("answer", [None, "а", "б"])
def test_executor_withdrawal_retires_only_its_target(answer):
    from cdpm import catalog_bridge as bridge
    doc, q, root = initial("R-A", "R-B")
    if answer:
        answers.apply_answers(doc, {root: answer})
    prior = copy.deepcopy(q.get("answer"))
    fam = next(f for f in sorted(m.template_families()) if bridge.family_has_executor(f))
    doc["rules"][0].update(kind="HARD", template_fit={
        "category": "A", "family": fam, "registry_rev": "x" * 12, "quote": "правило"})
    m.ensure_missing_data_questions(doc, {"checks": []})
    assert qr.resolved_targets(q) == {"R-A"} and q.get("answer") == prior
    assert not doc["rules"][0].get("excluded_until_data")
    if answer is None:
        answers.apply_answers(doc, {root: "а"})
        assert not doc["rules"][0].get("excluded_until_data")
        assert doc["rules"][1]["excluded_until_data"]["question"] == root


def test_code_retirement_freezes_an_unanswered_draft_before_data_returns():
    doc = _doc(_rule("R-D", kind="DATA", zone="PAIRING", statement="Требуется допуск"))
    root = m.ensure_missing_data_questions(doc, missing("R-D"))[0]
    q = doc["questions"][0]
    assert "edition" not in q
    m.ensure_missing_data_questions(doc, ran("R-D"))
    prior = copy.deepcopy(q)
    assert "edition" in q and "answer" not in q
    assert m.ensure_missing_data_questions(doc, missing("R-D")) == [root + "-E2"]
    assert q == prior and not qr.complete(qe.current_for_root(doc, root))


@pytest.mark.parametrize("raw", ["неприменимо", "исключить правило", "в"])
def test_invalid_missing_data_choice_cannot_remove_the_rule(raw):
    doc, q, root = initial()
    before = copy.deepcopy(doc["rules"])
    res = answers.apply_answers(doc, {root: raw})
    assert res["applied"] == 0 and doc["rules"] == before


@pytest.mark.parametrize("field,value", [
    ("outcome", []), ("outcome", "неизвестно"), ("checks", []),
    ("checks", [{"id": "C", "rule": "R-FOREIGN", "status": "прогнана"}]),
    ("checks", [{"id": "C", "rule": "R-D", "status": "не прогнана"}]),
    ("statement", None),
])
def test_malformed_or_nonpositive_code_proof_is_rejected(field, value):
    doc, q, root = initial()
    m.ensure_missing_data_questions(doc, ran("R-D"))
    q["code_resolutions"][0]["data"][field] = value
    assert qr.problems(doc, q) and not qr.complete(q)
    assert m.validate_schema(doc) or m.validate_model(doc)


def test_missing_rule_is_refused_without_mutating_the_question():
    doc, q, root = initial()
    doc["rules"].clear()
    before = copy.deepcopy(doc)
    with pytest.raises(qe.EditionRefusal, match="отсутствующую строку"):
        m.ensure_missing_data_questions(doc, {"checks": []})
    assert doc == before


def test_positive_and_other_failed_check_do_not_clear_the_exclusion():
    doc, q, root = initial()
    answers.apply_answers(doc, {root: "а"})
    report = ran("R-D")
    report["checks"].append({"id": "C-other", "status": "не скомпилирована",
                             "reason": "ошибка компилятора", "basis": {"rule": "R-D"}})
    m.ensure_missing_data_questions(doc, report)
    assert doc["rules"][0]["excluded_until_data"]["question"] == root
    assert not qr.complete(q)


def test_partial_resolution_carries_forward_without_reusing_airline_answer():
    doc, q, root = initial("R-A", "R-B")
    m.ensure_missing_data_questions(doc, ran("R-A"))
    before = copy.deepcopy(q)
    m.ensure_missing_data_questions(doc, missing("R-B", reason="нет колонки «Тип»"))
    new = qe.current_for_root(doc, root)
    assert new is not q and q == before and qr.resolved_targets(new) == {"R-A"}
    answers.apply_answers(doc, {new["id"]: "а"})
    assert not doc["rules"][0].get("excluded_until_data")
    assert doc["rules"][1]["excluded_until_data"]["question"] == new["id"]
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def _positive_ground(doc, ground):
    rule = doc["rules"][0]
    rule.pop("template_fit", None)
    rule["kind"] = "DATA"
    if ground == "coverage_gap":
        rule.update(kind="HARD", template_fit={
            "category": "C", "registry_rev": "x" * 12, "gap": "нет механики"})
    elif ground == "executor":
        from cdpm import catalog_bridge as bridge
        family = "crew_composition"
        assert m.template_families()[family] == ["crew_size"] and bridge.family_has_executor(family)
        assert m.template_family_traits()[family] == {"kind": "HARD", "zone": "BOTH"}
        rule.update(kind="HARD", template_fit={
            "category": "A", "family": family, "registry_rev": "x" * 12,
            "params": {"crew_size": {"value": 2, "quote": "Экипаж смены — ровно 2 пилота"}}})
    return ran("R-D") if ground == "present" else {"checks": []}


@pytest.mark.parametrize("old_ground,new_ground,new_statement", [
    ("coverage_gap", "executor", False), ("executor", "coverage_gap", False),
    ("coverage_gap", "present", False), ("present", "executor", False),
    ("present", "present", True), ("coverage_gap", "coverage_gap", True),
    ("executor", "executor", True),
])
def test_changed_positive_ground_has_a_new_immutable_edition(old_ground, new_ground, new_statement):
    doc, q, root = initial()
    answers.apply_answers(doc, {root: "а"})
    m.ensure_missing_data_questions(doc, _positive_ground(doc, old_ground))
    assert qr.complete(q)
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    before = copy.deepcopy(q)
    report = _positive_ground(doc, new_ground)
    if new_statement:
        doc["rules"][0]["statement"] += " для международного рейса"
    assert m.ensure_missing_data_questions(doc, report) == [root + "-E2"]
    new = qe.current_for_root(doc, root)
    assert q == before and "answer" not in new and qr.complete(new)
    proof = new["code_resolutions"][0]["data"]
    assert proof["outcome"] == new_ground
    assert proof["statement"] == doc["rules"][0]["statement"]
    assert "основание" in new["answer_format"]["note"].lower()
    assert not doc["rules"][0].get("blockers")
    assert not doc["rules"][0].get("excluded_until_data")
    assert m.validate_schema(doc) + m.validate_model(doc) == []
    frozen = copy.deepcopy(doc)
    assert m.ensure_missing_data_questions(doc, copy.deepcopy(report)) == []
    assert doc == frozen


@pytest.mark.parametrize("old_ground", ["coverage_gap", "executor", "present"])
def test_lost_positive_basis_without_a_fresh_report_is_refused(old_ground):
    doc, q, root = initial()
    m.ensure_missing_data_questions(doc, _positive_ground(doc, old_ground))
    doc["rules"][0].pop("template_fit", None)
    doc["rules"][0]["kind"] = "DATA"
    doc["rules"][0]["statement"] += " для международного рейса"
    before = copy.deepcopy(doc)
    with pytest.raises(qe.EditionRefusal, match="основание"):
        m.ensure_missing_data_questions(doc, {"checks": []})
    assert doc == before


@pytest.mark.parametrize("answer", ["а", "б"])
@pytest.mark.parametrize("surface", ["svod", "md", "xlsx"])
def test_return_and_new_deficit_have_distinct_current_and_historical_display(tmp_path, answer, surface):
    from cdpm import render
    from test_cdpm_edition_scope_display import shown
    doc, q, root = initial()
    answers.apply_answers(doc, {root: answer})
    prior_answer = copy.deepcopy(q["answer"])
    m.ensure_missing_data_questions(doc, ran("R-D"))
    text = render.render_svod(doc) if surface == "svod" else shown(doc, q, surface, tmp_path)
    assert "данные поступили" in text and "снят кодом" in text
    assert not doc["rules"][0].get("blockers") and not doc["rules"][0].get("excluded_until_data")
    if surface == "svod":
        rule_line = next(line for line in text.splitlines() if line.startswith("| R-D |"))
        assert "данные поступили" in rule_line
    closed = copy.deepcopy(q)
    reason = "нет колонки «База» после повторной выгрузки"
    assert m.ensure_missing_data_questions(doc, missing("R-D", reason=reason)) == [root + "-E2"]
    new = qe.current_for_root(doc, root)
    before_render = copy.deepcopy(doc)
    text = render.render_svod(doc) if surface == "svod" else shown(doc, new, surface, tmp_path)
    assert new["id"] in text and reason in text
    assert new["id"] in doc["rules"][0]["blockers"]
    assert q == closed and q["answer"] == prior_answer and "answer" not in new
    assert doc == before_render
    if surface == "svod":
        assert "Историческое издание" in text and "актуальный вопрос — " + new["id"] in text


@pytest.mark.parametrize("bad_format", ["choice", ["choice"], True, None])
def test_malformed_format_refuses_before_mutating_the_model(bad_format):
    doc, q, root = initial()
    q["answer_format"] = bad_format
    before = copy.deepcopy(doc)
    with pytest.raises(qe.EditionRefusal):
        m.ensure_missing_data_questions(doc, missing("R-D"))
    assert doc == before
