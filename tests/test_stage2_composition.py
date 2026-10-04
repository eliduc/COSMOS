"""Путь Б (2026-07-24): пер-записный состав мест в каталоге этапа 2.

Три контракта:
  1. `pipeline_representable`/`assert_representable` с
     allow_heterogeneous_seats=True снимают ТОЛЬКО причину C01
     (неоднородная сигнатура); C02 (коллизия идентичности участков)
     остаётся фатальной безусловно.
  2. `build_synthetic_payload` над записью с "seats" выпускает места
     ровно по списку записи (id "i:idx", indexInFlight по позиции).
  3. Записи БЕЗ "seats" идут прежней веткой — синтетика для существующих
     каталогов байт-идентична (закреплено сравнением до/после).
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from _leg_identity import (GeradPipelineUnsupported,          # noqa: E402
                           assert_representable,
                           pipeline_representable)


def _payload(uniform: bool) -> dict:
    """Two-leg canonical payload; non-uniform when uniform=False."""
    flights = [
        {"flightNumber": "F1", "flightLegId": "F1|2026-08-01T06:00:00",
         "departureAirport": "AAA",
         "departureUTCDateTime": "2026-08-01T06:00:00",
         "arrivalAirport": "BBB",
         "arrivalUTCDateTime": "2026-08-01T08:00:00",
         "aircraftType": "A320neo"},
        {"flightNumber": "F2", "flightLegId": "F2|2026-08-01T09:00:00",
         "departureAirport": "BBB",
         "departureUTCDateTime": "2026-08-01T09:00:00",
         "arrivalAirport": "AAA",
         "arrivalUTCDateTime": "2026-08-01T11:00:00",
         "aircraftType": "A320neo"},
    ]
    slots = [
        {"id": "F1#0", "crewSlotId": "F1#0", "flight": "F1",
         "flightLegId": "F1|2026-08-01T06:00:00", "indexInFlight": 0,
         "requiredSkill": "PURS", "employee": None},
        {"id": "F1#1", "crewSlotId": "F1#1", "flight": "F1",
         "flightLegId": "F1|2026-08-01T06:00:00", "indexInFlight": 1,
         "requiredSkill": "CA", "employee": None},
        {"id": "F2#0", "crewSlotId": "F2#0", "flight": "F2",
         "flightLegId": "F2|2026-08-01T09:00:00", "indexInFlight": 0,
         "requiredSkill": "PURS" if uniform else "CA", "employee": None},
        {"id": "F2#1", "crewSlotId": "F2#1", "flight": "F2",
         "flightLegId": "F2|2026-08-01T09:00:00", "indexInFlight": 1,
         "requiredSkill": "CA", "employee": None},
    ]
    return {
        "airports": [{"code": "AAA"}, {"code": "BBB"}],
        "employees": [{"id": "E1", "homeAirport": "AAA",
                       "skills": ["PURS", "CA"],
                       "typeRatings": ["A320neo"]}],
        "flights": flights,
        "flightAssignments": slots,
    }


# ------------------------------------------------------------------ гейт C01

def test_heterogeneous_seats_refused_by_default():
    reasons = pipeline_representable(_payload(uniform=False))
    assert any("неоднородный" in r for r in reasons)
    with pytest.raises(GeradPipelineUnsupported):
        assert_representable(_payload(uniform=False), "pytest")


def test_allow_heterogeneous_seats_lifts_only_c01():
    p = _payload(uniform=False)
    assert pipeline_representable(p, allow_heterogeneous_seats=True) == []
    assert_representable(p, "pytest", allow_heterogeneous_seats=True)
    # C02 — дубль идентичности участка — остаётся фатальным и с флагом.
    p2 = _payload(uniform=False)
    p2["flights"][1]["flightLegId"] = p2["flights"][0]["flightLegId"]
    p2["flights"][1]["flightNumber"] = p2["flights"][0]["flightNumber"]
    reasons = pipeline_representable(p2, allow_heterogeneous_seats=True)
    assert reasons, "C02 обязан остаться причиной отказа"
    with pytest.raises(GeradPipelineUnsupported):
        assert_representable(p2, "pytest", allow_heterogeneous_seats=True)


def test_uniform_payload_unaffected_by_flag():
    p = _payload(uniform=True)
    assert pipeline_representable(p) == []
    assert pipeline_representable(p, allow_heterogeneous_seats=True) == []


# --------------------------------------------------------- синтетика этапа 2

def _import_stage2():
    import importlib
    return importlib.import_module("_gerad_stage2_roster")


def test_seats_record_emits_own_composition():
    st2 = _import_stage2()
    p = _payload(uniform=False)
    selected = [
        {"fns": ["F1"], "active_fns": ["F1"], "cost": 0,
         "seats": ["PURS", "CA"]},
        {"fns": ["F2"], "active_fns": ["F2"], "cost": 0,
         "seats": ["CA", "CA"]},
    ]
    syn = st2.build_synthetic_payload(p, selected)
    assert len(syn["flights"]) == 2
    by_flight = {}
    for a in syn["flightAssignments"]:
        by_flight.setdefault(a["flight"], []).append(a)
    f1 = sorted(by_flight["C_000000"], key=lambda a: a["indexInFlight"])
    f2 = sorted(by_flight["C_000001"], key=lambda a: a["indexInFlight"])
    assert [a["requiredSkill"] for a in f1] == ["PURS", "CA"]
    assert [a["requiredSkill"] for a in f2] == ["CA", "CA"]
    assert [a["id"] for a in f1] == ["1:0", "1:1"]
    assert [a["id"] for a in f2] == ["2:0", "2:1"]
    assert all(a.get("employee") is None or "employee" not in a
               for a in syn["flightAssignments"])


def test_records_without_seats_are_byte_identical():
    """Существующие каталоги (без seats) обязаны давать ту же синтетику,
    что и до пути Б: сравниваем с эталоном, построенным по прежним
    правилам (crew_roles над однородным payload'ом)."""
    st2 = _import_stage2()
    from _leg_identity import crew_roles
    p = _payload(uniform=True)
    selected = [{"fns": ["F1", "F2"], "active_fns": ["F1", "F2"],
                 "cost": 5}]
    syn = st2.build_synthetic_payload(p, selected)
    roles = crew_roles(p)
    assert roles is not None and len(roles) == 2
    slots = sorted(syn["flightAssignments"],
                   key=lambda a: a["indexInFlight"])
    assert [a["requiredSkill"] for a in slots] == [s for _, s in roles]
    # Прежний формат id многоролевой ветки: "i+1:idx".
    assert [a["id"] for a in slots] == ["1:0", "1:1"]
    assert json.dumps(syn, sort_keys=True)  # сериализуемость

    # Смешанный каталог: запись с seats получает свой состав, запись без —
    # глобальные роли; обе живут в одной синтетике.
    mixed = [{"fns": ["F1"], "active_fns": ["F1"], "cost": 0,
              "seats": ["CA"]},
             {"fns": ["F2"], "active_fns": ["F2"], "cost": 0}]
    syn2 = st2.build_synthetic_payload(p, mixed)
    by_flight = {}
    for a in syn2["flightAssignments"]:
        by_flight.setdefault(a["flight"], []).append(a)
    assert [a["requiredSkill"] for a in by_flight["C_000000"]] == ["CA"]
    assert sorted(a["requiredSkill"]
                  for a in by_flight["C_000001"]) == ["CA", "PURS"]


# ------------------------------------------------------ критерий каталога

def test_catalog_composition_mode_criterion():
    st2 = _import_stage2()
    full = [{"fns": ["F1"], "active_fns": ["F1"], "seats": ["CA"]},
            {"fns": ["F2"], "active_fns": ["F2"], "seats": ["PURS"]}]
    assert st2.catalog_composition_mode(full) == (True, False)
    none = [{"fns": ["F1"], "active_fns": ["F1"]}]
    assert st2.catalog_composition_mode(none) == (False, False)
    # Смешанный каталог — обнаруживается (ревью 2026-07-24, P2): run_one
    # отвергает его до солва вместо фолбэка '(0, Captain)'.
    mixed = full[:1] + none
    assert st2.catalog_composition_mode(mixed) == (False, True)
    # Записи pure-deadhead (пустой active) в критерии не участвуют.
    with_dh = full + [{"fns": ["F3"], "active_fns": []}]
    assert st2.catalog_composition_mode(with_dh) == (True, False)
    # seats кривого типа (строка) — НЕ состав.
    bad = [{"fns": ["F1"], "active_fns": ["F1"], "seats": "CA"}]
    assert st2.catalog_composition_mode(bad) == (False, False)


# ------------------------------------------------- обрезка расширения

def test_expand_clips_crew_to_leg_demand():
    """Экипаж юнита шире спроса участка: рядов ровно по спросу, idx и
    crewSlotId зеркалят СПРОС; лишние члены экипажа рядов не порождают."""
    st2 = _import_stage2()
    p = _payload(uniform=False)      # F1: PURS+CA, F2: CA+CA
    solved = {
        "flights": [{"flightNumber": "C_000000",
                     "legIds": ["F1", "F2"]}],
        "flightAssignments": [
            {"flight": "C_000000", "indexInFlight": 0,
             "requiredSkill": "PURS", "employee": {"id": "E9"}},
            {"flight": "C_000000", "indexInFlight": 1,
             "requiredSkill": "CA", "employee": {"id": "E1"}},
            {"flight": "C_000000", "indexInFlight": 2,
             "requiredSkill": "CA", "employee": {"id": "E2"}},
        ],
    }
    out = st2.expand_to_flight_level(p, solved)
    rows = {}
    for a in out["flightAssignments"]:
        rows.setdefault(a["flight"], []).append(a)
    f1 = sorted(rows["F1"], key=lambda a: a["indexInFlight"])
    f2 = sorted(rows["F2"], key=lambda a: a["indexInFlight"])
    # F1 требует PURS+CA: PURS=E9, первый CA=E1; E2 сверхштатно (нет ряда).
    assert [(a["requiredSkill"], a["employee"]) for a in f1] == [
        ("PURS", "E9"), ("CA", "E1")]
    assert [a["crewSlotId"] for a in f1] == [
        "F1|2026-08-01T06:00:00#0", "F1|2026-08-01T06:00:00#1"]
    # F2 требует CA+CA: E1 и E2; PURS сверхштатно.
    assert [(a["requiredSkill"], a["employee"]) for a in f2] == [
        ("CA", "E1"), ("CA", "E2")]


def test_expand_unchanged_when_crew_matches_demand():
    """Экипаж == спрос (все прежние режимы): расширение байт-идентично —
    ряд на каждый слот экипажа, idx экипажа."""
    st2 = _import_stage2()
    p = _payload(uniform=True)       # F1 и F2: PURS+CA
    solved = {
        "flights": [{"flightNumber": "C_000000", "legIds": ["F1"]}],
        "flightAssignments": [
            {"flight": "C_000000", "indexInFlight": 0,
             "requiredSkill": "PURS", "employee": {"id": "E9"}},
            {"flight": "C_000000", "indexInFlight": 1,
             "requiredSkill": "CA", "employee": None},
        ],
    }
    out = st2.expand_to_flight_level(p, solved)
    f1 = sorted((a for a in out["flightAssignments"]
                 if a["flight"] == "F1"),
                key=lambda a: a["indexInFlight"])
    assert [(a["requiredSkill"], a["employee"]) for a in f1] == [
        ("PURS", "E9"), ("CA", None)]


# --------------------- Ярус 2в + режим залоченных (2026-07-24) ------------

def test_exact_unit_fields_flow_into_synthetic_flight():
    """Точные поля каталога (кредиты по месяцам, отдых после юнита, окна
    занятости) переезжают на синтетический рейс как есть; без полей —
    прежние вычисленные значения."""
    st2 = _import_stage2()
    p = _payload(uniform=False)
    rec = {"fns": ["F1", "F2"], "active_fns": ["F1", "F2"],
           "seats": ["PURS", "CA"],
           "workMinutesByMonth": {"2026-08": 555},
           "blockMinutesByMonth": {"2026-08": 444},
           "minRestAfterMin": 2160,
           "busyWindows": [[100, 200], [300, 400]]}
    syn = st2.build_synthetic_payload(p, [rec])
    f = syn["flights"][0]
    assert f["workMinutesByMonth"] == {"2026-08": 555}
    assert f["blockMinutesByMonth"] == {"2026-08": 444}
    assert f["minRestAfterMin"] == 2160
    assert f["busyWindows"] == [[100, 200], [300, 400]]
    # без точных полей — вычисленные (налёт = длительности участков)
    syn2 = st2.build_synthetic_payload(
        p, [{"fns": ["F1", "F2"], "active_fns": ["F1", "F2"],
             "seats": ["PURS", "CA"]}])
    f2 = syn2["flights"][0]
    assert f2["blockMinutesByMonth"] == {"2026-08": 240}
    assert "minRestAfterMin" not in f2


def test_locked_employees_pinned_on_compatible_seats():
    """Залоченные сотрудники садятся на первое свободное место своей роли
    и пинятся; несовместимый по роли — честно в счётчик неразмещённых."""
    st2 = _import_stage2()
    p = _payload(uniform=False)
    p["employees"] = [
        {"id": "E1", "homeAirport": "AAA", "skills": ["PURS"],
         "typeRatings": ["A320neo"]},
        {"id": "E2", "homeAirport": "AAA", "skills": ["CA"],
         "typeRatings": ["A320neo"]},
        {"id": "E3", "homeAirport": "AAA", "skills": ["INST"],
         "typeRatings": ["A320neo"]},
    ]
    rec = {"fns": ["F1", "F2"], "active_fns": ["F1", "F2"],
           "seats": ["PURS", "CA"],
           "lockedEmployees": ["E2", "E1", "E3"]}
    syn = st2.build_synthetic_payload(p, [rec])
    by_idx = {a["indexInFlight"]: a for a in syn["flightAssignments"]}
    assert by_idx[0]["employee"] == "E1" and by_idx[0]["pinned"] is True
    assert by_idx[1]["employee"] == "E2" and by_idx[1]["pinned"] is True
    assert st2.build_synthetic_payload._locked_placed == 2
    assert st2.build_synthetic_payload._locked_unplaced == 1


def test_build_locked_units_from_solver_file():
    """Выпуск закреплений: только строки locked с сотрудником, наряды
    отдыха и юниты вне каталога — в счётчики."""
    from _solver_to_payload import build_locked_units
    solver = {
        "duties": [
            {"id": "D1", "ground": False, "rest": False},
            {"id": "D2", "ground": False, "rest": True},
            {"id": "D3", "ground": False, "rest": False},
        ],
        "sequences": [{"id": "SQ_full", "sequenceId": "SQ1"}],
        "employeeAssignments": [
            {"duty": "D1", "sequence": "SQ_full", "employee": "E1",
             "locked": True},
            {"duty": "D2", "employee": "E1", "locked": True},   # отдых
            {"duty": "D3", "employee": "E2", "locked": True},   # вне каталога
            {"duty": "D1", "sequence": "SQ_full", "employee": "E9",
             "locked": False},                                  # не залочен
        ],
    }
    catalog_units = [{"sourceUnit": "sequence:SQ1"}]
    doc = build_locked_units(solver, catalog_units)
    assert doc["units"] == [{"sourceUnit": "sequence:SQ1",
                             "employees": ["E1"]}]
    assert doc["locked_rows"] == 1
    assert doc["skipped_rest_rows"] == 1
    assert doc["skipped_outside_catalog"] == 1
