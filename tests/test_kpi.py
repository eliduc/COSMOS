"""KPI layer (#12): per-instance record, fleet view, API, dashboard tab."""
import importlib

import pytest
from fastapi.testclient import TestClient

from webapp import kpi
from _stand_identity import ADMIN_EMAIL


def test_helpers():
    assert kpi._pct(50, 200) == 25.0
    assert kpi._pct(1, 0) is None and kpi._pct(None, 5) is None
    assert kpi._div(10, 4) == 2.5
    assert kpi._div(10, 0) is None


def test_instance_kpi_gerad1_end_to_end():
    k = kpi.instance_kpi("gerad-1")
    assert k and k["available"] is True
    assert k["total_cost"] > 0
    assert k["unit_cost_block_h"] == pytest.approx(
        k["total_cost"] / k["block_h"], rel=0.01)
    assert 0 < k["productivity_pct"] <= 100
    mix = (k["cost_mix_pay_pct"] + k["cost_mix_perdiem_pct"]
           + k["cost_mix_hotel_pct"])
    assert mix == pytest.approx(100.0, abs=0.5)
    assert k["pairings"] > 0 and k["avg_block_per_pairing_h"] > 0
    # roster side exists for inst1 (stage-2 journal has rows)
    if k["roster_available"]:
        assert k["gate_status"] is not None


def test_instance_kpi_unknown():
    assert kpi.instance_kpi("upload-nope") is None


def test_fleet_kpi_sorted_and_available_only():
    rows = kpi.fleet_kpi()
    assert rows, "seed instances must produce KPI rows"
    costs = [r["total_cost"] for r in rows]
    assert costs == sorted(costs, reverse=True)
    assert all(r["available"] for r in rows)


def test_fleet_kpi_scans_registry_once(monkeypatch):
    """Гейт против повторения зависания 2026-09-04: отчёт по парку обходит
    реестр РОВНО один раз и не разрешает наборы поимённо. Прежде на каждый
    набор шло два полных обхода выгрузок (get_instance в kpi и в costing);
    при 573 выгрузках на машине разработчика один отчёт занимал минуты, а
    test_kpi_endpoints — «висел» без вывода."""
    from webapp import costing, registry
    base = registry.get_instance("gerad-1")
    assert base is not None
    fleet = [dict(base, id=f"synthetic-{i}") for i in range(40)]
    calls = {"list": 0}

    def listing():
        calls["list"] += 1
        return list(fleet)

    def forbidden(inst_id):
        raise AssertionError(
            f"поимённое разрешение {inst_id!r} внутри отчёта по парку")

    monkeypatch.setattr(kpi, "list_instances", listing)
    monkeypatch.setattr(kpi, "get_instance", forbidden)
    monkeypatch.setattr(costing, "get_instance", forbidden)
    rows = kpi.fleet_kpi()
    assert calls["list"] == 1
    assert len(rows) == len(fleet) and all(r["available"] for r in rows)
    assert {r["instance"] for r in rows} == {i["id"] for i in fleet}


def test_instance_kpi_for_does_not_touch_registry(monkeypatch):
    """Расчёт по разрешённой записи — без единого обращения к реестру:
    ручка /api/kpi/{id} разрешает id один раз проверкой владения."""
    from webapp import costing, registry
    inst = registry.get_instance("gerad-1")
    assert inst is not None

    def forbidden(*_a, **_k):
        raise AssertionError("обращение к реестру внутри instance_kpi_for")

    monkeypatch.setattr(kpi, "get_instance", forbidden)
    monkeypatch.setattr(kpi, "list_instances", forbidden)
    monkeypatch.setattr(costing, "get_instance", forbidden)
    k = kpi.instance_kpi_for(inst)
    assert k["available"] is True and k["instance"] == "gerad-1"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_kpi_endpoints(client):
    r = client.get("/api/kpi/gerad-1")
    assert r.status_code == 200 and r.json()["available"] is True
    r2 = client.get("/api/kpi")
    assert r2.status_code == 200
    rows = r2.json()["rows"]
    assert any(x["instance"] == "gerad-1" for x in rows)
    assert client.get("/api/kpi/upload-nope").status_code == 404


def test_dashboard_kpi_tab_builds():
    from webapp import runs_dashboard
    view = runs_dashboard._kpi_view()
    assert hasattr(view, "objects") and len(view.objects) >= 3
    dash = runs_dashboard.make_dashboard()
    # Tabs with two views now
    assert len(dash) == 2
