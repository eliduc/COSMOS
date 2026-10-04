# -*- coding: utf-8 -*-
"""Удаление записи прогона из истории (проверка инструкции 2026-08-12).

Контракты: работающую задачу удалить нельзя (сначала остановка);
удаляются запись и журнал выполнения, артефакты результатов не
затрагиваются (delete_job их путей вообще не знает); несуществующая
задача — 404.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


@pytest.fixture()
def jobs(tmp_path, monkeypatch):
    from webapp import jobs as j
    monkeypatch.setattr(j, "JOBS_DIR", tmp_path, raising=False)
    monkeypatch.setattr(j, "_job_path",
                        lambda jid: tmp_path / f"{jid}.json")
    monkeypatch.setattr(j, "_log_path",
                        lambda jid: tmp_path / f"{jid}.log")
    return j, tmp_path


def _put(j, tmp, jid, status):
    with j._lock:
        j._jobs[jid] = {"id": jid, "status": status, "owner": "x@y"}
    (tmp / f"{jid}.json").write_text("{}", encoding="utf-8")
    (tmp / f"{jid}.log").write_text("log", encoding="utf-8")


def test_delete_removes_record_and_log(jobs):
    j, tmp = jobs
    _put(j, tmp, "stage1-a-000001", "failed")
    try:
        out = j.delete_job("stage1-a-000001")
        assert out["ok"] is True
        assert not (tmp / "stage1-a-000001.json").exists()
        assert not (tmp / "stage1-a-000001.log").exists()
        assert j.get_job("stage1-a-000001") is None
    finally:
        with j._lock:
            j._jobs.pop("stage1-a-000001", None)


def test_delete_running_refused(jobs):
    j, tmp = jobs
    _put(j, tmp, "stage1-b-000002", "running")
    try:
        with pytest.raises(j.JobError) as e:
            j.delete_job("stage1-b-000002")
        assert e.value.http_status == 409
        assert (tmp / "stage1-b-000002.json").exists()
    finally:
        with j._lock:
            j._jobs.pop("stage1-b-000002", None)


def test_delete_missing_404(jobs):
    j, _tmp = jobs
    with pytest.raises(j.JobError) as e:
        j.delete_job("net-takoy")
    assert e.value.http_status == 404


def test_progress_meta_running_and_finished(jobs, monkeypatch):
    """Время из бюджета для графика (замечание владельца 2026-08-12):
    работающая задача несёт elapsed_s и budget_s; завершённая — только
    budget_s (время берётся из следа); неизвестная — пусто."""
    j, _tmp = jobs
    import time as _time
    with j._lock:
        j._jobs["stage1-t-000003"] = {
            "id": "stage1-t-000003", "type": "stage1", "status": "running",
            "params": {"budget_min": 60}, "_started_ts": _time.time() - 100}
        j._jobs["stage2-t-000004"] = {
            "id": "stage2-t-000004", "type": "stage2", "status": "failed",
            "params": {"budget_s": 1800}, "_started_ts": _time.time() - 500}
    try:
        m1 = j.progress_meta("stage1-t-000003")
        assert m1["budget_s"] == 3600.0
        assert 95 <= m1["elapsed_s"] <= 130
        m2 = j.progress_meta("stage2-t-000004")
        assert m2["budget_s"] == 1800.0 and "elapsed_s" not in m2
        assert j.progress_meta("net") == {}
    finally:
        with j._lock:
            j._jobs.pop("stage1-t-000003", None)
            j._jobs.pop("stage2-t-000004", None)
