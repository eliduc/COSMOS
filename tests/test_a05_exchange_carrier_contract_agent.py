"""A05: complete exchange candidates must keep parameter carriers consistent.

Synthetic source only. Both ordinary exchange and arbitration use the real
_apply_patches receiver; round99 exercises its actual enforcement options,
not LLM calls, a full arbitration lifecycle, or a claim of semantic approval.
The selected shipped legacy catalogue is not upgraded or copied by tests.
"""
import copy
import hashlib
from pathlib import Path

import pytest

from cdpm import catalog_bridge as cb, exchange as ex, extract, model as m


NAME = "workload_spread_threshold"
ANCHOR = "п.1"
QUOTE = "10 кредитных часов"
WEIGHT_QUOTE = "Штраф 3 единицы"
CLAUSE = "Штраф 3 единицы за каждый час разброса сверх 10 кредитных часов."


@pytest.fixture
def catalogue(monkeypatch):
    monkeypatch.setenv(cb.ENV_CATALOG, str(Path(__file__).resolve().parents[1] / "catalog"))
    cb.forget()
    try:
        cat = cb.catalog()
        assert cat.version() == "0.5.0"
        definition = next(p for p in cat.family("balanced_workload")["params"]
                          if p["name"] == NAME)
        assert definition["unit"] == "credit_hours"
        assert definition["relation"] == "max"
        yield cat
    finally:
        cb.forget()


@pytest.fixture(params=[(1, True), (99, False)], ids=["exchange", "arbitration-receiver"])
def route(request):
    return request.param


def _initial():
    source = {"sid": "S1", "file": "synthetic-carrier-rule.txt",
              "sha256": hashlib.sha256(CLAUSE.encode("utf-8")).hexdigest()}
    candidate = {"anchor": ANCHOR, "title": "Synthetic spread penalty",
                 "statement": CLAUSE, "kind": "SOFT", "zone": "ROSTERING",
                 "weight": {"value": 3, "as_written": WEIGHT_QUOTE},
                 "params": [{"name": NAME, "value": 10, "unit": "credit_hours",
                             "as_written": QUOTE, "relation": "min"}]}
    before = copy.deepcopy(candidate)
    asm = extract._Assembler("Synthetic A05 carrier contract", [source])
    job = {"sid": "S1", "known": {ANCHOR}, "listing": f"[{ANCHOR}] {CLAUSE}",
           "texts": {ANCHOR: CLAUSE}}
    rejected = []
    assert extract._assemble(asm, [job], [{"candidates": [candidate]}],
                             rejected, {}, {}) == 1
    assert candidate == before
    doc = asm.doc
    assert len(doc["rules"]) == 1
    row = doc["rules"][0]
    assert row["template_fit"]["category"] == "C"
    assert row["params"][0]["relation"] == "min"
    assert m.validate_param(row["params"][0], "SOFT") == []
    assert m.validate_schema(doc) == m.validate_model(doc) == []
    assert m.carrier_problems(doc) == []
    return doc


def _proposal(doc, *, explicit_params):
    row = copy.deepcopy(doc["rules"][0])
    row.pop("note", None)
    row["template_fit"] = {"category": "A", "family": "balanced_workload",
                           "params": {NAME: {"value": 10, "quote": QUOTE}}}
    if explicit_params:
        row["params"][0]["relation"] = "max"
    else:
        row.pop("params")
    return row


def _receive(doc, proposal, route, *, remark_id="A-CARRIER"):
    round_no, convert = route
    rid = doc["rules"][0]["id"]
    remark = {"id": remark_id, "severity": "КРИТИЧЕСКОЕ",
              "defect_class": "искажение", "target": {"rule": rid},
              "source_refs": [{"sid": "S1", "anchor": ANCHOR}],
              "text": "Уточнить машинную запись исходного правила",
              "evidence": CLAUSE, "proposal": "Явная полная заплата строки"}
    pack = {"round": round_no, "remarks": [remark]}
    response = {"round": round_no, "answers": [{"remark": remark_id,
                "verdict": "ПРИНЯТО", "basis": CLAUSE, "change": "Явная правка",
                "patch": {"rule_id": rid, "rule": copy.deepcopy(proposal)}}]}
    assert ex.validate_remarks(pack, doc) == []
    before, proposal_before = copy.deepcopy(doc), copy.deepcopy(proposal)
    executed = set()
    result = ex._apply_patches(doc, pack, response, round_no,
                              valid_anchors={("S1", ANCHOR)},
                              clause_texts={("S1", ANCHOR): CLAUSE},
                              convert_reading_choice=convert, executed_out=executed)
    assert doc == before and proposal == proposal_before
    return result, executed


def test_new_fit_cannot_inherit_conflicting_legacy_params(catalogue, route):
    doc = _initial()
    proposal = _proposal(doc, explicit_params=False)
    (new, targets, rejected, tokens), executed = _receive(doc, proposal, route)
    assert new == doc and targets == set() and executed == set(), (new, rejected, executed)
    assert rejected and rejected[0]["answer"] == "A-CARRIER"
    reasons = " ".join(rejected[0]["reasons"])
    assert NAME in reasons and "max" in reasons and "min" in reasons, rejected
    assert tokens == [] and m.carrier_problems(new) == []


def test_explicit_consistent_full_patch_is_accepted(catalogue, route):
    doc = _initial()
    (new, targets, rejected, tokens), executed = _receive(
        doc, _proposal(doc, explicit_params=True), route)
    row = new["rules"][0]
    assert rejected == [] and targets == {row["id"]}, rejected
    assert executed == {"A-CARRIER"} and tokens
    assert row["params"][0]["relation"] == "max"
    assert row["template_fit"]["family"] == "balanced_workload"
    assert m.validate_schema(new) == m.validate_model(new) == []
    assert m.carrier_problems(new) == []
    assert row["sources"] == doc["rules"][0]["sources"]
    assert row["weight"] == 3 and row["weight_quote"] == WEIGHT_QUOTE


def test_healthy_full_row_patch_may_omit_unchanged_fit(catalogue, route):
    doc = _initial()
    (healthy, _, rejected, _), _ = _receive(doc, _proposal(doc, explicit_params=True), route)
    assert rejected == [] and m.carrier_problems(healthy) == []
    proposal = copy.deepcopy(healthy["rules"][0])
    proposal.pop("template_fit")
    proposal.pop("params")
    proposal["note"] = "Явное пояснение без изменения машинных значений"
    (new, targets, rejected, tokens), executed = _receive(
        healthy, proposal, route, remark_id="A-NOTE")
    row = new["rules"][0]
    assert rejected == [] and targets == {row["id"]}, rejected
    assert executed == {"A-NOTE"} and tokens
    assert row["note"] == proposal["note"]
    assert row["template_fit"] == healthy["rules"][0]["template_fit"]
    assert row["params"] == healthy["rules"][0]["params"]
    assert m.validate_schema(new) == m.validate_model(new) == []
    assert m.carrier_problems(new) == []


@pytest.mark.parametrize("name", ["_PROMPT_RESPOND", "_PROMPT_ENFORCE"])
def test_handoff_prompt_formats_with_explicit_params_guidance(name):
    # Exercise real str.format syntax, including all literal JSON braces.
    # This is a text contract only; no response generation is simulated.
    rendered = getattr(ex, name).format(remarks="REMARK-MARKER", rules="RULE-MARKER",
                                       clauses="CLAUSE-MARKER", dictionary="DICTIONARY-MARKER")
    text = " ".join(rendered.split())
    assert all(marker in rendered for marker in ("REMARK-MARKER", "RULE-MARKER", "CLAUSE-MARKER"))
    assert '{"answers": [{' in text
    assert "{name, value, unit, as_written, relation}" in text
    assert "params САМОЙ СТРОКИ" in text
    assert "Опущенный params наследуется от прежней строки" in text
    assert "Не удаляй параметры ради обхода проверки" in text
