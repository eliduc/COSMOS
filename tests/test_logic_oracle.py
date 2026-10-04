# -*- coding: utf-8 -*-
"""Оракул формы «выходных в месяц не менее N» (webapp/logic_oracle.py).

То же прочтение, что у решателя, — проверяется на тех же случаях, что и
котлиновый LogicFormsTest: полный месяц, неполный месяц вне оценки, рейс
через полночь занимает двое суток, сотрудник без назначений — все дни
выходные, область — код базы.
"""

from datetime import date

from webapp import logic_oracle as lo


def _flight(leg, dep, arr):
    return {"flightLegId": leg, "flightNumber": leg,
            "departureUTCDateTime": dep, "arrivalUTCDateTime": arr}


def _payload(flights, employees):
    return {"flights": flights, "employees": employees, "flightAssignments": []}


def _solved(assignments):
    return {"flightAssignments": [{"employee": {"id": e}, "flight": {"flightLegId": leg}}
                                  for e, leg in assignments]}


def test_полный_месяц_нехватка_за_каждый_день():
    flights = [_flight("F%d" % d, "2026-03-%02dT10:00:00" % d, "2026-03-%02dT12:00:00" % d)
               for d in range(1, 26)] + [_flight("F99", "2026-03-31T10:00:00", "2026-03-31T12:00:00")]
    payload = _payload(flights, [{"id": "E1", "homeAirport": {"code": "PLY"}},
                                 {"id": "E2", "homeAirport": {"code": "PLY"}}])
    solved = _solved([("E1", "F%d" % d) for d in range(1, 26)])
    rows = lo.days_off_per_month(payload, solved, 8)
    assert len(rows) == 2
    by = {r["employee"]: r for r in rows}
    assert by["E1"]["busy_days"] == 25 and by["E1"]["days_off"] == 6 and by["E1"]["shortfall"] == 2
    assert by["E2"]["busy_days"] == 0 and by["E2"]["days_off"] == 31 and by["E2"]["shortfall"] == 0
    assert lo.shortfall_total(rows) == 2
    assert lo.expected_hard(rows, 1) == -2
    rows32 = lo.days_off_per_month(payload, solved, 32)
    assert lo.shortfall_total(rows32) == 26 + 1


def test_неполный_месяц_вне_оценки():
    flights = [_flight("F%d" % d, "2026-03-%02dT10:00:00" % d, "2026-03-%02dT12:00:00" % d)
               for d in range(10, 21)]
    payload = _payload(flights, [{"id": "E1"}])
    solved = _solved([("E1", "F%d" % d) for d in range(10, 21)])
    assert lo.days_off_per_month(payload, solved, 8) == []
    days = lo.horizon(payload)
    assert lo.full_months(days) == [] and lo.partial_months(days) == ["2026-03"]
    wide = [date(2026, 3, 1) + __import__("datetime").timedelta(days=i) for i in range(36)]
    assert lo.full_months(wide) == [(2026, 3)] and lo.partial_months(wide) == ["2026-04"]


def test_рейс_через_полночь_занимает_двое_суток():
    flights = [_flight("F0", "2026-03-01T08:00:00", "2026-03-01T10:00:00"),
               _flight("F31", "2026-03-31T08:00:00", "2026-03-31T10:00:00"),
               _flight("FN", "2026-03-15T22:00:00", "2026-03-16T02:00:00")]
    payload = _payload(flights, [{"id": "E1"}])
    solved = _solved([("E1", "F0"), ("E1", "F31"), ("E1", "FN")])
    rows = lo.days_off_per_month(payload, solved, 29)
    assert rows[0]["busy_days"] == 4 and rows[0]["days_off"] == 27 and rows[0]["shortfall"] == 2


def test_область_код_базы():
    flights = [_flight("F0", "2026-03-01T08:00:00", "2026-03-01T10:00:00"),
               _flight("F31", "2026-03-31T08:00:00", "2026-03-31T10:00:00")]
    payload = _payload(flights, [{"id": "E1", "homeAirport": {"code": "PLY"}},
                                 {"id": "E2", "homeAirport": {"code": "SVG"}}])
    rows = lo.days_off_per_month(payload, _solved([]), 32, base_scope="ply")
    assert [r["employee"] for r in rows] == ["E1"] and lo.shortfall_total(rows) == 1
    assert lo.shortfall_total(lo.days_off_per_month(payload, _solved([]), 32)) == 2

def test_серии_рабочих_дней_и_превышение():
    flights = [_flight("F%d" % d, "2026-03-%02dT10:00:00" % d, "2026-03-%02dT12:00:00" % d)
               for d in list(range(1, 8)) + list(range(10, 14))]
    payload = _payload(flights, [{"id": "E1"}])
    solved = _solved([("E1", "F%d" % d) for d in list(range(1, 8)) + list(range(10, 14))])
    rows = lo.consecutive_runs(payload, solved, 5)
    assert [(r["days"], r["overage"]) for r in rows] == [(7, 2), (4, 0)]
    assert lo.overage_total(lo.consecutive_runs(payload, solved, 3)) == 4 + 1
    # Ровно одни сутки — серия длины 1, не 2 (ловушка «+1»).
    one = _payload([_flight("F1", "2026-03-05T10:00:00", "2026-03-05T12:00:00")], [{"id": "E1"}])
    assert lo.overage_total(lo.consecutive_runs(one, _solved([("E1", "F1")]), 1)) == 0
    # Рейс через полночь сшивает сутки; вторая смена тех же суток не удваивает день.
    stitch = _payload([_flight("FN", "2026-03-05T22:00:00", "2026-03-06T02:00:00"),
                       _flight("FD", "2026-03-05T08:00:00", "2026-03-05T09:00:00"),
                       _flight("FA", "2026-03-07T10:00:00", "2026-03-07T12:00:00")], [{"id": "E1"}])
    rows2 = lo.consecutive_runs(stitch, _solved([("E1", "FN"), ("E1", "FD"), ("E1", "FA")]), 2)
    assert [(r["days"], r["overage"]) for r in rows2] == [(3, 1)]

def test_отдых_после_продлённой_смены():
    # Смена 05:00–18:30 (размах 810 > 720), отдых 810 мин < 840 — недобор 30.
    flights = [_flight("A1", "2026-03-02T05:00:00", "2026-03-02T12:00:00"),
               _flight("A2", "2026-03-02T13:00:00", "2026-03-02T18:30:00"),
               _flight("B1", "2026-03-03T08:00:00", "2026-03-03T10:00:00")]
    payload = {"flights": flights,
               "employees": [{"id": "E1", "homeAirport": {"code": "PLY"}}],
               "flightAssignments": [
                   {"id": "s%d" % i, "requiredSkill": "Pilot",
                    "flight": {"flightNumber": f["flightNumber"]},
                    "employee": {"id": "E1"}} for i, f in enumerate(flights)]}
    solved = {"flightAssignments": payload["flightAssignments"]}
    rows = lo.rest_after_long_duty(payload, solved, 720, 840, {"home": 480, "outstation": 480})
    assert [(r["span_min"], r["rest_min"], r["shortfall_min"]) for r in rows] == [(810, 810, 30)]
    assert lo.rest_shortfall_total(rows) == 30
    # Короткая смена (порог 900) — не судится; отдых после последней — тоже.
    assert lo.rest_after_long_duty(payload, solved, 900, 840, {"home": 480, "outstation": 480}) == []

def test_отдых_в_скользящем_окне():
    # 10 суток по рейсу 06:00–20:00: непрерывных 36 ч нет — окна 1..4 нарушены.
    flights = [_flight("F%d" % d, "2026-03-%02dT06:00:00" % d, "2026-03-%02dT20:00:00" % d)
               for d in range(1, 11)]
    payload = _payload(flights, [{"id": "E1"}, {"id": "E2"}])
    solved = _solved([("E1", "F%d" % d) for d in range(1, 11)])
    rows = lo.rest_in_rolling_windows(payload, solved, 7, 2160)
    by = {r["employee"]: r for r in rows}
    assert by["E1"] == {"employee": "E1", "windows": 4, "violated": 4}
    # Сотрудник без назначений выполняет все окна.
    assert by["E2"]["violated"] == 0
    assert lo.violated_total(rows) == 4
    # Выходные 4–5 марта дают 58 ч отдыха: при 36 ч чисто, при 60 ч — нарушено.
    part = _solved([("E1", "F%d" % d) for d in (1, 2, 3, 6, 7, 8, 9, 10)])
    assert lo.violated_total(lo.rest_in_rolling_windows(payload, part, 7, 2160)) == 0
    assert lo.violated_total(lo.rest_in_rolling_windows(payload, part, 7, 3600)) == 4

def test_ночные_смены_подряд():
    # Три ночные смены (23:00 -> 01:00) без дневных между ними.
    flights = [_flight("N%d" % d, "2026-03-%02dT23:00:00" % d, "2026-03-%02dT01:00:00" % (d + 1))
               for d in (2, 4, 6)] + [_flight("D1", "2026-03-09T10:00:00", "2026-03-09T12:00:00")]
    payload = {"flights": flights, "employees": [{"id": "E1"}],
               "flightAssignments": [
                   {"id": "s%d" % i, "requiredSkill": "Pilot",
                    "flight": {"flightNumber": f["flightNumber"]},
                    "employee": {"id": "E1"}} for i, f in enumerate(flights)]}
    solved = {"flightAssignments": payload["flightAssignments"]}
    thr = {"home": 480, "outstation": 480}
    rows = lo.night_runs_overage(payload, solved, 1320, 360, 2, thr)
    assert [(r["nights"], r["overage"]) for r in rows] == [(3, 1)]
    assert lo.night_overage_total(lo.night_runs_overage(payload, solved, 1320, 360, 3, thr)) == 0
    # Вылет 05:00 — тоже ночь (окно через полночь).
    early = {"flights": [_flight("M1", "2026-03-02T05:00:00", "2026-03-02T07:00:00")],
             "employees": [{"id": "E1"}],
             "flightAssignments": [{"id": "s1", "requiredSkill": "Pilot",
                                    "flight": {"flightNumber": "M1"},
                                    "employee": {"id": "E1"}}]}
    rows2 = lo.night_runs_overage(early, {"flightAssignments": early["flightAssignments"]},
                                  1320, 360, 0, thr)
    assert [(r["nights"], r["overage"]) for r in rows2] == [(1, 1)]



# ---- история сотрудника (ревизия 1.17, решение владельца 05.09.2026) ----

def _hist(emp_id, as_of, **fields):
    return {"id": emp_id, "historyAsOf": as_of, "history": dict(fields)}


def test_история_месяц_среза_с_переносом_отработанных_суток():
    """Горизонт 10..31 марта — полных месяцев нет. С историей (срез 10.03,
    отработано 8) и занятостью 10..31: занято 22 + 8, выходной 1, при
    норме 8 нехватка 7. Без истории месяц вне оценки (строк нет)."""
    flights = [_flight("F%d" % d, "2026-03-%02dT10:00:00" % d, "2026-03-%02dT12:00:00" % d)
               for d in range(10, 32)]
    payload = _payload(flights, [_hist("E1", "2026-03-10T00:00", historyMonthDaysWorked=8),
                                 {"id": "E2"},
                                 _hist("E3", "2026-03-10T00:00", historyMonthDaysWorked=8)])
    solved = _solved([("E1", "F%d" % d) for d in range(10, 32)]
                     + [("E2", "F%d" % d) for d in range(10, 32)])
    rows = lo.days_off_per_month(payload, solved, 8)
    assert [r["employee"] for r in rows] == ["E1", "E3"]
    assert rows[0] == {"employee": "E1", "month": "2026-03", "busy_days": 22, "days_off": 1,
                       "bound": 8, "shortfall": 7, "carry_days": 8}
    # С историей без назначений: 31 − 8 = 23 выходных.
    assert rows[1]["days_off"] == 23 and rows[1]["shortfall"] == 0
    assert lo.shortfall_total(lo.days_off_per_month(payload, solved, 24)) == 23 + 1
    # Срез в месяце, который начат в горизонте (полный месяц) — переноса нет.
    wide = [_flight("W%d" % d, "2026-03-%02dT10:00:00" % d, "2026-03-%02dT12:00:00" % d)
            for d in range(1, 32)]
    payload2 = _payload(wide, [_hist("E1", "2026-03-01T00:00", historyMonthDaysWorked=0)])
    rows2 = lo.days_off_per_month(payload2, _solved([]), 8)
    assert rows2 == [{"employee": "E1", "month": "2026-03", "busy_days": 0, "days_off": 31,
                      "bound": 8, "shortfall": 0}]
    # Горизонт не доходит до конца месяца среза — месяц вне оценки.
    short = [_flight("S%d" % d, "2026-03-%02dT10:00:00" % d, "2026-03-%02dT12:00:00" % d)
             for d in range(10, 20)]
    assert lo.days_off_per_month(_payload(short, [_hist("E1", "2026-03-10T00:00",
                                                        historyMonthDaysWorked=8)]),
                                 _solved([]), 8) == []


def test_история_конец_последней_смены_открывает_окна():
    """Работа 3..10 по рейсу 06:00–20:00. Без истории старты окон 3..4 (2
    нарушения). Последняя смена истории кончилась 1-го в 20:00 — известный
    отдых до 3-го 06:00 (34 ч < 36), старты окон со 2-го: 2..8, 3..9, 4..10 —
    три нарушения. При работе 4..10 окно 2..8 содержит 54 ч отдыха — 2."""
    flights = [_flight("F%d" % d, "2026-03-%02dT06:00:00" % d, "2026-03-%02dT20:00:00" % d)
               for d in range(3, 11)]
    payload = _payload(flights, [{"id": "E1"},
                                 _hist("E2", "2026-03-03T00:00", historyLastDutyEnd="2026-03-01T20:00"),
                                 _hist("E3", "2026-03-03T00:00", historyLastDutyEnd="2026-03-01T20:00"),
                                 {"id": "E4"}])
    solved = _solved([("E1", "F%d" % d) for d in range(3, 11)]
                     + [("E2", "F%d" % d) for d in range(3, 11)]
                     + [("E3", "F%d" % d) for d in range(4, 11)]
                     + [("E4", "F%d" % d) for d in range(4, 11)])
    by = {r["employee"]: r for r in lo.rest_in_rolling_windows(payload, solved, 7, 2160)}
    assert (by["E1"]["windows"], by["E1"]["violated"]) == (2, 2)
    assert (by["E2"]["windows"], by["E2"]["violated"]) == (3, 3)
    assert (by["E3"]["windows"], by["E3"]["violated"]) == (3, 2)
    assert (by["E4"]["windows"], by["E4"]["violated"]) == (1, 1)
    # Конец последней смены ровно в полночь — те сутки уже полные (старт 2-го).
    p2 = _payload(flights, [_hist("E2", "2026-03-03T00:00", historyLastDutyEnd="2026-03-02T00:00")])
    rows = lo.rest_in_rolling_windows(p2, _solved([("E2", "F%d" % d) for d in range(3, 11)]), 7, 2160)
    assert (rows[0]["windows"], rows[0]["violated"]) == (3, 3)


def test_история_серия_ночных_продолжается():
    """Перенос 2 ночных из истории + одна ночная смена горизонта = серия 3
    при границе 2 (превышение 1); первая смена дневная — переноса нет;
    без истории — прежнее прочтение."""
    def payload_for(emp, flights):
        return {"flights": flights, "employees": [emp],
                "flightAssignments": [
                    {"id": "s%d" % i, "requiredSkill": "Pilot",
                     "flight": {"flightNumber": f["flightNumber"]},
                     "employee": {"id": emp["id"]}} for i, f in enumerate(flights)]}
    thr = {"home": 480, "outstation": 480}
    night = [_flight("N2", "2026-03-02T23:00:00", "2026-03-03T01:00:00")]
    p = payload_for(_hist("E1", "2026-03-02T00:00", historyConsecutiveNightDuties=2), night)
    rows = lo.night_runs_overage(p, {"flightAssignments": p["flightAssignments"]}, 1320, 360, 2, thr)
    assert [(r["nights"], r["overage"], r.get("carry")) for r in rows] == [(3, 1, 2)]
    # Первая смена дневная — перенос не действует, серия ночных потом 1.
    mixed = [_flight("D1", "2026-03-02T10:00:00", "2026-03-02T12:00:00"),
             _flight("N4", "2026-03-04T23:00:00", "2026-03-05T01:00:00")]
    p2 = payload_for(_hist("E1", "2026-03-02T00:00", historyConsecutiveNightDuties=2), mixed)
    rows2 = lo.night_runs_overage(p2, {"flightAssignments": p2["flightAssignments"]}, 1320, 360, 2, thr)
    assert [(r["nights"], r["overage"], r.get("carry")) for r in rows2] == [(1, 0, None)]
    # Без истории — одна ночная, нарушений нет.
    p3 = payload_for({"id": "E1"}, night)
    rows3 = lo.night_runs_overage(p3, {"flightAssignments": p3["flightAssignments"]}, 1320, 360, 2, thr)
    assert [(r["nights"], r["overage"]) for r in rows3] == [(1, 0)]


def test_счётчики_окон_выходных_и_ночных_с_календарём():
    """Те же случаи, что у kotlin LogicFormsTest: E1 работает 1..7 и 9..14
    (горизонт 1..14) — окно 7, K=2: нехватка 9 (окно 1..7 — 2, семь окон
    по 1); календарь истории открывает окна через границу; сутки без
    записи делают окно несудимым."""
    flights = [_flight("F%d" % d, "2026-03-%02dT10:00:00" % d, "2026-03-%02dT12:00:00" % d)
               for d in list(range(1, 8)) + list(range(9, 15))]
    cal = [{"date": "2026-02-%02d" % d, "kind": "work", "night": False} for d in (23, 24, 25, 28)] \
        + [{"date": "2026-02-%02d" % d, "kind": "day_off", "night": False} for d in (26, 27)]
    payload = _payload(flights, [{"id": "E1"},
                                 {"id": "E2", "historyAsOf": "2026-03-01T00:00",
                                  "history": {"historyCalendar": cal}},
                                 {"id": "E3", "historyAsOf": "2026-03-01T00:00",
                                  "history": {"historyCalendar": cal[:3] + cal[4:]
                                              + [{"date": "2026-02-28", "kind": "unknown"}]}}])
    solved = _solved([(e, "F%d" % d) for e in ("E1", "E2", "E3")
                      for d in list(range(1, 8)) + list(range(9, 15))])
    by = {r["employee"]: r for r in lo.days_off_in_rolling_windows(payload, solved, 7, 2)}
    # окон всего 14 (6 до горизонта + 8 внутри), без календаря судятся 8
    assert (by["E1"]["windows"], by["E1"]["judged"], by["E1"]["shortfall"]) == (14, 8, 9)
    # E2: календарь открывает 6 окон до горизонта: 23..1 (2 выходных), 24..2 (2),
    # 25..3 (2), 26..4 (2), 27..5 (1 — нехватка 1), 28..6 (0 — нехватка 2) → + 3
    assert (by["E2"]["windows"], by["E2"]["judged"], by["E2"]["shortfall"]) == (14, 14, 12)
    # E3: 28.02 без записи — окна с ним не судятся (6 окон до горизонта пропущены)
    assert (by["E3"]["windows"], by["E3"]["judged"], by["E3"]["shortfall"]) == (14, 8, 9)
    # K=1: E1 — окно 1..7 (1), E2 — окна 28..6 и 1..7 (2), E3 — окно 1..7 (1) → 4
    assert lo.window_shortfall_total(lo.days_off_in_rolling_windows(payload, solved, 7, 1)) == 4
    # ночные: смены 2, 3, 4 марта (23:00→01:00), горизонт 1..6, окно 3, K=2 → 1
    nights = [_flight("N%d" % d, "2026-03-%02dT23:00:00" % d, "2026-03-%02dT01:00:00" % (d + 1))
              for d in (2, 3, 4)] + [_flight("D1", "2026-03-01T10:00:00", "2026-03-01T12:00:00"),
                                     _flight("D6", "2026-03-06T10:00:00", "2026-03-06T12:00:00")]
    ncal = [{"date": "2026-02-27", "kind": "work", "night": True},
            {"date": "2026-02-28", "kind": "work", "night": True}]
    p2 = {"flights": nights,
          "employees": [{"id": "E1"}, {"id": "E2"},
                        {"id": "E3", "historyAsOf": "2026-03-01T00:00",
                         "history": {"historyCalendar": ncal}}],
          "flightAssignments": []}
    asg = []
    for emp in ("E1", "E3"):
        asg += [{"id": "%s-%d" % (emp, i), "requiredSkill": "Pilot",
                 "flight": {"flightNumber": "N%d" % d}, "employee": {"id": emp}}
                for i, d in enumerate((2, 3, 4))]
    asg += [{"id": "E2-1", "requiredSkill": "Pilot", "flight": {"flightNumber": "D1"},
             "employee": {"id": "E2"}},
            {"id": "E2-6", "requiredSkill": "Pilot", "flight": {"flightNumber": "D6"},
             "employee": {"id": "E2"}}]
    p2["flightAssignments"] = asg
    thr = {"home": 480, "outstation": 480}
    rows = lo.night_duties_in_rolling_windows(p2, {"flightAssignments": asg}, 3, 2, 1320, 360, thr)
    by2 = {r["employee"]: r for r in rows}
    assert (by2["E1"]["windows"], by2["E1"]["judged"], by2["E1"]["overage"]) == (6, 4, 1)
    assert by2["E2"]["overage"] == 0
    # E3: окна 27..1 (2), 28..2 (2), 1..3 (2), 2..4 (3) → 1 при K=2; при K=1
    # превышения 1+1+1+2+1+0 = 6, у E1 — 1+2+1+0 = 4
    assert (by2["E3"]["windows"], by2["E3"]["judged"], by2["E3"]["overage"]) == (6, 6, 1)
    assert lo.window_overage_total(lo.night_duties_in_rolling_windows(
        p2, {"flightAssignments": asg}, 3, 1, 1320, 360, thr)) == 4 + 6
