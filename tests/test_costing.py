"""Cost layer (allowances) + hotel/layover analytics + API."""
import importlib
import json

import pytest

from webapp import config_scope
from fastapi.testclient import TestClient

from webapp import costing
from _stand_identity import ADMIN_EMAIL

CFG = {
    "currency": "USD",
    "block_hour_rate": 100.0,
    "per_diem_hour": 2.0,
    "hotel_default_rate": 110.0,
    "hotel_rates": {"AIR1": 90.0},
    "deadhead_factor": 0.5,
}

# Two-day pairing: BASE->AIR1 (2h), overnight at AIR1, AIR1->BASE (3h).
LEGS = [
    {"flightNumber": "L1", "departureAirport": "BASE",
     "departureUTCDateTime": "2026-03-01T08:00:00",
     "arrivalAirport": "AIR1", "arrivalUTCDateTime": "2026-03-01T10:00:00"},
    {"flightNumber": "L2", "departureAirport": "AIR1",
     "departureUTCDateTime": "2026-03-02T09:00:00",
     "arrivalAirport": "BASE", "arrivalUTCDateTime": "2026-03-02T12:00:00"},
]


def test_pairing_costing_math():
    c = costing.pairing_costing(LEGS, dict(CFG))
    assert c["block_h"] == 5.0                    # 2h + 3h
    assert c["tafb_h"] == 28.0                    # 08:00 -> +1d 12:00
    assert c["nights"] == [{"station": "AIR1", "date": "2026-03-01",
                            "rate": 90.0}]
    assert c["pay_cost"] == 500.0                 # 5 * 100
    assert c["per_diem_cost"] == 56.0             # 28 * 2
    assert c["hotel_cost"] == 90.0
    assert c["total_cost"] == 646.0


def test_two_night_layover_counts_two_nights():
    legs = [dict(LEGS[0]),
            {**LEGS[1],
             "departureUTCDateTime": "2026-03-03T09:00:00",
             "arrivalUTCDateTime": "2026-03-03T12:00:00"}]
    c = costing.pairing_costing(legs, dict(CFG))
    assert [n["date"] for n in c["nights"]] == ["2026-03-01", "2026-03-02"]


def test_same_day_sit_is_not_a_night():
    legs = [dict(LEGS[0]),
            {**LEGS[1],
             "departureUTCDateTime": "2026-03-01T15:00:00",
             "arrivalUTCDateTime": "2026-03-01T18:00:00"}]
    c = costing.pairing_costing(legs, dict(CFG))
    assert c["nights"] == [] and c["hotel_cost"] == 0.0


def test_hotel_rate_fallback_to_default():
    assert costing.hotel_rate("AIR1", CFG) == 90.0
    assert costing.hotel_rate("XXX", CFG) == 110.0
    assert costing.hotel_rate("air1", CFG) == 90.0    # case-insensitive


def test_validate_cost_config():
    assert costing.validate_cost_config({"block_hour_rate": 1}) == []
    errs = costing.validate_cost_config({
        "block_hour_rate": -1, "per_diem_hour": True,
        "hotel_rates": {"TOOLONGCODE": 1, "OK": -5}, "currency": ""})
    joined = " ".join(errs)
    assert "block_hour_rate" in joined and "per_diem_hour" in joined
    assert "TOOLONGCODE" in joined and "OK" in joined and "currency" in joined


def test_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setitem(config_scope.INSTALL_FILES,
                        config_scope.DOMAIN_COST, tmp_path / "cc.json")
    cfg = costing.save_cost_config({"block_hour_rate": 150,
                                    "hotel_rates": {"atl": 95}})
    assert cfg["block_hour_rate"] == 150
    assert cfg["hotel_rates"] == {"ATL": 95.0}
    again = costing.load_cost_config()
    assert again["block_hour_rate"] == 150
    assert again["per_diem_hour"] == costing.COST_DEFAULTS["per_diem_hour"]


# ------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setitem(config_scope.INSTALL_FILES,
                        config_scope.DOMAIN_COST, tmp_path / "cc.json")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_cost_config_get_put(client):
    r = client.get("/api/cost-config")
    assert r.status_code == 200 and r.json()["currency"] == "USD"
    r2 = client.put("/api/cost-config",
                    json={"block_hour_rate": 130, "currency": "EUR"})
    assert r2.status_code == 200 and r2.json()["currency"] == "EUR"
    r3 = client.put("/api/cost-config", json={"per_diem_hour": -3})
    assert r3.status_code == 422


def test_costing_endpoint_on_seed_instance(client):
    r = client.get("/api/costing/gerad-1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["pairings"] > 0
    t = body["totals"]
    assert t["block_h"] > 0 and t["total_cost"] > 0
    assert t["total_cost"] == pytest.approx(
        t["pay_cost"] + t["per_diem_cost"] + t["hotel_cost"], abs=1.0)
    assert isinstance(body["hotel_by_station"], list)
    assert isinstance(body["rooms_forecast"], list)
    if body["rooms_forecast"]:
        d0 = body["rooms_forecast"][0]
        assert d0["rooms"] == sum(d0["stations"].values())


def test_costing_endpoint_missing_instance_404(client):
    assert client.get("/api/costing/upload-nope").status_code == 404
