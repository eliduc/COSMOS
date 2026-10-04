# -*- coding: utf-8 -*-
"""Устойчивость головы записи о прогоне (находка COSMOS 10.09.2026 19:4x).

У папки прогона без status.json момент рождения реконструированной головы
брался с часов: отпечаток объявления (в него входит created_at), а с ним
и manifest.json пакета, плыл через границу секунды — два экспорта одной
папки давали разные байты (ломался закон Ф-1.7 байт-идентичного
манифеста). Теперь момент рождения устойчив: статус → паспорт → первое
действие журнала → время рождения папки; часы не читаются."""
from __future__ import annotations

import json
import time

import pytest

from cdpm import run_record as rr


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(autouse=True)
def _no_clock(monkeypatch):
    """Часы для головы читаться не должны — любой вызов роняет тест."""
    def boom():
        raise AssertionError("created_at головы взят с часов")
    monkeypatch.setattr(rr, "_now_with_zone", boom)


def test_created_at_comes_from_first_action_when_status_is_absent(tmp_path):
    run = tmp_path / "run-0001"
    (run / "src").mkdir(parents=True)
    (run / "journal").mkdir()
    (run / "journal" / "actions.jsonl").write_text(
        json.dumps({"n": 1, "action": "create",
                    "at": "2026-09-10T07:00:00+02:00", "kind": "start",
                    "files": []}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    first = rr.build(run)
    assert first["declaration"]["created_at"] == "2026-09-10T07:00:00+02:00"
    time.sleep(1.1)                                   # граница секунды
    again = rr.build(run)
    assert again["declaration"]["created_at"] == first["declaration"]["created_at"]
    assert again["declaration_sha256"] == first["declaration_sha256"]


def test_head_of_bare_folder_is_stable_across_a_second(tmp_path):
    """Папка без статуса и без журнала (стенды, синтетика): отметка —
    время рождения папки; два счёта через секунду дают один отпечаток,
    и manifest.json пакета не может разойтись из-за часов."""
    run = tmp_path / "run-0002"
    (run / "src").mkdir(parents=True)
    a = rr.declaration_sha256_of(run)
    created = rr.build(run)["declaration"]["created_at"]
    time.sleep(1.1)
    assert rr.declaration_sha256_of(run) == a
    assert rr.build(run)["declaration"]["created_at"] == created
    assert created and created != "1970-01-01T00:00:00+00:00"


def test_status_and_passport_moments_win_over_journal(tmp_path):
    run = tmp_path / "run-0003"
    (run / "journal").mkdir(parents=True)
    (run / "journal" / "actions.jsonl").write_text(
        json.dumps({"n": 1, "action": "create",
                    "at": "2026-09-10T07:00:00+02:00", "kind": "start"},
                   ensure_ascii=False) + "\n", encoding="utf-8")
    _write(run / "status.json",
           {"passport": {"at": "2026-09-10T06:59:59+02:00"}})
    assert rr.build(run)["declaration"]["created_at"] == "2026-09-10T06:59:59+02:00"
    _write(run / "status.json",
           {"created_at": "2026-09-10T06:59:58+02:00",
            "passport": {"at": "2026-09-10T06:59:59+02:00"}})
    assert rr.build(run)["declaration"]["created_at"] == "2026-09-10T06:59:58+02:00"


def test_sync_action_without_end_is_closed_not_aborted(tmp_path):
    """Живая находка 10.09.2026 (run-0033 на Test): загрузка книги данных
    журналируется одной строкой kind «sync» без записи об окончании, а
    сборщик читал её как «сбой/оборвано» — и state_from_journal словаря
    давал прогону «сбой» при состоянии хранителя «завершён». Синхронное
    действие закрыто по определению: конец — момент записи."""
    run = tmp_path / "run-0005"
    (run / "journal").mkdir(parents=True)
    acts = [
        {"n": 1, "action": "create", "at": "2026-09-10T22:42:20+02:00",
         "kind": "start", "files": []},
        {"n": 1, "kind": "end", "at": "2026-09-10T22:42:28+02:00",
         "duration_s": 8.0, "state_after": {"state": "заморожено"}, "error": None},
        {"n": 2, "action": "data", "at": "2026-09-10T22:42:30+02:00",
         "kind": "sync", "files": [{"name": "ID-H0-DATA.xlsx", "sha256": "5" * 64,
                                    "stored": "data-src/ID-H0-DATA.xlsx"}]},
        {"n": 3, "action": "extract", "at": "2026-09-10T22:42:40+02:00",
         "kind": "start", "files": []},
        {"n": 3, "kind": "end", "at": "2026-09-10T22:44:00+02:00",
         "duration_s": 80.0, "state_after": {"state": "извлечено"}, "error": None},
    ]
    (run / "journal" / "actions.jsonl").write_text(
        "\n".join(json.dumps(a, ensure_ascii=False) for a in acts) + "\n",
        encoding="utf-8")
    _write(run / "status.json", {"state": "извлечено", "data_state": "данные заморожены",
                                 "created_at": "2026-09-10T22:42:20+02:00"})
    entries = rr.journal_entries(run)
    data = [e for e in entries if e["substage"] == "data"]
    assert len(data) == 1
    assert data[0]["outcome"] == "успех" and data[0]["reason_code"] is None
    assert data[0]["finished_at"] == "2026-09-10T22:42:30+02:00"
    assert all(e["outcome"] != "сбой" for e in entries)


def test_data_upload_action_records_its_end(tmp_path, monkeypatch):
    """Панель: загрузка книги данных закрывается записью об окончании
    (как все действия), и запись о прогоне не несёт «сбоя»."""
    from fastapi.testclient import TestClient

    from cdpm import webui
    from test_cdpm_extract import _RULES_A
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "cdpm-settings.json"))
    monkeypatch.setenv("CREW_CATALOG_URL", "http://catalog.invalid")
    client = TestClient(webui.app)
    got = client.post("/api/runs", files=[
        ("files", ("RULES-A.md", _RULES_A.encode("utf-8"), "text/markdown"))],
        params={"data_class": "proprietary"})
    assert got.status_code == 200, got.text
    run_id = got.json()["run"]
    book = (tmp_path / "DATA.csv")
    book.write_text("a,b\n1,2\n", encoding="utf-8")
    up = client.post(f"/api/runs/{run_id}/data", files=[
        ("files", ("DATA.csv", book.read_bytes(), "text/csv"))])
    assert up.status_code in (200, 422), up.text
    acts = [json.loads(line) for line in
            (tmp_path / "runs" / run_id / "journal" / "actions.jsonl")
            .read_text(encoding="utf-8").splitlines() if line.strip()]
    data_n = [a["n"] for a in acts if a.get("action") == "data"]
    assert data_n, "действие data не записано"
    assert any(a.get("kind") == "end" and a.get("n") == data_n[0] for a in acts), \
        "у действия data нет записи об окончании"
    rec = rr.build(tmp_path / "runs" / run_id)
    assert all(e["outcome"] != "сбой" for e in rec["journal"])
