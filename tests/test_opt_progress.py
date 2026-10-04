# -*- coding: utf-8 -*-
"""Машинный след схождения оптимизации (_opt_progress).

Контракты: события пишутся строками JSONL из той же точки кода, что и
печать; имя файла — личность прогона (CREW_RUN_ID) либо <label>-manual;
ошибка записи не роняет расчёт; API задач отдаёт след по публичному id
задачи через приватный run_id.
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _writer(tmp_path, monkeypatch, run_id=None, label="uz-x"):
    import _data_paths
    monkeypatch.setattr(_data_paths, "results_dir", lambda: tmp_path)
    if run_id is None:
        monkeypatch.delenv("CREW_RUN_ID", raising=False)
    else:
        monkeypatch.setenv("CREW_RUN_ID", run_id)
    import _opt_progress
    return _opt_progress.ProgressWriter(label, "cover")


def _events(path: Path):
    return [json.loads(x) for x in
            path.read_text("utf-8").splitlines() if x.strip()]


def test_writer_appends_events_with_run_id(tmp_path, monkeypatch):
    w = _writer(tmp_path, monkeypatch, run_id="abc123def456")
    w.emit(incumbent=100.0, bound=90.0, n=1)
    w.emit(incumbent=95.0, bound=91.0, n=2)
    p = tmp_path / "runs" / "progress" / "abc123def456.jsonl"
    evs = _events(p)
    assert evs[0] == {"t": evs[0]["t"], "phase": "cover"}
    assert evs[1]["incumbent"] == 100.0 and evs[1]["bound"] == 90.0
    assert evs[2]["n"] == 2
    assert all(evs[i]["t"] <= evs[i + 1]["t"] for i in range(len(evs) - 1))


def test_writer_manual_name_and_fresh_start(tmp_path, monkeypatch):
    w = _writer(tmp_path, monkeypatch, label="uz-y")
    p = tmp_path / "runs" / "progress" / "uz-y-manual.jsonl"
    assert p.exists()
    w.emit(incumbent=1)
    assert len(_events(p)) == 2
    # fresh=True нового писателя стирает прежний след того же прогона
    _writer(tmp_path, monkeypatch, label="uz-y")
    assert len(_events(p)) == 1            # только событие фазы


def test_stage2_event_carries_hard_and_source_time(tmp_path, monkeypatch):
    w = _writer(tmp_path, monkeypatch, run_id="r2")
    w.emit(incumbent=-680000, hard=0, t=104.4)
    ev = _events(tmp_path / "runs" / "progress" / "r2.jsonl")[-1]
    assert ev == {"t": 104.4, "incumbent": -680000, "hard": 0}


def test_write_error_never_raises(tmp_path, monkeypatch):
    w = _writer(tmp_path, monkeypatch, run_id="r3")
    w.path = tmp_path / "no-such-dir" / "x.jsonl"
    w.emit(incumbent=1)                    # не падает
    assert w.path is None                  # писатель честно замолчал


def test_jobs_run_id_of_exposes_private_identity(monkeypatch):
    from webapp import jobs
    with jobs._lock:
        jobs._jobs["stage1-x-000001"] = {"id": "stage1-x-000001",
                                         "_run_id": "deadbeef0001"}
    try:
        assert jobs.run_id_of("stage1-x-000001") == "deadbeef0001"
        assert jobs.run_id_of("net-takogo") is None
    finally:
        with jobs._lock:
            jobs._jobs.pop("stage1-x-000001", None)
