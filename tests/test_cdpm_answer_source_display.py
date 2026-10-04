"""COSMOS: a rule cell must lead to its exact full answer, not a root guess."""
import copy
import re

import pytest

from cdpm import answers, render
from test_cdpm_a03m_provenance import QID, RAW_540, RAW_600, _answered, _cell


def _rule_line(text):
    return next(line for line in text.splitlines() if line.startswith("| R-R |"))


@pytest.mark.parametrize("legacy", [False, True])
def test_rule_has_resolvable_link_to_full_answer_without_mutating_model(legacy):
    doc, cell = _answered()
    if legacy:
        cell.pop("source")
    before = copy.deepcopy(doc)
    text = render.render_svod(doc)
    line = _rule_line(text)
    target = re.search(r"\]\(#(cdpm-answer-[a-f0-9]+)\)", line)
    assert target, "answer-sourced rule lacks a route to the full answer"
    assert QID in line
    assert f'<a id="{target[1]}"></a>' in text
    assert RAW_600 in text
    assert RAW_600 not in text.split("## Часть 2.", 1)[1]
    assert text == render.render_svod(doc) and doc == before


def test_revision_link_names_the_current_answer_not_its_root():
    doc, _ = _answered()
    revision = answers.open_revision(doc, QID)["created"]
    assert answers.apply_answers(doc, {revision: RAW_540})["applied"] == 1
    text = render.render_svod(doc)
    assert revision in _rule_line(text)
    assert RAW_540 in text
    assert "версия ответа" in text and "издание" in text


def test_unmarked_cell_does_not_gain_an_inferred_answer_link():
    doc, cell = _answered()
    cell.pop("source")
    cell.pop("answer")
    text = render.render_svod(doc)
    assert "cdpm-answer-" not in text
    assert RAW_600 not in _rule_line(text)


def test_broken_exact_identity_is_reported_not_replaced_with_root_text():
    doc, cell = _answered()
    cell["source"]["version"]["fingerprint"] = "0" * 12
    text = render.render_svod(doc)
    assert "источник ответа не разрешён" in _rule_line(text)
    assert "cdpm-answer-" not in _rule_line(text)
    assert RAW_600 not in text


def test_full_raw_with_markdown_fence_is_shown_literally():
    doc, cell = _answered()
    q = next(q for q in doc["questions"] if q["id"] == QID)
    raw = RAW_600 + "\n```\n[подробности](https://example.invalid/)\n```"
    q["answer"]["raw"] = raw
    # Construct a valid answer receipt for the new synthetic raw, not a
    # tampered old receipt (that belongs to the negative provenance probes).
    import hashlib
    q["answer"]["answer_version"]["fingerprint"] = hashlib.sha256(
        " ".join(raw.split()).encode()).hexdigest()[:12]
    from cdpm import answer_parsing, provenance
    # This renderer fixture changes the synthetic received text. Its saved
    # parse must describe that text too; stale-parse refusal has separate tests.
    q["answer"]["parsed"] = answer_parsing.parameter(q, raw)
    assert not q["answer"]["parsed"]["ambiguous"]
    assert q["answer"]["parsed"]["value"] == cell["value"] == 600
    cell["source"] = provenance.answer_source(q, "min_rest_outstation", raw)
    assert provenance.cell_problems(
        doc, "R-R", "rest_floors", "min_rest_outstation", cell) == []
    text = render.render_svod(doc)
    assert "````\n" + raw + "\n````" in text
