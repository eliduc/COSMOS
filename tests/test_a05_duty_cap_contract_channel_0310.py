"""Ведомость исполнения: канал контракта предела смены при потолке продления
(Stage 03.10, РВ-108).

Путь пакета ВМП судит предел смены контрактом правил (work_time_limit_by_tier).
При поданном потолке продления (--duty-cap, мера 100) контракт несёт потолок,
а база правила предела смены едет ключом --duty-span. Ведомость принимает
канал контракта как потолок лишь при поданном потолке выше базы и ярусах,
ровно равных ему; иное число — по-прежнему две правды. Реальные landing/2
правила и build() ведомости; covered_full — фикстура, не допуск стенда.
"""
import copy

import pytest

from cdpm import model as m
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge  # noqa: F401 -- fixture
from vmp import execution_manifest as em


def _rule(tmp_path, monkeypatch, value=720):
    doc, rule = typed_rule(tmp_path, monkeypatch, "duty_limits", {
        "max_duty_duration": {"value": value, "unit": "duration_minutes",
                              "relation": "max", "condition": None,
                              "quote": str(value)},
    })
    rule.update(kind="HARD", zone="PAIRING",
                statement=f"Предел полётной смены: {value} мин.")
    rule.pop("params", None)
    doc["rules"] = [rule]
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    return rule


def _judge(rule, bargs, tiers):
    before = copy.deepcopy(rule)
    registry = {"rules": [{"rule_id": rule["id"], "verdict": "covered_full"}]}
    result = em.build([rule], registry, "pairing", "feasibility-hard",
                      list(bargs), {}, {"work_time_limit_by_tier": tiers})
    assert rule == before
    return result


def test_cap_contract_accepted_and_base_judged_by_cli(tmp_path, monkeypatch):
    rule = _rule(tmp_path, monkeypatch)
    result = _judge(rule, ["--duty-span", "720", "--duty-cap", "780"], [780])
    assert result["blocking"] == [], result
    passed = {p["channel"]: p for p in result["rules"][0]["passed"]}
    assert passed["ключ запуска"]["comparison"] == "совпало"
    assert passed["контракт"]["cap_of"] == "--duty-cap"
    assert "потолок продления" in passed["контракт"]["comparison"]


def test_without_cap_contract_cap_value_is_two_truths(tmp_path, monkeypatch):
    rule = _rule(tmp_path, monkeypatch)
    result = _judge(rule, ["--duty-span", "720"], [780])
    assert result["blocking"] and "две правды" in result["blocking"][0]["note"]


@pytest.mark.parametrize("tiers", [[800], [760], [780, 720], [780.0], [True]],
                         ids=["other-above", "other-between", "nonuniform",
                              "float", "bool"])
def test_cap_given_but_contract_other_number_is_two_truths(
        tmp_path, monkeypatch, tiers):
    rule = _rule(tmp_path, monkeypatch)
    result = _judge(rule, ["--duty-span", "720", "--duty-cap", "780"], tiers)
    assert result["blocking"], result
    assert all(not p.get("cap_of") for p in result["rules"][0]["passed"])


def test_base_cli_still_judged_with_cap(tmp_path, monkeypatch):
    rule = _rule(tmp_path, monkeypatch)
    result = _judge(rule, ["--duty-span", "780", "--duty-cap", "800"], [800])
    assert result["blocking"] and "две правды" in result["blocking"][0]["note"]


def test_cap_not_above_base_is_not_a_cap(tmp_path, monkeypatch):
    rule = _rule(tmp_path, monkeypatch)
    result = _judge(rule, ["--duty-span", "720", "--duty-cap", "720"], [720])
    assert result["blocking"] == [], result
    assert all(not p.get("cap_of") for p in result["rules"][0]["passed"])
