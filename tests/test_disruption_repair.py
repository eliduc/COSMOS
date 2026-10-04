"""Disruption Phase B (flight surgery): event kinds and their validation,
resolution of aircraft events via tailNumber, catalog split around the
affected legs, derived-payload construction (cancel/delay/sick), the
driver's merge + report logic, stage-2 employee/pinned passthrough, jobs
wiring, API endpoints."""
import importlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import _disruption_repair as drv
from webapp import disruption as dz
from webapp import jobs
from webapp import registry
from webapp import roster_board as rb
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL
USER = "someone@example.com"


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(dz, "DISRUPTIONS_PATH", tmp_path / "dz.json")
    monkeypatch.setattr(dz, "REPAIRS_PATH", tmp_path / "repairs.json")
    monkeypatch.setattr(dz, "REPAIR_RUNS", tmp_path / "repair-runs")
    monkeypatch.setattr(rb, "EDITS_PATH", tmp_path / "edits.json")
    yield tmp_path


def _inst():
    return registry.get_instance("gerad-1")


def _some_fns(n=2):
    payload = registry.load_payload(
        registry.instance_payload_path(_inst()))
    return [str(f["flightNumber"]) for f in payload["flights"][:n]]


# ------------------------------------------------------------------- events

def test_add_flights_validates(iso):
    inst = _inst()
    with pytest.raises(dz.DisruptionError):
        dz.add_flights("gerad-1", inst, [])
    with pytest.raises(dz.DisruptionError):
        dz.add_flights("gerad-1", inst, ["NO_SUCH_FLIGHT"])
    fns = _some_fns()
    ev = dz.add_flights("gerad-1", inst, fns, "буря", ADMIN)
    assert ev["kind"] == "flights" and ev["fns"] == sorted(set(fns))


def test_add_flights_accepts_comma_string(iso):
    inst = _inst()
    fns = _some_fns()
    ev = dz.add_flights("gerad-1", inst, ", ".join(fns))
    assert ev["fns"] == sorted(set(fns))


def test_add_delay_validates(iso):
    inst = _inst()
    fns = _some_fns(1)
    for bad in (0, "x", 99999, -99999, None):
        with pytest.raises(dz.DisruptionError):
            dz.add_delay("gerad-1", inst, fns, bad)
    ev = dz.add_delay("gerad-1", inst, fns, 90)
    assert ev["shift_min"] == 90


def test_add_aircraft_needs_tail_data(iso):
    """GERAD seed payloads carry no tailNumber — an aircraft event must be
    rejected with a pointer to the cancelled-flights kind."""
    inst = _inst()
    with pytest.raises(dz.DisruptionError, match="tailNumber"):
        dz.add_aircraft("gerad-1", inst, "RA-73001",
                        "2000-01-01", "2000-01-02")


def test_add_aircraft_resolves_snapshot(iso, tmp_path, monkeypatch):
    """With tailNumber data the event stores the resolved flight list."""
    inst = dict(_inst())
    payload = json.loads(json.dumps(registry.load_payload(
        registry.instance_payload_path(inst))))
    tail_fns = [str(f["flightNumber"]) for f in payload["flights"][:3]]
    for f in payload["flights"]:
        if str(f["flightNumber"]) in tail_fns:
            f["tailNumber"] = "RA-73001"
    p = tmp_path / "tails.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(dz, "_payload_for", lambda i: payload)
    day = payload["flights"][0]["departureUTCDateTime"][:10]
    days = sorted({str(f["departureUTCDateTime"])[:10]
                   for f in payload["flights"]
                   if str(f["flightNumber"]) in tail_fns})
    ev = dz.add_aircraft("gerad-1", inst, "RA-73001", days[0], days[-1])
    assert ev["kind"] == "aircraft"
    assert set(ev["fns"]) == set(tail_fns)
    with pytest.raises(dz.DisruptionError, match="no flights"):
        dz.add_aircraft("gerad-1", inst, "RA-99999", day, day)


def test_flight_disruptions_cancel_beats_delay(iso):
    inst = _inst()
    fns = _some_fns(3)
    dz.add_delay("gerad-1", inst, fns, 60)
    dz.add_delay("gerad-1", inst, [fns[1]], 120)    # later wins per flight
    dz.add_flights("gerad-1", inst, [fns[0]])       # cancel beats delay
    cancelled, delayed = dz.flight_disruptions("gerad-1")
    assert cancelled == {fns[0]}
    assert delayed[fns[1]] == 120 and delayed[fns[2]] == 60
    assert fns[0] not in delayed


# ----------------------------------------------------------- catalog split

def test_split_catalog_deadhead_counts_as_broken():
    records = [
        {"fns": ["F1", "F2"], "active_fns": ["F1", "F2"]},
        {"fns": ["F3", "F4"], "active_fns": ["F4"]},   # F3 is a deadhead
        {"fns": ["F5"], "active_fns": ["F5"]},
    ]
    untouched, broken = dz.split_catalog(records, {"F3"})
    assert untouched == [0, 2] and broken == [1]


def test_surgery_preview_on_seed(iso):
    inst = _inst()
    assert dz.surgery_preview("gerad-1", inst) == {"active": False}
    records = dz._catalog_records(inst)
    victim = str((records[0].get("active_fns")
                  or records[0]["fns"])[0])
    dz.add_flights("gerad-1", inst, [victim])
    pv = dz.surgery_preview("gerad-1", inst)
    assert pv["active"] is True and pv["cancelled"] == [victim]
    assert pv["broken"], "the owning pairing must be broken"
    assert pv["untouched_count"] + len(pv["broken"]) == pv["pairings_total"]
    # the cancelled leg itself is not re-covered
    total_active = sum(
        len(records[int(b["pid"][2:])].get("active_fns")
            or records[int(b["pid"][2:])]["fns"]) for b in pv["broken"])
    assert pv["recover_legs"] == total_active - 1


# --------------------------------------------------------- derived payload

def test_build_repair_payload_cancel_delay_sick():
    payload = {
        "airports": [{"code": "AAA"}],
        "employees": [{"id": "E1", "homeAirport": "AAA", "skills": [],
                       "unavailableDays": ["2000-01-01"]},
                      {"id": "E2", "homeAirport": "AAA", "skills": []}],
        "flights": [
            {"flightNumber": "F1", "departureAirport": "AAA",
             "departureUTCDateTime": "2000-01-02T08:00:00",
             "arrivalAirport": "BBB",
             "arrivalUTCDateTime": "2000-01-02T10:00:00"},
            {"flightNumber": "F2", "departureAirport": "BBB",
             "departureUTCDateTime": "2000-01-02T11:00:00",
             "arrivalAirport": "AAA",
             "arrivalUTCDateTime": "2000-01-02T13:00:00"},
        ],
        "flightAssignments": [
            {"id": "1", "flight": "F1", "indexInFlight": 0,
             "requiredSkill": "Captain"},
            {"id": "2", "flight": "F2", "indexInFlight": 0,
             "requiredSkill": "Captain"},
        ],
    }
    out = dz.build_repair_payload(
        payload, cancelled={"F1"}, delayed={"F2": 90},
        sick={"E2": {"2000-01-05"}})
    assert [f["flightNumber"] for f in out["flights"]] == ["F2"]
    assert out["flights"][0]["departureUTCDateTime"] == "2000-01-02T12:30:00"
    assert out["flights"][0]["arrivalUTCDateTime"] == "2000-01-02T14:30:00"
    assert [a["flight"] for a in out["flightAssignments"]] == ["F2"]
    e2 = next(e for e in out["employees"] if e["id"] == "E2")
    assert e2["unavailableDays"] == ["2000-01-05"]
    assert out["provenance"]["adapter"] == "disruption-repair"
    # the source payload is untouched (deep copy)
    assert len(payload["flights"]) == 2
    with pytest.raises(dz.DisruptionError, match="nothing to repair"):
        dz.build_repair_payload(payload, {"F1", "F2"}, {}, {})


# ------------------------------------------------------------ driver logic

def _meta(tmp_path):
    return {
        "base_id": "gerad-1", "base_label": "inst1",
        "derived_id": "upload-x--r", "derived_label": "x--r",
        "derived_payload": str(tmp_path / "derived.json"),
        "workdir": str(tmp_path / "wd"),
        "cancelled": ["F1"], "delayed": {},
        "recover_fns": ["F2"],
        "untouched": [
            {"fns": ["F5"], "active_fns": ["F5"], "cost": 100,
             "employee": "E1", "pinned": True, "old_pid": "C_000000"},
            {"fns": ["F6"], "active_fns": ["F6"], "cost": 90,
             "employee": None, "pinned": False, "old_pid": "C_000002"},
        ],
        "broken": [{"old_pid": "C_000001", "employee": "E9",
                    "fns": ["F1", "F2"], "active_fns": ["F1", "F2"]}],
        "profile": "gerad", "bases": None,
        "budget_s": 60, "seed": 7,
        "catalog_out": str(tmp_path / "catalog.json"),
        "report_path": str(tmp_path / "report.json"),
    }


def test_merge_catalog_order_and_pins(tmp_path):
    meta = _meta(tmp_path)
    new_records = [{"fns": ["F2"], "active_fns": ["F2"], "cost": 55}]
    merged = drv.merge_catalog(meta, new_records, uncovered=[])
    sel = merged["selected"]
    assert len(sel) == 3
    assert sel[0]["employee"] == "E1" and sel[0]["pinned"] is True
    assert sel[1]["employee"] is None and sel[1]["pinned"] is False
    assert "pinned" not in sel[2] and sel[2]["fns"] == ["F2"]
    assert merged["source"] == "disruption-repair"
    assert merged["label"] == "x--r"


def test_build_neighbourhood_payload_filters():
    derived = {"airports": [{"code": "A"}], "employees": [{"id": "E1"}],
               "flights": [{"flightNumber": "F2"}, {"flightNumber": "F9"}],
               "flightAssignments": [{"id": "1"}]}
    neigh = drv.build_neighbourhood_payload(derived, ["F2"])
    assert [f["flightNumber"] for f in neigh["flights"]] == ["F2"]
    assert neigh["flightAssignments"] == []
    assert neigh["employees"] == [{"id": "E1"}]


def test_build_report_maps_pids_and_crew(tmp_path, monkeypatch):
    meta = _meta(tmp_path)
    new_records = [{"fns": ["F2"], "active_fns": ["F2"], "cost": 55}]
    merged = drv.merge_catalog(meta, new_records, uncovered=["FX"])
    artifact = {
        "pairings": [
            {"pid": "C_000000", "employee": "E1"},   # pinned, kept
            {"pid": "C_000001", "employee": "E7"},   # was unpinned-ill
            {"pid": "C_000002", "employee": "E9"},   # new pairing
        ],
        "stage2_score": "0hard/5soft", "result_status": "OK",
    }
    monkeypatch.setattr(drv, "ROOT", tmp_path)
    # №50 этап 2: артефакты ищутся в каталоге данных — изоляция той же
    # переменной, которой управляется и продукт
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    fr = tmp_path / "final-report"
    fr.mkdir()
    (fr / "gerad_stage2_assignment_x--r.json").write_text(
        json.dumps(artifact), encoding="utf-8")
    rep = drv.build_report(meta, merged, ["FX"], "timefold")
    assert rep["pid_map"] == {"C_000000": "C_000000",
                              "C_000002": "C_000001"}
    assert rep["before"]["pairings_total"] == 3
    assert rep["after"]["pairings_total"] == 3
    assert rep["after"]["new_pairings"][0]["employee"] == "E9"
    assert rep["after"]["uncovered_legs"] == ["FX"]
    # E9 lost the broken pairing and gained the new one
    e9 = next(c for c in rep["crew_changes"] if c["employee"] == "E9")
    assert e9["lost"] == ["C_000001"] and e9["gained"] == ["C_000002"]
    # E7 picked up the unpinned-ill pairing
    e7 = next(c for c in rep["crew_changes"] if c["employee"] == "E7")
    assert e7["gained"] == ["C_000001"]
    assert rep["stage2"]["result_status"] == "OK"


# ------------------------------------------------- stage-2 pin passthrough

def test_synthetic_payload_passthrough():
    from _gerad_stage2_roster import build_synthetic_payload
    payload = {
        "airports": [{"code": "AAA"}],
        "employees": [{"id": "E1"}],
        "flights": [
            {"flightNumber": "F1", "departureAirport": "AAA",
             "departureUTCDateTime": "2000-01-02T08:00:00",
             "arrivalAirport": "AAA",
             "arrivalUTCDateTime": "2000-01-02T10:00:00"},
        ],
    }
    plain = [{"fns": ["F1"], "active_fns": ["F1"], "cost": 1}]
    syn = build_synthetic_payload(payload, plain)
    assert "employee" not in syn["flightAssignments"][0]
    assert "pinned" not in syn["flightAssignments"][0]
    pinned = [{"fns": ["F1"], "active_fns": ["F1"], "cost": 1,
               "employee": "E1", "pinned": True}]
    syn = build_synthetic_payload(payload, pinned)
    a = syn["flightAssignments"][0]
    assert a["employee"] == "E1" and a["pinned"] is True
    # employee without pin (or pin without employee) stays cold-start
    half = [{"fns": ["F1"], "active_fns": ["F1"], "cost": 1,
             "employee": "E1"}]
    assert "employee" not in build_synthetic_payload(
        payload, half)["flightAssignments"][0]


# -------------------------------------------------------------- jobs wiring

def test_jobs_repair_command_validation(tmp_path):
    inst = {"id": "upload-x--r", "label": "x--r", "source": "upload",
            "payload_path": "data/uploads/x/r.json"}
    with pytest.raises(jobs.JobError):
        jobs._build_commands("repair", inst, {})
    with pytest.raises(jobs.JobError):
        jobs._build_commands("repair", inst,
                             {"meta_path": str(tmp_path / "meta.json")})
    real = (__import__("_data_paths").results_dir() / "runs" / "repair"
            / "pytest-tmp" / "meta.json")
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text(json.dumps({"derived_id": "upload-x--r"}),
                    encoding="utf-8")
    try:
        cmds = jobs._build_commands("repair", inst,
                                    {"meta_path": str(real)})
        assert cmds[0][1] == "_disruption_repair.py"
        assert cmds[0][2:] == ["--meta", str(real.resolve())]
    finally:
        real.unlink()
        real.parent.rmdir()


# ---------------------------------------------------------- prepare_rebuild

def test_prepare_rebuild_end_to_end_prep(iso, tmp_path, monkeypatch):
    """Everything up to the job launch: derived instance written, meta
    complete and consistent, repairs registry updated. The job itself is
    faked — the driver's heavy path is covered by its own unit tests."""
    inst = _inst()
    monkeypatch.setattr(registry, "OWNERSHIP_PATH", tmp_path / "own.json")
    monkeypatch.setattr(registry, "LLM_SCOPE_PATH", tmp_path / "scope.json")
    launched = {}

    def fake_start(jtype, dinst, params, owner):
        launched.update(jtype=jtype, inst=dinst, params=params, owner=owner)
        return "repair-fake-0001"

    monkeypatch.setattr(jobs, "start_job", fake_start)
    monkeypatch.setattr(jobs, "get_job", lambda jid: {
        "status": "running"} if launched else None)

    with pytest.raises(dz.DisruptionError, match="no flight events"):
        dz.prepare_rebuild("gerad-1", inst, ADMIN)

    records = dz._catalog_records(inst)
    victim = str((records[0].get("active_fns") or records[0]["fns"])[0])
    dz.add_flights("gerad-1", inst, [victim])

    with pytest.raises(dz.DisruptionError, match="integer"):
        dz.prepare_rebuild("gerad-1", inst, ADMIN, budget_s="abc")

    st = dz.prepare_rebuild("gerad-1", inst, ADMIN, budget_s=60)
    try:
        assert st["status"] == "running"
        assert launched["jtype"] == "repair" and launched["owner"] == ADMIN
        meta = json.loads((dz.REPAIR_RUNS / st["derived_label"]
                           / "meta.json").read_text(encoding="utf-8"))
        assert meta["cancelled"] == [victim]
        assert victim not in meta["recover_fns"]
        # GERAD lane detected from the base catalog document
        assert meta["profile"] == "gerad"
        assert meta["bases"] == "BASE1,BASE2,BASE3"
        # untouched + broken == full catalog; pins only where crew is set
        assert (len(meta["untouched"]) + len(meta["broken"])
                == len(records))
        assert all(not u["pinned"] or u["employee"]
                   for u in meta["untouched"])
        # derived payload has the victim removed
        with open(meta["derived_payload"], encoding="utf-8") as fh:
            dp = json.load(fh)
        assert victim not in {str(f["flightNumber"])
                              for f in dp["flights"]}
        # a second rebuild while the first "runs" must 409 — and must NOT
        # leak a derived instance (audit 2026-07-16: rejection formerly
        # created payload/ownership/scope orphans first)
        user_dir = registry.instance_payload_path(
            registry.get_instance(st["derived_id"])).parent
        uploads_before = sorted(p.name for p in user_dir.glob("*.json"))
        with pytest.raises(dz.DisruptionError, match="already running"):
            dz.prepare_rebuild("gerad-1", inst, ADMIN)
        uploads_after = sorted(p.name for p in user_dir.glob("*.json"))
        assert uploads_before == uploads_after
    finally:
        entry = dz._load_repairs().get("gerad-1") or {}
        dz._remove_derived_repair(str(entry.get("derived_id") or ""),
                                  str(entry.get("derived_label") or ""))


# ------------------------------------------------- Phase C: expansion loop

def _flt(fn, dep, dep_t, arr, arr_t, ac="A320"):
    return {"flightNumber": fn, "departureAirport": dep,
            "departureUTCDateTime": dep_t, "arrivalAirport": arr,
            "arrivalUTCDateTime": arr_t, "aircraftType": ac}


def test_expansion_candidates_stitch_points():
    """Same aircraft type + airport/time adjacency scores; different type
    or far-away pairings are never picked; ranking is deterministic."""
    flights = {f["flightNumber"]: f for f in [
        # uncovered leg: AAA 10:00 -> BBB 12:00
        _flt("U1", "AAA", "2000-01-02T10:00:00", "BBB",
             "2000-01-02T12:00:00"),
        # candidate A: arrives AAA one hour before U1 departs (stitch)
        _flt("A1", "CCC", "2000-01-02T07:00:00", "AAA",
             "2000-01-02T09:00:00"),
        # candidate B: departs BBB after U1 arrives (stitch) TWICE
        _flt("B1", "BBB", "2000-01-02T13:00:00", "CCC",
             "2000-01-02T15:00:00"),
        _flt("B2", "BBB", "2000-01-02T16:00:00", "DDD",
             "2000-01-02T18:00:00"),
        # candidate C: right airports but wrong aircraft type
        _flt("C1", "CCC", "2000-01-02T07:00:00", "AAA",
             "2000-01-02T09:00:00", ac="B737"),
        # candidate D: same type but 5 days away (outside the window)
        _flt("D1", "CCC", "2000-01-07T07:00:00", "AAA",
             "2000-01-07T09:00:00"),
    ]}
    untouched = [
        {"old_pid": "C_000000", "fns": ["A1"], "active_fns": ["A1"]},
        {"old_pid": "C_000001", "fns": ["B1", "B2"],
         "active_fns": ["B1", "B2"]},
        {"old_pid": "C_000002", "fns": ["C1"], "active_fns": ["C1"]},
        {"old_pid": "C_000003", "fns": ["D1"], "active_fns": ["D1"]},
    ]
    got = drv._expansion_candidates(["U1"], untouched, flights, limit=4)
    assert got == [1, 0]          # B (2 active stitches) before A (1); C/D out
    assert drv._expansion_candidates(["U1"], untouched, flights,
                                     limit=1) == [1]
    # active vs deadhead stitch: both count, but an ACTIVE stitch (weight
    # 2) outranks a deadhead-only one (weight 1) — calibrated on GERAD,
    # where the re-stitching pairings are reachable via deadhead proximity
    mixed = [
        {"old_pid": "C_000010", "fns": ["A1"], "active_fns": ["A1"]},
        {"old_pid": "C_000011", "fns": ["A1", "D1"],
         "active_fns": ["D1"]},            # A1 is a deadhead ride here
    ]
    assert drv._expansion_candidates(["U1"], mixed, flights, limit=2) == \
        [0, 1]                             # active (2) before deadhead (1)
    assert drv._expansion_candidates(["U1"], mixed, flights, limit=1) == [0]


def test_expand_neighbourhood_loop(tmp_path, monkeypatch):
    """Round 1 leaves a leg uncovered -> the adjacent untouched pairing is
    unfrozen, its active legs join the pool, round 2 covers everything."""
    meta = _meta(tmp_path)
    meta["recover_fns"] = ["U1"]
    meta["untouched"] = [
        {"old_pid": "C_000000", "fns": ["A1"], "active_fns": ["A1"],
         "cost": 10, "employee": "E1", "pinned": True},
        {"old_pid": "C_000001", "fns": ["Z1"], "active_fns": ["Z1"],
         "cost": 10, "employee": "E2", "pinned": True},   # far away
    ]
    payload = {"airports": [], "employees": [], "flightAssignments": [],
               "flights": [
        _flt("U1", "AAA", "2000-01-02T10:00:00", "BBB",
             "2000-01-02T12:00:00"),
        _flt("A1", "BBB", "2000-01-02T13:00:00", "AAA",
             "2000-01-02T15:00:00"),
        _flt("Z1", "XXX", "2000-01-20T10:00:00", "XXX",
             "2000-01-20T12:00:00"),
    ]}
    Path(meta["derived_payload"]).write_text(json.dumps(payload),
                                             encoding="utf-8")
    calls = []

    def fake_round(m, wd, dp, pool, rnd):
        calls.append(sorted(pool))
        if rnd == 1:
            return [], ["U1"], ["U1"]          # nothing coverable alone
        return ([{"fns": ["U1", "A1"], "active_fns": ["U1", "A1"],
                  "cost": 7}], [], [])
    monkeypatch.setattr(drv, "run_neighbourhood_colgen", fake_round)

    (untouched, unfrozen, records,
     uncovered, proven, rounds) = drv.expand_neighbourhood(
        meta, Path(meta["workdir"]))
    assert rounds == 2 and uncovered == [] and len(records) == 1
    assert [u["old_pid"] for u in unfrozen] == ["C_000000"]
    assert [u["old_pid"] for u in untouched] == ["C_000001"]
    assert calls == [["U1"], ["A1", "U1"]]     # pool grew by A1 only


def test_expand_neighbourhood_no_candidates_stops(tmp_path, monkeypatch):
    meta = _meta(tmp_path)
    meta["recover_fns"] = ["U1"]
    meta["untouched"] = [{"old_pid": "C_000000", "fns": ["Z1"],
                          "active_fns": ["Z1"], "cost": 10,
                          "employee": "E1", "pinned": True}]
    payload = {"airports": [], "employees": [], "flightAssignments": [],
               "flights": [
        _flt("U1", "AAA", "2000-01-02T10:00:00", "BBB",
             "2000-01-02T12:00:00"),
        _flt("Z1", "XXX", "2000-01-20T10:00:00", "XXX",
             "2000-01-20T12:00:00"),
    ]}
    Path(meta["derived_payload"]).write_text(json.dumps(payload),
                                             encoding="utf-8")
    monkeypatch.setattr(drv, "run_neighbourhood_colgen",
                        lambda m, wd, dp, pool, rnd: ([], ["U1"], []))
    (untouched, unfrozen, _records,
     uncovered, _proven, rounds) = drv.expand_neighbourhood(
        meta, Path(meta["workdir"]))
    assert rounds == 1 and uncovered == ["U1"]
    assert unfrozen == [] and len(untouched) == 1    # nothing torn up


def test_expand_neighbourhood_rolls_back_useless_round(tmp_path,
                                                       monkeypatch):
    """Keep-best: when the later round does not beat round 1 and no further
    candidates remain, the best (round 1, zero churn) is returned — the
    unfrozen pairings never make it into the result."""
    meta = _meta(tmp_path)
    meta["recover_fns"] = ["U1"]
    meta["untouched"] = [
        {"old_pid": "C_000000", "fns": ["A1"], "active_fns": ["A1"],
         "cost": 10, "employee": "E1", "pinned": True}]
    payload = {"airports": [], "employees": [], "flightAssignments": [],
               "flights": [
        _flt("U1", "AAA", "2000-01-02T10:00:00", "BBB",
             "2000-01-02T12:00:00"),
        _flt("A1", "BBB", "2000-01-02T13:00:00", "AAA",
             "2000-01-02T15:00:00"),
    ]}
    Path(meta["derived_payload"]).write_text(json.dumps(payload),
                                             encoding="utf-8")
    r1 = ([{"fns": ["R1"], "active_fns": ["R1"], "cost": 1}], ["U1"], [])
    monkeypatch.setattr(
        drv, "run_neighbourhood_colgen",
        lambda m, wd, dp, pool, rnd: r1 if rnd == 1
        else ([], ["U1"], ["U1"]))          # round 2: same leg uncovered
    (untouched, unfrozen, records,
     uncovered, proven, rounds) = drv.expand_neighbourhood(
        meta, Path(meta["workdir"]))
    assert rounds == 1 and unfrozen == []
    assert [u["old_pid"] for u in untouched] == ["C_000000"]
    assert records == r1[0] and uncovered == ["U1"]
    # impossibility certificates are monotone: round 2 PROVED U1 impossible
    # in a superset pool — the kept round 1 result inherits the certificate
    # so the stage-2 gate classifies honestly (keep-best verification)
    assert proven == ["U1"]


def test_expand_neighbourhood_unreadable_payload_exits_2(tmp_path):
    """Contract: hard failure = exit 2 with a [repair] diagnostic, never a
    raw traceback (Phase-C audit)."""
    meta = _meta(tmp_path)          # derived_payload path never written
    meta["recover_fns"] = ["U1"]
    with pytest.raises(SystemExit) as exc:
        drv.expand_neighbourhood(meta, Path(meta["workdir"]))
    assert exc.value.code == 2


def test_report_expansion_section(tmp_path, monkeypatch):
    meta = _meta(tmp_path)
    unfrozen = [{"old_pid": "C_000009", "employee": "E5",
                 "fns": ["F7", "F8"], "active_fns": ["F7", "F8"]}]
    merged = drv.merge_catalog(meta, [], [])
    monkeypatch.setattr(drv, "ROOT", tmp_path)
    # №50 этап 2: артефакты ищутся в каталоге данных — изоляция той же
    # переменной, которой управляется и продукт
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    (tmp_path / "final-report").mkdir()
    rep = drv.build_report(meta, merged, [], "timefold",
                           unfrozen=unfrozen, rounds=2)
    assert rep["expansion"]["rounds"] == 2
    assert rep["expansion"]["unfrozen"] == [
        {"pid": "C_000009", "employee": "E5", "legs": 2}]
    # unfrozen crew appears among the losers; before-total counts it
    e5 = next(c for c in rep["crew_changes"] if c["employee"] == "E5")
    assert e5["lost"] == ["C_000009"]
    assert rep["before"]["pairings_total"] == 2 + 1 + 1   # unt + unfroz + brk


# -------------------------------------------------------- audit-round fixes

def test_catalog_roster_mismatch_is_refused():
    """Positional pid join is refused when the catalog was rebuilt after
    the last stage-2 run (fns no longer match)."""
    records = [{"fns": ["F1", "F2"]}, {"fns": ["F3"]}]
    by_pid = {"C_000000": {"legs": [{"fn": "F1"}, {"fn": "F2"}]},
              "C_000001": {"legs": [{"fn": "F9"}]}}       # shifted!
    with pytest.raises(dz.DisruptionError, match="Re-run stage 2"):
        dz._check_catalog_matches_roster(records, by_pid)
    by_pid["C_000001"] = {"legs": [{"fn": "F3"}]}
    dz._check_catalog_matches_roster(records, by_pid)     # aligned: ok


def test_stage1_lane_from_catalog_doc():
    """The catalog document is the lane's source of truth — scenarios and
    repair-of-repair inherit the base space instead of the label
    heuristic."""
    up = {"id": "upload-x--y", "source": "upload", "label": "x--y"}
    assert dz._stage1_lane(up, {"profile": "gerad",
                                "bases": ["BASE1", "BASE2"]}) == \
        ("gerad", "BASE1,BASE2")
    assert dz._stage1_lane(up, {"profile": "v2", "bases": None}) == \
        ("v2", None)
    # fallback without keys: plain upload -> v2, gerad seed -> gerad lane
    assert dz._stage1_lane(up, {}) == ("v2", None)
    ger = {"id": "gerad-1", "source": "gerad", "label": "inst1"}
    assert dz._stage1_lane(ger, {}) == ("gerad", "BASE1,BASE2,BASE3")


def test_catalog_records_drop_non_dict(tmp_path):
    p = tmp_path / "cat.json"
    p.write_text(json.dumps({"selected": [{"fns": ["F1"]}, None, "junk"]}),
                 encoding="utf-8")
    inst = {"catalog_path": str(p.relative_to(p.anchor))}
    # use an absolute path via monkey-free trick: registry.REPO_ROOT join
    inst = {"catalog_path": str(p)}
    # REPO_ROOT / absolute path resolves to the absolute path on Windows
    recs = dz._catalog_records(inst)
    assert recs == [{"fns": ["F1"]}]


def test_merge_catalog_carries_lane(tmp_path):
    meta = _meta(tmp_path)
    meta["profile"] = "gerad"
    meta["bases"] = "BASE1,BASE2,BASE3"
    merged = drv.merge_catalog(meta, [], [])
    assert merged["profile"] == "gerad"
    assert merged["bases"] == ["BASE1", "BASE2", "BASE3"]


def test_greedy_fallback_passes_frozen_pids(tmp_path, monkeypatch):
    """The catalog's frozen pairings must reach build_repair_plan as
    frozen_pids — otherwise depth-1 swaps may re-crew 'frozen' crew."""
    meta = _meta(tmp_path)
    Path(meta["workdir"]).mkdir(parents=True, exist_ok=True)
    merged = drv.merge_catalog(
        meta, [{"fns": ["F2"], "active_fns": ["F2"], "cost": 5}], [])
    monkeypatch.setattr(drv, "ROOT", tmp_path)
    # №50 этап 2: артефакты ищутся в каталоге данных — изоляция той же
    # переменной, которой управляется и продукт
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    (tmp_path / "final-report").mkdir()

    import webapp.disruption as wdz
    import webapp.registry as wreg
    seen = {}
    monkeypatch.setattr(wreg, "get_instance",
                        lambda i: {"id": i, "label": "x--r"})

    def fake_plan(inst_id, inst, extra_pids=None, frozen_pids=None):
        seen["frozen"] = set(frozen_pids or ())
        return {"available": False, "reason": "test"}

    monkeypatch.setattr(wdz, "build_repair_plan", fake_plan)
    drv.greedy_fallback(meta, merged)
    # exactly the pinned-with-crew records, positional pids
    assert seen["frozen"] == {"C_000000"}


def test_driver_degrades_on_corrupt_nb_catalog(tmp_path, monkeypatch):
    meta = _meta(tmp_path)
    wd = Path(meta["workdir"])
    (wd / "runs" / "stage1" / "x").mkdir(parents=True)
    (wd / "runs" / "stage1" / "x" / "gerad_colgen_selected_nb1.json"
     ).write_text('{"selected": [{"fns"', encoding="utf-8")   # truncated
    payload = {"airports": [], "employees": [],
               "flights": [{"flightNumber": "F2"}], "flightAssignments": []}
    Path(meta["derived_payload"]).write_text(json.dumps(payload),
                                             encoding="utf-8")
    import subprocess as sp
    monkeypatch.setattr(drv.subprocess, "run",
                        lambda *a, **k: sp.CompletedProcess(a, 0))
    records, uncovered, proven = drv.run_neighbourhood_colgen(
        meta, wd, payload, ["F2"], 1)
    assert records == [] and uncovered == ["F2"] and proven == []


def test_jobs_repair_meta_bound_to_instance(tmp_path):
    """Cross-tenant replay: a meta naming a different derived instance is
    refused even when the file itself is valid (audit 2026-07-16)."""
    real_dir = (__import__("_data_paths").results_dir() / "runs" / "repair"
                / "pytest-bind")
    real_dir.mkdir(parents=True, exist_ok=True)
    meta_p = real_dir / "meta.json"
    meta_p.write_text(json.dumps({"derived_id": "upload-b--r",
                                  "derived_label": "b--r"}),
                      encoding="utf-8")
    try:
        mine = {"id": "upload-a--x", "label": "a--x", "source": "upload",
                "payload_path": "data/uploads/a/x.json"}
        with pytest.raises(jobs.JobError, match="does not belong"):
            jobs._build_commands("repair", mine,
                                 {"meta_path": str(meta_p)})
        theirs = {"id": "upload-b--r", "label": "b--r", "source": "upload",
                  "payload_path": "data/uploads/b/r.json"}
        cmds = jobs._build_commands("repair", theirs,
                                    {"meta_path": str(meta_p)})
        assert cmds[0][1] == "_disruption_repair.py"
    finally:
        meta_p.unlink()
        real_dir.rmdir()


def test_jobs_stage2_repair_mutually_exclusive(tmp_path, monkeypatch):
    """stage2 and repair publish the same label-named artifacts — they may
    not run concurrently on one instance."""
    real_dir = (__import__("_data_paths").results_dir() / "runs" / "repair"
                / "pytest-mutex")
    real_dir.mkdir(parents=True, exist_ok=True)
    meta_p = real_dir / "meta.json"
    meta_p.write_text(json.dumps({"derived_id": "upload-m--r"}),
                      encoding="utf-8")
    fake_running = {"id": "stage2-upload-m--r-000000", "type": "stage2",
                    "instance_id": "upload-m--r", "status": "running",
                    "owner": ADMIN}
    jobs._load_persisted()
    jobs._jobs[fake_running["id"]] = fake_running
    try:
        inst = {"id": "upload-m--r", "label": "m--r", "source": "upload",
                "payload_path": "data/uploads/m/r.json"}
        with pytest.raises(jobs.JobError, match="mutually exclusive"):
            jobs.start_job("repair", inst, {"meta_path": str(meta_p)},
                           owner=ADMIN)
    finally:
        jobs._jobs.pop(fake_running["id"], None)
        meta_p.unlink()
        real_dir.rmdir()


# ---------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(dz, "DISRUPTIONS_PATH", tmp_path / "dz.json")
    monkeypatch.setattr(dz, "REPAIRS_PATH", tmp_path / "repairs.json")
    monkeypatch.setattr(dz, "REPAIR_RUNS", tmp_path / "repair-runs")
    monkeypatch.setattr(rb, "EDITS_PATH", tmp_path / "edits.json")
    users.ensure_db()
    users.add(USER, "user", by=ADMIN)
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c


def test_api_event_and_status(client, monkeypatch):
    fns = _some_fns(2)
    r = client.post("/api/disruption/gerad-1/event",
                    json={"kind": "flights", "fns": fns})
    assert r.status_code == 200
    imp = r.json()
    assert imp["surgery"]["active"] is True
    assert imp["surgery"]["cancelled"] == sorted(set(fns))
    assert imp["rebuild"] == {"status": "none"}
    r = client.post("/api/disruption/gerad-1/event",
                    json={"kind": "delay", "fns": [fns[0]],
                          "shift_min": 45})
    assert r.status_code == 200
    assert client.post("/api/disruption/gerad-1/event",
                       json={"kind": "nope"}).status_code == 422
    assert client.post("/api/disruption/gerad-1/event",
                       json={"kind": "flights",
                             "fns": ["NOPE"]}).status_code == 422
    assert client.get(
        "/api/disruption/gerad-1/rebuild").json() == {"status": "none"}
    # tenancy: plain users see nothing of admin instances
    monkeypatch.setenv("CREW_DEV_USER", USER)
    assert client.post("/api/disruption/gerad-1/event",
                       json={"kind": "flights",
                             "fns": fns}).status_code == 404
    assert client.get(
        "/api/disruption/gerad-1/rebuild").status_code == 404


# --- Трек occurrence-идентичности (2026-07-19) -------------------------------


def _dup_fn_payload():
    return {
        "airports": [{"code": "AAA"}, {"code": "BBB"}],
        "employees": [{"id": "E1", "homeAirport": "AAA",
                       "skills": ["Captain"]}],
        "flights": [
            {"flightNumber": "F100", "flightLegId": "F100|d1",
             "departureAirport": "AAA", "arrivalAirport": "BBB",
             "departureUTCDateTime": "2026-03-01T08:00:00",
             "arrivalUTCDateTime": "2026-03-01T10:00:00"},
            {"flightNumber": "F100", "flightLegId": "F100|d2",
             "departureAirport": "BBB", "arrivalAirport": "AAA",
             "departureUTCDateTime": "2026-03-02T08:00:00",
             "arrivalUTCDateTime": "2026-03-02T10:00:00"},
            {"flightNumber": "F200", "flightLegId": "F200|d1",
             "departureAirport": "AAA", "arrivalAirport": "BBB",
             "departureUTCDateTime": "2026-03-01T12:00:00",
             "arrivalUTCDateTime": "2026-03-01T14:00:00"},
        ],
        "flightAssignments": [
            {"id": "s1", "flight": "F100", "flightLegId": "F100|d1",
             "indexInFlight": 0, "requiredSkill": "Captain"},
            {"id": "s2", "flight": "F100", "flightLegId": "F100|d2",
             "indexInFlight": 0, "requiredSkill": "Captain"},
            {"id": "s3", "flight": "F200", "flightLegId": "F200|d1",
             "indexInFlight": 0, "requiredSkill": "Captain"},
        ],
    }


def test_clean_fns_rejects_bare_ambiguous_number():
    # Тихая дыра до трека: «отмена F100» проходила валидацию и гасила ОБА
    # вхождения. Теперь — громкий отказ; flightLegId принимается.
    from webapp import disruption as dz
    p = _dup_fn_payload()
    with pytest.raises(dz.DisruptionError, match="вхождение"):
        dz._clean_fns("F100", p)
    assert dz._clean_fns("F100|d1", p) == ["F100|d1"]
    assert dz._clean_fns("F200", p) == ["F200"]
    assert dz._clean_fns("F100|d1, F200", p) == ["F100|d1", "F200"]
    with pytest.raises(dz.DisruptionError, match="unknown"):
        dz._clean_fns("NOPE", p)


def test_build_repair_payload_cancels_one_occurrence_only():
    # Флагман трека: отмена ОДНОГО вхождения — второй F100 нетронут,
    # его слот жив; слот отменённого удалён.
    from webapp import disruption as dz
    p = _dup_fn_payload()
    out = dz.build_repair_payload(p, cancelled={"F100|d1"}, delayed={},
                                  sick={})
    legs = {f["flightLegId"] for f in out["flights"]}
    assert legs == {"F100|d2", "F200|d1"}
    slot_ids = {a["id"] for a in out["flightAssignments"]}
    assert slot_ids == {"s2", "s3"}
    # второе вхождение байт-идентично исходному
    orig = [f for f in p["flights"] if f["flightLegId"] == "F100|d2"][0]
    kept = [f for f in out["flights"] if f["flightLegId"] == "F100|d2"][0]
    assert kept == orig


def test_build_repair_payload_delays_one_occurrence_only():
    from webapp import disruption as dz
    p = _dup_fn_payload()
    out = dz.build_repair_payload(p, cancelled=set(),
                                  delayed={"F100|d2": 30}, sick={})
    by_leg = {f["flightLegId"]: f for f in out["flights"]}
    assert by_leg["F100|d2"]["departureUTCDateTime"] == "2026-03-02T08:30:00"
    assert by_leg["F100|d1"]["departureUTCDateTime"] == "2026-03-01T08:00:00"


def test_split_catalog_addr_translates_wire_names():
    from webapp import disruption as dz
    records = [{"fns": ["F100@1", "F200"]}, {"fns": ["F100@2"]}]
    wm = {"F100@1": {"flightNumber": "F100", "flightLegId": "F100|d1"},
          "F100@2": {"flightNumber": "F100", "flightLegId": "F100|d2"}}
    untouched, broken = dz.split_catalog_addr(records, {"F100|d1"}, wm)
    assert broken == [0] and untouched == [1]
    # без wire_map — прежнее поведение
    u2, b2 = dz.split_catalog_addr(records, {"F200"}, None)
    assert b2 == [0] and u2 == [1]


def test_merge_catalog_emits_derived_wire_with_stamp(tmp_path):
    # merged-каталог — wire derived payload'а со штампом wire_map:
    # stage-2-ребёнок сверит штамп и работает штатно.
    import json as _json
    import _disruption_repair as dr
    p = _dup_fn_payload()
    dp = tmp_path / "derived.json"
    dp.write_text(_json.dumps(p), encoding="utf-8")
    meta = {"derived_payload": str(dp), "derived_label": "x",
            "base_label": "b", "profile": "v2", "bases": None,
            "cancelled": [], "delayed": {},
            "untouched": [{"fns": ["F100|d1", "F200|d1"],
                           "active_fns": ["F100|d1", "F200|d1"],
                           "cost": 10, "employee": "E1", "pinned": True,
                           "old_pid": "C_000000"}]}
    new_records = [{"fns": ["F100|d2"], "active_fns": ["F100|d2"],
                    "cost": 5}]
    merged = dr.merge_catalog(meta, new_records, uncovered=[],
                              proven_impossible=["F100|d2"])
    from _leg_rekey import rekey_payload
    _, wm = rekey_payload(p)
    assert merged["wire_map"] == wm
    assert merged["selected"][0]["fns"] == ["F100@1", "F200"]
    assert merged["selected"][1]["fns"] == ["F100@2"]
    assert merged["proven_impossible_legs"] == ["F100@2"]


def test_generate_disruptions_addresses_occurrence(monkeypatch, tmp_path):
    # Факт «fn@день» порождает событие с flightLegId-адресом, не с номером.
    from webapp import disruption as dz, ops_log, ops_replay
    monkeypatch.setattr(dz, "DISRUPTIONS_PATH",
                        tmp_path / "disruptions.json")
    p = _dup_fn_payload()
    monkeypatch.setattr(dz, "_payload_for", lambda inst: p)
    keys = ops_log.payload_fact_keys(p)
    assert keys["F100@2026-03-01"]["addr"] == "F100|d1"
    facts = {"F100@2026-03-01": {"cancelled": True}}
    inst = {"id": "i-dup", "label": "i-dup"}
    res = ops_replay.generate_disruptions(
        "i-dup", inst, facts,
        addr_by_key={k: v.get("addr") for k, v in keys.items()})
    assert res["cancelledFlights"] == 1
    evs = dz.events_for("i-dup")
    cancel = [e for e in evs if e.get("kind") == "flights"]
    assert cancel and cancel[0]["fns"] == ["F100|d1"]


def test_neighbourhood_subpayload_renames_to_addr():
    # CRITICAL ревью: оба вхождения повторного номера в пуле раньше упирались
    # в rekey-блокер (colgen exit 3); одно вхождение возвращалось голым
    # номером. Теперь sub-payload переименован в addr — имена уникальны.
    import _disruption_repair as dr
    p = _dup_fn_payload()
    # оба вхождения
    neigh = dr.build_neighbourhood_payload(p, ["F100|d1", "F100|d2"])
    fns = [f["flightNumber"] for f in neigh["flights"]]
    assert sorted(fns) == ["F100|d1", "F100|d2"]
    from _leg_rekey import needs_rekey
    assert needs_rekey(neigh) is False          # блокер недостижим
    # одно вхождение — имя сразу addr, не голый номер
    neigh1 = dr.build_neighbourhood_payload(p, ["F100|d2"])
    assert [f["flightNumber"] for f in neigh1["flights"]] == ["F100|d2"]
    # уникальный номер — без переименования
    neigh2 = dr.build_neighbourhood_payload(p, ["F200"])
    assert [f["flightNumber"] for f in neigh2["flights"]] == ["F200"]


def test_stale_event_addr_after_delta_is_loud(monkeypatch, tmp_path):
    # MEDIUM ревью: событие с голым номером, ставшим повторным после дельты,
    # раньше молча превращалось в no-op — теперь громкий отказ.
    from webapp import disruption as dz
    monkeypatch.setattr(dz, "DISRUPTIONS_PATH",
                        tmp_path / "disruptions.json")
    p = _dup_fn_payload()
    with pytest.raises(dz.DisruptionError, match="пересоздайте"):
        dz._validate_event_addrs(p, {"F100"}, {})
    # валидные адреса проходят
    dz._validate_event_addrs(p, {"F100|d1"}, {"F200": 30})


def test_fact_key_intraminute_collision_unaddressable():
    # LOW ревью: два рейса одного номера в одну минуту — addr None
    # (пропуск со счётчиком), а не произвольное вхождение.
    from webapp import ops_log
    p = _dup_fn_payload()
    p["flights"][1]["departureUTCDateTime"] = "2026-03-01T08:00:00"
    keys = ops_log.payload_fact_keys(p)
    collided = [k for k, v in keys.items() if v["fn"] == "F100"]
    assert len(collided) == 1
    assert keys[collided[0]]["addr"] is None


# --- P0-1 (ревью 2026-07-20): проводка гейта в драйвере ремонта -------------


def test_greedy_fallback_writes_diagnostics_not_canonical(tmp_path,
                                                          monkeypatch):
    import _disruption_repair as dr
    import _roster_manifest as rm
    monkeypatch.setattr(dr, "ROOT", tmp_path)
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    canonical = (tmp_path / "final-report"
                 / "gerad_stage2_assignment_dz1.json")
    canonical.parent.mkdir(parents=True)
    canonical.write_text('{"old": true}', encoding="utf-8")
    payload = tmp_path / "payload.json"
    payload.write_text("{}", encoding="utf-8")
    calls = {}

    def _spy_publish(manifest, *, auto_promote, **kw):
        calls["auto_promote"] = auto_promote
        calls["status"] = manifest["result_status"]
        calls["ap"] = manifest["artifacts"]["assignment_path"]
        return {"promoted": auto_promote}

    monkeypatch.setattr(rm, "publish", _spy_publish)
    from webapp import registry as _reg
    monkeypatch.setattr(_reg, "get_instance", lambda i: None)
    cat = tmp_path / "merged_catalog_dz1.json"
    cat.write_text("{}", encoding="utf-8")
    meta = {"derived_label": "dz1", "derived_id": "dz1-id",
            "derived_payload": str(payload), "catalog_out": str(cat)}
    merged = {"selected": [{"employee": "E1", "fns": ["F1"],
                            "active_fns": ["F1"], "pinned": True}]}
    doc = dr.greedy_fallback(meta, merged)
    assert doc["result_status"] == "REPAIR_GREEDY"
    # канонический файл НЕ тронут; stub — в диагностике
    assert json.loads(canonical.read_text(encoding="utf-8")) == {"old": True}
    diags = list((tmp_path / "final-report" / "runs"
                  / "stage2-diagnostics").glob("dz1-*-assignment.json"))
    assert len(diags) == 1
    assert calls["auto_promote"] is False
    assert calls["status"] == "REPAIR_GREEDY"
    assert "stage2-diagnostics" in calls["ap"].replace(chr(92), "/")


def test_run_stage2_nonok_manifest_is_verdict_not_crash(tmp_path,
                                                        monkeypatch):
    import _disruption_repair as dr
    monkeypatch.setattr(dr, "ROOT", tmp_path)
    monkeypatch.setenv("CREW_RUN_ID", "runzz")
    import _roster_manifest as rm
    mdir = tmp_path / "final-report" / "runs" / "roster-manifests" / "dz2"
    monkeypatch.setattr(rm, "FINAL_REPORT", tmp_path / "final-report")
    mdir.mkdir(parents=True)
    diag_ap = (tmp_path / "final-report" / "runs" / "stage2-diagnostics"
               / "dz2-x-assignment.json")
    diag_ap.parent.mkdir(parents=True)
    diag_ap.write_text('{"result_status": "INFEASIBLE_INCUMBENT", '
                       '"pairings": []}', encoding="utf-8")
    (mdir / "runzz.json").write_text(json.dumps({
        "result_status": "INFEASIBLE_INCUMBENT",
        "artifacts": {"assignment_path":
                      "final-report/runs/stage2-diagnostics/"
                      "dz2-x-assignment.json"}}), encoding="utf-8")
    monkeypatch.setattr(dr.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 6})())
    meta = {"derived_label": "dz2", "derived_payload": "x.json",
            "budget_s": 5, "seed": 7}
    m = dr.run_stage2(meta, tmp_path / "cat.json")
    assert m is not None
    assert m["result_status"] == "INFEASIBLE_INCUMBENT"


def test_run_stage2_missing_manifest_falls_back_to_stamped_canonical(
        tmp_path, monkeypatch):
    # Ревью: отказ записи манифеста ребёнком не крах — фолбэк по штампу
    # run_id в каноническом артефакте
    import _disruption_repair as dr
    import _roster_manifest as rm
    monkeypatch.setattr(dr, "ROOT", tmp_path)
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(rm, "FINAL_REPORT", tmp_path / "final-report")
    monkeypatch.setenv("CREW_RUN_ID", "runqq")
    art = tmp_path / "final-report" / "gerad_stage2_assignment_dz3.json"
    art.parent.mkdir(parents=True)
    art.write_text(json.dumps({"result_status": "OK", "run_id": "runqq",
                               "pairings": []}), encoding="utf-8")
    monkeypatch.setattr(dr.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0})())
    meta = {"derived_label": "dz3", "derived_payload": "x.json",
            "budget_s": 5, "seed": 7}
    m = dr.run_stage2(meta, tmp_path / "cat.json")
    assert m is not None and m.get("manifest_missing") is True
    assert m["result_status"] == "OK"
