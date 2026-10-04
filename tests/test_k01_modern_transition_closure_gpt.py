"""Remaining K01 transition components, not native/executor acceptance.

Actual validated modern catalogue pairs and actual completeness, question,
answer, manifest-projection and readiness readers. No support approval,
consistency report, exporter, solver, server or network is created/invoked.
Existing param_carried_scope tests cover unit/relation; only the missing
parameter-removal reader path is added here.
"""
import copy
import json

import pytest

from cdpm import answers, catalog_bridge as cb, family_manifest
from cdpm import family_params as fp, model as m, parameter_contract
from cdpm import export_task_package as producer
from test_k01_semantic_transition import pair, clean_bridge  # noqa: F401
from test_k01_carried_conditions import FAMILY as EXTENSION
from test_k01_param_set_reference import expanded, pinned_doc


CAP = "duty_extension_cap"
THRESHOLD = "long_duty_threshold"
DAYS = "days_off_floor"
WINDOW = "days_off_window_days"


def select(cat, monkeypatch):
    monkeypatch.setenv(cb.ENV_CATALOG, str(cat.path))
    cb.forget()


def k01(doc):
    return [p for p in m.readiness_barriers(doc)
            if p.get("code") == fp.FAMILY_INCOMPLETE]


@pytest.mark.parametrize("raw,declined", [("Не применяется", True), ("28", False)],
                         ids=["unconditional-decline", "healthy-value"])
def test_modern_expansion_closes_by_decline_or_value_without_rebinding(
        tmp_path, monkeypatch, raw, declined):
    _seed, old, new = pair(tmp_path, monkeypatch, expanded)
    doc = pinned_doc(old, new, monkeypatch)
    fit = doc["rules"][-1]["template_fit"]
    original_cells = copy.deepcopy(fit["params"])
    pin = copy.deepcopy(fit["family_contract"])
    before = fp.family_completeness(doc)[DAYS]
    assert before["missing"] == [WINDOW] and before["curable"]
    # The fixture also introduces an easier alternative: the aggregate
    # family shape remains compatible, but the SELECTED monthly pin expands.
    assert before["contract_state"] == fp.CONTRACT_COMPATIBLE
    reference, = before["contract"]["references"]
    assert reference["change"] == "expanded"
    assert reference["origin"]["param_set"] == "monthly"
    assert reference["old_members"] == ["min_days_off"]
    assert set(reference["new_members"]) == {"min_days_off", WINDOW}
    created = fp.ensure_family_param_questions(doc)
    assert len(created) == 1
    question = next(q for q in doc["questions"] if q["id"] == created[0])
    assert fp.question_target(question) == (DAYS, WINDOW)
    assert k01(doc)
    result = answers.apply_answers(doc, {created[0]: raw})
    assert result["applied"] == 1, (result, question.get("answer"))
    after = fp.family_completeness(doc)[DAYS]
    assert after["missing"] == [] and after["verdict"] == fp.VERDICT_COMPLETE
    assert after["declined"] == ([WINDOW] if declined else [])
    assert fit["family_contract"] == pin
    assert {key: fit["params"][key] for key in original_cells} == original_cells
    if declined:
        assert WINDOW not in fit["params"]
    else:
        assert fit["params"][WINDOW]["value"] == 28
        assert fit["params"][WINDOW]["source"]["kind"] == "answer"
    projected = family_manifest.project(doc)[DAYS]
    assert projected["missing"] == [] and projected["declined"] == after["declined"]
    assert not k01(doc)
    # No assertion that unreleased K06 support became healthy.


def test_two_incomplete_families_after_transition_keep_independent_obligations(
        tmp_path, monkeypatch):
    seed, old, new = pair(tmp_path, monkeypatch, expanded)
    extension = copy.deepcopy(seed["rules"][-1])
    extension["template_fit"]["params"].pop(THRESHOLD)
    doc = pinned_doc(old, new, monkeypatch)
    days = copy.deepcopy(doc["rules"][-1])
    doc["rules"], doc["questions"], doc["objectives"] = [days, extension], [], []
    originals = copy.deepcopy(doc["rules"])
    initial = fp.family_completeness(doc)
    assert initial[DAYS]["missing"] == [WINDOW]
    assert initial[EXTENSION]["missing"] == [THRESHOLD]
    assert {p["family"] for p in k01(doc)} == {DAYS, EXTENSION}
    created = fp.ensure_family_param_questions(doc)
    assert len(created) == 2
    questions = {fp.question_target(q): q for q in doc["questions"]}
    assert set(questions) == {(DAYS, WINDOW), (EXTENSION, THRESHOLD)}
    for rule, original, target in zip(doc["rules"], originals,
                                    ((DAYS, WINDOW), (EXTENSION, THRESHOLD))):
        assert "blockers" not in original
        assert rule["blockers"] == [questions[target]["id"]]
        assert {key: value for key, value in rule.items()
                if key != "blockers"} == original
    extension_while_question_open = copy.deepcopy(extension)
    first = questions[(DAYS, WINDOW)]
    assert answers.apply_answers(doc, {first["id"]: "28"})["applied"] == 1
    intermediate = fp.family_completeness(doc)
    assert intermediate[DAYS]["verdict"] == fp.VERDICT_COMPLETE
    assert intermediate[EXTENSION]["missing"] == [THRESHOLD]
    assert extension == extension_while_question_open
    assert {p["family"] for p in k01(doc)} == {EXTENSION}
    second = questions[(EXTENSION, THRESHOLD)]
    assert answers.apply_answers(doc, {second["id"]: "720 минут"})["applied"] == 1
    final = fp.family_completeness(doc)
    assert all(final[family]["verdict"] == fp.VERDICT_COMPLETE
               for family in (DAYS, EXTENSION))
    assert days["template_fit"]["params"][WINDOW]["value"] == 28
    assert extension["template_fit"]["params"][THRESHOLD]["value"] == 720
    assert not k01(doc)


def remove_cap(docs):
    record = next(r for r in docs["families.json"]["families"] if r["id"] == EXTENSION)
    record["params"] = [p for p in record["params"] if p["name"] != CAP]


@pytest.mark.parametrize("failure", ["removed-parameter", "unavailable-old-release"])
def test_old_question_early_refusal_preserves_cells_and_canonical_source(
        tmp_path, monkeypatch, failure):
    edit = remove_cap if failure == "removed-parameter" else (
        lambda docs: next(r for r in docs["families.json"]["families"]
                          if r["id"] == EXTENSION).update(note="Unrelated current note"))
    doc, old, new = pair(tmp_path, monkeypatch, edit)
    select(old, monkeypatch)
    doc["rules"] = [doc["rules"][-1]]
    doc["questions"], doc["objectives"] = [], []
    doc["rules"][0]["template_fit"]["params"].pop(CAP)
    qid, = fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    assert q["parameter_contract"]["registry_rev"] == old.fingerprint()
    healthy = copy.deepcopy(doc)
    hq = next(q for q in healthy["questions"] if q["id"] == qid)
    assert fp.apply_param_answer(healthy, hq, "960 минут", 2)["applied"], hq.get("answer")
    assert fp.family_completeness(healthy)[EXTENSION]["verdict"] == fp.VERDICT_COMPLETE
    source = tmp_path / "canonical" / "model.json"
    source.parent.mkdir()
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    source.write_bytes(raw)
    original_rules = copy.deepcopy(doc["rules"])
    original_edition = copy.deepcopy(q["edition"])
    if failure == "unavailable-old-release":
        release = new.path / "releases" / old.version()
        destination = release.with_name("unavailable-component-input")
        assert release.resolve().is_relative_to(tmp_path.resolve())
        assert destination.resolve().is_relative_to(tmp_path.resolve())
        release.rename(destination)
    select(new, monkeypatch)
    # Explicitly diagnose the actual contract before the answer writer.
    contract_reason = parameter_contract.application_problem(q, EXTENSION, CAP)
    assert contract_reason and "требуется новое издание" in contract_reason
    if failure == "removed-parameter":
        assert CAP in contract_reason and "убрано" in contract_reason
    else:
        assert "не доказана" in contract_reason
    result = fp.apply_param_answer(doc, q, "960 минут", 2)
    assert result == {"applied": False, "touched": []}
    assert q["answer"]["status"] == "не разобран" and q["answer"]["note"]
    assert doc["rules"] == original_rules and q["edition"] == original_edition
    assert source.read_bytes() == raw
    # This is the pre-write component boundary, NOT a simulated successful
    # _sync_canon call: refused answers never authorize writing this source.


def test_carried_parameter_removal_reaches_actual_family_readers(
        tmp_path, monkeypatch):
    doc, old, new = pair(tmp_path, monkeypatch, remove_cap)
    select(old, monkeypatch)
    healthy = fp.family_completeness(doc)[EXTENSION]
    assert healthy["verdict"] == fp.VERDICT_COMPLETE
    assert not [p for p in k01(doc) if p["family"] == EXTENSION]
    select(new, monkeypatch)
    before = copy.deepcopy(doc)
    state = fp.family_completeness(doc)[EXTENSION]
    assert state["contract_state"] == fp.CONTRACT_INCOMPATIBLE
    assert state["verdict"] == fp.VERDICT_RELAND and not state["curable"]
    assert state["missing"] == []
    assert any(CAP in text and "из действующей записи убрано" in text
               for text in state["contract"]["differences"])
    assert any(CAP in text and "перепосадка" in text for text in m.validate_model(doc))
    barriers = [p for p in k01(doc) if p["family"] == EXTENSION]
    assert barriers and all(p["proven"] for p in barriers)
    matrix = m.readiness_matrix(doc)
    assert not matrix["pairing"]["optimization"]["ready"]
    assert "feasibility-soft" not in producer.allowed_modes(doc)[0]["pairing"]
    assert fp.ensure_family_param_questions(doc) == []
    assert family_manifest.project(doc)[EXTENSION]["verdict"] == fp.VERDICT_RELAND
    assert doc == before
