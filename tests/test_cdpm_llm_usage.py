# -*- coding: utf-8 -*-
"""Учёт токенов вызовов моделей — шаг 1 проекта «Запись о прогоне»
(решение владельца 10.09.2026: считать в токенах по каждой модели).

Боковой канал `cdpm.llm_usage`: адаптер объявляет расход ответа, обёртка
журнала вызовов забирает его в запись (`usage`); канал без сведений —
честное «не сообщил», командная строка по подписке — «не считается».
Сеть не используется — httpx и конверт канала подменяются."""
from __future__ import annotations

import json
import threading

import pytest

from cdpm import actions_journal as aj
from cdpm import call_journal as cj
from cdpm import external_llm, llm as llm_mod, llm_usage


@pytest.fixture(autouse=True)
def _fresh():
    cj.forget()
    aj.forget()
    llm_usage.clear()
    yield
    cj.forget()
    aj.forget()
    llm_usage.clear()


class _Resp:
    def __init__(self, status: int, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload


def test_normalize_and_register_semantics():
    assert llm_usage.normalize(None) is None
    assert llm_usage.normalize({}) is None
    got = llm_usage.normalize({"provider": "openai", "model": "m",
                               "input_tokens": "12", "output_tokens": 3,
                               "thinking_tokens": -1, "extra": "нет"})
    assert got["input_tokens"] == 12 and got["output_tokens"] == 3
    assert got["thinking_tokens"] is None and "extra" not in got
    assert got["counted"] is True and got["reason"] is None
    # Без чисел — не считается, причина названа.
    got = llm_usage.normalize({"provider": "cloud", "model": "(claude-cli)"})
    assert got["counted"] is False and got["reason"]
    # Регистр: объявил — забрал — пусто.
    llm_usage.clear()
    assert llm_usage.take() is None
    llm_usage.report("gemini", "g", input_tokens=5, output_tokens=7)
    assert llm_usage.take()["output_tokens"] == 7
    assert llm_usage.take() is None
    llm_usage.not_counted("cloud", "(claude-cli)", "cli")
    assert llm_usage.take() == {
        "provider": "cloud", "model": "(claude-cli)", "input_tokens": None,
        "output_tokens": None, "cache_read_tokens": None,
        "cache_write_tokens": None, "thinking_tokens": None,
        "counted": False, "reason": "cli"}


def test_register_is_thread_local():
    seen = {}

    def worker(name, n):
        llm_usage.report("p", name, input_tokens=n, output_tokens=n)
        seen[name] = llm_usage.take()["input_tokens"]
    ts = [threading.Thread(target=worker, args=(f"m{i}", i)) for i in range(4)]
    llm_usage.report("p", "main", input_tokens=99, output_tokens=1)
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert seen == {"m0": 0, "m1": 1, "m2": 2, "m3": 3}
    assert llm_usage.take()["input_tokens"] == 99      # поток вызывающего не тронут


def test_openai_and_gemini_adapters_report_usage(monkeypatch):
    payloads = {
        "openai": {"choices": [{"message": {"content": "ок"}}],
                   "usage": {"prompt_tokens": 120, "completion_tokens": 30,
                             "completion_tokens_details": {"reasoning_tokens": 10},
                             "prompt_tokens_details": {"cached_tokens": 100}}},
        "gemini": {"candidates": [{"content": {"parts": [{"text": "ок"}]}}],
                   "usageMetadata": {"promptTokenCount": 40,
                                     "candidatesTokenCount": 8,
                                     "thoughtsTokenCount": 2}},
    }

    def fake_post(url, json=None, timeout=None, headers=None):
        return _Resp(200, payloads["gemini" if "generativelanguage" in url
                                   or ":generateContent" in url else "openai"])
    monkeypatch.setattr(external_llm.httpx, "post", fake_post)
    llm_usage.clear()
    assert external_llm.complete("openai", key="k", model="m-o",
                                 prompt="привет") == "ок"
    got = llm_usage.take()
    assert got["provider"] == "openai" and got["model"] == "m-o"
    assert (got["input_tokens"], got["output_tokens"],
            got["thinking_tokens"], got["cache_read_tokens"]) == (120, 30, 10, 100)
    assert got["counted"] is True
    assert external_llm.complete("gemini", key="k", model="m-g",
                                 prompt="привет") == "ок"
    got = llm_usage.take()
    assert got["provider"] == "gemini" and got["model"] == "m-g"
    assert (got["input_tokens"], got["output_tokens"],
            got["thinking_tokens"]) == (40, 8, 2)
    # Ответ без блока расхода — «не сообщил», не ноль.
    payloads["openai"].pop("usage")
    assert external_llm.complete("grok", key="k", model="m-x",
                                 prompt="привет") == "ок"
    got = llm_usage.take()
    assert got["provider"] == "grok" and got["counted"] is False
    assert "usage" in got["reason"]


def _run(tmp_path):
    run = tmp_path / "run-0001"
    (run / "extract").mkdir(parents=True)
    (run / "status.json").write_text('{"state": "заморожено"}', encoding="utf-8")
    return run


def test_journal_records_usage_of_each_call(tmp_path):
    """Запись вызова несёт расход, объявленный адаптером; канал, который
    ничего не объявил — `usage: null`; расход не течёт между вызовами."""
    run = _run(tmp_path)
    cj.set_mode(run, "record")

    def counting(prompt):
        llm_usage.report("openai", "m-1", input_tokens=len(prompt),
                         output_tokens=2)
        return "ответ"

    def silent(prompt):
        return "ответ"
    w1 = cj.wrap(run, counting, "extractor", "openai")
    w2 = cj.wrap(run, silent, "auditor", "cloud")
    assert w1("вопрос") == "ответ"
    assert w2("вопрос") == "ответ"
    idx = cj.read_index(run)
    assert idx[0]["usage"]["input_tokens"] == len("вопрос")
    assert idx[0]["usage"]["output_tokens"] == 2
    assert idx[0]["usage"]["counted"] is True
    assert idx[1]["usage"] is None
    # Отказ канала — расход всё равно записан, если адаптер успел объявить.
    def failing(prompt):
        llm_usage.report("openai", "m-1", input_tokens=1, output_tokens=0)
        raise RuntimeError("сбой после ответа")
    w3 = cj.wrap(run, failing, "extractor", "openai")
    try:
        w3("вопрос")
    except RuntimeError:
        pass
    assert cj.read_index(run)[2]["usage"]["input_tokens"] == 1


def test_cloud_channel_reads_usage_from_envelope(monkeypatch, tmp_path):
    """Облачный канал берёт расход из поля `usage` конверта complete()
    (контракт с COSMOS); без поля — «не сообщил»; командная строка —
    «не считается».

    Среда не читается (находка COSMOS 10.09 на db4d01ee): при ключе
    облака в настройках машины cosmos_llm подставляет модель умолчания и
    транспорт остаётся API — поэтому настройки провайдеров уводятся в
    tmp и чтение ключа подменяется, иначе третья часть зависела бы от
    того, чей это каталог данных."""
    from cdpm import providers as providers_mod
    from webapp import llm_backend
    monkeypatch.setenv("CDPM_SETTINGS_FILE", str(tmp_path / "cdpm-settings.json"))
    monkeypatch.setattr(providers_mod, "provider_key", lambda name: None)
    env = {"ok": True, "text": "ок", "backend": "cloud", "model": "claude-x",
           "usage": {"provider": "cloud", "model": "claude-x",
                     "input_tokens": 11, "output_tokens": 5}}
    monkeypatch.setattr(llm_backend, "resolve_backend", lambda scope, req: "cloud")
    monkeypatch.setattr(llm_backend._settings, "has_api_key", lambda: True)
    monkeypatch.setattr(llm_backend, "complete", lambda prompt, **kw: env)
    fn = llm_mod.cosmos_llm(model="claude-x", attempts=1)
    llm_usage.clear()
    assert fn("привет") == "ок"
    got = llm_usage.take()
    assert got["counted"] is True and got["input_tokens"] == 11
    env.pop("usage")
    assert fn("привет") == "ок"
    got = llm_usage.take()
    assert got["counted"] is False and "usage" in got["reason"]
    # РВ-140 п. 3: без ключа и без модели — отказ словами, не CLI.
    monkeypatch.setattr(llm_backend._settings, "has_api_key", lambda: False)
    with pytest.raises(llm_mod.LLMCallError):
        llm_mod.cosmos_llm(attempts=1)
    # Командная строка (без ключа, модель из настроек) — не считается.
    monkeypatch.setattr(providers_mod, "load_settings",
                        lambda: {"cloud_default_model": "claude-sub-5"})
    fn_cli = llm_mod.cosmos_llm(attempts=1)
    assert fn_cli("привет") == "ок"
    got = llm_usage.take()
    assert got["counted"] is False and got["model"] == "claude-sub-5"
