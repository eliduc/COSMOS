# -*- coding: utf-8 -*-
"""Оракул резерва (строка 8 свода) — независимое прочтение на Python.

Проверяется прочтение, а не форма: по ролям, а не «любые двое»; база
без людей даёт нехватку; отпуск — не резерв; сутки по датам вылета и
прилёта; рейс через полночь занимает обе даты.
"""

import pytest

from webapp import reserve_oracle as ro


def _emp(i, base, role, unavailable=()):
    return {"id": i, "homeAirport": {"code": base}, "skills": [role],
            "unavailableDays": list(unavailable)}


def _flight(num, dep, arr):
    return {"flightNumber": num, "flightLegId": num + "|" + dep,
            "departureUTCDateTime": dep, "arrivalUTCDateTime": arr,
            "departureAirport": {"code": "PLY"}, "arrivalAirport": {"code": "OUT"}}


def _payload(flights, employees):
    seats = []
    for f in flights:
        for role in ("Captain", "FirstOfficer"):
            seats.append({"flight": f["flightLegId"], "requiredSkill": role})
    return {"flights": flights, "employees": employees, "flightAssignments": seats}


def _solved(assignments):
    return {"flightAssignments": [{"flight": leg, "employee": {"id": emp} if emp else None,
                                   "requiredSkill": role}
                                  for leg, role, emp in assignments]}


X = _flight("X", "2026-03-02T10:00:00", "2026-03-02T12:00:00")
CREW = [_emp("C1", "PLY", "Captain"), _emp("C2", "PLY", "Captain"),
        _emp("F1", "PLY", "FirstOfficer"), _emp("F2", "PLY", "FirstOfficer")]


def test_все_свободны_ноль_экипаж_занят_один():
    p = _payload([X], CREW)
    rows = ro.reserve_table(p, _solved([]), {"PLY": 2})
    assert ro.total_shortfall(rows) == 0 and rows[0]["crews"] == 2
    rows = ro.reserve_table(p, _solved([(X["flightLegId"], "Captain", "C1"),
                                        (X["flightLegId"], "FirstOfficer", "F1")]), {"PLY": 2})
    assert ro.total_shortfall(rows) == 1
    assert rows[0]["free"] == {"Captain": 1, "FirstOfficer": 1}


def test_счёт_по_ролям_не_любые_двое():
    p = _payload([X], CREW)
    rows = ro.reserve_table(p, _solved([(X["flightLegId"], "Captain", "C1")]), {"PLY": 2})
    assert rows[0]["free"] == {"Captain": 1, "FirstOfficer": 2}
    assert rows[0]["crews"] == 1 and ro.total_shortfall(rows) == 1


def test_отпуск_не_резерв():
    crew = [_emp("C1", "PLY", "Captain"), _emp("C2", "PLY", "Captain"),
            _emp("F1", "PLY", "FirstOfficer", ["2026-03-02"]), _emp("F2", "PLY", "FirstOfficer")]
    rows = ro.reserve_table(_payload([X], crew), _solved([]), {"PLY": 2})
    assert rows[0]["free"]["FirstOfficer"] == 1 and ro.total_shortfall(rows) == 1


def test_база_без_людей_даёт_нехватку_равную_норме():
    rows = ro.reserve_table(_payload([X], CREW), _solved([]), {"GRT": 1})
    assert rows[0]["employees_of_base"] == 0 and rows[0]["crews"] == 0
    assert ro.total_shortfall(rows) == 1


def test_горизонт_и_полночь():
    y = _flight("Y", "2026-03-03T10:00:00", "2026-03-03T12:00:00")
    p = _payload([X, y], CREW)
    rows = ro.reserve_table(p, _solved([(X["flightLegId"], "Captain", "C1"),
                                        (X["flightLegId"], "FirstOfficer", "F1")]), {"PLY": 2})
    assert [r["day"] for r in rows] == ["2026-03-02", "2026-03-03"]
    assert [r["shortfall"] for r in rows] == [1, 0]
    # Единый план 05.09.2026 (день 1, находка аудитора 5.2): сутки горизонта
    # — по вылету; прилёт 3-го после полуночи не создаёт суток резерва
    # 3-го марта (то же чтение, что у решателя).
    night = _flight("N", "2026-03-02T23:00:00", "2026-03-03T01:00:00")
    p = _payload([night], CREW)
    rows = ro.reserve_table(p, _solved([(night["flightLegId"], "Captain", "C1"),
                                        (night["flightLegId"], "FirstOfficer", "F1")]), {"PLY": 2})
    assert [r["day"] for r in rows] == ["2026-03-02"]
    assert [r["shortfall"] for r in rows] == [1]
    # дневной рейс 3-го держит сутки 3-го в горизонте; ночной экипаж занят и 3-го
    p = _payload([night, y], CREW)
    rows = ro.reserve_table(p, _solved([(night["flightLegId"], "Captain", "C1"),
                                        (night["flightLegId"], "FirstOfficer", "F1")]), {"PLY": 2})
    assert [r["shortfall"] for r in rows] == [1, 1]


def test_без_норм_пусто_без_рейсов_отказ():
    assert ro.reserve_table(_payload([X], CREW), _solved([]), {}) == []
    with pytest.raises(ro.ReserveOracleError):
        ro.reserve_table(_payload([], CREW), _solved([]), {"PLY": 1})


def test_нормы_из_отчёта_и_пересечения():
    rep = {"lastRun": {"thresholds": {"applied": [
        {"threshold": "reserve_crews_min[PLY]", "value": 2},
        {"threshold": "crew_size", "value": 2}]}}}
    assert ro.norms_from_applied(rep) == {"PLY": 2}
    z = _flight("Z", "2026-03-02T11:00:00", "2026-03-02T13:00:00")   # пересекается с X
    p = _payload([X, z], CREW)
    solved = _solved([(X["flightLegId"], "Captain", "C1"), (z["flightLegId"], "Captain", "C1"),
                      (z["flightLegId"], "FirstOfficer", None)])
    assert ro.overlapping_pairs(p, solved) == 1
    assert ro.unassigned_seats(solved) == 1
