"""COSMOS book V: a separately labelled penalty weight is not the duration."""
import json
from pathlib import Path

import pytest

from cdpm import answers, family_params as fp, model
from _k02_work_budget import k02_work_budget


BOOK = Path(__file__).resolve().parents[1] / "open-bench/polyaris-nano/etalon/ANSWER-BOOK-MACHINE.json"
BOOK_V = next(row["answer"] for row in json.loads(BOOK.read_text(encoding="utf-8"))["regex"]
              if row["pattern"] == "продлени")
NOTE = "Продление — мягкое предпочтение со штрафом (вес 15 за смену с превышением)."


@pytest.mark.parametrize("raw,want", [
    (BOOK_V, 720),
    ("12 часов. " + NOTE, 720),
    ("12 ч. " + NOTE, 720),
    ("10:00. Штраф (вес 15 за смену).", 600),
    ("600 минут. Штраф (вес 2,5 за рейс с превышением).", 600),
    ("Пояснение. Длительность 600 минут. Штраф (вес 20 за полёт).", 600),
    ("600 минут.\nШтраф\t(вес 20 за\nсмену).", 600),
])
def test_separate_penalty_keeps_absolute_duration(raw, want):
    assert fp.period_qualifier(raw, "duration_minutes") == ""
    value, quote = fp.parse_value(raw, "duration_minutes")
    assert value == want
    assert quote == fp._sentences(raw)[1 if raw.startswith("Пояснение.") else 0]


def test_actual_book_answer_is_applied_not_only_parsed():
    from test_cdpm_k02_number_grammar import _prepared, QID
    doc, rule, question = _prepared()
    result = answers.apply_answers(doc, {QID: BOOK_V})
    assert result["applied"] == 1
    assert question["answer"]["raw"] == BOOK_V
    assert question["answer"]["status"] == "применён"
    assert rule["template_fit"]["params"]["min_rest_outstation"]["value"] == 720
    assert not model.is_open_question(question)
    assert QID not in rule["blockers"]


@pytest.mark.parametrize("raw", [
    "100 условных единиц за календарный день командировки",
    "12 часов в неделю. " + NOTE,
    "12 часов. В неделю. " + NOTE,
    "12 часов. " + NOTE + " В неделю.",
    "12 часов. " + NOTE + " Норма 10 часов в неделю.",
    "12 часов. " + NOTE + " В каждые 2 дня.",
    "12 часов. Норма 15 за смену.",
    "12 часов. Вес 15 за смену.",
    "12 часов. Не штраф (вес 15 за смену).",
    "12 часов; штраф (вес 15 за смену).",
    "12 часов. штраф (вес 15 за смену).",
    "12 часов. Штраф (вес -15 за смену).",
    "12 часов. Штраф (вес пятнадцать за смену).",
    "12 часов. Штраф (вес 15 за смену",
    "12 часов. Штраф (вес 15 за смену и 20 за рейс).",
    "12 часов. Штраф (вес 15 за смену); за месяц.",
    "Штраф (вес 15 за смену). 12 часов.",
    "Сведения. Штраф (вес 15 за смену).",
])
def test_real_or_unresolved_period_is_still_refused(raw):
    assert fp.period_qualifier(raw, "duration_minutes")
    assert fp.parse_value(raw, "duration_minutes") is None


@pytest.mark.parametrize("unit", ["count", "credit_hours", "hours_per_credit_hour"])
def test_penalty_exception_does_not_apply_to_other_numeric_contracts(unit):
    assert fp.period_qualifier("12. Штраф (вес 15 за смену).", unit)
    assert fp.parse_value("12. Штраф (вес 15 за смену).", unit) is None


def test_penalty_period_scan_keeps_the_linear_work_budget(k02_work_budget):
    raw = "Пояснение. " * 1000 + "600 минут. " + NOTE
    with k02_work_budget(raw) as work:
        assert fp.parse_value(raw, "duration_minutes")[0] == 600
    assert work.work <= work.limit


def test_price_corpus_contains_both_penalty_and_real_period():
    from test_cdpm_k02_price_tool import price
    rows = price.PENALTY_ANSWERS
    corpus = set(price.corpus())
    assert len(rows) == len(set(rows)) == 12
    for raw in rows:
        assert raw in corpus and raw + " В неделю." in corpus
        assert fp.parse_value(raw, "duration_minutes")[0] in (600, 720)
        assert fp.parse_value(raw + " В неделю.", "duration_minutes") is None
