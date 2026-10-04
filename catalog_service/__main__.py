# -*- coding: utf-8 -*-
"""Запуск службы каталога шаблонов.

    python -m catalog_service --port 8105 --actor owner       # рабочее место
    python -m catalog_service --port 8105 --read-only         # витрина
    python -m catalog_service import-requests --inbox <папка> --reply

Умолчание `--host 127.0.0.1`: входа и паролей у службы нет, публикуемое
умолчание обязано быть безопасным; для доверенной сети — явно 0.0.0.0
(наружу площадки отдают службу туннелем, а не открытым портом).

Первый ключ-слово (без дефисов) — команда: сегодня она одна,
`import-requests` (приём очередей заявок с площадок, §5.1 проекта шага
6). Разбор команд отделён от разбора ключей запуска нарочно: служба и
инструмент — разные работы с разными ключами, и общий парсер однажды
принял бы `--inbox` за настройку службы.
"""
from __future__ import annotations

import sys

from .errors import ServiceError
from .settings import Settings, SettingsError, startup_words

#: Команды пакета: слово → функция `main(argv) -> int`.
COMMANDS = ("import-requests",)


def command(argv: list[str]) -> int:
    from .import_requests import main as import_main
    return import_main(argv[1:])


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")     # печать кириллицы
    args = list(argv) if argv is not None else sys.argv[1:]
    if args and args[0] in COMMANDS:
        return command(args)
    argv = args
    try:
        settings = Settings.from_env(argv)
    except SettingsError as e:
        print(f"служба каталога не запущена: {e}")
        return 2
    except SystemExit as e:                          # --help
        return int(e.code or 0)

    from .app import create_app
    app = create_app(settings)
    store = app.state.store
    try:
        store.acquire_catalog_lock()
    except ServiceError as e:
        print(f"служба каталога не запущена: {e.words}")
        return 3
    print(startup_words(settings, store.catalog_words()))
    try:
        import uvicorn
        uvicorn.run(app, host=settings.host, port=settings.port,
                    log_level="warning")
    finally:
        store.release_catalog_lock()
    return 0


if __name__ == "__main__":                           # pragma: no cover
    sys.exit(main())
