"""Offline validator tests, NOT Kotlin execution/support evidence.

The component reader is an explicit test seam with already-decoded native
data. Positive examples below are synthetic protocol fixtures, never support
receipts. A separate negative restores the real reader to prove it is called.
"""
from __future__ import annotations

import copy
import hashlib
import json
import socket
import subprocess
from pathlib import Path
from uuid import UUID

import pytest

from cdpm import component_input as ci
from vmp import qualification_run as qr, stand_adapters as sa, stand_tasks as st

RECORD = "families/calendar_windows"
POSITIVE, HEALTHY = qr.PAIRS[RECORD]
CONSTRAINT = "Calendar window layover penalty"
COMMIT = "a" * 40
CHALLENGE = "abcdef01-1234-4234-8234-123456789abc"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class FakeCatalog:
    """Declared test catalogue; not a release/runtime catalogue."""
    def __init__(self):
        self.version_value = "mock-qualification-only"
        self.fingerprint_value = "b" * 64
        base = {"form": "schedule", "params": {}, "rule_form": {"days": ["thursday"]},
            "crews": [{"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T1"]}],
            "flights": [{"id": "F1", "from": "ST1", "to": "ST2", "fleet_type": "T1",
                         "dep": "2026-10-01T06:00:00", "arr": "2026-10-01T07:00:00"}],
            "assignments": [{"flight": "F1", "crew": "P1", "seat": 0}]}
        healthy = copy.deepcopy(base)
        healthy["flights"][0].update(dep="2026-10-02T06:00:00", arr="2026-10-02T07:00:00")
        self.rows = [{"id": "calendar_windows", "kind": "SOFT", "zone": "BOTH", "params": [],
            "title": "Mock protocol fixture", "violation_size": "count", "stand_tasks": [
                {"id": POSITIVE, "world": base, "expected": {"kotlin_constraint": {
                    "constraint": CONSTRAINT, "matches": 1, "amount": 1}}},
                {"id": HEALTHY, "world": healthy, "expected": {"kotlin_constraint": {
                    "constraint": CONSTRAINT, "matches": 0, "amount": 0}}}]}]

    def version(self):
        return self.version_value

    def fingerprint(self):
        return self.fingerprint_value

    def families_full(self):
        return copy.deepcopy(self.rows)

    def criteria(self):
        return {}


class ProtocolFixture:
    def __init__(self, root, monkeypatch, *, catalog=None, record=RECORD):
        self.root = root
        self.repo, self.jdk = root / "repo", root / "jdk"
        self.inputs, self.output = root / "inputs", root / "output"
        self.output.mkdir()
        for name in {*qr.PYTHON_SOURCES, *st.CODE_FILES, qr.PROBE_SOURCE,
                     "kotlin-solver/pom.xml", "kotlin-solver/src/main/kotlin/Mock.kt",
                     "kotlin-solver/src/main/resources/solverConfig.xml",
                     "kotlin-solver/target/classes/Mock.class",
                     "kotlin-solver/target/test-classes/org/acme/flighcrewscheduling/solver/A06QualificationRunProbe.class"}:
            path = self.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("MOCK-ONLY " + name).encode())
        (self.repo / ".git").mkdir()
        (self.repo / ".git/HEAD").write_bytes((COMMIT + "\n").encode("ascii"))
        for name, body in {"release": b'JAVA_VERSION="21.0-test"\r\n',
                           "bin/java.exe": b"NOT EXECUTABLE", "lib/modules": b"MOCK MODULES"}.items():
            path = self.jdk / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        self.catalog = catalog or FakeCatalog()
        positive, healthy = qr.PAIRS[record]
        self.request_path = root / "qualification-request.json"
        self.path = self.output / "qualification-run.json"
        self.native, self.reader_calls, self.contents = {}, [], {}
        self.real_reader = ci.read_bytes
        self.request = {"schema": qr.REQUEST_SCHEMA, "scope": qr.SCOPE,
            "catalog": qr._stamp(self.catalog), "challenge": CHALLENGE,
            "limits": {**qr.LIMITS, "max_tasks": 2}, "cases": []}
        observations = []
        section, rid = record.split("/", 1)
        records = self.catalog.criteria().values() if section == "criteria" else self.catalog.families_full()
        selected_record = next(row for row in records if row["id"] == rid)
        for index, task in enumerate(sorted(selected_record["stand_tasks"], key=lambda row: row["id"])):
            task_id = task["id"]
            is_healthy = task_id == healthy
            raw = ("NOT-A-ZIP; EXPLICIT MOCK-READER INPUT: " + task_id).encode()
            folder = self.inputs / task_id
            folder.mkdir(parents=True)
            (folder / (task_id + ".zip")).write_bytes(raw)
            self.request["cases"].append({"record": record, "task": task_id,
                "role": "healthy" if is_healthy else "violation", "control_for": positive if is_healthy else None,
                "component_zip": task_id + ".zip", "sha256": digest(raw),
                "executor_kind": "kotlin_constraint", "mode": "feasibility-soft", "objective_id": None})
            manifest = {"task": {"record": record, "id": task_id}}
            problem = sa.world_to_problem(task["world"])
            native = {"manifest": manifest, "files": {ci.MANIFEST: encoded(manifest),
                "model.json": encoded({"rules": [{"id": "R-STAND-calendar_windows", "template_fit": {"params": {}}}]}),
                "problem.json": encoded(problem), "objective.json": encoded({"pairing": {"components": [
                    {"family": "calendar_windows", "role": "soft_penalty", "active": True, "weight": 1}]}}),
                "threshold-forms.json": encoded({"calendar_window_penalties": [
                    {"rule": "R-STAND-calendar_windows", "action": "penalize", "days": ["thursday"]}]})}}
            self.native[raw] = native
            fingerprint = digest(native["files"][ci.MANIFEST])
            context = {"input_fingerprint": fingerprint, "profile": "DEFAULT",
                "applied": {"applied": [], "ignored": [], "paramToThreshold": {}, "qualifiedParamToThreshold": {},
                            "offLiterals": {}, "literalsInForce": {}, "hardWeights": {}},
                "coverage": {"qualifications": {"employeesTotal": 1}},
                "fit": {"blocksSeen": 0, "plans": [], "generated": [], "executedElsewhere": [], "ignored": [],
                        "refused": [], "notHard": [], "registered": [CONSTRAINT], "fitOn": "qualification:" + task_id, "states": {}},
                "provider_readings": {"baseline": 1}, "provider_code_readings": {}, "provider_policy_readings": {},
                "applied_weights": {CONSTRAINT: 1},
                "effective_weights": {**dict.fromkeys(qr._default_soft_literals(), 0), CONSTRAINT: 1},
                "literals_disabled": sorted(qr._default_soft_literals() - {CONSTRAINT}),
                "calendar_rules": [{"ruleId": "R-STAND-calendar_windows", "action": "penalize", "days": ["THURSDAY"]}],
                "history": None, "mode": "feasibility-soft", "objective": None}
            solution = {**copy.deepcopy(problem), "score": "0hard/0soft"}
            self.contents[task_id] = {"execution_context": context, "solution": solution}
            context_ref = self.write_ref(task_id, "execution_context")
            solution_ref = self.write_ref(task_id, "solution")
            for name, subject, value in (("declared_world_breakdown", digest(native["files"]["problem.json"]), 0 if is_healthy else 1),
                                        ("solved_solution_breakdown", solution_ref["sha256"], 0)):
                self.contents[task_id][name] = {"execution_context_sha256": context_ref["sha256"],
                    "score_subject_sha256": subject, "breakdown": {"score": f"0hard/{-value}soft", "hard": 0, "soft": -value,
                    "packageFingerprint": fingerprint, "generated": [], "constraints": {
                        CONSTRAINT: {"score": f"0hard/{-value}soft", "hard": 0, "soft": -value, "matches": value, "weight": "0hard/-1soft"}}}}
            observations.append({"record": record, "task": task_id, "run_id": str(UUID(int=index + 1)),
                "started_at": "2026-09-20T10:00:00Z", "completed_at": "2026-09-20T10:00:00.200Z",
                "input_fingerprint": fingerprint, "solve": {"called": True, "completed": True, "exception": None},
                "termination": {"version": 1, "kind": "step_count", "mode": "deterministic", "reproducible": True,
                    "search_steps": 1, "unimproved_steps": 0, "search_started": True, "construction_steps": 0,
                    "elapsed_ms": 100, "step_limit": 1, "unimproved_step_limit": None, "wall_clock_limit_ms": 1000,
                    "unimproved_wall_clock_limit_ms": None, "words": "MOCK ONLY actual-counter protocol"},
                **{name: self.write_ref(task_id, name) for name in qr._SIDECARS}})
        identity = qr.identities(self.repo, self.jdk)
        input_hashes = {row["task"]: row["sha256"] for row in self.request["cases"]}
        self.doc = {"schema": qr.SCHEMA, "scope": qr.SCOPE, "proof_source": qr.PROOF_SOURCE,
            "catalog": qr._stamp(self.catalog), "challenge": CHALLENGE, "request_sha256": "",
            "producer": {"class": qr.PROBE, "commit": COMMIT, "java_version": "21.0-test"},
            "identities": {"before": identity, "after": copy.deepcopy(identity)},
            "inputs": {"before": input_hashes, "after": copy.deepcopy(input_hashes)}, "observations": observations, "files": []}
        self.save_request()
        self.save()

        def mock_verified_native(raw, *, catalog):
            assert catalog is self.catalog
            self.reader_calls.append(raw)
            return copy.deepcopy(self.native[raw])

        monkeypatch.setattr(ci, "read_bytes", mock_verified_native)

    def write_ref(self, task, name):
        path = task + qr._SIDECARS[name]
        raw = encoded(self.contents[task][name])
        (self.output / path).write_bytes(raw)
        return {"path": path, "sha256": digest(raw), "bytes": len(raw)}

    def row(self, task=POSITIVE):
        return next(row for row in self.doc["observations"] if row["task"] == task)

    def save_request(self):
        raw = encoded(self.request)
        self.request_path.write_bytes(raw)
        self.request_sha = digest(raw)
        self.doc["request_sha256"] = self.request_sha

    def save(self):
        self.doc["files"] = sorted([row[name] for row in self.doc["observations"] for name in qr._SIDECARS], key=lambda ref: ref["path"])
        self.path.write_bytes(encoded(self.doc))

    def reseal(self, name, *, task=POSITIVE, follow_context=True, follow_solution=True):
        row = self.row(task)
        row[name] = self.write_ref(task, name)
        if name == "execution_context" and follow_context:
            for item in ("declared_world_breakdown", "solved_solution_breakdown"):
                self.contents[task][item]["execution_context_sha256"] = row[name]["sha256"]
                row[item] = self.write_ref(task, item)
        if name == "solution" and follow_solution:
            self.contents[task]["solved_solution_breakdown"]["score_subject_sha256"] = row[name]["sha256"]
            row["solved_solution_breakdown"] = self.write_ref(task, "solved_solution_breakdown")
        self.save()

    def load(self, **changes):
        args = dict(request_path=self.request_path, request_sha256=self.request_sha, challenge=CHALLENGE, expected_commit=COMMIT,
                    catalog=self.catalog, input_root=self.inputs, repo=self.repo, java_home=self.jdk)
        args.update(changes)
        return qr.load_evidence(self.path, **args)


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Offline validator test attempted network/process execution")
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket, "getaddrinfo", deny)
    monkeypatch.setattr(subprocess, "Popen", deny)


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    return ProtocolFixture(tmp_path, monkeypatch)


def test_mock_protocol_is_imported_and_original_score_not_solution_score(bundle):
    evidence = bundle.load()
    assert len(bundle.reader_calls) == 2
    task = bundle.catalog.rows[0]["stand_tasks"][0]
    got = evidence.judge("kotlin_constraint", task["expected"]["kotlin_constraint"], RECORD, POSITIVE, task["world"])
    assert got["got"]["amount"] == 1
    assert bundle.contents[POSITIVE]["solved_solution_breakdown"]["breakdown"]["soft"] == 0
    assert qr.load_reference(evidence.reference, expected_commit=COMMIT, catalog=bundle.catalog, repo=bundle.repo).contains(RECORD, HEALTHY)


def test_real_native_reader_is_mandatory_not_a_marker_only_check(bundle, monkeypatch):
    monkeypatch.setattr(ci, "read_bytes", bundle.real_reader)
    with pytest.raises((ValueError, OSError)):
        bundle.load()


@pytest.mark.parametrize("field,value", [
    ("schema", "a06-kotlin-components/1"), ("scope", "live"), ("proof_source", "live-http"),
    ("challenge", "aaaaaaaa-1234-4234-8234-123456789abc"), ("request_sha256", "c" * 64),
    ("catalog", {"version": "released", "fingerprint": "b" * 64}),
    ("got", {"amount": 1}), ("expected", {"amount": 1})])
def test_envelope_relabels_or_oracle_fields_fail(bundle, field, value):
    bundle.doc[field] = value
    bundle.save()
    with pytest.raises(qr.QualificationError):
        bundle.load()


@pytest.mark.parametrize("change", ["schema", "scope", "challenge", "limits", "limits_bool", "case_task", "case_role",
    "case_control", "case_basename", "case_duplicate", "case_order", "case_missing", "catalog", "case_oracle"])
def test_request_closed_allowlist_and_control_contract(bundle, change):
    request = bundle.request
    if change in {"schema", "scope", "challenge"}:
        request[change] = "wrong"
    elif change == "limits":
        request["limits"]["spent_limit_ms"] = 10000
    elif change == "limits_bool":
        request["limits"]["step_limit"] = True
    elif change == "catalog":
        request["catalog"]["fingerprint"] = "c" * 64
    elif change == "case_task":
        request["cases"][0]["task"] = "undeclared"
    elif change == "case_role":
        request["cases"][0]["role"] = "healthy"
    elif change == "case_control":
        request["cases"][1]["control_for"] = None
    elif change == "case_basename":
        request["cases"][0]["component_zip"] = "../other.zip"
    elif change == "case_duplicate":
        request["cases"][1] = copy.deepcopy(request["cases"][0])
    elif change == "case_order":
        request["cases"].reverse()
    elif change == "case_missing":
        request["cases"].pop()
        request["limits"]["max_tasks"] = 1
    elif change == "case_oracle":
        request["cases"][0]["expected"] = {"amount": 1}
    bundle.save_request()
    bundle.save()
    with pytest.raises(qr.QualificationError):
        bundle.load()


@pytest.mark.parametrize("change", ["expected_zero", "healthy_nonzero", "same_world", "different_constraint", "not_schedule"])
def test_catalogue_pair_is_not_invented_or_inverted(bundle, change):
    violation, healthy = bundle.catalog.rows[0]["stand_tasks"]
    if change == "expected_zero":
        violation["expected"]["kotlin_constraint"].update(matches=0, amount=0)
    elif change == "healthy_nonzero":
        healthy["expected"]["kotlin_constraint"].update(matches=1, amount=1)
    elif change == "same_world":
        healthy["world"] = copy.deepcopy(violation["world"])
    elif change == "different_constraint":
        healthy["expected"]["kotlin_constraint"]["constraint"] = "different"
    elif change == "not_schedule":
        healthy["world"]["form"] = "arbitrary"
    with pytest.raises(qr.QualificationError):
        bundle.load()


@pytest.mark.parametrize("change", ["request_hash", "request_challenge", "request_bytes", "catalog_version", "catalog_fingerprint"])
def test_independent_external_binding_rejects_replay(bundle, change):
    kwargs = {}
    if change == "request_hash":
        kwargs["request_sha256"] = "c" * 64
    elif change == "request_challenge":
        kwargs["challenge"] = "aaaaaaaa-1234-4234-8234-123456789abc"
    elif change == "request_bytes":
        bundle.request_path.write_bytes(bundle.request_path.read_bytes() + b" ")
    elif change == "catalog_version":
        bundle.catalog.version_value = "relabeled"
    else:
        bundle.catalog.fingerprint_value = "c" * 64
    with pytest.raises(qr.QualificationError):
        bundle.load(**kwargs)


@pytest.mark.parametrize("kind", ["sources", "artifacts", "probe", "config", "jdk"])
@pytest.mark.parametrize("when", ["before", "after", "disk"])
def test_every_identity_census_is_current_and_complete(bundle, kind, when):
    if when == "disk":
        rel = next(iter(bundle.doc["identities"]["before"][kind]))
        root = bundle.jdk if kind == "jdk" else bundle.repo
        (root / rel).write_bytes(b"MUTATED")
    else:
        values = bundle.doc["identities"][when][kind]
        values.pop(next(iter(values)))
        bundle.save()
    with pytest.raises(qr.QualificationError):
        bundle.load()


@pytest.mark.parametrize("change", ["zip_bytes", "input_before", "input_after", "fingerprint", "native_task", "duplicate_run", "foreign_task"])
def test_exact_input_and_run_identity(bundle, change):
    row = bundle.row()
    if change == "zip_bytes":
        (bundle.inputs / POSITIVE / (POSITIVE + ".zip")).write_bytes(b"changed")
    elif change.startswith("input_"):
        bundle.doc["inputs"][change.removeprefix("input_")][POSITIVE] = "c" * 64
    elif change == "fingerprint":
        row["input_fingerprint"] = "c" * 64
    elif change == "native_task":
        next(value for value in bundle.native.values() if value["manifest"]["task"]["id"] == POSITIVE)["manifest"]["task"]["id"] = HEALTHY
    elif change == "duplicate_run":
        row["run_id"] = bundle.row(HEALTHY)["run_id"]
    elif change == "foreign_task":
        row["task"] = "foreign"
    bundle.save()
    with pytest.raises(qr.QualificationError):
        bundle.load()


@pytest.mark.parametrize("change", ["called", "completed", "exception", "termination_missing", "search_started", "reproducible",
    "mode", "kind", "step_limit", "wall_limit", "elapsed", "counter_bool", "wall_after_steps", "timestamp", "duration"])
def test_actual_solve_and_termination_cannot_be_replaced_by_score_only(bundle, change):
    row = bundle.row()
    stop = row["termination"]
    if change in {"called", "completed"}:
        row["solve"][change] = False
    elif change == "exception":
        row["solve"]["exception"] = "cancelled"
    elif change == "termination_missing":
        row.pop("termination")
    elif change in {"search_started", "reproducible"}:
        stop[change] = False
    elif change == "mode":
        stop["mode"] = "wall-clock"
    elif change == "kind":
        stop["kind"] = "cancelled"
    elif change == "step_limit":
        stop["step_limit"] = 99
    elif change == "wall_limit":
        stop["wall_clock_limit_ms"] = None
    elif change == "elapsed":
        stop["elapsed_ms"] = 9999
    elif change == "counter_bool":
        stop["search_steps"] = True
    elif change == "wall_after_steps":
        stop.update(elapsed_ms=1000, kind="wall_clock_safety")
        row["completed_at"] = "2026-09-20T10:00:01Z"
    elif change == "timestamp":
        row["started_at"] = "not a timestamp"
    elif change == "duration":
        row["completed_at"] = "2026-09-20T10:01:00Z"
    bundle.save()
    with pytest.raises(qr.QualificationError):
        bundle.load()


@pytest.mark.parametrize("change", ["fit_missing", "fit_refused", "fit_other_task", "coverage_missing", "coverage_wrong",
    "provider_missing", "calendar", "weights", "effective", "disabled", "history", "ignored", "profile", "registered", "fit_census"])
def test_resealed_context_still_requires_native_source_fit_and_coverage(bundle, change):
    ctx = bundle.contents[POSITIVE]["execution_context"]
    if change == "fit_missing":
        ctx.pop("fit")
    elif change == "fit_refused":
        ctx["fit"]["refused"] = ["refused"]
    elif change == "fit_other_task":
        ctx["fit"]["fitOn"] = "qualification:" + HEALTHY
    elif change == "coverage_missing":
        ctx["coverage"] = {}
    elif change == "coverage_wrong":
        ctx["coverage"]["qualifications"]["employeesTotal"] = 2
    elif change == "provider_missing":
        ctx["provider_readings"] = {}
    elif change == "calendar":
        ctx["calendar_rules"][0]["days"] = ["FRIDAY"]
    elif change == "weights":
        ctx["applied_weights"][CONSTRAINT] = 2
        ctx["effective_weights"][CONSTRAINT] = 2
    elif change == "effective":
        ctx["effective_weights"][CONSTRAINT] = 0
    elif change == "disabled":
        ctx["literals_disabled"] = [CONSTRAINT]
    elif change == "history":
        ctx["history"] = {"assumedZero": 1}
    elif change == "ignored":
        ctx["applied"]["ignored"] = ["parameter"]
    elif change == "profile":
        ctx["profile"] = "GERAD"
    elif change == "registered":
        ctx["fit"]["registered"] = []
    elif change == "fit_census":
        ctx["fit"]["blocksSeen"] = 1
    bundle.reseal("execution_context")
    with pytest.raises(qr.QualificationError):
        bundle.load()


@pytest.mark.parametrize("change", ["declared_subject", "solved_subject", "context", "package", "total", "row_score", "zero_weight", "positive_weight", "missing_target", "extra_got"])
def test_resealed_breakdowns_cannot_swap_subject_or_invent_zero(bundle, change):
    value = bundle.contents[POSITIVE]["declared_world_breakdown"]
    body = value["breakdown"]
    if change == "declared_subject":
        value["score_subject_sha256"] = bundle.row()["solution"]["sha256"]
    elif change == "solved_subject":
        bundle.contents[POSITIVE]["solved_solution_breakdown"]["score_subject_sha256"] = value["score_subject_sha256"]
        bundle.reseal("solved_solution_breakdown")
    elif change == "context":
        value["execution_context_sha256"] = bundle.row(HEALTHY)["execution_context"]["sha256"]
    elif change == "package":
        body["packageFingerprint"] = bundle.row(HEALTHY)["input_fingerprint"]
    elif change == "total":
        body.update(score="0hard/0soft", soft=0)
    elif change == "row_score":
        body["constraints"][CONSTRAINT]["score"] = "0hard/0soft"
    elif change == "zero_weight":
        body["constraints"][CONSTRAINT]["weight"] = "0hard/0soft"
    elif change == "positive_weight":
        body["constraints"][CONSTRAINT]["weight"] = "0hard/1soft"
    elif change == "missing_target":
        body["constraints"] = {"other": body["constraints"][CONSTRAINT]}
    elif change == "extra_got":
        value["got"] = {"matches": 1, "amount": 1}
    bundle.reseal("declared_world_breakdown")
    with pytest.raises((ValueError, RuntimeError)):
        bundle.load()


@pytest.mark.parametrize("change", ["crew_id", "crew_rating", "crew_history", "flight_time", "flight_type", "airport", "seat", "pin", "employee", "score"])
def test_resealed_solution_cannot_change_facts_or_hide_different_score(bundle, change):
    solution = bundle.contents[POSITIVE]["solution"]
    if change == "crew_id":
        solution["employees"][0]["id"] = "P2"
    elif change == "crew_rating":
        solution["employees"][0]["typeRatings"] = ["T2"]
    elif change == "crew_history":
        solution["employees"][0]["history"] = {"historyMonthBlockMin": 0}
    elif change == "flight_time":
        solution["flights"][0]["arrivalUTCDateTime"] = "2026-10-01T08:00:00"
    elif change == "flight_type":
        solution["flights"][0]["aircraftType"] = "T2"
    elif change == "airport":
        solution["airports"][0]["code"] = "ST3"
    elif change == "seat":
        solution["flightAssignments"][0]["requiredSkill"] = "FO"
    elif change == "pin":
        solution["flightAssignments"][0]["pinned"] = True
    elif change == "employee":
        solution["flightAssignments"][0]["employee"] = "P2"
    elif change == "score":
        solution["score"] = "0hard/-100soft"
    bundle.reseal("solution")
    with pytest.raises(qr.QualificationError):
        bundle.load()


def test_planning_variable_and_jackson_defaults_are_not_false_mutations(bundle):
    solution = bundle.contents[POSITIVE]["solution"]
    solution["flightAssignments"][0].update(employee=None, crewSlotId="F1#0", pinned=False, requiredCodes=[], chainId=None)
    solution["flights"][0].update(flightLegId="F1", transportOnly=False, arrivalUTCDateTime="2026-10-01T07:00")
    solution["employees"][0].update(history=None, historyAssumedZero=False, unavailableDays=[])
    solution["airports"][0].update(name="", latitude=0.0, longitude=0.0, taxiTimeInMinutes=None)
    solution.update(history=None, window_horizon=None, solverStatus=None)
    bundle.reseal("solution")
    assert bundle.load().contains(RECORD, POSITIVE)


def test_mutated_measurement_is_not_compared_to_a_copied_expected_input(bundle):
    body = bundle.contents[POSITIVE]["declared_world_breakdown"]["breakdown"]
    body.update(score="0hard/-2soft", soft=-2)
    body["constraints"][CONSTRAINT].update(score="0hard/-2soft", soft=-2, matches=2)
    bundle.reseal("declared_world_breakdown")
    evidence = bundle.load()  # diagnostics preserve a real mismatch
    task = bundle.catalog.rows[0]["stand_tasks"][0]
    with pytest.raises(qr.QualificationError, match="differs from oracle"):
        evidence.judge("kotlin_constraint", task["expected"]["kotlin_constraint"], RECORD, POSITIVE, task["world"])


@pytest.mark.parametrize("change", ["extra_file", "missing_file", "hash", "bytes", "size_bool", "path", "duplicate_ref", "duplicate_json", "extra_observation"])
def test_strict_output_census_and_json(bundle, change):
    if change == "extra_file":
        (bundle.output / "unknown.txt").write_bytes(b"not listed")
    elif change == "missing_file":
        (bundle.output / bundle.row()["solution"]["path"]).unlink()
    elif change in {"hash", "bytes", "size_bool", "path"}:
        key, value = {"hash": ("sha256", "c" * 64), "bytes": ("bytes", 1),
                      "size_bool": ("bytes", True), "path": ("path", "../solution.json")}[change]
        bundle.row()["solution"][key] = value
        bundle.save()
    elif change == "duplicate_ref":
        bundle.doc["files"].append(copy.deepcopy(bundle.doc["files"][0]))
        bundle.path.write_bytes(encoded(bundle.doc))
    elif change == "duplicate_json":
        raw = bundle.path.read_bytes()
        bundle.path.write_bytes(b'{"schema":"fake",' + raw[1:])
    elif change == "extra_observation":
        bundle.doc["observations"].append(copy.deepcopy(bundle.row()))
        bundle.save()
    with pytest.raises(qr.QualificationError):
        bundle.load()


def stand_document(bundle):
    evidence = bundle.load()
    rows = st.run_tasks(catalog=bundle.catalog, kinds=["kotlin_constraint"], tmp_dir=bundle.root / "stand",
                        qualification_evidence=evidence)
    document = st.results_document(rows, bundle.catalog, commit=COMMIT, repo=bundle.repo, qualification_reference=evidence.reference)
    return evidence, rows, document


def test_stand_integration_is_distinct_read_only_source(bundle):
    evidence, rows, document = stand_document(bundle)
    assert len(rows) == 2 and all(row["result"] == "passed" for row in rows)
    assert all(row["proof_source"] == qr.PROOF_SOURCE and row["run_id"] is None and row["package_fingerprint"] is None for row in rows)
    assert document["solver"] is None
    assert st.stale_reasons(document, bundle.catalog, repo=bundle.repo) == []
    with pytest.raises(ValueError, match="separate proof sources"):
        st.run_tasks(catalog=bundle.catalog, kinds=["kotlin_constraint"], solver_url="http://forbidden.invalid", qualification_evidence=evidence)


@pytest.mark.parametrize("change", ["got", "marker", "source", "live_header", "http_id", "fingerprint", "termination", "commit", "reference", "duplicate", "kind"])
def test_stand_result_cannot_relabel_qualification_or_drop_primary_proof(bundle, change):
    _, rows, document = stand_document(bundle)
    row = rows[0]
    if change == "got":
        row["got"]["amount"] = 777
    elif change == "marker":
        row.pop("qualification_run_id")
    elif change == "source":
        row.pop("proof_source")
    elif change == "live_header":
        document["solver"] = {"url": "http://forbidden.invalid"}
    elif change == "http_id":
        row["run_id"] = row["qualification_run_id"]
    elif change == "fingerprint":
        row["qualification_input_fingerprint"] = "c" * 64
    elif change == "termination":
        row["termination"]["search_started"] = False
    elif change == "commit":
        document["commit"] = "c" * 40
    elif change == "reference":
        document.pop("qualification_evidence")
    elif change == "duplicate":
        rows.append(copy.deepcopy(row))
    elif change == "kind":
        row["executor_kind"] = "kotlin_knob"
    assert st.stale_reasons(document, bundle.catalog, repo=bundle.repo)


def test_normal_no_solver_path_still_skips_constraint_and_never_imports(bundle, monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError("qualification import was not requested")
    monkeypatch.setattr(qr, "load_evidence", unexpected)
    rows = st.run_tasks(catalog=bundle.catalog, kinds=["kotlin_constraint"], tmp_dir=bundle.root / "stand")
    assert all(row["result"] == "skipped" and "proof_source" not in row for row in rows)


def test_component_kinds_not_extended_to_actual_constraint():
    from vmp import stand_components
    assert "kotlin_constraint" not in stand_components.KINDS


def test_balanced_workload_uses_additive_native_pair_not_original_oracle_relabel():
    assert qr.PAIRS["families/balanced_workload"] == ("st-spread-native-1", "st-spread-native-healthy-1")
    assert all(task not in qr.PAIRS["families/balanced_workload"] for task in ("st-spread-1", "st-spread-healthy-1"))


@pytest.mark.parametrize("matches,amount,accepted", [(1, 0, True), (0, 0, False), (2, 0, False), (1, 1, False)])
def test_balanced_healthy_aggregate_is_exactly_one_zero_impact_match(tmp_path, monkeypatch, matches, amount, accepted):
    # Request-contract fixture only, not a native input or Kotlin measurement.
    catalog = FakeCatalog()
    record = "families/balanced_workload"
    catalog.rows[0]["id"] = "balanced_workload"
    for task, name in zip(catalog.rows[0]["stand_tasks"], qr.PAIRS[record]):
        task["id"] = name
        task["expected"]["kotlin_constraint"]["constraint"] = "Credit hours spread over threshold"
    catalog.rows[0]["stand_tasks"][1]["expected"]["kotlin_constraint"].update(matches=matches, amount=amount)
    fixture = ProtocolFixture(tmp_path, monkeypatch, catalog=catalog, record=record)
    args = dict(sha256=fixture.request_sha, challenge=CHALLENGE, catalog=catalog)
    if accepted:
        assert len(qr.read_request(fixture.request_path, **args)[1]) == 2
    else:
        with pytest.raises(qr.QualificationError, match="healthy/violation"):
            qr.read_request(fixture.request_path, **args)


def test_normal_http_dispatch_and_proof_fields_are_unchanged(bundle, monkeypatch):
    called = []
    sentinel = object()
    def normal(kind, ctx, solver):
        called.append((kind, solver))
        return st.Outcome("passed", ctx["expected"], "explicit mocked HTTP path, no request")
    monkeypatch.setattr(st, "_judge_with_solver", normal)
    expected = bundle.catalog.rows[0]["stand_tasks"][0]["expected"]["kotlin_constraint"]
    assert st._kind_outcome("kotlin_constraint", {"expected": expected, "world": {"form": "schedule"}}, solver=sentinel).result == "passed"
    assert called == [("kotlin_constraint", sentinel)]
    legacy = {"solver": {"url": "http://offline.invalid"}, "results": [{"record": RECORD, "task": POSITIVE,
        "executor_kind": "kotlin_constraint", "result": "passed", "run_id": "normal-http", "package_fingerprint": "f" * 64}]}
    assert st._qualification_stale_reasons(legacy, bundle.catalog, bundle.repo) == []


@pytest.mark.parametrize("name,threshold,value,factor", [
    ("workload_spread_threshold", "workload_spread_threshold_min", 1, 60),
    ("min_rest_home", "min_rest_home_min", 660, 1), ("min_rest_outstation", "min_rest_outstation_min", 700, 1),
    ("max_duty_duration", "max_duty_span_min", 780, 1), ("max_pairing_days", "max_pairing_days", 7, 1),
    ("base_tz_offset_min", "base_tz_offset_min", 0, 1)])
@pytest.mark.parametrize("mutate", [False, True])
def test_applied_and_provider_values_are_bound_to_world_not_resealed_each_other(bundle, name, threshold, value, factor, mutate):
    ctx = copy.deepcopy(bundle.contents[POSITIVE]["execution_context"])
    native = next(value for value in bundle.native.values() if value["manifest"]["task"]["id"] == POSITIVE)
    world = copy.deepcopy(bundle.catalog.rows[0]["stand_tasks"][0]["world"])
    world["params"] = {name: value}
    native = copy.deepcopy(native)
    native["files"]["model.json"] = encoded({"rules": [{"id": "R-STAND-mock", "template_fit": {"params": {name: {"value": value}}}}]})
    reading = value * factor + int(mutate)
    ctx["applied"]["applied"] = [{"threshold": threshold, "value": reading,
        "executor": "pairing-builder" if name == "max_pairing_days" else "kotlin-provider", "literal": 0,
        "fromParams": [name], "rules": ["R-STAND-mock"], "declarations": [], "codes": [], "dutyExtensions": []}]
    ctx["provider_readings"][threshold] = reading
    if mutate:
        with pytest.raises(qr.QualificationError, match="differs from declared world"):
            qr._context(ctx, native, world, ctx["input_fingerprint"], bundle.request["cases"][0])
    else:
        qr._context(ctx, native, world, ctx["input_fingerprint"], bundle.request["cases"][0])


@pytest.mark.parametrize("change", ["output", "independent", "head", "reference", "all_output_fields"])
def test_resealed_commit_needs_independent_and_current_checkout_binding(bundle, change):
    other = "c" * 40
    if change == "reference":
        ref = bundle.load().reference
        ref["expected_commit"] = other
        with pytest.raises(qr.QualificationError):
            qr.load_reference(ref, expected_commit=COMMIT, catalog=bundle.catalog, repo=bundle.repo)
        return
    kwargs = {}
    if change == "output":
        bundle.doc["producer"]["commit"] = other
    elif change == "independent":
        kwargs["expected_commit"] = other
    elif change == "head":
        (bundle.repo / ".git/HEAD").write_bytes(other.encode())
    elif change == "all_output_fields":
        bundle.doc["producer"]["commit"] = other
        kwargs["expected_commit"] = other
    bundle.save()
    with pytest.raises(qr.QualificationError):
        bundle.load(**kwargs)


@pytest.mark.parametrize("representation", ["loose", "packed", "worktree"])
def test_current_git_identity_can_be_read_without_a_process(bundle, representation):
    git = bundle.repo / ".git"
    (git / "HEAD").write_text("ref: refs/heads/test\n", encoding="ascii")
    if representation == "loose":
        (git / "refs/heads").mkdir(parents=True)
        (git / "refs/heads/test").write_text(COMMIT + "\n", encoding="ascii")
    else:
        (git / "packed-refs").write_text(COMMIT + " refs/heads/test\n", encoding="ascii")
        if representation == "worktree":
            git.rename(bundle.root / "git-common")
            actual = bundle.root / "git-common/worktrees/mock"
            actual.mkdir(parents=True)
            (actual / "HEAD").write_text("ref: refs/heads/test\n", encoding="ascii")
            (actual / "commondir").write_text("../..\n", encoding="ascii")
            git.write_text("gitdir: " + str(actual) + "\n", encoding="utf-8")
    assert bundle.load().contains(RECORD, POSITIVE)


@pytest.mark.parametrize("changed_time", [False, True])
def test_known_employee_history_timestamp_equivalence_not_causal_mutation(bundle, changed_time):
    # No new execution: source and solved JSON of the mock reader fixture only.
    native = next(value for value in bundle.native.values() if value["manifest"]["task"]["id"] == POSITIVE)
    problem = json.loads(native["files"]["problem.json"])
    problem["employees"][0].update(history={"historyLastDutyEnd": "2026-10-01T00:00"}, historyAsOf="2026-10-01T06:00")
    native["files"]["problem.json"] = encoded(problem)
    solution = bundle.contents[POSITIVE]["solution"]
    solution["employees"][0].update(history={"historyLastDutyEnd": "2026-10-01T00:01:00" if changed_time else "2026-10-01T00:00:00",
                                           "historyMonthBlockMin": None, "historyCalendar": []}, historyAsOf="2026-10-01T06:00:00")
    bundle.contents[POSITIVE]["declared_world_breakdown"]["score_subject_sha256"] = digest(native["files"]["problem.json"])
    bundle.reseal("solution")
    bundle.reseal("declared_world_breakdown")
    if changed_time:
        with pytest.raises(qr.QualificationError, match="immutable fact"):
            bundle.load()
    else:
        assert bundle.load().contains(RECORD, POSITIVE)
