# -*- coding: utf-8 -*-
"""Подменный переводчик SMT для стендов — ОДИН носитель у набора pytest
(`tests/conftest.py`, автофикстура) и у драйверов подпроцессов
(`tests/_replay_driver.py`, `tests/_cdpm_replay_driver.py`), где
фикстуры conftest не действуют.

Рубеж непротиворечивости при выпуске пакета постановки
(`cdpm/export_task_package.py::consistency_of`, слово владельца
11.09.2026) зовёт переводчика SMT через шов `smt_channel_factory`.
Стенд судит МЕХАНИЗМ, а не модель: подменяется ровно обращение к
языковой модели — каждое жёсткое правило задания переводится в
совместное содержательное утверждение (`(>= stub_n 0)` над одной
целочисленной константой; «true» переводом больше не считается — A01,
12.09.2026), вердикт sat; Z3 в окружении настоящий, отчёт настоящий,
отказы рубежа настоящие. Тесты самого рубежа (конфликт, отказ канала,
устаревший отчёт, неполный перевод) ставят своего переводчика поверх.
"""
from __future__ import annotations

import json
import re


def translation(prompt: str) -> str:
    """Ответ подменного переводчика на задание `smt_verify._PROMPT_SMT`:
    JSON с одним объявлением и совместным утверждением на КАЖДОЕ правило
    задания (идентификаторы — из перечня «Правила (N):»)."""
    tail = prompt.split("\nПравила (", 1)[-1]
    ids = re.findall(r"(?m)^([^\s|]+) \| ", tail)
    return json.dumps(
        {"decls": ["(declare-fun stub_n () Int)"],
         "asserts": [{"rule": rid, "smt": f"(>= stub_n {i})"}
                     for i, rid in enumerate(ids)]},
        ensure_ascii=False)


def channel_factory(run_dir):
    """Шов `export_task_package.smt_channel_factory`: канал переводчика
    для любого прогона — подменный переводчик."""
    return translation


def install() -> None:
    """Поставить подменного переводчика в сборщик пакета ТЕКУЩЕГО
    процесса (для драйверов подпроцессов; в pytest то же делает
    автофикстура conftest через monkeypatch)."""
    from cdpm import export_task_package as _etp
    _etp.smt_channel_factory = channel_factory
