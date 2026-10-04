"""Tests for the tier-1 rules-config core (rules_config.py) and its wiring
into the colgen fingerprint and the trajectory validator.

Contract under test:
  - no env / no file -> DEFAULTS, exactly the six documented values
    (hard reproducibility contract);
  - an existing but malformed / unknown-key / non-positive config raises
    ValueError (loud failure);
  - space_fingerprint is stable under defaults and CHANGES when the rule
    set changes (the rule set is part of the problem identity);
  - the validator constants are really read from the config (behavior
    changes under a reloaded module with a different config).
"""
from __future__ import annotations

import importlib
import json
import sys
from collections import defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import rules_config

# The documented rule values — restated literally on purpose: if someone
# edits rules_config.DEFAULTS, this test must fail loudly. Extended
# 2026-07-19 (multirole increment 4): three crew-composition rules, all OFF
# (0) by default — the no-config behavior stays byte-for-byte identical.
DOCUMENTED_DEFAULTS = {
    "min_sit_min": 30,
    "max_landings_per_duty": 5,
    "max_duty_span_min": 840,
    "min_rest_min": 600,
    "max_monthly_block_min": 5100,
    "max_pairing_span_min": 5760,
    "comp_no_two_reserves": 0,
    "comp_min_pair_seniority_weight": 0,
    "comp_language_mix": 0,
}


@pytest.fixture(autouse=True)
def _clean_rules_env(monkeypatch):
    """Every test starts and ends with no config env and a cold cache; any
    module reloaded under a custom env is re-reloaded under defaults so
    the rest of the battery sees the historical behavior."""
    monkeypatch.delenv(rules_config.ENV_VAR, raising=False)
    rules_config.reset_cache()
    yield
    monkeypatch.delenv(rules_config.ENV_VAR, raising=False)
    rules_config.reset_cache()
    for name in ("_gerad_trajectory_check", "_gerad_colgen"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])


def _write_cfg(tmp_path: Path, params: dict, webapp_layout: bool = True,
               name: str = "rules.json") -> Path:
    p = tmp_path / name
    body = {"parameters": params} if webapp_layout else params
    p.write_text(json.dumps(body), encoding="utf-8")
    return p


# --- (a) defaults contract -------------------------------------------------

def test_defaults_contract_no_env():
    assert rules_config.effective() == DOCUMENTED_DEFAULTS
    assert rules_config.is_default() is True


def test_missing_file_falls_back_to_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv(rules_config.ENV_VAR,
                       str(tmp_path / "does_not_exist.json"))
    rules_config.reset_cache()
    assert rules_config.effective() == DOCUMENTED_DEFAULTS
    assert rules_config.is_default() is True


def test_both_layouts_accepted(monkeypatch, tmp_path):
    for layout in (True, False):
        cfg = _write_cfg(tmp_path, {"min_sit_min": 45},
                         webapp_layout=layout, name=f"l{layout}.json")
        monkeypatch.setenv(rules_config.ENV_VAR, str(cfg))
        rules_config.reset_cache()
        eff = rules_config.effective()
        assert eff["min_sit_min"] == 45
        # untouched keys keep their defaults
        assert eff["min_rest_min"] == 600
        assert rules_config.is_default() is False


# --- (b) loud failures -----------------------------------------------------

def test_malformed_json_raises(monkeypatch, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setenv(rules_config.ENV_VAR, str(p))
    rules_config.reset_cache()
    with pytest.raises(ValueError, match="malformed"):
        rules_config.load()
    with pytest.raises(ValueError):
        rules_config.effective()


def test_unknown_key_raises(monkeypatch, tmp_path):
    cfg = _write_cfg(tmp_path, {"min_sit_min": 30, "min_sit_minutes": 30})
    monkeypatch.setenv(rules_config.ENV_VAR, str(cfg))
    rules_config.reset_cache()
    with pytest.raises(ValueError, match="unknown keys"):
        rules_config.load()


@pytest.mark.parametrize("bad", [0, -5, "30", None, True])
def test_non_positive_or_non_numeric_raises(monkeypatch, tmp_path, bad):
    cfg = _write_cfg(tmp_path, {"min_sit_min": bad},
                     name=f"bad_{type(bad).__name__}_{bad}.json")
    monkeypatch.setenv(rules_config.ENV_VAR, str(cfg))
    rules_config.reset_cache()
    with pytest.raises(ValueError, match="positive number"):
        rules_config.load()


def test_non_object_top_level_raises(monkeypatch, tmp_path):
    p = tmp_path / "list.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setenv(rules_config.ENV_VAR, str(p))
    rules_config.reset_cache()
    with pytest.raises(ValueError, match="JSON object"):
        rules_config.load()


# --- (c) fingerprint identity ----------------------------------------------

def _synthetic_fingerprint(gc):
    legs = [
        gc.Leg(0, "F001", 0, 60, "BASE1", "AAA", 60, "737"),
        gc.Leg(1, "F002", 120, 180, "AAA", "BASE1", 60, "737"),
    ]
    dump = {"bases": ["BASE1", "BASE2", "BASE3"], "pricer": "duty",
            "max_legs": "natural", "ip_model": "set_covering"}
    return gc.space_fingerprint(legs, dump)


def test_fingerprint_stable_under_defaults_and_changes_with_rules(
        monkeypatch, tmp_path):
    gc = importlib.import_module("_gerad_colgen")
    gc = importlib.reload(gc)          # ensure default-config constants
    fp_default = _synthetic_fingerprint(gc)
    assert fp_default == _synthetic_fingerprint(gc)  # deterministic

    cfg = _write_cfg(tmp_path, {"min_sit_min": 45})
    monkeypatch.setenv(rules_config.ENV_VAR, str(cfg))
    rules_config.reset_cache()
    gc = importlib.reload(gc)
    assert gc.MIN_GROUND_TIME_MIN == 45  # wiring, not just the hash
    fp_45 = _synthetic_fingerprint(gc)
    assert fp_45 != fp_default

    # and back: defaults reproduce the original fingerprint exactly
    monkeypatch.delenv(rules_config.ENV_VAR)
    rules_config.reset_cache()
    gc = importlib.reload(gc)
    assert _synthetic_fingerprint(gc) == fp_default


# --- (d) behavior wiring in the trajectory validator ------------------------

# One employee, one pairing, continuous route, 20-min gap between legs:
# a "sit<30" violation under defaults, legal under min_sit_min=15.
_SEQ = [
    (0, 100, "BASE1", "AAA", False, "P1"),
    (120, 200, "AAA", "BASE1", False, "P1"),   # gap = 20 min
]


def test_trajectory_validator_reads_config(monkeypatch, tmp_path):
    tc = importlib.import_module("_gerad_trajectory_check")
    tc = importlib.reload(tc)   # defaults: MIN_SIT = 30
    v = defaultdict(int)
    tc.check_employee_sequence(list(_SEQ), v)
    assert v["sit<30"] == 1

    cfg = _write_cfg(tmp_path, {"min_sit_min": 15})
    monkeypatch.setenv(rules_config.ENV_VAR, str(cfg))
    rules_config.reset_cache()
    tc = importlib.reload(tc)
    assert tc.MIN_SIT == 15
    v = defaultdict(int)
    tc.check_employee_sequence(list(_SEQ), v)
    assert v["sit<30"] == 0
    assert sum(v.values()) == 0
