"""COSMOS/CDPM-202: a single duration introduced by a preposition is not a rate."""
import pytest

from cdpm import answers, family_params as fp, model


MAGNITUDES = [("Перерыв в 30 минут", 30), ("Задержка в 2 часа", 120),
              ("Интервал в 15 минут", 15), ("Стыковка в 45 минут", 45),
              ("Резерв в 3 часа", 180),
              ("Минимальный отдых — перерыв в 12 часов", 720),
              ("Перерыв в 2,5 часа", 150), ("Перерыв за 10 минут", 10)]


@pytest.mark.parametrize("raw", [
    "Не правило: покрывается вызовом за 2 часа (см. журнал)",
    "Не перерыв в 2 часа", "Нет ограничения в 2 часа", "Без перерыва в 2 часа",
])
@pytest.mark.parametrize("unit", ["duration_minutes", "credit_hours", "hours_per_credit_hour"])
def test_magnitude_does_not_turn_unresolved_negation_into_a_value(raw, unit):
    assert fp.parse_value(raw, unit) is None


def test_magnitude_can_keep_an_explicit_upper_bound():
    assert fp.parse_value("Перерыв не более 30 минут", "duration_minutes")[0] == 30
    assert fp.parse_value("Не более перерыва в 30 минут", "duration_minutes")[0] == 30


@pytest.mark.parametrize("raw,want", MAGNITUDES)
def test_magnitude_is_read_without_discarding_a_real_period(raw, want):
    assert fp.period_qualifier(raw, "duration_minutes") == ""
    assert fp.parse_value(raw, "duration_minutes")[0] == want


@pytest.mark.parametrize("raw,want", MAGNITUDES)
def test_magnitude_answer_reaches_the_carrier(raw, want):
    from test_cdpm_k02_number_grammar import _prepared, QID
    doc, rule, question = _prepared()
    result = answers.apply_answers(doc, {QID: raw})
    assert result["applied"] == 1
    assert question["answer"]["raw"] == raw
    assert question["answer"]["status"] == "применён"
    assert rule["template_fit"]["params"]["min_rest_outstation"]["value"] == want
    assert not model.is_open_question(question)
    assert QID not in rule["blockers"]


@pytest.mark.parametrize("raw", [
    "10 часов в 2 дня", "В 2 дня: 10 часов", "10 часов. В 2 дня.",
    "10 часов за каждые 2 дня", "Каждые 2 дня: 10 часов",
    "10 часов в течение 2 дней", "10 часов/2 дня",
    "Перерыв в 30 минут в неделю", "В неделю: перерыв в 30 минут",
    "Перерыв в 30 минут. В неделю.",
    "Перерыв в 30 минут. Ежемесячно.",
    "Перерыв в 30 минут; не более 20 часов в месяц",
])
def test_rate_or_separate_qualifier_cannot_hide_behind_magnitude(raw):
    assert fp.period_qualifier(raw, "duration_minutes")
    assert fp.parse_value(raw, "duration_minutes") is None


def test_instruments_cover_both_sides_of_preposition_number_form():
    from test_cdpm_k02_price_tool import lattice, price
    rows = lattice.magnitude_lattice()
    assert len(rows) == len(set(rows)) == 24
    assert "Перерыв в 10 минут" in rows
    assert "Резерв за 2 часов" in rows
    corpus = set(price.corpus())
    assert set(rows) <= corpus
    rates = lattice.magnitude_lattice(periodic=True)
    assert len(rates) == 24 and set(rates) <= corpus
    for raw in rows:
        assert fp.parse_value(raw, "duration_minutes") is not None
    for raw in rates:
        assert fp.period_qualifier(raw, "duration_minutes")
        assert fp.parse_value(raw, "duration_minutes") is None
