"""A05 neighboring repair writers, with real assembler/slot selection.

In-memory candidate/reply fixtures only; no live model, audit verdict, or
gate is mocked. Receiver controls are distinguished from the public repair
loop: an already consumed old value does not itself schedule another repair.
No saved healthy model is damaged to manufacture the initial carrier conflict.
"""
import copy
import hashlib
import json

import pytest

from cdpm import balance, extract, model as m, repair
from cdpm.param_projection import derive_params_from_landing
from test_a05_extracted_relation_contract_agent import (
    NAME, FAMILY, CLAUSE, QUOTE, _assemble, _candidate, catalog,
)


def _assemble_clause(candidate, clause):
    before = copy.deepcopy(candidate)
    source = {"sid": "S1", "file": "a05-neighbor-clause.txt",
              "sha256": hashlib.sha256(clause.encode("utf-8")).hexdigest()}
    asm = extract._Assembler("A05 neighboring writer probe", [source])
    job = {"sid": "S1", "known": {"п.1"}, "listing": f"[п.1] {clause}",
           "texts": {"п.1": clause}}
    rejected = []
    assert extract._assemble(asm, [job], [{"candidates": [copy.deepcopy(candidate)]}],
                             rejected, {}, {}) == 1
    assert candidate == before
    assert len(asm.doc["rules"]) == 1
    assert m.validate_schema(asm.doc) == []
    return asm.doc


def _assert_slot(row, slot):
    available = repair._slots_for(row, False, m.template_families())
    assert any(s["name"] == slot and not s.get("internal") for s in available), available


def _rejected_param_doc(relation="max"):
    healthy, _ = _assemble(_candidate("max"))
    proposal = _candidate(relation)
    # This NEW explicit typed proposal lacks a value, so the real assembler
    # rejects it without filling it from legacy params. The accepted first
    # document is then C + legacy, each independently consistent.
    proposal["template_fit"] = copy.deepcopy(healthy["rules"][0]["template_fit"])
    proposal["template_fit"]["params"] = {}
    doc, _ = _assemble(proposal)
    row = doc["rules"][0]
    assert repair.rejected_family(row["template_fit"]) == FAMILY, row
    assert row["params"][0]["relation"] == relation
    assert m.validate_template_fit(doc) == m.carrier_problems(doc) == []
    _assert_slot(row, f"param:{NAME}")
    return doc


@pytest.mark.parametrize("case", ["equal", "relation-conflict", "unproved-value", "foreign-quote"])
def test_rejected_family_receiver_checks_destination_carriers(catalog, case):
    doc = _rejected_param_doc("min" if case == "relation-conflict" else "max")
    row = doc["rules"][0]
    before = copy.deepcopy(doc)
    reply = {"value": 11 if case == "unproved-value" else 10,
             "quote": "10 кредитных часов из другого документа" if case == "foreign-quote" else QUOTE}
    original_reply = copy.deepcopy(reply)
    error = repair._apply(row, False, f"param:{NAME}", reply, CLAUSE, doc)
    assert reply == original_reply
    assert m.carrier_problems(doc) == [], (case, error, m.carrier_problems(doc))
    if case != "equal":
        assert error is not None, row
        assert doc == before
    else:
        assert error is None, error
        assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
        assert row["params"] == before["rules"][0]["params"]
        cell = row["template_fit"]["params"][NAME]
        assert (cell["value"], cell["unit"], cell["relation"], cell["condition"]) == (10, "credit_hours", "max", None)
        assert cell["source"] == {"kind": "rule", "rule_id": row["id"]}
        projected, = derive_params_from_landing(row)
        assert projected["relation"] == "max"


DUTY_CLAUSE = "Длительность смены не более 8 часов. За смену не более 4 посадок."
DUTY_NAME = "max_landings_per_duty"


def _duty_doc():
    candidate = {"anchor": "п.1", "title": "Пределы смены", "statement": DUTY_CLAUSE,
                 "zone": "PAIRING", "kind": "HARD", "params": [],
                 "template_fit": {"category": "A", "family": "duty_limits",
                                  "params": {"max_duty_duration": {"value": 480, "quote": "8 часов"}}}}
    doc = _assemble_clause(candidate, DUTY_CLAUSE)
    row = doc["rules"][0]
    assert row["template_fit"]["category"] == "A", row
    assert row["template_fit"]["schema_version"] == "landing/2"
    assert DUTY_NAME not in row["template_fit"]["params"]
    assert m.validate_template_fit(doc) == m.carrier_problems(doc) == []
    _assert_slot(row, f"param:{DUTY_NAME}")
    return doc


def _public_repair(doc, clause, slot, reply, finding_value):
    row = doc["rules"][0]
    src = row["sources"][0]
    entries = [{"sid": src["sid"], "clauses": [{"anchor": src["anchor"], "text": clause}]}]
    findings = balance.report(entries, doc)["findings"]
    selected = [f for f in findings if f["value"] == finding_value and row["id"] in f["rows"]]
    assert selected, findings  # proves real balance -> scheduler reachability
    calls = []

    def respond(prompt):
        calls.append(prompt)
        assert slot in prompt
        return json.dumps({"slot": slot, **reply}, ensure_ascii=False)

    report = repair.repair(doc, entries, selected[:1], respond, max_attempts=1)
    assert len(calls) == 1
    return report


@pytest.mark.parametrize("route", ["receiver", "repair-loop"])
@pytest.mark.parametrize("case", ["valid", "unproved-value", "foreign-quote", "boolean"])
def test_missing_current_cell_is_typed_evidenced_and_atomic(catalog, route, case):
    doc = _duty_doc()
    row = doc["rules"][0]
    before = copy.deepcopy(doc)
    reply = {"value": {"unproved-value": 5, "boolean": True}.get(case, 4),
             "quote": "4 посадок другого документа" if case == "foreign-quote" else "4 посадок"}
    if route == "receiver":
        error = repair._apply(row, False, f"param:{DUTY_NAME}", reply, DUTY_CLAUSE, doc)
        accepted = error is None
        result = error
    else:
        result = _public_repair(doc, DUTY_CLAUSE, f"param:{DUTY_NAME}", reply, 4)
        accepted = bool(result["fixed"])
    if case != "valid":
        assert not accepted, (case, result, row)
        assert doc == before
        return
    assert accepted, result
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    assert m.carrier_problems(doc) == []
    fit = row["template_fit"]
    cell = fit["params"][DUTY_NAME]
    assert cell == {"value": 4, "quote": "4 посадок", "unit": "count", "relation": "max",
                    "condition": None, "source": {"kind": "rule", "rule_id": row["id"]}}
    assert fit["params"]["max_duty_duration"] == before["rules"][0]["template_fit"]["params"]["max_duty_duration"]
    assert fit["family_contract"] == before["rules"][0]["template_fit"]["family_contract"]


@pytest.mark.parametrize("bad_quote", [False, True])
def test_parameterless_actual_rejected_slot_retains_quote_law(catalog, bad_quote):
    clause = "Начисляется штраф за каждую ночёвку."
    proposal = {"anchor": "п.1", "title": "Штраф за ночёвку", "statement": clause,
                "zone": "PAIRING", "kind": "SOFT", "params": [],
                "template_fit": {"category": "A", "family": "layover_penalty"}}
    doc = _assemble_clause(proposal, clause)
    row = doc["rules"][0]
    assert repair.rejected_family(row["template_fit"]) == "layover_penalty", row
    _assert_slot(row, "fit_quote:layover_penalty")
    before = copy.deepcopy(doc)
    quote = "штраф за каждую ночёвку" + (" в другом документе" if bad_quote else "")
    error = repair._apply(row, False, "fit_quote:layover_penalty", {"quote": quote}, clause, doc)
    if bad_quote:
        assert error is not None and doc == before
        return
    assert error is None, error
    fit = row["template_fit"]
    assert fit["schema_version"] == "landing/2" and fit["params"] == {} and fit["quote"] == quote
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    assert m.carrier_problems(doc) == []


@pytest.mark.parametrize("old_carrier", [False, True])
def test_rejected_param_public_loop_refuses_new_value_conflicting_with_old(catalog, old_carrier):
    clause = "При разбросе кредитных часов порог существенности 11 кредитных часов; 10 кредитных часов упомянуты отдельно."
    healthy, _ = _assemble(_candidate("max"))
    proposal = _candidate("min")
    proposal.pop("weight")
    proposal["statement"] = clause
    proposal["template_fit"] = copy.deepcopy(healthy["rules"][0]["template_fit"])
    proposal["template_fit"]["params"] = {}
    if not old_carrier:
        proposal["params"] = []
    doc = _assemble_clause(proposal, clause)
    row = doc["rules"][0]
    assert repair.rejected_family(row["template_fit"]) == FAMILY, row
    assert m.carrier_problems(doc) == []
    _assert_slot(row, f"param:{NAME}")
    before = copy.deepcopy(doc)
    result = _public_repair(doc, clause, f"param:{NAME}",
                            {"value": 11, "quote": "11 кредитных часов"}, 11)
    if old_carrier:
        assert result["fixed"] == [] and len(result["left"]) == 1, result
        assert doc == before
    else:
        assert len(result["fixed"]) == 1 and result["left"] == [], result
        assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    assert m.carrier_problems(doc) == []
