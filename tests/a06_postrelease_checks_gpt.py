"""Actual in-process postrelease checks of an already released TEST catalogue.

The reviewed tools are sourced explicitly from draft.6; their arbitrary-target
CLI judges the released draft.6 body in a
separate working deployment copied from the real immutable release snapshot.
Full real tools are installed only in that working deployment. The original
no-tools refresh refusal remains a measured healthy safety control.
The caller owns the process/network guard and the genuine isolated TEST act.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import traceback
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_TOOLS = ROOT / "catalog-integration/0.7.0-draft.6/tools"
TRANSITION_WITNESSES = ROOT / "catalog-integration/0.7.0-draft.3/transition"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _census(folder):
    folder = Path(folder)
    return {p.relative_to(folder).as_posix(): _sha(p)
            for p in sorted(folder.rglob("*")) if p.is_file()}


def _dump(path, value):
    # Evidence outputs are new files outside the released tree.
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2,
                  allow_nan=False)
        stream.write("\n")


def verify_postrelease(state, tmp_path, monkeypatch):
    """Return actual observations, or fail AFTER preserving all primary outputs.

    Only tools/catalog_seed.json and transition/step4_additions.json may change
    in the working TEST catalogue. Every releases/ file, support.json and both
    raw approved bindings must remain byte-identical. Red/unjudged tools do not
    become green; a parity rc0 is reported with its actual skipped/warning rows.
    """
    import template_catalog as tc
    from template_catalog import bindings as tb
    from vmp import bindings_gate, catalog_access
    from cdpm import catalog_bridge

    def forget_readers():
        catalog_access.forget()
        catalog_bridge.forget()
        tc.forget_current()

    environment_before = os.environ.get("CREW_TEMPLATE_CATALOG")

    target = Path(state.folder).resolve()
    temporary = Path(tmp_path).resolve()
    assert target.is_relative_to(temporary), "Only the caller's isolated TEST catalogue is permitted"
    assert target != ROOT / "catalog" and not target.is_relative_to(ROOT / "catalog-integration")
    evidence = temporary / "postrelease"
    assert not evidence.is_relative_to(target)
    evidence.mkdir(exist_ok=False)
    cat = tc.load(target)
    assert cat.version() == "0.7.0"
    assert (target / "releases/0.7.0/support.json").is_file()
    assert (target / "support.json").read_bytes() == (target / "releases/0.7.0/support.json").read_bytes()
    raw_paths = [Path(state.settings.cosmos_bindings), Path(state.settings.cdpm_bindings)]
    assert all(path.resolve().is_relative_to(temporary) for path in raw_paths)
    before = _census(target)
    before_raw = {str(p): _sha(p) for p in raw_paths}
    before_body = tc.canonical_bytes(cat.body())
    before_fp = cat.fingerprint()
    sources = [p for p in sorted(SOURCE_TOOLS.rglob("*")) if p.is_file()
               and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    assert all((SOURCE_TOOLS / name).is_file() for name in ("build_from_code.py", "parity.py", "catalog_seed.json"))
    source_hashes = {p.relative_to(ROOT).as_posix(): _sha(p) for p in sources}
    report = {"schema": "a06-postrelease-checks/1", "scope": "isolated TEST postrelease only",
              "catalog": {"version": cat.version(), "fingerprint": before_fp, "path": str(target)},
              "tool_source": "actual reviewed draft.6 tools; independent transition witnesses from draft.3",
              "sources": source_hashes, "target_tools_existed": (target / "tools").is_dir(),
              "before": {"catalog_files": before, "raw_bindings": before_raw},
              "steps": {}, "errors": []}
    working = temporary / "postrelease-working/catalog"
    assert not working.exists() and not working.is_relative_to(target)
    snapshot = target / "releases/0.7.0"
    shutil.copytree(snapshot, working)
    assert _census(working) == _census(snapshot), "Working deployment differs from actual release snapshot"
    assert not (working / "tools").exists()
    shutil.copytree(SOURCE_TOOLS, working / "tools",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    assert _census(working / "tools") == {p.relative_to(SOURCE_TOOLS).as_posix(): _sha(p) for p in sources}
    witnesses = {}
    for name in ("executors_legacy.json", "families_order.json"):
        source = TRANSITION_WITNESSES / name
        destination = working / "transition" / name
        assert source.is_file()
        before_sha = _sha(destination) if destination.is_file() else None
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        assert _sha(destination) == _sha(source)
        witnesses["transition/" + name] = {
            "source": source.relative_to(ROOT).as_posix(), "source_sha256": _sha(source),
            "working_before_sha256": before_sha, "installed_sha256": _sha(destination),
            "scope": "noncanonical deployment witness, not changed catalogue body or support"}
    working_before = _census(working)
    workcat = tc.load(working)
    assert workcat.version() == cat.version() and workcat.fingerprint() == before_fp
    assert tc.canonical_bytes(workcat.body()) == before_body
    report["working_deployment"] = {"path": str(working), "copied_from": str(snapshot),
        "installed_tools": _census(working / "tools"), "before": working_before,
        "transition_witnesses": witnesses,
        "reader_stamp_before": {"version": workcat.version(), "fingerprint": workcat.fingerprint()}}
    _dump(evidence / "before.json", report["before"])

    def invoke(name, function, argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        row = {"argv": argv}
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                row["rc"] = int(function(argv))
            except (Exception, SystemExit) as exc:
                row.update(rc=None, exception={"type": type(exc).__name__, "message": str(exc)})
        for suffix, text in (("stdout", stdout.getvalue()), ("stderr", stderr.getvalue())):
            path = evidence / (name + "." + suffix + ".txt")
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
            row[suffix] = {"path": str(path), "sha256": _sha(path)}
        report["steps"][name] = row
        return row["rc"]

    try:
        # Import the real source modules, not relocated copies or replaced
        # functions. This scope restores the global module names afterwards.
        with monkeypatch.context() as patch:
            patch.syspath_prepend(str(SOURCE_TOOLS))
            patch.setenv("CREW_TEMPLATE_CATALOG", str(working))
            forget_readers()
            reader_stamps = {}
            for name, reader in (("template_catalog", lambda: tc.current(working)),
                                 ("COSMOS", catalog_access.current),
                                 ("CDPM", catalog_bridge.catalog)):
                read = reader()
                assert Path(read.path).resolve() == working, (name, read.path, str(working))
                assert read.version() == "0.7.0" and read.fingerprint() == before_fp
                reader_stamps[name] = {"path": str(Path(read.path).resolve()),
                                      "version": read.version(), "fingerprint": read.fingerprint()}
            report["working_deployment"]["actual_reader_stamps"] = reader_stamps

            def load(name, filename):
                spec = importlib.util.spec_from_file_location(name, SOURCE_TOOLS / filename)
                assert spec is not None and spec.loader is not None
                module = importlib.util.module_from_spec(spec)
                patch.setitem(sys.modules, name, module)
                spec.loader.exec_module(module)
                return module

            bfc = load("build_from_code", "build_from_code.py")
            parity = load("a06_postrelease_actual_parity", "parity.py")
            assert bfc.REPO.resolve() == ROOT and parity.bfc is bfc
            # This is the protected original, NOT the mutable deployment.
            assert not (target / "tools").exists()
            refused = invoke("original-no-tools-refusal", bfc.main, ["--refresh", "--out", str(target)])
            assert refused == bfc.GUARD_RC == 3
            assert _census(target) == before, "Guarded original catalogue changed"
            checks = ["--refresh", "--check", "--out", str(working)]
            initial = invoke("derived-before-check", bfc.main, checks)
            assert _census(working) == working_before, "Initial --check changed catalogue files"
            report["initial_derived_check_rc"] = initial
            invoke("derived-refresh", bfc.main, ["--refresh", "--out", str(working)])
            invoke("derived-after-check", bfc.main, checks)
            invoke("rebuild-from-catalog", bfc.main,
                   ["--check", "--from-catalog", "--out", str(working)])

            # The actual gate uses the explicitly loaded target and frozen raw
            # documents. Do not use in-memory regenerated bindings as a witness.
            fresh = tc.load(working)
            docs = [tb.load_bindings(p) for p in raw_paths]
            binding_problems = tb.check_bindings(fresh, *docs)
            binding_report = bindings_gate.gate(fresh, *docs)
            report["bindings_gate"] = {
                "ok": binding_report.ok,
                "errors": list(binding_report.errors), "notes": list(binding_report.notes),
                "products": list(binding_report.products), "matrix": binding_report.matrix,
                "catalog_binding_errors": [str(p) for p in binding_problems if p.severity == "error"],
                "catalog_binding_warnings": [str(p) for p in binding_problems if p.severity != "error"],
            }
            _dump(evidence / "bindings-gate.json", report["bindings_gate"])
            parity_file = evidence / "parity.json"
            rc = invoke("parity", parity.main, ["--catalog", str(working), "--bindings",
                        str(raw_paths[0]), "--json", str(parity_file)])
            if parity_file.is_file():
                doc = json.loads(parity_file.read_bytes())
                report["parity"] = {"rc": rc, "file": str(parity_file), "sha256": _sha(parity_file),
                    "counts": doc.get("counts"),
                    "skipped": {k: v for k, v in doc.get("rules", {}).items() if v.get("ok") is None},
                    "warnings": {k: v for k, v in doc.get("rules", {}).items() if v.get("warning")},
                    "claim": "Zero red is not all rules judged; skipped and warnings retained"}
                if rc != doc.get("rc") or rc != 0 or doc.get("counts", {}).get("red") != 0:
                    report["errors"].append("Actual parity failed or was unjudged; see primary parity.json")
            else:
                report["errors"].append("Actual parity produced no primary JSON")
            for name in ("derived-refresh", "derived-after-check", "rebuild-from-catalog"):
                if report["steps"][name]["rc"] != 0:
                    report["errors"].append(name + " did not return 0")
            if not binding_report.ok or report["bindings_gate"]["catalog_binding_errors"]:
                report["errors"].append("Actual bindings gate failed")
    except (Exception, SystemExit) as exc:
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        report["exception_traceback"] = traceback.format_exc()
    finally:
        # The monkeypatch context has restored the real environment; discard
        # the working-copy caches before returning to native exports on original.
        forget_readers()
        if os.environ.get("CREW_TEMPLATE_CATALOG") != environment_before:
            report["errors"].append("Reader environment was not restored")
        after = _census(target)
        raw_after = {str(p): _sha(p) for p in raw_paths}
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        report["after"] = {"catalog_files": after, "raw_bindings": raw_after,
                           "changed_catalog_files": changed}
        permitted = {"tools/catalog_seed.json", "transition/step4_additions.json"}
        if changed:
            report["errors"].append("Protected original catalogue files changed: " + repr(changed))
        working_after = _census(working)
        workchanged = sorted(k for k in set(working_before) | set(working_after)
                             if working_before.get(k) != working_after.get(k))
        report["working_deployment"].update(after=working_after, changed_files=workchanged)
        if not set(workchanged) <= permitted:
            report["errors"].append("Non-derived working files changed: " + repr(sorted(set(workchanged) - permitted)))
        if raw_after != before_raw:
            report["errors"].append("Approved raw bindings changed")
        try:
            final = tc.load(target)
            if final.version() != "0.7.0" or final.fingerprint() != before_fp or tc.canonical_bytes(final.body()) != before_body:
                report["errors"].append("Canonical catalogue body, fingerprint or release version changed")
            final_work = tc.load(working)
            report["working_deployment"]["reader_stamp_after"] = {
                "version": final_work.version(), "fingerprint": final_work.fingerprint()}
            if final_work.version() != "0.7.0" or final_work.fingerprint() != before_fp or tc.canonical_bytes(final_work.body()) != before_body:
                report["errors"].append("Working catalogue differs from real released body")
        except Exception as exc:
            report["errors"].append("Postcheck catalogue unreadable: " + str(exc))
        if {p.relative_to(ROOT).as_posix(): _sha(p) for p in sources} != source_hashes:
            report["errors"].append("Reviewed source tools changed")
        if any(_sha(ROOT / row["source"]) != row["source_sha256"] for row in witnesses.values()):
            report["errors"].append("Source transition witnesses changed")
        report["ok"] = not report["errors"]
        _dump(evidence / "summary.json", report)
    assert report["ok"], "Actual postrelease checks failed; retained " + str(evidence / "summary.json") + ": " + "; ".join(report["errors"])
    return report
