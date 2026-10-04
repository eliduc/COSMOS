"""Stand-only SMT exception: provenance, exact coverage and strict consumers.

Deterministic translator replies are test inputs, not semantic translation
evidence.  Parsing, report readers, subset Z3, producer and ZIP gate are real.
The draft fixture builds unsigned support, never approved/live support.
"""
import copy
import hashlib
import io
import json
from types import SimpleNamespace
import zipfile

import pytest

from cdpm import export_task_package as tp, model as m, smt_verify as sv
from cdpm.gates import gate_task_package as gate
from vmp import stand_solver as ss
from test_stand_draft_package_composition_agent import draft_composition  # noqa: F401
from test_stand_landing_v2_contract_agent import rule_of, world_with


NOTE = "The synthetic test translator does not support this predicate."
DECLS = ["(declare-fun x () Int)", "(declare-fun flag () Bool)"]


def _plant(tmp_path, *, customer="стенд", source_kind="synthetic-code-world",
           extra_source=False, empty=False):
    rules = [] if empty else [
        {"id": "R-U", "kind": "HARD", "statement": "flag must be true"},
        {"id": "R-A", "kind": "HARD", "statement": "x >= 2"},
        {"id": "R-B", "kind": "HARD", "statement": "x <= 7"},
    ]
    for rule in rules:
        rule["sources"] = [{"sid": "S1", "anchor": "стенд"}]
    source = json.dumps({"стенд": {"kind": source_kind, "world": {},
                                 "rules": rules, "objective": None}},
                        ensure_ascii=False).encode("utf-8")
    doc = {"meta": {"customer": customer, "mode": 1, "revision": 1},
           "sources": [{"sid": "S1", "file": "stand-world.json",
                        "sha256": hashlib.sha256(source).hexdigest()}],
           "rules": rules, "questions": [], "objectives": []}
    if extra_source:
        doc["sources"].append({"sid": "S2", "file": "customer.txt",
                               "sha256": "f" * 64})
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    run = tmp_path / "run-stand"
    (run / "extract").mkdir(parents=True)
    (run / "extract" / "model.json").write_bytes(raw)
    (run / "extract" / "stand-world.json").write_bytes(source)
    context = tp._StandExportContext(run_dir=run, model_bytes=raw, source_bytes=source)
    return run, doc, raw, source, context


def _report(run, doc, raw, *, all_untranslated=False, healthy=False):
    rows = []
    for rule in doc["rules"]:
        rid = rule["id"]
        if not healthy and (all_untranslated or rid == "R-U"):
            rows.append({"rule": rid, "status": "untranslatable", "note": NOTE})
        else:
            rows.append({"rule": rid,
                         "smt": {"R-U": "flag", "R-A": "(>= x 2)",
                                 "R-B": "(<= x 7)"}[rid]})

    def translate(prompt):
        assert all(rule["id"] + " | " in prompt for rule in doc["rules"])
        return json.dumps({"decls": DECLS, "asserts": rows})

    report = sv.verify(doc, translate, model_sha256=sv.model_fingerprint(raw))
    sv.write_report(run, report)
    return report


@pytest.mark.parametrize("all_untranslated", [False, True], ids=["mixed", "all"])
def test_stand_exception_preserves_honest_none_and_exact_rule_reasons(tmp_path, all_untranslated):
    run, doc, raw, source, context = _plant(tmp_path)
    report = _report(run, doc, raw, all_untranslated=all_untranslated)
    report_bytes = sv.report_path(run).read_bytes()
    assert report["decls"] == DECLS, "subset verification needs the retained declarations"
    result = tp.consistency_of(run, raw, _stand_context=context)
    assert result["verdict"] == "none"
    assert result["n_rules"] == 3
    assert result["n_translated"] == (0 if all_untranslated else 2)
    expected_rules = ["R-U", "R-A", "R-B"] if all_untranslated else ["R-U"]
    assert result["stand_exception"] == {
        "policy": "stand-untranslatable/1",
        "rules": [{"rule": rid, "note": NOTE} for rid in expected_rules],
        "source_sha256": hashlib.sha256(source).hexdigest(),
    }
    assert sv.report_path(run).read_bytes() == report_bytes
    assert (run / "extract" / "model.json").read_bytes() == raw
    assert (run / "extract" / "stand-world.json").read_bytes() == source
    # Even a model labelled 'стенд' has no exemption on the public path.
    with pytest.raises(tp.TaskPackageError) as failure:
        tp.consistency_of(run, raw)
    assert failure.value.code == tp.SMT_INCOMPLETE


@pytest.mark.parametrize("empty", [False, True], ids=["sat", "no-hard-rules"])
def test_healthy_consistency_has_no_new_exception_or_shape(tmp_path, empty):
    run, doc, raw, _, context = _plant(tmp_path, empty=empty)
    _report(run, doc, raw, healthy=True)
    ordinary = tp.consistency_of(run, raw)
    stand = tp.consistency_of(run, raw, _stand_context=context)
    assert stand == ordinary
    assert "stand_exception" not in stand
    assert stand["verdict"] == ("none" if empty else "sat")
    assert stand["n_rules"] == stand["n_translated"] == (0 if empty else 3)


@pytest.mark.parametrize("foreign_kind", ["duck", "subclass"])
def test_stand_context_requires_exact_internal_type_with_other_evidence_valid(
        tmp_path, foreign_kind):
    run, doc, raw, source, context = _plant(tmp_path)
    _report(run, doc, raw)
    report_bytes = sv.report_path(run).read_bytes()
    assert type(context) is tp._StandExportContext
    # The very same saved bytes/report authorize the real internal context.
    healthy = tp.consistency_of(run, raw, _stand_context=context)
    assert healthy["verdict"] == "none"
    assert healthy["stand_exception"] == {
        "policy": "stand-untranslatable/1",
        "rules": [{"rule": "R-U", "note": NOTE}],
        "source_sha256": hashlib.sha256(source).hexdigest(),
    }

    class DerivedContext(tp._StandExportContext):
        pass

    factory = SimpleNamespace if foreign_kind == "duck" else DerivedContext
    foreign = factory(run_dir=run, model_bytes=raw, source_bytes=source)
    assert type(foreign) is not tp._StandExportContext
    assert (foreign.run_dir, foreign.model_bytes, foreign.source_bytes) == (
        context.run_dir, context.model_bytes, context.source_bytes)
    # No bad path/bytes/source can accidentally make this a passing negative.
    # Removing the exact-type guard admits both; isinstance admits subclass.
    with pytest.raises(tp.TaskPackageError) as failure:
        tp.consistency_of(run, raw, _stand_context=foreign)
    assert failure.value.code == "stand-context"
    assert "нет контекста стендового вызова" in str(failure.value)
    assert (run / "extract" / "model.json").read_bytes() == raw
    assert (run / "extract" / "stand-world.json").read_bytes() == source
    assert sv.report_path(run).read_bytes() == report_bytes


@pytest.mark.parametrize("attack", [
    "customer", "source-kind", "extra-source", "wrong-run", "context-model",
    "context-source", "saved-model", "saved-source",
])
def test_context_cannot_authorize_other_or_changed_inputs(tmp_path, attack):
    run, doc, raw, source, context = _plant(
        tmp_path, customer="real customer" if attack == "customer" else "стенд",
        source_kind="customer-document" if attack == "source-kind" else "synthetic-code-world",
        extra_source=attack == "extra-source")
    _report(run, doc, raw)
    if attack == "wrong-run":
        context = tp._StandExportContext(run_dir=tmp_path / "other-run",
                                        model_bytes=raw, source_bytes=source)
    elif attack == "context-model":
        context = tp._StandExportContext(run_dir=run, model_bytes=raw + b" ", source_bytes=source)
    elif attack == "context-source":
        context = tp._StandExportContext(run_dir=run, model_bytes=raw, source_bytes=source + b" ")
    elif attack == "saved-model":
        (run / "extract" / "model.json").write_bytes(raw + b" ")
    elif attack == "saved-source":
        (run / "extract" / "stand-world.json").write_bytes(source + b" ")
    with pytest.raises(tp.TaskPackageError):
        tp.consistency_of(run, raw, _stand_context=context)


@pytest.mark.parametrize("attack", [
    "empty-note", "placeholder-note", "missing", "foreign", "duplicate",
    "malformed", "bad-decls", "mixed-invalid", "mixed-unsat", "unknown", "false-sat",
])
def test_stand_exception_does_not_turn_other_report_failures_into_permission(tmp_path, attack):
    run, doc, raw, _, context = _plant(tmp_path)
    report = _report(run, doc, raw)
    rows = report["translations"]
    if attack == "empty-note":
        rows[0]["note"] = " "
    elif attack == "placeholder-note":
        rows[0]["note"] = "..."
    elif attack == "missing":
        del rows[0]
    elif attack == "foreign":
        rows.append({"rule": "R-FOREIGN", "status": "untranslatable", "note": NOTE})
    elif attack == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif attack == "malformed":
        report["malformed"] = [{"field": "asserts", "index": 3,
                                 "kind": "null", "text": "null"}]
    elif attack == "bad-decls":
        report["decls"] = ["(not-a-valid-smt-declaration)"]
    elif attack == "mixed-invalid":
        rows[1]["smt"] = "(>= undeclared_x 2)"
    elif attack == "mixed-unsat":
        rows[1]["smt"] = "(>= x 8)"  # Contradicts the retained x <= 7.
    elif attack == "unknown":
        # Negative stored input, not a claim that Z3 returned unknown in this test.
        report["verdict"] = "unknown"
    elif attack == "false-sat":
        report["verdict"] = "sat"
    sv.write_report(run, report)
    with pytest.raises(tp.TaskPackageError):
        tp.consistency_of(run, raw, _stand_context=context)


def test_stand_channel_failure_remains_a_release_refusal(tmp_path, monkeypatch):
    run, _doc, raw, _, context = _plant(tmp_path)
    calls = []

    def unavailable(prompt):
        calls.append(prompt)
        raise RuntimeError("isolated translator unavailable")

    monkeypatch.setattr(tp, "smt_channel_factory", lambda _run: unavailable)
    with pytest.raises(tp.TaskPackageError) as failure:
        tp.consistency_of(run, raw, _stand_context=context)
    assert failure.value.code == tp.SMT_UNAVAILABLE
    assert "isolated translator unavailable" in str(failure.value)
    assert len(calls) == 1


def test_actual_stand_zip_keeps_incomplete_report_and_ordinary_gate_rejects(
        draft_composition, tmp_path, monkeypatch):
    cat, _documents, binding_paths, support = draft_composition
    protected = {p: p.read_bytes() for p in [*binding_paths.values(), support]}
    world = world_with({"max_duty_duration": 720, "max_landings_per_duty": 3,
                        "max_pairing_days": 7})
    rules = [rule_of(cat, "duty_limits", world)]
    assert rules[0]["kind"] == "HARD"
    doc = ss.model_doc(rules, world=world, family="duty_limits", catalog=cat)
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    before = copy.deepcopy((world, rules))
    calls = []

    def decline(prompt):
        calls.append(prompt)
        assert "R-STAND-duty_limits | " in prompt
        return json.dumps({"decls": [], "asserts": [{"rule": "R-STAND-duty_limits",
            "status": "untranslatable", "note": NOTE}]})

    monkeypatch.setattr(tp, "smt_channel_factory", lambda _run: decline)
    body = ss.package_bytes(world, rules, stamp=ss.stamp_of(cat),
                            base=tmp_path / "package", catalog=cat, family="duty_limits")
    assert len(calls) == 1
    assert (world, rules) == before
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = m.loads_strict(files["manifest.json"])
    packaged = m.loads_strict(files["model.json"])
    report = m.loads_strict(files["smt-report.json"])
    assert manifest["spec_version"] == tp.SPEC_VERSION == "1.18"
    assert manifest["stand_only"] is True
    assert manifest["consistency"]["verdict"] == report["verdict"] == "none"
    assert manifest["consistency"]["n_rules"] == report["n_rules"] == 1
    assert manifest["consistency"]["n_translated"] == report["n_translated"] == 0
    assert report["translations"] == [{"rule": "R-STAND-duty_limits",
                                         "status": "untranslatable", "note": NOTE}]
    assert [r["id"] for r in packaged["rules"]] == ["R-STAND-duty_limits"]
    assert packaged["rules"][0]["kind"] == "HARD"
    assert packaged["rules"][0]["template_fit"]["params"]["max_duty_duration"]["value"] == 720
    assert gate._check_files(files)[0] == []
    path = tmp_path / "stand-only.zip"
    path.write_bytes(body)
    problems, _ = gate.check(path)
    assert any("stand_only" in p or "стенд" in p.lower() for p in problems), problems
    # Removing only the label cannot make an incomplete report customer-safe.
    without_label = copy.deepcopy(manifest)
    without_label.pop("stand_only")
    assert gate._check_consistency(without_label, {}, files, packaged)
    for p, raw in protected.items():
        assert p.read_bytes() == raw
