"""Actual 0.7 draft, fresh unsigned bindings and the in-process stand ZIP path.

This is an explicit SOFT-only synthetic composition, NOT a complete catalogue
stand task: no HARD rule is removed from an existing task. The real verifier
records zero HARD rules; any translator/Z3 invocation fails. Both real binding
builders run without stand results or approval. No live solver/HTTP/release.
Required external draft bytes are pinned; missing inputs are errors, not skips.
"""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import zipfile

import pytest

import template_catalog as tc
from template_catalog import bindings
from cdpm import bindings_build as cdpm_bindings, catalog_bridge as cb, model as m
from cdpm import export_task_package as producer, smt_verify
from cdpm.gates import gate_task_package as gate
from vmp import bindings_build as cosmos_bindings
from vmp import stand_adapters as sa, stand_solver as ss, stand_tasks as st
from webapp import task_package as receiver


ROOT = Path(__file__).resolve().parents[1]
FAMILY = "duty_extension_penalty"
FINGERPRINT = "f1fff61c6b8ba48dfe936c93d64fe23c60d38f9de027ba393685e1cd6c1ab0ad"
AT = "2026-09-18T04:40:00Z"


@pytest.fixture
def draft_composition(tmp_path, monkeypatch):
    origin = Path(os.environ["STAND_DRAFT_CATALOG"]).resolve(strict=True)
    original = tc.current(origin, validated=True)
    assert original.version() == "0.7.0-draft.1"
    assert original.fingerprint() == FINGERPRINT
    protected = sorted(origin.rglob("*.json")) + [
        ROOT / "catalog" / "catalog.json", ROOT / "cdpm" / "bindings.json",
        ROOT / "vmp" / "bindings.json", ROOT / "vmp" / "bindings_seed.json"]
    inputs = {p: p.read_bytes() for p in protected}
    path = tmp_path / "isolated-draft"
    shutil.copytree(origin, path)
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(path))
    cb.forget()
    try:
        cat = tc.current(path, validated=True)
        commit = os.environ["K07_TEST_COMMIT"]
        documents = {
            "CDPM": cdpm_bindings.build(cat, commit=commit, generated_at=AT,
                                         stand_results=None),
            "COSMOS": cosmos_bindings.build(cat, commit=commit, generated_at=AT,
                stand_results=None, seed=cosmos_bindings.load_seed(ROOT / "vmp" / "bindings_seed.json")),
        }
        assert not [p for p in bindings.check_bindings(cat, *documents.values())
                    if p.severity == "error"]
        paths = {}
        for product, doc in documents.items():
            assert doc["catalog_fingerprint"] == FINGERPRINT
            assert doc["catalog_version"] == "0.7.0-draft.1"
            assert doc["generated_from"] == commit
            assert doc["bindings"] and all(not r["stand_verified"] for r in doc["bindings"])
            destination = tmp_path / (product.lower() + "-bindings.json")
            destination.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            paths[product] = destination
            monkeypatch.setenv(cb.SUPPORT_ENV[product], str(destination))
        assert documents["CDPM"]["product_version"] == producer.SPEC_VERSION == "1.18"
        native = bindings.support_table(cat, paths, stand=None, approved_by=None,
                                        computed_at=AT)
        assert native["cells"] and all(c["approved"] is None for c in native["cells"])
        assert all(c["state"] != "есть" for c in native["cells"])
        support = path / "support.json"
        support.write_text(json.dumps(native, ensure_ascii=False), encoding="utf-8")
        assert bindings.support_of(cat, native)["valid"]
        yield cat, documents, paths, support
    finally:
        cb.forget()
        assert producer.SPEC_VERSION == "1.18"
        for p, raw in inputs.items():
            assert p.read_bytes() == raw, str(p)


def _soft_world():
    # New bounded synthetic world, not a filtered or modified declared task.
    return {"form": "schedule", "params": {
        "duty_extension_cap": 960, "long_duty_threshold": 720},
        "conditions": {"extension_bases": ["ST1"], "extension_consent": "yes"},
        "crews": [{"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T1"]}],
        "flights": [{"id": "F1", "from": "ST1", "to": "ST2",
                     "dep": "2026-10-01T06:00", "arr": "2026-10-01T08:00",
                     "fleet_type": "T1"}],
        "horizon": {"from": "2026-10-01", "to": "2026-10-31"}}


def _prepare(cat, base, monkeypatch):
    world = _soft_world()
    meta = st._family_meta(cat)
    assert st.families_for(world, FAMILY, meta) == [FAMILY]
    rules = [sa._rule_of(world, FAMILY, meta[FAMILY], meta[FAMILY]["params"])]
    assert rules and all(r["kind"] == "SOFT" for r in rules)
    doc = ss.model_doc(rules, world=world, family=FAMILY, catalog=cat)
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    assert m.reconcile_carriers(doc["rules"][0]) == []
    source = ss._stand_source(world, rules, None)
    run = ss._write_run(base, doc, ss._sheets(world), source=source)
    raw = (run / "extract" / "model.json").read_bytes()

    def forbidden(*_args, **_kwargs):
        pytest.fail("SOFT-only composition cannot call translator/Z3/SMT channel")

    monkeypatch.setattr(producer, "smt_channel_factory", forbidden)
    monkeypatch.setattr(smt_verify, "_run_z3", forbidden)
    report = smt_verify.verify(doc, forbidden,
                              model_sha256=smt_verify.model_fingerprint(raw))
    assert report["n_rules"] == report["n_translated"] == 0
    smt_verify.write_report(run, report)
    return world, rules, doc, run, raw


@pytest.mark.parametrize("revision", ["1.20", "1.21"])
def test_actual_draft_stand_zip_preserves_explicit_spec_and_unsigned_support(
        draft_composition, tmp_path, monkeypatch, revision):
    cat, documents, binding_paths, support = draft_composition
    world, rules, doc, run, raw = _prepare(cat, tmp_path / "run", monkeypatch)
    before = copy.deepcopy((world, rules))
    frozen_inputs = {p: p.read_bytes() for p in [*binding_paths.values(), support]}
    body = ss.package_bytes(world, rules, stamp=ss.stamp_of(cat),
                            base=tmp_path / "run", catalog=cat, family=FAMILY,
                            spec_version=revision)
    assert (run / "extract" / "model.json").read_bytes() == raw
    assert (world, rules) == before
    zipped = tmp_path / (revision + ".zip")
    zipped.write_bytes(body)
    problems, counters = gate.check(zipped)
    assert problems == [], problems
    assert counters["spec_version"] == revision
    loaded = receiver.load(zipped, run_gate=False)
    manifest = loaded["manifest"]
    assert manifest["spec_version"] == revision
    assert manifest["catalog"] == ss.stamp_of(cat)
    assert manifest["support"]["catalog"] == ss.stamp_of(cat)
    cells = manifest["support"]["cells"]
    assert cells and {c["product"] for c in cells} == {"CDPM", "COSMOS"}
    assert all(c["approved"] is None and c["state"] != "есть" for c in cells)
    for c in cells:
        assert c["bindings_file"]["product_version"] == documents[c["product"]]["product_version"]
    assert loaded["model"]["rules"][0]["template_fit"] == doc["rules"][0]["template_fit"]
    assert loaded["landing_projection"]["archive_only"] is False
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        objective = json.loads(archive.read("objective.json"))
        assert objective["matrix"] == m.readiness_matrix(loaded["model"], revision)
    source = run / "extract" / "stand-world.json"
    assert loaded["model"]["sources"][0]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receiver.check_support_execution(loaded, task="pairing", mode="feasibility-soft")
    assert error.value.code == "support-missing"
    for p, saved in frozen_inputs.items():
        assert p.read_bytes() == saved


def test_actual_draft_stand_refuses_missing_support_without_legacy_fallback(
        draft_composition, tmp_path, monkeypatch):
    cat, _, _, support = draft_composition
    world, rules, _, _, _ = _prepare(cat, tmp_path / "run", monkeypatch)
    support.unlink()
    cb.forget()
    with pytest.raises(ss.StandSolverError, match="support"):
        ss.package_bytes(world, rules, stamp=ss.stamp_of(cat),
                         base=tmp_path / "run", family=FAMILY, catalog=cat,
                         spec_version="1.20")
    assert not support.exists()
