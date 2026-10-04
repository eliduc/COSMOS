"""Offline actual native serialization, not Kotlin/solver/support acceptance.

The only positive SMT translator is the previously closed calendar grammar
with real local Z3. Protocol mutations below never manufacture a verdict.
"""
import contextlib
import copy
import hashlib
import io
import json
import socket
import subprocess
import zipfile

import pytest

from cdpm import component_input as ci, export_task_package as tp, smt_verify as sv
from cdpm.gates import gate_task_package
from webapp import task_package
from test_a06_component_inputs_gpt import component_catalog, calendar_local_report, translate_window_subject
from test_a06_native_stand_proof_agent import local_report, translate_duty_statement


RECORD = "families/calendar_windows"
TASK = "st-window-1"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("component-input tests forbid network and child processes")
    for name in ("connect", "connect_ex", "bind", "listen", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)


@pytest.fixture
def prepared(component_catalog, tmp_path):
    cat = component_catalog
    run = ci.prepare_run(tmp_path / "declared-source", catalog=cat, record=RECORD, task_id=TASK)
    raw = (run / "extract/model.json").read_bytes()
    report = calendar_local_report(json.loads(raw), raw)
    sv.write_report(run, report)
    return {"cat": cat, "run": run, "report": report, "tmp": tmp_path}


def build(case):
    return ci.build(case["run"], catalog=case["cat"], record=RECORD, task_id=TASK)


def write_archive(case, files, name="component.zip"):
    path = case["tmp"] / name
    path.write_bytes(tp.write_zip(files))
    return path


def change_manifest(files, change):
    result = dict(files)
    manifest = json.loads(result[ci.MANIFEST])
    change(manifest)
    result[ci.MANIFEST] = ci._canonical(manifest)
    return result


def reseal(files):
    return change_manifest(files, lambda doc: doc.update(files=[
        {"name": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        for name, raw in sorted(files.items()) if name != ci.MANIFEST]))


def test_actual_support_refusal_before_and_after_component_export(prepared, record_property):
    case = prepared
    support = (case["cat"].path / "support.json").read_bytes()
    for stage in ("before", "after"):
        with pytest.raises(tp.TaskPackageError) as refused:
            tp.export_zip(case["run"], spec_version="1.21")
        assert refused.value.code == tp.SUPPORT_MISSING
        record_property("normal_export_" + stage, refused.value.code)
        if stage == "before":
            files = build(case)
    assert (case["cat"].path / "support.json").read_bytes() == support
    assert "manifest.json" not in files
    assert all(cell["approved"] is None for cell in json.loads(support)["cells"])
    record_property("actual_smt", case["report"]["verdict"])
    record_property("actual_smt_rules", case["report"]["n_translated"])


def test_native_content_bytes_are_shared_and_declared(prepared, record_property):
    case = prepared
    files = build(case)
    raw = tp.write_zip(files)
    read = ci.read_bytes(raw, catalog=case["cat"])
    manifest = read["manifest"]
    assert manifest["catalog"] == ci._stamp(case["cat"])
    assert manifest["schema"] == "component-input/1"
    assert manifest["scope"] == "component-only"
    assert manifest["spec_version"] == "1.21"
    assert not {"support", "allowed_modes", "readiness", "readiness_matrix", "objectives", "stand_only"} & set(manifest)
    native, metadata, _ = tp._serialize_native_content(
        tp._prepare_native_model(case["run"], spec_version="1.21"))
    assert native and all(files[name] == body for name, body in native.items())
    assert manifest["consistency"] == metadata["consistency"]
    assert json.loads(files["threshold-forms.json"])["calendar_window_penalties"]
    assert json.loads(files["world.json"])["world"] == json.loads(files["declared-world.json"])
    assert ci.export_zip(case["run"], catalog=case["cat"], record=RECORD, task_id=TASK) == raw
    path = write_archive(case, files)
    record_property("component_input", str(path))
    record_property("component_sha256", hashlib.sha256(raw).hexdigest())


@pytest.mark.parametrize("mixed", [False, True, "empty", "invalid"])
def test_customer_and_producer_gate_reject_component_even_without_gate(prepared, mixed):
    files = build(prepared)
    if mixed:
        if mixed == "empty":
            files["manifest.json"] = b"{}"
        elif mixed == "invalid":
            files["manifest.json"] = b"invalid"
        else:
            files["manifest.json"] = files[ci.MANIFEST]
    path = write_archive(prepared, files)
    problems, counters = gate_task_package.check(path)
    assert problems and "component-only" in problems[0]
    assert counters["modes"] == 0
    with pytest.raises(task_package.TaskPackageRefused) as refused:
        task_package.load(path, run_gate=False)
    assert refused.value.code == "component-only"
    if mixed:
        with pytest.raises(ci.ComponentInputError):
            ci.read_bytes(path.read_bytes(), catalog=prepared["cat"])


@pytest.mark.parametrize("marker", ["members", "schema", "scope"])
def test_renaming_and_fake_modes_cannot_make_component_executable(prepared, marker):
    files = build(prepared)
    manifest = json.loads(files.pop(ci.MANIFEST))
    manifest["allowed_modes"] = {"pairing": ["feasibility-hard"], "rostering": ["optimization"]}
    if marker != "members":
        files.pop("declared-world.json")
        files.pop("declared-expected.json")
    if marker != "schema":
        manifest.pop("schema")
    if marker != "scope":
        manifest.pop("scope")
    files["manifest.json"] = ci._canonical(manifest)
    path = write_archive(prepared, files)
    assert "component-only" in gate_task_package.check(path)[0][0]
    with pytest.raises(task_package.TaskPackageRefused) as refused:
        task_package.load(path, run_gate=False)
    assert refused.value.code == "component-only"


@pytest.mark.parametrize("field,value", [
    ("schema", "component-input/2"), ("scope", "stand-only"), ("spec_version", "1.18"),
    ("allowed_modes", {"rostering": ["optimization"]}), ("support", {"approved": True}),
    ("readiness", "a"), ("readiness_matrix", {}), ("objectives", {}), ("stand_only", True),
])
def test_fake_or_admission_fields_refuse(prepared, field, value):
    files = change_manifest(build(prepared), lambda doc: doc.update({field: value}))
    with pytest.raises(ci.ComponentInputError):
        ci.read_bytes(tp.write_zip(files), catalog=prepared["cat"])


@pytest.mark.parametrize("field", ["version", "fingerprint"])
def test_catalogue_cannot_be_relabelled(prepared, field):
    files = change_manifest(build(prepared), lambda doc: doc["catalog"].update({field: "other"}))
    with pytest.raises(ci.ComponentInputError, match="catalogue identity"):
        ci.read_bytes(tp.write_zip(files), catalog=prepared["cat"])


@pytest.mark.parametrize("field,value", [
    ("record", "families/duty_limits"), ("id", "st-duty-1"),
    ("world_sha256", "0" * 64), ("expected_sha256", "0" * 64),
])
def test_task_or_oracle_identity_refuses(prepared, field, value):
    files = change_manifest(build(prepared), lambda doc: doc["task"].update({field: value}))
    with pytest.raises(ci.ComponentInputError):
        ci.read_bytes(tp.write_zip(files), catalog=prepared["cat"])


@pytest.mark.parametrize("name", sorted(ci.REQUIRED_MEMBERS))
def test_missing_or_changed_payload_refuses(prepared, name):
    files = build(prepared)
    files[name] += b" "
    with pytest.raises(ci.ComponentInputError, match="payload bytes differ"):
        ci.read_bytes(tp.write_zip(files), catalog=prepared["cat"])
    del files[name]
    with pytest.raises(ci.ComponentInputError, match="missing or unknown"):
        ci.read_bytes(tp.write_zip(files), catalog=prepared["cat"])


@pytest.mark.parametrize("name", ["world.json", "declared-world.json", "declared-expected.json",
                                  "problem.json", "stand-world.json"])
def test_resealed_foreign_declared_inputs_refuse(prepared, name):
    files = build(prepared)
    files[name] = b"{}"
    with pytest.raises(ci.ComponentInputError):
        ci.read_bytes(tp.write_zip(reseal(files)), catalog=prepared["cat"])


@pytest.mark.parametrize("mutation", ["duplicate-entry", "foreign-entry", "wrong-size", "duplicate-json"])
def test_manifest_census_is_strict(prepared, mutation):
    files = build(prepared)
    manifest = json.loads(files[ci.MANIFEST])
    if mutation == "duplicate-entry":
        manifest["files"][1] = copy.deepcopy(manifest["files"][0])
    elif mutation == "foreign-entry":
        manifest["files"][0]["name"] = "../model.json"
    elif mutation == "wrong-size":
        manifest["files"][0]["bytes"] = True
    else:
        files[ci.MANIFEST] = files[ci.MANIFEST][:-1] + b',"scope":"component-only"}'
    if mutation != "duplicate-json":
        files[ci.MANIFEST] = ci._canonical(manifest)
    with pytest.raises(ci.ComponentInputError):
        ci.read_bytes(tp.write_zip(files), catalog=prepared["cat"])


@pytest.mark.parametrize("name", [ci.MANIFEST, "model.json", "../model.json", "/model.json", "manifest.json"])
def test_duplicate_unsafe_and_mixed_zip_members_refuse(prepared, name):
    files = build(prepared)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        for member, body in files.items():
            archive.writestr(member, body)
        with pytest.warns(UserWarning) if name in files else contextlib.nullcontext():
            archive.writestr(name, b"{}")
    with pytest.raises(ci.ComponentInputError):
        ci.read_bytes(raw.getvalue(), catalog=prepared["cat"])


@pytest.mark.parametrize("name", ["extract/model.json", "extract/stand-world.json",
                                  "data-freeze/dataset.json", "data-report/data-report.json"])
def test_source_or_provenance_mutation_refuses_before_native_serialization(prepared, name, monkeypatch):
    path = prepared["run"] / name
    doc = json.loads(path.read_bytes())
    doc["injected"] = "not the declared task"
    path.write_bytes(ci._canonical(doc))
    monkeypatch.setattr(tp, "_serialize_native_content", lambda *a, **k: pytest.fail("foreign input serialized"))
    with pytest.raises(ci.ComponentInputError, match="(source|stand source) differs"):
        build(prepared)


def test_resealed_model_provenance_and_smt_mutations_refuse(prepared):
    files = build(prepared)
    doc = json.loads(files["model.json"])
    doc["sources"][0]["sha256"] = "0" * 64
    files["model.json"] = ci._canonical(doc)
    with pytest.raises(ci.ComponentInputError, match="source provenance"):
        ci.read_bytes(tp.write_zip(reseal(files)), catalog=prepared["cat"])
    files = build(prepared)
    report = json.loads(files["smt-report.json"])
    report["verdict"] = "unsat"
    files["smt-report.json"] = ci._canonical(report)
    with pytest.raises(ci.ComponentInputError, match="consistency"):
        ci.read_bytes(tp.write_zip(reseal(files)), catalog=prepared["cat"])


@pytest.mark.parametrize("damage", ["soft_weight", "soft_form", "hard_value", "objects",
                                   "objectives", "questions"])
def test_resealed_model_requires_the_entire_normalized_declared_source(prepared, damage):
    files = build(prepared)
    doc = json.loads(files["model.json"])
    if damage == "soft_weight":
        doc["rules"][0]["weight"] = 8
    elif damage == "soft_form":
        doc["rules"][0]["export_solver"]["days"] = ["sunday"]
    elif damage == "hard_value":
        rule = next(row for row in doc["rules"] if row["id"] == "R-STAND-duty_limits")
        rule["template_fit"]["params"]["max_duty_duration"]["value"] = 781
    elif damage == "objects":
        doc["objects"] = [{"id": "injected-world-object"}]
    elif damage == "objectives":
        doc["objectives"] = [{"id": "O-injected"}]
    else:
        doc["questions"][0]["answer"]["goal_decline"] = False
    # These are not a hash mismatch; all ZIP hashes are resealed and the
    # asserted source identity is intact. Full source->native binding refuses.
    assert doc["sources"] == json.loads(files["model.json"])["sources"]
    files["model.json"] = ci._canonical(doc)
    with pytest.raises(ci.ComponentInputError, match="normalized declared source"):
        ci.read_bytes(tp.write_zip(reseal(files)), catalog=prepared["cat"])


@pytest.mark.parametrize("name", ["objective.json", "threshold-forms.json", "vmp-form.json",
                                  "vmp-form.md", "logic-examples.json", "clean-data.json"])
def test_resealed_native_projection_cannot_diverge_from_source(prepared, name):
    files = build(prepared)
    files[name] = b"{}"
    with pytest.raises(ci.ComponentInputError, match="native projection differs"):
        ci.read_bytes(tp.write_zip(reseal(files)), catalog=prepared["cat"])


@pytest.mark.parametrize("name", ["time", "horizon", "history", "crew_size", "rule_maps",
                                  "fits_catalog", "logic_unproven", "form", "measure", "coverage"])
def test_resealed_native_metadata_is_recomputed(prepared, name):
    files = change_manifest(build(prepared), lambda doc: doc.update({name: {"injected": True}}))
    with pytest.raises(ci.ComponentInputError):
        ci.read_bytes(tp.write_zip(files), catalog=prepared["cat"])


def test_oracle_never_reaches_source_normalization(prepared, monkeypatch):
    actual = tp._normalize_native_model
    seen = []

    def source_only(raw):
        value = json.loads(raw)
        assert "expected" not in value
        seen.append(copy.deepcopy(value))
        return actual(raw)

    monkeypatch.setattr(tp, "_normalize_native_model", source_only)
    files = build(prepared)
    ci.read_bytes(tp.write_zip(files), catalog=prepared["cat"])
    assert seen and all(value == seen[0] for value in seen)
    assert json.loads(files["declared-expected.json"])


def test_no_report_cannot_create_smt_pass(component_catalog, tmp_path):
    run = ci.prepare_run(tmp_path / "no-proof", catalog=component_catalog, record=RECORD, task_id=TASK)
    with pytest.raises(ci.ComponentInputError) as refused:
        ci.build(run, catalog=component_catalog, record=RECORD, task_id=TASK)
    assert refused.value.code == tp.SMT_UNAVAILABLE
    assert not (run / ci.MANIFEST).exists()


def test_prepare_never_overwrites_an_existing_source(prepared):
    raw = (prepared["run"] / "extract/model.json").read_bytes()
    with pytest.raises(ci.ComponentInputError, match="already exists"):
        ci.prepare_run(prepared["run"].parent, catalog=prepared["cat"], record=RECORD, task_id=TASK)
    assert (prepared["run"] / "extract/model.json").read_bytes() == raw


def test_ordinary_native_duty_export_remains_accepted(component_catalog, tmp_path):
    run = ci.prepare_run(tmp_path / "ordinary-duty", catalog=component_catalog,
                         record="families/duty_limits", task_id="st-duty-1")
    raw = (run / "extract/model.json").read_bytes()
    sv.write_report(run, local_report(json.loads(raw), raw))
    archive = tmp_path / "ordinary-native.zip"
    archive.write_bytes(tp.export_zip(run, spec_version="1.21"))
    assert gate_task_package.check(archive)[0] == []
    received = task_package.load(archive, run_gate=False)
    assert received["manifest"]["spec_version"] == "1.21"
    with zipfile.ZipFile(archive) as zf:
        files = {name: zf.read(name) for name in zf.namelist()}
    assert ci.MANIFEST not in files
    # A genuinely accepted normal task does not remain executable when mixed
    # with a component envelope; refusal is independent of other metadata.
    files[ci.MANIFEST] = b"{}"
    mixed = tmp_path / "mixed-ordinary.zip"
    mixed.write_bytes(tp.write_zip(files))
    assert "component-only" in gate_task_package.check(mixed)[0][0]
    with pytest.raises(task_package.TaskPackageRefused) as refused:
        task_package.load(mixed, run_gate=False)
    assert refused.value.code == "component-only"


@pytest.mark.parametrize("excluded", [False, True])
def test_ordinary_snapshot_includes_exact_emitted_excluded_family(component_catalog, tmp_path, excluded):
    from cdpm import support_manifest
    from vmp import stand_solver, stand_tasks

    cat = component_catalog
    # An independent ordinary producer test, not a shortened declared stand.
    definition = ci._declared(cat, "families/duty_limits", "st-duty-1")
    world = copy.deepcopy(definition["task"]["world"])
    world["rule_form"] = {"days": ["thursday"]}
    rules = stand_solver._rules_for(world, ["duty_limits", "calendar_windows"], stand_tasks._family_meta(cat))
    calendar = next(rule for rule in rules if rule["template_fit"]["family"] == "calendar_windows")
    if excluded:
        calendar["excluded_until_data"] = {"question": "Q-synthetic-exclusion", "revision": 1}
    doc = stand_solver.model_doc(rules, world=world, catalog=cat)
    source = stand_solver._stand_source(world, rules, None, catalog=cat)
    run = stand_solver._write_run(tmp_path / "ordinary-source", doc, stand_solver._sheets(world), source=source)
    raw = (run / "extract/model.json").read_bytes()
    sv.write_report(run, local_report(json.loads(raw), raw))
    files = tp.build_package(run, spec_version="1.21")
    manifest = json.loads(files["manifest.json"])
    emitted = json.loads(files["model.json"])
    assert manifest["support"] == support_manifest.project(emitted)
    assert any(cell["record_id"] == "calendar_windows" for cell in manifest["support"]["cells"])
    assert ("R-STAND-calendar_windows" in manifest["excluded_until_data"]) == excluded
    archive = tmp_path / "ordinary-exclusion.zip"
    archive.write_bytes(tp.write_zip(files))
    assert gate_task_package.check(archive)[0] == []
    assert task_package.load(archive, run_gate=False)["manifest"]["support"] == manifest["support"]
    # Exclusion changes applicability/native forms, never the support snapshot
    # census. A resealed deletion of its unsupported cells is still refused.
    manifest["support"]["cells"] = [cell for cell in manifest["support"]["cells"]
                                     if cell["record_id"] != "calendar_windows"]
    files["manifest.json"] = ci._canonical(manifest)
    archive.write_bytes(tp.write_zip(files))
    assert any("manifest.support" in words for words in gate_task_package.check(archive)[0])


def test_actual_synthetic_source_contains_each_emitted_quote(prepared):
    raw = (prepared["run"] / "extract/model.json").read_bytes()
    doc = json.loads(raw)
    source = (prepared["run"] / "extract/stand-world.json").read_bytes()
    declared_source = json.loads(source)["стенд"]
    assert declared_source["rules"] == doc["rules"]
    assert doc["sources"][0]["sha256"] == hashlib.sha256(source).hexdigest()
    for rule in doc["rules"]:
        fit = rule["template_fit"]
        assert fit["quote"] in rule["statement"]
        assert all(cell["quote"] in rule["statement"] for cell in fit["params"].values())


@pytest.mark.parametrize("damage", ["prefix", "source_quote", "parameter_quote", "trailing_prose"])
def test_full_finite_statement_grammar_refuses_unknown_or_false_quotes(prepared, damage):
    doc = json.loads((prepared["run"] / "extract/model.json").read_bytes())
    subjects = sv.subject_of(doc)
    target = next(row for row in subjects if row["id"] == "R-STAND-duty_limits")
    if damage == "prefix":
        target["statement"] = "unknown rule " + target["statement"]
    elif damage == "source_quote":
        target["statement"] = target["statement"].replace("правила: стенд", "правила: invented")
    elif damage == "parameter_quote":
        target["statement"] = target["statement"].replace("max_duty_duration = 780 мин", "max_duty_duration = 781 мин")
    else:
        target["statement"] += " unknown additional obligation"
    with pytest.raises(ValueError):
        translate_duty_statement(target["statement"])
    with pytest.raises(ValueError):
        translate_window_subject(subjects)
