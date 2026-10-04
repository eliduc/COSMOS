# -*- coding: utf-8 -*-
"""S06-R1 (ревью GPT-020, план единой версии §3.9 п.3): повтор того же
канала после сетевой ошибки не меняет транспорт. Контрпример ревью:
облачный канал построен с ключом API (транспорт «api»), первая
попытка отказала, в паузе ключ снят — прежде повтор молча уходил в
командную строку при отпечатке «api». Теперь: транспорт закреплён при
построении, смена конфигурации во время повторов — честный отказ
словами, вызовов другим транспортом нет."""
from __future__ import annotations

import json

import pytest

from cdpm import llm as llm_mod


class _Settings:
    def __init__(self, key: bool):
        self.key = key

    def has_api_key(self) -> bool:
        return self.key


def _install_backend(monkeypatch, settings: _Settings, calls: list):
    from webapp import llm_backend

    def fake_complete(prompt, *, backend, model=None, **kw):
        transport = "api" if (model and settings.has_api_key()) else "cli"
        calls.append(transport)
        if transport == "api":
            return {"ok": False, "backend": backend, "model": model,
                    "error": "синтетический отказ API"}
        return {"ok": True, "text": "синтетический ответ CLI",
                "backend": backend, "model": model}

    monkeypatch.setattr(llm_backend, "_settings", settings)
    monkeypatch.setattr(llm_backend, "complete", fake_complete)
    monkeypatch.setattr(llm_backend, "resolve_backend",
                        lambda scope, requested: "cloud")
    monkeypatch.setattr(llm_mod.time, "sleep", lambda *_: None)


def test_retry_keeps_pinned_transport_and_refuses_on_config_change(
        monkeypatch):
    settings = _Settings(key=True)
    calls: list = []
    _install_backend(monkeypatch, settings, calls)
    fn = llm_mod.cosmos_llm("any", model="review-m", attempts=3,
                            backoff_s=0)
    ident = json.loads(llm_mod.channel_identity(fn))
    assert ident["transport"] == "api"

    # первая попытка отказала по API, в «паузе» ключ снят
    def sleep_and_drop_key(*_):
        settings.key = False
    monkeypatch.setattr(llm_mod.time, "sleep", sleep_and_drop_key)

    with pytest.raises(llm_mod.LLMCallError) as err:
        fn("проверка")
    assert "конфигурация изменилась во время повторов" in str(err.value)
    assert "подмены транспорта нет" in str(err.value)
    # ни одного вызова другим транспортом: только неудачный API
    assert calls == ["api"]
    # отпечаток не изменился: канал по-прежнему заявляет api
    assert json.loads(llm_mod.channel_identity(fn))["transport"] == "api"


def test_retry_same_transport_repeats_same_channel(monkeypatch):
    settings = _Settings(key=True)
    calls: list = []
    _install_backend(monkeypatch, settings, calls)
    fn = llm_mod.cosmos_llm("any", model="review-m", attempts=2,
                            backoff_s=0)
    with pytest.raises(llm_mod.LLMCallError) as err:
        fn("проверка")
    assert calls == ["api", "api"], "повторы — тем же транспортом"
    assert "отказал после 2 попыток" in str(err.value)


def test_cli_channel_without_key_is_pinned_cli(monkeypatch):
    settings = _Settings(key=False)
    calls: list = []
    _install_backend(monkeypatch, settings, calls)
    # РВ-140 п. 3: путь CLI без ключа требует модели из настроек площадки.
    from cdpm import providers as _prov
    monkeypatch.setattr(_prov, "load_settings",
                        lambda: {"cloud_default_model": "claude-sub-5"})
    fn = llm_mod.cosmos_llm("any", attempts=1, backoff_s=0)
    assert json.loads(llm_mod.channel_identity(fn))["transport"] == "cli"
    assert fn("проверка") == "синтетический ответ CLI"
    assert calls == ["cli"]

# ── S06-R1-RACE (GPT-025): смена ключа МЕЖДУ предпроверкой канала и
# исполнением — исполнитель держит закреплённый транспорт ──────────────

def _install_real_complete(monkeypatch, settings: _Settings, calls: list,
                           flip_at_entry: bool):
    """Настоящий llm_backend.complete; подменены только низкоуровневые
    API/CLI; ключ переключается при ВХОДЕ в complete (после предпроверки
    канала CDPM) — детерминированная гонка из ревью GPT-025."""
    from webapp import llm_backend
    real_complete = llm_backend.complete

    def api_gen(prompt, **kw):
        calls.append("api")
        return "синтетический ответ API"

    def cli_gen(prompt, timeout_s, **kw):
        calls.append("cli")
        return "синтетический ответ CLI"

    def racing_complete(prompt, **kw):
        if flip_at_entry:
            settings.key = not settings.key
        return real_complete(prompt, **kw)

    monkeypatch.setattr(llm_backend, "_settings", settings)
    monkeypatch.setattr(llm_backend, "_api_generate", api_gen)
    monkeypatch.setattr(llm_backend, "_cli_generate", cli_gen)
    monkeypatch.setattr(llm_backend, "complete", racing_complete)
    monkeypatch.setattr(llm_backend, "resolve_backend",
                        lambda scope, requested: "cloud")
    monkeypatch.setattr(llm_mod.time, "sleep", lambda *_: None)


def test_race_api_pinned_key_removed_at_dispatch_refuses_without_cli(monkeypatch):
    settings = _Settings(key=True)
    calls: list = []
    _install_real_complete(monkeypatch, settings, calls, flip_at_entry=True)
    fn = llm_mod.cosmos_llm("any", model="review-m", attempts=1, backoff_s=0)
    assert json.loads(llm_mod.channel_identity(fn))["transport"] == "api"
    with pytest.raises(llm_mod.LLMCallError) as err:
        fn("проверка")
    assert "закреплённый транспорт api" in str(err.value)
    assert calls == [], "ни API (ключа нет), ни CLI (подмена запрещена)"
    assert json.loads(llm_mod.channel_identity(fn))["transport"] == "api"


def test_race_cli_pinned_key_added_at_dispatch_stays_cli(monkeypatch):
    settings = _Settings(key=False)
    calls: list = []
    _install_real_complete(monkeypatch, settings, calls, flip_at_entry=True)
    fn = llm_mod.cosmos_llm("any", model="review-m", attempts=1, backoff_s=0)
    assert json.loads(llm_mod.channel_identity(fn))["transport"] == "cli"
    assert fn("проверка") == "синтетический ответ CLI"
    assert calls == ["cli"], "появившийся ключ не уводит закреплённый cli в API"


def test_no_race_pinned_transports_execute_as_pinned(monkeypatch):
    settings = _Settings(key=True)
    calls: list = []
    _install_real_complete(monkeypatch, settings, calls, flip_at_entry=False)
    fn = llm_mod.cosmos_llm("any", model="review-m", attempts=1, backoff_s=0)
    assert fn("проверка") == "синтетический ответ API"
    assert calls == ["api"]
