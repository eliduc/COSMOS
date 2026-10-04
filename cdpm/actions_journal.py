# -*- coding: utf-8 -*-
"""Журнал действий над прогоном CDPM (проект повтора, этап Р1; раздел 2.3
`final-report/CDPM-POVTOR-PROEKT-CLD.md`).

`<run>/journal/actions.jsonl` — только дописывание: строка `start` при
принятии действия (имя, параметры, файлы по sha256, состояние до), строка
`end` по завершении фоновой стадии (состояние после, длительность, диапазон
номеров вызовов журнала внешних вызовов), строка `refused` — если ручка
отказала (действие содержания не изменило), строка `sync` — действие без
фона (завершено в запросе). Текущее действие процесса — контекстная
переменная: ручка ставит её, фоновая нить наследует явно
(`webui._start_stage_thread`), обёртки каналов снимают номер при построении.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path

#: Происхождение действия «smt-verify», начатого РУБЕЖОМ выпуска пакета
#: (`webui._smt_gate_scope`), а не кнопкой панели: слово одно у писателя
#: и у читателей (повтор прогона, глаза человека в журнале).
SMT_GATE_ORIGIN = "гейт выпуска пакета"

_current: contextvars.ContextVar = contextvars.ContextVar("cdpm_action_n",
                                                           default=None)
_GLOBAL = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}
_COUNTS: dict[str, int] = {}


def path_of(run_dir) -> Path:
    return Path(run_dir) / "journal" / "actions.jsonl"


def _lock(run_dir) -> threading.Lock:
    k = str(Path(run_dir).resolve())
    with _GLOBAL:
        lk = _LOCKS.get(k)
        if lk is None:
            lk = _LOCKS[k] = threading.Lock()
        return lk


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read(run_dir) -> list[dict]:
    p = path_of(run_dir)
    if not p.is_file():
        return []
    out: list[dict] = []
    for line in p.read_bytes().decode("utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _append(run_dir, obj: dict) -> None:
    p = path_of(run_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "ab") as f:
        f.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))


def _next_n(run_dir) -> int:
    k = str(Path(run_dir).resolve())
    n = _COUNTS.get(k)
    if n is None:
        n = max([int(r.get("n") or 0) for r in read(run_dir)], default=0)
    n += 1
    _COUNTS[k] = n
    return n


def file_entry(name: str, data: bytes, stored: str | None = None) -> dict:
    """Запись файла действия: имя, sha256 и размер байтов, где сохранён."""
    return {"name": name, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "stored": stored}


def note(run_dir, action: str, params: dict | None = None,
         files: list[dict] | None = None, sync: bool = False,
         state_before: dict | None = None) -> int:
    """Принятое действие: строка `start` (фоновая стадия) или `sync`;
    номер становится текущим действием контекста."""
    with _lock(run_dir):
        n = _next_n(run_dir)
        _append(run_dir, {"n": n, "kind": "sync" if sync else "start",
                          "action": action, "params": params or {},
                          "files": files or [], "at": _now(),
                          "state_before": state_before})
    _current.set(n)
    return n


def end(run_dir, n: int, state_after: dict | None = None,
        duration_s: float | None = None, calls: list | None = None,
        error: str | None = None) -> None:
    with _lock(run_dir):
        _append(run_dir, {"n": n, "kind": "end", "at": _now(),
                          "state_after": state_after,
                          "duration_s": duration_s, "calls": calls,
                          "error": error})


def refused(run_dir, n: int, status: int, detail: str,
            code: str | None = None, items=None) -> None:
    """Ручка отказала: действие содержания не изменило (повтор его
    пропускает). `code` и `items` (контракт П0 §1.4, 12.09.2026) —
    машинный код отказа и перечень идентификаторов, к которым он
    относится; у прежних отказов пусты."""
    with _lock(run_dir):
        _append(run_dir, {"n": n, "kind": "refused", "at": _now(),
                          "status": int(status), "detail": str(detail)[:500],
                          "code": code,
                          "items": [str(x) for x in (items or [])][:50]})


def action_of(run_dir, n) -> str | None:
    """Имя действия по его номеру (`journal/actions.jsonl`, строка
    `start`/`sync`) — вопрос «чьё это действие» журнал действий
    отвечает сам. Номера нет либо журнала нет — None."""
    if n is None:
        return None
    for r in read(run_dir):
        if r.get("kind") in ("start", "sync") and r.get("n") == n:
            name = str(r.get("action") or "")
            return name or None
    return None


def current() -> int | None:
    return _current.get()


def set_current(n: int | None):
    return _current.set(n)


def reset_current(token) -> None:
    try:
        _current.reset(token)
    except (ValueError, LookupError):
        _current.set(None)


def actions(run_dir) -> list[dict]:
    """Действия с их завершением: `start`/`sync` слиты с `end`/`refused`."""
    by_n: dict[int, dict] = {}
    order: list[int] = []
    for r in read(run_dir):
        n = int(r.get("n") or 0)
        kind = r.get("kind")
        if kind in ("start", "sync"):
            rec = dict(r)
            rec["done"] = kind == "sync"
            rec["refused"] = False
            by_n[n] = rec
            order.append(n)
        elif kind == "end" and n in by_n:
            by_n[n].update({"done": True, "state_after": r.get("state_after"),
                            "duration_s": r.get("duration_s"),
                            "calls": r.get("calls"), "error": r.get("error"),
                            "ended_at": r.get("at")})
        elif kind == "refused" and n in by_n:
            by_n[n].update({"done": True, "refused": True,
                            "status": r.get("status"),
                            "detail": r.get("detail")})
    return [by_n[n] for n in order]


def count(run_dir) -> int:
    return sum(1 for r in read(run_dir) if r.get("kind") in ("start", "sync"))


def forget(run_dir=None) -> None:
    with _GLOBAL:
        if run_dir is None:
            _COUNTS.clear()
        else:
            _COUNTS.pop(str(Path(run_dir).resolve()), None)
