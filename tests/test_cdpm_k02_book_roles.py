"""K02-05: declared row roles, not Q-only guessing or numerical parsing.

The measurement reader rejects misplaced reserved metadata; it does not claim
that arbitrary prose can be classified or that a value reached the product.
"""
import copy
import json
from pathlib import Path

import pytest

from test_cdpm_k02_price_tool import price


SERVICES = ["Итого", "Всего", "Сумма", "Итого строк", "Всего строк",
            "Версия анкеты", "Источник", "_источник", "_comment"]


@pytest.mark.parametrize("section,label", [
    (section, label) for section in ["exact", "prefix"]
    for label in SERVICES + ["  ИТОГО   СТРОК  ", "", " "]
    if not (section == "prefix" and label == "")
])
def test_machine_book_refuses_reserved_or_empty_match_keys(label, section):
    book = {"exact": {"Минимальный отдых вне базы": "720 минут"},
            section: {"Минимальный отдых вне базы": "720 минут", label: "3"}}
    with pytest.raises(ValueError, match="ключ|служебн"):
        price._json_answers(json.dumps(book, ensure_ascii=False))


@pytest.mark.parametrize("label", SERVICES + ["  ИТОГО   СТРОК  ", "", " "])
def test_markdown_refuses_service_rows_not_their_numbers(label):
    text = "| Вопрос | Ответ | Комментарий |\n|---|---|---|\n"
    text += f"| Отдых | 720 минут | норма |\n| {label} | 3 | заметка |\n"
    with pytest.raises(ValueError, match="ключ|служебн"):
        price._markdown_answers(text)


@pytest.mark.parametrize("header", ["| Вопрос | Ответ |", "| ВОПРОС | ответ |"])
def test_inline_repeated_header_is_not_an_answer(header):
    text = "| Вопрос | Ответ |\n|---|---|\n| Отдых | 720 минут |\n" + header + "\n"
    with pytest.raises(ValueError, match="повтор.*шапк"):
        price._markdown_answers(text)


@pytest.mark.parametrize("header", ["| Ответ |", "| Неизвестно | Ответ |",
                                      "| Вопрос | Тема | Ответ |"])
def test_question_column_must_be_unambiguous(header):
    width = len(price._md_cells(header))
    text = header + "\n|" + "---|" * width + "\n|" + "720 минут|" * width + "\n"
    with pytest.raises(ValueError, match="колонк.*вопрос|колонк.*тем"):
        price._markdown_answers(text)


def test_arbitrary_nonreserved_question_identifiers_and_topics_are_preserved():
    book = {"_источник": "служебные данные на своём месте", "form_version": 3,
            "exact": {"Минимальный отдых вне базы": "720 минут", "Q-Итого": "3"},
            "prefix": {"Предел смены": "12 часов"},
            "regex": [{"pattern": "^посадок", "answer": "4 посадки", "note": "3"}]}
    assert price._json_answers(json.dumps(book, ensure_ascii=False)) == [
        "720 минут", "3", "12 часов", "4 посадки"]
    assert price._markdown_answers("## Ответ\n\n| Ответ | Тема (дефект) | Комментарий |\n"
                                   "|---|---|---|\n| A\\|B | Q-Итого | Итого |\n"
                                   "| 720 минут | Сумма кредитных часов | 3 |\n") == ["A|B", "720 минут"]


def test_blank_answers_are_still_counted_but_never_measured(tmp_path):
    (tmp_path / "ANSWER-BOOK.md").write_text(
        "| Тема | Ответ |\n|---|---|\n| Отдых | 720 минут |\n| Иной отдых |  |\n",
        encoding="utf-8")
    snapshot = price.book_snapshot(tmp_path)
    assert snapshot["lines"] == ["720 минут"]
    assert snapshot["manifest"][0]["blank_answers"] == 1


def test_misplaced_metadata_fails_measurement_before_an_artifact(tmp_path, capsys, monkeypatch):
    from cdpm import family_params as fp
    monkeypatch.setattr(price, "corpus", lambda: ["10 часов"])
    (tmp_path / "ANSWER-BOOK.json").write_text(
        json.dumps({"exact": {"Отдых": "720 минут", "Версия анкеты": "3"}}), encoding="utf-8")
    out = tmp_path / "measurement.json"
    assert price.main(["dump", "--package-root", str(Path(fp.__file__).parents[1]),
                       "--books-root", str(tmp_path), "--out", str(out)]) == 2
    assert not out.exists()
    assert "ANSWER-BOOK.json" in capsys.readouterr().err


def test_real_h0b_book_matches_the_catalog_question_and_reaches_the_carrier():
    from tools import nano_regression as nr
    from cdpm import answers, model
    from test_cdpm_k02_number_grammar import _prepared, QID
    book = json.loads((Path(__file__).resolve().parents[1] / "open-bench/polyaris-h0b/etalon/ANSWER-BOOK-MACHINE.json").read_text(encoding="utf-8"))
    doc, rule, question = _prepared()
    raw = nr.match_answer(book, QID, question.get("text", ""))
    assert raw == book["exact"][QID] and "600 минут" in raw
    result = answers.apply_answers(doc, {QID: raw})
    assert result["applied"] == 1
    assert rule["template_fit"]["params"]["min_rest_outstation"]["value"] == 600
    assert not model.is_open_question(question) and QID not in rule["blockers"]


def test_numeric_metadata_does_not_match_a_real_question_in_the_product():
    from tools import nano_regression as nr
    from cdpm import answers, model
    from test_cdpm_k02_number_grammar import _prepared, QID
    doc, rule, question = _prepared()
    before = copy.deepcopy(rule)
    book = {"exact": {"Версия анкеты": "3", "Итого строк": "1"},
            "prefix": {"_источник": "720 минут"}, "regex": []}
    raw = nr.match_answer(book, QID, "Версия анкеты Итого строк _источник")
    assert raw is None  # exact/prefix match IDs, not the question text.
    assert answers.apply_answers(doc, {})["applied"] == 0
    assert rule == before and model.is_open_question(question) and QID in rule["blockers"]


@pytest.mark.parametrize("book,qid,want", [
    ({"prefix": {"": "720 минут"}}, "Q-any", "720 минут"),
    ({"exact": {"Q-one": "600 минут"}, "prefix": {"": "720 минут"}},
     "Q-one", "600 минут"),
    ({"prefix": {"Q-specific": "600 минут", "": "720 минут"}},
     "Q-other", "720 минут"),
])
def test_empty_prefix_is_the_real_router_fallback_not_an_empty_question(book, qid, want):
    from tools import nano_regression as nr
    assert nr.match_answer(book, qid, "") == want
    expected = list(book.get("exact", {}).values()) + list(book["prefix"].values())
    assert price._json_answers(json.dumps(book)) == expected


@pytest.mark.parametrize("section", ["exact", "prefix"])
@pytest.mark.parametrize("label,answer,accepted", [
    ("Вопрос", "Ответ", False),
    ("Тема", "Правильный ответ", False),
    (" ID вопроса ", " ПРАВИЛЬНЫЙ ОТВЕТ АВИАКОМПАНИИ ", False),
    ("Вопрос", "720 минут", True),
    ("Отдых", "Ответ", True),
    ("Версия анкеты", "3", False),
    ("Итого строк", "1", False),
    ("Примечание", "", True),
])
def test_format_seam_applies_same_role_law(tmp_path, section, label, answer, accepted):
    # CDPM's probe exposed a real repeated-header seam; compare outcomes,
    # not temporary path prefixes inside exception messages.
    rows = [("Основной отдых", "600 минут"), (label, answer)]
    md = "| Вопрос | Ответ |\n|---|---|\n"
    md += "".join(f"| {key} | {value} |\n" for key, value in rows)
    js = json.dumps({section: dict(rows)}, ensure_ascii=False)
    for suffix, source in [("md", md), ("json", js)]:
        root = tmp_path / suffix
        root.mkdir()
        (root / f"ANSWER-BOOK.{suffix}").write_text(source, encoding="utf-8")
        if accepted:
            assert price.book_snapshot(root)["lines"] == [
                value for _, value in rows if value.strip()]
        else:
            with pytest.raises(ValueError):
                price.book_snapshot(root)
