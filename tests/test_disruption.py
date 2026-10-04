"""Disruption recovery slice A: sickness overlay reaches the board
validator, greedy repair covers broken pairings without new violations,
pins are honestly overridden for broken pairings only, apply is
reversible, store CRUD, API tenancy."""
import importlib

import pytest
from fastapi.testclient import TestClient

from webapp import disruption as dz
from webapp import registry
from webapp import roster_board as rb
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL
USER = "someone@example.com"


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(dz, "DISRUPTIONS_PATH", tmp_path / "dz.json")
    monkeypatch.setattr(rb, "EDITS_PATH", tmp_path / "edits.json")
    yield tmp_path


def _inst():
    return registry.get_instance("gerad-1")


def _busiest():
    """(crew_id, its pairings, window) of the busiest solver crew."""
    prs, crew, _days = rb._resolve(_inst())
    busy = max(crew, key=lambda c: sum(
        1 for p in prs if p["solver_employee"] == c))
    mine = [p for p in prs if p["solver_employee"] == busy]
    d0 = min(p["days"][0] for p in mine)
    d1 = max(p["days"][-1] for p in mine)
    return busy, mine, d0, d1


# -------------------------------------------------------------------- store

def test_add_sick_validates_input(iso):
    inst = _inst()
    with pytest.raises(dz.DisruptionError):
        dz.add_sick("gerad-1", inst, "NOPE", "2000-01-01", "2000-01-02")
    with pytest.raises(dz.DisruptionError):
        dz.add_sick("gerad-1", inst, "EMP004", "2000-01-05", "2000-01-01")
    with pytest.raises(dz.DisruptionError):
        dz.add_sick("gerad-1", inst, "EMP004", "2000-01-01", "2001-01-01")


def test_event_crud_and_sick_days(iso):
    inst = _inst()
    ev = dz.add_sick("gerad-1", inst, "EMP004", "2000-01-02", "2000-01-04",
                     "грипп", ADMIN)
    days = dz.sick_days("gerad-1")
    assert days["EMP004"] == {"2000-01-02", "2000-01-03", "2000-01-04"}
    assert dz.remove_event("gerad-1", ev["id"]) is True
    assert dz.remove_event("gerad-1", ev["id"]) is False
    assert dz.sick_days("gerad-1") == {}


def test_sickness_reaches_board_validator(iso):
    """The overlay is one source of truth: after add_sick the BOARD itself
    flags the sick crew's assignments as 'unavailable' violations."""
    inst = _inst()
    busy, mine, d0, d1 = _busiest()
    before = rb.board("gerad-1", inst)["violation_count"]
    dz.add_sick("gerad-1", inst, busy, d0, d1)
    after = rb.board("gerad-1", inst)["violation_count"]
    assert after >= before + len(mine)


# ------------------------------------------------------------------- repair

def test_repair_covers_all_and_reduces_violations(iso):
    inst = _inst()
    busy, mine, d0, d1 = _busiest()
    dz.add_sick("gerad-1", inst, busy, d0, d1)
    plan = dz.build_repair_plan("gerad-1", inst)
    assert plan["available"] is True
    assert plan["broken_count"] == len(mine)
    assert plan["violations_after"] < plan["violations_before"]
    # nothing is assigned back to the sick crew
    assert all(m["to"] != busy for m in plan["repairs"])
    # honest arithmetic: repaired + uncovered == broken (swap_out rows are
    # auxiliary moves of OTHER crews' pairings)
    repaired_broken = [m for m in plan["repairs"]
                       if m["via"] in ("direct", "swap_in")]
    assert len(repaired_broken) + len(plan["uncovered"]) == len(mine)


def test_repair_prefers_reserves(iso):
    """Targets with no flying in the window (reserves) must be used before
    busy crew when they are legal candidates."""
    inst = _inst()
    busy, mine, d0, d1 = _busiest()
    prs, crew, _ = rb._resolve(inst)
    flying = {p["solver_employee"] for p in prs}
    reserves = set(crew) - flying
    dz.add_sick("gerad-1", inst, busy, d0, d1)
    plan = dz.build_repair_plan("gerad-1", inst)
    if reserves:            # seed data has idle crew: they must be hit first
        assert any(m["to"] in reserves for m in plan["repairs"])


def test_apply_is_reversible_board_moves(iso):
    inst = _inst()
    busy, mine, d0, d1 = _busiest()
    dz.add_sick("gerad-1", inst, busy, d0, d1)
    res = dz.apply_repair("gerad-1", inst, ADMIN)
    assert res["applied_moves"] == res["changes_count"] > 0
    assert rb.board("gerad-1", inst)["violation_count"] == 0
    # fully reversible: reset restores the solver roster (violations return
    # because the sickness event is still active)
    assert rb.reset("gerad-1", ADMIN, True) is True
    assert rb.board("gerad-1", inst)["violation_count"] >= len(mine)


def test_pinned_broken_pairing_is_unpinned_and_repaired(iso):
    """Sickness beats a pin: a pinned pairing of the sick crew is repaired,
    and the plan reports unpin=True for it."""
    inst = _inst()
    busy, mine, d0, d1 = _busiest()
    pinned = mine[0]["pid"]
    rb.set_pin("gerad-1", inst, pinned, True)
    dz.add_sick("gerad-1", inst, busy, d0, d1)
    plan = dz.build_repair_plan("gerad-1", inst)
    row = next(m for m in plan["repairs"] if m["pid"] == pinned)
    assert row["unpin"] is True
    res = dz.apply_repair("gerad-1", inst, ADMIN)
    assert res["applied_moves"] == res["changes_count"]
    assert rb.board("gerad-1", inst)["violation_count"] == 0


def test_unbroken_pinned_pairings_never_move(iso):
    """Pins of HEALTHY crews' pairings are sacred even during repair."""
    inst = _inst()
    busy, mine, d0, d1 = _busiest()
    prs, _crew, _ = rb._resolve(inst)
    other = next(p for p in prs if p["solver_employee"] != busy)
    rb.set_pin("gerad-1", inst, other["pid"], True)
    dz.add_sick("gerad-1", inst, busy, d0, d1)
    plan = dz.build_repair_plan("gerad-1", inst)
    assert all(m["pid"] != other["pid"] for m in plan["repairs"])


def test_extra_pids_manual_recrew(iso):
    """A healthy pairing can be re-crewed by explicit request (e.g. the
    aircraft case handled manually in slice A)."""
    inst = _inst()
    prs, _crew, _ = rb._resolve(inst)
    target = prs[0]
    plan = dz.build_repair_plan("gerad-1", inst,
                                extra_pids=[target["pid"]])
    assert plan["broken_count"] == 1
    covered = [m for m in plan["repairs"] if m["pid"] == target["pid"]]
    assert covered and covered[0]["to"] != target["solver_employee"]


def test_no_roster_shape(iso, monkeypatch):
    monkeypatch.setattr(dz, "_effective_state", lambda i, j: None)
    plan = dz.build_repair_plan("gerad-1", _inst())
    assert plan == {"available": False, "reason": "no_roster"}


# ---------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(dz, "DISRUPTIONS_PATH", tmp_path / "dz.json")
    monkeypatch.setattr(rb, "EDITS_PATH", tmp_path / "edits.json")
    users.ensure_db()
    users.add(USER, "user", by=ADMIN)
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_api_flow_and_tenancy(client, monkeypatch):
    r = client.post("/api/disruption/gerad-1/sick",
                    json={"employee": "EMP004", "day_from": "2000-01-01",
                          "day_to": "2000-01-31", "note": "грипп"})
    assert r.status_code == 200
    imp = client.get("/api/disruption/gerad-1").json()
    assert imp["available"] and imp["broken"]
    plan = client.post("/api/disruption/gerad-1/plan", json={}).json()
    assert plan["changes_count"] > 0
    ap = client.post("/api/disruption/gerad-1/apply", json={}).json()
    assert ap["applied_moves"] == ap["changes_count"]
    ev_id = imp["events"][0]["id"]
    assert client.delete(
        f"/api/disruption/gerad-1/{ev_id}").status_code == 200
    # tenancy: a plain user cannot see the admin-owned seed instance
    monkeypatch.setenv("CREW_DEV_USER", USER)
    assert client.get("/api/disruption/gerad-1").status_code == 404
    assert client.post("/api/disruption/gerad-1/sick",
                       json={"employee": "EMP004",
                             "day_from": "2000-01-01",
                             "day_to": "2000-01-02"}).status_code == 404
