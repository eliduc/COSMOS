# -*- coding: utf-8 -*-
"""Выгрузка решения во вход сертификатора (мост, этап Б).

Проверяется не форма, а суть: границы смен обязаны совпадать с теми,
по которым построен ростер. Порог отдыха берётся у ПРОФИЛЯ, которым
прогон считал, а не у яруса правил и не из конфигурации установки:
профиль по умолчанию ярус не читает вовсе. Подстановка умолчания
запрещена — это тот же класс «число ниоткуда», что вес и численность
экипажа.
"""

import pytest

from webapp import solution_export as se


def _payload():
    """Четыре рейса: E1 летает HOME→OUT→HOME→OUT, база E1 — HOME."""
    def f(num, dep, arr, a, b):
        return {"flightNumber": num,
                "departureUTCDateTime": dep, "arrivalUTCDateTime": arr,
                "departureAirport": {"code": a}, "arrivalAirport": {"code": b}}
    return {"flights": [
        f("L1", "2026-08-01T06:00:00", "2026-08-01T08:00:00", "HOME", "OUT"),
        f("L2", "2026-08-01T09:00:00", "2026-08-01T11:00:00", "OUT", "HOME"),
        f("L3", "2026-08-02T06:00:00", "2026-08-02T08:00:00", "HOME", "OUT"),
        f("L4", "2026-08-01T07:00:00", "2026-08-01T09:00:00", "OUT", "HOME"),
    ], "employees": [{"id": "E1", "homeAirport": {"code": "HOME"}}]}


def _solved():
    return {"flightAssignments": [
        {"flight": "L1", "employee": {"id": "E1"}},
        {"flight": "L2", "employee": {"id": "E1"}},
        {"flight": "L3", "employee": {"id": "E1"}},
        {"flight": "L4", "employee": None},          # место осталось пустым
    ]}


def test_назначения_переносятся_с_временами_рейсов():
    rows = se.assignments_of(_payload(), _solved())
    # Незанятое место в решение сертификатора не попадает.
    assert len(rows) == 3
    assert {r["flight"] for r in rows} == {"L1", "L2", "L3"}
    first = next(r for r in rows if r["flight"] == "L1")
    assert first["employee"] == "E1"
    assert first["dep_time"] == "2026-08-01T06:00:00"
    assert first["arr_time"] == "2026-08-01T08:00:00"


def test_перегон_едет_в_строке_с_явным_флагом():
    """Перегон (этап Г): пассажирское место несёт deadhead=True, обычное —
    без флага вовсе; молчание превратило бы пассажира в пилота."""
    solved = {"flightAssignments": [
        {"flight": "L1", "employee": {"id": "E1"}},
        {"flight": "L2", "employee": {"id": "E1"}, "deadhead": True},
        {"flight": "L3", "employee": None, "deadhead": True},  # пустое кресло
    ]}
    rows = se.assignments_of(_payload(), solved)
    assert len(rows) == 2                     # пустой перегон не попадает
    by_flight = {r["flight"]: r for r in rows}
    assert by_flight["L2"]["deadhead"] is True
    assert "deadhead" not in by_flight["L1"]


def test_смены_режутся_ровно_по_порогу_отдыха():
    rows = se.assignments_of(_payload(), _solved())
    # Порог 600 минут: разрыв L1->L2 равен 60 мин (та же смена),
    # разрыв L2->L3 равен 19 часам (новая смена).
    duties = se.duties_from_assignments(rows, 600)
    assert len(duties) == 2
    assert duties[0]["start"] == "2026-08-01T06:00:00"
    assert duties[0]["end"] == "2026-08-01T11:00:00"
    assert duties[1]["start"] == "2026-08-02T06:00:00"
    # Контроль: порог меняет ответ, а не украшает его. При пороге
    # в 30 минут тот же час между рейсами уже режет смену.
    assert len(se.duties_from_assignments(rows, 30)) == 3


def test_две_ручки_отдыха_режут_по_месту():
    """Разрыв L2->L3 — 19 часов ДОМА (L2 прилетает в HOME).

    Домашний порог 20 часов — разрыв его не дотягивает, смена НЕ режется;
    внебазовый порог здесь ни при чём. Тот же разрыв с домашним порогом
    10 часов — режется. Именно так режет решатель: по месту отдыха.
    """
    rows = se.assignments_of(_payload(), _solved())
    homes = se.homes_of(_payload())
    assert homes == {"E1": "HOME"}
    assert len(se.duties_from_assignments(
        rows, {"home": 1200, "outstation": 600}, homes)) == 1
    assert len(se.duties_from_assignments(
        rows, {"home": 600, "outstation": 1200}, homes)) == 2
    # Два разных порога без баз приписки НИ У КОГО — отказ: домашняя
    # ручка не применилась бы ни к одному разрыву. И None, и {}.
    for nobody in (None, {}):
        with pytest.raises(se.SolutionExportError) as e:
            se.duties_from_assignments(rows, {"home": 1200, "outstation": 600}, nobody)
        assert "ни для кого" in str(e.value)


def test_сотрудник_без_базы_судится_внебазовым_порогом_и_это_посчитано():
    """Правило решателя (atHome): нет базы — дома нигде. Не молча: покрытие."""
    rows = se.assignments_of(_payload(), _solved())
    homes = {"КТО-ТО ДРУГОЙ": "HOME"}              # у E1 базы нет
    coverage = {}
    duties = se.duties_from_assignments(
        rows, {"home": 1200, "outstation": 600}, homes, coverage)
    # 19-часовой разрыв по внебазовому порогу 600 — режет (дома не резал бы).
    assert len(duties) == 2
    assert coverage["home"] == 0 and coverage["outstation"] == 2
    assert coverage["no_base"] == ["E1"]
    # С базой тот же разрыв судится дома, и покрытие это показывает.
    coverage = {}
    se.duties_from_assignments(rows, {"home": 1200, "outstation": 600},
                               {"E1": "HOME"}, coverage)
    assert coverage == {"home": 1, "outstation": 1, "no_base": [],
                        "no_arrival_airport": 0}


def test_равный_вылет_режется_по_полному_ключу_а_не_по_порядку_входа():
    """Тот же ключ, что у решателя: (вылет, аэропорт вылета, прилёт, ...)."""
    def leg(num, dep, arr, a, b):
        return {"employee": "E1", "flight": num, "dep_time": dep, "arr_time": arr,
                "dep_airport": a, "arr_airport": b}
    # F1 и F2 вылетают в одну минуту; F1 прилетает ДОМОЙ в 12:00, F2 вне
    # базы в 11:00. По полному ключу последний — F1 (дома): 630 минут до
    # F3 меньше домашних 720 — одна смена. Порядок входа роли не играет.
    f1 = leg("F1", "2026-03-02T10:00:00", "2026-03-02T12:00:00", "XXX", "HOME")
    f2 = leg("F2", "2026-03-02T10:00:00", "2026-03-02T11:00:00", "XXX", "OUT")
    f3 = leg("F3", "2026-03-02T22:30:00", "2026-03-02T23:30:00", "HOME", "OUT")
    thr = {"home": 720, "outstation": 600}
    for order in ([f1, f2, f3], [f2, f1, f3], [f3, f2, f1]):
        rows = [dict(x) for x in order]
        assert len(se.duties_from_assignments(rows, thr, {"E1": "HOME"})) == 1


def test_вход_несёт_аэропорты_и_роль():
    """Без них проверки сертификатора о базе и составе ложатся «нет данных»."""
    rows = se.assignments_of(_payload(), _solved())
    first = next(r for r in rows if r["flight"] == "L1")
    assert first["dep_airport"] == "HOME" and first["arr_airport"] == "OUT"


def test_порог_отдыха_подставить_умолчанием_нельзя():
    rows = se.assignments_of(_payload(), _solved())
    for bad in (0, -600, None, "600"):
        with pytest.raises(se.SolutionExportError) as e:
            se.duties_from_assignments(rows, bad)
        assert "умолчание" in str(e.value) or "положительным" in str(e.value)


def test_порог_берётся_у_профиля_которым_прогон_считал():
    """Не у яруса правил — профиль по умолчанию его не читает вовсе.

    История дефекта: прежняя редакция брала показания яруса
    (applied.min_rest_min = 600), а прогоны по пакету идут профилем по
    умолчанию с литеральным порогом 480. Смены резались не тем числом,
    которым решатель считал легальность, и расхождение было молчаливым:
    обе величины правдоподобны и обе «из отчёта решателя».
    """
    # Профиль по умолчанию — ДВЕ ручки отдыха.
    got = se.min_rest_from_applied(
        {"lastRun": {"rulesProfile": "DEFAULT",
                     "profileReadings": {"min_rest_home_min": 720,
                                         "min_rest_outstation_min": 600}}})
    assert got == {"home": 720, "outstation": 600}
    # Профили GERAD режут смены по календарной дате, не по отдыху:
    # выгрузка по порогу отдыха для них неверна — отказ, не число.
    with pytest.raises(se.SolutionExportError) as e:
        se.min_rest_from_applied(
            {"lastRun": {"rulesProfile": "GERAD",
                         "profileReadings": {"min_rest_min": 600}}})
    assert "календарной дате" in str(e.value)
    # Половина правила — отказ: резать смены по одной ручке из двух нельзя.
    with pytest.raises(se.SolutionExportError):
        se.min_rest_from_applied(
            {"lastRun": {"rulesProfile": "DEFAULT",
                         "profileReadings": {"min_rest_home_min": 720}}})


def test_показания_яруса_больше_не_принимаются():
    """Ровно тот вход, который прежняя редакция принимала за истину."""
    with pytest.raises(se.SolutionExportError) as e:
        se.min_rest_from_applied({"applied": {"min_rest_min": 600}})
    assert "profileReadings" in str(e.value)


def test_нет_порога_у_профиля_отказ_а_не_догадка():
    with pytest.raises(se.SolutionExportError) as e:
        se.min_rest_from_applied(
            {"lastRun": {"rulesProfile": "DEFAULT", "profileReadings": {"min_sit_home_min": 60}}})
    assert "порог отдыха" in str(e.value)
    with pytest.raises(se.SolutionExportError):
        se.min_rest_from_applied(
            {"lastRun": {"rulesProfile": "GERAD",
                         "profileReadings": {"min_rest_min": 0}}})


def test_назначение_на_рейс_вне_набора_отвергается():
    solved = {"flightAssignments": [
        {"flight": "НЕТ-ТАКОГО", "employee": "E1"}]}
    with pytest.raises(se.SolutionExportError) as e:
        se.assignments_of(_payload(), solved)
    assert "которого нет в наборе" in str(e.value)


def test_вход_сертификатора_собирается_целиком():
    coverage = {}
    got = se.certifier_input(_payload(), _solved(), {"home": 1200, "outstation": 600},
                             name="проба", coverage=coverage)
    assert got["solution"] == "проба"
    assert len(got["assignments"]) == 3
    # Разрыв L2->L3 дома (19 ч < 20 ч) — одна смена; покрытие посчитано
    # и во вход сертификатора не входит (у него закрытый перечень полей).
    assert len(got["duties"]) == 1
    assert coverage["home"] == 1 and coverage["outstation"] == 1
    # Закрытый перечень полей входа (единая версия 06.09.2026, W-21):
    # штатные три плюс горизонт, перечень сотрудников, контекст цены и
    # интервалы истории (W-9/W-10); pairings/history — только при данных.
    assert set(got) == {"solution", "assignments", "duties", "window_horizon",
                        "window_employees", "cost_context", "cost_history_intervals"}
    assert got["window_horizon"] is None       # из назначений не выводится
    got = se.certifier_input(_payload(), _solved(), 600, name="проба")
    assert len(got["duties"]) == 2
    # Контракт сертификатора: у смены — три обязательных плюс номер, база
    # и аэропорт прилёта последнего рейса (место отдыха после смены).
    assert set(got["duties"][0]) == {"id", "employee", "start", "end",
                                     "base", "last_arr_airport"}
    assert got["duties"][0]["base"] == "HOME"
    assert got["duties"][0]["last_arr_airport"] == "HOME"     # L2 прилетает домой
    # У назначения — обязательные четыре плюс аэропорты, роль, база, смена.
    first = got["assignments"][0]
    assert {"employee", "flight", "dep_time", "arr_time", "base", "duty_id"} <= set(first)
    assert first["base"] == "HOME"
    # Привязка согласована с нарезкой: номера смен назначений = номера смен.
    assert {a["duty_id"] for a in got["assignments"]} == {d["id"] for d in got["duties"]}


# ── Единая версия 06.09.2026 (W-9/W-10): горизонт, сотрудники, контекст цены ──

def test_объявленный_горизонт_не_выводится_из_назначений():
    """W-10: горизонт — только объявленный (payload.window_horizon либо
    planSince/planUntil набора, +1 минута к включительному концу, целые
    сутки); без объявления — None, не размах назначений."""
    assert se.declared_window(_payload()) is None
    p = dict(_payload(), _coverage={"plan_since": "2026-08-01T00:00:00",
                                    "plan_until": "2026-08-31T23:59:00"})
    got = se.declared_window(p)
    assert got["start_inclusive"] == "2026-08-01T00:00:00"
    assert got["end_exclusive"] == "2026-09-01T00:00:00"
    assert "planSince/planUntil" in got["source"]
    # Не целые сутки — честно None, не округление.
    p2 = dict(_payload(), _coverage={"plan_since": "2026-08-01T06:00:00",
                                     "plan_until": "2026-08-31T23:59:00"})
    assert se.declared_window(p2) is None
    p3 = dict(_payload(), _coverage={"plan_since": "2026-08-31T00:00:00",
                                     "plan_until": "2026-08-01T23:59:00"})
    assert se.declared_window(p3) is None
    # Явный горизонт набора — копией, без пересчёта.
    explicit = {"start_inclusive": "2026-08-01T00:00:00",
                "end_exclusive": "2026-08-08T00:00:00", "source": "manifest.horizon"}
    p4 = dict(_payload(), window_horizon=explicit, _coverage=p["_coverage"])
    assert se.declared_window(p4) == explicit and se.declared_window(p4) is not explicit
    inp = se.certifier_input(p, _solved(), 600, name="проба")
    assert inp["window_horizon"]["end_exclusive"] == "2026-09-01T00:00:00"


def test_перечень_сотрудников_и_незанятые_места_во_входе():
    """W-9: все сотрудники с базой — включая человека без назначений
    (область учёта ростеринга); незанятые обязательные места — с рейсом
    и временем; перегонное пустое кресло местом не считается."""
    payload = _payload()
    payload["employees"].append({"id": "E2", "homeAirport": {"code": "OUT"}})
    payload["employees"].append({"id": "E3"})           # базы нет — честно None
    solved = _solved()
    solved["flightAssignments"].append({"flight": "L2", "employee": None, "deadhead": True})
    solved["layoverCost"] = {"total": 3, "unit": "nights"}
    inp = se.certifier_input(payload, solved, 600, name="проба")
    assert inp["window_employees"] == [{"employee": "E1", "base": "HOME"},
                                       {"employee": "E2", "base": "OUT"},
                                       {"employee": "E3", "base": None}]
    ctx = inp["cost_context"]
    assert ctx["task"] == "rostering" and ctx["contract"] == "B"
    assert ctx["boundary"] == {"mode": "unknown"}
    assert ctx["unassigned_required_slots"] == [
        {"crewSlotId": None, "flight": "L4", "employee": None, "station": None,
         "start": "2026-08-01T07:00:00", "end": "2026-08-01T09:00:00"}]
    assert inp["cost_history_intervals"] == []
    assert inp["solver_layover_cost"] == {"total": 3, "unit": "nights"}
    # Интервалы присутствия из истории — строкой на интервал с табельным.
    payload["employees"][0]["history"] = {"historyLocationIntervals": [
        {"station": "HOME", "start": "2026-07-30T00:00:00", "end": "2026-08-01T00:00:00",
         "kind": "history_fact"}]}
    inp = se.certifier_input(payload, _solved(), 600, name="проба")
    assert inp["cost_history_intervals"] == [
        {"employee": "E1", "station": "HOME", "start": "2026-07-30T00:00:00",
         "end": "2026-08-01T00:00:00", "kind": "history_fact"}]
    assert "solver_layover_cost" not in inp          # цены решателя нет — поля нет
