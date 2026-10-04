"""CDPM-428: the declared unit word belongs to the scalar token."""
import copy
import json
from pathlib import Path

import pytest

from cdpm import answer_parsing as ap, answers, catalog_bridge as cb, family_params as fp, model, provenance
from template_catalog import subject_vocabulary, unit_vocabulary
from test_k03_subject_vocabulary import vocabulary as subject_words
from test_k03_unit_vocabulary import vocabulary as unit_words
from test_catalog_060_versioned_schema import modern_docs
from test_template_catalog_validate import _broken
from test_cdpm_a03m_provenance import _doc


def selected(tmp_path, monkeypatch, change=None):
    def prepare(docs):
        modern_docs(docs)
        docs.update(subject_vocabulary.attach(docs, subject_words()))
        words = unit_words()
        if change:
            change(words)
        docs.update(unit_vocabulary.attach(docs, words))
    folder = _broken(tmp_path, prepare)
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    cb.forget()
    cat = cb.catalog()
    assert not [p for p in cat.validate() if p.severity == "error"]
    return cat


@pytest.fixture(autouse=True)
def cleanup():
    yield
    cb.forget()


@pytest.mark.parametrize("unit,family,param,words", [
    ("count", "duty_series_limit", "max_duty_series_length",
     ("штука", "штуки", "штук", "шт", "раз", "раза", "посадка", "посадки",
      "посадок", "перегон", "перегона", "перегонов", "смена", "смены", "смен",
      "сутки", "суток", "день", "дня", "дней", "человек", "человека")),
    ("credit_hours", "balanced_workload", "workload_spread_threshold",
     ("кредитный час", "кредитных часов", "кредитного часа", "ч кредита",
      "час", "часа", "часов", "часы", "часах", "ч")),
])
def test_every_declared_count_and_credit_word_is_consumed(tmp_path, monkeypatch, unit, family, param, words):
    cat = selected(tmp_path, monkeypatch)
    assert set(words) == set(cat.dictionaries()["unit_words"][unit]["words"])
    q = {"id": f"Q-3b-параметр-{family}-{param}"}
    failures = []
    for word in words:
        raw = f"{param} — 3 {word}"
        got = ap.parameter(q, raw)
        if got["ambiguous"] or got["value"] != 3:
            failures.append((raw, got))
        else:
            assert got["quote"] == raw
            assert provenance.value_problems(family, param, {"value": 3, "quote": raw}, raw) == []
    assert not failures, failures


@pytest.mark.parametrize("raw,unit,want", [
    ("22:00 чч:мм", "time_of_day_minutes", 1320),
    ("10:00 чч:мм", "duration_minutes", 600),
    ("3 дн", "duration_minutes", 4320),
    ("10 ч 30 мин", "duration_minutes", 630),
    ("10ч30мин", "duration_minutes", 630),
    ("3 человека", "count", 3),
    ("3 ЧЕЛОВЕКА", "count", 3),
    ("3 шт.", "count", 3),
    ("3 ч кредита", "credit_hours", 3),
])
def test_direct_reader_uses_declared_words_and_exact_conversion(tmp_path, monkeypatch, raw, unit, want):
    selected(tmp_path, monkeypatch)
    got = fp.parse_value(raw, unit)
    assert got is not None and got[0] == want, (raw, got)


@pytest.mark.parametrize("raw,unit", [
    ("22:00 часов", "time_of_day_minutes"), ("22:00 килограммов", "time_of_day_minutes"),
    ("3 человекоподобных", "count"), ("3 человек лишних", "count"),
    ("3 часов", "count"), ("3 минуты", "credit_hours"),
    ("10 ч 90 мин", "duration_minutes"), ("1.5 ч 30 мин", "duration_minutes"),
    ("3 чч:мм", "time_of_day_minutes"), ("24:00 чч:мм", "time_of_day_minutes"),
])
def test_unknown_or_incompatible_unit_does_not_become_a_known_scalar(tmp_path, monkeypatch, raw, unit):
    selected(tmp_path, monkeypatch)
    assert fp.parse_value(raw, unit) is None, raw


def test_real_selected_scale_is_used_not_a_word_whitelist(tmp_path, monkeypatch):
    def change(words):
        entry = words["unit_words"]["duration_minutes"]
        entry["words"].append("тестовая единица")
        entry["scale"]["тестовая единица"] = 7
    selected(tmp_path, monkeypatch, change)
    assert fp.parse_value("3 тестовая единица", "duration_minutes")[0] == 21


@pytest.mark.parametrize("modifier", ["порог", "не ниже", "по меньшей мере", "не превышает"])
def test_consumed_unit_does_not_legalize_unknown_relation(tmp_path, monkeypatch, modifier):
    selected(tmp_path, monkeypatch)
    q = {"id": "Q-3b-параметр-duty_series_limit-max_duty_series_length"}
    got = ap.parameter(q, f"max_duty_series_length — {modifier} 3 человека")
    assert got["ambiguous"] and got["value"] is None


def test_import_is_from_this_worktree():
    assert Path(fp.__file__).resolve().parent.parent == Path.cwd().resolve()


@pytest.mark.parametrize("family,param,word", [
    ("night_series", "max_consecutive_night_duties", "смены"),
    ("balanced_workload", "workload_spread_threshold", "ч кредита"),
    ("rest_floors", "min_rest_outstation", "часов"),
])
def test_unit_refusal_names_actual_words_and_the_suggested_input_works(tmp_path, monkeypatch, family, param, word):
    selected(tmp_path, monkeypatch)
    q = {"id": f"Q-3b-параметр-{family}-{param}"}
    bad = ap.parameter(q, f"{param} — 3 неопознанных единиц")
    note = " ".join(bad["problems"])
    assert bad["value"] is None and "слова записи:" in note and word in note
    assert "верхнего предела" not in note and "нижнего предела" not in note
    assert ap.parameter(q, f"{param} — 3 {word}")["value"] is not None


def test_all_declared_numeric_words_on_the_real_reader(tmp_path, monkeypatch):
    cat = selected(tmp_path, monkeypatch)
    failed, checked = [], 0
    for unit in fp._NUMERIC_UNITS:
        entry = cat.dictionaries()["unit_words"][unit]
        for word in entry["words"]:
            raw = f"10:00 {word}" if word == "чч:мм" else f"3 {word}"
            expected = 600 if word == "чч:мм" else 3 * entry["scale"].get(word, 1)
            got = fp.parse_value(raw, unit)
            checked += 1
            if got is None or got[0] != expected:
                failed.append((unit, raw, expected, got))
    assert checked == 55 and not failed, failed


@pytest.mark.parametrize("raw,unit,expected", [
    ("0,1 часа", "duration_minutes", 6),
    ("10,5 часа", "duration_minutes", 630),
    ("0,1 минуты", "duration_minutes", None),
    ("3,5 смены", "count", None),
    ("3,5 ч кредита", "credit_hours", 3.5),
    ("3,5 часа на кредитный час", "hours_per_credit_hour", 3.5),
    ("0 часов на час кредита", "hours_per_credit_hour", None),
    ("3 часов на час кредита в день", "hours_per_credit_hour", None),
    ("3 часов на час кредита для SVO", "hours_per_credit_hour", None),
    ("-0 ч 30 мин", "duration_minutes", None),
])
def test_catalogue_conversion_keeps_numeric_form_constraints(tmp_path, monkeypatch, raw, unit, expected):
    selected(tmp_path, monkeypatch)
    got = fp.parse_value(raw, unit)
    assert (got is None) if expected is None else (got is not None and got[0] == expected)


@pytest.mark.parametrize("family,param,word", [
    ("night_series", "max_consecutive_night_duties", "смены"),
    ("balanced_workload", "workload_spread_threshold", "ч кредита"),
])
def test_real_apply_retry_and_restoration_preserve_unit_and_source(tmp_path, monkeypatch, family, param, word):
    selected(tmp_path, monkeypatch)
    doc = _doc()
    rule = next(r for r in doc["rules"] if r["id"] == "R-R")
    rule["template_fit"] = {"category": "A", "registry_rev": model.registry_fingerprint(),
                            "family": family, "params": {}}
    fp.ensure_family_param_questions(doc)
    doc = model.loads_model(json.dumps(doc, ensure_ascii=False))
    q = next(q for q in doc["questions"] if fp.question_target(q) == (family, param))
    raw = f"{param} — 3 {word}"
    assert answers.apply_answers(doc, {q["id"]: raw})["applied"] == 1
    rule = next(r for r in doc["rules"] if r["id"] == "R-R")
    cell, receipt = copy.deepcopy(rule["template_fit"]["params"][param]), copy.deepcopy(q["answer"])
    assert cell["value"] == 3 and cell["quote"] == raw
    assert provenance.cell_problems(doc, "R-R", family, param, cell) == []
    assert model.validate_schema(doc) == []
    answers.apply_answers(doc, {q["id"]: raw})
    assert q["answer"] == receipt
    # Exercise the historical re-reader, not the cached parsed carrier.
    q["answer"].pop("parsed")
    rule["template_fit"]["params"].pop(param)
    assert provenance.active_answer(doc, q)[0] is q
    fp.ensure_family_param_questions(doc)
    assert rule["template_fit"]["params"][param] == cell
    assert q["answer"]["raw"] == raw
    assert model.validate_schema(doc) == []
