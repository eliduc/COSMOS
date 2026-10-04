# -*- coding: utf-8 -*-
"""Месячный предел налёта из документа постановки (ревизия v2 свода,
решение владельца 2026-08-12).

Контракт: ключ `monthly_flight_time_limit_min` в машинной форме свода →
поле CosRules → персональные `flightTimeLimitMin` сотрудников на этапе 2
(решатель применяет их вместо глобального порога; доказано матрицей на
живом решателе). Документ без ключа не меняет набор ни байтом; документ
не перетирает персональный лимит из данных.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import _cos_rules  # noqa: E402
from _gerad_stage2_roster import apply_monthly_limit  # noqa: E402


# --- контракт CosRules -------------------------------------------------

def test_default_is_none():
    assert _cos_rules.CosRules().monthly_flight_time_limit_min is None


def test_roundtrip_snapshot():
    r = _cos_rules.CosRules(monthly_flight_time_limit_min=6000)
    d = r.as_dict()
    assert d["monthly_flight_time_limit_min"] == 6000
    back = _cos_rules.from_dict(d)
    assert back.monthly_flight_time_limit_min == 6000
    assert "monthly_flight_time_limit_min" in back.source_keys_read


def test_snapshot_without_key_stays_none():
    d = _cos_rules.CosRules().as_dict()
    d.pop("monthly_flight_time_limit_min")
    back = _cos_rules.from_dict(d)
    assert back.monthly_flight_time_limit_min is None
    assert ("monthly_flight_time_limit_min"
            in back.source_keys_missing)


def test_unknown_key_still_refused():
    d = _cos_rules.CosRules().as_dict()
    d["monthly_flight_time_limit_mim"] = 6000          # опечатка
    d.pop("monthly_flight_time_limit_min")
    with pytest.raises(_cos_rules.SourceRulesError):
        _cos_rules.from_dict(d)


def test_from_markdown_machine_block():
    doc = (
        "# Постановка\n\n"
        + _cos_rules.MACHINE_SECTION + "\n\n"
        "```json\n"
        '{"min_sit_min": 40, "monthly_flight_time_limit_min": 6000}\n'
        "```\n")
    r = _cos_rules.from_markdown(doc)
    assert r.monthly_flight_time_limit_min == 6000
    assert r.min_sit_min == 40


# --- проводка в этап 2 -------------------------------------------------

def _emps(*recs):
    return {"employees": [dict(r) for r in recs]}


def test_apply_sets_missing_only():
    p = _emps({"id": "E1"}, {"id": "E2", "flightTimeLimitMin": 4800})
    n = apply_monthly_limit(p, 6000)
    assert n == 1
    assert p["employees"][0]["flightTimeLimitMin"] == 6000
    # персональный лимит из данных сильнее документного
    assert p["employees"][1]["flightTimeLimitMin"] == 4800


def test_apply_none_changes_nothing():
    p = _emps({"id": "E1"})
    before = [dict(e) for e in p["employees"]]
    assert apply_monthly_limit(p, None) == 0
    assert apply_monthly_limit(p, 0) == 0
    assert p["employees"] == before
