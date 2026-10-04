# -*- coding: utf-8 -*-
"""Личность прогона переживает перезапуск сервера.

Вопрос владельца 2026-08-13: «после окончания пейринга и перезагрузки
браузера — как снова увидеть график?». Кнопка «Журнал» в истории
возвращает график всегда, ПОКА жив процесс приложения: файл машинного
следа схождения назван личностью прогона (CREW_RUN_ID), а она хранилась
только в памяти (поле на подчёркивании, а `_save` такие поля отсекал).
После перезапуска приложения — в том числе после любого раската —
`run_id_of` возвращал None, эндпоинт хода искал след под именем задачи и
не находил: график завершённого прогона исчезал навсегда.

Момент старта хранится там же и по той же причине: живое время из
бюджета считается сервером от него.
"""
import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


@pytest.fixture()
def jobs(tmp_path, monkeypatch):
    from webapp import jobs as j
    monkeypatch.setattr(j, "JOBS_DIR", tmp_path, raising=False)
    monkeypatch.setattr(j, "_job_path", lambda jid: tmp_path / f"{jid}.json")
    monkeypatch.setattr(j, "_log_path", lambda jid: tmp_path / f"{jid}.log")
    return j, tmp_path


def _job(jid="stage1-x-000001", **extra):
    job = {"id": jid, "type": "stage1", "instance_id": "x",
           "owner": "a@b", "status": "succeeded", "params": {},
           "_run_id": "run-7f3a", "_started_ts": 1755000000.0,
           "_inst": {"label": "uz-x"}}
    job.update(extra)
    return job


def _restart(j, tmp):
    """Перезапуск сервера: память пуста, на диске — только файлы."""
    with j._lock:
        j._jobs.clear()
    j._loaded = False
    j._load_persisted()


def test_run_identity_survives_restart(jobs):
    j, tmp = jobs
    j._save(_job())
    _restart(j, tmp)
    try:
        assert j.run_id_of("stage1-x-000001") == "run-7f3a"
        print("run identity restored after restart")
    finally:
        with j._lock:
            j._jobs.clear()
        j._loaded = False


def test_started_ts_survives_restart(jobs):
    j, tmp = jobs
    j._save(_job(status="running", pid=None))
    _restart(j, tmp)
    try:
        with j._lock:
            job = j._jobs["stage1-x-000001"]
        assert job["_started_ts"] == 1755000000.0
        print("start timestamp restored after restart")
    finally:
        with j._lock:
            j._jobs.clear()
        j._loaded = False


def test_public_payload_unchanged(jobs):
    # Публичный контракт не меняется: служебный блок наружу не выходит.
    j, tmp = jobs
    job = _job()
    j._save(job)
    _restart(j, tmp)
    try:
        pub = j.get_job("stage1-x-000001")
        leaked = [k for k in pub if k.startswith("_")]
        assert not leaked, leaked
        assert "run_id" not in pub
        print(f"public fields: {len(pub)}, internal leaks: 0")
    finally:
        with j._lock:
            j._jobs.clear()
        j._loaded = False


def test_file_carries_internal_block(jobs):
    j, tmp = jobs
    j._save(_job())
    doc = json.loads((tmp / "stage1-x-000001.json").read_text(
        encoding="utf-8"))
    assert doc["_internal"] == {"_run_id": "run-7f3a",
                                "_started_ts": 1755000000.0}
    # Ссылка на экземпляр НЕ сохраняется: она пересобирается из реестра.
    assert "_inst" not in doc and "_inst" not in doc["_internal"]
    print("on disk: internal block with two fields, nothing extra")


def test_identity_recovered_from_manifest_and_written(jobs, monkeypatch,
                                                      tmp_path):
    """Записи ДО фикса личности не несут — она восстанавливается по
    идентификатору постановки (манифест лежит в каталоге, имя которого и
    есть личность) и сохраняется, чтобы поиск был разовым."""
    j, tmp = jobs
    from webapp import config_scope, problem_manifest
    runs = tmp_path / "runs-config"
    for name, mid in (("run-aaa", "m-старый"), ("run-bbb", "m-нужный")):
        d = runs / name
        d.mkdir(parents=True)
        (d / problem_manifest.FILENAME).write_text(
            json.dumps({"manifest_id": mid, "body": {}}), encoding="utf-8")
    monkeypatch.setattr(config_scope, "RUNS_CONFIG_DIR", runs)
    (tmp / "stage1-rec-000003.json").write_text(json.dumps({
        "id": "stage1-rec-000003", "type": "stage1", "status": "succeeded",
        "owner": "a@b", "problem_manifest_id": "m-нужный"}), encoding="utf-8")
    _restart(j, tmp)
    try:
        assert j.run_id_of("stage1-rec-000003") == "run-bbb"
        doc = json.loads((tmp / "stage1-rec-000003.json").read_text(
            encoding="utf-8"))
        assert doc["_internal"]["_run_id"] == "run-bbb"
        print("identity recovered from manifest among 2 dirs and persisted")
    finally:
        with j._lock:
            j._jobs.clear()
        j._loaded = False


def test_old_record_without_internal_still_loads(jobs):
    # Записи прежних прогонов служебного блока не имеют — загрузка обязана
    # работать, просто без личности прогона (графика у них и не было).
    j, tmp = jobs
    (tmp / "stage1-old-000002.json").write_text(json.dumps({
        "id": "stage1-old-000002", "type": "stage1", "status": "succeeded",
        "owner": "a@b"}), encoding="utf-8")
    _restart(j, tmp)
    try:
        assert j.get_job("stage1-old-000002") is not None
        assert j.run_id_of("stage1-old-000002") is None
        print("legacy record without the internal block still loads")
    finally:
        with j._lock:
            j._jobs.clear()
        j._loaded = False
