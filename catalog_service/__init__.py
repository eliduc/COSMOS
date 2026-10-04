# -*- coding: utf-8 -*-
"""Служба каталога шаблонов (шаг 5): рабочее место владельца и витрина.

Каталог шаблонов — версионируемый пакет данных (`catalog/`), а не живая
база. Служба даёт пять экранов над ним и три осознанных действия:
«сохранить черновик» (вне пакета, в рабочей папке), «собрать черновик»
(запись разделов писателем читателя) и «выпустить версию» (только
владелец: шапка без суффикса, запись в журнал версий, неизменяемая копия
в `catalog/releases/<версия>/`).

Границы (гейт tests/catalog_service/test_service_isolation.py):
служба зовёт только читателя `template_catalog` (в том числе `writer`,
`diff`, `bindings`), FastAPI/Starlette/Pydantic и стандартную
библиотеку; ленивый импорт `tools.customer_markers` — единственный
допускаемый импорт вне читателя и только в `markers.py`. Импортов
`cdpm`, `vmp`, `webapp`, `_data_paths` нет ни одного: формула корня
данных повторена в `settings.py` пятью строками.

При импорте пакета не читаются ни файлы, ни переменные среды: настройки
строятся ЯВНО — `Settings.from_env(argv)` при старте, `create_app(settings)`
в тестах.

Запуск:
    python -m catalog_service --port 8105 --actor owner
    python -m catalog_service --port 8105 --read-only        # витрина

Контракт ручек, коды и тексты отказов — `catalog_service/API-CONTRACT.md`.
"""
from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
