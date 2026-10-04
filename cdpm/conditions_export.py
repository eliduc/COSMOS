# -*- coding: utf-8 -*-
"""ПЕРЕХОДНАЯ ОБЁРТКА-РЕЭКСПОРТ (перенос ВМП, 2026-08-28).

Сборка файла условий переехала в приёмную часть COSMOS:
vmp/conditions_export.py (решение владельца, узел vmp-boundary).
Обёртка держит старые импорты кнопки живыми на переходный период;
снять отдельным коммитом после недели стабильности или по слову
владельца — двух копий истины нет, здесь только реэкспорт."""
from vmp.conditions_export import *  # noqa: F401,F403
from vmp.conditions_export import (  # noqa: F401
    ConditionsExportError, build_conditions)
