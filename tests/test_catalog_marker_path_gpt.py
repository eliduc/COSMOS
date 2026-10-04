"""Offline marker-path transport controls; never read the private policy.

The subprocess is a stub, but it calls the real L11 and the real catalogue
validator on an in-memory catalogue with fictional record prose. No actual
release, live parity command, HTTP, JVM or catalogue write occurs.
"""
import copy
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

import template_catalog as tc
from catalog_service import markers, releases
from catalog_service.settings import Settings
from tools import customer_markers as cm


ROOT = Path(__file__).resolve().parents[1]
FICTIONAL = "FictionalZyqfural"
OTHER = "FictionalVexnuro"


@pytest.fixture(autouse=True)
def isolated_policy(monkeypatch, tmp_path):
    monkeypatch.delenv(cm.MARKERS_ENV, raising=False)
    monkeypatch.delenv("CUSTOMER_MARKERS_ENFORCE_REPO", raising=False)
    monkeypatch.setattr(cm, "MARKERS_FILE", tmp_path / "absent-default.json")

    def no_network(*args, **kwargs):
        raise AssertionError("Network is outside this offline marker test")
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket, "getaddrinfo", no_network)


def policy(tmp_path, name="chosen.json", value=FICTIONAL):
    path = tmp_path / name
    path.write_text(json.dumps({"checks": [{"kind": "token", "values": [value]}]}), encoding="utf-8")
    return path


def settings(tmp_path, path=None):
    return Settings(catalog_dir=ROOT / "catalog", work_dir=tmp_path / "work",
                    actor="owner", markers_file=path)


@pytest.fixture
def real_parity(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "catalog/tools"))
    name = "_catalog_marker_path_gpt_parity"
    spec = importlib.util.spec_from_file_location(name, ROOT / "catalog/tools/parity.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def in_memory_catalog(note):
    catalog = copy.deepcopy(tc.load(ROOT / "catalog"))
    # A02 scans record prose, not header notes. This independent in-memory
    # draft has a correctly recomputed fingerprint; original worlds/oracles,
    # on-disk catalogue and historical releases are never changed.
    assert catalog._docs["families"]["families"][0] is catalog._families[0]
    catalog._families[0]["title"] = note
    catalog._fingerprint = None
    for header in (catalog.header, catalog._docs["header"]):
        header["version"] = catalog.version().split("-draft.")[0] + "-draft.999"
        header["fingerprint"] = catalog.fingerprint()
    return catalog


def parity_call(monkeypatch, real_parity, configured, note):
    catalog = in_memory_catalog(note)
    calls = []
    monkeypatch.setattr(releases, "repo_of", lambda folder: ROOT)
    monkeypatch.setattr(releases, "parity_python", lambda repo: "never-started-python")

    def run_stub(command, **kwargs):
        assert command[0] == "never-started-python"
        assert kwargs["cwd"] == str(ROOT)
        assert kwargs["env"]["CREW_TEMPLATE_CATALOG"] == str(configured.catalog_dir)
        assert kwargs["env"]["PYTHONUTF8"] == "1"
        child_path = kwargs["env"].get(cm.MARKERS_ENV)
        calls.append(child_path)
        with monkeypatch.context() as child:
            if child_path is None:
                child.delenv(cm.MARKERS_ENV, raising=False)
            else:
                child.setenv(cm.MARKERS_ENV, child_path)
            rule = real_parity.rule_l11(catalog)
        rc = 1 if rule.ok is False else 0
        output = Path(command[command.index("--json") + 1])
        output.write_text(json.dumps({
            "catalog": {"version": catalog.version(), "fingerprint": catalog.fingerprint()},
            "rules": {"L11": rule.as_dict()},
            "counts": {"rules": 1, "green": int(rule.ok is True), "red": int(rule.ok is False),
                       "skipped": int(rule.ok is None), "warning": int(rule.warning)},
            "words": rule.words,
        }), encoding="utf-8")
        return subprocess.CompletedProcess(command, rc, stdout="", stderr="")

    monkeypatch.setattr(releases.subprocess, "run", run_stub)
    verdict = releases.parity_verdict(configured)
    assert len(calls) == 1
    assert calls[0] == str(markers.markers_path(configured).resolve())
    return verdict


def test_shared_path_priority_and_legacy_default(monkeypatch, tmp_path):
    default = cm.MARKERS_FILE
    explicit = policy(tmp_path)
    ambient = policy(tmp_path, "ambient.json", OTHER)
    assert cm.markers_path() == default
    assert markers.markers_path(settings(tmp_path)) == default
    assert cm.load_checks() is None  # Historical optional default, not evidence.
    monkeypatch.setenv(cm.MARKERS_ENV, str(ambient))
    assert cm.markers_path() == ambient
    assert markers.markers_path(settings(tmp_path)) == ambient
    assert cm.markers_path(explicit) == explicit
    assert markers.markers_path(settings(tmp_path, explicit)) == explicit
    assert cm.load_checks(explicit)["checks"][0]["values"] == [FICTIONAL]
    assert cm.load_checks()["checks"][0]["values"] == [OTHER]


def test_blank_environment_keeps_the_legacy_default(monkeypatch, tmp_path):
    monkeypatch.setenv(cm.MARKERS_ENV, "  ")
    assert cm.markers_path() == cm.MARKERS_FILE
    assert markers.markers_path(settings(tmp_path)) == cm.MARKERS_FILE
    assert cm.load_checks() is None


def test_settings_overrides_ambient_in_actual_l11(monkeypatch, tmp_path, real_parity):
    chosen = policy(tmp_path)
    ambient = policy(tmp_path, "ambient.json", OTHER)
    monkeypatch.setenv(cm.MARKERS_ENV, str(ambient))
    note = "Fictional marker test: " + FICTIONAL
    # Causal counter-control: retaining the ambient path would be green.
    assert real_parity.rule_l11(in_memory_catalog(note)).ok is True
    cfg = settings(tmp_path, chosen)
    terms, reason = markers.read(cfg)
    assert terms and reason is None
    assert any(pattern.search(FICTIONAL) for pattern in terms)
    verdict = parity_call(monkeypatch, real_parity, cfg, note)
    assert verdict["ok"] is False and verdict["rc"] == 1
    assert verdict["verdict"]["rules"]["L11"]["ok"] is False
    assert releases._check_parity(verdict).code == "parity-red"
    assert cm.markers_path() == ambient  # No mutation of the parent's env.


@pytest.mark.parametrize("source", ["settings", "environment", "default", "relative-settings"])
def test_healthy_l11_uses_the_same_one_real_fixture_check(monkeypatch, tmp_path, real_parity, source):
    path = policy(tmp_path)
    if source == "environment":
        monkeypatch.setenv(cm.MARKERS_ENV, str(path))
        selected = None
    elif source == "default":
        monkeypatch.setattr(cm, "MARKERS_FILE", path)
        selected = None
    elif source == "relative-settings":
        monkeypatch.chdir(tmp_path)
        selected = Path(path.name)
    else:
        selected = path
    cfg = settings(tmp_path, selected)
    verdict = parity_call(monkeypatch, real_parity, cfg, "Clean fictional catalogue note")
    assert verdict["ok"] is True and verdict["warnings"] == []
    assert verdict["verdict"]["rules"]["L11"]["numbers"]["markers"] == 1
    assert releases._check_parity(verdict) is None


@pytest.mark.parametrize("source", ["settings", "environment", "default"])
@pytest.mark.parametrize("directory", [False, True])
def test_unavailable_selected_policy_is_not_a_green_zero(monkeypatch, tmp_path, real_parity, source, directory):
    missing = cm.MARKERS_FILE if source == "default" else tmp_path / "missing-selected.json"
    if directory:
        missing.mkdir()
    ambient = policy(tmp_path, "ambient.json", OTHER)
    if source != "default":
        monkeypatch.setenv(cm.MARKERS_ENV, str(missing if source == "environment" else ambient))
    cfg = settings(tmp_path, missing if source == "settings" else None)
    terms, reason = markers.read(cfg)
    assert terms is None and reason
    assert releases._check_markers(cfg, ()) is not None
    verdict = parity_call(monkeypatch, real_parity, cfg, "Clean fictional catalogue note")
    rule = verdict["verdict"]["rules"]["L11"]
    assert rule["ok"] is None and rule["warning"] is True
    assert verdict["warnings"]
    assert releases._check_parity(verdict).code == "parity-unconfirmed"


BAD_POLICIES = [
    "", "{", "null", "[]", "{}", '{"checks": []}',
    '{"checks": [{"kind": "token", "values": []}]}',
    '{"checks": [{"kind": "token", "values": [""]}]}',
    '{"checks": [{"kind": "unknown", "values": ["fictional"]}]}',
    '{"checks": [{"kind": "regex", "values": ["("]}]}',
    '{"checks": [{"kind": "token", "values": ["ok"]}, {"kind": "unknown", "values": ["bad"]}]}',
    '{"checks": [{"kind": "token", "values": ["ok"], "context_allow": ["["]}]}',
    '{"checks": [], "checks": [{"kind": "token", "values": ["ok"]}]}',
]


@pytest.mark.parametrize("raw", BAD_POLICIES)
def test_invalid_or_empty_explicit_policy_refuses_service_and_l11(monkeypatch, tmp_path, real_parity, raw):
    path = tmp_path / "invalid-selected.json"
    path.write_text(raw, encoding="utf-8")
    monkeypatch.setenv(cm.MARKERS_ENV, str(path))
    with pytest.raises(ValueError):
        cm.load_checks()
    cfg = settings(tmp_path, path)
    terms, reason = markers.read(cfg)
    assert terms is None and reason
    verdict = parity_call(monkeypatch, real_parity, cfg, "Clean fictional catalogue note")
    assert verdict["verdict"]["rules"]["L11"]["ok"] is None
    assert verdict["warnings"] and releases._check_parity(verdict).code == "parity-unconfirmed"


def test_legacy_markers_key_reaches_all_consumers(monkeypatch, tmp_path, real_parity):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"markers": [{"kind": "token", "values": [FICTIONAL]}]}), encoding="utf-8")
    monkeypatch.setenv(cm.MARKERS_ENV, str(path))
    assert cm.load_checks()["checks"] == cm.load_checks()["markers"]
    verdict = parity_call(monkeypatch, real_parity, settings(tmp_path, path), FICTIONAL)
    assert verdict["ok"] is False


@pytest.mark.parametrize("invalid", [False, True])
def test_lint_main_explicit_failure_is_diagnostic_not_pass(monkeypatch, tmp_path, capsys, invalid):
    path = tmp_path / "selected-missing.json"
    if invalid:
        path.write_text('{"checks": []}', encoding="utf-8")
    monkeypatch.setenv(cm.MARKERS_ENV, str(path))
    monkeypatch.setattr(cm, "tracked_files", lambda repo: pytest.fail("failed policy must not scan"))
    assert cm.main(["--repo", str(tmp_path)]) == 2
    output = capsys.readouterr().out
    assert str(path) in output and "линт не выполнен" in output


def test_lint_main_healthy_and_finding_with_selected_policy(monkeypatch, tmp_path, capsys):
    selected = policy(tmp_path)
    monkeypatch.setenv(cm.MARKERS_ENV, str(selected))
    sample = tmp_path / "sample.txt"
    sample.write_text("clean fictional text", encoding="utf-8")
    monkeypatch.setattr(cm, "tracked_files", lambda repo: [sample.name])
    assert cm.main(["--repo", str(tmp_path)]) == 0
    sample.write_text(FICTIONAL, encoding="utf-8")
    assert cm.main(["--repo", str(tmp_path)]) == 1
    capsys.readouterr()


def test_lint_diagnostic_missing_default_uses_shared_path(tmp_path, capsys):
    assert cm.main(["--repo", str(tmp_path)]) == 0
    assert str(cm.markers_path()) in capsys.readouterr().out
