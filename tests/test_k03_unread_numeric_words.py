"""A named subject cannot license dropping the rest of a numeric answer."""
import copy
import re
from pathlib import Path

import pytest

from cdpm import answer_parsing as ap, answer_subjects, answers, catalog_bridge as cb
from cdpm import family_params as fp, model, provenance, render
from test_k03_relation_synonyms import prepared
from test_k03_subject_vocabulary import selected_catalogue


@pytest.fixture(autouse=True)
def clean():
    yield
    cb.forget()


def test_imports_belong_to_the_tree_being_measured():
    assert Path(answer_subjects.__file__).resolve().parents[1] == Path.cwd().resolve()


CASES = [
    ("connection_windows", "max_sit_duration", "10 часов", 600),
    ("connection_windows", "min_turnaround", "10 часов", 600),
    ("night_series", "night_duty_end", "22:00", 1320),
    ("duty_series_limit", "max_duty_series_length", "3", 3),
    ("balanced_workload", "workload_spread_threshold", "75 часов", 75),
]
MODIFIERS = ("порог", "потолок", "предел", "не ниже", "не короче",
             "по меньшей мере", "не выше", "не дольше", "не превышает",
             "ориентировочно", "где-то", "в районе", "плюс-минус",
             "неизвестный_модификатор", "неучтённое условие")


@pytest.mark.parametrize("family,param,number,value", CASES)
@pytest.mark.parametrize("position", ["prefix", "infix", "suffix", "sentence"])
def test_unknown_words_cannot_disappear_next_to_a_named_subject(
        tmp_path, monkeypatch, family, param, number, value, position):
    selected_catalogue(tmp_path, monkeypatch)
    q = {"id": f"Q-3a-параметр-{family}-{param}"}
    healthy = f"{param} — {number}"
    assert ap.parameter(q, healthy)["value"] == value
    bad = []
    for word in MODIFIERS:
        raw = {"prefix": f"{word} {healthy}", "infix": f"{param} — {word} {number}",
               "suffix": f"{healthy} {word}", "sentence": f"{healthy}. {word.capitalize()}."}[position]
        got = ap.parameter(q, raw)
        if got["value"] is not None or not got["ambiguous"]:
            bad.append((raw, got))
        assert got["problems"], (raw, got)
    assert not bad, bad


@pytest.mark.parametrize("raw", [
    "max_sit_duration — ориентировочно 10 часов",
    "max_sit_duration — 10 часов. Оговорка неопознана.",
    "max_sit_duration — 10 часов ~ 600 минут",
    "max_sit_duration — 10 часов + 600 минут",
])
def test_quote_cannot_launder_unknown_words_or_operators(tmp_path, monkeypatch, raw):
    selected_catalogue(tmp_path, monkeypatch)
    problems = provenance.value_problems("connection_windows", "max_sit_duration",
                                         {"value": 600, "quote": "10 часов"}, raw)
    assert problems and any("не распознан" in p for p in problems), problems


@pytest.mark.parametrize("raw", [
    "max_sit_duration — не более 10 часов",
    "max_sit_duration: 10 часов = 600 минут",
    "max_sit_duration — 10 часов и 600 минут",
    "max_sit_duration — 10 часов. max_sit_duration — 600 минут.",
    "max_sit_duration — 10 ч 0 мин",
    "max_sit_duration — 10 часов для всех баз",
    "max_sit_duration — для всех баз 10 часов",
])
def test_consumed_grammar_and_healthy_repetition_keep_exact_raw(tmp_path, monkeypatch, raw):
    selected_catalogue(tmp_path, monkeypatch)
    q = {"id": "Q-3a-параметр-connection_windows-max_sit_duration"}
    parsed = ap.parameter(q, raw)
    assert parsed["value"] == 600 and not parsed["ambiguous"], parsed
    assert parsed["quote"] in raw
    assert provenance.value_problems("connection_windows", "max_sit_duration",
                                     {"value": 600, "quote": parsed["quote"]}, raw) == []


def test_catalogue_phrase_is_data_even_when_it_looks_like_an_unknown_modifier(tmp_path, monkeypatch):
    selected_catalogue(tmp_path, monkeypatch)
    original = cb.subjects
    monkeypatch.setattr(cb, "subjects", lambda family: {
        **original(family), "max_sit_duration": ["ориентировочно до порога"]}
        if family == "connection_windows" else original(family))
    raw = "Ориентировочно до порога — 10 часов"
    q = {"id": "Q-3a-параметр-connection_windows-max_sit_duration"}
    assert ap.parameter(q, raw)["value"] == 600
    bad = raw + " ориентировочно"
    assert ap.parameter(q, bad)["value"] is None


@pytest.mark.parametrize("relation", ["min", "max", "eq"])
def test_every_advertised_relation_form_is_a_healthy_complete_input(tmp_path, monkeypatch, relation):
    selected_catalogue(tmp_path, monkeypatch)
    old = fp._param_spec
    monkeypatch.setattr(fp, "_param_spec", lambda family, name: {
        **old(family, name), "relation": relation})
    q = {"id": "Q-3a-параметр-connection_windows-max_sit_duration"}
    bad = ap.parameter(q, "max_sit_duration — неопознанное слово 10 часов")
    note = " ".join(bad["problems"])
    assert "неопознанное слово" in note and "не распознан" in note
    assert "не удаляйте ограничения" in note
    forms = [x for x in re.findall("«([^»]+)»", note) if "…" in x]
    assert len(forms) >= 3
    for form in forms:
        raw = "max_sit_duration — " + form.replace("…", "10 часов")
        assert ap.parameter(q, raw)["value"] == 600, (form, ap.parameter(q, raw))
    if relation == "max":
        assert not any("в пределах" in x for x in forms)
        mismatch = ap.parameter(q, "max_sit_duration — ровно 10 часов")
        text = " ".join(mismatch["problems"])
        assert "отношение ответа eq" in text and "(max)" in text
        assert "не более …" in text


@pytest.mark.parametrize("relation", ["max", "min"])
def test_refusal_is_atomic_and_explained(tmp_path, monkeypatch, relation):
    selected_catalogue(tmp_path, monkeypatch)
    doc, q = prepared(relation)
    family, param = fp.question_target(q)
    raw = f"{param} — неопознанный оборот 10 часов"
    before = copy.deepcopy(doc["rules"])
    got = answers.apply_answers(doc, {q["id"]: raw})
    assert got["applied"] == 0 and got["unparsed"] == 1
    assert doc["rules"] == before and model.is_open_question(q, doc=doc)
    assert q["answer"]["raw"] == raw
    assert "не распознан" in q["answer"]["note"]
    assert "неопознанный оборот" in render.render_svod(doc)
    assert model.validate_schema(doc) == []


@pytest.mark.parametrize("relation", ["max", "min"])
def test_real_historical_receipt_reaches_guard_and_cannot_restore_unknown_words(tmp_path, monkeypatch, relation):
    selected_catalogue(tmp_path, monkeypatch)
    doc, q = prepared(relation)
    family, param = fp.question_target(q)
    raw = f"{param} — неопознанный оборот 10 часов"
    # Construct an actual old accepted receipt, rather than inventing its
    # fields. Restore the production guard before any restoration attempt.
    with monkeypatch.context() as old:
        old.setattr(answer_subjects, "_unread_numeric_problem", lambda *args: None)
        assert answers.apply_answers(doc, {q["id"]: raw})["applied"] == 1
    q["answer"].pop("parsed")
    for rule in doc["rules"]:
        if rule.get("template_fit", {}).get("family") == family:
            rule["template_fit"].get("params", {}).pop(param, None)
    active, _ = provenance.active_answer(doc, q)
    assert active is q
    notes = []
    fp.ensure_family_param_questions(doc, notes=notes)
    assert all(param not in r.get("template_fit", {}).get("params", {})
               for r in doc["rules"] if r.get("template_fit", {}).get("family") == family)
    assert q["answer"]["raw"] == raw
    assert any("не восстановлено" in note and "не распознан" in note for note in notes)
