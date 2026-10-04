"""Fair-share rebalancer (#9 + pay Phase C): legality preservation, pin
respect, payroll monotonicity, guarantee semantics, apply-as-board-moves,
API tenancy."""
import importlib

import pytest

from webapp import config_scope
from fastapi.testclient import TestClient

from webapp import fair_share as fs
from webapp import pay_credit as pc
from webapp import registry
from webapp import roster_board as rb
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(rb, "EDITS_PATH", tmp_path / "edits.json")
    monkeypatch.setitem(config_scope.INSTALL_FILES,
                        config_scope.DOMAIN_PAY, tmp_path / "pay.json")
    yield tmp_path


def _inst():
    return registry.get_instance("gerad-1")


def _plan(inst_id="gerad-1"):
    return fs.build_plan(inst_id, registry.get_instance(inst_id))


# ---------------------------------------------------------------- planning

def test_plan_improves_payroll_without_new_violations(iso):
    plan = _plan()
    assert plan["available"] is True
    assert plan["after"]["payroll_h"] <= plan["before"]["payroll_h"]
    assert plan["violations_after"] <= plan["violations_before"]
    assert plan["savings_h"] >= 0
    # the floor is a true lower bound
    assert plan["after"]["payroll_h"] >= plan["floor_h"] - 0.1


def test_plan_moves_are_individually_legal(iso):
    """Every proposed move must pass the SAME legality the board enforces:
    replay the plan through set_move (which the board uses) and confirm the
    validator count does not grow."""
    inst = _inst()
    plan = _plan()
    assert plan["moves"], "seed plan expected to find moves"
    pairings, crew_by_id, _ = rb._resolve(inst)
    before = len(rb._validate([dict(p) | {"employee": p["solver_employee"]}
                               for p in pairings], crew_by_id))
    for mv in plan["moves"]:
        rb.set_move("gerad-1", inst, mv["pid"], mv["to"])
    board = rb.board("gerad-1", inst)
    assert board["violation_count"] <= before


def test_plan_respects_pins(iso):
    inst = _inst()
    base = _plan()
    pinned_pid = base["moves"][0]["pid"]
    rb.set_pin("gerad-1", inst, pinned_pid, True)
    plan2 = _plan()
    assert all(m["pid"] != pinned_pid for m in plan2["moves"])


def test_plan_fairness_never_worsens_payroll(iso):
    """The fairness pass is payroll-neutral by construction."""
    plan = _plan()
    # after == payroll after BOTH passes; recompute payroll from before
    # minus savings must match
    assert plan["after"]["payroll_h"] == pytest.approx(
        plan["before"]["payroll_h"] - plan["savings_h"], abs=0.2)
    # fairness improved or equal
    if plan["before"]["cv"] is not None and plan["after"]["cv"] is not None:
        assert plan["after"]["cv"] <= plan["before"]["cv"] + 1e-9


def test_guarantee_zero_means_no_payroll_moves(iso, monkeypatch):
    """With guarantee 0 payroll == total credit for ANY assignment — only
    the payroll-neutral fairness pass may act, and savings must be 0."""
    pc.save_pay_config({"duty_rig": 2.0, "trip_rig": 3.5,
                        "min_duty_credit_h": 5.0,
                        "monthly_guarantee_h": 0.0})
    plan = _plan()
    assert plan["savings_h"] == 0.0
    if plan["before"]["cv"] is not None:
        assert plan["after"]["cv"] <= plan["before"]["cv"] + 1e-9


def test_no_roster_shape(monkeypatch):
    monkeypatch.setattr(rb, "_resolve", lambda inst: (None, {}, []))
    plan = fs.build_plan("gerad-1", _inst())
    assert plan == {"available": False, "reason": "no_roster"}


def test_apply_sequence_every_prefix_is_legal(iso):
    """Application follows the RAW execution order: after EVERY single
    set_move the board must show no new violations (the coalesced report
    order transited an illegal intermediate roster — audit 2026-07-15)."""
    inst = _inst()
    plan = _plan()
    assert len(plan["apply_sequence"]) >= len(plan["moves"])
    base = fs.build_plan("gerad-1", inst)["violations_before"]
    for mv in plan["apply_sequence"]:
        rb.set_move("gerad-1", inst, mv["pid"], mv["to"])
        board = rb.board("gerad-1", inst)
        assert board["violation_count"] <= base, mv
    rb.reset("gerad-1", ADMIN, True)


# ------------------------------------------------------------------- apply

def test_apply_writes_reversible_board_moves(iso):
    inst = _inst()
    plan = fs.apply_plan("gerad-1", inst, ADMIN)
    # applied counts the RAW sequence; the edits store keys by pid, so it
    # ends up with exactly the coalesced move count
    assert plan["applied_moves"] == len(plan["apply_sequence"]) > 0
    ed = rb.edits_for("gerad-1")
    assert len(ed.get("moves") or {}) == len(plan["moves"])
    # ...visible to the pay layer (payroll dropped)
    rep = pc.instance_pay("gerad-1")
    assert rep["roster"]["payroll_h"] == pytest.approx(
        plan["after"]["payroll_h"], abs=0.5)
    # ...and fully reversible
    assert rb.reset("gerad-1", ADMIN, True) is True
    rep2 = pc.instance_pay("gerad-1")
    assert rep2["roster"]["payroll_h"] == pytest.approx(
        plan["before"]["payroll_h"], abs=0.5)


def test_apply_is_idempotent_at_optimum(iso):
    inst = _inst()
    fs.apply_plan("gerad-1", inst, ADMIN)
    second = fs.apply_plan("gerad-1", inst, ADMIN)
    assert second["savings_h"] == 0.0
    assert second["applied_moves"] == len(second["moves"])


# --------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(rb, "EDITS_PATH", tmp_path / "edits.json")
    monkeypatch.setitem(config_scope.INSTALL_FILES,
                        config_scope.DOMAIN_PAY, tmp_path / "pay.json")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_api_plan_and_apply(client):
    r = client.get("/api/fairshare/gerad-1")
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["available"] is True and plan["savings_h"] > 0
    r2 = client.post("/api/fairshare/gerad-1/apply")
    assert r2.status_code == 200
    assert r2.json()["applied_moves"] > 0
    # board sees the edits (one per pid = the coalesced count)
    b = client.get("/api/board/gerad-1").json()
    assert b["moves"] == len(r2.json()["moves"])


def test_api_missing_instance_404(client):
    assert client.get("/api/fairshare/upload-nope").status_code == 404
    assert client.post(
        "/api/fairshare/upload-nope/apply").status_code == 404
