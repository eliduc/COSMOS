# -*- coding: utf-8 -*-
"""Гейт: все площадки комплекта установки совпадают с эталоном байт в байт.

Эталон — корень рабочего репозитория (crew-demo): именно он проходит
набор тестов и попадает в публикацию. Остальные площадки — копии,
которые люди берут руками, и потому они молча устаревают: 2026-08-16
живой комплект в оснастке публикации ставил вчерашним установщиком —
без описи установленного, из-за чего снос шёл запасным, менее точным
способом.

Список площадок — местный для этой машины (комплекты передачи живут вне
репозиториев). Отсутствующая площадка не отказ, а строка в отчёте:
комплект могли передать и удалить. Отсутствующий ФАЙЛ на существующей
площадке — отказ: половина комплекта хуже его отсутствия.
"""
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Исполняемая часть комплекта: обязана совпадать везде.
KIT = (
    "install_cosmos.py",
    "uninstall_cosmos.py",
    "install_bootstrap.ps1",
    "uninstall_bootstrap.ps1",
    "INSTALL-COSMOS.cmd",
    "UNINSTALL-COSMOS.cmd",
    "УСТАНОВИТЬ-COSMOS.cmd",
    "УДАЛИТЬ-COSMOS.cmd",
)
# Руководства: обязательны на площадках передачи (в KITS помечено),
# в служебных копиях не требуются.
DOCS = (
    "README-RU.md", "README-EN.md", "README-AR.md",
    "README-UZ.md", "README-KK.md", "UNINSTALL-PLAN-RU.md",
)

# (путь, нужны ли руководства). Эталон руководств — crew-demo/docs/install.
KITS = (
    (Path(r"C:\work\COSMOS"), True),
    (REPO.parent / "COSMOS" / "Installation", True),
    (REPO.parent / "handoff" / "github-export", False),
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

    etalon = {name: sha(REPO / name) for name in KIT}
    etalon.update({name: sha(REPO / "docs" / "install" / name)
                   for name in DOCS})

    checked = 0
    problems = []
    for root, with_docs in KITS:
        if not root.is_dir():
            print("площадки нет (не отказ): %s" % root)
            continue
        names = KIT + (DOCS if with_docs else ())
        for name in names:
            checked += 1
            p = root / name
            if not p.is_file():
                problems.append("НЕТ ФАЙЛА: %s" % p)
            elif sha(p) != etalon[name]:
                problems.append("РАСХОДИТСЯ С ЭТАЛОНОМ: %s" % p)
        print("площадка %s: рассмотрено файлов %d" % (root, len(names)))

    print("итого рассмотрено: %d файлов на площадках + %d эталонных"
          % (checked, len(etalon)))
    assert checked >= len(KIT), "гейт вхолостую: ни одной площадки"
    if problems:
        for s in problems:
            print("  ! %s" % s)
        print("ОТКАЗ: комплекты разошлись (%d)" % len(problems))
        return 1
    print("ВЕРДИКТ: все площадки совпадают с эталоном")
    return 0


if __name__ == "__main__":
    sys.exit(main())
