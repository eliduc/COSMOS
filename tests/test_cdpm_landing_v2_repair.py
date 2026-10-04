"""New repair/adoption output obeys the extraction law, not archive rules."""
import copy
import json

import pytest

from cdpm import model as m, repair, second_reading
from test_cdpm_landing_v2_candidate import CLAUSE, case
from test_cdpm_landing_v2_parameterless import case as parameterless_case
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge


def rejected():
    return {"category": "C", "registry_rev": m.registry_fingerprint(),
            "gap": "посадка модели отброшена: семья 'days_off_floor' со словарными параметрами — params обязателен"}


def apply_new(doc, row, route, clause, value=8, quote="8"):
    fit = {"category": "A", "family": "days_off_floor",
           "params": {"min_days_off": {"value": value, "quote": quote}}}
    if route == "second_reading":
        other = copy.deepcopy(doc)
        other["rules"][-1]["template_fit"] = fit
        source = row["sources"][0]
        before = copy.deepcopy(other)
        result = second_reading.adopt_stronger_fits(
            doc, other, {(source["sid"], source["anchor"]): clause})
        assert other == before
        return any(item["row"] == row.get("id") for item in result)
    slot = "param:min_days_off" if route == "param" else "fit_from_second"
    reply = {"value": value, "quote": quote, "fit": fit}
    before = copy.deepcopy(reply)
    error = repair._apply(row, False, slot, reply, clause, doc)
    assert reply == before
    return error is None


@pytest.mark.parametrize("route", ["param", "fit_from_second", "second_reading"])
@pytest.mark.parametrize("bad", [None, "value", "clipped", "unit", "id"])
def test_new_repair_output_is_typed_and_bound_to_exact_clause(tmp_path, monkeypatch, route, bad):
    doc, row = case(tmp_path, monkeypatch)
    row["template_fit"] = rejected()
    clause = "Выходных в месяц не менее 80." if bad == "clipped" else (
        "Выходных в месяц не менее 8 часов." if bad == "unit" else CLAUSE)
    row["statement"] = clause
    if bad == "id":
        del row["id"]
    before = copy.deepcopy(doc)
    accepted = apply_new(doc, row, route, clause, 80 if bad == "value" else 8)
    if bad:
        assert not accepted, (route, bad, row)
        assert doc == before
        return
    assert accepted
    fit = row["template_fit"]
    assert fit["schema_version"] == "landing/2"
    assert fit["family_contract"]["catalog_version"] == "0.7.1"
    assert fit["params"]["min_days_off"] == {
        "value": 8, "quote": "8", "unit": "count", "relation": "min", "condition": None,
        "source": {"kind": "rule", "rule_id": row["id"]}}
    assert "landing_migrations" not in doc["meta"]
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []


def test_parameterless_repair_emits_empty_typed_cells_with_quote(tmp_path, monkeypatch):
    doc, row = parameterless_case(tmp_path, monkeypatch)
    clause = "Начисляется штраф за каждую ночёвку."
    quote = "штраф за каждую ночёвку"
    row["statement"] = clause
    row["template_fit"] = {"category": "C", "registry_rev": m.registry_fingerprint(),
        "gap": "посадка модели отброшена: семья 'layover_penalty' — обязателен params либо quote"}
    error = repair._apply(row, False, "fit_quote:layover_penalty", {"quote": quote}, clause, doc)
    assert error is None, error
    fit = row["template_fit"]
    assert fit["schema_version"] == "landing/2"
    assert fit["params"] == {} and fit["quote"] == quote
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []


@pytest.mark.parametrize("route", ["candidate", "fit_from_second", "second_reading"])
@pytest.mark.parametrize("bad", ["value", "clipped", "unit"])
def test_explicit_typed_candidate_cannot_bypass_clause_evidence(tmp_path, monkeypatch, route, bad):
    doc, row = case(tmp_path, monkeypatch)
    clause = "Выходных в месяц не менее 80." if bad == "clipped" else (
        "Выходных в месяц не менее 8 часов." if bad == "unit" else CLAUSE)
    row["statement"] = clause
    if bad == "value":
        row["template_fit"]["params"]["min_days_off"]["value"] = 80
    typed = copy.deepcopy(row["template_fit"])
    if route == "candidate":
        before = copy.deepcopy(row)
        actual, _confidence, note = m.accept_fit_candidate(row, clause, new_output=True)
        assert actual["category"] == "C" and note
        assert row == before
        return
    other = copy.deepcopy(doc)
    row["template_fit"] = rejected()
    before = copy.deepcopy(doc)
    if route == "fit_from_second":
        error = repair._apply(row, False, route, {"quote": "8", "fit": typed}, clause, doc)
        assert error is not None
    else:
        src = row["sources"][0]
        assert second_reading.adopt_stronger_fits(
            doc, other, {(src["sid"], src["anchor"]): clause}) == []
    assert doc == before


@pytest.mark.parametrize("quote,valid", [
    ("с 22:00 до 06:00", True), ("22:00–06:00", True),
    ("с 22:900 до 06:00", False), ("с 24:00 до 06:00", False),
    ("с 22:00 до 06:00 и 07:00", False), ("с 22:00 до 06:00 (+60)", False),
])
def test_new_typed_output_keeps_both_clock_endpoints_of_one_range(tmp_path, monkeypatch, quote, valid):
    from cdpm import catalog_bridge
    clause = f"Ночные смены: окно {quote}."
    cells = {name: {"value": value, "quote": quote, "unit": "time_of_day_minutes",
                    "relation": relation, "condition": None}
             for name, value, relation in [("night_duty_start", 1320, "min"),
                                            ("night_duty_end", 360, "max")]}
    doc, row = typed_rule(tmp_path, monkeypatch, "night_series", cells)
    for cell in row["template_fit"]["params"].values():
        cell["tz"] = catalog_bridge.catalog().dictionaries()["param_tz"][0]
    row["statement"] = clause
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    before = copy.deepcopy(row)
    if valid:
        compatible, _, note = m.accept_fit_candidate(row, clause)
        assert compatible["category"] == "A", note
    actual, _, note = m.accept_fit_candidate(row, clause, new_output=True)
    assert row == before
    if valid:
        assert actual["category"] == "A", note
        assert actual == before["template_fit"]
    else:
        assert actual["category"] == "C" and note


@pytest.mark.parametrize("value", [8, 80])
@pytest.mark.parametrize("via_hint", [False, True])
def test_real_repair_loop_publishes_only_evidence_bound_typed_output(tmp_path, monkeypatch, value, via_hint):
    doc, row = case(tmp_path, monkeypatch)
    row["template_fit"] = rejected()
    src = row["sources"][0]
    entries = [{"sid": src["sid"], "clauses": [{"anchor": src["anchor"], "text": CLAUSE}]}]
    findings = [{"sid": src["sid"], "anchor": src["anchor"], "class": "number",
                 "value": 8, "text": "8", "rows": [row["id"]]}]
    calls = []

    def reply(prompt):
        calls.append(prompt)
        assert "param:min_days_off" in prompt
        return json.dumps({"slot": "param:min_days_off", "value": value, "quote": "8"})

    hints = {(src["sid"], src["anchor"]): [{"fit": {
        "category": "A", "family": "days_off_floor",
        "params": {"min_days_off": {"value": value, "quote": "8"}}}}]} if via_hint else None
    before = copy.deepcopy(doc)
    out = repair.repair(doc, entries, findings, reply, hints=hints, max_attempts=0 if via_hint else 1)
    assert len(calls) == (0 if via_hint else 1)
    if value != 8:
        assert out["fixed"] == [] and len(out["left"]) == 1
        assert doc == before
        return
    assert len(out["fixed"]) == 1 and out["left"] == []
    fit = row["template_fit"]
    assert fit["schema_version"] == "landing/2"
    assert fit["params"]["min_days_off"]["value"] == 8
    assert fit["params"]["min_days_off"]["source"] == {"kind": "rule", "rule_id": row["id"]}
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
