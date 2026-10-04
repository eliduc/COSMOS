# -*- coding: utf-8 -*-
"""Каждый сторонний импорт объявлен в pyproject.toml.

Зачем эта проверка появилась. Установка на чистую машину 2026-08-15
остановилась на том, что приложение не импортируется:

    ModuleNotFoundError: No module named 'jsonschema'

`webapp/registry.py` и три адаптера импорта пользовались `jsonschema`,
но в зависимостях его не было. На машине разработчика пакет стоял давно,
поэтому недостача не проявлялась ни в одном прогоне; проявилась она
только там, где окружение собирается с нуля — на чистой установке и в
сборке CI. Всего таких пакетов нашлось пять.

Класс дефекта: «работает у нас» вместо «объявлено». Тест закрывает
класс, а не случай — он сверяет ВСЕ сторонние импорты дерева с
объявленными зависимостями и падает на любом новом необъявленном.
"""
from __future__ import annotations

import ast
import io
import os
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

SKIP_DIRS = {".venv", ".git", "__pycache__", ".tools", "data",
             ".pytest_cache", ".codex-cache", "node_modules", ".idea"}

# Имя импорта отличается от имени пакета — сопоставление ведётся здесь.
IMPORT_TO_DIST = {
    "yaml": "pyyaml",
    "dateutil": "python-dateutil",
    "bokeh_fastapi": "bokeh-fastapi",
    "jwt": "pyjwt",
    # CDPM П-2: чтение docx при заморозке источников
    "docx": "python-docx",
    "PIL": "pillow",
    # PDF-вход заморозки (2026-08-26): модуль fitz — пакет PyMuPDF.
    "fitz": "pymupdf",
    # СМТ-1: проверка непротиворечивости свода через Z3.
    "z3": "z3-solver",
}

# Разрешённые исключения. Каждое — с причиной; список закрытый, и любое
# новое имя обязано либо попасть в зависимости, либо быть объяснено здесь.
ALLOWED_UNDECLARED = {
    # поставляется вместе с pydantic (тот объявлен) — отдельного пакета нет
    "pydantic_core",
    # устаревший движок решателя: необязательное дополнение
    # timefold-legacy, ставится отдельно и на 3.14 не ставится вовсе
    "solverforge_legacy",
    "timefold",
}


def _declared() -> set[str]:
    with io.open(REPO / "pyproject.toml", "rb") as fh:
        project = tomllib.load(fh)["project"]
    names: set[str] = set()
    groups = [project.get("dependencies", [])]
    groups += list(project.get("optional-dependencies", {}).values())
    for group in groups:
        for spec in group:
            head = spec.split("[")[0]
            for sep in (">", "<", "=", "!", "~", ";", " "):
                head = head.split(sep)[0]
            if head.strip():
                names.add(head.strip().lower())
    return names


def _local_modules() -> set[str]:
    """Имена собственных модулей и пакетов дерева."""
    local = {"webapp", "tests", "tools", "src", "flight_crew_scheduling"}
    for root, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if name.endswith(".py"):
                local.add(name[:-3])
        if "__init__.py" in names and Path(root) != REPO:
            local.add(Path(root).name)
    return local


def _imports() -> tuple[dict[str, set[str]], int]:
    """Верхнеуровневые имена всех импортов дерева и число разобранных файлов."""
    used: dict[str, set[str]] = {}
    parsed = 0
    for root, dirs, names in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if not name.endswith(".py"):
                continue
            path = Path(root) / name
            try:
                tree = ast.parse(io.open(path, encoding="utf-8").read())
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
            parsed += 1
            rel = path.relative_to(REPO).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        used.setdefault(alias.name.split(".")[0],
                                        set()).add(rel)
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    if node.module:
                        used.setdefault(node.module.split(".")[0],
                                        set()).add(rel)
    return used, parsed


def test_every_third_party_import_is_declared():
    declared = _declared()
    local = _local_modules()
    std = set(sys.stdlib_module_names)
    used, parsed = _imports()

    # Проверка обязана доказать, что не прошла вхолостую.
    assert parsed > 200, f"разобрано подозрительно мало файлов: {parsed}"
    assert len(used) > 50, f"импортов найдено подозрительно мало: {len(used)}"
    assert "fastapi" in used, "разбор не увидел даже fastapi — проверка слепа"

    missing = {}
    for module, where in used.items():
        if not module or module in std or module in local:
            continue
        if module in ALLOWED_UNDECLARED:
            continue
        dist = IMPORT_TO_DIST.get(module, module).lower()
        if dist not in declared:
            missing[module] = sorted(where)[:3]

    assert not missing, (
        "сторонние импорты без объявленной зависимости "
        f"({len(missing)}): " + "; ".join(
            f"{mod} ← {', '.join(files)}" for mod, files in
            sorted(missing.items())))


def test_allowed_undeclared_list_does_not_rot():
    """Разрешённое исключение обязано существовать в дереве.

    Иначе список исключений тихо копит мёртвые имена и однажды прикроет
    собой настоящую недостачу.
    """
    used, _ = _imports()
    stale = sorted(name for name in ALLOWED_UNDECLARED if name not in used)
    assert not stale, (
        "в списке разрешённых исключений имена, которых в дереве больше "
        f"нет: {', '.join(stale)}")
