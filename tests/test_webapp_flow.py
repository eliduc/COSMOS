# -*- coding: utf-8 -*-
"""Состояние конвейера для главного экрана-схемы (webapp/flow.py).

Инварианты:
  1. Подфаза пейринга определяется ПОСЛЕДНЕЙ строкой-меткой лога
     («этап:» / «веха [чч:мм:сс]»), посторонние строки не судятся.
  2. Эскалация — отдельный блок между пулом и отбором; активная
     подфаза даёт working, пройденные — done, будущие — waiting.
  3. % близости к оптимуму считается из следа прогресса
     (целевая, граница) и выводится всегда, где обе есть.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from webapp import flow  # noqa: E402


def test_подфаза_по_последней_метке_лога():
    lines = [
        "этап: перебор цепочек от 4 смен, до 10 с...",
        "прочий шум без метки: эскалация не судится",
        "этап: спасательный проход — осиротевших было 3, осталось 2",
        "веха [12:00:01] эскалация: ступень 1 — сирот 2, покрыто 1",
    ]
    phase, params = flow._pairing_phase(lines)
    assert phase == "escalate"
    assert params["orphans"] == 2
    assert params["esc_step"] == 1
    # следующая метка двигает фазу дальше
    lines.append("веха [12:00:05] сборка базовой модели: кандидатов 42")
    phase2, params2 = flow._pairing_phase(lines)
    assert phase2 == "select"
    assert params2["candidates"] == 42


def test_состояние_блоков_с_активной_эскалацией(monkeypatch, tmp_path):
    rec = {"id": "job-1", "type": "stage1", "status": "running",
           "instance": "inst-т", "started_at": "2026-08-28T20:00:00",
           "objective": None, "lp_bound": None, "gap_pct": None}
    monkeypatch.setattr(flow.runs, "list_runs", lambda owner: [rec])
    monkeypatch.setattr(flow.jobs, "run_id_of", lambda jid: "run-т")
    monkeypatch.setattr(flow.jobs, "JOBS_DIR", tmp_path)
    (tmp_path / "job-1.log").write_text(
        "этап: перебор цепочек от 9 смен, до 10 с...\n"
        "этап: спасательный проход — осиротевших было 5, осталось 3\n"
        "веха [10:00:00] эскалация: ступень 2 — сирот 3, покрыто 1\n",
        encoding="utf-8")
    monkeypatch.setattr(
        flow, "_progress_last",
        lambda rid: {"objective": 103.0, "bound": 100.0,
                     "gap_pct": 3.0, "elapsed_s": 7}
        if rid == "run-т" else {})
    st = flow.build_state()
    by = {b["id"]: b for b in st["blocks"]}
    assert st["active"] == "pairing.escalate"
    assert by["pairing.pool"]["status"] == "done"
    assert by["pairing.escalate"]["status"] == "working"
    assert by["pairing.select"]["status"] == "waiting"
    assert by["pairing.arbiter"]["status"] == "waiting"
    # живые параметры активного блока несут % близости к оптимуму
    assert by["pairing.escalate"]["params"]["gap_pct"] == 3.0
    assert by["pairing.escalate"]["params"]["orphans"] == 3


def test_процент_близости_из_следа_прогресса(monkeypatch, tmp_path):
    class _DP:
        @staticmethod
        def results_dir():
            return tmp_path
    monkeypatch.setitem(sys.modules, "_data_paths", _DP)
    d = tmp_path / "runs" / "progress"
    d.mkdir(parents=True)
    (d / "r7.jsonl").write_text(
        '{"t": 0.0, "phase": "cover"}\n'
        '{"t": 10.5, "incumbent": 110.0, "bound": 100.0, "n": 1}\n'
        '{"t": 20.0, "incumbent": 101.0, "n": 2}\n',
        encoding="utf-8")
    out = flow._progress_last("r7")
    assert out["objective"] == 101.0
    assert out["bound"] == 100.0
    assert out["gap_pct"] == 1.0
    assert out["elapsed_s"] == 20
