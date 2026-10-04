"""Stand goal wording has task scope without changing answer identity.

These are constructor/normalizer checks, not exports, SMT, solver runs or
independent acceptance. model_doc uses one new synthetic SOFT rule from the
real pinned draft; objective zone is varied only to check goal declarations.
"""
import copy
import os
from pathlib import Path

import pytest

import template_catalog as tc
from cdpm import model as m
from vmp import stand_adapters as sa, stand_solver as ss, stand_tasks as st


FINGERPRINT = "f1fff61c6b8ba48dfe936c93d64fe23c60d38f9de027ba393685e1cd6c1ab0ad"
TASKS = {"3a": "пейринга", "3b": "ростеринга"}


def _assert_question(question, part, ids, decline):
    assert question["id"] == f"Q-{part}-GOAL"
    assert question["part"] == part
    assert question["title"] == f"Целевая {TASKS[part]}"
    assert question["text"] == f"целевая функция {TASKS[part]}"
    assert question["answer_format"] == {"type": "registry"}
    assert question["rule_refs"] == ids
    assert question["source_refs"] == [{"sid": "S1", "anchor": "стенд"}]
    raw = "Синтетическая декларация стенда (не ответ авиакомпании): " + (
        "любое допустимое решение" if decline else "критерий объявлен целевой пакета")
    assert question["answer"] == {"raw": raw, "status": "применён",
                                  "revision": 1, "goal_decline": decline}


@pytest.mark.parametrize("part", ["3a", "3b"])
@pytest.mark.parametrize("decline", [False, True])
def test_goal_question_preserves_identity_references_and_answer(part, decline):
    ids = ["R-STAND-one", "R-STAND-two"]
    before = list(ids)
    question = ss._goal_question(part, ids, decline=decline)
    _assert_question(question, part, before, decline)
    assert ids == before and question["rule_refs"] is not ids
    question["rule_refs"].append("R-only-in-question")
    assert ids == before


@pytest.mark.parametrize("decline", [False, True])
def test_real_normalizer_distinguishes_tasks_but_still_detects_duplicates(decline):
    pairing = ss._goal_question("3a", ["R"], decline=decline)
    rostering = ss._goal_question("3b", ["R"], decline=decline)
    assert m.normalized_question_text(pairing["text"]) != m.normalized_question_text(rostering["text"])
    assert m.normalized_question_text(pairing["title"]) != m.normalized_question_text(rostering["title"])
    for question in (pairing, rostering):
        variant = "  «" + question["text"].upper().replace(" ", " — ") + "»?! "
        assert m.normalized_question_text(variant) == m.normalized_question_text(question["text"])
        other_answer = ss._goal_question(question["part"], ["R"], decline=not decline)
        assert m.normalized_question_text(other_answer["text"]) == m.normalized_question_text(question["text"])


@pytest.fixture
def typed_stand_input():
    catalog = tc.current(Path(os.environ["STAND_DRAFT_CATALOG"]).resolve(strict=True), validated=True)
    assert catalog.version() == "0.7.0-draft.1" and catalog.fingerprint() == FINGERPRINT
    family = "duty_extension_penalty"
    world = {"form": "schedule", "params": {
        "duty_extension_cap": 960, "long_duty_threshold": 720},
        "conditions": {"extension_bases": ["ST1"], "extension_consent": "yes"},
        "crews": [{"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T1"]}],
        "flights": [{"id": "F1", "from": "ST1", "to": "ST2",
                     "dep": "2026-10-01T06:00", "arr": "2026-10-01T08:00",
                     "fleet_type": "T1"}],
        "horizon": {"from": "2026-10-01", "to": "2026-10-31"}}
    meta = st._family_meta(catalog)
    assert st.families_for(world, family, meta) == [family]
    rule = sa._rule_of(world, family, meta[family], meta[family]["params"])
    assert rule["kind"] == "SOFT"
    return catalog, family, world, [rule]


@pytest.mark.parametrize("zone,declines", [
    (None, (True, True)), ("PAIRING", (False, True)),
    ("ROSTERING", (True, False)), ("BOTH", (False, False)),
])
def test_model_doc_keeps_both_task_questions_and_exact_goal_scope(typed_stand_input, zone, declines):
    catalog, family, world, rules = typed_stand_input
    objective = None if zone is None else {
        "id": "O-STAND-SCOPE", "zone": zone, "title": "Синтетическая целевая",
        "statement": "Декларация области целевой для проверки конструктора",
        "direction": "minimize", "sources": [{"sid": "S1", "anchor": "стенд"}]}
    before = copy.deepcopy((world, rules, objective))
    doc = ss.model_doc(rules, world=world, family=family, catalog=catalog, objective=objective)
    assert (world, rules, objective) == before
    assert len(doc["questions"]) == 2
    ids = [rule["id"] for rule in doc["rules"]]
    assert ids == [rule["id"] for rule in rules]
    for question, part, decline in zip(doc["questions"], ("3a", "3b"), declines):
        _assert_question(question, part, ids, decline)
    assert len({m.normalized_question_text(q["text"]) for q in doc["questions"]}) == 2
    assert doc["objectives"] == ([objective] if objective else [])
    if objective:
        assert doc["objectives"][0] is not objective
    # This checks the genuine rule constructor, not objective executability.
    assert doc["rules"][0]["template_fit"]["schema_version"] == "landing/2"
