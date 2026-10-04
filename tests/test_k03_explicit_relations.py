"""A named subject cannot turn an upper bound into a lower bound.

These are bounded relation checks, not an unrestricted prose interpreter.
"""
import copy

import pytest

from cdpm import answer_parsing, catalog_bridge as cb, family_params as fp, model, provenance
from test_cdpm_a03m_provenance import _cell, _q, QID, FAMILY, PARAM
from test_k03_subject_application import apply
from test_k03_subject_vocabulary import selected_catalogue


@pytest.fixture(autouse=True)
def clean():
    yield
    cb.forget()


@pytest.mark.parametrize("raw", [
    "Вне базы — не более 10 часов.",
    "Вне базы — максимум 10 часов.",
    "Вне базы — ровно 10 часов.",
    "Вне базы — менее 10 часов.",
    "Вне базы — более 10 часов.",
    "Вне базы — не минимум 10 часов.",
    "Вне базы — не менее 10 часов. Вне базы — не более 10 часов.",
    "Вне базы — не более 10 часов. Вне базы — не менее 10 часов.",
    "Вне базы — 10 часов от.",
    "Вне базы — и более 10 часов.",
])
def test_incompatible_unsupported_or_misplaced_relation_cannot_apply(tmp_path, monkeypatch, raw):
    selected_catalogue(tmp_path, monkeypatch)
    doc, answer, result = apply(raw)
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert model.is_open_question(_q(doc, QID), doc=doc)
    assert any("отношени" in p for p in answer["parsed"]["problems"])


@pytest.mark.parametrize("raw", [
    "Вне базы — не менее 10 часов.",
    "Вне базы — минимум 10 часов.",
    "Вне базы — от 10 часов.",
    "Вне базы — 10 часов и более.",
    "Вне базы — 10 часов.",
    "Вне базы — не менее 10 часов. Вне базы — минимум 10 часов.",
])
def test_matching_relation_preserves_apply_source_and_restore(tmp_path, monkeypatch, raw):
    selected_catalogue(tmp_path, monkeypatch)
    doc, answer, result = apply(raw)
    assert result["applied"] == 1 and result["unparsed"] == 0
    assert _cell(doc)["value"] == 600
    assert provenance.cell_problems(doc, "R-R", FAMILY, PARAM, _cell(doc)) == []
    expected = copy.deepcopy(_cell(doc))
    next(r for r in doc["rules"] if r["id"] == "R-R")["template_fit"]["params"].pop(PARAM)
    fp.ensure_family_param_questions(doc)
    assert _cell(doc) == expected
    assert model.validate_schema(doc) == []


@pytest.mark.parametrize("raw,applied", [
    ("Максимальная длительность смены — не более 10 часов", True),
    ("Максимальная длительность смены — минимум 10 часов", False),
])
def test_upper_bound_uses_parameter_contract_not_fixed_min(tmp_path, monkeypatch, raw, applied):
    selected_catalogue(tmp_path, monkeypatch)
    parsed = answer_parsing.parameter({"id": "Q-3b-параметр-duty_limits-max_duty_duration"}, raw)
    assert (not parsed["ambiguous"]) is applied
    if applied:
        assert parsed["value"] == 600
    else:
        assert any("отношени" in p for p in parsed["problems"])


@pytest.mark.parametrize("quote", ["10 часов", "Вне базы — не более 10 часов"])
def test_exact_quote_cannot_discard_relation_of_full_source(tmp_path, monkeypatch, quote):
    selected_catalogue(tmp_path, monkeypatch)
    result = provenance.value_problems(FAMILY, PARAM, {"value": 600, "quote": quote},
                                       "Вне базы — не более 10 часов.")
    assert result and any("отношени" in p for p in result)


def test_old_answer_cannot_restore_incompatible_relation(tmp_path, monkeypatch):
    doc, answer, result = apply("Вне базы — не более 10 часов.")  # explicit old 0.5 boundary
    assert result["applied"] == 1
    answer.pop("parsed", None)
    params = next(r for r in doc["rules"] if r["id"] == "R-R")["template_fit"]["params"]
    params.pop(PARAM)
    selected_catalogue(tmp_path, monkeypatch)
    notes = []
    fp.ensure_family_param_questions(doc, notes=notes)
    assert PARAM not in params and notes
