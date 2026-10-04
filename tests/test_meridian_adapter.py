"""Tests for the Meridian import adapter (_meridian_to_payload.py).

Runs the adapter as a subprocess (the same way the webapp does) against a
SYNTHETIC Meridian export built in-test (fictional carrier ZZ — no real
airline data, no PII, nothing customer-shaped lands in the repo) and
checks: leg conversion and exclusions, the three seat-derivation sources
(Meridian duties, Solver map, aircraft-type mode fill), identity
uniqueness and ASCII-ness (including Cyrillic homoglyph carriers), the
UTC guard against the Solver reference, PII minimization, qualification
emission/classification, placeholder-rank skipping, ground-activity
unavailability, schema validity, and the loud-failure paths.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ADAPTER = REPO / "_meridian_to_payload.py"
SCHEMA = REPO / "webapp" / "schema" / "canonical_payload.schema.json"

# Marker that must never leak into the payload (PII minimization).
SECRET = "SECRETNAME"


def make_meridian() -> dict:
    """A minimal synthetic Meridian export (format:1)."""
    return {
        "format": 1,
        "type": "pairing",
        "planSince": "2026-08-01T00:00:00",
        "planUntil": "2026-08-31T23:59:00",
        "description": "pytest synthetic",
        "airports": [
            {"IATA": "BSE", "name": "БАЗОВЫЙ", "city": "БАЗОВЫЙ"},
            {"IATA": "SEC", "name": "ВТОРОЙ", "city": "ВТОРОЙ"},
            {"IATA": "FRU", "name": "БИШКЕК", "city": "БИШКЕК"},
        ],
        "legs": [
            # Two legs of duty D1$1 (Meridian-native seat source).
            {"legId": "ZZ101$05.08.2026$1", "flightNumber": "ZZ101",
             "carrier": "ZZ", "legNumber": 1, "deadhead": False,
             "properties": "J_REGULAR",
             "departureStation": "BSE", "arrivalStation": "SEC",
             "start": "2026-08-05T03:00:00.000",
             "end": "2026-08-05T04:00:00.000",
             "dutyCrewTypes": {"D1$1": "Р"}},
            {"legId": "ZZ101$05.08.2026$2", "flightNumber": "ZZ101",
             "carrier": "ZZ", "legNumber": 2, "deadhead": False,
             "properties": "J_REGULAR",
             "departureStation": "SEC", "arrivalStation": "BSE",
             "start": "2026-08-05T05:00:00.000",
             "end": "2026-08-05T06:00:00.000",
             "dutyCrewTypes": {"D1$1": "Р"}},
            # Covered only by the Solver leg2Duties map.
            {"legId": "ZZ202$10.08.2026$1", "flightNumber": "ZZ202",
             "carrier": "ZZ", "legNumber": 1, "deadhead": False,
             "properties": "J_REGULAR",
             "departureStation": "BSE", "arrivalStation": "FRU",
             "start": "2026-08-10T07:00:00.000",
             "end": "2026-08-10T08:00:00.000",
             "dutyCrewTypes": {}},
            # No duty anywhere -> aircraft-type mode fill.
            {"legId": "ZZ303$15.08.2026$1", "flightNumber": "ZZ303",
             "carrier": "ZZ", "legNumber": 1, "deadhead": False,
             "properties": "J_REGULAR",
             "departureStation": "BSE", "arrivalStation": "SEC",
             "start": "2026-08-15T09:00:00.000",
             "end": "2026-08-15T10:00:00.000",
             "dutyCrewTypes": {}},
            # Cyrillic carrier (homoglyph 'С') in a Meridian duty.
            {"legId": "7С909$12.08.2026$1", "flightNumber": "7С909",
             "carrier": "7С", "legNumber": 1, "deadhead": False,
             "properties": "J_REGULAR",
             "departureStation": "SEC", "arrivalStation": "BSE",
             "start": "2026-08-12T11:00:00.000",
             "end": "2026-08-12T12:00:00.000",
             "dutyCrewTypes": {"D2$1": "Р"}},
            # Outside the plan window -> excluded (counted).
            {"legId": "ZZ404$20.07.2026$1", "flightNumber": "ZZ404",
             "carrier": "ZZ", "legNumber": 1, "deadhead": False,
             "properties": "J_REGULAR",
             "departureStation": "BSE", "arrivalStation": "SEC",
             "start": "2026-07-20T03:00:00.000",
             "end": "2026-07-20T04:00:00.000",
             "dutyCrewTypes": {}},
            # Deadhead -> excluded (counted).
            {"legId": "ZZ505$06.08.2026$1", "flightNumber": "ZZ505",
             "carrier": "ZZ", "legNumber": 1, "deadhead": True,
             "properties": "TRANSFER",
             "departureStation": "BSE", "arrivalStation": "SEC",
             "start": "2026-08-06T03:00:00.000",
             "end": "2026-08-06T04:00:00.000",
             "dutyCrewTypes": {}},
        ],
        "duties": [
            {"dutyId": "D1$1", "pairingId": "D1", "numberOfDuty": 1,
             "start": "2026-08-05T02:00:00.000",
             "end": "2026-08-05T07:00:00.000",
             "complements": [
                 {"complementRank": "CA", "employeeId": None,
                  "locked": False, "requirements": []},
                 {"complementRank": "PURS", "employeeId": None,
                  "locked": False, "requirements": []},
                 {"complementRank": "CA", "employeeId": None,
                  "locked": False, "requirements": []},
             ]},
            {"dutyId": "D2$1", "pairingId": "D2", "numberOfDuty": 1,
             "start": "2026-08-12T10:00:00.000",
             "end": "2026-08-12T13:00:00.000",
             "complements": [
                 {"complementRank": "CA", "employeeId": None,
                  "locked": False, "requirements": []},
             ]},
        ],
        "pairings": [],
        "employees": [
            {"employeeId": "E001", "base": "BSE", "sex": "F",
             "birth": "1990-01-01", "citizenship": "ZZ",
             "squadron": f"Test$СБП${SECRET} X.Y.",
             "mentors": [], "doNotFlyWith": [],
             "fleets": [
                 {"aircraft": "A320neo", "rankQualification": "CA",
                  "from": "2020-01-01T00:00:00.000",
                  "to": "9999-12-31T00:00:00.000",
                  "left": "NO", "right": "NO", "instr": "NO"},
             ],
             "features": [
                 {"code": "Опасные грузы",
                  "activeSince": "2025-06-01T00:00:00.000",
                  "activeUntil": "2026-12-31T00:00:00.000"},
                 {"code": "A320neo_АСП на суше",
                  "activeSince": "2025-06-01T00:00:00.000",
                  "activeUntil": "2026-11-30T00:00:00.000"},
                 {"code": "Давно истёкший допуск",
                  "activeSince": "2024-01-01T00:00:00.000",
                  "activeUntil": "2026-01-01T00:00:00.000"},
             ]},
            {"employeeId": "E002", "base": "BSE", "sex": "M",
             "birth": "1985-05-05", "citizenship": "ZZ",
             "squadron": f"Test$СБП${SECRET} A.B.",
             "fleets": [
                 {"aircraft": "A320neo", "rankQualification": "PURS",
                  "from": "2019-01-01T00:00:00.000",
                  "to": "9999-12-31T00:00:00.000"},
             ],
             "features": []},
            # Placeholder rank + an expired fleet window -> no skills.
            {"employeeId": "E003", "base": "BSE",
             "squadron": f"Test$СБП${SECRET} C.D.",
             "fleets": [
                 {"aircraft": "A320neo", "rankQualification": "<нет>",
                  "from": "2020-01-01T00:00:00.000",
                  "to": "9999-12-31T00:00:00.000"},
                 {"aircraft": "B767-300", "rankQualification": "CA",
                  "from": "2015-01-01T00:00:00.000",
                  "to": "2020-01-01T00:00:00.000"},
             ],
             "features": []},
        ],
        "groundActivities": [
            {"groundId": "0", "employeeId": "E001", "groundType": "DAYOFF",
             "groundRank": "CA", "groundDescription": "Выходной",
             "start": "2026-08-03T19:00:00.000",
             "end": "2026-08-04T18:59:00.000",
             "dislocationStart": "BSE", "dislocationEnd": "BSE"},
            # Entirely outside the window -> contributes no days.
            {"groundId": "1", "employeeId": "E001", "groundType": "DAYOFF",
             "groundRank": "CA", "groundDescription": "Выходной",
             "start": "2026-07-01T19:00:00.000",
             "end": "2026-07-02T18:59:00.000",
             "dislocationStart": "BSE", "dislocationEnd": "BSE"},
        ],
    }


def make_solver() -> dict:
    """The matching synthetic Solver export (format:5) — reference only.
    Leg ends mirror the Meridian fixture (departure + 1h) so the UTC
    guard, which checks BOTH endpoints, sees a consistent reference."""
    def zoned(naive_utc: str, plus_h: int = 0) -> str:
        # +05:00 local for a UTC instant: local = UTC + 5h.
        from datetime import datetime, timedelta
        dt = (datetime.fromisoformat(naive_utc)
              + timedelta(hours=5 + plus_h))
        return dt.strftime("%Y-%m-%dT%H:%M:%S") + "+05:00[Asia/Karachi]"

    legs = []
    for leg_id, dep, ac in [
        ("ZZ101$05.08.2026$1", "2026-08-05T03:00:00", "A320neo"),
        ("ZZ101$05.08.2026$2", "2026-08-05T05:00:00", "A320neo"),
        ("ZZ202$10.08.2026$1", "2026-08-10T07:00:00", "A320neo"),
        ("ZZ303$15.08.2026$1", "2026-08-15T09:00:00", "A320neo"),
        ("7С909$12.08.2026$1", "2026-08-12T11:00:00", "А320  "),
    ]:
        legs.append({"id": leg_id, "start": zoned(dep),
                     "end": zoned(dep, plus_h=1), "acType": ac})
    return {
        "format": 5,
        "legs": legs,
        "stations": [
            {"iata": "BSE", "latitude": 10.123, "longitude": 20.456,
             "timeZone": "Asia/Karachi"},
            {"iata": "SEC", "latitude": 11.987, "longitude": 21.654,
             "timeZone": "Asia/Karachi"},
            {"iata": "FRU", "latitude": 0, "longitude": 0,
             "timeZone": "Asia/Bishkek"},
        ],
        "leg2Duties": [
            {"leg": "ZZ202$10.08.2026$1", "duty": "SD1", "crewRole": "STD"},
        ],
        "employeeAssignments": [
            {"id": "a1", "duty": "SD1", "preferredRank": "CA"},
            {"id": "a2", "duty": "SD1", "preferredRank": "CA"},
        ],
    }


def run_adapter(tmp_path, meridian: dict, solver: dict | None = None,
                *extra_args):
    """Write fixtures, run the adapter; return (proc, payload_path, check)."""
    src = tmp_path / "meridian.json"
    src.write_text(json.dumps(meridian, ensure_ascii=False),
                   encoding="utf-8")
    out = tmp_path / "payload.json"
    check = tmp_path / "check.json"
    cmd = [sys.executable, str(ADAPTER), str(src),
           "--out", str(out), "--check", str(check)]
    if solver is not None:
        sol = tmp_path / "solver.json"
        sol.write_text(json.dumps(solver, ensure_ascii=False),
                       encoding="utf-8")
        cmd += ["--solver", str(sol)]
    cmd += list(extra_args)
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO,
                          encoding="utf-8", errors="replace")
    return proc, out, check


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    """One full conversion (with the Solver reference) shared by the
    read-only assertions."""
    tmp = tmp_path_factory.mktemp("meridian_full")
    proc, out, check = run_adapter(tmp, make_meridian(), make_solver())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    check_doc = json.loads(check.read_text(encoding="utf-8"))
    return payload, check_doc


def slots_of(payload, flight_number):
    return sorted((s for s in payload["flightAssignments"]
                   if s["flight"] == flight_number),
                  key=lambda s: s["indexInFlight"])


# ------------------------------------------------------------------ conversion

def test_leg_conversion_and_exclusions(converted):
    payload, check = converted
    assert len(payload["flights"]) == 5
    assert check["legs_total"] == 7
    assert check["legs_converted"] == 5
    assert check["legs_excluded"] == {"deadhead": 1, "outside_range": 1}
    assert check["range_from"] == "2026-08-01"       # plan window default
    assert check["range_to"] == "2026-08-31"
    assert check["ok"] is True


def test_identities_unique_and_ascii(converted):
    payload, _ = converted
    fns = [f["flightNumber"] for f in payload["flights"]]
    legs = [f["flightLegId"] for f in payload["flights"]]
    slot_ids = [s["crewSlotId"] for s in payload["flightAssignments"]]
    assert len(set(fns)) == len(fns)
    assert len(set(legs)) == len(legs)
    assert len(set(slot_ids)) == len(slot_ids)
    assert all(fn.isascii() for fn in fns)
    # The Cyrillic carrier is transliterated via homoglyphs; the vendor
    # leg id is preserved for traceability.
    cyr = [f for f in payload["flights"]
           if f["sourceLegId"] == "7С909$12.08.2026$1"]
    assert len(cyr) == 1
    assert cyr[0]["flightNumber"] == "7C909-20260812-1"
    # First-class identity follows the repo convention fn|depUTC.
    f0 = next(f for f in payload["flights"]
              if f["flightNumber"] == "ZZ101-20260805-1")
    assert f0["flightLegId"] == "ZZ101-20260805-1|2026-08-05T03:00:00"


def test_seats_from_meridian_duty_ordered(converted):
    payload, check = converted
    # D1$1 complements = CA, PURS, CA -> 3 seats, PURS first (RANK_ORDER).
    for fn in ("ZZ101-20260805-1", "ZZ101-20260805-2"):
        seats = slots_of(payload, fn)
        assert [s["requiredSkill"] for s in seats] == ["PURS", "CA", "CA"]
        assert [s["indexInFlight"] for s in seats] == [0, 1, 2]
        assert all(s["employee"] is None for s in seats)
    assert check["seat_source"]["meridian_duty"] == 3   # 2×ZZ101 + 7C909


def test_seats_from_solver_map(converted):
    payload, check = converted
    seats = slots_of(payload, "ZZ202-20260810-1")
    assert [s["requiredSkill"] for s in seats] == ["CA", "CA"]
    assert check["seat_source"]["solver_duty"] == 1


def test_seats_from_actype_mode_fill(converted):
    payload, check = converted
    # ZZ303 has no duty anywhere; its aircraft type A320neo has the
    # observed mode signature PURS+CA+CA (from the two ZZ101 legs).
    seats = slots_of(payload, "ZZ303-20260815-1")
    assert [s["requiredSkill"] for s in seats] == ["PURS", "CA", "CA"]
    assert check["seat_source"]["actype_mode"] == 1
    assert check["seat_fill_by_aircraft_type"] == {"A320neo": 1}
    assert check["seat_unresolved_total"] == 0


def test_aircraft_type_from_solver_homoglyph_canonical(converted):
    payload, _ = converted
    by_src = {f["sourceLegId"]: f for f in payload["flights"]}
    assert by_src["ZZ101$05.08.2026$1"]["aircraftType"] == "A320neo"
    # Cyrillic 'А320  ' -> Latin, whitespace collapsed.
    assert by_src["7С909$12.08.2026$1"]["aircraftType"] == "A320"


def test_utc_guard_counted(converted):
    _, check = converted
    assert check["utc_guard_checked"] == 5
    assert check["utc_guard_mismatches"] == []


# ------------------------------------------------------------------ employees

def test_employees_pii_minimized(converted):
    payload, check = converted
    text = json.dumps(payload, ensure_ascii=False)
    assert SECRET not in text
    for banned in ("squadron", "birth", "citizenship"):
        assert banned not in payload["employees"][0]
    for e in payload["employees"]:
        assert e["name"] == e["id"]
    assert any("squadron" in f for f in check["pii_dropped_fields"])


def test_employee_skills_ratings_unavailable(converted):
    payload, _ = converted
    by_id = {e["id"]: e for e in payload["employees"]}
    assert by_id["E001"]["skills"] == ["CA"]
    assert by_id["E001"]["typeRatings"] == ["A320neo"]
    assert by_id["E001"]["unavailableDays"] == ["2026-08-03", "2026-08-04"]
    assert by_id["E002"]["skills"] == ["PURS"]


def test_placeholder_rank_and_expired_window_skipped(converted):
    payload, check = converted
    by_id = {e["id"]: e for e in payload["employees"]}
    assert by_id["E003"]["skills"] == []
    assert by_id["E003"]["typeRatings"] == []
    assert check["placeholder_ranks_skipped"] == {"<нет>": 1}


def test_qualifications_emitted_and_classified(converted):
    payload, check = converted
    quals = {(q["employee"], q["code"]): q
             for q in payload["qualifications"]}
    dg = quals[("E001", "Опасные грузы")]
    assert dg["validTo"] == "2026-12-31"
    assert dg["validFrom"] == "2025-06-01"
    assert dg["kind"] == "licence"          # generic dated-clearance bucket
    asp = quals[("E001", "A320neo_АСП на суше")]
    assert asp["kind"] == "type_rating"     # fleet-prefixed
    # The long-expired clearance is skipped (validTo before the window).
    assert ("E001", "Давно истёкший допуск") not in quals
    assert check["qualifications_skipped_expired"] == 1


# ------------------------------------------------------------------ artifacts

def test_airports_referenced_subset_with_geo(converted):
    payload, check = converted
    codes = {a["code"] for a in payload["airports"]}
    assert codes == {"BSE", "SEC", "FRU"}
    by_code = {a["code"]: a for a in payload["airports"]}
    assert by_code["BSE"]["latitude"] == pytest.approx(10.123)
    assert by_code["BSE"]["name"] == "БАЗОВЫЙ"
    # Zero coordinates mean "absent" — not emitted.
    assert "latitude" not in by_code["FRU"]
    assert check["airports_with_geo"] == 2


def test_output_validates_against_schema(converted):
    import jsonschema
    payload, _ = converted
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema)
    assert payload["formatVersion"] == "2"
    assert payload["provenance"]["adapter"] == "meridian"


def test_server_validate_payload_accepts(converted):
    sys.path.insert(0, str(REPO))
    from webapp import registry
    payload, _ = converted
    res = registry.validate_payload(payload)
    assert res["ok"] is True, res.get("errors")


# ------------------------------------------------------------------ failures

def test_utc_guard_mismatch_fails_loudly(tmp_path):
    solver = make_solver()
    # Shift one Solver leg by +1h: Meridian times are no longer UTC.
    solver["legs"][0]["start"] = solver["legs"][0]["start"].replace(
        "T08:00:00", "T09:00:00")
    proc, out, check = run_adapter(tmp_path, make_meridian(), solver)
    assert proc.returncode == 1
    assert "UTC" in proc.stdout
    assert not out.exists()
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["ok"] is False
    assert doc["utc_guard_mismatches"]


def test_no_fill_makes_unresolved_fatal(tmp_path):
    proc, out, check = run_adapter(tmp_path, make_meridian(), make_solver(),
                                   "--no-fill")
    assert proc.returncode == 1
    assert not out.exists()
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["seat_unresolved_total"] == 1
    assert doc["seat_unresolved"][0]["legId"] == "ZZ303$15.08.2026$1"


def test_no_solver_falls_back_to_meridian_only(tmp_path):
    # Without the Solver reference: no aircraft types -> ZZ202 (solver-map
    # leg) and ZZ303 (mode-fill leg) cannot be resolved -> loud failure
    # listing them; the Meridian-duty legs alone are not enough.
    proc, out, check = run_adapter(tmp_path, make_meridian(), None)
    assert proc.returncode == 1
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["seat_unresolved_total"] == 2


def test_empty_range_fails(tmp_path):
    proc, out, _ = run_adapter(tmp_path, make_meridian(), make_solver(),
                               "--from", "2027-01-01",
                               "--to", "2027-01-31")
    assert proc.returncode == 3
    assert not out.exists()


def test_nonzero_seconds_fail_loudly(tmp_path):
    meridian = make_meridian()
    meridian["legs"][0]["start"] = "2026-08-05T03:00:30.000"
    proc, out, _ = run_adapter(tmp_path, meridian, make_solver())
    assert proc.returncode == 1
    assert "second" in (proc.stdout + proc.stderr)
    assert not out.exists()


def test_duplicate_leg_id_fails_loudly(tmp_path):
    meridian = make_meridian()
    meridian["legs"].append(dict(meridian["legs"][0]))
    proc, out, _ = run_adapter(tmp_path, meridian, make_solver())
    assert proc.returncode == 1
    assert "duplicate legId" in (proc.stdout + proc.stderr)


def test_ambiguous_signatures_most_common_wins(tmp_path):
    # A leg in THREE candidate duties: two vote PURS+CA+CA, one votes
    # CA-only -> the most common signature wins, the leg is counted
    # ambiguous (review 2026-07-24, P1 coverage finding).
    meridian = make_meridian()
    meridian["duties"].append(
        {"dutyId": "D3$1", "complements": [{"complementRank": "CA"}]})
    meridian["duties"].append(
        {"dutyId": "D4$1", "complements": [
            {"complementRank": "CA"}, {"complementRank": "PURS"},
            {"complementRank": "CA"}]})
    meridian["legs"][0]["dutyCrewTypes"] = {
        "D1$1": "Р", "D3$1": "Р", "D4$1": "Р"}
    proc, out, check = run_adapter(tmp_path, meridian, make_solver())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    doc = json.loads(check.read_text(encoding="utf-8"))
    seats = slots_of(payload, "ZZ101-20260805-1")
    assert [s["requiredSkill"] for s in seats] == ["PURS", "CA", "CA"]
    assert doc["seat_signature_ambiguous_legs"] == 1


def test_ambiguous_tie_breaks_on_smallest_duty_id(tmp_path):
    # 1:1 tie between two signatures -> the smallest dutyId decides,
    # deterministically.
    meridian = make_meridian()
    meridian["duties"].append(
        {"dutyId": "D0$1", "complements": [{"complementRank": "CA"}]})
    meridian["legs"][0]["dutyCrewTypes"] = {"D0$1": "Р", "D1$1": "Р"}
    proc, out, check = run_adapter(tmp_path, meridian, make_solver())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    seats = slots_of(payload, "ZZ101-20260805-1")
    assert [s["requiredSkill"] for s in seats] == ["CA"]   # D0$1 < D1$1
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["seat_signature_ambiguous_legs"] == 1


def test_homoglyph_flight_number_collision_fails_sanity(tmp_path):
    # Two LEGAL distinct legIds — '7С909' (Cyrillic Es) and '7C909'
    # (Latin) — collapse into one canonical flightNumber: the sanity
    # pass must fail loudly with exit 3 (review 2026-07-24).
    meridian = make_meridian()
    meridian["legs"].append({
        "legId": "7C909$12.08.2026$1", "flightNumber": "7C909",
        "carrier": "7C", "legNumber": 1, "deadhead": False,
        "departureStation": "SEC", "arrivalStation": "BSE",
        "start": "2026-08-12T11:00:00.000",
        "end": "2026-08-12T12:00:00.000",
        "dutyCrewTypes": {"D2$1": "Р"}})
    proc, out, check = run_adapter(tmp_path, meridian, make_solver())
    assert proc.returncode == 3
    assert not out.exists()
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["ok"] is False
    assert "7C909-20260812-1" in doc["duplicate_flight_numbers"]


def test_reversed_ground_interval_fails_loudly(tmp_path):
    meridian = make_meridian()
    meridian["groundActivities"][0]["start"] = "2026-08-10T19:00:00.000"
    meridian["groundActivities"][0]["end"] = "2026-08-04T18:59:00.000"
    proc, out, _ = run_adapter(tmp_path, meridian, make_solver())
    assert proc.returncode == 1
    assert "ends before it starts" in (proc.stdout + proc.stderr)
    assert not out.exists()


def test_midnight_ending_ground_frees_the_end_day(tmp_path):
    # 03.08 19:00 .. 05.08 00:00 occupies nothing of the 5th — only the
    # 3rd and the 4th are blocked (review 2026-07-24).
    meridian = make_meridian()
    meridian["groundActivities"][0]["end"] = "2026-08-05T00:00:00.000"
    proc, out, _ = run_adapter(tmp_path, meridian, make_solver())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    e = next(x for x in payload["employees"] if x["id"] == "E001")
    assert e["unavailableDays"] == ["2026-08-03", "2026-08-04"]


def test_malformed_ground_records_fail_loudly(tmp_path):
    meridian = make_meridian()
    meridian["groundActivities"].append({"employeeId": 123})
    proc, out, _ = run_adapter(tmp_path, meridian, make_solver())
    assert proc.returncode == 1
    assert "groundActivities[2]" in (proc.stdout + proc.stderr)
    meridian2 = make_meridian()
    meridian2["groundActivities"] = {"broken": True}
    proc2, out2, _ = run_adapter(tmp_path, meridian2, make_solver())
    assert proc2.returncode == 1
    assert "groundActivities must be a list" in (proc2.stdout + proc2.stderr)


def test_wrong_type_duty_crew_types_collected_with_check(tmp_path):
    # A non-dict dutyCrewTypes is a collected error (no traceback), and
    # the check artifact IS written (review 2026-07-24, finding 1).
    meridian = make_meridian()
    meridian["legs"][0]["dutyCrewTypes"] = ["D1"]
    proc, out, check = run_adapter(tmp_path, meridian, make_solver())
    assert proc.returncode == 1
    assert "dutyCrewTypes must be an object" in (proc.stdout + proc.stderr)
    assert "Traceback" not in proc.stderr
    assert not out.exists()
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["ok"] is False


def test_calendar_invalid_dates_are_loud_not_tracebacks(tmp_path):
    # '2026-13-01' passes the digit form but is not a date — every such
    # value must end as a collected error with a check artifact, never a
    # raw ValueError traceback (review 2026-07-24, finding 2).
    cases = [
        ("legs", lambda m: m["legs"][0].update(
            start="2026-13-01T03:00:00.000")),
        ("planSince", lambda m: m.update(planSince="2026-00-01T00:00:00")),
        ("ground", lambda m: m["groundActivities"][0].update(
            end="2026-08-32T18:59:00.000")),
    ]
    for name, mutate in cases:
        meridian = make_meridian()
        mutate(meridian)
        proc, out, check = run_adapter(tmp_path, meridian, make_solver())
        assert proc.returncode != 0, name
        assert "Traceback" not in proc.stderr, name
        doc = json.loads(check.read_text(encoding="utf-8"))
        assert doc["ok"] is False, name


def test_unreadable_fleet_window_grants_no_skill(tmp_path):
    # A PRESENT but unreadable window must not become an eternal one —
    # the fleet row is skipped and counted (review 2026-07-24, finding 3).
    meridian = make_meridian()
    meridian["employees"][0]["fleets"][0]["to"] = "not-a-date"
    proc, out, check = run_adapter(tmp_path, meridian, make_solver())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    e = next(x for x in payload["employees"] if x["id"] == "E001")
    assert e["skills"] == []
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["fleet_windows_unreadable"] == 1


def test_undated_feature_counted_not_silent(tmp_path):
    meridian = make_meridian()
    del meridian["employees"][0]["features"][0]["activeUntil"]
    proc, out, check = run_adapter(tmp_path, meridian, make_solver())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["qualifications_skipped_undated"] == 1
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert not any(q["code"] == "Опасные грузы"
                   for q in payload["qualifications"])


def test_nonzero_milliseconds_fail_loudly(tmp_path):
    meridian = make_meridian()
    meridian["legs"][0]["start"] = "2026-08-05T03:00:00.999"
    proc, out, _ = run_adapter(tmp_path, meridian, make_solver())
    assert proc.returncode == 1
    assert "fractional" in (proc.stdout + proc.stderr)
    assert not out.exists()


def test_utc_guard_must_engage_when_solver_present(tmp_path):
    # If NONE of the shared legs' solver times parse, the guard never
    # engaged — that is a loud failure, not a silent green run (review
    # 2026-07-24, finding 8).
    solver = make_solver()
    for l in solver["legs"]:
        l["start"] = l["start"][:19] + "Z"          # unparseable suffix
        l["end"] = l["end"][:19] + "Z"
    proc, out, check = run_adapter(tmp_path, make_meridian(), solver)
    assert proc.returncode == 1
    assert "UTC guard could not engage" in (proc.stdout + proc.stderr)
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["utc_guard_unparsed"] == 5
    assert doc["utc_guard_checked"] == 0


def test_utc_guard_checks_leg_end_too(tmp_path):
    solver = make_solver()
    # Shift only the END of one leg by an hour: start passes, end must
    # still be caught (review 2026-07-24, finding 8).
    solver["legs"][0]["end"] = solver["legs"][0]["end"].replace(
        "T09:00:00", "T10:00:00")
    proc, out, check = run_adapter(tmp_path, make_meridian(), solver)
    assert proc.returncode == 1
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert any(m["field"] == "end" for m in doc["utc_guard_mismatches"])


def test_cyrillic_station_homoglyph_joins_not_forks(tmp_path):
    # A departure station 'ВSE' with a Cyrillic В must join the Latin
    # 'BSE' airport, not fork a second one (review 2026-07-24, finding 9).
    meridian = make_meridian()
    meridian["legs"][0]["departureStation"] = "ВSE"   # Cyrillic В
    proc, out, _ = run_adapter(tmp_path, meridian, make_solver())
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    codes = sorted(a["code"] for a in payload["airports"])
    assert codes == ["BSE", "FRU", "SEC"]
    assert all(c.isascii() for c in codes)


def test_duty_without_complements_falls_through(tmp_path):
    # D2$1 loses its complements -> the 7С909 leg falls through Meridian
    # (no signature) and, having no solver duty, lands on the aircraft-
    # type mode (its solver acType А320__ canonicalizes to A320 — which
    # has no observed signature, so with A320neo mode absent it would be
    # unresolved; give it the same acType as the observed legs instead).
    meridian = make_meridian()
    meridian["duties"][1]["complements"] = []
    solver = make_solver()
    solver["legs"][4]["acType"] = "A320neo"
    proc, out, check = run_adapter(tmp_path, meridian, solver)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["seat_source"]["actype_mode"] == 2       # ZZ303 + 7C909
    payload = json.loads(out.read_text(encoding="utf-8"))
    seats = slots_of(payload, "7C909-20260812-1")
    assert [s["requiredSkill"] for s in seats] == ["PURS", "CA", "CA"]


def test_solver_reference_without_legs_or_stations(tmp_path):
    # A degenerate solver reference (no legs/stations keys) must behave
    # exactly like --solver absent for enrichment: no aircraft types, no
    # geo, guard never engages (and with zero shared legs that is NOT an
    # error) — seat resolution falls back and fails loudly on the two
    # legs Meridian alone cannot cover.
    proc, out, check = run_adapter(tmp_path, make_meridian(), {"format": 5})
    assert proc.returncode == 1
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["utc_guard_checked"] == 0
    assert doc["seat_unresolved_total"] == 2
    assert "UTC guard could not engage" not in (proc.stdout + proc.stderr)


def test_pii_sex_field_never_reaches_payload(converted):
    payload, _ = converted
    for e in payload["employees"]:
        for banned in ("squadron", "birth", "sex", "citizenship",
                       "mentors", "doNotFlyWith"):
            assert banned not in e, (e["id"], banned)


def test_emit_catalog_partitions_flights_with_own_seats(tmp_path):
    """Путь Б: --emit-catalog строит вендорный каталог — юниты-наряды с
    СОБСТВЕННЫМ составом мест, образующие точное разбиение рейсов; состав
    юнита байт-совпадает с местами каждого его участка."""
    src = tmp_path / "meridian.json"
    src.write_text(json.dumps(make_meridian(), ensure_ascii=False),
                   encoding="utf-8")
    sol = tmp_path / "solver.json"
    sol.write_text(json.dumps(make_solver()), encoding="utf-8")
    out = tmp_path / "payload.json"
    check = tmp_path / "check.json"
    cat = tmp_path / "catalog.json"
    proc = subprocess.run(
        [sys.executable, str(ADAPTER), str(src), "--solver", str(sol),
         "--out", str(out), "--check", str(check),
         "--emit-catalog", str(cat), "--catalog-label", "pytest-cat"],
        capture_output=True, text=True, cwd=REPO,
        encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    doc = json.loads(cat.read_text(encoding="utf-8"))
    assert doc["label"] == "pytest-cat"
    assert doc["profile"] == "meridian-duties"
    assert doc["ip_status"] == "VENDOR_CATALOG"
    units = doc["selected"]
    # Точное разбиение: каждый рейс ровно в одном юните.
    unit_fns = [fn for u in units for fn in u["fns"]]
    assert sorted(unit_fns) == sorted(f["flightNumber"]
                                      for f in payload["flights"])
    # Юнит = ПЕЙРИНГ вендора (ревью 2026-07-24): наряд D1$1 принадлежит
    # пейрингу D1 — юнит с ДВУМЯ участками и составом PURS+CA+CA.
    d1 = next(u for u in units if len(u["fns"]) == 2)
    assert d1["fns"] == ["ZZ101-20260805-1", "ZZ101-20260805-2"]
    assert d1["seats"] == ["PURS", "CA", "CA"]
    assert d1["sourceUnit"] == "meridian:D1"
    # Состав юнита >= места каждого его участка (поролевой максимум);
    # на однонарядных пейрингах фикстуры — равенство, порядок включительно.
    slots_by_fn = {}
    for a in payload["flightAssignments"]:
        slots_by_fn.setdefault(a["flight"], []).append(a)
    for u in units:
        for fn in u["fns"]:
            seats = [a["requiredSkill"] for a in
                     sorted(slots_by_fn[fn],
                            key=lambda a: a["indexInFlight"])]
            assert seats == u["seats"], (fn, u["sourceUnit"])
    # Источники юнитов: пейринги Меридиана, наряд солвера, мода-одиночка.
    kinds = {u["sourceUnit"].split(":")[0] for u in units}
    assert kinds == {"meridian", "solver", "mode"}
    chk = json.loads(check.read_text(encoding="utf-8"))
    assert chk["catalog_units"] == len(units)
    # Замкнутость: D1 (BSE->SEC->BSE) замкнут; одиночки — нет.
    assert chk["catalog_units_closed"] >= 1
    assert chk["catalog_units_varying_composition"] == 0


def test_emit_catalog_pairing_unit_takes_role_max(tmp_path):
    """Пейринг с нарядами РАЗНОГО состава: юнит несёт поролевой максимум,
    участки сохраняют собственный спрос, счётчик плавающих составов
    честен (ревью 2026-07-24, P1 — юнит должен быть замкнутым пейрингом)."""
    meridian = make_meridian()
    # Второй наряд того же пейринга D1, состав шире: 2×CA + 1×CA C-cl.
    meridian["duties"].append(
        {"dutyId": "D1$2", "pairingId": "D1", "numberOfDuty": 2,
         "complements": [{"complementRank": "CA"},
                         {"complementRank": "CA"},
                         {"complementRank": "CA C-cl"}]})
    meridian["legs"].append({
        "legId": "ZZ103$06.08.2026$1", "flightNumber": "ZZ103",
        "carrier": "ZZ", "legNumber": 1, "deadhead": False,
        "departureStation": "SEC", "arrivalStation": "BSE",
        "start": "2026-08-06T05:00:00.000",
        "end": "2026-08-06T06:00:00.000",
        "dutyCrewTypes": {"D1$2": "Р"}})
    solver = make_solver()
    solver["legs"].append(dict(solver["legs"][0],
                               id="ZZ103$06.08.2026$1",
                               start="2026-08-06T10:00:00+05:00"
                                     "[Asia/Karachi]",
                               end="2026-08-06T11:00:00+05:00"
                                   "[Asia/Karachi]"))
    src = tmp_path / "meridian.json"
    src.write_text(json.dumps(meridian, ensure_ascii=False),
                   encoding="utf-8")
    sol = tmp_path / "solver.json"
    sol.write_text(json.dumps(solver), encoding="utf-8")
    cat = tmp_path / "catalog.json"
    proc = subprocess.run(
        [sys.executable, str(ADAPTER), str(src), "--solver", str(sol),
         "--out", str(tmp_path / "p.json"),
         "--check", str(tmp_path / "c.json"),
         "--emit-catalog", str(cat)],
        capture_output=True, text=True, cwd=REPO,
        encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    doc = json.loads(cat.read_text(encoding="utf-8"))
    d1 = next(u for u in doc["selected"] if u["sourceUnit"] == "meridian:D1")
    # Три участка пейринга в хронологии; состав — поролевой максимум:
    # PURS 1 (из D1$1), CA C-cl 1 (из D1$2), CA 2 (максимум из 2 и 2).
    assert d1["fns"] == ["ZZ101-20260805-1", "ZZ101-20260805-2",
                         "ZZ103-20260806-1"]
    assert d1["seats"] == ["PURS", "CA C-cl", "CA", "CA"]
    chk = json.loads((tmp_path / "c.json").read_text(encoding="utf-8"))
    assert chk["catalog_units_varying_composition"] == 1


def test_window_clips_by_pairing_not_by_leg(tmp_path):
    """Инкремент 1 (2026-07-24): пейринг, зацепивший окно хоть одним
    участком, входит ЦЕЛИКОМ — июльский хвост не отрезается, юнит остаётся
    замкнутым. Сироты вне окна (ZZ404) исключаются по-прежнему."""
    meridian = make_meridian()
    meridian["duties"].append(
        {"dutyId": "D5$1", "pairingId": "D5",
         "complements": [{"complementRank": "CA"}]})
    meridian["legs"] += [
        {"legId": "ZZ900$31.07.2026$1", "flightNumber": "ZZ900",
         "carrier": "ZZ", "legNumber": 1, "deadhead": False,
         "departureStation": "BSE", "arrivalStation": "SEC",
         "start": "2026-07-31T20:00:00.000",
         "end": "2026-07-31T21:00:00.000",
         "dutyCrewTypes": {"D5$1": "Р"}},
        {"legId": "ZZ901$01.08.2026$1", "flightNumber": "ZZ901",
         "carrier": "ZZ", "legNumber": 1, "deadhead": False,
         "departureStation": "SEC", "arrivalStation": "BSE",
         "start": "2026-08-01T05:00:00.000",
         "end": "2026-08-01T06:00:00.000",
         "dutyCrewTypes": {"D5$1": "Р"}},
    ]
    solver = make_solver()
    proc, out, check = run_adapter(tmp_path, meridian, solver,
                                   "--emit-catalog",
                                   str(tmp_path / "cat.json"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    fns = {f["flightNumber"] for f in payload["flights"]}
    assert "ZZ900-20260731-1" in fns          # июльский хвост включён
    assert not any(f.startswith("ZZ404") for f in fns)   # сирота — нет
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["boundary_legs_included"] == 1
    assert doc["legs_excluded"]["outside_range"] == 1    # только ZZ404
    assert doc["effective_span"][0] == "2026-07-31"
    cat = json.loads((tmp_path / "cat.json").read_text(encoding="utf-8"))
    d5 = next(u for u in cat["selected"]
              if u["sourceUnit"] == "meridian:D5")
    assert d5["fns"] == ["ZZ900-20260731-1", "ZZ901-20260801-1"]


def test_deadhead_role_leg_rides_in_unit_fns(tmp_path):
    """Инкремент 2 (2026-07-24): участок, входящий в наряд ролью D/H
    (экипаж летит пассажиром), попадает в fns юнита этого пейринга, но НЕ
    в active_fns — цепочка непрерывна, спрос обслуживает юнит-владелец."""
    meridian = make_meridian()
    # ZZ202 едет пассажиром в наряде D1$1 (между двумя его участками
    # хронологически он не встанет — ставим третьим, важна лишь механика).
    meridian["legs"][2]["dutyCrewTypes"] = {"D1$1": "D/H"}
    solver = make_solver()
    proc, out, check = run_adapter(tmp_path, meridian, solver,
                                   "--emit-catalog",
                                   str(tmp_path / "cat.json"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    cat = json.loads((tmp_path / "cat.json").read_text(encoding="utf-8"))
    d1 = next(u for u in cat["selected"]
              if u["sourceUnit"] == "meridian:D1")
    assert "ZZ202-20260810-1" in d1["fns"]
    assert "ZZ202-20260810-1" not in d1["active_fns"]
    # Сам ZZ202 при этом активен в СВОЁМ юните (солверный наряд SD1).
    owner = next(u for u in cat["selected"]
                 if "ZZ202-20260810-1" in u["active_fns"])
    assert owner["sourceUnit"].startswith("solver:")
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["catalog_rider_legs_attached"] == 1


def _solver_with_sequences() -> tuple[dict, dict]:
    """Вариант фикстур: солвер несёт sequences + назначения с duty→sequence
    (восстановимая пейринговая структура солвера), ZZ203 замыкает ротацию
    SQ1 = ZZ202 (BSE→FRU) + ZZ203 (FRU→BSE)."""
    meridian = make_meridian()
    meridian["legs"].append({
        "legId": "ZZ203$10.08.2026$2", "flightNumber": "ZZ203",
        "carrier": "ZZ", "legNumber": 2, "deadhead": False,
        "departureStation": "FRU", "arrivalStation": "BSE",
        "start": "2026-08-10T10:00:00.000",
        "end": "2026-08-10T11:00:00.000",
        "dutyCrewTypes": {}})
    solver = make_solver()
    solver["legs"].append(dict(solver["legs"][2],
                               id="ZZ203$10.08.2026$2",
                               start="2026-08-10T15:00:00+05:00"
                                     "[Asia/Karachi]",
                               end="2026-08-10T16:00:00+05:00"
                                   "[Asia/Karachi]"))
    solver["sequences"] = [
        {"id": "SQ1_CA_1", "sequenceId": "SQ1", "crewPosition": "CA"}]
    solver["leg2Duties"].append(
        {"leg": "ZZ203$10.08.2026$2", "duty": "SD2", "crewRole": "STD"})
    solver["employeeAssignments"] = [
        {"id": "a1", "duty": "SD1", "preferredRank": "CA",
         "sequence": "SQ1_CA_1"},
        {"id": "a2", "duty": "SD1", "preferredRank": "CA",
         "sequence": "SQ1_CA_1"},
        {"id": "a3", "duty": "SD2", "preferredRank": "CA",
         "sequence": "SQ1_CA_1"},
        {"id": "a4", "duty": "SD2", "preferredRank": "CA",
         "sequence": "SQ1_CA_1"},
    ]
    return meridian, solver


def test_solver_duties_collapse_into_sequence_pairing(tmp_path):
    """Инкремент 3 (2026-07-24): наряды солвера с общим sequenceId
    сворачиваются в ОДИН замкнутый юнит-пейринг «sequence:<id>» —
    восстановление пейрингов из солвер-файла детерминированным кодом."""
    meridian, solver = _solver_with_sequences()
    cat = tmp_path / "cat.json"
    proc, out, check = run_adapter(tmp_path, meridian, solver,
                                   "--emit-catalog", str(cat))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    doc = json.loads(cat.read_text(encoding="utf-8"))
    sq1 = next(u for u in doc["selected"]
               if u["sourceUnit"] == "sequence:SQ1")
    assert sq1["fns"] == ["ZZ202-20260810-1", "ZZ203-20260810-2"]
    assert sq1["seats"] == ["CA", "CA"]
    chk = json.loads(check.read_text(encoding="utf-8"))
    assert chk["catalog_units_by_kind"].get("sequence") == 1
    # Ротация замкнута: BSE -> FRU -> BSE.
    payload = json.loads(out.read_text(encoding="utf-8"))
    by_fn = {f["flightNumber"]: f for f in payload["flights"]}
    assert by_fn[sq1["fns"][0]]["departureAirport"] == "BSE"
    assert by_fn[sq1["fns"][-1]]["arrivalAirport"] == "BSE"


def test_catalog_source_solver_prefers_solver_structure(tmp_path):
    """--catalog-source solver: состав/владелец берутся из структуры
    солвера ПЕРВЫМИ (меридиан — фолбэк), метка источника пишется в чек."""
    meridian, solver = _solver_with_sequences()
    cat = tmp_path / "cat.json"
    proc, out, check = run_adapter(tmp_path, meridian, solver,
                                   "--emit-catalog", str(cat),
                                   "--catalog-source", "solver")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    chk = json.loads(check.read_text(encoding="utf-8"))
    assert chk["catalog_source"] == "solver"
    # ZZ202/ZZ203 покрыты солвером -> solver_duty; участки лишь Меридиана
    # (ZZ101×2, 7C909) — фолбэк meridian_duty; мода прежняя (ZZ303).
    assert chk["seat_source"]["solver_duty"] == 2
    assert chk["seat_source"]["meridian_duty"] == 3
    doc = json.loads(cat.read_text(encoding="utf-8"))
    kinds = {u["sourceUnit"].split(":")[0] for u in doc["selected"]}
    assert "sequence" in kinds and "meridian" in kinds


def test_check_artifact_written_even_on_failure(tmp_path):
    src = tmp_path / "meridian.json"
    src.write_text("{not json", encoding="utf-8")
    check = tmp_path / "check.json"
    proc = subprocess.run(
        [sys.executable, str(ADAPTER), str(src),
         "--out", str(tmp_path / "p.json"), "--check", str(check)],
        capture_output=True, text=True, cwd=REPO,
        encoding="utf-8", errors="replace")
    assert proc.returncode == 1
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["ok"] is False
    assert doc["errors_total"] == 1


def test_emit_catalog_carries_duty_boundaries(tmp_path):
    """Каталог обязан нести границы смен (`duty_fns`). Без них уровень
    свободы 2 («сохраняем смены заказчика, цепочки строим заново»)
    невыполним: построитель считает пейринг одной сменой и молча
    подменяет постановку уровнем 1. Наряды в источнике заданы явно —
    терять их в каталоге нельзя."""
    meridian = make_meridian()
    meridian["duties"].append(
        {"dutyId": "D1$2", "pairingId": "D1", "numberOfDuty": 2,
         "complements": [{"complementRank": "PURS"}]})
    meridian["legs"].append({
        "legId": "ZZ103$06.08.2026$1", "flightNumber": "ZZ103",
        "carrier": "ZZ", "legNumber": 1, "deadhead": False,
        "departureStation": "SEC", "arrivalStation": "BSE",
        "start": "2026-08-06T05:00:00.000",
        "end": "2026-08-06T06:00:00.000",
        "dutyCrewTypes": {"D1$2": "Р"}})
    solver = make_solver()
    solver["legs"].append(dict(solver["legs"][0],
                               id="ZZ103$06.08.2026$1",
                               start="2026-08-06T10:00:00+05:00"
                                     "[Asia/Karachi]",
                               end="2026-08-06T11:00:00+05:00"
                                   "[Asia/Karachi]"))
    src = tmp_path / "meridian.json"
    src.write_text(json.dumps(meridian, ensure_ascii=False), encoding="utf-8")
    sol = tmp_path / "solver.json"
    sol.write_text(json.dumps(solver), encoding="utf-8")
    cat = tmp_path / "catalog.json"
    proc = subprocess.run(
        [sys.executable, str(ADAPTER), str(src), "--solver", str(sol),
         "--out", str(tmp_path / "p.json"),
         "--check", str(tmp_path / "c.json"),
         "--emit-catalog", str(cat)],
        capture_output=True, text=True, cwd=REPO,
        encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    doc = json.loads(cat.read_text(encoding="utf-8"))
    assert all("duty_fns" in u for u in doc["selected"]), \
        "каталог без границ смен ломает уровень свободы 2"
    d1 = next(u for u in doc["selected"] if u["sourceUnit"] == "meridian:D1")
    # два наряда пейринга: первый из двух участков, второй из одного
    assert d1["duty_fns"] == [["ZZ101-20260805-1", "ZZ101-20260805-2"],
                              ["ZZ103-20260806-1"]]
    # разбивка обязана покрывать все рабочие участки юнита без потерь
    flat = [fn for grp in d1["duty_fns"] for fn in grp]
    assert sorted(flat) == sorted(d1["active_fns"])


def test_catalog_marks_unit_kind_and_completeness(tmp_path):
    """Вид юнита — явное поле каталога, а не вывод из служебного имени.
    Цепочка (пейринг вендора, ротация решателя) несёт `kind: chain` и
    счётчик потерянных участков; достройка адаптера — одиночный участок
    по моде типа судна, голый наряд без ротации — `kind: fill`: у неё нет
    ни начала, ни конца командировки, и замкнутость на базу к ней
    неприменима по определению."""
    cat = tmp_path / "cat.json"
    proc, _out, check = run_adapter(tmp_path, make_meridian(), make_solver(),
                                    "--emit-catalog", str(cat))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    units = json.loads(cat.read_text(encoding="utf-8"))["selected"]
    by_src = {u["sourceUnit"]: u for u in units}
    assert by_src["meridian:D1"]["kind"] == "chain"
    assert by_src["meridian:D1"]["sourceLegsMissing"] == 0
    mode = next(u for u in units if u["sourceUnit"].startswith("mode:"))
    solver_unit = next(u for u in units
                       if u["sourceUnit"].startswith("solver:"))
    assert mode["kind"] == "fill"
    assert solver_unit["kind"] == "fill"
    # У достройки счётчика целости нет вовсе: сравнивать её не с чем.
    assert "sourceLegsMissing" not in mode
    chk = json.loads(check.read_text(encoding="utf-8"))
    assert chk["catalog_units_fill"] == sum(1 for u in units
                                            if u["kind"] == "fill")
    assert chk["catalog_chains_complete"] == sum(
        1 for u in units if u["kind"] == "chain" and not u["sourceLegsMissing"])
    assert chk["catalog_chains_truncated"] == 0


def test_leg_taken_by_another_pairing_marks_chain_truncated(tmp_path):
    """Участок числится сразу в двух пейрингах, а владелец у него один:
    проигравший пейринг остаётся урезанным и обязан сказать об этом полем
    `sourceLegsMissing`. Иначе он выглядит незамкнутым, и дефект стыковки
    выдаётся за дефект данных заказчика."""
    meridian = make_meridian()
    # Тот же участок ZZ101$05.08.2026$2 числится и в наряде D3$1
    # (пейринг D3), у которого есть ещё и свой собственный участок.
    meridian["legs"][1]["dutyCrewTypes"] = {"D1$1": "Р", "D3$1": "Р"}
    meridian["duties"].append(
        {"dutyId": "D3$1", "pairingId": "D3", "numberOfDuty": 1,
         "complements": [{"complementRank": "PURS"},
                         {"complementRank": "CA"},
                         {"complementRank": "CA"},
                         {"complementRank": "CA"}]})
    meridian["legs"].append({
        "legId": "ZZ104$07.08.2026$1", "flightNumber": "ZZ104",
        "carrier": "ZZ", "legNumber": 1, "deadhead": False,
        "departureStation": "BSE", "arrivalStation": "SEC",
        "start": "2026-08-07T05:00:00.000",
        "end": "2026-08-07T06:00:00.000",
        "dutyCrewTypes": {"D3$1": "Р"}})
    cat = tmp_path / "cat.json"
    proc, _out, check = run_adapter(tmp_path, meridian, make_solver(),
                                    "--emit-catalog", str(cat))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    units = {u["sourceUnit"]: u
             for u in json.loads(cat.read_text(encoding="utf-8"))["selected"]}
    winner, loser = ("meridian:D3", "meridian:D1") \
        if "ZZ101-20260805-2" in units["meridian:D3"]["fns"] \
        else ("meridian:D1", "meridian:D3")
    assert units[winner]["sourceLegsMissing"] == 0
    assert units[loser]["sourceLegsMissing"] == 1
    chk = json.loads(check.read_text(encoding="utf-8"))
    assert chk["catalog_chains_truncated"] == 1


def test_solver_rotation_gets_its_positioning_legs(tmp_path):
    """Перегонные строки решателя ссылаются на участок с приставкой
    `dh%` — тот же самый участок, что и без неё. Без снятия приставки
    ротации не привязывается НИ ОДИН позиционный перелёт, и юнит теряет
    привязку к базе."""
    meridian, solver = _solver_with_sequences()
    # Экипаж ротации SQ1 возвращается в базу пассажиром рейсом ZZ303:
    # решатель ссылается на него материализованным перегоном.
    solver["leg2Duties"].append({"leg": "dh%ZZ303$15.08.2026$1",
                                 "duty": "SD2", "crewRole": "LAST_DH"})
    cat = tmp_path / "cat.json"
    proc, _out, check = run_adapter(tmp_path, meridian, solver,
                                    "--emit-catalog", str(cat))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    units = json.loads(cat.read_text(encoding="utf-8"))["selected"]
    sq1 = next(u for u in units if u["sourceUnit"] == "sequence:SQ1")
    assert "ZZ303-20260815-1" in sq1["fns"], \
        "приставка dh% не снята — позиционный перелёт потерян"
    assert "ZZ303-20260815-1" not in sq1["active_fns"]
    assert sq1["kind"] == "chain" and sq1["sourceLegsMissing"] == 0
    chk = json.loads(check.read_text(encoding="utf-8"))
    assert chk["catalog_rider_legs_attached"] == 1


def test_solver_rotation_keeps_its_duty_boundaries(tmp_path):
    """Ротация решателя состоит из НЕСКОЛЬКИХ нарядов. Отдать её одной
    группой — значит выдать многодневную командировку за одну смену и
    молча выродить уровень свободы 2 (та же мина, что закрыта для нарядов
    «Меридиана»)."""
    meridian, solver = _solver_with_sequences()
    cat = tmp_path / "cat.json"
    proc, _out, _check = run_adapter(tmp_path, meridian, solver,
                                     "--emit-catalog", str(cat))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    units = json.loads(cat.read_text(encoding="utf-8"))["selected"]
    sq1 = next(u for u in units if u["sourceUnit"] == "sequence:SQ1")
    # SD1 (ZZ202) и SD2 (ZZ203) — две смены, а не одна.
    assert sq1["duty_fns"] == [["ZZ202-20260810-1"], ["ZZ203-20260810-2"]]
    flat = [fn for grp in sq1["duty_fns"] for fn in grp]
    assert sorted(flat) == sorted(sq1["active_fns"])


def test_personal_monthly_limits_reach_the_payload():
    """Личные месячные пределы сотрудника файл «Меридиана» несёт, а
    адаптер их терял: у всех выходил ноль, и решатель судил по общему
    пределу. Пределы в фикстуре ниже — вымышленные тестовые значения."""
    import json
    import subprocess
    import sys
    import tempfile
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    src = {
        "format": "1", "type": "pairing", "division": "CC",
        "planSince": "2026-08-01T00:00:00", "planUntil": "2026-08-31T23:59:00",
        "legs": [{"legId": "ZZ001$01.08.2026$1", "flightNumber": "ZZ001",
                  "legNumber": 1, "carrier": "ZZ", "fleet": "A320",
                  "deadhead": False, "properties": "",
                  "departureStation": "BSE", "arrivalStation": "IST",
                  "start": "2026-08-01T06:00:00", "end": "2026-08-01T10:00:00",
                  "dutyCrewTypes": {"D1": "Р"}}],
        "duties": [{"dutyId": "D1", "pairingId": "P1", "numberOfDuty": 1,
                    "start": "2026-08-01T06:00:00",
                    "end": "2026-08-01T10:00:00",
                    "firstDepartureStation": "BSE",
                    "lastArrivalStation": "IST",
                    "stationsVisited": ["BSE", "IST"],
                    "flightTimeCredit": "240", "workTimeCredit": "240",
                    "complements": [{"complementRank": "CA",
                                     "employeeId": None, "locked": False,
                                     "requirements": []}],
                    "restOverride": None, "split": False}],
        "pairings": [{"pairingId": "P1", "start": "2026-08-01T06:00:00",
                      "end": "2026-08-01T10:00:00", "locked": False,
                      "primaryPairing": None, "requirements": [],
                      "restOverride": None, "flightMinutesCredit": "240",
                      "workMinutesCredit": "240"}],
        "airports": [{"code": "BSE", "name": "BSE"},
                     {"code": "IST", "name": "IST"}],
        "employees": [{"employeeId": "E1", "base": "BSE",
                       "flightTimeLimit": 6000, "workTimeLimit": 8640,
                       "features": [], "fleets": ["A320"],
                       "stationsDenied": [], "doNotFlyWith": []}],
        "groundActivities": [],
    }
    with tempfile.TemporaryDirectory() as td:
        p_in = Path(td) / "src.json"
        p_out = Path(td) / "out.json"
        p_in.write_text(json.dumps(src, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(root / "_meridian_to_payload.py"), str(p_in),
             "--out", str(p_out), "--check", str(Path(td) / "chk.json")],
            capture_output=True, cwd=str(root))
        assert p_out.exists(), r.stdout.decode("utf-8", "replace")[-600:]
        pay = json.loads(p_out.read_text(encoding="utf-8"))
    emp = pay["employees"][0]
    assert emp["flightTimeLimitMin"] == 6000, emp
    assert emp["workTimeLimitMin"] == 8640, emp
