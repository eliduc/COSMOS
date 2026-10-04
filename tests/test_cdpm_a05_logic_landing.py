"""A05: logic readers see the same landing value before export; no live model."""
import copy

import pytest

from cdpm import model as m, logic_proof as lp
from test_cdpm_logic_proof import _rule_duration


def _landed():
    r = _rule_duration()
    r.pop("params")
    r["template_fit"] = {"category": "A", "family": "duty_limits",
                         "registry_rev": m.registry_fingerprint(),
                         "params": {"max_duty_duration": {
                             "value": 12, "unit": "ч", "relation": "max",
                             "quote": "12 часов"}}}
    return r


def test_logic_validation_accepts_landing_without_creating_legacy():
    r = _landed()
    before = copy.deepcopy(r)
    assert m.validate_logic(r["logic"], r) == []
    assert r == before and "params" not in r


def test_logic_retelling_preserves_the_same_canonical_number():
    r = _landed()
    before = copy.deepcopy(r)
    assert m.render_logic(r["logic"], r) == m.render_logic(_rule_duration()["logic"], _rule_duration())
    assert "12:00" in m.render_logic(r["logic"], r)
    assert r == before


def test_logic_certificate_spec_uses_landing_without_export():
    r = _landed()
    before = copy.deepcopy(r)
    expected, why = lp.logic_to_check_spec(_rule_duration())
    assert expected is not None, why
    actual, why = lp.logic_to_check_spec(r)
    assert actual == expected, why
    assert r == before


@pytest.mark.parametrize("change", [{"value": 600}, {"relation": "min"},
                                    {"qualifier": "только в базе"}])
def test_conflicting_carriers_never_become_a_logic_proof(change):
    r = _landed()
    r["params"] = copy.deepcopy(_rule_duration()["params"])
    r["params"][0].update(change)
    if "qualifier" in change:
        r["template_fit"]["params"]["max_duty_duration"]["condition"] = "вне базы"
    before = copy.deepcopy(r)
    errors = m.validate_logic(r["logic"], r)
    assert any("носител" in e for e in errors), errors
    spec, why = lp.logic_to_check_spec(r)
    assert spec is None and "носител" in why
    assert r == before


def test_bad_landing_does_not_silently_fall_back_to_legacy():
    r = _landed()
    r["template_fit"]["params"]["max_duty_duration"]["unit"] = "парсек"
    before = copy.deepcopy(r)
    assert m.validate_logic(r["logic"], r)
    spec, why = lp.logic_to_check_spec(r)
    assert spec is None and why
    assert r == before

