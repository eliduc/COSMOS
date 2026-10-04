# -*- coding: utf-8 -*-
"""Журнал внешних вызовов прогона CDPM (проект повтора, этап Р1;
`final-report/CDPM-POVTOR-PROEKT-CLD.md`, разделы 2.1, 2.2, 2.4, 2.6).

Единственная внешняя сторона CDPM — модели: текстовые каналы всех ролей
(`fn(prompt) -> str`) и зрительный канал заморозки (`fn(prompt, path) ->
str`). Каждый вызов записывается в `<run>/journal/calls/`: индекс
`index.jsonl` (одна строка на вызов, в порядке фактического завершения),
тела запросов и ответов байтами по содержимому в `bodies/<sha256>`
(больше 64 КБ — gzip), манифест с режимом, счётчиками и версиями.

Отпечаток запроса — sha256 канонического JSON `{role, klass, prompt_sha256,
images[]}`; личность канала (провайдер, модель, усилие) в отпечаток НЕ
входит: смена раскладки между записью и повтором — различие окружения, а не
«вызов вне журнала». Одинаковые запросы различаются `occurrence`; повтор из
журнала отдаёт ответы по очереди на отпечаток. Запись — под замком прогона;
параллельные нити извлечения адресуются содержимым, не порядком.

Режимы (`set_mode`): `record` — запись; `replay-journal` — ответы из журнала
источника, модель не вызывается, отпечатка нет → `ReplayMismatch` словами с
разницей текстов запроса; `replay-fresh` — вызовы заново, запись.
Выключатель площадки `CDPM_CALL_JOURNAL=0` отключает тела (индекс остаётся,
манифест `degraded`) — повтор из журнала тогда недоступен.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from cdpm import file_replace as _fr

from cdpm import file_replace as _fr

from cdpm import file_replace as _fr

SCHEMA = "cdpm-call-journal/v1"
ENV_BODIES = "CDPM_CALL_JOURNAL"
GZIP_FROM = 64 * 1024
MODES = ("record", "replay-journal", "replay-fresh")
_IDENTITY_KEYS = ("provider", "model", "channel", "effort", "temperature",
                  "config_source", "klass", "role")


class ReplayMismatch(RuntimeError):
    """Повтор из журнала: запроса с таким отпечатком в журнале источника
    нет (или очередь на отпечаток исчерпана) — код панели изменил запрос
    к модели. Слова называют стадию, роль и первую строку расхождения с
    ближайшим по тексту запросом источника."""

    def __init__(self, words: str, detail: dict | None = None):
        super().__init__(words)
        self.detail = detail or {}


# ------------------------------------------------------------ состояние

_GLOBAL = threading.Lock()
_LOCKS: dict[str, threading.Lock] = {}
_STATE: dict[str, dict] = {}


def _key(run_dir) -> str:
    return str(Path(run_dir).resolve())


def _lock(run_dir) -> threading.Lock:
    k = _key(run_dir)
    with _GLOBAL:
        lk = _LOCKS.get(k)
        if lk is None:
            lk = _LOCKS[k] = threading.Lock()
        return lk


def _state(run_dir) -> dict:
    k = _key(run_dir)
    with _GLOBAL:
        st = _STATE.get(k)
        if st is None:
            st = _STATE[k] = {"mode": "record", "source": None,
                              "occurrences": None, "n": None,
                              "source_index": None, "served": 0}
        return st


def forget(run_dir=None) -> None:
    """Сброс состояния процесса (тесты, смена режима)."""
    with _GLOBAL:
        if run_dir is None:
            _STATE.clear()
        else:
            _STATE.pop(_key(run_dir), None)


def set_mode(run_dir, mode: str, source_run_dir=None) -> None:
    """Режим журнала прогона на этот процесс: водитель повтора (Р2) и
    тесты. `replay-journal` требует каталог источника с журналом."""
    if mode not in MODES:
        raise ValueError(f"режим журнала вне словаря {MODES}: {mode!r}")
    if mode == "replay-journal":
        if source_run_dir is None or not index_path(source_run_dir).is_file():
            raise ValueError("повтор из журнала: у источника нет журнала "
                             "вызовов (journal/calls/index.jsonl)")
    st = _state(run_dir)
    source = str(Path(source_run_dir).resolve()) \
        if source_run_dir is not None else None
    with _lock(run_dir):
        if st["mode"] == mode and st["source"] == source:
            # Тот же режим и источник — очередь записей не сбрасывается:
            # рождение потомка уже могло взять из журнала вызов зрения
            # (живой урок run-0088 → run-0090: повторный set_mode водителя
            # «возвращал» отданную запись — «неизрасходовано 1»).
            return
        st["mode"] = mode
        st["source"] = source
        st["source_index"] = None
        st["served"] = 0


def mode_of(run_dir) -> str:
    return _state(run_dir)["mode"]


# ------------------------------------------------------------ каталог

def journal_dir(run_dir) -> Path:
    return Path(run_dir) / "journal" / "calls"


def index_path(run_dir) -> Path:
    return journal_dir(run_dir) / "index.jsonl"


def manifest_path(run_dir) -> Path:
    return journal_dir(run_dir) / "manifest.json"


def bodies_enabled() -> bool:
    return (os.environ.get(ENV_BODIES) or "").strip() != "0"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _AlreadyThere(Exception):
    """Тело по содержимому уже лежит — замена не нужна."""


def _atomic_write(path: Path, data: bytes,
                  content_addressed: bool = False) -> None:
    """Запись через временный файл и замену. Имя временного файла — своё у
    нити и процесса (параллельные нити извлечения пишут одно и то же тело
    одновременно). Замена — `cdpm.file_replace`: на Windows семантика
    POSIX, и читатель манифеста (`manifest()`, FILE_SHARE_DELETE) писателю
    не мешает; чужого читателя (антивирус, индексатор) писатель ждёт окно
    по времени, а не 40 попыток (красный CI среза eafe537 на двух ядрах
    windows-latest, 07.09.2026: горячие читатели не оставляли писателю ни
    мгновения). Только у тела по содержимому «уже лежит» = готово (живой
    урок run-0088 → run-0091: прежний запас молча терял обновление
    счётчиков манифеста). Не удалось за окно — PermissionError словами,
    временный файл убран."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    tmp.write_bytes(data)

    def busy(_attempt: int) -> None:
        if content_addressed and path.is_file():
            raise _AlreadyThere

    try:
        _fr.replace_with_retry(tmp, path, window_s=_fr.REPLACE_WINDOW_S,
                               on_busy=busy)
    except _AlreadyThere:
        tmp.unlink(missing_ok=True)
    except PermissionError:
        tmp.unlink(missing_ok=True)
        raise


def _environment() -> dict:
    try:
        from cdpm import run_passport as rp
        env = rp.environment()
        return {"code": (env.get("code") or {}).get("commit"),
                "spec_version": env.get("spec_version"),
                "python": env.get("python"),
                "packages": dict(env.get("packages") or {}),
                "catalog": env.get("catalog")}
    except Exception:                                     # noqa: BLE001
        return {}


def open_journal(run_dir, mode: str | None = None,
                 source_run: str | None = None) -> dict:
    """Каталог и манифест журнала (идемпотентно): существующий манифест
    не переписывается, кроме режима/источника, если они переданы."""
    jd = journal_dir(run_dir)
    (jd / "bodies").mkdir(parents=True, exist_ok=True)
    mp = manifest_path(run_dir)
    st = _state(run_dir)
    if mp.is_file():
        try:
            man = json.loads(mp.read_bytes().decode("utf-8"))
        except (OSError, ValueError):
            man = {}
    else:
        man = {}
    if not man:
        import platform as _platform
        man = {"schema": SCHEMA, "run": Path(run_dir).name,
               "mode": mode or st["mode"], "source_run": source_run,
               "opened_at": _now(), "closed_at": None, "complete": True,
               "degraded": None,
               "counts": {"llm": 0, "vision": 0, "errors": 0,
                          "served_from_journal": 0, "served_from_cache": 0,
                          "unconsumed": 0},
               "versions": {"roles": {},
                            "panel": {**_environment(),
                                      "platform": _platform.platform(),
                                      "data_root": str(Path(run_dir).parent)}}}
        if not bodies_enabled():
            man["complete"] = False
            man["degraded"] = "тела запросов и ответов отключены площадкой " \
                              f"({ENV_BODIES}=0)"
    if mode:
        man["mode"] = mode
    if source_run is not None:
        man["source_run"] = source_run
    _atomic_write(mp, _canon_pretty(man))
    return man


def _canon_pretty(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def manifest(run_dir) -> dict | None:
    """Манифест журнала либо None (журнала нет). Чтение — `open_shared`:
    писатель заменяет файл, не дожидаясь читателя. Миг замены на площадке
    без семантики POSIX (запасной os.replace) — чтение повторяется."""
    mp = manifest_path(run_dir)
    if not mp.is_file():
        return None
    for attempt in range(6):
        try:
            return json.loads(_fr.read_bytes_shared(mp).decode("utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            time.sleep(0.01 * (attempt + 1))
    return None


# ------------------------------------------------------------ тела

def write_body(run_dir, data: bytes) -> str:
    """Тело по содержимому: sha256; файл пишется один раз; больше 64 КБ —
    gzip. Без тел (площадка) — только sha."""
    sha = _sha(data)
    if not bodies_enabled():
        return sha
    bd = journal_dir(run_dir) / "bodies"
    bd.mkdir(parents=True, exist_ok=True)
    raw, gz = bd / sha, bd / (sha + ".gz")
    with _lock(run_dir):
        if raw.is_file() or gz.is_file():
            return sha
        if len(data) > GZIP_FROM:
            _atomic_write(gz, gzip.compress(data), content_addressed=True)
        else:
            _atomic_write(raw, data, content_addressed=True)
    return sha


def read_body(run_dir, sha: str) -> bytes | None:
    bd = journal_dir(run_dir) / "bodies"
    raw, gz = bd / sha, bd / (sha + ".gz")
    if raw.is_file():
        return raw.read_bytes()
    if gz.is_file():
        return gzip.decompress(gz.read_bytes())
    return None


# ------------------------------------------------------------ индекс

def read_index(run_dir) -> list[dict]:
    p = index_path(run_dir)
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


def count(run_dir) -> int:
    """Число записанных вызовов (без служебной строки `end`)."""
    return sum(1 for r in read_index(run_dir) if r.get("kind") != "end")


def _load_counters(run_dir, st: dict) -> None:
    if st["occurrences"] is not None:
        return
    occ: dict[str, int] = {}
    n = 0
    for r in read_index(run_dir):
        if r.get("kind") == "end":
            continue
        n = max(n, int(r.get("n") or 0))
        fp = (r.get("input") or {}).get("fingerprint")
        if fp:
            occ[fp] = occ.get(fp, 0) + 1
    st["occurrences"] = occ
    st["n"] = n


def fingerprint(role: str, klass: str | None, prompt: str,
                images: list[dict] | None = None) -> tuple[str, str, int]:
    """(отпечаток, sha256 запроса, длина запроса в байтах)."""
    pb = (prompt or "").encode("utf-8")
    psha = _sha(pb)
    fp = _sha(_canon({"role": role, "klass": klass, "prompt_sha256": psha,
                      "images": [i.get("sha256") for i in (images or [])]}))
    return fp, psha, len(pb)


def _identity_of(fn) -> dict:
    raw = getattr(fn, "identity", None)
    if not raw:
        return {}
    try:
        got = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {"channel": str(raw)}
    if not isinstance(got, dict):
        return {}
    eff = got.get("effective") if isinstance(got.get("effective"), dict) else {}
    out = {k: got.get(k) for k in _IDENTITY_KEYS if got.get(k) is not None}
    for k in ("provider", "model", "channel"):
        if eff.get(k) is not None:
            out[k] = eff.get(k)
    return out


def record(run_dir, entry: dict) -> int:
    """Запись вызова под замком: номер, occurrence, индекс, счётчики
    манифеста. Возвращает `n`."""
    st = _state(run_dir)
    with _lock(run_dir):
        open_journal(run_dir)
        _load_counters(run_dir, st)
        fp = (entry.get("input") or {}).get("fingerprint")
        if fp:
            st["occurrences"][fp] = st["occurrences"].get(fp, 0) + 1
            entry["occurrence"] = st["occurrences"][fp]
        st["n"] = int(st["n"] or 0) + 1
        entry["n"] = st["n"]
        line = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        with open(index_path(run_dir), "ab") as f:
            f.write(line)
        man = manifest(run_dir) or open_journal(run_dir)
        c = man.setdefault("counts", {})
        kind = entry.get("kind") or "llm"
        c[kind] = int(c.get(kind, 0)) + 1
        if entry.get("error"):
            c["errors"] = int(c.get("errors", 0)) + 1
        if entry.get("served_from") == "journal":
            c["served_from_journal"] = int(c.get("served_from_journal", 0)) + 1
        roles = man.setdefault("versions", {}).setdefault("roles", {})
        ident = entry.get("identity") or {}
        if entry.get("role"):
            roles[f"{entry['role']}/{entry.get('klass')}"] = {
                k: ident.get(k) for k in ("provider", "model", "channel",
                                          "effort", "temperature",
                                          "config_source")}
        _atomic_write(manifest_path(run_dir), _canon_pretty(man))
        return entry["n"]


def close(run_dir, complete: bool = True) -> dict | None:
    """Служебная строка `end` и закрытие манифеста (счёт неизрасходованных
    записей источника — у повтора из журнала)."""
    st = _state(run_dir)
    with _lock(run_dir):
        man = manifest(run_dir)
        if man is None:
            return None
        n = count(run_dir)
        unconsumed = 0
        if st["mode"] == "replay-journal" and st["source_index"] is not None:
            unconsumed = sum(len(q) for q in st["source_index"].values())
        man["counts"]["unconsumed"] = unconsumed
        man["closed_at"] = _now()
        man["complete"] = bool(complete) and man.get("degraded") is None
        with open(index_path(run_dir), "ab") as f:
            f.write((json.dumps({"kind": "end", "complete": man["complete"],
                                 "count": n}, ensure_ascii=False)
                     + "\n").encode("utf-8"))
        _atomic_write(manifest_path(run_dir), _canon_pretty(man))
        return man


# ------------------------------------------------------------ повтор

def _source_index(run_dir, st: dict) -> dict[str, deque]:
    if st["source_index"] is None:
        idx: dict[str, deque] = {}
        for r in read_index(st["source"]):
            if r.get("kind") == "end":
                continue
            fp = (r.get("input") or {}).get("fingerprint")
            if fp:
                idx.setdefault(fp, deque()).append(r)
        st["source_index"] = idx
    return st["source_index"]


def _nearest_prompt(source_dir, role: str, prompt: str) -> dict | None:
    """Ближайший по общему префиксу запрос той же роли у источника и
    первая строка расхождения — слова для ReplayMismatch."""
    best: tuple[int, dict, str] | None = None
    for r in read_index(source_dir):
        if r.get("kind") == "end" or r.get("role") != role:
            continue
        psha = (r.get("input") or {}).get("prompt_sha256")
        body = read_body(source_dir, psha) if psha else None
        if body is None:
            continue
        text = body.decode("utf-8", errors="replace")
        k = 0
        for a, b in zip(text, prompt):
            if a != b:
                break
            k += 1
        if best is None or k > best[0]:
            best = (k, r, text)
    if best is None:
        return None
    _k, r, text = best
    src_lines, new_lines = text.splitlines(), prompt.splitlines()
    line_no = 0
    src_line = new_line = ""
    for i in range(max(len(src_lines), len(new_lines))):
        s = src_lines[i] if i < len(src_lines) else ""
        t = new_lines[i] if i < len(new_lines) else ""
        if s != t:
            line_no, src_line, new_line = i + 1, s, t
            break
    return {"n": r.get("n"), "prompt_sha256": (r.get("input") or {}).get("prompt_sha256"),
            "line": line_no, "source_line": src_line[:300],
            "replay_line": new_line[:300]}


def _serve(run_dir, st: dict, fp: str, role: str, stage, prompt: str) -> dict:
    # Индекс источника строится и расходуется под замком прогона:
    # параллельные нити извлечения иначе строили бы его дважды и
    # «возвращали» уже отданные записи (живой урок стенда Р2).
    with _lock(run_dir):
        idx = _source_index(run_dir, st)
        q = idx.get(fp)
        rec = q.popleft() if q else None
        if rec is not None:
            st["served"] += 1
    if rec is None:
        near = _nearest_prompt(st["source"], role, prompt)
        words = (f"повтор из журнала: стадия {stage or '—'}, роль {role}: "
                 f"запроса с таким отпечатком в журнале источника нет")
        if near:
            words += (f"; ближайший запрос источника — вызов {near['n']}, "
                      f"первое расхождение в строке {near['line']}: "
                      f"источник «{near['source_line'][:120]}», повтор "
                      f"«{near['replay_line'][:120]}»")
        else:
            words += " (у источника нет запросов этой роли)"
        raise ReplayMismatch(words, {"fingerprint": fp, "role": role,
                                     "stage": stage, "nearest": near})
    return rec


# ------------------------------------------------------------ обёртки

def _stage_of(run_dir, action_n=None) -> str | None:
    """Стадия вызова СЛОВАМИ: занятая линия статуса («извлечение идёт»
    → «извлечение»), а при свободных линиях — слова текущего действия
    журнала действий.

    Синхронное действие линию статуса не занимает (рубеж
    непротиворечивости при выпуске пакета, заявки в каталог), и до
    11.09.2026 его вызов ложился в индекс без стадии — живая находка
    run-0166: запись прогона вызов считала (номер действия в строке
    есть), а индекс называл стадию null. Слова стадии берутся из
    словаря действий записи о прогоне — чтобы одна проверка читалась
    ОДНОЙ стадией, кем бы она ни была начата: кнопкой панели (линия
    статуса) или рубежом выпуска (действие журнала). Действия нет в
    словаре — стадией зовётся само имя действия."""
    try:
        st = json.loads((Path(run_dir) / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    for k in ("state", "data_state", "smt_state"):
        s = str(st.get(k) or "")
        if s.endswith("идёт") or s.endswith("идут"):
            for suffix in (" идёт", " идут"):
                if s.endswith(suffix):
                    return s[:-len(suffix)]
    from cdpm import actions_journal, run_record
    action = actions_journal.action_of(run_dir, action_n)
    return (run_record.action_stage_words(action) or action) if action else None


def _call_recorded(run_dir, fn, kind: str, role: str, klass, action_n,
                   stage, prompt: str, images: list[dict], call):
    """Общая часть обёрток: режим, отпечаток, запись, повтор."""
    st = _state(run_dir)
    fp, psha, pbytes = fingerprint(role, klass, prompt, images)
    entry = {"kind": kind, "role": role, "klass": klass,
             "action_n": action_n,
             "stage": stage or _stage_of(run_dir, action_n),
             "input": {"fingerprint": fp, "prompt_sha256": psha,
                       "prompt_bytes": pbytes, "images": images},
             "identity": _identity_of(fn), "reply_sha256": None,
             "reply_bytes": 0, "error": None, "served_from": None,
             "at": _now(), "elapsed_ms": 0}
    t0 = time.monotonic()
    if st["mode"] == "replay-journal":
        try:
            src = _serve(run_dir, st, fp, role, entry["stage"], prompt)
        except ReplayMismatch as e:
            write_body(run_dir, prompt.encode("utf-8"))
            entry["error"] = {"type": "ReplayMismatch", "words": str(e)}
            entry["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
            record(run_dir, entry)
            raise
        entry["served_from"] = "journal"
        entry["source_n"] = src.get("n")
        entry["identity_source"] = src.get("identity")
        write_body(run_dir, prompt.encode("utf-8"))
        if src.get("error"):
            from cdpm import llm as llm_mod
            entry["error"] = dict(src["error"])
            entry["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
            record(run_dir, entry)
            raise llm_mod.LLMCallError(
                f"(из журнала, вызов {src.get('n')}) "
                f"{src['error'].get('type')}: {src['error'].get('words')}")
        body = read_body(st["source"], src.get("reply_sha256") or "")
        if body is None:
            entry["error"] = {"type": "ReplayMismatch",
                              "words": "тело ответа отсутствует в журнале "
                                       f"источника (вызов {src.get('n')})"}
            record(run_dir, entry)
            raise ReplayMismatch(entry["error"]["words"])
        reply = body.decode("utf-8")
        entry["reply_sha256"] = src.get("reply_sha256")
        entry["reply_bytes"] = len(body)
        # Расход из журнала-источника: повтор модель не звал, токены —
        # те, что были потрачены исходным прогоном (честнее нуля).
        entry["usage"] = src.get("usage")
        write_body(run_dir, body)
        entry["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        record(run_dir, entry)
        return reply
    # record | replay-fresh — вызов как есть, запись факта.
    from cdpm import llm_usage
    write_body(run_dir, prompt.encode("utf-8"))
    llm_usage.clear()
    try:
        reply = call()
    except BaseException as e:                              # noqa: BLE001
        entry["error"] = {"type": type(e).__name__, "words": str(e)[:1000]}
        entry["usage"] = llm_usage.take()
        entry["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        record(run_dir, entry)
        raise
    text = reply if isinstance(reply, str) else json.dumps(
        reply, ensure_ascii=False)
    rb = text.encode("utf-8")
    entry["reply_sha256"] = write_body(run_dir, rb)
    entry["reply_bytes"] = len(rb)
    # Расход ответа — из бокового канала адаптера (шаг 1 «Записи о
    # прогоне»): None = канал не сообщил; counted=false = честно не
    # считается (командная строка по подписке).
    entry["usage"] = llm_usage.take()
    entry["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
    record(run_dir, entry)
    return reply


def wrap(run_dir, fn, role: str, klass: str | None = None,
         action_n: int | None = None, stage: str | None = None):
    """Текстовый канал `fn(prompt) -> str` под журналом. Отпечаток канала
    (`identity`) переносится на обёртку — соль кэшей и журнал стадий
    видят тот же канал; повторная обёртка не создаётся."""
    if getattr(fn, "journal_wrapped", False):
        return fn

    def wrapped(prompt, *args, **kwargs):
        return _call_recorded(run_dir, fn, "llm", role, klass, action_n,
                              stage, str(prompt), [],
                              lambda: fn(prompt, *args, **kwargs))
    wrapped.identity = getattr(fn, "identity", None)
    wrapped.journal_wrapped = True
    wrapped.inner = fn
    wrapped.__name__ = getattr(fn, "__name__", "channel")
    return wrapped


def wrap_vision(run_dir, fn, action_n: int | None = None,
                stage: str | None = "заморозка"):
    """Зрительный канал `fn(prompt, path) -> str` под журналом: в
    отпечатке — sha256 байтов изображения и его имя."""
    if getattr(fn, "journal_wrapped", False):
        return fn

    def wrapped(prompt, path, *args, **kwargs):
        p = Path(path)
        try:
            isha = _sha(p.read_bytes())
        except OSError:
            isha = None
        images = [{"name": p.name, "sha256": isha}]
        return _call_recorded(run_dir, fn, "vision", "vision", None,
                              action_n, stage, str(prompt), images,
                              lambda: fn(prompt, path, *args, **kwargs))
    wrapped.identity = getattr(fn, "identity", None)
    wrapped.journal_wrapped = True
    wrapped.inner = fn
    wrapped.__name__ = getattr(fn, "__name__", "vision")
    return wrapped
