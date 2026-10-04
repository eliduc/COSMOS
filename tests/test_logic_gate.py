# -*- coding: utf-8 -*-
"""Гейт ступени 3 (webapp/logic_gate.py): раскладка logic против пакета.

Каждая отказная ветка — нарушающим случаем и контрольным. Модуль не
знает ни имён ограничений, ни словарей: всё приходит отчётом решателя,
поэтому фикстуры здесь — отчёты, а не пакеты на диске.
"""

import pytest

from webapp import logic_gate as lg


def _model(*rules):
    return {"rules": list(rules)}


def _rule(rid, kind="HARD", enforce=None):
    logic = {"subject": "duty", "demand": {"kind": "duration_within", "relation": "max",
                                           "param": "max_duty_duration", "as_written": "12:00"}}
    if enforce:
        logic["enforce"] = enforce
    return {"id": rid, "kind": kind, "logic": logic}


def _entry(rid, state, constraints=(), considered=None, reason=None, enforce="required"):
    return {"rule": rid, "state": state, "enforce": enforce,
            "constraints": list(constraints), "considered": considered,
            "consideredMeasure": "назначений набора", "reason": reason,
            "declarations": ["ПРОЧТЕНИЕ: проба"]}


def _logic(**baskets):
    out = {b: [] for b in lg.BASKETS}
    out.update(baskets)
    out["blocksSeen"] = sum(len(v) for b, v in out.items() if b in lg.BASKETS)
    out.setdefault("registered", ["Max duty period (rest-segmented)", "Flight conflict"])
    return out


def _defects(vs):
    return [v for v in vs if v["verdict"] == lg.DEFECT]


def test_пакет_без_logic_гейту_нечего_сверять():
    vs = lg.check(_model({"id": "R-1", "kind": "HARD"}), None)
    assert [v["verdict"] for v in vs] == [lg.EMPTY]
    assert lg.is_green(vs)


def test_пакет_с_logic_а_отчёт_без_раздела_отказ():
    with pytest.raises(lg.LogicGateError):
        lg.check(_model(_rule("R-7")), None)


def test_исполняется_существующим_сходится():
    logic = _logic(executedElsewhere=[_entry("R-7", "ИСПОЛНЯЕТСЯ СУЩЕСТВУЮЩИМ",
                                              ["Max duty period (rest-segmented)"], considered=40)])
    vs = lg.check(_model(_rule("R-7")), logic)
    assert lg.is_green(vs)
    assert [v["rule"] for v in vs] == ["R-7"]
    assert "предметов рассмотрено 40" in vs[0]["note"]
    out = lg.report(vs)
    assert "рассмотрено правил с logic: 1" in out
    assert "ПРОЧТЕНИЕ: проба" in out


def test_исполнитель_не_зарегистрирован_фабрикой_дефект():
    logic = _logic(executedElsewhere=[_entry("R-7", "ИСПОЛНЯЕТСЯ СУЩЕСТВУЮЩИМ",
                                              ["Нет такого"], considered=40)])
    d = _defects(lg.check(_model(_rule("R-7")), logic))
    assert len(d) == 1 and "не зарегистрирован" in d[0]["note"]


def test_ноль_предметов_рассмотрено_дефект():
    logic = _logic(executedElsewhere=[_entry("R-7", "ИСПОЛНЯЕТСЯ СУЩЕСТВУЮЩИМ",
                                              ["Flight conflict"], considered=0)])
    d = _defects(lg.check(_model(_rule("R-7")), logic))
    assert len(d) == 1 and "считать нечего" in d[0]["note"]


def test_правило_не_названо_или_названо_дважды_дефект():
    logic = _logic(executedElsewhere=[_entry("R-7", "ИСПОЛНЯЕТСЯ СУЩЕСТВУЮЩИМ",
                                              ["Flight conflict"], considered=3)])
    d = _defects(lg.check(_model(_rule("R-7"), _rule("R-8")), logic))
    assert any(v["rule"] == "R-8" and "ни одной" in v["note"] for v in d)
    assert any(v["rule"] == "*" and "blocksSeen" in v["note"] for v in d)
    twice = _logic(executedElsewhere=[_entry("R-7", "x", ["Flight conflict"], 3)],
                   ignored=[_entry("R-7", "НЕ ИСПОЛНЯЕТСЯ", reason="post-check")])
    d2 = _defects(lg.check(_model(_rule("R-7")), twice))
    assert any(v["rule"] == "R-7" and "ровно одна" in v["note"] for v in d2)


def test_отвергнутое_в_состоявшемся_прогоне_дефект():
    logic = _logic(refused=[_entry("R-7", "ОТВЕРГНУТО", reason="предмет вне словаря")])
    d = _defects(lg.check(_model(_rule("R-7")), logic))
    assert len(d) == 1 and "ОТВЕРГНУТО" in d[0]["note"]


def test_required_в_не_исполняется_дефект_а_post_check_нет():
    logic = _logic(ignored=[_entry("R-7", "НЕ ИСПОЛНЯЕТСЯ", reason="форма ещё не порождается")])
    d = _defects(lg.check(_model(_rule("R-7")), logic))
    assert len(d) == 1 and "required" in d[0]["note"]
    logic2 = _logic(ignored=[_entry("R-7", "НЕ ИСПОЛНЯЕТСЯ", reason="post-check", enforce="post-check")])
    vs = lg.check(_model(_rule("R-7", enforce="post-check")), logic2)
    assert lg.is_green(vs)


def test_вид_правила_только_для_не_hard():
    logic = _logic(notHard=[_entry("R-D", "ВИД ПРАВИЛА", reason="вид DATA")])
    assert lg.is_green(lg.check(_model(_rule("R-D", kind="DATA")), logic))
    bad = _logic(notHard=[_entry("R-7", "ВИД ПРАВИЛА", reason="?")])
    d = _defects(lg.check(_model(_rule("R-7")), bad))
    assert len(d) == 1 and "жёсткое" in d[0]["note"]


def test_отрицательный_контроль_имён_logic_у_фабрики():
    logic = _logic(executedElsewhere=[_entry("R-7", "x", ["Flight conflict"], 3)],
                   registered=["Flight conflict", "logic 0a1b2c3d"])
    d = _defects(lg.check(_model(_rule("R-7")), logic))
    assert len(d) == 1 and "logic 0a1b2c3d" in d[0]["note"]
    # Контроль: порождённое имя, названное в отчёте, — не дефект.
    ok = _logic(generated=[_entry("R-34", "ПОРОЖДЕНО", ["logic 0a1b2c3d"], considered=68)],
                registered=["Flight conflict", "logic 0a1b2c3d"])
    assert lg.is_green(lg.check(_model(_rule("R-34")), ok))


def test_без_registered_дефект():
    logic = _logic(executedElsewhere=[_entry("R-7", "x", ["Flight conflict"], 3)])
    logic["registered"] = None
    d = _defects(lg.check(_model(_rule("R-7")), logic))
    assert any("registered" in v["note"] for v in d)


def test_волновая_мягкая_форма_в_generated_законна():
    """Волна П-2: SOFT с demand.kind из словаря волны законно живёт в
    generated (включатель порождает исполнителя); прочий SOFT в
    generated — по-прежнему дефект (живой урок Г-П4)."""
    wave = _rule("R-W", kind="SOFT")
    wave["logic"]["demand"] = {"kind": "штраф-ступенчатый-за-стыковку",
                               "threshold_param": "long_sit_threshold",
                               "step_param": "long_sit_step",
                               "as_written": "60 мин"}
    logic = _logic(generated=[_entry(
        "R-W", "ПОСЧИТАНО", considered=92,
        constraints=["Long sit penalty (generated)"])])
    logic["registered"] = ["Long sit penalty (generated)"]
    vs = lg.check(_model(wave), logic)
    assert not _defects(vs), vs

    plain = _rule("R-P", kind="SOFT")
    logic2 = _logic(generated=[_entry(
        "R-P", "ПОСЧИТАНО",
        constraints=["Long sit penalty (generated)"])])
    logic2["registered"] = ["Long sit penalty (generated)"]
    d = _defects(lg.check(_model(plain), logic2))
    assert any("попало в корзину" in x["note"] for x in d), d


def test_форма_из_посадки_семьи_истории_узнаётся_кодом_проверок():
    """Признак «правило семьи истории исполняется формой из посадки» —
    из кода проверок (`logic_checks.FAMILY_FORMS` + `plan_from_fit`), а
    не из рукописного зеркала таблиц решателя: первая семья, пришедшая
    конвейером пополнения (duty_series_limit, 0.4.0, 09.09.2026), в
    зеркале отсутствовала, и гейт останавливал кнопку «в модели пакета
    нет блоков logic, а решатель видел 1»."""
    from vmp.run import logic_checks as lc
    fit = {"category": "A", "family": "duty_series_limit",
           "params": {"max_duty_series_length": {"value": 3},
                      "post_series_days_off": {"value": 1}}}
    rule = {"id": "R-1", "kind": "HARD", "zone": "ROSTERING", "template_fit": fit}
    assert lg.fitted_history_form(rule) == "duty_series_limit"
    assert lg.logic_rules({"rules": [rule]}) == [rule]
    half = dict(rule, template_fit=dict(fit, params={"max_duty_series_length": {"value": 3}}))
    assert lg.fitted_history_form(half) is None
    assert lg.fitted_history_form(dict(rule, template_fit=dict(fit, category="C"))) is None
    other = dict(rule, template_fit={"category": "A", "family": "rest_floors",
                                     "params": {"min_rest_home": {"value": 720}}})
    assert lg.fitted_history_form(other) is None
    # все семьи истории кода проверок узнаются той же формулой
    for fam in lc.FAMILY_FORMS:
        assert fam in lc.FAMILY_FORMS
    assert {"night_series", "rolling_rest", "days_off_floor",
            "duty_series_limit"} <= set(lc.FAMILY_FORMS)
