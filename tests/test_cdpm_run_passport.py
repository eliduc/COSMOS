# -*- coding: utf-8 -*-
"""Паспорт прогона — рецепт воспроизведения (слова владельца 07.09.2026:
«прогон должен хранить также модели и солверы, которые использовались»;
«информация о прогоне должна давать возможность его однозначно
воспроизвести»).
"""
import json
import re
import time

import pytest
from fastapi.testclient import TestClient

from cdpm import run_passport as rp
from cdpm import webui
from test_cdpm_extract import _RULES_A, _RULES_B, fake_llm


@pytest.fixture(autouse=True)
def _s06r2_declared_layout(declared_test_layout):
    yield


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


def _wait_extract(client, run_id: str, timeout_s: float = 30.0) -> dict:
    """Извлечение идёт фоном: ждём, пока линия статуса освободится."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = client.get(f"/api/runs/{run_id}").json()
        if st["state"] != "извлечение идёт":
            return st
        time.sleep(0.1)
    raise AssertionError("извлечение не завершилось за отведённое время")


def test_environment_is_recorded_once_and_has_code_and_solvers():
    env = rp.environment()
    # Неизменная часть считается один раз на процесс; блок каталога —
    # ВНЕ кэша (шаг 7 переезда: починку площадки нельзя запоминать
    # отказом на всю жизнь панели), поэтому равенство, а не тождество.
    assert env == rp.environment()
    assert rp._stable_environment() is rp._stable_environment()
    assert set(env) >= {"code", "spec_version", "python", "packages", "catalog"}
    assert env["spec_version"]
    assert set(env["packages"]) == set(rp.SOLVER_PACKAGES)
    assert env["packages"]["openpyxl"]      # стоит в окружении тестов
    assert re.fullmatch(r"\d+\.\d+\.\d+", env["python"])
    # Код — либо коммит, либо честный null вне git; оба — не выдумка.
    code = env["code"]
    assert set(code) == {"commit", "short"}
    if code["commit"]:
        assert re.fullmatch(r"[0-9a-f]{40}", code["commit"])
        assert code["short"] == code["commit"][:7]
    # Каталог шаблонов — версия, отпечаток и признак порядка словаря
    # (шаг 7 переезда): отметка берётся у моста той же дверью, что
    # штампует посадки правил и ключ manifest.catalog. Каталог
    # обязателен, но паспорт от его отсутствия не падает — беда
    # называется словами в поле error (тест ниже).
    from cdpm import catalog_bridge as bridge
    assert set(env["catalog"]) == {"version", "fingerprint",
                                   "dictionary_order"}
    assert env["catalog"]["dictionary_order"] == bridge.DICTIONARY_ORDER
    assert env["catalog"] == bridge.stamp_or_error()
    assert re.fullmatch(r"[0-9a-f]{64}", env["catalog"]["fingerprint"])


def test_passport_catalog_block_says_the_trouble_in_words(monkeypatch,
                                                          tmp_path):
    """Без каталога паспорт НЕ падает и не выдумывает: блок `catalog`
    несёт слова отказа, версия и отпечаток — null. Блок считается вне
    кэша окружения, поэтому починка контура видна следующему вызову."""
    from cdpm import catalog_bridge as bridge
    monkeypatch.setenv(bridge.ENV_CATALOG, str(tmp_path / "нет-каталога"))
    bridge.forget()
    try:
        cat = rp.environment()["catalog"]
        assert cat["version"] is None and cat["fingerprint"] is None
        assert "каталог" in cat["error"].lower(), cat
        assert "dictionary_order" not in cat
        # Паспорт и «чем сделан пакет» строятся и без каталога.
        status = {"created_at": "2026-09-08T00:00:00",
                  "passport": {"environment": rp.environment()}}
        assert rp.passport_of(tmp_path, status)["created"]["catalog"] == cat
        assert rp.made_by_of(tmp_path, status)["product"] == "CDPM"
        assert rp.summary_of(status)["catalog"] is None
    finally:
        monkeypatch.delenv(bridge.ENV_CATALOG, raising=False)
        bridge.forget()
    assert rp.environment()["catalog"]["fingerprint"], "починка видна сразу"


def test_run_birth_writes_environment_and_status_exposes_passport(client):
    run_id = _create(client)
    st = client.get(f"/api/runs/{run_id}").json()
    born = st["passport"]
    assert born["created"] == rp.environment()
    assert born["created_at"] == st["created_at"]
    docs = born["inputs"]["documents"]
    assert [d["file"] for d in docs] == ["RULES-A.md", "RULES-B.md"]
    assert all(re.fullmatch(r"[0-9a-f]{64}", d["sha256"]) for d in docs)
    assert born["inputs"]["data"] == [] and born["inputs"]["answers"] is None
    assert {k: born["journals"][k] for k in ("extract_cache", "exchange_rounds",
                                             "answers_revisions")} == {
        "extract_cache": 0, "exchange_rounds": 0, "answers_revisions": 0}
    # Журналы повтора (этап Р1): действие «рождение» записано, вызовов нет.
    assert born["journals"]["actions"] == 1
    assert born["journals"]["calls"] == 0
    # Модели по стадиям появляются после стадий; до них — пусто.
    assert born["stages"] == []


def test_stage_channel_journal_carries_code_and_appears_in_passport(client):
    run_id = _create(client)
    run_dir = webui._run_dir(run_id)
    # Стадия извлечения на поддельном канале с отпечатком роли.
    r = client.post(f"/api/runs/{run_id}/extract")
    assert r.status_code == 200, r.text
    st = _wait_extract(client, run_id)
    rows = st["passport"]["stages"]
    assert rows, "журнал каналов стадий пуст после извлечения"
    roles = {row["role"] for row in rows}
    assert "extractor" in roles or "worker" in roles
    for row in rows:
        assert set(row) >= {"role", "role_title", "klass", "stage",
                            "provider", "model", "channel", "code"}
        assert row["role_title"]
    # Коммит стадии совпадает с коммитом панели (одна панель — один код).
    assert all(row["code"] == rp.code_short() for row in rows)
    # Краткая строка списка — те же модели.
    listed = [x for x in client.get("/api/runs").json() if x["run"] == run_id][0]
    summary = listed["passport_summary"]
    assert set(summary) == {"models", "solvers", "code", "catalog"}
    assert summary["code"] == rp.code_short()
    assert summary["models"], summary
    assert rp.journals_of(run_dir)["extract_cache"] >= 0


def test_inputs_reflect_data_and_answers_on_disk(client, tmp_path):
    run_id = _create(client)
    run_dir = webui._run_dir(run_id)
    (run_dir / "data-freeze").mkdir()
    (run_dir / "data-freeze" / "manifest.json").write_text(json.dumps({
        "files": [{"sid": "D1", "file": "DATA.xlsx", "sha256": "a" * 64,
                   "rows_seen": 135}], "sheets": []}), encoding="utf-8")
    rev = run_dir / "answers" / "rev-002"
    rev.mkdir(parents=True)
    (rev / "anketa-otvety.xlsx").write_bytes(b"book-bytes")
    inputs = rp.inputs_of(run_dir)
    assert inputs["data"] == [{"sid": "D1", "file": "DATA.xlsx",
                               "sha256": "a" * 64, "rows": 135}]
    assert inputs["answers"]["revision"] == "rev-002"
    assert inputs["answers"]["file"] == "anketa-otvety.xlsx"
    assert re.fullmatch(r"[0-9a-f]{64}", inputs["answers"]["sha256"])
    assert rp.journals_of(run_dir)["answers_revisions"] == 1


def test_passport_never_invents_when_disk_is_empty(tmp_path):
    empty = tmp_path / "run-0000"
    empty.mkdir()
    p = rp.passport_of(empty, {})
    assert p["created"] is None and p["inputs"] == {
        "documents": [], "data": [], "answers": None}
    assert p["stages"] == [] and p["journals"] == {
        "calls": 0, "calls_complete": None, "actions": 0,
        "extract_cache": 0, "exchange_rounds": 0, "answers_revisions": 0}
    assert rp.summary_of({}) == {"models": {}, "solvers": [], "code": None,
                                 "catalog": None}
