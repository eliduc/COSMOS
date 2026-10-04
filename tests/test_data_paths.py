# -*- coding: utf-8 -*-
"""Каталог данных расчётов (№50, этап 2): артефакты живут вне репозитория.

Проверяются свойства, на которые опирается вынос: корень по умолчанию —
СОСЕДНИЙ с репозиторием каталог (не внутри рабочего дерева git),
переменная окружения его переопределяет, а зеркальная структура
сохраняет прежние относительные пути.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import _data_paths  # noqa: E402


def test_default_root_is_outside_the_repo(monkeypatch):
    monkeypatch.delenv("CREW_DATA_DIR", raising=False)
    root = _data_paths.data_root()
    assert _data_paths.REPO not in root.parents and root != _data_paths.REPO, \
        "корень данных внутри репозитория вернул бы артефакты под git"
    assert root.name == _data_paths.REPO.name + "-data"
    assert root.parent == _data_paths.REPO.parent


def test_env_overrides_the_root(monkeypatch, tmp_path):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "d"))
    assert _data_paths.data_root() == tmp_path / "d"
    assert _data_paths.results_dir() == tmp_path / "d" / "final-report"
    assert _data_paths.results_dir().is_dir()          # создан
    assert _data_paths.gerad_data_dir() == \
        tmp_path / "d" / "data" / "gerad"


def test_registry_final_report_points_at_the_data_root(monkeypatch):
    """Узел webapp обязан смотреть в каталог данных, а свидетельства
    исследований — в репозиторий: перепутанные корни либо вернули бы
    артефакты под git, либо потеряли бы отслеживаемые свидетельства."""
    from webapp import registry
    assert registry.FINAL_REPORT == _data_paths.results_dir(create=False)
    assert registry.EVIDENCE_DIR == registry.REPO_ROOT / "final-report"
