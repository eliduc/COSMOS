"""Clock notation must not discard the words attached to its magnitude."""
import copy
import json
from pathlib import Path

import pytest

from cdpm import answer_parsing as ap, answers, catalog_bridge as cb
from cdpm import family_params as fp, model, provenance
from test_cdpm_a03m_provenance import _doc
from test_k03_subject_vocabulary import selected_catalogue

UNIT = "time_of_day_minutes"
FAMILY, PARAM = "night_series", "night_duty_end"
WORDS = ("килограммов", "часов", "минут", "суток", "kg", "seconds",
         "неизвестных единиц", "очень условных килограммов")


@pytest.fixture(autouse=True)
def clean():
    yield
    cb.forget()


def test_imported_reader_is_in_this_tree():
    assert Path(fp.__file__).resolve().parents[1] == Path.cwd().resolve()


@pytest.mark.parametrize("word", WORDS)
@pytest.mark.parametrize("template", ["22:00 {}", "22:00 ({})", "22:00 {} = 22:00"])
def test_direct_clock_reader_cannot_drop_words(word, template):
    assert fp.parse_value("22:00", UNIT)[0] == 1320
    raw = template.format(word)
    assert fp.parse_value(raw, UNIT) is None, raw


@pytest.mark.parametrize("raw,value", [
    ("00:00", 0), ("23:59", 1439), ("с 22:00", 1320),
    ("не более 22:00", 1320), ("22:00 и менее", 1320),
    ("22:00 (22:00)", 1320), ("22:00 = 22:00", 1320),
    ("22:00 и 22:00", 1320), ("22:00 для всех баз", 1320),
])
def test_clock_grammar_retains_healthy_values(raw, value):
    assert fp.parse_value(raw, UNIT)[0] == value


@pytest.mark.parametrize("modern", [False, True])
def test_typed_reader_and_provenance_refuse_unread_words(tmp_path, monkeypatch, modern):
    if modern:
        selected_catalogue(tmp_path, monkeypatch)
    q = {"id": f"Q-3b-параметр-{FAMILY}-{PARAM}"}
    healthy = f"{PARAM} — 22:00" if modern else "22:00"
    assert ap.parameter(q, healthy)["value"] == 1320
    for word in WORDS:
        raw = healthy + " " + word
        got = ap.parameter(q, raw)
        assert got["value"] is None and got["ambiguous"], (raw, got)
        assert provenance.value_problems(FAMILY, PARAM,
                                         {"value": 1320, "quote": "22:00"}, raw)


def prepared():
    doc = _doc()
    rule = next(r for r in doc["rules"] if r["id"] == "R-R")
    rule["template_fit"] = {"category": "A", "registry_rev": model.registry_fingerprint(),
                            "family": FAMILY, "params": {}}
    fp.ensure_family_param_questions(doc)
    doc = model.loads_model(json.dumps(doc, ensure_ascii=False))
    count = next(q for q in doc["questions"] if fp.question_target(q) ==
                 (FAMILY, "max_consecutive_night_duties"))
    assert answers.apply_answers(doc, {count["id"]: "3"})["applied"] == 1
    assert model.validate_schema(doc) == []
    q = next(q for q in doc["questions"] if fp.question_target(q) == (FAMILY, PARAM))
    return doc, q


@pytest.mark.parametrize("modern", [False, True])
def test_application_keeps_raw_and_no_value_with_useful_clock_advice(tmp_path, monkeypatch, modern):
    if modern:
        selected_catalogue(tmp_path, monkeypatch)
    doc, q = prepared()
    healthy = copy.deepcopy(doc)
    assert answers.apply_answers(healthy, {q["id"]: "22:00"})["applied"] == 1
    raw = "22:00 килограммов"
    before = copy.deepcopy(doc["rules"])
    result = answers.apply_answers(doc, {q["id"]: raw})
    assert result["unparsed"] == 1 and result["applied"] == 0, result
    assert doc["rules"] == before
    assert q["answer"]["raw"] == raw and q["answer"]["status"] == "не разобран"
    assert "килограммов" in q["answer"]["note"]
    assert "ЧЧ:ММ" in q["answer"]["note"]
    assert model.validate_schema(doc) == []


def test_actual_legacy_receipt_cannot_restore_unread_clock_words(monkeypatch):
    doc, q = prepared()
    raw = "22:00 килограммов"
    # Construct the historical accepted receipt through the real writer;
    # only then restore the new reader before testing reconstruction.
    with monkeypatch.context() as old:
        old.setattr(fp, "_clock_values", lambda s, rejected_words=None:
                    fp._hhmm_tokens(s, signed=False))
        assert answers.apply_answers(doc, {q["id"]: raw})["applied"] == 1
    rule = next(r for r in doc["rules"] if r["id"] == "R-R")
    assert rule["template_fit"]["params"][PARAM]["value"] == 1320
    q["answer"].pop("parsed")
    rule["template_fit"]["params"].pop(PARAM)
    active, _ = provenance.active_answer(doc, q)
    assert active is q
    notes = []
    fp.ensure_family_param_questions(doc, notes=notes)
    assert PARAM not in rule["template_fit"]["params"]
    assert q["answer"]["raw"] == raw
    assert any("не восстановлено" in note for note in notes), notes
    assert model.validate_schema(doc) == []


@pytest.mark.parametrize("modern", [False, True])
def test_healthy_clock_apply_retry_restore_preserves_source(tmp_path, monkeypatch, modern):
    if modern:
        selected_catalogue(tmp_path, monkeypatch)
    doc, q = prepared()
    raw = f"{PARAM} — 22:00" if modern else "22:00"
    assert answers.apply_answers(doc, {q["id"]: raw})["applied"] == 1
    rule = next(r for r in doc["rules"] if r["id"] == "R-R")
    cell = copy.deepcopy(rule["template_fit"]["params"][PARAM])
    receipt = copy.deepcopy(q["answer"])
    assert provenance.cell_problems(doc, "R-R", FAMILY, PARAM, cell) == []
    answers.apply_answers(doc, {q["id"]: raw})
    assert q["answer"] == receipt
    rule["template_fit"]["params"].pop(PARAM)
    fp.ensure_family_param_questions(doc)
    assert rule["template_fit"]["params"][PARAM] == cell
    assert model.validate_schema(doc) == []
