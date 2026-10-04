# -*- coding: utf-8 -*-
"""S06-IDENTITY-1 (ревью GPT-027, план единой версии §3.9 п.3):
`effective.channel` отпечатка роли привязан к транспорту, закреплённому в
построенном канале, а не к повторному чтению ключа. Контрпример ревью:
канал построен с ключом (транспорт api), ключ снят до записи отпечатка,
исполнение идёт по api — а отпечаток говорил «cli»."""
from __future__ import annotations

import json

import pytest

from cdpm import llm as llm_mod
from cdpm import providers


class _Settings:
    def __init__(self, key: bool):
        self.key = key

    def has_api_key(self) -> bool:
        return self.key

    def model_for(self, usage, backend):
        return None


def _cloud_backend(monkeypatch, settings: _Settings, calls: list):
    from webapp import llm_backend
    monkeypatch.setattr(llm_backend, "_settings", settings)
    monkeypatch.setattr(llm_backend, "_api_generate",
                        lambda prompt, **kw: calls.append("api") or "ответ API")
    monkeypatch.setattr(llm_backend, "_cli_generate",
                        lambda prompt, timeout_s, **kw: calls.append("cli") or "ответ CLI")
    monkeypatch.setattr(llm_backend, "resolve_backend",
                        lambda scope, requested: "cloud")
    monkeypatch.setattr(llm_mod.time, "sleep", lambda *_: None)


def test_effective_channel_follows_pinned_transport_not_current_key(monkeypatch):
    settings = _Settings(key=True)
    calls: list = []
    _cloud_backend(monkeypatch, settings, calls)
    # ключ есть при построении → транспорт закреплён api
    fn = llm_mod.cosmos_llm("any", model="review-m", attempts=1, backoff_s=0)
    assert json.loads(llm_mod.channel_identity(fn))["transport"] == "api"
    # штатный setter снял ключ между построением канала и записью отпечатка
    settings.key = False
    monkeypatch.setattr(providers, "provider_key", lambda name: None)
    eff = providers.effective_channel(fn, "cloud", "review-m")
    assert eff == {"provider": "cloud", "model": "review-m", "channel": "api"}
    # ключ вернулся — исполнение идёт закреплённым api, как и говорит отпечаток
    settings.key = True
    assert fn("проверка") == "ответ API"
    assert calls == ["api"]


def test_effective_channel_cli_pinned_survives_key_appearing(monkeypatch):
    settings = _Settings(key=False)
    calls: list = []
    _cloud_backend(monkeypatch, settings, calls)
    # без ключа CDPM канал — командная строка с моделью из настроек
    # площадки (РВ-140 п. 3: без модели — отказ, своей модели CLI нет)
    monkeypatch.setattr(providers, "provider_key", lambda name: None)
    monkeypatch.setattr(providers, "load_settings",
                        lambda: {"cloud_default_model": "claude-sub-5"})
    fn = llm_mod.cosmos_llm("any", attempts=1, backoff_s=0)
    assert json.loads(llm_mod.channel_identity(fn))["transport"] == "cli"
    settings.key = True
    monkeypatch.setattr(providers, "provider_key", lambda name: "sk-synthetic")
    eff = providers.effective_channel(fn, "cloud", None)
    assert eff["channel"] == "cli"
    # Модель названа (РВ-140 п. 3), поэтому появившийся ключ сменил бы
    # транспорт: закреплённый канал отказывает словами, подмены нет.
    with pytest.raises(llm_mod.LLMCallError) as e:
        fn("проверка")
    assert "подмены транспорта нет" in str(e.value) and calls == []


def test_effective_channel_without_pinned_transport_falls_back_to_key():
    def plain(prompt: str) -> str:
        return "x"
    llm_mod.stamp_identity(plain, provider="openai", model="gpt-x")
    assert providers.effective_channel(plain)["channel"] == "api"
