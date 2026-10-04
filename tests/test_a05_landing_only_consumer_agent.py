"""A05 landing-only input through real modern package gates and threshold judge.

The normal producer derives legacy params; that healthy control is retained.
Only that redundant projection is removed from a second, correctly rehashed
ZIP. Both ZIPs must pass the actual producer gate and receiver before the
public threshold comparison runs. No gate/reader/verdict is replaced.

The one literal HARD720 translation is an explicit deterministic input to
real in-process Z3, not evidence about an LLM's semantic translation. Native
support and catalogue helpers use temporary synthetic evidence, not executor
approval. No HTTP, service, JVM, operational solver or customer data is used.
"""
import copy
import hashlib
import json

import pytest

from cdpm import export_task_package as producer, model as m, smt_verify
from cdpm.gates import gate_task_package as gate
from test_a05_readiness_carrier_agent import _case, isolated_consumer  # noqa: F401
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from test_k06_integral_measure_path import source_freeze
from test_task_package import _pkg_A7D
from webapp import task_package as receiver, threshold_gate


pytestmark = pytest.mark.usefixtures("declared_test_layout")


def _landing_only_package(tmp_path, monkeypatch, spec):
    _manifest, doc, _clean, _registry = _case(tmp_path, monkeypatch)
    monkeypatch.setattr(producer, "SPEC_VERSION", spec)
    rule, = doc["rules"]
    rid = rule["id"]
    assert rule["kind"] == "HARD" and rule["zone"] == "PAIRING"
    assert not rule.get("params")
    assert rule["template_fit"]["schema_version"] == "landing/2"
    assert rule["template_fit"]["params"]["max_duty_duration"]["value"] == 720
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []

    run = tmp_path / "run-landing-only"
    model_path = run / "extract" / "model.json"
    model_path.parent.mkdir(parents=True)
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    model_path.write_bytes(raw)
    dataset = _pkg_A7D()["clean_data"]
    for folder, filename, value in (
            ("data-freeze", "dataset.json", dataset),
            ("data-report", "data-report.json", {"findings": [], "checks": []})):
        target = run / folder / filename
        target.parent.mkdir()
        target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    freeze = source_freeze(run, doc)
    frozen_bytes = {p: p.read_bytes() for p in freeze.rglob("*.json")}

    def exact_translation(prompt):
        assert f"{rid} | Рабочая смена не более 720 минут." in prompt
        return json.dumps({
            "decls": ["(declare-const duty_minutes Int)"],
            "asserts": [{"rule": rid, "smt": "(<= duty_minutes 720)"}]})

    def forbidden_channel(*_args, **_kwargs):
        pytest.fail("Unexpected retranslation: no external model is permitted")

    monkeypatch.setattr(producer, "smt_channel_factory", forbidden_channel)
    report = smt_verify.verify(
        doc, exact_translation, model_sha256=smt_verify.model_fingerprint(raw))
    assert report["verdict"] == "sat"
    assert report["n_rules"] == report["n_translated"] == 1
    assert report["translations"][0]["rule"] == rid
    smt_verify.write_report(run, report)

    files = producer.build_package(run)
    original_files = copy.deepcopy(files)
    normal_path = tmp_path / "normal-producer.zip"
    normal_bytes = producer.write_zip(files)
    normal_path.write_bytes(normal_bytes)
    problems, _ = gate.check(normal_path)
    assert problems == [], {"stage": "normal producer gate", "problems": problems}
    normal = receiver.load(normal_path, run_gate=False)
    emitted = normal["model"]
    emitted_rule, = emitted["rules"]
    projection, = emitted_rule["params"]
    assert (projection["name"], projection["value"], projection["unit"],
            projection["relation"]) == (
                "max_duty_duration", 720, "duration_minutes", "max")
    assert normal["manifest"]["measure"]["available"] is True
    assert normal["manifest"]["measure"]["ok"] is True
    assert normal["manifest"]["coverage"]["values_without_addressee"] == 0
    assert normal["manifest"]["coverage"]["normative_without_rows"] == 0
    subject = smt_verify.subject_fingerprint(emitted)
    assert subject == report["subject_sha256"]

    # P0 permits landing/2 without legacy params. Preserve every other
    # model field and every other ZIP member, including the genuine report.
    landing_only = copy.deepcopy(emitted)
    removed = landing_only["rules"][0].pop("params")
    restored = copy.deepcopy(landing_only)
    restored["rules"][0]["params"] = removed
    assert restored == emitted
    assert m.validate_schema(landing_only) == m.validate_model(landing_only) == []
    assert smt_verify.subject_fingerprint(landing_only) == subject
    files["model.json"] = json.dumps(landing_only, ensure_ascii=False).encode("utf-8")
    manifest = json.loads(files["manifest.json"])
    assert manifest["spec_version"] == spec
    assert manifest["consistency"]["subject_sha256"] == subject
    assert "feasibility-hard" in manifest["allowed_modes"]["pairing"]
    member, = [entry for entry in manifest["files"] if entry["name"] == "model.json"]
    member.update(sha256=hashlib.sha256(files["model.json"]).hexdigest(),
                  bytes=len(files["model.json"]))
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    assert all(body == original_files[name] for name, body in files.items()
               if name not in {"model.json", "manifest.json"})
    path = tmp_path / "landing-only.zip"
    body = producer.write_zip(files)
    path.write_bytes(body)
    problems, counters = gate.check(path)
    assert problems == [], {"stage": "landing-only actual gate", "problems": problems}
    assert counters["landing_archive_only"] is False
    # Only the duplicate subprocess gate launcher is skipped; the same
    # bytes were just checked above, and all receiver checks remain real.
    pkg = receiver.load(path, run_gate=False)
    assert pkg["model"] == landing_only
    assert "params" not in pkg["model"]["rules"][0]
    assert pkg["landing_projection"]["archive_only"] is False
    assert pkg["objective_projection"]["judged"] is True
    assert model_path.read_bytes() == raw
    assert {p: p.read_bytes() for p in freeze.rglob("*.json")} == frozen_bytes
    assert normal_path.read_bytes() == normal_bytes and path.read_bytes() == body
    return pkg, path, body, rid


@pytest.mark.parametrize("spec", ["1.20", "1.21"])
@pytest.mark.parametrize("applied, expected", [(720, "совпадает"), (780, "расходится")])
def test_real_gates_allow_landing_only_but_threshold_judge_must_not_skip_it(
        tmp_path, monkeypatch, spec, applied, expected):
    pkg, path, body, rid = _landing_only_package(tmp_path, monkeypatch, spec)
    before = copy.deepcopy(pkg)
    # Claimed and actually read values agree: the model-to-application
    # comparison, not the separate report-to-provider check, must detect780.
    actual = threshold_gate.compare(
        pkg, {"max_duty_span_min": applied}, profile="explicit synthetic reading",
        mapping={"max_duty_duration": "max_duty_span_min"},
        applied=[{"threshold": "max_duty_span_min", "value": applied,
                  "fromParams": ["max_duty_duration"], "rules": [rid]}])
    assert pkg == before and path.read_bytes() == body
    wanted = {"verdict": expected, "params": ["max_duty_duration"],
              "reading": "max_duty_span_min", "solver_value": applied,
              "package_values": [720], "rules": [rid]}
    evidence = {
        "spec": spec, "normal_and_landing_only_gates": "passed",
        "receiver_kept_landing_only_model": True,
        "actual": actual, "expected_single_comparison": wanted}
    print(json.dumps(evidence, ensure_ascii=False))
    assert len(actual) == 1, evidence
    assert {key: actual[0][key] for key in wanted} == wanted
