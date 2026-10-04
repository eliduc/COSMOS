"""РВ-143 D: профиль выпуска v2 — по платформам, а не по машине.

Синтетика без сети и без запуска чего-либо: интерпретаторы, venv, pip и
winget подменены записывающим `run`; настоящий pip-прогон замка — отдельной
пробой на колёсах (сырьё в канале), не здесь."""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import install_cosmos as ic
import installer_release as ir
from test_installer_exact_release import case, encode, sha  # noqa: F401

PLAT = ic._platform_key()
LOCK = ("# synthetic lock\n"
        "fixture-only==1.0 --hash=sha256:" + "a" * 64 + "\n"
        "other-pkg==2.0.1 --hash=sha256:" + "b" * 64 + " --hash=sha256:" + "c" * 64 + "\n").encode()
LOCK_PINS = {"fixture-only": "1.0", "other-pkg": "2.0.1"}


class FakeMachine:
    """Records every command; plays CPython 3.14 / venv / pip / winget."""

    def __init__(self, plat=PLAT, distributions=None, pip_rc=0, venv_ok=True):
        self.plat, self.calls = plat, []
        self.distributions = dict(LOCK_PINS if distributions is None else distributions)
        self.pip_rc, self.venv_ok = pip_rc, venv_ok

    def facts(self, exe, venv):
        system, machine = ic.RUNTIME_PLATFORMS[self.plat]["system"], ic.RUNTIME_PLATFORMS[self.plat]["machine"]
        return {"implementation": "CPython", "version": "3.14.4", "system": system,
                "machine": machine, "executable": str(exe), "executable_sha256": "d" * 64,
                "venv": venv, "distributions": dict(self.distributions, pip="26.1") if venv else {}}

    def __call__(self, cmd, **kw):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        if cmd[1:3] == ["-m", "venv"]:
            vpy = Path(cmd[3]) / ic.RUNTIME_PLATFORMS[self.plat]["venv_python"]
            vpy.parent.mkdir(parents=True)
            vpy.write_bytes(b"venv python")
            return SimpleNamespace(returncode=0 if self.venv_ok else 1, stdout="", stderr="")
        if cmd[1:4] == ["-m", "pip", "install"]:
            return SimpleNamespace(returncode=self.pip_rc, stdout="", stderr="")
        if cmd[1:3] == ["-I", "-c"]:
            venv = "venv" in Path(cmd[0]).parts
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.facts(cmd[0], venv)), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


@pytest.fixture
def v2(case, monkeypatch):  # noqa: F811
    bundle = case["bundle"]
    (bundle / "locks").mkdir()
    (bundle / ("locks/requirements-lock-%s.txt" % PLAT)).write_bytes(LOCK)
    old = case["profile"]
    profile = {"schema": ic.EXACT_PROFILE_SCHEMA_V2, "qualification_id": "synthetic-v2",
               "manifest": old["manifest"], "artifacts": old["artifacts"],
               "examples": old["examples"],
               "platforms": {PLAT: {"python": "3.14", "java": "21",
                                    "lock": {"path": "locks/requirements-lock-%s.txt" % PLAT,
                                             "sha256": sha(LOCK)}}}}
    machine = FakeMachine()
    base_python = case["root"] / "python314" / "python.exe"
    base_python.parent.mkdir()
    base_python.write_bytes(b"base python")
    monkeypatch.setattr(ic, "_python_candidates", lambda plat, run=None: [base_python])
    monkeypatch.setattr(ic, "_jdk_candidates", lambda plat: [case["java"]])
    case.update(profile=profile, machine=machine, install_root=case["root"] / "cosmos")
    return case


def save_v2(c):
    c["profile_path"].write_bytes(encode(c["profile"]))
    c["site_path"].write_bytes(encode(c["site"]))
    return sha(encode(c["profile"]))


def install(c, **kw):
    return ic.prepare_release_install(c["profile_path"], save_v2(c), c["bundle"], c["site_path"],
                                      c["install_root"], run=c["machine"], **kw)


def test_release_install_builds_venv_from_lock_and_pins_this_machine(v2):
    receipt = install(v2)
    root = v2["install_root"]
    assert sorted(p.name for p in root.iterdir()) == ["site", "venv"]
    site = root / "site"
    assert receipt["state"] == "prepared-not-started" and receipt["activated"] is False
    assert "--require-hashes" in receipt["runtime_check"]
    pip = next(c for c in v2["machine"].calls if c[1:4] == ["-m", "pip", "install"])
    assert {"--no-deps", "--only-binary=:all:", "--require-hashes"} <= set(pip)
    assert pip[-1].endswith("requirements-lock-%s.txt" % PLAT)
    config = json.loads((site / "exact-launch.json").read_bytes())
    vpy = root / "venv" / ic.RUNTIME_PLATFORMS[PLAT]["venv_python"]
    assert config["python"] == str(vpy)
    assert all(config["services"][s]["argv"][0] == str(vpy) for s in ("webapp", "cdpm", "catalog"))
    java = v2["java"] / ic.RUNTIME_PLATFORMS[PLAT]["java"]
    assert config["java_exe"] == str(java) and config["services"]["solver"]["argv"][0] == str(java)
    assert config["runtime"]["python"]["distributions"] == dict(LOCK_PINS, pip="26.1")
    assert config["runtime"]["python"]["version"] == "3.14.4"
    assert config["runtime"]["java"]["version"] == "21.0.8"
    assert (site / "exact-lock.txt").read_bytes() == LOCK
    assert "no executable bit" in (site / "EXACT-INSTALL.txt").read_text()
    assert str(vpy) in (site / "EXACT-INSTALL.txt").read_text()
    assert not any(c[0] in ("winget", "sudo") for c in v2["machine"].calls)


@pytest.mark.parametrize("damage", ["lock-hash", "lock-line", "platform", "python", "java"])
def test_bad_profile_or_lock_refused_before_any_output(v2, damage):
    lock = v2["bundle"] / ("locks/requirements-lock-%s.txt" % PLAT)
    spec = v2["profile"]["platforms"][PLAT]
    if damage == "lock-hash":
        lock.write_bytes(LOCK + b"extra==1 --hash=sha256:" + b"e" * 64 + b"\n")
    elif damage == "lock-line":
        lock.write_bytes(b"fixture-only==1.0\n")
        spec["lock"]["sha256"] = sha(b"fixture-only==1.0\n")
    elif damage == "platform":
        v2["profile"]["platforms"] = {"Plan9-mips": spec}
    elif damage == "python":
        spec["python"] = "3.13"
    else:
        spec["java"] = "17"
    with pytest.raises(ir.ReleaseError):
        install(v2)
    assert not v2["install_root"].exists()
    assert not any(c[1:3] == ["-m", "venv"] for c in v2["machine"].calls)


def test_profile_without_this_platform_is_refused(v2, monkeypatch):
    other = "Linux-x86_64" if PLAT != "Linux-x86_64" else "Windows-AMD64"
    monkeypatch.setattr(ic, "_platform_key", lambda *a: other)
    with pytest.raises(ir.ReleaseError, match="unsupported-runtime-platform"):
        install(v2)
    assert not v2["install_root"].exists()


def test_venv_that_differs_from_lock_is_refused_and_own_venv_removed(v2):
    v2["machine"].distributions = dict(LOCK_PINS, intruder="9.9")
    with pytest.raises(ir.ReleaseError, match="venv-distributions-differ-from-lock"):
        install(v2)
    assert not v2["install_root"].exists()


def test_pip_hash_failure_is_refused_and_nothing_remains(v2):
    v2["machine"].pip_rc = 1
    with pytest.raises(ir.ReleaseError, match="venv-lock-install"):
        install(v2)
    assert not v2["install_root"].exists()


def test_existing_root_is_never_touched(v2):
    v2["install_root"].mkdir()
    (v2["install_root"] / "keep.txt").write_text("user file")
    with pytest.raises(ir.ReleaseError, match="destination-exists"):
        install(v2)
    assert (v2["install_root"] / "keep.txt").read_text() == "user file"


def test_probe_reports_runtime_and_writes_nothing(v2):
    got = install(v2, probe=True)
    assert got["state"] == "probe-only" and got["platform"] == PLAT
    assert got["python"] and got["jdk"] == str(v2["java"])
    assert not v2["install_root"].exists()


def test_windows_bootstrap_runs_winget_only_with_consent(monkeypatch):
    machine = FakeMachine(plat="Windows-AMD64")
    monkeypatch.setattr(ic, "find_release_runtime", lambda plat, run=None: (None, None))
    with pytest.raises(ic.RuntimeRefused) as refused:
        ic.bootstrap_runtime("Windows-AMD64", allow_install=False, run=machine)
    assert machine.calls == []
    assert "Python.Python.3.14" in str(refused.value) and "Temurin.21" in str(refused.value)
    with pytest.raises(ic.RuntimeRefused):
        ic.bootstrap_runtime("Windows-AMD64", allow_install=True, run=machine)
    assert machine.calls == [ic.WINGET_PYTHON, ic.WINGET_JDK]


def test_linux_never_calls_sudo_and_says_what_to_install(monkeypatch):
    machine = FakeMachine(plat="Linux-x86_64")
    monkeypatch.setattr(ic, "find_release_runtime", lambda plat, run=None: (None, Path("/jdk")))
    with pytest.raises(ic.RuntimeRefused) as refused:
        ic.bootstrap_runtime("Linux-x86_64", allow_install=True, run=machine)
    assert machine.calls == []
    words = str(refused.value)
    assert "CPython 3.14" in words and "JDK" not in words.split("on Linux")[0]
    assert "python3.14-venv" in words and "never calls sudo" in words
    assert "Debian 13" in words and "uv python install 3.14" in words


def test_linux_candidates_include_uv_location(monkeypatch, tmp_path):
    uv_python = tmp_path / ".local/bin/python3.14"
    uv_python.parent.mkdir(parents=True)
    uv_python.write_bytes(b"python")
    monkeypatch.setattr(ic.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(ic.shutil, "which", lambda name: None)
    assert uv_python in ic._python_candidates("Linux-x86_64")


def test_java_is_found_by_release_file_and_os_specific_name(tmp_path):
    home = tmp_path / "jdk"
    (home / "bin").mkdir(parents=True)
    (home / "release").write_bytes(b'JAVA_VERSION="21.0.11"\n')
    (home / "bin/java").write_bytes(b"linux java")
    assert ic._java_facts(home, "Windows-AMD64") is None
    linux = ic._java_facts(home, "Linux-x86_64")
    assert linux["version"] == "21.0.11" and linux["executable"].endswith("java")
    (home / "release").write_bytes(b'JAVA_VERSION="17.0.2"\n')
    assert ic._java_facts(home, "Linux-x86_64") is None


@pytest.mark.parametrize("system,machine,key", [
    ("Windows", "AMD64", "Windows-AMD64"), ("Windows", "x86_64", "Windows-AMD64"),
    ("Linux", "x86_64", "Linux-x86_64"), ("Linux", "AMD64", "Linux-x86_64"),
    ("Linux", "aarch64", "Linux-aarch64")])
def test_platform_key_is_normalized(system, machine, key):
    assert ic._platform_key(system, machine) == key


def test_python_314_only(v2):
    machine = FakeMachine()
    facts = machine.facts("python", False)
    assert ic._python_ok(facts, PLAT)
    assert not ic._python_ok(dict(facts, version="3.13.9"), PLAT)
    assert not ic._python_ok(dict(facts, implementation="PyPy"), PLAT)
    assert not ic._python_ok(None, PLAT)


def test_committed_locks_parse_and_carry_hashes_for_every_line():
    root = Path(__file__).resolve().parents[1] / "locks"
    for plat in ic.RUNTIME_PLATFORMS:
        raw = (root / ("requirements-lock-%s.txt" % plat)).read_bytes()
        pins = ic._parse_lock(raw)
        assert {"fastapi", "uvicorn", "ortools", "numpy"} <= set(pins)
        assert all(" --hash=sha256:" in line for line in raw.decode().splitlines()
                   if line and not line.startswith("#"))
