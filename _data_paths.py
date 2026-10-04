# -*- coding: utf-8 -*-
"""Каталог данных расчётов, отделённый от репозитория (№50, этап 2).

Артефакты прогонов (каталоги цепочек, решения этапа 2, best-наборы,
диагностики, свидетельства покрытия) живут НЕ в рабочем дереве git:
этап 1 снял их с учёта, этот модуль выносит их физически. Отслеживаемые
свидетельства исследований и документы-отчёты остаются в репозитории —
их прогоны не перезаписывают.

Устройство: один корень данных, внутри — зеркало прежних относительных
путей (`final-report/...`, `data/gerad/...`). Так писатели и читатели
меняют только базу, а взаимное расположение артефактов — нет.

Корень: переменная окружения `CREW_DATA_DIR`, а без неё — соседний с
репозиторием каталог `<имя-репозитория>-data` (у трёх контуров на одной
машине корни различаются сами собой: crew-demo-data, cosmos-stage-data,
cosmos-test-data). Значение по умолчанию НЕ внутри репозитория — иначе
этап 2 не наступил бы нигде, где переменная не задана.
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent


def data_root() -> Path:
    env = os.environ.get("CREW_DATA_DIR")
    return Path(env) if env else REPO.parent / (REPO.name + "-data")


def results_dir(create: bool = True) -> Path:
    """Артефакты, прежде жившие в `<репозиторий>/final-report`."""
    p = data_root() / "final-report"
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def gerad_data_dir(create: bool = True) -> Path:
    """Решения и производные, прежде жившие в `<репозиторий>/data/gerad`."""
    p = data_root() / "data" / "gerad"
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p
