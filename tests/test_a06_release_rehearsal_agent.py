"""Isolated A06 act rehearsal, never a release/approval of a real contour.

Run this file directly: its narrow runner permits ONLY pinned Git commands in
its new temporary root. No network, services, JVM, LLM or external parity runner.
The act fixtures omit tools/parity.py explicitly: unjudged is never green.
One post-act test then loads actual draft3 tools in-process, with temp outputs.
No stand outcome is fabricated. Optional two CDPM duty table tasks really run.
"""
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "catalog-integration" / "0.7.0-draft.3"
SOURCE_COMMIT = "89dd5ed3a5264ccb80030ad2cd9d114ba71db615"
GIT = Path("C:/Program Files/Git/cmd/git.exe")
GIT_SHA = "81ef35ae005ca9318018d18e3327578ce939fb99feaad6b2d7c8ab15f3de8db5"
FP = "dd8687d5f8574f2f3573b5a908a15c7c7bb8b1142991bb1297c2e445cbfaa02e"
ALLOWED_ARGS = {
    ("init", "--initial-branch=a06-rehearsal"),
    ("add", "--", "catalog", "template_catalog", "cosmos-bindings.json", "cdpm-bindings.json"),
    ("commit", "--no-gpg-sign", "-m", "A06 isolated rehearsal input"),
    ("rev-parse", "--show-toplevel"), ("rev-parse", "HEAD"),
    ("-c", "core.quotepath=false", "status", "--porcelain", "--", "catalog", "template_catalog"),
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(repo, *args):
    result = subprocess.run([str(GIT), *args], cwd=str(repo), text=True,
        encoding="utf-8", capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    import template_catalog as tc
    from catalog_service.settings import Settings
    from catalog_service.store import Store
    from catalog_service.journal import Journal
    from cdpm import catalog_bridge
    from vmp import catalog_access

    protected = [ROOT / "catalog", CANDIDATE]
    paths = [p for folder in protected for p in folder.rglob("*") if p.is_file()]
    paths += [ROOT / product / name for product in ("cdpm", "vmp")
              for name in ("bindings.json", "stand_results.json")]
    before = {p: sha(p) for p in paths}
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    for key in ("CREW_CDPM_RECORD_API", "CREW_TASK_INBOX", "CREW_CONTOUR",
                "CDPM_CATALOG_API_URL", "CDPM_CATALOG_TOKEN", "CATALOG_MACHINE_TOKEN",
                "CREW_TASK_CHANNEL_URL", "CREW_TASK_CHANNEL_TOKEN"):
        monkeypatch.setenv(key, "")
    for key in ("GIT_CONFIG_PARAMETERS", "CREW_BINDINGS_SEED", "COSMOS_TEST_CATALOG_GROWN",
                "CREW_VMP_TEMPLATE_BASE"):
        monkeypatch.delenv(key, raising=False)
    assert sha(GIT) == GIT_SHA
    monkeypatch.setenv("PATH", str(GIT.parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "3")
    for i, (key, val) in enumerate((("core.hooksPath", str(tmp_path / "empty-hooks")),
                                    ("maintenance.auto", "false"), ("gc.auto", "0"))):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{i}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{i}", val)
    for side in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{side}_NAME", "A06 test fixture, not product owner")
        monkeypatch.setenv(f"GIT_{side}_EMAIL", "a06-test@invalid.example")

    repo = tmp_path / "repo"
    repo.mkdir()
    folder = repo / "catalog"
    # Explicit incomplete deployment fixture; no parity verdict is invented.
    shutil.copytree(CANDIDATE, folder, ignore=shutil.ignore_patterns("tools", "__pycache__"))
    shutil.copytree(ROOT / "template_catalog", repo / "template_catalog",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    assert not (folder / "tools" / "parity.py").exists()
    marker = tmp_path / "synthetic-markers.json"
    marker.write_text(json.dumps({"checks": [{"name": "isolated fixture only",
        "kind": "token", "values": ["ФРЕГЕНОЛЁТ"]}]}), encoding="utf-8")
    settings = Settings(catalog_dir=folder, work_dir=tmp_path / "work", actor="owner",
        markers_file=marker, cosmos_bindings=repo / "cosmos-bindings.json",
        cdpm_bindings=repo / "cdpm-bindings.json", machine_token=None)
    store = Store(settings)
    for key, path in (("CREW_TEMPLATE_CATALOG", folder),
                      ("CREW_COSMOS_BINDINGS", settings.cosmos_bindings),
                      ("CREW_CDPM_BINDINGS", settings.cdpm_bindings),
                      ("CDPM_BINDINGS_FILE", settings.cdpm_bindings)):
        monkeypatch.setenv(key, str(path))
    catalog_bridge.forget()
    catalog_access.forget()
    cat = store.catalog()
    assert cat.fingerprint() == FP and cat.version() == "0.7.0-draft.3"
    assert not [p for p in cat.validate() if p.severity == "error"]
    yield SimpleNamespace(repo=repo, folder=folder, settings=settings, store=store,
                          cat=cat, journal=Journal(settings.journal_path))
    store.release_catalog_lock()
    catalog_bridge.forget()
    catalog_access.forget()
    tc.forget_current()
    assert {p: sha(p) for p in paths} == before


def build_inputs(state, *, tables=False):
    from cdpm import bindings_build as cb, stand_tables
    from vmp import bindings_build as vb
    candidate = copy.deepcopy(state.cat)
    candidate.header["version"] = "0.7.0"
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    evidence = None
    if tables:
        rows, sources = stand_tables.run_all(candidate, seed={},
            records=["families/duty_limits"], source="catalog")
        assert len(rows) == 2 and {r["result"] for r in rows} == {"passed"}
        assert {r["got"]["findings"] for r in rows} == {0, 2}
        evidence = stand_tables.results_doc(candidate, rows, tasks_source=sources,
                                            commit=SOURCE_COMMIT, at=at)
        assert stand_tables.stale_reason(evidence, candidate) is None
    cosmos = vb.build(candidate, commit=SOURCE_COMMIT, generated_at=at, stand_results=None)
    cdpm = cb.build(candidate, commit=SOURCE_COMMIT, generated_at=at, stand_results=evidence)
    state.settings.cosmos_bindings.write_bytes(vb.dump(cosmos).encode("utf-8"))
    state.settings.cdpm_bindings.write_bytes(cb.dump(cdpm).encode("utf-8"))
    assert all(not row["stand_verified"] for row in cosmos["bindings"])
    git(state.repo, "init", "--initial-branch=a06-rehearsal")
    git(state.repo, "add", "--", "catalog", "template_catalog", "cosmos-bindings.json", "cdpm-bindings.json")
    git(state.repo, "commit", "--no-gpg-sign", "-m", "A06 isolated rehearsal input")
    return evidence


def check_preconditions(state):
    from catalog_service import releases
    checked = releases.checks(state.store, version="0.7.0")
    bad = [r for r in checked["checks"] if not r["ok"]]
    assert len(checked["checks"]) == 12
    assert [(r["key"], r["code"]) for r in bad] == [("parity", "parity-unconfirmed")]
    assert checked["parity"]["ok"] is None and checked["confirm_parity_needed"]
    return checked


@pytest.mark.parametrize("tables", [False, True])
def test_real_act_only_approves_actual_local_evidence(prepared, tables, record_property):
    import template_catalog as tc
    from catalog_service import releases, executors
    from catalog_service.errors import ServiceError
    state = prepared
    evidence = build_inputs(state, tables=tables)
    check_preconditions(state)
    before = {p: p.read_bytes() for p in
              (state.settings.cosmos_bindings, state.settings.cdpm_bindings)}
    source_body = state.cat.body()
    with pytest.raises(ServiceError) as refused:
        releases.release(state.store, state.journal, version="0.7.0", note="TEST rehearsal")
    assert refused.value.code == "parity-unconfirmed"
    assert not (state.folder / "releases" / "0.7.0").exists()
    result = releases.release(state.store, state.journal, version="0.7.0",
        note="TEST ONLY: isolated rehearsal, not approval of a real contour", confirm_parity=True)
    assert result["parity"]["ok"] is None and result["confirmed_parity"]
    assert state.journal.last(op="release")["outcome"] == "warning"
    final = state.store.catalog()
    assert final.fingerprint() == FP and final.body() == source_body
    assert final.version() == "0.7.0"
    history = releases.releases_gate(final)
    assert history and all(row["ok"] for row in history), history
    from template_catalog import bindings as tb
    binding_docs = [tb.load_bindings(p) for p in before]
    assert not [p for p in tb.check_bindings(final, *binding_docs) if p.severity == "error"]
    from cdpm import catalog_bridge, support_manifest
    from test_catalog_release_binding_order_agent import _model
    from vmp import catalog_access
    assert catalog_bridge.stamp()["fingerprint"] == FP
    assert catalog_access.current().fingerprint() == FP
    model = _model(SimpleNamespace(cat=final))
    support_problems = support_manifest.readiness_problems(model)
    check_problems = [p for p in support_problems
                      if p.get("record") == "families/duty_limits"
                      and p.get("kind") == "check_template"]
    assert bool(check_problems) is (not tables)
    frozen = state.folder / "releases" / "0.7.0"
    assert tc.load(frozen).body() == source_body
    assert (frozen / "support.json").read_bytes() == (state.folder / "support.json").read_bytes()
    support = json.loads((frozen / "support.json").read_bytes())
    approved = [cell for cell in support["cells"] if cell["approved"]]
    assert [(c["product"], c["record_id"], c["kind"]) for c in approved] == (
        [("CDPM", "duty_limits", "check_template")] if tables else [])
    assert all(c["state"] != "есть" for c in support["cells"] if c["product"] == "COSMOS")
    if tables:
        assert approved[0]["bindings_file"]["sha256"] == sha(state.settings.cdpm_bindings)
        record_property("actual_local_table_evidence", json.dumps(evidence, ensure_ascii=False))
    assert {p: p.read_bytes() for p in before} == before
    record_property("test_act_git_commit", result["code"])
    record_property("test_act_parity", "unjudged; explicit fixture confirmation, NOT green")
    frozen_bytes = {p: p.read_bytes() for p in frozen.rglob("*") if p.is_file()}
    refreshed = executors.refresh_support(state.store, state.journal)
    assert all(c["approved"] is None for c in refreshed["cells"])
    assert any(p.get("record") == "families/duty_limits" and p.get("kind") == "check_template"
               for p in support_manifest.readiness_problems(model))
    assert {p: p.read_bytes() for p in before} == before
    assert {p: p.read_bytes() for p in frozen_bytes} == frozen_bytes


def test_dirty_reader_blocks_act_without_writing_catalogue(prepared):
    from catalog_service import releases
    from catalog_service.errors import ServiceError
    state = prepared
    build_inputs(state)
    check_preconditions(state)
    frozen = {p: p.read_bytes() for p in state.folder.rglob("*") if p.is_file()}
    reader = state.repo / "template_catalog" / "reader.py"
    reader.write_bytes(reader.read_bytes() + b"\n# isolated dirty-gate probe\n")
    checked = releases.checks(state.store, version="0.7.0")
    assert next(r for r in checked["checks"] if r["key"] == "git")["code"] == "git-dirty"
    with pytest.raises(ServiceError) as refused:
        releases.release(state.store, state.journal, version="0.7.0",
                         note="TEST refusal probe", confirm_parity=True)
    assert refused.value.code == "git-dirty"
    assert {p: p.read_bytes() for p in frozen} == frozen
    assert not (state.folder / "releases" / "0.7.0").exists()


def test_real_derived_refresh_and_inprocess_parity(prepared, monkeypatch, record_property, capsys):
    """Actual draft3 CLI functions, never a substituted release parity result.

    The test act below still explicitly has unjudged parity. Only after it do
    we install the copied derived carriers and separately judge the resulting
    temporary working catalogue with the actual source tools.
    """
    import template_catalog as tc
    from catalog_service import releases
    state = prepared
    build_inputs(state)
    result = releases.release(state.store, state.journal, version="0.7.0",
        note="TEST ONLY: postrelease derived rehearsal", confirm_parity=True)
    assert result["parity"]["ok"] is None
    shutil.copytree(CANDIDATE / "tools", state.folder / "tools",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    tools = CANDIDATE / "tools"
    monkeypatch.syspath_prepend(str(tools))

    def load(name, filename):
        spec = importlib.util.spec_from_file_location(name, tools / filename)
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, mod)
        spec.loader.exec_module(mod)
        return mod

    bfc = load("build_from_code", "build_from_code.py")
    parity = load("a06_actual_draft3_parity", "parity.py")
    assert bfc.REPO.resolve() == ROOT and parity.bfc is bfc
    witness_files = [ROOT / "tests/fixtures/registry_step1.json", ROOT / "vmp/bindings_seed.json"]
    from vmp import kotlin_tables as kt
    witness_files += [kt.KOTLIN_DIR / name for name in (kt.PROVIDER, kt.RUN_THRESHOLDS,
                     kt.LOGIC_MODEL, kt.LOGIC_FORMS, kt.TASK_PACKAGE, kt.TASK_MODES)]
    record_property("actual_parity_witness_files", json.dumps({
        p.relative_to(ROOT).as_posix(): sha(p) for p in witness_files}, sort_keys=True))
    from tools import customer_markers
    record_property("parity_marker_file_present", customer_markers.MARKERS_FILE.is_file())
    record_property("actual_tool_sources", json.dumps({str(p.relative_to(ROOT)): sha(p)
        for p in (tools / "build_from_code.py", tools / "parity.py", tools / "catalog_seed.json")},
        sort_keys=True))

    def hashes():
        return {p.relative_to(state.folder).as_posix(): sha(p)
                for p in state.folder.rglob("*") if p.is_file()}

    before = hashes()
    raw = {p: sha(p) for p in (state.settings.cosmos_bindings, state.settings.cdpm_bindings)}
    body = tc.load(state.folder).body()
    checks = ["--refresh", "--check", "--out", str(state.folder)]
    initial_rc = bfc.main(checks)
    record_property("initial_derived_check_rc", initial_rc)
    assert initial_rc in (0, 1)
    assert hashes() == before  # --check really is non-mutating.
    assert bfc.main(["--refresh", "--out", str(state.folder)]) == 0
    assert bfc.main(checks) == 0
    fresh = hashes()
    changed = sorted(k for k in set(before) | set(fresh) if before.get(k) != fresh.get(k))
    assert set(changed) <= {bfc.SEED_REL, bfc.DELTA_REL}
    record_property("derived_files_changed", json.dumps(changed))
    rebuild_rc = bfc.main(["--check", "--from-catalog", "--out", str(state.folder)])
    record_property("rebuild_from_catalog_rc", rebuild_rc)

    def judge(label):
        out = state.repo.parent / ("parity-" + label + ".json")
        rc = parity.main(["--catalog", str(state.folder), "--bindings",
                          str(state.settings.cosmos_bindings), "--json", str(out), "--quiet"])
        doc = json.loads(out.read_bytes())
        assert rc == doc["rc"]
        record_property("actual_parity_" + label, json.dumps(doc, ensure_ascii=False, sort_keys=True))
        return rc, doc

    healthy_rc, healthy = judge("refreshed")
    # Negative control only in the temporary derived carrier: no oracle or
    # critical gate is monkeypatched. A stale fingerprint must reach L23.
    delta = state.folder / bfc.DELTA_REL
    damaged = json.loads(delta.read_bytes())
    damaged["fingerprint"] = "0" * 64
    delta.write_text(json.dumps(damaged, ensure_ascii=False), encoding="utf-8")
    assert bfc.main(checks) == 1
    bad_rc, bad = judge("stale-control")
    assert bad_rc == 1 and bad["rules"]["L23"]["ok"] is False
    assert bfc.main(["--refresh", "--out", str(state.folder)]) == 0
    assert bfc.main(checks) == 0
    final_rc, final = judge("repaired")
    assert final == healthy and final_rc == healthy_rc
    assert hashes() == fresh
    assert {p: sha(p) for p in raw} == raw
    final_cat = tc.load(state.folder)
    assert final_cat.body() == body and final_cat.fingerprint() == FP
    assert final_cat.version() == "0.7.0"
    assert all(not c["approved"] for c in json.loads((state.folder / "support.json").read_bytes())["cells"])
    record_property("actual_derived_stdout", capsys.readouterr().out)
    loaded = {}
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if path and Path(path).is_file() and Path(path).resolve().is_relative_to(ROOT):
            p = Path(path).resolve()
            if name.startswith(("cdpm.", "vmp.", "template_catalog.", "catalog_service.")) \
                    or name in {"tools.customer_markers", "build_from_code", "a06_actual_draft3_parity"}:
                loaded[p.relative_to(ROOT).as_posix()] = sha(p)
    record_property("actual_local_module_sources", json.dumps(loaded, sort_keys=True))
    # Fail on actual product parity/rebuild failures, retain full JSON above.
    assert rebuild_rc == 0
    assert healthy_rc == 0 and healthy["counts"]["red"] == 0, healthy
    assert healthy["rules"]["L23"]["ok"] is True


def main():
    # This is deliberately not the broad suite or the no-process runner:
    # release git judges must execute, not be replaced with green functions.
    with tempfile.TemporaryDirectory(prefix="a06-release-rehearsal-") as base:
        base_path = Path(base).resolve()
        allowed, denied = [], []

        def audit(event, args):
            if event == "subprocess.Popen":
                executable, command, cwd, _env = args
                parts = list(command) if isinstance(command, (list, tuple)) else []
                if isinstance(command, str):
                    parts = next(([name, *rest] for name in ("git", str(GIT))
                                  for rest in ALLOWED_ARGS
                                  if command == subprocess.list2cmdline([name, *rest])), [])
                location = Path(cwd).resolve() if cwd else Path.cwd().resolve()
                exe = executable or (parts[0] if parts else "")
                resolved = Path(shutil.which(str(exe)) or str(exe)).resolve()
                if (resolved == GIT.resolve() and parts and tuple(parts[1:]) in ALLOWED_ARGS
                        and location.is_relative_to(base_path)):
                    allowed.append({"args": parts[1:], "cwd": str(location)})
                    return
            elif not (event in {"socket.connect", "socket.bind", "socket.getaddrinfo",
                                 "socket.sendto", "socket.sendmsg", "os.system",
                                 "os.posix_spawn", "os.exec", "os.spawn"}
                      or event.startswith("os.spawn")):
                return
            denied.append(event)
            raise RuntimeError("rehearsal guard denied " + event)

        sys.addaudithook(audit)
        os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        os.environ["COSMOS_SKIP_CONFIG_MIGRATION"] = "1"
        sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
        import socket
        from _pytest import junitxml
        junitxml.platform = SimpleNamespace(node=socket.gethostname)
        code = pytest.main(["--noconftest", "-p", "no:cacheprovider", "-o", "junit_family=xunit1",
            "--basetemp=" + str(base_path / "pytest"), str(Path(__file__).resolve()), *sys.argv[1:]])
        print(json.dumps({"exit": int(code), "git_children": len(allowed), "denied": denied}))
        return 90 if denied else int(code)


if __name__ == "__main__":
    raise SystemExit(main())
