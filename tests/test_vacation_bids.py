"""Vacation bidding (#14): validation, seniority-weighted CP-SAT award under
a daily quota, apply into unavailableDays of a derived instance, tenancy."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from webapp import registry, vacation_bids
from _stand_identity import ADMIN_EMAIL

EMAIL = ADMIN_EMAIL


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(vacation_bids, "BIDS_PATH", tmp_path / "vac.json")
    monkeypatch.setattr(registry, "OWNERSHIP_PATH", tmp_path / "own.json")
    monkeypatch.setattr(registry, "LLM_SCOPE_PATH", tmp_path / "scope.json")
    # Каталог выгрузок — тот, что выберет код (слаг с отпечатком после
    # подмены реестра владения), а не устаревший короткий: иначе уборка
    # смотрит не в ту папку, и производные наборы vacation-* оседают в
    # data/uploads с каждым прогоном (см. test_qualifications, 2026-09-04).
    updir = registry.UPLOADS_DIR / registry.user_slug(EMAIL)
    before = set(updir.glob("vacation-*.json")) if updir.exists() else set()
    yield tmp_path
    if updir.exists():
        for p in updir.glob("vacation-*.json"):
            if p not in before:
                p.unlink(missing_ok=True)


def _base():
    return registry.get_instance("gerad-1")


def test_demo_bids_have_distinct_seniority_and_windows(iso):
    inst = _base()
    bids = vacation_bids.demo_bids(inst, want=8)
    assert len(bids) == 8
    sen = [b["seniority"] for b in bids]
    assert sen == sorted(sen) and len(set(sen)) == 8   # distinct 1..8
    assert all(len(b["windows"]) == 2 for b in bids)
    # windows are valid ISO ranges inside the payload's month
    for b in bids:
        for w in b["windows"]:
            assert len(vacation_bids._window_days(w)) >= 1


def test_priority_is_finegrained_not_bucketed(iso):
    # audit 2026-07-15: demo seniorities 1..12 must map to DISTINCT priorities
    # (a coarse 10-band weight collapsed them and hid seniority order).
    ps = [vacation_bids._priority(s) for s in range(1, 13)]
    assert len(set(ps)) == 12
    assert vacation_bids._priority(1) > vacation_bids._priority(12)


def test_demo_award_honors_seniority_on_contended_day(iso):
    # the showcase path: demo bids all contend for a popular window; with
    # quota 1 the MOST SENIOR (seniority 1) must win it.
    inst = _base()
    bids = vacation_bids.demo_bids(inst, want=6)
    vacation_bids.submit_bids("gerad-1", inst, bids, EMAIL)
    vacation_bids.set_quota("gerad-1", 1, EMAIL)
    res = vacation_bids.award("gerad-1", inst)
    granted = {g["employee_id"]: g for g in res["granted"]}
    top = min(bids, key=lambda b: b["seniority"])       # seniority 1
    # the most senior got their FIRST choice (the contended popular window)
    assert granted[top["employee_id"]]["choice_rank"] == 1


def test_validate_rejects_bad_bids(iso):
    inst = _base()
    known = set(vacation_bids._employee_seniority(inst))
    with pytest.raises(vacation_bids.VacationBidError):
        vacation_bids.validate_bids([{"employee_id": "NOPE",
                                      "windows": [["2000-01-01", "2000-01-03"]]}],
                                     known)
    with pytest.raises(vacation_bids.VacationBidError):    # end before start
        vacation_bids.validate_bids([{"employee_id": next(iter(known)),
                                      "windows": [["2000-01-05", "2000-01-01"]]}],
                                    known)
    with pytest.raises(vacation_bids.VacationBidError):    # no windows
        vacation_bids.validate_bids([{"employee_id": next(iter(known)),
                                      "windows": []}], known)


def test_award_respects_quota_and_seniority(iso):
    inst = _base()
    ids = list(vacation_bids._employee_seniority(inst))[:3]
    # three employees all want the SAME single day; quota 1 -> only one wins
    win = [["2000-01-10", "2000-01-10"]]
    bids = [
        {"employee_id": ids[0], "seniority": 300, "windows": win},   # junior
        {"employee_id": ids[1], "seniority": 5, "windows": win},     # senior
        {"employee_id": ids[2], "seniority": 800, "windows": win},   # junior
    ]
    vacation_bids.submit_bids("gerad-1", inst, bids, EMAIL)
    vacation_bids.set_quota("gerad-1", 1, EMAIL)
    res = vacation_bids.award("gerad-1", inst)
    assert res["status"] in ("OPTIMAL", "FEASIBLE")
    assert len(res["granted"]) == 1                 # quota binds
    assert res["granted"][0]["employee_id"] == ids[1]   # most senior wins
    assert res["per_day_load"]["2000-01-10"] == 1
    assert len(res["denied"]) == 2


def test_award_grants_all_when_quota_allows(iso):
    inst = _base()
    ids = list(vacation_bids._employee_seniority(inst))[:3]
    win = [["2000-01-10", "2000-01-12"]]
    bids = [{"employee_id": e, "seniority": 100 * (i + 1), "windows": win}
            for i, e in enumerate(ids)]
    vacation_bids.submit_bids("gerad-1", inst, bids, EMAIL)
    vacation_bids.set_quota("gerad-1", 3, EMAIL)
    res = vacation_bids.award("gerad-1", inst)
    assert len(res["granted"]) == 3 and not res["denied"]
    assert res["per_day_load"]["2000-01-11"] == 3


def test_second_choice_used_when_first_full(iso):
    inst = _base()
    ids = list(vacation_bids._employee_seniority(inst))[:2]
    # both want day 10 first; junior has a viable second choice day 20
    bids = [
        {"employee_id": ids[0], "seniority": 5,
         "windows": [["2000-01-10", "2000-01-10"]]},
        {"employee_id": ids[1], "seniority": 400,
         "windows": [["2000-01-10", "2000-01-10"], ["2000-01-20", "2000-01-20"]]},
    ]
    vacation_bids.submit_bids("gerad-1", inst, bids, EMAIL)
    vacation_bids.set_quota("gerad-1", 1, EMAIL)
    res = vacation_bids.award("gerad-1", inst)
    granted = {g["employee_id"]: g for g in res["granted"]}
    assert granted[ids[0]]["window"] == ["2000-01-10", "2000-01-10"]
    assert granted[ids[1]]["window"] == ["2000-01-20", "2000-01-20"]  # 2nd
    assert granted[ids[1]]["choice_rank"] == 2


def test_apply_bakes_into_unavailable_days_of_derived_instance(iso):
    inst = _base()
    ids = list(vacation_bids._employee_seniority(inst))[:1]
    vacation_bids.submit_bids("gerad-1", inst, [
        {"employee_id": ids[0], "seniority": 5,
         "windows": [["2000-01-10", "2000-01-12"]]}], EMAIL)
    vacation_bids.set_quota("gerad-1", 1, EMAIL)
    vacation_bids.award("gerad-1", inst)
    created = vacation_bids.apply_award("gerad-1", inst, EMAIL)
    new = registry.get_instance(created["id"])
    assert new is not None
    payload = registry.load_payload(registry.instance_payload_path(new))
    emp = next(e for e in payload["employees"] if e["id"] == ids[0])
    assert set(emp["unavailableDays"]) >= {
        "2000-01-10", "2000-01-11", "2000-01-12"}
    # the BASE payload cache must NOT be mutated (deep copy)
    base_payload = registry.load_payload(registry.instance_payload_path(inst))
    base_emp = next(e for e in base_payload["employees"] if e["id"] == ids[0])
    assert base_emp["unavailableDays"] == []


def test_quota_counts_baseline_unavailability(iso, monkeypatch):
    # audit 2026-07-15: pre-existing unavailableDays must count against the
    # daily quota, else "max off per day" is silently exceeded.
    inst = _base()
    ids = list(vacation_bids._employee_seniority(inst))[:1]
    # fake one OTHER employee already off on 2000-01-10
    real = vacation_bids._payload_employees

    def fake_emps(i):
        emps = [dict(e) for e in real(i)]
        emps[5]["unavailableDays"] = ["2000-01-10"]     # baseline off
        return emps
    monkeypatch.setattr(vacation_bids, "_payload_employees", fake_emps)
    vacation_bids.submit_bids("gerad-1", inst, [
        {"employee_id": ids[0], "seniority": 5,
         "windows": [["2000-01-10", "2000-01-10"]]}], EMAIL)
    vacation_bids.set_quota("gerad-1", 1, EMAIL)         # already full
    res = vacation_bids.award("gerad-1", inst)
    assert res["granted"] == []                          # no room left
    assert res["baseline_off_days"] == 1


def test_reapply_replaces_prior_derived_no_orphan(iso):
    inst = _base()
    ids = list(vacation_bids._employee_seniority(inst))[:1]
    vacation_bids.submit_bids("gerad-1", inst, [
        {"employee_id": ids[0], "seniority": 5,
         "windows": [["2000-01-10", "2000-01-10"]]}], EMAIL)
    vacation_bids.award("gerad-1", inst)
    first = vacation_bids.apply_award("gerad-1", inst, EMAIL)
    second = vacation_bids.apply_award("gerad-1", inst, EMAIL)
    assert first["id"] != second["id"]
    assert registry.get_instance(first["id"]) is None    # prior removed
    assert registry.get_instance(second["id"]) is not None


def test_award_requires_bids(iso):
    with pytest.raises(vacation_bids.VacationBidError):
        vacation_bids.award("gerad-1", _base())


def test_clear_removes_round_and_derived(iso):
    inst = _base()
    ids = list(vacation_bids._employee_seniority(inst))[:1]
    vacation_bids.submit_bids("gerad-1", inst, [
        {"employee_id": ids[0], "seniority": 5,
         "windows": [["2000-01-10", "2000-01-10"]]}], EMAIL)
    vacation_bids.award("gerad-1", inst)
    created = vacation_bids.apply_award("gerad-1", inst, EMAIL)
    assert vacation_bids.clear("gerad-1", EMAIL, is_admin=False) is True
    assert registry.get_instance(created["id"]) is None
    assert vacation_bids.meta_for("gerad-1")["bids"] == []


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
    assert client.post("/api/vacation/gerad-1/demo").status_code == 200
    assert client.put("/api/vacation/gerad-1/quota",
                      json={"quota": 2}).status_code == 200
    aw = client.post("/api/vacation/gerad-1/award")
    assert aw.status_code == 200, aw.text
    body = aw.json()
    assert body["status"] in ("OPTIMAL", "FEASIBLE")
    assert body["granted"]
    ap = client.post("/api/vacation/gerad-1/apply")
    assert ap.status_code == 201, ap.text
    new_id = ap.json()["id"]
    assert client.get(f"/api/instances/{new_id}").status_code == 200
    assert client.delete("/api/vacation/gerad-1").status_code == 200


def test_api_unknown_instance_404(client):
    assert client.get("/api/vacation/upload-nope").status_code == 404
    assert client.post("/api/vacation/upload-nope/award").status_code == 404
