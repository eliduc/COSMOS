# -*- coding: utf-8 -*-
"""Семья истории `duty_series_limit` (заявка CDPM req-153177713226351645
31, слово владельца 09.09.2026): лимит серии рабочих суток подряд и
обязательные свободные сутки сразу после серии.

Стенды с известным ответом — на исполнителя ПРОВЕРКИ КОДА
(`vmp.run.logic_checks._duty_series`) и на его связку с решателем:

  * план формы собирается ИЗ ПОСАДКИ правила (категория A), обе границы
    приходят параметрами записи, ни одного своего числа у проверки нет;
  * серия ровно в предел с достаточным отдыхом после неё — ноль находок;
    серия сверх предела и короткий отдых после неё — одна находка на
    серию; хвост последней серии не судится (следующей серии нет);
  * таблицы решателя объявили ту же семью и те же имена с теми же
    единицами и отношениями, что несёт запись кандидата (закон паритета
    судит рост таблиц по записи, а этот стенд — по коду).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from vmp import kotlin_tables as ktab              # noqa: E402
from vmp.run import fit_checks as fc               # noqa: E402
from vmp.run import logic_checks as lc             # noqa: E402
from vmp.run import roster_certify as rc           # noqa: E402

FAMILY = "duty_series_limit"
SERIES = "max_duty_series_length"
DAYS_OFF = "post_series_days_off"


def _rule(**params):
    return {"id": "R-DS", "kind": "HARD", "zone": "ROSTERING",
            "title": "лимит серии рабочих смен подряд",
            "statement": "серия рабочих смен и отдых после неё",
            "template_fit": {"category": "A", "family": FAMILY,
                             "registry_rev": "стенд", "quote": "стенд",
                             "params": {k: {"value": v} for k, v in params.items()}}}


def _dataset(worked_days: list[int]):
    """Решение из синтетических кодов: рабочие сутки октября — пара
    рейсов ST1→ST2→ST1; горизонт 01–10 октября объявлен."""
    rows = []
    for d in worked_days:
        for n, (a, b, dep, arr) in enumerate((
                ("ST1", "ST2", "06:00", "08:30"), ("ST2", "ST1", "09:15", "12:00"))):
            rows.append({"employee": "P1", "flight": f"F{d}{n}",
                         "date": f"2026-10-{d:02d}",
                         "dep_time": f"2026-10-{d:02d}T{dep}",
                         "arr_time": f"2026-10-{d:02d}T{arr}",
                         "dep_airport": a, "arr_airport": b,
                         "base": "ST1", "role": "CP", "duty_id": f"D{d}"})
    solution = {"solution": "стенд", "assignments": rows,
                "window_horizon": {"start_inclusive": "2026-10-01T00:00:00",
                                   "end_exclusive": "2026-10-11T00:00:00"},
                "window_employees": [{"employee": "P1", "base": "ST1"}]}
    return rc.build_solution_dataset(solution)


def _run(worked_days, series=3, days_off=2):
    rule = _rule(**{SERIES: series, DAYS_OFF: days_off})
    plan = lc.plan_from_fit(FAMILY, rule)
    assert plan is not None and plan["form"] == lc.FORM_DUTY_SERIES
    check, found = lc.run_plan("R-DS", rule, plan, _dataset(worked_days),
                               fc._Counter())
    return plan, check, found


def test_план_формы_собирается_из_посадки_обеими_границами():
    plan, _check, _found = _run([2, 3, 4, 7, 8])
    # Ни одного своего числа: длина серии и свободные сутки после неё —
    # ровно значения параметров записи каталога.
    assert plan["bound"] == 2 and plan["condBound"] == 3
    assert plan["rules"] == ["R-DS"]
    # Не хватает любой из двух границ — плана нет (правило остаётся
    # компилятору, а не судится половиной формы).
    for params in ({SERIES: 3}, {DAYS_OFF: 2}, {}):
        assert lc.plan_from_fit(FAMILY, _rule(**params)) is None


def test_серия_в_предел_с_достаточным_отдыхом_находок_не_даёт():
    # Рабочие сутки 2,3,4 (серия 3 = предел), свободные 5 и 6, затем 7,8
    # — последняя серия, её хвост не судится.
    _plan, check, found = _run([2, 3, 4, 7, 8])
    assert check["status"] == "прогнана", check.get("reason")
    assert found == []
    assert check["windows_judged"] == 1 and check["windows_violated"] == 0


def test_серия_сверх_предела_и_короткий_отдых_дают_одну_находку():
    # Рабочие сутки 2,3,4,5 (серия 4 > 3), свободные только 6 (нужно 2).
    _plan, check, found = _run([2, 3, 4, 5, 7, 8])
    assert check["status"] == "прогнана", check.get("reason")
    assert len(found) == 1, found
    assert check["windows_violated"] == 1
    text = found[0]["text"]
    assert "рабочих суток подряд 4 больше предела 3" in text
    assert "свободных суток 1, требуется 2" in text


def test_серия_ровно_в_предел_но_отдыха_после_неё_мало():
    # Рабочие сутки 2,3,4 (предел достигнут), свободные только 5.
    _plan, check, found = _run([2, 3, 4, 6, 7])
    assert check["status"] == "прогнана", check.get("reason")
    assert len(found) == 1, found
    assert "больше предела" not in found[0]["text"]
    assert "свободных суток 1, требуется 2" in found[0]["text"]


def test_границы_приезжают_посадкой_а_не_кодом_проверки():
    # Тот же мир при пределе 5 и одном свободном дне — находок нет.
    _plan, check, found = _run([2, 3, 4, 5, 7, 8], series=5, days_off=1)
    assert check["status"] == "прогнана", check.get("reason")
    assert found == []


def test_таблицы_решателя_объявили_семью_и_её_имена():
    tables = ktab.history()
    assert FAMILY in tables["families"]
    assert tables["units"][SERIES] == ("count", "max")
    assert tables["units"][DAYS_OFF] == ("count", "min")
    assert ktab.logic_forms()[lc.FORM_DUTY_SERIES] == "FORM_DUTY_SERIES_LIMIT"


def test_запись_кандидата_объявила_рост_таблиц_решателя():
    # Запись — как её несёт дерево: кандидатом до сборки, записью
    # каталога после (файл кандидата сборка службой удаляет).
    from template_catalog import candidates as tcc
    record, _source = tcc.record_in_tree(REPO / "catalog", "families", FAMILY)
    units = ktab.history()["units"]
    by_name = {p["name"]: p for p in record["params"]}
    assert set(by_name) == {SERIES, DAYS_OFF}
    for name, p in sorted(by_name.items()):
        assert (p["unit"], p["relation"]) == units[name], (name, p)
    seed = json.loads((REPO / "vmp" / "bindings_seed.json")
                      .read_text(encoding="utf-8"))
    rows = seed["records"][f"families/{FAMILY}"]
    kinds = {r["kind"]: r for r in rows}
    assert set(kinds) == set(record["executor_kinds"])
    assert kinds["check_code"]["symbol"] == "_duty_series"
    assert kinds["kotlin_logic_form"]["form"] == lc.FORM_DUTY_SERIES


def test_сертификат_проверяет_правило_семьи_кодом_по_посадке():
    """Сертификат (fit_code_checks) берёт семьи истории из кода проверок
    (FAMILY_FORMS), а не из перечня: правило, посаженное на
    duty_series_limit, получает проверку кодом по посадке — живой прогон
    Н0-C фазы 2 (0.4.0, 09.09.2026) оставлял его «непроверенным»."""
    rule = _rule(**{SERIES: 3, DAYS_OFF: 2})
    svod = {"rules": [rule]}
    specs, checks, found, rest = fc.fit_code_checks(svod, _dataset([2, 3, 4, 5, 7, 8]))
    assert [c for c in checks if str((c.get("basis") or {}).get("rule")) == "R-DS"], checks
    assert len(found) == 1
    assert all(str(r.get("id")) != "R-DS" for r in rest["rules"])
