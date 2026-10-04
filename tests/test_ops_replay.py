"""Phase-2 day-of-ops replay: BTS import (ops_replay.import_bts) and the
idempotent «Сбой»-event generation (ops_replay.generate_disruptions).
A tiny synthetic BTS CSV stands in for the 280 MB bundle; the disruption
store is redirected to a tmp file."""
import csv
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _flight(fn, dep_utc):
    return {"flightNumber": fn, "departureAirport": "ATL",
            "arrivalAirport": "LAS",
            "departureUTCDateTime": dep_utc,
            "arrivalUTCDateTime": dep_utc.replace("T12", "T14"),
            "aircraftType": "NARROWBODY"}


# instance built for 2024-08-15; flightNumber = DL{num}-{local HHMM}
PAYLOAD = {"flights": [
    _flight("DL555-1215", "2024-08-15T16:15:00"),
    _flight("DL700-0800", "2024-08-15T12:00:00"),
    _flight("DL900-1900", "2024-08-16T00:00:00"),   # late: UTC rolls to +1
]}

_BTS_HEADER = ["FlightDate", "Reporting_Airline",
               "Flight_Number_Reporting_Airline", "CRSDepTime", "DepDelay",
               "ArrDelay", "Cancelled", "Diverted", "CarrierDelay",
               "WeatherDelay", "NASDelay", "SecurityDelay",
               "LateAircraftDelay", "Origin", "Dest"]


def _row(date, num, crs, **kw):
    r = {k: "" for k in _BTS_HEADER}
    r.update({"FlightDate": date, "Reporting_Airline": "DL",
              "Flight_Number_Reporting_Airline": num, "CRSDepTime": crs,
              "Origin": "ATL", "Dest": "LAS"})
    r.update(kw)
    return r


@pytest.fixture()
def bts_csv(tmp_path):
    path = tmp_path / "bts.csv"
    rows = [
        # our day: a delay w/ cause, a cancellation, and a late flight
        _row("2024-08-15", "555", "1215", DepDelay="45.00", ArrDelay="50.00",
             CarrierDelay="10", LateAircraftDelay="40"),
        _row("2024-08-15", "700", "0800", Cancelled="1.00",
             WeatherDelay="0"),
        # a late (20:00 local) flight of the SAME operating day 2024-08-15,
        # even though its UTC departure rolls to the 16th — BTS FlightDate
        # is local, so it is still the 15th
        _row("2024-08-15", "900", "1900", DepDelay="20.00", ArrDelay="18.00",
             NASDelay="20"),
        # SAME number+time but a DIFFERENT day — must NOT be imported
        _row("2024-08-14", "555", "1215", DepDelay="200.00",
             ArrDelay="200.00"),
        # a flight not in the instance — ignored
        _row("2024-08-15", "111", "0600", DepDelay="99.00"),
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_BTS_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


# ------------------------------------------------------------- import_bts

def test_import_matches_by_key_and_date(bts_csv):
    from webapp import ops_replay
    facts = ops_replay.import_bts(PAYLOAD, csv_path=bts_csv)
    assert set(facts) == {"DL555-1215", "DL700-0800", "DL900-1900"}
    # the 2024-08-14 DL555-1215 (200 min) was pinned out by flight date
    assert facts["DL555-1215"]["dep_delay_min"] == 45
    assert facts["DL555-1215"]["cause"] == "late_aircraft"   # dominant bucket
    assert facts["DL700-0800"]["cancelled"] is True
    assert facts["DL700-0800"]["dep_delay_min"] is None      # cancelled: no delay
    assert facts["DL900-1900"]["cause"] == "nas"


def test_import_missing_file(tmp_path):
    from webapp import ops_replay
    with pytest.raises(ops_replay.OpsReplayError):
        ops_replay.import_bts(PAYLOAD, csv_path=tmp_path / "nope.csv")


def test_fn_key_padding():
    from webapp import ops_replay
    assert ops_replay._fn_key("DL", "700", "800") == "DL700-0800"
    assert ops_replay._fn_key("DL", "555", "1215") == "DL555-1215"
    assert ops_replay._fn_key("DL", "1", "2400") == "DL1-0000"   # 24:00 -> 00:00
    assert ops_replay._fn_key("", "1", "800") is None


# -------------------------------------------------- generate_disruptions

@pytest.fixture()
def disruption_mod(tmp_path, monkeypatch):
    from webapp import disruption, registry
    monkeypatch.setattr(disruption, "DISRUPTIONS_PATH",
                        tmp_path / "disruptions.json")
    # add_flights / add_delay validate fns against the instance payload
    payload_path = tmp_path / "inst.json"
    payload_path.write_text(__import__("json").dumps(PAYLOAD),
                            encoding="utf-8")
    monkeypatch.setattr(disruption, "_payload_for", lambda inst: PAYLOAD)
    return disruption


def test_generate_events_and_idempotent(disruption_mod, monkeypatch):
    from webapp import ops_replay
    inst = {"id": "i1", "label": "i1"}
    facts = {
        "DL700-0800": {"cancelled": True},
        "DL555-1215": {"dep_delay_min": 45, "arr_delay_min": 50},
        "DL900-1900": {"dep_delay_min": 20, "arr_delay_min": 18},
        "DL_ontime": {"dep_delay_min": 3},        # below threshold -> ignored
    }
    r1 = ops_replay.generate_disruptions("i1", inst, facts,
                                         delay_threshold_min=15)
    assert r1["cancelledFlights"] == 1
    assert r1["delayedFlights"] == 2              # 45 and 20 (3 skipped)
    assert r1["cleared"] == 0
    evs = disruption_mod.events_for("i1")
    assert {e["kind"] for e in evs} == {"flights", "delay"}
    assert all(str(e["note"]).startswith("ops:auto") for e in evs)

    # re-run clears the prior auto events, not double-counts
    r2 = ops_replay.generate_disruptions("i1", inst, facts,
                                         delay_threshold_min=15)
    assert r2["cleared"] == len(evs)
    assert len(disruption_mod.events_for("i1")) == len(evs)


def test_generate_respects_threshold(disruption_mod):
    from webapp import ops_replay
    inst = {"id": "i1", "label": "i1"}
    facts = {"DL555-1215": {"dep_delay_min": 30}}
    r = ops_replay.generate_disruptions("i1", inst, facts,
                                        delay_threshold_min=60)
    assert r["delayedFlights"] == 0              # 30 < 60


def test_generate_clears_only_its_own_source(disruption_mod):
    """A manual event whose NOTE starts with 'ops:auto' but has no source
    marker must survive re-generation (audit 2026-07-17)."""
    from webapp import ops_replay
    inst = {"id": "i1", "label": "i1"}
    # a user-made cancellation event noted like the auto tag, but no source
    manual = disruption_mod.add_flights("i1", inst, ["DL700-0800"],
                                        note="ops:auto looks-automatic")
    ops_replay.generate_disruptions("i1", inst,
                                    {"DL555-1215": {"dep_delay_min": 45}})
    ops_replay.generate_disruptions("i1", inst,   # re-run clears ONLY auto
                                    {"DL555-1215": {"dep_delay_min": 45}})
    ids = {e["id"] for e in disruption_mod.events_for("i1")}
    assert manual["id"] in ids                    # manual event preserved
    assert any(e.get("source") == ops_replay.AUTO_SOURCE
               for e in disruption_mod.events_for("i1"))


def test_generate_threshold_zero_is_clamped(disruption_mod):
    """delay_threshold_min <= 0 would let a 0-minute shift through, which
    add_delay rejects — generate_disruptions clamps to >= 1 (audit)."""
    from webapp import ops_replay
    inst = {"id": "i1", "label": "i1"}
    facts = {"DL555-1215": {"dep_delay_min": 0},        # exactly on time
             "DL700-0800": {"dep_delay_min": 20}}
    r = ops_replay.generate_disruptions("i1", inst, facts,
                                        delay_threshold_min=0)
    assert r["delayedFlights"] == 1               # only the 20-min flight
    assert {e["shift_min"] for e in disruption_mod.events_for("i1")
            if e["kind"] == "delay"} == {20}


def test_import_picks_best_matching_date(bts_csv, tmp_path):
    """import_bts picks the FlightDate covering the MOST instance flights,
    with no UTC/local frame assumption (audit 2026-07-17). The 2024-08-14
    row for DL555-1215 loses to 2024-08-15 (3 matches)."""
    from webapp import ops_replay
    facts = ops_replay.import_bts(PAYLOAD, csv_path=bts_csv)
    assert facts["DL555-1215"]["dep_delay_min"] == 45   # 08-15, not the 200 of 08-14
