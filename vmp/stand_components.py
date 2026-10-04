"""Fail-closed import of explicitly run, offline Kotlin component measurements.

This is not a solver invocation, admission decision, signature, or release.
Hashes prove which local evidence and bytes are being read, not who authored
the JSON. The reviewed JVM probe and its actual test log are primary evidence.
No result is copied from a different executor kind or from a JUnit green count.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from template_catalog.stand_contracts import component_expectation_problem

KINDS = ("kotlin_knob", "kotlin_form")
PROBE = "org.acme.flighcrewscheduling.solver.A06TypedComponentProbe"
PROBE_SOURCE = "kotlin-solver/src/test/kotlin/" + PROBE.replace(".", "/") + ".kt"


def _require(ok, message):
    if not ok:
        raise ValueError("component evidence: " + message)


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(path):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "duplicate JSON key " + key)
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(
                          ValueError("non-finite JSON " + value)))


def _map(repo, paths):
    result = {}
    root = Path(repo).resolve()
    for path in paths:
        _require(path.is_file() and not path.is_symlink(), "not a regular file: " + str(path))
        _require(path.resolve().is_relative_to(root), "file outside repository")
        result[path.relative_to(root).as_posix()] = _sha(path)
    _require(bool(result), "empty identity census")
    return result


def identities(repo):
    root = Path(repo).resolve()
    kotlin = root / "kotlin-solver"
    main = kotlin / "src/main"
    sources = [kotlin / "pom.xml", root / PROBE_SOURCE, *sorted(main.rglob("*.kt"))]
    _require(any(main.rglob("*.kt")), "missing Kotlin main sources")
    classes = kotlin / "target/classes"
    probe_dir = kotlin / "target/test-classes/org/acme/flighcrewscheduling/solver"
    return {
        "sources": _map(root, sources),
        "artifacts": _map(root, sorted(p for p in classes.rglob("*") if p.is_file())),
        "probe": _map(root, sorted(probe_dir.glob("A06TypedComponentProbe*.class"))),
    }


class Evidence:
    def __init__(self, document, declared, observations):
        self.document, self.declared, self.observations = document, declared, observations

    def judge(self, kind, expected, record, task, world):
        key = (record, task, kind)
        _require(key in self.observations, "missing observation for " + repr(key))
        source = self.declared[(record, task)]
        _require(world == source["world"], "world changed after import")
        _require(expected == source["expected"].get(kind), "expectation changed after import")
        problem = component_expectation_problem(kind, expected)
        _require(problem is None, str(problem))
        got = self.observations[key]
        problem = component_expectation_problem(kind, got)
        _require(problem is None, "invalid observation: " + str(problem))
        _require(got == expected, "measured component differs from declared expectation")
        return got


def load_evidence(path, *, catalog, input_root, repo):
    doc = _json(path)
    _require(isinstance(doc, dict) and set(doc) == {
        "schema", "scope", "catalog", "producer", "sources", "artifacts", "probe",
        "inputs", "observations", "guards"}, "invalid envelope fields")
    _require(doc["schema"] == "a06-kotlin-component/1" and doc["scope"] == "component-only",
             "wrong schema or scope")
    _require(doc["catalog"] == {"version": catalog.version(), "fingerprint": catalog.fingerprint()},
             "wrong catalogue identity")
    producer = doc["producer"]
    _require(isinstance(producer, dict) and set(producer) == {"class", "commit", "java_version"},
             "invalid producer")
    _require(producer["class"] == PROBE and
             re.fullmatch(r"[0-9a-f]{40}", str(producer["commit"])) is not None and
             isinstance(producer["java_version"], str) and bool(producer["java_version"].strip()),
             "invalid producer identity")
    for key, current in identities(repo).items():
        _require(doc[key] == current, "changed or incomplete " + key + " identity census")
    _require(doc["guards"] == {"inputs_unchanged": True, "sources_unchanged": True,
                              "artifacts_unchanged": True}
             and all(type(v) is bool for v in doc["guards"].values()), "failed before/after guards")
    declared = {}
    for section, rows in (("families", catalog.families_full()), ("criteria", catalog.criteria().values())):
        for record in rows:
            for task in record.get("stand_tasks", []):
                declared[(section + "/" + record["id"], task["id"])] = task
    _require(isinstance(doc["observations"], list) and bool(doc["observations"]), "empty observations")
    observations = {}
    used_inputs = set()
    root = Path(input_root).resolve()
    _require(isinstance(doc["inputs"], dict), "inputs not an object")
    for row in doc["observations"]:
        _require(isinstance(row, dict) and set(row) == {"record", "task", "executor_kind", "got"},
                 "invalid observation fields")
        record, task, kind = row["record"], row["task"], row["executor_kind"]
        _require(all(isinstance(v, str) for v in (record, task, kind)), "invalid observation identity")
        _require(re.fullmatch(r"[A-Za-z0-9_-]+", task) is not None, "unsafe task filename")
        key = (record, task, kind)
        _require(kind in KINDS and (record, task) in declared, "unknown task or executor kind")
        _require(key not in observations, "duplicate observation")
        source = declared[(record, task)]
        _require(kind in source["expected"], "executor kind has no declared expectation")
        folder = root / task
        _require(folder.resolve().is_relative_to(root) and not folder.is_symlink(), "unsafe input path")
        files = {"zip_sha256": folder / (task + ".zip"),
                 "world_sha256": folder / (task + ".world.json"),
                 "problem_sha256": folder / (task + ".problem.json"),
                 "identity_sha256": folder / "identity.json"}
        _require(all(p.is_file() and not p.is_symlink() for p in files.values()), "missing input file")
        hashes = {k: _sha(v) for k, v in files.items()}
        _require(doc["inputs"].get(task) == hashes, "input hash mismatch")
        identity = _json(files["identity_sha256"])
        _require(identity.get("task") == task and identity.get("catalog_version") == catalog.version()
                 and identity.get("catalog_fingerprint") == catalog.fingerprint(), "wrong input identity")
        _require(identity.get("files") == {p.name: hashes[k] for k, p in files.items()
                                           if k != "identity_sha256"}, "input manifest mismatch")
        _require(_json(files["world_sha256"]) == {"world": source["world"], "expected": source["expected"]},
                 "input world/expectation not the declared catalogue task")
        problem = component_expectation_problem(kind, row["got"])
        _require(problem is None, "invalid measurement: " + str(problem))
        observations[key] = row["got"]
        used_inputs.add(task)
    _require(set(doc["inputs"]) == used_inputs, "extra or missing input identity")
    return Evidence(doc, declared, observations)


def reference(path, input_root):
    return {"path": str(Path(path).resolve()), "sha256": _sha(path),
            "input_root": str(Path(input_root).resolve())}


def load_reference(ref, *, catalog, repo):
    _require(isinstance(ref, dict) and set(ref) == {"path", "sha256", "input_root"},
             "invalid evidence reference")
    _require(_sha(ref["path"]) == ref["sha256"], "evidence file changed")
    return load_evidence(ref["path"], catalog=catalog, input_root=ref["input_root"], repo=repo)
