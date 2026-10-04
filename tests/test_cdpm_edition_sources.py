"""P0 §12.4: normative sources change identity; technical traces do not."""
import copy

import pytest

from cdpm import answers, exchange, model as m, question_editions as qe
from test_cdpm_question_editions import _doc, _filled

EXTRA = {"sid": "S1", "anchor": "П.2.5"}


def test_added_normative_source_invalidates_old_book(tmp_path):
    doc, q = _doc()
    qe.freeze(q)
    book = _filled(tmp_path, doc, q)
    q["source_refs"].append(dict(EXTRA))
    reading = answers.read_book(book, doc)
    assert q["id"] in reading.refused
    assert not reading.answers
    assert any("источник" in p for p in reading.problems)


def test_normative_patch_reissues_even_without_scope_growth():
    doc, q = _doc()
    qe.freeze(q)
    old = copy.deepcopy(q)
    assert exchange._apply_question_patch(doc, {
        "question_id": q["id"], "add_source_refs": [EXTRA]}, None) == []
    new = next((x for x in doc["questions"] if x.get("edition_of") == q["id"]), None)
    assert new is not None
    assert q == old
    assert new["edition"]["targets"] == old["edition"]["targets"]
    assert EXTRA in new["edition"]["sources"]
    assert qe.fingerprint(q) != qe.fingerprint(new)
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_technical_patch_keeps_normative_identity_and_has_own_trace(tmp_path):
    doc, q = _doc()
    qe.freeze(q)
    before = copy.deepcopy(doc)
    book = _filled(tmp_path, doc, q)
    patch = {"question_id": q["id"], "add_source_refs_technical": [EXTRA]}
    assert exchange._apply_question_patch(doc, patch, None) == []
    assert q["edition"] == before["questions"][-1]["edition"]
    assert EXTRA not in q["source_refs"]
    assert q["source_refs_technical"] == [EXTRA]
    assert len(doc["questions"]) == len(before["questions"])
    assert answers.read_book(book, doc).answers == {q["id"]: "600 минут"}
    reply = {"remark": "A-src", "verdict": "ПРИНЯТО", "question_patch": patch}
    assert exchange.executed_by_trace(before, doc, [reply]) == {"A-src"}
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_unrelated_source_in_successor_is_not_execution_of_requested_source():
    doc, q = _doc()
    qe.freeze(q)
    before = copy.deepcopy(doc)
    rid = doc["rules"][0]["id"]
    qe.expand(doc, q, [rid], content={"source_refs": q["source_refs"] + [
        {"sid": "S1", "anchor": "П.3.1"}]})
    reply = {"remark": "A-src", "verdict": "ПРИНЯТО", "question_patch": {
        "question_id": q["id"], "add_rule_refs": [rid], "add_source_refs": [EXTRA]}}
    assert exchange.executed_by_trace(before, doc, [reply]) == set()


def test_technical_label_does_not_hide_normative_mutation():
    doc, q = _doc()
    qe.freeze(q)
    q["source_refs_technical"] = [EXTRA]
    assert qe.problems(q) == []
    q["source_refs"].append(EXTRA)
    assert qe.problems(q)


def test_technical_change_cannot_satisfy_a_normative_patch_trace():
    doc, q = _doc()
    qe.freeze(q)
    before = copy.deepcopy(doc)
    q["source_refs_technical"] = [EXTRA]
    reply = {"remark": "A-src", "verdict": "ПРИНЯТО", "question_patch": {
        "question_id": q["id"], "add_source_refs": [EXTRA]}}
    assert exchange.executed_by_trace(before, doc, [reply]) == set()


@pytest.mark.parametrize("bad_field", ["add_source_refs", "add_source_refs_technical"])
def test_malformed_source_cannot_disappear_from_mixed_patch_trace(bad_field):
    doc, q = _doc()
    qe.freeze(q)
    before = copy.deepcopy(doc)
    good_field = "add_source_refs_technical" if bad_field == "add_source_refs" \
        else "add_source_refs"
    good = {"question_id": q["id"], good_field: [EXTRA]}
    assert exchange._apply_question_patch(doc, good, None) == []
    # Replaying an edited receipt must not erase the malformed half and
    # claim that the whole mixed request was executed by the valid half.
    reply = {"remark": "A-mixed", "verdict": "ПРИНЯТО",
             "question_patch": dict(good, **{bad_field: ["not-a-source"]})}
    assert exchange.executed_by_trace(before, doc, [reply]) == set()


@pytest.mark.parametrize("field", ["add_source_refs", "add_source_refs_technical"])
def test_source_patch_anchor_refusal_is_atomic(field):
    doc, q = _doc()
    qe.freeze(q)
    before = copy.deepcopy(doc)
    result = exchange._apply_question_patch(doc, {
        "question_id": q["id"], field: [EXTRA]}, set())
    assert result and any("заморозки" in p for p in result)
    assert doc == before


def test_normative_reissue_is_executed_through_real_patch_pipeline():
    doc, q = _doc()
    qe.freeze(q)
    reply = {"remark": "A-src", "verdict": "ПРИНЯТО", "basis": "источник",
             "question_patch": {"question_id": q["id"], "add_source_refs": [EXTRA]}}
    pack = {"round": 1, "remarks": [{"id": "A-src", "target": {"question": q["id"]},
                                   "defect_class": "потерянная трассировка"}]}
    executed = set()
    new, accepted, rejected, _ = exchange._apply_patches(doc, pack,
        {"round": 1, "answers": [reply]}, 1, executed_out=executed)
    assert rejected == []
    assert executed == {"A-src"}
    assert any(x.get("edition_of") == q["id"] for x in new["questions"])
    assert exchange.check_diff_only(doc, new, accepted) == []


@pytest.mark.parametrize("writer", ["family", "history"])
def test_code_built_question_reissues_for_new_normative_clause(writer):
    from cdpm import family_params as fp, history_questions as hq
    from test_cdpm_family_params import _doc_with_home_floor, QID
    from test_cdpm_history_editions import _published
    from test_cdpm_history_1_17_2 import _book
    if writer == "family":
        doc, carrier = _doc_with_home_floor()
        fp.ensure_family_param_questions(doc)
        q = next(x for x in doc["questions"] if x["id"] == QID)
        qe.freeze(q)
        run = lambda: fp.ensure_family_param_questions(doc)
    else:
        doc, q = _published()
        carrier = next(r for r in doc["rules"] if r["id"] == "R-H-REST")
        run = lambda: hq.ensure_history_questions(doc, _book())
    old = copy.deepcopy(q)
    carrier["sources"].append(EXTRA)
    assert run() == [q["id"] + "-E2"]
    new = next(x for x in doc["questions"] if x.get("edition_of") == q["id"])
    assert q == old
    assert EXTRA in new["edition"]["sources"]
    assert qe.fingerprint(new) != qe.fingerprint(q)
    assert run() == []
