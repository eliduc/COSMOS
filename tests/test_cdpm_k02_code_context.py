"""K02: unread context must not silently select the code it negates.

These expectations describe the answer contract, not parser output. The
same raw answers must stay open in application and repair. A typed carrier
is a different contract: preserve its whole identifier, never extract a code.
"""
import pytest

from cdpm import answers, family_params as fp, model


BAD_LEADS = ["Не", "Нет", "Кроме", "Исключая", "Ту", "Без",
             "За исключением", "Возможно", "Не только", "Вместо"]
BAD = [f"{lead}{sep}A320" for lead in BAD_LEADS
       for sep in (" ", ": ", "—", "–")]
BAD += ["A320 не используем", "A320 кроме B738", "A320 и не B738",
        "A320. Кроме B738.", "Не этот тип. A320.",
        "A320. Неверно.", "A320 только при согласовании",
        "Типы: A320 (кроме B738)", "¬ A320", "!= A320"]


@pytest.mark.parametrize("raw", BAD)
@pytest.mark.parametrize("unit", ["code", "code_list"])
def test_unread_context_cannot_produce_a_selected_code(raw, unit):
    assert fp.parse_value(raw, unit, fp.default_domain(unit)) is None


@pytest.mark.parametrize("raw", BAD)
def test_context_answer_stays_open_and_does_not_write_a_carrier(raw):
    from test_cdpm_k02_number_grammar import _prepared_fleet, QID_FLEET
    doc, rule, question = _prepared_fleet()
    result = answers.apply_answers(doc, {QID_FLEET: raw})
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert question["answer"]["raw"] == raw
    assert question["answer"]["status"] == "не разобран"
    assert model.is_open_question(question)
    assert QID_FLEET in rule["blockers"]
    assert rule["template_fit"]["params"] == {}
    assert question["answer"]["note"]


@pytest.mark.parametrize("raw,want", [
    ("A320", "A320"), ("B738, A320, A320", "A320, B738"),
    ("A320 B738", "A320, B738"), ("A320\nB738", "A320, B738"),
    ("Типы: A320 и B738", "A320, B738"),
    ("Тип судна—A320", "A320"), ("Типы:A320", "A320"),
    ("Флот: 320, 737. Конец", "320, 737"),
    ("Типы, а именно A320, B738", "A320, B738"),
    ("A320-200", "A320-200"), ("A_320", "A_320"),
])
def test_declared_code_lists_still_land_the_exact_value(raw, want):
    from test_cdpm_k02_number_grammar import _prepared_fleet, QID_FLEET
    doc, rule, question = _prepared_fleet()
    result = answers.apply_answers(doc, {QID_FLEET: raw})
    assert result["applied"] == 1 and result["unparsed"] == 0
    assert rule["template_fit"]["params"]["fleet_types"]["value"] == want
    assert not model.is_open_question(question)
    assert QID_FLEET not in rule["blockers"]


@pytest.mark.parametrize("raw", ["Не A320", "Кроме—A320",
                                  "A320. Кроме B738."])
def test_repair_cannot_restore_a_legacy_contextual_code_answer(raw):
    from test_cdpm_k02_number_grammar import _prepared_fleet
    doc, rule, question = _prepared_fleet()
    question["answer"] = {"raw": raw, "status": "применён", "revision": 1}
    notes = []
    assert fp._reapply_values(doc, fp.family_coverage(doc), notes) == []
    assert rule["template_fit"]["params"] == {}
    assert any("не удалось разобрать" in note for note in notes)


@pytest.mark.parametrize("raw", ["", " ", [], [""], ["A320", 1], "A320,,B738"])
@pytest.mark.parametrize("unit", ["code", "code_list"])
def test_carrier_codes_require_nonempty_string_elements(raw, unit):
    value, reason = fp.canonical_value(raw, "", unit)
    assert value is None and reason


@pytest.mark.parametrize("raw,unit,want", [
    ("A320", "code", "A320"), ("737-800", "code", "737-800"),
    (["B738", "A320"], "code_list", "A320, B738"),
    ("B738, A320", "code_list", "A320, B738"),
    ("Полярис-Р200", "code", "Полярис-Р200"),
    (["Полярис-Р200"], "code_list", "Полярис-Р200"),
    ("A" * 17, "code", "A" * 17),  # typed carrier has no 16-char limit
])
def test_canonical_code_carriers_keep_their_declared_forms(raw, unit, want):
    assert fp.canonical_value(raw, "", unit) == (want, None)


def test_context_note_names_the_actual_unread_word():
    from test_cdpm_k02_number_grammar import _prepared_fleet, QID_FLEET
    doc, rule, question = _prepared_fleet()
    answers.apply_answers(doc, {QID_FLEET: "Не A320"})
    assert "непрочитанный контекст «Не»" in question["answer"]["note"]


def test_price_corpus_keeps_the_entire_new_context_neighbourhood():
    from test_cdpm_k02_price_tool import price
    assert set(BAD) <= set(price.corpus())
    assert price._cause(fp, "Не A320", "code_list") == (
        "непрочитанный контекст обозначений")


def test_invalid_carrier_code_is_not_counted_as_family_coverage():
    from test_cdpm_k02_number_grammar import _prepared_fleet
    doc, rule, question = _prepared_fleet()
    rule["template_fit"]["params"]["fleet_types"] = {
        "value": ["A320", {"code": "B738"}], "quote": "исходный ответ"}
    coverage = fp.family_coverage(doc)["fleet_exclusions"]
    assert "fleet_types" not in coverage["given"]
    assert "fleet_types" in coverage["unreadable"]
    assert "fleet_types" in coverage["missing"]


def test_cyrillic_typed_carrier_still_covers_its_family():
    from test_cdpm_k02_number_grammar import _prepared_fleet
    doc, rule, question = _prepared_fleet()
    rule["template_fit"]["params"]["fleet_types"] = {
        "value": "Полярис-Р200", "quote": "исходный ответ"}
    coverage = fp.family_coverage(doc)["fleet_exclusions"]
    assert "fleet_types" in coverage["given"]
    assert "fleet_types" not in coverage["unreadable"]
