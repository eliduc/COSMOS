"""Тесты выгрузки цепочек в рабочий формат вендора (`_pairing_to_solver.py`).

Главная проверка — ОБРАТНЫЙ ИМПОРТ: выгруженный файл читается тем же
адаптером, что и оригинал вендора, и восстановленные командировки
сверяются с исходным каталогом. Сверка чисел в отчёте доказывала бы лишь
то, что мы умеем считать свои же строки; сверка через чужой разбор
доказывает, что файл читается ЕГО средствами так же, как задумано
нашими.

Синтетика вымышленного перевозчика: данных заказчика в тестах нет.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

_pts = pytest.importorskip("_pairing_to_solver")


def _leg(fn, dep, arr, start, minutes, brief=None, debrief=None):
    t0 = datetime.fromisoformat(start)
    code, day, no = fn.split("-")
    src = "%s$%s.%s.%s$%s" % (code, day[6:8], day[4:6], day[0:4], no)
    f = {"flightNumber": fn, "sourceLegId": src,
         "departureAirport": dep, "arrivalAirport": arr,
         "departureUTCDateTime": t0.isoformat(),
         "arrivalUTCDateTime": (t0 + timedelta(minutes=minutes)).isoformat(),
         "aircraftType": "A320"}
    if brief is not None:
        f["briefingMin"] = brief
    if debrief is not None:
        f["debriefingMin"] = debrief
    return f


def _fixture():
    """Командировка из двух смен с перелётом пассажиром домой."""
    flights = [
        _leg("QQ100-20260801-1", "BASE", "OUT", "2026-08-01T06:00", 120,
             brief=100, debrief=60),
        _leg("QQ101-20260803-1", "OUT", "FAR", "2026-08-03T06:00", 120,
             brief=60, debrief=30),
        _leg("QQ102-20260803-1", "FAR", "BASE", "2026-08-03T14:00", 120),
    ]
    assigns = []
    for f in flights:
        for i, rank in enumerate(("Captain", "CA")):
            assigns.append({"id": "%s#%d" % (f["flightNumber"], i),
                            "flight": f["flightNumber"], "indexInFlight": i,
                            "requiredSkill": rank})
    payload = {
        "flights": flights, "flightAssignments": assigns,
        "employees": [{"id": "E1", "homeAirport": "BASE"}],
        "airports": [{"code": "BASE", "timeZone": "Asia/BaseCity"},
                     {"code": "OUT", "timeZone": "Europe/Istanbul"},
                     {"code": "FAR", "timeZone": "Europe/Istanbul"}],
    }
    catalog = {"selected": [{
        "fns": [f["flightNumber"] for f in flights],
        "active_fns": ["QQ100-20260801-1", "QQ101-20260803-1"],
        "duty_fns": [["QQ100-20260801-1"], ["QQ101-20260803-1"]],
        "seats": ["Captain", "CA"], "kind": "chain", "sourceLegsMissing": 0,
        "sourceUnit": "pairing-builder-l3:0"}]}
    source = {
        "format": "5",
        "legs": [{"id": f["sourceLegId"], "flightCode": f["flightNumber"][:5],
                  "acType": "A320",
                  "departureStation": f["departureAirport"],
                  "arrivalStation": f["arrivalAirport"],
                  "start": f["departureUTCDateTime"] + "+05:00[Asia/BaseCity]",
                  "end": f["arrivalUTCDateTime"] + "+05:00[Asia/BaseCity]",
                  "deadhead": False, "allocable": True}
                 for f in flights],
        "stations": [{"iata": "BASE", "timeZone": "Asia/BaseCity"},
                     {"iata": "OUT", "timeZone": "Europe/Istanbul"},
                     {"iata": "FAR", "timeZone": "Europe/Istanbul"}],
        "parameters": {},
    }
    return payload, catalog, source


def test_export_writes_our_chain_structure_not_the_vendors():
    """Выгрузка пишет НАШИ смены и командировки. Прежний инструмент
    заполнял людей в чужую структуру — для результата первого этапа это
    не годится: у нас другие смены и другие перелёты пассажиром."""
    payload, catalog, source = _fixture()
    doc, check = _pts.convert(payload, catalog, source)
    assert check["ok"], check["errors"]
    assert check["duties"] == 3          # две рабочие смены + наряд перегона
    assert check["командировок"] == 1
    # места экипажа: командировка на КАЖДОЕ место, как у вендора
    assert len({s["crewPosition"] for s in doc["sequences"]}) == 2
    assert check["sequences"] == 2
    # участки, станции и настройки взяты из источника без изменений
    assert doc["legs"] == source["legs"]
    assert doc["stations"] == source["stations"]


def test_positioning_legs_get_a_passenger_role():
    payload, catalog, source = _fixture()
    doc, _check = _pts.convert(payload, catalog, source)
    roles = {r["crewRole"] for r in doc["leg2Duties"]}
    assert "ONLY_DH" in roles or "LAST_DH" in roles
    work = [r for r in doc["leg2Duties"] if r["crewRole"] in ("STD", "ENF", "DBL")]
    assert len(work) == 2, "рабочих участков ровно два"


def test_crew_tier_follows_work_time_not_block_time():
    """Ярус состава берётся по РАБОЧЕМУ времени: предполётная подготовка
    входит в него и может перевести смену в следующий ярус."""
    payload, catalog, source = _fixture()
    # смена: 120 мин блочного + 100 подготовки + 60 разбора = 280 минут
    doc, _c = _pts.convert(payload, catalog, source, tiers=(200, 400, 500))
    d = next(x for x in doc["duties"] if x["flight"])
    assert d["crewType"] == "ENF", d["crewType"]
    doc2, _c2 = _pts.convert(payload, catalog, source, tiers=(600, 700, 800))
    d2 = next(x for x in doc2["duties"] if x["flight"])
    assert d2["crewType"] == "STD"


def test_no_employees_and_no_borrowed_score():
    """Назначение людей — отдельный этап, выдавать пустое за сделанное
    нельзя. Оценка вендора описывает ЕГО функцию качества, которую COSMOS
    не воспроизводит."""
    payload, catalog, source = _fixture()
    doc, _check = _pts.convert(payload, catalog, source)
    assert doc["employees"] == []
    assert doc["score"] is None
    assert doc["metrics"] == []
    assert all(a["employee"] is None for a in doc["employeeAssignments"])


def test_chain_without_duty_boundaries_is_refused_loudly():
    """Без границ смен выгрузка невозможна: вендорский формат хранит
    участок В СМЕНЕ, и склеить командировку в одну смену значило бы
    объявить другую структуру работы."""
    payload, catalog, source = _fixture()
    catalog["selected"][0].pop("duty_fns")
    _doc, check = _pts.convert(payload, catalog, source)
    assert not check["ok"]
    assert any("границ смен" in e for e in check["errors"])


def test_round_trip_restores_the_same_chains(tmp_path):
    """Единственное настоящее доказательство: файл читается ТЕМ ЖЕ
    адаптером, что и оригинал вендора, и командировки восстанавливаются
    те же. Совпадение чисел в отчёте доказывало бы лишь то, что мы умеем
    считать свои же строки."""
    payload, catalog, source = _fixture()
    doc, check = _pts.convert(payload, catalog, source)
    assert check["ok"]
    out = tmp_path / "solution_format5.json"
    out.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    rt = _pts.verify_round_trip(out, catalog)
    assert rt["ошибок чтения"] == 0, rt
    assert rt["потеряно"] == 0, rt
    assert rt["совпало"] == rt["командировок у нас"], rt
