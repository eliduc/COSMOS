# -*- coding: utf-8 -*-
"""Удаление папок попытки сборки решателя при атрибуте «только чтение»
(vmp.solver_build._rmtree_writable).

copytree переносит на копию исходников режим папок дерева, а клиенты
синхронизации рабочего стола выставляют папкам «только чтение»; прежнее
rmtree(ignore_errors=True) молча оставляло попытку в scratch (09.09.2026).
"""
from __future__ import annotations

import os
import stat

from vmp import solver_build as sb

FILE_ATTRIBUTE_READONLY = 0x1


def _только_чтение(p) -> bool:
    st = os.stat(p)
    if os.name == "nt":
        return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_READONLY)
    return not (st.st_mode & stat.S_IWUSR)


def test_папка_попытки_с_подпапкой_только_чтение_удаляется(tmp_path):
    dest = tmp_path / "attempt" / "kotlin-solver"
    (dest / "src" / "main").mkdir(parents=True)
    (dest / "src" / "main" / "A.kt").write_text("fun a() = 1\n", encoding="utf-8")
    (dest / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    os.chmod(dest / "src", stat.S_IRUSR | stat.S_IXUSR)
    assert _только_чтение(dest / "src")
    sb._rmtree_writable(dest)
    assert not dest.exists()


def test_отсутствующая_папка_и_обычная_папка_без_исключений(tmp_path):
    sb._rmtree_writable(tmp_path / "нет-такой")          # молча, как ignore_errors
    plain = tmp_path / "plain"
    (plain / "target").mkdir(parents=True)
    (plain / "target" / "x.jar").write_bytes(b"jar")
    sb._rmtree_writable(plain)
    assert not plain.exists()
