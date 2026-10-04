# -*- coding: utf-8 -*-
"""Журналы прогона в панели (проект повтора, этап Р1): рождение и стадии
оставляют действия с параметрами и файлами, вызовы моделей журналируются
под номером действия, отказ ручки — строка refused, паспорт несёт счётчики,
сброс сохраняет факты рождения; гейт «каждая изменяющая ручка журналирует»."""
import hashlib
import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cdpm import actions_journal as aj
from cdpm import call_journal as cj
from cdpm import webui
from test_cdpm_extract import _RULES_A, _RULES_B, fake_llm


@pytest.fixture(autouse=True)
def _s06r2_declared_layout(declared_test_layout):
    cj.forget()
    aj.forget()
    aj.set_current(None)
    yield
    cj.forget()
    aj.forget()
    aj.set_current(None)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    monkeypatch.setattr(webui, "llm_factory", lambda scope: fake_llm)
    monkeypatch.setattr(webui, "exchange_llm_factory",
                        lambda scope, requested: fake_llm)
    return TestClient(webui.app)


def _create(client) -> str:
    r = client.post("/api/runs", files=[
        ("files", ("RULES-A.md", _RULES_A.encode("utf-8"), "text/markdown")),
        ("files", ("RULES-B.md", _RULES_B.encode("utf-8"), "text/markdown")),
    ], params={"customer": "Полярис (тест)", "data_class": "synthetic"})
    assert r.status_code == 200, r.text
    return r.json()["run"]


def _wait(client, run_id: str, busy: str, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = client.get(f"/api/runs/{run_id}").json()
        if st["state"] != busy:
            return st
        time.sleep(0.1)
    raise AssertionError(f"стадия не завершилась: {busy}")


def test_birth_and_extract_leave_actions_and_calls(client):
    run_id = _create(client)
    run_dir = webui._run_dir(run_id)
    acts = aj.actions(run_dir)
    assert [a["action"] for a in acts] == ["create"]
    birth = acts[0]
    assert birth["done"] is True and birth["refused"] is False
    assert birth["params"]["customer"] == "Полярис (тест)"
    assert birth["params"]["data_class"] == "synthetic"
    assert [f["name"] for f in birth["files"]] == ["RULES-A.md", "RULES-B.md"]
    assert birth["files"][0]["sha256"] == hashlib.sha256(
        _RULES_A.encode("utf-8")).hexdigest()
    assert birth["files"][0]["stored"] == "src/RULES-A.md"
    assert birth["state_after"]["state"] == "заморожено"
    assert birth["calls"] == [0, 0]                      # зрения не было
    # Извлечение — второе действие с параметрами, вызовы под его номером.
    r = client.post(f"/api/runs/{run_id}/extract")
    assert r.status_code == 200, r.text
    st = _wait(client, run_id, "извлечение идёт")
    assert st["state"] == "извлечено"
    acts = aj.actions(run_dir)
    assert [a["action"] for a in acts] == ["create", "extract"]
    ex = acts[1]
    assert ex["params"] == {"customer": "", "scope": None}
    assert ex["state_before"]["state"] == "заморожено"
    assert ex["done"] is True and ex["state_after"]["state"] == "извлечено"
    assert ex["duration_s"] is not None and ex["error"] is None
    idx = cj.read_index(run_dir)
    assert idx, "журнал вызовов пуст после извлечения"
    assert ex["calls"] == [0, len(idx)]
    assert {r["action_n"] for r in idx} == {2}
    assert {r["role"] for r in idx} <= {"extractor", "second_reader",
                                        "logic_verifier"}
    assert all(r["stage"] == "извлечение" for r in idx)
    assert all(r["kind"] == "llm" and r["served_from"] is None for r in idx)
    for r in idx[:3]:
        assert cj.read_body(run_dir, r["input"]["prompt_sha256"])
        assert cj.read_body(run_dir, r["reply_sha256"]) is not None
    man = cj.manifest(run_dir)
    assert man["mode"] == "record" and man["counts"]["llm"] == len(idx)
    assert man["versions"]["roles"]
    # Паспорт несёт счётчики журналов.
    j = st["passport"]["journals"]
    assert j["calls"] == len(idx) and j["calls_complete"] is True
    assert j["actions"] == 2
    assert all(s["calls_code"] == "live" for s in st["passport"]["stages"])


def test_refused_action_is_recorded_not_replayed(client):
    run_id = _create(client)
    run_dir = webui._run_dir(run_id)
    r = client.put(f"/api/runs/{run_id}/kit-scope", json={"чужое": 1})
    assert r.status_code == 400
    acts = aj.actions(run_dir)
    assert acts[-1]["action"] == "kit-scope" and acts[-1]["refused"] is True
    assert acts[-1]["status"] == 400 and "неизвестные поля" in acts[-1]["detail"]
    # Ручка без прогона журнала не пишет (404 до записи).
    assert client.post("/api/runs/run-9999/extract").status_code == 404


def test_data_upload_and_config_actions_carry_files_and_bodies(client):
    run_id = _create(client)
    run_dir = webui._run_dir(run_id)
    body = b"col\n1\n"
    r = client.post(f"/api/runs/{run_id}/data",
                    files=[("files", ("D.csv", body, "text/csv"))])
    assert r.status_code == 200, r.text
    r = client.put(f"/api/runs/{run_id}/files-config", json={
        "files": {"RULES-B.md": {"processing": "local"}}})
    assert r.status_code == 200, r.text
    acts = aj.actions(run_dir)
    names = [a["action"] for a in acts]
    assert names == ["create", "data", "files-config"]
    data = acts[1]
    assert data["kind"] == "sync" and data["done"] is True
    assert data["files"] == [{"name": "D.csv", "bytes": len(body),
                              "sha256": hashlib.sha256(body).hexdigest(),
                              "stored": "data-src/D.csv"}]
    cfg = acts[2]
    assert cfg["params"]["body"] == {"files": {"RULES-B.md": {"processing": "local"}}}
    assert cfg["done"] is True and cfg["state_after"]


def test_reset_keeps_birth_facts_and_journals(client):
    run_id = _create(client)
    run_dir = webui._run_dir(run_id)
    client.post(f"/api/runs/{run_id}/extract")
    st = _wait(client, run_id, "извлечение идёт")
    born = st["passport"]["created"]
    n_calls = cj.count(run_dir)
    assert n_calls > 0
    r = client.post(f"/api/runs/{run_id}/reset")
    assert r.status_code == 200, r.text
    st = client.get(f"/api/runs/{run_id}").json()
    assert st["state"] == "заморожено"
    assert st["created_at"] and st["passport"]["created"] == born
    # Журналы сброс не трогает: сброс — тоже действие.
    assert cj.count(run_dir) == n_calls
    assert [a["action"] for a in aj.actions(run_dir)] == ["create", "extract", "reset"]
    assert aj.actions(run_dir)[-1]["done"] is True


def test_every_mutating_run_route_is_journaled():
    """Гейт: каждая ручка, меняющая содержание прогона, — действие
    журнала (декоратор `_journaled` либо явная запись в функции «от
    байтов»); исключения — только оформление списка."""
    src = Path(webui.__file__).read_text(encoding="utf-8")
    # Золотой прогон (шаг 3 проекта «Запись о прогоне», 12.2) — тоже
    # действие панели, но записывается ЯВНО и только состоявшееся:
    # строка `refused` декоратора сделала бы завершённый прогон
    # «остановленным по правилу» (`run_record._record_state` судит по
    # последнему исходу журнала), а отказанное нажатие содержания
    # прогона не меняет и права менять его состояние не имеет.
    explicit = {"/api/runs", "/api/runs/{run_id}/answers",
                "/api/runs/{run_id}/third-party", "/api/runs/{run_id}/data",
                "/api/runs/{run_id}/golden",
                "/api/runs/{run_id}/golden/unmark"}
    # Повтор (Р2) содержания источника не меняет — только указатель на
    # потомка; рождение и действия потомка журналирует сам потомок.
    # `record/append` (шаг 2 проекта «Запись о прогоне», 11.2) —
    # ХРАНИТЕЛЬ записи: панель принимает чужие записи этапов (COSMOS) в
    # `journal/record-appends.jsonl`; содержания прогона это не меняет и
    # действием панели не является (иначе журнал панели и запись прогона
    # заросли бы строками «панель приняла чужую строку»).
    excluded = {"/api/runs/{run_id}/title", "/api/runs/order",
                "/api/runs/archive", "/api/runs/{run_id}/replay",
                "/api/runs/{run_id}/record/append"}
    seen: set = set()
    for m in re.finditer(r'@app\.(post|put|delete)\("(/api/runs[^"]*)"\)\n'
                         r'((?:@[^\n]*\n)*)(?:async )?def (\w+)', src):
        path, decos = m.group(2), m.group(3)
        seen.add(path)
        if path in excluded:
            assert "@_journaled(" not in decos, path
            continue
        nxt = src.find("\n@app.", m.end())
        region = src[m.start(): nxt if nxt > 0 else len(src)]
        if path in explicit:
            assert "actions_journal.note(" in region, path
        else:
            assert "@_journaled(" in decos, f"{path}: ручка без журнала действий"
    assert explicit <= seen and excluded <= seen
    assert "/api/runs/{run_id}/extract" in seen
