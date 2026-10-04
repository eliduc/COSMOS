"""API tests for the pipeline web app (webapp.app), written strictly against
webapp/API-CONTRACT.md v1 (2026-07-12; tenancy update 2026-07-13).

Auth: the module fixture runs the app in the explicit DEV mode
(CREW_AUTH_MODE=dev, CREW_DEV_USER=<primary admin>) with the user catalog
and the ownership map redirected to a tmp directory — no shared-token
login, no writes to the real webapp/config. Tenancy itself is covered by
tests/test_tenancy.py; here the single dev identity just exercises the API.

The backend is developed in parallel with these tests: the module import is
deferred to a fixture so that pytest COLLECTION always succeeds, but if the
app is missing or broken the tests FAIL loudly (no skips — an absent backend
is a red result, not a green one).

No pipeline job execution is tested here: only registry, validation, config,
results, artifact whitelisting, and job-request rejection paths. The one
subprocess allowed is the SSIM import adapter (_ssim_to_payload.py on the
tiny data/samples/sample.ssim — sub-second).
"""
import copy
import json
from pathlib import Path

import pytest

from webapp import config_scope
from _stand_identity import ADMIN_EMAIL, ADMIN_SLUG, LEGACY_UPLOAD_ID

REPO_ROOT = Path(__file__).resolve().parent.parent
# Dev-mode identity for this module: the primary admin. Uploads land in the
# caller's per-user directory (email local part slug) and the instance id
# carries the same slug: upload-<user-slug>--<name-slug> (the '--' delimiter
# keeps the identity/name join injective, registry.UPLOAD_LABEL_SEP).
DEV_USER = ADMIN_EMAIL
USER_SLUG = ADMIN_SLUG
UPLOADS_DIR = REPO_ROOT / "data" / "uploads"
USER_UPLOADS = UPLOADS_DIR / USER_SLUG
UPLOAD_FILE = USER_UPLOADS / "pytest-tiny.json"
SCHEMA_FILE = REPO_ROOT / "webapp" / "schema" / "canonical_payload.schema.json"

# Minimal payload that satisfies webapp/schema/canonical_payload.schema.json:
# 2 airports, 1 employee, 1 flight, 1 assignment (all references consistent).
MINIMAL_VALID_PAYLOAD = {
    "formatVersion": "1",
    "airports": [
        {"code": "AAA"},
        {"code": "BBB"},
    ],
    "employees": [
        {"id": "E1", "homeAirport": "AAA", "skills": ["Pilot"]},
    ],
    "flights": [
        {
            "flightNumber": "F1",
            "departureAirport": "AAA",
            "departureUTCDateTime": "2026-07-01T06:00:00Z",
            "arrivalAirport": "BBB",
            "arrivalUTCDateTime": "2026-07-01T08:00:00Z",
        },
    ],
    "flightAssignments": [
        {"id": "F1-0", "flight": "F1", "indexInFlight": 0,
         "requiredSkill": "Pilot"},
    ],
}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    """FastAPI TestClient over webapp.app:app in DEV auth mode.

    Import is inside the fixture so collection works before the backend
    exists; a missing/broken app fails every test with a clear message.

    CREW_AUTH_MODE=dev + CREW_DEV_USER give every request the primary
    admin identity via request.state.user. The user catalog (users.db)
    and the tenancy ownership map (ownership.json) are module variables
    on purpose — redirected to a tmp directory so the tests never touch
    the real webapp/config.
    """
    try:
        from webapp import registry, users
        from webapp.app import app
    except Exception as exc:  # noqa: BLE001 — report the real import error
        pytest.fail(f"webapp.app is not importable: {exc!r}")
    from fastapi.testclient import TestClient

    cfg_dir = tmp_path_factory.mktemp("webapp-dev-auth")
    mp = pytest.MonkeyPatch()   # module scope: builtin monkeypatch is per-test
    mp.setenv("CREW_AUTH_MODE", "dev")
    mp.setenv("CREW_DEV_USER", DEV_USER)
    mp.delenv("CREW_ACCESS_AUD", raising=False)
    # Fresh tmp catalog: created on first open, seeds the default admins
    # (DEV_USER among them), so the dev identity authorizes immediately.
    mp.setattr(users, "USERS_DB_PATH", cfg_dir / "users.db")
    mp.setattr(registry, "OWNERSHIP_PATH", cfg_dir / "ownership.json")
    # P0-03: слаг включает digest email, КРОМЕ grandfathered-владельцев с
    # легаси-строками в карте (на реальном стенде у владельца стенда их 48).
    # Сеем одну, чтобы тесты работали с коротким историческим слагом, как прод.
    (cfg_dir / "ownership.json").write_text(json.dumps(
        {LEGACY_UPLOAD_ID: DEV_USER}), encoding="utf-8")
    # SSIM import writes the source bytes into the immutable raw zone —
    # redirect it to tmp so the suite never touches the real data/raw
    # (write-once would otherwise turn an interrupted run red next time).
    from webapp import raw_zone
    mp.setattr(raw_zone, "RAW_DIR", cfg_dir / "raw")
    try:
        with TestClient(app) as c:
            yield c
    finally:
        mp.undo()
        try:                   # drop the per-user dir if tests emptied it
            USER_UPLOADS.rmdir()
        except OSError:
            pass               # non-empty: real user data, keep it


def _instance_by_id(client, instance_id):
    resp = client.get("/api/instances")
    assert resp.status_code == 200
    instances = resp.json()
    matches = [i for i in instances if i["id"] == instance_id]
    return matches[0] if matches else None


# --- Instances registry -----------------------------------------------------

def test_instances_registry(client):
    resp = client.get("/api/instances")
    assert resp.status_code == 200
    instances = resp.json()
    assert isinstance(instances, list)
    # gerad-1..7 + bts-day at minimum
    assert len(instances) >= 8
    gerad1 = _instance_by_id(client, "gerad-1")
    assert gerad1 is not None, "gerad-1 missing from the registry"
    assert gerad1["source"] == "gerad"
    assert gerad1["legs"] == 1013
    assert gerad1["employees"] == 33
    assert gerad1["stage2_available"] is True


def test_bts_day_stage2_follows_catalog(client):
    # Stage-2 generalization: availability = «у метки есть ГОДНЫЙ каталог»
    # (внешний аудит R6, P1-15: сам факт существования файла годности не
    # даёт — нужен блок покрытия либо meta продвижения), and the instance
    # exposes the repo-relative catalog_path.
    bts = _instance_by_id(client, "bts-day")
    assert bts is not None, "bts-day missing from the registry"
    assert bts["source"] == "bts"
    # №50 этап 2: каталоги живут в каталоге данных, не в репозитории
    from webapp import registry as _reg
    has_pub = any(
        p.exists() and _reg._catalog_is_publishable(p)
        for p in (_reg.FINAL_REPORT / "gerad_colgen_selected_bts-day.json",
                  _reg.BEST_DIR / "gerad_best_catalog_bts-day.json"))
    assert bts["stage2_available"] is has_pub
    assert (bts["catalog_path"] is not None) is has_pub


# --- Schema endpoint --------------------------------------------------------

def test_schema_endpoint(client):
    resp = client.get("/api/schema")
    assert resp.status_code == 200
    schema = resp.json()
    assert schema["$id"] == "crew-demo/canonical-payload/v2"
    assert set(schema["required"]) == {
        "airports", "employees", "flights", "flightAssignments"}


# --- Upload validation ------------------------------------------------------

def test_upload_invalid_payload_missing_flights(client):
    payload = copy.deepcopy(MINIMAL_VALID_PAYLOAD)
    del payload["flights"]
    resp = client.post("/api/instances/upload",
                       json={"name": "pytest-invalid", "payload": payload})
    assert resp.status_code == 422
    body = resp.json()
    assert body["validation"]["ok"] is False
    errors = body["validation"]["errors"]
    assert isinstance(errors, list) and len(errors) >= 1
    assert all(isinstance(e, str) for e in errors)
    # Nothing must be saved on validation failure.
    assert not (USER_UPLOADS / "pytest-invalid.json").exists()


def test_upload_referential_check_unknown_airport(client):
    # Schema-valid payload whose flight departs from an airport that is not
    # in the airports list -> referential check must reject it with 422.
    payload = copy.deepcopy(MINIMAL_VALID_PAYLOAD)
    payload["flights"][0]["departureAirport"] = "ZZZ"
    resp = client.post("/api/instances/upload",
                       json={"name": "pytest-badref", "payload": payload})
    assert resp.status_code == 422
    body = resp.json()
    assert body["validation"]["ok"] is False
    assert len(body["validation"]["errors"]) >= 1
    assert not (USER_UPLOADS / "pytest-badref.json").exists()


def test_upload_valid_payload_roundtrip(client):
    # Sanity: the fixture payload really is schema-valid (so a 422 below
    # would point at the backend, not at a broken test payload).
    import jsonschema
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    jsonschema.validate(MINIMAL_VALID_PAYLOAD, schema)

    try:
        resp = client.post(
            "/api/instances/upload",
            json={"name": "pytest-tiny",
                  "payload": copy.deepcopy(MINIMAL_VALID_PAYLOAD)})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Tenancy: the id embeds the uploader's user slug.
        assert body["id"] == f"upload-{USER_SLUG}--pytest-tiny"
        assert body["validation"]["ok"] is True
        assert "stats" in body["validation"]
        # Saved file appears in the caller's per-user directory.
        assert UPLOAD_FILE.exists()
        # And the registry now lists it, annotated with its owner.
        uploaded = _instance_by_id(client, f"upload-{USER_SLUG}--pytest-tiny")
        assert uploaded is not None
        assert uploaded["source"] == "upload"
        assert uploaded["owner"] == DEV_USER
        assert uploaded["shared"] is False
        assert uploaded["legs"] == 1
        assert uploaded["employees"] == 1
        assert uploaded["slots"] == 1
    finally:
        UPLOAD_FILE.unlink(missing_ok=True)


# --- SSIM import --------------------------------------------------------------

SAMPLE_SSIM = REPO_ROOT / "data" / "samples" / "sample.ssim"


def test_ssim_import_roundtrip(client):
    from webapp import raw_zone
    assert SAMPLE_SSIM.is_file(), f"sample SSIM missing: {SAMPLE_SSIM}"
    ssim_text = SAMPLE_SSIM.read_text(encoding="utf-8")
    payload_file = USER_UPLOADS / "pytest-ssim.json"
    check_file = USER_UPLOADS / "pytest-ssim.conversion_check.json"
    inst_id = f"upload-{USER_SLUG}--pytest-ssim"
    try:
        resp = client.post("/api/import/ssim", json={
            "name": "pytest-ssim",
            "ssim_text": ssim_text,
            "date_from": None,
            "date_to": None,
            "bases": [{"airport": "ATL", "count": 2}],
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == f"upload-{USER_SLUG}--pytest-ssim"
        check = body["conversion_check"]
        assert check["ok"] is True
        assert check["flights_expanded"] >= 1
        assert check["records_parsed_type3"] >= 1
        # Payload + conversion-check artifact land in the caller's
        # per-user upload directory.
        assert payload_file.exists()
        assert check_file.exists()
        # The registry lists the payload but must NOT list the audit
        # artifact as a phantom instance.
        ids = [i["id"] for i in client.get("/api/instances").json()]
        assert f"upload-{USER_SLUG}--pytest-ssim" in ids
        assert not any("conversion" in i for i in ids)
        # Ф3: the exact SSIM text landed in the immutable raw zone
        man = raw_zone.manifest_for(inst_id)
        assert man is not None and len(man["files"]) == 1
        assert man["files"][0]["name"] == "pytest-ssim.ssim"
    finally:
        payload_file.unlink(missing_ok=True)
        check_file.unlink(missing_ok=True)
        raw_zone.discard(inst_id)         # keep the real repo tree clean


def test_ssim_import_field_validation_422(client):
    # Bad bases entry is rejected before the adapter even runs.
    resp = client.post("/api/import/ssim", json={
        "name": "pytest-ssim-bad",
        "ssim_text": "3 XX ...",
        "bases": [{"airport": "TOOLONG", "count": 2}],
    })
    assert resp.status_code == 422
    assert any("airport" in e for e in resp.json()["errors"])


def test_ssim_import_adapter_failure_saves_nothing(client):
    # A base airport absent from the schedule makes the adapter fail
    # loudly; the endpoint must return its output verbatim and leave
    # nothing in data/uploads.
    ssim_text = SAMPLE_SSIM.read_text(encoding="utf-8")
    resp = client.post("/api/import/ssim", json={
        "name": "pytest-ssim-nobase",
        "ssim_text": ssim_text,
        "bases": [{"airport": "SVO", "count": 2}],
    })
    assert resp.status_code == 422
    body = resp.json()
    assert any("SVO" in e for e in body["errors"])
    assert not (USER_UPLOADS / "pytest-ssim-nobase.json").exists()
    assert not (USER_UPLOADS
                / "pytest-ssim-nobase.conversion_check.json").exists()


# --- Meridian import ---------------------------------------------------------

def _tiny_meridian() -> dict:
    """A minimal one-leg synthetic Meridian export (fictional carrier)."""
    return {
        "format": 1, "type": "pairing",
        "planSince": "2026-08-01T00:00:00",
        "planUntil": "2026-08-31T23:59:00",
        "airports": [{"IATA": "BSE", "name": "БАЗОВЫЙ"},
                     {"IATA": "SEC", "name": "ВТОРОЙ"}],
        "legs": [{"legId": "ZZ101$05.08.2026$1", "flightNumber": "ZZ101",
                  "carrier": "ZZ", "legNumber": 1, "deadhead": False,
                  "departureStation": "BSE", "arrivalStation": "SEC",
                  "start": "2026-08-05T03:00:00.000",
                  "end": "2026-08-05T04:00:00.000",
                  "dutyCrewTypes": {"D1$1": "Р"}}],
        "duties": [{"dutyId": "D1$1",
                    "complements": [{"complementRank": "CA"},
                                    {"complementRank": "PURS"}]}],
        "employees": [{"employeeId": "E001", "base": "BSE",
                       "squadron": "Test$СБП$PYTESTNAME X.Y.",
                       "fleets": [{"aircraft": "A320neo",
                                   "rankQualification": "CA",
                                   "from": "2020-01-01T00:00:00.000",
                                   "to": "9999-12-31T00:00:00.000"}],
                       "features": []}],
        "groundActivities": [],
    }


def test_meridian_import_roundtrip(client):
    from webapp import raw_zone
    payload_file = USER_UPLOADS / "pytest-meridian.json"
    check_file = USER_UPLOADS / "pytest-meridian.conversion_check.json"
    inst_id = f"upload-{USER_SLUG}--pytest-meridian"
    try:
        resp = client.post("/api/import/meridian", json={
            "name": "pytest-meridian",
            "meridian_json": json.dumps(_tiny_meridian(),
                                        ensure_ascii=False),
            "solver_json": None,
            "date_from": None,
            "date_to": None,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == inst_id
        check = body["conversion_check"]
        assert check["ok"] is True
        assert check["legs_converted"] == 1
        assert check["seat_source"] == {"meridian_duty": 1}
        assert payload_file.exists()
        assert check_file.exists()
        # PII minimization holds through the endpoint path too.
        saved = json.loads(payload_file.read_text(encoding="utf-8"))
        assert "PYTESTNAME" not in json.dumps(saved, ensure_ascii=False)
        ids = [i["id"] for i in client.get("/api/instances").json()]
        assert inst_id in ids
        assert not any("conversion" in i for i in ids)
        # Ф3: the exact Meridian text landed in the immutable raw zone.
        man = raw_zone.manifest_for(inst_id)
        assert man is not None and len(man["files"]) == 1
        assert man["files"][0]["name"] == "pytest-meridian.meridian.json"
    finally:
        payload_file.unlink(missing_ok=True)
        check_file.unlink(missing_ok=True)
        raw_zone.discard(inst_id)         # keep the real repo tree clean


def _tiny_solver() -> dict:
    """The matching one-leg solver reference (zoned times = UTC + 5h)."""
    return {
        "format": 5,
        "legs": [{"id": "ZZ101$05.08.2026$1",
                  "start": "2026-08-05T08:00:00+05:00[Asia/Karachi]",
                  "end": "2026-08-05T09:00:00+05:00[Asia/Karachi]",
                  "acType": "A320neo"}],
        "stations": [{"iata": "BSE", "latitude": 45.512,
                      "longitude": 52.371},
                     {"iata": "SEC", "latitude": 46.148,
                      "longitude": 54.906}],
        "leg2Duties": [], "employeeAssignments": [],
    }


def test_meridian_import_with_solver_reference(client):
    # The --solver plumbing: second raw-zone file, aircraft type and geo
    # from the reference (review 2026-07-24, P1 coverage finding).
    from webapp import raw_zone
    payload_file = USER_UPLOADS / "pytest-meridian-sol.json"
    check_file = USER_UPLOADS / "pytest-meridian-sol.conversion_check.json"
    inst_id = f"upload-{USER_SLUG}--pytest-meridian-sol"
    try:
        resp = client.post("/api/import/meridian", json={
            "name": "pytest-meridian-sol",
            "meridian_json": json.dumps(_tiny_meridian(),
                                        ensure_ascii=False),
            "solver_json": json.dumps(_tiny_solver()),
        })
        assert resp.status_code == 200, resp.text
        check = resp.json()["conversion_check"]
        assert check["ok"] is True
        assert check["utc_guard_checked"] == 1
        saved = json.loads(payload_file.read_text(encoding="utf-8"))
        fl = saved["flights"][0]
        assert fl["aircraftType"] == "A320neo"
        man = raw_zone.manifest_for(inst_id)
        assert man is not None and len(man["files"]) == 2
        names = {f["name"] for f in man["files"]}
        assert names == {"pytest-meridian-sol.meridian.json",
                         "pytest-meridian-sol.solver.json"}
    finally:
        payload_file.unlink(missing_ok=True)
        check_file.unlink(missing_ok=True)
        raw_zone.discard(inst_id)


def test_meridian_import_no_fill_flag(client):
    # The DISTINGUISHING scenario proves the flag reaches the adapter:
    # a duty-less leg whose aircraft type has an observed mode signature
    # imports fine by default (mode fill) but must 422 with no_fill.
    from webapp import raw_zone
    meridian = _tiny_meridian()
    meridian["legs"].append({
        "legId": "ZZ102$06.08.2026$1", "flightNumber": "ZZ102",
        "carrier": "ZZ", "legNumber": 1, "deadhead": False,
        "departureStation": "BSE", "arrivalStation": "SEC",
        "start": "2026-08-06T03:00:00.000",
        "end": "2026-08-06T04:00:00.000", "dutyCrewTypes": {}})
    solver = _tiny_solver()
    solver["legs"].append(dict(solver["legs"][0],
                               id="ZZ102$06.08.2026$1",
                               start="2026-08-06T08:00:00+05:00"
                                     "[Asia/Karachi]",
                               end="2026-08-06T09:00:00+05:00"
                                   "[Asia/Karachi]"))
    body = {"meridian_json": json.dumps(meridian, ensure_ascii=False),
            "solver_json": json.dumps(solver)}
    inst_id = f"upload-{USER_SLUG}--pytest-meridian-fill"
    try:
        ok = client.post("/api/import/meridian",
                         json=dict(body, name="pytest-meridian-fill"))
        assert ok.status_code == 200, ok.text
        assert ok.json()["conversion_check"]["seat_source"][
            "actype_mode"] == 1
        strict = client.post("/api/import/meridian",
                             json=dict(body, name="pytest-meridian-strict",
                                       no_fill=True))
        assert strict.status_code == 422
        assert strict.json()["conversion_check"][
            "seat_unresolved_total"] == 1
    finally:
        for stem in ("pytest-meridian-fill",):
            (USER_UPLOADS / f"{stem}.json").unlink(missing_ok=True)
            (USER_UPLOADS
             / f"{stem}.conversion_check.json").unlink(missing_ok=True)
        raw_zone.discard(inst_id)
    # Strict typing: the string "false" must be rejected, not coerced.
    resp2 = client.post("/api/import/meridian", json={
        "name": "pytest-meridian-nofill2",
        "meridian_json": json.dumps(_tiny_meridian(), ensure_ascii=False),
        "no_fill": "false",
    })
    assert resp2.status_code == 422
    assert any("no_fill" in e for e in resp2.json()["errors"])


def test_meridian_import_duplicate_name_422(client):
    from webapp import raw_zone
    payload_file = USER_UPLOADS / "pytest-meridian-dup.json"
    check_file = USER_UPLOADS / "pytest-meridian-dup.conversion_check.json"
    inst_id = f"upload-{USER_SLUG}--pytest-meridian-dup"
    body = {"name": "pytest-meridian-dup",
            "meridian_json": json.dumps(_tiny_meridian(),
                                        ensure_ascii=False)}
    try:
        assert client.post("/api/import/meridian",
                           json=body).status_code == 200
        resp = client.post("/api/import/meridian", json=body)
        assert resp.status_code == 422
        assert any("already exists" in e for e in resp.json()["errors"])
        # The first upload is intact after the rejected second one.
        assert payload_file.exists()
    finally:
        payload_file.unlink(missing_ok=True)
        check_file.unlink(missing_ok=True)
        raw_zone.discard(inst_id)


def test_meridian_import_timeout_is_contractual_422(client, monkeypatch):
    # A hung adapter must produce the contractual 422 (not a naked 500)
    # and leave NOTHING in data/uploads — including *.tmp intermediates
    # (review 2026-07-24).
    import webapp.app as app_module
    monkeypatch.setattr(app_module, "MERIDIAN_TIMEOUT_S", 0.01)
    resp = client.post("/api/import/meridian", json={
        "name": "pytest-meridian-hang",
        "meridian_json": json.dumps(_tiny_meridian(), ensure_ascii=False),
    })
    assert resp.status_code == 422
    assert any("timed out" in e for e in resp.json()["errors"])
    leftovers = list(USER_UPLOADS.glob("pytest-meridian-hang*"))
    assert leftovers == []


def test_meridian_import_field_validation_422(client):
    resp = client.post("/api/import/meridian", json={
        "name": "pytest-meridian-bad",
        "meridian_json": "",
    })
    assert resp.status_code == 422
    assert any("meridian_json" in e for e in resp.json()["errors"])


def test_meridian_import_adapter_failure_saves_nothing(client):
    # Malformed JSON makes the adapter fail loudly; the endpoint returns
    # its output verbatim and leaves nothing in data/uploads.
    resp = client.post("/api/import/meridian", json={
        "name": "pytest-meridian-broken",
        "meridian_json": "{not json",
    })
    assert resp.status_code == 422
    body = resp.json()
    assert body["errors"]
    assert body["conversion_check"] is not None
    assert body["conversion_check"]["ok"] is False
    # The contract is "NOTHING left in data/uploads" — including the
    # *.tmp intermediates, not just the two final names (review 2026-07-24).
    assert list(USER_UPLOADS.glob("pytest-meridian-broken*")) == []


# --- Rules import (зона 2) ---------------------------------------------------

def test_rules_import_endpoint_meridian_limits(client):
    from webapp import rule_packs
    mer = {"format": 1,
           "employees": [{"employeeId": "1", "workTimeLimit": 8640,
                          "flightTimeLimit": 6000}],
           "duties": []}
    resp = client.post("/api/import/rules", json={
        "format": "meridian-limits", "name": "pytest-rules",
        "country": "QQ",
        "file_json": json.dumps(mer), "source_name": "m.json"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["imported_rules"] == 2
    assert body["pack_name"] == "pytest-rules"
    # Пакет реально создан в хранилище — со страной оператора (QQ —
    # синтетика вымышленного перевозчика) — и убирается за собой.
    pid = body["pack_id"]
    assert any(p["id"] == pid for p in rule_packs.list_packs())
    assert rule_packs.get_pack(pid)["country"] == "QQ"
    rule_packs.delete_pack(pid)


def test_rules_import_endpoint_country_required(client):
    """Файл format:1 страну юрисдикции не несёт: запрос без country —
    отказ 422 словами, а не тихий пакет «на все наборы»."""
    mer = {"format": 1,
           "employees": [{"employeeId": "1", "workTimeLimit": 8640,
                          "flightTimeLimit": 6000}],
           "duties": []}
    resp = client.post("/api/import/rules", json={
        "format": "meridian-limits", "name": "pytest-rules-nocountry",
        "file_json": json.dumps(mer), "source_name": "m.json"})
    assert resp.status_code == 422
    assert any("country" in e for e in resp.json()["errors"])


def test_rules_import_endpoint_closed_format_honest_422(client):
    resp = client.post("/api/import/rules", json={
        "format": "sabre-altitude", "file_json": "{}"})
    assert resp.status_code == 422
    msg = " ".join(resp.json()["errors"])
    assert "образцов" in msg and "догадкам" in msg


def test_rules_import_endpoint_validation(client):
    resp = client.post("/api/import/rules", json={"format": "auto"})
    assert resp.status_code == 422
    assert any("file_text" in e for e in resp.json()["errors"])


def test_rules_import_endpoint_text_formats(client):
    """Текстовые приёмники через file_text (2026-07-24): Rave и таблица
    норм; автоопределение формата на сервере."""
    from webapp import rule_packs
    rave = ('%max_duty% = parameter 13:00 remark "Предел наряда";\n'
            'rule r1 = valid duty.%x%; duty.%span% <= %max_duty%; end\n')
    resp = client.post("/api/import/rules", json={
        "format": "auto", "name": "pytest-rave",
        "file_text": rave, "source_name": "demo.rave"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["imported_rules"] >= 3
    rule_packs.delete_pack(body["pack_id"])

    csv_text = ("name;value;unit;window\n"
                "min_rest;10;h;\n")
    resp = client.post("/api/import/rules", json={
        "format": "auto", "name": "pytest-norm",
        "file_text": csv_text, "source_name": "norms.csv"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    pack = rule_packs.get_pack(body["pack_id"])
    kinds = [r["constraint"] for r in pack["rules"]]
    assert {"kind": "parameter", "parameter": "min_rest_min",
            "value": 600} in kinds
    rule_packs.delete_pack(body["pack_id"])


# --- Airline rule config ----------------------------------------------------

def test_airline_config_get(client):
    resp = client.get("/api/airline-config")
    assert resp.status_code == 200
    body = resp.json()
    assert "parameters" in body
    assert body["parameters"]["min_sit_min"] == 30
    # Since the CREW_RULES_CONFIG wiring the parameters ARE applied: every
    # stage run gets them via the env variable, fingerprints include rules.
    assert body["applied"] is True
    assert isinstance(body["note"], str)
    assert "CREW_RULES_CONFIG" in body["note"]


def test_airline_config_put_roundtrip(client):
    original = client.get("/api/airline-config").json()["parameters"]
    changed = dict(original)
    changed["min_sit_min"] = original["min_sit_min"] + 5
    try:
        resp = client.put("/api/airline-config",
                          json={"parameters": changed})
        assert resp.status_code == 200
        after = client.get("/api/airline-config").json()["parameters"]
        assert after["min_sit_min"] == original["min_sit_min"] + 5
        # Persisted to the configured airline-config file per the contract.
        # Путь берём У ПРИЛОЖЕНИЯ, а не собираем литералом: иначе тест
        # писал бы в РАБОЧИЙ конфиг репозитория (autouse-изоляция в
        # conftest, волна 1) и проверял бы не то, что настроено.
        from webapp import app as _app_module
        cfg_file = config_scope.install_path(config_scope.DOMAIN_RULES)
        assert cfg_file.exists()
        on_disk = json.loads(cfg_file.read_text(encoding="utf-8"))
        assert on_disk["parameters"]["min_sit_min"] == \
            original["min_sit_min"] + 5
    finally:
        restore = client.put("/api/airline-config",
                             json={"parameters": original})
        assert restore.status_code == 200


# --- Results ----------------------------------------------------------------

def test_results_gerad1_stage2_ok(client):
    # final-report/gerad_phase2_two_stage.json exists in the repo with a
    # completed OK run for inst1, so this must be served, not null.
    resp = client.get("/api/results/gerad-1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["stage2"] is not None
    assert body["stage2"]["result_status"] == "OK"
    assert body["stage2"]["missing_legs"] == 0
    assert isinstance(body["artifacts"], list)


def test_results_gerad1_roster_csv(client):
    resp = client.get("/api/results/gerad-1/roster.csv")
    assert resp.status_code == 200
    raw = resp.content
    # UTF-8 BOM for Excel, then the exact header from the contract.
    assert raw.startswith(b"\xef\xbb\xbf")
    header = raw[3:].split(b"\n", 1)[0].rstrip(b"\r").decode("utf-8")
    # phase 3 (C01/C02): + crew_slot_id — first-class seat identity
    assert header == ("employee,pairing_id,leg,role,dep_airport,dep_utc,"
                      "arr_airport,arr_utc,is_deadhead,crew_slot_id")


def test_results_gerad1_roster_view(client):
    """The visualization endpoint returns the joined crew×day roster,
    fairness-ready block hours, resolved pairing timelines, and coverage —
    all derived from the same artifacts as the CSV, for the real inst1."""
    resp = client.get("/api/results/gerad-1/roster")
    assert resp.status_code == 200
    v = resp.json()
    # meta: real inst1 shape (1013 legs, a full synthetic month)
    assert v["meta"]["legs"] == 1013
    assert len(v["meta"]["days"]) == 31
    assert v["meta"]["days"][0] == "2000-01-01"
    assert set(v["meta"]["bases"]) == {"BASE1", "BASE2", "BASE3"}
    # every crew row has one cell per day, states from the fixed vocabulary
    assert v["crew"], "expected crew rows"
    states = {"duty", "deadhead", "vacation", "standby", "off", "off_pref"}
    for c in v["crew"]:
        assert len(c["cells"]) == 31
        assert all(cell["s"] in states for cell in c["cells"])
        assert c["block_min"] >= 0
    # KPIs mirror the scalar results
    assert v["stage2"]["result_status"] == "OK"
    assert v["stage2"]["hard"] == 0
    assert v["coverage"]["total_legs"] == 1013
    assert v["coverage"]["missing"] == 0
    assert v["coverage"]["over"] == 180
    # pairing timelines: chronological legs, deadhead flag present
    assert 1 <= len(v["pairings_sample"]) <= 3
    s0 = v["pairings_sample"][0]
    utcs = [leg["dep_utc"] for leg in s0["legs"]]
    assert utcs == sorted(utcs)                       # chronological
    assert all("dh" in leg for leg in s0["legs"])


def test_results_roster_view_404_without_stage2(client, monkeypatch):
    """No stage-2 artifact → 404, so the frontend shows the empty state
    instead of a broken visualization."""
    from webapp import results
    monkeypatch.setattr(results, "roster_view", lambda inst: None)
    resp = client.get("/api/results/gerad-1/roster")
    assert resp.status_code == 404


def test_results_gerad1_pairing_view(client):
    """Данные визуализации пейринга по НАСТОЯЩЕМУ артефакту inst1:
    201 цепочка colgen-формы (только fns/active_fns/cost), смены —
    честно помеченной эвристикой gap600, полное покрытие, непустая
    карта дуг."""
    resp = client.get("/api/results/gerad-1/pairing")
    assert resp.status_code == 200
    v = resp.json()
    assert v["label"] == "inst1"
    assert v["meta"]["chains_total"] == 201
    assert len(v["chains"]) == 201                # ниже капа в 600 строк
    assert v["meta"]["chains_truncated"] is False
    assert set(v["meta"]["bases"]) == {"BASE1", "BASE2", "BASE3"}
    # colgen-каталог не несёт duty_fns — смены делятся эвристикой, и
    # ответ обязан это называть, а не выдавать за структуру источника.
    assert v["meta"]["duty_split"] == "gap600"
    for c in v["chains"]:
        assert c["cost"] is not None
        utcs = [leg["dep_utc"] for leg in c["legs"]]
        assert utcs == sorted(utcs)               # хронология
        assert all("dh" in leg and "duty" in leg for leg in c["legs"])
    # inst1 покрыт полностью (coverage.missing == 0 в /api/results).
    assert v["uncovered"] == []
    assert v["arcs"], "expected non-empty network arcs"
    base_flags = {n["code"]: n["base"] for n in v["nodes"]}
    assert base_flags.get("BASE1") is True


def test_results_pairing_view_404_without_catalog(client, monkeypatch):
    """Нет каталога этапа 1 → 404: фронт показывает пустое состояние,
    а не сломанную визуализацию."""
    from webapp import pairing_viz
    monkeypatch.setattr(pairing_viz, "pairing_view", lambda inst: None)
    resp = client.get("/api/results/gerad-1/pairing")
    assert resp.status_code == 404


# --- Artifact whitelist -----------------------------------------------------

def test_artifact_whitelisted_download(client):
    resp = client.get("/api/artifacts/gerad_stage1_results.json")
    assert resp.status_code == 200


def test_artifact_path_traversal_rejected(client):
    # Both the raw traversal path and the percent-encoded variant must fail:
    # the contract requires a literal whitelist match, nothing else.
    for path in ("/api/artifacts/../../LICENSE",
                 "/api/artifacts/..%2F..%2FLICENSE"):
        resp = client.get(path)
        assert resp.status_code == 404, path


def test_artifact_not_whitelisted_rejected(client):
    resp = client.get("/api/artifacts/secret.txt")
    assert resp.status_code == 404


# --- Jobs: request validation only (no execution) ---------------------------

def test_job_stage2_rejected_without_catalog(client):
    # Stage 2 needs a stage-1 catalog for the label; an upload without one
    # must get an honest 400 (and no job may be started).
    upload = USER_UPLOADS / "pytest-nocat.json"
    try:
        resp = client.post("/api/instances/upload",
                           json={"name": "pytest-nocat",
                                 "payload": copy.deepcopy(MINIMAL_VALID_PAYLOAD)})
        assert resp.status_code == 200, resp.text
        resp = client.post("/api/jobs",
                           json={"type": "stage2",
                                 "instance_id":
                                     f"upload-{USER_SLUG}--pytest-nocat",
                                 "params": {}})
        assert resp.status_code == 400
        assert "catalog" in resp.json()["detail"]
    finally:
        upload.unlink(missing_ok=True)
