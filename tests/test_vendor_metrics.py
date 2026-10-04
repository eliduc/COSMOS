"""Тесты пересчёта метрик заказчика на нашем решении
(`_vendor_metrics.py`).

Фикстура синтетическая — вымышленный перевозчик, никаких данных
заказчика в репозитории. Проверяются определения, от которых зависит
сопоставимость: что считается сменой, назначением, ночёвкой и перелётом
пассажиром, и что метрика без опубликованной формулы заказчика НЕ
выдумывается.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
TOOL = REPO / "_vendor_metrics.py"

_vm = pytest.importorskip("_vendor_metrics")


def _leg(fn, dep, arr, start, minutes, ac="A320"):
    t0 = datetime.fromisoformat(start)
    return {"flightNumber": fn, "departureAirport": dep,
            "arrivalAirport": arr, "aircraftType": ac,
            "departureUTCDateTime": t0.isoformat(),
            "arrivalUTCDateTime": (t0 + timedelta(minutes=minutes))
            .isoformat()}


def _payload():
    """Два дня работы вдали от базы плюс перелёт пассажиром домой.

    Спрос объявлен на рабочих участках (по два места); перелёт пассажиром
    спроса не создаёт — его обслуживает юнит-владелец."""
    flights = [
        _leg("F1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2", "AAA", "BBB", "2026-08-01T10:00:00", 120, "B767"),
        _leg("F3", "BBB", "AAA", "2026-08-03T08:00:00", 120),
        _leg("P9", "AAA", "BASE", "2026-08-03T14:00:00", 120),
    ]
    return {
        "airports": [{"code": c} for c in ("BASE", "AAA", "BBB")],
        "employees": [{"id": "E1", "homeAirport": "BASE"},
                      {"id": "E2", "homeAirport": "BASE"},
                      {"id": "E3", "homeAirport": "BASE"}],
        "flights": flights,
        "flightAssignments": [
            {"id": f"{fn}#{i}", "flight": fn, "indexInFlight": i,
             "requiredSkill": "CA"}
            for fn in ("F1", "F2", "F3") for i in range(2)],
    }


def _assignment():
    return {"label": "тест", "result_status": "OK", "pairings": [{
        "pid": "C_000000",
        "employee": "E1",
        "crew": [{"indexInFlight": 0, "requiredSkill": "CA",
                  "employee": "E1"},
                 {"indexInFlight": 1, "requiredSkill": "CA",
                  "employee": "E2"}],
        "fns": ["F1", "F2", "F3", "P9"],
        "active_fns": ["F1", "F2", "F3"]}]}


def _solution(unfilled=0):
    slots = []
    for fn in ("F1", "F2", "F3"):
        for i in range(2):
            slots.append({"id": f"{fn}#{i}", "flight": fn,
                          "employee": None if unfilled and fn == "F3" and i
                          else "E1"})
    return {"flightAssignments": slots}


def _metrics(payload=None, assignment=None, solution=None, vendor=None,
             tz=0, **kw):
    m = _vm.Metrics(payload or _payload(), assignment or _assignment(),
                    solution or _solution(), vendor, tz,
                    kw.get("plan_from"), kw.get("plan_until"),
                    kw.get("catalog_units"))
    return m.compute()


def test_duty_is_a_working_day_inside_the_chain():
    """Смена — рабочий день цепочки: два дня работы дают две смены, а
    назначение — это человек x смена, значит на двух членах экипажа их
    четыре."""
    o = _metrics()
    assert o["DUTY_COUNT"][0] == 2
    assert o["FLIGHT_DUTY_COUNT"][0] == 2
    assert o["ASSIGNMENT_COUNT"][0] == 4
    assert o["PRODUCTION_DAYS"][0] == 4
    assert o["PAIRING_COUNT"][0] == 1
    assert o["TWODAY_SEQUENCE_COUNT"][0] == 1


def test_window_counts_the_ride_home_but_block_does_not():
    """Окно смены строится по ВСЕМ участкам дня, включая перелёт
    пассажиром — экипаж в это время на работе; налёт — только по рабочим.
    Второй день: работа 08:00-10:00 и перелёт домой 14:00-16:00."""
    o = _metrics()
    # окна: день 1 — 06:00..12:00 (360), день 2 — 08:00..16:00 (480)
    assert o["TOTAL_WT"][0] == _vm._pt((360 + 480) * 2)
    # налёт: день 1 — два участка по 120, день 2 — один
    assert o["TOTAL_FT"][0] == _vm._pt((240 + 120) * 2)
    assert o["USED_DEADHEADS_COUNT"][0] == 2      # человек x перелёт
    assert o["DH_PAIRING"][0] == 1


def test_night_station_and_cost():
    """Ночёвка — человеко-ночь между сменами; станция ночёвки = прилёт
    последнего участка дня. Цена берётся из справочника заказчика, и
    ночи без цены считаются отдельно, а не обнуляются молча."""
    vendor = {"stations": [{"iata": "BBB", "cost": 100}]}
    o = _metrics(vendor=vendor)
    assert o["RELAY_NIGHTS_TOTAL"][0] == 4        # 2 ночи x 2 человека
    assert o["RELAY_ON_BASE"][0] == 0
    assert o["RELAY_OFF_BASE"][0] == 4
    assert o["RELAY_COST_TOTAL"][0] == 400
    assert o["RELAY_NIGHTS_NO_COST"][0] == 0
    # без справочника цен ночи честно попадают в «без цены»
    o2 = _metrics()
    assert o2["RELAY_COST_TOTAL"][0] == 0
    assert o2["RELAY_NIGHTS_NO_COST"][0] == 4


def test_aircraft_type_change_inside_one_day():
    """Смена ТИПА судна внутри одного рабочего дня считается на каждого
    члена экипажа; смена борта при одном типе не видна — бортовой номер
    в канон не переносится."""
    o = _metrics()
    assert o["AC_CHANGE"][0] == 2                 # F1 A320 + F2 B767


def test_plan_window_splits_flights_and_shortfall():
    o = _metrics(solution=_solution(unfilled=1),
                 plan_from=datetime(2026, 8, 2),
                 plan_until=datetime(2026, 8, 4))
    assert o["FLIGHTS_BEFORE_PLAN_SINCE"][0] == 2      # F1, F2
    assert o["FLIGHTS_AFTER_PLAN_UNTIL"][0] == 0
    assert o["FLIGHTS_COUNT"][0] == 4
    assert o["UNASSIGNED_FLIGHT_COUNT"][0] == 1       # F3 в окне
    assert o["UNASSIGNED_FLIGHT_RATIO"][0] == 0.5     # из двух плеч окна


def test_unemployed_crew_and_free_pool():
    o = _metrics()
    assert o["EMPLOYEE_COUNT"][0] == 3
    assert o["FREE_EMPLOYEE"][0] == 1                 # E3 без цепочек


def test_metric_without_a_published_formula_is_not_invented():
    """Метрику, формулы которой заказчик не публиковал, выдумывать
    нельзя: выдуманное число в сравнении хуже честного пропуска."""
    o = _metrics()
    for code in ("NIGHT_ASSIGNMENTS", "ENFORCED_ASSIGNMENTS",
                 "ASSIGNMENTS_CLOSE_TO_LIMIT", "USED_DEADHEADS_COST"):
        assert o[code][0] == _vm.NEED_FORMULA
        assert o[code][1], "причина обязана быть названа"


def test_unknown_vendor_code_is_named_not_dropped():
    """Код метрики заказчика, которого мы не считаем, обязан попасть в
    отчёт с явной пометкой — молча выпавшая строка читается как
    отсутствие расхождения."""
    vendor_metrics = {"НЕИЗВЕСТНЫЙ": {"metricCode": "НЕИЗВЕСТНЫЙ",
                                      "metricDescription": "нечто",
                                      "metricValue": 42}}
    rows = _vm.build_rows(_metrics(), vendor_metrics)
    assert len(rows) == 1
    assert rows[0]["ours"] == "??"
    assert "НЕ КЛАССИФИЦИРОВАНО" in rows[0]["note"]
    text = _vm.as_markdown(rows, "тест", "примечание")
    assert "НЕИЗВЕСТНЫЙ" in text


def test_dataset_notes_overlay():
    """Пояснения, относящиеся к конкретному набору, подаются данными, а
    не зашиваются в продукт."""
    vendor_metrics = {"EMPLOYEE_COUNT": {"metricCode": "EMPLOYEE_COUNT",
                                         "metricValue": 3}}
    rows = _vm.build_rows(_metrics(), vendor_metrics,
                          {"EMPLOYEE_COUNT": "см. вопрос №1"})
    assert rows[0]["note"] == "см. вопрос №1"
    assert rows[0]["theirs"] == 3 and rows[0]["ours"] == 3


def test_shortfall_is_reconciled_against_the_dataset_demand():
    """Недобор считается сверкой со СПРОСОМ НАБОРА, а не по перечню в
    файле решения. Решение, где незанятые места просто отсутствуют,
    иначе даёт «ноль незанятых» — благополучие там, где его нет
    (аудит 2026-07-26, P0-06)."""
    p = _payload()                                    # спрос 6 мест
    sol = {"flightAssignments": [
        {"id": "F1#0", "flight": "F1", "employee": "E1"},
        {"id": "F1#1", "flight": "F1", "employee": "E2"}]}  # занято 2
    m = _vm.Metrics(p, _assignment(), sol, None, 0, None, None)
    o = m.compute()
    comp = m.completeness()
    assert comp["demand_seats"] == 6
    assert comp["filled_seats"] == 2
    assert comp["shortfall_seats"] == 4, "недобор обязан быть виден"
    assert o["UNASSIGNED_FLIGHT_COUNT"][0] == 2      # F2 и F3 не заняты


def test_measurement_on_a_non_ok_run_is_incomplete():
    """Метрики неполного прогона выглядят как метрики полного решения,
    пока вердикт этапа 2 не назван."""
    a = _assignment()
    a["result_status"] = "INFEASIBLE_INCUMBENT"
    m = _vm.Metrics(_payload(), a, _solution(), None, 0, None, None)
    m.compute()
    comp = m.completeness()
    assert comp["verdict"] == "INCOMPLETE"
    assert "INFEASIBLE_INCUMBENT" in comp["reasons"][0]
    ok = _assignment()
    ok["result_status"] = "OK"
    m2 = _vm.Metrics(_payload(), ok, _solution(), None, 0, None, None)
    m2.compute()
    assert m2.completeness()["verdict"] == "OK"


def test_artifacts_from_different_runs_are_caught():
    """Решение занимает рейс, которого нет в спросе набора, — признак
    того, что артефакты собраны из разных прогонов."""
    a = _assignment()
    a["result_status"] = "OK"
    sol = _solution()
    sol["flightAssignments"].append(
        {"id": "ЧУЖОЙ#0", "flight": "ЧУЖОЙ", "employee": "E1"})
    m = _vm.Metrics(_payload(), a, sol, None, 0, None, None)
    m.compute()
    comp = m.completeness()
    assert comp["verdict"] == "INCOMPLETE"
    assert comp["alien_flights"] == ["ЧУЖОЙ"]


def test_cli_refuses_an_incomplete_measurement(tmp_path):
    """Код возврата 3 и явная причина; --allow-incomplete снимает отказ
    осознанно, а не молча."""
    p = tmp_path / "p.json"
    p.write_text(json.dumps(_payload()), encoding="utf-8")
    a = tmp_path / "a.json"
    rec = _assignment()
    rec["result_status"] = "INFEASIBLE_INCUMBENT"
    a.write_text(json.dumps(rec), encoding="utf-8")
    s = tmp_path / "s.json"
    s.write_text(json.dumps(_solution()), encoding="utf-8")
    base = [sys.executable, str(TOOL), "--payload", str(p),
            "--assignment", str(a), "--solution", str(s)]
    proc = subprocess.run(base, capture_output=True, text=True, cwd=REPO,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "INCOMPLETE" in proc.stdout
    proc2 = subprocess.run(base + ["--allow-incomplete"], capture_output=True,
                           text=True, cwd=REPO, encoding="utf-8",
                           errors="replace")
    assert proc2.returncode == 0, proc2.stdout + proc2.stderr


def test_cli_writes_both_artifacts(tmp_path):
    p = tmp_path / "p.json"
    p.write_text(json.dumps(_payload()), encoding="utf-8")
    a = tmp_path / "a.json"
    a.write_text(json.dumps(_assignment()), encoding="utf-8")
    s = tmp_path / "s.json"
    s.write_text(json.dumps(_solution()), encoding="utf-8")
    md, js = tmp_path / "m.md", tmp_path / "m.json"
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--payload", str(p),
         "--assignment", str(a), "--solution", str(s),
         "--out-md", str(md), "--out-json", str(js)],
        capture_output=True, text=True, cwd=REPO,
        encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "DUTY_COUNT" in md.read_text(encoding="utf-8")
    doc = json.loads(js.read_text(encoding="utf-8"))
    assert doc["metrics"]["DUTY_COUNT"]["ours"] == 2
    # знаменатель назван в самом артефакте, а не только в тексте отчёта
    assert doc["completeness"]["demand_seats"] == 6
    assert doc["completeness"]["verdict"] == "OK"
    assert "спрос набора" in md.read_text(encoding="utf-8")


def test_no_network_or_model_calls_in_source():
    src = TOOL.read_text(encoding="utf-8")
    for needle in ("requests", "httpx", "urllib", "socket", "openai",
                   "anthropic", "claude_cli", "ollama"):
        assert needle not in src, f"недопустимый вызов: {needle}"


def test_overfill_makes_the_measurement_incomplete():
    """Повторный аудит 2026-07-26 (P0-06): спрос одного места и ДВЕ
    занятые строки того же рейса давали «недобор 0, вердикт OK» — лишняя
    строка решения маскировала расхождение артефактов так же, как чужой
    рейс. Перебор обязан делать измерение недостоверным."""
    p = _payload()
    sol = {"flightAssignments": [
        # F1 просит 2 места (см. _payload) — занимаем 4
        {"id": "F1#0", "flight": "F1", "employee": "E1"},
        {"id": "F1#1", "flight": "F1", "employee": "E2"},
        {"id": "F1#2", "flight": "F1", "employee": "E3"},
        {"id": "F1#3", "flight": "F1", "employee": "E4"}]}
    m = _vm.Metrics(p, _assignment(), sol, None, 0, None, None)
    m.compute()
    comp = m.completeness()
    assert comp["overfill_seats"] == 2
    assert comp["verdict"] == "INCOMPLETE"
    assert any("больше мест" in r for r in comp["reasons"])
    # а точное совпадение спроса перебором не считается
    exact = {"flightAssignments": [
        {"id": "F1#0", "flight": "F1", "employee": "E1"},
        {"id": "F1#1", "flight": "F1", "employee": "E2"}]}
    m2 = _vm.Metrics(p, _assignment(), exact, None, 0, None, None)
    m2.compute()
    assert m2.completeness()["overfill_seats"] == 0
