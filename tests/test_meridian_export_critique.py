"""Исправления выгрузки по замечаниям авиакомпании от 31.07.2026.

Три замечания были признаны нашими дефектами:

1. Состав экипажа собирался ЗАНОВО по каждой смене из поучасткового
   спроса, тогда как построитель считает его по ВСЕЙ командировке (один
   экипаж везёт её целиком) и эту численность оплачивает. Выгрузка из-за
   этого систематически занижала состав и давала разное число мест у смен
   одной командировки.
2. Границы смены писались вылетом и прилётом, поэтому `workTimeCredit`
   содержал размах полётов, а не рабочее время. Сам расчёт подготовку и
   разбор учитывал всегда — не отражала их именно выгрузка.
3. Перелёты пассажиром выделялись в отдельный наряд командировки. В
   формате источника такого наряда нет, и именно он порождал смены с
   пустым списком мест.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import _payload_to_meridian as pm  # noqa: E402


def _leg(fn, dep, arr, start, minutes, brief=None, debrief=None):
    t0 = datetime.fromisoformat(start)
    out = {"flightNumber": fn, "departureAirport": dep, "arrivalAirport": arr,
           "departureUTCDateTime": t0.isoformat(),
           "arrivalUTCDateTime": (t0 + timedelta(minutes=minutes)).isoformat(),
           "fleet": "B787-9"}
    if brief is not None:
        out["briefingMin"] = brief
    if debrief is not None:
        out["debriefingMin"] = debrief
    return out


def _payload(flights, seats=("PURS", "CA")):
    assigns = []
    for f in flights:
        for i, role in enumerate(seats):
            assigns.append({"id": f"{f['flightNumber']}#{i}",
                            "flight": f["flightNumber"], "indexInFlight": i,
                            "requiredSkill": role})
    return {"airports": [{"code": c} for c in ("BSE", "AAA", "BBB")],
            "employees": [], "flights": flights, "flightAssignments": assigns}


def _two_duty_chain():
    """Командировка из двух смен: BSE→AAA и AAA→BSE через сутки."""
    flights = [
        _leg("F1", "BSE", "AAA", "2026-08-01T10:00:00", 120,
             brief=100, debrief=30),
        _leg("F2", "AAA", "BSE", "2026-08-02T10:00:00", 120,
             brief=60, debrief=60),
    ]
    catalog = {"selected": [{
        "fns": ["F1", "F2"], "active_fns": ["F1", "F2"],
        "duty_fns": [["F1"], ["F2"]],
        # состав ЦЕПОЧКИ, посчитанный построителем (с надбавкой за ярус)
        "seats": ["PURS", "CA", "CA", "CA"],
        "kind": "chain", "sourceLegsMissing": 0}]}
    return _payload(flights), catalog


def test_complements_are_chain_level_and_identical_across_duties():
    """Состав берётся из каталога и одинаков во всех сменах командировки.

    Прежде каждая смена получала свой поучастковый максимум: у смены с
    одним участком выходило два места вместо четырёх, посчитанных
    построителем.
    """
    payload, catalog = _two_duty_chain()
    doc, check, errors = pm.export(payload, catalog, level=3)
    assert errors == []
    duties = doc["duties"]
    assert len(duties) == 2
    ranks = [[c["complementRank"] for c in d["complements"]] for d in duties]
    assert ranks[0] == ranks[1] == ["PURS", "CA", "CA", "CA"], ranks
    assert check["complements_provenance"].get("каталог") == 1


def test_catalog_crew_is_not_reduced_to_leg_demand():
    """Численность каталога НЕ пересчитывается вниз по спросу участка.

    Именно это занижение дало в поставке 536 смен с меньшим составом, чем
    во внутреннем решении, в том числе 11 мест → 2 на широкофюзеляжной
    смене.
    """
    payload, catalog = _two_duty_chain()
    # спрос участка — два места, каталог несёт четыре
    doc, _check, errors = pm.export(payload, catalog, level=3)
    assert errors == []
    assert all(len(d["complements"]) == 4 for d in doc["duties"])


def test_duty_bounds_carry_briefing_and_debriefing():
    """Границы смены и рабочее время несут подготовку и разбор."""
    payload, catalog = _two_duty_chain()
    doc, _check, errors = pm.export(payload, catalog, level=3)
    assert errors == []
    first = next(d for d in doc["duties"] if d["numberOfDuty"] == 1)
    # 10:00 минус 100 минут подготовки
    assert first["start"] == "2026-08-01T08:20:00"
    # 12:00 плюс 30 минут разбора
    assert first["end"] == "2026-08-01T12:30:00"
    # рабочее время = 100 + 120 + 30
    assert first["workTimeCredit"] == "250"
    # полётное время осталось чистым
    assert first["flightTimeCredit"] == "120"


def test_work_time_is_not_flight_span():
    """`workTimeCredit` перестал быть размахом полётов."""
    payload, catalog = _two_duty_chain()
    doc, _check, _e = pm.export(payload, catalog, level=3)
    for d in doc["duties"]:
        assert int(d["workTimeCredit"]) > int(d["flightTimeCredit"]), (
            "рабочее время обязано превышать полётное на подготовку и разбор")


def test_no_duty_has_empty_complements():
    """Смен с пустым списком мест не существует: отдельного наряда
    перелёта пассажиром больше нет."""
    payload, catalog = _two_duty_chain()
    doc, _check, _e = pm.export(payload, catalog, level=3)
    assert all(d["complements"] for d in doc["duties"])
    assert all(not d["dutyId"].endswith("-DH") for d in doc["duties"])


def test_every_duty_declares_assignment_type_flight():
    """Верификатор заказчика использует тип наряда; он обязан быть у всех
    смен, включая содержащие перегоны."""
    payload, catalog = _two_duty_chain()
    doc, _check, _e = pm.export(payload, catalog, level=3)
    assert all(d["assignmentType"] == "FLIGHT" for d in doc["duties"])


def test_pairing_uses_empty_object_for_requirements():
    """Пустой ОБЪЕКТ, а не пустой массив; переопределение отдыха — ноль."""
    payload, catalog = _two_duty_chain()
    doc, _check, _e = pm.export(payload, catalog, level=3)
    p = doc["pairings"][0]
    assert p["requirements"] == {}
    assert p["restOverride"] == 0
    assert all(d["restOverride"] == 0 for d in doc["duties"])


def test_export_without_source_is_declared_incomplete():
    """Сборка без справочника источника объявляется громко.

    Именно так была собрана поставка, вызвавшая замечания по формату: у
    участков не было типа судна и перевозчика, у документа —
    идентификаторов набора и пресета. Механизм переноса в экспортёре был,
    его просто не задействовали.
    """
    payload, catalog = _two_duty_chain()
    _doc, check, _e = pm.export(payload, catalog, level=3)
    assert check["source_reference_used"] is False
    assert check["format_incomplete_without_source"] is True


def test_dataset_id_follows_the_source_reference():
    """Идентификатор набора берётся из справочника источника: по нему
    верификатор связывает выгрузку с постановкой."""
    payload, catalog = _two_duty_chain()
    source = {"format": 1, "datasetId": "uuid-from-customer",
              "division": "CC", "presetId": "preset-uuid",
              "planSince": "2026-08-01", "planUntil": "2026-08-31",
              "legs": [], "airports": [{"IATA": "BSE", "ICAO": "XBSE"}]}
    doc, check, _e = pm.export(payload, catalog, level=3, source=source)
    assert doc["datasetId"] == "uuid-from-customer"
    assert doc["division"] == "CC"
    assert doc["presetId"] == "preset-uuid"
    assert doc["planSince"] == "2026-08-01"
    assert check["source_reference_used"] is True
    assert all(d["crewType"] == "CC" for d in doc["duties"])
