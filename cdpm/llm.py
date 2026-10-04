# -*- coding: utf-8 -*-
"""Узкий интерфейс языковой модели для CDPM.

CDPM автономен: ядро (`cdpm.extract`) получает модель как функцию
`prompt -> ответ` и не знает, кто отвечает. Реализации:

- в тестах — подменная функция с заготовленными ответами (конвейер
  проверяется без сети и без ключей);
- в живых прогонах — `cosmos_llm()`: ЕДИНСТВЕННАЯ точка соприкосновения
  с COSMOS, лениво импортирующая `webapp.llm_backend` и наследующая его
  дисциплину контура (У-03): выбор локальной/облачной модели по
  признаку набора `llm_scope` и настройкам контура. Своих каналов к
  моделям CDPM не заводит.

Решения владельца 2026-08-17: локальный прогон на документах заказчика —
облачная модель Claude с маскированием; ИЗОБРАЖЕНИЯ во внешние модели
не отправляются никогда — поэтому зрительный канал (`vision_channel`,
пакет ПИ-1) строится ТОЛЬКО по явному решению: локальной зрительной
моделью, если она настроена, либо облаком по явному разрешению
вызывающего; недоступный канал — честный отказ, не тихая деградация.
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import Callable

LLMFn = Callable[[str], str]
# Зрительный канал: (промпт, путь к изображению) -> текст ответа.
VisionFn = Callable[[str, Path], str]

# MIME-типы изображений первой очереди (ПИ-1).
_IMAGE_MEDIA = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".png": "image/png"}


class LLMReplyError(ValueError):
    """Ответ модели не разобрался по контракту; причина названа."""


def parallel_workers(default: int = 4) -> int:
    """Число одновременных вызовов модели независимыми чанками
    (ПАР-1, директива владельца 2026-08-21: блоки конвейера должны
    укладываться в секунды). Переменная окружения CDPM_PARALLEL;
    «1» — прежний строго последовательный путь."""
    import os
    try:
        return max(1, int(os.environ.get("CDPM_PARALLEL", "") or
                          default))
    except ValueError:
        return default


def parallel_map(fn, jobs: list, workers: int | None = None,
                 control=None, on_done=None) -> list:
    """Параллельные вызовы модели по НЕЗАВИСИМЫМ заданиям.

    Контракт детерминизма (ПАР-1): параллельны только сами вызовы —
    результаты возвращаются строго В ПОРЯДКЕ `jobs`, поэтому сборка
    (нумерация, склейка, реестры) остаётся воспроизводимой байт-в-байт
    и не зависит от порядка завершения потоков. `control` (пауза/стоп
    ПС-1) и `on_done(k)` (прогресс) выполняются в ГЛАВНОМ потоке по
    мере завершения заданий. Исключение любого задания — наружу
    (fail-closed): начатые задания дорабатывают, очередь отменяется;
    кэш чанков делает повтор дешёвым."""
    if workers is None:
        workers = parallel_workers()
    results = [None] * len(jobs)
    if workers <= 1 or len(jobs) <= 1:
        for k, job in enumerate(jobs):
            if control:
                control()
            results[k] = fn(job)
            if on_done:
                on_done(k)
        return results
    import concurrent.futures as _fut
    with _fut.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, job): k
                   for k, job in enumerate(jobs)}
        try:
            for f in _fut.as_completed(futures):
                k = futures[f]
                results[k] = f.result()
                if control:
                    control()
                if on_done:
                    on_done(k)
        except BaseException:
            for f in futures:
                f.cancel()
            raise
    return results


class LLMCallError(RuntimeError):
    """Канал модели отказал (конверт ok=False); причина названа."""


# РВ-119/РВ-138 (04.10.2026): имени облачной модели в коде нет — модель
# берётся из настройки площадки (cloud_default_model) или роли блока
# (шестерёнка); без неё — отказ словами, а не зашитое умолчание.
CLOUD_MODEL_MISSING = ("модель облака не задана: укажите настройку "
                       "cloud_default_model площадки или модель роли в "
                       "настройках блока — умолчания в коде нет (РВ-119)")


# ── Отпечаток канала (аудит 04.09, пункт A11) ──
# Ответ модели — функция И запроса, И канала, который его дал. Поэтому
# каждый канал несёт свой отпечаток (провайдер, модель ПОСЛЕ подстановки
# умолчания, адрес, усилие, температура, максимум токенов), а ключи
# кэшей стадий (извлечение, противоречия, второе чтение, проход логики,
# аудит цикла) включают его: смена модели, провайдера или политики файла
# не отдаёт ответы прежнего канала. Ключей и иных секретов в отпечатке
# нет — только имена и адреса.

def stamp_identity(fn, **fields):
    """Записать отпечаток канала в атрибут `identity` функции канала;
    уже имеющиеся поля отпечатка сохраняются, новые дополняют их
    (роль, класс политики файла — от вызывающей стороны)."""
    base: dict = {}
    prev = getattr(fn, "identity", None)
    if prev:
        try:
            got = json.loads(prev)
            base = got if isinstance(got, dict) else {"channel": prev}
        except (TypeError, ValueError):
            base = {"channel": str(prev)}
    base.update({k: v for k, v in fields.items() if v is not None})
    text = json.dumps(base, ensure_ascii=False, sort_keys=True)
    try:
        fn.identity = text
        return fn
    except AttributeError:
        def _wrapped(prompt: str) -> str:
            return fn(prompt)
        _wrapped.identity = text
        return _wrapped


def channel_identity(fn) -> str:
    """Отпечаток канала для соли ключа кэша; у канала без отпечатка
    (подменные функции тестов, прежние обёртки) — пустая строка."""
    return str(getattr(fn, "identity", "") or "")


def cosmos_llm(scope: str = "any", timeout_s: float = 300.0,
               requested: str | None = None, attempts: int | None = None,
               backoff_s: float | None = None,
               model: str | None = None,
               effort: str | None = None,
               temperature: float | None = None,
               max_tokens: int | None = None,
               base_url: str | None = None,
               think: bool | None = None,
               num_ctx: int | None = None) -> LLMFn:
    """Модель через дисциплину COSMOS (ленивый импорт webapp.llm_backend —
    вне этой функции ядро CDPM webapp не знает). `requested` — просьба о
    конкретном канале ("cloud" — облачная Claude); законность просьбы
    решает resolve_backend по scope.

    Разовый отказ канала (пустой ответ, таймаут) получает `attempts`
    попыток с нарастающей паузой: на живом прогоне 2026-08-17 один
    пустой ответ CLI на 14-м запросе убил 35 минут работы — долгому
    конвейеру нужен канал, переживающий единичные сбои.

    Умолчания попыток/пауз переопределяются переменными
    CDPM_LLM_ATTEMPTS / CDPM_LLM_BACKOFF_S (линия UrA 2026-08-30:
    Ollama legion эпизодически падает и перезапускается службой —
    рестарт длится дольше, чем 3 попытки с паузами 15/30 с, и
    аудит-цикл терял заходы целиком; терпеливый канал — данные
    контура запуска, не правка вызывающих)."""
    import os as _os

    from webapp import llm_backend
    if attempts is None:
        try:
            attempts = max(1, int(_os.environ.get(
                "CDPM_LLM_ATTEMPTS", "") or 3))
        except ValueError:
            attempts = 3
    if backoff_s is None:
        try:
            backoff_s = max(0.0, float(_os.environ.get(
                "CDPM_LLM_BACKOFF_S", "") or 15.0))
        except ValueError:
            backoff_s = 15.0

    backend = llm_backend.resolve_backend(scope, requested)
    if num_ctx is not None and backend != "local":
        raise LLMCallError("num_ctx применим только к локальному каналу")
    try:
        pinned_num_ctx = llm_backend.ollama_context_length(num_ctx) \
            if backend == "local" else None
    except llm_backend.LLMError as exc:
        raise LLMCallError(str(exc)) from exc
    if backend == "cloud" and not model:
        # API-ветка complete() включается только при ВЫБРАННОЙ модели
        # (иначе — агентный CLI, подписочный путь). Модель без явной
        # модели роли — из настройки cloud_default_model площадки, и на
        # пути API (ключ есть), и на пути CLI (ключа нет): без неё —
        # отказ словами ДО вызова транспорта, запасного умолчания CLI
        # нет (РВ-119; РВ-140 п. 3, слово владельца 04.10 05:35).
        from cdpm import providers as providers_mod
        model = providers_mod.load_settings().get("cloud_default_model")
        if not model:
            raise LLMCallError(CLOUD_MODEL_MISSING)

    def _transport() -> str:
        """Транспорт облачного канала, как его выбирает
        llm_backend.complete: Messages API — при названной модели и
        ключе; иначе — командная строка Claude по подписке.
        Локальный канал — «local»."""
        if backend != "cloud":
            return "local"
        return "api" if (model and llm_backend._settings.has_api_key()) \
            else "cli"

    # S06-R1 (ревью GPT-020, 06.09.2026): транспорт закрепляется при
    # ПОСТРОЕНИИ канала. Прежде каждая попытка заново выбирала API/CLI
    # по текущему ключу: снятый в паузе ключ уводил повтор в командную
    # строку при отпечатке «api» — запрещённая таблицей S06 подмена
    # канала (п. «повтор того же канала — не fallback»).
    pinned_transport = _transport()

    def _call(prompt: str) -> str:
        # complete() возвращает КОНВЕРТ {ok, text, backend, model,
        # error} и не бросает исключений — распаковка и превращение
        # отказа в исключение лежат на вызывающем. Дефект «конверт
        # вместо текста» пойман первым живым прогоном 2026-08-17:
        # тесты ходили только через подменные функции.
        last: dict = {}
        for i in range(attempts):
            now = _transport()
            if now != pinned_transport:
                raise LLMCallError(
                    f"канал {backend}/{model or '(claude-cli)'}: конфигурация "
                    f"изменилась во время повторов (транспорт при построении "
                    f"{pinned_transport}, сейчас {now}: ключ API снят или "
                    f"появился) — повтор тем же каналом невозможен, канал "
                    f"нужно построить заново; подмены транспорта нет")
            # S06-R1-RACE (GPT-025): транспорт закрепляется В
            # ИСПОЛНИТЕЛЕ — предпроверка выше лишь даёт раннее слово,
            # выбор по ключу внутри complete закреплённым значением
            # отключён; смена ключа между проверкой и вызовом даёт
            # отказ конвертом, не подмену.
            got = llm_backend.complete(
                prompt, backend=backend, timeout_s=timeout_s, model=model,
                effort=effort, temperature=temperature,
                max_tokens=max_tokens, base_url=base_url, think=think,
                transport=(pinned_transport if backend == "cloud" else None),
                **({"num_ctx": pinned_num_ctx} if backend == "local" else {}))
            if got.get("ok"):
                # Расход — из поля `usage` конверта (контракт с COSMOS,
                # шаг 1 «Записи о прогоне»); конверт без поля — честное
                # «канал не сообщил», командная строка — «не считается».
                from cdpm import llm_usage
                if got.get("usage") is not None:
                    llm_usage.report_raw(got.get("usage"))
                elif pinned_transport == "cli":
                    llm_usage.not_counted(
                        backend, model or "(claude-cli)",
                        "командная строка по подписке — расход не сообщается")
                else:
                    llm_usage.not_counted(
                        backend, model or got.get("model"),
                        "конверт канала без поля usage")
                return got["text"]
            last = got
            if got.get("retryable") is False:
                break
            if i + 1 < attempts:
                time.sleep(backoff_s * (i + 1))
        raise LLMCallError(
            f"канал {last.get('backend')}/{last.get('model')} отказал "
            f"после {i + 1} попыток: {last.get('error')}")
    # Отпечаток канала (A11): модель — ПОСЛЕ подстановки умолчания:
    # локальный канал без модели идёт моделью умолчания Ollama, облако
    # без модели — командной строкой Claude (своя модель у CLI).
    if backend == "local":
        eff_model = model or llm_backend.ollama_model()
    else:
        eff_model = model or "(claude-cli)"
    stamp_identity(_call, provider=backend, model=eff_model,
                   base_url=base_url, effort=effort,
                   temperature=temperature, max_tokens=max_tokens,
                   think=think, transport=pinned_transport, num_ctx=pinned_num_ctx)
    return _call


def channel(scope: str = "any", provider: str | None = None,
            model: str | None = None, timeout_s: float = 300.0,
            attempts: int = 3, backoff_s: float = 15.0,
            temperature: float | None = None,
            max_tokens: int | None = None,
            effort: str | None = None,
            base_url: str | None = None,
            think: bool | None = None,
            num_ctx: int | None = None) -> LLMFn:
    """Канал модели по имени провайдера — единая точка выбора для всех
    ролей CDPM.

    `local`/`cloud`/`auto`/None — инфраструктура COSMOS (cosmos_llm);
    `openai`/`grok`/`gemini` — собственные адаптеры CDPM.

    ДИСЦИПЛИНА КОНТУРА (У-03): внешний провайдер — это облако, поэтому
    при `scope='local-only'` он запрещён так же, как облачная Claude;
    отказ — ScopeViolation инфраструктуры COSMOS, чтобы правило было
    ОДНО на весь продукт, а не два похожих."""
    from cdpm import external_llm, providers as providers_mod

    if provider in (None, "", "auto", "local", "cloud"):
        # МП-4: температура и максимум токенов слота доезжают и до
        # облачной Claude (Messages API) — прежде молча выбрасывались.
        return cosmos_llm(scope, timeout_s=timeout_s,
                          requested=(provider or None), attempts=attempts,
                          backoff_s=backoff_s, model=model,
                          effort=effort, temperature=temperature,
                          max_tokens=max_tokens, base_url=base_url,
                          think=think, num_ctx=num_ctx)
    if provider not in external_llm.SPECS:
        raise ValueError(f"неизвестный провайдер {provider!r}")
    if scope == "local-only":
        from webapp.llm_backend import ScopeViolation
        raise ScopeViolation(
            f"набор помечен local-only; внешний провайдер "
            f"{external_llm.SPECS[provider]['title']} для него "
            f"использовать нельзя")

    if num_ctx is not None:
        raise LLMCallError("num_ctx применим только к локальному каналу")

    key = providers_mod.provider_key(provider)
    chosen = model if model not in (None, "", "(по умолчанию)") \
        else (external_llm.SPECS[provider]["models"] or [""])[0]

    def _call(prompt: str) -> str:
        last = ""
        for i in range(attempts):
            try:
                return external_llm.complete(
                    provider, key=key or "", model=chosen, prompt=prompt,
                    timeout_s=timeout_s, temperature=temperature,
                    max_tokens=max_tokens, effort=effort)
            except external_llm.ExternalLLMError as e:
                last = str(e)
                if "ключ не задан" in last or "не выбрана модель" in last \
                        or "HTTP 400" in last:
                    # МП-4: 400 после лестницы деградации —
                    # детерминированный отказ, повтор не поможет.
                    break
                if i + 1 < attempts:
                    time.sleep(backoff_s * (i + 1))
        raise LLMCallError(
            f"канал {provider}/{chosen} отказал после {attempts} "
            f"попыток: {last}")
    stamp_identity(_call, provider=provider, model=chosen,
                   effort=effort, temperature=temperature,
                   max_tokens=max_tokens)
    return _call


# ---------------------------------------------------------------------------
# Зрительный канал (ПИ-1): изображение документа -> дословный текст
# ---------------------------------------------------------------------------

def local_vision_model() -> str | None:
    """Имя локальной зрительной модели (Ollama) или None. Задаётся
    переменной CDPM_LOCAL_VISION_MODEL либо ключом `local_vision_model`
    в настройках CDPM; текстовая модель контура зрения НЕ умеет, поэтому
    умолчания нет — не настроено значит недоступно (fail-closed)."""
    import os
    env = (os.environ.get("CDPM_LOCAL_VISION_MODEL") or "").strip()
    if env:
        return env
    from cdpm import providers as providers_mod
    got = (providers_mod.load_settings().get("local_vision_model")
           or "").strip()
    return got or None


def local_vision_available() -> bool:
    """Есть ли локальный зрительный канал (нужно заморозке, чтобы
    выбрать строжайший доступный класс для изображений)."""
    return local_vision_model() is not None


def _image_media_type(image_path: Path) -> str:
    media = _IMAGE_MEDIA.get(image_path.suffix.lower())
    if not media:
        raise LLMCallError(
            f"{image_path.name}: формат {image_path.suffix!r} не "
            f"изображение первой очереди {tuple(_IMAGE_MEDIA)}")
    return media


def _ollama_vision(prompt: str, image_path: Path, model: str,
                   timeout_s: float) -> str:
    """Один запрос к локальной зрительной модели Ollama: изображение
    уходит полем `images` (base64) запроса /api/generate — данные не
    покидают контур."""
    import urllib.error
    import urllib.request
    from webapp import llm_backend
    body = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "images": [base64.b64encode(image_path.read_bytes())
                   .decode("ascii")],
        "options": {"temperature": 0.0},
    }
    req = urllib.request.Request(
        llm_backend.ollama_url() + "/api/generate",
        data=json.dumps(body).encode("utf-8"),
        headers=llm_backend._ollama_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:                                 # noqa: BLE001
        raise LLMCallError(f"локальная зрительная модель {model} "
                           f"недоступна: {e}") from e
    text = str(payload.get("response") or "").strip()
    if not text:
        raise LLMCallError(f"локальная зрительная модель {model} "
                           f"вернула пустой ответ")
    # Расход Ollama: prompt_eval_count / eval_count (шаг 1 «Записи о прогоне»).
    from cdpm import llm_usage
    if payload.get("eval_count") is not None or payload.get("prompt_eval_count") is not None:
        llm_usage.report("local", model,
                         input_tokens=payload.get("prompt_eval_count"),
                         output_tokens=payload.get("eval_count"))
    else:
        llm_usage.not_counted("local", model, "ответ без счётчиков eval_count")
    return text


def _cli_vision(prompt: str, image_path: Path, timeout_s: float) -> str:
    """Один запрос облачному Claude CLI (-p): CLI умеет читать файл по
    абсолютному пути, названному в промпте, своим инструментом чтения —
    проверено живой пробой 2026-08-20 (кириллица и опечатки переписаны
    дословно). Изображение с машины не уходит вложением — его читает
    сам CLI."""
    from webapp import llm_backend
    ask = (f"Прочитай файл изображения по абсолютному пути (у тебя "
           f"есть инструмент чтения файлов): {image_path.resolve()}"
           f"\n\n{prompt}")
    try:
        text = llm_backend._cli_generate(ask, timeout_s)
    except llm_backend.LLMError as e:
        raise LLMCallError(f"облачный CLI-канал зрения отказал: "
                           f"{e}") from e
    from cdpm import llm_usage
    llm_usage.not_counted("cloud", "(claude-cli)",
                          "командная строка по подписке — расход не сообщается")
    return text


def _api_vision(prompt: str, image_path: Path, key: str, model: str,
                timeout_s: float) -> str:
    """Один запрос зрения через Anthropic Messages API (свой ключ из
    настроек провайдеров): изображение — блоком base64."""
    from webapp import llm_backend
    try:
        import httpx
    except ImportError as e:                       # pragma: no cover
        raise LLMCallError("httpx недоступен для канала API") from e
    body = {
        "model": model,
        "max_tokens": 16000,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {
                "type": "base64",
                "media_type": _image_media_type(image_path),
                "data": base64.b64encode(image_path.read_bytes())
                        .decode("ascii")}},
            {"type": "text", "text": prompt},
        ]}],
    }
    headers = {"x-api-key": key,
               "anthropic-version": llm_backend.ANTHROPIC_VERSION,
               "content-type": "application/json"}
    try:
        resp = httpx.post(llm_backend.ANTHROPIC_API_URL,
                          headers=headers, json=body, timeout=timeout_s)
    except httpx.HTTPError as e:
        raise LLMCallError(f"канал API зрения отказал: {e}") from e
    if resp.status_code != 200:
        raise LLMCallError(f"канал API зрения: HTTP "
                           f"{resp.status_code}: {resp.text[:300]}")
    payload = resp.json()
    parts = [b.get("text", "") for b in payload.get("content", [])
             if isinstance(b, dict) and b.get("type") == "text"]
    text = "".join(parts).strip()
    if not text:
        raise LLMCallError("канал API зрения вернул пустой текст")
    # Расход Messages API: input/output и кэш (шаг 1 «Записи о прогоне»).
    from cdpm import llm_usage
    usage = payload.get("usage")
    if isinstance(usage, dict):
        llm_usage.report("cloud", model,
                         input_tokens=usage.get("input_tokens"),
                         output_tokens=usage.get("output_tokens"),
                         cache_write_tokens=usage.get("cache_creation_input_tokens"),
                         cache_read_tokens=usage.get("cache_read_input_tokens"))
    else:
        llm_usage.not_counted("cloud", model, "ответ без блока usage")
    return text


def vision_channel(scope: str = "any", requested: str | None = None,
                   timeout_s: float = 300.0, attempts: int = 2,
                   backoff_s: float = 15.0,
                   cloud_model: str | None = None,
                   local_model: str | None = None) -> VisionFn:
    """Зрительный канал по дисциплине контура: законность просьбы
    решает resolve_backend по scope (как у cosmos_llm).

    - локальный канал: настроенная зрительная модель Ollama; модели нет
      — честный отказ «распознавание изображений локальной моделью
      недоступно» УЖЕ при построении канала (fail-closed, тихой
      деградации в текстовую модель нет);
    - облачный канал: Claude CLI читает изображение по абсолютному
      пути из промпта (проверено живой пробой 2026-08-20); CLI не
      найден — прямой вызов Anthropic API своим ключом из настроек
      провайдеров; нет ни CLI, ни ключа — честный отказ."""
    from webapp import llm_backend

    backend = llm_backend.resolve_backend(scope, requested)
    if backend == "local":
        # Роль «vision» раскладки (2.09) главнее ключа настроек.
        model = local_model or local_vision_model()
        if not model:
            raise LLMCallError(
                "распознавание изображений локальной моделью "
                "недоступно: зрительная модель не настроена (ключ "
                "local_vision_model в настройках CDPM или переменная "
                "CDPM_LOCAL_VISION_MODEL)")

        def _one(prompt: str, image_path: Path) -> str:
            return _ollama_vision(prompt, image_path, model, timeout_s)
    else:
        from cdpm import providers as providers_mod
        key = providers_mod.provider_key("cloud")
        # Роль «vision» раскладки (2.09) главнее ключа настроек
        # cloud_vision_model — тот остаётся площадочным умолчанием.
        configured = cloud_model or providers_mod.load_settings().get(
            "cloud_vision_model")
        # Раскладка владельца первична (блок 1 обратной задачи H0,
        # Тест 2.09): при заданной модели зрения и ключе API зрение
        # идёт Messages API именно этой моделью; CLI-путь — только
        # без раскладки (у CLI своя модель и свой логин процесса —
        # «Not logged in» ронял заморозку при живом ключе).
        if configured and key:
            def _one(prompt: str, image_path: Path) -> str:
                return _api_vision(prompt, image_path, key,
                                   configured, timeout_s)
        # РВ-140 п. 3 (слово владельца 04.10 05:35): запасных путей молча
        # нет — ни CLI со своей моделью (он не берёт заданную модель), ни
        # «новейшей модели из перечня ключа». Нет модели зрения или нет
        # ключа — отказ словами.
        elif not configured:
            raise LLMCallError(
                "облачный канал зрения: модель не задана — роль «vision» "
                "раскладки или настройка cloud_vision_model; умолчания "
                "и запасного пути нет (РВ-140)")
        else:
            raise LLMCallError(
                f"облачный канал зрения: модель {configured} задана, но "
                f"ключа API облака нет — путь командной строки заданную "
                f"модель не исполняет, подмены нет (РВ-140)")

    def _call(prompt: str, image_path: Path) -> str:
        last = ""
        for i in range(attempts):
            try:
                return _one(prompt, image_path)
            except LLMCallError as e:
                last = str(e)
                if i + 1 < attempts:
                    time.sleep(backoff_s * (i + 1))
        raise LLMCallError(f"зрительный канал отказал после "
                           f"{attempts} попыток: {last}")
    return _call


def parse_json_reply(text: str) -> dict:
    """JSON из ответа модели: допускается обёртка в ограждение
    ```json … ``` и произвольный текст вокруг ПЕРВОГО объекта — модели
    любят предисловия; всё остальное — отказ с показом начала ответа."""
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise LLMReplyError(
            f"в ответе модели нет JSON-объекта; начало ответа: "
            f"{text[:200]!r}")
    try:
        got = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise LLMReplyError(
            f"JSON ответа не разбирается ({e}); начало ответа: "
            f"{text[:200]!r}") from None
    if not isinstance(got, dict):
        raise LLMReplyError("ответ модели — не JSON-объект")
    return got
