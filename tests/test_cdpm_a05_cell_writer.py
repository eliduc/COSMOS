"""Current-format answer writers: no new or silently healed carrier conflict.

Synthetic dictionaries and real catalog/parser/provenance; no model or solver.
These tests do not certify landing/2 or package migration.
"""
import copy

import pytest

from cdpm import answers, family_params as fp, model as m, provenance, question_editions
from test_cdpm_a02_worker_row_results import _worker


FAMILY = "rest_floors"
PARAM = "min_rest_outstation"
OTHER = "min_rest_home"


def _doc():
    rule = {"id": "R-REST", "title": "Отдых", "statement": "дома не менее 12 часов",
            "kind": "HARD", "zone": "BOTH", "sources": [{"sid": "S1", "anchor": "п.1"}],
            "template_fit": {"category": "A", "family": FAMILY,
                             "registry_rev": m.registry_fingerprint(),
                             "params": {OTHER: {"value": 720, "quote": "12 часов"}}}}
    doc = {"meta": {"customer": "Тест", "mode": 1, "cdpm_version": "0.1"},
           "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
           "rules": [rule], "questions": [], "objectives": []}
    created = fp.ensure_family_param_questions(doc)
    assert len(created) == 1
    q = next(q for q in doc["questions"] if q["id"] == created[0])
    assert m.validate_schema(doc) == []
    assert m.validate_model(doc) == []
    return doc, rule, q


def _legacy(value):
    return [{"name": PARAM, "value": value, "unit": "duration_minutes",
             "relation": "min", "as_written": f"{value} минут"}]


@pytest.mark.parametrize("legacy_value,accepted", [(600, True), (900, False)])
def test_answer_checks_candidate_before_writing_or_dropping_other_owned_cells(
        legacy_value, accepted, monkeypatch):
    doc, rule, q = _doc()
    rule["params"] = _legacy(legacy_value)
    other = copy.deepcopy(rule)
    # expand freezes the canonical sorted scope. Keep the prospective writer
    # before the row whose old answer cell must be dropped.
    other["id"] = "R-SECOND"
    other.pop("params")
    doc["rules"].append(other)
    original_question = copy.deepcopy(q)
    q = question_editions.expand(doc, q, [other["id"]])
    assert doc["questions"][0] == original_question
    assert q["rule_refs"] == [rule["id"], other["id"]]
    # Loaded/edited ownership is intentional: the candidate must be checked
    # before this pre-existing evidence can be removed.
    other["template_fit"]["params"][PARAM] = {
        "value": 600, "quote": "10 часов", "answer": q["id"],
        "source": provenance.answer_source(q, PARAM, "10 часов")}
    # Observe the REAL writer, without replacing its decision. A scope or
    # routing refusal must not masquerade as evidence of the A05 boundary.
    writes = []
    real_write = fp.write_cell

    def observed_write(target, name, cell):
        writes.append((target["id"], name, cell["value"]))
        return real_write(target, name, cell)

    monkeypatch.setattr(fp, "write_cell", observed_write)
    before = copy.deepcopy(doc["rules"])
    result = fp.apply_param_answer(doc, q, "10 часов", 1,
                                   targets=answers._answer_targets(q, doc))
    assert writes == [(rule["id"], PARAM, 600)]
    # Even a healthy cell write does not complete a two-row answer (A02).
    assert result["applied"] is False, (result, q.get("answer"))
    if not accepted:
        assert doc["rules"] == before, q.get("answer")
        assert result["touched"] == []
        assert q["answer"]["status"] == "не разобран"
        assert "два носителя" in q["answer"]["note"]
    else:
        assert result["partial"] is True
        assert q["answer"]["status"] == m.PARTIAL_ANSWER_STATUS
        assert result["touched"] == [rule["id"], other["id"]]
        cell = rule["template_fit"]["params"][PARAM]
        assert cell["value"] == 600
        assert cell["source"] == provenance.answer_source(q, PARAM, "10 часов")
        assert PARAM not in other["template_fit"]["params"]
        assert m.reconcile_carriers(rule) == []
        assert answers._affected_rules(doc, q) == [other]
        first_after_write = copy.deepcopy(rule)
        completed = answers.apply_worker_patches(doc, _worker(q, [other]))
        assert completed["worker_rejected"] == [], completed
        assert completed["worker_applied"] == 1
        assert not m.is_open_question(q, doc=doc)
        assert rule == first_after_write
    assert doc["questions"][0] == original_question
    assert m.validate_schema(doc) == []
    if accepted:
        assert m.validate_model(doc) == []
    else:
        # Refusal preserves the edited cell, so the full model must ALSO
        # expose its now-unparsed answer reference, not certify it as ready.
        assert m.validate_model(doc) == [
            f"R-SECOND: параметр '{PARAM}' помечен ответом '{q['id']}' "
            "со статусом 'не разобран', а не применённым"]


def test_answer_cannot_heal_an_original_conflict_by_overwriting_evidence():
    doc, rule, q = _doc()
    rule["params"] = _legacy(600)
    rule["template_fit"]["params"][PARAM] = {"value": 540, "quote": "9 часов"}
    assert m.reconcile_carriers(rule)
    before = copy.deepcopy(rule)
    assert fp.apply_param_answer(doc, q, "10 часов", 2)["applied"] is False
    assert rule == before
    assert "два носителя" in q["answer"]["note"]


@pytest.mark.parametrize("bad", [{}, False, 0, "", [None], [{"value": 600}]])
def test_malformed_original_legacy_is_not_hidden_by_answer_write(bad):
    doc, rule, q = _doc()
    rule["params"] = bad
    before = copy.deepcopy(rule)
    assert fp.apply_param_answer(doc, q, "10 часов", 2)["applied"] is False
    assert rule == before


@pytest.mark.parametrize("legacy_value,accepted", [(600, True), (900, False)])
def test_reapplication_uses_the_same_consistency_boundary(legacy_value, accepted):
    doc, rule, q = _doc()
    q["answer"] = {"raw": "10 часов", "status": "применён", "revision": 2}
    rule["params"] = _legacy(legacy_value)
    coverage = fp.family_coverage(doc)
    before = copy.deepcopy(rule)
    notes = []
    touched = fp._reapply_values(doc, coverage, notes)
    assert bool(touched) is accepted
    if accepted:
        assert rule["template_fit"]["params"][PARAM]["value"] == 600
        assert PARAM in coverage[FAMILY]["given"]
    else:
        assert rule == before
        assert PARAM not in coverage[FAMILY]["given"]
        assert any("два носителя" in note for note in notes)


@pytest.mark.parametrize("legacy_value,accepted", [(600, 1), (900, 0)])
def test_complete_answer_handler_keeps_status_and_rules_honest(legacy_value, accepted):
    doc, rule, q = _doc()
    rule["params"] = _legacy(legacy_value)
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []
    before = copy.deepcopy(rule)
    result = answers.apply_answers(doc, {q["id"]: "10 часов"})
    assert result["applied"] == accepted
    assert result["unparsed"] == 1 - accepted
    if not accepted:
        assert rule == before
        assert m.is_open_question(q)
        assert "два носителя" in q["answer"]["note"]
    else:
        assert rule["template_fit"]["params"][PARAM]["value"] == 600
    assert m.validate_schema(doc) == []
    assert m.validate_model(doc) == []


@pytest.mark.parametrize("values", [(600, 900), (900, 600)])
def test_writer_checks_all_legacy_duplicates_without_creating_empty_cells(values):
    _, rule, _ = _doc()
    rule["template_fit"].pop("params")
    rule["params"] = [p for value in values for p in _legacy(value)]
    before = copy.deepcopy(rule)
    assert fp.write_cell(rule, PARAM, {"value": 600, "quote": "10 часов"})
    assert rule == before


@pytest.mark.parametrize("bad", [None, [], {}, {"value": 600},
                                 {"value": True, "quote": "да"},
                                 {"value": float("inf"), "quote": "бесконечно"}])
def test_writer_refuses_unreadable_cell_without_mutation(bad):
    _, rule, _ = _doc()
    before = copy.deepcopy(rule)
    assert fp.write_cell(rule, PARAM, bad)
    assert rule == before


def test_writer_owns_its_copy_and_preserves_sibling_cells_and_metadata():
    _, rule, q = _doc()
    before = copy.deepcopy(rule)
    cell = {"value": 600, "quote": "10 часов",
            "source": provenance.answer_source(q, PARAM, "10 часов")}
    expected = copy.deepcopy(cell)
    assert fp.write_cell(rule, PARAM, cell) is None
    cell["source"]["question"]["id"] = "changed later"
    assert rule["template_fit"]["params"][PARAM] == expected
    del rule["template_fit"]["params"][PARAM]
    assert rule == before


def test_writer_rejects_relation_conflict_even_with_equal_value():
    _, rule, _ = _doc()
    rule["params"] = _legacy(600)
    rule["params"][0]["relation"] = "max"
    before = copy.deepcopy(rule)
    reason = fp.write_cell(rule, PARAM, {"value": 600, "quote": "10 часов"})
    assert reason and "отношение" in reason
    assert rule == before


@pytest.mark.parametrize("raw", ["9 часов", "не применяется"])
@pytest.mark.parametrize("conflict", [False, True])
def test_revision_cannot_hide_conflict_by_dropping_a_second_owned_cell(raw, conflict):
    # Loaded/edited state, not a claim that the normal producer duplicates cells.
    doc, first, q = _doc()
    second = copy.deepcopy(first)
    second["id"] = "R-SECOND"
    doc["rules"].append(second)
    original_question = copy.deepcopy(q)
    q = question_editions.expand(doc, q, [second["id"]])
    assert doc["questions"][0] == original_question
    # A02: the deterministic family answer writes one carrier. The other
    # addressed row still needs its own result; applied counts WHOLE answers.
    first_stage = answers.apply_answers(doc, {q["id"]: "10 часов"})
    assert first_stage["applied"] == 0
    assert q["answer"]["status"] == m.PARTIAL_ANSWER_STATUS
    assert answers._affected_rules(doc, q) == [second]
    worker = answers.apply_worker_patches(doc, _worker(q, [second]))
    assert worker["worker_rejected"] == [], worker
    assert worker["worker_applied"] == 1 and not m.is_open_question(q, doc=doc)
    second["template_fit"]["params"][PARAM] = copy.deepcopy(
        first["template_fit"]["params"][PARAM])
    if conflict:
        second["params"] = _legacy(900)
    assert m.validate_schema(doc) == [] and m.validate_model(doc) == []
    opened = answers.open_revision(doc, q["id"])
    revision = next(x for x in doc["questions"] if x["id"] == opened["created"])
    before = copy.deepcopy(doc["rules"])
    assert bool(m.reconcile_carriers(second)) is conflict
    result = answers.apply_answers(doc, {revision["id"]: raw})
    if not conflict and raw == "9 часов":
        assert result["applied"] == 0
        assert revision["answer"]["status"] == m.PARTIAL_ANSWER_STATUS
        assert answers._affected_rules(doc, revision) == [second]
        first_after_value = copy.deepcopy(first)
        completed = answers.apply_worker_patches(doc, _worker(revision, [second]))
        assert completed["worker_rejected"] == [], completed
        assert completed["worker_applied"] == 1
        assert not m.is_open_question(revision, doc=doc)
        assert first == first_after_value
    else:
        assert result["applied"] == (0 if conflict else 1)
    if conflict:
        assert doc["rules"] == before
        assert m.reconcile_carriers(second)
        assert revision["answer"]["status"] == "не разобран"
        assert "R-SECOND" in revision["answer"]["note"]
        assert "два носителя" in revision["answer"]["note"]
    else:
        assert PARAM not in second["template_fit"]["params"]
        if raw == "9 часов":
            assert first["template_fit"]["params"][PARAM]["value"] == 540
