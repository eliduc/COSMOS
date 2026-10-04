"""Read-only, Local-only import of explicitly requested JVM qualification runs.

This does not run a solver, admit a package, approve support or issue a release.
The reviewed test runner and its independently retained execution log are the
primary evidence. Hashes identify local bytes; they are not author signatures.
The original-world breakdown is measured after a real bounded solve, matching
stand_solver.breakdown_of. It is NOT the optimized solution's breakdown.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from uuid import UUID

from cdpm import component_input as ci

REQUEST_SCHEMA = "a06-qualification-request/2"
SCHEMA = "a06-kotlin-qualification-run/2"
SCOPE = "catalog-executor-qualification/local-only"
PROOF_SOURCE = "offline-qualification-run"
PROBE = "org.acme.flighcrewscheduling.solver.A06QualificationRunProbe"
PROBE_SOURCE = "kotlin-solver/src/test/kotlin/" + PROBE.replace(".", "/") + ".kt"
PYTHON_SOURCES = (
    "cdpm/component_input.py", "cdpm/export_task_package.py", "cdpm/model.py",
    "cdpm/gates/gate_task_package.py", "vmp/stand_adapters.py", "vmp/stand_solver.py",
    "vmp/stand_tasks.py", "vmp/qualification_run.py", "vmp/kotlin_tables.py",
    "template_catalog/stand_contracts.py",
)
PAIRS = {
    "families/balanced_workload": ("st-spread-native-1", "st-spread-native-healthy-1"),
    "families/crew_composition": ("st-crew-1", "st-crew-healthy-1"),
    "families/fleet_exclusions": ("st-fleet-1", "st-fleet-healthy-1"),
    "families/reserve_floors": ("st-reserve-1", "st-reserve-healthy-1"),
    "families/rest_floors": ("st-rest-1", "st-rest-healthy-1"),
    "families/calendar_windows": ("st-window-1", "st-window-healthy-1"),
    "families/crew_unavailability": ("st-unavail-native-1", "st-unavail-native-healthy-1"),
    "criteria/credit_hours_spread": ("st-chs-1", "st-chs-healthy-1"),
}
METRIC_RECORD = "criteria/credit_hours_spread"
METRIC = "credit_hours_spread"
OBJECTIVE_ID = "O-STAND-" + METRIC
KINDS = {"kotlin_constraint", "kotlin_metric"}
LIMITS = {"spent_limit_ms": 1000, "step_limit": 1, "seed": 0}
MAX_CASES = 16
MAX_SOLVE_ELAPSED_MS = 5000
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_ZIP_BYTES = 32 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")
_SCORE = re.compile(r"(-?\d+)hard/(-?\d+)soft")
_NAME = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*")
_SIDECARS = {"execution_context": ".execution-context.json", "solution": ".solution.json",
             "declared_world_breakdown": ".declared-world-breakdown.json",
             "solved_solution_breakdown": ".solved-solution-breakdown.json"}


class QualificationError(ValueError):
    pass


def _require(ok, message):
    if not ok:
        raise QualificationError("qualification evidence: " + message)


def _keys(value, keys, label):
    _require(isinstance(value, dict) and set(value) == set(keys), "invalid " + label + " fields")


def _same(left, right):
    return ci._canonical(left) == ci._canonical(right)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _digest(value, label):
    _require(isinstance(value, str) and _SHA.fullmatch(value), "invalid " + label + " SHA256")
    return value


def _regular(path, *, root=None, limit=None):
    path = Path(path)
    _require(path.is_file() and not path.is_symlink(), "not a regular file: " + str(path))
    if root is not None:
        root = Path(root).resolve(strict=True)
        _require(path.resolve(strict=True).is_relative_to(root), "file escaped its explicit root")
        current = path.absolute().parent
        while current != root and current != current.parent:
            _require(not current.is_symlink(), "symlink ancestor is forbidden")
            current = current.parent
    if limit is not None:
        _require(path.stat().st_size <= limit, "artifact exceeds resource limit")
    return path


def _raw(path, *, root=None, limit=MAX_JSON_BYTES):
    return _regular(path, root=root, limit=limit).read_bytes()


def _json(raw):
    try:
        return ci._json(raw)
    except (ValueError, TypeError) as error:
        raise QualificationError("qualification evidence: invalid strict JSON: " + str(error)) from error


def _census(root, paths):
    root = Path(root).resolve(strict=True)
    result = {}
    for path in sorted(paths):
        path = _regular(path, root=root)
        name = path.relative_to(root).as_posix()
        _require(name not in result, "duplicate identity path")
        with path.open("rb") as stream:
            result[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    _require(bool(result), "empty identity census")
    return result


def identities(repo, java_home):
    """Current exact files, read without invoking Maven, Git, Java or services."""
    root, jdk = Path(repo).resolve(strict=True), Path(java_home).resolve(strict=True)
    kotlin = root / "kotlin-solver"
    main = kotlin / "src/main"
    classes = kotlin / "target/classes"
    probes = kotlin / "target/test-classes/org/acme/flighcrewscheduling/solver"
    return {
        "sources": _census(root, [kotlin / "pom.xml", root / PROBE_SOURCE,
            *sorted(main.rglob("*.kt")), *(root / name for name in PYTHON_SOURCES)]),
        "artifacts": _census(root, [p for p in classes.rglob("*") if p.is_file()]),
        "probe": _census(root, list(probes.glob("A06QualificationRunProbe*.class"))),
        "config": _census(root, [p for p in (main / "resources").rglob("*") if p.is_file()]),
        "jdk": _census(jdk, [jdk / "release", jdk / "bin/java.exe", jdk / "lib/modules"]),
    }


def _git_head(repo):
    """Read the explicit checkout identity, without running Git or hooks."""
    marker = Path(repo).resolve(strict=True) / ".git"
    if marker.is_dir() and not marker.is_symlink():
        gitdir = marker
    else:
        value = _raw(marker).decode("utf-8").strip()
        _require(value.startswith("gitdir: "), "repository lacks an exact Git worktree marker")
        gitdir = (marker.parent / value[len("gitdir: "):]).resolve(strict=True)
    head = _raw(gitdir / "HEAD").decode("ascii").strip()
    if re.fullmatch(r"[0-9a-f]{40}", head):
        return head
    _require(head.startswith("ref: refs/") and ".." not in head and "\\" not in head, "unsupported Git HEAD identity")
    ref = head[len("ref: "):]
    _require(re.fullmatch(r"refs/[A-Za-z0-9_./-]+", ref), "unsafe Git ref identity")
    common = gitdir
    if (gitdir / "commondir").is_file():
        common = (gitdir / _raw(gitdir / "commondir").decode("utf-8").strip()).resolve(strict=True)
    for base in dict.fromkeys((gitdir, common)):
        if (base / ref).is_file():
            value = _raw(base / ref, root=base).decode("ascii").strip()
            _require(re.fullmatch(r"[0-9a-f]{40}", value), "invalid current Git commit")
            return value
    for base in dict.fromkeys((gitdir, common)):
        packed = base / "packed-refs"
        if packed.is_file():
            matches = [line.split(" ", 1)[0] for line in _raw(packed).decode("ascii").splitlines()
                       if line.endswith(" " + ref)]
            if matches:
                _require(len(matches) == 1 and re.fullmatch(r"[0-9a-f]{40}", matches[0]), "ambiguous current Git commit")
                return matches[0]
    raise QualificationError("qualification evidence: current Git HEAD is unresolved")


def _uuid(value, label):
    try:
        _require(isinstance(value, str) and str(UUID(value)) == value, "invalid " + label)
    except (ValueError, AttributeError) as error:
        raise QualificationError("qualification evidence: invalid " + label) from error


def _stamp(catalog):
    return {"version": catalog.version(), "fingerprint": catalog.fingerprint()}


def _declared(catalog, record, task_id):
    section, rid = record.split("/", 1)
    records = catalog.criteria().values() if section == "criteria" else catalog.families_full()
    rows = [row for row in records if str(row.get("id")) == rid]
    _require(len(rows) == 1, "missing/duplicate declared record")
    tasks = [task for task in rows[0].get("stand_tasks", []) if task.get("id") == task_id]
    _require(len(tasks) == 1, "missing/duplicate declared task")
    task = copy.deepcopy(tasks[0])
    kind = "kotlin_metric" if record == METRIC_RECORD else "kotlin_constraint"
    expected = task.get("expected", {}).get(kind)
    if kind == "kotlin_metric":
        _keys(expected, {"value", "matches", "refusal"}, "declared metric expectation")
        _require(expected["refusal"] is False
                 and all(type(expected[key]) is int and expected[key] >= 0 for key in ("matches", "value")),
                 "invalid declared metric expectation")
        _require(rows[0].get("zone") == "ROSTERING" and rows[0].get("direction") == "minimize",
                 "unreviewed metric task/direction")
    else:
        _keys(expected, {"constraint", "matches", "amount"}, "declared constraint expectation")
        _require(isinstance(expected["constraint"], str) and bool(expected["constraint"])
                 and all(type(expected[key]) is int and expected[key] >= 0 for key in ("matches", "amount")),
                 "invalid declared constraint expectation")
    _require(isinstance(task.get("world"), dict) and task["world"].get("form") == "schedule",
             "not a declared synthetic schedule world")
    return task, rows[0]


def read_request(path, *, sha256, challenge, catalog):
    raw = _raw(path)
    _require(_hash(raw) == _digest(sha256, "independent request"), "request changed/replayed")
    request = _json(raw)
    _keys(request, {"schema", "scope", "catalog", "challenge", "limits", "cases"}, "request")
    _uuid(challenge, "independent challenge")
    _require(request["schema"] == REQUEST_SCHEMA and request["scope"] == SCOPE,
             "wrong request schema/scope")
    _require(request["challenge"] == challenge, "request challenge differs/replayed")
    _require(_same(request["catalog"], _stamp(catalog)), "request catalogue differs")
    cases = request["cases"]
    _require(isinstance(cases, list) and 2 <= len(cases) <= MAX_CASES, "invalid finite case census")
    _require(_same(request["limits"], {**LIMITS, "max_tasks": len(cases)}), "unapproved resource limits")
    selected, declared, records = {}, {}, {}
    for case in cases:
        _keys(case, {"record", "task", "role", "control_for", "component_zip", "sha256",
                     "executor_kind", "mode", "objective_id"}, "case")
        record, task_id = case["record"], case["task"]
        _require(isinstance(record, str) and record in PAIRS and task_id in PAIRS[record],
                 "case is outside the finite qualification allowlist")
        violation, healthy = PAIRS[record]
        kind, mode, objective_id = (("kotlin_metric", "optimization", OBJECTIVE_ID) if record == METRIC_RECORD
                                    else ("kotlin_constraint", "feasibility-soft", None))
        _require((case["executor_kind"], case["mode"], case["objective_id"]) == (kind, mode, objective_id),
                 "wrong qualification executor/mode/objective")
        is_healthy = task_id == healthy
        _require(case["role"] == ("healthy" if is_healthy else "violation")
                 and case["control_for"] == (violation if is_healthy else None), "wrong control relation")
        _require(case["component_zip"] == task_id + ".zip", "wrong component basename")
        _digest(case["sha256"], "input")
        key = (record, task_id)
        _require(key not in selected, "duplicate requested task")
        task, row = _declared(catalog, record, task_id)
        expected = task["expected"][kind]
        amount = expected["value" if kind == "kotlin_metric" else "amount"]
        # The two load-balance aggregate streams retain one zero-impact match.
        # Other reviewed healthy controls have no matching violation tuples.
        healthy_matches = 1 if record in (METRIC_RECORD, "families/balanced_workload") else 0
        _require((amount == 0 and expected["matches"] == healthy_matches) if is_healthy
                 else (expected["matches"] > 0 and amount > 0), "wrong healthy/violation oracle")
        if kind == "kotlin_metric":
            _metric_rate(task["world"])
        selected[key], declared[key], records[key] = case, task, row
    _require(list(selected) == sorted(selected), "unordered case census")
    for record, _ in selected:
        keys = [(record, task_id) for task_id in PAIRS[record]]
        _require(all(key in selected for key in keys), "missing independent healthy/violation counterpart")
        one, two = (declared[key] for key in keys)
        same_executor = (record == METRIC_RECORD
                         or one["expected"]["kotlin_constraint"]["constraint"] == two["expected"]["kotlin_constraint"]["constraint"])
        _require(same_executor and not _same(one["world"], two["world"]),
                 "control is not a distinct declared world for the same executor")
    return request, selected, declared, records


def _ref(value, root, expected_name):
    _keys(value, {"path", "sha256", "bytes"}, "sidecar reference")
    _require(value["path"] == expected_name and _NAME.fullmatch(expected_name), "unsafe/swapped sidecar path")
    _require(type(value["bytes"]) is int and 0 < value["bytes"] <= MAX_JSON_BYTES, "invalid sidecar size")
    body = _raw(root / expected_name, root=root)
    _require(len(body) == value["bytes"] and _hash(body) == _digest(value["sha256"], "sidecar"),
             "sidecar bytes changed")
    return _json(body), body


def _termination(row, started_at, completed_at):
    _keys(row, {"version", "kind", "mode", "reproducible", "search_steps", "unimproved_steps",
               "search_started", "construction_steps", "elapsed_ms", "step_limit", "unimproved_step_limit",
               "wall_clock_limit_ms", "unimproved_wall_clock_limit_ms", "words"}, "termination")
    _require(type(row["version"]) is int and row["version"] == 1 and row["mode"] == "deterministic"
             and row["reproducible"] is True and row["search_started"] is True, "no completed deterministic solve lifecycle")
    for key in ("search_steps", "unimproved_steps", "construction_steps", "elapsed_ms"):
        _require(type(row[key]) is int and row[key] >= 0, "invalid actual solve counter")
    _require(row["step_limit"] == 1 and type(row["step_limit"]) is int
             and row["wall_clock_limit_ms"] == 1000 and type(row["wall_clock_limit_ms"]) is int
             and row["unimproved_step_limit"] is None and row["unimproved_wall_clock_limit_ms"] is None
             and row["elapsed_ms"] <= MAX_SOLVE_ELAPSED_MS, "solve limits differ/unbounded")
    kind = ("wall_clock_safety" if row["elapsed_ms"] >= 750 else
            "step_count" if row["search_steps"] >= 1 else "search_exhausted")
    _require(row["kind"] == kind and (kind != "wall_clock_safety" or row["search_steps"] == 0),
             "termination contradicts actual counters or used wall-clock search")
    _require(isinstance(row["words"], str) and bool(row["words"].strip()), "missing termination reading")
    try:
        start, end = (datetime.fromisoformat(value.replace("Z", "+00:00")) for value in (started_at, completed_at))
        _require(start.tzinfo is not None and end.tzinfo is not None, "run timestamps lack timezone")
        seconds = (end - start).total_seconds()
        _require(0 <= seconds <= 30 and seconds * 1000 + 2 >= row["elapsed_ms"], "invalid run time interval")
    except (ValueError, TypeError, AttributeError) as error:
        raise QualificationError("qualification evidence: invalid actual run timestamps") from error


def _metric_rate(world):
    rate = world.get("objective_rate")
    _keys(rate, {"value", "as_written", "per"}, "explicit declared objective rate")
    _require(type(rate["value"]) is int and rate["value"] == 1
             and all(isinstance(rate[key], str) and bool(rate[key].strip()) for key in ("as_written", "per")),
             "qualification metric requires the reviewed explicit unit rate")
    return rate


def _default_soft_literals():
    """Read the actual provider's closed zero-unmentioned-soft census."""
    from vmp import kotlin_tables
    body = kotlin_tables.block(kotlin_tables.read(kotlin_tables.PROVIDER),
        "val SOFT_CONSTRAINTS: List<String> = listOf(", where=kotlin_tables.PROVIDER)
    strings = re.findall(r'"(?:[^"\\]|\\.)*"', body)
    remainder = re.sub(r'"(?:[^"\\]|\\.)*"', "", body)
    _require(strings and re.fullmatch(r"[\s,]*", remainder), "unreadable production soft-literal census")
    values = [json.loads(value) for value in strings]
    _require(len(values) == len(set(values)), "duplicate production soft literal")
    return set(values)


def _objective_context(context, objective, world, case, declared_weights):
    """Bind actual production resolution to native data, never to the oracle."""
    _require(context["mode"] == case["mode"], "actual mode differs from independently requested mode")
    objectives = {zone: objective.get(zone, {}).get("objectives", []) for zone in ("pairing", "rostering")}
    _require(all(isinstance(rows, list) for rows in objectives.values()), "invalid native objective census")
    if case["executor_kind"] == "kotlin_constraint":
        _require(context["objective"] is None and not any(objectives.values()),
                 "constraint qualification acquired an objective")
        return dict(declared_weights)
    rate = _metric_rate(world)
    _require(not objectives["pairing"] and len(objectives["rostering"]) == 1,
             "metric qualification needs exactly one native rostering objective")
    selected = objectives["rostering"][0]
    _require(isinstance(selected, dict) and selected.get("id") == case["objective_id"] == OBJECTIVE_ID
             and selected.get("metric") == METRIC and selected.get("direction") == "minimize"
             and _same(selected.get("rate"), rate), "native metric objective/rate differs from declared source")
    from vmp import kotlin_tables, stand_solver
    executor = kotlin_tables.metrics().get(METRIC)
    constraint = stand_solver.metric_constraint(METRIC)
    _require(isinstance(executor, dict) and executor["tasks"] == ["rostering"]
             and not executor["refusal"], "metric executor differs from reviewed production contract")
    actual = context["objective"]
    expected = {"id": OBJECTIVE_ID, "title": selected.get("title"), "metric": METRIC,
        "direction": "minimize", "constraint": constraint, "executorSource": "словарь метрик §5а",
        "weight": rate["value"], "rate": rate["value"],
        "weightSource": "objective.json: rostering.objectives[" + OBJECTIVE_ID + "].rate.value",
        "approximation": executor["approximation"], "sharesConstraintWithPenalty": constraint in declared_weights,
        "criterion": "целевая + Σ штрафов мягких задачи (direction=minimize)"}
    _keys(actual, set(expected), "actual resolved metric objective")
    _require(_same(actual, expected), "resolved metric objective differs from native data/production executor")
    effective = dict(declared_weights)
    effective[constraint] = effective.get(constraint, 0) + rate["value"]
    return effective


def _context(context, native, world, fingerprint, case):
    _keys(context, {"input_fingerprint", "profile", "applied", "coverage", "fit", "provider_readings",
        "provider_code_readings", "provider_policy_readings", "applied_weights", "effective_weights",
        "literals_disabled", "calendar_rules", "history", "mode", "objective"}, "execution context")
    _require(context["input_fingerprint"] == fingerprint and context["profile"] == "DEFAULT", "wrong native execution context")
    for key in ("provider_readings", "provider_code_readings", "provider_policy_readings",
                "applied_weights", "effective_weights", "coverage"):
        _require(isinstance(context[key], dict), "missing actual " + key)
    _require(bool(context["provider_readings"]), "missing provider construction readings")
    applied = context["applied"]
    _keys(applied, {"applied", "ignored", "paramToThreshold", "qualifiedParamToThreshold", "offLiterals",
                   "literalsInForce", "hardWeights"}, "applied threshold snapshot")
    _require(applied["ignored"] == [] and isinstance(applied["applied"], list), "native parameters ignored")
    native_doc = _json(native["files"]["model.json"])
    rules = {rule["id"]: set((rule.get("template_fit") or {}).get("params") or {})
             for rule in native_doc.get("rules", [])}
    seen = set()
    for item in applied["applied"]:
        _keys(item, {"threshold", "value", "executor", "literal", "fromParams", "rules", "declarations",
                     "codes", "dutyExtensions"}, "applied threshold")
        key = item["threshold"]
        _require(isinstance(key, str) and key not in seen and type(item["value"]) is int
                 and isinstance(item["fromParams"], list) and bool(item["fromParams"])
                 and isinstance(item["rules"], list) and bool(item["rules"]), "invalid applied threshold")
        _require(all(isinstance(name, str) for name in item["fromParams"] + item["rules"])
                 and all(rule in rules for rule in item["rules"])
                 and set(item["fromParams"]) <= set().union(*(rules[rule] for rule in item["rules"])),
                 "applied threshold provenance differs from native rules")
        seen.add(key)
        if item["executor"] == "kotlin-provider":
            reading = context["provider_code_readings"].get(key) if item["codes"] else context["provider_readings"].get(key)
            _require(_same(reading, item["codes"] if item["codes"] else item["value"]), "provider differs from applied threshold")
        else:
            _require(item["executor"] == "pairing-builder", "unknown applied executor")
    # Closed source-to-applied checks, independent of catalogue expected.
    # Values come only from the declared world, already checked by ci.read_bytes.
    by_threshold = {item["threshold"]: item for item in applied["applied"]}
    params = world.get("params") or {}
    numeric = (("workload_spread_threshold", "workload_spread_threshold_min", 60),
            ("crew_size", "crew_size", 1), ("min_rest_home", "min_rest_home_min", 1),
            ("min_rest_outstation", "min_rest_outstation_min", 1),
            ("max_duty_duration", "max_duty_span_min", 1), ("max_pairing_days", "max_pairing_days", 1),
            ("base_tz_offset_min", "base_tz_offset_min", 1),
            ("min_turnaround", "min_sit_home_min", 1), ("min_turnaround", "min_sit_outstation_min", 1))
    _require(set(params) <= {name for name, _, _ in numeric} | {"fleet_types", "reserve_crews"},
             "qualification has an unreviewed world parameter")
    expected_thresholds = {threshold for name, threshold, _ in numeric if name in params}
    for name, threshold, factor in numeric:
        if name in params:
            item = by_threshold.get(threshold)
            _require(type(params[name]) is int and isinstance(item, dict)
                     and item["value"] == params[name] * factor and name in item["fromParams"],
                     "applied value differs from declared world: " + name)
    if "fleet_types" in params:
        expected_thresholds.add("fleet_types")
        item = by_threshold.get("fleet_types")
        _require(isinstance(item, dict) and _same(sorted(item["codes"]), sorted(params["fleet_types"]))
                 and item["value"] == len(set(params["fleet_types"])),
                 "applied fleet codes differ from declared world")
    if "reserve_crews" in params:
        for base, value in params["reserve_crews"].items():
            expected_thresholds.add("reserve_crews_min[" + base + "]")
            item = by_threshold.get("reserve_crews_min[" + base + "]")
            _require(isinstance(item, dict) and type(value) is int and item["value"] == value,
                     "applied reserve differs from declared world")
    _require(set(by_threshold) == expected_thresholds, "applied threshold census differs from declared world")
    coverage = context["coverage"]
    qualifications = coverage.get("qualifications")
    _require(isinstance(qualifications, dict) and type(qualifications.get("employeesTotal")) is int
             and qualifications["employeesTotal"] == len(world.get("crews", [])), "missing/wrong actual fact coverage")
    if "crew_size" in (world.get("params") or {}):
        crew = coverage.get("crewSize")
        _require(isinstance(crew, dict) and crew.get("seatsPerFlight") == world["params"]["crew_size"]
                 and crew.get("flightsChecked") == len(world.get("flights", [])), "crew-size fact coverage differs")
    if "reserve_crews" in (world.get("params") or {}):
        reserve = coverage.get("reserve")
        _require(isinstance(reserve, dict) and sorted(reserve.get("namedBases", [])) == sorted(world["params"]["reserve_crews"])
                 and type(reserve.get("horizonDays")) is int and reserve["horizonDays"] > 0, "reserve fact coverage missing")
    fit = context["fit"]
    _keys(fit, {"blocksSeen", "plans", "generated", "executedElsewhere", "ignored", "refused", "notHard",
                "registered", "fitOn", "states"}, "logic fit")
    _require(type(fit["blocksSeen"]) is int and fit["blocksSeen"] >= 0
             and fit["fitOn"] == "qualification:" + native["manifest"]["task"]["id"]
             and fit["ignored"] == fit["refused"] == [] and isinstance(fit["registered"], list)
             and bool(fit["registered"]) and all(isinstance(name, str) for name in fit["registered"])
             and len(set(fit["registered"])) == len(fit["registered"]), "missing/refused native fit or actual factory registrations")
    _require(all(isinstance(fit[key], list) for key in ("plans", "generated", "executedElsewhere", "notHard"))
             and fit["blocksSeen"] == sum(len(fit[key]) for key in ("generated", "executedElsewhere", "notHard")),
             "incomplete actual logic fit census")
    _require(isinstance(context["literals_disabled"], list) and isinstance(context["calendar_rules"], list),
             "missing native application snapshots")
    problem = _json(native["files"]["problem.json"])
    # world_to_problem emits addressed employee history, not a blanket zero-
    # history authority. Qualification may not manufacture that authority.
    _require(problem.get("history") is None and context["history"] is None,
             "qualification has undeclared zero-history authority")
    forms = _json(native["files"].get("threshold-forms.json", b"{}"))
    calendar = [{"ruleId": row["rule"], "action": row["action"], "days": [day.upper() for day in row["days"]]}
                for row in forms.get("calendar_window_penalties", [])]
    _require(_same(context["calendar_rules"], calendar), "applied calendar differs from native form")
    for weights in (context["applied_weights"], context["effective_weights"]):
        _require(all(isinstance(key, str) and type(value) is int and value >= 0 for key, value in weights.items()),
                 "invalid actual soft weights")
    disabled = context["literals_disabled"]
    _require(all(isinstance(name, str) for name in disabled) and len(disabled) == len(set(disabled)), "invalid disabled literal census")
    objective = _json(native["files"]["objective.json"])
    names = {"balanced_workload": "Credit hours spread over threshold", "calendar_windows": "Calendar window layover penalty"}
    declared_weights = {}
    for zone in ("pairing", "rostering"):
        for component in objective.get(zone, {}).get("components", []):
            if component.get("role") == "soft_penalty" and component.get("active"):
                family = component.get("family")
                _require(family in names, "qualification has an unreviewed soft component")
                name, weight = names[family], component.get("weight")
                _require(type(weight) is int and weight >= 0, "native penalty weight is not exact")
                _require(name not in declared_weights or declared_weights[name] == weight, "ambiguous native penalty weight")
                declared_weights[name] = weight
    _require(_same(context["applied_weights"], declared_weights), "applied penalty weight differs from native source")
    expected_weights = _objective_context(context, objective, world, case, declared_weights)
    _require(all(context["effective_weights"].get(name) == value for name, value in expected_weights.items())
             and set(context["effective_weights"]) == set(expected_weights) | _default_soft_literals()
             and set(disabled) == set(context["effective_weights"]) - set(expected_weights)
             and all(context["effective_weights"][name] == 0 for name in disabled),
             "native objective+penalty/zero-unmentioned-soft law differs")


def _breakdown(wrapped, *, context_sha, subject_sha, fingerprint, registered, expected_constraint, effective_weights):
    _keys(wrapped, {"execution_context_sha256", "score_subject_sha256", "breakdown"}, "breakdown subject")
    _require(wrapped["execution_context_sha256"] == context_sha and wrapped["score_subject_sha256"] == subject_sha,
             "swapped/resealed breakdown execution context or score subject")
    breakdown = wrapped["breakdown"]
    _keys(breakdown, {"score", "hard", "soft", "packageFingerprint", "generated", "constraints"}, "breakdown")
    _require(breakdown["packageFingerprint"] == fingerprint and isinstance(breakdown["generated"], list)
             and isinstance(breakdown.get("constraints"), dict)
             and bool(breakdown["constraints"]), "missing actual constraint breakdown")
    _require(set(breakdown["constraints"]) <= set(registered), "breakdown constraint not registered by actual factory")
    from vmp import stand_solver
    target = stand_solver.constraint_row(breakdown, expected_constraint)
    for name, row in breakdown["constraints"].items():
        _require(isinstance(name, str), "invalid breakdown row name")
        _keys(row, {"score", "hard", "soft", "matches", "weight"}, "breakdown row")
        _require(all(type(row.get(key)) is int for key in ("hard", "soft", "matches"))
                 and row["matches"] >= 0, "invalid breakdown counts")
        for key in ("score", "weight"):
            _require(isinstance(row.get(key), str) and _SCORE.fullmatch(row[key]), "invalid HardSoft reading")
        _require(tuple(map(int, _SCORE.fullmatch(row["score"]).groups())) == (row["hard"], row["soft"]),
                 "score text differs from actual numeric breakdown")
        if name in effective_weights:
            # NativeExecutionPlan stores nonnegative override magnitudes;
            # penalize() makes ScoreDirector's constraintWeight negative.
            # Bind that signed weight exactly, never accept abs(weight).
            _require(tuple(map(int, _SCORE.fullmatch(row["weight"]).groups())) == (0, -effective_weights[name]),
                     "breakdown weight differs from actual resolved execution context")
    _require(type(breakdown["hard"]) is int and type(breakdown["soft"]) is int
             and isinstance(breakdown["score"], str) and _SCORE.fullmatch(breakdown["score"]), "invalid breakdown total")
    total = tuple(sum(row[key] for row in breakdown["constraints"].values()) for key in ("hard", "soft"))
    _require(total == (breakdown["hard"], breakdown["soft"])
             == tuple(map(int, _SCORE.fullmatch(breakdown["score"]).groups())), "breakdown totals differ from contributions")
    weights = tuple(map(int, _SCORE.fullmatch(target["weight"]).groups()))
    _require(any(weights), "target constraint disabled by zero weight")
    if expected_constraint in effective_weights:
        _require(target["hard"] == 0 and target["soft"] <= 0,
                 "native minimizing metric/penalty has a non-penalty contribution")
    for value, weight in zip((target["hard"], target["soft"]), weights):
        _require(value == 0 if weight == 0 else value % abs(weight) == 0, "non-integral/unsupported violation measure")
    return {"constraint": expected_constraint, "matches": target["matches"], "amount": stand_solver.amount_of(target)}


def _moment_same(value, wanted):
    try:
        return isinstance(value, str) and isinstance(wanted, str) and datetime.fromisoformat(value) == datetime.fromisoformat(wanted)
    except (TypeError, ValueError):
        return False


def _solution(solution, problem):
    """Check immutable native facts, allowing only actual planning variables.

    Jackson emits default-valued fields absent from the source JSON. The closed
    v1 defaults below are from the hashed native domain classes, not the oracle.
    Unknown/non-default facts fail closed rather than being silently accepted.
    """
    defaults = {
        "airports": {"name": "", "latitude": 0.0, "longitude": 0.0, "taxiTimeInMinutes": None, "languages": []},
        "employees": {**{key: [] for key in ("dutyExtensionConsents", "unavailableDays", "languages", "doNotFlyWith",
            "stationsDenied", "undesiredAirports", "preferredFlights")}, "qualificationWindows": {},
            "workTimeLimitMin": 0, "flightTimeLimitMin": 0, "isReserve": False,
            "historyAsOf": None, "history": None, "historyAssumedZero": False},
        "flights": {"transportOnly": False, **{key: [] for key in ("aircraftTypes", "stationCodes", "requiredCodesAll", "legIds", "busyWindows")},
            "blockMinutes": 0, "blockMinutesByMonth": {}, "workMinutesByMonth": {}, "minRestAfterMin": 0},
        "flightAssignments": {"pinned": False, "requiredCodes": [], "chainId": None},
    }
    _require(isinstance(solution, dict) and set(solution) <= set(problem) | {"score", "solverStatus", "history", "window_horizon"},
             "foreign solved solution fields")
    _require(solution.get("history") is None and solution.get("solverStatus") is None,
             "foreign solved solution state/history")
    employees = {row["id"] for row in problem["employees"]}
    for collection, field in (("airports", "code"), ("employees", "id"), ("flights", "flightNumber"), ("flightAssignments", "id")):
        values = solution.get(collection)
        _require(isinstance(values, list) and all(isinstance(row, dict) and isinstance(row.get(field), str) for row in values),
                 "missing actual solved entity collection")
        actual = {row[field]: row for row in values}
        source = {row[field]: row for row in problem[collection]}
        _require(len(values) == len(actual) and set(actual) == set(source), "solution's immutable entity census differs from declared world")
        for identity, original in source.items():
            allowed = {**copy.deepcopy(defaults[collection]), **original}
            if collection == "flights":
                allowed["flightLegId"] = original.get("flightLegId") or identity
            if collection == "flightAssignments":
                allowed["crewSlotId"] = original.get("crewSlotId") or identity
                assigned = actual[identity].get("employee")
                _require(assigned is None or isinstance(assigned, str) and assigned in employees,
                         "solved assignment refers to an undeclared employee")
                allowed["employee"] = assigned  # the only mutable planning value
            row = actual[identity]
            _require(set(row) <= set(allowed) and set(original) <= set(row), "solved entity has foreign/missing facts")
            for key, value in row.items():
                wanted = allowed[key]
                if key in ("departureUTCDateTime", "arrivalUTCDateTime", "historyAsOf") and wanted is not None:
                    same = _moment_same(value, wanted)
                elif key == "history" and isinstance(wanted, dict):
                    # EmployeeHistory's non-addressed fields are nullable or
                    # empty collections; extra non-empty facts are not native.
                    same = isinstance(value, dict) and all(
                        _moment_same(value.get(k), v) if k == "historyLastDutyEnd" and v is not None else _same(value.get(k), v)
                        for k, v in wanted.items())
                    same = same and all(v is None or v == [] or v == {} for k, v in value.items() if k not in wanted)
                else:
                    same = _same(value, wanted)
                _require(same, "solved immutable fact differs from declared world: " + collection + "." + key)
    horizon = solution.get("window_horizon")
    expected_horizon = problem.get("window_horizon")
    if expected_horizon is None:
        _require(horizon is None, "undeclared solved horizon")
    else:
        _require(isinstance(horizon, dict) and set(horizon) <= set(expected_horizon) | {"source"}
                 and all(_same(horizon.get(key), value) for key, value in expected_horizon.items())
                 and horizon.get("source") == expected_horizon.get("source"), "solved horizon differs from declared world")


class Evidence:
    def __init__(self, document, request, declared, observations, reference):
        self.document, self.request = document, request
        self.declared, self.observations = declared, observations
        self.reference = reference

    def contains(self, record, task):
        return (record, task) in self.observations

    def judge(self, kind, expected, record, task, world):
        _require(kind in KINDS and self.contains(record, task)
                 and self.observations[(record, task)]["executor_kind"] == kind, "unmeasured qualification kind/task")
        source = self.declared[(record, task)]
        _require(_same(world, source["world"]) and _same(expected, source["expected"][kind]), "qualification task changed after import")
        measured = self.observations[(record, task)]
        _require(_same(measured["got"], expected), "actual declared-world measurement differs from oracle")
        return copy.deepcopy(measured)


def load_evidence(path, *, request_path, request_sha256, challenge, expected_commit, catalog, input_root, repo, java_home):
    """Validate existing exact artifacts only. Never starts or contacts a solver."""
    request, selected, declared, records = read_request(request_path, sha256=request_sha256, challenge=challenge, catalog=catalog)
    path = _regular(path)
    _require(path.name == "qualification-run.json", "wrong qualification envelope basename")
    output_root, input_root = path.parent.resolve(), Path(input_root).resolve(strict=True)
    doc = _json(_raw(path))
    _keys(doc, {"schema", "scope", "proof_source", "catalog", "challenge", "request_sha256", "producer",
                "identities", "inputs", "observations", "files"}, "qualification output")
    _require(doc["schema"] == SCHEMA and doc["scope"] == SCOPE and doc["proof_source"] == PROOF_SOURCE, "wrong evidence source/scope/schema")
    _require(_same(doc["catalog"], _stamp(catalog)) and doc["challenge"] == challenge
             and doc["request_sha256"] == request_sha256, "stale/relabelled/replayed qualification")
    _keys(doc["producer"], {"class", "commit", "java_version"}, "producer")
    _require(isinstance(expected_commit, str) and re.fullmatch(r"[0-9a-f]{40}", expected_commit)
             and expected_commit == _git_head(repo), "independent commit differs from current checkout")
    _require(doc["producer"]["class"] == PROBE and doc["producer"]["commit"] == expected_commit
             and isinstance(doc["producer"]["java_version"], str) and bool(doc["producer"]["java_version"]), "wrong producer identity")
    current = identities(repo, java_home)
    release = _raw(Path(java_home) / "release").decode("utf-8")
    version = re.search(r'^JAVA_VERSION="([^"\r\n]+)"\r?$', release, re.MULTILINE)
    _require(version is not None and doc["producer"]["java_version"] == version.group(1), "producer JDK version differs from runtime identity")
    _keys(doc["identities"], {"before", "after"}, "identity guards")
    _require(_same(doc["identities"]["before"], current) and _same(doc["identities"]["after"], current), "stale/incomplete source/artifact/config/JDK identity")
    wanted_inputs = {case["task"]: case["sha256"] for case in selected.values()}
    _require(_same(doc["inputs"], {"before": wanted_inputs, "after": wanted_inputs}), "input census changed")
    _require(isinstance(doc["observations"], list) and len(doc["observations"]) == len(selected), "incomplete actual run census")
    observations, run_ids, sidecars = {}, set(), []
    for row in doc["observations"]:
        _keys(row, {"record", "task", "run_id", "started_at", "completed_at", "input_fingerprint", "solve", "termination", *_SIDECARS}, "observation")
        key = (row["record"], row["task"])
        _require(key in selected and key not in observations, "foreign/duplicate run task")
        case, task = selected[key], declared[key]
        _uuid(row["run_id"], "run UUID")
        _require(row["run_id"] not in run_ids, "reused actual run UUID")
        run_ids.add(row["run_id"])
        _require(_same(row["solve"], {"called": True, "completed": True, "exception": None}), "actual solve missing/failed")
        _termination(row["termination"], row["started_at"], row["completed_at"])
        raw = _raw(input_root / case["task"] / case["component_zip"], root=input_root, limit=MAX_ZIP_BYTES)
        _require(_hash(raw) == case["sha256"], "exact native input changed")
        native = ci.read_bytes(raw, catalog=catalog)
        native_task = native["manifest"]["task"]
        _require(native_task["record"] == key[0] and native_task["id"] == key[1], "native task identity differs")
        fingerprint = _hash(native["files"][ci.MANIFEST])
        _require(row["input_fingerprint"] == fingerprint, "native input fingerprint differs")
        values, bodies = {}, {}
        for name, suffix in _SIDECARS.items():
            values[name], bodies[name] = _ref(row[name], output_root, row["task"] + suffix)
            sidecars.append(row[name])
        _context(values["execution_context"], native, task["world"], fingerprint, case)
        solution = values["solution"]
        problem = _json(native["files"]["problem.json"])
        _require(1 <= len(problem["flights"]) <= 8 and 1 <= len(problem["employees"]) <= 8
                 and 1 <= len(problem["flightAssignments"]) <= 32,
                 "synthetic world exceeds qualification resource limits")
        _require(all(row.get("chainId") is None and row.get("deadhead") is False for row in problem["flightAssignments"]),
                 "qualification has no pairing/deadhead authority")
        _solution(solution, problem)
        kind = case["executor_kind"]
        expected = task["expected"][kind]
        from vmp import stand_solver
        constraint = (stand_solver.metric_constraint(METRIC) if kind == "kotlin_metric" else expected["constraint"])
        got = _breakdown(values["declared_world_breakdown"], context_sha=_hash(bodies["execution_context"]),
                         subject_sha=_hash(native["files"]["problem.json"]), fingerprint=fingerprint,
                         registered=values["execution_context"]["fit"]["registered"], expected_constraint=constraint,
                         effective_weights=values["execution_context"]["effective_weights"])
        _breakdown(values["solved_solution_breakdown"], context_sha=_hash(bodies["execution_context"]),
                   subject_sha=_hash(bodies["solution"]), fingerprint=fingerprint,
                   registered=values["execution_context"]["fit"]["registered"], expected_constraint=constraint,
                   effective_weights=values["execution_context"]["effective_weights"])
        _require(isinstance(solution.get("score"), str) and _SCORE.fullmatch(solution["score"])
                 and values["solved_solution_breakdown"]["breakdown"].get("score") == solution["score"],
                 "solved solution and same-context breakdown score differ")
        if kind == "kotlin_metric":
            got = {"value": got["amount"], "matches": got["matches"], "refusal": False}
        else:
            from vmp import stand_tasks
            _require(stand_tasks.violation_size_refusal(records[key].get("violation_size"), expected, got) is None,
                     "constraint measurement contradicts declared violation size")
        observations[key] = {"got": got, "run_id": row["run_id"], "input_fingerprint": fingerprint,
                             "termination": copy.deepcopy(row["termination"]), "executor_kind": kind}
    _require(list(observations) == sorted(selected), "unordered/foreign actual run census")
    _require(_same(doc["files"], sorted(sidecars, key=lambda item: item["path"])), "missing/duplicate/foreign output census")
    actual_files = {p.relative_to(output_root).as_posix() for p in output_root.rglob("*") if p.is_file()}
    _require(actual_files == {path.name, *(ref["path"] for ref in sidecars)}, "unlisted/missing output artifact")
    _require(not any(p.is_symlink() for p in output_root.rglob("*")), "symlink output artifact")
    reference = {"path": str(path.resolve()), "sha256": _hash(_raw(path)),
        "request_path": str(Path(request_path).resolve()), "request_sha256": request_sha256, "challenge": challenge,
        "input_root": str(input_root), "java_home": str(Path(java_home).resolve()), "expected_commit": expected_commit}
    return Evidence(doc, request, declared, observations, reference)


def load_reference(ref, *, expected_commit, catalog, repo):
    _keys(ref, {"path", "sha256", "request_path", "request_sha256", "challenge", "input_root", "java_home", "expected_commit"}, "qualification reference")
    _require(ref["expected_commit"] == expected_commit, "reference commit differs from independent identity")
    _require(_hash(_raw(ref["path"])) == _digest(ref["sha256"], "output"), "primary qualification evidence changed")
    return load_evidence(ref["path"], request_path=ref["request_path"], request_sha256=ref["request_sha256"],
                         challenge=ref["challenge"], expected_commit=expected_commit, catalog=catalog,
                         input_root=ref["input_root"], repo=repo, java_home=ref["java_home"])
