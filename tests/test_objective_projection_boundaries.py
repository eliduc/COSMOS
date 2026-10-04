"""A08: semantic rejection must occur at both real package reading boundaries."""
import copy
import hashlib
import json

import pytest

from cdpm import catalog_bridge as cb, export_task_package as tp, model, smt_verify
from cdpm.gates import gate_task_package as producer
from template_catalog import family_contract
from vmp import catalog_access as ca, objective_projection as op
from webapp import task_package as consumer
from test_a06_model_catalogue import use_real_catalogue
from test_cdpm_task_package import _ready_doc, _run_dir
from test_k06_scope_agent import native_support, publish


@pytest.fixture(autouse=True)
def clear_catalogue_cache():
    yield
    cb.forget()
    ca.forget()


def modern_soft_rule(cat):
    """Keep the weight/source target, without an unrelated hard SMT task."""
    rule = copy.deepcopy(_ready_doc()["rules"][1])
    rule["weight"] = 10
    rule["template_fit"] = {
        "category": "A", "schema_version": "landing/2",
        "family": "layover_penalty", "registry_rev": cat.fingerprint(),
        "family_contract": family_contract.snapshot(cat, "layover_penalty", {}),
        "params": {}, "quote": rule["statement"],
    }
    return rule


def build_projection_files(tmp_path, monkeypatch, doc, cat, spec):
    """Real modern producer; synthetic support is not execution approval.

    A06/A08 judge projection data, not an unrelated hard-rule translation.
    The real verifier sees no hard rules; no verdict, producer or gate is
    substituted. A future spec is selected for this package only.
    """
    assert model.validate_model(doc) == []
    assert all(rule["kind"] == "SOFT" for rule in doc["rules"])
    records = {("criteria", "min_person_days")}
    records.update(("families", rule["template_fit"]["family"])
                   for rule in doc["rules"])
    support = native_support(tmp_path, monkeypatch, cat, records)
    publish(cat, doc, support, records)
    run = _run_dir(tmp_path, doc)
    raw = (run / "extract" / "model.json").read_bytes()

    def forbidden(*_args, **_kwargs):
        pytest.fail("Projection fixture must not call a translator or solver")

    monkeypatch.setattr(tp, "smt_channel_factory", forbidden)
    monkeypatch.setattr(smt_verify, "_run_z3", forbidden)
    report = smt_verify.verify(doc, forbidden,
                              model_sha256=smt_verify.model_fingerprint(raw))
    assert report["n_rules"] == report["n_translated"] == 0
    smt_verify.write_report(run, report)
    files = tp.build_package(run, spec_version=spec)
    actual = json.loads(files["model.json"])
    manifest = json.loads(files["manifest.json"])
    assert manifest["spec_version"] == spec
    assert manifest["consistency"]["verdict"] == "none"
    assert manifest["consistency"]["n_rules"] == 0
    assert all(rule["template_fit"]["schema_version"] == "landing/2"
               for rule in actual["rules"])
    assert (run / "extract" / "model.json").read_bytes() == raw
    return files


def package(tmp_path, monkeypatch, *, mutation=None, spec="1.20"):
    # Real validated readers of a temporary modern catalogue, not a release.
    cat = use_real_catalogue(tmp_path, monkeypatch)
    doc = _ready_doc()
    doc["rules"] = [modern_soft_rule(cat)]
    for question in doc["questions"]:
        question["rule_refs"] = [doc["rules"][0]["id"]]
    doc["objectives"][0]["criterion"] = {
        "kind": "min_person_days", "rate_per_duty": 100,
        "version": "v1", "effective_from": "2030-01-01",
        "as_written": "цена рабочего дня (смены) — 100 условных единиц"}
    files = build_projection_files(tmp_path, monkeypatch, doc, cat, spec)
    actual = json.loads(files["model.json"])
    projected = json.loads(files["objective.json"])
    assert op.compare(projected, actual, cat) == []
    assert projected["pairing"]["components"][0]["weight"] == 10
    assert projected["pairing"]["criterion"]["rate_per_duty"] == 100
    if mutation == "weight":
        projected["pairing"]["components"][0]["weight"] = 999
    elif mutation == "rate":
        projected["pairing"]["criterion"]["rate_per_duty"] = 999
    elif mutation == "direction":
        projected["pairing"]["direction"] = "maximize"
    elif mutation == "empty":
        projected = {}
    elif mutation == "duplicate":
        rows = projected["pairing"]["components"]
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == "version":
        del projected["pairing"]["criterion"]["rates_version"]
    files["objective.json"] = op.canonical_bytes(projected)
    manifest = json.loads(files["manifest.json"])
    for entry in manifest["files"]:
        if entry["name"] == "objective.json":
            entry["sha256"] = hashlib.sha256(files["objective.json"]).hexdigest()
    files["manifest.json"] = json.dumps(manifest).encode("utf-8")
    path = tmp_path / "projection.zip"
    path.write_bytes(tp.write_zip(files))
    return path


@pytest.mark.parametrize("boundary", ["producer", "consumer"])
@pytest.mark.parametrize("mutation", ["weight", "rate", "direction", "empty", "duplicate", "version"])
def test_rehashed_projection_corruption_rejected_at_each_boundary(tmp_path, monkeypatch, boundary, mutation):
    path = package(tmp_path, monkeypatch, mutation=mutation)
    if boundary == "producer":
        problems, _ = producer.check(path)
        assert any("objective-mismatch" in p or "criterion-direction" in p
                   for p in problems), problems
    else:
        with pytest.raises(consumer.TaskPackageRefused) as exc:
            consumer.load(path, run_gate=False)
        assert exc.value.code in {"objective-mismatch", "criterion-direction"}


def test_healthy_projection_passes_both_boundaries(tmp_path, monkeypatch):
    path = package(tmp_path, monkeypatch)
    assert producer.check(path)[0] == []
    assert consumer.load(path, run_gate=False)["objective"]["pairing"]["criterion"]["rate_per_duty"] == 100


def test_legacy_spec_is_explicitly_unjudged_at_consumer(tmp_path, monkeypatch):
    path = package(tmp_path, monkeypatch, spec="1.18")
    got = consumer.load(path, run_gate=False)
    assert got["objective_projection"]["judged"] is False
    assert "архив" in got["objective_projection"]["words"]
