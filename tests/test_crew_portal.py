"""Crew self-service portal (#18): links CRUD + validation, personal
schedule (board moves applied), clearances filter, vacation bid upsert,
acknowledgement staleness, and the API tenancy model (a crew LINK grants
cabinet-only access to an otherwise invisible instance; everything else
stays owner-or-admin 404)."""
import importlib

import pytest
from fastapi.testclient import TestClient

from webapp import crew_portal as cp
from webapp import registry
from webapp import roster_board as rb
from webapp import vacation_bids as vb
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL
CREW = "crew.member@example.com"


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """Isolate every store the portal touches."""
    monkeypatch.setattr(cp, "LINKS_PATH", tmp_path / "links.json")
    monkeypatch.setattr(cp, "ACKS_PATH", tmp_path / "acks.json")
    monkeypatch.setattr(vb, "BIDS_PATH", tmp_path / "bids.json")
    monkeypatch.setattr(rb, "EDITS_PATH", tmp_path / "edits.json")
    from webapp import qualifications as q
    monkeypatch.setattr(q, "QUALS_PATH", tmp_path / "quals.json")
    yield tmp_path


def _inst():
    return registry.get_instance("gerad-1")


def _busiest_emp(inst):
    pairings, _, _ = rb._resolve(inst)
    counts = {}
    for p in pairings:
        e = p["solver_employee"]
        if e:
            counts[e] = counts.get(e, 0) + 1
    return max(counts, key=counts.get)


# ------------------------------------------------------------------- links

def test_set_link_validates(iso):
    inst = _inst()
    with pytest.raises(cp.CrewPortalError):
        cp.set_link("gerad-1", inst, "not-an-email", "EMP001")
    with pytest.raises(cp.CrewPortalError):
        cp.set_link("gerad-1", inst, CREW, "NOPE999")


def test_link_roundtrip_and_case_folding(iso):
    inst = _inst()
    cp.set_link("gerad-1", inst, "Crew.Member@Example.COM", "EMP001")
    assert cp.linked_employee("gerad-1", CREW) == "EMP001"
    links = cp.links_for_email(CREW.upper())
    assert links and links[0]["instance"] == "gerad-1"
    assert links[0]["employee_id"] == "EMP001"
    # re-link replaces (one employee per email per instance)
    cp.set_link("gerad-1", inst, CREW, "EMP002")
    assert cp.linked_employee("gerad-1", CREW) == "EMP002"
    assert cp.remove_link("gerad-1", CREW) is True
    assert cp.remove_link("gerad-1", CREW) is False
    assert cp.linked_employee("gerad-1", CREW) is None


def test_links_skip_vanished_instances(iso, monkeypatch):
    inst = _inst()
    cp.set_link("gerad-1", inst, CREW, "EMP001")
    real = registry.get_instance
    monkeypatch.setattr(
        cp.registry, "get_instance",
        lambda i: None if i == "gerad-1" else real(i))
    assert cp.links_for_email(CREW) == []


# ---------------------------------------------------------------- schedule

def test_cabinet_schedule_matches_solver_assignment(iso):
    inst = _inst()
    emp = _busiest_emp(inst)
    cab = cp.cabinet("gerad-1", inst, emp)
    assert cab["me"]["employee_id"] == emp
    s = cab["schedule"]
    assert s["n_pairings"] >= 1 and s["block_hours"] > 0
    # every pairing in my schedule is really assigned to me by the solver
    pairings, _, _ = rb._resolve(inst)
    solver_mine = {p["pid"] for p in pairings if p["solver_employee"] == emp}
    assert {p["pid"] for p in s["pairings"]} == solver_mine
    # chronological order
    deps = [p["dep_utc"] for p in s["pairings"]]
    assert deps == sorted(deps)


def test_board_move_is_reflected_in_cabinet(iso):
    """The planner reassigns one of my pairings on the board -> it must
    disappear from MY cabinet and the roster signature must change."""
    inst = _inst()
    emp = _busiest_emp(inst)
    before = cp.cabinet("gerad-1", inst, emp)
    pid = before["schedule"]["pairings"][0]["pid"]
    sig_before = before["roster_sig"]
    rb.set_move("gerad-1", inst, pid, "EMP001" if emp != "EMP001"
                else "EMP002")
    after = cp.cabinet("gerad-1", inst, emp)
    assert pid not in {p["pid"] for p in after["schedule"]["pairings"]}
    assert after["schedule"]["n_pairings"] == \
        before["schedule"]["n_pairings"] - 1
    assert after["roster_sig"] != sig_before


def test_cabinet_unknown_employee_raises(iso):
    with pytest.raises(cp.CrewPortalError):
        cp.cabinet("gerad-1", _inst(), "NOPE999")


def test_cabinet_without_roster(iso, monkeypatch):
    """No stage-2 artifact: cabinet still answers with identity + vacation,
    schedule honestly absent."""
    monkeypatch.setattr(rb, "_resolve", lambda inst: (None, {}, []))
    cab = cp.cabinet("gerad-1", _inst(), "EMP001")
    assert cab["roster_available"] is False
    assert cab["schedule"] is None and cab["roster_sig"] is None


# ---------------------------------------------------------------- vacation

def test_vacation_upsert_keeps_others_and_invalidates_award(iso):
    inst = _inst()
    vb.submit_bids("gerad-1", inst, [
        {"employee_id": "EMP001", "windows": [["2000-01-03", "2000-01-05"]]},
        {"employee_id": "EMP002", "windows": [["2000-01-10", "2000-01-12"]]},
    ])
    meta = vb.meta_for("gerad-1")
    meta["award"] = {"status": "OPTIMAL"}          # pretend an award exists
    vb._store("gerad-1", meta)

    cp.submit_my_windows("gerad-1", inst, "EMP002",
                         [["2000-01-20", "2000-01-24"]])
    meta = vb.meta_for("gerad-1")
    by_emp = {b["employee_id"]: b for b in meta["bids"]}
    assert by_emp["EMP001"]["windows"] == [["2000-01-03", "2000-01-05"]]
    assert by_emp["EMP002"]["windows"] == [["2000-01-20", "2000-01-24"]]
    assert meta["award"] is None                    # stale award dropped
    # seniority filled from the payload, not client-supplied
    assert by_emp["EMP002"]["seniority"] == \
        vb._employee_seniority(inst)["EMP002"]


def test_vacation_submit_validates_windows(iso):
    inst = _inst()
    with pytest.raises(vb.VacationBidError):
        cp.submit_my_windows("gerad-1", inst, "EMP001", [])
    with pytest.raises(vb.VacationBidError):
        cp.submit_my_windows("gerad-1", inst, "EMP001",
                             [["2000-01-10", "2000-01-01"]])   # end < start


def test_vacation_withdraw(iso):
    inst = _inst()
    cp.submit_my_windows("gerad-1", inst, "EMP001",
                         [["2000-01-03", "2000-01-05"]])
    assert cp.withdraw_my_windows("gerad-1", "EMP001") is True
    assert cp.withdraw_my_windows("gerad-1", "EMP001") is False
    assert vb.meta_for("gerad-1")["bids"] == []


def test_vacation_concurrent_upserts_lose_nothing(iso):
    """Two crew filing concurrently must both land — the unlocked merge used
    to drop an uninvolved committed bid (audit 2026-07-15)."""
    import threading
    inst = _inst()
    cp.submit_my_windows("gerad-1", inst, "EMP001",
                         [["2000-01-03", "2000-01-05"]])
    errs = []

    def file_bid(emp, day):
        try:
            cp.submit_my_windows("gerad-1", inst, emp,
                                 [[f"2000-01-{day:02d}",
                                   f"2000-01-{day + 2:02d}"]])
        except Exception as exc:              # noqa: BLE001
            errs.append(exc)

    threads = [threading.Thread(target=file_bid, args=(f"EMP{i:03d}", 6 + i))
               for i in range(2, 12)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert not errs
    got = {b["employee_id"] for b in vb.meta_for("gerad-1")["bids"]}
    assert got == {"EMP001"} | {f"EMP{i:03d}" for i in range(2, 12)}


def test_vacation_upsert_respects_max_bids(iso, monkeypatch):
    """The MAX_BIDS solver-size guardrail survives per-employee upserts."""
    inst = _inst()
    monkeypatch.setattr(vb, "MAX_BIDS", 2)
    cp.submit_my_windows("gerad-1", inst, "EMP001",
                         [["2000-01-03", "2000-01-05"]])
    cp.submit_my_windows("gerad-1", inst, "EMP002",
                         [["2000-01-10", "2000-01-12"]])
    with pytest.raises(vb.VacationBidError):
        cp.submit_my_windows("gerad-1", inst, "EMP003",
                             [["2000-01-20", "2000-01-22"]])
    # re-submitting an EXISTING bid is an update, not growth — still allowed
    cp.submit_my_windows("gerad-1", inst, "EMP001",
                         [["2000-01-06", "2000-01-08"]])


# -------------------------------------------------------------------- acks

def test_ack_and_staleness(iso):
    inst = _inst()
    emp = _busiest_emp(inst)
    cab = cp.cabinet("gerad-1", inst, emp)
    assert cab["ack"] == {"acknowledged": False, "at": None, "stale": False}
    cp.acknowledge("gerad-1", emp, CREW, cab["roster_sig"])
    cab2 = cp.cabinet("gerad-1", inst, emp)
    assert cab2["ack"]["acknowledged"] is True
    assert cab2["ack"]["stale"] is False
    # aware UTC, never naive server-local time (audit 2026-07-15)
    assert cab2["ack"]["at"].endswith("+00:00")
    # a reassignment of one of my pairings makes the ack stale
    pid = cab2["schedule"]["pairings"][0]["pid"]
    rb.set_move("gerad-1", inst, pid, "EMP001" if emp != "EMP001"
                else "EMP002")
    cab3 = cp.cabinet("gerad-1", inst, emp)
    assert cab3["ack"]["stale"] is True


# --------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(cp, "LINKS_PATH", tmp_path / "links.json")
    monkeypatch.setattr(cp, "ACKS_PATH", tmp_path / "acks.json")
    monkeypatch.setattr(vb, "BIDS_PATH", tmp_path / "bids.json")
    monkeypatch.setattr(rb, "EDITS_PATH", tmp_path / "edits.json")
    from webapp import qualifications as q
    monkeypatch.setattr(q, "QUALS_PATH", tmp_path / "quals.json")
    users.ensure_db()
    users.add(CREW, "user", by=ADMIN)      # the crew member's account
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def _as(monkeypatch, email):
    monkeypatch.setenv("CREW_DEV_USER", email)


def test_api_links_management_admin_only_or_owner(client, monkeypatch):
    # admin links the crew member
    r = client.put("/api/crew-links/gerad-1",
                   json={"email": CREW, "employee_id": "EMP004"})
    assert r.status_code == 200, r.text
    r2 = client.get("/api/crew-links/gerad-1")
    assert r2.json()["links"] == {CREW: "EMP004"}
    assert any(e["id"] == "EMP004" for e in r2.json()["employees"])
    # the crew member (not owner, not admin) cannot manage links: 404
    _as(monkeypatch, CREW)
    r3 = client.get("/api/crew-links/gerad-1")
    assert r3.status_code == 404
    r4 = client.put("/api/crew-links/gerad-1",
                    json={"email": CREW, "employee_id": "EMP001"})
    assert r4.status_code == 404


def test_api_cabinet_link_grants_cabinet_only(client, monkeypatch):
    client.put("/api/crew-links/gerad-1",
               json={"email": CREW, "employee_id": "EMP004"})
    _as(monkeypatch, CREW)
    # my links
    r = client.get("/api/cabinet")
    assert r.status_code == 200
    assert r.json()["links"][0]["employee_id"] == "EMP004"
    # my cabinet works even though the instance is not mine
    r2 = client.get("/api/cabinet/gerad-1")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["me"]["employee_id"] == "EMP004"
    assert body["schedule"]["n_pairings"] >= 1
    # ...but the link does NOT open the rest of the instance
    assert client.get("/api/board/gerad-1").status_code == 404
    assert client.get("/api/costing/gerad-1").status_code == 404
    assert client.get("/api/base-balance/gerad-1").status_code == 404
    # and a crew member cannot preview someone else via ?employee=
    r3 = client.get("/api/cabinet/gerad-1", params={"employee": "EMP001"})
    assert r3.status_code == 200
    assert r3.json()["me"]["employee_id"] == "EMP004"   # override ignored


def test_api_cabinet_unlinked_user_gets_404(client, monkeypatch):
    _as(monkeypatch, CREW)
    assert client.get("/api/cabinet/gerad-1").status_code == 404
    assert client.get("/api/cabinet").json()["links"] == []


def test_api_admin_preview_via_employee_param(client):
    r = client.get("/api/cabinet/gerad-1", params={"employee": "EMP004"})
    assert r.status_code == 200
    assert r.json()["me"]["employee_id"] == "EMP004"
    # admin without link and without ?employee= -> 404 (no crew link)
    assert client.get("/api/cabinet/gerad-1").status_code == 404


def test_api_preview_rejects_bogus_employee_on_every_verb(client):
    """?employee= must name a real crew member — the ack write used to
    store junk ids (audit 2026-07-15)."""
    for method, path, kw in [
        ("get", "/api/cabinet/gerad-1", {}),
        ("post", "/api/cabinet/gerad-1/ack", {"json": {"sig": "x"}}),
        ("put", "/api/cabinet/gerad-1/vacation",
         {"json": {"windows": [["2000-01-10", "2000-01-12"]]}}),
        ("delete", "/api/cabinet/gerad-1/vacation", {}),
    ]:
        r = getattr(client, method)(path, params={"employee": "BOGUS"}, **kw)
        assert r.status_code == 422, (method, path, r.status_code)


def test_api_vacation_self_service_flow(client, monkeypatch):
    client.put("/api/crew-links/gerad-1",
               json={"email": CREW, "employee_id": "EMP004"})
    _as(monkeypatch, CREW)
    r = client.put("/api/cabinet/gerad-1/vacation",
                   json={"windows": [["2000-01-10", "2000-01-14"]]})
    assert r.status_code == 200, r.text
    assert r.json()["bid"]["employee_id"] == "EMP004"
    cab = client.get("/api/cabinet/gerad-1").json()
    assert cab["vacation"]["windows"] == [["2000-01-10", "2000-01-14"]]
    r2 = client.put("/api/cabinet/gerad-1/vacation",
                    json={"windows": [["2000-01-10", "2000-03-14"]]})
    assert r2.status_code == 422                     # window too long
    assert client.delete(
        "/api/cabinet/gerad-1/vacation").status_code == 200
    assert client.delete(
        "/api/cabinet/gerad-1/vacation").status_code == 404


def test_api_ack_flow(client, monkeypatch):
    client.put("/api/crew-links/gerad-1",
               json={"email": CREW, "employee_id": "EMP004"})
    _as(monkeypatch, CREW)
    cab = client.get("/api/cabinet/gerad-1").json()
    r = client.post("/api/cabinet/gerad-1/ack",
                    json={"sig": cab["roster_sig"]})
    assert r.status_code == 200
    cab2 = client.get("/api/cabinet/gerad-1").json()
    assert cab2["ack"]["acknowledged"] is True
    assert cab2["ack"]["stale"] is False
    assert client.post("/api/cabinet/gerad-1/ack",
                       json={}).status_code == 422


def test_api_missing_instance_404(client):
    assert client.get("/api/cabinet/upload-nope").status_code == 404
    assert client.put("/api/crew-links/upload-nope",
                      json={"email": CREW,
                            "employee_id": "EMP001"}).status_code == 404
