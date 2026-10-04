"""COSMOS 19356033 residual: relation phrases are not unknown subjects."""
import copy

import pytest

from cdpm import answer_parsing, catalog_bridge as cb, family_params as fp, model
from test_k03_subject_vocabulary import selected_catalogue
from test_k03_subject_application import apply
from test_cdpm_a03m_provenance import _cell, _q, QID, PARAM


@pytest.fixture(autouse=True)
def clean():
    yield
    cb.forget()


@pytest.mark.parametrize("raw", ["не менее 10 часов", "Не меньше 10 часов", "минимум 10 часов",
                                 "от 10 часов", "10 часов и более"])
def test_lower_bound_is_a_healthy_addressed_answer(tmp_path, monkeypatch, raw):
    selected_catalogue(tmp_path, monkeypatch)
    doc, answer, result = apply(raw)
    assert result["applied"] == 1 and result["unparsed"] == 0
    assert _cell(doc)["value"] == answer["parsed"]["value"] == 600
    assert not answer["parsed"]["ambiguous"]
    expected = copy.deepcopy(_cell(doc))
    next(r for r in doc["rules"] if r["id"] == "R-R")["template_fit"]["params"].pop(PARAM)
    fp.ensure_family_param_questions(doc)
    assert _cell(doc) == expected


@pytest.mark.parametrize("raw", ["не более 10 часов", "не больше 10 часов", "максимум 10 часов"])
def test_upper_bound_uses_its_actual_catalogue_relation(tmp_path, monkeypatch, raw):
    selected_catalogue(tmp_path, monkeypatch)
    q = {"id": "Q-3b-параметр-duty_limits-max_duty_duration"}
    parsed = answer_parsing.parameter(q, raw)
    assert parsed["value"] == 600 and not parsed["ambiguous"]


@pytest.mark.parametrize("raw", ["не более 10 часов", "ровно 10 часов", "не менее 10 часов для пилотов"])
def test_relation_or_condition_is_not_silently_changed(tmp_path, monkeypatch, raw):
    selected_catalogue(tmp_path, monkeypatch)
    doc, answer, result = apply(raw)
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert model.is_open_question(_q(doc, QID), doc=doc)
    assert answer["parsed"]["problems"]


@pytest.mark.parametrize("raw", ["Вне базы — 10 часов. Вне базы — -12 часов.",
                                  "Вне базы — 10 часов. Вне базы — 10:90."])
def test_invalid_second_value_cannot_hide_behind_first(tmp_path, monkeypatch, raw):
    selected_catalogue(tmp_path, monkeypatch)
    doc, answer, result = apply(raw)
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert model.is_open_question(_q(doc, QID), doc=doc)


def test_missing_unit_is_not_diagnosed_as_missing_subject(tmp_path, monkeypatch):
    selected_catalogue(tmp_path, monkeypatch)
    doc, answer, result = apply("10")
    assert result["applied"] == 0
    assert "предмет" not in answer["note"]
    assert "единиц" in answer["note"]


def test_bare_clock_number_requests_clock_format_not_subject(tmp_path, monkeypatch):
    selected_catalogue(tmp_path, monkeypatch)
    q = {"id": "Q-3b-параметр-night_series-night_duty_end"}
    parsed = answer_parsing.parameter(q, "10")
    assert parsed["ambiguous"] and parsed["value"] is None
    assert "ЧЧ:ММ" in " ".join(parsed["problems"])
    assert "предмет" not in " ".join(parsed["problems"])
