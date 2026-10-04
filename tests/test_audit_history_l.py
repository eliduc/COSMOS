# -*- coding: utf-8 -*-
"""Ступень L аудита A7 — раздел «история сотрудника» пакета (ревизия
1.17, проект): сторона COSMOS.

Стенды с известным ответом:
  1. раздел переносится в набор решателя по сотруднику плоскими полями,
     сотрудник без записи получает history=null явно, шапка раздела несёт
     поимённо сотрудников без истории;
  2. выгрузка решения несёт строки листа «История» сертификатора;
  3. сертификат читает историю: отдых до первой смены горизонта короче
     пола дома — нарушение с доказательством; налёт месяца среза с
     остатком больше предела — нарушение; без истории те же решения чисты
     (допущение «остаток = 0» действует и названо);
  4. кнопка: раздел передан, у сотрудника нет записи, нулевая подстановка
     запрещена и есть правила накопителей — остановка словами; при
     assume_zero_when_missing=true — нет.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from webapp import task_package as tp                 # noqa: E402
from webapp import solution_export as se              # noqa: E402
from vmp import history_assumption as ha              # noqa: E402
from vmp.run import roster_certify as rc              # noqa: E402
from cdpm import data_report as dr                    # noqa: E402
from cdpm.gates import gate_certify                   # noqa: E402


def _history_section(with_e2: bool = True, assume_zero: bool = False) -> dict:
    emps = [{"employee_id": "E1", "month_to_date": {"block_min": 200, "work_min": 300,
                                                    "flight_time_min": 200,
                                                    "days_worked": 5, "landings": 6},
             "last_duty": {"end": "2026-01-14T22:00", "end_station": "PLY",
                           "work_min": 400, "span_min": 500, "night": True},
             "rest_before_horizon_min": 480,
             "series": {"consecutive_duty_days": 3, "consecutive_night_duties": 1,
                        "days_since_day_off": 3},
             "location": "PLY"}]
    if with_e2:
        emps.append({"employee_id": "E2", "month_to_date": {"block_min": 0},
                     "last_duty": {"end": "2026-01-10T10:00", "end_station": "SVG"},
                     "series": {"consecutive_duty_days": 0}, "location": "SVG"})
    return {"as_of": "2026-01-15T00:00", "coverage": {"employees_total": 2,
                                                        "with_history": len(emps)},
            "assume_zero_when_missing": assume_zero, "employees": emps}


def test_payload_carries_history_per_employee_and_null_when_missing():
    pkg = {"manifest": {"history": _history_section(with_e2=False)}}
    employees = [{"id": "E1", "homeAirport": "PLY"}, {"id": "E2", "homeAirport": "PLY"}]
    meta = tp._attach_history(pkg, employees)
    assert meta["as_of"] == "2026-01-15T00:00"
    assert meta["assume_zero_when_missing"] is False
    assert meta["employees_without_history"] == ["E2"]
    e1, e2 = employees
    assert e1["historyAsOf"] == "2026-01-15T00:00"
    assert e1["history"]["historyMonthBlockMin"] == 200
    assert e1["history"]["historyLastDutyEnd"] == "2026-01-14T22:00"
    assert e1["history"]["historyLastDutyStation"] == "PLY"
    assert e1["history"]["historyConsecutiveDutyDays"] == 3
    assert e1["history"]["historyLocation"] == "PLY"
    assert e2["history"] is None and "historyAsOf" not in e2
    # спецификация 1.17: сотрудник без строки листа присутствует в
    # разделе со всеми блоками null — истории у него нет
    pkg2 = {"manifest": {"history": _history_section(with_e2=False)}}
    pkg2["manifest"]["history"]["employees"].append(
        {"employee_id": "E2", "month_to_date": None, "rolling": None,
         "last_duty": None, "series": None, "location": None,
         "provenance": None})
    emps = [{"id": "E1"}, {"id": "E2"}]
    meta2 = tp._attach_history(pkg2, emps)
    assert meta2["employees_without_history"] == ["E2"] and emps[1]["history"] is None
    assert ha.missing_history(pkg2["manifest"],
                              {"tables": {"Экипажи": {"rows": [
                                  {"Табельный номер": "E1"},
                                  {"Табельный номер": "E2"}]}}}) == ["E2"]
    # окна rolling переносятся как есть
    pkg3 = {"manifest": {"history": _history_section(with_e2=False)}}
    pkg3["manifest"]["history"]["employees"][0]["rolling"] = {"last_7d_work_min": 1500}
    emps3 = [{"id": "E1"}]
    tp._attach_history(pkg3, emps3)
    assert emps3[0]["history"]["historyRolling"] == {"last_7d_work_min": 1500}
    # раздела нет — полей нет вовсе (прежние наборы не меняются)
    employees2 = [{"id": "E1"}]
    assert tp._attach_history({"manifest": {}}, employees2) is None
    assert "history" not in employees2[0]


def test_approved_assignments_become_pinned_seats_fail_closed():
    """Утверждённые назначения раздела (канон «Рейс» + «Дата») закрепляют
    место рейса той же роли за сотрудником; рейс вне набора, чужой
    сотрудник, занятая роль — отказ словами, не пропуск."""
    import pytest
    employees = [{"id": "H01", "skills": ["Captain"]},
                 {"id": "H02", "skills": ["FirstOfficer"]}]
    flights = [{"flightNumber": "PL101|2026-09-04T06:00:00",
                "departureUTCDateTime": "2026-09-04T06:00:00"},
               {"flightNumber": "PL101|2026-09-05T06:00:00",
                "departureUTCDateTime": "2026-09-05T06:00:00"}]

    def seats():
        return [{"id": "A001-0", "flight": "PL101|2026-09-04T06:00:00",
                 "indexInFlight": 0, "requiredSkill": "Captain"},
                {"id": "A001-1", "flight": "PL101|2026-09-04T06:00:00",
                 "indexInFlight": 1, "requiredSkill": "FirstOfficer"}]

    def pkg(entries):
        return {"manifest": {"history": {"employees": entries}}}
    a = seats()
    pins = tp._apply_approved_assignments(
        pkg([{"employee_id": "H01", "approved_assignments": [
            {"Рейс": "PL101", "Дата": "04.09.2026",
             "provenance": {"sid": "D1", "sheet": "Утверждённые назначения", "row": 2}}]}]),
        employees, flights, a)
    assert pins == [{"employee": "H01", "flight": "PL101|2026-09-04T06:00:00",
                     "seat": "A001-0", "role": "Captain",
                     "provenance": {"sid": "D1", "sheet": "Утверждённые назначения", "row": 2}}]
    assert a[0]["employee"] == "H01" and a[0]["pinned"] is True
    assert "pinned" not in a[1]
    # без раздела — ничего не меняется
    assert tp._apply_approved_assignments({"manifest": {}}, employees, flights, seats()) == []
    # шаг кнопки ростеринга: закрепления кладутся в набор и в шапку истории
    payload = {"employees": employees, "flights": flights,
               "flightAssignments": seats(), "history": {"as_of": "2026-09-04T00:00"}}
    got = tp.apply_history_pins(
        pkg([{"employee_id": "H02", "approved_assignments": [
            {"Рейс": "PL101", "Дата": "04.09.2026"}]}]), payload)
    assert [p["seat"] for p in got] == ["A001-1"]
    assert payload["history"]["approved_assignments_pinned"] == got
    assert payload["flightAssignments"][1]["pinned"] is True
    # to_payload сам закрепления не ставит (пейринг и карта конвейера)
    src = open(tp.__file__, encoding="utf-8").read()
    assert "apply_history_pins(" not in src.split("def to_payload(")[1].split("\ndef ")[0]
    # рейс с этой датой не в наборе
    with pytest.raises(tp.TaskPackageRefused, match="рейса с этой датой в наборе нет"):
        tp._apply_approved_assignments(
            pkg([{"employee_id": "H01", "approved_assignments": [
                {"Рейс": "PL101", "Дата": "06.09.2026"}]}]), employees, flights, seats())
    # сотрудника нет на листе «Экипажи»
    with pytest.raises(tp.TaskPackageRefused, match="нет на листе"):
        tp._apply_approved_assignments(
            pkg([{"employee_id": "H99", "approved_assignments": [
                {"Рейс": "PL101", "Дата": "04.09.2026"}]}]), employees, flights, seats())
    # два капитана на один рейс с одним местом капитана — второму места нет
    with pytest.raises(tp.TaskPackageRefused, match="нет свободного места роли Captain"):
        tp._apply_approved_assignments(
            pkg([{"employee_id": "H01", "approved_assignments": [
                     {"Рейс": "PL101", "Дата": "04.09.2026"}]},
                 {"employee_id": "H03", "approved_assignments": [
                     {"Рейс": "PL101", "Дата": "2026-09-04"}]}]),
            employees + [{"id": "H03", "skills": ["Captain"]}], flights, seats())


def test_solution_export_history_rows():
    payload = {"history": {"as_of": "2026-01-15T00:00"},
               "employees": [
                   {"id": "E1", "historyAsOf": "2026-01-15T00:00",
                    "history": tp.history_record(_history_section()["employees"][0])},
                   {"id": "E2", "history": None}]}
    rows = se.history_rows(payload)
    assert [r["employee"] for r in rows] == ["E1", "E2"]
    assert rows[0]["has_history"] is True and rows[0]["month_block_min"] == 200
    assert rows[0]["last_duty_end"] == "2026-01-14T22:00"
    assert rows[1]["has_history"] is False and "month_block_min" not in rows[1]
    assert se.history_rows({"employees": []}) is None


def test_solution_dataset_carries_location_history_and_cost_context():
    """C-9 (план §3.10.3 K-1, контракт Б): явные интервалы местоположения
    решения (cost_history_intervals) — лист «История местоположений»
    набора сертификатора (Табельный номер, Начало, Конец, Станция);
    контекст цены и запись решателя о ночёвках едут в набор как есть.
    Без интервалов листа нет — вклад «неизвестен» по замыслу (Н0)."""
    sol = _solution(with_history=True)
    sol["cost_history_intervals"] = [{"employee": "E1", "start": "2026-01-10T00:00",
                                      "end": "2026-01-15T00:00", "station": "PLY"}]
    sol["cost_context"] = {"night_weight": 10, "bases": ["PLY"]}
    sol["solver_layover_cost"] = {"events": [], "known_subtotal": 0, "complete": False}
    dataset = rc.build_solution_dataset(sol)
    sheet = dataset["tables"]["SOL"]["История местоположений"]
    assert sheet["columns"] == ["Табельный номер", "Начало", "Конец", "Станция"]
    assert sheet["rows"] == [{"Табельный номер": "E1", "Начало": "2026-01-10T00:00",
                              "Конец": "2026-01-15T00:00", "Станция": "PLY"}]
    assert dataset["cost_context"] == sol["cost_context"]
    assert dataset["solver_layover_cost"] == sol["solver_layover_cost"]
    assert dataset["cost_history_intervals"] == sol["cost_history_intervals"]
    assert dataset["window_horizon"] == sol["window_horizon"]
    plain = rc.build_solution_dataset(_solution(with_history=True))
    assert "История местоположений" not in plain["tables"]["SOL"]
    assert "cost_context" not in plain and "solver_layover_cost" not in plain
    # интервал не объект — отказ словами, не молчаливый пропуск строки
    bad = _solution(with_history=True)
    bad["cost_history_intervals"] = ["E1"]
    with pytest.raises(rc.CertifyError):
        rc.build_solution_dataset(bad)


def _rule(rid, family, params, zone="ROSTERING"):
    return {"id": rid, "title": rid, "statement": "правило " + rid,
            "zone": zone, "kind": "HARD",
            "template_fit": {"category": "A", "family": family,
                             "params": {k: {"value": v} for k, v in params.items()}},
            "sources": [{"sid": "S1", "anchor": "п.1"}]}


def _svod():
    return {"meta": {"customer": "Т", "mode": 1, "cdpm_version": "0.1"},
            "sources": [{"sid": "S1", "file": "a.md", "sha256": "0" * 64}],
            "rules": [_rule("R-REST-H", "rest_floors", {"min_rest_home": 720}),
                      _rule("R-MONTH", "span_accumulators", {"max_monthly_block": 300})],
            "questions": [], "objectives": []}


def _solution(with_history: bool):
    sol = {"solution": "стенд истории",
           # Объявленный горизонт и состав (C-43): первый отдых и префикс
           # месяца судятся на границе истории, а не выводятся из назначений.
           "window_horizon": {"start_inclusive": "2026-01-15T00:00",
                              "end_exclusive": "2026-02-01T00:00",
                              "source": "явный календарный горизонт стенда"},
           "window_employees": [{"employee": "E1", "base": "PLY"}],
           "assignments": [
               {"employee": "E1", "flight": "PL101", "dep_time": "2026-01-15T06:00",
                "arr_time": "2026-01-15T08:00", "dep_airport": "PLY",
                "arr_airport": "SVG", "duty_id": "E1/D1", "base": "PLY"}],
           "duties": [{"employee": "E1", "id": "E1/D1", "start": "2026-01-15T06:00",
                       "end": "2026-01-15T08:00", "base": "PLY",
                       "last_arr_airport": "SVG"}]}
    if with_history:
        sol["history"] = se.history_rows({
            "history": {"as_of": "2026-01-15T00:00"},
            "employees": [{"id": "E1", "historyAsOf": "2026-01-15T00:00",
                           "history": tp.history_record(_history_section()["employees"][0])}]})
    return sol


def _compiler(prompt):
    return json.dumps({"checks": [], "skipped": []}, ensure_ascii=False)


def test_certificate_reads_history_for_rest_and_monthly_carry():
    # Контракт Б (C-43) устраняет прежний ложный зелёный: известный налёт
    # 120 ≤ 300 не доказывает полный месяц, первый отдых без истории
    # неизвестен — допустимость не установлена, нарушений нет.
    clean = rc.certify(_svod(), _solution(with_history=False), _compiler)
    assert clean["certificate"]["certified"] is False, clean["certificate"]
    assert clean["certificate"]["result"]["feasibility"] == "not_established"
    assert clean["certificate"]["violated_rules"] == []
    assert clean["certificate"]["window_coverage"]["unknown"] == 2
    assert gate_certify.check(_svod(), clean)[0] == []
    # Решение пользователя continue_zero (main 1.17.2): без записи в
    # истории первый отдых и префикс месяца судятся по названному
    # допущению — тот же стенд чист и сертифицирован.
    zero = _solution(with_history=False)
    zero["history_policy"] = {"coverage_kind": "none", "assume_zero_when_missing": True,
                              "user_decision": {"question": "Q-3b-история-нет",
                                                "decision": "continue_zero"}}
    zero_rep = rc.certify(_svod(), zero, _compiler)
    assert zero_rep["certificate"]["certified"] is True, zero_rep["certificate"]
    for c in zero_rep["checks"]:
        assert c["windows_assumed_employees"] == 1 and "continue_zero" in c["windows_assumed_basis"]
    assert gate_certify.check(_svod(), zero_rep)[0] == []
    # с историей: последняя смена кончилась 14.01 22:00 дома, первая смена
    # 15.01 06:00 — отдых 480 < 720; налёт 120 + остаток 200 = 320 > 300
    rep = rc.certify(_svod(), _solution(with_history=True), _compiler)
    v = rep["certificate"]
    assert v["certified"] is False
    assert v["violated_rules"] == ["R-MONTH", "R-REST-H"]
    texts = {f["rule_refs"][0]: f for f in rep["findings"]}
    assert "480" in texts["R-REST-H"]["text"] and "история" in texts["R-REST-H"]["text"]
    assert "История" in texts["R-REST-H"]["evidence"]
    assert "320" in texts["R-MONTH"]["text"] and "остаток 200" in texts["R-MONTH"]["text"]
    assert gate_certify.check(_svod(), rep)[0] == []
    rest = next(c for c in rep["checks"] if c["id"] == "C-R-REST-H-посадка")
    assert "история передана" in rest["note"]
    first_rest = rest["window_coverage"]["windows"][0]["rest_assessments"][0]
    assert first_rest["previous_source"] == "История"
    assert first_rest["observed_minutes"] == 480 and first_rest["outcome"] == "violation"
    # сотрудник с историей, но без смен в решении — назван, не «потерян»:
    # он в покрытии (отдых перед сменой не требуется)
    sol2 = _solution(with_history=True)
    sol2["history"].append({"employee": "E2", "as_of": "2026-01-15T00:00",
                            "has_history": True, "month_block_min": 10,
                            "last_duty_end": "2026-01-14T20:00",
                            "last_duty_station": "PLY"})
    sol2["window_employees"].append({"employee": "E2", "base": "PLY"})
    rep2 = rc.certify(_svod(), sol2, _compiler)
    rest2 = next(c for c in rep2["checks"] if c["id"] == "C-R-REST-H-посадка")
    assert "судился у 1 сотрудников" in rest2["note"]
    assert "без смен в решении: E2" in rest2["note"]
    rest_windows = rest2["window_coverage"]["windows"]
    assert {w["employee"] for w in rest_windows} == {"E1", "E2"}
    idle = next(w for w in rest_windows if w["employee"] == "E2")
    assert idle["rest_expected"] == 0 and "нет назначенных смен" in idle["reason"]
    h2 = rep2["certificate"]["history"]
    assert h2["with_history"] == 2
    # у E2 рабочее время не задано — в сумму не входит и не считается нулём
    assert h2["month_block_min_total"] == 210 and h2["month_block_known"] == 2
    assert h2["month_work_min_total"] == 300 and h2["month_work_known"] == 1
    assert "известен у 1 из 2" in rc.render_certificate(rep2)
    # шапка сертификата несёт сводку истории; без листа — None
    h = v["history"]
    assert h["passed"] is True and h["with_history"] == 1 and h["employees_total"] == 1
    assert h["month_block_min_total"] == 200 and h["with_last_duty"] == 1
    assert h["month_block_known"] == 1 and h["month_work_known"] == 1
    assert clean["certificate"]["history"] is None
    md = rc.render_certificate(rep)
    assert "ИСТОРИЯ СОТРУДНИКОВ" in md and "остаток налёта месяца 200" in md


def test_gate_refuses_green_certificate_once_history_windows_are_unknown():
    """Ревизия 1.18.3 (граница H12/K-15, зона CDPM): как только сертификат
    честно называет окно на границе среза неизвестным — «частично прогнана»
    с window_coverage — гейт не принимает certified=true, а доказанное
    нарушение по частично прогнанной проверке остаётся находкой. Сторона
    сертификатора (окна в сертификате без истории) — зона COSMOS
    (vmp/run/roster_certify), здесь судится только гейт."""
    import copy
    # Единая версия (C-43): стенд без истории и без решения анкеты сам даёт
    # «частично прогнана» с window_coverage и certified=false (тест выше);
    # чистая база для рукотворного частичного сертификата — решение
    # пользователя continue_zero (окна судятся по названному допущению).
    zero = _solution(with_history=False)
    zero["history_policy"] = {"coverage_kind": "none", "assume_zero_when_missing": True,
                              "user_decision": {"question": "Q-3b-история-нет",
                                                "decision": "continue_zero"}}
    clean = rc.certify(_svod(), zero, _compiler)
    assert clean["certificate"]["certified"] is True
    rest = next(c for c in clean["checks"] if c["id"] == "C-R-REST-H-посадка")
    assert rest["status"] == "прогнана"
    partial = copy.deepcopy(clean)
    check = next(c for c in partial["checks"] if c["id"] == "C-R-REST-H-посадка")
    check.update(status="частично прогнана",
                 reason="отдых до первой смены горизонта у E1 неизвестен: "
                        "истории нет и решения анкеты нет",
                 window_coverage={
                     "expected": 1, "evaluated": 0, "unknown": 1,
                     "granularity": "windows", "expected_unit": "windows",
                     "windows": [{"employee": "E1",
                                  "start_inclusive": "2026-01-14T00:00",
                                  "end_exclusive": "2026-01-15T06:00",
                                  "status": "unknown", "outcome": None,
                                  "reason": "конец последней смены до среза "
                                            "неизвестен — истории нет"}]})
    partial["counters"] = dr.metrics_of(partial)
    partial["certificate"].update(checks_run=partial["counters"]["checks_run"],
                                  checks_not_run=partial["counters"]["checks_skipped"])
    probs = gate_certify.check(_svod(), partial)[0]
    assert any("certified противоречит телу" in p for p in probs), probs
    partial["certificate"]["certified"] = False
    assert gate_certify.check(_svod(), partial)[0] == []
    # находка по частично прогнанной проверке — допустима и удерживает
    # violated_rules
    partial["findings"].append({
        "id": "F-0099", "check": "C-R-REST-H-посадка", "severity": "КРИТИЧЕСКОЕ",
        "class": "нарушение правила", "rule_refs": ["R-REST-H"],
        "target": {"sid": rc.SOLUTION_SID, "sheet": "Смены", "row": 2},
        "text": "отдых 480 < 720", "evidence": "по истории"})
    partial["counters"] = dr.metrics_of(partial)
    partial["certificate"].update(findings=1, violated_rules=["R-REST-H"])
    assert gate_certify.check(_svod(), partial)[0] == []


def test_missing_history_and_assume_zero():
    clean_data = {"tables": {"Экипажи": {"rows": [
        {"Табельный номер": "E1"}, {"Табельный номер": "E2"}]}}}
    man = {"history": _history_section(with_e2=False)}
    assert ha.missing_history(man, clean_data) == ["E2"]
    # раскладка пакета: таблицы под идентификатором источника (H0-КИ: D1)
    nested = {"tables": {"D1": {"Экипажи": {"rows": [
        {"Табельный номер": "E1"}, {"Табельный номер": "E2"}]}}}}
    assert ha.missing_history(man, nested) == ["E2"]
    assert ha.crew_rows({"tables": {"D1": {"Рейсы": {"rows": [{}]}}}}) == []
    # словарь из одних null — «без записи» (ревью CDPM 05.09)
    man2 = {"history": _history_section(with_e2=False)}
    man2["history"]["employees"].append(
        {"employee_id": "E2", "month_to_date": {"block_min": None, "work_min": None},
         "last_duty": {"end": None, "end_station": ""}, "series": {},
         "location": None})
    assert ha.entry_has_history(man2["history"]["employees"][1]) is False
    assert ha.missing_history(man2, clean_data) == ["E2"]
    assert tp.history_record(man2["history"]["employees"][1]) is None
    # у пейринга при переданной истории — «судятся на ростеринге», не
    # ростеринговая формулировка
    p = ha.statement({"rules": [{"id": "R-W", "zone": "PAIRING", "kind": "SOFT",
                                 "export_solver": {"family": "calendar_windows"}}]},
                     man2, "pairing")["history"]
    assert p["passed"] is True and "судятся на ростеринге" in p["note"]
    assert ha.assume_zero(man) is False
    assert ha.assume_zero({"history": _history_section(assume_zero=True)}) is True
    assert ha.missing_history({}, clean_data) == []


def test_button_stops_when_history_is_partial_and_zero_is_forbidden(tmp_path, monkeypatch):
    from tests.test_run_button import FakeSolver, _doc, _fakes, _run_dir, btn
    from vmp import registry_check as _regc
    root = Path(btn.__file__).resolve().parent.parent.parent

    def _fake_press_registry(run_dir, model, task):
        reg = {"created": "тест", "scope": "кнопка: тест",
               "code_fingerprints": _regc.code_fingerprints(root),
               "rules": [{"rule_id": str(r["id"]), "verdict": "covered_full",
                          "title": r.get("title") or ""}
                         for r in (model or {}).get("rules") or []]}
        path = Path(run_dir) / "registry" / "vmp-executors-registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return path
    monkeypatch.setattr(btn, "press_registry", _fake_press_registry)
    doc = _doc()
    # семья — легаси-полем export_solver (форма ВМП стенда без посадки)
    doc["rules"].append({"id": "R-MONTH", "title": "Месячный налёт",
                         "statement": "налёт за месяц не больше 300 мин",
                         "zone": "ROSTERING", "kind": "HARD",
                         "export_solver": {"family": "span_accumulators"},
                         "sources": [{"sid": "S1", "anchor": "п.9"}]})
    clean_data = {"tables": {"Экипажи": {"rows": [
        {"Табельный номер": "E1"}, {"Табельный номер": "E2"}]}}}

    def load_pkg(path, section):
        return {"manifest": {"allowed_modes": {"rostering": ["optimization"]},
                             "objectives": {"rostering": ["G-1"]},
                             "spec_version": "1.17", "history": section},
                "model": doc, "clean_data": clean_data}

    run = _run_dir(tmp_path, doc)
    fakes = _fakes(True)
    fakes["load_pkg"] = lambda p: load_pkg(p, _history_section(with_e2=False))
    d = btn.orchestrate(run, FakeSolver(), sleep=lambda *_: None, **fakes)
    assert d["verdict"].startswith("остановка: у части сотрудников нет истории"), d
    assert "E2" in str(d.get("detail")) and "R-MONTH" in str(d.get("detail"))
    # нулевая подстановка разрешена производителем — остановки нет
    run2 = _run_dir(tmp_path / "b", doc)
    fakes2 = _fakes(True)
    fakes2["load_pkg"] = lambda p: load_pkg(
        p, _history_section(with_e2=False, assume_zero=True))
    d2 = btn.orchestrate(run2, FakeSolver(), sleep=lambda *_: None, **fakes2)
    assert not d2["verdict"].startswith("остановка: у части сотрудников"), d2
    assert d2["assumptions"]["history"]["passed"] is True


def test_history_pin_catalog_for_pairing_groups_by_day_and_merges_crew():
    """Закрепления из истории для ПЕЙРИНГА (слово владельца 05.09.2026):
    рейсы сотрудника одной даты — одна смена, смены — одна цепочка;
    два сотрудника с одинаковой структурой (экипаж) — одна запись;
    без раздела или без назначений — None."""
    employees = [{"id": "H01", "skills": ["Captain"]},
                 {"id": "H02", "skills": ["FirstOfficer"]},
                 {"id": "H03", "skills": ["Captain"]}]
    flights = [{"flightNumber": "PL101|2026-09-04T06:00:00",
                "departureUTCDateTime": "2026-09-04T06:00:00"},
               {"flightNumber": "PL102|2026-09-04T10:00:00",
                "departureUTCDateTime": "2026-09-04T10:00:00"},
               {"flightNumber": "PL103|2026-09-05T06:00:00",
                "departureUTCDateTime": "2026-09-05T06:00:00"},
               {"flightNumber": "PL104|2026-09-06T06:00:00",
                "departureUTCDateTime": "2026-09-06T06:00:00"}]
    payload = {"employees": employees, "flights": flights}

    def pkg(entries):
        return {"manifest": {"history": {"as_of": "2026-09-04T00:00",
                                         "employees": entries}}}
    rows = [{"Рейс": "PL102", "Дата": "04.09.2026"},
            {"Рейс": "PL101", "Дата": "04.09.2026"},
            {"Рейс": "PL103", "Дата": "2026-09-05"}]
    cat = tp.history_pin_catalog(
        pkg([{"employee_id": "H01", "approved_assignments": rows},
             {"employee_id": "H02", "approved_assignments": list(rows)},
             {"employee_id": "H03", "approved_assignments": [
                 {"Рейс": "PL104", "Дата": "06.09.2026"}]}]), payload)
    assert cat["origin"].startswith("manifest.history.approved_assignments")
    assert cat["as_of"] == "2026-09-04T00:00"
    assert len(cat["selected"]) == 2
    first, second = cat["selected"]
    assert first["duty_fns"] == [["PL101|2026-09-04T06:00:00", "PL102|2026-09-04T10:00:00"],
                                 ["PL103|2026-09-05T06:00:00"]]
    assert first["fns"] == first["active_fns"] == [
        "PL101|2026-09-04T06:00:00", "PL102|2026-09-04T10:00:00",
        "PL103|2026-09-05T06:00:00"]
    assert first["sourceUnit"] == "history:H01+H02"
    assert first["employees"] == ["H01", "H02"]
    assert first["days"] == ["2026-09-04", "2026-09-05"]
    assert second["duty_fns"] == [["PL104|2026-09-06T06:00:00"]]
    assert second["sourceUnit"] == "history:H03"
    assert tp.history_pin_catalog({"manifest": {}}, payload) is None
    assert tp.history_pin_catalog(pkg([{"employee_id": "H01"}]), payload) is None


def test_history_pin_catalog_for_pairing_fails_closed():
    """Отказ словами: рейс+дата вне набора, неоднозначная дата,
    неизвестный сотрудник, частичное пересечение структур двух
    сотрудников (в точном разбиении несовместимо)."""
    import pytest
    employees = [{"id": "H01"}, {"id": "H02"}]
    flights = [{"flightNumber": "PL101|2026-09-04T06:00:00",
                "departureUTCDateTime": "2026-09-04T06:00:00"},
               {"flightNumber": "PL102|2026-09-04T10:00:00",
                "departureUTCDateTime": "2026-09-04T10:00:00"},
               {"flightNumber": "PL105|2026-09-07T06:00:00",
                "departureUTCDateTime": "2026-09-07T06:00:00"},
               {"flightNumber": "PL105|2026-09-07T18:00:00",
                "departureUTCDateTime": "2026-09-07T18:00:00"}]
    payload = {"employees": employees, "flights": flights}

    def pkg(entries):
        return {"manifest": {"history": {"employees": entries}}}
    with pytest.raises(tp.TaskPackageRefused, match="рейса с этой датой в наборе нет"):
        tp.history_pin_catalog(pkg([{"employee_id": "H01", "approved_assignments": [
            {"Рейс": "PL101", "Дата": "05.09.2026"}]}]), payload)
    with pytest.raises(tp.TaskPackageRefused, match="рейсов с этой датой несколько"):
        tp.history_pin_catalog(pkg([{"employee_id": "H01", "approved_assignments": [
            {"Рейс": "PL105", "Дата": "07.09.2026"}]}]), payload)
    with pytest.raises(tp.TaskPackageRefused, match="сотрудника нет на листе"):
        tp.history_pin_catalog(pkg([{"employee_id": "H99", "approved_assignments": [
            {"Рейс": "PL101", "Дата": "04.09.2026"}]}]), payload)
    with pytest.raises(tp.TaskPackageRefused, match="пересекаются по рейсам"):
        tp.history_pin_catalog(pkg([
            {"employee_id": "H01", "approved_assignments": [
                {"Рейс": "PL101", "Дата": "04.09.2026"},
                {"Рейс": "PL102", "Дата": "04.09.2026"}]},
            {"employee_id": "H02", "approved_assignments": [
                {"Рейс": "PL101", "Дата": "04.09.2026"}]}]), payload)


def test_user_decision_of_revision_1_17_2_lifts_or_keeps_the_lock(tmp_path, monkeypatch):
    """Ревизия 1.17.2 (решение владельца 05.09.2026): решение пользователя
    в анкете «продолжить по частичным данным» снимает замок словами —
    допущение о нуле названо поимённо в журнале и заявлении; «данные
    запрошены» (вопрос открыт) и «истории нет без решения» — остановка с
    именем вопроса."""
    from tests.test_run_button import FakeSolver, _doc, _fakes, _run_dir, btn
    from vmp import registry_check as _regc
    from vmp import history_assumption as ha
    root = Path(btn.__file__).resolve().parent.parent.parent

    def _fake_press_registry(run_dir, model, task):
        reg = {"created": "тест", "scope": "кнопка: тест",
               "code_fingerprints": _regc.code_fingerprints(root),
               "rules": [{"rule_id": str(r["id"]), "verdict": "covered_full",
                          "title": r.get("title") or ""}
                         for r in (model or {}).get("rules") or []]}
        path = Path(run_dir) / "registry" / "vmp-executors-registry.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reg, ensure_ascii=False), encoding="utf-8")
        return path
    monkeypatch.setattr(btn, "press_registry", _fake_press_registry)
    doc = _doc()
    doc["rules"].append({"id": "R-MONTH", "title": "Месячный налёт",
                         "statement": "налёт за месяц не больше 300 мин",
                         "zone": "ROSTERING", "kind": "HARD",
                         "export_solver": {"family": "span_accumulators"},
                         "sources": [{"sid": "S1", "anchor": "п.9"}]})
    clean_data = {"tables": {"Экипажи": {"rows": [
        {"Табельный номер": "E1"}, {"Табельный номер": "E2"}]}}}

    def section(decision, kind="partial", with_e1=True):
        s = _history_section(with_e2=False)
        if not with_e1:
            s["employees"] = []
        s["coverage"]["kind"] = kind
        s["user_decision"] = ({"question": "Q-3b-история-частично",
                               "key": "Q-3b-история-частично",
                               "decision": decision} if decision else None)
        return s

    def load_pkg(path, sec):
        return {"manifest": {"allowed_modes": {"rostering": ["optimization"]},
                             "objectives": {"rostering": ["G-1"]},
                             "spec_version": "1.17", "history": sec},
                "model": doc, "clean_data": clean_data}

    def press(name, sec):
        run = _run_dir(tmp_path / name, doc)
        fakes = _fakes(True)
        fakes["load_pkg"] = lambda p: load_pkg(p, sec)
        d = btn.orchestrate(run, FakeSolver(), sleep=lambda *_: None, **fakes)
        log = json.loads((run / "button" / "log.json").read_text(encoding="utf-8"))
        return d, log

    # «продолжить по частичным» — замок снят, допущение названо
    d, log = press("a", section("continue_partial"))
    assert not d["verdict"].startswith("остановка"), d
    h = d["assumptions"]["history"]
    assert h["assumed_zero_for"] == ["E2"]
    assert h["user_decision"] == {"question": "Q-3b-история-частично",
                                  "key": "Q-3b-история-частично", "decision": "continue_partial"}
    # писатель 1.17.2: key — ответ анкеты («б»), question — имя вопроса
    assert ha.zero_allowed({"history": {**section("continue_zero", kind="none"),
                                        "user_decision": {"question": "Q-3b-история-нет",
                                                          "key": "б",
                                                          "decision": "continue_zero"}}}) == (
        True, "решение пользователя по вопросу Q-3b-история-нет (ответ б): continue_zero")
    assert h["coverage_kind"] == "partial" and h["zero_allowed"] is True
    assert "решение пользователя Q-3b-история-частично: continue_partial" in h["assumption"]
    assert any(r["step"] == "Ш-2с" and r["status"] == "допущение" and "E2" in r["detail"]
               and "continue_partial" in r["detail"] for r in log), log
    # «данные запрошены» — вопрос открыт, замок стоит
    d2, _ = press("b", section("supplement_requested"))
    assert d2["verdict"].startswith("остановка: у части сотрудников нет истории"), d2
    assert "Q-3b-история-частично" in d2["verdict"] and "открыт" in d2["verdict"]
    # истории нет вовсе и решения нет — остановка со словами о покрытии
    d3, _ = press("c", section(None, kind="none", with_e1=False))
    assert d3["verdict"].startswith("остановка"), d3
    assert "coverage none" in d3["verdict"]
    # «продолжить с нулевыми» при отсутствии истории — допущение у всех
    d4, _ = press("d", section("continue_zero", kind="none", with_e1=False))
    assert not d4["verdict"].startswith("остановка"), d4
    assert d4["assumptions"]["history"]["assumed_zero_for"] == ["E1", "E2"]
    # помощники заявления
    man = {"history": section("continue_zero", kind="none", with_e1=False)}
    assert ha.zero_allowed(man) == (True, "решение пользователя Q-3b-история-частично: continue_zero")
    assert ha.zero_allowed({"history": section("enter_requested", kind="none")})[0] is False
    assert ha.zero_allowed({"history": section(None, kind="none")})[1].startswith("история отсутствует")
    assert ha.coverage_kind(man) == "none" and ha.user_decision({"history": section(None)}) is None
    st = ha.statement(doc, man, "rostering")["history"]
    assert st["zero_allowed"] is True and "покрытие истории: none" in st["note"]
    # у пейринга решение не меняет формулировку (людей у цепочек нет)
    assert "покрытие истории" not in ha.statement(doc, man, "pairing")["history"]["note"]


def test_history_families_of_path_a_depend_on_history():
    """Семьи night_series / rolling_rest / days_off_floor (путь А) зависят
    от истории: их правила входят в перечень зависимых у ростеринга (замок
    Ш-2с и заявление), у пейринга — нет (у цепочек людей нет)."""
    from vmp import history_assumption as ha
    model = {"rules": [
        {"id": "VN-091", "kind": "HARD", "zone": "ROSTERING", "title": "ночные подряд",
         "template_fit": {"category": "A", "family": "night_series",
                          "params": {"max_consecutive_night_duties": {"value": 2}}}},
        {"id": "VN-092", "kind": "HARD", "zone": "ROSTERING", "title": "отдых в окне",
         "template_fit": {"category": "A", "family": "rolling_rest"}},
        {"id": "VN-093", "kind": "HARD", "zone": "ROSTERING", "title": "выходных в месяц",
         "template_fit": {"category": "A", "family": "days_off_floor"}},
        {"id": "VN-012", "kind": "HARD", "zone": "BOTH", "title": "состав",
         "template_fit": {"category": "A", "family": "crew_composition"}},
    ]}
    dep = ha.dependent_rules(model, "rostering")
    assert [r["rule_id"] for r in dep] == ["VN-091", "VN-092", "VN-093"]
    assert {r["family"] for r in dep} == {"night_series", "rolling_rest", "days_off_floor"}
    assert ha.dependent_rules(model, "pairing") == []
    st = ha.statement(model, None, "rostering")["history"]
    assert st["assumption"] == "остаток на начало периода = 0"
    assert "VN-091, VN-092, VN-093" in st["note"]
