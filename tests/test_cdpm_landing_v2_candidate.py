"""Typed candidates keep their exact origin; legacy repair is not migration."""
import copy

import pytest

from cdpm import extract, model as m, repair, second_reading
from test_k01_family_manifest import setup_case
from test_k01_semantic_transition import clean_bridge
from test_cdpm_landing_v2_projection import typed_rule


CLAUSE = "Выходных в месяц не менее 8."


def case(tmp_path, monkeypatch):
    doc, cat = setup_case(tmp_path, monkeypatch)
    rule = doc["rules"][-1]
    rule["statement"] = CLAUSE
    fit = rule["template_fit"]
    fit["schema_version"] = "landing/2"
    fit["params"]["min_days_off"].update(unit="count", relation="min", condition=None,
        source={"kind": "rule", "rule_id": rule["id"]})
    assert fit["family_contract"]["catalog_version"] == "0.7.0"
    assert cat.version() == "0.7.1"
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    return doc, rule


def test_typed_candidate_preserves_origin_and_input(tmp_path, monkeypatch):
    _doc, rule = case(tmp_path, monkeypatch)
    before = copy.deepcopy(rule)
    fit, _confidence, note = m.accept_fit_candidate(rule, CLAUSE)
    assert fit["category"] == "A", note
    assert fit == before["template_fit"] and rule == before


@pytest.mark.parametrize("bad", [None, "value", "id", "source"])
def test_new_output_from_legacy_candidate_is_typed_and_evidence_bound(tmp_path, monkeypatch, bad):
    doc, rule = case(tmp_path, monkeypatch)
    rule["template_fit"] = {"category": "A", "family": "days_off_floor",
        "params": {"min_days_off": {"value": 8, "quote": "8"}}}
    if bad == "value": rule["template_fit"]["params"]["min_days_off"]["value"] = 80
    elif bad == "id": del rule["id"]
    elif bad == "source":
        rule["template_fit"]["params"]["min_days_off"]["source"] = {"kind": "code", "note": "claimed"}
    before = copy.deepcopy(rule)
    fit, _confidence, note = m.accept_fit_candidate(rule, CLAUSE, new_output=True)
    assert rule == before
    assert fit["schema_version"] == "landing/2"
    if bad:
        assert fit["category"] == "C" and note and "family_contract" not in fit
    else:
        assert note is None and fit["category"] == "A"
        cell = fit["params"]["min_days_off"]
        assert cell == {"value": 8, "quote": "8", "unit": "count", "relation": "min",
                        "condition": None, "source": {"kind": "rule", "rule_id": rule["id"]}}
        assert fit["family_contract"]["catalog_version"] == "0.7.1"
        rule["template_fit"] = fit
        assert m.validate_schema(doc) == m.validate_template_fit(doc) == []


def test_standard_assembler_emits_typed_legacy_candidate_without_migration_history(tmp_path, monkeypatch):
    doc, rule = case(tmp_path, monkeypatch)
    sid, anchor = "S1", "п.1"
    rid = m.stable_rule_id(sid, anchor, 0)
    candidate = {"id": "MODEL-CANNOT-ASSIGN-ID", "anchor": anchor, "title": "Выходные",
        "statement": CLAUSE, "zone": "PAIRING", "kind": "HARD",
        "template_fit": {"category": "A", "family": "days_off_floor",
            "params": {"min_days_off": {"value": 8, "quote": "8"}}}}
    asm = extract._Assembler("Тест", doc["sources"])
    job = {"sid": sid, "known": {anchor}, "listing": f"[{anchor}] {CLAUSE}", "texts": {anchor: CLAUSE}}
    extract._assemble(asm, [job], [{"candidates": [candidate]}], [], {}, {})
    actual = asm.doc["rules"][0]
    assert actual["id"] == rid
    assert actual["template_fit"]["schema_version"] == "landing/2"
    assert actual["template_fit"]["params"]["min_days_off"]["source"] == {"kind": "rule", "rule_id": rid}
    assert "landing_migrations" not in asm.doc["meta"]
    assert m.validate_schema(asm.doc) == m.validate_template_fit(asm.doc) == []


@pytest.mark.parametrize("clause,quote,value", [
    ("Выходных в месяц не менее 80.", "8", 8),
    ("Выходных в месяц не менее 8.5.", "8", 8),
    ("Выходных в месяц не менее 8 часов.", "8", 8),
])
def test_new_output_cannot_clip_number_or_unit_from_clause(tmp_path, monkeypatch, clause, quote, value):
    _doc, rule = case(tmp_path, monkeypatch)
    rule["statement"] = clause
    rule["template_fit"] = {"category": "A", "family": "days_off_floor",
        "params": {"min_days_off": {"value": value, "quote": quote}}}
    before = copy.deepcopy(rule)
    fit, _confidence, note = m.accept_fit_candidate(rule, clause, new_output=True)
    assert rule == before
    assert fit["schema_version"] == "landing/2" and fit["category"] == "C"
    assert note and "цитата" in note


@pytest.mark.parametrize("category", ["C", "D"])
@pytest.mark.parametrize("bad_field,value", [
    (None, None), ("family_contract", "pin"), ("family_contract", None),
    ("registry_rev", 123), ("registry_rev", None), ("registry_rev", ""),
    ("registry_rev", " "), ("registry_rev", "missing"),
])
def test_typed_unresolved_candidates_cannot_escape_header_validation(
        tmp_path, monkeypatch, category, bad_field, value):
    doc, rule = case(tmp_path, monkeypatch)
    pin = copy.deepcopy(rule["template_fit"]["family_contract"])
    candidate = {"schema_version": "landing/2", "category": category,
                 "registry_rev": m.registry_fingerprint()}
    if category == "C": candidate["gap"] = "не удалось выбрать семью"
    else: candidate["candidates"] = ["days_off_floor", "rest_floors"]
    rule["template_fit"] = candidate
    if bad_field:
        if value == "missing": del candidate[bad_field]
        else: candidate[bad_field] = pin if value == "pin" else value
    before = copy.deepcopy(rule)
    fit, _confidence, note = m.accept_fit_candidate(rule, CLAUSE)
    assert rule == before
    if bad_field:
        assert note and bad_field in note
        assert fit["category"] == "C" and "family_contract" not in fit
    else:
        assert note is None and fit == candidate
    rule["template_fit"] = fit
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []


@pytest.mark.parametrize("change", ["unit", "condition", "quote", "source", "pin", "extra", "answer"])
def test_invalid_typed_candidate_is_not_healed(tmp_path, monkeypatch, change):
    _doc, rule = case(tmp_path, monkeypatch)
    fit = rule["template_fit"]
    cell = fit["params"]["min_days_off"]
    if change == "unit": cell["unit"] = "duration_minutes"
    elif change == "condition": del cell["condition"]
    elif change == "quote": cell["quote"] = "не из клаузы"
    elif change == "source": cell["source"]["rule_id"] = "R-OTHER"
    elif change == "pin": fit["registry_rev"] = "0" * 64
    elif change == "extra": fit["unknown"] = True
    elif change == "answer": cell["answer"] = "Q-UNPROVEN"
    before = copy.deepcopy(rule)
    accepted, _confidence, note = m.accept_fit_candidate(rule, CLAUSE)
    assert accepted["category"] == "C" and note and rule == before


def test_repair_second_fit_uses_carrier_identity_and_preserves_pin(tmp_path, monkeypatch):
    doc, rule = case(tmp_path, monkeypatch)
    fit = copy.deepcopy(rule["template_fit"])
    rule["template_fit"] = {"category": "C", "gap": "модель не выполнила посадку",
                             "registry_rev": m.registry_fingerprint()}
    error = repair._apply(rule, False, "fit_from_second", {"quote": "8", "fit": fit}, CLAUSE, doc)
    assert error is None, error
    assert rule["template_fit"] == fit


def test_second_reading_preserves_original_typed_pin(tmp_path, monkeypatch):
    doc2, rule2 = case(tmp_path, monkeypatch)
    doc1 = copy.deepcopy(doc2)
    rule1 = doc1["rules"][-1]
    rule1["template_fit"] = {"category": "C", "gap": "модель не выполнила посадку",
                              "registry_rev": m.registry_fingerprint()}
    source = rule2["sources"][0]
    before = copy.deepcopy(doc2)
    adopted = second_reading.adopt_stronger_fits(doc1, doc2, {(source["sid"], source["anchor"]): CLAUSE})
    assert any(row["row"] == rule1["id"] for row in adopted)
    assert rule1["template_fit"] == rule2["template_fit"] and doc2 == before
    rule1["template_fit"]["params"]["min_days_off"]["value"] = 9
    assert doc2 == before  # no shared nested cell after adoption


def test_real_assembler_supplies_exact_identity_without_consuming_rejected_row(tmp_path, monkeypatch):
    doc, rule = case(tmp_path, monkeypatch)
    sid, anchor = "S1", "п.1"
    rid = m.stable_rule_id(sid, anchor, 0)
    fit = copy.deepcopy(rule["template_fit"])
    fit["params"]["min_days_off"]["source"]["rule_id"] = rid
    candidate = {"id": "MODEL-CANNOT-ASSIGN-ID", "anchor": anchor, "title": "Выходные",
                 "statement": CLAUSE, "zone": "PAIRING", "kind": "HARD", "template_fit": fit}
    asm = extract._Assembler("Тест", doc["sources"])
    rejected = []
    job = {"sid": sid, "known": {anchor}, "listing": f"[{anchor}] {CLAUSE}", "texts": {anchor: CLAUSE}}
    extract._assemble(asm, [job], [{"candidates": [{**candidate, "title": ""}, candidate]}], rejected, {}, {})
    assert len(asm.doc["rules"]) == 1 and len(rejected) == 1
    actual = asm.doc["rules"][0]
    assert actual["id"] == rid and actual["template_fit"] == fit
    assert m.validate_schema(asm.doc) == m.validate_template_fit(asm.doc) == []


@pytest.mark.parametrize("missing", ["id", "clause"])
def test_typed_candidate_requires_actual_carrier_and_clause(tmp_path, monkeypatch, missing):
    _doc, rule = case(tmp_path, monkeypatch)
    if missing == "id": del rule["id"]
    accepted, _confidence, note = m.accept_fit_candidate(rule, "" if missing == "clause" else CLAUSE)
    assert accepted["category"] == "C" and "правила-носителя" in note


def test_candidate_cannot_claim_code_migration_source(tmp_path, monkeypatch):
    _doc, rule = case(tmp_path, monkeypatch)
    rule["template_fit"]["params"]["min_days_off"]["source"] = {"kind": "code", "note": "claimed"}
    accepted, _confidence, note = m.accept_fit_candidate(rule, CLAUSE)
    assert accepted["category"] == "C" and "происхождение" in note


@pytest.mark.parametrize("family,cells,clause", [
    ("fleet_exclusions", {"fleet_types": {
        "value": ["Airbus A320", "B737"], "unit": "code_list", "relation": None,
        "condition": None, "quote": "Airbus A320, B737"}},
     "Запрещены типы самолётов Airbus A320, B737."),
    ("connection_windows", {"min_turnaround": {
        "value": 45, "unit": "duration_minutes", "relation": "min",
        "condition": {"kind": "area", "value": "база"}, "quote": "45 минут"}},
     "Разворот в базе не менее 45 минут."),
    ("reserve_floors", {"reserve_crews": {
        "value": {"ST1": 1, "ST2": 2}, "unit": "count", "relation": "min",
        "condition": None, "quote": "ST1:1,ST2:2"}},
     "Резерв экипажей по базам ST1:1,ST2:2."),
    ("duty_extension_penalty", {
        "duty_extension_cap": {"value": 960, "unit": "duration_minutes",
            "relation": "max", "condition": None, "quote": "960 минут"},
        "extension_bases": {"value": ["ST1", "ST2"], "unit": "code_list",
            "relation": "in", "condition": None, "quote": "ST1, ST2"},
        "extension_consent": {"value": True, "unit": "flag",
            "relation": "yes", "condition": None, "quote": "с согласия"}},
     "Продление смены не более 960 минут для ST1, ST2 только с согласия экипажа."),
])
@pytest.mark.parametrize("new_output", [False, True])
def test_typed_candidate_keeps_structured_values_scope_and_conditions(
        tmp_path, monkeypatch, family, cells, clause, new_output):
    doc, rule = typed_rule(tmp_path, monkeypatch, family, cells)
    rule["statement"] = clause
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    before = copy.deepcopy(rule)
    accepted, _confidence, note = m.accept_fit_candidate(rule, clause, new_output=new_output)
    assert accepted["category"] == "A", note
    assert accepted == before["template_fit"] and rule == before
