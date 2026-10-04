"""Z33: reach the alias scope guard through the real strict projection.

These are reader-boundary witnesses, not evidence that a whole package passes
execution gates. The repository draft is read-only. Optional mutation removes
only the post-projection guard in memory; no projector or catalogue is mocked.
"""
import ast
import copy
import inspect
import os
from pathlib import Path
import textwrap

import pytest

from cdpm import catalog_bridge as cb, model
from template_catalog import family_contract, landing
from vmp import chain_price as cp, pairing_input as pi
from webapp.threshold_gate import ThresholdGateError, projected_params


@pytest.fixture(autouse=True)
def optional_guard_mutation(monkeypatch):
    mode = os.environ.get("CDPM_Z33_MUTANT")
    if not mode:
        return
    assert mode == "remove_post_projection_guard", "unknown mutation requested"
    source = textwrap.dedent(inspect.getsource(cp.extension_alias_params))
    tree = ast.parse(source)
    candidates = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Call)
        and isinstance(node.test.func, ast.Name)
        and node.test.func.id == "any"
        and "условный long_duty_threshold" in ast.get_source_segment(source, node)
    ]
    assert len(candidates) == 1, "guard identity changed: do not guess a mutation"
    target = candidates[0]
    assert len(target.body) == 1 and isinstance(target.body[0], ast.Raise)
    assert not target.orelse
    guard_source = ast.get_source_segment(source, target)
    assert 'p.get("condition")' in guard_source
    assert 'p.get("qualifier")' in guard_source
    assert "for p in projected" in guard_source

    class RemoveGuard(ast.NodeTransformer):
        def visit_If(self, node):
            if node is target:
                return None
            return self.generic_visit(node)

    mutated = RemoveGuard().visit(tree)
    ast.fix_missing_locations(mutated)
    # Keep the actual import/call to projected_params and its exception path.
    calls = [node for node in ast.walk(mutated) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "projected_params"]
    assert len(calls) == 1
    namespace = {}
    exec(compile(mutated, "<Z33-remove-post-projection-guard>", "exec"),
         cp.__dict__, namespace)
    monkeypatch.setattr(cp, "extension_alias_params", namespace["extension_alias_params"])
    print("Z33_MUTANT: removed only the post-projection alias scope guard")


@pytest.fixture
def mixed_rule(monkeypatch):
    folder = Path(__file__).resolve().parents[1] / "catalog-integration/0.7.0-draft.2"
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    cb.forget()
    try:
        cat = cb.catalog()
        # The modern carrier owns the cap; the compatible old carrier owns
        # the alias. No second alias cell exists to trigger carrier divergence.
        cells = {"duty_extension_cap": {
            "value": 780, "unit": "duration_minutes", "relation": "max",
            "condition": None, "quote": "780 минут",
            "source": {"kind": "rule", "rule_id": "S-Z33"},
        }}
        rule = {
            "id": "S-Z33", "kind": "SOFT", "zone": "PAIRING", "weight": 15,
            "statement": "Базовый предел 720 минут, продление до 780 минут.",
            "params": [{"name": "long_duty_threshold", "value": 720,
                        "unit": "duration_minutes", "relation": "max",
                        "as_written": "720 минут"}],
            "template_fit": {
                "schema_version": "landing/2", "category": "A",
                "family": "duty_extension_penalty", "params": cells,
                "registry_rev": cat.fingerprint(),
                "family_contract": family_contract.snapshot(
                    cat, "duty_extension_penalty", cells),
            },
        }
        assert landing.problems(rule, cat) == []
        yield rule
    finally:
        cb.forget()


def assert_real_projection_reaches_alias(rule, qualifier):
    before = copy.deepcopy(rule)
    assert landing.problems(rule, cb.catalog()) == []
    assert model.carrier_problems({"rules": [rule]}) == []
    assert model.validate_param(rule["params"][0], "SOFT") == []
    projected = projected_params(rule)
    aliases = [p for p in projected if p["name"] == "long_duty_threshold"]
    assert len(aliases) == 1
    assert aliases[0]["value"] == 720
    assert aliases[0].get("qualifier") == qualifier
    assert next(p for p in projected if p["name"] == "duty_extension_cap")["value"] == 780
    assert rule == before


def consume(rule, route):
    if route == "direct":
        return cp.extension_alias_params(rule)
    penalties = pi.pairing_soft_penalties({"model": {"rules": [rule]}})
    assert len(penalties) == 1
    assert penalties[0]["params"]["duty_extension_cap"] == 780
    return {"long_duty_threshold": penalties[0]["params"]["long_duty_threshold"]}


@pytest.mark.parametrize("route", ["direct", "pairing"])
def test_unconditional_mixed_carrier_alias_is_preserved(mixed_rule, route):
    before = copy.deepcopy(mixed_rule)
    assert_real_projection_reaches_alias(mixed_rule, None)
    assert consume(mixed_rule, route) == {"long_duty_threshold": 720}
    assert mixed_rule == before


@pytest.mark.parametrize("route", ["direct", "pairing"])
@pytest.mark.parametrize("qualifier", ["база", "вне базы"])
def test_qualified_legacy_only_alias_reaches_guard_and_is_refused(mixed_rule, route, qualifier):
    mixed_rule["params"][0]["qualifier"] = qualifier
    before = copy.deepcopy(mixed_rule)
    assert_real_projection_reaches_alias(mixed_rule, qualifier)
    error = cp.ChainPriceError if route == "direct" else pi.PairingInputError
    with pytest.raises(error, match="условный long_duty_threshold"):
        consume(mixed_rule, route)
    assert mixed_rule == before


@pytest.mark.parametrize("damage", ["registry", "source", "condition"])
def test_mixed_carrier_still_requires_real_strict_landing(mixed_rule, damage):
    fit = mixed_rule["template_fit"]
    if damage == "registry":
        fit["registry_rev"] = "0" * 64
    elif damage == "source":
        fit["params"]["duty_extension_cap"]["source"]["rule_id"] = "FOREIGN"
    else:
        fit["params"]["duty_extension_cap"]["condition"] = {"kind": "area", "value": "база"}
    before = copy.deepcopy(mixed_rule)
    assert landing.problems(mixed_rule, cb.catalog())
    with pytest.raises(ThresholdGateError):
        projected_params(mixed_rule)
    with pytest.raises(cp.ChainPriceError):
        cp.extension_alias_params(mixed_rule)
    with pytest.raises(pi.PairingInputError):
        pi.pairing_soft_penalties({"model": {"rules": [mixed_rule]}})
    assert mixed_rule == before
