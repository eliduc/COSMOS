# -*- coding: utf-8 -*-
"""Месяц среза у сотрудника БЕЗ записи истории в форме «выходных в месяц
не менее N» (ревизия 1.17.2, слово владельца 05.09.2026 «пользователь нажмёт
„Продолжать с нулевой историей“, расчёт продолжится»):

- continue_zero / assume_zero_when_missing → клетка месяца среза судится с
  нулевым переносом по допущению (названо в проверке);
- continue_partial → месяц среза не судится, окно названо адресно, правило
  покрыто частично;
- без раздела истории вовсе → клетки месяца среза нет (как прежде).

Стенд: горизонт 04–31.03.2026 (март закрыт горизонтом), срез 04.03; E7
занят 24 сутки марта → выходных 7 при норме 8.
"""
from __future__ import annotations

import json

from vmp.run import logic_checks as lc
from vmp.run import roster_certify as rc


def _rule(rid):
    return {"id": rid, "title": rid, "statement": "правило " + rid,
            "zone": "ROSTERING", "kind": "HARD",
            "logic": {"subject": "day_off", "enforce": "required",
                      "demand": {"kind": "count_within", "relation": "min", "param": "p"},
                      "params": [{"name": "p", "value": 8, "unit": "count"}]},
            "sources": [{"sid": "S1", "anchor": "п.1"}]}


def _svod():
    return {"meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
            "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
            "rules": [_rule("R-DAYSOFF")], "questions": [], "objectives": []}


def _logic():
    p = {"rules": ["R-DAYSOFF"], "constraint": "logic R-DAYSOFF",
         "form": lc.FORM_DAYS_OFF, "subject": "x", "relation": "min", "bound": 8,
         "boundParam": "p", "baseScope": "SVG", "condBound": None, "condParam": None,
         "nightFrom": None, "nightTo": None, "enforce": "required", "weight": 1,
         "reading": "стенд", "shapeSha": "x", "soft": False}
    gen = [{"rule": "R-DAYSOFF", "state": "ПОРОЖДЕНО", "enforce": "required",
            "constraints": [p["constraint"]], "viaThresholds": [], "reason": None,
            "declarations": [], "shapeSha": "x", "considered": 1,
            "consideredMeasure": "стенд", "plan": p}]
    return {"blocksSeen": 1, "generated": gen, "executedElsewhere": [], "ignored": [],
            "refused": [], "notHard": [], "registered": [p["constraint"]], "fitOn": "стенд"}


def _asg(emp, d):
    return {"employee": emp, "flight": f"F{emp}-{d}", "dep_time": f"2026-03-{d:02d}T08:00",
            "arr_time": f"2026-03-{d:02d}T12:00", "dep_airport": "SVG",
            "arr_airport": "SVG", "base": "SVG", "duty_id": f"{emp}/F{d}"}


def _solution(policy, declared: bool = True):
    # E7: 24 занятых суток марта (04–27) → выходных 7 < 8; E8 — 4 сутки,
    # его назначение 31.03 закрывает месяц горизонтом.
    a = [_asg("E7", d) for d in range(4, 28)] + [_asg("E8", d) for d in (5, 12, 19, 31)]
    sol = {"solution": "стенд месяца среза", "assignments": a, "duties": [],
           "lastRunLogic": _logic()}
    if declared:
        # Объявленный горизонт стенда 04–31.03 (C-27b: без объявленного
        # горизонт выводится по назначениям — частичная диагностика,
        # правило покрыто лишь частично; см. последний тест).
        sol["window_horizon"] = {"start_inclusive": "2026-03-04", "end_exclusive": "2026-04-01"}
    if policy is not None:
        sol["history_policy"] = policy
    return sol


def _compiler(prompt):
    return json.dumps({"checks": [], "skipped": []}, ensure_ascii=False)


def _check(report):
    return {x["id"]: x for x in report["checks"]}["C-R-DAYSOFF-форма"]


def test_continue_zero_judges_cut_month_with_zero_carry_and_names_assumption():
    policy = {"as_of": "2026-03-04T00:00", "coverage_kind": "none",
              "assume_zero_when_missing": True,
              "user_decision": {"question": "Q-3b-история-нет", "decision": "continue_zero"},
              "employees_without_history": ["E7", "E8"]}
    report = rc.certify(_svod(), _solution(policy), _compiler)
    c = _check(report)
    assert c["windows_assumed_employees"] == 2
    assert "continue_zero" in c["windows_assumed_basis"]
    assert c["windows_judged"] == 2 and c["windows_unjudged"] == 0
    assert c["windows_violated"] == 1
    fs = [json.dumps(f, ensure_ascii=False) for f in report["findings"]]
    assert any("E7" in s and "выходных в 2026-03: 7 меньше нормы 8" in s for s in fs)
    assert not any("E8" in s and "выходных в 2026-03" in s for s in fs)
    assert report["certificate"]["rules_partially_covered"] == []
    assert report["certificate"]["certified"] is False


def test_continue_partial_leaves_cut_month_unjudged_with_address():
    policy = {"as_of": "2026-03-04T00:00", "coverage_kind": "none",
              "assume_zero_when_missing": False,
              "user_decision": {"question": "Q-3b-история-нет", "decision": "continue_partial"},
              "employees_without_history": ["E7", "E8"]}
    report = rc.certify(_svod(), _solution(policy), _compiler)
    c = _check(report)
    assert c["windows_unjudged"] == 2
    (reason, n), = c["unjudged_by_reason"].items()
    assert n == 2
    assert reason.startswith("нет записи сотрудника в истории (решение пользователя: continue_partial)")
    # неизвестные сутки префикса месяца названы адресно
    assert "2026-03-01, 2026-03-02, 2026-03-03" in reason
    assert {u["employee"] for u in c["unjudged_sample"]} == {"E7", "E8"}
    assert not [f for f in report["findings"]
                if "выходных в 2026-03" in json.dumps(f, ensure_ascii=False)]
    assert report["certificate"]["rules_partially_covered"] == ["R-DAYSOFF"]
    assert report["certificate"]["result"]["feasibility"] == "not_established"


def test_without_history_section_cut_month_is_unknown_not_zero():
    """Без раздела истории вовсе префикс месяца среза неизвестен (единая
    версия 06.09.2026, C-31: отсутствие остатка ≠ ноль) — клетка месяца
    есть, но не судится, с причиной «история не передана»."""
    report = rc.certify(_svod(), _solution(None), _compiler)
    c = _check(report)
    assert c["windows_judged"] == 0 and c["windows_unjudged"] == 2
    assert all(r.startswith("история не передана") for r in c["unjudged_by_reason"])
    assert "windows_assumed_employees" not in c
    assert not [f for f in report["findings"]
                if "выходных в 2026-03" in json.dumps(f, ensure_ascii=False)]
    assert report["certificate"]["result"]["feasibility"] == "not_established"


def test_inferred_horizon_is_partial_diagnostics_not_pass():
    """C-27b (план §3.10.3): тот же стенд continue_zero БЕЗ объявленного
    window_horizon — горизонт выведен по назначениям (source = inferred),
    окна судятся так же (то же нарушение E7), но правило покрыто лишь
    частично: сертификат не подтверждает допустимость по нему, причина
    названа в проверке."""
    policy = {"as_of": "2026-03-04T00:00", "coverage_kind": "none",
              "assume_zero_when_missing": True,
              "user_decision": {"question": "Q-3b-история-нет", "decision": "continue_zero"},
              "employees_without_history": ["E7", "E8"]}
    report = rc.certify(_svod(), _solution(policy, declared=False), _compiler)
    c = _check(report)
    assert c["horizon_inferred"] is True
    assert c["window_coverage"]["horizon"]["source"] == lc.HORIZON_INFERRED
    assert c["status"] == "прогнана" and c["windows_unjudged"] == 0
    assert c["windows_violated"] == 1
    assert "частичная диагностика" in c["reason"]
    v = report["certificate"]
    assert v["rules_horizon_inferred"] == ["R-DAYSOFF"]
    assert v["rules_partially_covered"] == ["R-DAYSOFF"]
    assert v["certified"] is False
    assert "выведенном по назначениям" in v["note"] or v["findings"] > 0
    # без нарушения (E7 занят 04–20 → выходных 3 по допущению + 11 = 14 ≥ 8)
    # тот же выведенный горизонт даёт «не установлена», не сертификат
    clean = _solution(policy, declared=False)
    clean["assignments"] = [a for a in clean["assignments"]
                            if not (a["employee"] == "E7" and int(a["dep_time"][8:10]) > 20)]
    report2 = rc.certify(_svod(), clean, _compiler)
    v2 = report2["certificate"]
    c2 = _check(report2)
    assert c2["status"] == "прогнана" and c2["windows_violated"] == 0
    assert v2["checks_run"] == 1
    assert v2["findings"] == 0 and v2["rules_partially_covered"] == ["R-DAYSOFF"]
    assert v2["certified"] is False
    assert v2["result"]["feasibility"] == "not_established"
    assert v2["result"]["coverage_complete"] is False
    assert "выведенном по назначениям" in v2["note"]
