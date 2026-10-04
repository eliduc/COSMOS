"""Human choices and stored evidence keep their distinguishing suffixes."""
import copy
import json

import pytest

from cdpm import answers, exchange as ex, family_params, logic_proof, model as m
from cdpm.sample import sample_model
from test_cdpm_exchange import _pack, _remark
from test_cdpm_exchange_run import workspace  # isolated fake-LLM/tmp-path fixture
from test_cdpm_logic_proof import _rule_duration


def _text(long, ending="после трёх ночных смен отдых составляет 24 часа"):
    return ("Условия выполнения рейсов и предоставления отдыха; " * 6 if long else "") + ending


@pytest.mark.parametrize("count", [0, 1, 5])
def test_worker_contract_keeps_every_real_schema_error(count):
    doc = sample_model()
    original = next(r for r in doc["rules"] if r["kind"] == "HARD")
    doc["rules"] = []
    for i in range(5):
        r = copy.deepcopy(original)
        r["id"] = f"R-DIAG-{i}"
        r["blockers"] = ["Q-3b-DIAG"]
        r.pop("conditional", None)
        doc["rules"].append(r)
    doc["questions"] = [{"id": "Q-3b-DIAG", "part": "3b", "title": "Уточнение",
        "text": "Как читаются эти пять строк?", "rule_refs": [r["id"] for r in doc["rules"]],
        "source_refs": copy.deepcopy(original["sources"]),
        "answer_format": {"type": "value", "note": "полный текст"}}]
    assert answers.apply_answers(doc, {"Q-3b-DIAG": "Подтверждаем прежние формулировки."})["awaiting"] == 1
    before = copy.deepcopy(doc["rules"])
    candidates = copy.deepcopy(before)
    for i, r in enumerate(candidates):
        r.pop("blockers", None)
        if i < count:
            r["kind"] = f"НЕСУЩЕСТВУЮЩИЙ-{r['id']}"
    direct = m.validate_schema({**doc, "rules": candidates})
    assert len(direct) == count
    reply = json.dumps({"patches": [{"question": "Q-3b-DIAG", "rules": candidates}]}, ensure_ascii=False)
    result = answers.apply_worker_patches(doc, lambda _: reply)
    if count:
        reasons = result["worker_rejected"][0]["reasons"]
        assert result["worker_applied"] == 0
        for error in direct:
            assert "патч не проходит контракт: " + error in reasons
        assert doc["rules"] == before
    else:
        assert result["worker_applied"] == 1 and result["worker_rejected"] == []


@pytest.mark.parametrize("long", [False, True])
def test_revision_quotes_the_complete_previous_airline_answer(long):
    doc = sample_model()
    qid = "Q-2-MODAL"
    answers.apply_answers(doc, {qid: "б"})
    old = next(q for q in doc["questions"] if q["id"] == qid)
    raw = _text(long)
    old["answer"]["raw"] = raw
    created = answers.open_revision(doc, qid)["created"]
    new = next(q for q in doc["questions"] if q["id"] == created)
    assert f"Прежний ответ на {qid} принят и применён («{raw}»)." in new["text"]
    assert old["answer"]["raw"] == raw and old["answer"]["status"] == "пересмотрен"


@pytest.mark.parametrize("long", [False, True])
def test_reading_question_preserves_both_distinguishing_endings(long):
    doc = sample_model()
    auditor, worker = _text(long, "отдых не менее 24 часов"), _text(long, "отдых ровно 12 часов")
    rm = _remark(proposal=auditor)
    qid = ex._reading_question(doc, rm, worker, 1)
    q = next(q for q in doc["questions"] if q["id"] == qid)
    assert q["answer_format"]["options"] == [
        {"key": "а", "text": "чтение аудитора: " + auditor},
        {"key": "б", "text": "чтение обработчика: " + worker}]


@pytest.mark.parametrize("long", [False, True])
def test_retelling_question_keeps_both_readings(long, monkeypatch):
    rule = _rule_duration()
    rule["statement"] = _text(long, "отдых не менее 24 часов")
    retelling = _text(long, "отдых ровно 12 часов")
    # Isolate transport of an already rendered reading, not the semantic renderer.
    monkeypatch.setattr(m, "render_logic", lambda *a: retelling)
    doc = {"rules": [rule], "questions": []}
    result = logic_proof.retell_check(doc, lambda _: json.dumps({"verdicts": [
        {"rule": rule["id"], "equivalent": False, "discrepancy": "разные пределы"}]}))
    assert result["blocked"] == [rule["id"]]
    assert doc["questions"][0]["answer_format"]["options"] == [
        {"key": "а", "text": "формулировка свода: " + rule["statement"]},
        {"key": "б", "text": "машинный пересказ: " + retelling}]


@pytest.mark.parametrize("long", [False, True])
def test_family_question_quotes_complete_rule(long):
    statement = _text(long)
    text = family_params._question_text("night_series", "night_window_days",
                                        [{"id": "R-NIGHT", "statement": statement}])
    assert f"({statement}) посажено" in text


@pytest.mark.parametrize("long", [False, True])
def test_reinstate_question_does_not_drop_condition(long):
    statement = _text(long, "при разнице часовых поясов отдых не менее 24 часов")
    rule = {"id": "R-REINSTATE", "kind": "OUT", "statement": statement,
        "note": "отменено: выбрано значение", "params": [{"name": "rest", "value": 24}],
        "sources": [{"sid": "S1", "anchor": "п.1"}, {"sid": "S2", "anchor": "п.2"}]}
    doc = {"rules": [rule], "questions": []}
    assert len(m.ensure_reinstate_questions(doc)) == 1
    assert f"(«{statement}»)" in doc["questions"][0]["text"]
    assert rule["kind"] == "OUT" and rule["blockers"] == [doc["questions"][0]["id"]]


@pytest.mark.parametrize("long", [False, True])
def test_registry_keeps_full_remark_and_basis_on_every_round(long):
    words, basis = _text(long), _text(long, "норма относится только к ночным рейсам")
    pack = _pack(_remark(text=words))
    registry = {"findings": []}
    for round_no in (1, 2):
        ex.registry_update(registry, round_no, pack,
            {"answers": [{"remark": "A-001", "basis": basis}]})
        assert registry["findings"][0]["text"] == words
        assert registry["findings"][0]["basis"] == basis


@pytest.mark.parametrize("long", [False, True])
def test_auditor_question_listing_keeps_full_text_and_counts_hidden_refs(long):
    doc = sample_model()
    q = doc["questions"][0]
    q["text"] = _text(long)
    q["rule_refs"] = [f"R-{i}" for i in range(9)]
    q["source_refs"] = [{"sid": "S1", "anchor": f"п.{i}"} for i in range(11)]
    doc["questions"] = [q]
    listing = ex._open_questions_listing(doc)
    assert q["text"] in listing
    assert ", ".join(q["rule_refs"][:6]) + " (и ещё 3)" in listing
    assert ", ".join(f"S1:п.{i}" for i in range(8)) + " (и ещё 3)" in listing


@pytest.mark.parametrize("validator", ["validate_schema", "validate_model"])
@pytest.mark.parametrize("count", [0, 1, 5])
def test_goal_answer_keeps_all_contract_diagnostics(validator, count, monkeypatch):
    from test_cdpm_goal_editions import _goal
    doc, q = _goal(soft=False)
    answers.apply_answers(doc, {q["id"]: "Оптимизация не нужна"})
    before = copy.deepcopy(doc)
    expected = [f"R-G-{i}: полная причина {i}" for i in range(count)]
    monkeypatch.setattr(m, validator, lambda *a: expected)
    got = answers._apply_goal_answer(doc, q,
        lambda _: json.dumps({"decline": True, "objectives": [], "weights": []}))
    assert got == ["разбор целевой не проходит контракт: " + error for error in expected]
    if count:
        assert doc == before
    else:
        assert q["answer"]["status"] == "применён"


@pytest.mark.parametrize("long", [False, True])
def test_real_arbiter_question_preserves_both_full_readings(workspace, long):
    from test_cdpm_exchange_run import (
        _REMARK_DISTORT, _make_auditor, _make_worker, _make_arbiter, _enforcing_worker)
    kit, out, exdir = workspace
    proposal = _text(long, "отдых не менее 24 часов")
    basis = _text(long, "отдых ровно 12 часов")
    remark = {**_REMARK_DISTORT, "proposal": proposal}
    audit = _make_auditor([[remark]] * 6)
    worker = _make_worker({"Потеряно уточнение": {"verdict": "ОТКЛОНЕНО", "basis": basis}})
    ex.run_exchange(kit, out, exdir, audit, worker, max_rounds=6)
    result = ex.run_arbiter(kit, exdir, _make_arbiter("в анкету"), _enforcing_worker)
    assert result["added_questions"]
    final = m.load_model(exdir / "versions" / result["final_version"])
    q = next(q for q in final["questions"] if q["id"] == result["added_questions"][0])
    assert q["answer_format"]["options"] == [
        {"key": "а", "text": "чтение аудитора: " + proposal},
        {"key": "б", "text": "чтение обработчика: " + basis}]
    from cdpm.gates import gate_arbiter
    assert gate_arbiter.check(exdir)[0] == []


@pytest.mark.parametrize("long", [False, True])
def test_code_locked_arbiter_keeps_complete_reason_and_does_not_enforce(workspace, long):
    from test_cdpm_a09_arbiter_duplicates import _code_rejected_cycle
    from test_cdpm_exchange_run import _make_arbiter, _enforcing_worker
    kit, out, exdir = workspace
    _code_rejected_cycle(kit, out, exdir)
    path = exdir / "registry.json"
    registry = json.loads(path.read_text(encoding="utf-8"))
    finding = next(f for f in registry["findings"] if f["state"] == "тупик")
    # Keep the real terminal state; vary the diagnostic text supplied to its builder.
    words = _text(long, finding["code_rejection"])
    finding["code_rejection"] = words
    path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    result = ex.run_arbiter(kit, exdir, _make_arbiter("аудитор прав"), _enforcing_worker)
    assert result["enforced"] == 0 and result["unenforced"]
    final = m.load_model(exdir / "versions" / result["final_version"])
    q = next(q for q in final["questions"] if q["id"] == result["added_questions"][0])
    assert q["answer_format"]["options"][1]["text"] == "оставить требование кода в силе: " + words
    from cdpm.gates import gate_arbiter
    assert gate_arbiter.check(exdir)[0] == []
