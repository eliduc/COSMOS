"""Closed-list choice effects: exact values, immutable scope and feedback."""
import copy
import json

import pytest

from cdpm import answers, model as m, question_editions as qe
from test_cdpm_data_scope_editions import initial, report


def exercise(statement, *, published, values=("AAA",)):
    doc, q, root = initial()
    m.ensure_data_scope_questions(doc, report(*values))
    if published:
        qe.freeze(q)
    answers.apply_answers(doc, {root: "а"})
    before = copy.deepcopy(doc["rules"])
    rule = copy.deepcopy(doc["rules"][0])
    rule.update(statement=statement)
    rule.pop("blockers", None)
    result = answers.apply_worker_patches(doc, lambda _: json.dumps({
        "patches": [{"question": root, "rules": [rule]}]}))
    return doc, q, root, before, result


@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize("statement,values", [
    ("База ∈ {OLD, ZZZ}", ("AAA",)),
    ("База ∈ {AAA}", ("AAA",)),
    ("База ∈ {OLD, AAA}", ("AAA", "BBB")),
])
def test_wrong_value_shrinkage_and_partial_extension_are_not_applied(published, statement, values):
    doc, q, root, before, result = exercise(statement, published=published, values=values)
    assert result["worker_applied"] == 0
    assert doc["rules"] == before
    assert q["answer"]["status"] == "ожидает обработчика"
    assert q["answer"].get("processing_note")


@pytest.mark.parametrize("published", [False, True])
@pytest.mark.parametrize("statement,values", [
    ("База ∈ {OLD, AAA}", ("AAA",)),
    ("База ∈ {OLD, AAA, BBB}", ("AAA", "BBB")),
])
def test_all_requested_values_with_old_values_preserved_are_applied(published, statement, values):
    doc, q, root, before, result = exercise(statement, published=published, values=values)
    assert result["worker_applied"] == 1
    assert q["answer"]["status"] == "применён"
    assert not doc["rules"][0].get("blockers")


@pytest.mark.parametrize("published", [False, True])
def test_actual_report_rerun_reissues_changed_statement(published):
    # Measures the claimed loss of re-asking separately from wrong acceptance.
    # The auditor's supplied probe does not rerun ensure_data_scope_questions.
    doc, q, root, before, result = exercise("База ∈ {OLD, ZZZ}", published=published)
    if result["worker_applied"]:
        old_answer = copy.deepcopy(q["answer"])
        assert m.ensure_data_scope_questions(doc, report("AAA")) == [root + "-E2"]
        assert q["answer"] == old_answer
        assert "answer" not in qe.current_for_root(doc, root)
    else:
        assert q["answer"]["status"] == "ожидает обработчика"


@pytest.mark.parametrize("extended", [False, True])
def test_valid_fit_update_is_not_itself_an_extension(extended):
    from test_cdpm_data_scope_residuals import patch
    doc, q, root = initial()
    doc["rules"][0]["kind"] = "HARD"  # DATA deliberately drops an inapplicable fit.
    qe.freeze(q)
    answers.apply_answers(doc, {root: "а"})
    fit = {"category": "C", "registry_rev": "rev-1", "gap": "Нужна новая семья"}
    result = patch(doc, q, template_fit=fit,
                   statement="База ∈ {OLD, AAA}" if extended else "База ∈ {OLD}")
    assert result["worker_applied"] == int(extended)
    if extended:
        assert doc["rules"][0]["template_fit"] == fit
    else:
        assert "перечень не расширен" in q["answer"]["processing_note"]


@pytest.mark.parametrize("candidate", ["В777", "B\u200b777", "B\ufeff777"])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("surface", ["md", "xlsx"])
def test_confusable_code_is_refused_and_both_sides_explained(tmp_path, candidate, reverse, surface):
    from test_cdpm_edition_scope_display import shown
    from unicode_data import code_points
    wanted, proposed = (candidate, "B777") if reverse else ("B777", candidate)
    doc, q, root, before, result = exercise(f"База ∈ {{OLD, {proposed}}}",
                                           published=True, values=(wanted,))
    assert result["worker_applied"] == 0 and doc["rules"] == before
    text = shown(doc, q, surface, tmp_path)
    assert code_points(wanted) in text and code_points(proposed) in text
    assert "строки различаются" in text


@pytest.mark.parametrize("statement", [
    "База ∈ {OLD, AAA, ZZZ}", "База ∉ {OLD, AAA}",
    "База ∈ {OLD}; примечание: AAA", "База ∈ {OLD, AAA}; кроме AAA",
])
def test_extra_values_and_context_rewrites_are_not_authorized(statement):
    doc, q, root, before, result = exercise(statement, published=True)
    assert result["worker_applied"] == 0 and doc["rules"] == before


@pytest.mark.parametrize("wanted", ["Airbus A320", "New York", "A,B", "A;B", " A320 "])
def test_one_value_is_not_split_or_trimmed(wanted):
    statement = "База ∈ {OLD, " + json.dumps(wanted, ensure_ascii=False) + "}"
    doc, q, root, before, result = exercise(statement, published=True, values=(wanted,))
    assert result["worker_applied"] == 1
    assert doc["rules"][0]["statement"] == statement


@pytest.mark.parametrize("prefix", ["Разрешены типы ", "Допустимые базы: "])
@pytest.mark.parametrize("valid", [False, True])
def test_explicit_prose_list_keeps_old_values(prefix, valid):
    from test_cdpm_data_scope_residuals import patch
    doc, q, root = initial()
    doc["rules"][0]["statement"] = prefix + "A320, A321"
    m.ensure_data_scope_questions(doc, report("B777", "E190"))
    qe.freeze(q)
    answers.apply_answers(doc, {root: "а"})
    text = prefix + ("A320, A321, B777, E190" if valid else "A320, B777, E190")
    result = patch(doc, q, statement=text)
    assert result["worker_applied"] == int(valid)


def test_current_rule_change_does_not_retarget_an_old_answer():
    from test_cdpm_data_scope_residuals import patch
    doc, q, root = initial()
    qe.freeze(q)
    answers.apply_answers(doc, {root: "а"})
    doc["rules"][0]["statement"] = "База ∈ {OLD, BBB}"
    before = copy.deepcopy(doc["rules"])
    result = patch(doc, q, statement="База ∈ {OLD, BBB, AAA}")
    assert result["worker_applied"] == 0 and doc["rules"] == before


@pytest.mark.parametrize("mutation", ["values", "statement", "remove", "wording", "rule"])
def test_issued_choice_evidence_cannot_be_changed(mutation):
    doc, q, root = initial()
    qe.freeze(q)
    if mutation == "remove":
        q.pop("data_scope")
    elif mutation == "wording":
        q["answer_format"]["options"][0]["text"] = "добавить ZZZ в перечень"
    elif mutation == "rule":
        q["data_scope"]["rule"] = "R-other"
    else:
        q["data_scope"][mutation] = ["aaa"] if mutation == "values" else "База ∈ {old}"
        from cdpm.data_scope import wording
        q.update(wording(q["data_scope"]))
    assert qe.problems(q)


@pytest.mark.parametrize("legacy", [False, True])
def test_case_only_change_gets_distinct_edition_and_no_v1_alias(legacy):
    doc, q, root = initial()
    qe.freeze(q, legacy=legacy)
    before = copy.deepcopy(q)
    assert "fingerprint_v1" not in q["edition"]
    assert m.ensure_data_scope_questions(doc, report("aaa")) == [root + "-E2"]
    new = qe.current_for_root(doc, root)
    assert q == before and new["edition"]["fingerprint"] != q["edition"]["fingerprint"]
    assert m.validate_schema(doc) + m.validate_model(doc) == []


def test_legacy_choice_is_reissued_not_reinterpreted():
    from test_cdpm_data_scope_residuals import patch
    doc, q, root = initial()
    q.pop("data_scope")
    qe.freeze(q, legacy=True)
    old_fp = qe.fingerprint(q)
    answers.apply_answers(doc, {root: "а"})
    assert patch(doc, q, statement="База ∈ {OLD, AAA}")["worker_applied"] == 0
    before = copy.deepcopy(q)
    assert m.ensure_data_scope_questions(doc, report("AAA")) == [root + "-E2"]
    assert q == before and qe.fingerprint(q) == old_fp
    assert "answer" not in qe.current_for_root(doc, root)


@pytest.mark.parametrize("value", ["Airbus A320", "A,B", "A»B", "B\u200b777", "В777"])
def test_actual_report_preserves_one_exact_value_through_question(value):
    from cdpm import data_checks as dc, data_report as dr
    from test_cdpm_data_checks import _dataset, _spec
    from unicode_data import code_points
    ds = _dataset()
    ds["tables"]["D1"]["Ростер"]["rows"] = [{"Табельный": value}]
    spec = _spec("перечень-значений", {"sheet": "D1/Ростер", "column": "Табельный",
                                     "allowed": ["B777"]}, {"rule": "R-D"})
    actual = dc.build_data_report("Т", ds, [spec])
    assert dr.validate_report(actual) == []
    finding = actual["findings"][0]
    assert finding["scope_value"] == value
    if value in ("B\u200b777", "В777"):
        assert code_points(value) in finding["text"] and code_points("B777") in finding["text"]
    doc, q, root = initial()
    m.ensure_data_scope_questions(doc, actual)
    assert q["data_scope"]["values"] == [value]
    assert m.validate_schema(doc) + m.validate_model(doc) == []


@pytest.mark.parametrize("new_values", [["OLD"], ["OLD", "AAA"], ["AAA"], None])
def test_machine_list_cannot_be_stale_or_removed(new_values):
    from cdpm.data_scope import extension_problems
    doc, q, root = initial()
    old = copy.deepcopy(doc["rules"][0])
    old["logic"] = {"conditions": [{"allowed": ["OLD"]}]}
    new = copy.deepcopy(old)
    new["statement"] = "База ∈ {OLD, AAA}"
    if new_values is None:
        new.pop("logic")
    else:
        new["logic"]["conditions"][0]["allowed"] = new_values
    issues = extension_problems(q, {old["id"]: old}, {new["id"]: new})
    assert bool(issues) == (new_values != ["OLD", "AAA"])


def test_semantic_guard_mutation_reopens_the_original_bug(monkeypatch):
    import cdpm.data_scope as scope
    monkeypatch.setattr(scope, "extension_problems", lambda *args: [])
    doc, q, root, before, result = exercise("База ∈ {OLD, ZZZ}", published=True)
    assert result["worker_applied"] == 1  # Counterexample reaches THIS guard.


@pytest.mark.parametrize("carrier", ["params", "template_fit", "codes", "formal"])
@pytest.mark.parametrize("updated", [False, True])
def test_every_list_carrier_must_follow_the_same_extension(monkeypatch, carrier, updated):
    from cdpm.data_scope import extension_problems
    # Unit-level proof of carrier traversal; executable patch contract is
    # separately exercised above, including a real valid category-C update.
    monkeypatch.setattr(m, "fit_param_unit", lambda family, name: "code_list")
    doc, q, root = initial()
    old = copy.deepcopy(doc["rules"][0])
    if carrier == "params":
        old[carrier] = [{"unit": "code_list", "value": "OLD"}]
    elif carrier == "template_fit":
        old[carrier] = {"family": "f", "params": {"p": {"value": "OLD"}}}
    elif carrier == "codes":
        old[carrier] = [{"code": "OLD"}]
    else:
        old[carrier] = "База ∈ {OLD}"
    new = copy.deepcopy(old)
    new["statement"] = "База ∈ {OLD, AAA}"
    if updated:
        if carrier == "params":
            new[carrier][0]["value"] = "AAA, OLD"
        elif carrier == "template_fit":
            new[carrier]["params"]["p"]["value"] = "AAA, OLD"
        elif carrier == "codes":
            new[carrier].append({"code": "AAA"})
        else:
            new[carrier] = new["statement"]
    assert bool(extension_problems(q, {old["id"]: old}, {new["id"]: new})) != updated


def test_difference_beyond_display_prefix_is_named():
    wanted, proposed = "A" * 100 + "B777", "A" * 100 + "В777"
    doc, q, root, before, result = exercise(f"База ∈ {{OLD, {proposed}}}",
                                           published=True, values=(wanted,))
    assert result["worker_applied"] == 0 and doc["rules"] == before
    reason = q["answer"]["processing_note"]
    assert "позиция 101" in reason and "U+0042" in reason and "U+0412" in reason
