# -*- coding: utf-8 -*-
"""Окружение пусковика площадки для стенда и служебных команд COSMOS
(РВ-138, исправление 3 — класс «вне окружения пусковика»).

Панель COSMOS и CDPM получают окружение от пусковика площадки: каталог,
привязки обеих сторон, контракт пакетов (`CDPM_EXPORT_SPEC_VERSION`),
контур. Стенд и любая служебная команда, запущенные рукой, этого
окружения не наследуют: 03.10 стенд без `CDPM_EXPORT_SPEC_VERSION` собрал
пакеты по умолчанию производителя (1.18), решатель прочёл их «архивом»,
а результаты легли бы в свидетельство поддержки как настоящие. Тот же
класс трижды за день: привязки, модель, версия пакета.

Закон один: команда, чьи результаты становятся свидетельством (запись
результатов стенда) или идут в живую службу (решатель), требует ПОЛНОГО
окружения пусковика. Неполное — отказ словами с именами недостающих
переменных. Явный ключ «вне пусковика» оставлен для разработки и проб:
такие результаты помечаются в шапке, и дверь привязок их не принимает.

Здесь только имена и несекретные значения (пути, версии, отпечатки
файлов привязок): ключи и токены в паспорт не попадают."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

#: Переменные, которые пусковик площадки задаёт всегда
#: (`exact-launcher-core.mjs`, `environment()`), и без которых стенд
#: судит не то, что исполняет панель.
REQUIRED = ("CREW_CONTOUR", "CDPM_EXPORT_SPEC_VERSION", "CREW_TEMPLATE_CATALOG",
            "CREW_COSMOS_BINDINGS", "CREW_CDPM_BINDINGS")

OUTSIDE_WORDS = "вне окружения пусковика"


def missing(env=None) -> list:
    """Имена переменных пусковика, которых нет (или они пусты)."""
    env = os.environ if env is None else env
    return [k for k in REQUIRED if not str(env.get(k) or "").strip()]


def _sha(path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except (OSError, TypeError, ValueError):
        return None


def passport(env=None) -> dict:
    """Паспорт окружения команды для шапки результатов: что действовало,
    чего не было. Значения — пути, версия контракта и отпечатки файлов
    привязок; секретов здесь нет по построению."""
    env = os.environ if env is None else env
    gone = missing(env)
    get = lambda k: (str(env.get(k)).strip() or None) if env.get(k) else None
    return {"launcher": not gone,
            "missing": gone,
            "contour": get("CREW_CONTOUR"),
            "spec_version": get("CDPM_EXPORT_SPEC_VERSION"),
            "catalog": get("CREW_TEMPLATE_CATALOG"),
            "cosmos_bindings_sha256": _sha(get("CREW_COSMOS_BINDINGS")),
            "cdpm_bindings_sha256": _sha(get("CREW_CDPM_BINDINGS"))}


def refusal(env=None, *, purpose: str) -> str | None:
    """Слова отказа для команды `purpose`, если окружение неполное."""
    gone = missing(env)
    if not gone:
        return None
    return ("отказ: %s %s — нет %s. Запускайте команду в окружении "
            "пусковика площадки (то же, что у панели: каталог, привязки, "
            "контракт пакетов, контур), а не собранным рукой; для "
            "разработки и проб — явный ключ --outside-launcher, тогда "
            "результаты помечаются «%s» и в привязки не идут"
            % (purpose, OUTSIDE_WORDS, ", ".join(gone), OUTSIDE_WORDS))


def stale_reason(doc: dict) -> str | None:
    """Почему результаты нельзя сливать в привязки по окружению: шапка
    `environment` называет недостающие переменные пусковика. Файлы без
    шапки (до РВ-138) судятся прежними законами — их окружение не
    записано, и выдумывать его здесь нельзя."""
    env = doc.get("environment") if isinstance(doc, dict) else None
    if not isinstance(env, dict):
        return None
    gone = env.get("missing") or []
    if env.get("launcher") is False or gone:
        return ("результаты сняты %s (нет %s): свидетельством поддержки "
                "не становятся — перепрогоните стенд в окружении пусковика"
                % (OUTSIDE_WORDS, ", ".join(map(str, gone)) or "?"))
    return None
