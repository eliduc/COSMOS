"""CDPM347: real closed definitions, not keyword-matching all prose rules."""
import copy
import json
from pathlib import Path

import pytest

from cdpm import answers, model as m, question_editions as qe
from test_cdpm_data_scope_editions import initial, report
from test_cdpm_data_scope_residuals import patch


REAL_LISTS = [
    ("Должности пилотов: КВС и Второй пилот.",
     "Должности пилотов: КВС, Второй пилот и Бортинженер.",
     "Бортинженер", {"КВС", "Второй пилот"}),
    ("Должности пилотов в наборе — КВС и Второй пилот (закрытый перечень).",
     "Должности пилотов в наборе — КВС, Второй пилот и Бортинженер (закрытый перечень).",
     "Бортинженер", {"КВС", "Второй пилот"}),
    ("Должности пилотов в наборе: КВС и Второй пилот (закрытый перечень).",
     "Должности пилотов в наборе: Бортинженер, КВС и Второй пилот (закрытый перечень).",
     "Бортинженер", {"КВС", "Второй пилот"}),
    ("Парк набора состоит только из воздушных судов типа Полярис-Р200; иных типов нет.",
     "Парк набора состоит только из воздушных судов типа Полярис-Р200 и Полярис-Р300; иных типов нет.",
     "Полярис-Р300", {"Полярис-Р200"}),
    ("Парк набора состоит только из воздушных судов типа Полярис-Р200; судов иных типов нет.",
     "Парк набора состоит только из воздушных судов типа Полярис-Р300, Полярис-Р200; судов иных типов нет.",
     "Полярис-Р300", {"Полярис-Р200"}),
]


def pending(statement, value):
    doc, q, root = initial()
    doc["rules"][0]["statement"] = statement
    m.ensure_data_scope_questions(doc, report(value))
    qe.freeze(q)
    answers.apply_answers(doc, {root: "а"})
    return doc, q


@pytest.mark.parametrize("original,expanded,value,old_values", REAL_LISTS)
def test_real_definition_expands_without_rewriting_context(original, expanded, value, old_values):
    from cdpm.data_scope import enumeration
    parsed = enumeration(original)
    assert parsed is not None and parsed[1] == old_values
    doc, q = pending(original, value)
    assert patch(doc, q, statement=expanded)["worker_applied"] == 1
    assert doc["rules"][0]["statement"] == expanded
    assert m.validate_schema(doc) + m.validate_model(doc) == []


@pytest.mark.parametrize("original,expanded,value,old_values", REAL_LISTS)
def test_real_definition_does_not_accept_another_value(original, expanded, value, old_values):
    doc, q = pending(original, value)
    before = copy.deepcopy(doc["rules"])
    result = patch(doc, q, statement=expanded.replace(value, "ЧУЖОЕ"))
    assert result["worker_applied"] == 0 and doc["rules"] == before
    assert "запрошенное значение не внесено" in q["answer"]["processing_note"]


@pytest.mark.parametrize("surface", ["md", "xlsx"])
def test_residual_sentence_tail_is_preserved_not_a_fictitious_value(tmp_path, surface):
    from test_cdpm_edition_scope_display import shown
    original = "Разрешены типы A320, A321. Прочие по согласованию."
    doc, q = pending(original, "B777")
    result = patch(doc, q, statement=original.replace("A321", "A321, B777"))
    assert result["worker_applied"] == 1
    assert "processing_note" not in q["answer"]
    assert doc["rules"][0]["statement"].endswith(". Прочие по согласованию.")
    assert "прежнее значение потеряно" not in shown(doc, q, surface, tmp_path)


@pytest.mark.parametrize("statement", [
    "Рейс на воздушном судне типа Полярис-Р200 выполняет только пилот, "
    "у которого допуск на этот тип действует на дату рейса.",
    "Экипаж отдыхает не менее 11 часов.",
    "Не разрешены типы A320, A321.",
    "Запрещены типы — A320 и A321 (закрытый перечень).",
    "Экипаж каждого рейса и смены — ровно 2 пилота: КВС и Второй пилот.",
    "Парк состоит только из двух типов A320 и A321.",
])
def test_relational_and_negative_rules_are_not_positive_lists(statement):
    from cdpm.data_scope import enumeration
    assert enumeration(statement) is None


def test_conjunction_inside_quotes_is_one_identifier():
    doc, q = pending('Разрешены значения "X и Y", OLD.', "AAA")
    assert patch(doc, q, statement='Разрешены значения "X и Y", OLD и AAA.')["worker_applied"] == 1


def test_existing_set_member_with_conjunction_is_not_reinterpreted():
    doc, q = pending('База ∈ {X и Y, OLD}', "AAA")
    assert patch(doc, q, statement='База ∈ {X и Y, OLD, AAA}')["worker_applied"] == 1


FORMAL_LISTS = [
    ("Домен баз приписки = {SVG, PLY, GRT}; главная база = PLY.", "AAA"),
    ("Домен должностей = {КВС, Второй пилот}.", "Бортинженер"),
    ("Множество баз приписки экипажей = {SVG, PLY, GRT}; главная база = PLY.", "AAA"),
]


@pytest.mark.parametrize("original,value", FORMAL_LISTS)
def test_formal_domain_equality_extends_the_supplied_list(original, value):
    doc, q = pending(original, value)
    candidate = original.replace("}", ", " + value + "}")
    assert patch(doc, q, statement=candidate)["worker_applied"] == 1
    assert doc["rules"][0]["statement"] == candidate
    assert m.validate_schema(doc) + m.validate_model(doc) == []


@pytest.mark.parametrize("change", ["broken_brace", "wrong_value", "lost_value", "other_main_base"])
def test_formal_domain_equality_does_not_authorize_other_changes(change):
    original, value = FORMAL_LISTS[0]
    candidate = original.replace("}", ", " + value + "}")
    candidate = {
        "broken_brace": candidate.replace("}", "]"),
        "wrong_value": candidate.replace(value, "ЧУЖОЕ"),
        "lost_value": candidate.replace("SVG, ", ""),
        "other_main_base": candidate.replace("главная база = PLY", "главная база = GRT"),
    }[change]
    doc, q = pending(original, value)
    before = copy.deepcopy(doc["rules"])
    assert patch(doc, q, statement=candidate)["worker_applied"] == 0
    assert doc["rules"] == before


@pytest.mark.parametrize("statement", [
    "Домен баз != {SVG, PLY}.", "Не домен баз = {SVG, PLY}.",
    "Домен баз = {SVG, PLY}; домен типов = {A320, A321}.",
    "Штраф за выход из баз = {SVG, PLY}.",
])
def test_formal_non_domain_or_ambiguous_expression_is_not_a_single_list(statement):
    from cdpm.data_scope import enumeration
    assert enumeration(statement) is None


@pytest.mark.parametrize("change", ["remove_old", "change_tail"])
def test_real_list_still_retains_old_values_and_closed_tail(change):
    original, expanded, value, _ = next(
        case for case in REAL_LISTS if "(закрытый перечень)" in case[0])
    doc, q = pending(original, value)
    candidate = (expanded.replace("КВС, ", "") if change == "remove_old"
                 else expanded.replace("закрытый", "открытый"))
    assert candidate != expanded  # The negative probe must actually mutate it.
    before = copy.deepcopy(doc["rules"])
    assert patch(doc, q, statement=candidate)["worker_applied"] == 0
    assert doc["rules"] == before


@pytest.mark.parametrize("tail", ["Прочие без согласования.", "Прочие всегда запрещены."])
def test_changing_residual_condition_is_not_authorized(tail):
    doc, q = pending("Разрешены типы A320, A321. Прочие по согласованию.", "B777")
    before = copy.deepcopy(doc["rules"])
    assert patch(doc, q, statement=f"Разрешены типы A320, A321, B777. {tail}")["worker_applied"] == 0
    assert doc["rules"] == before


def test_sentence_words_inside_quotes_remain_one_identifier():
    original = 'Разрешены типы "X. Прочие", A321.'
    doc, q = pending(original, "B777")
    assert patch(doc, q, statement='Разрешены типы "X. Прочие", A321, B777.')["worker_applied"] == 1


def test_unrecognized_sentence_is_reported_as_unsupported_not_lost_value():
    original = "Разрешены типы A320, A321. Уточните условия у владельца."
    doc, q = pending(original, "B777")
    assert patch(doc, q, statement=original.replace("A321", "A321, B777"))["worker_applied"] == 0
    assert "нельзя однозначно проверить" in q["answer"]["processing_note"]
    assert "прежнее значение потеряно" not in q["answer"]["processing_note"]


CORPUS = json.loads((Path(__file__).parent / "fixtures" / "cdpm_closed_lists_cdpm348.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CORPUS, ids=lambda c: c["id"])
def test_supplied_corpus_closed_definitions_and_non_lists(case):
    from cdpm.data_scope import enumeration
    parsed = enumeration(case["statement"])
    if case["expected"] is None:
        assert parsed is None
        return
    assert parsed is not None and parsed[1] == set(case["expected"])
    value = "Полярис-Р300" if "Полярис-Р200" in case["expected"] else "Бортинженер"
    original = case["statement"]
    first = case["expected"][0]
    candidate = original.replace(first, first + " и " + value, 1)
    doc, q = pending(original, value)
    doc["rules"][0]["kind"] = case["kind"]
    # A synthetic finding drives this patch. These archived DEF statements
    # do not prove the live compiler produces an out-of-list question.
    assert patch(doc, q, statement=candidate)["worker_applied"] == 1
    assert m.validate_schema(doc) + m.validate_model(doc) == []

@pytest.mark.parametrize("probe", ["values", "healthy", "changed_tail_with_old_tail_retained"])
@pytest.mark.parametrize("separator", [" — ", " = ", " "])
def test_named_main_base_tail_is_context_not_a_list_member(probe, separator):
    from cdpm.data_scope import enumeration
    original = f"Базы приписки экипажей набора: SVG, PLY, GRT; главная база{separator}PLY."
    if probe == "values":
        parsed = enumeration(original)
        assert parsed is not None and parsed[1] == {"SVG", "PLY", "GRT"}
        assert parsed[2] == f"; главная база{separator}PLY."
        return
    doc, q = pending(original, "AAA")
    candidate = original.replace("GRT;", "GRT, AAA;")
    if probe != "healthy":
        candidate = candidate.replace(f"главная база{separator}PLY",
                                      f"главная база{separator}GRT; главная база{separator}PLY")
    before = copy.deepcopy(doc["rules"])
    result = patch(doc, q, statement=candidate)
    assert result["worker_applied"] == int(probe == "healthy")
    if probe != "healthy":
        assert doc["rules"] == before
