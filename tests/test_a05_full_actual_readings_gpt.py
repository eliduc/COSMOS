"""Retained native JVM readings -> real threshold judges, component scope only.

Required A05_FULL_{INPUT_ROOT,JVM_EVIDENCE,JVM_COMMIT,CATALOG_DIRECTORY}.
INPUT_ROOT is the st-window-1 directory; CATALOG_DIRECTORY is its released
TEST catalogue working copy, not a replacement default catalogue. No JVM,
solver, service, HTTP, subprocess or remote call is made by this module.
Mutations below are labelled copies, never claimed as JVM measurements.
"""
import copy
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import zipfile

import pytest
import template_catalog as tc
from cdpm import catalog_bridge as cb
from vmp import catalog_access, execution_manifest as em, pairing_input
from webapp import threshold_gate as tg


ROOT = Path(__file__).resolve().parents[1]
PROBE = "A05ActualReadingsProbe"
PROBE_SOURCE = f"kotlin-solver/src/test/kotlin/org/acme/flighcrewscheduling/solver/{PROBE}.kt"
MEASURED = {
    "min_sit_home_min": 45, "min_sit_outstation_min": 45,
    "max_duty_span_min": 780, "min_rest_home_min": 660,
    "min_rest_outstation_min": 700, "base_tz_offset_min": 0,
}
EXTERNAL = "max_pairing_days"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _census(root, directory, accept=lambda _p: True):
    assert directory.is_dir(), directory
    return {p.relative_to(root).as_posix(): _sha(p)
            for p in sorted(directory.rglob("*")) if p.is_file() and accept(p)}


def _current_maps():
    sources = _census(ROOT, ROOT / "kotlin-solver/src/main/kotlin", lambda p: p.suffix == ".kt")
    sources.update({name: _sha(ROOT / name) for name in ("kotlin-solver/pom.xml", PROBE_SOURCE)})
    artifacts = _census(ROOT, ROOT / "kotlin-solver/target/classes")
    probe = _census(ROOT, ROOT / "kotlin-solver/target/test-classes/org/acme/flighcrewscheduling/solver",
                    lambda p: p.name.startswith(PROBE) and p.suffix == ".class")
    assert sources and artifacts and probe
    return {"sources": sources, "artifacts": artifacts, "probe": probe}


def _validate_envelope(document, input_dir, commit):
    assert document["schema"] == "a05-actual-readings/1"
    assert document["scope"] == "component/admission-prefix-only"
    assert re.fullmatch(r"[0-9a-f]{40}", commit)
    assert document["producer"]["commit"] == commit
    assert document["producer"]["class"] == "org.acme.flighcrewscheduling.solver." + PROBE
    for name, expected in _current_maps().items():
        assert document[name] == expected, f"Incomplete or stale {name} census"
    assert document["guards"] == {
        "inputs_unchanged": True, "sources_unchanged": True,
        "artifacts_unchanged": True, "probe_unchanged": True, "run_window_restored": True}
    names = {"identity.json", "st-window-1.zip", "st-window-1.world.json", "st-window-1.problem.json"}
    assert document["inputs"]["task"] == "st-window-1"
    assert document["inputs"]["files"] == {name: _sha(input_dir / name) for name in names}
    identity = json.loads((input_dir / "identity.json").read_bytes())
    assert identity["task"] == "st-window-1"
    assert identity["files"] == {name: _sha(input_dir / name) for name in names - {"identity.json"}}
    with zipfile.ZipFile(input_dir / "st-window-1.zip") as archive:
        assert len(archive.namelist()) == len(set(archive.namelist()))
        members = {name: archive.read(name) for name in archive.namelist()}
    assert document["inputs"]["zip_members"] == {
        name: hashlib.sha256(raw).hexdigest() for name, raw in members.items()}
    manifest = json.loads(members["manifest.json"])
    for item in manifest["files"]:
        assert item["sha256"] == hashlib.sha256(members[item["name"]]).hexdigest()
    assert manifest["spec_version"] == "1.20"
    assert manifest["allowed_modes"] == {"pairing": ["feasibility-hard"], "rostering": []}
    assert document["catalog"] == manifest["catalog"]
    assert identity["catalog_version"] == manifest["catalog"]["version"]
    assert identity["catalog_fingerprint"] == manifest["catalog"]["fingerprint"]
    observed = document["observations"]
    assert observed["mode"] == "feasibility-hard" and observed["task"] == "pairing"
    assert observed["allowed_modes"] == manifest["allowed_modes"]
    snapshot = observed["thresholds"]
    assert observed["paramToThreshold"] == snapshot["paramToThreshold"]
    assert observed["qualifiedParamToThreshold"] == snapshot["qualifiedParamToThreshold"]
    assert {a["threshold"]: a["value"] for a in snapshot["applied"]} == {**MEASURED, EXTERNAL: 7}
    assert len(snapshot["applied"]) == len(MEASURED) + 1
    assert {a["threshold"]: a["executor"] for a in snapshot["applied"]} == {
        **dict.fromkeys(MEASURED, "kotlin-provider"), EXTERNAL: "pairing-builder"}
    extended = {a["threshold"]: a for a in observed["applied"]}
    assert set(extended) == set(MEASURED) | {EXTERNAL}
    for entry in snapshot["applied"]:
        extra = extended[entry["threshold"]]
        for field in ("value", "fromParams", "rules", "literal", "declarations", "codes"):
            assert extra[field] == entry[field]
        assert extra["units"] == sorted({p["unit"] for p in extra["parameters"]})
        assert extra["provider_reading_present"] == (entry["threshold"] in observed["profileReadings"])
        assert extra["provider_value"] == observed["profileReadings"].get(entry["threshold"])
    for name, value in MEASURED.items():
        assert observed["profileReadings"][name] == value
    assert EXTERNAL not in observed["profileReadings"]
    assert observed["missing_provider_readings"] == [EXTERNAL]
    assert observed["mismatches"] == []
    assert observed["profileReadings"]["max_landings_per_duty"] == 0
    assert all(p["name"] != "max_landings_per_duty" for p in observed["hard_params"])
    return {"model": json.loads(members["model.json"]), "manifest": manifest}, members


@pytest.fixture
def actual(monkeypatch):
    folder = Path(os.environ["A05_FULL_INPUT_ROOT"]).resolve()
    evidence = Path(os.environ["A05_FULL_JVM_EVIDENCE"]).resolve()
    commit = os.environ["A05_FULL_JVM_COMMIT"]
    catalog = Path(os.environ["A05_FULL_CATALOG_DIRECTORY"]).resolve()
    assert catalog.is_dir()
    monkeypatch.setenv(cb.ENV_CATALOG, str(catalog))
    cb.forget()
    catalog_access.forget()
    protected = {evidence, *[p for p in folder.rglob("*") if p.is_file()],
                 *[p for p in catalog.rglob("*") if p.is_file()]}
    before = {str(p): _sha(p) for p in protected}
    before_input_tree = _census(folder, folder)
    before_catalog_tree = _census(catalog, catalog)
    maps = _current_maps()
    try:
        document = json.loads(evidence.read_bytes())
        pkg, members = _validate_envelope(document, folder, commit)
        cat = tc.load(catalog)
        assert cat.fingerprint() == document["catalog"]["fingerprint"]
        assert cat.version() == document["catalog"]["version"]
        yield document, pkg, members, before
    finally:
        assert {str(p): _sha(p) for p in protected} == before
        assert _census(folder, folder) == before_input_tree
        assert _census(catalog, catalog) == before_catalog_tree
        assert _current_maps() == maps
        cb.forget()
        catalog_access.forget()


def _judges(pkg, observed):
    snapshot = observed["thresholds"]
    rows = tg.compare(pkg, observed["profileReadings"],
        profile="actual JVM component (mutated copies labelled by test)",
        mapping=snapshot["paramToThreshold"], qualified=snapshot["qualifiedParamToThreshold"],
        ignored=snapshot["ignored"], applied=snapshot["applied"],
        off_literals=snapshot["offLiterals"])
    applied = {entry["threshold"]: entry["value"] for entry in snapshot["applied"]}
    # Keep each task's real native zone. Do not relabel rostering rules to make
    # the pairing execution judge appear to cover them.
    gaps = {task: em.thresholds_gaps(pkg["model"]["rules"], applied, task=task)
            for task in ("pairing", "rostering")}
    return rows, gaps


def _external_is_still_unmeasured(rows):
    external = [row for row in rows if row["reading"] == EXTERNAL]
    assert {r["verdict"] for r in external} == {tg.INCOMPARABLE}, external
    assert all(r["solver_value"] is None for r in external)
    # Transit applied7 stays visible and unjudged, never a fabricated provider reading.
    assert all("pairing-builder" in r["note"] for r in external)


def _write(tmp_path, label, rows, gaps, observed, hashes, **extra):
    (tmp_path / f"{label}.json").write_text(json.dumps({
        "scope": "component/admission-prefix-only", "case": label,
        "immutable_primary_sha256": hashes, "threshold_verdicts": rows,
        "execution_gaps_by_task": gaps, "tested_observations": observed,
        "limits": ["max_pairing_days remains Builder-only, not a Kotlin provider measurement",
                   "execution_manifest does not cover native ROSTERING rest_floors in its pairing-only channel map",
                   "execution_manifest base_tz_offset checks presence, not typed value semantics"],
        **extra}, ensure_ascii=False, indent=2), encoding="utf-8")


def test_full_actual_measured_thresholds_reach_real_judges(actual, tmp_path):
    document, pkg, _members, hashes = actual
    before = copy.deepcopy((document, pkg))
    observed = document["observations"]
    rows, gaps = _judges(pkg, observed)
    _external_is_still_unmeasured(rows)
    assert tg.unjudged_claimed_params(rows, observed["thresholds"]["applied"]) == {EXTERNAL}
    measured = [r for r in rows if r["reading"] in MEASURED]
    assert {r["reading"] for r in measured} == set(MEASURED)
    assert all(r["verdict"] == tg.MATCH for r in measured), measured
    assert gaps == {"pairing": [], "rostering": []}
    _write(tmp_path, "actual-measured-healthy", rows, gaps, observed, hashes)
    assert (document, pkg) == before


@pytest.mark.parametrize("threshold", MEASURED)
@pytest.mark.parametrize("mutation", ["provider_only", "applied_only", "both", "provider_missing"])
def test_each_measured_threshold_has_a_reachable_negative(actual, tmp_path, threshold, mutation):
    document, pkg, _members, hashes = actual
    primary = copy.deepcopy(document)
    observed = copy.deepcopy(document["observations"])
    original = MEASURED[threshold]
    if mutation in {"provider_only", "both"}:
        observed["profileReadings"][threshold] = original + 1
    if mutation == "provider_missing":
        del observed["profileReadings"][threshold]
    if mutation in {"applied_only", "both"}:
        entry, = [a for a in observed["thresholds"]["applied"] if a["threshold"] == threshold]
        entry["value"] = original + 1
    rows, gaps = _judges(pkg, observed)
    _external_is_still_unmeasured(rows)
    affected = [r for r in rows if r["reading"] == threshold]
    assert any(r["verdict"] == tg.DIVERGE for r in affected), affected
    if mutation == "provider_missing":
        assert any(r["verdict"] == tg.INCOMPARABLE and r["solver_value"] is None for r in affected)
    if mutation in {"provider_only", "applied_only", "provider_missing"}:
        assert any("ПРОЧИТАЛ" in r["note"] for r in affected)
    if mutation == "both":
        assert any(r["package_values"] == [original] and r["solver_value"] == original + 1
                   and r["verdict"] == tg.DIVERGE for r in affected)
    # em judges only its real channels: these three measured pairing keys
    # have typed semantics; rest is ROSTERING and offset lacks value_semantics.
    em_typed = threshold in {"min_sit_home_min", "min_sit_outstation_min", "max_duty_span_min"}
    expects_gap = em_typed and mutation in {"applied_only", "both"}
    assert bool(gaps["pairing"]) is expects_gap, gaps
    assert gaps["rostering"] == []
    if expects_gap:
        assert all(g["kind"] == "rule_threshold_mismatch" and "две правды" in g["note"] for g in gaps["pairing"])
    _write(tmp_path, f"copy-{threshold}-{mutation}", rows, gaps, observed, hashes,
           mutation={"threshold": threshold, "channel": mutation, "actual_value": original})
    assert document == primary


def test_builder_only_days_reach_real_contract_and_calendar_boundary(actual, tmp_path):
    import _cos_rules
    from _pairing_builder import Builder

    document, pkg, members, hashes = actual
    observed = document["observations"]
    applied = {a["threshold"]: a["value"] for a in observed["thresholds"]["applied"]}
    forms = json.loads(members["threshold-forms.json"])
    contract = pairing_input.source_rules_contract(forms, applied, manifest_time=pkg["manifest"].get("time"))
    assert contract[EXTERNAL] == 7 and contract["base_tz_offset_min"] == 0
    rules = _cos_rules.from_dict(contract, strict=True)
    assert EXTERNAL in rules.source_keys_read and rules.max_pairing_days == 7
    # Deliberately predicate-only: do not invoke Builder.__init__, generate
    # candidates or pretend this narrow receiver state is an admitted run.
    builder = object.__new__(Builder)
    builder.rules = rules
    start = datetime(2026, 10, 1, 6)
    outcomes = []
    for days in (7, 8):
        end = start + timedelta(days=days - 1, hours=1)
        chain = [{"t0": start, "t1": end, "work0": start, "work1": end}]
        got = builder.pairing_days_ok(chain, [])
        assert rules.pairing_days(start, end) == days
        assert got is (days == 7)
        outcomes.append({"calendar_days": days, "permitted_by_predicate": got})
    rows, gaps = _judges(pkg, observed)
    _external_is_still_unmeasured(rows)
    # Explicit component observation taken from the actual contract reader.
    # Its hash is a content binding, not an attestation of a live run.
    raw = json.dumps(contract, ensure_ascii=False, sort_keys=True).encode("utf-8")
    captured = _cos_rules.load_bytes(raw, where="actual native A05 component contract", strict=True)
    assert captured.max_pairing_days == rules.max_pairing_days
    evidence = {"schema": "pairing-builder-thresholds/1",
        "package_identity": tg.builder_package_identity(pkg),
        "contract_bytes": raw, "contract_sha256": hashlib.sha256(raw).hexdigest(),
        "source_keys_read": list(captured.source_keys_read),
        "readings": {EXTERNAL: {"value": captured.max_pairing_days,
            "unit": "count", "relation": "max", "semantics": "inclusive-local-calendar-days",
            "base_tz_offset_min": captured.base_tz_offset_min}}}
    snapshot = observed["thresholds"]
    joined = tg.compare(pkg, observed["profileReadings"], mapping=snapshot["paramToThreshold"],
        qualified=snapshot["qualifiedParamToThreshold"], ignored=snapshot["ignored"],
        applied=snapshot["applied"], off_literals=snapshot["offLiterals"], builder_readings=evidence)
    assert joined and all(r["verdict"] == tg.MATCH for r in joined), joined
    assert not tg.unjudged_claimed_params(joined, snapshot["applied"])
    _write(tmp_path, "builder-only-calendar-boundary", rows, gaps, observed, hashes,
           builder_component={"contract": contract, "source_keys_read": rules.source_keys_read,
                              "observations": outcomes, "joined_verdicts": joined,
                              "contract_sha256": evidence["contract_sha256"],
                              "scope": "source-rules adapter and real predicate only"})
