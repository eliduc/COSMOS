"""CDPM-194: a period must never disappear into an absolute value.

The current numeric-unit API has no declared parameter period. In that
contract a qualified periodic norm is unsupported, even when its numeric
magnitude has a recognized unit. Preserve the raw answer and ask again.
"""
import pytest

from cdpm import answers, family_params as fp, model


PERIODS = ["в неделю", "в неделях", "в месяц", "за календарный месяц",
           "в сутки", "за день", "в час", "за квартал", "на год",
           "за смену", "на рейс", "еженедельно", "/неделю",
           "каждые сутки", "каждый месяц", "за каждые 10 часов",
           "каждые 10 часов", "в минуту", "в минутах", "в часах",
           "в днях", "в сутках", "в течение каждого полного месяца",
           "на протяжении недели", "в пределах недели", "ежечасно",
           "поминутно", "в течение одного месяца", "за 2 календарные недели"]
POSITIONS = ["10 часов {period}", "{period}: 10 часов",
             "10 часов. {period}."]


@pytest.mark.parametrize("period", PERIODS)
@pytest.mark.parametrize("form", POSITIONS)
@pytest.mark.parametrize("unit", ["duration_minutes", "credit_hours"])
def test_period_cannot_be_discarded_by_value_parser(period, form, unit):
    raw = form.format(period=period)
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None


@pytest.mark.parametrize("period", PERIODS)
@pytest.mark.parametrize("form", POSITIONS)
def test_periodic_rest_answer_remains_open_and_unwritten(period, form):
    from test_cdpm_k02_number_grammar import _prepared, QID
    doc, rule, question = _prepared()
    raw = form.format(period=period)
    result = answers.apply_answers(doc, {QID: raw})
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert question["answer"]["raw"] == raw
    assert question["answer"]["status"] == "не разобран"
    assert "период" in question["answer"]["note"]
    assert model.is_open_question(question)
    assert QID in rule["blockers"]
    assert "min_rest_outstation" not in rule["template_fit"]["params"]


@pytest.mark.parametrize("raw,value", [("10 часов", 600),
    ("не более 10 часов", 600), ("600 минут", 600), ("10:30", 630)])
def test_absolute_rest_forms_are_still_applied(raw, value):
    from test_cdpm_k02_number_grammar import _prepared, QID
    doc, rule, question = _prepared()
    result = answers.apply_answers(doc, {QID: raw})
    assert result["applied"] == 1
    assert rule["template_fit"]["params"]["min_rest_outstation"]["value"] == value
    assert question["answer"]["status"] == "применён"
    assert not model.is_open_question(question)
    assert QID not in rule["blockers"]


@pytest.mark.parametrize("period", ["в неделю", "в сутки", "за смену"])
def test_repair_cannot_restore_a_legacy_periodic_answer_as_absolute(period):
    from test_cdpm_k02_number_grammar import _prepared
    doc, rule, question = _prepared()
    question["answer"] = {"raw": f"10 часов {period}",
                          "status": "применён", "revision": 1}
    notes = []
    touched = fp._reapply_values(doc, fp.family_coverage(doc), notes)
    assert touched == []
    assert "min_rest_outstation" not in rule["template_fit"]["params"]
    assert any("не удалось разобрать" in note for note in notes)


@pytest.mark.parametrize("raw", ["10 часов/неделю", "10 часов/10 часов",
                                  "10 часов/месяц"])
def test_attached_period_denominator_is_not_silent_punctuation(raw):
    assert fp.period_qualifier(raw, "duration_minutes")
    assert fp.parse_value(raw, "duration_minutes") is None


@pytest.mark.parametrize("raw", ["10 часов в неделю", "10 часов в сутки",
                                  "10 часов за смену"])
def test_canonical_string_value_cannot_bypass_the_period_guard(raw):
    value, problem = fp.canonical_value(raw, "", "duration_minutes")
    assert value is None
    assert problem
