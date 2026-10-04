"""Local H0/H0B regressions: typed VMP and questionnaire provenance.

Real temporary catalog, real answer writer and real form converter. No
network, LLM, running service or execution permission is mocked into PASS.
"""
import copy
import json

import pytest

from cdpm import answers, model as m, provenance, vmp_form as vf
from test_cdpm_landing_v2_answer_writer import case
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge


def answered(tmp_path, monkeypatch):
    doc, rule, q = case(tmp_path, monkeypatch)
    assert answers.apply_answers(doc, {q["id"]: "600 минут"})["applied"] == 1
    rule["zone"] = "ROSTERING"
    doc["rules"] = [rule]
    doc["objectives"] = []
    return doc, rule, q


def row_of(form, rid):
    return next(row for block in (form["common"], form["pairing"], form["rostering"])
                for section in ("world", "hard", "soft")
                for row in block.get(section, []) if row["id"] == rid)


def test_answered_typed_rule_roundtrips_exactly_and_validates(tmp_path, monkeypatch):
    doc, rule, _q = answered(tmp_path, monkeypatch)
    original = copy.deepcopy(doc)
    form = vf.to_form(doc)
    back = vf.from_form(form)
    assert back["rules"][0]["template_fit"] == rule["template_fit"]
    assert provenance.package_problems(back) == []
    assert m.validate_template_fit(back) == []
    assert vf.validate_form(form, strict=True) == []
    assert vf.roundtrip_problems(doc) == []
    assert doc == original


@pytest.mark.parametrize("family,name,value,unit,relation,quote,zone", [
    ("fleet_exclusions", "fleet_types", ["Airbus A320", "Полярис-Р200"], "code_list", None,
     "Airbus A320, Полярис-Р200", "BOTH"),
    ("reserve_floors", "reserve_crews", {"ST1": 1, "ST2": 2}, "count", "min",
     "ST1:1,ST2:2", "ROSTERING"),
])
def test_structured_cells_are_valid_and_lossless(tmp_path, monkeypatch, family, name,
                                                value, unit, relation, quote, zone):
    doc, rule = typed_rule(tmp_path, monkeypatch, family, {
        name: {"value": value, "unit": unit, "relation": relation, "condition": None,
               "quote": quote}})
    rule.update(kind="HARD", zone=zone, statement=quote)
    doc.update(rules=[rule], questions=[], objectives=[])
    form = vf.to_form(doc)
    assert vf.validate_form(form, strict=True) == []
    assert vf.from_form(form)["rules"][0]["template_fit"] == rule["template_fit"]


@pytest.mark.parametrize("mutation", ["value", "quote", "source", "unit", "target", "version"])
def test_answer_mutations_do_not_get_a_form_pass(tmp_path, monkeypatch, mutation):
    doc, rule, _q = answered(tmp_path, monkeypatch)
    cell = rule["template_fit"]["params"]["min_rest_outstation"]
    if mutation == "value":
        cell["value"] = 601
    elif mutation == "quote":
        cell["quote"] = "601 минут"
    elif mutation == "source":
        cell["source"] = None
        cell.pop("answer", None)
    elif mutation == "unit":
        cell["unit"] = "count"
    elif mutation == "target":
        cell["source"]["effect"]["rule"] = "min_rest_home"
    else:
        cell["source"]["version"]["fingerprint"] = "0" * 12
    assert vf.validate_form(vf.to_form(doc), strict=True)


@pytest.mark.parametrize("field", ["unit", "relation", "condition", "source", "family_contract"])
def test_loss_detector_checks_machine_semantics(tmp_path, monkeypatch, field):
    doc, rule, _q = answered(tmp_path, monkeypatch)
    changed = copy.deepcopy(doc)
    fit = changed["rules"][0]["template_fit"]
    if field == "family_contract":
        fit[field]["record_fingerprint"] = "0" * 64
    else:
        fit["params"]["min_rest_outstation"].pop(field)
    assert vf.canonical_diff(doc, changed), field


@pytest.mark.parametrize("bad", [[], ["A", "A"], [1], [""], {"ST1": True},
                                 {"ST1": -1}, {"ST1": 1.5}, {"ST1": {"value": 1}}])
def test_structured_schema_rejects_invalid_shapes(tmp_path, monkeypatch, bad):
    doc, rule, _q = answered(tmp_path, monkeypatch)
    form = vf.to_form(doc)
    row_of(form, rule["id"])["params"][0]["value"] = bad
    assert vf.validate_form_schema(form)


def test_typed_numeric_param_rejects_structured_value(tmp_path, monkeypatch):
    doc, rule, _q = answered(tmp_path, monkeypatch)
    form = vf.to_form(doc)
    row_of(form, rule["id"])["params"][0]["value"] = ["720"]
    assert vf.validate_form(form)


def test_visible_edit_is_not_overridden_and_duplicate_parameter_refused(tmp_path, monkeypatch):
    doc, rule, _q = answered(tmp_path, monkeypatch)
    form = vf.to_form(doc)
    row = row_of(form, rule["id"])
    p = next(p for p in row["params"] if p["name"] == "min_rest_outstation")
    p["value"] = 601
    assert vf.from_form(form)["rules"][0]["template_fit"]["params"][p["name"]]["value"] == 601
    assert vf.validate_form(form)
    p["value"] = 600
    row["params"].append(copy.deepcopy(p))
    assert any("повтор" in words for words in vf.validate_form(form))


def test_explicit_empty_params_are_preserved(tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, "calendar_windows", {})
    rule.update(kind="HARD", zone="ROSTERING", statement="Учитывать календарные ограничения.")
    rule["template_fit"]["quote"] = rule["statement"]
    doc.update(rules=[rule], questions=[], objectives=[])
    assert vf.from_form(vf.to_form(doc))["rules"][0]["template_fit"] == rule["template_fit"]


def test_typed_family_conditions_use_typed_judge(tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, "duty_extension_penalty", {
        "duty_extension_cap": {"value": 840, "quote": "840 минут", "unit": "duration_minutes",
                               "relation": "max", "condition": None},
        "extension_bases": {"value": ["ST1", "ST2"], "quote": "ST1, ST2", "unit": "code_list",
                            "relation": "in", "condition": None},
        "extension_consent": {"value": True, "quote": "с согласия", "unit": "flag",
                              "relation": "yes", "condition": None}})
    rule.update(kind="SOFT", zone="PAIRING", weight=3,
                statement="Продление до 840 минут для ST1, ST2 с согласия экипажа.")
    from cdpm import catalog_bridge
    from template_catalog import family_contract
    hard = {"id": "R-CONNECTION", "title": "Стыковка", "kind": "HARD", "zone": "PAIRING",
            "statement": "Стыковка не менее 45 минут.", "sources": copy.deepcopy(rule["sources"])}
    cells = {"min_turnaround": {"value": 45, "quote": "45 минут", "unit": "duration_minutes",
                                "relation": "min", "condition": None,
                                "source": {"kind": "rule", "rule_id": hard["id"]}}}
    hard["template_fit"] = {"category": "A", "schema_version": "landing/2",
                            "family": "connection_windows", "params": cells,
                            "registry_rev": catalog_bridge.catalog().fingerprint(),
                            "family_contract": family_contract.snapshot(catalog_bridge.catalog(),
                                                                        "connection_windows", cells)}
    doc.update(rules=[rule, hard], questions=[], objectives=[])
    assert vf.validate_form(vf.to_form(doc)) == []


def test_structured_legacy_cell_is_not_authorized_by_schema_alone(tmp_path, monkeypatch):
    doc, rule, _q = answered(tmp_path, monkeypatch)
    rule["template_fit"].pop("schema_version")
    form = vf.to_form(doc)
    row_of(form, rule["id"])["params"][0]["value"] = ["720"]
    assert vf.validate_form(form)


def test_deployment_spec_selects_real_export_without_default_change(tmp_path, monkeypatch):
    from cdpm import export_task_package as tp
    from test_cdpm_landing_v2_export import real_soft_package
    monkeypatch.delenv("CDPM_EXPORT_SPEC_VERSION", raising=False)
    bundle = real_soft_package(tmp_path, monkeypatch, "1.18")
    run = bundle[4]
    original = (run / "extract/model.json").read_bytes()
    monkeypatch.setenv("CDPM_EXPORT_SPEC_VERSION", "1.21")
    configured = json.loads(tp.build_package(run)["manifest.json"])
    assert configured["spec_version"] == "1.21"
    explicit = json.loads(tp.build_package(run, spec_version="1.20")["manifest.json"])
    assert explicit["spec_version"] == "1.20"
    assert tp.SPEC_VERSION == "1.18"
    monkeypatch.setenv("CDPM_EXPORT_SPEC_VERSION", "nonsense")
    with pytest.raises(tp.TaskPackageError, match="версия"):
        tp.build_package(run)
    assert (run / "extract/model.json").read_bytes() == original
