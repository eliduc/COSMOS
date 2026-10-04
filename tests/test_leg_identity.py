"""C01/C02 phase 1 — flight-leg/crew-slot identity + fail-closed guard."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
import _leg_identity as li  # noqa: E402


def test_leg_id_and_slot_key():
    assert li.leg_id({"flightNumber": "AA1"}) == "AA1"
    assert li.leg_id({"flightLegId": "AA1|d1", "flightNumber": "AA1"}) == "AA1|d1"
    assert li.leg_id({"flight": {"flightNumber": "AA1"}}) == "AA1"   # dict form
    assert li.leg_id({"flight": "AA1"}) == "AA1"                     # string form
    assert li.slot_key({"flight": "AA1"}) == ("AA1", 0)             # default seat
    assert li.slot_key({"flight": "AA1", "indexInFlight": 1}) == ("AA1", 1)


def test_representable_gerad_like():
    # single-crew, unique numbers -> representable (empty reasons)
    p = {"flights": [{"flightNumber": "AA1"}, {"flightNumber": "AA2"}],
         "flightAssignments": [{"flight": "AA1", "indexInFlight": 0,
                                "requiredSkill": "Captain"},
                               {"flight": "AA2", "indexInFlight": 0,
                                "requiredSkill": "Captain"}]}
    assert li.pipeline_representable(p) == []


def test_representable_flags_repeated_number():
    p = {"flights": [{"flightNumber": "AA1"}, {"flightNumber": "AA1"}]}
    reasons = li.pipeline_representable(p)
    assert len(reasons) == 1 and "AA1" in reasons[0]


def test_uniform_multi_seat_is_representable():
    # C01 phase 2: a UNIFORM Captain+FO signature across all legs is the
    # co-located crew model -> representable (empty reasons).
    p = {"flights": [{"flightNumber": "AA1"}, {"flightNumber": "AA2"}],
         "flightAssignments": [
             {"flight": "AA1", "indexInFlight": 0, "requiredSkill": "Captain"},
             {"flight": "AA1", "indexInFlight": 1,
              "requiredSkill": "FirstOfficer"},
             {"flight": "AA2", "indexInFlight": 0, "requiredSkill": "Captain"},
             {"flight": "AA2", "indexInFlight": 1,
              "requiredSkill": "FirstOfficer"}]}
    assert li.pipeline_representable(p) == []
    assert li.seat_signature(p) == ("Captain", "FirstOfficer")
    assert li.crew_roles(p) == [(0, "Captain"), (1, "FirstOfficer")]


def test_non_uniform_seats_rejected():
    # Different legs demanding different crew complements the co-located
    # pairing model still cannot represent -> fail-closed.
    p = {"flights": [{"flightNumber": "AA1"}, {"flightNumber": "AA2"}],
         "flightAssignments": [
             {"flight": "AA1", "indexInFlight": 0, "requiredSkill": "Captain"},
             {"flight": "AA1", "indexInFlight": 1,
              "requiredSkill": "FirstOfficer"},
             {"flight": "AA2", "indexInFlight": 0, "requiredSkill": "Captain"}]}
    reasons = li.pipeline_representable(p)
    assert len(reasons) == 1 and "AA2" in reasons[0]
    assert li.seat_signature(p) is None
    assert li.crew_roles(p) is None


def test_gerad_single_seat_roles():
    # GERAD single-Captain -> one slot, isomorphic to today's pipeline.
    p = {"flights": [{"flightNumber": "AA1"}],
         "flightAssignments": [
             {"flight": "AA1", "indexInFlight": 0, "requiredSkill": "Captain"}]}
    assert li.seat_signature(p) == ("Captain",)
    assert li.crew_roles(p) == [(0, "Captain")]
    assert li.pipeline_representable(p) == []


def test_repeated_fn_with_full_materialization_is_representable():
    # Пакет B (аудит-2): повторный flightNumber с ПОЛНОЙ материализацией
    # flightLegId решаем через биекцию wire-имён (_leg_rekey) — отказ снят.
    p = {"flights": [
        {"flightNumber": "AA1", "flightLegId": "AA1|d1"},
        {"flightNumber": "AA1", "flightLegId": "AA1|d2"}],
        "flightAssignments": [
            {"id": "s1", "flight": "AA1", "flightLegId": "AA1|d1",
             "indexInFlight": 0, "requiredSkill": "Captain"},
            {"id": "s2", "flight": "AA1", "flightLegId": "AA1|d2",
             "indexInFlight": 0, "requiredSkill": "Captain"}]}
    assert li.pipeline_representable(p) == []


def test_repeated_fn_without_slot_leg_ids_still_rejected():
    # ...но слот повторного номера без flightLegId — блокер биекции:
    # ссылку `flight: fn` не разрешить в конкретное вхождение.
    p = {"flights": [
        {"flightNumber": "AA1", "flightLegId": "AA1|d1"},
        {"flightNumber": "AA1", "flightLegId": "AA1|d2"}],
        "flightAssignments": [
            {"id": "s1", "flight": "AA1", "indexInFlight": 0,
             "requiredSkill": "Captain"}]}
    reasons = li.pipeline_representable(p)
    assert reasons and any("flightLegId" in r for r in reasons)


def test_repeated_fn_without_any_leg_ids_still_rejected():
    # Без материализации идентичности повторный номер по-прежнему отказ.
    p = {"flights": [
        {"flightNumber": "AA1"},
        {"flightNumber": "AA1"}]}
    reasons = li.pipeline_representable(p)
    assert reasons                      # C02: повторная идентичность рейса


def test_unique_wire_numbers_with_leg_ids_representable():
    # The BTS convention: unique wire numbers carrying leg-id data — solvable.
    p = {"flights": [
        {"flightNumber": "AA1-0800", "flightLegId": "AA1|d1"},
        {"flightNumber": "AA1-0900", "flightLegId": "AA1|d2"}],
        "flightAssignments": [
            {"flight": "AA1-0800", "indexInFlight": 0,
             "requiredSkill": "Captain"},
            {"flight": "AA1-0900", "indexInFlight": 0,
             "requiredSkill": "Captain"}]}
    assert li.pipeline_representable(p) == []


def test_assert_raises_and_kill_switch(monkeypatch):
    rep = {"flights": [{"flightNumber": "AA1"}, {"flightNumber": "AA1"}]}
    monkeypatch.setenv("CREW_GERAD_STRICT_IDENTITY", "1")
    with pytest.raises(li.GeradPipelineUnsupported):
        li.assert_representable(rep, "t")
    monkeypatch.setenv("CREW_GERAD_STRICT_IDENTITY", "0")   # kill-switch
    li.assert_representable(rep, "t")                       # no raise
