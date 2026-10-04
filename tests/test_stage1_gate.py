# -*- coding: utf-8 -*-
"""Gate-семантика кода выхода 4 у задач этапа 1 (находка владельца
2026-08-12: штатный прогон пейринга из интерфейса — FEASIBLE, зазор
1,3 %, каталог опубликован — был помечен «ошибкой»).

Контракт кодов возврата _pairing_builder: 4 = «непокрытое есть, но всё
оно непокрываемо в принципе; каталог опубликован, признак внутри».
Исполнитель задач обязан считать это вердиктом, а не сбоем — как уже
делает для этапа 2 (contract, «Nonzero exit != crash»).

Проверки печатают, сколько объектов рассмотрели, — пустой прогон теста
не считается пройденным.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


@pytest.fixture()
def jobs_env(tmp_path, monkeypatch):
    from webapp import jobs as j, registry
    monkeypatch.setattr(registry, "FINAL_REPORT", tmp_path)
    monkeypatch.setattr(j, "JOBS_DIR", tmp_path, raising=False)
    monkeypatch.setattr(j, "_job_path",
                        lambda jid: tmp_path / f"{jid}.json")
    monkeypatch.setattr(j, "_log_path",
                        lambda jid: tmp_path / f"{jid}.log")
    # Профиль прогона пишет в свой каталог — в этом тесте он не предмет.
    monkeypatch.setattr(j, "_record_run_profile", lambda job: None)
    return j, tmp_path


def _catalog(tmp: Path, label: str, run_id, verdict="INFEASIBLE_INPUT"):
    path = tmp / f"gerad_colgen_selected_{label}.json"
    path.write_text(json.dumps({
        "label": label, "run_id": run_id,
        "coverage": {"verdict": verdict, "uncovered": 37,
                     "no_pair_in_horizon": 37, "not_built": 0},
    }), encoding="utf-8")
    return path


def test_stage1_catalog_completed_by_run_id(jobs_env):
    j, tmp = jobs_env
    inst = {"label": "s1gate"}
    _catalog(tmp, "s1gate", "run-a")
    started = time.time() + 5.0   # mtime старше «старта» — решает штамп
    checked = 0
    assert j._stage1_catalog_completed(inst, started, "run-a") is True
    checked += 1
    assert j._stage1_catalog_completed(inst, started, "run-b") is False
    checked += 1
    print(f"run_id comparisons checked: {checked}")
    assert checked == 2


def test_stage1_catalog_completed_mtime_fallback(jobs_env):
    # Каталог без штампа (ручной прогон, run_id: null) — строгий mtime.
    j, tmp = jobs_env
    inst = {"label": "s1mt"}
    _catalog(tmp, "s1mt", None)
    assert j._stage1_catalog_completed(inst, time.time() - 60,
                                       "run-x") is True
    assert j._stage1_catalog_completed(inst, time.time() + 60,
                                       "run-x") is False
    print("mtime fallback branches checked: 2")


def test_stage1_catalog_completed_pending_is_not_published(jobs_env):
    # Отложенный .pending-файл (коды 2/3) публикацией НЕ является.
    j, tmp = jobs_env
    inst = {"label": "s1pend"}
    p = _catalog(tmp, "s1pend", "run-a")
    p.rename(tmp / "gerad_colgen_selected_s1pend.pending.json")
    assert j._stage1_catalog_completed(inst, 0.0, "run-a") is False
    print("checked: .pending not taken as the canonical catalog")


def _watch_with_exit(j, tmp, exit_code, label, run_id="run-a"):
    job = {"id": f"stage1-{label}-000001", "type": "stage1",
           "instance_id": label, "owner": "x@y", "status": "running",
           "params": {}, "_inst": {"label": label},
           "_started_ts": time.time() - 5.0, "_run_id": run_id}
    with j._lock:
        j._jobs[job["id"]] = job
    cmd = [sys.executable, "-c", f"import sys; sys.exit({exit_code})"]
    try:
        j._watch(job, [cmd], None)
    finally:
        with j._lock:
            j._jobs.pop(job["id"], None)
    return job


def test_watch_stage1_exit4_with_catalog_is_verdict(jobs_env):
    j, tmp = jobs_env
    _catalog(tmp, "s1ok", "run-a")
    job = _watch_with_exit(j, tmp, 4, "s1ok")
    assert job["status"] == "succeeded", job
    assert job["gate_nonzero"] is True
    assert job["exit_code"] == 4
    print("_watch end-to-end: exit 4 + catalog = verdict-success")


def test_watch_stage1_exit4_without_catalog_is_failure(jobs_env):
    # Код 4 без опубликованного каталога — настоящий сбой (fail-closed).
    j, tmp = jobs_env
    job = _watch_with_exit(j, tmp, 4, "s1none")
    assert job["status"] == "failed", job
    assert "gate_nonzero" not in job
    print("_watch end-to-end: exit 4 without catalog = failure")


def test_watch_stage1_other_nonzero_stays_failure(jobs_env):
    # Свежий каталог НЕ прощает другие коды: 2/3 его не пишут, а
    # неизвестный ненулевой код — сбой по fail-closed.
    j, tmp = jobs_env
    _catalog(tmp, "s1e3", "run-a")
    checked = 0
    for code in (1, 2, 3):
        job = _watch_with_exit(j, tmp, code, "s1e3")
        assert job["status"] == "failed", (code, job)
        checked += 1
    print(f"exit codes that stay failures with a fresh catalog: {checked}")
    assert checked == 3
