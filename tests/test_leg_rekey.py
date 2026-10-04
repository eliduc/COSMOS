"""Re-key повторных flightNumber (Пакет B аудита-2): детерминированная
биекция wire-имён, идемпотентность, блокеры, roundtrip восстановления."""
import copy
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import _leg_rekey as rk                     # noqa: E402


def _dup_payload():
    return {
        "airports": [{"code": "AAA"}, {"code": "BBB"}],
        "employees": [
            {"id": "E1", "homeAirport": "AAA", "skills": ["Captain"],
             "preferredFlights": ["F100", "F200"]},
        ],
        "flights": [
            {"flightNumber": "F100", "flightLegId": "F100|d1",
             "departureAirport": "AAA", "arrivalAirport": "BBB",
             "departureUTCDateTime": "2026-03-01T08:00:00",
             "arrivalUTCDateTime": "2026-03-01T10:00:00"},
            {"flightNumber": "F100", "flightLegId": "F100|d2",
             "departureAirport": "BBB", "arrivalAirport": "AAA",
             "departureUTCDateTime": "2026-03-02T08:00:00",
             "arrivalUTCDateTime": "2026-03-02T10:00:00"},
            {"flightNumber": "F200", "flightLegId": "F200|d1",
             "departureAirport": "AAA", "arrivalAirport": "BBB",
             "departureUTCDateTime": "2026-03-01T12:00:00",
             "arrivalUTCDateTime": "2026-03-01T14:00:00"},
        ],
        "flightAssignments": [
            {"id": "s1", "flight": "F100", "flightLegId": "F100|d1",
             "indexInFlight": 0, "requiredSkill": "Captain"},
            {"id": "s2", "flight": "F100", "flightLegId": "F100|d2",
             "indexInFlight": 0, "requiredSkill": "Captain"},
            {"id": "s3", "flight": "F200", "flightLegId": "F200|d1",
             "indexInFlight": 0, "requiredSkill": "Captain"},
        ],
    }


def test_identity_path_returns_same_object_and_empty_map():
    p = {"flights": [{"flightNumber": "A"}, {"flightNumber": "B"}]}
    out, wm = rk.rekey_payload(p)
    assert out is p and wm == {}
    assert rk.needs_rekey(p) is False


def test_rekey_renames_all_occurrences_and_is_deterministic():
    p = _dup_payload()
    out, wm = rk.rekey_payload(p)
    fns = [f["flightNumber"] for f in out["flights"]]
    # все вхождения группы переименованы, голого F100 не осталось
    assert "F100" not in fns
    assert "F100@1" in fns and "F100@2" in fns and "F200" in fns
    # порядок — по flightLegId (d1 < d2)
    assert wm["F100@1"]["flightLegId"] == "F100|d1"
    assert wm["F100@2"]["flightLegId"] == "F100|d2"
    # слоты разрешены через их flightLegId
    slot_by_id = {s["id"]: s for s in out["flightAssignments"]}
    assert slot_by_id["s1"]["flight"] == "F100@1"
    assert slot_by_id["s2"]["flight"] == "F100@2"
    assert slot_by_id["s3"]["flight"] == "F200"
    # префы развёрнуты во все wire-имена группы
    assert out["employees"][0]["preferredFlights"] == \
        ["F100@1", "F100@2", "F200"]
    # исходный объект не мутирован
    assert p["flights"][0]["flightNumber"] == "F100"
    # детерминизм
    out2, wm2 = rk.rekey_payload(_dup_payload())
    assert wm2 == wm
    # идемпотентность: у пере-ключенного номера уникальны -> identity
    out3, wm3 = rk.rekey_payload(out)
    assert out3 is out and wm3 == {}


def test_blockers_slot_without_leg_id_and_at_in_number():
    p = _dup_payload()
    del p["flightAssignments"][0]["flightLegId"]
    assert any("flightLegId" in r for r in rk.rekey_blockers(p))
    p2 = _dup_payload()
    for f in p2["flights"][:2]:
        f["flightNumber"] = "F@X"
    for s in p2["flightAssignments"][:2]:
        s["flight"] = "F@X"
    assert any("@" in r for r in rk.rekey_blockers(p2))
    with pytest.raises(ValueError):
        rk.rekey_payload(p)


def test_kill_switch_disables_rekey(monkeypatch):
    monkeypatch.setenv("CREW_GERAD_REKEY", "0")
    p = _dup_payload()
    assert any("CREW_GERAD_REKEY" in r for r in rk.rekey_blockers(p))


def test_restore_solution_roundtrip():
    p = _dup_payload()
    wirep, wm = rk.rekey_payload(p)
    solution = {
        "flights": copy.deepcopy(wirep["flights"]),
        "flightAssignments": [
            {"id": "s1", "flight": "F100@1", "flightLegId": "F100|d1",
             "indexInFlight": 0, "requiredSkill": "Captain",
             "employee": "E1"},
            {"id": "s2", "flight": "F100@2", "flightLegId": "F100|d2",
             "indexInFlight": 0, "requiredSkill": "Captain",
             "employee": "E1"},
        ],
    }
    pub = rk.restore_solution(solution, wm)
    fns = [f["flightNumber"] for f in pub["flights"]]
    assert fns.count("F100") == 2 and "F100@1" not in fns
    assert {a["flight"] for a in pub["flightAssignments"]} == {"F100"}
    assert {a["flightLegId"] for a in pub["flightAssignments"]} == \
        {"F100|d1", "F100|d2"}


def test_coverage_join_after_restore_is_exact():
    # Восстановленное решение против ИСХОДНОГО payload'а: покрытие точное
    # (джойн по slot_key/leg_id), оба вхождения различимы.
    from _coverage_validator import check
    p = _dup_payload()
    wirep, wm = rk.rekey_payload(p)
    solution = {
        "flights": copy.deepcopy(wirep["flights"]),
        "flightAssignments": [
            {"id": "s1", "flight": "F100@1", "flightLegId": "F100|d1",
             "indexInFlight": 0, "requiredSkill": "Captain",
             "employee": "E1"},
            {"id": "s2", "flight": "F100@2", "flightLegId": "F100|d2",
             "indexInFlight": 0, "requiredSkill": "Captain",
             "employee": "E1"},
            {"id": "s3", "flight": "F200", "flightLegId": "F200|d1",
             "indexInFlight": 0, "requiredSkill": "Captain",
             "employee": "E1"},
        ],
    }
    pub = rk.restore_solution(solution, wm)
    cov = check(p, pub)
    assert cov["fully_covered"] is True
    assert cov["missing"] == 0 and cov["skill_mismatches"] == 0


def test_wire_to_leg_ids_translation():
    p = _dup_payload()
    _, wm = rk.rekey_payload(p)
    assert rk.wire_to_leg_ids(["F100@2", "F200"], wm) == \
        {"F100|d2", "F200"}
    assert rk.wire_to_leg_ids(["X"], {}) == {"X"}


def test_colgen_load_legs_two_distinct_legs(tmp_path):
    # load_legs над payload с двумя F100 -> 2 Leg с уникальными wire-fn.
    from _gerad_colgen import load_legs
    import json
    p = _dup_payload()
    src = tmp_path / "p.json"
    src.write_text(json.dumps(p), encoding="utf-8")
    legs, by_fn, wm = load_legs(0, str(src))
    assert len(legs) == 3
    assert len({leg.fn for leg in legs}) == 3
    assert set(wm) == {"F100@1", "F100@2"}
    assert len(by_fn) == 3


def test_joint_payload_over_rekeyed_views():
    # Трек B (joint-re-key): конкатенация строится над re-key-нутыми
    # представлениями — каталоги lane'ов в wire-пространстве сходятся
    # с синтетикой, MRJ-префикс ложится ПОВЕРХ wire-имени.
    from _gerad_multirole import build_joint_payload
    p = _dup_payload()
    # представление одной роли = сам payload (Captain-only здесь)
    views = {"Captain": p}
    selected = {"Captain": [
        {"fns": ["F100@1", "F200"], "active_fns": ["F100@1", "F200"],
         "cost": 100},
        {"fns": ["F100@2"], "active_fns": ["F100@2"], "cost": 50}]}
    joint = build_joint_payload(p, views, selected, ["Captain"])
    fns = {f["flightNumber"] for f in joint["flights"]}
    # пары каталога стали synthetic-рейсами с MRJ-префиксом
    assert any(fn.startswith("MRJ") for fn in fns)
    # ни одно исходное дублированное имя не протекло голым
    assert "F100" not in fns
