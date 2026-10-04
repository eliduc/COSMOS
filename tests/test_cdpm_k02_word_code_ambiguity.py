"""CDPM-217: whitespace does not prove where a word-like identifier ends.

Without a registry/domain oracle, a multiword phrase can be one designation
or an annotated code. Refuse it instead of inventing extra carrier values.
Explicit lists and compact digit-bearing codes keep their declared syntax;
typed carrier identifiers retain their whole names.
"""
import pytest

from cdpm import answers, family_params as fp, model


AMBIGUOUS = [
    "Airbus A320", "AIRBUS A320", "airbus a320", "Airbus — A320",
    "Airbus: A320", "Airbus\nA320", "Airbus (A320)", "A320 Airbus",
    "Boeing 737-800", "Embraer E190-E2", "ATR 72-600", "737 MAX",
    "Base SVO", "BASE SVO", "base svo", "UnknownLabel X9",
    "Airbus A320, Boeing 737", "Airbus A320 и Boeing 738",
    "A320, Airbus B738", "Типы: Airbus A320", "A320. Airbus B738.",
]


@pytest.mark.parametrize("raw", AMBIGUOUS)
def test_free_word_phrase_is_not_fragmented(raw):
    assert fp.parse_value(raw, "code_list") is None
    assert fp.code_context_problem(raw, "code_list")


@pytest.mark.parametrize("raw", AMBIGUOUS)
def test_ambiguous_answer_keeps_question_open_and_carrier_empty(raw):
    from test_cdpm_k02_number_grammar import _prepared_fleet, QID_FLEET
    doc, rule, question = _prepared_fleet()
    result = answers.apply_answers(doc, {QID_FLEET: raw})
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert rule["template_fit"]["params"] == {}
    assert model.is_open_question(question) and QID_FLEET in rule["blockers"]
    assert question["answer"]["raw"] == raw
    assert question["answer"]["status"] == "не разобран"
    assert "контекст" in question["answer"]["note"]


@pytest.mark.parametrize("raw", [
    "Airbus A320", "Airbus A320, Boeing 737", "Base SVO",
])
def test_repair_does_not_restore_legacy_word_fragmentation(raw):
    from test_cdpm_k02_number_grammar import _prepared_fleet
    doc, rule, question = _prepared_fleet()
    question["answer"] = {"raw": raw, "status": "применён", "revision": 1}
    notes = []
    assert fp._reapply_values(doc, fp.family_coverage(doc), notes) == []
    assert rule["template_fit"]["params"] == {}
    assert any("не удалось разобрать" in note for note in notes)


@pytest.mark.parametrize("raw,want", [
    ("A320, B738", "A320, B738"), ("A320", "A320"),
    ("Тип: B738", "B738"), ("A320 B738", "A320, B738"),
    ("A320\nB738", "A320, B738"), ("320 737", "320, 737"),
    ("SVO, DME", "DME, SVO"), ("SVO; DME", "DME, SVO"),
    ("SVO и DME", "DME, SVO"), ("Alpha, Bravo", "Alpha, Bravo"),
    ("MAX", "MAX"), ("737-MAX", "737-MAX"),
])
def test_unambiguous_declared_list_syntax_still_lands(raw, want):
    from test_cdpm_k02_number_grammar import _prepared_fleet, QID_FLEET
    assert fp.parse_value(raw, "code_list")[0] == want
    doc, rule, question = _prepared_fleet()
    result = answers.apply_answers(doc, {QID_FLEET: raw})
    assert result["applied"] == 1 and result["unparsed"] == 0
    assert rule["template_fit"]["params"]["fleet_types"]["value"] == want
    assert not model.is_open_question(question)
    assert QID_FLEET not in rule["blockers"]


@pytest.mark.parametrize("raw", ["SVO DME", "Alpha Bravo"])
def test_word_like_values_require_an_explicit_list_boundary(raw):
    # This declared compatibility restriction avoids guessing word roles.
    # Use commas/semicolons/conjunction, or a typed whole identifier.
    assert fp.parse_value(raw, "code_list") is None


@pytest.mark.parametrize("raw", ["Airbus A320", "Boeing 737 MAX", "Base SVO"])
@pytest.mark.parametrize("unit", ["code", "code_list"])
def test_typed_names_are_not_reparsed_by_the_free_text_guard(raw, unit):
    assert fp.canonical_value(raw, "", unit) == (raw, None)


def test_price_generator_contains_the_ambiguity_and_healthy_neighbours():
    from test_cdpm_k02_price_tool import price
    samples = set(price.corpus())
    assert set(AMBIGUOUS) <= samples
    assert {"SVO, DME", "SVO DME", "A320 B738", "Alpha, Bravo"} <= samples
