# -*- coding: utf-8 -*-
"""Расход читателя ИДАК сочтён в записи о прогоне.

Принцип шага 1 системы прогонов: ВСЕ вызовы моделей сочтены в записи.
Сверка решения с исходными документами авиакомпании зовёт читателя
ОТДЕЛЬНЫМ вызовом — вне попытки нажатия, — и его расход в токенах
обязан доехать одним путём с расходом сертификатора: разобранный
`usage` канала (`webapp/llm_backend`) → снимок «последняя сверка» →
ручка `/api/source-check/last` → запись этапа «сверка с ИДАК» летописи
прогона → свод по дереву прогонов. Второго носителя расхода нет.

Три исхода, и путать их нельзя:

  * канал назвал токены — `calls: 1`, входные и выходные, `counted: true`;
  * проверки взяты из кэша (модель не звалась) — `calls: 0`, токены `0`,
    `counted: true` и причина словами «чтение из кэша»: ноль вызовов —
    величина ИЗМЕРЕННАЯ;
  * канал расхода не отдаёт — `counted: false` с причиной словами;
    неизвестное за ноль не выдаётся.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vmp import run_record as rr
from vmp import run_tree
from vmp import source_check_run as scr

from tests.test_task_channel import (  # noqa: F401 — фикстуры pytest
    _channel_env, client)

CLAUSE = {"clauses": [{"anchor": "п.1", "text": "Смена не длиннее 12:00."}]}
SPECS = [{"id": "span", "kind": "duty_span_max", "anchor": "п.1",
          "params": {"limit_min": 720, "rest_split_min": 480}}]
ROSTER = {"flights": [], "employees": [], "flightAssignments": [],
          "score": "0hard/0soft"}
#: Расход вызова читателя так, как его разбирает труба канала модели.
USAGE = {"provider": "anthropic", "counted": True,
         "input_tokens": 4200, "output_tokens": 130}


def _judge(entry: dict) -> dict:
    """Запись этапа судит СЛОВАРЬ записи (`run_record.append_stage`):
    второго перечня полей у стенда нет."""
    record: dict = {}
    rr.append_stage(record, entry)
    return record["journal"][0]


def _clauses(tmp_path: Path, name: str = "run-т") -> Path:
    run_dir = tmp_path / name
    cl = run_dir / "freeze" / "clauses"
    cl.mkdir(parents=True)
    (cl / "S1.json").write_text(json.dumps(CLAUSE, ensure_ascii=False),
                                encoding="utf-8")
    return run_dir


def _fake_compile(calls: list, usage: dict | None):
    """Подставной читатель: расход отдаёт так же, как компилятор —
    полем `usage` возврата (`webapp/source_check_compile.compile_checks`)."""
    def _fn(clauses, scope, requested=None, model=None, timeout_s=0.0,
            effort="medium", channel=None):
        calls.append(model)
        got = {"specs": SPECS, "problems": [], "backend": "cloud",
               "model": model, "channel": channel}
        if usage is not None:
            got["usage"] = dict(usage, model=model, channel=channel)
        return got
    return _fn


def _sverka(tmp_path, monkeypatch, usage: dict | None, *, run_dir: Path,
            calls: list, label: str = "ростеринг (r1)") -> dict:
    """Прогон сверки с подставным читателем; возврат — снимок задачи."""
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    from webapp import source_check_compile as scc
    monkeypatch.setattr(scc, "compile_checks", _fake_compile(calls, usage))
    scr.run_full(run_dir, [(label, ROSTER, "roster", None)],
                 requested="cloud", model="читатель-стенда", channel="api",
                 snap_sub="rostering")
    return json.loads((tmp_path / "data" / "source-check" / "rostering"
                       / "last.json").read_text(encoding="utf-8"))


def _entry(tmp_path, snap: dict, task: str = "rostering") -> dict:
    """Запись этапа «сверка с ИДАК» по снимку — через тот же крючок,
    каким её кладёт служба после сверки."""
    from webapp import run_journal
    wd = tmp_path / "package-runs" / ("f" * 64)
    got = run_journal.note_source_check(wd, task, "A1", snap,
                                        actor="user:стенд")
    return got["row"]["entry"]


# ------------------------------------------- канал назвал токены

def test_расход_читателя_из_канала_доезжает_до_записи(tmp_path, monkeypatch):
    """Токены вызова читателя — в снимке сверки и в записи этапа:
    роль названа словарём ролей, числа те же, что отдал канал."""
    calls: list = []
    snap = _sverka(tmp_path, monkeypatch, USAGE,
                   run_dir=_clauses(tmp_path), calls=calls)
    assert calls == ["читатель-стенда"]
    row = snap["resources"]["models"][0]
    assert row["role"] == "читатель ИДАК" \
        and row["role_code"] == "source_check_reader"
    assert row["calls"] == 1 and row["counted"] is True
    assert (row["input_tokens"], row["output_tokens"]) == (4200, 130)
    assert row["provider"] == "anthropic" and row["channel"] == "api"
    assert snap["compiled_cached"] is False

    entry = _entry(tmp_path, snap)
    assert _judge(entry) == entry, "этап не сошёлся со словарём записи"
    assert entry["stage"] == "сверка с ИДАК"
    got = entry["resources"]["models"][0]
    assert got["role"] == "читатель ИДАК" and got["counted"] is True
    assert got["calls"] == 1
    assert (got["input_tokens"], got["output_tokens"]) == (4200, 130)


# ------------------------------------------- проверки взяты из кэша

def test_кэш_даёт_ноль_вызовов_и_сосчитанный_ноль(tmp_path, monkeypatch):
    """Вторая сверка теми же клаузами читателя НЕ зовёт: вызовов ноль,
    токенов ноль, и этот ноль сосчитан — причина словами «чтение из
    кэша». Прежний расход второму прогону не приписывается."""
    calls: list = []
    run_dir = _clauses(tmp_path)
    _sverka(tmp_path, monkeypatch, USAGE, run_dir=run_dir, calls=calls)
    snap = _sverka(tmp_path, monkeypatch, USAGE, run_dir=run_dir,
                   calls=calls, label="ростеринг (r2)")
    assert calls == ["читатель-стенда"], "второй прогон звал модель заново"
    assert snap["compiled_cached"] is True
    row = snap["resources"]["models"][0]
    assert row["calls"] == 0 and row["counted"] is True
    assert (row["input_tokens"], row["output_tokens"]) == (0, 0)
    assert row["note"].startswith("чтение из кэша")

    entry = _entry(tmp_path, snap)
    assert _judge(entry) == entry
    got = entry["resources"]["models"][0]
    assert got["calls"] == 0 and got["counted"] is True
    assert (got["input_tokens"], got["output_tokens"]) == (0, 0)
    assert got["note"].startswith("чтение из кэша")


# --------------------------------------- канал расхода не отдаёт

def test_канал_без_расхода_честное_не_считается(tmp_path, monkeypatch):
    """Канал, который токенов не называет (CLI подписки), даёт строку
    `counted: false` с причиной словами и без выдуманных нулей."""
    calls: list = []
    snap = _sverka(tmp_path, monkeypatch, None,
                   run_dir=_clauses(tmp_path), calls=calls)
    row = snap["resources"]["models"][0]
    assert row["calls"] == 1 and row["counted"] is False
    assert "api" in row["note"], "причина не назвала канал"
    assert "input_tokens" not in row and "output_tokens" not in row

    entry = _entry(tmp_path, snap)
    assert _judge(entry) == entry
    got = entry["resources"]["models"][0]
    assert got["counted"] is False and got["note"]
    assert got["input_tokens"] is None and got["output_tokens"] is None, \
        "неизвестное выдано за ноль"


def test_снимок_без_расхода_строка_остаётся_с_причиной(tmp_path):
    """Снимок прежней версии расхода не называл: строка читателя всё
    равно стоит — с честным «не считается» и причиной словами. Пропажа
    строки читалась бы «читателя не звали», а это другое."""
    entry = _entry(tmp_path, {
        "status": "done", "at": "2026-09-11T10:00:00", "task": "rostering",
        "counts": {"ok": 3}, "problems": 0,
        "reader": "cloud/модель-читателя", "reader_channel": "api"})
    assert _judge(entry) == entry
    row = entry["resources"]["models"][0]
    assert row["role"] == "читатель ИДАК"
    assert row["model"] == "модель-читателя", "контур попал в имя модели"
    assert row["counted"] is False and row["calls"] is None
    assert "api" in row["note"] and "не назвал" in row["note"]

    # Читателя не назвал и снимок — звать было некого, строки нет.
    empty = _entry(tmp_path, {"status": "error", "at": "2026-09-11T10:05:00",
                              "error": "клаузы заморозки пусты"},
                   task="pairing")
    assert empty["resources"]["models"] == []


# --------------------------------------------- отказавшая сверка

def test_отказ_компилятора_снимок_несёт_потраченный_расход(client, tmp_path,
                                                           monkeypatch):
    """Сверка падает и ПОСЛЕ ответа читателя: ответ пришёл, а компилятор
    его не разобрал. Токены на этот вызов потрачены — снимок отказа
    обязан их назвать тем же путём, что и снимок удачи, и запись этапа
    «сверка с ИДАК» должна показать строку читателя сосчитанной.
    Молчание снимка читалось бы «читателя не звали», а его звали."""
    from tests.test_source_check_blocks import _handler_env
    from vmp import source_check_run as scr
    from webapp import llm_backend

    env = _handler_env(tmp_path, monkeypatch)

    def _boom(*a, **kw):
        # Так падает разбор ответа: труба канала расход уже положила
        # (compile_checks её только подсматривает), компилятор — отказ.
        llm_backend.set_last_usage(dict(USAGE, model="claude-sonnet-5",
                                        channel="cli"))
        raise ValueError("в ответе читателя нет JSON-массива")
    monkeypatch.setattr(scr, "run_full", _boom)

    r = client.post("/api/source-check/run",
                    json={"task": "rostering",
                          "solverRunId": env["roster"]["runId"]})
    assert r.status_code == 202, r.text
    snap = json.loads((tmp_path / "source-check" / "rostering" / "last.json")
                      .read_text(encoding="utf-8"))
    assert snap["status"] == "error" and "JSON" in snap["error"]
    row = snap["resources"]["models"][0]
    assert row["role_code"] == "source_check_reader"
    assert row["calls"] == 1 and row["counted"] is True
    assert (row["input_tokens"], row["output_tokens"]) == (4200, 130)
    assert llm_backend.peek_last_usage() is None, "расход снят не один раз"

    entry = _entry(tmp_path, snap)
    assert _judge(entry) == entry
    assert entry["reason_code"] == "source-check-failed"
    got = entry["resources"]["models"][0]
    assert got["counted"] is True and got["calls"] == 1
    assert (got["input_tokens"], got["output_tokens"]) == (4200, 130)


def test_отказ_сверки_ложится_этапом_записи_сам(client, tmp_path, monkeypatch):
    """Крючок записи зовёт САМА служба — на обоих путях, а не только на
    удачном. Отказ компилятора ПОСЛЕ ответа читателя даёт запись с
    этапом «сверка с ИДАК», исходом «сбой», кодом `source-check-failed`
    и строкой расхода читателя из снимка отказа. Молчание записи
    читалось бы «сверки не было», а она была — и токены потрачены."""
    from tests.test_source_check_blocks import _handler_env
    from vmp import source_check_run as scr
    from webapp import llm_backend, run_journal

    env = _handler_env(tmp_path, monkeypatch)

    def _boom(*a, **kw):
        llm_backend.set_last_usage(dict(USAGE, model="claude-sonnet-5",
                                        channel="cli"))
        raise ValueError("в ответе читателя нет JSON-массива")
    monkeypatch.setattr(scr, "run_full", _boom)

    r = client.post("/api/source-check/run",
                    json={"task": "rostering",
                          "solverRunId": env["roster"]["runId"]})
    assert r.status_code == 202, r.text
    rows = run_journal.entries(tmp_path / "package-runs" / env["sha"])
    assert len(rows) == 1, "этапа отказавшей сверки в записи нет: %s" % rows
    entry = rows[0]["entry"]
    assert _judge(entry) == entry
    assert entry["stage"] == run_journal.STAGE_SOURCE_CHECK
    assert entry["outcome"] == rr.OUTCOME_FAILURE
    assert entry["reason_code"] == "source-check-failed"
    assert "JSON" in entry["words"]
    got = entry["resources"]["models"][0]
    assert got["role"] == "читатель ИДАК"
    assert got["counted"] is True and got["calls"] == 1
    assert (got["input_tokens"], got["output_tokens"]) == (4200, 130)


def test_отказ_без_расхода_честное_не_считается_с_причиной(client, tmp_path,
                                                           monkeypatch):
    """Отказ ДО ответа читателя (расхода в потоке нет) выдуманных нулей
    не пишет: строка читателя стоит с `counted: false` и причиной
    словами, называющей канал, — неизвестное за ноль не выдаётся."""
    from tests.test_source_check_blocks import _handler_env
    from vmp import source_check_run as scr
    from webapp import llm_backend

    env = _handler_env(tmp_path, monkeypatch)
    llm_backend.take_last_usage()                  # чужой несняток — прочь

    def _boom(*a, **kw):
        raise RuntimeError("читатель не ответил")
    monkeypatch.setattr(scr, "run_full", _boom)

    r = client.post("/api/source-check/run",
                    json={"task": "rostering",
                          "solverRunId": env["roster"]["runId"]})
    assert r.status_code == 202, r.text
    snap = json.loads((tmp_path / "source-check" / "rostering" / "last.json")
                      .read_text(encoding="utf-8"))
    assert snap["status"] == "error"
    row = snap["resources"]["models"][0]
    assert row["counted"] is False and row["note"]
    assert "cli" in row["note"], "причина не назвала канал читателя"
    assert row["model"] == "claude-sonnet-5"
    assert "input_tokens" not in row and "output_tokens" not in row


# ------------------------------------------------- ручка последней сверки

def test_ручка_последней_сверки_несёт_расход(client, tmp_path, monkeypatch):
    """`/api/source-check/last` отдаёт расход читателя рядом с
    `reader` / `reader_channel`; снимок прежней версии — пустой перечень
    с кодом пробела, а не пропажа поля."""
    import _data_paths
    from tests.test_source_check_blocks import _source_context, _selected_context
    _selected_context(monkeypatch)
    monkeypatch.setattr(_data_paths, "data_root", lambda: tmp_path)
    d = tmp_path / "source-check" / "rostering"
    d.mkdir(parents=True)
    models = [{"role": "читатель ИДАК", "role_code": "source_check_reader",
               "provider": "anthropic", "model": "модель-читателя",
               "channel": "api", "calls": 1, "counted": True,
               "input_tokens": 4200, "output_tokens": 130}]
    (d / "last.json").write_text(json.dumps({
        **_source_context("rostering"),
        "status": "done", "at": "2026-09-11T11:00:00", "run": "run-1",
        "reader": "cloud/модель-читателя", "reader_channel": "api",
        "counts": {"ok": 2}, "resources": {"models": models}},
        ensure_ascii=False), encoding="utf-8")
    r = client.get("/api/source-check/last?task=rostering")
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["reader_channel"] == "api"
    assert got["resources"]["models"] == models

    (d / "last.json").write_text(json.dumps({
        **_source_context("rostering"),
        "status": "done", "at": "2026-09-11T11:05:00", "run": "run-1",
        "reader": "cloud/модель-читателя", "counts": {"ok": 2}},
        ensure_ascii=False), encoding="utf-8")
    old = client.get("/api/source-check/last?task=rostering").json()
    assert old["resources"]["models"] == []
    assert old["resources"]["models_code"] == "snapshot-without-resources"


# ------------------------------------------------- свод по дереву прогонов

def _stage(models: list, stage: str = "сверка с ИДАК") -> dict:
    return rr.normalize_entry({"stage": stage, "substage": "A1/rostering",
                               "outcome": rr.OUTCOME_OK,
                               "resources": {"models": models}})


def test_свод_по_дереву_складывает_строки_читателя():
    """Свод расхода по записи складывает строки роли «читатель ИДАК»
    как есть: живой вызов и ноль из кэша дают одну сумму, форма свода не
    меняется. Несосчитанный вызов метит сумму как несосчитанную —
    половина расхода, выданная за целое, хуже честного «не считается»."""
    live = {"role": "читатель ИДАК", "model": "модель-читателя",
            "provider": "anthropic", "calls": 1, "counted": True,
            "input_tokens": 4200, "output_tokens": 130}
    cached = {"role": "читатель ИДАК", "model": "модель-читателя",
              "provider": None, "calls": 0, "counted": True,
              "input_tokens": 0, "output_tokens": 0,
              "note": "чтение из кэша"}
    got = run_tree._tokens({"journal": [_stage([live]), _stage([cached])]})
    row = got["читатель ИДАК/модель-читателя"]
    assert row == {"input_tokens": 4200, "output_tokens": 130, "calls": 1,
                   "counted": True}

    quiet = dict(live, counted=False, input_tokens=None, output_tokens=None)
    mixed = run_tree._tokens({"journal": [_stage([live]), _stage([quiet])]})
    row2 = mixed["читатель ИДАК/модель-читателя"]
    assert row2["counted"] is False and row2["calls"] == 2
    assert row2["input_tokens"] == 4200


if __name__ == "__main__":                                  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
