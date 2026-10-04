# -*- coding: utf-8 -*-
"""Внешние провайдеры CDPM (OpenAI, xAI Grok, Google Gemini).

Охраняемое: дисциплина контура (внешний провайдер = облако, при наборе
local-only запрещён), правильные протоколы и заголовки, ключ не течёт в
сообщения об ошибках, повторы канала и честный отказ без ключа.
Сеть не используется — httpx подменяется."""
import json

import pytest

from cdpm import external_llm, llm as llm_mod, providers


class _Resp:
    def __init__(self, status: int, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload, ensure_ascii=False) \
            if not isinstance(payload, str) else payload

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("не JSON")
        return self._payload


def _openai_ok(text="ответ"):
    return _Resp(200, {"choices": [{"message": {"content": text}}]})


def _gemini_ok(text="ответ"):
    return _Resp(200, {"candidates": [
        {"content": {"parts": [{"text": text}]}}]})


@pytest.fixture()
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("CDPM_SETTINGS_FILE",
                       str(tmp_path / "cdpm-settings.json"))
    return tmp_path


def test_openai_and_grok_speak_the_same_protocol(monkeypatch):
    seen = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        seen[url] = {"body": json, "headers": headers}
        return _openai_ok("готов")
    monkeypatch.setattr(external_llm.httpx, "post", fake_post)

    for provider, host in (("openai", "api.openai.com"),
                           ("grok", "api.x.ai")):
        got = external_llm.complete(provider, key="k-123",
                                    model="m-1", prompt="привет",
                                    temperature=0.2, max_tokens=100)
        assert got == "готов"
        url = next(u for u in seen if host in u)
        assert seen[url]["headers"]["Authorization"] == "Bearer k-123"
        body = seen[url]["body"]
        assert body["model"] == "m-1"
        assert body["messages"][0]["content"] == "привет"
        assert body["temperature"] == 0.2
        assert body["max_tokens"] == 100


def test_gemini_uses_its_own_protocol(monkeypatch):
    seen = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        seen["url"] = url
        seen["body"] = json
        seen["headers"] = headers
        return _gemini_ok("готов")
    monkeypatch.setattr(external_llm.httpx, "post", fake_post)
    got = external_llm.complete("gemini", key="AIza-1",
                                model="gemini-3-pro", prompt="привет",
                                max_tokens=64)
    assert got == "готов"
    assert seen["url"].endswith("/gemini-3-pro:generateContent")
    assert seen["headers"]["x-goog-api-key"] == "AIza-1"
    assert seen["body"]["contents"][0]["parts"][0]["text"] == "привет"
    assert seen["body"]["generationConfig"]["maxOutputTokens"] == 64


@pytest.mark.parametrize("payload,expected", [
    (_Resp(401, {"error": "bad key"}), "HTTP 401"),
    (_Resp(200, {"нет": "полей"}), "неожиданный ответ"),
    (_Resp(200, {"choices": [{"message": {"content": "  "}}]}),
     "пустой ответ"),
], ids=["код ошибки", "чужая структура", "пустой текст"])
def test_openai_failures_are_named(monkeypatch, payload, expected):
    monkeypatch.setattr(external_llm.httpx, "post",
                        lambda *a, **k: payload)
    with pytest.raises(external_llm.ExternalLLMError) as e:
        external_llm.complete("openai", key="k", model="m",
                              prompt="п")
    assert expected in str(e.value)


def test_missing_key_and_unknown_provider_are_honest():
    with pytest.raises(external_llm.ExternalLLMError) as e:
        external_llm.complete("openai", key="", model="m", prompt="п")
    assert "ключ не задан" in str(e.value)
    with pytest.raises(external_llm.ExternalLLMError) as e:
        external_llm.complete("чужой", key="k", model="m", prompt="п")
    assert "неизвестный провайдер" in str(e.value)


def test_local_only_scope_forbids_every_external_provider(
        isolated_settings):
    """Дисциплина контура У-03: внешний провайдер — облако; набор
    local-only запрещает его так же, как облачную Claude."""
    from webapp.llm_backend import ScopeViolation
    for provider in ("openai", "grok", "gemini"):
        with pytest.raises(ScopeViolation):
            llm_mod.channel("local-only", provider=provider)
    # А локальная модель для такого набора законна.
    assert callable(llm_mod.channel("local-only", provider="local"))


def test_channel_retries_then_names_the_failure(isolated_settings,
                                                monkeypatch):
    providers.set_provider_key("openai", "k-1")
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return _Resp(500, {"error": "temporary"})
        return _openai_ok("готов")
    monkeypatch.setattr(external_llm.httpx, "post", flaky)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    fn = llm_mod.channel("any", provider="openai", model="gpt-5.4")
    assert fn("привет") == "готов"
    assert calls["n"] == 2

    monkeypatch.setattr(external_llm.httpx, "post",
                        lambda *a, **k: _Resp(500, {"error": "down"}))
    fn = llm_mod.channel("any", provider="grok", attempts=2)
    with pytest.raises(llm_mod.LLMCallError) as e:
        fn("привет")
    assert "после 2 попыток" in str(e.value)
    assert "grok" in str(e.value)


def test_key_never_leaks_into_error_text(isolated_settings,
                                         monkeypatch):
    providers.set_provider_key("gemini", "AIza-СЕКРЕТ-1234")
    monkeypatch.setattr(external_llm.httpx, "post",
                        lambda *a, **k: _Resp(403, {"error": "denied"}))
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    fn = llm_mod.channel("any", provider="gemini", attempts=1)
    with pytest.raises(llm_mod.LLMCallError) as e:
        fn("привет")
    assert "СЕКРЕТ" not in str(e.value)


def test_provider_keys_roundtrip_and_masking(isolated_settings):
    providers.set_provider_key("openai", "sk-0123456789")
    providers.set_provider_key("grok", "xai-9876543210")
    assert providers.provider_key("openai") == "sk-0123456789"
    listing = {p["name"]: p for p in providers.list_providers()}
    assert listing["openai"]["key_masked"] == "sk-0…6789"
    assert listing["openai"]["configured"] is True
    assert listing["gemini"]["configured"] is False
    assert listing["openai"]["cloud"] is True
    assert listing["local"]["cloud"] is False
    providers.set_provider_key("openai", "")
    assert providers.provider_key("openai") is None


def test_legacy_claude_key_migrates_to_provider_keys(isolated_settings):
    providers.save_settings({"claude_api_key": "sk-ant-0123456789"})
    assert providers.provider_key("cloud") == "sk-ant-0123456789"
    providers.set_provider_key("openai", "sk-new")
    assert providers.provider_key("cloud") == "sk-ant-0123456789"
    assert "claude_api_key" not in providers.load_settings()


# ---------------------------------------------------------------------------
# МП-3: усилие рассуждения и адаптивная деградация параметров
# ---------------------------------------------------------------------------

def test_mp3_effort_maps_to_native_params(monkeypatch):
    """Единый уровень усилия → родной параметр провайдера: OpenAI
    reasoning_effort medium, Grok — low/high (medium сводится к low),
    Gemini — thinkingConfig.thinkingBudget."""
    bodies = {}

    def fake_post(url, json=None, timeout=None, headers=None):
        bodies[url] = json
        return _gemini_ok() if "generativelanguage" in url \
            else _openai_ok()
    monkeypatch.setattr(external_llm.httpx, "post", fake_post)

    external_llm.complete("openai", key="k", model="m",
                          prompt="п", effort="medium")
    external_llm.complete("grok", key="k", model="m",
                          prompt="п", effort="medium")
    external_llm.complete("gemini", key="k", model="m",
                          prompt="п", effort="max")
    openai_body = next(b for u, b in bodies.items()
                       if "api.openai.com" in u)
    grok_body = next(b for u, b in bodies.items() if "api.x.ai" in u)
    gem_body = next(b for u, b in bodies.items()
                    if "generativelanguage" in u)
    assert openai_body["reasoning_effort"] == "medium"
    assert grok_body["reasoning_effort"] == "low"
    assert gem_body["generationConfig"]["thinkingConfig"] \
        == {"thinkingBudget": 24576}


def test_mp3_unsupported_params_degrade_and_retry(monkeypatch):
    """Практика продакшн-клиентов: возможности модели не угадываются
    по имени — отвергнутый параметр (temperature у рассуждающей,
    reasoning_effort у старой) снимается и запрос повторяется."""
    calls: list[dict] = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(json)
        if "temperature" in json:
            return _Resp(400, "Unsupported parameter: "
                               "'temperature' is not supported "
                               "with this model.")
        return _openai_ok("готов")
    monkeypatch.setattr(external_llm.httpx, "post", fake_post)
    got = external_llm.complete("openai", key="k", model="m",
                                prompt="п", temperature=0.2,
                                effort="high")
    assert got == "готов"
    assert len(calls) == 2
    assert "temperature" not in calls[1]
    assert calls[1]["reasoning_effort"] == "high"

    calls.clear()

    def fake_post2(url, json=None, timeout=None, headers=None):
        calls.append(json)
        if "reasoning_effort" in json:
            return _Resp(400, "Unknown parameter: "
                               "'reasoning_effort'")
        return _openai_ok("готов")
    monkeypatch.setattr(external_llm.httpx, "post", fake_post2)
    got = external_llm.complete("openai", key="k", model="m",
                                prompt="п", effort="low")
    assert got == "готов"
    assert len(calls) == 2 and "reasoning_effort" not in calls[1]


def test_mp3_gemini_thinking_degrades(monkeypatch):
    calls: list[dict] = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(json)
        gen = json.get("generationConfig") or {}
        if "thinkingConfig" in gen:
            return _Resp(400, "thinking is not supported "
                               "for this model")
        return _gemini_ok("готов")
    monkeypatch.setattr(external_llm.httpx, "post", fake_post)
    got = external_llm.complete("gemini", key="k", model="m",
                                prompt="п", effort="low",
                                temperature=0.1)
    assert got == "готов"
    assert len(calls) == 2
    assert calls[1]["generationConfig"] == {"temperature": 0.1}


def test_mp1_mythos_class_ranks_above_opus():
    """Fable/Mythos — тир выше Opus: «новейшая» подсказка и порядок
    списка обязаны ставить их первыми (скриншот владельца 2026-08-21:
    новейшей показывался claude-opus-5)."""
    entries = [{"id": "claude-opus-5", "created": 200},
               {"id": "claude-fable-5", "created": 100},
               {"id": "claude-sonnet-5", "created": 300},
               {"id": "claude-haiku-4-5-20251001", "created": 400}]
    ranked = sorted(entries, key=external_llm.rank_key, reverse=True)
    assert [e["id"] for e in ranked][:2] \
        == ["claude-fable-5", "claude-opus-5"]


# ---------------------------------------------------------------------------
# МП-4: доработки лестницы деградации и весов (состязательная проверка)
# ---------------------------------------------------------------------------

def test_mp4_value_errors_are_not_masked(monkeypatch):
    """«Invalid value for temperature: 5.0» — ошибка КОНФИГУРАЦИИ, не
    неподдержанный параметр: деградация не должна её маскировать."""
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(json)
        return _Resp(400, "Invalid value for 'temperature': 5.0 is "
                          "greater than the maximum of 2")
    monkeypatch.setattr(external_llm.httpx, "post", fake_post)
    with pytest.raises(external_llm.ExternalLLMError,
                       match="HTTP 400"):
        external_llm.complete("openai", key="k", model="m",
                              prompt="п", temperature=5.0)
    assert len(calls) == 1


def test_mp4_specific_param_wins_over_mention(monkeypatch):
    """Текст об отвергнутом reasoning_effort, попутно упоминающий
    temperature, снимает именно reasoning_effort."""
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(dict(json))
        if "reasoning_effort" in json:
            return _Resp(400, "Argument not supported: "
                              "reasoning_effort (note: temperature "
                              "is fixed for reasoning models)")
        return _openai_ok("готов")
    monkeypatch.setattr(external_llm.httpx, "post", fake_post)
    got = external_llm.complete("openai", key="k", model="m",
                                prompt="п", temperature=0.2,
                                effort="high")
    assert got == "готов"
    assert len(calls) == 2
    assert "reasoning_effort" not in calls[1]
    assert calls[1]["temperature"] == 0.2


def test_mp4_double_degradation_recovers(monkeypatch):
    """Модель класса o1-mini отвергает и temperature, и
    reasoning_effort по одному за раз — лестница снимает оба."""
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(dict(json))
        if "temperature" in json:
            return _Resp(400, "'temperature' is not supported")
        if "reasoning_effort" in json:
            return _Resp(400, "'reasoning_effort' is not supported")
        return _openai_ok("готов")
    monkeypatch.setattr(external_llm.httpx, "post", fake_post)
    got = external_llm.complete("openai", key="k", model="m",
                                prompt="п", temperature=0.1,
                                effort="low")
    assert got == "готов"
    assert len(calls) == 3


def test_mp4_max_tokens_converts_to_max_completion_tokens(monkeypatch):
    """Рассуждающие модели OpenAI просят max_completion_tokens —
    лимит конвертируется, а не теряется."""
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(dict(json))
        if "max_tokens" in json:
            return _Resp(400, "Unsupported parameter: 'max_tokens'. "
                              "Use 'max_completion_tokens' instead.")
        return _openai_ok("готов")
    monkeypatch.setattr(external_llm.httpx, "post", fake_post)
    got = external_llm.complete("openai", key="k", model="m",
                                prompt="п", max_tokens=5000)
    assert got == "готов"
    assert calls[1]["max_completion_tokens"] == 5000
    assert "max_tokens" not in calls[1]


def test_mp4_weight_marks_match_whole_tokens():
    """Метки веса сравниваются целыми токенами: «gemini» не штрафуется
    за «mini» внутри слова (learnlm вставал выше gemini той же версии)."""
    assert external_llm._weight_of("gemini-2.0-flash") \
        == external_llm._weight_of("learnlm-2.0-flash")
    assert external_llm._weight_of("gpt-5.4-mini") == -1
    assert external_llm._weight_of("gemini-3.5-flash-lite") == -2


def test_mp4_grok_fallback_effort_is_valid(monkeypatch):
    """Мусорный уровень усилия у Grok сводится к валидному low —
    у xAI нет уровня medium."""
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(json)
        return _openai_ok()
    monkeypatch.setattr(external_llm.httpx, "post", fake_post)
    external_llm.complete("grok", key="k", model="m", prompt="п",
                          effort="minimal")
    assert calls[0]["reasoning_effort"] == "low"


def test_mp4_http_400_is_not_retried_by_channel(isolated_settings,
                                                monkeypatch):
    """400 после лестницы деградации — детерминированный отказ:
    канал не жжёт повторы с паузами."""
    providers.set_provider_key("openai", "sk-т")
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append(json)
        return _Resp(400, "просто плохой запрос")
    monkeypatch.setattr(external_llm.httpx, "post", fake_post)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)
    fn = llm_mod.channel("any", provider="openai", model="m")
    with pytest.raises(llm_mod.LLMCallError):
        fn("привет")
    assert len(calls) == 1
