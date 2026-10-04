"""K01 lifecycle on a fresh, actual, isolated partial TEST release.

The release's PURE/table evidence is executed by the existing bootstrap. This
file never invents support/stand approvals. Its explicitly synthetic SOFT
model is a completeness component, not certification of a complete stand world,
an airline answer, a JVM solve, a production release, or rollout permission.
The real zero-HARD consistency checker needs no translator or external solver.

Run directly: --evidence-root NEW --commit FULL_SHA [pytest arguments].
Only pinned, reviewed local Git commands inside NEW are allowed as subprocesses.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import test_a06_partial_release_bootstrap_gpt as bootstrap
from test_a06_partial_release_bootstrap_gpt import isolated
from test_cdpm_task_package import _ready_doc

FAMILY = "duty_extension_penalty"
PARAM = "long_duty_threshold"
CAP = "duty_extension_cap"
CONDITIONS = {"extension_bases", "extension_consent"}
REQUIRED = {CAP, PARAM}
VALUE = 720  # Independent fixture oracle: not copied from production output.


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def missing_document(cat):
    from template_catalog import family_contract
    doc = _ready_doc()
    rule = {
        "id": "R-K01-ACTUAL", "title": "Synthetic K01 extension component",
        "kind": "SOFT", "zone": "PAIRING", "weight": 3,
        "statement": "Продление смены не более 960 минут для ST1 только с согласия экипажа.",
        "sources": [{"sid": "S1", "anchor": "п.1"}],
    }
    cells = {
        CAP: {"value": 960, "unit": "duration_minutes", "relation": "max",
              "condition": None, "quote": "960 минут"},
        "extension_bases": {"value": ["ST1"], "unit": "code_list", "relation": "in",
                            "condition": None, "quote": "ST1"},
        "extension_consent": {"value": True, "unit": "flag", "relation": "yes",
                              "condition": None, "quote": "с согласия"},
    }
    for cell in cells.values():
        cell["source"] = {"kind": "rule", "rule_id": rule["id"]}
    rule["template_fit"] = {
        "category": "A", "schema_version": "landing/2", "family": FAMILY,
        "registry_rev": cat.fingerprint(),
        "family_contract": family_contract.snapshot(cat, FAMILY, cells),
        "params": cells, "quote": rule["statement"],
    }
    doc["rules"], doc["questions"], doc["objectives"] = [rule], [], []
    return doc


def repack(path, files, manifest):
    """Reach semantic gates with valid member lengths/hashes, not corruption."""
    from cdpm import export_task_package as producer
    files, manifest = dict(files), copy.deepcopy(manifest)
    for entry in manifest["files"]:
        body = files[entry["name"]]
        entry["sha256"] = hashlib.sha256(body).hexdigest()
        if "bytes" in entry:
            entry["bytes"] = len(body)
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    path.write_bytes(producer.write_zip(files))


def rejected_at_both(path, expected):
    from cdpm.gates import gate_task_package as gate
    from webapp import task_package as receiver
    problems, _ = gate.check(path)
    assert any(expected in problem for problem in problems), problems
    assert not any("SHA-256" in p or "контрольная сумма" in p or "support-missing" in p
                   for p in problems), problems
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receiver.load(path, run_gate=False)
    assert error.value.code == expected, str(error.value)
    assert FAMILY in str(error.value), str(error.value)
    return {"code": error.value.code, "gate": problems, "receiver": str(error.value)}


def roundtrip(root, doc, cat, pin, *, complete, asked, monkeypatch):
    from cdpm import family_manifest, family_params as fp, model, smt_verify, webui
    from cdpm import export_task_package as producer
    from cdpm.gates import gate_task_package as gate
    from webapp import task_package as receiver
    original = copy.deepcopy(doc)
    assert model.validate_schema(doc) == model.validate_template_fit(doc) == []
    # Missing ordinary input is curable; it is not a model-contract violation.
    assert model.validate_model(doc) == []
    rule = doc["rules"][0]
    fit = rule["template_fit"]
    assert fit["family_contract"] == pin
    clauses = {(s["sid"], s["anchor"]): rule["statement"] for s in rule["sources"]}
    assert model.validate_fit_quotes(doc, clauses) == []
    assert set(fit["params"]) == (REQUIRED if complete else {CAP}) | CONDITIONS
    expected_missing = [] if complete else [PARAM]
    expected_given = sorted(REQUIRED if complete else {CAP})
    expected_verdict = "полна" if complete else "неполна"
    state = fp.family_completeness(doc)[FAMILY]
    assert state["given"] == expected_given and state["missing"] == expected_missing, state
    assert state["declined"] == state["pending"] == []
    assert state["verdict"] == expected_verdict, state
    if not complete:
        assert state["asked"][PARAM] == asked
    projected = family_manifest.project(doc)[FAMILY]
    assert projected["given"] == expected_given and projected["missing"] == expected_missing
    assert projected["verdict"] == expected_verdict
    # Real current native support, not a substitute [] for support_problems.
    assert model.support_problems(doc, "1.20") == []
    matrix = model.readiness_matrix(doc, "1.20")["pairing"]
    assert matrix["feasibility-hard"]["ready"]
    assert matrix["feasibility-soft"]["ready"] is complete, matrix
    modes, _ = producer.allowed_modes(doc, "1.20")
    assert "feasibility-hard" in modes["pairing"]
    assert ("feasibility-soft" in modes["pairing"]) is complete

    run = root / "run-0042"
    source = run / "extract/model.json"
    source.parent.mkdir(parents=True)
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    source.write_bytes(raw)

    def forbidden(*_a, **_k):
        raise AssertionError("No translator/Z3/network/service in this SOFT completeness fixture")

    monkeypatch.setattr(producer, "smt_channel_factory", forbidden)
    monkeypatch.setattr(smt_verify, "_run_z3", forbidden)
    report = smt_verify.verify(doc, forbidden, model_sha256=smt_verify.model_fingerprint(raw))
    assert report["n_rules"] == report["n_translated"] == 0
    smt_verify.write_report(run, report)
    files = producer.build_package(run, spec_version="1.20")
    archive = root / "native.zip"
    body = producer.write_zip(files)
    archive.write_bytes(body)
    problems, _ = gate.check(archive)
    assert problems == [], problems
    received = receiver.load(archive, run_gate=False)
    manifest = received["manifest"]
    assert manifest["spec_version"] == "1.20" and not manifest.get("stand_only")
    assert manifest["catalog"] == {"version": cat.version(), "fingerprint": cat.fingerprint()}
    assert received["family_projection"]["judged"]
    assert received["landing_projection"]["archive_only"] is False
    assert manifest["families"][FAMILY] == projected
    assert manifest["allowed_modes"] == modes
    assert received["model"]["rules"][0]["template_fit"] == fit
    receiver.check_support_execution(received, task="pairing", mode="feasibility-hard")
    if complete:
        receiver.check_support_execution(received, task="pairing", mode="feasibility-soft")

    # Real endpoint on real source bytes; no HTTP server or socket transport.
    monkeypatch.setenv("CDPM_RUNS_DIR", str(root))
    response = webui.run_effective_parameters("run-0042")
    assert response.status_code == 200
    view = json.loads(response.body)
    assert view["model_sha256"] == hashlib.sha256(raw).hexdigest()
    shown = next(row for row in view["family_review"]["families"] if row["family"] == FAMILY)
    assert shown["missing"] == expected_missing and shown["verdict"] == expected_verdict, shown
    parameters = {p["name"]: p for p in view["rules"][0]["params"]}
    assert parameters[CAP]["value"] == 960
    if complete:
        assert parameters[PARAM]["value"] == VALUE
        assert parameters[PARAM]["unit"] == "duration_minutes"
        assert parameters[PARAM]["quote"] == "720 минут"
    else:
        assert PARAM not in parameters
    dump(root / "actual-human-review.json", view)

    # Honest partial transfer is healthy. The lie is only its claimed mode.
    negatives = {}
    if not complete:
        forbidden_mode = copy.deepcopy(manifest)
        forbidden_mode["allowed_modes"]["pairing"].append("feasibility-soft")
        bad = root / "forged-permission-rehashed.zip"
        repack(bad, files, forbidden_mode)
        negatives["permission"] = rejected_at_both(bad, "family-incomplete")
    false_family = copy.deepcopy(manifest)
    false_family["families"][FAMILY]["given"] = [] if complete else sorted(REQUIRED)
    bad = root / "forged-family-rehashed.zip"
    repack(bad, files, false_family)
    negatives["family"] = rejected_at_both(bad, "manifest-mismatch")
    assert doc == original and source.read_bytes() == raw and archive.read_bytes() == body
    return {"complete": complete, "asked": asked, "source_sha256": sha(source),
            "native_sha256": sha(archive), "allowed_modes": modes,
            "given": expected_given, "missing": expected_missing,
            "verdict": expected_verdict, "negatives": negatives}


def test_missing_question_answer_lifecycle_with_actual_test_support(
        isolated, tmp_path, monkeypatch, record_property):
    from cdpm import answers, family_params as fp
    bootstrap.test_fresh_partial_test_release_enables_only_supported_native_mode(
        isolated, tmp_path, monkeypatch, record_property)
    cat = isolated.store.catalog()
    # Guard against accidental promotion/re-stamping of any TEST act/evidence.
    protected = {p: sha(p) for p in isolated.folder.rglob("*") if p.is_file()}
    protected.update({p: sha(p) for p in (isolated.settings.cosmos_bindings,
                                         isolated.settings.cdpm_bindings)})
    record = cat.family(FAMILY)
    assert {p["name"] for p in record["params"]} == REQUIRED
    assert {p["name"] for p in record["conditions"]} == CONDITIONS
    assert not record.get("param_sets")  # Explicit all-parameters law.
    doc = missing_document(cat)
    pin = copy.deepcopy(doc["rules"][0]["template_fit"]["family_contract"])
    assert pin["param_set"] == "all"
    originals = copy.deepcopy(doc["rules"][0]["template_fit"]["params"])
    support_before = (isolated.folder / "support.json").read_bytes()
    rows = []
    rows.append(roundtrip(tmp_path / "missing", doc, cat, pin,
                          complete=False, asked=None, monkeypatch=monkeypatch))
    created = fp.ensure_family_param_questions(doc)
    assert len(created) == 1, created
    question = next(q for q in doc["questions"] if q["id"] == created[0])
    assert fp.question_target(question) == (FAMILY, PARAM)
    assert doc["rules"][0]["template_fit"]["params"] == originals
    rows.append(roundtrip(tmp_path / "open-question", doc, cat, pin,
                          complete=False, asked=created[0], monkeypatch=monkeypatch))
    applied = answers.apply_answers(doc, {created[0]: "720 минут"})
    assert applied["applied"] == 1, applied
    fit = doc["rules"][0]["template_fit"]
    assert {key: fit["params"][key] for key in originals} == originals
    assert fit["family_contract"] == pin
    cell = fit["params"][PARAM]
    assert cell["value"] == VALUE and cell["unit"] == "duration_minutes"
    assert cell["relation"] == "max" and cell["quote"] == "720 минут"
    assert cell["source"]["kind"] == "answer"
    assert cell["source"]["question"]["id"] == created[0]
    assert cell["source"]["question"]["revision"] == 1
    assert cell["source"]["question"]["fingerprint"]
    assert cell["source"]["version"]["fingerprint"]
    assert cell["source"]["effect"] == {"rule": PARAM, "field": "value"}
    rows.append(roundtrip(tmp_path / "answered", doc, cat, pin,
                          complete=True, asked=created[0], monkeypatch=monkeypatch))
    assert fp.ensure_family_param_questions(doc) == []
    assert (isolated.folder / "support.json").read_bytes() == support_before
    assert {p: sha(p) for p in protected} == protected
    summary = {
        "scope": "K01 completeness component on actual partial TEST support; no solve/PROD",
        "source_commit": os.environ["A06_BOOTSTRAP_SOURCE_COMMIT"],
        "test_source_sha256": sha(Path(__file__)), "family_contract": pin,
        "catalog": {"version": cat.version(), "fingerprint": cat.fingerprint()},
        "support_sha256": hashlib.sha256(support_before).hexdigest(),
        "independent_answer_oracle": VALUE, "rows": rows,
        "protected_evidence_unchanged": True,
    }
    dump(tmp_path / "k01-lifecycle-summary.json", summary)
    record_property("k01_actual_support_evidence", str(tmp_path))


def rest_missing_document(cat):
    """Separate HARD/ROSTERING component, not a changed catalogue stand world."""
    from template_catalog import family_contract
    doc = _ready_doc()
    rule = {
        "id": "R-K01-REST", "title": "Synthetic K01 rest component",
        "kind": "HARD", "zone": "ROSTERING",
        "statement": "Отдых в базе не менее 660 минут; длительность отдыха вне базы не задана.",
        "sources": [{"sid": "S1", "anchor": "п.1"}],
    }
    cells = {"min_rest_home": {
        "value": 660, "unit": "duration_minutes", "relation": "min",
        "condition": None, "quote": "660 минут",
        "source": {"kind": "rule", "rule_id": rule["id"]},
    }}
    rule["template_fit"] = {
        "category": "A", "schema_version": "landing/2", "family": "rest_floors",
        "registry_rev": cat.fingerprint(),
        "family_contract": family_contract.snapshot(cat, "rest_floors", cells),
        "params": cells, "quote": rule["statement"],
    }
    doc["rules"], doc["questions"], doc["objectives"] = [rule], [], []
    return doc


@pytest.mark.parametrize("family,param,zone,kind,modes", [
    (FAMILY, PARAM, "PAIRING", "SOFT", ("optimization", "feasibility-soft")),
    ("rest_floors", "min_rest_outstation", "ROSTERING", "HARD",
     ("optimization", "feasibility-soft", "feasibility-hard")),
], ids=["pairing-soft-shared", "rostering-hard-shared"])
def test_k01_consumers_do_not_depend_on_question_creation(
        tmp_path, monkeypatch, record_property, family, param, zone, kind, modes):
    """P0 3.7 + existing K14 consumer law, without hiding neighbour K06.

    Both catalogue families declare Builder and Kotlin consumers. K14 already
    requires their parameter questions to address both tasks even when the
    physical rule occupies only one zone. K01 must hold the same consumers
    BEFORE that question exists. This direct K01 regression neither replaces
    native support nor mistakes absent K06 support for a passed K01 check.
    """
    import template_catalog as tc
    from cdpm import answers, catalog_bridge, family_params as fp, model
    monkeypatch.setenv(catalog_bridge.ENV_CATALOG, str(bootstrap.CATALOG))
    bootstrap.forget()
    try:
        cat = tc.current(bootstrap.CATALOG, validated=True)
        assert cat.version() == "0.7.0-draft.6" and cat.fingerprint() == bootstrap.FP
        assert cat.family(family)["zone"] == zone
        declared = set(catalog_bridge.executor_kinds(family))
        assert {"builder_method", "kotlin_constraint"} <= declared
        doc = missing_document(cat) if family == FAMILY else rest_missing_document(cat)
        assert doc["rules"][0]["zone"] == zone and doc["rules"][0]["kind"] == kind
        pin = copy.deepcopy(doc["rules"][0]["template_fit"]["family_contract"])
        original_cells = copy.deepcopy(doc["rules"][0]["template_fit"]["params"])
        assert model.validate_schema(doc) == model.validate_template_fit(doc) == []

        def snapshot(label):
            original = copy.deepcopy(doc)
            state = fp.family_completeness(doc)[family]
            problems = fp.family_barrier_problems(doc)
            assert all(p["code"] == "family-incomplete" and p["family"] == family
                       for p in problems), problems
            affected = sorted({pair for problem in problems for pair in model.barrier_affects(problem)})
            # Full readiness is recorded honestly; unapproved K06 may still
            # block an otherwise complete synthetic component at this draft.
            matrix = model.readiness_matrix(doc, "1.20")
            assert doc == original
            return {"phase": label, "missing": state["missing"], "verdict": state["verdict"],
                    "k01_problems": problems, "k01_affected": affected,
                    "actual_matrix": matrix}

        before = snapshot("missing-no-question")
        assert before["missing"] == [param] and before["verdict"] == "неполна"
        created = fp.ensure_family_param_questions(doc)
        assert len(created) == 1, created
        question = next(q for q in doc["questions"] if q["id"] == created[0])
        assert fp.question_target(question) == (family, param)
        # This is an independent healthy control of the established K14 law,
        # not a dynamically copied expected scope for the failing K01 oracle.
        assert model.question_tasks(doc, question) == ("pairing", "rostering")
        opened = snapshot("missing-question-open")
        assert opened["missing"] == [param] and opened["verdict"] == "неполна"
        assert doc["rules"][0]["template_fit"]["params"] == original_cells
        result = answers.apply_answers(doc, {created[0]: "720 минут"})
        assert result["applied"] == 1, result
        fit = doc["rules"][0]["template_fit"]
        assert fit["params"][param]["value"] == VALUE
        assert fit["params"][param]["source"]["kind"] == "answer"
        assert fit["family_contract"] == pin
        assert {key: fit["params"][key] for key in original_cells} == original_cells
        answered = snapshot("answered-720")
        # Healthy control finishes BEFORE the failure assertion below.
        assert answered["missing"] == [] and answered["verdict"] == "полна"
        assert answered["k01_problems"] == [] and answered["k01_affected"] == []
        expected = sorted((task, mode) for task in ("pairing", "rostering") for mode in modes)
        evidence = {"scope": "K01-only causal consumer regression; no support/launch claim",
                    "family": family, "source_zone": zone, "kind": kind,
                    "expected_k01_affected_while_missing": expected,
                    "source_commit": os.environ["A06_BOOTSTRAP_SOURCE_COMMIT"],
                    "test_source_sha256": sha(Path(__file__)),
                    "states": [before, opened, answered]}
        path = tmp_path / "k01-consumer-scope.json"
        dump(path, evidence)
        record_property("k01_consumer_scope_evidence", str(path))
        assert before["k01_affected"] == expected, evidence
        assert opened["k01_affected"] == expected, evidence
    finally:
        bootstrap.forget()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    options, arguments = parser.parse_known_args()
    if re.fullmatch(r"[0-9a-f]{40}", options.commit) is None:
        raise SystemExit("Expected exact source SHA")
    os.environ["A06_BOOTSTRAP_SOURCE_COMMIT"] = options.commit
    base = options.evidence_root.resolve()
    if base.exists():
        raise SystemExit("Evidence root must be new")
    base.mkdir(parents=True)
    allowed, denied = [], []

    def audit(event, args):
        if event == "subprocess.Popen":
            import subprocess
            executable, command, cwd, _env = args
            parts = list(command) if isinstance(command, (list, tuple)) else []
            if isinstance(command, str):
                parts = next(([name, *rest] for name in ("git", str(bootstrap.rehearsal.GIT))
                              for rest in bootstrap.rehearsal.ALLOWED_ARGS
                              if command == subprocess.list2cmdline([name, *rest])), [])
            location = Path(cwd).resolve() if cwd else Path.cwd().resolve()
            exe = executable or (parts[0] if parts else "")
            resolved = Path(shutil.which(str(exe)) or str(exe)).resolve()
            if (resolved == bootstrap.rehearsal.GIT.resolve() and parts
                    and tuple(parts[1:]) in bootstrap.rehearsal.ALLOWED_ARGS
                    and location.is_relative_to(base)):
                allowed.append({"args": parts[1:], "cwd": str(location)})
                return
        elif not (event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.sendto", "socket.sendmsg",
                             "os.system", "os.posix_spawn", "os.exec", "os.spawn"} or event.startswith("os.spawn")):
            return
        denied.append(event)
        raise RuntimeError("K01 actual support guard denied " + event)

    sys.addaudithook(audit)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["COSMOS_SKIP_CONFIG_MIGRATION"] = "1"
    import socket
    from _pytest import junitxml
    junitxml.platform = SimpleNamespace(node=socket.gethostname)
    code = pytest.main(["--noconftest", "-p", "no:cacheprovider", "-o", "junit_family=xunit1",
                       "--basetemp=" + str(base / "pytest"), str(Path(__file__).resolve()), *arguments])
    dump(base / "guard.json", {"exit": int(code), "allowed_git": allowed, "denied": denied})
    print(json.dumps({"exit": int(code), "git_children": len(allowed), "denied": denied}))
    return 90 if denied else int(code)


if __name__ == "__main__":
    raise SystemExit(main())
