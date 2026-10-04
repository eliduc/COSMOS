"""Day-of-operations plan-vs-fact: the pure matcher/KPIs (plan_fact.py),
the fact store (ops_log.py) and the HTTP surface. The store is redirected
to a tmp file; HTTP runs in dev auth mode."""
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from _stand_identity import ADMIN_EMAIL

REPO_ROOT = Path(__file__).resolve().parents[1]


def _flight(fn, dep, arr, dep_ap="ATL", arr_ap="LAS"):
    return {"flightNumber": fn, "departureAirport": dep_ap,
            "arrivalAirport": arr_ap,
            "departureUTCDateTime": dep, "arrivalUTCDateTime": arr,
            "aircraftType": "NARROWBODY"}


PAYLOAD = {"flights": [
    _flight("DL1-0800", "2024-08-15T08:00:00", "2024-08-15T10:00:00"),  # 120m
    _flight("DL2-0900", "2024-08-15T09:00:00", "2024-08-15T10:30:00"),  # 90m
    _flight("DL3-1000", "2024-08-15T10:00:00", "2024-08-15T11:00:00"),  # 60m
    _flight("DL4-1100", "2024-08-15T11:00:00", "2024-08-15T12:00:00"),  # 60m
]}


# ------------------------------------------------------------- pure plan_fact

def test_no_facts_means_all_on_plan():
    from webapp import plan_fact
    s = plan_fact.summary(PAYLOAD, {})
    assert s["scheduled"] == 4 and s["operated"] == 4
    assert s["cancelled"] == 0 and s["delayed"] == 0 and s["affected"] == 0
    # H11: zero observed facts -> completion/OTP are N/A, not a perfect 100%
    assert s["completionFactorPct"] is None
    assert s["otpDep15Pct"] is None and s["otpArr15Pct"] is None
    assert s["reported"] == 0 and s["coveragePct"] == 0.0
    assert s["planBlockHours"] == round((120 + 90 + 60 + 60) / 60.0, 1)
    assert s["operatedBlockHours"] == s["planBlockHours"]


def test_cancel_delay_divert_classified():
    from webapp import plan_fact
    facts = {
        "DL1-0800": {"cancelled": True, "cause": "weather"},
        "DL2-0900": {"dep_delay_min": 40, "arr_delay_min": 35,
                     "cause": "carrier"},
        "DL3-1000": {"dep_delay_min": 5, "arr_delay_min": 8},  # within window
        "DL4-1100": {"diverted": True},
    }
    s = plan_fact.summary(PAYLOAD, facts)
    assert s["cancelled"] == 1 and s["delayed"] == 1 and s["diverted"] == 1
    assert s["operated"] == 3                       # 4 - 1 cancelled
    assert s["completionFactorPct"] == 75.0
    assert s["affected"] == 3
    assert s["reported"] == 4 and s["coveragePct"] == 100.0
    # OTP over operated (3). Dep: DL2 late, DL3+DL4 on time -> 2/3.
    # Arr: DL2 late, DL3 on time, DL4 DIVERTED (never arrived on time at
    # dest, so excluded from the arr-OTP numerator) -> only DL3 -> 1/3.
    assert s["otpDep15Pct"] == round(2 / 3 * 100, 1)
    assert s["otpArr15Pct"] == round(1 / 3 * 100, 1)
    assert s["avgDepDelayMin"] == 40.0 and s["avgArrDelayMin"] == 35.0
    assert s["totalDelayMin"] == 40                 # max(dep,arr) of the delay
    assert s["causeCounts"]["carrier"] == 1
    assert s["causeMinutes"]["carrier"] == 40
    # cancelled flight drops its block from operated (120m gone)
    assert s["operatedBlockHours"] == round((90 + 60 + 60) / 60.0, 1)


def test_deviations_sorted_disruptions_first():
    from webapp import plan_fact
    facts = {"DL4-1100": {"cancelled": True},
             "DL2-0900": {"dep_delay_min": 30}}
    rows = plan_fact.deviations(PAYLOAD, facts)
    assert [r["status"] for r in rows][:2] == ["cancelled", "delayed"]
    assert rows[0]["flightNumber"] == "DL4-1100"
    assert {r["status"] for r in rows[2:]} == {"scheduled"}


def test_empty_payload_unavailable():
    from webapp import plan_fact
    assert plan_fact.summary({"flights": []}, {})["available"] is False


def test_daymin_orders_utc_rollover_flights():
    """dayMin measures minutes from the earliest departure, so a late flight
    whose UTC departure rolls past midnight sorts AFTER the morning ones
    (audit 2026-07-17 scrubber fix)."""
    from webapp import plan_fact
    payload = {"flights": [
        _flight("M-morning", "2024-08-15T12:00:00", "2024-08-15T14:00:00"),
        _flight("N-rollover", "2024-08-16T00:50:00", "2024-08-16T03:00:00"),
    ]}
    rows = {r["flightNumber"]: r["dayMin"]
            for r in plan_fact.deviations(payload, {})}
    assert rows["M-morning"] == 720          # 12:00 from 08-15 00:00
    assert rows["N-rollover"] == 24 * 60 + 50  # 1490 — after, not before
    assert rows["N-rollover"] > rows["M-morning"]


# ---------------------------------------------------------------- ops_log

@pytest.fixture()
def store(tmp_path):
    from webapp import ops_log
    ops_log.OPS_LOG_PATH = tmp_path / "ops_log.json"
    return ops_log


def test_upsert_validates_flight_and_clamps(store):
    fns = {"DL1-0800", "DL2-0900"}
    with pytest.raises(store.OpsLogError):
        store.upsert_fact("i1", fns, "NOPE-0000", {"dep_delay_min": 5})
    f = store.upsert_fact("i1", fns, "DL1-0800",
                          {"dep_delay_min": 99999, "cause": "nas"}, by="a@x")
    assert f["dep_delay_min"] == store.MAX_DELAY_MIN     # clamped
    assert f["cause"] == "nas" and f["by"] == "a@x"
    assert store.facts_for("i1")["DL1-0800"]["dep_delay_min"] == \
        store.MAX_DELAY_MIN


def test_cancel_clears_delays(store):
    f = store.upsert_fact("i1", {"DL1-0800"}, "DL1-0800",
                          {"cancelled": True, "dep_delay_min": 30,
                           "diverted": True})
    assert f["cancelled"] is True
    assert f["dep_delay_min"] is None and f["diverted"] is False


def test_bad_cause_rejected(store):
    with pytest.raises(store.OpsLogError):
        store.upsert_fact("i1", {"DL1-0800"}, "DL1-0800", {"cause": "aliens"})


def test_remove_and_clear(store):
    store.upsert_fact("i1", {"DL1-0800", "DL2-0900"}, "DL1-0800",
                      {"dep_delay_min": 10})
    store.upsert_fact("i1", {"DL1-0800", "DL2-0900"}, "DL2-0900",
                      {"cancelled": True})
    assert store.remove_fact("i1", "DL1-0800") is True
    assert store.remove_fact("i1", "DL1-0800") is False
    assert len(store.facts_for("i1")) == 1
    assert store.clear("i1") == 1
    assert store.facts_for("i1") == {}


def test_bulk_upsert_skips_unknown(store):
    fns = {"DL1-0800", "DL2-0900"}
    n = store.bulk_upsert("i1", {
        "DL1-0800": {"dep_delay_min": 20},
        "DL2-0900": {"cancelled": True},
        "GHOST-9999": {"dep_delay_min": 5},        # not in instance -> skipped
        "DL1-0800-bad": "not a dict",
    }, fns, by="import")
    assert n == 2
    assert set(store.facts_for("i1")) == {"DL1-0800", "DL2-0900"}


def test_corrupt_store_fails_closed(store):
    # H14 (corrected): a corrupt/unreadable EXISTING store must NOT silently
    # degrade to {} — the next write would then erase every other instance's
    # facts. It must fail closed so the operator inspects the file.
    store.OPS_LOG_PATH.write_text("{not json", encoding="utf-8")
    with pytest.raises(store.OpsLogError):
        store.facts_for("i1")


# ----------------------------------------------------------------- HTTP

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    from webapp import users, ops_log, registry
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(ops_log, "OPS_LOG_PATH", tmp_path / "ops_log.json")
    users.ensure_db()

    # a minimal instance visible to the admin, with a real payload on disk
    inst = {"id": "opsinst", "label": "opsinst", "source": "upload"}
    payload_path = tmp_path / "opsinst.json"
    payload_path.write_text(__import__("json").dumps(PAYLOAD),
                            encoding="utf-8")
    monkeypatch.setattr(registry, "get_instance_for",
                        lambda iid, user: inst if iid == "opsinst" else None)
    monkeypatch.setattr(registry, "instance_payload_path",
                        lambda i: payload_path)
    monkeypatch.setattr(registry, "load_payload",
                        lambda p: __import__("json").loads(
                            Path(p).read_text(encoding="utf-8")))

    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_ops_report_and_fact_roundtrip(client):
    # empty day: nothing observed yet -> completion is N/A, not a fake 100% (H11)
    r = client.get("/api/ops/opsinst")
    assert r.status_code == 200
    assert r.json()["summary"]["completionFactorPct"] is None

    # record a cancellation + a delay
    assert client.post("/api/ops/opsinst/fact",
                       json={"flightNumber": "DL1-0800", "cancelled": True,
                             "cause": "weather"}).status_code == 200
    assert client.post("/api/ops/opsinst/fact",
                       json={"flightNumber": "DL2-0900",
                             "dep_delay_min": 45}).status_code == 200
    s = client.get("/api/ops/opsinst").json()["summary"]
    assert s["cancelled"] == 1 and s["delayed"] == 1
    assert s["completionFactorPct"] == 75.0

    # unknown flight -> 400; unknown instance -> 404
    assert client.post("/api/ops/opsinst/fact",
                       json={"flightNumber": "ZZZ"}).status_code == 400
    assert client.get("/api/ops/other").status_code == 404

    # delete reverts, clear empties
    assert client.delete("/api/ops/opsinst/fact/DL1-0800").json()["removed"]
    assert client.post("/api/ops/opsinst/clear").json()["cleared"] == 1
    assert client.get("/api/ops/opsinst").json()["summary"]["affected"] == 0


# --- Аудит-2 (P1-05b): ключ факта «номер+день» -------------------------------


def _two_day_payload():
    return {
        "flights": [
            {"flightNumber": "DL555-1215", "departureAirport": "AAA",
             "arrivalAirport": "BBB",
             "departureUTCDateTime": "2024-08-01T12:15:00",
             "arrivalUTCDateTime": "2024-08-01T14:15:00"},
            {"flightNumber": "DL555-1215", "departureAirport": "AAA",
             "arrivalAirport": "BBB",
             "departureUTCDateTime": "2024-08-02T12:15:00",
             "arrivalUTCDateTime": "2024-08-02T14:15:00"},
            {"flightNumber": "DL777-0900", "departureAirport": "BBB",
             "arrivalAirport": "AAA",
             "departureUTCDateTime": "2024-08-01T09:00:00",
             "arrivalUTCDateTime": "2024-08-01T11:00:00"},
        ],
    }


def test_fact_keys_repeated_number_gets_day_suffix():
    from webapp import ops_log
    keys = ops_log.payload_fact_keys(_two_day_payload())
    assert "DL555-1215@2024-08-01" in keys
    assert "DL555-1215@2024-08-02" in keys
    assert "DL777-0900" in keys                      # уникальный — без даты
    assert keys["DL777-0900"]["ambiguous"] is False


def test_fact_key_for_requires_day_on_ambiguous_number():
    from webapp import ops_log
    p = _two_day_payload()
    with pytest.raises(ops_log.OpsLogError, match="several times"):
        ops_log.fact_key_for(p, "DL555-1215")
    assert ops_log.fact_key_for(p, "DL555-1215", "2024-08-01") == \
        "DL555-1215@2024-08-01"
    assert ops_log.fact_key_for(p, "DL777-0900") == "DL777-0900"
    with pytest.raises(ops_log.OpsLogError):
        ops_log.fact_key_for(p, "DL555-1215", "2024-08-09")
    with pytest.raises(ops_log.OpsLogError):
        ops_log.fact_key_for(p, "NOPE-0000")


def test_cancel_one_date_does_not_cancel_the_other():
    from webapp import plan_fact
    # Ядро находки P1-05b: один cancel-факт гасил ОБА календарных occurrence.
    p = _two_day_payload()
    facts = {"DL555-1215@2024-08-01": {"cancelled": True}}
    rows = plan_fact.deviations(p, facts)
    st = {(r["flightNumber"], r["serviceDay"]): r["status"] for r in rows}
    assert st[("DL555-1215", "2024-08-01")] == "cancelled"
    assert st[("DL555-1215", "2024-08-02")] == "scheduled"
    s = plan_fact.summary(p, facts)
    assert s["cancelled"] == 1


def test_unique_number_legacy_fact_key_still_applies():
    from webapp import plan_fact
    # Обратная совместимость: для уникального номера ключ = сам номер,
    # существующие хранилища работают без миграции.
    p = _two_day_payload()
    facts = {"DL777-0900": {"cancelled": True}}
    rows = plan_fact.deviations(p, facts)
    st = {(r["flightNumber"], r["serviceDay"]): r["status"] for r in rows}
    assert st[("DL777-0900", "2024-08-01")] == "cancelled"


# --- P0-2 (аудит документации 2026-07-20): внутриминутная коллизия ----------


def _intraminute_payload():
    """Два вхождения DL555-1215 вылетают в ОДНУ И ТУ ЖЕ минуту — самый
    точный ключ 'номер@день-время' их не различает."""
    return {
        "flights": [
            {"flightNumber": "DL555-1215", "departureAirport": "AAA",
             "arrivalAirport": "BBB",
             "departureUTCDateTime": "2024-08-01T12:15:00",
             "arrivalUTCDateTime": "2024-08-01T14:15:00"},
            {"flightNumber": "DL555-1215", "departureAirport": "AAA",
             "arrivalAirport": "CCC",
             "departureUTCDateTime": "2024-08-01T12:15:00",
             "arrivalUTCDateTime": "2024-08-01T15:00:00"},
            {"flightNumber": "DL777-0900", "departureAirport": "BBB",
             "arrivalAirport": "AAA",
             "departureUTCDateTime": "2024-08-01T09:00:00",
             "arrivalUTCDateTime": "2024-08-01T11:00:00"},
        ],
    }


_COLL_KEY = "DL555-1215@2024-08-01T12:15"


def test_intraminute_collision_marks_only_that_key_ambiguous():
    from webapp import ops_log
    keys = ops_log.payload_fact_keys(_intraminute_payload())
    assert keys[_COLL_KEY]["ambiguous"] is True
    assert keys[_COLL_KEY]["addr"] is None
    assert keys["DL777-0900"]["ambiguous"] is False
    # суженная семантика: обычный повтор по датам ambiguous НЕ ставит
    two = ops_log.payload_fact_keys(_two_day_payload())
    assert two["DL555-1215@2024-08-01"]["ambiguous"] is False
    assert two["DL555-1215@2024-08-02"]["ambiguous"] is False


def test_fact_key_for_rejects_intraminute_collision():
    from webapp import ops_log
    p = _intraminute_payload()
    with pytest.raises(ops_log.OpsLogError, match="same minute"):
        ops_log.fact_key_for(p, "DL555-1215", "2024-08-01")
    with pytest.raises(ops_log.OpsLogError, match="same minute"):
        ops_log.fact_key_for(p, "DL555-1215", "2024-08-01T12:15")
    with pytest.raises(ops_log.OpsLogError, match="several times"):
        ops_log.fact_key_for(p, "DL555-1215")
    assert ops_log.fact_key_for(p, "DL777-0900") == "DL777-0900"


def test_keys_by_flight_returns_none_for_collision():
    from webapp import ops_log
    keys = ops_log.keys_by_flight(_intraminute_payload())
    assert keys[0] is None and keys[1] is None
    assert keys[2] == "DL777-0900"


def test_legacy_intraminute_fact_ignored_with_counter():
    from webapp import plan_fact
    # Легаси-факт под коллизионным ключом раньше ложился на ОБЕ строки
    # (две отмены от одного факта) — теперь игнорируется со счётчиком.
    p = _intraminute_payload()
    facts = {_COLL_KEY: {"cancelled": True},
             "DL777-0900": {"cancelled": True}}
    rows = plan_fact.deviations(p, facts)
    dl555 = [r["status"] for r in rows if r["flightNumber"] == "DL555-1215"]
    assert dl555 == ["scheduled", "scheduled"]
    s = plan_fact.summary(p, facts)
    assert s["cancelled"] == 1                      # только DL777
    assert s["reported"] == 1
    assert s["unaddressableFacts"] == 1


@pytest.fixture()
def client_intraminute(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    from webapp import users, ops_log, registry
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(ops_log, "OPS_LOG_PATH", tmp_path / "ops_log.json")
    users.ensure_db()
    inst = {"id": "collinst", "label": "collinst", "source": "upload"}
    payload_path = tmp_path / "collinst.json"
    payload_path.write_text(
        __import__("json").dumps(_intraminute_payload()), encoding="utf-8")
    monkeypatch.setattr(registry, "get_instance_for",
                        lambda iid, user: inst if iid == "collinst" else None)
    monkeypatch.setattr(registry, "instance_payload_path",
                        lambda i: payload_path)
    monkeypatch.setattr(registry, "load_payload",
                        lambda p: __import__("json").loads(
                            Path(p).read_text(encoding="utf-8")))
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_http_post_intraminute_fact_rejected(client_intraminute):
    r = client_intraminute.post(
        "/api/ops/collinst/fact",
        json={"flightNumber": "DL555-1215", "serviceDay": "2024-08-01T12:15",
              "cancelled": True})
    assert r.status_code == 400
    assert "same minute" in r.json()["detail"]
    # уникальный номер по-прежнему принимается
    assert client_intraminute.post(
        "/api/ops/collinst/fact",
        json={"flightNumber": "DL777-0900", "cancelled": True}
    ).status_code == 200


def test_http_delete_legacy_intraminute_fact(client_intraminute, tmp_path):
    from webapp import ops_log
    # положить легаси-факт напрямую в хранилище (ввод теперь запрещён)
    ops_log.OPS_LOG_PATH.write_text(
        __import__("json").dumps(
            {"collinst": {_COLL_KEY: {"cancelled": True}}}),
        encoding="utf-8")
    s = client_intraminute.get("/api/ops/collinst").json()["summary"]
    assert s["unaddressableFacts"] == 1
    r = client_intraminute.delete(
        "/api/ops/collinst/fact/DL555-1215",
        params={"day": "2024-08-01T12:15"})
    assert r.status_code == 200
    assert r.json()["removed"] is True and r.json()["legacy_key"] is True
    s2 = client_intraminute.get("/api/ops/collinst").json()["summary"]
    assert s2["unaddressableFacts"] == 0
