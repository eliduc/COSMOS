# -*- coding: utf-8 -*-
"""МП-1: живое определение новейших моделей провайдеров.

Ранжирование без зашитых номеров семейств (устаревают: живой случай
2026-08-20 — в подсказках gpt-5.4 при фактических 5.6): версия из
имени → полновесность → имя без даты-снимка → отметка created.
"""
from fastapi.testclient import TestClient

from cdpm import external_llm as x


def _ranked(entries):
    clean = [e for e in entries
             if not any(m in e["id"].lower()
                        for m in x.NOISE_MODEL_MARKS)]
    clean.sort(key=x.rank_key, reverse=True)
    return [e["id"] for e in clean]


def test_version_dominates_and_full_weight_beats_light():
    got = _ranked([
        {"id": "gpt-5.6-mini", "created": 900},
        {"id": "gpt-5.4", "created": 100},
        {"id": "gpt-5.6", "created": 300},
        {"id": "o4", "created": 999},
    ])
    assert got[0] == "gpt-5.6"
    assert got.index("gpt-5.6-mini") < got.index("gpt-5.4")
    assert got[-1] == "o4"


def test_dateless_alias_preferred_over_snapshot():
    got = _ranked([
        {"id": "gpt-5.5-pro-2026-04-23", "created": 500},
        {"id": "gpt-5.5-pro", "created": 100},
    ])
    assert got[0] == "gpt-5.5-pro"


def test_noise_models_filtered():
    got = _ranked([
        {"id": "whisper-9", "created": 999},
        {"id": "gpt-embed-9", "created": 999},
        {"id": "grok-4.6", "created": 1},
    ])
    assert got == ["grok-4.6"]


def test_gemini_version_parse_without_created():
    got = _ranked([
        {"id": "gemini-2.5-pro", "created": None},
        {"id": "gemini-3.7-flash", "created": None},
        {"id": "gemini-3.7-flash-lite", "created": None},
    ])
    assert got[0] == "gemini-3.7-flash"
    assert got[-1] == "gemini-2.5-pro" or got[1] == "gemini-3.7-flash-lite"


def test_ranked_models_uses_detailed_listing(monkeypatch):
    monkeypatch.setattr(
        x, "list_models_detailed",
        lambda provider, *, key, timeout_s=30.0: [
            {"id": "grok-4.5", "created": 10},
            {"id": "grok-4.6", "created": 20},
            {"id": "grok-image-1", "created": 99},
        ])
    got = x.ranked_models("grok", key="k")
    assert got == ["grok-4.6", "grok-4.5"]


def test_models_endpoint_returns_latest(monkeypatch, tmp_path):
    from cdpm import webui
    monkeypatch.setattr(webui, "_MODELS_CACHE", {})
    monkeypatch.setattr(
        x, "ranked_models",
        lambda provider, *, key, timeout_s=30.0:
            ["gpt-5.6-luna", "gpt-5.5-pro"])
    monkeypatch.setattr(webui.providers_mod, "provider_key",
                        lambda name: "key")
    client = TestClient(webui.app)
    got = client.get("/api/providers/openai/models")
    assert got.status_code == 200
    body = got.json()
    assert body["latest"] == "gpt-5.6-luna"
    assert body["models"][0] == "gpt-5.6-luna"
    # Неизвестному провайдеру — честный отказ.
    assert client.get("/api/providers/nope/models").status_code == 404
