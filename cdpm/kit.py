# -*- coding: utf-8 -*-
"""Признак допуска моделей у КОМПЛЕКТА документов — единый источник
правила для панели (cdpm/webui.py) и сборщика пакета постановки
(cdpm/export_task_package.py, ключ манифеста kit).

Решение владельца 2026-09-03 с поправкой «если признака нет — только
локальные модели»; 04.09 (расчёт переехал в COSMOS): действующий круг
моделей едет с пакетом, потому что сертификация решений у потребителя
обращается к языковой модели, а класс данных — свойство комплекта, не
площадки."""
from __future__ import annotations

import os

# ── Признак допуска моделей у КОМПЛЕКТА документов (решение владельца
# 2026-09-03, поправка того же дня «если признака нет — только локальные
# модели»). Единственный источник допуска — признак прогона: класс данных
# и явный llm_scope с основанием. Площадка НЕ прижимает и не отказывает:
# переменная CDPM_SCOPE_FORCE прежней постановки (2026-08-25) больше не
# действует — о ней предупреждается один раз. Умолчание по классу данных
# действует только при явно названном классе; без класса и без признака
# — local-only с заявлением в статусе. Просьба запроса (параметр scope) —
# только сверка с признаком: расхождение — отказ словами.
DATA_CLASSES = ("synthetic", "open", "proprietary")
DATA_CLASS_RU = {"synthetic": "синтетический", "open": "открытый",
                 "proprietary": "проприетарный (данные авиакомпании)"}
LLM_SCOPES = ("any", "local-only")
_SCOPE_ENV_WARNED = False


def kit_scope(status: dict) -> dict:
    """Эффективный допуск моделей по признаку комплекта прогона и его
    чтение словами: {effective, source, reading, data_class, llm_scope,
    basis, env_force_ignored}."""
    global _SCOPE_ENV_WARNED
    kit = status.get("kit") if isinstance(status.get("kit"), dict) else {}
    data_class = kit.get("data_class") or None
    explicit = kit.get("llm_scope") or None
    basis = kit.get("llm_scope_basis") or None
    if explicit is not None and explicit not in LLM_SCOPES:
        # Кривой явный признак (рука, старый код) — не умолчание
        # класса, а безопасный исход: неверное значение не может
        # превратиться в разрешение облака.
        effective = "local-only"
        source = "признак вне перечня"
        reading = ("признак комплекта %r вне перечня %s — действует "
                   "local-only до исправления признака"
                   % (explicit, list(LLM_SCOPES)))
    elif explicit in LLM_SCOPES:
        effective = explicit
        source = "признак комплекта"
        reading = "признак комплекта выставлен явно: %s" % explicit
        if data_class == "proprietary" and explicit == "any":
            reading += (" — согласие авиакомпании на облако, основание: %s"
                        % (basis or "?"))
        elif data_class:
            reading += " (класс данных %s)" % DATA_CLASS_RU.get(data_class, data_class)
    elif data_class in ("synthetic", "open"):
        effective = "any"
        source = "умолчание по классу данных"
        reading = ("класс данных %s — любые модели, включая облачные"
                   % DATA_CLASS_RU.get(data_class, data_class))
    elif data_class == "proprietary":
        effective = "local-only"
        source = "умолчание по классу данных"
        reading = ("класс данных %s — только локальные модели; облако — "
                   "лишь с согласием авиакомпании в признаке комплекта"
                   % DATA_CLASS_RU["proprietary"])
    else:
        effective = "local-only"
        source = "умолчание без признака"
        reading = "признак не выставлен, действует умолчание local-only"
    forced = (os.environ.get("CDPM_SCOPE_FORCE") or "").strip()
    ignored = bool(forced)
    if ignored and not _SCOPE_ENV_WARNED:
        _SCOPE_ENV_WARNED = True
        print("предупреждение: переменная CDPM_SCOPE_FORCE=%r не действует — "
              "допуск моделей решает признак комплекта" % forced, flush=True)
    return {"effective": effective, "source": source, "reading": reading,
            "data_class": data_class, "llm_scope": explicit, "basis": basis,
            "env_force_ignored": ignored}


def validate_kit_scope(data_class, llm_scope, basis) -> list[str]:
    """Нарушения формы признака: значения из перечней; проприетарный
    класс с допуском any обязан нести основание (согласие авиакомпании
    — документ, дата)."""
    problems = []
    if data_class not in (None, "", *DATA_CLASSES):
        problems.append("класс данных %r вне перечня %s"
                        % (data_class, list(DATA_CLASSES)))
    if llm_scope not in (None, "", *LLM_SCOPES):
        problems.append("допуск моделей %r вне перечня %s"
                        % (llm_scope, list(LLM_SCOPES)))
    if data_class == "proprietary" and llm_scope == "any" \
            and not (basis or "").strip():
        problems.append("проприетарный класс с допуском any требует основания "
                        "(согласие авиакомпании: документ, дата)")
    return problems


def package_kit(status: dict | None) -> dict | None:
    """Ключ манифеста пакета kit: класс данных, ДЕЙСТВУЮЩИЙ круг моделей
    (не сырое поле статуса: у синтетического комплекта без явного
    признака это «any»), источник действия и основание. Нет статуса —
    None: ключа в пакете нет, потребитель судит «только локальные»."""
    if not isinstance(status, dict):
        return None
    ks = kit_scope(status)
    return {"data_class": ks["data_class"], "llm_scope": ks["effective"],
            "llm_scope_source": ks["source"], "llm_scope_basis": ks["basis"]}
