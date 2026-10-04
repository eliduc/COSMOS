"""Offline witnesses for two H0-C prompt/retelling contract failures.

Real prompt builders, landing validators and logic gates remain active. The
callables below capture prompts or return declared finite replies: no semantic
LLM verdict, catalogue acceptance or end-to-end dataset PASS is claimed.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket

import pytest

from cdpm import catalog_bridge as cb, exchange as ex, extract, logic_proof
from cdpm import family_params as fp, model as m


QUOTE = "Ежедневно резерв: PLY ≥ 2 экипажа, SVG ≥ 1, GRT ≥ 1"
VALUES = {"GRT": 1, "PLY": 2, "SVG": 1}
NORM = "Член экипажа не может быть назначен на рейс в дни своего отпуска"
GENERIC = "Член экипажа не может быть назначен на рейс в дни недоступности"
BASE_MARKER = "ФОРМАТ ЗНАЧЕНИЙ ПО БАЗАМ"
SCOPE_MARKER = "ТОЧНОСТЬ ОБЛАСТИ ЛОГИЧЕСКОГО ПРЕДИКАТА"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("offline contract test attempted network access")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket.socket, "sendto", denied)
    monkeypatch.setenv("CDPM_PARALLEL", "1")
    monkeypatch.setenv(cb.ENV_CATALOG, str(
        Path(__file__).resolve().parents[1] / "catalog-integration" / "0.7.0-draft.2"))
    cb.forget()
    yield
    cb.forget()


def _doc(rule):
    return {
        "meta": {"customer": "Offline format contract", "mode": 1,
                 "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "rules.txt",
                     "sha256": hashlib.sha256(rule["statement"].encode()).hexdigest()}],
        "rules": [rule], "objectives": [], "questions": [],
    }


def _reserve(value=VALUES):
    candidate = {
        "id": "R-reserve", "title": "Резерв", "kind": "HARD", "zone": "BOTH",
        "statement": QUOTE, "sources": [{"sid": "S1", "anchor": "п.1"}],
        "template_fit": {"category": "A", "family": "reserve_floors",
                         "params": {"reserve_crews": {"value": deepcopy(value),
                                                        "quote": QUOTE}}},
    }
    fit, _confidence, note = m.accept_fit_candidate(candidate, QUOTE, new_output=True)
    assert fit["category"] == "A", (fit, note)
    candidate["template_fit"] = fit
    return candidate


def _vacation(statement=NORM):
    return {
        "id": "R-vacation", "title": "Недоступность", "statement": statement,
        "kind": "HARD", "zone": "ROSTERING",
        "sources": [{"sid": "S1", "anchor": "п.1"}],
        "template_fit": {"category": "C", "gap": "Нужна механика исходной нормы",
                         "schema_version": "landing/2", "registry_rev": cb.catalog().fingerprint()},
    }


def _logic(quote="в дни своего отпуска"):
    return {"subject": "assignment", "condition": [
        {"predicate": "marked_unavailable", "as_written": quote}],
        "demand": {"kind": "not_exists", "subject": "assignment",
                   "as_written": "не может быть назначен на рейс"},
        "enforce": "required", "source_params": []}


class Captured(RuntimeError):
    pass


def _capture_role(role):
    doc = _doc(_reserve())
    before = deepcopy(doc)
    kit = [{"sid": "S1", "clauses": [{"anchor": "п.1", "text": QUOTE}]}]
    pack = {"round": 1, "remarks": [{
        "id": "A1-001", "severity": "СЕРЬЁЗНОЕ", "defect_class": "искажение",
        "target": {"rule": "R-reserve"}, "source_refs": [{"sid": "S1", "anchor": "п.1"}],
        "text": "Проверить форму значений по базам", "evidence": QUOTE,
        "proposal": "Сохранить все нормы и их основания"}]}
    prompts = []

    def capture(prompt):
        prompts.append(prompt)
        raise Captured()

    with pytest.raises(Captured):
        if role == "auditor":
            ex._audit_round(capture, doc, kit, 1)
        else:
            ex._respond_round(capture, doc, pack, {("S1", "п.1"): QUOTE},
                              template=ex._PROMPT_ENFORCE if role == "enforce" else None)
    assert len(prompts) == 1
    assert doc == before
    return prompts[0]


@pytest.mark.parametrize("role", ["auditor", "worker", "enforce"])
def test_real_prompts_agree_on_one_cell_and_supported_map_value(role):
    prompt = _capture_role(role)
    assert BASE_MARKER in prompt
    assert "value — объект" in prompt
    assert "legacy-вход" in prompt and "преобразует в карту" in prompt
    assert "В landing/2 строка вместо карты не канон" in prompt
    assert "не список ячеек" in prompt
    assert "condition.base_code" in prompt
    assert "сам по себе не дефект" in prompt
    assert "одной записью, value строкой канонической формы" not in prompt
    assert QUOTE in prompt


@pytest.mark.parametrize("value", [VALUES, {"SVG": 1, "PLY": 2, "GRT": 1},
                                  "GRT:1,PLY:2,SVG:1"])
def test_supported_base_values_and_provenance_remain_valid(value):
    rule = _reserve(value)
    before = deepcopy(rule)
    cell = rule["template_fit"]["params"]["reserve_crews"]
    assert fp._qualified_cells(value) == (
        {key: str(count) for key, count in VALUES.items()} if isinstance(value, str) else VALUES)
    if isinstance(value, dict):
        assert fp.base_map(value) == VALUES
    assert cell["value"] == VALUES  # The existing legacy reader upgrades the packed string.
    assert cell["unit"] == "count" and cell["relation"] == "min"
    assert cell["source"] == {"kind": "rule", "rule_id": "R-reserve"}
    assert m.validate_template_fit(_doc(rule)) == []
    assert m.validate_fit_quotes(_doc(rule), {("S1", "п.1"): QUOTE}) == []
    assert rule == before


def test_explicit_typed_cell_does_not_accept_legacy_packed_string():
    rule = _reserve()
    rule["template_fit"]["params"]["reserve_crews"]["value"] = "GRT:1,PLY:2,SVG:1"
    assert m.validate_template_fit(_doc(rule))


def test_one_base_scalar_with_explicit_canonical_condition_remains_valid():
    rule = _reserve()
    rule["statement"] = "PLY ≥ 2 экипажа"
    cell = rule["template_fit"]["params"]["reserve_crews"]
    cell.update(value=2, condition={"kind": "base_code", "value": "PLY"},
                quote=rule["statement"])
    assert m.validate_template_fit(_doc(rule)) == []
    assert m.validate_fit_quotes(_doc(rule), {("S1", "п.1"): rule["statement"]}) == []


def test_rejected_worker_list_still_fails_the_real_landing_validator():
    rule = _reserve()
    params = rule["template_fit"]["params"]
    original = params["reserve_crews"]
    params["reserve_crews"] = [
        {**deepcopy(original), "value": count, "condition": {"base_code": base}}
        for base, count in VALUES.items()]
    before = deepcopy(rule)
    problems = m.validate_template_fit(_doc(rule))
    assert problems
    assert m._fit_shape_reason(rule["template_fit"], QUOTE) == (
        "запись параметра 'reserve_crews' не объект")
    assert rule == before


@pytest.mark.parametrize("value", [True, 2, {}, {"PLY": True}, {"PLY": -1},
                                  {"PLY": 2.5}, {"PLY": "2"}, {"PLY": {"value": 2}},
                                  {"GRT": 1, "PLY": 1, "SVG": 2}])
def test_bad_or_misbound_base_values_are_not_accepted(value):
    rule = _reserve()
    rule["template_fit"]["params"]["reserve_crews"]["value"] = value
    before = deepcopy(rule)
    assert m.validate_template_fit(_doc(rule))
    assert rule == before


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def test_initial_extraction_receives_both_general_contract_boundaries(tmp_path):
    kit = tmp_path / "freeze"
    _write(kit / "manifest.json", {"files": [{"sid": "S1"}]})
    _write(kit / "clauses/S1.json", {"sid": "S1", "file": "rules.txt", "sha256": "0" * 64,
           "clauses": [{"anchor": "п.1", "text": NORM}]})
    prompts = []

    def capture(prompt):
        prompts.append(prompt)
        raise Captured()

    with pytest.raises(Captured):
        extract.run_extract(kit, tmp_path / "extract", capture, "Offline customer")
    assert len(prompts) == 1
    assert BASE_MARKER in prompts[0]
    assert SCOPE_MARKER in prompts[0]
    assert "as_written не сужает" in prompts[0]
    assert NORM in prompts[0]


def test_honest_null_preserves_original_rule_gap_and_source(tmp_path):
    rule = _vacation()
    doc = _doc(rule)
    out = tmp_path / "extract"
    _write(out / "model.json", doc)
    prompts = []

    def declared_null(prompt):
        prompts.append(prompt)
        assert SCOPE_MARKER in prompt
        assert "marked_unavailable" in prompt
        assert "причину" in prompt and "отпуска" in prompt
        assert "as_written не сужает" in prompt
        assert "верни null" in prompt
        return '{"logic": null}'

    summary = extract.produce_logic(out, declared_null)
    saved = json.loads((out / "model.json").read_text(encoding="utf-8"))
    assert summary == {"asked": 1, "attached": 0, "dropped": 0}
    assert saved["rules"] == [rule]
    assert saved["questions"] == []
    assert saved["sources"] == doc["sources"]
    assert len(prompts) == 1


@pytest.mark.parametrize("quote", ["в дни своего отпуска", "в дни недоступности"])
def test_retell_describes_the_machine_predicate_not_its_narrow_source_quote(quote):
    rule = _vacation()
    lg = _logic(quote)
    assert m.validate_logic(lg, rule) == []
    assert m.LOGIC_PREDICATES["marked_unavailable"] == ()
    retelling = m.render_logic(lg, rule)
    assert "помечен как недоступный" in retelling
    assert "отпуск" not in retelling
    assert "в отпуске или недоступности" not in retelling
    assert "назначение" in retelling


@pytest.mark.parametrize("reply", [
    {"verdicts": []},
    {"verdicts": [{"rule": "R-vacation", "equivalent": False,
                   "discrepancy": "Общая недоступность шире отпуска"}]},
])
def test_broad_predicate_still_blocks_on_silence_or_non_equivalence(reply):
    rule = _vacation()
    rule["logic"] = _logic()
    original = deepcopy(rule)
    doc = _doc(rule)
    prompts = []

    def verifier(prompt):
        prompts.append(prompt)
        return json.dumps(reply, ensure_ascii=False)

    result = logic_proof.retell_check(doc, verifier)
    assert result["blocked"] == [rule["id"]] and result["equivalent"] == 0
    assert NORM in prompts[0]
    assert "помечен как недоступный" in prompts[0]
    assert doc["rules"][0]["statement"] == original["statement"]
    assert doc["rules"][0]["logic"] == original["logic"]
    assert doc["rules"][0]["template_fit"] == original["template_fit"]
    assert doc["rules"][0]["blockers"] == [m.stable_question_id("2", "лог-R-vacation")]
    assert len(doc["questions"]) == 1


def test_generic_unavailability_logic_remains_an_allowed_healthy_form(tmp_path):
    rule = _vacation(GENERIC)
    out = tmp_path / "extract"
    _write(out / "model.json", _doc(rule))
    lg = _logic("в дни недоступности")

    def declared_logic(_prompt):
        return json.dumps({"logic": lg}, ensure_ascii=False)

    def declared_equivalent(_prompt):
        return json.dumps({"verdicts": [{"rule": rule["id"], "equivalent": True}]})

    summary = extract.produce_logic(out, declared_logic, verifier=declared_equivalent)
    saved = json.loads((out / "model.json").read_text(encoding="utf-8"))
    assert summary["attached"] == 1 and summary["dropped"] == 0
    assert summary["retell"]["blocked"] == []
    assert saved["rules"][0]["logic"] == lg
    assert saved["rules"][0]["statement"] == GENERIC
    assert saved["rules"][0]["template_fit"] == rule["template_fit"]
    assert not saved["rules"][0].get("blockers")
