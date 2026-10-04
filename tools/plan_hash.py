# -*- coding: utf-8 -*-
"""Хеш тела плана для подписей согласования (план единой версии
06.09.2026, стык 5 GPT): SHA-256 текста плана БЕЗ раздела подписей
(«## 9. Подписи согласования» до следующего заголовка «## »), с
нормализацией концов строк (CRLF → LF) и без BOM. Подпись в разделе 9
ссылается на этот хеш; изменение тела плана меняет хеш, добавление
подписи — нет.

Запуск: python tools/plan_hash.py [путь к плану]
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

DEFAULT = Path(__file__).resolve().parent.parent / "final-report" \
    / "PLAN-EDINAYA-VERSIYA-CLD-GPT-20260906.md"
SIGN_HEADER = re.compile(r"^## 9\. ", re.M)
NEXT_HEADER = re.compile(r"^## ", re.M)


def plan_body(text: str) -> str:
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    m = SIGN_HEADER.search(text)
    if not m:
        return text
    rest = text[m.end():]
    n = NEXT_HEADER.search(rest)
    return text[:m.start()] + (rest[n.start():] if n else "")


def plan_hash(path: Path = DEFAULT) -> str:
    body = plan_body(path.read_text(encoding="utf-8"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    # Консоль Windows без UTF-8 роняет кириллицу в сообщениях — правило
    # репозитория для печатающих инструментов (tests/test_cli_console).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    args = list(sys.argv[1:] if argv is None else argv)
    path = Path(args[0]) if args else DEFAULT
    print(plan_hash(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
