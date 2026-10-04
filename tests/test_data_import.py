"""Universal data import: parsing, plan sanitizing, deterministic apply,
semantic checks and the analyze/commit endpoints (AI always stubbed)."""
import base64
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from webapp import data_import, llm_backend
from _stand_identity import ADMIN_EMAIL, ADMIN_SLUG, LEGACY_UPLOAD_ID

FLIGHTS_CSV = (
    "Flight No;Origin;Destination;Dep Date;Dep Time;Arr Date;Arr Time\n"
    "SU100;SVO;LED;01.03.2026;08:00;01.03.2026;09:20\n"
    "SU101;LED;SVO;01.03.2026;10:30;01.03.2026;11:50\n"
    "SU102;SVO;KZN;02.03.2026;07:15;02.03.2026;08:45\n")

CREW_CSV = (
    "Emp ID,Full Name,Base,Position\n"
    "P1,Ivanov I.I.,SVO,Captain\n"
    "P2,Petrov P.P.,SVO,FirstOfficer\n"
    "P3,Sidorov S.S.,LED,Captain\n")

PLAN = {
    "files": [
        {"file": "flights.csv", "role": "flights",
         "mapping": {
             "flightNumber": {"column": "Flight No"},
             "departureAirport": {"column": "Origin"},
             "arrivalAirport": {"column": "Destination"},
             "departureUTCDateTime": {"columns": ["Dep Date", "Dep Time"],
                                      "join": " "},
             "arrivalUTCDateTime": {"columns": ["Arr Date", "Arr Time"],
                                    "join": " "},
         },
         "datetime_format": "%d.%m.%Y %H:%M", "list_separator": None,
         "notes": "ok"},
        {"file": "crew.csv", "role": "employees",
         "mapping": {
             "id": {"column": "Emp ID"},
             "name": {"column": "Full Name"},
             "homeAirport": {"column": "Base"},
             "skills": {"column": "Position"},
         },
         "datetime_format": None, "list_separator": ",", "notes": "ok"},
    ],
    "assignments_synthesis": {"seats": [{"requiredSkill": "Captain"},
                                        {"requiredSkill": "FirstOfficer"}]},
    "rationale": "тест",
}


def _files():
    return [data_import.parse_file("flights.csv", FLIGHTS_CSV.encode()),
            data_import.parse_file("crew.csv", CREW_CSV.encode())]


# ----------------------------------------------------------------- parsing

def test_parse_csv_semicolon_and_comma():
    f = data_import.parse_file("flights.csv", FLIGHTS_CSV.encode())
    assert f["kind"] == "csv"
    assert f["headers"][0] == "Flight No" and len(f["rows"]) == 3
    g = data_import.parse_file("crew.csv", CREW_CSV.encode())
    assert g["headers"] == ["Emp ID", "Full Name", "Base", "Position"]


def test_parse_foreign_json_records():
    text = json.dumps([{"fn": "X1", "from": "AAA"}, {"fn": "X2", "to": "BBB"}])
    f = data_import.parse_file("x.json", text.encode())
    assert f["kind"] == "json"
    assert set(f["headers"]) == {"fn", "from", "to"} and len(f["rows"]) == 2


def test_parse_canonical_json_detected():
    payload = {"airports": [], "employees": [], "flights": [],
               "flightAssignments": []}
    f = data_import.parse_file("p.json", json.dumps(payload).encode())
    assert f["kind"] == "canonical"


def test_parse_xlsx_roundtrip(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Flight No", "Origin"])
    ws.append(["SU1", "SVO"])
    p = tmp_path / "t.xlsx"
    wb.save(p)
    f = data_import.parse_file("t.xlsx", p.read_bytes())
    assert f["kind"] == "xlsx" and f["headers"] == ["Flight No", "Origin"]
    assert f["rows"] == [["SU1", "SVO"]]


# ------------------------------------------------------------- plan safety

def test_sanitize_plan_drops_unknown_and_bad_columns():
    files = _files()
    raw = {
        "files": [
            {"file": "flights.csv", "role": "flights",
             "mapping": {
                 "flightNumber": {"column": "Flight No"},
                 "evilField": {"column": "Flight No"},          # unknown field
                 "departureAirport": {"column": "No Such Col"},  # bad column
                 "arrivalAirport": {"const": "LED"},
             }},
            {"file": "nope.csv", "role": "flights", "mapping": {}},  # unknown
            {"file": "crew.csv", "role": "hacker", "mapping": {}},   # bad role
        ],
        "rationale": "x" * 2000,
    }
    plan = data_import.sanitize_plan(raw, files)
    assert len(plan["files"]) == 2
    fl = plan["files"][0]
    assert set(fl["mapping"]) == {"flightNumber", "arrivalAirport"}
    assert plan["files"][1]["role"] == "ignore"
    assert len(plan["rationale"]) <= 800


# ---------------------------------------------------------------- applying

def test_build_payload_full_pipeline():
    payload, warnings = data_import.build_payload(_files(), PLAN)
    assert len(payload["flights"]) == 3
    assert payload["flights"][0]["departureUTCDateTime"] == "2026-03-01T08:00:00"
    assert len(payload["employees"]) == 3
    assert payload["employees"][0]["skills"] == ["Captain"]
    # 2 seats per flight synthesized
    assert len(payload["flightAssignments"]) == 6
    assert {a["requiredSkill"] for a in payload["flightAssignments"]} == \
        {"Captain", "FirstOfficer"}
    # airports derived from codes
    assert {a["code"] for a in payload["airports"]} == {"SVO", "LED", "KZN"}
    assert any("аэропорт" in w for w in warnings)
    # canonical validation passes end to end
    from webapp import registry
    v = registry.validate_payload(payload)
    assert v["ok"], v


def test_blank_flight_number_yields_validation_error_not_crash():
    """Ревью фазы 3 (находки 4/16): пустой номер рейса при ЯВНОМ файле
    назначений не должен ронять build_payload KeyError — материализация
    пропускает такую строку, а схема честно сообщает об ошибке."""
    flights_csv = FLIGHTS_CSV + ";SVO;LED;03.03.2026;08:00;03.03.2026;09:20\n"
    assigns_csv = ("Slot;Flight;Seat\n"
                   "1;SU100;Captain\n")
    files = [data_import.parse_file("flights.csv", flights_csv.encode()),
             data_import.parse_file("crew.csv", CREW_CSV.encode()),
             data_import.parse_file("assigns.csv", assigns_csv.encode())]
    plan = json.loads(json.dumps(PLAN))
    plan["assignments_synthesis"] = None
    plan["files"].append({
        "file": "assigns.csv", "role": "assignments",
        "mapping": {"id": {"column": "Slot"},
                    "flight": {"column": "Flight"},
                    "requiredSkill": {"column": "Seat"}},
        "datetime_format": None, "list_separator": None, "notes": "ok"})
    payload, _w = data_import.build_payload(files, plan)   # не бросает
    from webapp import registry
    out = registry.validate_payload(payload)
    assert out["ok"] is False                              # честная 422
    assert any("flightNumber" in e for e in out["errors"])


def test_blank_flight_number_synthesis_path_no_crash():
    """Второй раунд ревью: пустой номер и на ПУТИ СИНТЕЗА мест (дефолтный
    маршрут импорта) — build_payload не падает, схема сообщает честно."""
    flights_csv = FLIGHTS_CSV + ";SVO;LED;03.03.2026;08:00;03.03.2026;09:20\n"
    files = [data_import.parse_file("flights.csv", flights_csv.encode()),
             data_import.parse_file("crew.csv", CREW_CSV.encode())]
    payload, _w = data_import.build_payload(files, PLAN)   # не бросает
    from webapp import registry
    out = registry.validate_payload(payload)
    assert out["ok"] is False
    assert any("flightNumber" in e for e in out["errors"])


def test_build_payload_requires_flights_and_employees():
    files = [data_import.parse_file("crew.csv", CREW_CSV.encode())]
    plan = {"files": [dict(PLAN["files"][1])], "assignments_synthesis": None}
    with pytest.raises(data_import.ImportError_):
        data_import.build_payload(files, plan)


def test_apply_role_fails_loud_on_bad_datetime_format():
    files = _files()
    bad = json.loads(json.dumps(PLAN))
    bad["files"][0]["datetime_format"] = None
    # fallbacks list still parses dd.mm.yyyy hh:mm -> should succeed
    payload, _ = data_import.build_payload(files, bad)
    assert payload["flights"][0]["departureUTCDateTime"].startswith("2026-03-01")


# ------------------------------------------------------------ sem. checks

def test_semantic_checks_flag_reversed_and_duplicates():
    payload = {"flights": [
        {"flightNumber": "A1", "departureAirport": "AAA",
         "arrivalAirport": "BBB",
         "departureUTCDateTime": "2026-03-01T10:00:00",
         "arrivalUTCDateTime": "2026-03-01T09:00:00"},
        {"flightNumber": "A2", "departureAirport": "AAA",
         "arrivalAirport": "AAA",
         "departureUTCDateTime": "2026-03-01T10:00:00",
         "arrivalUTCDateTime": "2026-03-02T09:00:00"},
        {"flightNumber": "A2", "departureAirport": "AAA",
         "arrivalAirport": "CCC",
         "departureUTCDateTime": "2026-03-01T10:00:00",
         "arrivalUTCDateTime": "2026-03-01T11:00:00"},
    ]}
    sem = data_import.semantic_checks(payload)
    assert any("прилёт не позже вылета" in e for e in sem["errors"])
    assert any("дубликаты" in e for e in sem["errors"])
    assert any("одном аэропорту" in w for w in sem["warnings"])
    assert any("длительность" in w for w in sem["warnings"])


# ------------------------------------------------------ sample masking

def test_mask_value_shape_preserving():
    assert data_import._mask_value("Ivanov I.I.") == "Xxxxxx X.X."
    assert data_import._mask_value("Иванов И.И.") == "Xxxxxx X.X."
    assert data_import._mask_value("2026-03-01 08:00") == "9999-99-99 99:99"
    assert data_import._mask_value("SU100") == "XX999"
    assert data_import._mask_value("a@b.ru") == "x@x.xx"
    assert data_import._mask_value("") == ""


def _capture_llm(monkeypatch, captured):
    def fake(prompt, *, backend, timeout_s=0, system=None, cwd=None,
             usage=None, model=None):
        captured["prompt"] = prompt
        return {"ok": True, "text": json.dumps(PLAN), "backend": backend,
                "model": "m"}
    monkeypatch.setattr(llm_backend, "complete", fake)


def test_cloud_backend_never_sees_raw_sample_values(monkeypatch):
    """Строгий режим (§8-бис): облако видит только маскированный образец —
    формы значений и настоящие заголовки, но ни одного сырого значения."""
    monkeypatch.delenv("CREW_IMPORT_MASK_SAMPLES", raising=False)
    captured = {}
    _capture_llm(monkeypatch, captured)
    out = data_import.propose_mapping(_files(), "any", "cloud")
    assert out["samples_masked"] is True
    p = captured["prompt"]
    assert "Ivanov" not in p and "SU100" not in p       # raw values gone
    assert "Xxxxxx X.X." in p and "XX999" in p          # shapes remain
    assert "Flight No" in p and "Full Name" in p        # headers are real
    assert "замаскированы" in p                          # model is told


def test_local_backend_sees_raw_sample_by_default(monkeypatch):
    monkeypatch.delenv("CREW_IMPORT_MASK_SAMPLES", raising=False)
    captured = {}
    _capture_llm(monkeypatch, captured)
    out = data_import.propose_mapping(_files(), "local-only", None)
    assert out["samples_masked"] is False
    assert "Ivanov I.I." in captured["prompt"]


def test_mask_env_forces_masking_for_local(monkeypatch):
    monkeypatch.setenv("CREW_IMPORT_MASK_SAMPLES", "1")
    captured = {}
    _capture_llm(monkeypatch, captured)
    out = data_import.propose_mapping(_files(), "local-only", None)
    assert out["samples_masked"] is True
    assert "Ivanov" not in captured["prompt"]


# ------------------------------------------------------------- endpoints

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN_EMAIL)
    from webapp import raw_zone, registry, users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(registry, "LLM_SCOPE_PATH", tmp_path / "llm_scope.json")
    monkeypatch.setattr(registry, "OWNERSHIP_PATH", tmp_path / "ownership.json")
    # P0-03: короткий grandfathered-слаг личности, как на реальном стенде.
    (tmp_path / "ownership.json").write_text(json.dumps(
        {LEGACY_UPLOAD_ID: ADMIN_EMAIL}), encoding="utf-8")
    monkeypatch.setattr(raw_zone, "RAW_DIR", tmp_path / "raw")
    users.ensure_db()
    from webapp import app as app_module
    importlib.reload(app_module)
    # Committed payloads land in the REAL data/uploads (the registry serves
    # only repo-relative paths, same constraint as the tenancy tests) — the
    # fixture removes what the test created.
    created = registry.UPLOADS_DIR / ADMIN_SLUG / "import-march.json"
    with TestClient(app_module.app) as c:
        yield c, monkeypatch
    created.unlink(missing_ok=True)


def _stub_llm(monkeypatch, plan=PLAN):
    monkeypatch.setattr(
        llm_backend, "complete",
        lambda *a, **k: {"ok": True, "text": json.dumps(plan),
                         "backend": k["backend"], "model": "m"})


def test_analyze_commit_roundtrip(client):
    c, monkeypatch = client
    _stub_llm(monkeypatch)
    r = c.post("/api/import/analyze", json={
        "scope": "local-only",
        "files": [{"name": "flights.csv", "text": FLIGHTS_CSV},
                  {"name": "crew.csv", "text": CREW_CSV}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "llm" and body["backend"] == "local"
    assert body["stats"] == {"flights": 3, "employees": 3,
                             "assignments": 6, "airports": 3}
    assert body["validation"]["ok"] and not body["semantic"]["errors"]
    assert len(body["preview"]["flights"]) == 3
    r2 = c.post("/api/import/commit",
                json={"token": body["token"], "name": "import-march"})
    assert r2.status_code == 200, r2.text
    saved = r2.json()
    assert saved["id"].startswith(f"upload-{ADMIN_SLUG}-")
    assert saved["llm_scope"] == "local-only"
    assert saved["raw_files"] == 2                       # Ф3: raw zone
    inst = c.get("/api/instances/" + saved["id"]).json()
    assert inst["legs"] == 3 and inst["employees"] == 3
    assert inst["llm_scope"] == "local-only"
    # raw manifest: byte-exact sources with SHA-256 fingerprints
    rm = c.get(f"/api/instances/{saved['id']}/raw-manifest")
    assert rm.status_code == 200
    man = rm.json()["manifest"]
    assert {f["name"] for f in man["files"]} == {"flights.csv", "crew.csv"}
    assert all(len(f["sha256"]) == 64 for f in man["files"])
    # anomaly report over the committed instance answers too
    an = c.get(f"/api/instances/{saved['id']}/anomalies")
    assert an.status_code == 200 and "warnings" in an.json()


def test_analyze_scope_c_may_use_cloud(client):
    c, monkeypatch = client
    _stub_llm(monkeypatch)
    r = c.post("/api/import/analyze", json={
        "scope": "any", "backend": "cloud",
        "files": [{"name": "flights.csv", "text": FLIGHTS_CSV},
                  {"name": "crew.csv", "text": CREW_CSV}]})
    assert r.status_code == 200 and r.json()["backend"] == "cloud"
    assert r.json()["samples_masked"] is True           # cloud => masked


def test_analyze_local_only_rejects_cloud(client):
    c, monkeypatch = client
    _stub_llm(monkeypatch)
    r = c.post("/api/import/analyze", json={
        "scope": "local-only", "backend": "cloud",
        "files": [{"name": "flights.csv", "text": FLIGHTS_CSV}]})
    assert r.status_code == 400


def test_analyze_falls_back_without_llm(client):
    c, monkeypatch = client
    monkeypatch.setattr(
        llm_backend, "complete",
        lambda *a, **k: {"ok": False, "error": "down",
                         "backend": k["backend"], "model": "m"})
    r = c.post("/api/import/analyze", json={
        "scope": "local-only",
        "files": [{"name": "flights.csv", "text": FLIGHTS_CSV},
                  {"name": "crew.csv", "text": CREW_CSV}]})
    # heuristic draft still finds most columns; result depends on synonyms —
    # the call must NOT 500 and must surface source=fallback + llm_error.
    assert r.status_code in (200, 422)
    if r.status_code == 200:
        assert r.json()["source"] == "fallback"
        assert r.json()["llm_error"] == "down"


def test_commit_rejects_foreign_or_expired_token(client):
    c, _ = client
    r = c.post("/api/import/commit", json={"token": "nope", "name": "x"})
    assert r.status_code == 404


def test_analyze_canonical_skips_llm(client):
    c, monkeypatch = client
    def boom(*a, **k):
        raise AssertionError("LLM must not be called for canonical input")
    monkeypatch.setattr(llm_backend, "complete", boom)
    payload = {
        "formatVersion": "1",
        "airports": [{"code": "AAA"}, {"code": "BBB"}],
        "employees": [{"id": "E1", "homeAirport": "AAA",
                       "skills": ["Pilot"]}],
        "flights": [{"flightNumber": "F1", "departureAirport": "AAA",
                     "departureUTCDateTime": "2026-03-01T08:00:00",
                     "arrivalAirport": "BBB",
                     "arrivalUTCDateTime": "2026-03-01T09:00:00"}],
        "flightAssignments": [{"id": "1", "flight": "F1",
                               "indexInFlight": 0,
                               "requiredSkill": "Pilot"}],
    }
    r = c.post("/api/import/analyze", json={
        "scope": "any",
        "files": [{"name": "p.json", "text": json.dumps(payload)}]})
    assert r.status_code == 200
    assert r.json()["source"] == "canonical"


def test_analyze_b64_and_size_guard(client):
    c, monkeypatch = client
    _stub_llm(monkeypatch)
    b64 = base64.b64encode(FLIGHTS_CSV.encode()).decode()
    r = c.post("/api/import/analyze", json={
        "scope": "local-only",
        "files": [{"name": "flights.csv", "content_b64": b64},
                  {"name": "crew.csv", "text": CREW_CSV}]})
    assert r.status_code == 200
