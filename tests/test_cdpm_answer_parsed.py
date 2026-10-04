"""Persisted parameter parse is evidence, not an executable conditional rule."""
import copy
import json

import pytest

from cdpm import answers, family_params as fp, model as m, provenance as prov
from test_cdpm_a03m_provenance import _answered, _cell, _doc, _q, QID, PARAM


def _apply(raw):
    doc = m.loads_model(json.dumps(_doc(), ensure_ascii=False))
    fp.ensure_family_param_questions(doc)
    doc = m.loads_model(json.dumps(doc, ensure_ascii=False))
    result = answers.apply_answers(doc, {QID: raw})
    return doc, _q(doc, QID)["answer"], result


@pytest.mark.parametrize("raw,value", [("10,5 часов", 630), ("1.5 часа", 90),
                                      ("10:00", 600)])
def test_applied_value_has_the_same_persisted_parse(raw, value):
    doc, answer, result = _apply(raw)
    parsed = answer["parsed"]
    assert result["applied"] == 1
    assert parsed["subject"] == PARAM and parsed["unit"] == "duration_minutes"
    assert parsed["value"] == _cell(doc)["value"] == value
    assert parsed["quote"] == _cell(doc)["quote"]
    assert parsed["ambiguous"] is False and parsed["problems"] == []
    assert parsed["declines"] == [] and parsed["exceptions"] == []
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []
    assert m.loads_model(json.dumps(doc, ensure_ascii=False)) == doc


@pytest.mark.parametrize("raw", ["10:90", "-600 минут", "10 часов только для пилотов",
                                  "10 часов, кроме пилотов"])
def test_unresolved_parse_does_not_write_or_close(raw):
    doc, answer, result = _apply(raw)
    parsed = answer["parsed"]
    assert result["unparsed"] == 1 and result["applied"] == 0
    assert PARAM not in next(r for r in doc["rules"] if r["id"] == "R-R")["template_fit"]["params"]
    assert parsed["value"] is None and parsed["ambiguous"] is True
    assert parsed["problems"] and answer["raw"] == raw
    assert m.is_open_question(_q(doc, QID), doc=doc)
    if "пилотов" in raw:
        assert parsed["condition"] == {"raw": raw, "resolved": False}
    if "кроме" in raw:
        assert parsed["exceptions"] == [raw]
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []


@pytest.mark.parametrize("raw,applied", [("Не применяется.", True),
                                        ("Не применяется для пилотов.", False)])
def test_decline_keeps_exact_text_and_never_promotes_conditional_scope(raw, applied):
    doc, answer, result = _apply(raw)
    parsed = answer["parsed"]
    assert bool(result["applied"]) is applied
    assert parsed["declines"] == [raw] and parsed["value"] is None
    assert parsed["ambiguous"] is not applied
    if not applied:
        assert parsed["condition"] == {"raw": raw, "resolved": False}
        assert m.is_open_question(_q(doc, QID), doc=doc)
    assert m.validate_schema(doc) == []


@pytest.mark.parametrize("field,value", [("value", 660), ("subject", "min_rest_home"),
                                       ("ambiguous", 0), ("problems", ["made up"])])
def test_tampered_parse_is_rejected_by_model_source_and_package(field, value):
    doc, cell = _answered()
    _q(doc, QID)["answer"]["parsed"][field] = value
    assert any("parsed" in p for p in m.validate_model(doc))
    assert any("parsed" in p for p in prov.package_problems(doc))
    with pytest.raises(prov.ProvenanceRefusal, match="parsed"):
        prov.resolve_source(doc, cell["source"])
    before = copy.deepcopy(doc)
    with pytest.raises(ValueError, match="parsed"):
        answers.apply_answers(doc, {QID: "9 часов"})
    assert doc == before


def test_old_missing_parse_is_not_fabricated_on_load():
    doc, cell = _answered()
    _q(doc, QID)["answer"].pop("parsed", None)
    loaded = m.loads_model(json.dumps(doc, ensure_ascii=False))
    assert "parsed" not in _q(loaded, QID)["answer"]
    assert _cell(loaded) == cell and m.validate_model(loaded) == []


def test_retry_preserves_parse_and_original_receipt():
    doc, answer, _ = _apply("10 часов")
    old = copy.deepcopy(answer)
    answers.apply_answers(doc, {QID: "10 часов"})
    assert _q(doc, QID)["answer"] == old


@pytest.mark.parametrize("tampered", [False, True])
def test_self_healing_checks_the_same_persisted_parse(tampered):
    doc, cell = _answered()
    expected = copy.deepcopy(cell)
    next(r for r in doc["rules"] if r["id"] == "R-R")["template_fit"]["params"].pop(PARAM)
    if tampered:
        _q(doc, QID)["answer"]["parsed"]["value"] = 660
    notes = []
    fp.ensure_family_param_questions(doc, notes=notes)
    params = next(r for r in doc["rules"] if r["id"] == "R-R")["template_fit"]["params"]
    if tampered:
        assert PARAM not in params
        assert any("parsed" in note for note in notes)
    else:
        assert params[PARAM] == expected


@pytest.mark.parametrize("value", [None, [], {"ambiguous": False}])
def test_malformed_parse_is_neither_trusted_nor_repaired(value):
    doc, _ = _answered()
    _q(doc, QID)["answer"]["parsed"] = value
    assert m.validate_schema(doc)
    assert any("parsed" in p for p in m.validate_model(doc))
    loaded = m.loads_model(json.dumps(doc, ensure_ascii=False))
    assert _q(loaded, QID)["answer"]["parsed"] == value
    assert any("parsed" in p for p in prov.package_problems(loaded))
