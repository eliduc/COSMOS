# -*- coding: utf-8 -*-
"""Внешние провайдеры языковых моделей для CDPM: OpenAI, xAI Grok,
Google Gemini.

Зачем отдельный модуль: инфраструктура COSMOS (`webapp/llm_backend`)
знает два канала — локальную модель и Claude; независимость ролей
аудит-цикла требует РАЗНЫХ поставщиков, поэтому CDPM держит свои
адаптеры. Дисциплина контура (У-03) при этом не ослабляется: внешний
провайдер — это облако, и признак набора `local-only` запрещает его так
же, как облачную Claude (проверка — в `cdpm.llm.channel`).

Адаптеры на httpx (уже зависимость проекта), без сторонних клиентских
библиотек: OpenAI и Grok говорят на одном протоколе (chat/completions),
Gemini — на своём (generateContent). Ключи приходят параметром; читает
их `cdpm.providers` из каталога данных, в репозиторий они не попадают.
"""
from __future__ import annotations

import re

import httpx

# Точки входа и подсказки моделей. Перечень моделей — ПОДСКАЗКА
# интерфейсу: канал принимает любое имя, которое примет провайдер, —
# иначе устаревший список блокировал бы новые модели.
SPECS: dict[str, dict] = {
    "openai": {
        "title": "OpenAI",
        "url": "https://api.openai.com/v1/chat/completions",
        "protocol": "openai",
        "models": ["gpt-5.4", "gpt-5.4-mini", "o4"],
        "prefer": ["gpt-5", "o4", "gpt-4"],
        # Семейства для ранжирования свежести (МП-1): без номеров версий —
        # зашитый номер устаревает (живой случай 2026-08-20: gpt-5.4 в
        # подсказках при фактических 5.6).
        "families": ["gpt", "o"],
        "key_hint": "sk-…",
    },
    "grok": {
        "title": "xAI Grok",
        "url": "https://api.x.ai/v1/chat/completions",
        "protocol": "openai",           # протокол OpenAI-совместимый
        # МП-3: у xAI уровни reasoning_effort только low/high.
        "effort_style": "grok",
        "models": ["grok-4", "grok-4-fast"],
        "prefer": ["grok-4", "grok-3"],
        "families": ["grok"],
        "key_hint": "xai-…",
    },
    "gemini": {
        "title": "Google Gemini",
        "url": "https://generativelanguage.googleapis.com/v1beta/models",
        "protocol": "gemini",
        # Живой перечень ключа 2026-08-18: gemini-3-pro под этим
        # именем НЕ существует — подсказки только из проверенных.
        "models": ["gemini-3-flash-preview", "gemini-2.5-pro",
                   "gemini-2.5-flash"],
        "prefer": ["gemini-3", "gemini-2.5-pro", "gemini-2.5-flash"],
        "families": ["gemini"],
        "key_hint": "AIza…",
    },
}

# Модели, которые не годятся для текстового обмена конвейера, — их
# автоподбор проверки связи пропускает.
NOISE_MODEL_MARKS = ("tts", "image", "computer-use", "embed",
                     "deep-research", "antigravity", "audio", "live",
                     "whisper", "moderation", "transcribe", "realtime",
                     "search", "instruct", "dall-e", "imagen", "aqa",
                     "gemma", "robotics", "banana")

# ── МП-1: живое ранжирование моделей по свежести ──
# Лучшие практики CodeForge (checkmodels.py) БЕЗ его слабости: там
# семейства зашиты номерами (gpt-5.2, grok-3) и устарели так же, как
# наши подсказки. Здесь номер версии разбирается из живого перечня:
# ничего зашитого, кроме словарей «легче/тяжелее».
_LIGHT_MARKS = ("mini", "nano", "fast", "lite", "flash", "haiku",
                "air")
_HEAVY_MARKS = ("pro", "opus", "max")
# Mythos-класс Claude 5 — ТИР ВЫШЕ Opus (живой скриншот владельца
# 2026-08-21: «новейшей» показывался opus-5, а fable-5 стоял вторым —
# оба весили 2, ничью решал алфавит).
_TOP_MARKS = ("fable", "mythos")
_VERSION_RE = re.compile(r"(\d+(?:\.\d+)?)")
_DATE_RE = re.compile(r"(?:19|20)\d{2}|\d{4}(?![\d.])")


_TOKEN_RE = re.compile(r"[a-zа-яё]+|\d+(?:\.\d+)?")


def _weight_of(name: str) -> int:
    # Mythos-класс выше pro/opus; pro/opus выше базы; каждая
    # облегчающая метка понижает ранг (flash-lite легче flash —
    # ничья решалась именем, поймано тестом). Метки сравниваются
    # ЦЕЛЫМИ токенами имени: подстрочное сравнение штрафовало всё
    # семейство gemini за «mini» внутри слова (вердикт состязательной
    # проверки 2026-08-21: learnlm-2.0-flash вставал выше
    # gemini-2.0-flash).
    tokens = set(_TOKEN_RE.findall(name.lower()))
    if tokens & set(_TOP_MARKS):
        return 3
    if tokens & set(_HEAVY_MARKS):
        return 2
    return -len(tokens & set(_LIGHT_MARKS))


def _version_of(name: str) -> float:
    m = _VERSION_RE.search(name)
    return float(m.group(1)) if m else 0.0


def rank_key(entry: dict) -> tuple:
    """Ключ свежести (больше = свежее/предпочтительнее): номер версии
    из имени → полновесность (pro/база выше mini/flash) → имя без
    даты-снимка (провайдеры держат его на новейшем снимке) → отметка
    created провайдера → имя."""
    name = entry.get("id", "")
    low = name.lower()
    dateless = 0 if _DATE_RE.search(low.split("/")[-1]) else 1
    if "latest" in low:
        dateless = 2
    return (_version_of(low), _weight_of(low), dateless,
            entry.get("created") or 0, name)


def list_models_detailed(provider: str, *, key: str,
                         timeout_s: float = 30.0) -> list[dict]:
    """Живой перечень моделей ключа с данными свежести:
    [{id, created|None}] — без фильтрации и сортировки."""
    spec = SPECS.get(provider)
    if spec is None:
        raise ExternalLLMError(f"неизвестный провайдер {provider!r}")
    if not key:
        raise ExternalLLMError(f"{spec['title']}: ключ не задан")
    try:
        if spec["protocol"] == "openai":
            base = spec["url"].split("/chat/")[0]
            resp = httpx.get(f"{base}/models", timeout=timeout_s,
                             headers={"Authorization": f"Bearer {key}"})
            if resp.status_code != 200:
                raise ExternalLLMError(
                    f"HTTP {resp.status_code}: {resp.text[:200]}")
            return [{"id": m.get("id", ""),
                     "created": m.get("created")}
                    for m in resp.json().get("data", [])
                    if m.get("id")]
        resp = httpx.get(spec["url"], timeout=timeout_s,
                         headers={"x-goog-api-key": key})
        if resp.status_code != 200:
            raise ExternalLLMError(
                f"HTTP {resp.status_code}: {resp.text[:200]}")
        out = []
        for m in resp.json().get("models", []):
            methods = m.get("supportedGenerationMethods") or []
            if "generateContent" in methods:
                name = (m.get("name") or "").removeprefix("models/")
                if name:
                    out.append({"id": name, "created": None})
        return out
    except httpx.HTTPError as e:
        raise ExternalLLMError(
            f"{spec['title']} недоступен: {e}") from None


def anthropic_models(key: str,
                     timeout_s: float = 30.0) -> list[dict]:
    """Живой перечень моделей Anthropic по ключу API: провайдер
    «Облако (Claude)» ходит через CLI и в SPECS внешних каналов не
    входит, но перечень моделей у него честный HTTP (МП-1: зашитые
    псевдонимы opus/sonnet/haiku не знали ни fable, ни версий —
    замечание владельца 2026-08-20)."""
    if not key:
        raise ExternalLLMError("Облако (Claude): ключ не задан")
    try:
        resp = httpx.get("https://api.anthropic.com/v1/models",
                         timeout=timeout_s,
                         headers={"x-api-key": key,
                                  "anthropic-version": "2023-06-01"})
    except httpx.HTTPError as e:
        raise ExternalLLMError(
            f"Anthropic недоступен: {e}") from None
    if resp.status_code != 200:
        raise ExternalLLMError(
            f"HTTP {resp.status_code}: {resp.text[:200]}")
    out = []
    for m in resp.json().get("data", []):
        mid = m.get("id") or ""
        if not mid:
            continue
        created = m.get("created_at")
        epoch = 0
        if isinstance(created, str):
            import datetime as _dt
            try:
                epoch = int(_dt.datetime.fromisoformat(
                    created.replace("Z", "+00:00")).timestamp())
            except ValueError:
                epoch = 0
        out.append({"id": mid, "created": epoch})
    return out


def ranked_anthropic(key: str, timeout_s: float = 30.0) -> list[str]:
    """Модели Anthropic, свежие первыми (то же ранжирование МП-1)."""
    entries = [e for e in anthropic_models(key, timeout_s=timeout_s)
               if not any(mark in e["id"].lower()
                          for mark in NOISE_MODEL_MARKS)]
    entries.sort(key=rank_key, reverse=True)
    return [e["id"] for e in entries]


def ranked_models(provider: str, *, key: str,
                  timeout_s: float = 30.0) -> list[str]:
    """Имена моделей ключа, пригодные конвейеру, СВЕЖИЕ ПЕРВЫМИ."""
    entries = [e for e in
               list_models_detailed(provider, key=key,
                                    timeout_s=timeout_s)
               if not any(mark in e["id"].lower()
                          for mark in NOISE_MODEL_MARKS)]
    entries.sort(key=rank_key, reverse=True)
    return [e["id"] for e in entries]


def latest_model(provider: str, *, key: str,
                 timeout_s: float = 30.0) -> str | None:
    """Новейшая полновесная модель ключа (первая в ранжировании)."""
    ranked = ranked_models(provider, key=key, timeout_s=timeout_s)
    return ranked[0] if ranked else None


class ExternalLLMError(RuntimeError):
    """Отказ внешнего канала; причина названа (провайдер, код, тело)."""


# ── МП-3: усилие рассуждения (директива владельца 2026-08-21) ──
# Единые уровни конфигурации → родной параметр каждого провайдера.
# Практика продакшн-клиентов: возможности модели не угадываются по
# имени (карта устаревает, как зашитые версии), а проверяются
# ДЕГРАДАЦИЕЙ — отвергнутый параметр снимается и запрос повторяется.
EFFORT_LEVELS = ("low", "medium", "high", "max")
_EFFORT_OPENAI = {"low": "low", "medium": "medium", "high": "high",
                  "max": "high"}
_EFFORT_GROK = {"low": "low", "medium": "low", "high": "high",
                "max": "high"}
# Gemini: thinkingBudget токенов; -1 — динамический (модель решает
# сама, может думать и больше high — уровни здесь про ПРЕДСКАЗУЕМОСТЬ
# затрат, не строгую монотонность).
_EFFORT_GEMINI = {"low": 512, "medium": -1, "high": 8192,
                  "max": 24576}


def _unsupported_param(resp_text: str, *names: str) -> bool:
    """Отказ 400 говорит о НЕПОДДЕРЖАННОМ параметре (а не о неверном
    ЗНАЧЕНИИ: «Invalid value for temperature: 5.0» — ошибка
    конфигурации, её маскировать нельзя; вердикт состязательной
    проверки 2026-08-21)."""
    low = (resp_text or "").lower()
    return any(n in low for n in names) and \
        ("unsupported" in low or "not supported" in low
         or "does not support" in low or "unknown parameter" in low
         or "unexpected keyword" in low or "is not allowed" in low)


def _openai_call(spec: dict, key: str, model: str, prompt: str,
                 timeout_s: float, temperature: float | None,
                 max_tokens: int | None,
                 effort: str | None = None) -> str:
    body: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if effort:
        if spec.get("effort_style") == "grok":
            # У xAI только low/high; запасной уровень тоже валидный.
            body["reasoning_effort"] = _EFFORT_GROK.get(effort, "low")
        else:
            body["reasoning_effort"] = _EFFORT_OPENAI.get(effort,
                                                          "medium")

    def _post(payload: dict):
        return httpx.post(spec["url"], json=payload, timeout=timeout_s,
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"})

    resp = _post(body)
    # Лестница деградации (МП-4): за каждый 400 снимается или
    # переименовывается РОВНО ОДИН отвергнутый параметр — по
    # СПЕЦИФИЧНОСТИ упоминания (не по фиксированному порядку: текст об
    # отвергнутом reasoning_effort может попутно упоминать
    # temperature). До трёх повторов — модель класса o1-mini отвергает
    # и temperature, и reasoning_effort по одному за раз.
    for _ in range(3):
        if resp.status_code != 400:
            break
        text = resp.text
        if "max_tokens" in body and (
                "max_completion_tokens" in (text or "").lower()
                or _unsupported_param(text, "max_tokens")):
            # Рассуждающие модели OpenAI: тот же лимит под новым
            # именем — конвертация, не потеря.
            body["max_completion_tokens"] = body.pop("max_tokens")
        elif "reasoning_effort" in body and _unsupported_param(
                text, "reasoning_effort", "reasoning"):
            body.pop("reasoning_effort")
        elif "temperature" in body and _unsupported_param(
                text, "temperature"):
            body.pop("temperature")
        else:
            break
        resp = _post(body)
    if resp.status_code != 200:
        raise ExternalLLMError(
            f"HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        payload = resp.json()
        choices = payload["choices"]
        text = (choices[0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, ValueError) as e:
        raise ExternalLLMError(
            f"неожиданный ответ ({e}): {resp.text[:300]}") from None
    if not text:
        raise ExternalLLMError("пустой ответ модели")
    _report_openai_usage(spec, model, payload)
    return text


def _report_openai_usage(spec: dict, model: str, payload) -> None:
    """Расход ответа протокола OpenAI (OpenAI, xAI): `usage.prompt_tokens`
    / `completion_tokens`, рассуждение — `completion_tokens_details.
    reasoning_tokens` (шаг 1 «Записи о прогоне»: токены по каждой
    модели). Нет блока — честное «не сообщил»."""
    from cdpm import llm_usage
    usage = payload.get("usage") if isinstance(payload, dict) else None
    provider = str(spec.get("id") or spec.get("title") or "openai")
    if not isinstance(usage, dict):
        llm_usage.not_counted(provider, model, "ответ без блока usage")
        return
    details = usage.get("completion_tokens_details")
    thinking = details.get("reasoning_tokens") if isinstance(details, dict) \
        else None
    cached = usage.get("prompt_tokens_details")
    cache_read = cached.get("cached_tokens") if isinstance(cached, dict) \
        else None
    llm_usage.report(provider, model,
                     input_tokens=usage.get("prompt_tokens"),
                     output_tokens=usage.get("completion_tokens"),
                     cache_read_tokens=cache_read,
                     thinking_tokens=thinking)


def _gemini_call(spec: dict, key: str, model: str, prompt: str,
                 timeout_s: float, temperature: float | None,
                 max_tokens: int | None,
                 effort: str | None = None) -> str:
    body: dict = {"contents": [{"parts": [{"text": prompt}]}]}
    gen: dict = {}
    if temperature is not None:
        gen["temperature"] = temperature
    if max_tokens is not None:
        gen["maxOutputTokens"] = max_tokens
    if effort:
        gen["thinkingConfig"] = {
            "thinkingBudget": _EFFORT_GEMINI.get(effort, -1)}
    if gen:
        body["generationConfig"] = gen
    url = f"{spec['url']}/{model}:generateContent"

    def _post(payload: dict):
        return httpx.post(url, json=payload, timeout=timeout_s,
                          headers={"x-goog-api-key": key,
                                   "Content-Type": "application/json"})

    resp = _post(body)
    if resp.status_code == 400 and effort and _unsupported_param(
            resp.text, "thinking"):
        retry = dict(body)
        retry["generationConfig"] = {
            k: v for k, v in gen.items() if k != "thinkingConfig"}
        if not retry["generationConfig"]:
            retry.pop("generationConfig")
        resp = _post(retry)
    if resp.status_code != 200:
        raise ExternalLLMError(
            f"HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        payload = resp.json()
        parts = payload["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts).strip()
    except (KeyError, IndexError, ValueError) as e:
        raise ExternalLLMError(
            f"неожиданный ответ ({e}): {resp.text[:300]}") from None
    if not text:
        raise ExternalLLMError("пустой ответ модели")
    _report_gemini_usage(spec, model, payload)
    return text


def _report_gemini_usage(spec: dict, model: str, payload) -> None:
    """Расход ответа Gemini: `usageMetadata.promptTokenCount` /
    `candidatesTokenCount`, рассуждение — `thoughtsTokenCount`,
    кэш — `cachedContentTokenCount`."""
    from cdpm import llm_usage
    meta = payload.get("usageMetadata") if isinstance(payload, dict) else None
    provider = str(spec.get("id") or spec.get("title") or "gemini")
    if not isinstance(meta, dict):
        llm_usage.not_counted(provider, model, "ответ без usageMetadata")
        return
    llm_usage.report(provider, model,
                     input_tokens=meta.get("promptTokenCount"),
                     output_tokens=meta.get("candidatesTokenCount"),
                     cache_read_tokens=meta.get("cachedContentTokenCount"),
                     thinking_tokens=meta.get("thoughtsTokenCount"))


def list_models(provider: str, *, key: str,
                timeout_s: float = 30.0) -> list[str]:
    """Живой перечень моделей, доступных КЛЮЧУ: зашитые подсказки
    устаревают быстрее кода (живая проверка связи Gemini 2026-08-18:
    ключ верный, а модель-подсказка не существует)."""
    spec = SPECS.get(provider)
    if spec is None:
        raise ExternalLLMError(f"неизвестный провайдер {provider!r}")
    if not key:
        raise ExternalLLMError(f"{spec['title']}: ключ не задан")
    try:
        if spec["protocol"] == "openai":
            base = spec["url"].split("/chat/")[0]
            resp = httpx.get(f"{base}/models", timeout=timeout_s,
                             headers={"Authorization": f"Bearer {key}"})
            if resp.status_code != 200:
                raise ExternalLLMError(
                    f"HTTP {resp.status_code}: {resp.text[:200]}")
            return sorted(m.get("id", "")
                          for m in resp.json().get("data", [])
                          if m.get("id"))
        resp = httpx.get(spec["url"], timeout=timeout_s,
                         headers={"x-goog-api-key": key})
        if resp.status_code != 200:
            raise ExternalLLMError(
                f"HTTP {resp.status_code}: {resp.text[:200]}")
        out = []
        for m in resp.json().get("models", []):
            methods = m.get("supportedGenerationMethods") or []
            if "generateContent" in methods:
                name = (m.get("name") or "")
                out.append(name.removeprefix("models/"))
        return sorted(x for x in out if x)
    except httpx.HTTPError as e:
        raise ExternalLLMError(
            f"{spec['title']} недоступен: {e}") from None


def complete(provider: str, *, key: str, model: str, prompt: str,
             timeout_s: float = 300.0, temperature: float | None = None,
             max_tokens: int | None = None,
             effort: str | None = None) -> str:
    """Один запрос к внешнему провайдеру. Отказ — исключение с
    названной причиной (повторы делает вызывающий канал). `effort` —
    уровень усилия рассуждения из EFFORT_LEVELS (МП-3)."""
    spec = SPECS.get(provider)
    if spec is None:
        raise ExternalLLMError(f"неизвестный провайдер {provider!r}")
    if not key:
        raise ExternalLLMError(
            f"{spec['title']}: ключ не задан — введите его в разделе "
            f"«Настройки»")
    if not model:
        raise ExternalLLMError(
            f"{spec['title']}: не выбрана модель")
    # Имя провайдера едет в описание адаптеру ради записи расхода
    # (`usage.provider` = ключ SPECS, а не заголовок для человека).
    spec = {**spec, "id": provider}
    try:
        if spec["protocol"] == "openai":
            return _openai_call(spec, key, model, prompt, timeout_s,
                                temperature, max_tokens, effort)
        return _gemini_call(spec, key, model, prompt, timeout_s,
                            temperature, max_tokens, effort)
    except httpx.HTTPError as e:
        raise ExternalLLMError(
            f"{spec['title']} недоступен: {e}") from None
