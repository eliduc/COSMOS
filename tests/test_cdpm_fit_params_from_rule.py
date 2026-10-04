# -*- coding: utf-8 -*-
"""К-1 принципом (находка COSMOS на нано 04.09): посадка модели на семью
со словарными параметрами без params НЕ отбрасывается, если параметры
извлечены у самого правила — они берутся по именам семьи (значение +
цитата as_written). Без параметров у правила — прежний честный отказ
К-1; чужие имена семье не приписываются; готовые params посадки не
перезаписываются."""
from cdpm import model as m

CLAUSE = ("Справедливость распределения кредитных часов: разброс между "
          "членами экипажа одной базы — не более 10 кредитных часов за "
          "календарный месяц ростера.")


def _cand(fit_params=None, rule_params=None):
    fit = {"category": "A", "family": "balanced_workload", "confidence": 96}
    if fit_params is not None:
        fit["params"] = fit_params
    c = {"kind": "SOFT", "template_fit": fit}
    if rule_params is not None:
        c["params"] = rule_params
    return c


def test_params_посадки_берутся_из_params_правила():
    c = _cand(rule_params=[{"name": "workload_spread_threshold", "value": 10,
                            "unit": "credit_hours", "relation": "max",
                            "as_written": "не более 10 кредитных часов"}])
    fit, conf, note = m.accept_fit_candidate(c, CLAUSE)
    assert fit["category"] == "A", fit
    assert fit["params"] == {"workload_spread_threshold":
                             {"value": 10, "quote": "не более 10 кредитных часов"}}
    assert conf == 96 and note is None


def test_без_params_у_правила_прежний_отказ_к1():
    fit, conf, note = m.accept_fit_candidate(_cand(), CLAUSE)
    assert fit["category"] == "C"
    assert "params обязателен" in fit["gap"]


def test_чужие_имена_семье_не_приписываются():
    c = _cand(rule_params=[{"name": "max_duty_duration", "value": 720,
                            "as_written": "не более 12:00"}])
    fit, _conf, _note = m.accept_fit_candidate(c, CLAUSE)
    assert fit["category"] == "C" and "params обязателен" in fit["gap"]


def test_готовые_params_посадки_не_перезаписываются():
    own = {"workload_spread_threshold": {"value": 10, "quote": "не более 10 кредитных часов"}}
    c = _cand(fit_params=own, rule_params=[{"name": "workload_spread_threshold", "value": 99,
                                            "as_written": "не более 10 кредитных часов"}])
    fit, _conf, _note = m.accept_fit_candidate(c, CLAUSE)
    assert fit["category"] == "A" and fit["params"] == own


def test_помощник_не_трогает_категории_c_и_d():
    assert m.fill_fit_params_from_rule({"category": "C", "gap": "x"},
                                       [{"name": "crew_size", "value": 2, "as_written": "2"}]) \
        == {"category": "C", "gap": "x"}
