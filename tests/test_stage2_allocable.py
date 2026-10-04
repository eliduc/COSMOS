# -*- coding: utf-8 -*-
"""Отсев непригодных к назначению при подготовке этапа 2.

Решение владельца 2026-08-12 (простой способ): сотрудник с ЯВНЫМ
allocable=false решателю не передаётся — поля в домене решателя нет, и
без отсева решатель ставил бы такого человека наравне с годными
(доказано матрицей на живом решателе: случай allocable-false «сел»).

Три контракта:
  1. allocable=false — исключён из синтетического набора, счётчик виден;
  2. сотрудник БЕЗ поля — проходит как раньше (прежние наборы
     байт-идентичны);
  3. закреплённый планировщиком (pinned) остаётся даже при
     allocable=false: отсев — про новые назначения решателем, а не про
     отмену человеческих решений.
"""
import copy
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from _gerad_stage2_roster import build_synthetic_payload  # noqa: E402


def _payload(employees):
    return {
        "airports": [{"code": "AAA", "name": "AAA",
                      "latitude": 0.0, "longitude": 0.0},
                     {"code": "BBB", "name": "BBB",
                      "latitude": 1.0, "longitude": 1.0}],
        "employees": employees,
        "flights": [
            {"flightNumber": "F1", "departureAirport": "AAA",
             "departureUTCDateTime": "2026-08-01T06:00:00",
             "arrivalAirport": "BBB",
             "arrivalUTCDateTime": "2026-08-01T08:00:00",
             "aircraftType": "A320"},
            {"flightNumber": "F2", "departureAirport": "BBB",
             "departureUTCDateTime": "2026-08-01T09:00:00",
             "arrivalAirport": "AAA",
             "arrivalUTCDateTime": "2026-08-01T11:00:00",
             "aircraftType": "A320"},
        ],
        "flightAssignments": [
            {"id": "a1", "flight": "F1", "indexInFlight": 0,
             "requiredSkill": "Captain"},
            {"id": "a2", "flight": "F2", "indexInFlight": 0,
             "requiredSkill": "Captain"},
        ],
    }


CAT = [{"fns": ["F1", "F2"], "active_fns": ["F1", "F2"]}]


def _emp(eid, **over):
    e = {"id": eid, "name": eid, "homeAirport": "AAA",
         "skills": ["Captain"], "typeRatings": ["A320"]}
    e.update(over)
    return e


def test_not_allocable_excluded():
    p = _payload([_emp("E1", allocable=False), _emp("E2", allocable=True)])
    syn = build_synthetic_payload(p, copy.deepcopy(CAT))
    ids = [e["id"] for e in syn["employees"]]
    assert ids == ["E2"]
    assert build_synthetic_payload._excluded_not_allocable == 1


def test_missing_field_passes_unchanged():
    emps = [_emp("E1"), _emp("E2")]
    p = _payload(emps)
    syn = build_synthetic_payload(p, copy.deepcopy(CAT))
    # прежние наборы (без поля) — состав сотрудников тот же, те же объекты
    assert [e["id"] for e in syn["employees"]] == ["E1", "E2"]
    assert build_synthetic_payload._excluded_not_allocable == 0
    assert syn["employees"][0] is p["employees"][0]


def test_pinned_not_allocable_survives():
    # закрепление приезжает записью каталога с seats + lockedEmployees
    cat = [{"fns": ["F1", "F2"], "active_fns": ["F1", "F2"],
            "seats": ["Captain"], "lockedEmployees": ["E1"]}]
    p = _payload([_emp("E1", allocable=False), _emp("E2", allocable=True)])
    syn = build_synthetic_payload(p, cat)
    ids = [e["id"] for e in syn["employees"]]
    assert "E1" in ids, "закреплённый исчез — пин указывал бы в пустоту"
    pinned = [a for a in syn["flightAssignments"] if a.get("pinned")]
    assert pinned and pinned[0]["employee"] == "E1"
    assert build_synthetic_payload._excluded_not_allocable == 0
