"""Pay-and-credit model (#1, Phase A): duty split, rig formulas with
hand-computed numbers, config hardening, instance/roster report, API."""
import importlib
import json

import pytest

from webapp import config_scope
from fastapi.testclient import TestClient

from webapp import pay_credit as pc
from webapp import roster_board as rb
from _stand_identity import ADMIN_EMAIL

CFG = {"duty_rig": 2.0, "trip_rig": 3.5,
       "min_duty_credit_h": 5.0, "monthly_guarantee_h": 75.0}


def _leg(dep, arr, fn="L", a="A", b="B"):
    return {"flightNumber": fn, "departureAirport": a, "arrivalAirport": b,
            "departureUTCDateTime": dep, "arrivalUTCDateTime": arr,
            "aircraftType": "727"}


# ---------------------------------------------------------------- formulas

def test_split_duties_at_rest_boundary():
    legs = [
        _leg("2026-03-01T08:00:00", "2026-03-01T10:00:00", "L1"),
        _leg("2026-03-01T11:00:00", "2026-03-01T13:00:00", "L2"),  # 1h sit
        _leg("2026-03-02T09:00:00", "2026-03-02T11:00:00", "L3"),  # 20h gap
    ]
    duties = pc.split_duties(legs, 600)          # rest >= 10h
    assert [len(d) for d in duties] == [2, 1]
    # a 9h59m gap is a sit, not a rest
    legs[2]["departureUTCDateTime"] = "2026-03-01T22:59:00"
    legs[2]["arrivalUTCDateTime"] = "2026-03-02T00:59:00"
    assert [len(d) for d in pc.split_duties(legs, 600)] == [3]


def test_duty_rig_binds():
    """2h block in an 11h duty span (9h sit < the 10h rest): rig 11/2 =
    5.5h beats block(2) and the 5h floor."""
    legs = [_leg("2026-03-01T08:00:00", "2026-03-01T09:00:00", "L1"),
            _leg("2026-03-01T18:00:00", "2026-03-01T19:00:00", "L2")]
    c = pc.pairing_credit(legs, CFG, 600)
    assert c["n_duties"] == 1
    assert c["duties"][0]["binding"] == "duty_rig"
    assert c["duties"][0]["credit_h"] == 5.5
    assert c["credit_h"] == 5.5 and c["binding"] == "duties"


def test_min_duty_floor_binds():
    """1h block, 2h span: floor 5h beats block(1) and rig(1)."""
    legs = [_leg("2026-03-01T08:00:00", "2026-03-01T09:00:00")]
    c = pc.pairing_credit(legs, CFG, 600)
    assert c["duties"][0]["binding"] == "min_duty"
    assert c["credit_h"] == 5.0


def test_trip_rig_binds_on_long_layover():
    """Two 5h-floor duties (10h) vs TAFB 47h/3.5 = 13.43h: trip rig wins."""
    legs = [_leg("2026-03-01T08:00:00", "2026-03-01T09:00:00", "L1"),
            _leg("2026-03-03T06:00:00", "2026-03-03T07:00:00", "L2")]
    c = pc.pairing_credit(legs, CFG, 600)
    assert c["n_duties"] == 2
    assert c["duty_credit_h"] == 10.0
    assert c["tafb_h"] == 47.0
    assert c["credit_h"] == pytest.approx(47.0 / 3.5, abs=0.01)
    assert c["binding"] == "trip_rig"


def test_deadhead_legs_are_credit_not_block():
    """A TDH deadhead leg keeps the crew on duty (span/TAFB count) but is
    NOT flown block — paying it as block inflated the module's own headline
    metric ~3x on the seed (audit 2026-07-15, HIGH)."""
    legs = [_leg("2026-03-01T08:00:00", "2026-03-01T13:00:00", "L1"),
            _leg("2026-03-01T14:00:00", "2026-03-01T18:00:00", "L2")]
    # L2 is a deadhead: only L1 (5h) is flown, span still 10h
    c = pc.pairing_credit(legs, CFG, 600, active_fns=["L1"])
    assert c["block_h"] == 5.0          # active only
    assert c["dh_h"] == 4.0             # the ride is reported
    assert c["duties"][0]["span_h"] == 10.0   # duty covers the ride
    assert c["credit_h"] == 5.0         # max(5, 10/2, 5)
    # same legs all-active: block 9h wins
    c2 = pc.pairing_credit(legs, CFG, 600, active_fns=["L1", "L2"])
    assert c2["block_h"] == 9.0 and c2["dh_h"] == 0.0


def test_instance_report_uses_active_fns(iso):
    """Catalog totals must count every physical leg exactly once: the sum
    of active block over all pairings equals the payload's whole-fleet
    block."""
    rep = pc.instance_pay("gerad-1")
    payload = pc.registry.load_payload(
        pc.registry.instance_payload_path(pc.registry.get_instance("gerad-1")))
    fleet_block = sum(
        pc._hours(f["departureUTCDateTime"], f["arrivalUTCDateTime"])
        for f in payload["flights"])
    assert rep["totals"]["block_h"] == pytest.approx(fleet_block, abs=1.0)
    assert rep["totals"]["deadhead_h"] > 0      # the seed has TDH riders


def test_min_rest_survives_non_dict_config(tmp_path, monkeypatch):
    """A parseable-but-non-dict airline config must degrade to the default,
    never 500 a money report (audit 2026-07-15, MEDIUM)."""
    p = tmp_path / "airline.json"
    monkeypatch.setitem(config_scope.INSTALL_FILES,
                        config_scope.DOMAIN_RULES, p)
    for bad in ('[1, 2]', '"600"', '{"parameters": [1, 2]}',
                '{"parameters": {"min_rest_min": null}}'):
        p.write_text(bad, encoding="utf-8")
        assert pc.min_rest_minutes() == 600


def test_block_binds_when_flying_is_dense():
    """9h block in a 10h span: block beats rig(5) and floor(5)."""
    legs = [_leg("2026-03-01T08:00:00", "2026-03-01T13:00:00", "L1"),
            _leg("2026-03-01T14:00:00", "2026-03-01T18:00:00", "L2")]
    c = pc.pairing_credit(legs, CFG, 600)
    assert c["duties"][0]["binding"] == "block"
    assert c["credit_h"] == 9.0


# ------------------------------------------------------------------ config

def test_pay_config_roundtrip_and_hardening(tmp_path, monkeypatch):
    monkeypatch.setitem(config_scope.INSTALL_FILES,
                        config_scope.DOMAIN_PAY, tmp_path / "pay.json")
    cfg = pc.save_pay_config({"duty_rig": 2.5, "trip_rig": 4.0,
                              "min_duty_credit_h": 4.5,
                              "monthly_guarantee_h": 80})
    assert cfg["duty_rig"] == 2.5
    assert pc.load_pay_config()["monthly_guarantee_h"] == 80.0
    # bad on-disk values degrade to defaults, never crash
    (tmp_path / "pay.json").write_text(
        '{"duty_rig": null, "trip_rig": "x", "min_duty_credit_h": 99,'
        ' "monthly_guarantee_h": true}', encoding="utf-8")
    assert pc.load_pay_config() == dict(pc.PAY_DEFAULTS)
    errs = " ".join(pc.validate_pay_config({"duty_rig": 0}))
    assert "duty_rig" in errs and "trip_rig" in errs


def test_min_rest_fallback(tmp_path, monkeypatch):
    monkeypatch.setitem(config_scope.INSTALL_FILES,
                        config_scope.DOMAIN_RULES, tmp_path / "nope.json")
    assert pc.min_rest_minutes() == 600


# ------------------------------------------------------------------ report

@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setitem(config_scope.INSTALL_FILES,
                        config_scope.DOMAIN_PAY, tmp_path / "pay.json")
    monkeypatch.setattr(rb, "EDITS_PATH", tmp_path / "edits.json")
    yield tmp_path


def test_instance_report_invariants(iso):
    rep = pc.instance_pay("gerad-1")
    assert rep["available"] is True
    t = rep["totals"]
    # credit is never below block (it is a max over floors incl. block)
    assert t["credit_h"] >= t["block_h"]
    assert t["credit_to_block"] >= 1.0
    # totals are rounded to 0.1h each -> allow +-0.2h x rate of slack
    assert t["soft_cost"] == pytest.approx(
        (t["credit_h"] - t["block_h"]) * rep["config"]["block_hour_rate"],
        abs=0.2 * rep["config"]["block_hour_rate"])
    assert sum(rep["binding_counts"].values()) == rep["pairings"]
    for r in rep["top_pairings"]:
        assert r["credit_h"] >= r["block_h"]


def test_roster_payroll_invariants(iso):
    rep = pc.instance_pay("gerad-1")
    r = rep["roster"]
    assert r is not None and r["total_crew"] > 0
    g = rep["config"]["monthly_guarantee_h"]
    for c in r["crew"]:
        assert c["paid_h"] == pytest.approx(max(c["credit_h"], g), abs=0.1)
        assert c["guarantee_burn_h"] >= 0
    assert r["payroll_h"] >= rep["totals"]["credit_h"] - 0.1 - \
        r["unassigned_credit_h"]


def test_board_move_shifts_credit(iso):
    """A manual board move re-attributes the pairing's credit to the new
    crew member — payroll follows the board, not the raw solver output."""
    rep1 = pc.instance_pay("gerad-1")
    crew1 = {c["employee_id"]: c["credit_h"] for c in rep1["roster"]["crew"]}
    donor = max(rep1["roster"]["crew"], key=lambda c: c["credit_h"])
    inst = pc.registry.get_instance("gerad-1")
    pairings, _, _ = rb._resolve(inst)
    pid = next(p["pid"] for p in pairings
               if p["solver_employee"] == donor["employee_id"])
    rcpt = next(c["employee_id"] for c in rep1["roster"]["crew"]
                if c["employee_id"] != donor["employee_id"])
    rb.set_move("gerad-1", inst, pid, rcpt)
    rep2 = pc.instance_pay("gerad-1")
    crew2 = {c["employee_id"]: c["credit_h"] for c in rep2["roster"]["crew"]}
    assert crew2[donor["employee_id"]] < crew1[donor["employee_id"]]
    assert crew2[rcpt] > crew1[rcpt]


def test_missing_and_no_catalog(monkeypatch):
    assert pc.instance_pay("nope-xyz") is None
    monkeypatch.setattr(pc, "_catalog_pairings", lambda inst: None)
    rep = pc.instance_pay("gerad-1")
    assert rep == {"available": False, "reason": "no_catalog"}


# --------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setitem(config_scope.INSTALL_FILES,
                        config_scope.DOMAIN_PAY, tmp_path / "pay.json")
    monkeypatch.setattr(rb, "EDITS_PATH", tmp_path / "edits.json")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_api_pay_config_get_put(client):
    r = client.get("/api/pay-config")
    assert r.status_code == 200 and r.json()["duty_rig"] == 2.0
    r2 = client.put("/api/pay-config",
                    json={"duty_rig": 2.5, "trip_rig": 4,
                          "min_duty_credit_h": 5,
                          "monthly_guarantee_h": 70})
    assert r2.status_code == 200 and r2.json()["trip_rig"] == 4.0
    assert client.put("/api/pay-config",
                      json={"duty_rig": 0}).status_code == 422


def test_api_pay_report(client):
    r = client.get("/api/pay/gerad-1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["totals"]["credit_to_block"] >= 1.0
    assert body["roster"]["payroll_h"] > 0


def test_api_pay_missing_404(client):
    assert client.get("/api/pay/upload-nope").status_code == 404


def test_reselect_report_reads_artifact(tmp_path, monkeypatch):
    """Phase B artifact surfacing: available:false without the file, the
    parsed comparison with it."""
    from webapp import registry
    monkeypatch.setattr(registry, "FINAL_REPORT", tmp_path)
    monkeypatch.setattr(pc.registry, "FINAL_REPORT", tmp_path)
    assert pc.reselect_report("nope-xyz") is None
    rep = pc.reselect_report("gerad-1")
    assert rep == {"available": False, "reason": "not_computed"}
    (tmp_path / "pay_reselect_inst1.json").write_text(json.dumps({
        "label": "inst1",
        "current": {"n_pairings": 10, "credit_h": 100.0},
        "credit_selected": {"n_pairings": 9, "credit_h": 90.0},
        "savings_credit_h": 10.0}), encoding="utf-8")
    rep2 = pc.reselect_report("gerad-1")
    assert rep2["available"] is True
    assert rep2["savings_credit_h"] == 10.0


def test_api_pay_reselect(client):
    r = client.get("/api/pay-reselect/gerad-1")
    assert r.status_code == 200
    assert "available" in r.json()
    assert client.get("/api/pay-reselect/upload-nope").status_code == 404


def test_api_airline_config_put_admin_only(client, monkeypatch):
    """min_rest_min now feeds MONEY (duty split) — the historically ungated
    airline-config PUT is admin-only since 2026-07-15 (closes the standing
    2026-07-14 audit finding)."""
    from webapp import users
    users.add("plain.user@example.com", "user", by=ADMIN_EMAIL)
    monkeypatch.setenv("CREW_DEV_USER", "plain.user@example.com")
    r = client.put("/api/airline-config",
                   json={"parameters": {"min_rest_min": 30}})
    assert r.status_code == 403
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    assert client.get("/api/airline-config").status_code == 200
