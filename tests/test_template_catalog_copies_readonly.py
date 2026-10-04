# -*- coding: utf-8 -*-
"""tmp-копии каталога и папки «только чтение» (tests/catalog_copies).

Клиенты синхронизации рабочего стола выставляют папкам дерева атрибут
«только чтение»; copytree переносит его на копию, и голый rmtree копии на
Windows падает «Access is denied» (09.09.2026 — девять красных строк
полного набора при нетронутом коде). Помощник rmtree_writable обязан
снимать помеху и удалять копию на обеих платформах."""
from __future__ import annotations

import os
import shutil
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import catalog_copies as cc                                       # noqa: E402

FILE_ATTRIBUTE_READONLY = 0x1


def _только_чтение(p: Path) -> bool:
    st = os.stat(p)
    if os.name == "nt":
        return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_READONLY)
    return not (st.st_mode & stat.S_IWUSR)


def test_копия_папки_только_чтение_удаляется_помощником(tmp_path):
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "a.json").write_text("{}", encoding="utf-8")
    (src / "b.json").write_text("{}", encoding="utf-8")
    # Папка-источник «только чтение»: на Windows — атрибут R, на POSIX —
    # режим без права записи (записи папки нельзя удалять).
    os.chmod(src / "sub", stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert _только_чтение(src / "sub")
        dst = tmp_path / "copy"
        shutil.copytree(src, dst)
        # copystat перенёс помеху на копию — ровно случай полного набора.
        assert _только_чтение(dst / "sub"), "copytree не перенёс режим папки"
        cc.rmtree_writable(dst)
        assert not dst.exists()
        # Обычную копию помощник удаляет как rmtree.
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("sub"))
        cc.rmtree_writable(dst)
        assert not dst.exists()
    finally:
        os.chmod(src / "sub", stat.S_IRWXU)


def test_copy_catalog_повторно_в_ту_же_папку_при_только_чтение(tmp_path):
    """copy_catalog поверх прежней копии, у которой папка стала «только
    чтение», — путь девяти красных строк: помощник обязан пройти."""
    folder = cc.copy_catalog(tmp_path)
    os.chmod(folder / "transition", stat.S_IRUSR | stat.S_IXUSR)
    assert _только_чтение(folder / "transition")
    folder2 = cc.copy_catalog(tmp_path)
    assert folder2 == folder and (folder2 / "catalog.json").is_file()
    # Режим папки СВЕЖЕЙ копии повторяет режим живого каталога (copystat):
    # на машине с клиентом синхронизации он бывает «только чтение» — это
    # не предмет проверки; предмет — что прежняя копия снята и новая
    # собрана целиком.
    assert (folder2 / "transition" / "families_order.json").is_file()


def test_помощник_не_глотает_чужие_ошибки(tmp_path):
    """Не-PermissionError идёт наружу как у rmtree (например, «нет такой
    папки»)."""
    import pytest
    with pytest.raises(FileNotFoundError):
        cc.rmtree_writable(tmp_path / "нет-такой")
