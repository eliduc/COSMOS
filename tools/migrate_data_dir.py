# -*- coding: utf-8 -*-
"""Перенос артефактов расчётов в каталог данных (№50, этап 2).

Правило отбора — не догадка, а собственное объявление репозитория:
переезжает файл под `final-report/` или `data/gerad/`, который
(а) НЕ отслеживается git и (б) подпадает под `.gitignore` — то есть
уже признан артефактом расчёта этапом 1 выноса. Неотслеживаемые, но НЕ
игнорируемые файлы (свежие документы-отчёты, свидетельства) не
трогаются: их судьбу решает человек.

Идемпотентен: повторный запуск не находит, что переносить. Печатает
каждый перенос; `--dry-run` только показывает план.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import _data_paths  # noqa: E402
from _cli_console import force_utf8_console  # noqa: E402

force_utf8_console()

SCOPES = ("final-report", str(Path("data") / "gerad"))


def _git(args: list[str], text_input: str | None = None) -> str:
    res = subprocess.run(["git"] + args, cwd=str(REPO), text=True,
                         input=text_input, capture_output=True,
                         encoding="utf-8", errors="replace")
    return res.stdout


def _ignored(rels: list[str]) -> set[str]:
    """`git check-ignore` с нулевыми разделителями: текстовый режим на
    Windows превращает `\\n` в `\\r\\n`, git видит пути с хвостовым CR и
    не совпадает ни один, кроме последнего."""
    res = subprocess.run(
        ["git", "check-ignore", "--stdin", "-z"], cwd=str(REPO),
        input=("\0".join(rels)).encode("utf-8"), capture_output=True)
    return {x.decode("utf-8", "replace")
            for x in res.stdout.split(b"\0") if x}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    tracked = set(_git(["ls-files"]).splitlines())
    candidates: list[Path] = []
    for scope in SCOPES:
        base = REPO / scope
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(REPO).as_posix()
            if rel in tracked:
                continue
            candidates.append(p)
    if not candidates:
        print("переносить нечего")
        return 0
    rels = [p.relative_to(REPO).as_posix() for p in candidates]
    ignored = _ignored(rels)
    root = _data_paths.data_root()
    moved = kept = 0
    for p, rel in zip(candidates, rels):
        if rel not in ignored:
            kept += 1
            continue
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(("ПЛАН: " if args.dry_run else "-> ") + rel)
        if not args.dry_run:
            shutil.move(str(p), str(dest))
        moved += 1
    print("перенесено: %d; оставлено на разбор человеку "
          "(неотслеживаемые, но не игнорируемые): %d; корень: %s"
          % (moved, kept, root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
