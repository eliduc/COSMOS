"""Pluggable LLM backend for the solver-advisor knowledge base (2026-07-14).

Two providers behind one `complete()` call:

  * "cloud"  — the Claude Code CLI (`claude -p`), prompt over stdin. Same
    binary/credential the evolution pipeline already uses; reachable only
    where CLAUDE_CODE_OAUTH_TOKEN is in the environment.
  * "local"  — the user's OWN self-hosted Ollama server (HTTP
    /api/generate): either on this machine (127.0.0.1:11434) or the
    dedicated inference host internallm.ai (CREW_OLLAMA_URL). Either way
    the data stays within the user's own infrastructure — no third-party
    AI provider is involved, which is what the internal contour requires.

Data-contour guard (`resolve_backend`): every dataset carries an
`llm_scope` ("local-only" | "any"). A "local-only" dataset may ONLY be sent
to the local backend — asking for "cloud" is a ScopeViolation, surfaced
loudly rather than silently downgraded. An "any" dataset may use either,
defaulting to the local backend unless the caller asks for cloud. The
consult path additionally sends only non-PII aggregates, so scope is a
second line of defence, not the only one.

The two low-level calls (`_cli_generate`, `_ollama_generate`) are the
monkeypatch seams for tests — no real subprocess/HTTP in unit tests.

Расход моделей (решение владельца № 1 от 10.09.2026). Каждый вызов
трубы разбирает ответное `usage` провайдера в один словарь имён
(USAGE_KEYS) и кладёт его двумя путями: полем `usage` конверта
`complete` и переменной потока (`take_last_usage`) — для писателей
журналов, которые зовут канал как `(prompt) -> str` и конверта не
видят. Считаются ТОКЕНЫ, не деньги: цены на момент прогона и на момент
просмотра записи различаются, стоимость берётся при просмотре.
Канал, не отдающий usage (агентный CLI подписки), даёт строку
`counted: false` с причиной словами — не ноль.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import urllib.error
import urllib.request

from . import settings as _settings
from .registry import REPO_ROOT

BACKENDS = ("local", "cloud")

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_VERSION = "2023-06-01"


class LLMError(RuntimeError):
    """The requested backend was reachable-in-principle but failed (spawn
    error, timeout, HTTP error, empty reply). Callers treat it as
    'no recommendation' and fall back to a deterministic default."""


class LLMNonRetryableError(LLMError):
    """A configuration/context refusal cannot heal by repeating the same call."""


class ScopeViolation(RuntimeError):
    """A cloud backend was requested for a local-only dataset."""


# -------------------------------------------------------------- расход моделей

#: Решение владельца № 1 от 10.09.2026 (проект «Запись о прогоне», раздел 6):
#: расход моделей считается В ТОКЕНАХ по каждой задействованной модели —
#: цены на момент прогона и на момент просмотра записи различаются, поэтому
#: деньги в запись не пишутся вовсе. Имена полей — по соглашениям
#: OpenTelemetry GenAI (раздел 9 проекта: словарь берём готовый, зависимость —
#: нет): один словарь на все каналы продукта.
USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens",
              "cache_write_tokens", "thinking_tokens")

#: Канал агентного CLI подписки ответного usage не отдаёт вовсе: вызовы
#: считаются, токены — нет, и запись говорит об этом словами, а не нулём
#: (ноль читался бы как «модель ничего не потратила»).
CLI_UNCOUNTED_NOTE = "канал CLI не отдаёт usage"

#: Расход ПОСЛЕДНЕГО вызова этого потока. Канал модели — вызываемое
#: `(prompt) -> str` (сертификатор COSMOS, роли CDPM), и протащить конверт
#: сквозь него, не меняя подписи у всех вызывающих, нельзя. Поэтому труба
#: кладёт разобранный usage сюда, а писатель журнала снимает его сразу
#: после вызова в ТОМ ЖЕ потоке (нажатие и сверка идут каждое в своём
#: потоке — общей переменной между ними нет).
_LAST = threading.local()


def _clear_last_usage() -> None:
    _LAST.usage = None


def set_last_usage(usage: dict | None) -> dict | None:
    """Запомнить расход последнего вызова этого потока (труба канала)."""
    _LAST.usage = dict(usage) if isinstance(usage, dict) else None
    return _LAST.usage


def peek_last_usage() -> dict | None:
    """Прочитать расход последнего вызова этого потока, НЕ снимая его
    (конверт `complete` кладёт копию, а журнал снимает оригинал)."""
    return getattr(_LAST, "usage", None)


def take_last_usage() -> dict | None:
    """Снять расход последнего вызова этого потока (читается один раз:
    несняток не приписывается следующему вызову)."""
    got = getattr(_LAST, "usage", None)
    _LAST.usage = None
    return got


def uncounted_usage(provider: str | None, model: str | None,
                    channel: str | None, note: str,
                    reason: str | None = None) -> dict:
    """Запись расхода канала, который токенов не отдаёт: `counted: false`,
    причина словами (`note`) и кодом (`reason` — по договору с CDPM
    10.09.2026: у канала CLI `reason: "cli"`; без кода — имя канала)."""
    return {"provider": provider, "model": model, "channel": channel,
            "counted": False, "reason": reason or channel or "unknown",
            "note": note}


def _int_or_none(value):
    try:
        got = int(value)
    except (TypeError, ValueError):
        return None
    return got


def _usage_row(provider: str | None, model: str | None, channel: str | None,
               pairs: dict) -> dict:
    """Строка расхода: провайдер, модель, канал и ТОЛЬКО те счётчики,
    которые провайдер действительно назвал (отсутствующий счётчик — не
    ноль; ноль читался бы как измеренная величина)."""
    out: dict = {"provider": provider, "model": model, "channel": channel,
                 "counted": False}
    for key in USAGE_KEYS:
        got = _int_or_none(pairs.get(key))
        if got is not None:
            out[key] = got
            out["counted"] = True
    if not out["counted"]:
        out["note"] = "провайдер не назвал ни одного счётчика токенов"
    return out


def usage_anthropic(payload: dict, *, model: str | None = None,
                    channel: str | None = None,
                    provider: str = "anthropic") -> dict:
    """Расход ответа Messages API Anthropic: `usage.input_tokens`,
    `usage.output_tokens`, `usage.cache_creation_input_tokens` (запись
    кэша), `usage.cache_read_input_tokens` (чтение кэша). Размышления
    Anthropic отдельным счётчиком не называет — они входят в
    `output_tokens`, и выдуманного `thinking_tokens` здесь нет."""
    u = (payload or {}).get("usage")
    u = u if isinstance(u, dict) else {}
    return _usage_row(provider, (payload or {}).get("model") or model, channel,
                      {"input_tokens": u.get("input_tokens"),
                       "output_tokens": u.get("output_tokens"),
                       "cache_write_tokens": u.get("cache_creation_input_tokens"),
                       "cache_read_tokens": u.get("cache_read_input_tokens")})


def usage_openai(payload: dict, *, model: str | None = None,
                 channel: str | None = None,
                 provider: str = "openai") -> dict:
    """Расход ответа OpenAI-совместимого API (сам OpenAI и Grok, который
    держит ту же форму): `usage.prompt_tokens`, `usage.completion_tokens`,
    `usage.prompt_tokens_details.cached_tokens` (чтение кэша),
    `usage.completion_tokens_details.reasoning_tokens` (размышления)."""
    u = (payload or {}).get("usage")
    u = u if isinstance(u, dict) else {}
    pd = u.get("prompt_tokens_details")
    pd = pd if isinstance(pd, dict) else {}
    cd = u.get("completion_tokens_details")
    cd = cd if isinstance(cd, dict) else {}
    return _usage_row(provider, (payload or {}).get("model") or model, channel,
                      {"input_tokens": u.get("prompt_tokens"),
                       "output_tokens": u.get("completion_tokens"),
                       "cache_read_tokens": pd.get("cached_tokens"),
                       "thinking_tokens": cd.get("reasoning_tokens")})


def usage_gemini(payload: dict, *, model: str | None = None,
                 channel: str | None = None,
                 provider: str = "gemini") -> dict:
    """Расход ответа Gemini: `usageMetadata.promptTokenCount`,
    `usageMetadata.candidatesTokenCount`, `cachedContentTokenCount`
    (чтение кэша), `thoughtsTokenCount` (размышления)."""
    u = (payload or {}).get("usageMetadata")
    u = u if isinstance(u, dict) else {}
    return _usage_row(provider, (payload or {}).get("modelVersion") or model,
                      channel,
                      {"input_tokens": u.get("promptTokenCount"),
                       "output_tokens": u.get("candidatesTokenCount"),
                       "cache_read_tokens": u.get("cachedContentTokenCount"),
                       "thinking_tokens": u.get("thoughtsTokenCount")})


def usage_ollama(payload: dict, *, model: str | None = None,
                 channel: str | None = None,
                 provider: str = "ollama") -> dict:
    """Расход ответа локальной Ollama (/api/generate): `prompt_eval_count`
    — вход, `eval_count` — выход."""
    payload = payload if isinstance(payload, dict) else {}
    return _usage_row(provider, payload.get("model") or model, channel,
                      {"input_tokens": payload.get("prompt_eval_count"),
                       "output_tokens": payload.get("eval_count")})


#: Разборщики по имени провайдера — одна точка выбора для всех каналов.
USAGE_PARSERS = {"anthropic": usage_anthropic, "openai": usage_openai,
                 "grok": lambda payload, **kw: usage_openai(
                     payload, provider="grok",
                     **{k: v for k, v in kw.items() if k != "provider"}),
                 "gemini": usage_gemini, "ollama": usage_ollama}


def parse_usage(provider: str, payload: dict, *, model: str | None = None,
                channel: str | None = None) -> dict:
    """Расход ответа провайдера по его имени; неизвестный провайдер —
    строка «не считается» с причиной, а не молчаливый ноль."""
    fn = USAGE_PARSERS.get(str(provider or "").strip().lower())
    if fn is None:
        return uncounted_usage(provider, model, channel,
                               "разборщика usage для провайдера %r нет"
                               % provider)
    return fn(payload, model=model, channel=channel)


def merge_usage(rows) -> dict:
    """Свод расхода нескольких вызовов ОДНОЙ модели: счётчики
    складываются, `calls` — число вызовов, `counted` — назвал ли токены
    хоть один вызов; несчитанные вызовы называются словами в `note` и
    числом в `uncounted_calls`."""
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    out: dict = {"calls": len(rows), "counted": False}
    notes: list[str] = []
    uncounted = 0
    for row in rows:
        if row.get("counted"):
            for key in USAGE_KEYS:
                got = _int_or_none(row.get(key))
                if got is not None:
                    out[key] = out.get(key, 0) + got
                    out["counted"] = True
        else:
            uncounted += 1
            note = str(row.get("note") or "").strip()
            if note and note not in notes:
                notes.append(note)
    if uncounted:
        out["uncounted_calls"] = uncounted
    if notes:
        out["note"] = "; ".join(notes)
    return out


# ------------------------------------------------------------------- config

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def claude_cli() -> str:
    return _env("CREW_CLAUDE_CLI", "claude")


def resolve_claude_cli() -> str | None:
    """Находимый путь к Claude CLI или None. Явный CREW_CLAUDE_CLI с
    каталогом уважается как есть; голое имя резолвится через PATH и — на
    Windows — через известный npm-каталог пользователя: службы площадок
    стартуют с урезанным PATH без %APPDATA%\\npm, и спавн по голому имени
    падал классом «cloud CLI spawn failed: WinError 2» (Test, оценка
    публикаций 2026-08-06)."""
    exe = claude_cli()
    if os.path.dirname(exe):
        return exe if os.path.isfile(exe) else None
    hit = shutil.which(exe)
    if hit:
        return hit
    if os.name == "nt":
        appdata = _env("APPDATA")
        if appdata:
            for suffix in (".cmd", ".exe"):
                cand = os.path.join(appdata, "npm", exe + suffix)
                if os.path.isfile(cand):
                    return cand
    return None


def ollama_url() -> str:
    # Super-admin Settings override wins over the env default.
    return (_settings.get_ollama_url()
            or _env("CREW_OLLAMA_URL", "http://127.0.0.1:11434")).rstrip("/")


class LocalModelNotConfigured(RuntimeError):
    """Локальная модель площадки не названа ни настройкой, ни пусковиком."""


def ollama_model_or_none() -> str | None:
    """Локальная модель площадки для ПОКАЗА (статус, перечень каналов,
    страница настроек, отпечатки): настройка супер-админа, иначе
    `CREW_OLLAMA_MODEL` пусковика, иначе None — имени модели в коде нет
    (РВ-119 / РВ-138, исправление 2: «модель — в настройках блока, не
    умолчанием в коде»)."""
    return ((_settings.get_ollama_model() or "").strip()
            or (_env("CREW_OLLAMA_MODEL") or "").strip() or None)


def ollama_model() -> str:
    """Локальная модель площадки для ВЫЗОВА. Не названа — отказ словами,
    а не подстановка зашитого имени: прежнее умолчание `qwen3.6:35b-a3b`
    молча выбирало модель за владельца (РВ-119 / РВ-138, исправление 2).
    Пусковики площадок задают `CREW_OLLAMA_MODEL` явно."""
    model = ollama_model_or_none()
    if not model:
        raise LocalModelNotConfigured(
            "локальная модель площадки не задана: настройка «модель Ollama» "
            "(шестерёнка блока / настройки супер-админа) либо "
            "CREW_OLLAMA_MODEL пусковика площадки — имени модели в коде нет")
    return model


def _ollama_headers() -> dict:
    """Request headers for the self-hosted Ollama. A custom User-Agent is
    REQUIRED when the server sits behind Cloudflare (the default
    Python-urllib UA is rejected with 403); the bearer key is sent only
    when configured (internallm.ai gates /api/generate with one)."""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "cosmos-solver-advisor/1.0",
    }
    key = _env("CREW_OLLAMA_KEY")
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def default_backend() -> str:
    """Backend used for an 'any'-scope dataset when the caller does not pin
    one. Local by default so nothing egresses without an explicit choice.
    Super-admin Settings override wins over the env default."""
    b = _settings.get_default_backend() or _env("CREW_LLM_DEFAULT_BACKEND",
                                                 "local")
    return b if b in BACKENDS else "local"


# ------------------------------------------------------------- scope guard

def resolve_backend(scope: str, requested: str | None) -> str:
    """Map (dataset scope, caller preference) -> concrete backend, enforcing
    the contour rule. `requested` may be "local", "cloud", None or "auto"."""
    if scope not in ("local-only", "any"):
        raise ValueError(f"unknown llm_scope {scope!r}")
    req = (requested or "auto").strip().lower()
    if req not in ("local", "cloud", "auto"):
        raise ValueError(f"unknown backend request {requested!r}")
    if scope == "local-only":
        if req == "cloud":
            raise ScopeViolation(
                "dataset is marked local-only; the external cloud LLM cannot "
                "be used for it")
        return "local"
    # scope == "any"
    if req == "auto":
        return default_backend()
    return req


# --------------------------------------------------------- low-level calls

def _cli_generate(prompt: str, timeout_s: float, cwd: str | None = None,
                  model: str | None = None) -> str:
    """One-shot Claude CLI completion. Prompt via stdin ONLY (a .cmd
    trampoline on Windows truncates an argv prompt at the first newline).
    When `model` is given it is passed as --model (subscription OAuth).

    Расход: канал CLI ответного usage не отдаёт — в поток кладётся строка
    «не считается» с причиной словами (решение владельца № 1)."""
    _clear_last_usage()
    set_last_usage(uncounted_usage("anthropic", model, "cli",
                                   CLI_UNCOUNTED_NOTE))
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    argv = [resolve_claude_cli() or claude_cli(), "-p"]
    if model:
        argv += ["--model", model]
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else str(REPO_ROOT), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            errors="replace")
    except OSError as exc:
        raise LLMError(f"cloud CLI spawn failed: {exc}") from exc
    try:
        out, _ = proc.communicate(input=prompt, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=15)
        except (subprocess.TimeoutExpired, ValueError, OSError):
            pass
        raise LLMError(f"cloud CLI timed out after {timeout_s}s") from None
    if proc.returncode != 0:
        raise LLMError(
            f"cloud CLI exit {proc.returncode}: {(out or '')[-400:]}")
    text = (out or "").strip()
    if not text:
        raise LLMError("cloud CLI returned empty output")
    return text


def ollama_context_length(num_ctx: int | None = None) -> int:
    """Pin a positive local context budget: role > environment > default.

    This is a budget, not a character/token estimate or proof that input fits.
    The server's tokenizer must reject overflow rather than truncate it.
    """
    if num_ctx is None:
        raw = os.environ.get("CREW_OLLAMA_NUM_CTX", "").strip()
        if not raw:
            return 32768
        if not raw.isascii() or not raw.isdecimal():
            raise LLMNonRetryableError("num_ctx: CREW_OLLAMA_NUM_CTX must be a positive integer")
        try:
            num_ctx = int(raw)
        except ValueError as exc:
            raise LLMNonRetryableError("num_ctx: CREW_OLLAMA_NUM_CTX is too large") from exc
    if type(num_ctx) is not int or num_ctx <= 0:
        raise LLMNonRetryableError("num_ctx must be a positive integer (not bool or float)")
    return num_ctx


def _require_ollama_context_controls(base_url: str, timeout_s: float,
                                     headers: dict) -> None:
    """Old servers ignore unknown flags: refuse before sending any source.

    Both context controls are implemented from upstream Ollama 0.12.6.
    Read the current endpoint, not a cached claim surviving a server restart.
    Deployment acceptance still checks the actual server's overflow behavior.
    """
    request = urllib.request.Request(base_url + "/api/version", headers=headers)
    with urllib.request.urlopen(request, timeout=min(timeout_s, 10.0)) as response:
        payload = json.loads(response.read().decode("utf-8"))
    version = payload.get("version") if isinstance(payload, dict) else None
    match = re.fullmatch(r"(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})", version) \
        if isinstance(version, str) else None
    if not match or tuple(map(int, match.groups())) < (0, 12, 6):
        raise LLMNonRetryableError(
            f"local Ollama context controls unverified: server version {version!r}; "
            "require a stable Ollama release >= 0.12.6 for truncate:false/shift:false")


def _ollama_generate(prompt: str, timeout_s: float,
                     system: str | None = None,
                     model: str | None = None,
                     temperature: float | None = None,
                     base_url: str | None = None,
                     think: bool | None = None,
                     num_ctx: int | None = None) -> str:
    """One-shot local Ollama completion via /api/generate (stream=false).
    Uses stdlib urllib so the module has no third-party dependency.

    Расход: `prompt_eval_count` / `eval_count` ответа — в поток (usage
    локальной модели считается так же, как облачной: токены есть токены)."""
    _clear_last_usage()
    body: dict = {
        "model": model or ollama_model(),
        "prompt": prompt,
        "stream": False,
        # Keep the whole task/source: overflow is a server error, not a
        # successful answer to a silently shortened input or shifted context.
        "truncate": False,
        "shift": False,
        # МП-4: слотовая температура роли доезжает и до локальной
        # модели — обещание params_supported было пустым.
        "options": {"temperature": 0.2 if temperature is None
                    else temperature, "num_ctx": ollama_context_length(num_ctx)},
    }
    if system:
        body["system"] = system
    # Думающие Ollama-модели без явного think:false кладут ответ в
    # поле thinking, response ПУСТ (урок линии BA 2026-08-31, два
    # потерянных прогона). Умолчание трубы — false; включение —
    # слотом роли.
    body["think"] = bool(think) if think is not None else False
    data = json.dumps(body).encode("utf-8")
    endpoint = base_url.rstrip("/") if base_url else ollama_url().rstrip("/")
    headers = _ollama_headers()
    req = urllib.request.Request(endpoint + "/api/generate", data=data,
                                 headers=headers, method="POST")
    try:
        _require_ollama_context_controls(endpoint, timeout_s, headers)
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(200).decode("utf-8", "replace")
        except Exception:                               # noqa: BLE001
            pass
        if exc.code in (401, 403):
            raise LLMError(
                f"local Ollama at {ollama_url()} rejected the request "
                f"({exc.code}): {detail or 'API key required'} — set "
                f"CREW_OLLAMA_KEY") from exc
        error_type = LLMNonRetryableError if exc.code == 400 else LLMError
        raise error_type(f"local Ollama HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"local Ollama unreachable at {ollama_url()}: "
                       f"{exc}") from exc
    except json.JSONDecodeError as exc:
        raise LLMError(f"local Ollama returned non-JSON: {exc}") from exc
    set_last_usage(usage_ollama(payload, model=body["model"],
                                channel="local"))
    if payload.get("done_reason") == "length" or payload.get("done") is not True:
        raise LLMNonRetryableError(
            "local Ollama response incomplete: context/output limit reached "
            "or generation not finished; no partial answer accepted")
    text = str(payload.get("response") or "").strip()
    if not text:
        raise LLMError("local Ollama returned empty response")
    return text


# Extended-thinking budgets per effort level (МП-3). The Messages API
# requires max_tokens to EXCEED the thinking budget, and models cap the
# total output (32k on the Opus class) — so the total is clamped and
# the budget shrinks to stay below it (МП-4: effort=max on a 32k model
# produced a 400 about max_tokens that the thinking-degradation retry
# did not match).
_THINKING_BUDGETS = {"low": 2048, "medium": 8192, "high": 16384,
                     "max": 24576}
_API_BASE_MAX_TOKENS = 16000
_API_TOTAL_CAP = 32000


def _api_generate(prompt: str, *, model: str, timeout_s: float,
                  system: str | None = None,
                  effort: str | None = None,
                  temperature: float | None = None,
                  max_tokens: int | None = None) -> str:
    """One-shot completion via the Anthropic Messages API with the
    super-admin's stored API key (`x-api-key`). Uses httpx (already a project
    dependency) rather than the anthropic SDK so the offline internal contours
    need no extra install; the api backend only fires where cloud is already
    permitted by the data contour. `effort` maps to an extended-thinking
    budget; a model that rejects thinking (or its enlarged max_tokens)
    gets one retry without it. With thinking enabled the API forbids
    temperature — it is not sent then.

    Расход: `usage` ответа (вход, выход, запись и чтение кэша) — в поток;
    при повторе считается ответ, который в итоге принят (у отвергнутой
    попытки тела ответа нет)."""
    _clear_last_usage()
    key = _settings.get_api_key()
    if not key:
        raise LLMError("no Claude API key configured (set it in Установки)")
    try:
        import httpx
    except ImportError as exc:                          # pragma: no cover
        raise LLMError("httpx unavailable for the Claude API backend") from exc
    headers = {
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    base_max = max_tokens or _API_BASE_MAX_TOKENS
    body: dict = {
        "model": model,
        "max_tokens": base_max,
        "messages": [{"role": "user", "content": prompt}],
    }
    budget = _THINKING_BUDGETS.get(effort or "")
    if budget:
        total = min(base_max + budget, _API_TOTAL_CAP)
        budget = min(budget, total - 4096)   # budget must stay below
        body["thinking"] = {"type": "enabled", "budget_tokens": budget}
        body["max_tokens"] = total
    elif temperature is not None:
        body["temperature"] = temperature
    if system:
        body["system"] = system
    try:
        resp = httpx.post(ANTHROPIC_API_URL, headers=headers, json=body,
                          timeout=timeout_s)
        if resp.status_code == 400 and budget and \
                "thinking.type.enabled" in resp.text:
            # Пятое поколение (живой урок пилота 2026-08-31):
            # thinking.type=enabled не поддерживается — API просит
            # adaptive + output_config.effort. Прежний ретрай ВЫРЕЗАЛ
            # thinking вовсе, а у этих моделей размышления включены по
            # умолчанию и без бюджета съедали весь max_tokens — ответ
            # приходил пустым (stop_reason=max_tokens, один
            # thinking-блок). Ретраим ЯВНЫМ adaptive-режимом.
            retry = {k: v for k, v in body.items() if k != "thinking"}
            retry["thinking"] = {"type": "adaptive"}
            retry["output_config"] = {
                "effort": effort if effort in ("low", "medium", "high")
                else "high"}
            resp = httpx.post(ANTHROPIC_API_URL, headers=headers,
                              json=retry, timeout=timeout_s)
        elif resp.status_code == 400 and budget and (
                "thinking" in resp.text.lower()
                or "max_tokens" in resp.text.lower()):
            retry = {k: v for k, v in body.items() if k != "thinking"}
            retry["max_tokens"] = base_max
            if temperature is not None:
                retry["temperature"] = temperature
            resp = httpx.post(ANTHROPIC_API_URL, headers=headers,
                              json=retry, timeout=timeout_s)
    except httpx.HTTPError as exc:
        raise LLMError(f"Claude API request failed: {exc}") from exc
    if resp.status_code != 200:
        raise LLMError(f"Claude API HTTP {resp.status_code}: "
                       f"{resp.text[:400]}")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise LLMError(f"Claude API returned non-JSON: {exc}") from exc
    set_last_usage(usage_anthropic(payload, model=model, channel="api"))
    if payload.get("stop_reason") == "refusal":
        raise LLMError("Claude API declined the request (refusal)")
    parts = [b.get("text", "") for b in payload.get("content", [])
             if isinstance(b, dict) and b.get("type") == "text"]
    text = "".join(parts).strip()
    if not text:
        raise LLMError("Claude API returned empty text")
    return text


def list_models(api_key: str) -> list[dict]:
    """Live Claude model catalog from GET /v1/models with the given key,
    filtered to `claude-*` ids (all handle coding + text), newest first.
    Raises LLMError on failure."""
    key = (api_key or "").strip()
    if not key:
        raise LLMError("no Claude API key provided")
    try:
        import httpx
    except ImportError as exc:                          # pragma: no cover
        raise LLMError("httpx unavailable for the Claude API backend") from exc
    headers = {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    try:
        resp = httpx.get(ANTHROPIC_MODELS_URL, headers=headers,
                         params={"limit": 100}, timeout=30.0)
    except httpx.HTTPError as exc:
        raise LLMError(f"models request failed: {exc}") from exc
    if resp.status_code != 200:
        raise LLMError(f"models HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        data = resp.json().get("data", [])
    except ValueError as exc:
        raise LLMError(f"models returned non-JSON: {exc}") from exc
    out = []
    for m in data:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "")
        if not mid.startswith("claude-"):
            continue
        out.append({"id": mid,
                    "display_name": m.get("display_name") or mid,
                    "created_at": str(m.get("created_at") or "")})
    out.sort(key=lambda x: x["created_at"], reverse=True)
    return out


# --------------------------------------------------------------- interface

def complete(prompt: str, *, backend: str, timeout_s: float = 120.0,
             system: str | None = None, cwd: str | None = None,
             usage: str | None = None, model: str | None = None,
             effort: str | None = None,
             temperature: float | None = None,
             max_tokens: int | None = None,
             base_url: str | None = None,
             think: bool | None = None,
             transport: str | None = None,
             num_ctx: int | None = None) -> dict:
    """Run a completion on the given concrete backend ("local"|"cloud").

    `transport` (S06-R1, единая версия 06.09.2026): транспорт облачного
    канала, закреплённый вызывающей стороной при построении канала —
    "api" (Messages API) либо "cli" (командная строка Claude по подписке).
    С закреплённым транспортом исполнитель НЕ перевыбирает его по
    текущему наличию ключа: "api" без ключа/модели в момент вызова —
    честный отказ конвертом (ok=False), не уход в CLI; "cli" — CLI даже
    при появившемся ключе. None — прежнее поведение (ключ и модель есть →
    API, иначе CLI).

    `usage` (e.g. "ask_ai") lets the super admin's Settings pick a specific
    Claude model for this call — but ONLY on the cloud backend (local calls
    always use the global Ollama model). An explicit `model` overrides it.
    On the cloud backend, a chosen model + a configured API key routes through
    the Anthropic Messages API; otherwise the agentic CLI is used (with the
    chosen model as --model, if any). Returns {ok, text, backend, model,
    error}. Never raises for an expected backend failure — the caller inspects
    `ok` and falls back.

    В конверт добавлено поле `usage` — расход этого вызова в токенах по
    словарю USAGE_KEYS (решение владельца № 1 от 10.09.2026); канал CLI
    токенов не отдаёт, и там стоит `counted: false` с причиной словами.
    Копия — оригинал остаётся в потоке для журнала (`take_last_usage`)."""
    if backend not in BACKENDS:
        raise ValueError(f"unknown backend {backend!r}")
    _clear_last_usage()
    chosen = model or _settings.model_for(usage, backend)
    if backend == "local":
        label = chosen or ollama_model()
        try:
            kw: dict = {"system": system}
            if chosen:
                kw["model"] = chosen
            if temperature is not None:
                kw["temperature"] = temperature
            if base_url:
                # Роль-на-адрес (принцип трёх моделей, 2026-08-31):
                # слот роли ведёт на СВОЮ Ollama (другой GPU/машина).
                kw["base_url"] = base_url
            if think is not None:
                kw["think"] = think
            if num_ctx is not None:
                kw["num_ctx"] = num_ctx
            # Пустой ответ ловит сама труба Ollama (LLMError «empty
            # response») — с think=false умолчанием думающая модель
            # больше не прячет текст в поле thinking.
            text = _ollama_generate(prompt, timeout_s, **kw)
        except LLMError as exc:
            return {"ok": False, "text": "", "backend": backend,
                    "model": label, "error": str(exc),
                    "usage": peek_last_usage(),
                    **({"retryable": False} if isinstance(exc, LLMNonRetryableError) else {})}
        return {"ok": True, "text": text, "backend": backend, "model": label,
                "error": None, "usage": peek_last_usage()}
    # cloud: prefer the Messages API when a model is chosen AND a key exists;
    # закреплённый транспорт (S06-R1) сильнее текущего наличия ключа.
    if transport not in (None, "api", "cli"):
        return {"ok": False, "text": "", "backend": backend,
                "model": chosen or "claude-cli",
                "error": f"неизвестный закреплённый транспорт {transport!r}",
                "usage": None}
    use_api = (chosen and _settings.has_api_key()) if transport is None \
        else transport == "api"
    if use_api and not (chosen and _settings.has_api_key()):
        return {"ok": False, "text": "", "backend": backend,
                "model": chosen or "claude-cli",
                "error": "закреплённый транспорт api: ключ API отсутствует "
                         "в момент вызова либо модель не названа — уход в "
                         "командную строку запрещён (S06: повтор того же "
                         "канала, не подмена)",
                "usage": None}
    if use_api:
        try:
            text = _api_generate(prompt, model=chosen, timeout_s=timeout_s,
                                 system=system, effort=effort,
                                 temperature=temperature,
                                 max_tokens=max_tokens)
        except LLMError as exc:
            return {"ok": False, "text": "", "backend": backend,
                    "model": chosen, "error": str(exc),
                    "usage": peek_last_usage()}
        return {"ok": True, "text": text, "backend": backend, "model": chosen,
                "error": None, "usage": peek_last_usage()}
    # else the agentic CLI (subscription OAuth), optionally pinned to a model
    label = chosen or "claude-cli"
    full = (system + "\n\n" + prompt) if system else prompt
    try:
        kw = {"cwd": cwd}
        if chosen:
            kw["model"] = chosen
        text = _cli_generate(full, timeout_s, **kw)
    except LLMError as exc:
        return {"ok": False, "text": "", "backend": backend, "model": label,
                "error": str(exc), "usage": peek_last_usage()}
    return {"ok": True, "text": text, "backend": backend, "model": label,
            "error": None, "usage": peek_last_usage()}


def _local_model_loaded() -> bool:
    """Best-effort: is the configured Ollama model currently resident (warm)?
    Queries /api/ps; returns False when the server is unreachable or the model
    is cold, so the UI can warn that the first request pays the load time (a
    big model can take tens of seconds to load into VRAM)."""
    want = ollama_model_or_none()
    if not want:
        return False
    try:
        req = urllib.request.Request(ollama_url() + "/api/ps",
                                     headers=_ollama_headers(), method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:                                   # noqa: BLE001
        return False
    # EXACT tag match (M16): "qwen3.6:8b" must NOT count as warm for
    # "qwen3.6:35b-a3b" — a different size is a different resident model.
    # If the configured name is UNTAGGED, Ollama resolves it to ":latest" for
    # generation, so /api/ps reports "<want>:latest"; accept exactly that (and
    # only that) — never a different explicit tag.
    want_latest = None if ":" in want else (want + ":latest")
    for m in data.get("models", []):
        name = str(m.get("name") or m.get("model") or "")
        if name == want or (want_latest is not None and name == want_latest):
            return True
    return False


def available() -> dict:
    """Best-effort reachability probe for the UI: {local: bool, cloud: bool}.
    Cloud is 'configured' when a CLI name resolves or an API key is stored
    (cheap check only — we do not spend a token probing). Local pings the
    Ollama /api/version, and (when up) reports whether the model is loaded so
    the UI can warn about first-request load time."""
    local_ok = False
    try:
        req = urllib.request.Request(ollama_url() + "/api/version",
                                     headers=_ollama_headers(), method="GET")
        with urllib.request.urlopen(req, timeout=4) as resp:
            local_ok = resp.status == 200
    except Exception:                                   # noqa: BLE001
        local_ok = False
    api_ok = _settings.has_api_key()
    # cloud is 'configured' only when a real credential/binary exists (M17):
    # bool(claude_cli()) was ALWAYS true (default name "claude"); resolve it
    # on PATH instead, and count the OAuth token or a stored API key too.
    cloud_ok = (bool(_env("CLAUDE_CODE_OAUTH_TOKEN")) or api_ok
                or bool(resolve_claude_cli()))
    return {"local": local_ok, "cloud": cloud_ok, "api_key": api_ok,
            "local_model": ollama_model_or_none(), "default": default_backend(),
            "local_model_loaded": (local_ok and _local_model_loaded())}
