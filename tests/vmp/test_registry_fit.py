# -*- coding: utf-8 -*-
"""Посадка правил на шаблоны (пакет A/B/C/D, решение владельца
2026-09-01): fit_gap судит посадку по закрытым словарям базы,
deep_check берёт посадку первичным источником покрытия, легаси-правила
без посадки идут прежним путём по реестру исполнителей."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))

from vmp import registry_check as rc  # noqa: E402


def _rule(fit: dict | None, rid: str = "R-1", zone: str = "PAIRING",
          kind: str = "HARD") -> dict:
    r = {"id": rid, "title": "правило", "zone": zone, "kind": kind}
    if fit is not None:
        r["template_fit"] = fit
    return r


def _fit_a(**over) -> dict:
    fit = {
        "category": "A",
        "registry_rev": "test-rev",
        "family": "duty_limits",
        "params": {"max_duty_duration": {
            "value": 720,
            "quote": "предел рабочей смены — 12:00"}},
    }
    fit.update(over)
    return fit


def test_посадка_A_валидна_покрытие_без_реестра():
    assert rc.fit_gap(_rule(_fit_a())) is None
    gaps = rc.deep_check([_rule(_fit_a())], None, {"rules": []})
    assert gaps == []


def test_посадка_B_требует_допущение():
    fit = _fit_a(category="B")
    g = rc.fit_gap(_rule(fit))
    assert g and g["kind"] == "fit_invalid" and "допущени" in g["note"]
    fit["assumption"] = "сутки — по местному времени базы"
    assert rc.fit_gap(_rule(fit)) is None


def test_категория_C_явный_пробел_с_текстом():
    g = rc.fit_gap(_rule({"category": "C",
                          "gap": "нет механики торгов отпусков"}))
    assert g["kind"] == "rule_no_template"
    assert "торгов" in g["note"]


def test_категория_D_не_менее_двух_кандидатов():
    bad = rc.fit_gap(_rule({"category": "D",
                            "candidates": ["duty_limits"]}))
    assert bad["kind"] == "fit_invalid"
    ok = rc.fit_gap(_rule({"category": "D",
                           "candidates": ["duty_limits",
                                          "rest_floors"]}))
    assert ok["kind"] == "rule_fit_conflict"
    assert "duty_limits" in ok["note"]


def test_кривые_посадки_fail_closed():
    # чужая семья у параметра
    g = rc.fit_gap(_rule(_fit_a(family="rest_floors")))
    assert g["kind"] == "fit_invalid" and "принадлежит семье" in g["note"]
    # параметр вне словаря
    g = rc.fit_gap(_rule(_fit_a(params={"unknown_param": {
        "value": 1, "quote": "текст"}})))
    assert g["kind"] == "fit_invalid" and "вне закрытого" in g["note"]
    # параметр без цитаты
    g = rc.fit_gap(_rule(_fit_a(params={"max_duty_duration": {
        "value": 720, "quote": "  "}})))
    assert g["kind"] == "fit_invalid" and "цитат" in g["note"]
    # семья вне базы
    g = rc.fit_gap(_rule(_fit_a(family="no_such_family")))
    assert g["kind"] == "fit_invalid" and "вне базы" in g["note"]
    # категория вне перечня
    g = rc.fit_gap(_rule({"category": "X"}))
    assert g["kind"] == "fit_invalid"


def test_беспараметрная_семья_требует_цитату_носитель():
    # мягкие механики без словарных параметров (layover_penalty и
    # родня) — посадка без params законна при дословной цитате
    fit = {"category": "A", "registry_rev": "r",
           "family": "layover_penalty",
           "quote": "штраф за каждую ночь вне базы"}
    assert rc.fit_gap(_rule(fit)) is None
    del fit["quote"]
    g = rc.fit_gap(_rule(fit))
    assert g["kind"] == "fit_invalid" and "цитаты-носителя" in g["note"]
    # а семья СО словарными параметрами без params — по-прежнему брак
    g = rc.fit_gap(_rule({"category": "A", "registry_rev": "r",
                          "family": "duty_limits",
                          "quote": "предел смены"}))
    assert g["kind"] == "fit_invalid" and "требует" in g["note"]


def test_легаси_без_посадки_идёт_прежним_путём():
    rule = _rule(None, rid="R-legacy")
    gaps = rc.deep_check([rule], None, {"rules": []})
    assert len(gaps) == 1 and gaps[0]["kind"] == "rule_unknown"
    reg = {"rules": [{"rule_id": "R-legacy",
                      "verdict": "covered_full"}]}
    assert rc.deep_check([rule], None, reg) == []


def test_смесь_посадок_и_легаси_в_одном_своде():
    rules = [
        _rule(_fit_a(), rid="R-a"),
        _rule({"category": "C", "gap": "нет механики"}, rid="R-c"),
        _rule(None, rid="R-old"),
        _rule(_fit_a(), rid="R-out", zone="ROSTERING"),  # вне зоны
    ]
    reg = {"rules": [{"rule_id": "R-old",
                      "verdict": "covered_full"}]}
    gaps = rc.deep_check(rules, None, reg)
    assert [g["rule_id"] for g in gaps] == ["R-c"]
