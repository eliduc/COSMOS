"""K11 adjacent SOFT alias reader: strict typed pins, healthy legacy path.

Pure in-process readers and repository draft catalogue, no solver or service.
"""
import copy
from pathlib import Path

import pytest

from cdpm import catalog_bridge as cb
from template_catalog import family_contract, landing
from vmp import chain_price as cp, pairing_input as pi


@pytest.fixture
def typed_rule(monkeypatch):
    folder = Path(__file__).resolve().parents[1] / "catalog-integration/0.7.0-draft.2"
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    cb.forget()
    cat = cb.catalog()
    cells = {
        name: {"value": value, "unit": "duration_minutes", "relation": "max",
               "condition": None, "quote": f"{value} минут",
               "source": {"kind": "rule", "rule_id": "S-K11"}}
        for name, value in {"long_duty_threshold": 720, "duty_extension_cap": 780}.items()
    }
    rule = {"id": "S-K11", "kind": "SOFT", "zone": "PAIRING", "weight": 15,
            "statement": "Базовый предел 720 минут, продление до 780 минут.",
            "template_fit": {"schema_version": "landing/2", "category": "A",
                "family": "duty_extension_penalty", "params": cells,
                "registry_rev": cat.fingerprint(),
                "family_contract": family_contract.snapshot(cat, "duty_extension_penalty", cells)}}
    assert landing.problems(rule, cat) == []
    yield rule
    cb.forget()


def test_healthy_typed_and_legacy_alias_reach_pairing_without_mutation(typed_rule):
    for rule in (typed_rule, {"id": "S-OLD", "kind": "SOFT", "zone": "PAIRING",
                             "weight": 15, "template_fit": {"family": "duty_extension_penalty",
                             "params": {"long_duty_threshold": {"value": 720}}}}):
        before = copy.deepcopy(rule)
        assert cp.extension_alias_params(rule) == {"long_duty_threshold": 720}
        assert pi.pairing_soft_penalties({"model": {"rules": [rule]}})[0]["params"]["long_duty_threshold"] == 720
        assert rule == before


@pytest.mark.parametrize("damage", ["registry", "record", "source", "condition"])
def test_invalid_typed_alias_never_becomes_pairing_penalty(typed_rule, damage):
    fit = typed_rule["template_fit"]
    if damage == "registry":
        fit["registry_rev"] = "0" * 64
    elif damage == "record":
        fit["family_contract"]["record_fingerprint"] = "0" * 64
    elif damage == "source":
        fit["params"]["long_duty_threshold"]["source"]["rule_id"] = "FOREIGN"
    else:
        fit["params"]["long_duty_threshold"]["condition"] = {"kind": "area", "value": "база"}
    before = copy.deepcopy(typed_rule)
    with pytest.raises(cp.ChainPriceError):
        cp.extension_alias_params(typed_rule)
    with pytest.raises(pi.PairingInputError):
        pi.pairing_soft_penalties({"model": {"rules": [typed_rule]}})
    assert typed_rule == before
