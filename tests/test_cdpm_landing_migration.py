"""Legacy proposal is evidence-preserving, atomic and not an execution grant."""
import copy
import json

import pytest

from cdpm import catalog_bridge, landing_migration as migration, model as m
from test_cdpm_landing_v2_reader import package
from test_k01_family_manifest import setup_case
from test_k01_param_set_reference import expanded
from test_k01_semantic_transition import clean_bridge, other_unit


def legacy(tmp_path, monkeypatch):
    # Only an unrelated unit changes: distinct release fingerprints, same
    # days_off_floor record and meaning. The old origin is identifiable.
    doc, cat = setup_case(tmp_path, monkeypatch, edit=other_unit)
    rule = doc["rules"][-1]
    rule["statement"] = "Выходных в месяц не менее 8."
    doc["rules"], doc["questions"] = [rule], []
    fit = rule["template_fit"]
    for key in ("schema_version", "family_contract"):
        fit.pop(key, None)
    for key in ("unit", "relation", "condition", "source"):
        fit["params"]["min_days_off"].pop(key, None)
    assert m.validate_schema(doc) == []
    return doc, rule, catalog_bridge.catalog()


def test_shared_fingerprint_cannot_invent_an_original_release_version(tmp_path, monkeypatch):
    doc, rule, declared = package(tmp_path, monkeypatch)
    fit = rule["template_fit"]
    assert fit["family_contract"]["catalog_fingerprint"] == declared["catalog"]["fingerprint"]
    del fit["family_contract"]
    del fit["schema_version"]
    fit["params"]["min_days_off"].pop("condition")
    before = copy.deepcopy(rule)
    result = migration.prepare_rule(rule, catalog_bridge.catalog(), document=doc)
    assert result["state"] == "unconfirmed"
    assert "нескольких выпусков" in result["words"][0]
    assert result["rule"] == rule == before


def test_upgrade_has_original_pin_and_code_metadata_and_leaves_input_untouched(tmp_path, monkeypatch):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    before = copy.deepcopy(rule)
    result = migration.prepare_rule(rule, cat, document=doc)
    assert result["state"] == "confirmed", result["words"]
    fit = result["rule"]["template_fit"]
    assert fit["schema_version"] == "landing/2"
    assert fit["family_contract"]["catalog_version"] == "0.7.0"
    assert fit["registry_rev"] == before["template_fit"]["registry_rev"]
    assert fit["family_contract"]["catalog_fingerprint"] != cat.fingerprint()
    cell = fit["params"]["min_days_off"]
    assert cell["value"] == 8 and cell["quote"] == "8"
    assert (cell["unit"], cell["relation"], cell["condition"]) == ("count", "min", None)
    assert cell["source"]["kind"] == "code"
    assert result["changes"][0]["source"]["kind"] == "code"
    assert rule == before
    again = migration.prepare_rule(result["rule"], cat, document=doc)
    assert again["state"] == "unchanged" and again["rule"] == result["rule"]


@pytest.mark.parametrize("mutation", ["unknown-origin", "missing-origin", "pending", "bad-source",
                                      "bad-relation", "bad-unit", "free-condition", "extra-field",
                                      "value-vs-quote"])
def test_failed_upgrade_keeps_the_entire_original_rule(tmp_path, monkeypatch, mutation):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    fit, cell = rule["template_fit"], rule["template_fit"]["params"]["min_days_off"]
    if mutation == "unknown-origin": fit["registry_rev"] = "unknown"
    elif mutation == "missing-origin": del fit["registry_rev"]
    elif mutation == "pending": cell["pending_revision"] = "Q-existing-R"
    elif mutation == "bad-source": cell["source"] = []
    elif mutation == "bad-relation": cell["relation"] = "max"
    elif mutation == "bad-unit": cell["unit"] = "килограмм"
    elif mutation == "free-condition": cell["condition"] = "в некоторых случаях"
    elif mutation == "value-vs-quote": cell["value"] = 80
    else: fit["invented"] = "bad"
    before = copy.deepcopy(rule)
    result = migration.prepare_rule(rule, cat, document=doc)
    assert result["state"] in ("required", "unconfirmed"), result
    assert result["words"] and result["changes"] == []
    assert result["rule"] == rule == before


def test_changed_record_is_not_confirmed_by_restamping(tmp_path, monkeypatch):
    doc, cat = setup_case(tmp_path, monkeypatch, edit=expanded)
    rule = doc["rules"][-1]
    original = copy.deepcopy(rule)
    result = migration.prepare_rule(rule, cat, document=doc)
    assert result["state"] == "required"
    assert "содержание исходной записи" in result["words"][0]
    assert result["rule"] == rule == original


@pytest.mark.parametrize("version", ["landing/2", "landing/3", None])
def test_explicit_modern_or_unknown_shape_is_never_legacy_repaired(tmp_path, monkeypatch, version):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    rule["template_fit"]["schema_version"] = version
    before = copy.deepcopy(rule)
    result = migration.prepare_rule(rule, cat, document=doc)
    assert result["state"] == "unchanged"
    assert result["rule"] == rule == before
    assert result["changes"] == []


def test_existing_rule_source_is_preserved_when_metadata_is_added(tmp_path, monkeypatch):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    source = {"kind": "rule", "rule_id": rule["id"]}
    rule["template_fit"]["params"]["min_days_off"]["source"] = copy.deepcopy(source)
    result = migration.prepare_rule(rule, cat, document=doc)
    assert result["state"] == "confirmed", result["words"]
    assert result["rule"]["template_fit"]["params"]["min_days_off"]["source"] == source
    assert "source" not in result["changes"][0]["fields"]


def test_legacy_answer_cannot_be_disguised_as_code_source(tmp_path, monkeypatch):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    rule["template_fit"]["params"]["min_days_off"]["answer"] = "Q-old"
    before = copy.deepcopy(rule)
    result = migration.prepare_rule(rule, cat, document=doc)
    assert result["state"] == "unconfirmed"
    assert result["rule"] == before


def test_params_only_uses_original_names_and_verifies_units(tmp_path, monkeypatch):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    rule["template_fit"]["params"] = {}
    rule["params"] = [{"name": "min_days_off", "value": 8, "unit": "count",
                       "relation": "min", "as_written": "8"}]
    before = copy.deepcopy(rule)
    result = migration.prepare_rule(rule, cat, document=doc)
    assert result["state"] == "confirmed", result["words"]
    assert result["rule"]["template_fit"]["params"]["min_days_off"]["value"] == 8
    assert result["rule"]["params"] == before["params"]
    assert rule == before


def test_interrupted_document_proposal_preserves_bytes_and_retry_is_identical(tmp_path, monkeypatch):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    other = copy.deepcopy(rule)
    other["id"] = "R-OTHER"
    doc["rules"].append(other)
    path = tmp_path / "legacy-model.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    raw, original = path.read_bytes(), copy.deepcopy(doc)
    healthy = migration.prepare_document(doc, cat)
    assert [item["state"] for item in healthy["items"]] == ["confirmed", "confirmed"]
    real_prepare = migration.prepare_rule
    calls = []

    def interrupted(rule, *args, **kwargs):
        calls.append(rule["id"])
        if len(calls) == 2:
            raise RuntimeError("simulated interruption after first proposal")
        return real_prepare(rule, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(migration, "prepare_rule", interrupted)
        with pytest.raises(RuntimeError, match="simulated interruption"):
            migration.prepare_document(doc, cat)
    assert doc == original and path.read_bytes() == raw
    assert migration.prepare_document(json.loads(raw), cat) == healthy
    assert m.validate_schema(healthy["model"]) == m.validate_model(healthy["model"]) == []


def test_document_proposal_reports_unconfirmed_rule_without_losing_its_value(tmp_path, monkeypatch):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    rule["template_fit"]["registry_rev"] = "unknown"
    original = copy.deepcopy(doc)
    result = migration.prepare_document(doc, cat)
    assert result["model"] == doc == original
    assert result["items"][0]["state"] == "unconfirmed"
    assert result["items"][0]["rule"] == rule["id"]
    assert result["items"][0]["words"]


def answered_legacy(tmp_path, monkeypatch):
    from cdpm import answers
    from test_cdpm_a05_cell_writer import _doc, PARAM
    setup_case(tmp_path, monkeypatch, edit=other_unit)
    doc, rule, question = _doc()
    assert answers.apply_answers(doc, {question["id"]: "10 часов"})["applied"] == 1
    assert m.validate_schema(doc) == m.validate_model(doc) == []
    return doc, rule, question, PARAM, catalog_bridge.catalog()


def test_answer_source_survives_migration_without_becoming_code(tmp_path, monkeypatch):
    from cdpm import provenance
    doc, rule, question, name, cat = answered_legacy(tmp_path, monkeypatch)
    original = copy.deepcopy(doc)
    source = provenance.answer_source(question, name, "10 часов")
    result = migration.prepare_document(doc, cat)
    assert result["items"][0]["state"] == "confirmed", result["items"]
    cell = result["model"]["rules"][0]["template_fit"]["params"][name]
    assert cell["source"] == source
    assert cell["answer"] == question["id"]
    assert (cell["value"], cell["unit"], cell["relation"], cell["condition"]) == (
        600, "duration_minutes", "min", None)
    change = next(c for c in result["items"][0]["changes"] if c["param"] == name)
    assert change["source"]["kind"] == "code" and "source" not in change["fields"]
    assert result["model"]["questions"] == original["questions"]
    assert m.validate_schema(result["model"]) == m.validate_model(result["model"]) == []
    assert provenance.package_problems(result["model"]) == []
    assert doc == original


@pytest.mark.parametrize("mutation", ["question", "version", "effect", "root", "quote", "value", "code-source"])
def test_answer_migration_refuses_forged_origin_without_repair(tmp_path, monkeypatch, mutation):
    doc, rule, question, name, cat = answered_legacy(tmp_path, monkeypatch)
    cell = rule["template_fit"]["params"][name]
    if mutation == "question": cell["source"]["question"]["fingerprint"] = "forged"
    elif mutation == "version": cell["source"]["version"]["fingerprint"] = "forged"
    elif mutation == "effect": cell["source"]["effect"]["rule"] = "another-parameter"
    elif mutation == "root": cell["source"]["root"] = "another-question"
    elif mutation == "quote": cell["quote"] = "11 часов"
    elif mutation == "value": cell["value"] = 660
    else: cell["source"] = {"kind": "code", "note": "forged legacy answer origin"}
    before = copy.deepcopy(doc)
    result = migration.prepare_rule(rule, cat, document=doc)
    assert result["state"] == "unconfirmed", result
    assert result["changes"] == [] and result["words"]
    assert result["rule"] == before["rules"][0] and doc == before


def test_answer_migration_requires_document_and_preserves_open_revision(tmp_path, monkeypatch):
    from cdpm import answers
    doc, rule, question, name, cat = answered_legacy(tmp_path, monkeypatch)
    before = copy.deepcopy(rule)
    result = migration.prepare_rule(rule, cat)
    assert result["state"] == "unconfirmed" and result["rule"] == before
    opened = answers.open_revision(doc, question["id"])
    before = copy.deepcopy(doc)
    revision = next(q for q in doc["questions"] if q["id"] == opened["created"])
    assert rule["id"] in revision["revision_cells"]["targets"]
    assert name not in rule["template_fit"]["params"]
    result = migration.prepare_document(doc, cat)
    assert result["items"][0]["state"] == "unconfirmed"
    assert result["model"] == before == doc
    assert answers.apply_answers(result["model"], {opened["created"]: "9 часов"})["applied"] == 1
    accepted = result["model"]["rules"][0]["template_fit"]["params"][name]
    assert accepted["value"] == 540
    assert accepted["source"]["question"]["id"] == opened["created"]
    # Closed revision history must not make a model permanently unmigratable.
    after = migration.prepare_document(result["model"], cat)
    assert after["items"][0]["state"] == "confirmed", after["items"]
    assert after["model"]["rules"][0]["template_fit"]["params"][name]["source"] == accepted["source"]


def test_model_load_persists_migration_evidence_only_on_explicit_save(tmp_path, monkeypatch):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    original = copy.deepcopy(doc)
    path = tmp_path / "source.json"
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    loaded = m.load_model(path)
    assert loaded == m.loads_model(raw)
    assert loaded["rules"][0]["template_fit"]["schema_version"] == "landing/2"
    event, = loaded["meta"]["landing_migrations"]
    assert event["schema_version"] == "landing-migration/1"
    assert event["input_fingerprint"] != event["output_fingerprint"]
    assert event["catalog"]["fingerprint"] == cat.fingerprint()
    assert event["items"][0]["state"] == "confirmed"
    assert m.validate_schema(loaded) == m.validate_model(loaded) == []
    assert path.read_bytes() == raw and doc == original
    saved = tmp_path / "saved.json"
    m.dump_model(loaded, saved)
    saved_bytes = saved.read_bytes()
    assert m.load_model(saved) == loaded
    assert saved.read_bytes() == saved_bytes and path.read_bytes() == raw


def test_unconfirmed_load_keeps_values_and_does_not_repeat_journal(tmp_path, monkeypatch):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    rule["template_fit"]["registry_rev"] = "unknown"
    before = copy.deepcopy(doc)
    loaded = m.loads_model(json.dumps(doc))
    assert loaded["rules"] == before["rules"]
    event, = loaded["meta"]["landing_migrations"]
    assert event["items"][0]["state"] == "unconfirmed"
    assert event["input_fingerprint"] == event["output_fingerprint"]
    assert m.loads_model(json.dumps(loaded)) == loaded
    assert doc == before


@pytest.mark.parametrize("family,name,value,unit,relation,quote,clause", [
    ("fleet_exclusions", "fleet_types", ["Airbus A320", "B737"], "code_list", None,
     "Airbus A320, B737", "Запрещены типы самолётов Airbus A320, B737."),
    ("reserve_floors", "reserve_crews", {"ST1": 1, "ST2": 2}, "count", "min",
     "ST1:1,ST2:2", "Резерв экипажей по базам ST1:1,ST2:2."),
])
@pytest.mark.parametrize("path", ["migration", "new_candidate"])
def test_structured_values_never_enter_numeric_converter(
        tmp_path, monkeypatch, family, name, value, unit, relation, quote, clause, path):
    from test_cdpm_landing_v2_projection import typed_rule
    from template_catalog import units
    doc, rule = typed_rule(tmp_path, monkeypatch, family, {
        name: {"value": value, "unit": unit, "relation": relation, "condition": None, "quote": quote}})
    rule["statement"] = clause
    fit = rule["template_fit"]
    del fit["schema_version"]
    for field in ("unit", "relation", "condition", "source"):
        del fit["params"][name][field]
    if path == "new_candidate":
        del fit["family_contract"]
        del fit["registry_rev"]
    original = copy.deepcopy(rule)
    doc_before = copy.deepcopy(doc)
    real = units.to_canonical

    def numbers_only(candidate, *args, **kwargs):
        assert not isinstance(candidate, (dict, list, bool)), "structured value sent to numeric converter"
        return real(candidate, *args, **kwargs)

    monkeypatch.setattr(units, "to_canonical", numbers_only)
    if path == "migration":
        result = migration.prepare_rule(rule, catalog_bridge.catalog(), document=doc)
        assert result["state"] == "confirmed", result["words"]
        got = result["rule"]["template_fit"]
    else:
        got, _confidence, note = m.accept_fit_candidate(rule, clause, new_output=True)
        assert got["category"] == "A", note
    assert got["schema_version"] == "landing/2"
    assert got["params"][name]["value"] == value
    assert rule == original
    assert doc == doc_before


def test_forged_historical_green_never_confirms_a_current_legacy_rule(tmp_path, monkeypatch):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    forged = m.loads_model(json.dumps(doc))["meta"]["landing_migrations"]
    assert forged[0]["items"][0]["state"] == "confirmed"
    doc["meta"]["landing_migrations"] = copy.deepcopy(forged)
    rule["template_fit"]["registry_rev"] = "unknown"
    loaded = m.loads_model(json.dumps(doc))
    assert loaded["rules"] == doc["rules"]
    assert loaded["meta"]["landing_migrations"][0] == forged[0]
    assert loaded["meta"]["landing_migrations"][-1]["items"][0]["state"] == "unconfirmed"
    assert "schema_version" not in loaded["rules"][0]["template_fit"]


@pytest.mark.parametrize("path", ["migration", "new_candidate"])
@pytest.mark.parametrize("family,name,raw,expected,unit,relation", [
    ("reserve_floors", "reserve_crews", "ST1:1,ST2:2", {"ST1": 1, "ST2": 2}, "count", "min"),
    ("fleet_exclusions", "fleet_types", "Airbus A320, B737-800", ["Airbus A320", "B737-800"], "code_list", None),
    ("fleet_exclusions", "fleet_types", "Ту-204, Élan / 2", ["Ту-204", "Élan / 2"], "code_list", None),
    ("reserve_floors", "reserve_crews", "ST1:1,ST1:2", None, "count", "min"),
    ("reserve_floors", "reserve_crews", "ST1:1,ST1:1", None, "count", "min"),
    ("reserve_floors", "reserve_crews", "ST1:-1", None, "count", "min"),
    ("reserve_floors", "reserve_crews", "ST1:1.5", None, "count", "min"),
    ("fleet_exclusions", "fleet_types", "Airbus A320,,B737", None, "code_list", None),
    ("fleet_exclusions", "fleet_types", "Airbus A320, Airbus A320", None, "code_list", None),
])
def test_legacy_structural_strings_upgrade_without_losing_boundaries(
        tmp_path, monkeypatch, path, family, name, raw, expected, unit, relation):
    from test_cdpm_landing_v2_projection import typed_rule
    doc, rule = typed_rule(tmp_path, monkeypatch, family, {
        name: {"value": raw, "unit": unit, "relation": relation, "condition": None, "quote": raw}})
    clause = "Значение параметра: " + raw + "."
    rule["statement"] = clause
    fit = rule["template_fit"]
    del fit["schema_version"]
    for field in ("unit", "relation", "condition", "source"):
        del fit["params"][name][field]
    if path == "new_candidate":
        del fit["family_contract"]
        del fit["registry_rev"]
    before = copy.deepcopy(doc)
    if path == "migration":
        result = migration.prepare_rule(rule, catalog_bridge.catalog(), document=doc)
        if expected is None:
            assert result["state"] in ("required", "unconfirmed")
            assert result["rule"] == rule and result["changes"] == []
        else:
            assert result["state"] == "confirmed", result["words"]
            got = result["rule"]["template_fit"]
            assert "value" in result["changes"][0]["fields"]
            assert got["family_contract"] == fit["family_contract"]
    else:
        got, _confidence, note = m.accept_fit_candidate(rule, clause, new_output=True)
        assert got["category"] == ("C" if expected is None else "A"), note
    if expected is not None:
        assert got["params"][name]["value"] == expected
        assert got["params"][name]["quote"] == raw
        assert got["schema_version"] == "landing/2"
    assert doc == before


@pytest.mark.parametrize("history", [None, {}, [{"schema_version": "landing-migration/99"}]])
def test_invalid_existing_journal_is_not_silently_rewritten(tmp_path, monkeypatch, history):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    doc["meta"]["landing_migrations"] = history
    raw = json.dumps(doc)
    with pytest.raises(ValueError, match="история миграции"):
        m.loads_model(raw)
    assert json.dumps(doc) == raw


def test_parameter_view_exposes_journal_without_mutating_or_granting_execution(tmp_path, monkeypatch):
    from cdpm import parameter_review
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    doc = m.loads_model(json.dumps(doc))
    before = copy.deepcopy(doc)
    view = parameter_review.from_model(doc)
    assert view["landing_migrations"] == before["meta"]["landing_migrations"]
    assert view["rules"][0]["params"][0]["value"] == 8
    view["landing_migrations"][0]["items"][0]["state"] = "forged"
    assert doc == before


def test_invalid_rule_identity_is_a_read_refusal_not_an_unhandled_schema_error(tmp_path, monkeypatch):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    rule["id"] = 7
    raw = json.dumps(doc)
    with pytest.raises(ValueError, match="результат миграции"):
        m.loads_model(raw)
    assert json.dumps(doc) == raw


def test_params_without_known_family_remain_unconfirmed_without_guessing(tmp_path, monkeypatch):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    del rule["template_fit"]
    rule["params"] = [{"name": "min_days_off", "value": 8, "unit": "count", "relation": "min"}]
    before = copy.deepcopy(rule)
    loaded = m.loads_model(json.dumps(doc))
    assert loaded["rules"][0] == before
    assert "template_fit" not in loaded["rules"][0]
    assert loaded["meta"]["landing_migrations"][-1]["items"][0]["state"] == "unconfirmed"
    assert m.loads_model(json.dumps(loaded)) == loaded
