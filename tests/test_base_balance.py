"""Multi-base balancing + base sizing (#15): pairing facts, per-base
supply/demand/utilization, transfer recommendations, config, tenancy, API."""
import importlib

import pytest
from fastapi.testclient import TestClient

from webapp import base_balance as bb
from _stand_identity import ADMIN_EMAIL

EMAIL = ADMIN_EMAIL

# One two-leg pairing anchored at BASE1, touching AIR1 and BASE2 (5h block,
# spanning two calendar days).
LEGS = [
    {"flightNumber": "L1", "departureAirport": "BASE1",
     "departureUTCDateTime": "2026-03-01T08:00:00",
     "arrivalAirport": "AIR1", "arrivalUTCDateTime": "2026-03-01T10:00:00",
     "aircraftType": "727"},
    {"flightNumber": "L2", "departureAirport": "AIR1",
     "departureUTCDateTime": "2026-03-02T09:00:00",
     "arrivalAirport": "BASE2", "arrivalUTCDateTime": "2026-03-02T12:00:00",
     "aircraftType": "727"},
]


# ------------------------------------------------------------- pure helpers

def test_pairing_facts_anchor_block_days_fleet():
    pf = bb._pairing_facts(LEGS)
    assert pf["anchor"] == "BASE1"
    assert pf["block_h"] == 5.0                 # 2h + 3h
    assert pf["days"] == 2                       # 2026-03-01, -03-02
    assert pf["fleets"] == {"727"}
    assert "BASE2" in pf["airports"] and "AIR1" in pf["airports"]


def test_pairing_facts_orders_by_time():
    pf = bb._pairing_facts(list(reversed(LEGS)))
    assert pf["anchor"] == "BASE1"              # first by departure time


def test_validate_base_config():
    assert bb.validate_base_config({"cap_hours": 85, "target_util": 0.8}) == []
    errs = " ".join(bb.validate_base_config(
        {"cap_hours": 0, "target_util": 2}))
    assert "cap_hours" in errs and "target_util" in errs
    # booleans must not sneak through the isinstance(int) check
    assert bb.validate_base_config({"cap_hours": True, "target_util": True})


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(bb, "BASE_CONFIG_PATH", tmp_path / "bc.json")
    cfg = bb.save_base_config({"cap_hours": 90, "target_util": 0.75})
    assert cfg == {"cap_hours": 90.0, "target_util": 0.75}
    again = bb.load_base_config()
    assert again["cap_hours"] == 90.0 and again["target_util"] == 0.75


def test_config_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(bb, "BASE_CONFIG_PATH", tmp_path / "none.json")
    cfg = bb.load_base_config()
    assert cfg == dict(bb.BASE_DEFAULTS)


@pytest.mark.parametrize("bad", [
    '{"cap_hours": null, "target_util": 0.8}',      # null -> TypeError before
    '{"cap_hours": "abc", "target_util": 0.8}',     # string -> ValueError
    '{"cap_hours": 0, "target_util": 0.0}',         # zeros -> ZeroDivision
    '{"cap_hours": true, "target_util": 9000}',     # bool + out-of-range
    '{"cap_hours": [1, 2], "target_util": NaN}',    # list + NaN
])
def test_load_ignores_semantically_bad_values(tmp_path, monkeypatch, bad):
    """A JSON-valid but semantically-bad on-disk config must degrade to the
    defaults, never crash a report (audit 2026-07-15)."""
    p = tmp_path / "bad.json"
    p.write_text(bad, encoding="utf-8")
    monkeypatch.setattr(bb, "BASE_CONFIG_PATH", p)
    assert bb.load_base_config() == dict(bb.BASE_DEFAULTS)


def _synthetic(monkeypatch, employees, flights, selected):
    """Wire instance_base_balance to a fully synthetic payload/catalog."""
    inst = {"id": "syn", "label": "syn"}
    monkeypatch.setattr(bb, "get_instance", lambda _id: inst)
    monkeypatch.setattr(bb, "_catalog_pairings", lambda _i: selected)
    monkeypatch.setattr(bb, "instance_payload_path", lambda _i: None)
    monkeypatch.setattr(bb, "load_payload", lambda _p: {
        "employees": employees, "flights": flights})


def test_status_never_contradicts_displayed_util(monkeypatch):
    """Raw util 1.0004 rounds to 1.0 — the row must NOT say 'over' while
    displaying 1.0 (status derives from the rounded util; audit 2026-07-15)."""
    emp = [{"id": "E1", "homeAirport": "B1", "isReserve": False,
            "seniority": 1, "typeRatings": ["727"]}]
    fl = [{"flightNumber": "L1", "departureAirport": "B1",
           "departureUTCDateTime": "2026-03-01T00:00:00",
           "arrivalAirport": "X", "arrivalUTCDateTime": "2026-03-04T13:02:03",
           "aircraftType": "727"}]      # 85.034 h > 85 h cap, <0.1% over
    _synthetic(monkeypatch, emp, fl, [{"fns": ["L1"]}])
    rep = bb.instance_base_balance("syn")
    row = rep["bases"][0]
    assert row["util"] == 1.0
    assert row["status"] != "over"      # displayed 1.0 => not "over"


def test_balance_score_none_with_single_worked_base(monkeypatch):
    """<2 worked bases: no dispersion to measure -> None, not a flattering
    100 (audit 2026-07-15)."""
    emp = [{"id": "E1", "homeAirport": "B1", "isReserve": False,
            "seniority": 1, "typeRatings": ["727"]},
           {"id": "E2", "homeAirport": "B2", "isReserve": False,
            "seniority": 2, "typeRatings": ["727"]}]
    fl = [{"flightNumber": "L1", "departureAirport": "B1",
           "departureUTCDateTime": "2026-03-01T08:00:00",
           "arrivalAirport": "X", "arrivalUTCDateTime": "2026-03-01T10:00:00",
           "aircraftType": "727"}]
    _synthetic(monkeypatch, emp, fl, [{"fns": ["L1"]}])
    rep = bb.instance_base_balance("syn")
    assert rep["recommendations"]["balance_score"] is None


def test_demand_at_unmanned_station_goes_to_non_base_flag(monkeypatch):
    """A pairing anchored where nobody is homed lands in the non_base flag
    (surfaced, not silently dropped)."""
    emp = [{"id": "E1", "homeAirport": "B1", "isReserve": False,
            "seniority": 1, "typeRatings": ["727"]}]
    fl = [{"flightNumber": "L1", "departureAirport": "NOWHERE",
           "departureUTCDateTime": "2026-03-01T08:00:00",
           "arrivalAirport": "B1", "arrivalUTCDateTime": "2026-03-01T10:00:00",
           "aircraftType": "727"}]
    _synthetic(monkeypatch, emp, fl, [{"fns": ["L1"]}])
    rep = bb.instance_base_balance("syn")
    flags = rep["recommendations"]["flags"]
    nb = [f for f in flags if f["kind"] == "non_base"]
    assert nb and nb[0]["pairings"] == 1
    assert all(r["base"] != "NOWHERE" for r in rep["bases"])


# ------------------------------------------------------------- report shape

@pytest.fixture()
def report():
    rep = bb.instance_base_balance("gerad-1")
    assert rep and rep.get("available") is True, rep
    return rep


def test_report_has_all_bases(report):
    names = {r["base"] for r in report["bases"]}
    assert {"BASE1", "BASE2", "BASE3"} <= names


def test_headcount_matches_payload(report):
    # seed roster: 33 line crew across the three bases
    assert report["recommendations"]["total_line"] == \
        sum(r["line"] for r in report["bases"]) == 33


def test_utilization_and_required_are_consistent(report):
    cap, target = report["cap_hours"], report["target_util"]
    for r in report["bases"]:
        if r["line"] > 0:
            assert r["util"] == pytest.approx(
                r["demand_block_h"] / (r["line"] * cap), abs=0.01)
        if r["demand_block_h"] > 0:
            import math
            assert r["required"] == math.ceil(
                r["demand_block_h"] / (cap * target))
            assert r["surplus"] == r["line"] - r["required"]


def test_over_base_flagged_when_util_exceeds_one(report):
    over = [r for r in report["bases"] if r["util"] and r["util"] > 1.0]
    for r in over:
        assert r["status"] == "over"
    kinds = {f["kind"] for f in report["recommendations"]["flags"]}
    if over:
        assert "over" in kinds


def test_transfers_go_from_surplus_to_deficit(report):
    by_base = {r["base"]: r for r in report["bases"]}
    for t in report["recommendations"]["transfers"]:
        assert t["crew"] >= 1
        assert by_base[t["from"]]["surplus"] > 0        # source has slack
        # destination is short (deficit or over-subscribed)
        assert by_base[t["to"]]["surplus"] < 0 or \
            by_base[t["to"]]["status"] == "over"


def test_transfer_total_bounded_by_surplus(report):
    moved = {}
    for t in report["recommendations"]["transfers"]:
        moved[t["from"]] = moved.get(t["from"], 0) + t["crew"]
    by_base = {r["base"]: r for r in report["bases"]}
    for src, n in moved.items():
        assert n <= by_base[src]["surplus"]


def test_balance_score_in_range(report):
    assert 0 <= report["recommendations"]["balance_score"] <= 100


def test_inter_base_links_are_between_real_bases(report):
    names = {r["base"] for r in report["bases"]}
    for link in report["inter_base"]:
        assert link["from"] in names and link["to"] in names
        assert link["from"] != link["to"]
        assert link["pairings"] >= 1


def test_fleet_summary_present(report):
    fs = report["fleet_summary"]
    assert fs and any(f["fleet"] == "727" for f in fs)
    for f in fs:
        assert f["qualified_crew"] >= 0 and isinstance(f["bases"], list)


def test_no_catalog_instance_returns_available_false(monkeypatch):
    inst = {"label": "no-such-label-xyz", "id": "fake"}
    monkeypatch.setattr(bb, "get_instance", lambda _id: inst)
    rep = bb.instance_base_balance("fake")
    assert rep == {"available": False, "reason": "no_catalog"}


def test_missing_instance_returns_none(monkeypatch):
    monkeypatch.setattr(bb, "get_instance", lambda _id: None)
    assert bb.instance_base_balance("nope") is None


# ------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", EMAIL)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(bb, "BASE_CONFIG_PATH", tmp_path / "bc.json")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_base_config_get_put(client):
    r = client.get("/api/base-config")
    assert r.status_code == 200 and r.json()["cap_hours"] == 85.0
    r2 = client.put("/api/base-config",
                    json={"cap_hours": 90, "target_util": 0.75})
    assert r2.status_code == 200 and r2.json()["target_util"] == 0.75
    r3 = client.put("/api/base-config",
                    json={"cap_hours": 0, "target_util": 0.75})
    assert r3.status_code == 422


def test_base_balance_endpoint_on_seed(client):
    r = client.get("/api/base-balance/gerad-1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert len(body["bases"]) >= 3
    assert body["recommendations"]["total_line"] == 33


def test_base_balance_missing_instance_404(client):
    assert client.get("/api/base-balance/upload-nope").status_code == 404
