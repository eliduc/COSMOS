"""РВ-143, доля COSMOS в установщике: решатель (порт одной константой,
адрес для COSMOS), версия продукта одним местом, CPLEX — найти
установленный, иначе CP-SAT без ошибки установки, Linux — проба
ensurepip и права из zip.

Ничего не скачивается и не ставится: пробы — на временных папках и
подменённом окружении."""
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import install_cosmos as ic                                      # noqa: E402
import _select_engines as se                                     # noqa: E402


@pytest.fixture()
def clean_cplex_env(monkeypatch, tmp_path):
    monkeypatch.delenv("CREW_CPLEX_EXE", raising=False)
    for k in list(os.environ):
        if k.startswith("CPLEX_STUDIO_DIR"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ic.shutil, "which", lambda name: None)
    monkeypatch.setattr(ic, "CPLEX_STUDIO_ROOTS", (tmp_path / "ILOG",))
    return tmp_path


def _fake_cplex(root: Path, studio: str) -> Path:
    exe = root / studio / Path(*ic.CPLEX_REL)
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    return exe


# ------------------------------------------------------------- решатель

def test_solver_port_one_constant_and_reserved():
    assert ic.SOLVER_URL == "http://127.0.0.1:%d" % ic.SOLVER_PORT
    assert ic.SOLVER_PORT in ic.RESERVED_PORTS
    src = (REPO / "install_cosmos.py").read_text(encoding="utf-8")
    # номер порта решателя — лишь в объявлении константы и в комментарии
    lines = [ln for ln in src.splitlines() if str(ic.SOLVER_PORT) in ln]
    assert all(ln.startswith("SOLVER_PORT =") or ln.lstrip().startswith("#")
               for ln in lines), lines


def test_product_version_one_place():
    assert ic.PRODUCT_VERSION == "1.4.10"
    src = (REPO / "install_cosmos.py").read_text(encoding="utf-8")
    assert src.count('"1.4.10"') == 1


# ----------------------------------------------------------------- CPLEX

def test_cplex_not_found_is_not_an_error(clean_cplex_env, tmp_path):
    s = ic.Survey(tmp_path / "target", ic.REPO_URL_DEFAULT)
    s.probe_cplex()
    p = s.get("CPLEX")
    assert p is not None and p.ok and p.value is None and not p.blocking
    assert "CP-SAT" in p.detail


def test_cplex_found_under_studio_root(clean_cplex_env):
    exe = _fake_cplex(clean_cplex_env / "ILOG", "CPLEX_Studio2211")
    assert ic.find_cplex() == str(exe)


def test_cplex_newest_studio_first(clean_cplex_env):
    _fake_cplex(clean_cplex_env / "ILOG", "CPLEX_Studio201")
    new = _fake_cplex(clean_cplex_env / "ILOG", "CPLEX_Studio222")
    assert ic.find_cplex() == str(new)


def test_cplex_explicit_setting_is_the_only_candidate(clean_cplex_env, monkeypatch):
    _fake_cplex(clean_cplex_env / "ILOG", "CPLEX_Studio222")
    monkeypatch.setenv("CREW_CPLEX_EXE", str(clean_cplex_env / "нет-такого" / "cplex"))
    # неверная явная настройка не подменяется другим экземпляром
    assert ic.find_cplex() is None


def test_product_cplex_roots_cover_this_os():
    roots = [str(r) for r in se._CPLEX_STUDIO_ROOTS]
    assert roots == ([r"C:\Program Files\IBM\ILOG"] if os.name == "nt"
                     else ["/opt/ibm/ILOG"])


# ----------------------------------------------------------------- Linux

def test_venv_without_ensurepip_stops_with_words(monkeypatch, tmp_path):
    monkeypatch.setattr(ic, "IS_WIN", False)
    monkeypatch.setitem(sys.modules, "ensurepip", None)   # import → ImportError
    s = ic.Survey(tmp_path / "target", ic.REPO_URL_DEFAULT)
    s.probe_python()
    p = s.get("модуль venv")
    assert p is not None and not p.ok and p.blocking
    assert "python3-venv" in p.detail


@pytest.mark.skipif(os.name == "nt", reason="права Unix")
def test_unpack_restores_exec_bit_from_zip(tmp_path):
    arc = tmp_path / "tool.zip"
    with zipfile.ZipFile(arc, "w") as zf:
        info = zipfile.ZipInfo("tool/bin/mvn")
        info.external_attr = (0o755 | stat.S_IFREG) << 16
        zf.writestr(info, "#!/bin/sh\n")
    ic.Installer._unpack(arc, tmp_path / "out")
    assert os.access(tmp_path / "out" / "tool" / "bin" / "mvn", os.X_OK)
