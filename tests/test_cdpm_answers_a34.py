# -*- coding: utf-8 -*-
"""Аудит 04.09, п. A3.4 — журнал следствий ответа и пересмотр.

Следствие ответа — разница свода до/после применения (поле за полем по
строкам и целевым), одинаково во всех ветвях применения. Пересмотр
применённого ответа: следствия снимаются по журналу (строки — к виду
«до», развилка возвращается), строки блокируются вопросом `<id>-R`,
прежний ответ — «пересмотрен»; ответ на -R применяется с реальным
эффектом. Приёмка плана: «ответ → пересмотр → противоположный ответ»
меняет вид строки.
"""
from __future__ import annotations

import copy

import pytest

from cdpm import answers as ans
from cdpm import model as m
from cdpm import render
from cdpm.sample import sample_model

RULE = "R-S1-П-2-3"
Q = "Q-2-MODAL"


def _rule(doc):
    return next(r for r in doc["rules"] if r["id"] == RULE)


def _q(doc, qid):
    return next(x for x in doc["questions"] if x["id"] == qid)


def _clean(doc):
    return m.validate_schema(doc) + m.validate_model(doc)


def test_effects_journal_is_a_before_after_diff():
    doc = sample_model()
    before = copy.deepcopy(_rule(doc))
    ans.apply_answers(doc, {Q: "б"})
    eff = _q(doc, Q)["answer"]["effects"]
    assert eff and all(set(e) >= {"rule", "field", "before", "after"} for e in eff)
    by_field = {e["field"]: e for e in eff if e["rule"] == RULE}
    assert by_field["conditional"]["before"] == before["conditional"]
    assert by_field["conditional"].get("after_absent") is True
    assert by_field["blockers"]["before"] == before["blockers"]
    # Ничего лишнего: журнал только по затронутой строке.
    assert {e["rule"] for e in eff} == {RULE}
    assert _clean(doc) == []


def test_revision_restores_before_state_and_blocks_rows():
    doc = sample_model()
    before = copy.deepcopy(_rule(doc))
    ans.apply_answers(doc, {Q: "б"})
    assert _rule(doc)["kind"] == "HARD" and "conditional" not in _rule(doc)
    opened = ans.open_revision(doc, Q)
    assert opened["created"] == f"{Q}-R" and opened["restored"] is True
    assert opened["effects_reverted"] >= 2 and opened["blocked"] == [RULE]
    rule = _rule(doc)
    # Развилка вернулась, вид — как до ответа, блокировка — вопросом -R.
    assert rule["conditional"] == before["conditional"]
    assert rule["kind"] == before["kind"]
    assert f"{Q}-R" in rule["blockers"]
    old = _q(doc, Q)["answer"]
    assert old["status"] == "пересмотрен" and old["revised_by"] == f"{Q}-R"
    assert old["key"] == "б" and old["effects"]        # история сохранена
    new_q = _q(doc, f"{Q}-R")
    assert new_q["revision_of"] == Q and new_q.get("answer") is None
    assert new_q["answer_format"] == _q(doc, Q)["answer_format"]
    assert "сняты по журналу" in new_q["text"]
    assert _clean(doc) == []
    # -R открыт в анкете, исходный — в принятых со статусом «пересмотрен».
    anketa = render.render_anketa(doc)
    assert f"### ВОПРОС {Q}-R" in anketa
    assert "пересмотрен" in anketa


def test_answer_to_revision_applies_with_real_effect():
    """Приёмка: «ответ → пересмотр → противоположный ответ» меняет вид."""
    doc = sample_model()
    ans.apply_answers(doc, {Q: "б"})
    assert _rule(doc)["kind"] == "HARD"
    ans.open_revision(doc, Q)
    summary = ans.apply_answers(doc, {f"{Q}-R": "а"})
    assert summary["applied"] == 1 and summary["resolved_rules"] == [RULE]
    rule = _rule(doc)
    assert rule["kind"] == "SOFT"                     # противоположная ветвь
    assert "conditional" not in rule
    assert f"{Q}-R" not in rule.get("blockers", [])
    new_a = _q(doc, f"{Q}-R")["answer"]
    assert new_a["status"] == "применён" and new_a["key"] == "а"
    assert any(e["field"] == "kind" and e["after"] == "SOFT"
               for e in new_a["effects"] if e["rule"] == RULE)
    # Повторный ответ на исходный вопрос поверх пересмотра не применяется.
    again = ans.apply_answers(doc, {Q: "б"})
    assert again["already_answered"] == 1 and _rule(doc)["kind"] == "SOFT"
    assert _clean(doc) == []


def test_second_revision_points_at_root_and_applies():
    """Пересмотр пересмотра: id «-R-2», revision_of — КОРЕНЬ (не -R),
    иначе развилка корня не найдётся и второй пересмотр — тупик."""
    doc = sample_model()
    ans.apply_answers(doc, {Q: "б"})
    ans.open_revision(doc, Q)
    ans.apply_answers(doc, {f"{Q}-R": "а"})
    assert _rule(doc)["kind"] == "SOFT"
    opened = ans.open_revision(doc, f"{Q}-R")
    assert opened["created"] == f"{Q}-R-2" and opened["restored"] is True
    assert _q(doc, f"{Q}-R-2")["revision_of"] == Q
    # Восстановлено состояние до ответа на -R: развилка снова на месте.
    assert _rule(doc)["conditional"]["question"] == Q
    assert _clean(doc) == []
    summary = ans.apply_answers(doc, {f"{Q}-R-2": "б"})
    assert summary["applied"] == 1 and _rule(doc)["kind"] == "HARD"
    assert not (set(_rule(doc).get("blockers", [])) & {Q, f"{Q}-R", f"{Q}-R-2"})
    assert _clean(doc) == []


def test_revision_detects_later_changes_and_does_not_roll_back():
    """Порядок: ответ A применён, затем поле строки изменил кто-то ещё —
    откат A перекрыл бы чужое изменение молча. Конфликт замечен: отката
    нет, -R создан с пометкой, строки заблокированы."""
    doc = sample_model()
    ans.apply_answers(doc, {Q: "б"})
    # Позднейшее изменение поля, которое есть в журнале (blockers: после
    # ответа отсутствовали — кто-то поставил новую блокировку).
    _rule(doc)["blockers"] = ["Q-ДРУГОЙ"]
    _rule(doc)["weight"] = 7
    opened = ans.open_revision(doc, Q)
    assert opened["restored"] is False and opened["conflicts"]
    assert any("изменено позже" in c for c in opened["conflicts"])
    rule = _rule(doc)
    assert "conditional" not in rule and rule["weight"] == 7      # не тронуто
    assert set(rule["blockers"]) == {"Q-ДРУГОЙ", f"{Q}-R"}
    assert "перекрыты" in _q(doc, f"{Q}-R")["text"]
    assert _q(doc, Q)["answer"]["status"] == "пересмотрен"


def test_revision_of_goal_family_is_refused_with_words():
    doc = sample_model()
    m.ensure_goal_question(doc, "3a")
    goal = next(q for q in doc["questions"] if m.is_goal_family(q["id"]))
    goal["answer"] = {"raw": "любое допустимое решение", "status": "применён",
                      "revision": 1, "effects": []}
    with pytest.raises(ans.AnswersError, match="добором семьи GOAL"):
        ans.open_revision(doc, goal["id"])


def test_worker_path_recognises_revision_targets():
    """Путь обработчика: затронутые строки и проверка патча узнают -R за
    корень (развилка строки указывает на корень, блокировки — на оба)."""
    doc = sample_model()
    ans.apply_answers(doc, {Q: "б"})
    ans.open_revision(doc, Q)
    rq = _q(doc, f"{Q}-R")
    affected = ans._affected_rules(doc, rq)
    assert [r["id"] for r in affected] == [RULE]
    assert ans._answer_targets(rq) == {f"{Q}-R", Q}
    # Патч обработчика на строку с развилкой корня не бракуется как
    # «условный тип другого вопроса».
    patch_rule = copy.deepcopy(_rule(doc))
    patch_rule.pop("blockers", None)
    reason = ans._check_patch(rq, {"rules": [patch_rule]},
                              {r["id"]: r for r in affected})
    assert reason is None or "условный тип другого вопроса" not in reason


def test_model_contract_guards_revised_answers():
    doc = sample_model()
    ans.apply_answers(doc, {Q: "б"})
    ans.open_revision(doc, Q)
    assert _clean(doc) == []
    # Снять блокировку -R со строки — пересмотренный вопрос остаётся над
    # строкой без открытого преемника: контракт красный.
    _rule(doc)["blockers"] = [b for b in _rule(doc)["blockers"] if b != f"{Q}-R"]
    problems = m.validate_model(doc)
    assert any("без блокировки вопросом-пересмотра" in p for p in problems)
    # Ссылка revised_by на несуществующий вопрос — тоже.
    doc2 = sample_model()
    ans.apply_answers(doc2, {Q: "б"})
    ans.open_revision(doc2, Q)
    _q(doc2, Q)["answer"]["revised_by"] = "Q-НЕТ"
    assert any("без вопроса-пересмотра" in p for p in m.validate_model(doc2))


def test_vmp_form_treats_revised_as_closed():
    from cdpm import vmp_form
    doc = sample_model()
    ans.apply_answers(doc, {Q: "б"})
    ans.open_revision(doc, Q)
    form = vmp_form.model_to_form(doc) if hasattr(vmp_form, "model_to_form") \
        else vmp_form.to_form(doc)
    answered = {q["id"] for q in form["answered_questions"]}
    open_ = {q["id"] for q in form["open_questions"]}
    assert Q in answered and f"{Q}-R" in open_


def test_revision_without_journal_is_honest_not_silent():
    """Ответ, принятый до появления журнала: восстановить «до» нельзя —
    -R создаётся с пометкой, строки блокируются, следствия — обработчику."""
    doc = sample_model()
    ans.apply_answers(doc, {Q: "б"})
    _q(doc, Q)["answer"].pop("effects")            # как в старых моделях
    opened = ans.open_revision(doc, Q)
    assert opened["restored"] is False and opened["effects_reverted"] == 0
    rule = _rule(doc)
    assert rule["kind"] == "HARD" and "conditional" not in rule   # не откачено
    assert f"{Q}-R" in rule["blockers"]
    assert "без журнала" in _q(doc, f"{Q}-R")["text"]
    assert _clean(doc) == []


def test_revision_refuses_open_or_missing_question():
    doc = sample_model()
    with pytest.raises(ans.AnswersError, match="ПРИМЕНЁННЫЙ"):
        ans.open_revision(doc, Q)
    with pytest.raises(ans.AnswersError, match="нет"):
        ans.open_revision(doc, "Q-НЕТ")


def test_journal_covers_inapplicable_branch():
    doc = sample_model()
    ans.apply_answers(doc, {Q: ans.INAPPLICABLE_KEY})
    eff = _q(doc, Q)["answer"]["effects"]
    fields = {e["field"] for e in eff if e["rule"] == RULE}
    assert {"kind", "conditional"} <= fields
    assert next(e for e in eff if e["field"] == "kind")["after"] == "OUT"
    # Пересмотр возвращает строку в контур.
    ans.open_revision(doc, Q)
    assert _rule(doc)["kind"] != "OUT" and _rule(doc)["conditional"]["question"] == Q
    assert _clean(doc) == []
