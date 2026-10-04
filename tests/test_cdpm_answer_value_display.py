"""COSMOS 2026-09-15: distinguish the parsed value from its source quotation.

Local generated-question/apply path, pure rendering; no channel or service.
"""
import copy
import re

import pytest

from cdpm import provenance, render, vmp_form
from test_cdpm_a03m_provenance import PARAM, RAW_600, _answered, _doc, _rule


def rule_line(text):
    return next(line for line in text.splitlines() if line.startswith("| R-R |"))


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("value", [600, 999, None, 0])
def test_answer_cell_displays_actual_value_and_catalogue_unit(value, legacy):
    doc, cell = _answered()
    cell["value"] = value
    if legacy:
        cell.pop("source")
    before = copy.deepcopy(doc)
    text = render.render_svod(doc)
    line = rule_line(text)
    expected = "значение не получено" if value is None else f"значение в модели: ` {value} `"
    assert expected in line
    assert "единица: ` duration_minutes `" in line
    assert "cdpm-answer-" in line
    assert RAW_600 in text
    assert doc == before
    assert render.render_anketa(doc) == render.render_anketa(before)


def test_divergent_values_change_summary_and_vmp_but_not_statement_or_sections():
    original, _ = _answered()
    summaries, forms, headings = [], [], []
    for value in (600, 999, None):
        doc = copy.deepcopy(original)
        _rule(doc)["template_fit"]["params"][PARAM]["value"] = value
        before = copy.deepcopy(doc)
        text = render.render_svod(doc)
        summaries.append(text)
        forms.append(vmp_form.render_form_md(vmp_form.to_form(doc)))
        headings.append([line for line in text.splitlines() if line.startswith("#")])
        # Value is a note, not a fabricated amendment to the customer's clause.
        assert rule_line(text).split(" | ")[1] == rule_line(render.render_svod(original)).split(" | ")[1]
        assert doc == before
        if value == 999:
            assert provenance.package_problems(doc), "display must not legalize a wrong value"
    assert len(set(summaries)) == len(set(forms)) == 3
    assert headings[0] == headings[1] == headings[2]


def test_unmarked_parameters_and_unanswered_document_gain_no_value_note():
    doc = _doc()
    before = copy.deepcopy(doc)
    assert "значение в модели:" not in render.render_svod(doc)
    assert "значение не получено" not in render.render_svod(doc)
    assert doc == before
    doc, cell = _answered()
    cell.pop("source")
    cell.pop("answer")
    assert "значение в модели:" not in render.render_svod(doc)


def test_broken_source_does_not_hide_value_or_fabricate_a_valid_answer_link():
    doc, cell = _answered()
    cell["value"] = 999
    cell["source"]["version"]["fingerprint"] = "0" * 12
    line = rule_line(render.render_svod(doc))
    assert "значение в модели: ` 999 `" in line
    assert "источник ответа не разрешён" in line
    assert "cdpm-answer-" not in line


def test_missing_value_and_unknown_unit_are_explicit_not_inferred_from_raw():
    doc, cell = _answered()
    cell.pop("value")
    fit = _rule(doc)["template_fit"]
    fit["params"]["unknown_parameter"] = fit["params"].pop(PARAM)
    line = rule_line(render.render_svod(doc))
    assert "значение не получено" in line
    assert "единица не определена каталогом" in line
    assert "значение в модели: ` 600 `" not in line


def test_render_does_not_round_large_numbers_or_treat_false_as_missing():
    doc, cell = _answered()
    for value, word in [(int("9" * 60), "9" * 60), (False, "false")]:
        cell["value"] = value
        line = rule_line(render.render_svod(doc))
        assert f"значение в модели: ` {word} `" in line
        assert "значение не получено" not in line


def test_value_note_escapes_markdown_without_breaking_table_or_truncating():
    doc, cell = _answered()
    cell["value"] = "a|b\n`payload` " + "длинное " * 30 + "КОНЕЦ"
    before = copy.deepcopy(doc)
    line = rule_line(render.render_svod(doc))
    assert "a\\|b" in line and "КОНЕЦ" in line
    assert re.search(r"`` .*payload.* ``", line)
    assert len(re.split(r"(?<!\\)\|", line)) == 8
    assert doc == before
