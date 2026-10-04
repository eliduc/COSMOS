"""Catalog-coverage prose is not a missing numerical customer parameter."""
import pytest

from cdpm import balance as bal


H0C_CLAUSE = (
    "Член экипажа не может быть назначен на рейс в дни своего отпуска. "
    "Шаблона нет: недоступность члена экипажа в дни отпуска — семьи каталога нет: "
    "days_off_floor судит норму выходных в окне, rolling_rest — недельный отдых, "
    "reserve_floors — резерв на базах; дни, когда человек отсутствует по отпуску, "
    "не судит ни одна семья, критерий или словарь."
)


def report(text, kind="HARD"):
    entries = [{"sid": "S2", "file": "rules.xlsx", "sha256": "0" * 64,
                "clauses": [{"anchor": "строка-5", "text": text}]}]
    model = {"rules": [{"id": "R-VACATION", "kind": kind, "zone": "ROSTERING",
                        "statement": "Запрет назначения в дни отпуска.",
                        "sources": [{"sid": "S2", "anchor": "строка-5"}],
                        "template_fit": {"category": "A", "family": "crew_unavailability",
                                         "params": {}}}],
             "objectives": [], "questions": [], "meta": {}}
    return bal.report(entries, model)


def test_actual_catalog_gap_clause_does_not_request_the_number_one():
    result = report(H0C_CLAUSE)
    assert result["findings"] == [], result["findings"]
    # Preserve a classified source token, rather than discard the whole clause.
    one = [v for v in bal.values_of(H0C_CLAUSE) if v["text"] == "одна"]
    assert len(one) == 1 and one[0]["class"] == "dictionary_word"
    assert one[0]["value"] == "одна"
    a, b = one[0]["span"]
    assert H0C_CLAUSE[a:b] == "одна"


@pytest.mark.parametrize("text, word", [
    ("Ни одна семья каталога не описывает отпуск.", "одна"),
    ("Ни один шаблон каталога не подходит.", "один"),
    ("Нет ни одного критерия каталога с такой сигнатурой.", "одного"),
    ("Нет ни одной семьи каталога с такой сигнатурой.", "одной"),
    ("Не удалось выбрать ни одну семью каталога.", "одну"),
    ("Ни один словарь каталога не задаёт это правило.", "один"),
    ("НИ ОДИН ШАБЛОН КАТАЛОГА НЕ ПОДХОДИТ.", "ОДИН"),
])
def test_catalog_negative_quantifier_is_traceable_not_a_count(text, word):
    token = next(v for v in bal.values_of(text) if v["text"] == word)
    assert token["class"] == "dictionary_word"
    assert text[slice(*token["span"])] == word
    assert report(text)["findings"] == []


@pytest.mark.parametrize("text, word", [
    ("В смене ровно один пилот.", "один"),
    ("Ни один пилот не работает во время отпуска.", "один"),
    ("Ни одна смена не превышает лимит.", "одна"),
    ("Ни одна семья не остаётся без сопровождающего.", "одна"),
    ("Семья каталога известна; ни одна семья не остаётся без сопровождающего.", "одна"),
    ("Семья каталога известна. Ни одна семья не остаётся без сопровождающего.", "одна"),
    ("Семья каталога известна.\nНи один пилот не работает во время отпуска.", "один"),
    ("Не одна семья каталога подходит.", "одна"),
    ("Ровно одна семья каталога подходит.", "одна"),
    ("Подходят две семьи каталога.", "две"),
])
def test_actual_or_ambiguous_quantity_is_not_erased(text, word):
    token = next(v for v in bal.values_of(text) if v["text"] == word)
    assert token["class"] == "number_word"
    assert any(v["text"] == word for v in report(text)["findings"])


@pytest.mark.parametrize("kind", ["HARD", "DEF", "DATA"])
def test_catalog_comment_never_consumes_neighboring_threshold(kind):
    text = "Минимальный отдых 600 минут; ни одна семья каталога не подходит."
    result = report(text, kind)
    assert [(f["class"], f["value"]) for f in result["findings"]] == [("duration", 600)]


def test_same_clause_numeric_word_and_catalog_quantifier_stay_distinct():
    text = "Для правила нужны два пилота; ни одна семья каталога не подходит."
    result = report(text)
    assert [(f["class"], f["text"], f["value"]) for f in result["findings"]] == [
        ("number_word", "два", 2)]
