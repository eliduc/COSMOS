"""Qualification / currency management (#5): record validation, compliance
calendar against the planning month, grounding non-compliant crew into
unavailableDays of a derived instance, tenancy."""
import importlib

import pytest
from fastapi.testclient import TestClient

from webapp import qualifications as q
from webapp import registry
from _stand_identity import ADMIN_EMAIL

EMAIL = ADMIN_EMAIL


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(q, "QUALS_PATH", tmp_path / "quals.json")
    monkeypatch.setattr(registry, "OWNERSHIP_PATH", tmp_path / "own.json")
    monkeypatch.setattr(registry, "LLM_SCOPE_PATH", tmp_path / "scope.json")
    # Каталог выгрузок пользователя — ТОТ, что выберет код (слаг с
    # отпечатком, после подмены реестра владения выше): прежде здесь стоял
    # устаревший короткий слаг, уборка смотрела не в ту папку, и каждый
    # прогон оставлял производные наборы currency-* в data/uploads — к
    # 2026-09-04 их накопилось 562, и перечень наборов стал стоить минуты.
    updir = registry.UPLOADS_DIR / registry.user_slug(EMAIL)
    before = set(updir.glob("currency-*.json")) if updir.exists() else set()
    yield tmp_path
    if updir.exists():
        for p in updir.glob("currency-*.json"):
            if p not in before:
                p.unlink(missing_ok=True)


def _base():
    return registry.get_instance("gerad-1")   # planning month 2000-01


def _ids(inst, k):
    return [str(e["id"]) for e in q._payload_employees(inst)][:k]


def test_month_detected_from_flights(iso):
    lo, hi = q._payload_date_range(_base())
    # the seed spans January into an early-February arrival (a red-eye)
    assert lo.isoformat() == "2000-01-01"
    assert hi.isoformat() == "2000-02-01"


def test_validate_rejects_bad_records(iso):
    inst = _base()
    known = {str(e["id"]) for e in q._payload_employees(inst)}
    eid = next(iter(known))
    with pytest.raises(q.QualificationError):        # unknown kind
        q.validate_records({eid: [{"kind": "nope", "code": "x",
                                   "expires": "2000-01-10"}]}, known)
    with pytest.raises(q.QualificationError):        # bad date
        q.validate_records({eid: [{"kind": "medical", "code": "Class-1",
                                   "expires": "not-a-date"}]}, known)
    with pytest.raises(q.QualificationError):        # unknown employee
        q.validate_records({"NOPE": [{"kind": "medical", "code": "C",
                                      "expires": "2000-01-10"}]}, known)
    with pytest.raises(q.QualificationError):        # recency non-int
        q.validate_records({eid: [{"kind": "recency", "code": "r",
                                   "required_events": "x",
                                   "recent_events": 1,
                                   "within_days": 90}]}, known)


def test_compliance_classifies_statuses(iso):
    inst = _base()
    e = _ids(inst, 4)
    recs = {
        e[0]: [{"kind": "medical", "code": "Class-1",
                "expires": "1999-12-01"}],           # expired before month
        e[1]: [{"kind": "licence", "code": "ATPL",
                "expires": "2000-01-15"}],           # expires during month
        e[2]: [{"kind": "visa", "code": "US",
                "expires": "2000-02-10"}],           # soon (within warn 30)
        e[3]: [{"kind": "recency", "code": "3-ldg-90d",
                "required_events": 3, "recent_events": 1,
                "within_days": 90}],                 # not current
    }
    q.submit_records("gerad-1", inst, recs, EMAIL)
    comp = q.compliance("gerad-1", inst)
    st = {row["employee_id"]: row for row in comp["employees"]}
    assert st[e[0]]["status"] == "expired"
    assert st[e[0]]["first_off"] == "2000-01-01"
    assert st[e[1]]["status"] == "expires_during"
    assert st[e[1]]["first_off"] == "2000-01-16"     # day after expiry
    assert st[e[2]]["status"] == "expires_soon"
    assert st[e[2]]["first_off"] is None             # not grounded this month
    assert st[e[3]]["status"] == "not_current"
    assert st[e[3]]["first_off"] == "2000-01-01"
    assert comp["blocking_employees"] == 3           # e0, e1, e3
    # alerts sorted worst-first
    assert comp["alerts"][0]["status"] in (
        "expired", "expires_during", "not_current")


def test_expiry_on_last_planning_day_is_not_blocking(iso):
    # audit 2026-07-15: a clearance expiring exactly on hi (the last flight
    # day, 2000-02-01) is valid through the whole window and must NOT be
    # blocking — else compliance() and apply_compliance() disagree.
    inst = _base()
    e = _ids(inst, 2)
    q.submit_records("gerad-1", inst, {
        e[0]: [{"kind": "medical", "code": "Class-1",
                "expires": "2000-02-01"}],           # == hi -> not blocking
        e[1]: [{"kind": "medical", "code": "Class-1",
                "expires": "2000-01-31"}],           # hi-1 -> grounds just hi
    }, EMAIL)
    comp = q.compliance("gerad-1", inst)
    st = {r["employee_id"]: r for r in comp["employees"]}
    assert st[e[0]]["status"] == "expires_soon"
    assert st[e[0]]["first_off"] is None
    assert st[e[1]]["status"] == "expires_during"
    assert st[e[1]]["first_off"] == "2000-02-01"
    assert comp["blocking_employees"] == 1           # only e1
    # apply grounds exactly the crew the calendar called blocking
    created = q.apply_compliance("gerad-1", inst, EMAIL)
    assert created["grounded_employees"] == 1
    new = registry.get_instance(created["id"])
    payload = registry.load_payload(registry.instance_payload_path(new))
    emp1 = next(x for x in payload["employees"] if x["id"] == e[1])
    assert emp1["unavailableDays"] == ["2000-02-01"]
    emp0 = next(x for x in payload["employees"] if x["id"] == e[0])
    assert emp0["unavailableDays"] == []


def test_warn_days_boundary(iso):
    inst = _base()
    e = _ids(inst, 1)
    q.submit_records("gerad-1", inst, {
        e[0]: [{"kind": "visa", "code": "US", "expires": "2000-03-15"}]}, EMAIL)
    # month ends 2000-01-31; 2000-03-15 is 44 days later -> OK at warn 30
    comp = q.compliance("gerad-1", inst)
    assert comp["employees"][0]["status"] == "ok"
    q.set_warn_days("gerad-1", 60, EMAIL)            # now within warn window
    comp = q.compliance("gerad-1", inst)
    assert comp["employees"][0]["status"] == "expires_soon"


def test_apply_grounds_noncompliant_into_unavailable_days(iso):
    inst = _base()
    e = _ids(inst, 2)
    q.submit_records("gerad-1", inst, {
        e[0]: [{"kind": "medical", "code": "Class-1",
                "expires": "2000-01-15"}],           # grounded 16..31
        e[1]: [{"kind": "licence", "code": "ATPL",
                "expires": "2001-01-01"}],           # OK, not grounded
    }, EMAIL)
    created = q.apply_compliance("gerad-1", inst, EMAIL)
    assert created["grounded_employees"] == 1
    new = registry.get_instance(created["id"])
    payload = registry.load_payload(registry.instance_payload_path(new))
    emp0 = next(x for x in payload["employees"] if x["id"] == e[0])
    assert "2000-01-16" in emp0["unavailableDays"]
    assert "2000-01-31" in emp0["unavailableDays"]
    assert "2000-01-15" not in emp0["unavailableDays"]   # valid through 15th
    emp1 = next(x for x in payload["employees"] if x["id"] == e[1])
    assert emp1["unavailableDays"] == []
    # base payload untouched (deep copy)
    base_payload = registry.load_payload(registry.instance_payload_path(inst))
    base0 = next(x for x in base_payload["employees"] if x["id"] == e[0])
    assert base0["unavailableDays"] == []


def test_apply_requires_noncompliant(iso):
    inst = _base()
    e = _ids(inst, 1)
    q.submit_records("gerad-1", inst, {
        e[0]: [{"kind": "licence", "code": "ATPL",
                "expires": "2002-01-01"}]}, EMAIL)    # all OK
    with pytest.raises(q.QualificationError):
        q.apply_compliance("gerad-1", inst, EMAIL)


def test_reapply_replaces_prior_derived(iso):
    inst = _base()
    e = _ids(inst, 1)
    q.submit_records("gerad-1", inst, {
        e[0]: [{"kind": "medical", "code": "Class-1",
                "expires": "2000-01-10"}]}, EMAIL)
    first = q.apply_compliance("gerad-1", inst, EMAIL)
    second = q.apply_compliance("gerad-1", inst, EMAIL)
    assert first["id"] != second["id"]
    assert registry.get_instance(first["id"]) is None
    assert registry.get_instance(second["id"]) is not None


def test_demo_records_produce_alerts(iso):
    inst = _base()
    recs = q.demo_records(inst, want=16)
    q.submit_records("gerad-1", inst, recs, EMAIL)
    comp = q.compliance("gerad-1", inst)
    # the demo deliberately seeds some red/amber/not-current
    assert comp["counts"]["expires_during"] + comp["counts"]["expired"] >= 1
    assert comp["counts"]["not_current"] >= 1
    assert comp["blocking_employees"] >= 1


def test_clear_removes_data_and_derived(iso):
    inst = _base()
    e = _ids(inst, 1)
    q.submit_records("gerad-1", inst, {
        e[0]: [{"kind": "medical", "code": "Class-1",
                "expires": "2000-01-10"}]}, EMAIL)
    created = q.apply_compliance("gerad-1", inst, EMAIL)
    assert q.clear("gerad-1", EMAIL, is_admin=False) is True
    assert registry.get_instance(created["id"]) is None
    assert q.meta_for("gerad-1")["records"] == {}


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
    assert client.post("/api/qualifications/gerad-1/demo").status_code == 200
    assert client.put("/api/qualifications/gerad-1/warn",
                      json={"warn_days": 45}).status_code == 200
    comp = client.get("/api/qualifications/gerad-1/compliance")
    assert comp.status_code == 200
    assert comp.json()["alerts"]
    ap = client.post("/api/qualifications/gerad-1/apply")
    assert ap.status_code == 201, ap.text
    new_id = ap.json()["id"]
    assert client.get(f"/api/instances/{new_id}").status_code == 200
    assert client.delete("/api/qualifications/gerad-1").status_code == 200


def test_api_unknown_instance_404(client):
    assert client.get(
        "/api/qualifications/upload-nope/compliance").status_code == 404
