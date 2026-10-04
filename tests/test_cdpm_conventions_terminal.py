# -*- coding: utf-8 -*-
"""Конвенции формализации терминально и замки обмена (план «прямой ход»
2.5; уроки прохода №5 H0): клауза целевой не рождает строку общего
раздела; заплата, отвергнутая замком по содержанию, закрывает находку
кодом и её повтор отбраковывается; якоря замечаний без скобок листинга."""
from cdpm import exchange as ex


def _doc():
    return {"rules": [
        {"id": "R-S1-абз-7", "kind": "HARD", "zone": "PAIRING",
         "statement": "x", "sources": [{"sid": "S1", "anchor": "абз.7"}],
         "template_fit": {"category": "A", "family": "rest_floors",
                          "params": {"min_rest_home": {"value": 720, "quote": "12:00"}}}}],
        "objectives": [
        {"id": "O-S3-стр-1-абз-2", "zone": "PAIRING", "direction": "minimize",
         "statement": "мин", "sources": [{"sid": "S3", "anchor": "стр-1-абз-2"}]}],
        "questions": []}


def test_objective_clause_does_not_spawn_general_section_row():
    doc = _doc()
    dup = {"id": "R-S3-стр-1-абз-2", "kind": "DEF", "zone": "PAIRING",
           "statement": "Целевая функция PAIRING: total_pairing_cost",
           "sources": [{"sid": "S3", "anchor": "стр-1-абз-2"}]}
    why = ex._objective_anchor_conflict(doc, dup)
    assert why and "уже потреблена целевой O-S3-стр-1-абз-2" in why
    # Непосаженное HARD на той же клаузе — тоже дубль; посаженное A — законно.
    hard_c = dict(dup, kind="HARD", template_fit={"category": "C", "gap": "x"})
    assert ex._objective_anchor_conflict(doc, hard_c)
    hard_a = dict(dup, kind="HARD", template_fit={"category": "A", "family": "duty_limits",
                                                  "params": {}})
    assert ex._objective_anchor_conflict(doc, hard_a) is None
    # Чужой якорь — конфликта нет.
    other = dict(dup, sources=[{"sid": "S1", "anchor": "абз.9"}])
    assert ex._objective_anchor_conflict(doc, other) is None


def test_code_terminal_closures_only_for_content_locks():
    pack = {"remarks": [
        {"id": "A1-001", "defect_class": "искажение",
         "target": {"rule": "R-S2-строка-6", "question": None},
         "source_refs": [{"sid": "S2", "anchor": "строка-6"}]},
        {"id": "A1-002", "defect_class": "пропуск",
         "target": {"rule": None, "question": None},
         "source_refs": [{"sid": "S1", "anchor": "абз.3"}]}]}
    rejected = [
        {"answer": "A1-001", "reasons": ["R-S2-строка-6: параметр 'workload_spread_threshold' "
                                         "принадлежит ни одной семье базы, не 'balanced_workload'"]},
        {"answer": "A1-002", "reasons": ["заплата не объект: 'abc'"]}]
    closed = ex.code_terminal_closures(rejected, pack)
    assert set(closed) == {ex.remark_key(pack["remarks"][0])}
    reason = closed[ex.remark_key(pack["remarks"][0])]
    assert reason.startswith("требование замка кода") and "терминально" in reason


def test_omission_on_code_verdict_clause_is_rejected():
    verdicts = {("S1", "абз.3"): {"verdict": "не правило", "reason": "канон композиции",
                                  "by": "code"},
                ("S1", "абз.9"): {"verdict": "не правило", "reason": "пример", "by": "model"}}
    rm = {"defect_class": "пропуск", "target": {"rule": None, "question": None},
          "source_refs": [{"sid": "S1", "anchor": "[абз.3]"}]}
    why = ex.omission_closed_by_code(rm, verdicts)
    assert why and "канон композиции" in why and "пропуском не являются" in why
    # Вердикт МОДЕЛИ спорить можно — пропуск по такой клаузе законен.
    rm2 = dict(rm, source_refs=[{"sid": "S1", "anchor": "абз.9"}])
    assert ex.omission_closed_by_code(rm2, verdicts) is None
    # Не «пропуск» — не судится этим замком.
    assert ex.omission_closed_by_code(dict(rm, defect_class="искажение"), verdicts) is None


def test_strip_anchor_brackets():
    assert ex.strip_anchor_brackets("[абз.3]") == "абз.3"
    assert ex.strip_anchor_brackets(" абз.3 ") == "абз.3"
    assert ex.strip_anchor_brackets("лист-«VMP-H0-2»-строка-6") == "лист-«VMP-H0-2»-строка-6"
    assert ex.strip_anchor_brackets(None) == ""
