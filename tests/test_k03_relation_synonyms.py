"""COSMOS: relation direction and certainty cannot be inferred from the number."""
import copy
import json

import pytest

from cdpm import answer_parsing as ap, answers, catalog_bridge as cb
from cdpm import family_params as fp, model, provenance, render
from test_cdpm_a03m_provenance import _doc
from test_k03_subject_vocabulary import selected_catalogue

FAMILY = "connection_windows"
PARAMS = {"min": "min_turnaround", "max": "max_sit_duration"}
LOWER = ("минимум", "как минимум", "не менее", "свыше", "хотя бы", "от",
         "больше", "более", "начиная с", "не меньше")
UPPER = ("максимум", "не более", "не свыше", "до", "менее", "меньше", "не больше",
         "самое большее", "в пределах")
APPROX = ("примерно", "около", "порядка", "желательно")


@pytest.fixture(autouse=True)
def clean():
    yield
    cb.forget()


def question(relation):
    return {"id": f"Q-3a-параметр-{FAMILY}-{PARAMS[relation]}"}


@pytest.mark.parametrize("relation,phrases", [("max", LOWER), ("min", UPPER)])
@pytest.mark.parametrize("named", [True, False])
def test_every_opposing_phrase_refuses_by_relation(tmp_path, monkeypatch, relation, phrases, named):
    selected_catalogue(tmp_path, monkeypatch)
    q = question(relation)
    prefix = PARAMS[relation] + " — " if named else ""
    assert ap.parameter(q, prefix + "10 часов")["value"] == 600
    failures = []
    for phrase in phrases:
        raw = prefix + phrase + " 10 часов"
        got = ap.parameter(q, raw)
        if got["value"] is not None or not any("отношени" in p for p in got["problems"]):
            failures.append((raw, got))
        assert provenance.value_problems(FAMILY, PARAMS[relation],
                                         {"value": 600, "quote": "10 часов"}, raw)
    assert not failures, failures


@pytest.mark.parametrize("relation,phrases", [
    ("max", ("не более", "не свыше", "максимум", "до", "самое большее")),
    ("min", ("не менее", "минимум", "хотя бы", "начиная с", "от")),
])
@pytest.mark.parametrize("named", [True, False])
def test_matching_inclusive_phrases_keep_value_and_exact_quote(tmp_path, monkeypatch, relation, phrases, named):
    selected_catalogue(tmp_path, monkeypatch)
    for phrase in phrases:
        raw = (PARAMS[relation] + " — " if named else "") + phrase + " 10 часов"
        got = ap.parameter(question(relation), raw)
        assert got["value"] == 600 and not got["ambiguous"], (raw, got)
        assert got["quote"] in raw
        assert provenance.value_problems(FAMILY, PARAMS[relation],
                                         {"value": 600, "quote": got["quote"]}, raw) == []


@pytest.mark.parametrize("relation,phrase", [("min", "свыше"), ("min", "более"),
                                             ("max", "менее"), ("max", "меньше"),
                                             ("min", "точно"), ("max", "точно")])
def test_strict_relation_cannot_become_inclusive(tmp_path, monkeypatch, relation, phrase):
    selected_catalogue(tmp_path, monkeypatch)
    got = ap.parameter(question(relation), PARAMS[relation] + " — " + phrase + " 10 часов")
    assert got["value"] is None and any("отношени" in p for p in got["problems"])


def prepared(relation):
    doc = _doc()
    rule = next(r for r in doc["rules"] if r["id"] == "R-R")
    if relation == "min":
        # The real two-parameter rest family asks for its missing minimum.
        # Connection_windows has alternative complete sets, not two required
        # cells; its empty fit correctly asks only for the first (maximum).
        rule["template_fit"]["registry_rev"] = model.registry_fingerprint()
        target = ("rest_floors", "min_rest_outstation")
    else:
        rule.update(title="Стыковки", zone="PAIRING", statement="Предел стыковки требует уточнения.")
        rule["template_fit"] = {"category": "A", "registry_rev": model.registry_fingerprint(),
                                "family": FAMILY}
        target = (FAMILY, PARAMS[relation])
    fp.ensure_family_param_questions(doc)
    doc = model.loads_model(json.dumps(doc, ensure_ascii=False))
    assert model.validate_schema(doc) == []
    q = next(q for q in doc["questions"] if fp.question_target(q) == target)
    return doc, q


@pytest.mark.parametrize("relation,phrase", [("max", "свыше"), ("max", "хотя бы"),
    ("max", "начиная с"), ("min", "до"), ("min", "самое большее"), ("min", "в пределах")])
def test_apply_does_not_flip_relation_or_hide_reason(tmp_path, monkeypatch, relation, phrase):
    selected_catalogue(tmp_path, monkeypatch)
    doc, q = prepared(relation)
    subject = fp.question_target(q)[1]
    raw = subject + " — " + phrase + " 10 часов"
    healthy = copy.deepcopy(doc)
    assert answers.apply_answers(healthy, {q["id"]: subject + " — 10 часов"})["applied"] == 1
    before = copy.deepcopy(doc["rules"])
    result = answers.apply_answers(doc, {q["id"]: raw})
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert doc["rules"] == before and model.is_open_question(q, doc=doc)
    assert "отношени" in q["answer"]["note"]
    assert "отношени" in render.render_svod(doc)
    assert q["answer"]["raw"] == raw
    assert model.validate_schema(doc) == []


@pytest.mark.parametrize("phrase", APPROX)
@pytest.mark.parametrize("named", [True, False])
def test_approximation_is_not_an_exact_parameter(tmp_path, monkeypatch, phrase, named):
    selected_catalogue(tmp_path, monkeypatch)
    raw = (PARAMS["max"] + " — " if named else "") + phrase + " 10 часов"
    got = ap.parameter(question("max"), raw)
    assert got["value"] is None and got["ambiguous"]
    assert any("точное значение" in p and phrase in p for p in got["problems"])
    assert fp.parse_value(phrase + " 10 часов", "duration_minutes") is None
    assert provenance.value_problems(FAMILY, PARAMS["max"], {"value": 600, "quote": "10 часов"}, raw)


@pytest.mark.parametrize("phrase", APPROX)
def test_approximation_advice_reaches_apply_and_summary(tmp_path, monkeypatch, phrase):
    selected_catalogue(tmp_path, monkeypatch)
    doc, q = prepared("max")
    raw = PARAMS["max"] + " — " + phrase + " 10 часов"
    result = answers.apply_answers(doc, {q["id"]: raw})
    assert result["applied"] == 0 and result["unparsed"] == 1
    assert "точное значение" in q["answer"]["note"]
    assert phrase in q["answer"]["note"] and raw == q["answer"]["raw"]
    assert "точное значение" in render.render_svod(doc)


@pytest.mark.parametrize("raw", ["не «600»", "не (600)", 'не "600"', "не [600]"])
def test_negation_excerpt_keeps_the_actual_closing_delimiter(raw):
    assert raw in fp.negated_value_problem(raw, "duration_minutes")


def test_negative_then_desired_value_has_applicable_advice():
    reason = fp.negated_value_problem("не 600, а 700", "duration_minutes")
    assert "отрицан" in reason and "оставьте" in reason and "одно" in reason


def test_catalogue_subject_words_are_not_grammar_operators(tmp_path, monkeypatch):
    selected_catalogue(tmp_path, monkeypatch)
    subject = "примерно до порога"
    # A declared subject is data, not an approximation or a MAX operator.
    monkeypatch.setattr(cb, "subjects", lambda family: {PARAMS["min"]: [subject]})
    got = ap.parameter(question("min"), subject + " — 10 часов")
    assert got["value"] == 600 and not got["ambiguous"]


def test_within_a_period_is_not_silently_reinterpreted_as_an_upper_bound(tmp_path, monkeypatch):
    selected_catalogue(tmp_path, monkeypatch)
    raw = PARAMS["max"] + " — в пределах 10 часов"
    got = ap.parameter(question("max"), raw)
    assert got["value"] is None and any("период" in p for p in got["problems"])


@pytest.mark.parametrize("relation", ["min", "max"])
@pytest.mark.parametrize("raw", ["10 часов = 600 минут", "600 минут = 10 часов"])
def test_equal_notations_do_not_change_the_parameter_relation(tmp_path, monkeypatch, relation, raw):
    selected_catalogue(tmp_path, monkeypatch)
    got = ap.parameter(question(relation), raw)
    assert got["value"] == 600 and not got["ambiguous"]
    assert got["quote"] in raw
    assert provenance.value_problems(FAMILY, PARAMS[relation],
                                     {"value": 600, "quote": got["quote"]}, raw) == []


@pytest.mark.parametrize("relation,symbol,accepted", [
    ("min", ">=", True), ("min", "≥", True), ("min", "<=", False),
    ("min", ">", False), ("min", "=", False), ("min", "≠", False),
    ("max", "<=", True), ("max", "≤", True), ("max", ">=", False),
    ("max", "<", False), ("max", "=", False),
])
@pytest.mark.parametrize("named", [True, False])
def test_symbol_has_the_same_catalogue_contract(tmp_path, monkeypatch, relation, symbol, accepted, named):
    selected_catalogue(tmp_path, monkeypatch)
    raw = (PARAMS[relation] + " — " if named else "") + symbol + " 10 часов"
    got = ap.parameter(question(relation), raw)
    assert (got["value"] == 600 and not got["ambiguous"]) == accepted, got
    if not accepted:
        assert any("отношени" in p for p in got["problems"])
    else:
        assert got["quote"] in raw


@pytest.mark.parametrize("relation", ["eq", "window"])
@pytest.mark.parametrize("phrase", ["ровно", "точно", "=", "минимум", "максимум", "свыше", "до"])
def test_eq_and_window_do_not_inherit_an_inclusive_scalar_bound(tmp_path, monkeypatch, relation, phrase):
    selected_catalogue(tmp_path, monkeypatch)
    # Vary only the relation in the selected parameter contract: the parser
    # must follow that field, not guess from the parameter's machine name.
    original = fp._param_spec
    monkeypatch.setattr(fp, "_param_spec", lambda family, param:
                        {**original(family, param), "relation": relation})
    raw = PARAMS["max"] + " — " + phrase + " 10 часов"
    got = ap.parameter(question("max"), raw)
    accepted = relation == "eq" and phrase in ("ровно", "точно", "=")
    assert (got["value"] == 600 and not got["ambiguous"]) == accepted, got
    if not accepted:
        assert any("отношени" in p for p in got["problems"])
