# -*- coding: utf-8 -*-
"""Детерминированные проверки сертификата для правил с блоком logic по
формам решателя — с историей сотрудника (решение владельца 05.09.2026
«нужно учитывать»).

Стенды с известным ответом на явно объявленном общем горизонте
(единая версия 06.09.2026, строки C-41/C-42: оба стенда — без политики
истории и с решением continue_zero); неполная история не сужает
множество обязательных окон:
  * серия ночных: перенос 2 + одна ночная смена горизонта = 3 при
    границе 2 — нарушение с доказательством; без истории — неизвестно
    (при continue_zero — перенос 0 по названному допущению);
  * отдых в окне: все непрерывные окончания окон общего горизонта,
    включая неизвестные ячейки через историю; при continue_zero
    сотрудник без записи свободен до горизонта по допущению;
  * выходные месяца: 30 занятых суток и один явный выходной истории —
    нарушение; без посуточной истории месяц неизвестен;
  * серия рабочих дней: 7 дней со дня среза + 3 из истории при пределе
    8 — нарушение;
  * правила, взятые кодом, компилятору-модели не подаются; отсутствие
    нарушений при неизвестных окнах не даёт сертификат допустимости;
    полные доказательства (положительный контроль) — дают.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from vmp.run import roster_certify as rc          # noqa: E402
from vmp.run import logic_checks as lc            # noqa: E402
from cdpm.gates import gate_certify               # noqa: E402

RULES = ("R-NIGHT", "R-ROLL", "R-DAYSOFF", "R-CONSEC")

#: Статус проверки с неизвестными окнами по действующему контракту
#: отчёта данных («частично прогнана» после §3.8, до того — «прогнана»
#: с адресным window_coverage).
PARTIAL = lc.partial_status()

ZERO_POLICY = {"coverage_kind": "partial", "assume_zero_when_missing": True,
               "user_decision": {"question": "Q-3b", "key": "в",
                                 "decision": "continue_zero"}}


def _rule(rid, subject, demand_kind):
    return {"id": rid, "title": rid, "statement": "правило " + rid,
            "zone": "ROSTERING", "kind": "HARD",
            "logic": {"subject": subject, "enforce": "required",
                      "demand": {"kind": demand_kind, "relation": "max", "param": "p"},
                      "params": [{"name": "p", "value": 1, "unit": "count"}]},
            "sources": [{"sid": "S1", "anchor": "п.1"}]}


def _svod():
    return {
        "meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
        "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
        "rules": [_rule("R-NIGHT", "duty", "count_within"),
                  _rule("R-ROLL", "rest", "duration_within"),
                  _rule("R-DAYSOFF", "day_off", "count_within"),
                  _rule("R-CONSEC", "day", "count_within")],
        "questions": [], "objectives": [],
    }


def _plan(rid, form, bound, base, **extra):
    p = {"rules": [rid], "constraint": "logic " + rid, "form": form,
         "subject": "x", "relation": "max", "bound": bound, "boundParam": "p",
         "baseScope": base, "condBound": None, "condParam": None,
         "nightFrom": None, "nightTo": None, "enforce": "required",
         "weight": 1, "reading": "стенд", "shapeSha": "x", "soft": False}
    p.update(extra)
    return p


def _logic_report():
    plans = [
        _plan("R-NIGHT", lc.FORM_NIGHT, 2, None, nightFrom=22 * 60, nightTo=6 * 60),
        _plan("R-ROLL", lc.FORM_ROLLING, 2160, "PLY", condBound=7, condParam="w"),
        _plan("R-DAYSOFF", lc.FORM_DAYS_OFF, 8, "SVG"),
        _plan("R-CONSEC", lc.FORM_CONSECUTIVE, 8, "GRT"),
    ]
    gen = [{"rule": p["rules"][0], "state": "ПОРОЖДЕНО", "enforce": "required",
            "constraints": [p["constraint"]], "viaThresholds": [],
            "reason": None, "declarations": [], "shapeSha": "x",
            "considered": 1, "consideredMeasure": "стенд", "plan": p} for p in plans]
    return {"blocksSeen": len(plans), "generated": gen, "executedElsewhere": [],
            "ignored": [], "refused": [], "notHard": [],
            "registered": [p["constraint"] for p in plans], "fitOn": "стенд"}


def _asg(emp, base, flight, dep, arr):
    return {"employee": emp, "flight": flight, "dep_time": dep, "arr_time": arr,
            "dep_airport": base, "arr_airport": base, "base": base,
            "duty_id": f"{emp}/{flight}"}


def _day_flights(emp, base, days, tag, h0="06:00", h1="20:00"):
    return [_asg(emp, base, f"{tag}{d}", f"2026-03-{d:02d}T{h0}", f"2026-03-{d:02d}T{h1}")
            for d in days]


def _duties_of(assignments):
    """Полная разметка нормативных смен по назначениям (идентификаторы
    смен совпадают с колонкой «Смена») — основание занятости для
    доказательства отдыха (C-29)."""
    return [{"employee": row["employee"], "id": row["duty_id"],
             "start": row["dep_time"], "end": row["arr_time"],
             "base": row["base"], "last_arr_airport": row["arr_airport"]}
            for row in assignments]


def _solution(bad: bool, policy: bool = False):
    """Стенд форм с историей на объявленном горизонте 2.03–1.04.
    policy=True — решение пользователя continue_zero (сотрудники без
    записи E2, E4, E6 судятся по допущению нулевых остатков)."""
    a = []
    # R-NIGHT: E1 с историей (2 ночных подряд), одна ночная смена 2-го;
    # E2 без истории — та же смена.
    for emp in ("E1", "E2"):
        a.append(_asg(emp, "MOW", f"N-{emp}", "2026-03-02T23:00", "2026-03-03T01:00"))
    # R-ROLL (база PLY): E3 с историей (последняя смена кончилась 1-го в
    # 20:00), E4 без истории; плохой вариант — работа 3..10 без 36 ч
    # отдыха; чистый — 3..5 и 8..10 (отдых 58 ч).
    roll_days = list(range(3, 11)) if bad else [3, 4, 5, 8, 9, 10]
    for emp in ("E3", "E4"):
        a += _day_flights(emp, "PLY", roll_days, f"F{emp}-")
    # R-DAYSOFF (база SVG): E5 с историей (1 марта — явный выходной
    # календаря истории), E6 без истории; плохой вариант — занятость
    # 10..31; чистый — 10..13, 20..26 и 31.
    off_days = list(range(10, 32)) if bad else [10, 11, 12, 13] + list(range(20, 27)) + [31]
    for emp in ("E5", "E6"):
        a += _day_flights(emp, "SVG", off_days, f"G{emp}-", "10:00", "12:00")
    # Общий горизонт начинается 2 марта: восемь рабочих дней E5 2..9 —
    # назначения, а не индивидуальный поздний срез.
    a += _day_flights("E5", "SVG", list(range(2, 10)), "G-E5-EARLY-", "10:00", "12:00")
    # R-CONSEC (база GRT): E7 с историей (3 дня подряд к срезу 2.03);
    # плохой вариант — 2..8 (7 + 3 = 10 > 8); чистый — 12..16.
    con_days = list(range(2, 9)) if bad else list(range(12, 17))
    a += _day_flights("E7", "GRT", con_days, "H-", "10:00", "12:00")
    history = [
        # плохой вариант: перенос 2 + 1 = 3 > 2; чистый: перенос 1 + 1 = 2
        {"employee": "E1", "as_of": "2026-03-02T00:00", "has_history": True,
         "consecutive_night_duties": 2 if bad else 1, "last_duty_night": True,
         "last_duty_end": "2026-03-01T01:00"},
        {"employee": "E2", "as_of": "2026-03-02T00:00", "has_history": False},
        {"employee": "E3", "as_of": "2026-03-02T00:00", "has_history": True,
         "last_duty_end": "2026-03-01T20:00"},
        {"employee": "E4", "as_of": "2026-03-02T00:00", "has_history": False},
        {"employee": "E5", "as_of": "2026-03-02T00:00", "has_history": True,
         "month_days_worked": 0},
        {"employee": "E6", "as_of": "2026-03-02T00:00", "has_history": False},
        {"employee": "E7", "as_of": "2026-03-02T00:00", "has_history": True,
         "consecutive_duty_days": 3, "month_days_worked": 1},
    ]
    sol = {"solution": "стенд форм с историей", "assignments": a,
           "duties": _duties_of(a), "history": history,
           "history_calendar": [{"employee": "E5", "date": "2026-03-01",
                                 "kind": "day_off", "night": False}],
           "window_horizon": {"start_inclusive": "2026-03-02", "end_exclusive": "2026-04-01"},
           "window_employees": [{"employee": emp, "base": base} for emp, base in
                                [("E1", "MOW"), ("E2", "MOW"), ("E3", "PLY"),
                                 ("E4", "PLY"), ("E5", "SVG"), ("E6", "SVG"), ("E7", "GRT")]],
           "lastRunLogic": _logic_report()}
    if policy:
        sol["history_policy"] = dict(ZERO_POLICY)
    return sol


def _fully_known_solution(policy: bool = False):
    """Положительный контроль: у каждого обязательного окна достаточно
    доказательств. policy=True — записи E2/E4/E6 остаются пустыми, их
    закрывает решение continue_zero (принятое допущение)."""
    sol = _solution(bad=False, policy=policy)
    for row in sol["history"]:
        if row["employee"] == "E2" and not policy:
            row.update(has_history=True, consecutive_night_duties=0)
        if row["employee"] == "E3" or (row["employee"] == "E4" and not policy):
            row.update(has_history=True, as_of="2026-03-02T00:00",
                       last_duty_end="2026-02-28T00:00")
    if not policy:
        sol["history_calendar"].append({"employee": "E6", "date": "2026-03-01",
                                        "kind": "day_off", "night": False})
    return sol


PROMPTS: list[str] = []


def _compiler(prompt):
    PROMPTS.append(prompt)
    return json.dumps({"checks": [], "skipped": []}, ensure_ascii=False)


def _by_rule(report):
    by_rule: dict[str, list[dict]] = {}
    for f in report["findings"]:
        by_rule.setdefault(f["rule_refs"][0], []).append(f)
    return by_rule


def test_logic_rules_with_history_are_judged_by_code_not_by_the_compiler():
    PROMPTS.clear()
    report = rc.certify(_svod(), _solution(bad=True), _compiler)
    v = report["certificate"]
    for p in PROMPTS:
        for rid in RULES:
            assert rid not in p, "правило с формой ушло компилятору: " + rid
    assert v["certified"] is False
    assert v["violated_rules"] == ["R-CONSEC", "R-DAYSOFF", "R-NIGHT", "R-ROLL"]
    by_rule = _by_rule(report)
    # E1 — доказанное нарушение; неизвестная начальная серия E2 его не стирает.
    assert len(by_rule["R-NIGHT"]) == 1
    assert "3 больше предела 2" in by_rule["R-NIGHT"][0]["text"]
    assert "2 из истории" in by_rule["R-NIGHT"][0]["text"]
    assert "E1" in by_rule["R-NIGHT"][0]["evidence"]
    # У каждого сотрудника — нарушенная критическая точка и открытый
    # нарушенный интервал: они представляют ВСЕ непрерывные окончания до
    # 12 марта 08:00, а не выборку суток.
    roll = by_rule["R-ROLL"]
    assert len(roll) == 4
    assert sum(1 for f in roll if "«E3»" in f["evidence"]) == 2
    assert sum(1 for f in roll if "«E4»" in f["evidence"]) == 2
    assert any("2026-03-08T20:00:00" in f["text"] for f in roll)
    # E5: 30 рабочих дней плюс известный выходной 1 марта; месяц E6 неизвестен.
    assert len(by_rule["R-DAYSOFF"]) == 1
    assert "1 меньше нормы 8" in by_rule["R-DAYSOFF"][0]["text"]
    assert "по календарю истории" in by_rule["R-DAYSOFF"][0]["text"]
    # серия рабочих дней: E7 — 7 + 3 из истории = 10 > 8
    assert len(by_rule["R-CONSEC"]) == 1
    assert "10 больше предела 8" in by_rule["R-CONSEC"][0]["text"]
    assert "3 из истории" in by_rule["R-CONSEC"][0]["text"]
    checks = {c["id"]: c for c in report["checks"]}
    for rid in RULES:
        c = checks[f"C-{rid}-форма"]
        assert c["compiled_by"] == lc.COMPILED_BY
        assert c["status"] == ("прогнана" if rid == "R-CONSEC" else PARTIAL)
        cov = c["window_coverage"]
        assert cov["expected"] == cov["evaluated"] + cov["unknown"] == len(cov["windows"])
        assert cov["horizon"]["source"] == "объявленный горизонт прогона"
        # производные поля main — от того же покрытия
        assert c["windows_judged"] == cov["evaluated"]
        assert c["windows_unjudged"] == cov["unknown"]
    assert checks["C-R-ROLL-форма"]["window_coverage"]["expected"] == 14
    assert checks["C-R-ROLL-форма"]["window_coverage"]["unknown"] == 4
    assert v["window_coverage"]["unknown"] == sum(
        checks[f"C-{rid}-форма"]["window_coverage"]["unknown"] for rid in RULES)
    assert gate_certify.check(_svod(), report)[0] == []


def test_continue_zero_judges_missing_records_by_named_assumption():
    """Тот же стенд с решением пользователя continue_zero (main 1.17.2):
    сотрудники без записи (E2, E4, E6) судятся по названному допущению
    нулевых остатков, запись с недостаточной историей (E3) остаётся
    неизвестной — допущение закрывает отсутствие записи, не факты."""
    PROMPTS.clear()
    report = rc.certify(_svod(), _solution(bad=True, policy=True), _compiler)
    v = report["certificate"]
    assert v["violated_rules"] == ["R-CONSEC", "R-DAYSOFF", "R-NIGHT", "R-ROLL"]
    by_rule = _by_rule(report)
    checks = {c["id"]: c for c in report["checks"]}
    # E2: перенос 0 по допущению → 0 + 1 = 1 ≤ 2 — окно судимо, не unknown
    night = checks["C-R-NIGHT-форма"]
    assert night["status"] == "прогнана" and night["window_coverage"]["unknown"] == 0
    assert night["windows_assumed_employees"] == 1 and "continue_zero" in night["note"]
    assert len(by_rule["R-NIGHT"]) == 1
    # E4 свободен до горизонта по допущению — единственный нарушенный
    # открытый интервал; у E3 (история до 1.03 20:00) прежние две
    # ячейки неизвестны и две нарушены.
    roll = checks["C-R-ROLL-форма"]
    assert roll["status"] == PARTIAL
    assert roll["window_coverage"]["expected"] == 14
    assert roll["window_coverage"]["unknown"] == 2
    assert roll["windows_assumed_employees"] == 1
    assert {w["employee"] for w in roll["window_coverage"]["windows"] if w["status"] == "unknown"} == {"E3"}
    assert len(by_rule["R-ROLL"]) == 3
    assert sum(1 for f in by_rule["R-ROLL"] if "«E3»" in f["evidence"]) == 2
    assert sum(1 for f in by_rule["R-ROLL"] if "«E4»" in f["evidence"]) == 1
    # E6: префикс месяца (1 марта) — выходной по допущению → 9 ≥ 8, чисто;
    # E5 — нарушение по календарю.
    off = checks["C-R-DAYSOFF-форма"]
    assert off["status"] == "прогнана" and off["window_coverage"]["unknown"] == 0
    assert off["windows_assumed_employees"] == 1
    assert len(by_rule["R-DAYSOFF"]) == 1 and "«E5»" in by_rule["R-DAYSOFF"][0]["evidence"]
    assert len(by_rule["R-CONSEC"]) == 1
    assert gate_certify.check(_svod(), report)[0] == []


def test_clean_known_part_does_not_certify_unknown_history():
    PROMPTS.clear()
    report = rc.certify(_svod(), _solution(bad=False), _compiler)
    v = report["certificate"]
    assert v["certified"] is False, v
    assert v["findings"] == 0
    assert v["checks_run"] == (1 if PARTIAL != "прогнана" else 4)
    assert v["window_coverage"]["unknown"] > 0
    assert v["result"]["feasibility"] == "not_established"
    assert sorted(v["rules_partially_covered"]) == ["R-DAYSOFF", "R-NIGHT", "R-ROLL"]
    assert report["solver_logic"]["green"] is True
    checks = {c["id"]: c for c in report["checks"]}
    assert "перенос серии ночных применён у 1" in checks["C-R-NIGHT-форма"]["note"]
    assert checks["C-R-ROLL-форма"]["window_coverage"]["unknown"] == 4
    assert checks["C-R-DAYSOFF-форма"]["window_coverage"]["unknown"] == 1
    assert gate_certify.check(_svod(), report)[0] == []


def test_complete_known_windows_without_violations_are_certified():
    report = rc.certify(_svod(), _fully_known_solution(), _compiler)
    assert report["certificate"]["certified"] is True, report["certificate"]
    assert report["certificate"]["checks_run"] == 4
    assert report["certificate"]["window_coverage"]["unknown"] == 0
    assert report["certificate"]["findings"] == 0
    assert gate_certify.check(_svod(), report)[0] == []


def test_continue_zero_closes_missing_records_but_not_insufficient_history():
    """continue_zero закрывает отсутствие записи (E2, E4, E6 — по
    допущению), но не запись с недостаточной историей: с E3 «до 1.03
    20:00» сертификат не выдаётся, с E3 «до 28.02» — выдаётся."""
    partial = _solution(bad=False, policy=True)
    report = rc.certify(_svod(), partial, _compiler)
    v = report["certificate"]
    assert v["certified"] is False and v["findings"] == 0
    assert v["rules_partially_covered"] == ["R-ROLL"]
    roll = next(c for c in report["checks"] if c["id"] == "C-R-ROLL-форма")
    assert {w["employee"] for w in roll["window_coverage"]["windows"] if w["status"] == "unknown"} == {"E3"}
    full = rc.certify(_svod(), _fully_known_solution(policy=True), _compiler)
    assert full["certificate"]["certified"] is True, full["certificate"]
    assert full["certificate"]["window_coverage"]["unknown"] == 0
    checks = {c["id"]: c for c in full["checks"]}
    for rid in ("R-NIGHT", "R-ROLL", "R-DAYSOFF"):
        assert checks[f"C-{rid}-форма"]["windows_assumed_employees"] == 1
        assert "continue_zero" in checks[f"C-{rid}-форма"]["windows_assumed_basis"]
    assert gate_certify.check(_svod(), full)[0] == []


def test_without_solver_readings_logic_rules_stay_with_the_compiler():
    PROMPTS.clear()
    sol = _solution(bad=True)
    sol.pop("lastRunLogic")
    report = rc.certify(_svod(), sol, _compiler)
    assert all(not c["id"].endswith("-форма") for c in report["checks"])
    assert any("R-NIGHT" in p for p in PROMPTS)


def test_missing_sheet_is_an_honest_not_run():
    sol = _solution(bad=False)
    sol.pop("duties")
    report = rc.certify(_svod(), sol, _compiler)
    night = next(c for c in report["checks"] if c["id"] == "C-R-NIGHT-форма")
    assert night["status"] == "не прогнана" and "Смены" in night["reason"]
    assert report["certificate"]["certified"] is False


def test_rolling_counter_forms_are_judged_by_code_inside_the_horizon():
    """Счётчики окон (формы DAYS_OFF_IN_ROLLING_WINDOW_MIN и
    NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX): сертификат судит окна по листам
    «Назначения» и «Смены» на всём объявленном горизонте; окна через
    границу без посуточного календаря 1.17.2 — неизвестны, адресно."""
    svod = _svod()
    svod["rules"] = [_rule("R-OFF7", "day_off", "count_within"),
                     _rule("R-N2", "duty", "count_within")]
    plans = [_plan("R-OFF7", lc.FORM_DAYS_OFF_ROLLING, 2, "SVG", condBound=7, condParam="w"),
             _plan("R-N2", lc.FORM_NIGHT_ROLLING, 0, "MOW", condBound=2, condParam="w",
                   nightFrom=22 * 60, nightTo=6 * 60)]
    gen = [{"rule": p["rules"][0], "state": "ПОРОЖДЕНО", "enforce": "required",
            "constraints": [p["constraint"]], "viaThresholds": [], "reason": None,
            "declarations": [], "shapeSha": "x", "considered": 1,
            "consideredMeasure": "стенд", "plan": p} for p in plans]
    sol = _solution(bad=True)
    sol["assignments"] = [a for a in sol["assignments"] if not a["flight"].startswith("G-E5-EARLY-")]
    sol["duties"] = [d for d in sol["duties"] if "G-E5-EARLY-" not in d["id"]]
    sol["history_calendar"] = []
    sol["lastRunLogic"] = {"blocksSeen": 2, "generated": gen, "executedElsewhere": [],
                           "ignored": [], "refused": [], "notHard": [],
                           "registered": [p["constraint"] for p in plans], "fitOn": "стенд"}
    PROMPTS.clear()
    report = rc.certify(svod, sol, _compiler)
    v = report["certificate"]
    assert v["violated_rules"] == ["R-N2", "R-OFF7"], v
    by_rule = _by_rule(report)
    # E5 и E6 (база SVG) заняты 10..31: окна 7 суток со стартом 9..25 дают
    # меньше 2 выходных — 17 окон × 2 сотрудников
    assert len(by_rule["R-OFF7"]) == 34
    assert any("2026-03-09–2026-03-15: выходных 1 меньше нормы 2" in f["text"]
               for f in by_rule["R-OFF7"])
    # E1 и E2 (база MOW) — одна ночная смена 2-го; окно 2 суток при K=0 → по
    # одному нарушению (окно 2..3); окно 1..2 у обоих неизвестно (1 марта)
    assert len(by_rule["R-N2"]) == 2
    assert "ночных смен 1 больше предела 0" in by_rule["R-N2"][0]["text"]
    checks = {c["id"]: c for c in report["checks"]}
    for rid in ("R-OFF7", "R-N2"):
        assert checks[f"C-{rid}-форма"]["status"] == PARTIAL
        assert "календаря до горизонта" in checks[f"C-{rid}-форма"]["note"]
    assert checks["C-R-OFF7-форма"]["window_coverage"]["unknown"] == 12
    assert checks["C-R-N2-форма"]["window_coverage"]["unknown"] == 2
    assert gate_certify.check(svod, report)[0] == []


def test_history_calendar_sheet_opens_windows_across_the_horizon_boundary():
    """Лист «Календарь истории» (1.17.2): окна счётчиков через границу
    горизонта судятся по нему — выходной только явный день отдыха, сутки
    без записи делают окно неизвестным; ночные сутки календаря входят в
    окна ночных смен. Те же случаи, что у решателя и оракула."""
    svod = _svod()
    svod["rules"] = [_rule("R-OFF7", "day_off", "count_within"),
                     _rule("R-N3", "duty", "count_within")]
    plans = [_plan("R-OFF7", lc.FORM_DAYS_OFF_ROLLING, 2, "SVG", condBound=7, condParam="w"),
             _plan("R-N3", lc.FORM_NIGHT_ROLLING, 2, "MOW", condBound=3, condParam="w",
                   nightFrom=22 * 60, nightTo=6 * 60)]
    gen = [{"rule": p["rules"][0], "state": "ПОРОЖДЕНО", "enforce": "required",
            "constraints": [p["constraint"]], "viaThresholds": [], "reason": None,
            "declarations": [], "shapeSha": "x", "considered": 1,
            "consideredMeasure": "стенд", "plan": p} for p in plans]
    # Горизонт 2..8 марта: E5 (SVG) работает 2..8 (7 дней подряд);
    # E6 (SVG) — тоже. Календарь до горизонта: E5 — 23..28.02, выходные 26 и
    # 27; E6 — 28.02 без записи (unknown).
    a = []
    for emp in ("E5", "E6"):
        a += _day_flights(emp, "SVG", list(range(2, 9)), f"G{emp}-", "10:00", "12:00")
    # ночные (MOW): E1 — ночные смены 2, 3, 4 марта; календарь 27.02 и 28.02 — ночные.
    for d in (2, 3, 4):
        a.append(_asg("E1", "MOW", f"N{d}", f"2026-03-{d:02d}T23:00", f"2026-03-{d + 1:02d}T01:00"))
    duties = [{"employee": "E1", "id": f"E1/N{d}", "start": f"2026-03-{d:02d}T23:00",
               "end": f"2026-03-{d + 1:02d}T01:00", "base": "MOW", "last_arr_airport": "MOW"}
              for d in (2, 3, 4)]
    cal = []
    for d in (23, 24, 25, 28):
        cal.append({"employee": "E5", "date": f"2026-02-{d}", "kind": "work", "night": False})
    for d in (26, 27):
        cal.append({"employee": "E5", "date": f"2026-02-{d}", "kind": "day_off", "night": False})
    for d in (23, 24, 25, 26, 27):
        cal.append({"employee": "E6", "date": f"2026-02-{d}", "kind": "work", "night": False})
    cal.append({"employee": "E6", "date": "2026-02-28", "kind": "unknown", "night": None})
    for d in (27, 28):
        cal.append({"employee": "E1", "date": f"2026-02-{d}", "kind": "work", "night": True})
    # 1 марта — сутки до горизонта (as_of 2.03): у всех рабочие, не ночные
    for emp in ("E1", "E5", "E6"):
        cal.append({"employee": emp, "date": "2026-03-01", "kind": "work", "night": False})
    sol = {"solution": "стенд календаря", "assignments": a, "duties": duties,
           "window_horizon": {"start_inclusive": "2026-03-02", "end_exclusive": "2026-03-09"},
           "window_employees": [{"employee": e, "base": b} for e, b in
                                [("E1", "MOW"), ("E5", "SVG"), ("E6", "SVG")]],
           "history": [{"employee": e, "as_of": "2026-03-02T00:00", "has_history": True}
                       for e in ("E1", "E5", "E6")],
           "history_calendar": cal,
           "lastRunLogic": {"blocksSeen": 2, "generated": gen, "executedElsewhere": [],
                            "ignored": [], "refused": [], "notHard": [],
                            "registered": [p["constraint"] for p in plans], "fitOn": "стенд"}}
    report = rc.certify(svod, sol, _compiler)
    by_rule = _by_rule(report)
    off = by_rule["R-OFF7"]
    # E5: окна 24.02..2 (2 выходных, чисто), 25..3 (2), 26..4 (2), 27..5 (1 —
    # нехватка), 28..6 (0), 1..7 (0), 2..8 (0) → 4 нарушения; E6: окна с 28.02
    # остаются unknown, 1..7 и 2..8 → 2 нарушения (1 марта явно рабочее).
    assert sum(1 for f in off if "«E5»" in f["evidence"]) == 4
    assert sum(1 for f in off if "«E6»" in f["evidence"]) == 2
    assert any("2026-02-27–2026-03-05" in f["text"] and "по календарю истории" in f["text"]
               for f in off)
    # ночные: E1 окна 28.02..2 (28 ночная + 2-го = 2), 1..3 (2), 2..4 (3 →
    # превышение); далее окна общего горизонта до 8 марта → 1 нарушение.
    nights = by_rule["R-N3"]
    assert len(nights) == 1 and "2026-03-02–2026-03-04" in nights[0]["text"]
    checks = {c["id"]: c for c in report["checks"]}
    assert "окон через границу горизонта судимо" in checks["C-R-OFF7-форма"]["note"]
    assert "окон через границу горизонта судимо" in checks["C-R-N3-форма"]["note"]
    assert checks["C-R-OFF7-форма"]["status"] == PARTIAL
    assert checks["C-R-OFF7-форма"]["window_coverage"]["expected"] == 14
    assert checks["C-R-OFF7-форма"]["window_coverage"]["unknown"] == 5
    assert checks["C-R-N3-форма"]["status"] == "прогнана"
    assert checks["C-R-N3-форма"]["window_coverage"]["expected"] == 7
    assert gate_certify.check(svod, report)[0] == []


# ---------------------------------------------------------------------------
# Шаг 2 плана 05.09.2026 (H0-КИ-окна): три исхода проверки окон — судилось и
# выдержано, судилось и нарушено, НЕ судилось — с числом и адресами окон;
# несудимое окно не считается выдержанным: правило покрыто частично,
# допустимость по нему не установлена. Заявление «истории нет» по сотруднику
# при continue_zero — принятое допущение нулевых остатков: окна судятся по
# допущению. Стенды без объявленного window_horizon (пакеты до раздела
# manifest.horizon): горизонт выводится по назначениям и назван inferred.
# ---------------------------------------------------------------------------

def _roll_only_svod():
    svod = _svod()
    svod["rules"] = [_rule("R-ROLL", "rest", "duration_within")]
    return svod


def _roll_logic():
    p = _plan("R-ROLL", lc.FORM_ROLLING, 2160, "PLY", condBound=7, condParam="w")
    gen = [{"rule": "R-ROLL", "state": "ПОРОЖДЕНО", "enforce": "required",
            "constraints": [p["constraint"]], "viaThresholds": [], "reason": None,
            "declarations": [], "shapeSha": "x", "considered": 1,
            "consideredMeasure": "стенд", "plan": p}]
    return {"blocksSeen": 1, "generated": gen, "executedElsewhere": [], "ignored": [],
            "refused": [], "notHard": [], "registered": [p["constraint"]], "fitOn": "стенд"}


def test_rolling_rest_windows_have_three_outcomes_with_addresses():
    """Решение пользователя continue_partial: E3 — история с концом
    последней смены (окна после него судятся, ячейки до него неизвестны);
    E4 — строка «истории нет» и E8 — записи нет вовсе: их ячейки через
    границу НЕ судятся, адресно, с причиной «решение пользователя:
    continue_partial», и правило покрыто частично — допустимость не
    установлена. Горизонт не объявлен — выведен по назначениям."""
    PROMPTS.clear()
    a = []
    for emp in ("E3", "E4", "E8"):
        a += _day_flights(emp, "PLY", [3, 4, 5, 8, 9, 10], f"F{emp}-")
    sol = {"solution": "стенд трёх исходов", "assignments": a, "duties": _duties_of(a),
           "history": [{"employee": "E3", "as_of": "2026-03-03T00:00", "has_history": True,
                        "last_duty_end": "2026-03-01T20:00"},
                       {"employee": "E4", "as_of": "2026-03-03T00:00", "has_history": False}],
           "history_policy": {"coverage_kind": "partial", "assume_zero_when_missing": False,
                              "user_decision": {"question": "Q-3b", "key": "б",
                                                "decision": "continue_partial"}},
           "lastRunLogic": _roll_logic()}
    report = rc.certify(_roll_only_svod(), sol, _compiler)
    c = {x["id"]: x for x in report["checks"]}["C-R-ROLL-форма"]
    assert c["status"] == PARTIAL
    cov = c["window_coverage"]
    assert cov["horizon"] == {"start_inclusive": "2026-03-03", "end_exclusive": "2026-03-11",
                              "source": lc.HORIZON_INFERRED,
                              "basis": cov["horizon"]["basis"]}
    assert c["horizon_inferred"] is True
    # по 7 ячеек на сотрудника (точки и открытые интервалы), по 2 неизвестных
    # у каждого: до конца последней смены (E3) либо до начала горизонта (E4, E8)
    assert cov["expected"] == 21 and cov["unknown"] == 6 and cov["evaluated"] == 15
    assert c["windows_unjudged"] == 6 and c["unjudged_windows"] == 6
    reasons = c["unjudged_by_reason"]
    assert sum(n for r, n in reasons.items() if "continue_partial" in r) == 4
    assert sum(n for r, n in reasons.items() if "continue_partial" not in r) == 2
    sample = c["unjudged_sample"]
    assert {u["employee"] for u in sample} == {"E3", "E4", "E8"}
    assert all("window_from" in u and "window_to" in u and u["reason"] for u in sample)
    assert "windows_assumed_employees" not in c
    assert "окон не судилось: 6" in c["note"] and "не считается выдержанным" in c["note"]
    assert c["windows_judged"] == 15 and c["windows_violated"] == 0
    # C-27b: выведенный горизонт — частичная диагностика, названа в причине
    assert "частичная диагностика" in c["reason"]
    v = report["certificate"]
    assert v["findings"] == 0
    assert v["rules_partially_covered"] == ["R-ROLL"]
    assert v["rules_horizon_inferred"] == ["R-ROLL"]
    assert v["result"]["feasibility"] == "not_established"
    assert v["certified"] is False
    from vmp.run import verdicts as vd
    assert vd.verdict_words(v["result"], v).startswith("не удостоверено: непроверено 1 правил")
    assert gate_certify.check(_roll_only_svod(), report)[0] == []


def test_rolling_rest_without_history_sheet_names_boundary_windows_unjudged():
    """История не передана вовсе: ячейки через границу у каждого
    сотрудника не судятся с причиной «история не передана» —
    молчаливого положительного итога нет."""
    PROMPTS.clear()
    a = _day_flights("E4", "PLY", [3, 4, 5, 8, 9, 10], "FE4-")
    sol = {"solution": "стенд без истории", "assignments": a, "duties": _duties_of(a),
           "lastRunLogic": _roll_logic()}
    report = rc.certify(_roll_only_svod(), sol, _compiler)
    c = {x["id"]: x for x in report["checks"]}["C-R-ROLL-форма"]
    assert c["windows_unjudged"] == 2
    assert all("история не передана" in r for r in c["unjudged_by_reason"])
    assert report["certificate"]["rules_partially_covered"] == ["R-ROLL"]
    assert report["certificate"]["result"]["feasibility"] == "not_established"


def test_days_off_window_unknown_calendar_day_is_unjudged_with_address():
    """Окно выходных через границу: сутки до горизонта без записи в
    календаре истории делают окно неизвестным — окна названы адресно
    (сотрудник, окно, причина с датой), остальные судятся; правило
    покрыто частично."""
    PROMPTS.clear()
    svod = _svod()
    svod["rules"] = [_rule("R-OFF7", "day_off", "count_within")]
    p = _plan("R-OFF7", lc.FORM_DAYS_OFF_ROLLING, 2, "SVG", condBound=7, condParam="w")
    gen = [{"rule": "R-OFF7", "state": "ПОРОЖДЕНО", "enforce": "required",
            "constraints": [p["constraint"]], "viaThresholds": [], "reason": None,
            "declarations": [], "shapeSha": "x", "considered": 1,
            "consideredMeasure": "стенд", "plan": p}]
    a = _day_flights("E6", "SVG", list(range(2, 9)), "GE6-", "10:00", "12:00")
    cal = [{"employee": "E6", "date": f"2026-02-{d}", "kind": "work", "night": False}
           for d in (23, 24, 25, 26, 27)]
    cal.append({"employee": "E6", "date": "2026-02-28", "kind": "unknown", "night": None})
    cal.append({"employee": "E6", "date": "2026-03-01", "kind": "work", "night": False})
    sol = {"solution": "стенд календаря с пробелом", "assignments": a, "duties": [],
           "history": [{"employee": "E6", "as_of": "2026-03-02T00:00", "has_history": True}],
           "history_calendar": cal,
           "lastRunLogic": {"blocksSeen": 1, "generated": gen, "executedElsewhere": [],
                            "ignored": [], "refused": [], "notHard": [],
                            "registered": [p["constraint"]], "fitOn": "стенд"}}
    report = rc.certify(svod, sol, _compiler)
    c = {x["id"]: x for x in report["checks"]}["C-R-OFF7-форма"]
    # окна 24.02–2.03 … 28.02–6.03 содержат 28.02 без записи → 5 неизвестных;
    # 1.03–7.03 и 2.03–8.03 судятся, оба нарушены (выходных 0 < 2)
    assert c["windows_unjudged"] == 5
    assert list(c["unjudged_by_reason"].values()) == [5]
    assert "2026-02-28" in next(iter(c["unjudged_by_reason"]))
    assert [u["window_from"] for u in c["unjudged_sample"]] == \
        ["2026-02-24", "2026-02-25", "2026-02-26", "2026-02-27", "2026-02-28"]
    assert c["unjudged_sample"][0]["window_to"] == "2026-03-03"
    assert c["windows_judged"] == 2 and c["windows_violated"] == 2
    assert report["certificate"]["rules_partially_covered"] == ["R-OFF7"]
    assert report["certificate"]["result"]["feasibility"] == "violated"


def test_rolling_rest_continue_partial_leaves_missing_employee_windows_unjudged():
    """Флаги раздела history (семантика CDPM, 05.09.2026): при решении
    continue_partial сотрудник без записи в истории судится только там,
    где занятость известна — ячейки через границу не судятся (третий
    исход с адресами и причиной «решение пользователя: continue_partial»);
    при continue_zero (assume_zero_when_missing) те же ячейки судятся по
    допущению. Запись с пустым концом последней смены — неизвестно, не
    ноль: не судится при любом решении."""
    PROMPTS.clear()
    a = []
    for emp in ("E3", "E4", "E9"):
        a += _day_flights(emp, "PLY", [3, 4, 5, 8, 9, 10], f"F{emp}-")
    history = [{"employee": "E3", "as_of": "2026-03-03T00:00", "has_history": True,
                "last_duty_end": "2026-03-01T20:00"},
               {"employee": "E4", "as_of": "2026-03-03T00:00", "has_history": False},
               {"employee": "E9", "as_of": "2026-03-03T00:00", "has_history": True,
                "last_duty_end": None}]
    partial = {"solution": "стенд continue_partial", "assignments": a, "duties": _duties_of(a),
               "history": history,
               "history_policy": {"coverage_kind": "partial", "assume_zero_when_missing": False,
                                  "user_decision": {"question": "Q-3b", "key": "б",
                                                    "decision": "continue_partial"}},
               "lastRunLogic": _roll_logic()}
    c = {x["id"]: x for x in rc.certify(_roll_only_svod(), partial, _compiler)["checks"]}["C-R-ROLL-форма"]
    assert c["windows_unjudged"] == 6
    reasons = c["unjudged_by_reason"]
    assert sum(n for r, n in reasons.items() if "continue_partial" in r) == 2   # E4
    assert sum(n for r, n in reasons.items() if "continue_partial" not in r) == 4  # E3, E9
    assert "windows_assumed_employees" not in c
    zero = dict(partial, solution="стенд continue_zero",
                history_policy={"coverage_kind": "partial", "assume_zero_when_missing": True,
                                "user_decision": {"question": "Q-3b", "key": "в",
                                                  "decision": "continue_zero"}})
    c2 = {x["id"]: x for x in rc.certify(_roll_only_svod(), zero, _compiler)["checks"]}["C-R-ROLL-форма"]
    assert c2["windows_assumed_employees"] == 1
    assert "continue_zero" in c2["note"]
    # E9 с пустым концом смены и E3 с недостаточной историей не судятся и
    # при нулевом допущении
    assert c2["windows_unjudged"] == 4
    assert {w["employee"] for w in c2["window_coverage"]["windows"] if w["status"] == "unknown"} == {"E3", "E9"}
    assert all("continue_partial" not in r for r in c2["unjudged_by_reason"])


# ---------------------------------------------------------------------------
# C-31 (ревью группы C, 06.09.2026): форма без единого окна в объявленном
# горизонте — «не применима»; правило покрыто (проверять нечего), сертификат
# не «не установлена»; но вакуумный сертификат из одних «не применима»
# невозможен. Регрессия демо-набора polyaris-h0kw @ 7 суток: помесячная
# норма выходных (days_off_floor, min_days_off = 8) на семи сутках без
# полного месяца называла правило непокрытым.
# ---------------------------------------------------------------------------

def _logic_of(plans):
    gen = [{"rule": p["rules"][0], "state": "ПОРОЖДЕНО", "enforce": "required",
            "constraints": [p["constraint"]], "viaThresholds": [], "reason": None,
            "declarations": [], "shapeSha": "x", "considered": 1,
            "consideredMeasure": "стенд", "plan": p} for p in plans]
    return {"blocksSeen": len(plans), "generated": gen, "executedElsewhere": [],
            "ignored": [], "refused": [], "notHard": [],
            "registered": [p["constraint"] for p in plans], "fitOn": "стенд"}


def _seven_day_solution(declared: bool = True):
    """Семисуточный горизонт 2..8 марта (как у демо-набора polyaris-h0kw
    @ 7 суток): ни одного полного календарного месяца — у помесячной
    формы выходных окон нет; серия рабочих дней E5 (SVG) 2..4 марта с
    историей (перенос 0 при срезе = начало горизонта) известна целиком."""
    a = _day_flights("E5", "SVG", [2, 3, 4], "G-", "10:00", "12:00")
    sol = {"solution": "стенд семи суток", "assignments": a, "duties": _duties_of(a),
           "history": [{"employee": "E5", "as_of": "2026-03-02T00:00", "has_history": True,
                        "consecutive_duty_days": 0}],
           "window_employees": [{"employee": "E5", "base": "SVG"}],
           "lastRunLogic": _logic_of([_plan("R-DAYSOFF", lc.FORM_DAYS_OFF, 8, "SVG"),
                                      _plan("R-CONSEC", lc.FORM_CONSECUTIVE, 8, "SVG")])}
    if declared:
        sol["window_horizon"] = {"start_inclusive": "2026-03-02", "end_exclusive": "2026-03-09"}
    return sol


def _two_rule_svod():
    svod = _svod()
    svod["rules"] = [_rule("R-DAYSOFF", "day_off", "count_within"),
                     _rule("R-CONSEC", "day", "count_within")]
    return svod


def test_form_without_windows_in_horizon_is_not_applicable_and_rule_is_covered():
    PROMPTS.clear()
    svod = _two_rule_svod()
    report = rc.certify(svod, _seven_day_solution(), _compiler)
    checks = {c["id"]: c for c in report["checks"]}
    off = checks["C-R-DAYSOFF-форма"]
    assert off["status"] == lc.NOT_APPLICABLE_STATUS
    assert off["window_coverage"]["expected"] == 0 and off["window_coverage"]["windows"] == []
    assert "нет окон" in off["reason"] and off["rows_checked"] == 0
    con = checks["C-R-CONSEC-форма"]
    assert con["status"] == "прогнана" and con["window_coverage"]["unknown"] == 0
    v = report["certificate"]
    # правило покрыто: не в непокрытых и не в частично покрытых
    assert v["rules_uncovered"] == [] and v["rules_partially_covered"] == []
    # в прогнанные «не применима» не входит; в счёте непрогнанных шапки
    # остаётся (гейт сверяет checks_run + checks_not_run с телом)
    assert v["checks_run"] == 1 and v["checks_not_applicable"] == 1
    assert v["checks_not_run"] == 1
    assert v["findings"] == 0
    assert v["result"]["feasibility"] == "confirmed"
    assert v["result"]["coverage_complete"] is True
    assert v["certified"] is True, v
    assert lc.NOT_APPLICABLE_STATUS in v["note"]
    md = rc.render_certificate(report)
    assert "ДОПУСТИМОСТЬ ПОДТВЕРЖДЕНА" in md
    assert "не применимо к горизонту 1" in md
    assert gate_certify.check(svod, report)[0] == []


def test_only_not_applicable_checks_do_not_make_a_vacuous_certificate():
    svod = _svod()
    svod["rules"] = [_rule("R-DAYSOFF", "day_off", "count_within")]
    sol = _seven_day_solution()
    sol["lastRunLogic"] = _logic_of([_plan("R-DAYSOFF", lc.FORM_DAYS_OFF, 8, "SVG")])
    report = rc.certify(svod, sol, _compiler)
    v = report["certificate"]
    assert v["checks_run"] == 0 and v["checks_not_applicable"] == 1
    assert v["rules_uncovered"] == [] and v["findings"] == 0
    assert v["certified"] is False
    assert v["result"]["feasibility"] == "not_established"
    assert "ни одна проверка не прогнана" in v["note"]
    assert "НЕ СОСТОЯЛАСЬ" in rc.render_certificate(report)
    assert gate_certify.check(svod, report)[0] == []


def test_inferred_horizon_gives_partial_coverage_not_a_certificate():
    """C-27b (план §3.10.3): тот же полностью известный стенд БЕЗ
    объявленного window_horizon — горизонт выведен по назначениям
    (source = inferred): проверки прогнаны с той же арифметикой, но это
    частичная диагностика, не pass — оба правила покрыты частично,
    допустимость не установлена, сертификат не выдаётся."""
    PROMPTS.clear()
    svod = _two_rule_svod()
    report = rc.certify(svod, _seven_day_solution(declared=False), _compiler)
    checks = {c["id"]: c for c in report["checks"]}
    con, off = checks["C-R-CONSEC-форма"], checks["C-R-DAYSOFF-форма"]
    assert con["status"] == "прогнана" and con["horizon_inferred"] is True
    assert con["window_coverage"]["horizon"]["source"] == lc.HORIZON_INFERRED
    assert lc.HORIZON_INFERRED_REASON in con["reason"]
    assert off["status"] == lc.NOT_APPLICABLE_STATUS and off["horizon_inferred"] is True
    assert "выведенном горизонте" in off["reason"] and "частичная диагностика" in off["reason"]
    v = report["certificate"]
    assert v["findings"] == 0 and v["checks_run"] == 1
    assert v["rules_horizon_inferred"] == ["R-CONSEC", "R-DAYSOFF"]
    assert v["rules_partially_covered"] == ["R-CONSEC", "R-DAYSOFF"]
    assert v["rules_uncovered"] == ["R-CONSEC", "R-DAYSOFF"]
    assert v["result"]["feasibility"] == "not_established"
    assert v["result"]["coverage_complete"] is False
    assert v["certified"] is False
    assert "выведенном по назначениям" in v["note"]
    from vmp.run import verdicts as vd
    assert vd.verdict_words(v["result"], v).startswith("не удостоверено: непроверено 2 правил")
    assert "НЕ УДОСТОВЕРЕНО" in rc.render_certificate(report)
    assert gate_certify.check(svod, report)[0] == []


def test_explicit_inferred_source_cannot_be_promoted_to_declared():
    """C-27b-R1 (ревью GPT-029): тот же семисуточный стенд, но объект
    window_horizon ПЕРЕДАН с source = inferred (либо явным признаком
    inferred=True) — это выведенное происхождение, не объявленный
    горизонт: horizon_inferred ставится, правила покрыты частично,
    сертификат не выдаётся — как при отсутствии объекта. Обычный явный
    источник по-прежнему объявленный."""
    PROMPTS.clear()
    svod = _two_rule_svod()
    for carrier in ({"source": "inferred"}, {"source": "Inferred by exporter"},
                    {"inferred": True}):
        sol = _seven_day_solution()
        sol["window_horizon"].update(carrier)
        report = rc.certify(svod, sol, _compiler)
        checks = {c["id"]: c for c in report["checks"]}
        con, off = checks["C-R-CONSEC-форма"], checks["C-R-DAYSOFF-форма"]
        assert con["horizon_inferred"] is True and off["horizon_inferred"] is True, carrier
        assert con["window_coverage"]["horizon"]["source"] == lc.HORIZON_INFERRED
        assert con["window_coverage"]["horizon"]["start_inclusive"] == "2026-03-02"
        assert "помечен как выведенный" in con["window_coverage"]["horizon"]["basis"]
        assert lc.HORIZON_INFERRED_REASON in con["reason"]
        v = report["certificate"]
        assert v["rules_horizon_inferred"] == ["R-CONSEC", "R-DAYSOFF"], carrier
        assert v["rules_partially_covered"] == ["R-CONSEC", "R-DAYSOFF"]
        assert v["result"]["feasibility"] == "not_established"
        assert v["result"]["coverage_complete"] is False
        assert v["certified"] is False, carrier
        assert gate_certify.check(svod, report)[0] == []
    # Явный НЕвыведенный источник — объявленный горизонт, сертификат есть.
    sol = _seven_day_solution()
    sol["window_horizon"]["source"] = "объявлен пакетом постановки"
    report = rc.certify(svod, sol, _compiler)
    checks = {c["id"]: c for c in report["checks"]}
    assert not checks["C-R-CONSEC-форма"].get("horizon_inferred")
    assert checks["C-R-CONSEC-форма"]["window_coverage"]["horizon"]["source"] == "объявлен пакетом постановки"
    assert report["certificate"]["rules_horizon_inferred"] == []
    assert report["certificate"]["certified"] is True


def test_daily_form_is_kept_as_legacy_diagnostic_not_as_status_or_proof():
    """K-15 / C-30 (план §3.10.3): суточная форма main (старты окон по
    календарным суткам, версия показаний cells/1) сохранена как
    legacy-диагностика проверки отдыха в скользящем окне —
    check["daily_diagnostic"], не статус и не доказательство: статус,
    покрытие, находки и вердикт определяет непрерывная форма."""
    PROMPTS.clear()
    report = rc.certify(_svod(), _solution(bad=True), _compiler)
    roll = {c["id"]: c for c in report["checks"]}["C-R-ROLL-форма"]
    diag = roll["daily_diagnostic"]
    assert diag["version"] == lc.DAILY_DIAGNOSTIC_VERSION == "cells/1"
    assert diag["form"] == "daily_window_starts"
    assert "не доказательство" in diag["note"] and "legacy" in diag["note"]
    # арифметика main: E3 (история до 1.03 20:00) — старты 2..4 марта,
    # E4 (без записи, решения нет) — старты 3..4 и шесть несудимых окон
    # через границу; работа 3..10 без 36 ч отдыха — все окна нарушены
    assert (diag["windows_judged"], diag["windows_violated"], diag["windows_unjudged"]) == (5, 5, 6)
    assert diag["windows_opened_by_history"] == 1 and diag["windows_assumed_employees"] == 0
    assert {u["employee"] for u in diag["unjudged_sample"]} == {"E4"}
    # лучший промежуток окна 2..8 марта у E3: с конца последней смены
    # (1.03 20:00) до первого вылета 3.03 06:00, внутри окна — 30 ч
    assert diag["violations_sample"][0] == {"employee": "E3", "window_from": "2026-03-02",
                                            "window_to": "2026-03-08", "best_rest_min": 1800}
    # единицы разные: суточные окна ≠ критические ячейки покрытия
    cov = roll["window_coverage"]
    assert cov["granularity"] == "continuous_critical_cells"
    assert (cov["expected"], cov["evaluated"], cov["unknown"]) == (14, 10, 4)
    assert diag["windows_judged"] != cov["evaluated"]
    # статус, счётчики и находки — только от непрерывного покрытия
    assert roll["status"] == PARTIAL
    assert roll["windows_judged"] == cov["evaluated"] and roll["windows_unjudged"] == cov["unknown"]
    assert roll["windows_violated"] == sum(
        1 for w in cov["windows"] if w.get("outcome") == "violation") == 4
    assert len([f for f in report["findings"] if f["rule_refs"] == ["R-ROLL"]]) == 4
    # диагностика без нарушений не поднимает статус чистого стенда
    clean = rc.certify(_svod(), _solution(bad=False), _compiler)
    roll2 = {c["id"]: c for c in clean["checks"]}["C-R-ROLL-форма"]
    assert roll2["daily_diagnostic"]["windows_violated"] == 0
    assert roll2["status"] == PARTIAL and roll2["window_coverage"]["unknown"] == 4
    assert clean["certificate"]["certified"] is False
    # несудимые суточные окна диагностики не отзывают сертификат полного стенда
    full = rc.certify(_svod(), _fully_known_solution(), _compiler)
    roll3 = {c["id"]: c for c in full["checks"]}["C-R-ROLL-форма"]
    assert roll3["daily_diagnostic"]["version"] == "cells/1"
    assert full["certificate"]["certified"] is True, full["certificate"]
    # при continue_zero диагностика называет допущение (assumedFreeFrom)
    zero = rc.certify(_svod(), _solution(bad=True, policy=True), _compiler)
    diag4 = {c["id"]: c for c in zero["checks"]}["C-R-ROLL-форма"]["daily_diagnostic"]
    assert diag4["windows_assumed_employees"] == 1 and "continue_zero" in diag4["windows_assumed_basis"]
    assert diag4["windows_unjudged"] == 0
    for rep in (report, clean, full):
        assert gate_certify.check(_svod(), rep)[0] == []
