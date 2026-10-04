"""Typed HARD carrier through real export, package gate, receiver and readiness.

The temporary catalogue/support/bindings and book are explicit unit fixtures,
not released executor approval. One exact synthetic HARD rule is translated
deterministically and checked by real in-process Z3. No consistency, measure,
registry or readiness verdict is replaced. This proves the Python package /
applied seam, not HTTP, a roster/pairing execution, or independent acceptance.
"""
import copy
import json
from pathlib import Path

import pytest

from cdpm import export_task_package as producer, model as m, smt_verify
from cdpm.gates import gate_task_package as gate
from test_a05_readiness_carrier_agent import _case, isolated_consumer  # noqa: F401
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from test_k06_integral_measure_path import source_freeze
from test_task_package import _pkg_A7D
from vmp import readiness_verify as rv, registry_derive
from webapp import task_package as receiver


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("sent, allowed", [(720, True), (780, False), (660, False)])
def test_exported_typed_hard_threshold_reaches_readiness(
        tmp_path, monkeypatch, sent, allowed):
    # Reuse real catalogue/support preparation, but NOT its hand-built
    # manifest/clean-data: verify below receives only actual ZIP members.
    _manifest, doc, _clean, _registry = _case(tmp_path, monkeypatch)
    monkeypatch.setattr(producer, "SPEC_VERSION", "1.20")
    assert len(doc["rules"]) == 1
    rule = doc["rules"][0]
    rid = rule["id"]
    assert rule["kind"] == "HARD" and rule["zone"] == "PAIRING"
    original_fit = copy.deepcopy(rule["template_fit"])
    assert original_fit["family"] == "duty_limits"
    assert original_fit["params"]["max_duty_duration"]["value"] == 720
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []

    run = tmp_path / "run-integral-hard"
    model_path = run / "extract" / "model.json"
    model_path.parent.mkdir(parents=True)
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    model_path.write_bytes(raw)
    # Only the synthetic book is reused; _pkg_A7D's synthetic manifest and
    # crew-size declaration are NOT imported into the produced package.
    dataset = _pkg_A7D()["clean_data"]
    for folder, filename, value in (
            ("data-freeze", "dataset.json", dataset),
            ("data-report", "data-report.json", {"findings": [], "checks": []})):
        target = run / folder / filename
        target.parent.mkdir()
        target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    freeze = source_freeze(run, doc)
    source_bytes = {p: p.read_bytes() for p in freeze.rglob("*.json")}

    def exact_translation(prompt):
        assert f"{rid} | Рабочая смена не более 720 минут." in prompt
        return json.dumps({
            "decls": ["(declare-const duty_minutes Int)"],
            "asserts": [{"rule": rid, "smt": "(<= duty_minutes 720)"}]})

    def forbidden_channel(*_args, **_kwargs):
        pytest.fail("Unexpected SMT retranslation: no external channel is permitted")

    monkeypatch.setattr(producer, "smt_channel_factory", forbidden_channel)
    report = smt_verify.verify(
        doc, exact_translation, model_sha256=smt_verify.model_fingerprint(raw))
    assert report["verdict"] == "sat"
    assert report["n_rules"] == report["n_translated"] == 1
    assert report["translations"][0]["rule"] == rid
    smt_verify.write_report(run, report)

    body = producer.export_zip(run)
    package_path = tmp_path / "integral-hard.zip"
    package_path.write_bytes(body)
    problems, _ = gate.check(package_path)
    assert problems == [], problems
    # The actual producer gate was called above in-process. Skip only its
    # duplicate subprocess launcher; receiver parsing and replay stay real.
    pkg = receiver.load(package_path, run_gate=False)
    manifest, received = pkg["manifest"], pkg["model"]
    assert manifest["spec_version"] == "1.20"
    assert "feasibility-hard" in manifest["allowed_modes"]["pairing"]
    assert manifest["measure"]["available"] is True
    assert manifest["measure"]["ok"] is True
    assert manifest["coverage"]["values_without_addressee"] == 0
    assert manifest["coverage"]["normative_without_rows"] == 0
    assert manifest["consistency"]["subject_sha256"] == report["subject_sha256"]
    assert smt_verify.subject_fingerprint(received) == report["subject_sha256"]
    assert len(received["rules"]) == 1 and received["rules"][0]["id"] == rid
    assert received["rules"][0]["kind"] == "HARD"
    assert received["rules"][0]["template_fit"] == original_fit
    assert m.validate_schema(received) == m.validate_template_fit(received) == []
    assert pkg["clean_data"]["tables"] == dataset["tables"]

    # Derive coverage from the received, possibly healed model. A registry
    # for the pre-export model is not evidence about the released model.
    registry_path = tmp_path / "received-registry.json"
    registry = registry_derive.derive_and_write(
        received, ROOT, registry_path, scope="synthetic integral carrier fixture")
    assert len(registry["rules"]) == 1
    assert registry["rules"][0]["verdict"] == "covered_full", registry
    applied = {"max_duty_span_min": sent}
    before = copy.deepcopy((pkg, applied))
    registry_bytes = registry_path.read_bytes()
    result = rv.verify(manifest, received, pkg["clean_data"], ROOT,
                       registry_path, form=pkg["form"], applied=applied)
    assert result["has_matrix"] and result["verdict"] == "OK", result
    cell = result["columns"]["pairing"]
    assert cell["cell_match"] and cell["declared_state"] == cell["recomputed_state"]
    assert rv.state_allows(cell["declared_state"], "feasibility-hard"), cell
    assert cell["data_actual_missing"] == cell["criteria_tiers"] == []
    assert cell["registry"]["rules_in_zone"] == 1
    assert cell["registry"]["error"] is None, cell
    assert cell["registry"]["gaps"] == 0, cell
    assert cell["registry"]["drift"] == cell["registry"]["gap_rows"] == []
    assert cell["launch_allowed"] is allowed, cell
    if allowed:
        assert cell["thresholds_missing"] == cell["why_blocked"] == []
    else:
        assert len(cell["thresholds_missing"]) == len(cell["why_blocked"]) == 1, cell
        words = cell["thresholds_missing"][0]
        assert rid in words and "max_duty_duration" in words, words
        assert "720" in words and str(sent) in words, words
        assert words in cell["why_blocked"][0]

    assert (pkg, applied) == before
    assert model_path.read_bytes() == raw
    assert {p: p.read_bytes() for p in freeze.rglob("*.json")} == source_bytes
    assert package_path.read_bytes() == body
    assert registry_path.read_bytes() == registry_bytes
