"""C01/C02 фаза 3 — первоклассные поля flightLegId/crewSlotId в каноне.

Условные ссылочные проверки validate_payload срабатывают ТОЛЬКО при наличии
полей; payload'ы без них (BTS/дельты с повторными номерами по датам) проходят
как раньше — это охраняется тестами ниже.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from webapp import registry  # noqa: E402


def _base():
    return {
        "airports": [{"code": "AAA"}, {"code": "BBB"}],
        "employees": [
            {"id": "P1", "homeAirport": "AAA", "skills": ["Captain"]}],
        "flights": [
            {"flightNumber": "F1", "departureAirport": "AAA",
             "departureUTCDateTime": "2026-03-01T08:00:00",
             "arrivalAirport": "BBB",
             "arrivalUTCDateTime": "2026-03-01T10:00:00"},
            {"flightNumber": "F1", "departureAirport": "AAA",
             "departureUTCDateTime": "2026-03-02T08:00:00",
             "arrivalAirport": "BBB",
             "arrivalUTCDateTime": "2026-03-02T10:00:00"}],
        "flightAssignments": [
            {"id": "a1", "flight": "F1", "indexInFlight": 0,
             "requiredSkill": "Captain"}],
    }


def test_repeated_flight_number_without_legid_still_valid():
    # Слой дельт легитимно несёт повторные номера по датам — без flightLegId
    # проверки фазы 3 инертны и ничего не меняют.
    assert registry.validate_payload(_base())["ok"] is True


def test_leg_id_materialized_daily_flight_valid():
    p = _base()
    p["flights"][0]["flightLegId"] = "F1|2026-03-01T08:00:00"
    p["flights"][1]["flightLegId"] = "F1|2026-03-02T08:00:00"
    p["flightAssignments"][0]["flightLegId"] = "F1|2026-03-01T08:00:00"
    p["flightAssignments"][0]["crewSlotId"] = "s1"
    out = registry.validate_payload(p)
    assert out["ok"] is True, out


def test_duplicate_leg_id_rejected():
    p = _base()
    p["flights"][0]["flightLegId"] = "F1|X"
    p["flights"][1]["flightLegId"] = "F1|X"
    out = registry.validate_payload(p)
    assert out["ok"] is False
    assert any("flightLegId" in e and "duplicated" in e for e in out["errors"])


def test_assignment_dangling_leg_id_rejected():
    p = _base()
    p["flights"][0]["flightLegId"] = "F1|2026-03-01T08:00:00"
    p["flights"][1]["flightLegId"] = "F1|2026-03-02T08:00:00"
    p["flightAssignments"][0]["flightLegId"] = "F1|NOPE"
    out = registry.validate_payload(p)
    assert out["ok"] is False
    assert any("flightLegId 'F1|NOPE' not in flights" in e
               for e in out["errors"])


def test_duplicate_crew_slot_id_rejected():
    p = _base()
    p["flightAssignments"].append(
        {"id": "a2", "flight": "F1", "indexInFlight": 1,
         "requiredSkill": "Captain"})
    p["flightAssignments"][0]["crewSlotId"] = "s1"
    p["flightAssignments"][1]["crewSlotId"] = "s1"
    out = registry.validate_payload(p)
    assert out["ok"] is False
    assert any("seat identity 's1' duplicated" in e for e in out["errors"])


def test_partial_materialization_allowed():
    # Часть рейсов с flightLegId, часть без — допустимо (аддитивность):
    # уникальность проверяется среди присутствующих.
    p = _base()
    p["flights"][0]["flightLegId"] = "F1|2026-03-01T08:00:00"
    assert registry.validate_payload(p)["ok"] is True


def test_unhashable_id_values_do_not_crash():
    # Ревью (находка 1): list/dict в поле идентичности должны дать честную
    # schema-ошибку, а не TypeError→500 в ссылочном проходе.
    p = _base()
    p["flights"][0]["flightLegId"] = ["x"]
    p["flightAssignments"][0]["crewSlotId"] = {"a": 1}
    p["flightAssignments"][0]["flightLegId"] = ["y"]
    out = registry.validate_payload(p)          # не должен бросить
    assert out["ok"] is False
    assert any("schema" in e for e in out["errors"])


def test_effective_planning_id_collision_rejected():
    # Ревью (находки 5/12): planning id солвера = crewSlotId-или-id; коллизия
    # между явным crewSlotId и фолбэк-id другого места — отказ.
    p = _base()
    p["flightAssignments"] = [
        {"id": "1", "crewSlotId": "2", "flight": "F1", "indexInFlight": 0,
         "requiredSkill": "Captain"},
        {"id": "2", "flight": "F1", "indexInFlight": 1,
         "requiredSkill": "Captain"}]
    out = registry.validate_payload(p)
    assert out["ok"] is False
    assert any("seat identity '2' duplicated" in e for e in out["errors"])


def test_duplicate_raw_id_with_distinct_crewslotid_rejected():
    # Аудит 2026-07-19 (P1-02b): equals/hashCode и self-join-гарды Kotlin
    # ключуются по СЫРОМУ id, хотя planning id = crewSlotId-или-id. Дубль
    # raw id при различных crewSlotId рождает две различные planning-сущности,
    # которые equals-равны — парные ограничения молча выключаются. Отказ.
    p = _base()
    p["flightAssignments"] = [
        {"id": "1", "crewSlotId": "SEAT-A", "flight": "F1",
         "indexInFlight": 0, "requiredSkill": "Captain"},
        {"id": "1", "crewSlotId": "SEAT-B", "flight": "F1",
         "indexInFlight": 1, "requiredSkill": "Captain"}]
    out = registry.validate_payload(p)
    assert out["ok"] is False
    assert any("raw id '1' duplicated" in e for e in out["errors"])


def test_legacy_duplicate_ids_without_crewslotid_still_pass():
    # Без единого crewSlotId проверка эффективного пространства не включается
    # — легаси-payload'ы с дубликатами id валидируются как раньше.
    p = _base()
    p["flightAssignments"] = [
        {"id": "1", "flight": "F1", "indexInFlight": 0,
         "requiredSkill": "Captain"},
        {"id": "1", "flight": "F1", "indexInFlight": 1,
         "requiredSkill": "Captain"}]
    assert registry.validate_payload(p)["ok"] is True


def test_borrowed_leg_id_caught_when_named_flight_unmaterialized():
    # Второй раунд ревью: слот называет НЕматериализованный рейс, но несёт
    # leg id ЧУЖОГО рейса — владелец id известен, подмена ловится.
    p = _base()
    p["flights"][1]["flightNumber"] = "F2"
    p["flights"][1]["flightLegId"] = "F2|2026-03-02T08:00:00"
    # flights[0] (F1) НЕ материализован; слот: flight=F1, leg id рейса F2
    p["flightAssignments"][0]["flightLegId"] = "F2|2026-03-02T08:00:00"
    out = registry.validate_payload(p)
    assert out["ok"] is False
    assert any("does not belong to flight 'F1'" in e for e in out["errors"])


def test_unhashable_values_in_legacy_reference_fields_no_crash():
    # Второй раунд ревью: тот же класс крашей в СТАРЫХ ссылочных проверках
    # (flight-ссылка слота, аэропорты, employee v2-сущностей) — не 500.
    p = _base()
    p["flights"][0]["departureAirport"] = {"x": 1}
    p["flightAssignments"][0]["flight"] = ["F1"]
    p["employees"][0]["homeAirport"] = ["AAA"]
    p["qualifications"] = [{"employee": {"d": 1}, "kind": "medical",
                            "code": "C1", "validTo": "2026-12-31"}]
    p["contracts"] = [{"employee": ["P1"]}]
    p["dutyHistory"] = [{"employee": ["P1"], "month": "2026-02"}]
    out = registry.validate_payload(p)          # не должен бросить
    assert out["ok"] is False
    assert any("schema" in e for e in out["errors"])


def test_slot_leg_id_must_belong_to_its_flight():
    # Ревью (находка 9): flightLegId места обязан принадлежать рейсу из его
    # же ссылки flight — иначе точный CNL снял бы экипаж не с того рейса.
    p = _base()
    p["flights"][0]["flightLegId"] = "F1|2026-03-01T08:00:00"
    p["flights"][1]["flightLegId"] = "F1|2026-03-02T08:00:00"
    p["flights"][1]["flightNumber"] = "F2"
    p["flightAssignments"][0]["flightLegId"] = "F1|2026-03-02T08:00:00"
    out = registry.validate_payload(p)
    assert out["ok"] is False
    assert any("does not belong to flight" in e for e in out["errors"])
