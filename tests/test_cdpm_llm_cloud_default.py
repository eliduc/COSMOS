# -*- coding: utf-8 -*-
"""Умолчание облачной модели (смок контуров 2026-08-25).

Охраняемое: облачный канал без явной модели при ЖИВОМ API-ключе
берёт модель из настройки cloud_default_model — complete() уходит
API-веткой, а не мёртвым CLI; без настройки — отказ словами ДО вызова
транспорта (РВ-119, 04.10.2026: имени модели в коде нет); без ключа
поведение прежнее (CLI — подписочный путь, договор «закреплён CLI»);
scope=local-only не затронут."""
import pytest

from cdpm import llm as llm_mod
from cdpm import providers


def _run(monkeypatch, key, settings, model_in=None):
    seen = {}
    monkeypatch.setattr(providers, "provider_key",
                        lambda p: key if p == "cloud" else None)
    monkeypatch.setattr(providers, "load_settings", lambda: settings)
    from webapp import llm_backend

    def fake_complete(prompt, *, backend, model=None, **kw):
        seen["backend"] = backend
        seen["model"] = model
        return {"ok": True, "text": "ок", "backend": backend,
                "model": model}

    monkeypatch.setattr(llm_backend, "complete", fake_complete)
    fn = llm_mod.cosmos_llm("any", requested="cloud", model=model_in,
                            attempts=1)
    fn("привет")
    return seen


def test_no_model_setting_with_key_refuses_in_words(monkeypatch):
    with pytest.raises(llm_mod.LLMCallError) as e:
        _run(monkeypatch, key="k-1", settings={})
    assert "cloud_default_model" in str(e.value)
    assert not hasattr(llm_mod, "DEFAULT_CLOUD_MODEL"), "имя модели в коде"


def test_settings_override(monkeypatch):
    seen = _run(monkeypatch, key="k-1",
                settings={"cloud_default_model": "claude-opus-5"})
    assert seen["model"] == "claude-opus-5"


@pytest.mark.parametrize("key", [None, "k-1"])
def test_no_setting_refuses_before_transport(monkeypatch, key):
    """Внешний счётчик вызовов транспорта (ревью GPT 00:54): он виден и
    после исключения — проба не пройдёт, если complete() был вызван."""
    calls = []
    monkeypatch.setattr(providers, "provider_key",
                        lambda p: key if p == "cloud" else None)
    monkeypatch.setattr(providers, "load_settings", lambda: {})
    from webapp import llm_backend
    monkeypatch.setattr(llm_backend, "complete",
                        lambda *a, **kw: calls.append(kw) or
                        {"ok": True, "text": "ок"})
    with pytest.raises(llm_mod.LLMCallError) as e:
        # отказ — уже при построении канала, до любого вызова
        llm_mod.cosmos_llm("any", requested="cloud", attempts=1)("привет")
    assert "cloud_default_model" in str(e.value)
    assert calls == [], "транспорт вызван без модели"


def test_no_key_no_setting_refuses_rv140(monkeypatch):
    """РВ-140 п. 3: путь CLI без ключа при незаданной модели — отказ
    словами, своей модели CLI молча нет."""
    with pytest.raises(llm_mod.LLMCallError) as e:
        _run(monkeypatch, key=None, settings={})
    assert "cloud_default_model" in str(e.value)


def test_no_key_with_setting_goes_cli_with_that_model(monkeypatch):
    seen = _run(monkeypatch, key=None,
                settings={"cloud_default_model": "claude-opus-5"})
    assert seen["model"] == "claude-opus-5"


def test_explicit_model_wins(monkeypatch):
    seen = _run(monkeypatch, key="k-1", settings={},
                model_in="claude-sonnet-5")
    assert seen["model"] == "claude-sonnet-5"
