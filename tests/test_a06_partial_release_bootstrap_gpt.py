"""Delegated-owner TEST release bootstrap, confined to a new temporary repo.

Fresh PURE/table observations are not imported from historical verdicts.
The real release API has owner-role wording: this is GPT executing the owner's
delegated TEST authority, NOT an expert signature, production release or rollout.
Parity remains explicitly unjudged, not green. No service/JVM/network is used.
"""
import argparse
import copy
from datetime import datetime, timezone
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
import test_a06_release_rehearsal_agent as rehearsal

CATALOG = ROOT / "catalog-integration/0.7.0-draft.6"
FP = "a7180288f0c54b70a8009e6efcda730aec648e88ad9b8d148a51641664b8df81"
REQUIRED_PAIRING_HARD = {
    ("connection_windows", "builder_method"), ("connection_windows", "check_code"),
    ("duty_limits", "builder_method"), ("duty_limits", "check_code"),
    ("duty_limits", "check_template"), ("duty_limits", "export_form"),
    ("time_scale", "builder_method"), ("time_scale", "check_template"),
}


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def forget():
    import template_catalog as tc
    from cdpm import catalog_bridge
    from vmp import catalog_access
    tc.forget_current(); catalog_bridge.forget(); catalog_access.forget()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    # Reuse genuine draft3 isolation/role/Git setup, then replace only its
    # TEST copy with draft6. Its literal draft3 assertion is not suppressed.
    original = {p: rehearsal.sha(p) for p in CATALOG.rglob("*") if p.is_file()}
    monkeypatch.setattr(rehearsal, "CANDIDATE", ROOT / "catalog-integration/0.7.0-draft.3")
    monkeypatch.setattr(rehearsal, "FP", "dd8687d5f8574f2f3573b5a908a15c7c7bb8b1142991bb1297c2e445cbfaa02e")
    generator = rehearsal.prepared.__wrapped__(tmp_path, monkeypatch)
    state = next(generator)
    try:
        # Copy only canonical candidate files; old tools were already omitted.
        # No recursive deletion, no mutation of a source/signed/default tree.
        for source in CATALOG.iterdir():
            if source.is_file():
                shutil.copy2(source, state.folder / source.name)
        state.store.forget(); forget()
        state.cat = state.store.catalog()
        assert state.cat.version() == "0.7.0-draft.6" and state.cat.fingerprint() == FP
        assert not [p for p in state.cat.validate() if p.severity == "error"]
        yield state
    finally:
        assert {p: rehearsal.sha(p) for p in original} == original
        try:
            next(generator)
        except StopIteration:
            pass


def source_run(cat, base):
    from test_a06_component_inputs_gpt import calendar_local_report
    from cdpm import smt_verify
    from vmp import stand_tasks, stand_solver
    task = next(t for t in cat.family("calendar_windows")["stand_tasks"] if t["id"] == "st-window-1")
    world = copy.deepcopy(task["world"])
    meta = stand_tasks._family_meta(cat)
    families = stand_tasks.families_for(world, "calendar_windows", meta)
    rules = stand_solver._rules_for(world, families, meta)
    doc = stand_solver.model_doc(rules, world=world, family="calendar_windows", catalog=cat)
    run = stand_solver._write_run(base, doc, stand_solver._sheets(world),
                                 source=stand_solver._stand_source(world, rules, None))
    raw = (run / "extract/model.json").read_bytes()
    report = calendar_local_report(doc, raw)
    smt_verify.write_report(run, report)
    return task, world, rules, raw, report


def test_fresh_partial_test_release_enables_only_supported_native_mode(isolated, tmp_path, monkeypatch, record_property):
    import template_catalog as tc
    from template_catalog import bindings, canon
    from cdpm import bindings_build as cbb, stand_tables
    from cdpm import export_task_package as tp
    from cdpm.gates import gate_task_package
    from vmp import bindings_build as vbb, stand_tasks, stand_solver, stand_adapters
    from catalog_service import releases, executors
    from catalog_service.errors import ServiceError
    from webapp import task_package
    state = isolated
    commit = os.environ.get("A06_BOOTSTRAP_SOURCE_COMMIT", "")
    assert re.fullmatch(r"[0-9a-f]{40}", commit), "Pass exact source commit to explicit runner"
    # A separate UNRELEASED evidence candidate at the intended version. This
    # is preparation, not a release record/approval; body remains byte-derived.
    evidence_folder = tmp_path / "evidence-catalog"
    shutil.copytree(state.folder, evidence_folder)
    header = json.loads((evidence_folder / "catalog.json").read_bytes())
    header["version"] = "0.7.0"
    # H01 requires this role on a final-version header. This preparatory copy
    # has no release history entry, act or approval; provenance is explicit.
    header["released_by"] = "владелец"
    header["note"] = "TEST evidence candidate only; no act, no support approval"
    dump(evidence_folder / "catalog.json", header)
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(evidence_folder))
    forget()
    candidate = tc.current(evidence_folder, validated=True)
    assert candidate.body() == state.cat.body() and candidate.fingerprint() == FP
    assert not (evidence_folder / "support.json").exists()
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    pure_tmp = tmp_path / "pure-tmp"
    pure_tmp.mkdir()
    pure_rows = stand_tasks.run_tasks(catalog=candidate,
        kinds=["builder_method", "check_template", "check_code", "export_form"], tmp_dir=pure_tmp)
    assert sum(r["result"] == "passed" for r in pure_rows) == 39
    assert sum(r["result"] == "skipped" for r in pure_rows) == 15
    assert not [r for r in pure_rows if r["result"] == "failed"]
    pure = stand_tasks.results_document(pure_rows, candidate, at=at, commit=commit, repo=ROOT)
    assert stand_tasks.stale_reasons(pure, candidate, ROOT) == []
    table_rows, tasks_source = stand_tables.run_all(candidate, seed={}, source="catalog")
    assert len(table_rows) == 15 and all(r["result"] == "passed" for r in table_rows)
    tables = stand_tables.results_doc(candidate, table_rows, tasks_source=tasks_source, commit=commit, at=at)
    assert stand_tables.stale_reason(tables, candidate) is None
    dump(tmp_path / "actual-pure.json", pure)
    dump(tmp_path / "actual-tables.json", tables)
    cosmos = vbb.build(candidate, commit=commit, generated_at=at, stand_results=pure)
    cdpm = cbb.build(candidate, commit=commit, generated_at=at, stand_results=tables)
    state.settings.cosmos_bindings.write_text(vbb.dump(cosmos), encoding="utf-8")
    state.settings.cdpm_bindings.write_text(cbb.dump(cdpm), encoding="utf-8")
    raw_bindings = {p: p.read_bytes() for p in (state.settings.cosmos_bindings, state.settings.cdpm_bindings)}
    evidence_settings = state.settings.with_(catalog_dir=evidence_folder)
    unsigned = executors.support_table(candidate, evidence_settings, approved_by=None)
    executors.write_support(evidence_settings, unsigned)
    forget()
    before_cat = tc.current(evidence_folder, validated=True)
    assert not any(c["approved"] for c in unsigned["cells"])
    assert all(any(c["record_id"] == rec and c["kind"] == kind and c["state"] == "есть"
                   for c in unsigned["cells"]) for rec, kind in REQUIRED_PAIRING_HARD)
    dump(tmp_path / "before-act-support.json", unsigned)
    monkeypatch.setattr(tp, "smt_channel_factory", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("external SMT forbidden; use actual local finite report")))
    pre = source_run(before_cat, tmp_path / "before-act-source")
    with pytest.raises(stand_solver.StandSolverError) as refused:
        stand_solver.package_bytes(pre[1], pre[2], stamp=stand_solver.stamp_of(before_cat),
            base=tmp_path / "before-act-source", catalog=before_cat, family="calendar_windows", spec_version="1.20")
    assert isinstance(refused.value.__context__, tp.TaskPackageError)
    assert refused.value.__context__.code == tp.SUPPORT_MISSING
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(state.folder))
    forget(); state.store.forget()
    rehearsal.git(state.repo, "init", "--initial-branch=a06-rehearsal")
    rehearsal.git(state.repo, "add", "--", "catalog", "template_catalog", "cosmos-bindings.json", "cdpm-bindings.json")
    rehearsal.git(state.repo, "commit", "--no-gpg-sign", "-m", "A06 isolated rehearsal input")
    checked = releases.checks(state.store, version="0.7.0")
    dump(tmp_path / "actual-prechecks.json", checked)
    assert [(r["key"], r["code"]) for r in checked["checks"] if not r["ok"]] == [("parity", "parity-unconfirmed")]
    with pytest.raises(ServiceError) as parity_refusal:
        releases.release(state.store, state.journal, version="0.7.0", note="TEST delegated GPT; no parity confirmation")
    assert parity_refusal.value.code == "parity-unconfirmed"
    act = releases.release(state.store, state.journal, version="0.7.0", confirm_parity=True,
        note="TEST ONLY: GPT exercising owner-delegated local authority; partial actual evidence; parity unjudged; NOT expert signature, PROD or rollout")
    assert act["parity"]["ok"] is None and act["confirmed_parity"]
    assert state.journal.last(op="release")["outcome"] == "warning"
    dump(tmp_path / "actual-test-act.json", act)
    final = state.store.catalog()
    assert final.version() == "0.7.0" and final.fingerprint() == FP
    # The real writer calls canon.normalize before serialization. The FIRST
    # retained run proved exactly three list-order deltas, not changed records:
    # two newly appended stand tasks and the extended stand-kind dictionary.
    before_body, after_body = candidate.body(), final.body()
    assert tc.canonical_bytes(after_body) == tc.canonical_bytes(before_body)
    assert after_body == canon.normalize(before_body)
    only_known_order_changes = copy.deepcopy(before_body)
    order_changes = []
    for family in only_known_order_changes["families"]:
        if family["id"] in {"fleet_exclusions", "rest_floors"}:
            original_order = [task["id"] for task in family["stand_tasks"]]
            family["stand_tasks"].sort(key=lambda task: task["id"])
            order_changes.append({"path": "families/" + family["id"] + "/stand_tasks",
                                  "before": original_order,
                                  "after": [task["id"] for task in family["stand_tasks"]]})
    kinds = only_known_order_changes["dictionaries"]["stand_task_kinds"]
    sorted_kinds = sorted(kinds, key=lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True))
    only_known_order_changes["dictionaries"]["stand_task_kinds"] = sorted_kinds
    order_changes.append({"path": "dictionaries/stand_task_kinds", "before": kinds, "after": sorted_kinds})
    assert after_body == only_known_order_changes  # no other normalization/delta allowed
    assert len(order_changes) == 3 and all(row["before"] != row["after"] for row in order_changes)
    dump(tmp_path / "actual-body-order-normalization.json", order_changes)
    approved = json.loads((state.folder / "support.json").read_bytes())
    assert all(bool(c["approved"]) == (c["state"] == "есть") for c in approved["cells"])
    assert all(c["approved"]["by"] == "владелец" for c in approved["cells"] if c["approved"])
    assert {p: p.read_bytes() for p in raw_bindings} == raw_bindings
    assert (state.folder / "releases/0.7.0/support.json").read_bytes() == (state.folder / "support.json").read_bytes()
    assert all(r["ok"] for r in releases.releases_gate(final))
    forget()
    task, world, rules, raw, report = source_run(final, tmp_path / "after-act-source")
    package = stand_solver.package_bytes(world, rules, stamp=stand_solver.stamp_of(final),
        base=tmp_path / "after-act-source", catalog=final, family="calendar_windows", spec_version="1.20")
    native = tmp_path / "component-inputs/st-window-1"
    native.mkdir(parents=True)
    archive = native / "st-window-1.zip"
    archive.write_bytes(package)
    assert gate_task_package.check(archive)[0] == []
    received = task_package.load(archive, run_gate=False)
    manifest = received["manifest"]
    assert manifest["allowed_modes"] == {"pairing": ["feasibility-hard"], "rostering": []}
    task_package.check_support_execution(received, task="pairing", mode="feasibility-hard")
    with pytest.raises(task_package.TaskPackageRefused) as unsupported:
        task_package.check_support_execution(received, task="rostering", mode="feasibility-hard")
    assert unsupported.value.code == tp.SUPPORT_MISSING
    assert not manifest.get("stand_only")
    assert manifest["catalog"] == {"version": "0.7.0", "fingerprint": FP}
    assert (tmp_path / "after-act-source" / stand_solver.SOURCE_RUN / "extract/model.json").read_bytes() == raw
    dump(native / "st-window-1.problem.json", stand_adapters.world_to_problem(world))
    dump(native / "st-window-1.world.json", {"world": world, "expected": task["expected"]})
    identity = {"catalog_version": final.version(), "catalog_fingerprint": FP, "task": "st-window-1",
        "admission": "delegated-owner TEST partial release; pairing feasibility-hard only; no launch",
        "files": {p.name: rehearsal.sha(p) for p in native.iterdir() if p.is_file()}}
    dump(native / "identity.json", identity)
    preserved = {p: rehearsal.sha(p) for p in native.iterdir() if p.is_file()}
    summary = {"scope": "delegated-owner TEST only; no solve/PROD/expert signature",
        "source_commit": commit, "test_scope": "explicit local test against named source with actual code census",
        "test_source_sha256": rehearsal.sha(Path(__file__)),
        "actual_code": pure["code"], "pure_passed": 39,
        "pure_skipped": 15, "tables_passed": 15, "parity": "unjudged, explicit warning",
        "before_act": "support-missing", "after_act_allowed_modes": manifest["allowed_modes"],
        "execution_support": {"pairing_feasibility_hard": "accepted", "rostering_feasibility_hard": unsupported.value.code},
        "support_present": sum(c["state"] == "есть" for c in approved["cells"]),
        "native_inputs": str(native.parent), "native_sha256": rehearsal.sha(archive),
        "raw_bindings_unchanged": True, "smt": report,
        "primary_files": {str(p.relative_to(tmp_path)).replace('\\', '/'): rehearsal.sha(p) for p in
            (tmp_path / "actual-pure.json", tmp_path / "actual-tables.json", tmp_path / "actual-test-act.json",
             state.folder / "support.json", state.folder / "releases/0.7.0/support.json",
             state.settings.cosmos_bindings, state.settings.cdpm_bindings)}}
    dump(tmp_path / "summary.json", summary)
    assert {p: p.read_bytes() for p in raw_bindings} == raw_bindings
    assert {p: rehearsal.sha(p) for p in preserved} == preserved
    record_property("bootstrap_evidence", str(tmp_path))
    record_property("native_inputs", str(native.parent))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    options, arguments = parser.parse_known_args()
    if re.fullmatch(r"[0-9a-f]{40}", options.commit) is None:
        raise SystemExit("--commit must be the exact 40-character source SHA")
    os.environ["A06_BOOTSTRAP_SOURCE_COMMIT"] = options.commit
    base = options.evidence_root.resolve()
    if base.exists():
        raise SystemExit("Evidence root must be NEW: never replace previous evidence")
    base.mkdir(parents=True)
    allowed, denied = [], []

    def audit(event, args):
        if event == "subprocess.Popen":
            import subprocess
            executable, command, cwd, _env = args
            parts = list(command) if isinstance(command, (list, tuple)) else []
            if isinstance(command, str):
                parts = next(([name, *rest] for name in ("git", str(rehearsal.GIT))
                              for rest in rehearsal.ALLOWED_ARGS
                              if command == subprocess.list2cmdline([name, *rest])), [])
            location = Path(cwd).resolve() if cwd else Path.cwd().resolve()
            exe = executable or (parts[0] if parts else "")
            resolved = Path(shutil.which(str(exe)) or str(exe)).resolve()
            if (resolved == rehearsal.GIT.resolve() and parts and tuple(parts[1:]) in rehearsal.ALLOWED_ARGS
                    and location.is_relative_to(base)):
                allowed.append({"args": parts[1:], "cwd": str(location)})
                return
        elif not (event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.sendto", "socket.sendmsg",
                             "os.system", "os.posix_spawn", "os.exec", "os.spawn"} or event.startswith("os.spawn")):
            return
        denied.append(event)
        raise RuntimeError("bootstrap guard denied " + event)

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
    sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
    raise SystemExit(main())
