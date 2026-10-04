"""Tests for the SSIM chapter-7 import adapter (_ssim_to_payload.py).

Runs the adapter as a subprocess (the same way the pipeline does) against
data/samples/sample.ssim and checks: expansion counts, weekday masks,
overnight arrivals (explicit date variation and inferred), a hand-computed
UTC conversion, loud failure on malformed records, loud failure without
--bases, schema validity of the output, and the conversion check artifact.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ADAPTER = REPO / "_ssim_to_payload.py"
SAMPLE = REPO / "data" / "samples" / "sample.ssim"
SCHEMA = REPO / "webapp" / "schema" / "canonical_payload.schema.json"


def run_adapter(tmp_path, *extra_args, ssim=SAMPLE, bases="ATL:2,LAX:1"):
    """Run the adapter; return (proc, payload_path, check_path)."""
    out = tmp_path / "payload.json"
    check = tmp_path / "check.json"
    cmd = [sys.executable, str(ADAPTER), str(ssim),
           "--out", str(out), "--check", str(check)]
    if bases is not None:
        cmd += ["--bases", bases]
    cmd += list(extra_args)
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO)
    return proc, out, check


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    """One full-period conversion shared by the read-only assertions."""
    tmp = tmp_path_factory.mktemp("ssim_full")
    proc, out, check = run_adapter(tmp)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    check_doc = json.loads(check.read_text(encoding="utf-8"))
    return payload, check_doc


def flights_by_id(payload):
    return {f["flightNumber"]: f for f in payload["flights"]}


def test_expansion_count_full_period(converted):
    payload, check = converted
    # XX100 daily x3 + XX101 Sat/Mon x2 + XX200 + XX201 + XX300 + XX400
    assert len(payload["flights"]) == 9
    assert check["flights_expanded"] == 9
    assert check["records_parsed_type3"] == 6
    assert len(payload["flightAssignments"]) == 9
    # one slot per flight, indexInFlight 0, Captain
    for a in payload["flightAssignments"]:
        assert a["indexInFlight"] == 0
        assert a["requiredSkill"] == "Captain"
        assert a["employee"] is None
    assert {a["flight"] for a in payload["flightAssignments"]} \
        == set(flights_by_id(payload))


def test_weekday_mask_respected(converted):
    payload, _ = converted
    ids = set(flights_by_id(payload))
    # XX101 operates Mondays and Saturdays only: 2026-08-01 (Sat) and
    # 2026-08-03 (Mon), NOT 2026-08-02 (Sun).
    assert "XX101-1-2026-08-01" in ids
    assert "XX101-1-2026-08-03" in ids
    assert "XX101-1-2026-08-02" not in ids


def test_overnight_arrival_explicit_date_variation(converted):
    payload, _ = converted
    f = flights_by_id(payload)["XX200-1-2026-08-01"]
    # LAX 21:30 -0700 on Aug 1 -> 2026-08-02T04:30Z; arrival date
    # variation '1': LHR 15:45 +0100 on Aug 2 -> 2026-08-02T14:45Z.
    assert f["departureUTCDateTime"] == "2026-08-02T04:30:00"
    assert f["arrivalUTCDateTime"] == "2026-08-02T14:45:00"
    assert f["arrivalUTCDateTime"] > f["departureUTCDateTime"]


def test_overnight_arrival_inferred(converted):
    payload, _ = converted
    f = flights_by_id(payload)["XX300-1-2026-08-01"]
    # No date-variation field; local arrival 00:45 < local departure 23:00
    # -> next day. ATL 23:00 -0400 -> 2026-08-02T03:00Z; LAX 00:45 -0700
    # on Aug 2 -> 2026-08-02T07:45Z.
    assert f["departureUTCDateTime"] == "2026-08-02T03:00:00"
    assert f["arrivalUTCDateTime"] == "2026-08-02T07:45:00"


def test_utc_conversion_hand_computed(converted):
    payload, _ = converted
    f = flights_by_id(payload)["XX201-1-2026-08-02"]
    # LHR 11:00 +0100 -> 10:00 UTC; ATL 15:10 -0400 -> 19:10 UTC.
    assert f["departureUTCDateTime"] == "2026-08-02T10:00:00"
    assert f["arrivalUTCDateTime"] == "2026-08-02T19:10:00"
    assert f["aircraftType"] == "77W"


def test_date_range_subset(tmp_path):
    proc, out, _ = run_adapter(tmp_path, "--from", "2026-08-01",
                               "--to", "2026-08-01")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    # Aug 1 only: XX100, XX101, XX200, XX300
    assert sorted(flights_by_id(payload)) == [
        "XX100-1-2026-08-01", "XX101-1-2026-08-01",
        "XX200-1-2026-08-01", "XX300-1-2026-08-01"]


def test_output_validates_against_schema(converted):
    import jsonschema
    payload, _ = converted
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(payload, schema)   # raises on failure
    # referential integrity the webapp checks on upload
    airports = {a["code"] for a in payload["airports"]}
    for f in payload["flights"]:
        assert f["departureAirport"] in airports
        assert f["arrivalAirport"] in airports
    for e in payload["employees"]:
        assert e["homeAirport"] in airports
    assert payload["provenance"]["adapter"] == "ssim"
    assert payload["provenance"]["source"] == "sample.ssim"


def test_crew_synthesis_from_bases(converted):
    payload, _ = converted
    assert len(payload["employees"]) == 3   # ATL:2 + LAX:1
    homes = sorted(e["homeAirport"] for e in payload["employees"])
    assert homes == ["ATL", "ATL", "LAX"]
    for e in payload["employees"]:
        assert e["skills"] == ["Captain"]
        assert e["typeRatings"] == ["320", "77W"]   # aircraft types seen


def test_bases_required(tmp_path):
    proc, out, _ = run_adapter(tmp_path, bases=None)
    assert proc.returncode != 0
    assert "--bases" in (proc.stdout + proc.stderr)
    assert not out.exists()


def test_base_not_in_schedule_fails(tmp_path):
    proc, out, _ = run_adapter(tmp_path, bases="ZZZ:5")
    assert proc.returncode != 0
    assert "ZZZ" in (proc.stdout + proc.stderr)
    assert not out.exists()


def test_malformed_type3_fails_loudly(tmp_path):
    # Corrupt the month of the first type-3 record's period-from field.
    lines = SAMPLE.read_text(encoding="ascii").splitlines()
    idx = next(i for i, ln in enumerate(lines) if ln.startswith("3"))
    ln = lines[idx]
    lines[idx] = ln[:14] + "01XXX26" + ln[21:]   # cols 15-21 = period-from
    bad = tmp_path / "bad.ssim"
    bad.write_text("\n".join(lines) + "\n", encoding="ascii")

    proc, out, check = run_adapter(tmp_path, ssim=bad)
    assert proc.returncode != 0
    assert "malformed" in (proc.stdout + proc.stderr).lower()
    assert not out.exists()          # no payload on failure
    # check artifact is still written (audit trail), and lists the error
    doc = json.loads(check.read_text(encoding="utf-8"))
    assert doc["ok"] is False
    assert len(doc["malformed_type3"]) == 1
    assert doc["malformed_type3"][0]["line"] == idx + 1
    assert "XXX" in doc["malformed_type3"][0]["error"]


def test_unknown_record_types_skipped_with_count(converted):
    _, check = converted
    skipped = check["records_skipped_by_type"]
    # header, carrier, segment, trailer + the synthetic unknown type '8'
    assert skipped == {"1": 1, "2": 1, "4": 1, "5": 1, "8": 1}
    assert check["records_total"] == 11
    assert check["ok"] is True
    assert check["offset_violations"] == []
    assert check["duration_violations"] == []
    assert check["duplicate_flight_ids"] == []
