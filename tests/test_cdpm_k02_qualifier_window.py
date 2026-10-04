"""K02: an explicit own unit must not erase an unsupported qualifier."""
import pytest

from cdpm import answers
from cdpm import family_params as fp
from cdpm import model

OWN = [("часов", "duration_minutes", 600),
       ("минут", "duration_minutes", 10),
       ("суток", "duration_minutes", 14400),
       ("дней", "duration_minutes", 14400),
       ("часов", "credit_hours", 10),
       ("часов", "hours_per_credit_hour", 10)]
UNSUPPORTED = ["в процентах", "в долях", "в промилле", "в секунду",
               "в секундах", "в неделях", "в месяцах", "в кварталах",
               "в годах", "в декадах", "в полугодиях"]
PERIODS = ["в месяц", "в неделю", "за квартал", "на год", "по декаду",
           "в полугодие", "в полмесяца", "в мес."]


@pytest.mark.parametrize("word,unit,value", OWN)
@pytest.mark.parametrize("tail", UNSUPPORTED)
def test_own_unit_does_not_hide_an_unsupported_qualifier(word, unit, value, tail):
    raw = f"не более 10 {word} {tail}"
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
    assert fp.foreign_unit_word(raw, unit) == tail.split()[-1]


@pytest.mark.parametrize("word,unit,value", OWN)
@pytest.mark.parametrize("tail", PERIODS)
def test_recognized_calendar_annotation_cannot_erase_its_period(
        word, unit, value, tail):
    # CDPM-194: the former expectation silently discarded the period.
    # Keep every former input, now require refusal and the named period.
    raw = f"10 {word} {tail}"
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None
    assert fp.period_qualifier(raw, unit) == tail


@pytest.mark.parametrize("word,unit,value", OWN)
def test_the_same_own_units_without_a_period_keep_their_magnitude(word, unit, value):
    got = fp.parse_value(f"10 {word}", unit, fp.default_domain(unit))
    assert got is not None and got[0] == value


@pytest.mark.parametrize("credit", [False, True])
@pytest.mark.parametrize("tail", UNSUPPORTED)
def test_unsupported_qualifier_keeps_real_answer_and_carrier_blocked(credit, tail):
    from test_cdpm_k02_number_grammar import (
        _prepared, _prepared_credit_hours, QID, QID_CH)
    prepare, qid, param = ((_prepared_credit_hours, QID_CH,
                           "workload_spread_threshold") if credit else
                          (_prepared, QID, "min_rest_outstation"))
    doc, rule, question = prepare()
    # The credit fixture starts with an empty one-parameter landing. Refusal
    # must preserve that known incompleteness, not manufacture a valid value.
    before_issues = model.validate_model(doc)
    assert len(before_issues) == (2 if credit else 0)
    assert all(issue.startswith(rule["id"] + ":") and "params" in issue
               for issue in before_issues)
    raw = f"не более 10 часов {tail}"
    outcome = answers.apply_answers(doc, {qid: raw})
    assert outcome["applied"] == 0 and outcome["unparsed"] == 1
    assert question["answer"]["status"] == "не разобран"
    assert tail.split()[-1] in question["answer"]["note"]
    assert model.is_open_question(question) and qid in rule["blockers"]
    assert param not in rule["template_fit"]["params"]
    assert model.validate_model(doc) == before_issues
