"""K03 subject isolation at application, provenance and restoration.

Real validated temporary 0.7 catalogue; independent P0 home/outstation
examples. This is not a released vocabulary or multi-parameter assignment.
"""
import copy
import json

import pytest

from cdpm import answers, catalog_bridge as cb, family_params as fp
from cdpm import model, provenance
from test_catalog_060_versioned_schema import modern_docs
from test_template_catalog_validate import _broken
from test_cdpm_a03m_provenance import _doc, _q, _cell, QID, FAMILY, PARAM


def catalogue(tmp_path, monkeypatch, *, words=True, collision=False):
    def prepare(docs):
        modern_docs(docs)
        from template_catalog.unit_vocabulary import attach as attach_units
        from test_k03_unit_vocabulary import vocabulary as unit_words
        docs.update(attach_units(docs, unit_words()))
        family = next(f for f in docs["families.json"]["families"] if f["id"] == FAMILY)
        for p in family["params"]:
            if words:
                p["subject_words"] = (["отдых"] if collision else
                    ["дома"] if p["name"] == "min_rest_home" else ["вне базы"])
        fleet = next(f for f in docs["families.json"]["families"] if f["id"] == "fleet_exclusions")
        for p in fleet["params"]:
            p["subject_words"] = ["тип судна" if p["name"] == "fleet_type" else "типы судов"]
    folder = _broken(tmp_path, prepare)
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    cb.forget()
    assert cb.catalog().version() == "0.7.0"
    assert not [p for p in cb.catalog().validate() if p.severity == "error"]


@pytest.fixture(autouse=True)
def forget_catalogue():
    yield
    cb.forget()


def apply(raw):
    doc = _doc()
    for rule in doc["rules"]:
        if "template_fit" in rule:
            rule["template_fit"]["registry_rev"] = model.registry_fingerprint()
    fp.ensure_family_param_questions(doc)
    assert _q(doc, QID)
    doc = model.loads_model(json.dumps(doc, ensure_ascii=False))
    result = answers.apply_answers(doc, {QID: raw})
    return doc, _q(doc, QID)["answer"], result


@pytest.mark.parametrize("raw", [
    "Дома — 10 часов.",
    "Дома — 12 часов. Вне базы — 10 часов.",
    "Вне базы — 10 часов. Дома — 12 часов.",
    "Вне базы — 10 часов; дома — 10 часов.",
    "На неизвестной площадке — 10 часов.",
    "Вне базы — 10 часов. Вне базы — 12 часов.",
    "Вне базы — 12 часов. Вне базы — 10 часов.",
])
def test_other_or_unresolved_subject_cannot_write_target(tmp_path, monkeypatch, raw):
    catalogue(tmp_path, monkeypatch)
    doc, answer, result = apply(raw)
    assert result["applied"] == 0 and result["unparsed"] == 1
    params = next(r for r in doc["rules"] if r["id"] == "R-R")["template_fit"]["params"]
    assert PARAM not in params and params["min_rest_home"]["value"] == 720
    assert model.is_open_question(_q(doc, QID), doc=doc)
    assert answer["parsed"]["ambiguous"] and answer["parsed"]["problems"]
    assert "предмет" in answer["note"]
    assert model.validate_schema(doc) == []


@pytest.mark.parametrize("raw,value", [("Вне базы — 10 часов.", 600),
                                       ("10,5 часов", 630), ("10:00", 600)])
def test_correct_subject_and_bare_answer_keep_healthy_path(tmp_path, monkeypatch, raw, value):
    catalogue(tmp_path, monkeypatch)
    doc, answer, result = apply(raw)
    assert result["applied"] == 1 and result["unparsed"] == 0
    assert _cell(doc)["value"] == value and not answer["parsed"]["ambiguous"]
    assert provenance.cell_problems(doc, "R-R", FAMILY, PARAM, _cell(doc)) == []
    assert model.validate_schema(doc) == []


@pytest.mark.parametrize("mode", ["missing", "collision"])
def test_missing_or_colliding_catalogue_subject_is_not_guessed(tmp_path, monkeypatch, mode):
    catalogue(tmp_path, monkeypatch, words=mode != "missing", collision=mode == "collision")
    doc, answer, result = apply("Отдых — 10 часов.")
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert answer["parsed"]["problems"]
    assert model.is_open_question(_q(doc, QID), doc=doc)


@pytest.mark.parametrize("quote", ["10 часов", "Дома — 10 часов"])
def test_quote_cannot_hide_foreign_subject(tmp_path, monkeypatch, quote):
    catalogue(tmp_path, monkeypatch)
    cell = {"value": 600, "quote": quote}
    got = provenance.value_problems(FAMILY, PARAM, cell, "Дома — 10 часов.")
    assert got and any("предмет" in p for p in got)


def test_old_answer_without_parsed_cannot_restore_foreign_subject(tmp_path, monkeypatch):
    doc, answer, result = apply("Дома — 10 часов.")  # documented old 0.5 grammar
    assert result["applied"] == 1
    answer.pop("parsed")
    next(r for r in doc["rules"] if r["id"] == "R-R")["template_fit"]["params"].pop(PARAM)
    catalogue(tmp_path, monkeypatch)
    before = copy.deepcopy(doc)
    notes = []
    fp.ensure_family_param_questions(doc, notes=notes)
    params = next(r for r in doc["rules"] if r["id"] == "R-R")["template_fit"]["params"]
    assert PARAM not in params
    assert _q(doc, QID)["answer"]["raw"] == _q(before, QID)["answer"]["raw"]
    assert notes


@pytest.mark.parametrize("raw", ["Вне базы — 10 часов только для пилотов.",
                                  "Вне базы — 10 часов, кроме пилотов.",
                                  "Не применяется для пилотов. Вне базы — 10 часов."])
def test_subject_does_not_remove_conditions(tmp_path, monkeypatch, raw):
    catalogue(tmp_path, monkeypatch)
    doc, answer, result = apply(raw)
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert answer["parsed"]["condition"] == {"raw": raw, "resolved": False}
    assert model.is_open_question(_q(doc, QID), doc=doc)


@pytest.mark.parametrize("unit,parameter,raw,value", [
    ("code", "fleet_type", "A320", "A320"),
    ("code_list", "fleet_types", "A320, B738", "A320, B738"),
])
def test_bare_codes_stay_subject_safe(tmp_path, monkeypatch, unit, parameter, raw, value):
    catalogue(tmp_path, monkeypatch)
    from cdpm.answer_subjects import problem
    assert problem(raw, "fleet_exclusions", parameter, unit) is None
    assert provenance.value_problems("fleet_exclusions", parameter,
                                    {"value": value, "quote": raw}, raw) == []
