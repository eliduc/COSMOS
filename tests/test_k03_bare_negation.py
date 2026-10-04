"""COSMOS: rejecting a number is not an instruction to apply that number."""
import copy
import json

import pytest

from cdpm import answer_parsing as ap, answer_subjects, answers, catalog_bridge as cb
from cdpm import family_params as fp, model, provenance, render
from test_cdpm_a03m_provenance import _doc
from test_k03_subject_vocabulary import selected_catalogue

FAMILY = "duty_extension_penalty"
PARAM = "long_duty_threshold"
SUBJECT = "мягкий порог продлённой смены"
NEGATIVES = ["не 10 часов", "не 10 ч", "НЕ 10 часов", "никак не 10 часов",
             "ни в коем случае не 10 часов", "не 600 минут"]


@pytest.fixture(autouse=True)
def clean():
    yield
    cb.forget()


def question():
    return {"id": f"Q-3a-параметр-{FAMILY}-{PARAM}"}


@pytest.mark.parametrize("tail", NEGATIVES)
@pytest.mark.parametrize("named", [True, False])
def test_negative_number_refuses_with_exact_reason(tmp_path, monkeypatch, tail, named):
    selected_catalogue(tmp_path, monkeypatch)
    raw = (SUBJECT + " — " if named else "") + tail
    parsed = ap.parameter(question(), raw)
    assert parsed["value"] is None and parsed["ambiguous"]
    reason = " ".join(parsed["problems"])
    assert "отрицан" in reason.lower()
    assert ("НЕ 10" if "НЕ" in tail else "не 600" if "600" in tail else "не 10") in reason
    assert "желаем" in reason.lower()
    assert provenance.value_problems(FAMILY, PARAM, {"value": 600, "quote": "600 минут" if "600" in tail else "10 ч"}, raw)


def prepared():
    doc = _doc()
    rule = next(r for r in doc["rules"] if r["id"] == "R-R")
    rule.update(kind="SOFT", title="Продление смены", statement="Потолок продления — 14 часов.")
    rule["template_fit"] = {"category": "A", "registry_rev": model.registry_fingerprint(),
                            "family": FAMILY, "params": {"duty_extension_cap": {
                                "value": 840, "quote": "14 часов"}}}
    fp.ensure_family_param_questions(doc)
    doc = model.loads_model(json.dumps(doc, ensure_ascii=False))
    assert model.validate_schema(doc) == []
    q = next(q for q in doc["questions"] if fp.question_target(q) == (FAMILY, PARAM))
    return doc, q


@pytest.mark.parametrize("tail", NEGATIVES)
def test_real_apply_keeps_question_open_and_does_not_write_negated_value(tmp_path, monkeypatch, tail):
    selected_catalogue(tmp_path, monkeypatch)
    doc, q = prepared()
    healthy = copy.deepcopy(doc)
    assert answers.apply_answers(healthy, {q["id"]: SUBJECT + " — 10 часов"})["applied"] == 1
    raw = SUBJECT + " — " + tail
    result = answers.apply_answers(doc, {q["id"]: raw})
    assert result["applied"] == 0 and result["unparsed"] == 1
    rule = next(r for r in doc["rules"] if r["id"] == "R-R")
    assert PARAM not in rule["template_fit"]["params"]
    assert model.is_open_question(q, doc=doc)
    assert q["answer"]["raw"] == raw
    assert "отрицан" in q["answer"]["note"].lower()
    assert "отрицан" in render.render_svod(doc).lower()
    before = copy.deepcopy(doc)
    repeated = answers.apply_answers(doc, {q["id"]: raw})
    assert repeated["applied"] == 0 and repeated["unparsed"] == 1
    # Re-submitting an open, unparsed answer is a new processing revision.
    # Its refusal must remain identical; rule values/blockers cannot change.
    assert doc["rules"] == before["rules"]
    previous = next(x for x in before["questions"] if x["id"] == q["id"])["answer"]
    for key in ("raw", "parsed", "note", "status"):
        assert q["answer"][key] == previous[key]
    assert model.is_open_question(q, doc=doc)
    assert model.validate_schema(doc) == []


@pytest.mark.parametrize("tail", NEGATIVES)
def test_historical_applied_negative_cannot_be_restored(tmp_path, monkeypatch, tail):
    selected_catalogue(tmp_path, monkeypatch)
    doc, q = prepared()
    raw = SUBJECT + " — " + tail
    # Simulate the old parser only while creating the historical document.
    # The real restoration below runs with the production guard restored.
    with monkeypatch.context() as old:
        old.setattr(fp, "negated_value_problem", lambda raw, unit: "")
        # The later whole-text guard independently rejects the same old
        # input. Disable it only to construct history, never for restoration.
        old.setattr(answer_subjects, "_unread_numeric_problem", lambda *args: None)
        assert answers.apply_answers(doc, {q["id"]: raw})["applied"] == 1
    q["answer"].pop("parsed")
    rule = next(r for r in doc["rules"] if r["id"] == "R-R")
    rule["template_fit"]["params"].pop(PARAM)
    active, _ = provenance.active_answer(doc, q)
    assert active is q  # not stopped by an unrelated provenance barrier
    notes = []
    fp.ensure_family_param_questions(doc, notes=notes)
    assert PARAM not in rule["template_fit"]["params"]
    assert q["answer"]["raw"] == raw
    assert any("не восстановлено" in note and "отрицан" in note for note in notes)


@pytest.mark.parametrize("unit,raw", [
    ("duration_minutes", "не 10 часов"), ("duration_minutes", "не 10:00"),
    ("duration_minutes", "10 часов. Не 10 часов."),
    ("duration_minutes", "не 10 часов, а 12 часов"),
    ("count", "не 10"), ("time_of_day_minutes", "не 10:00"),
    ("credit_hours", "не 10 часов"), ("hours_per_credit_hour", "не 2"),
    ("duration_minutes", "не\n10 часов"), ("duration_minutes", "не «10 часов»"),
])
def test_direct_scalar_reader_cannot_bypass_negative_context(unit, raw):
    assert fp.parse_value(raw, unit) is None
    assert "отрицан" in fp.value_context_problem(raw, unit).lower()


@pytest.mark.parametrize("family,param,raw,value", [
    (FAMILY, PARAM, SUBJECT + " — не более 10 часов", 600),
    (FAMILY, PARAM, SUBJECT + " — максимум 10 часов", 600),
    (FAMILY, PARAM, SUBJECT + " — до 10 часов", 600),
    (FAMILY, PARAM, SUBJECT + " — 10 часов", 600),
    ("rest_floors", "min_rest_outstation", "Вне базы — не менее 10 часов", 600),
])
def test_legal_relation_negation_keeps_healthy_value(tmp_path, monkeypatch, family, param, raw, value):
    selected_catalogue(tmp_path, monkeypatch)
    parsed = ap.parameter({"id": f"Q-3b-параметр-{family}-{param}"}, raw)
    assert parsed["value"] == value and not parsed["ambiguous"], parsed


@pytest.mark.parametrize("raw", ["не более 10 часов", "не менее 10 часов", "не позже 10:00"])
def test_scalar_guard_does_not_reinterpret_relation_phrases(raw):
    assert fp.parse_value(raw, "duration_minutes")[0] == 600


@pytest.mark.parametrize("tail", ["не ровно 10 часов", "кроме 10 часов", "не десять часов"])
def test_previous_refusals_stay_refusals(tmp_path, monkeypatch, tail):
    selected_catalogue(tmp_path, monkeypatch)
    assert ap.parameter(question(), SUBJECT + " — " + tail)["value"] is None
