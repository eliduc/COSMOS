"""CDPM-290: an answer counting objects must not become credit hours.

No model, HTTP or solver calls: real in-memory answer/landing/consumer path.
The two hour units share a grammar; count is a separate declared boundary.
"""
import copy

import pytest

from cdpm import answers, family_params as fp, model
from vmp import execution_manifest as execution
from test_cdpm_applicability_loss import _prepared


WRONG = [f"75 {word}" for word in (
    "сегментов", "явок", "рейсов", "пар", "посадок", "вылетов",
    "смен", "сессий", "циклов", "баллов",
)] + ["75 лётных сегментов", "75 блок-сегментов", "75,5 явок",
     "75 (сегментов)", "75 (75 рейсов)", "75 (74 часов)",
     "75. Это норма."]  # existing line-number refusal
RIGHT = [(raw, 75) for raw in (
    "75 часов", "75 блок-часов", "75 лётных часов", "75 часов налёта",
    "не более 75 часов", "75 час", "75 ч.", "75", "не более 75",
    "75 кредитных часов", "75 блок‑часов", "75 летных часов",
    "75 кредит-часов", "75 кредит‑часов",
)] + [("75,5 часов", 75.5), ("1 кредитный час", 1),
     ("2 лётных часа", 2), ("75 (75 часов)", 75),
     ("75 часов (75)", 75)]


@pytest.mark.parametrize("unit", ["credit_hours", "hours_per_credit_hour"])
@pytest.mark.parametrize("raw", WRONG)
def test_foreign_subject_cannot_be_read_as_hours(unit, raw):
    assert fp.parse_value(raw, unit) is None
    value, problem = fp.canonical_value(raw, "", unit)
    assert value is None and problem


@pytest.mark.parametrize("unit", ["credit_hours", "hours_per_credit_hour"])
@pytest.mark.parametrize("raw,want", RIGHT)
def test_explicit_hours_and_bare_number_still_read(unit, raw, want):
    result = fp.parse_value(raw, unit)
    assert result is not None and result[0] == want


@pytest.mark.parametrize("raw", WRONG)
def test_real_answer_keeps_question_open_and_consumer_empty(raw):
    doc, rule, question, name = _prepared("credit_hours")
    before = copy.deepcopy(rule)
    result = answers.apply_answers(doc, {question["id"]: raw})
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert question["answer"]["raw"] == raw
    assert question["answer"]["status"] == "не разобран"
    assert question["answer"]["note"]
    assert model.is_open_question(question)
    assert question["id"] in rule["blockers"]
    assert rule == before
    assert name not in {p["name"] for p in execution.rule_params(rule)}


@pytest.mark.parametrize("raw,want", RIGHT)
def test_healthy_answer_reaches_consumer_with_trace(raw, want):
    doc, rule, question, name = _prepared("credit_hours")
    result = answers.apply_answers(doc, {question["id"]: raw})
    assert result["applied"] == 1 and result["unparsed"] == 0
    assert not model.is_open_question(question)
    assert question["id"] not in rule["blockers"]
    cell = rule["template_fit"]["params"][name]
    assert cell["value"] == want and cell["quote"] == raw
    received = next(p for p in execution.rule_params(rule) if p["name"] == name)
    assert received["value"] == want and received["quote"] == raw


def test_bad_historical_answer_is_not_reapplied_as_hours():
    doc, rule, question, name = _prepared("credit_hours")
    question["answer"] = {"raw": "75 сегментов", "status": "применён",
                          "revision": 1}
    notes = []
    assert fp._reapply_values(doc, fp.family_coverage(doc), notes) == []
    assert name not in rule["template_fit"]["params"]
    assert any("не удалось разобрать" in note for note in notes)


def test_count_contract_is_not_changed_by_the_hour_fix():
    assert fp.parse_value("75 смен", "count")[0] == 75
    assert fp.parse_value("3 суток", "count") is None
    assert fp.parse_value("3 часа", "count") is None


@pytest.mark.parametrize("unit", ["credit_hours", "hours_per_credit_hour"])
@pytest.mark.parametrize("raw,want", [
    ("75 блочных часов", 75), ("1 блочный час", 1),
    ("2 блочных часа", 2), ("75,5 блочных часов", 75.5),
])
def test_block_hour_adjective_is_an_explicit_hour_phrase(unit, raw, want):
    assert fp.parse_value(raw, unit) == (want, raw)


@pytest.mark.parametrize("unit", ["credit_hours", "duration_minutes"])
@pytest.mark.parametrize("phrase", [
    "сегментов", "явок", "рейсов", "пар", "посадок", "вылетов",
    "смен", "сессий", "циклов", "баллов", "лётных сегментов",
    "блок-сегментов", "блочных сегментов", "(сегментов)",
])
def test_rejected_quantity_names_exact_unread_words_without_reinterpreting(
        unit, phrase):
    raw = "75 " + phrase
    doc, rule, q, name = _prepared(unit)
    before = copy.deepcopy(rule)
    result = answers.apply_answers(doc, {q["id"]: raw})
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert q["answer"]["raw"] == raw and model.is_open_question(q)
    assert rule == before and q["id"] in rule["blockers"]
    assert name not in {p["name"] for p in execution.rule_params(rule)}
    assert "«" + phrase.strip("()") + "»" in q["answer"]["note"]
    assert "непрочитан" in q["answer"]["note"]
    assert "исходный ответ сохранён" in q["answer"]["note"]


@pytest.mark.parametrize("raw,want", [
    ("75 блочных часов", 75), ("75,5 блочных часов", 75.5),
])
def test_block_hour_answer_reaches_consumer_without_refusal_note(raw, want):
    doc, rule, q, name = _prepared("credit_hours")
    result = answers.apply_answers(doc, {q["id"]: raw})
    assert result["applied"] == 1 and result["unparsed"] == 0
    assert "непрочитан" not in q["answer"].get("note", "")
    cell = rule["template_fit"]["params"][name]
    assert cell["value"] == want and cell["quote"] == raw
    received = next(p for p in execution.rule_params(rule) if p["name"] == name)
    assert received["value"] == want and received["quote"] == raw


@pytest.mark.parametrize("raw", [
    "75 блочных сегментов", "75 блочных часов в месяц",
    "75 блочных часов для базы SVO", "75 блочных часов (74 часа)",
])
def test_block_adjective_does_not_remove_other_guards(raw):
    assert fp.parse_value(raw, "credit_hours") is None


@pytest.mark.parametrize("unit,raw", [
    ("credit_hours", "75 часов"), ("hours_per_credit_hour", "1,5 часа"),
    ("credit_hours", "75 блочных часов"),
    ("duration_minutes", "600 минут"), ("duration_minutes", "10:00"),
    ("duration_minutes", "10 ч 30 мин"),
    ("count", "75 сегментов"), ("time_of_day_minutes", "09:00"),
])
def test_quantity_diagnostic_is_silent_on_readable_and_out_of_scope_values(unit, raw):
    assert fp.unread_quantity_context(raw, unit) == ""


@pytest.mark.parametrize("raw,reason", [
    ("75 сегментов в месяц", "период"),
    ("75 сегментов для базы SVO", "оговорка применимости"),
    ("75 cегментов", "двумя алфавитами"),
    ("1) 75 сегментов", "номер строки анкеты"),
    ("75 минут", "слово «минут»"),
])
def test_quantity_diagnostic_does_not_hide_specific_existing_reasons(raw, reason):
    doc, _, q, _ = _prepared("credit_hours")
    result = answers.apply_answers(doc, {q["id"]: raw})
    assert result["applied"] == 0
    assert reason in q["answer"]["note"]
    assert "непрочитанные слова" not in q["answer"]["note"]


@pytest.mark.parametrize("unit,bad,advice,good", [
    ("duration_minutes", "75 недель", "в минутах, часах или сутках", "600 минут"),
    ("time_of_day_minutes", "75 часов", "в формате ЧЧ:ММ", "09:00"),
])
def test_foreign_unit_advice_offers_a_readable_form(unit, bad, advice, good):
    doc, _, q, _ = _prepared(unit)
    result = answers.apply_answers(doc, {q["id"]: bad})
    assert result["applied"] == 0
    assert advice in q["answer"]["note"]
    assert "число без слова единицы" not in q["answer"]["note"]
    assert fp.parse_value(good, unit) is not None


@pytest.mark.parametrize("unit", ["credit_hours", "duration_minutes"])
def test_answer_note_uses_the_rejecting_reader(monkeypatch, unit):
    name = "_duration_values" if unit == "duration_minutes" else "_number_values"
    original = getattr(fp, name)
    reached = []

    def traced(*args, **kwargs):
        collector = kwargs.get("rejected_words")
        if unit == "duration_minutes" and len(args) > 1:
            collector = args[1]
        if collector is not None:
            reached.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(fp, name, traced)
    doc, _, q, _ = _prepared(unit)
    result = answers.apply_answers(doc, {q["id"]: "75 сегментов"})
    assert result["applied"] == 0
    assert reached == ["75 сегментов"]
    assert "«сегментов»" in q["answer"]["note"]


@pytest.mark.parametrize("unit", ["credit_hours", "hours_per_credit_hour"])
@pytest.mark.parametrize("raw", [
    "1,5 для всех баз увеличенного состава", "1,5 для всех кроме SVO",
    "1,5 для каждого члена экипажа при согласии", "1,5 для любого экипажа ночью",
    "1,5 сегментов для всех", "1,5 для всех (2 часа)",
])
def test_bare_hour_universal_tail_does_not_consume_a_restriction_or_subject(unit, raw):
    assert fp.parse_value(raw, unit) is None
