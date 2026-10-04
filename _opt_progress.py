# -*- coding: utf-8 -*-
"""Серия хода оптимизации — машинный след для графика схождения.

Одна строка файла = одно событие улучшения:
    {"t": сек_от_старта, "incumbent": ..., "bound": ..., "n": номер}
Для этапа 2 (локальный поиск, нижней границы не существует) поле bound
отсутствует, а инкумбент — мягкий штраф решателя (см. запись фазы).

Файл лежит в артефактах прогона:
    <данные>/final-report/runs/progress/<run_id>.jsonl
и после завершения прогона неизменяем — история схождения хранится
вместе с результатом. Имя файла — личность прогона (CREW_RUN_ID от
задачи); ручной запуск без run_id пишет под <label>-manual, чтобы след
был и у командной строки.

Числа графика берутся ИЗ ЭТОГО файла, а не парсингом журнала: печать в
журнал и запись события выходят из одной точки кода и разойтись не
могут (правило «числа из машинного отчёта»).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def progress_path(label: str) -> Path:
    import _data_paths
    run_id = os.environ.get("CREW_RUN_ID") or f"{label}-manual"
    d = _data_paths.results_dir() / "runs" / "progress"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{run_id}.jsonl"


class ProgressWriter:
    """Дозаписывающий писатель серии. Никогда не роняет расчёт: ошибка
    записи гасится — график вторичен, результат первичен."""

    def __init__(self, label: str, stage: str, fresh: bool = True):
        self.t0 = time.monotonic()
        self.stage = stage
        try:
            self.path = progress_path(label)
            if fresh and self.path.exists():
                self.path.unlink()
            self.emit(phase=stage)
        except OSError:
            self.path = None

    def emit(self, incumbent=None, bound=None, n=None, phase=None,
             t=None, hard=None) -> None:
        """t — время события ПО ЧАСАМ ИСТОЧНИКА (сек), когда источник
        его знает точнее писателя (решатель этапа 2 отдаёт tMillis);
        hard — жёсткая часть счёта этапа 2 (у этапа 1 не существует)."""
        if self.path is None:
            return
        ev: dict = {"t": round(time.monotonic() - self.t0, 1)
                    if t is None else round(float(t), 1)}
        if phase is not None:
            ev["phase"] = phase
        if incumbent is not None:
            ev["incumbent"] = incumbent
        if bound is not None:
            ev["bound"] = bound
        if hard is not None:
            ev["hard"] = hard
        if n is not None:
            ev["n"] = n
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        except OSError:
            self.path = None
