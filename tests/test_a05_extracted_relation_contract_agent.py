"""A05: deterministic candidate assembly, not a replay of auditor run-9053.

The response and source clause below are explicit in-memory fixtures. The
current assembler is exercised unchanged; no model, verdict, or gate is mocked.
Saved conflicting carriers are a separate refusal control, not repair inputs.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from cdpm import catalog_bridge as cb, extract, family_params, model as m, repair, second_reading
from cdpm.param_projection import derive_params_from_landing, ParamProjectionError
from test_k03_unit_vocabulary import selected


NAME = "workload_spread_threshold"
FAMILY = "balanced_workload"
CLAUSE = ("Штраф 3 условные единицы за каждый кредитный час разброса кредитных часов "
          "сверх порога существенности 10 кредитных часов за месяц.")
OBSERVED_QUOTE = "сверх порога существенности 10 кредитных часов за месяц"
# A newly proposed exact value quotation, not a repair of the saved quote.
# The complete unchanged clause still carries the monthly scope.
QUOTE = "10 кредитных часов"
WEIGHT_QUOTE = "Штраф 3 условные единицы за каждый кредитный час разброса"
SOURCE = {"sid": "S1", "file": "a05-relation-source.txt",
          "sha256": hashlib.sha256(CLAUSE.encode("utf-8")).hexdigest()}


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    cb.forget()
    try:
        yield selected(tmp_path, monkeypatch)
    finally:
        cb.forget()


def _candidate(relation="min", *, cells=True):
    fit = {"category": "A", "family": FAMILY}
    if cells:
        fit["params"] = {NAME: {"value": 10, "quote": QUOTE}}
    return {"anchor": "п.1", "title": "Штраф за разброс кредитных часов",
            "statement": CLAUSE, "zone": "ROSTERING", "kind": "SOFT",
            "weight": {"value": 3, "as_written": WEIGHT_QUOTE},
            "params": [{"name": NAME, "value": 10, "unit": "credit_hours",
                        "as_written": QUOTE, "relation": relation}],
            "template_fit": fit}


def _assemble(candidate):
    original = copy.deepcopy(candidate)
    asm = extract._Assembler("A05 isolated relation probe", [SOURCE])
    job = {"sid": "S1", "known": {"п.1"}, "listing": f"[п.1] {CLAUSE}",
           "texts": {"п.1": CLAUSE}}
    rejected = []
    count = extract._assemble(asm, [job], [{"candidates": [copy.deepcopy(candidate)]}],
                              rejected, {}, {})
    assert count == 1 and len(asm.doc["rules"]) == 1
    assert candidate == original
    assert m.validate_schema(asm.doc) == []
    return asm.doc, rejected


@pytest.mark.parametrize("relation", ["min", "max"])
@pytest.mark.parametrize("cells", [True, False], ids=["fit-cell", "params-fallback"])
def test_fresh_candidate_uses_catalog_relation_not_legacy_proposal(catalog, relation, cells):
    assert next(p for p in catalog.family(FAMILY)["params"] if p["name"] == NAME)["relation"] == "max"
    doc, rejected = _assemble(_candidate(relation, cells=cells))
    rule = doc["rules"][0]
    fit = rule["template_fit"]
    assert fit["category"] == "A", (fit, rejected)
    assert fit["schema_version"] == "landing/2"
    assert fit["family_contract"]["catalog_fingerprint"] == catalog.fingerprint()
    cell = fit["params"][NAME]
    assert (cell["value"], cell["unit"], cell["relation"]) == (10, "credit_hours", "max")
    assert cell["quote"] == QUOTE
    assert cell["source"] == {"kind": "rule", "rule_id": rule["id"]}
    assert rule["sources"] == [{"sid": "S1", "anchor": "п.1"}]
    assert (rule["weight"], rule["weight_quote"]) == (3, WEIGHT_QUOTE)
    assert not rule.get("params")  # proposed duplicate is not saved as a second truth
    assert m.validate_template_fit(doc) == []
    assert m.carrier_problems(doc) == []
    p, = derive_params_from_landing(rule)
    assert (p["value"], p["unit"], p["relation"]) == (10, "credit_hours", "max")
    assert m.validate_param(p, "SOFT") == []


@pytest.mark.parametrize("cells", [True, False], ids=["fit-cell", "params-fallback"])
def test_observed_long_quote_refuses_before_relation_projection(catalog, cells):
    candidate = _candidate("min", cells=cells)
    if cells:
        candidate["template_fit"]["params"][NAME]["quote"] = OBSERVED_QUOTE
    candidate["params"][0]["as_written"] = "10 кредитных часов за месяц"
    quote = OBSERVED_QUOTE if cells else candidate["params"][0]["as_written"]
    assert family_params.period_qualifier(quote, "credit_hours")
    assert family_params.parse_value(quote, "credit_hours") is None
    doc, _ = _assemble(candidate)
    rule = doc["rules"][0]
    assert rule["template_fit"]["category"] == "C", rule
    assert "значение не подтверждено цитатой" in rule["note"]
    assert m.carrier_problems(doc) == []


@pytest.mark.parametrize("change", ["value", "quote"])
def test_new_cell_needs_value_and_verbatim_source_evidence(catalog, change):
    candidate = _candidate("max")
    cell = candidate["template_fit"]["params"][NAME]
    if change == "value":
        cell["value"] = 11
    else:
        cell["quote"] = "10 кредитных часов из другого документа"
    doc, _ = _assemble(candidate)
    rule = doc["rules"][0]
    assert rule["template_fit"]["category"] == "C", rule
    assert NAME not in rule["template_fit"].get("params", {})
    assert "цитат" in rule.get("note", ""), rule


@pytest.mark.parametrize("change", ["relation", "source"])
def test_explicit_typed_evidence_is_not_relabelled_as_normalization(catalog, change):
    healthy, _ = _assemble(_candidate("max"))
    candidate = _candidate("max")
    candidate["template_fit"] = copy.deepcopy(healthy["rules"][0]["template_fit"])
    cell = candidate["template_fit"]["params"][NAME]
    if change == "relation":
        cell["relation"] = "min"
    else:
        cell["source"] = {"kind": "code", "note": "untrusted claim of migration"}
    doc, _ = _assemble(candidate)
    rule = doc["rules"][0]
    assert rule["template_fit"]["category"] == "C", rule
    assert change in rule["note"] or (change == "source" and "происхождение" in rule["note"])


@pytest.mark.parametrize("value,relation", [(10, "min"), (11, "max")],
                         ids=["saved-relation-conflict", "saved-value-conflict"])
def test_saved_conflict_remains_refused_without_repair(catalog, value, relation):
    doc, _ = _assemble(_candidate("max"))
    rule = doc["rules"][0]
    rule["params"] = [{"name": NAME, "value": value, "unit": "credit_hours",
                       "relation": relation, "as_written": f"{value} кредитных часов"}]
    before = copy.deepcopy(doc)
    assert m.validate_schema(doc) == []
    assert m.carrier_problems(doc)
    assert m.restore_param_relations(doc) == []
    with pytest.raises(ParamProjectionError):
        derive_params_from_landing(rule)
    assert doc == before


def test_current_legacy_catalogue_does_not_emit_the_auditors_two_carrier_shape(monkeypatch):
    # Auditor passport used 0.5.0. Do not turn that catalogue into a modern
    # fixture or attribute this reconstructed response to the auditor's LLM.
    root_catalog = Path(__file__).resolve().parents[1] / "catalog"
    monkeypatch.setenv(cb.ENV_CATALOG, str(root_catalog))
    cb.forget()
    try:
        assert cb.catalog().version() == "0.5.0"
        doc, rejected = _assemble(_candidate("min"))
        rule = doc["rules"][0]
        print(json.dumps({"legacy_catalogue_fit": rule["template_fit"],
                          "params": rule.get("params"), "rejected": rejected}))
        assert rule["template_fit"]["category"] == "C", rule
        assert m.carrier_problems(doc) == []
        assert not (rule["template_fit"].get("family") == FAMILY and rule.get("params"))
    finally:
        cb.forget()


def _post_assembly_pair(relation):
    # Both states are outputs of the actual assembler. In particular, the
    # first C + legacy param is not made by altering a saved healthy model.
    first_response = _candidate(relation)
    del first_response["template_fit"]
    first, _ = _assemble(first_response)
    second, _ = _assemble(_candidate("max"))
    left, right = first["rules"][0], second["rules"][0]
    assert left["template_fit"]["category"] == "C"
    assert left["template_fit"]["gap"] == "модель не выполнила посадку"
    assert left["params"][0]["relation"] == relation
    assert m.validate_param(left["params"][0], "SOFT") == []
    assert right["template_fit"]["category"] == "A"
    assert left["id"] == right["id"]
    assert m.carrier_problems(first) == m.carrier_problems(second) == []
    assert m.validate_template_fit(first) == m.validate_template_fit(second) == []
    return first, second


def _adopt_after_assembly(first, second, route):
    other_before = copy.deepcopy(second)
    row = first["rules"][0]
    if route == "second-reading":
        out = second_reading.adopt_stronger_fits(first, second, {("S1", "п.1"): CLAUSE})
        accepted = any(item["row"] == row["id"] for item in out)
    else:
        # The exact internal-slot receiver used by repair; this does not
        # claim its balance/hint scheduler was exercised or needed a call.
        slots = repair._slots_for(row, False, m.template_families())
        assert any(slot["name"] == "fit_from_second" and slot["internal"] for slot in slots)
        reply = {"quote": QUOTE, "fit": second["rules"][0]["template_fit"]}
        out = repair._apply(row, False, "fit_from_second", reply, CLAUSE, first)
        accepted = out is None
    assert second == other_before
    return accepted, out


@pytest.mark.parametrize("route", ["repair-slot", "second-reading"])
@pytest.mark.parametrize("case", ["equal", "relation-conflict", "unproved-value", "unproved-source"])
def test_post_assembly_adoption_checks_the_whole_destination_carrier(catalog, route, case):
    first, second = _post_assembly_pair("min" if case == "relation-conflict" else "max")
    # The damaged proposal is a negative receiver input; neither source
    # document nor a previously accepted first model is repaired for it.
    cell = second["rules"][0]["template_fit"]["params"][NAME]
    if case == "unproved-value":
        cell["value"] = 11
    elif case == "unproved-source":
        cell["source"] = {"kind": "code", "note": "untrusted claim of migration"}
    before = copy.deepcopy(first)
    accepted, outcome = _adopt_after_assembly(first, second, route)
    divergences = m.carrier_problems(first)
    assert not divergences, {
        "route": route, "case": case, "accepted": accepted,
        "outcome": outcome, "new_divergences": divergences,
    }
    if case == "equal":
        assert accepted, outcome
        assert m.validate_schema(first) == m.validate_template_fit(first) == []
        rule = first["rules"][0]
        assert rule["template_fit"]["params"][NAME]["relation"] == "max"
        p, = derive_params_from_landing(rule)
        assert (p["value"], p["unit"], p["relation"]) == (10, "credit_hours", "max")
    else:
        assert not accepted, outcome
        assert first == before  # refusal must not leave a half-adopted fit
