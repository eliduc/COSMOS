"""Interactive roster board (#4): board build from the stage-2 artifact,
manual move/pin, inline legality validation (overlap, type rating,
unavailability, coverage, rest), reset, tenancy."""
import importlib

import pytest
from fastapi.testclient import TestClient

from webapp import registry, roster_board as rb
from _stand_identity import ADMIN_EMAIL

EMAIL = ADMIN_EMAIL


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "EDITS_PATH", tmp_path / "edits.json")
    yield tmp_path


def _base():
    return registry.get_instance("gerad-1")   # has a valid stage-2 roster


def test_board_builds_from_stage2(iso):
    b = rb.board("gerad-1", _base())
    assert b["available"] is True
    assert len(b["pairings"]) > 0
    assert len(b["crew"]) > 0
    # the pristine solver roster is legal (status OK) -> no violations
    assert b["violation_count"] == 0
    p0 = b["pairings"][0]
    assert p0["employee"] == p0["solver_employee"]
    assert p0["locked"] is False and p0["moved"] is False


def test_board_none_when_no_roster(iso):
    # inst4's stage-2 is INFEASIBLE but the artifact still has pairings;
    # use a fake instance with no artifact via label swap
    inst = dict(_base())
    inst["label"] = "no-such-label-xyz"
    b = rb.board("x", inst)
    assert b["available"] is False


def test_move_to_unqualified_crew_flags_type_rating(iso):
    inst = _base()
    b = rb.board("gerad-1", inst)
    pid = b["pairings"][0]["pid"]
    ac = b["pairings"][0]["aircraft"]
    # find a crew member WITHOUT the pairing's aircraft type rating
    victim = None
    for c in b["crew"]:
        if ac and not set(ac).issubset(set(c["type_ratings"])):
            victim = c["id"]
            break
    if victim is None:
        pytest.skip("all crew share the type rating in this seed")
    nb = rb.set_move("gerad-1", inst, pid, victim, EMAIL)
    moved = next(p for p in nb["pairings"] if p["pid"] == pid)
    assert moved["employee"] == victim and moved["moved"] is True
    assert "type_rating" in moved["violations"]
    assert any(v["type"] == "type_rating" and v["pid"] == pid
               for v in nb["violations"])


def test_validate_unit_all_checks():
    """Direct check of every legality rule on synthetic data (the seed's
    uniform type ratings can't exercise type_rating via real crew)."""
    crew = {
        "A": {"id": "A", "type_ratings": ["727"], "unavailable": set()},
        "B": {"id": "B", "type_ratings": ["320"],   # lacks 727
              "unavailable": {"2000-01-10"}},
    }
    pairings = [
        {"pid": "P1", "employee": "A", "aircraft": ["727"],
         "days": ["2000-01-01"], "dep_utc": "2000-01-01T06:00:00",
         "arr_utc": "2000-01-01T10:00:00", "violations": []},
        {"pid": "P2", "employee": "B", "aircraft": ["727"],   # B lacks 727
         "days": ["2000-01-10"], "dep_utc": "2000-01-10T06:00:00",
         "arr_utc": "2000-01-10T10:00:00", "violations": []},
        {"pid": "P3", "employee": None, "aircraft": ["727"],  # uncovered
         "days": ["2000-01-02"], "dep_utc": "2000-01-02T06:00:00",
         "arr_utc": "2000-01-02T10:00:00", "violations": []},
        {"pid": "P4", "employee": "A", "aircraft": ["727"],   # overlaps P1
         "days": ["2000-01-01"], "dep_utc": "2000-01-01T09:00:00",
         "arr_utc": "2000-01-01T12:00:00", "violations": []},
    ]
    viols = rb._validate(pairings, crew)
    types = {v["type"] for v in viols}
    assert "type_rating" in types      # B on a 727 pairing
    assert "unavailable" in types      # B unavailable 2000-01-10
    assert "uncovered" in types        # P3 has no crew
    assert "overlap" in types          # P1 and P4 overlap for A


def test_unassign_flags_uncovered(iso):
    inst = _base()
    b = rb.board("gerad-1", inst)
    pid = b["pairings"][0]["pid"]
    nb = rb.set_move("gerad-1", inst, pid, None, EMAIL)
    moved = next(p for p in nb["pairings"] if p["pid"] == pid)
    assert moved["employee"] is None
    assert "uncovered" in moved["violations"]


def test_move_back_to_solver_clears_edit(iso):
    inst = _base()
    b = rb.board("gerad-1", inst)
    p = b["pairings"][0]
    orig = p["solver_employee"]
    rb.set_move("gerad-1", inst, p["pid"], None, EMAIL)          # unassign
    assert rb.board("gerad-1", inst)["moves"] == 1
    nb = rb.set_move("gerad-1", inst, p["pid"], orig, EMAIL)     # restore
    assert nb["moves"] == 0                                       # edit cleared
    restored = next(x for x in nb["pairings"] if x["pid"] == p["pid"])
    assert restored["moved"] is False


def test_moving_two_pairings_onto_one_crew_flags_overlap(iso):
    inst = _base()
    b = rb.board("gerad-1", inst)
    # take the first crew member and pile TWO other crews' pairings onto them
    target = b["pairings"][0]["employee"]
    others = [p for p in b["pairings"] if p["employee"] != target][:2]
    if len(others) < 2:
        pytest.skip("not enough pairings")
    rb.set_move("gerad-1", inst, others[0]["pid"], target, EMAIL)
    nb = rb.set_move("gerad-1", inst, others[1]["pid"], target, EMAIL)
    # target now holds >= 3 pairings; at least an overlap or rest violation
    types = {v["type"] for v in nb["violations"]}
    assert types & {"overlap", "rest"}


def test_pin_protects_from_move(iso):
    inst = _base()
    b = rb.board("gerad-1", inst)
    pid = b["pairings"][0]["pid"]
    nb = rb.set_pin("gerad-1", inst, pid, True, EMAIL)
    pinned = next(p for p in nb["pairings"] if p["pid"] == pid)
    assert pinned["locked"] is True and nb["pins"] == 1
    with pytest.raises(rb.RosterBoardError):
        rb.set_move("gerad-1", inst, pid, None, EMAIL)
    # unpin then move works
    rb.set_pin("gerad-1", inst, pid, False, EMAIL)
    rb.set_move("gerad-1", inst, pid, None, EMAIL)


def test_move_unknown_pid_or_crew_rejected(iso):
    inst = _base()
    with pytest.raises(rb.RosterBoardError):
        rb.set_move("gerad-1", inst, "NOPE", None, EMAIL)
    pid = rb.board("gerad-1", inst)["pairings"][0]["pid"]
    with pytest.raises(rb.RosterBoardError):
        rb.set_move("gerad-1", inst, pid, "NO_SUCH_CREW", EMAIL)


def test_validate_flags_home_base_mismatch():
    """audit 2026-07-15: a base-anchored pairing on a crew of a different
    base is a HARD home-base violation the board must flag."""
    crew = {
        "A": {"id": "A", "base": "BASE1", "type_ratings": ["727"],
              "unavailable": set()},
        "B": {"id": "B", "base": "BASE2", "type_ratings": ["727"],
              "unavailable": set()},
    }
    pairings = [
        {"pid": "P1", "employee": "A", "base": "BASE1", "aircraft": ["727"],
         "days": ["2000-01-01"], "dep_utc": "2000-01-01T06:00:00",
         "arr_utc": "2000-01-01T10:00:00", "violations": []},
        {"pid": "P2", "employee": "B", "base": "BASE1", "aircraft": ["727"],
         "days": ["2000-01-02"], "dep_utc": "2000-01-02T06:00:00",
         "arr_utc": "2000-01-02T10:00:00", "violations": []},   # B is BASE2
    ]
    viols = rb._validate(pairings, crew)
    types = {v["type"] for v in viols}
    assert "home_base" in types
    p2 = next(p for p in pairings if p["pid"] == "P2")
    assert "home_base" in p2["violations"]


def test_board_truncation_is_self_consistent(iso, monkeypatch):
    """audit 2026-07-15: with tiny caps, every shown pairing's employee has a
    crew row, and every violation references a shown pid — no desync."""
    monkeypatch.setattr(rb, "MAX_CREW", 5)
    b = rb.board("gerad-1", _base())
    shown_pids = {p["pid"] for p in b["pairings"]}
    crew_ids = {c["id"] for c in b["crew"]}
    assert len(b["crew"]) <= 5
    assert b["crew_truncated"] is True
    # every shown pairing's employee is a visible crew row (or unassigned)
    for p in b["pairings"]:
        assert p["employee"] is None or p["employee"] in crew_ids
    # every violation references a shown pairing
    for v in b["violations"]:
        assert v["pid"] in shown_pids


def test_orphan_edits_are_pruned(iso):
    inst = _base()
    # inject ghost edits directly into the store
    rb._store("gerad-1", {"moves": {"C_GHOST": "EMP001"},
                          "pins": ["C_GHOST2"], "owner": EMAIL})
    b = rb.board("gerad-1", inst)
    assert b["moves"] == 0 and b["pins"] == 0     # ghosts pruned
    assert b["edited"] is False


def test_owner_recorded_on_first_edit(iso):
    inst = _base()
    pid = rb.board("gerad-1", inst)["pairings"][0]["pid"]
    rb.set_pin("gerad-1", inst, pid, True, EMAIL)
    assert rb.edits_for("gerad-1").get("owner") == EMAIL
    # a different non-admin user cannot reset it
    assert rb.reset("gerad-1", "someone@else.com", is_admin=False) is False
    assert rb.reset("gerad-1", EMAIL, is_admin=False) is True


def test_reset_restores_solver_roster(iso):
    inst = _base()
    pid = rb.board("gerad-1", inst)["pairings"][0]["pid"]
    rb.set_move("gerad-1", inst, pid, None, EMAIL)
    rb.set_pin("gerad-1", inst, pid, True, EMAIL)
    assert rb.reset("gerad-1", EMAIL, is_admin=False) is True
    b = rb.board("gerad-1", inst)
    assert b["moves"] == 0 and b["pins"] == 0 and b["violation_count"] == 0


@pytest.fixture()
def client(iso, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", EMAIL)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", iso / "users.db")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_api_lifecycle(client):
    b = client.get("/api/board/gerad-1")
    assert b.status_code == 200
    pid = b.json()["pairings"][0]["pid"]
    mv = client.post("/api/board/gerad-1/move",
                     json={"pid": pid, "employee": None})
    assert mv.status_code == 200
    assert any(p["pid"] == pid and p["employee"] is None
               for p in mv.json()["pairings"])
    pin = client.post("/api/board/gerad-1/pin",
                      json={"pid": pid, "locked": True})
    assert pin.status_code == 200
    assert client.delete("/api/board/gerad-1").status_code == 200


def test_api_unknown_instance_404(client):
    assert client.get("/api/board/upload-nope").status_code == 404


# --- P0-4 (аудит документации 2026-07-20): посадочная модель ----------------


def _seat_env(tmp_path, monkeypatch, crew_list, employees=None):
    """Изолированный инстанс с multi-crew артефактом: один пейринг,
    crew[] по местам."""
    import json as _json
    from webapp import active_roster, registry as _reg
    payload = {
        "airports": [{"code": "AAA"}, {"code": "BBB"}],
        "employees": employees or [
            {"id": "CP1", "homeAirport": "AAA", "skills": ["Captain"],
             "typeRatings": ["A320"]},
            {"id": "FO1", "homeAirport": "AAA", "skills": ["FirstOfficer"],
             "typeRatings": ["A320"]},
            {"id": "FO2", "homeAirport": "AAA", "skills": ["FirstOfficer"],
             "typeRatings": ["A320"]},
        ],
        "flights": [
            {"flightNumber": "S1", "departureAirport": "AAA",
             "arrivalAirport": "BBB",
             "departureUTCDateTime": "2026-03-01T08:00:00",
             "arrivalUTCDateTime": "2026-03-01T09:00:00",
             "aircraftType": "A320"},
        ],
        "flightAssignments": [],
    }
    doc = {"result_status": "OK",
           "pairings": [{"pid": "P0", "employee": crew_list[0]["employee"],
                         "crew": crew_list,
                         "fns": ["S1"], "active_fns": ["S1"]}]}
    ppath = tmp_path / "payload.json"
    ppath.write_text(_json.dumps(payload), encoding="utf-8")
    inst = {"id": "seatinst", "label": "seatinst", "source": "upload"}
    monkeypatch.setattr(_reg, "instance_payload_path", lambda i: ppath)
    monkeypatch.setattr(active_roster, "resolve",
                        lambda i: {"status": "legacy", "assignment": doc,
                                   "meta": {}, "warnings": [],
                                   "mismatches": []})
    return inst


def test_board_shows_all_seats_and_members(iso, tmp_path, monkeypatch):
    inst = _seat_env(tmp_path, monkeypatch, [
        {"indexInFlight": 0, "requiredSkill": "Captain", "employee": "CP1"},
        {"indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": "FO1"},
    ])
    b = rb.board("seatinst", inst)
    assert b["available"] is True
    p = b["pairings"][0]
    assert [s["employee"] for s in p["seats"]] == ["CP1", "FO1"]
    assert [s["requiredSkill"] for s in p["seats"]] == \
        ["Captain", "FirstOfficer"]
    # оба члена — держатели строк доски
    ids = {c["id"] for c in b["crew"]}
    assert {"CP1", "FO1"} <= ids
    assert b["violation_count"] == 0
    # crew_all несёт навыки для фильтра кандидатов
    assert any(c.get("skills") == ["Captain"] for c in b["crew_all"])


def test_board_flags_skill_violation_per_seat(iso, tmp_path, monkeypatch):
    # FO2 на капитанском месте: нарушение 'skill' с указанием места
    inst = _seat_env(tmp_path, monkeypatch, [
        {"indexInFlight": 0, "requiredSkill": "Captain", "employee": "FO2"},
        {"indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": "FO1"},
    ])
    b = rb.board("seatinst", inst)
    kinds = {(v["type"], v.get("slot")) for v in b["violations"]}
    assert ("skill", 0) in kinds
    assert not any(v[0] == "skill" and v[1] == 1 for v in kinds)


def test_board_flags_dup_crew_and_empty_seat(iso, tmp_path, monkeypatch):
    inst = _seat_env(tmp_path, monkeypatch, [
        {"indexInFlight": 0, "requiredSkill": "Captain", "employee": "CP1"},
        {"indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": "CP1"},
        {"indexInFlight": 2, "requiredSkill": "FirstOfficer",
         "employee": None},
    ])
    b = rb.board("seatinst", inst)
    kinds = {v["type"] for v in b["violations"]}
    assert "dup_crew" in kinds        # CP1 на двух местах
    assert "uncovered" in kinds       # пустое третье место
    # dup_crew: CP1 без роли FO — и 'skill' на втором месте тоже
    assert "skill" in kinds


def test_second_seat_member_sees_pairing_in_cabinet(iso, tmp_path,
                                                    monkeypatch):
    from webapp import crew_portal
    inst = _seat_env(tmp_path, monkeypatch, [
        {"indexInFlight": 0, "requiredSkill": "Captain", "employee": "CP1"},
        {"indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": "FO1"},
    ])
    mine, ok = crew_portal._my_pairings("seatinst", inst, "FO1")
    assert ok is True
    assert [p["pid"] for p in mine] == ["P0"]
    # первый член тоже видит
    mine2, _ = crew_portal._my_pairings("seatinst", inst, "CP1")
    assert [p["pid"] for p in mine2] == ["P0"]


def test_fair_share_move_legal_checks_required_skill():
    from webapp import fair_share as fs
    crew_by_id = {"FO2": {"id": "FO2", "base": "AAA",
                          "skills": ["FirstOfficer"],
                          "type_ratings": ["A320"],
                          "unavailable": set()}}
    p = {"pid": "P0", "aircraft": ["A320"], "base": "AAA",
         "days": ["2026-03-01"],
         "dep_utc": "2026-03-01T08:00:00", "arr_utc": "2026-03-01T09:00:00",
         "seats": [{"slot": 0, "requiredSkill": "Captain",
                    "employee": None, "solver_employee": None}]}
    assert fs._move_legal(p, "FO2", crew_by_id, {}, 600) is False
    # без записанного требования роли (legacy) — проверка пропускается
    p2 = dict(p)
    p2["seats"] = [{"slot": 0, "requiredSkill": None,
                    "employee": None, "solver_employee": None}]
    assert fs._move_legal(p2, "FO2", crew_by_id, {}, 600) is True


def test_set_move_seat_slot_roundtrip(iso, tmp_path, monkeypatch):
    # P0-4 (ревью): перенос конкретного места по slot; плоский ключ =
    # первое место; ключ 'pid#slot' переживает _reconcile
    inst = _seat_env(tmp_path, monkeypatch, [
        {"indexInFlight": 0, "requiredSkill": "Captain", "employee": "CP1"},
        {"indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": "FO1"},
    ])
    b = rb.set_move("seatinst", inst, "P0", "FO2", slot=1)
    p = b["pairings"][0]
    assert [s["employee"] for s in p["seats"]] == ["CP1", "FO2"]
    assert p["employee"] == "CP1"                 # первое место не тронуто
    import json as _json
    raw = _json.loads(rb.EDITS_PATH.read_text(encoding="utf-8"))
    assert raw["seatinst"]["moves"] == {"P0#1": "FO2"}
    # _reconcile не вычищает живой seat-ключ
    ed = rb._reconcile("seatinst", {"P0"})
    assert ed["moves"] == {"P0#1": "FO2"}
    # возврат к солверному сотруднику места снимает правку
    b2 = rb.set_move("seatinst", inst, "P0", "FO1", slot=1)
    raw2 = _json.loads(rb.EDITS_PATH.read_text(encoding="utf-8")) \
        if rb.EDITS_PATH.exists() else {}
    assert not (raw2.get("seatinst") or {}).get("moves")
    assert [s["employee"] for s in b2["pairings"][0]["seats"]] == \
        ["CP1", "FO1"]


def test_set_move_unknown_slot_refuses(iso, tmp_path, monkeypatch):
    inst = _seat_env(tmp_path, monkeypatch, [
        {"indexInFlight": 0, "requiredSkill": "Captain", "employee": "CP1"},
    ])
    with pytest.raises(rb.RosterBoardError, match="unknown seat"):
        rb.set_move("seatinst", inst, "P0", "FO1", slot=7)


def test_second_seat_move_visible_in_cabinet(iso, tmp_path, monkeypatch):
    from webapp import crew_portal
    inst = _seat_env(tmp_path, monkeypatch, [
        {"indexInFlight": 0, "requiredSkill": "Captain", "employee": "CP1"},
        {"indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": "FO1"},
    ])
    rb.set_move("seatinst", inst, "P0", "FO2", slot=1)
    mine, _ = crew_portal._my_pairings("seatinst", inst, "FO2")
    assert [p["pid"] for p in mine] == ["P0"]
    gone, _ = crew_portal._my_pairings("seatinst", inst, "FO1")
    assert gone == []


def test_fair_share_sees_second_seat_occupancy(iso, tmp_path, monkeypatch):
    # P0-4 (ревью HIGH): занятость второго места видна таймлайну fair-share
    from webapp import fair_share as fs
    inst = _seat_env(tmp_path, monkeypatch, [
        {"indexInFlight": 0, "requiredSkill": "Captain", "employee": "CP1"},
        {"indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": "FO1"},
    ])
    state = fs._effective_state("seatinst", inst)
    assert state is not None
    pairings, crew_by_id, _cfg = state
    p = pairings[0]
    assert [s["employee"] for s in p["seats"]] == ["CP1", "FO1"]
    credit = fs._credits_by_crew(pairings, crew_by_id)
    # кредит начислен обоим членам (не только первому месту)
    assert credit["CP1"] > 0 and credit["FO1"] == credit["CP1"]
    # занятому на втором месте FO1 нельзя отдать пересекающийся пейринг
    by_emp = {}
    for pp in pairings:
        for emp in fs._seat_holders(pp):
            by_emp.setdefault(emp, []).append(pp)
    clash = {"pid": "PX", "aircraft": ["A320"], "base": "AAA",
             "days": ["2026-03-01"],
             "dep_utc": "2026-03-01T08:30:00",
             "arr_utc": "2026-03-01T09:30:00",
             "seats": [{"slot": 0, "requiredSkill": "FirstOfficer",
                        "employee": None, "solver_employee": None}]}
    assert fs._move_legal(clash, "FO1", crew_by_id, by_emp, 600) is False


def test_phase_a_repairs_illness_on_second_seat(iso, tmp_path, monkeypatch):
    # Хвост аудита (2026-07-20): фаза A видит болезнь исполнителя ВТОРОГО
    # места и чинит именно его, не трогая первое.
    from webapp import disruption as dz
    monkeypatch.setattr(dz, "DISRUPTIONS_PATH", tmp_path / "dz.json")
    inst = _seat_env(tmp_path, monkeypatch, [
        {"indexInFlight": 0, "requiredSkill": "Captain", "employee": "CP1"},
        {"indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": "FO1"},
    ])
    # FO1 болен в день пейринга (2026-03-01); FO2 свободен и с ролью FO
    dz.add_sick("seatinst", inst, "FO1", "2026-03-01", "2026-03-01")
    plan = dz.build_repair_plan("seatinst", inst)
    assert plan["available"] is True
    assert plan["broken_count"] == 1
    seq = plan["apply_sequence"]
    assert len(seq) == 1
    assert seq[0]["pid"] == "P0" and seq[0]["slot"] == 1     # второе место
    assert seq[0]["to"] == "FO2"
    assert plan["violations_after"] < plan["violations_before"]
    # применяем и убеждаемся: первое место цело, второе перекрыто
    dz.apply_repair("seatinst", inst)
    b = rb.board("seatinst", inst)
    seats = b["pairings"][0]["seats"]
    assert [s["employee"] for s in seats] == ["CP1", "FO2"]
    assert not any(v["type"] in ("uncovered", "unavailable")
                   for v in b["violations"])


def test_phase_a_second_seat_impact_marks_pairing_broken(iso, tmp_path,
                                                         monkeypatch):
    from webapp import disruption as dz
    monkeypatch.setattr(dz, "DISRUPTIONS_PATH", tmp_path / "dz.json")
    inst = _seat_env(tmp_path, monkeypatch, [
        {"indexInFlight": 0, "requiredSkill": "Captain", "employee": "CP1"},
        {"indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": "FO1"},
    ])
    dz.add_sick("seatinst", inst, "FO1", "2026-03-01", "2026-03-01")
    imp = dz.impact("seatinst", inst)
    assert [b["pid"] for b in imp["broken"]] == ["P0"]


# --- Контур «Отклик планировщика»: что и когда попадает в журнал -------------


@pytest.fixture()
def feedback(iso, tmp_path, monkeypatch):
    """Журнал откликов в tmp: рабочий webapp/config трогать нельзя."""
    from webapp import planner_feedback as pf
    monkeypatch.setattr(pf, "OVERRIDE_PATH", tmp_path / "override_log.json")
    return pf


def _two_seats(tmp_path, monkeypatch):
    return _seat_env(tmp_path, monkeypatch, [
        {"indexInFlight": 0, "requiredSkill": "Captain", "employee": "CP1"},
        {"indexInFlight": 1, "requiredSkill": "FirstOfficer",
         "employee": "FO1"},
    ])


def test_no_op_move_writes_no_episode(feedback, tmp_path, monkeypatch):
    """Ревью волны 1 (MEDIUM): повторное «Применить» с ТЕМ ЖЕ сотрудником
    ничего на доске не меняет, а эпизод писался — размер кластера накручивался
    вхолостую, и ложный CR рождался из одного щелчка, повторённого N раз."""
    inst = _two_seats(tmp_path, monkeypatch)
    kw = dict(slot=1, reason_code="missing_rule", reason_text="так надо")
    rb.set_move("seatinst", inst, "P0", "FO2", EMAIL, **kw)
    assert len(feedback.episodes("seatinst")) == 1

    rb.set_move("seatinst", inst, "P0", "FO2", EMAIL, **kw)   # холостое
    rb.set_move("seatinst", inst, "P0", "FO2", EMAIL, **kw)   # и ещё раз
    assert len(feedback.episodes("seatinst")) == 1

    # настоящее изменение по-прежнему пишется
    rb.set_move("seatinst", inst, "P0", "FO1", EMAIL, **kw)   # назад к солверу
    eps = feedback.episodes("seatinst")
    assert len(eps) == 2
    assert eps[-1]["from"] == "FO2" and eps[-1]["to"] == "FO1"

    # ...а повтор возврата к солверному сотруднику — снова холостой
    rb.set_move("seatinst", inst, "P0", "FO1", EMAIL, **kw)
    assert len(feedback.episodes("seatinst")) == 2


def test_repeated_unassign_writes_one_episode(feedback, tmp_path,
                                              monkeypatch):
    inst = _two_seats(tmp_path, monkeypatch)
    kw = dict(reason_code="ops_decision", reason_text="снять")
    rb.set_move("seatinst", inst, "P0", None, EMAIL, **kw)
    rb.set_move("seatinst", inst, "P0", None, EMAIL, **kw)
    eps = feedback.episodes("seatinst")
    assert len(eps) == 1 and eps[0]["from"] == "CP1" and eps[0]["to"] is None


def test_move_without_reason_writes_no_episode(feedback, tmp_path,
                                               monkeypatch):
    """Инвариант волны 1: причина НЕОБЯЗАТЕЛЬНА, и без неё эпизода нет —
    правка без причины как сигнал бесполезна. Без этого теста мутант,
    пишущий эпизод всегда, выживает."""
    inst = _two_seats(tmp_path, monkeypatch)
    b = rb.set_move("seatinst", inst, "P0", "FO2", EMAIL, slot=1)
    assert [s["employee"] for s in b["pairings"][0]["seats"]] == \
        ["CP1", "FO2"]                      # правка применилась...
    assert feedback.episodes() == []        # ...а журнал не тронут
    assert not feedback.OVERRIDE_PATH.exists()   # файла нет вовсе

    # пустая строка причины — тоже не причина
    rb.set_move("seatinst", inst, "P0", None, EMAIL, reason_code="",
                reason_text="что-то")
    assert feedback.episodes() == []


def test_repair_apply_does_not_fill_the_feedback_journal(feedback, tmp_path,
                                                         monkeypatch):
    """Ремонт после сбоя — автоматический вызыватель set_move: его правки НЕ
    отклик планировщика и обязаны пройти мимо журнала."""
    from webapp import disruption as dz
    monkeypatch.setattr(dz, "DISRUPTIONS_PATH", tmp_path / "dz.json")
    inst = _two_seats(tmp_path, monkeypatch)
    dz.add_sick("seatinst", inst, "FO1", "2026-03-01", "2026-03-01")
    plan = dz.apply_repair("seatinst", inst, EMAIL)
    assert plan["applied_moves"] > 0                  # правки реально были
    assert rb.board("seatinst", inst)["moves"] > 0    # и легли на доску
    assert feedback.episodes() == []                  # но не в журнал
    assert not feedback.OVERRIDE_PATH.exists()


def test_fair_share_apply_does_not_fill_the_feedback_journal(feedback):
    from webapp import fair_share as fs
    inst = _base()
    plan = fs.apply_plan("gerad-1", inst, EMAIL)
    assert plan["applied_moves"] > 0
    assert feedback.episodes() == []
    assert not feedback.OVERRIDE_PATH.exists()


def test_feedback_failure_never_breaks_the_edit(feedback, tmp_path,
                                                monkeypatch):
    """Инвариант волны 1: сбор статистики не имеет права уронить правку."""
    inst = _two_seats(tmp_path, monkeypatch)

    def boom(*a, **kw):
        raise RuntimeError("журнал недоступен")

    monkeypatch.setattr(feedback, "record", boom)
    b = rb.set_move("seatinst", inst, "P0", "FO2", EMAIL, slot=1,
                    reason_code="missing_rule", reason_text="х")
    assert [s["employee"] for s in b["pairings"][0]["seats"]] == \
        ["CP1", "FO2"]
