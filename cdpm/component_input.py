"""Declared, non-executable native inputs for offline component measurements.

This is not a task-package export, a support approval or a solver invocation.
The ordinary task exporter retains its admission gate. Only content validation
and native serializers are shared; this envelope never has manifest.json.
Hashes identify exact local bytes, not an author or a successful measurement.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import stat
import zipfile
from pathlib import Path

from cdpm import export_task_package as tp, model as m


SCHEMA = "component-input/1"
SCOPE = "component-only"
SPEC_VERSION = "1.21"
MANIFEST = "component-manifest.json"
REQUIRED_FIELDS = frozenset({
    "schema", "scope", "spec_version", "catalog", "task", "files",
    "consistency", "families", "form", "coverage", "measure", "has_data",
    "excluded_until_data",
})
OPTIONAL_FIELDS = frozenset({
    "fits_catalog", "rule_maps", "logic_unproven", "crew_size", "time",
    "horizon", "history", "freeze_clauses", "obligations",
})
REQUIRED_MEMBERS = frozenset({
    "model.json", "objective.json", "vmp-form.json", "vmp-form.md",
    "smt-report.json", "world.json", "problem.json", "stand-world.json",
    "declared-world.json", "declared-expected.json",
})
OPTIONAL_MEMBERS = frozenset({
    "threshold-forms.json", "logic-examples.json", "clean-data.json",
    "measure-protocol.json", "obligations-ledger.json", "obligations-report.json",
    "svod.md", "anketa.md",
})
_HASH = re.compile(r"[0-9a-f]{64}")
_TASK = re.compile(r"[A-Za-z0-9_-]+")
_RECORD = re.compile(r"(families|criteria)/([A-Za-z0-9_-]+)")
_CLAUSE = re.compile(r"freeze/clauses/[A-Za-z0-9_-]+\.json")


class ComponentInputError(ValueError):
    def __init__(self, message: str, *, code: str = "component-input"):
        super().__init__("component input: " + message)
        self.code = code


def _require(ok, words):
    if not ok:
        raise ComponentInputError(words)


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _json(raw: bytes):
    try:
        value = m.loads_strict(raw)
        _canonical(value)  # Reject non-finite numbers, including unused fields.
        return value
    except (ValueError, TypeError, UnicodeDecodeError) as error:
        raise ComponentInputError("invalid strict JSON: " + str(error)) from error


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _same(left, right) -> bool:
    return _canonical(left) == _canonical(right)


def _stamp(catalog):
    return {"version": catalog.version(), "fingerprint": catalog.fingerprint()}


def _declared(catalog, record: str, task_id: str) -> dict:
    """Resolve the task from the exact catalogue; accept no caller oracle."""
    match = _RECORD.fullmatch(record) if isinstance(record, str) else None
    _require(match is not None and isinstance(task_id, str)
             and _TASK.fullmatch(task_id) is not None, "invalid declared task identity")
    section, rid = match.groups()
    records = (catalog.families_full() if section == "families"
               else catalog.criteria().values())
    found = [row for row in records if row.get("id") == rid]
    _require(len(found) == 1, "record is not declared exactly once")
    tasks = [task for task in found[0].get("stand_tasks", []) if task.get("id") == task_id]
    _require(len(tasks) == 1, "task is not declared exactly once")
    declared = copy.deepcopy(tasks[0])
    _require(isinstance(declared.get("world"), dict)
             and isinstance(declared.get("expected"), dict)
             and bool(declared["expected"]), "declared world/expectation is incomplete")
    from vmp import stand_adapters, stand_solver, stand_tasks
    meta = stand_tasks._family_meta(catalog)
    world = declared["world"]
    family = rid if section == "families" else None
    families = stand_tasks.families_for(world, family, meta)
    rules = stand_solver._rules_for(world, families, meta)
    objective = (stand_solver.objective_for(found[0], world=world)
                 if section == "criteria" else None)
    model = stand_solver.model_doc(rules, world=world, objective=objective,
                                  family=family, catalog=catalog)
    return {"task": declared, "model": model, "rules": rules,
            "source": stand_solver._stand_source(world, rules, objective, catalog=catalog),
            "sheets": stand_solver._sheets(world),
            "problem": stand_adapters.world_to_problem(world)}


def _producer(catalog):
    from cdpm import catalog_bridge
    _require(_stamp(catalog_bridge.catalog()) == _stamp(catalog),
             "selected and producer catalogue identities differ")


def prepare_run(base: Path, *, catalog, record: str, task_id: str) -> Path:
    """Create a NEW declared synthetic run; does not produce an SMT verdict.

    An actual consistency report must subsequently be obtained through the
    ordinary verification workflow. Existing runs are never overwritten.
    """
    from vmp import stand_solver
    _producer(catalog)
    definition = _declared(catalog, record, task_id)
    _require(not (Path(base) / stand_solver.SOURCE_RUN).exists(),
             "component source run already exists")
    return stand_solver._write_run(Path(base), definition["model"], definition["sheets"],
                                   source=definition["source"])


def _check_run(run_dir: Path, definition: dict) -> dict[str, bytes]:
    """A customer/altered source cannot become a component input by a flag."""
    expected = {
        "extract/model.json": definition["model"],
        "data-freeze/dataset.json": {"tables": {"D1": definition["sheets"]}},
        "data-report/data-report.json": {"findings": [], "checks": []},
    }
    before = {}
    for name, value in expected.items():
        path = Path(run_dir) / name
        _require(path.is_file() and not path.is_symlink(), "missing regular source " + name)
        raw = path.read_bytes()
        _require(_same(_json(raw), value), "source differs from declared task: " + name)
        before[name] = raw
    path = Path(run_dir) / "extract/stand-world.json"
    _require(path.is_file() and not path.is_symlink(), "missing declared stand source")
    before["extract/stand-world.json"] = path.read_bytes()
    _require(before["extract/stand-world.json"] == definition["source"],
             "stand source differs from declared task")
    return before


def _family_check(doc):
    from cdpm import family_params
    problems = family_params.family_barrier_problems(doc)
    if problems:
        raise ComponentInputError("; ".join(p["words"] for p in problems),
                                  code=tp.FAMILY_INCOMPLETE)


def build(run_dir: Path, *, catalog, record: str, task_id: str) -> dict[str, bytes]:
    """Build exact native content for a declared task, never an executable ZIP.

    There is deliberately no version override, support override, stand-SMT
    exception, arbitrary world/expected input, or executable-envelope option.
    """
    from cdpm import catalog_bridge, threshold_forms
    from vmp import stand_solver
    _producer(catalog)
    definition = _declared(catalog, record, task_id)
    run_dir = Path(run_dir)
    before = _check_run(run_dir, definition)
    try:
        prepared = tp._prepare_native_model(run_dir, spec_version=SPEC_VERSION)
        _family_check(prepared.doc)
        files, content, _ = tp._serialize_native_content(prepared)
    except (tp.TaskPackageError, threshold_forms.ThresholdFormError,
            *catalog_bridge.CATALOG_ERRORS) as error:
        raise ComponentInputError(str(error), code=getattr(error, "code", "component-input")) from error
    form_problems = content.get("form", {}).get("problems") or []
    _require(not form_problems, "native form validation failed: " + "; ".join(form_problems))
    _require("stand_only" not in content and "stand_exception" not in content["consistency"],
             "stand SMT exceptions are not component proof")
    _require(content.get("catalog") == _stamp(catalog), "native catalogue identity changed")
    stand_solver._require_sections_in_package(files, definition["task"]["world"], definition["rules"])
    _require(all((run_dir / name).read_bytes() == raw for name, raw in before.items()),
             "component source changed during native serialization")
    task = definition["task"]
    files.update({
        "declared-world.json": _canonical(task["world"]),
        "declared-expected.json": _canonical(task["expected"]),
        "world.json": _canonical({"world": task["world"], "expected": task["expected"]}),
        "problem.json": _canonical(definition["problem"]),
        "stand-world.json": definition["source"],
    })
    manifest = {key: value for key, value in content.items()
                if key in (REQUIRED_FIELDS | OPTIONAL_FIELDS) - {"files"}}
    manifest.update(schema=SCHEMA, scope=SCOPE, spec_version=SPEC_VERSION,
        catalog=_stamp(catalog), task={"record": record, "id": task_id,
            "world_sha256": _sha(files["declared-world.json"]),
            "expected_sha256": _sha(files["declared-expected.json"])},
        files=[{"name": name, "sha256": _sha(raw), "bytes": len(raw)}
               for name, raw in sorted(files.items())])
    files[MANIFEST] = _canonical(manifest)
    _validate(files, catalog=catalog)
    return files


def export_zip(run_dir: Path, *, catalog, record: str, task_id: str) -> bytes:
    return tp.write_zip(build(run_dir, catalog=catalog, record=record, task_id=task_id))


def _member_name(name: str) -> bool:
    return (name in REQUIRED_MEMBERS or name in OPTIONAL_MEMBERS
            or _CLAUSE.fullmatch(name) is not None)


def _native_projections(files: dict[str, bytes], manifest: dict, doc: dict,
                        definition: dict) -> None:
    """Recompute actual native serializers from the exact declared model.

    A new ZIP census is not authority to change the model, clean world or any
    native projection. The oracle is deliberately absent from every call.
    """
    from cdpm import export_clean_data, export_solver, history_section, logic_proof, threshold_forms, vmp_form

    doc_x = tp.excluded_rules_slice(doc)
    wanted = {"objective.json": export_solver.build_objective_json(
        doc_x, spec_version=SPEC_VERSION).encode("utf-8")}
    forms = threshold_forms.forms_bytes(doc_x, SPEC_VERSION)
    if forms is not None:
        wanted["threshold-forms.json"] = forms
    form = vmp_form.to_form(doc)
    _require(not vmp_form.canonical_diff(doc, vmp_form.from_form(form))
             and not vmp_form.validate_form(form, strict=True),
             "declared native form is not a lossless valid projection")
    wanted["vmp-form.json"] = (json.dumps(form, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    examples, unproven = {}, []
    for rule in doc_x.get("rules", []):
        if not isinstance(rule.get("logic"), dict) or rule.get("blockers"):
            continue
        pair, why = logic_proof.generate_pair(rule)
        if pair is None:
            unproven.append({"rule": rule.get("id"), "reason": why})
        else:
            _require(not logic_proof.check_pair(pair, rule.get("id"), rule=rule),
                     "declared logic pair is invalid")
            examples[rule.get("id")] = pair
    if examples:
        wanted["logic-examples.json"] = json.dumps(examples, ensure_ascii=False, indent=1).encode("utf-8")
    wanted["vmp-form.md"] = (vmp_form.render_form_md(form)
        + "\n## Доказанность логики\n\n"
        + logic_proof.render_unproven_summary(unproven) + "\n").encode("utf-8")
    wanted["clean-data.json"] = export_clean_data.export_json(
        {"tables": {"D1": definition["sheets"]}},
        {"findings": [], "checks": []}).encode("utf-8")
    for name in ("objective.json", "threshold-forms.json", "vmp-form.json",
                 "vmp-form.md", "logic-examples.json", "clean-data.json"):
        _require(files.get(name) == wanted.get(name),
                 "native projection differs from declared source: " + name)
    _require(manifest.get("logic_unproven", []) == unproven,
             "native logic evidence declaration differs")
    _require(manifest["has_data"] is True and manifest["excluded_until_data"] == [
        rule.get("id") for rule in doc.get("rules", []) if rule.get("excluded_until_data")],
        "native data/exclusion declaration differs")
    clean_data = _json(wanted["clean-data.json"])
    time = tp.time_declaration_of(doc, clean_data)
    projections = {
        "rule_maps": tp._rule_maps_for(doc),
        "crew_size": tp.crew_size_of(doc),
        "time": time,
        "horizon": history_section.declared_horizon(
            clean_data, tz=(time or {}).get("base_time_zone")),
        "history": history_section.history_section(clean_data, doc),
        "fits_catalog": tp.fits_catalog_of(doc, manifest["catalog"]),
    }
    for name, expected in projections.items():
        _require(_same(manifest.get(name), expected),
                 "native metadata differs from declared source: " + name)
    sections = {"common": {"definitions": len(form["common"]["definitions"]),
                            "world": len(form["common"]["world"])}}
    for section in ("pairing", "rostering"):
        sections[section] = {kind: len(form[section][kind]) for kind in ("hard", "soft", "criteria")}
    _require(isinstance(manifest["form"], dict)
             and set(manifest["form"]) == {"problems", "strict_gate", "sections"}
             and type(manifest["form"]["strict_gate"]) is bool
             and manifest["form"]["problems"] == []
             and _same(manifest["form"]["sections"], sections),
             "native form declaration differs from projection")


def _validate(files: dict[str, bytes], *, catalog) -> dict:
    _require("manifest.json" not in files and MANIFEST in files,
             "not an exclusively component-only envelope")
    manifest = _json(files[MANIFEST])
    _require(isinstance(manifest, dict) and REQUIRED_FIELDS <= set(manifest)
             and set(manifest) <= REQUIRED_FIELDS | OPTIONAL_FIELDS,
             "missing, extra or admission envelope fields")
    _require(manifest["schema"] == SCHEMA and manifest["scope"] == SCOPE
             and manifest["spec_version"] == SPEC_VERSION, "wrong schema/scope/specification")
    _require(manifest["catalog"] == _stamp(catalog), "wrong catalogue identity")
    task = manifest["task"]
    _require(isinstance(task, dict) and set(task) == {
        "record", "id", "world_sha256", "expected_sha256"}, "invalid task identity fields")
    definition = _declared(catalog, task["record"], task["id"])
    payloads = set(files) - {MANIFEST}
    _require(REQUIRED_MEMBERS <= payloads and all(_member_name(name) for name in payloads),
             "missing or unknown native payload")
    entries = manifest["files"]
    _require(isinstance(entries, list) and len(entries) == len(payloads), "invalid payload census")
    names = []
    for entry in entries:
        _require(isinstance(entry, dict) and set(entry) == {"name", "sha256", "bytes"},
                 "invalid payload identity fields")
        name, digest, size = entry["name"], entry["sha256"], entry["bytes"]
        _require(isinstance(name, str) and name in payloads and name not in names,
                 "duplicate, missing or foreign payload identity")
        _require(isinstance(digest, str) and _HASH.fullmatch(digest) is not None
                 and type(size) is int and size >= 0, "invalid payload hash or size")
        _require(_sha(files[name]) == digest and len(files[name]) == size,
                 "payload bytes differ: " + name)
        names.append(name)
    _require(names == sorted(payloads), "incomplete or unordered payload census")
    declared = definition["task"]
    for field, member in (("world", "declared-world.json"),
                          ("expected", "declared-expected.json")):
        _require(files[member] == _canonical(declared[field])
                 and task[field + "_sha256"] == _sha(files[member]),
                 "world/oracle differs from declared task")
    _require(_same(_json(files["world.json"]),
                   {"world": declared["world"], "expected": declared["expected"]}),
             "world wrapper differs from declared task")
    _require(files["stand-world.json"] == definition["source"], "source provenance differs")
    _require(_same(_json(files["problem.json"]), definition["problem"]),
             "problem differs from declared world")
    doc = _json(files["model.json"])
    _require(isinstance(doc, dict) and doc.get("sources") == definition["model"]["sources"],
             "native model source provenance differs")
    _, declared_native = tp._normalize_native_model(_canonical(definition["model"]))
    _require(_same(doc, declared_native),
             "native model differs from normalized declared source")
    tp.refuse_diverging_carriers(doc)
    tp.refuse_invalid_logic_units(doc)
    tp.refuse_answer_provenance(doc)
    tp.refuse_nameless_hard_rules(doc)
    tp._load_modern_package_model(files["model.json"])
    _family_check(doc)
    # Full semantic equality is established above; preserve the emitted JSON
    # object's member order for the actual byte-producing serializers.
    _native_projections(files, manifest, doc, definition)
    from cdpm import family_manifest
    from cdpm.gates import gate_task_package
    # The task checker also judges allowed_modes. Components have no modes:
    # compare its actual content projection, then retain the completeness gate.
    _require(_same(family_manifest.project(doc), manifest["families"]),
             "native family projection differs")
    problems = gate_task_package._check_consistency(manifest, {}, files, doc)
    problems += gate_task_package._check_measure(files, manifest)
    _require(not problems, "native consistency evidence invalid: " + "; ".join(problems))
    if "measure-protocol.json" not in files and not any(name.startswith("freeze/") for name in files):
        _require(manifest["coverage"] is None and manifest["measure"] == {
            "available": False, "reason": "нет заморозки — измерять не по чему"},
            "native coverage/measure declaration differs")
    _require(type(manifest["has_data"]) is bool
             and isinstance(manifest["excluded_until_data"], list)
             and isinstance(manifest["form"], dict)
             and manifest["form"].get("problems") == [], "invalid native content metadata")
    return {"manifest": manifest, "files": files}


def read_bytes(raw: bytes, *, catalog) -> dict:
    """Fail-closed component reader; does not call a task-package loader."""
    _producer(catalog)
    files = {}
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                _require(not info.is_dir() and info.filename not in files,
                         "directory or duplicate ZIP member")
                _require(info.filename == MANIFEST or _member_name(info.filename),
                         "unsafe, mixed or unknown ZIP member")
                _require(not stat.S_ISLNK(info.external_attr >> 16), "ZIP symlink is forbidden")
                files[info.filename] = archive.read(info)
    except (zipfile.BadZipFile, OSError, RuntimeError) as error:
        raise ComponentInputError("unreadable component archive: " + str(error)) from error
    return _validate(files, catalog=catalog)
