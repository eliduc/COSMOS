"""Technical input failure is not a business interpretation for a customer.

Direct/legacy retell checks use in-memory documents and deterministic fake
verifiers. Invalid logic stays invalid; no operational model or service runs.
"""
import copy
import json

import pytest

from cdpm import logic_proof as lp, model as m
from test_cdpm_logic_proof import _rule_duration, _rule_count


def _doc(*rules):
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
        "rules": list(rules), "questions": [], "objectives": [],
    }


def _invalid(kind):
    rule = _rule_duration()
    if kind == "unit":
        rule["params"][0].update(unit="time_of_day_minutes", tz="UTC")
    elif kind == "reference":
        rule["logic"]["demand"]["param"] = "absent_param"
        rule["logic"]["source_params"] = ["absent_param"]
    elif kind == "structure":
        rule["logic"]["condition"] = "not a list"
    return rule


@pytest.mark.parametrize("kind,reason", [
    ("unit", "time_of_day_minutes"),
    ("reference", "absent_param"), ("structure", "condition не список"),
])
@pytest.mark.parametrize("verdict", [True, False, None])
def test_invalid_input_is_not_sent_to_verifier_or_offered_as_a_choice(kind, reason, verdict):
    doc = _doc(_invalid(kind))
    before = copy.deepcopy(doc)
    calls = []

    def verifier(prompt):
        calls.append(prompt)
        return json.dumps({"verdicts": [] if verdict is None else [
            {"rule": "R-D", "equivalent": verdict}]})

    summary = lp.retell_check(doc, verifier)
    assert calls == []
    assert summary["checked"] == summary["equivalent"] == 0
    assert summary["blocked"] == []  # no new business question
    assert summary["unreadable"][0]["rule"] == "R-D"
    assert reason in "; ".join(summary["unreadable"][0]["problems"])
    assert doc == before
    assert any(reason in p for p in m.validate_model(doc))


@pytest.mark.parametrize("equivalent", [True, False, None])
def test_mixed_batch_checks_only_readable_rule_and_keeps_normal_business_question(equivalent):
    bad = _invalid("unit")
    good = _rule_count()
    before_bad = copy.deepcopy(bad)
    doc = _doc(bad, good)
    calls = []
    rendered = m.render_logic(good["logic"], good)

    def verifier(prompt):
        calls.append(prompt)
        # Even an unsolicited positive verdict cannot approve invalid input.
        rows = [{"rule": "R-D", "equivalent": True}]
        if equivalent is not None:
            rows.append({"rule": "R-C", "equivalent": equivalent})
        return json.dumps({"verdicts": rows})

    summary = lp.retell_check(doc, verifier)
    assert len(calls) == 1 and "R-C\nСВОД:" in calls[0]
    assert "R-D\nСВОД:" not in calls[0]
    assert "Логика не может быть пересказана" not in calls[0]
    assert summary["checked"] == 1
    assert summary["equivalent"] == (1 if equivalent is True else 0)
    assert summary["unreadable"][0]["rule"] == "R-D"
    assert bad == before_bad
    if equivalent is True:
        assert doc["questions"] == [] and summary["blocked"] == []
    else:
        assert summary["blocked"] == ["R-C"]
        question, = doc["questions"]
        assert question["rule_refs"] == ["R-C"]
        assert question["answer_format"]["options"][1]["text"] == (
            "машинный пересказ: " + rendered[:180])
        assert good["blockers"] == [question["id"]]


def test_valid_render_is_the_same_snapshot_in_prompt_and_question(monkeypatch):
    doc = _doc(_rule_duration())
    calls = []

    def render(*_):
        calls.append(True)
        return "первый пересказ" if len(calls) == 1 else "другой пересказ"

    monkeypatch.setattr(m, "render_logic", render)
    prompts = []

    def verifier(prompt):
        prompts.append(prompt)
        return json.dumps({"verdicts": []})

    lp.retell_check(doc, verifier)
    assert calls == [True]
    assert "первый пересказ" in prompts[0]
    assert doc["questions"][0]["answer_format"]["options"][1]["text"] == (
        "машинный пересказ: первый пересказ")


def test_repeated_invalid_input_is_stable_and_can_be_repaired_without_a_fake_answer():
    rule = _invalid("unit")
    doc = _doc(rule)
    calls = []

    def verifier(prompt):
        calls.append(prompt)
        return json.dumps({"verdicts": [{"rule": "R-D", "equivalent": True}]})

    first = lp.retell_check(doc, verifier)
    assert first == lp.retell_check(doc, verifier)
    assert calls == [] and doc["questions"] == []
    rule["params"][0]["unit"] = "duration_minutes"
    rule["params"][0].pop("tz")
    fixed = lp.retell_check(doc, verifier)
    assert fixed == {"checked": 1, "equivalent": 1, "blocked": [], "adjudicated": []}
    assert len(calls) == 1 and m.validate_model(doc) == []


def test_existing_blocked_or_human_adjudicated_rules_are_not_reasked():
    rule = _rule_duration()
    qid = m.stable_question_id("2", "лог-R-D")
    doc = _doc(rule)
    doc["questions"] = [{"id": qid, "rule_refs": ["R-D"],
                         "answer": {"status": "применён", "raw": "а"}}]

    def forbidden(_):
        pytest.fail("human-adjudicated or blocked rule was sent again")

    assert lp.retell_check(doc, forbidden) == {
        "checked": 0, "equivalent": 0, "blocked": [], "adjudicated": ["R-D"]}
    doc["questions"][0].pop("answer")
    rule["blockers"] = [qid]
    assert lp.retell_check(doc, forbidden) == {
        "checked": 0, "equivalent": 0, "blocked": [], "adjudicated": []}


def test_shared_validation_is_reached_before_render_and_verifier(monkeypatch):
    rule = _rule_duration()
    doc = _doc(rule)
    reached = []

    def refuse(logic, candidate):
        assert logic is rule["logic"] and candidate is rule
        reached.append(True)
        return ["injected shared validation refusal"]

    def forbidden(*_):
        pytest.fail("refused logic reached rendering or verifier")

    monkeypatch.setattr(m, "validate_logic", refuse)
    monkeypatch.setattr(m, "render_logic", forbidden)
    summary = lp.retell_check(doc, forbidden)
    assert reached == [True]
    assert summary["unreadable"] == [{
        "rule": "R-D", "problems": ["injected shared validation refusal"]}]
    assert doc["questions"] == []


def test_retell_refusal_does_not_remove_the_package_barrier(tmp_path):
    from cdpm import export_task_package as tp
    from test_cdpm_a05_carriers_barrier import _doc_with, _run_dir

    rule = _invalid("unit")
    doc = _doc_with(rule)
    before = copy.deepcopy(doc)
    summary = lp.retell_check(doc, lambda _: json.dumps({"verdicts": []}))
    assert summary["unreadable"][0]["rule"] == rule["id"]
    assert doc == before
    with pytest.raises(tp.TaskPackageError) as error:
        tp.build_package(_run_dir(tmp_path, doc, "retell-invalid"))
    assert "time_of_day_minutes" in str(error.value)
    assert "duration_minutes" in str(error.value)
