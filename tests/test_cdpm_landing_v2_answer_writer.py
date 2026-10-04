"""Real answer/revision path must write the typed format, not legacy cells."""
import copy

from cdpm import answers, family_params as fp, model as m, provenance
from cdpm import export_task_package as tp
from cdpm.param_projection import derive_params_from_landing
from template_catalog import family_contract
from test_cdpm_a05_cell_writer import _doc, _legacy, FAMILY, PARAM, OTHER
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_family_manifest import setup_case
from test_k01_semantic_transition import clean_bridge


def case(tmp_path, monkeypatch):
    _, cat = setup_case(tmp_path, monkeypatch)
    doc, rule, q = _doc()
    fit = rule["template_fit"]
    fit["schema_version"] = "landing/2"
    fit["family_contract"] = family_contract.snapshot(cat, FAMILY, fit["params"])
    fit["params"][OTHER].update(unit="duration_minutes", relation="min", condition=None,
        source={"kind": "rule", "rule_id": rule["id"]})
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    return doc, rule, q


def test_real_answer_writes_typed_cell_and_exact_source(tmp_path, monkeypatch):
    doc, rule, q = case(tmp_path, monkeypatch)
    pin = copy.deepcopy(rule["template_fit"]["family_contract"])
    result = answers.apply_answers(doc, {q["id"]: "10 часов"})
    assert result["applied"] == 1, q.get("answer")
    cell = rule["template_fit"]["params"][PARAM]
    assert cell["value"] == 600 and cell["unit"] == "duration_minutes"
    assert cell["relation"] == "min" and cell["condition"] is None
    assert cell["source"] == provenance.answer_source(q, PARAM, "10 часов")
    assert rule["template_fit"]["family_contract"] == pin
    assert m.validate_schema(doc) == m.validate_model(doc) == []
    assert next(p for p in derive_params_from_landing(rule) if p["name"] == PARAM)["value"] == 600


def test_revision_keeps_own_source_and_pending_does_not_restore_old_answer(tmp_path, monkeypatch):
    doc, rule, q = case(tmp_path, monkeypatch)
    assert answers.apply_answers(doc, {q["id"]: "10 часов"})["applied"] == 1
    opened = answers.open_revision(doc, q["id"])
    rev = next(x for x in doc["questions"] if x["id"] == opened["created"])
    before = copy.deepcopy(rule)
    notes = []
    assert fp._reapply_values(doc, fp.family_coverage(doc), notes) == []
    assert rule == before
    assert answers.apply_answers(doc, {rev["id"]: "9 часов"})["applied"] == 1
    cell = rule["template_fit"]["params"][PARAM]
    assert cell["value"] == 540 and cell["condition"] is None
    assert cell["source"] == provenance.answer_source(rev, PARAM, "9 часов")
    assert "pending_revision" not in cell
    assert m.validate_schema(doc) == m.validate_model(doc) == []


def test_typed_answer_still_refuses_legacy_conflict_atomically(tmp_path, monkeypatch):
    doc, rule, q = case(tmp_path, monkeypatch)
    rule["params"] = _legacy(900)
    before = copy.deepcopy(rule)
    result = answers.apply_answers(doc, {q["id"]: "10 часов"})
    assert result["applied"] == 0 and rule == before
    assert "два носителя" in q["answer"]["note"]


def test_package_canon_does_not_replace_typed_null_relation(tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, "fleet_exclusions", {
        "fleet_types": {"value": ["Airbus A320", "B737"], "unit": "code_list",
                        "relation": None, "condition": None, "quote": "Airbus A320, B737"}})
    rule["params"] = derive_params_from_landing(rule)
    before = copy.deepcopy(rule)
    assert tp.canon_fit_params(rule) == 0
    assert rule == before
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []


def test_extracted_parameter_builds_typed_cell_from_exact_rule_quote(tmp_path, monkeypatch):
    from cdpm import extract
    doc, rule, _q = case(tmp_path, monkeypatch)
    rule["statement"] = "Отдых дома 12 часов, вне базы 10 часов."
    assert extract.merge_soft_params(rule, [{"name": PARAM, "value": 600,
        "relation": "min", "as_written": "10 часов"}]) is None
    cell = rule["template_fit"]["params"][PARAM]
    assert cell == {"value": 600, "unit": "duration_minutes", "relation": "min",
        "condition": None, "quote": "10 часов", "source": {"kind": "rule", "rule_id": rule["id"]}}
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []


def test_typed_cell_preparation_preserves_scope_and_explicit_wrong_fields(tmp_path, monkeypatch):
    from cdpm import catalog_bridge
    from template_catalog import landing
    _, rule = typed_rule(tmp_path, monkeypatch, "connection_windows", {
        "min_turnaround": {"value": 45, "unit": "duration_minutes", "relation": "min",
                           "condition": {"kind": "area", "value": "база"}, "quote": "45 минут"}})
    before = copy.deepcopy(rule)
    raw = {"value": 50, "quote": "50 минут", "source": {"kind": "rule", "rule_id": rule["id"]}}
    cell = landing.prepare_cell(rule, "min_turnaround", raw, catalog_bridge.catalog())
    assert cell["condition"] == {"kind": "area", "value": "база"}
    assert cell["unit"] == "duration_minutes" and cell["relation"] == "min"
    assert raw == {"value": 50, "quote": "50 минут", "source": {"kind": "rule", "rule_id": rule["id"]}}
    bad = landing.prepare_cell(rule, "min_turnaround", {**raw, "relation": "max"}, catalog_bridge.catalog())
    assert bad["relation"] == "max" and fp.write_cell(rule, "min_turnaround", bad)
    assert rule == before
