"""Actual modern catalogue landing at the consumer registry, no execution."""
import copy
import json
from pathlib import Path

import pytest

from cdpm import catalog_bridge
from template_catalog import landing
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge
from vmp import registry_check as rc


def modern_rule(tmp_path, monkeypatch):
    _doc, rule = typed_rule(tmp_path, monkeypatch, "duty_extension_penalty", {
        "duty_extension_cap": {"value": 960, "unit": "duration_minutes", "relation": "max",
                               "condition": None, "quote": "960 минут"},
        "extension_bases": {"value": ["ST1", "ST2"], "unit": "code_list", "relation": "in",
                            "condition": None, "quote": "ST1, ST2"},
        "extension_consent": {"value": True, "unit": "flag", "relation": "yes",
                              "condition": None, "quote": "с согласия"},
    })
    cat = catalog_bridge.catalog()
    raw = json.loads((Path(__file__).resolve().parents[1] / "vmp" / "bindings.json").read_text(encoding="utf-8"))
    raw.update(catalog_version=cat.version(), catalog_fingerprint=cat.fingerprint())
    local = tmp_path / "registry-bindings.json"
    local.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("CREW_COSMOS_BINDINGS", str(local))
    assert not landing.problems(rule, cat)
    return rule


def test_registry_judges_typed_conditions_in_their_pinned_family(tmp_path, monkeypatch):
    rule = modern_rule(tmp_path, monkeypatch)
    original = copy.deepcopy(rule)
    assert rc.fit_gap(rule) is None
    assert rc.deep_check([rule], None, {"rules": []}) == []
    assert rule == original


@pytest.mark.parametrize("change,words", [
    ("unit-missing", "unit"), ("unit-wrong", "unit"),
    ("boolean-is-number", "JSON true/false"), ("source-wrong", "source"),
    ("foreign-cell", "landing-contract"), ("pin-wrong", "family_contract"),
    ("unknown-version", "landing-version"), ("missing-assumption", "допущени"),
])
def test_registry_modern_path_is_not_a_shape_or_pin_bypass(tmp_path, monkeypatch, change, words):
    rule = modern_rule(tmp_path, monkeypatch)
    fit = rule["template_fit"]
    if change == "unit-missing": del fit["params"]["extension_bases"]["unit"]
    elif change == "unit-wrong": fit["params"]["extension_bases"]["unit"] = "flag"
    elif change == "boolean-is-number": fit["params"]["extension_consent"]["value"] = 1
    elif change == "source-wrong": fit["params"]["extension_bases"]["source"]["rule_id"] = "FOREIGN"
    elif change == "foreign-cell": fit["params"]["foreign_cell"] = copy.deepcopy(fit["params"]["extension_bases"])
    elif change == "pin-wrong": fit["family_contract"]["record_fingerprint"] = "0" * 64
    elif change == "unknown-version": fit["schema_version"] = "landing/3"
    else: fit["category"] = "B"
    before = copy.deepcopy(rule)
    gap = rc.fit_gap(rule)
    assert gap and gap["kind"] == "fit_invalid"
    assert words in gap["note"]
    assert rule == before


def test_valid_modern_landing_does_not_cancel_an_executor_gap(tmp_path, monkeypatch):
    rule = modern_rule(tmp_path, monkeypatch)
    registry = {"rules": [{"rule_id": rule["id"], "verdict": "family_unknown",
                           "gap_note": "no executor for this family"}]}
    gaps = rc.deep_check([rule], None, registry)
    assert len(gaps) == 1 and gaps[0]["kind"] == "rule_family_unknown"
    assert gaps[0]["note"] == "no executor for this family"
