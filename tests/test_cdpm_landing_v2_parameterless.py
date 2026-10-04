"""Empty typed parameters are lawful only with the existing family law."""
import copy

from cdpm import family_params as fp, model as m
from cdpm.param_projection import derive_params_from_landing
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge


def case(tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, "layover_penalty", {})
    rule["kind"] = "SOFT"
    rule["template_fit"]["quote"] = "штраф за каждую ночёвку"
    return doc, rule


def test_parameterless_typed_family_is_valid_without_invented_cells(tmp_path, monkeypatch):
    doc, rule = case(tmp_path, monkeypatch)
    before = copy.deepcopy(doc)
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    assert derive_params_from_landing(rule) == []
    assert doc == before


def test_legacy_normalizer_must_not_remove_explicit_typed_empty_params(tmp_path, monkeypatch):
    _doc, rule = case(tmp_path, monkeypatch)
    fit = rule["template_fit"]
    before = copy.deepcopy(fit)
    normalized, notes = m.normalize_fit_shape(fit)
    assert normalized == before == fit and notes == []


def test_parameterless_typed_family_still_needs_its_quote(tmp_path, monkeypatch):
    doc, rule = case(tmp_path, monkeypatch)
    del rule["template_fit"]["quote"]
    assert any("quote" in p for p in m.validate_template_fit(doc))


def test_empty_params_do_not_make_a_parameterized_family_complete(tmp_path, monkeypatch):
    doc, rule = typed_rule(tmp_path, monkeypatch, "rest_floors", {})
    rule["kind"] = "HARD"
    rule["template_fit"]["quote"] = "не менее 12 часов отдыха"
    # landing/2's explicit container is well-formed; its empty content is
    # still a real K01 shortage, never readiness for either shared consumer.
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    state = fp.family_completeness(doc)["rest_floors"]
    assert state["verdict"] == fp.VERDICT_INCOMPLETE
    assert state["given"] == []
    assert state["missing"] == ["min_rest_home", "min_rest_outstation"]
    barriers = [p for p in m.readiness_barriers(doc)
                if p.get("code") == fp.FAMILY_INCOMPLETE
                and p.get("family") == "rest_floors"]
    assert len(barriers) == 2
    assert {p["param"] for p in barriers} == {"min_rest_home", "min_rest_outstation"}
    assert all(m.barrier_tasks(p) == {"PAIRING", "ROSTERING"} for p in barriers)
    matrix = m.readiness_matrix(doc)
    assert all(not cell["ready"] for task in ("pairing", "rostering")
               for cell in matrix[task].values())


def test_legacy_empty_params_normalization_is_unchanged():
    fit = {"category": "A", "family": "layover_penalty", "params": {}, "quote": "ночёвка"}
    normalized, _notes = m.normalize_fit_shape(fit)
    assert "params" not in normalized and fit["params"] == {}
