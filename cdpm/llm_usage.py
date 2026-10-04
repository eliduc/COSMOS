# -*- coding: utf-8 -*-
"""Учёт токенов вызовов моделей — боковой канал адаптеров (шаг 1 проекта
«Запись о прогоне», решение владельца 10.09.2026: «нужно считать в
токенах … по каждой из задействованных моделей»; цена на момент прогона
и на момент просмотра разная, поэтому в запись идут ТОЛЬКО токены).

Почему боковой канал, а не смена контракта `fn(prompt) -> str`: все
каналы CDPM (облако, командная строка, Ollama, внешние провайдеры)
отдают текст, и десятки вызывающих строятся на этом; менять сигнатуру
ради одного числа — риск сквозной правки. Адаптер, получив ответ
провайдера, ОБЪЯВЛЯЕТ расход `report(...)`; обёртка журнала вызовов
(`cdpm.call_journal._call_recorded`) сразу после возврата ЗАБИРАЕТ его
`take()` и кладёт в запись вызова полем `usage`. Регистр —
thread-local: параллельные вызовы (`cdpm.llm.parallel_map`) идут в
своих потоках и не смешивают расход.

Модуль намеренно без импортов пакета: его читают и `cdpm.llm`, и
`cdpm.external_llm`, и журнал вызовов — цикла импортов быть не должно.

Канал, который расход не сообщает (командная строка Claude по подписке),
объявляет `counted=False` с причиной — в записи это честное «не
считается», а не ноль.
"""
from __future__ import annotations

import threading

_LOCAL = threading.local()

#: Имена полей записи расхода (словарь записи о прогоне, раздел 3.2).
FIELDS = ("provider", "model", "input_tokens", "output_tokens",
          "cache_read_tokens", "cache_write_tokens", "thinking_tokens",
          "counted", "reason")


def _int(v) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def normalize(raw) -> dict | None:
    """Запись расхода из произвольного словаря адаптера: только поля
    FIELDS, числа — целые неотрицательные, иначе None; `counted`
    выводится из наличия чисел, если адаптер его не назвал. Не словарь
    или пусто — None («канал не сообщил»)."""
    if not isinstance(raw, dict) or not raw:
        return None
    out: dict = {
        "provider": (str(raw.get("provider")) if raw.get("provider") else None),
        "model": (str(raw.get("model")) if raw.get("model") else None),
        "input_tokens": _int(raw.get("input_tokens")),
        "output_tokens": _int(raw.get("output_tokens")),
        "cache_read_tokens": _int(raw.get("cache_read_tokens")),
        "cache_write_tokens": _int(raw.get("cache_write_tokens")),
        "thinking_tokens": _int(raw.get("thinking_tokens")),
    }
    counted = raw.get("counted")
    if counted is None:
        counted = out["input_tokens"] is not None or out["output_tokens"] is not None
    out["counted"] = bool(counted)
    reason = raw.get("reason")
    out["reason"] = str(reason) if reason else (None if out["counted"]
                                                else "канал расход не сообщил")
    return out


def report(provider: str | None, model: str | None, *,
           input_tokens=None, output_tokens=None, cache_read_tokens=None,
           cache_write_tokens=None, thinking_tokens=None,
           counted: bool | None = None, reason: str | None = None) -> dict:
    """Адаптер объявляет расход ПОСЛЕДНЕГО ответа в этом потоке."""
    rec = normalize({
        "provider": provider, "model": model,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_write_tokens": cache_write_tokens,
        "thinking_tokens": thinking_tokens,
        "counted": counted, "reason": reason,
    }) or {"provider": provider, "model": model, "input_tokens": None,
           "output_tokens": None, "cache_read_tokens": None,
           "cache_write_tokens": None, "thinking_tokens": None,
           "counted": False, "reason": reason or "канал расход не сообщил"}
    _LOCAL.last = rec
    return rec


def report_raw(raw) -> dict | None:
    """Расход из конверта канала как есть (например, поле `usage`
    конверта `webapp.llm_backend.complete`); пусто — регистр очищается."""
    rec = normalize(raw)
    _LOCAL.last = rec
    return rec


def take() -> dict | None:
    """Забрать и очистить расход последнего ответа этого потока."""
    rec = getattr(_LOCAL, "last", None)
    _LOCAL.last = None
    return rec


def clear() -> None:
    _LOCAL.last = None


def not_counted(provider: str | None, model: str | None,
                reason: str) -> dict:
    """Честное «не считается» (командная строка по подписке и т. п.)."""
    return report(provider, model, counted=False, reason=reason)
