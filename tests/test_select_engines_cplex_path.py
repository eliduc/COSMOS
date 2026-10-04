# -*- coding: utf-8 -*-
"""Поиск cplex.exe движком отбора (_select_engines.cplex_exe).

09.09.2026 на контуре Test legion CPLEX стоял не в каталоге Community222
машины разработки, и движок отвечал «исполняемый файл не найден» по одному
зашитому пути. Порядок поиска: явная переменная CREW_CPLEX_EXE → переменные
установщика IBM CPLEX_STUDIO_DIR* → cplex в PATH → каталоги
C:\\Program Files\\IBM\\ILOG\\CPLEX_Studio*. Молчаливой подмены движка нет:
не нашли — отказ словами с перечнем того, где искали.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import _select_engines as se                                       # noqa: E402

EXE = "cplex.exe" if os.name == "nt" else "cplex"


def _clean(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("CPLEX_STUDIO_DIR") or k == "CREW_CPLEX_EXE":
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "нет-такого"))
    monkeypatch.setattr(se, "_CPLEX_STUDIO_ROOTS", (tmp_path / "ILOG",))
    # прежний зашитый путь — последний кандидат; на машине разработки он
    # существует, и без подмены отказ не воспроизвести
    monkeypatch.setattr(se, "_CPLEX_DEFAULT", str(tmp_path / "нет" / EXE))


def _fake_studio(root: Path, name: str) -> Path:
    exe = root / name / Path(*se._CPLEX_REL)
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(b"MZ")
    return exe


def test_явная_переменная_главнее_всего(monkeypatch, tmp_path):
    _clean(monkeypatch, tmp_path)
    exe = tmp_path / "мой" / EXE
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    monkeypatch.setenv("CREW_CPLEX_EXE", str(exe))
    _fake_studio(tmp_path / "ILOG", "CPLEX_Studio222")
    assert se.cplex_exe() == str(exe)


def test_явная_но_неверная_переменная_честный_отказ(monkeypatch, tmp_path):
    """Явная настройка — единственный кандидат: неверный путь в ней даёт
    отказ словами, даже если рядом стоит другой экземпляр CPLEX;
    молчаливой подмены движка нет."""
    _clean(monkeypatch, tmp_path)
    _fake_studio(tmp_path / "ILOG", "CPLEX_Studio222")
    monkeypatch.setenv("CREW_CPLEX_EXE", str(tmp_path / "нет" / EXE))
    assert se.cplex_candidates() == [str(tmp_path / "нет" / EXE)]
    with pytest.raises(se.EngineUnavailable) as e:
        se.cplex_exe()
    assert "CREW_CPLEX_EXE" in str(e.value)


def test_переменная_установщика_ibm(monkeypatch, tmp_path):
    _clean(monkeypatch, tmp_path)
    home = tmp_path / "IBM" / "CPLEX_Studio222"
    exe = _fake_studio(home.parent, "CPLEX_Studio222")
    monkeypatch.setenv("CPLEX_STUDIO_DIR222", str(home))
    assert Path(se.cplex_exe()) == exe


def test_каталог_studio_без_переменных(monkeypatch, tmp_path):
    _clean(monkeypatch, tmp_path)
    exe = _fake_studio(tmp_path / "ILOG", "CPLEX_Studio_Community222")
    assert Path(se.cplex_exe()) == exe


def test_не_нашли_отказ_словами_с_перечнем(monkeypatch, tmp_path):
    _clean(monkeypatch, tmp_path)
    with pytest.raises(se.EngineUnavailable) as e:
        se.cplex_exe()
    words = str(e.value)
    assert "cplex" in words and "CREW_CPLEX_EXE" in words
    assert "CPLEX_STUDIO_DIR" in words and "PATH" in words


def test_перечень_кандидатов_без_дублей(monkeypatch, tmp_path):
    _clean(monkeypatch, tmp_path)
    home = tmp_path / "ILOG" / "CPLEX_Studio222"
    exe = _fake_studio(tmp_path / "ILOG", "CPLEX_Studio222")
    monkeypatch.setenv("CPLEX_STUDIO_DIR222", str(home))
    cands = se.cplex_candidates()
    assert cands.count(str(exe)) == 1
    assert cands[0] == str(exe)
