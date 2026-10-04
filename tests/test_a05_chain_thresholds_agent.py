"""Direct typed threshold extraction, not package admission or chain building.

The catalogue/pins are real, isolated synthetic fixtures. Expected thresholds
are literal test data, not a projection returned by the implementation. No
gate, readiness, translation, chain construction or operational solver runs.
"""
import copy

import pytest

from cdpm import catalog_bridge as cb, model as m
from template_catalog import family_contract
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from webapp import chain_seed as cs, threshold_gate as tg


READERS = (cs.thresholds_from_package, cs.colgen_thresholds_from_package)


def _cell(value, relation="min", area=None):
    return {"value": value, "unit": "duration_minutes", "relation": relation,
            "condition": None if area is None else {"kind": "area", "value": area},
            "quote": f"{value} минут"}


@pytest.fixture
def packages(tmp_path, monkeypatch):
    rows = [
        ("R-SIT-BASE", "connection_windows", {"min_turnaround": _cell(35, area="база")}),
        ("R-SIT-OUT", "connection_windows", {"min_turnaround": _cell(50, area="вне базы")}),
        ("R-DUTY", "duty_limits", {"max_duty_duration": _cell(720, "max")}),
        ("R-DUTY-STRICT", "duty_limits", {"max_duty_duration": _cell(615, "max")}),
        ("R-REST", "rest_floors", {"min_rest_home": _cell(705),
                                   "min_rest_outstation": _cell(585)}),
        ("R-BLOCK", "span_accumulators", {"max_monthly_block": _cell(5400, "max")}),
        ("R-BLOCK-STRICT", "span_accumulators", {"max_monthly_block": _cell(5100, "max")}),
    ]
    doc, prototype = typed_rule(tmp_path, monkeypatch, rows[0][1], rows[0][2])
    cat = cb.catalog()
    rules, legacy_rules = [], []
    for rid, family, cells in rows:
        rule = copy.deepcopy(prototype)
        rule.update(id=rid, kind="HARD", zone="PAIRING", title=family,
                    statement="; ".join(f"{name}: {cell['quote']}"
                                        for name, cell in cells.items()))
        rule.pop("params", None)
        fit = rule["template_fit"]
        fit.update(family=family,
                   family_contract=family_contract.snapshot(cat, family, cells),
                   params=copy.deepcopy(cells))
        for cell in fit["params"].values():
            cell["source"] = {"kind": "rule", "rule_id": rid}
        rules.append(rule)
        # A separate legacy representation of the literal inputs, without
        # calling derive_params_from_landing to manufacture the comparison.
        legacy = copy.deepcopy(rule)
        legacy.pop("template_fit")
        legacy["params"] = [
            {"name": name, "value": cell["value"], "unit": "duration_minutes",
             "relation": cell["relation"], "as_written": cell["quote"],
             **({"qualifier": cell["condition"]["value"]} if cell["condition"] else {})}
            for name, cell in cells.items()]
        legacy_rules.append(legacy)
    doc.update(rules=rules, questions=[], objectives=[])
    legacy_doc = copy.deepcopy(doc)
    legacy_doc["rules"] = legacy_rules
    for model in (doc, legacy_doc):
        assert m.validate_schema(model) == []
        assert m.validate_template_fit(model) == []
    assert all("params" not in rule for rule in doc["rules"])
    return {"model": doc}, {"model": legacy_doc}


def test_typed_and_legacy_extractors_keep_the_stricter_minimum_and_maximum(packages):
    expected_chain = {"sit_min": 50, "max_duty_min": 615,
                      "rest_min": 705, "max_block_min": 5100}
    expected_colgen = {"sit_min": 50, "max_duty_min": 615,
                       "rest_base_min": 705, "rest_out_min": 585,
                       "max_block_min": 5100}
    for pkg in packages:
        before = copy.deepcopy(pkg)
        assert cs.thresholds_from_package(pkg) == expected_chain
        assert pkg == before
        assert cs.colgen_thresholds_from_package(pkg) == expected_colgen
        assert pkg == before


def test_missing_landing_threshold_is_not_replaced_with_a_default(packages):
    pkg = packages[0]
    pkg["model"]["rules"] = [rule for rule in pkg["model"]["rules"]
                              if rule["template_fit"]["family"] != "span_accumulators"]
    assert m.validate_schema(pkg["model"]) == m.validate_template_fit(pkg["model"]) == []
    before = copy.deepcopy(pkg)
    for reader in READERS:
        with pytest.raises(RuntimeError, match="нет параметра max_monthly_block"):
            reader(pkg)
        assert pkg == before


def test_divergent_legacy_carrier_cannot_hide_behind_a_stricter_rule(packages):
    pkg = packages[0]
    rule = next(rule for rule in pkg["model"]["rules"] if rule["id"] == "R-DUTY")
    rule["params"] = [{"name": "max_duty_duration", "value": 780,
                       "unit": "duration_minutes", "relation": "max",
                       "as_written": "780 минут"}]
    # Structurally valid, but deliberately inconsistent carriers. A stricter
    # independent 615 cap must not let either public extractor accept it.
    assert m.validate_schema(pkg["model"]) == []
    before = copy.deepcopy(pkg)
    for reader in READERS:
        with pytest.raises(tg.ThresholdGateError) as caught:
            reader(pkg)
        assert "R-DUTY" in str(caught.value)
        assert "max_duty_duration" in str(caught.value)
        assert pkg == before
