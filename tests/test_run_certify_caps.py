# -*- coding: utf-8 -*-
"""Жёсткий предел семьи с ПРОДЛЕНИЕМ — это потолок продления, но АДРЕСНО.

Первый сертификат пейринга H0 (03.09): три смены по 760 минут при
базовых 720 и потолке 780 названы критическими — компилятор построил
проверку по базовому числу, не зная потолка дополняющей семьи.
Превышение базового предела судится штрафом мягкого правила, а
недопустимым решение делает только превышение потолка.

Аудит A8 (04.09.2026): прежняя редакция поднимала порог ВСЕМ сменам
без проверки условий исключения (класс ошибки — исключение шире своей
области); теперь компилированная проверка заменяется проверкой кода с
пределом по строке: потолок — сменам, удовлетворяющим условиям
посадки правила-потолка, иначе базовый предел. Прежние стенды
закрепляли глобальный подъём порога и переписаны."""
from vmp.run import roster_certify as rc


def _rule(rid, fam, params, kind="HARD"):
    return {"id": rid, "kind": kind, "zone": "PAIRING", "title": rid,
            "statement": "s", "sources": [{"sid": "S1", "anchor": rid}],
            "template_fit": {"category": "A", "family": fam,
                             "registry_rev": "x" * 12,
                             "params": {k: {"value": v, "quote": str(v)}
                                        for k, v in params.items()}}}


def _spec(rid, max_hours, template="максимальная-длительность-часов"):
    return {"id": f"C-{rid}-0", "basis": {"rule": rid}, "template": template,
            "params": {"sheet": "SOL/Смены", "start_column": "Начало",
                       "end_column": "Конец", "max_hours": max_hours},
            "statement": "смена ≤ предела", "unit": "смена",
            "status": "скомпилирована"}


def _dataset(*duties):
    """Лист «Смены» решения: (табельный, база, начало, конец)."""
    rows = [{"Табельный номер": e, "База приписки": b,
             "Начало": t0, "Конец": t1} for e, b, t0, t1 in duties]
    return {"tables": {rc.SOLUTION_SID: {"Смены": {
        "columns": ["Табельный номер", "База приписки", "Начало", "Конец"],
        "rows": rows}}}}


def test_потолок_продления_судится_кодом_по_строке():
    """Без условий продление безусловно: смена 12:40 (потолок 13:00)
    — не нарушение; смена 13:20 — нарушение потолка."""
    svod = {"rules": [
        _rule("R-LIM", "duty_limits", {"max_duty_duration": 720}),
        _rule("R-EXT", "duty_extension_penalty",
              {"long_duty_threshold": 720, "duty_extension_cap": 780},
              kind="SOFT"),
    ]}
    ds = _dataset(("E1", "AAA", "2026-01-15T06:00", "2026-01-15T18:40"),
                  ("E2", "AAA", "2026-01-15T06:00", "2026-01-15T19:20"))
    kept, notes, checks, findings = rc.apply_extension_caps(
        svod, [_spec("R-LIM", 12.0)], ds)
    assert kept == []                       # компилированная заменена кодом
    assert len(checks) == 1 and checks[0]["status"] == "прогнана"
    assert checks[0]["params"]["max_hours"] == 12.0
    assert checks[0]["params"]["cap_hours"] == 13.0
    assert checks[0]["params"]["conditions"] == {}
    assert "потолка продления" in checks[0]["cap_note"] or \
        "потолок продления" in checks[0]["cap_note"]
    assert notes and "R-LIM" in notes[0]
    assert [f["target"]["row"] for f in findings] == [3]
    assert "потолок продления" in findings[0]["text"]


def test_продление_адресно_по_базе():
    """Условие extension_bases=AAA: потолок только базе AAA; смена базы
    BBB той же длины судится базовым пределом."""
    svod = {"rules": [
        _rule("R-LIM", "duty_limits", {"max_duty_duration": 720}),
        _rule("R-EXT", "duty_extension_penalty",
              {"long_duty_threshold": 720, "duty_extension_cap": 780,
               "extension_bases": ["AAA"]}, kind="SOFT"),
    ]}
    ds = _dataset(("E1", "AAA", "2026-01-15T06:00", "2026-01-15T18:40"),
                  ("E2", "BBB", "2026-01-15T06:00", "2026-01-15T18:40"))
    _kept, _notes, checks, findings = rc.apply_extension_caps(
        svod, [_spec("R-LIM", 12.0)], ds)
    assert checks[0]["params"]["conditions"] == {"extension_bases": ["AAA"]}
    assert [f["target"]["row"] for f in findings] == [3]
    assert "базовый предел" in findings[0]["text"]
    assert "BBB" in findings[0]["evidence"]


def test_без_дополняющего_правила_порог_не_трогается():
    svod = {"rules": [_rule("R-LIM", "duty_limits", {"max_duty_duration": 720})]}
    kept, notes, checks, findings = rc.apply_extension_caps(
        svod, [_spec("R-LIM", 12.0)], _dataset())
    assert kept[0]["params"]["max_hours"] == 12.0
    assert notes == [] and checks == [] and findings == []


def test_потолок_ниже_порога_или_чужой_шаблон_не_меняют_проверку():
    svod = {"rules": [
        _rule("R-LIM", "duty_limits", {"max_duty_duration": 720}),
        _rule("R-EXT", "duty_extension_penalty",
              {"long_duty_threshold": 720, "duty_extension_cap": 700},
              kind="SOFT"),
    ]}
    kept, notes, checks, _f = rc.apply_extension_caps(
        svod, [_spec("R-LIM", 12.0)], _dataset())
    assert kept[0]["params"]["max_hours"] == 12.0 and notes == [] and checks == []
    # Проверка другого шаблона по тому же правилу — не длительность.
    other = _spec("R-LIM", 12.0, template="минимальный-разрыв-часов")
    kept, notes, checks, _f = rc.apply_extension_caps(svod, [other], _dataset())
    assert kept == [other] and checks == []
