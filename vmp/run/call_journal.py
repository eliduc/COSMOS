# -*- coding: utf-8 -*-
"""Журнал внешних вызовов попытки нажатия и подстановщик повтора
(система прогонов COSMOS, проект 07.09.2026, разделы 17.1-17.2).

Единица воспроизведения — попытка нажатия. Всё, что приходит извне
(ответы решателя ростеринга, каталог и побочные файлы построителя
пейринга, ответы модели-сертификатора), записывается в каталог
`<out>/external-calls/` попытки: `manifest.json`, `index.jsonl` (одна
строка на вызов в порядке вызовов) и тела — отдельными файлами.
Записанного достаточно, чтобы повторить попытку без решателя,
построителя и модели: «повтор из журнала» подставляет записанные
ответы в точках внешних вызовов, а детерминированные части панели
исполняются заново.

Правила (17.1):
- вход каждого вызова — отпечаток `sha256` канонического JSON словаря
  компонентов; тела входят в отпечаток хешем своих байтов;
- тела — только байтами; больше 64 КБ — gzip; `*_sha256` — от несжатого;
- одинаковые входы (опросы status) повторяются в записанном порядке;
- исключение внешней стороны записывается и пробрасывается как есть;
- остановка по часам панели (не внешняя сторона) — `manifest.terminal_stop`;
- секреты не пишутся; адреса — без userinfo;
- сбой собственного писателя не роняет нажатие: `degraded` словами,
  `complete: false` — журнал не мешает расчёту, но не лжёт о полноте.

Обёртки прозрачны для стендов: перехватчики ставятся только на имена,
которые есть у объекта; сигнатуры не меняются; `base` — через getattr.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import logging
import os
import platform
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from vmp.run.button import ButtonStop

SCHEMA = "cosmos-call-journal/v1"
DIR_NAME = "external-calls"
INDEX_NAME = "index.jsonl"
MANIFEST_NAME = "manifest.json"
ENV_SWITCH = "CREW_CALL_JOURNAL"
GZIP_THRESHOLD = 64 * 1024
#: Единственная остановка, которую поднимает не внешняя сторона, а цикл
#: опроса по часам панели (vmp.run.button.orchestrate, Ш-2а).
CLOCK_TIMEOUT_VERDICT = "остановка: превышено время ожидания счёта"
MISMATCH_VERDICT = ("остановка: повтор из журнала — вход вызова "
                    "разошёлся с журналом")
MODES = ("record", "replay-journal", "replay-fresh")

#: Привязка позиционных аргументов методов решателя (vmp.run.button.SolverHttp)
#: к именам; именованные — по ключу; неизвестные методы — как есть.
_SOLVER_ARGS = {
    "start": ("payload", "zip_b64", "mode", "objective_id", "limit_s",
              "task", "pairing_run_id"),
    "pairing_catalog": ("payload", "zip_b64", "catalog_b64",
                        "catalog_sha256"),
    "coverage": ("zip_b64",),
    "status": ("run_id",),
    "result": ("run_id",),
    "applied": ("run_id",),
    "score_breakdown": ("run_id", "problem"),
    "health": (),
}
#: Файловые аргументы построителя: путь одинаков, содержимое — нет,
#: поэтому в отпечаток входит хеш файла.
_BUILDER_FILE_ARGS = ("--source-rules", "--conditions", "--pin-catalog",
                      "--warm-catalog", "--inject-catalog")
#: Файлы, которые пишет сама обёртка `build` внутри вызова: каталог уже
#: лежит в `catalog.json`, payload воспроизводится из входа.
_BUILDER_OWN_FILES = ("builder-payload.json", "builder-catalog.json")
#: Собственные журналы попытки в том же каталоге выдачи — не побочные файлы.
_BUILDER_SKIP_DIRS = (DIR_NAME, "llm-exchanges")

log = logging.getLogger("cosmos.call_journal")

_ABSENT = object()


# ------------------------------------------------------------ помощники

def enabled() -> bool:
    """Выключатель площадки: умолчание — включено; `0`/`off`/`false`/`no`
    — обёртки решателя и построителя не ставятся (код `disabled-by-site`).
    В режимах повтора не действует."""
    got = (os.environ.get(ENV_SWITCH) or "").strip().lower()
    return got not in ("0", "off", "false", "no")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json_fallback(o):
    return repr(o)


def canon(obj) -> bytes:
    """Канонический JSON: sort_keys, без пробелов, UTF-8, запасной repr."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=_json_fallback
                      ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canon_sha256(obj) -> str:
    return sha256_bytes(canon(obj))


def fingerprint_of(components: dict) -> str:
    return canon_sha256(components)


def _strip_userinfo(url) -> str | None:
    """`http://user:pass@host:9099/x` → `http://host:9099/x`; не строка —
    None; строка без схемы — как есть."""
    if not isinstance(url, str) or not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.netloc or "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, host, parts.path, parts.query,
                       parts.fragment))


#: Учётные данные в адресах внутри произвольного текста: `схема://user:pass@`
#: → `схема://` (тот же смысл, что у _strip_userinfo, но для сообщений
#: исключений: httpx подставляет в них полный URL запроса вместе с userinfo).
_USERINFO_IN_TEXT = re.compile(r"\b([a-z][a-z0-9+.\-]*://)[^/\s@]+@", re.I)


def strip_userinfo_text(text) -> str:
    """Убрать `user:pass@` из всех URL-подобных подстрок текста; не
    строка — str() значения. Применяется к сообщениям и деталям
    исключений внешних сторон до записи в журнал и в запись попытки
    (правило 17.1: секреты не пишутся; ревью 07.09)."""
    return _USERINFO_IN_TEXT.sub(r"\1", str(text))


def _write_body(path: Path, data: bytes) -> None:
    """Единственный писатель тел журнала (подмена в тестах — здесь, не
    у Path.write_bytes: тот уронил бы и запись пакета кнопки)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def read_body(path: Path) -> bytes:
    """Тело записи байтами; gzip снимается по расширению."""
    path = Path(path)
    raw = path.read_bytes()
    if path.suffix == ".gz":
        return gzip.decompress(raw)
    return raw


def _dump_body(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=1,
                       default=_json_fallback) + "\n").encode("utf-8")


def _error_record(e: BaseException) -> dict:
    """Запись исключения внешней стороны: слова — без учётных данных
    адресов (сообщение httpx несёт полный URL запроса с user:pass@)."""
    out = {"type": type(e).__name__,
           "message": strip_userinfo_text(str(e))[:2000]}
    if isinstance(e, ButtonStop):
        out["verdict"] = e.verdict
        out["detail"] = (strip_userinfo_text(e.detail)
                         if isinstance(e.detail, str) else e.detail)
        out["extra"] = _jsonable(e.extra)
    return out


def _jsonable(obj):
    """JSON-вид значения С СОХРАНЕНИЕМ порядка ключей: артефакты кнопки
    (solver-status.json, solved.json, applied-rules.json) пишутся из
    ответа решателя без сортировки ключей, и повтор обязан отдать тот же
    порядок — иначе равные по смыслу файлы разошлись бы байтами."""
    try:
        return json.loads(json.dumps(obj, ensure_ascii=False,
                                     default=_json_fallback))
    except Exception:                                      # noqa: BLE001
        return repr(obj)


def _raise_recorded(err: dict):
    """Записанное исключение — тем же классом и словами."""
    kind = str(err.get("type") or "RuntimeError")
    if kind == "ButtonStop" or err.get("verdict") is not None:
        extra = err.get("extra") if isinstance(err.get("extra"), dict) else {}
        raise ButtonStop(str(err.get("verdict")), str(err.get("detail") or ""),
                         **extra)
    import builtins
    cls = getattr(builtins, kind, None)
    message = str(err.get("message") or "")
    if isinstance(cls, type) and issubclass(cls, Exception):
        try:
            raise cls(message)
        except TypeError:
            pass
    raise RuntimeError("%s: %s" % (kind, message))


def _rel_args(args, wd: Path | None) -> list[str]:
    """Аргументы построителя относительно рабочего каталога: `str(wd)` →
    `<wd>`, разделитель `/`."""
    out = []
    root = str(wd) if wd is not None else None
    root_alt = root.replace("\\", "/") if root else None
    for a in args or ():
        s = str(a)
        if root and s.startswith(root):
            s = "<wd>" + s[len(root):]
        elif root_alt and s.replace("\\", "/").startswith(root_alt):
            s = "<wd>" + s.replace("\\", "/")[len(root_alt):]
        out.append(s.replace("\\", "/"))
    return out


def _builder_components(payload, args, wd, select_engine, deterministic,
                        label) -> dict:
    file_hashes: dict[str, str] = {}
    lst = [str(a) for a in (args or ())]
    for i, a in enumerate(lst[:-1]):
        if a in _BUILDER_FILE_ARGS:
            p = Path(lst[i + 1])
            if p.is_file():
                try:
                    file_hashes[a] = sha256_bytes(p.read_bytes())
                except OSError:
                    file_hashes[a] = None
    return {"payload_sha256": canon_sha256(payload),
            "args_rel": _rel_args(lst, wd),
            "file_args_sha256": file_hashes,
            "select_engine": select_engine,
            "deterministic_env": bool(deterministic),
            "label": label}


def _solver_components(method: str, args: tuple, kwargs: dict
                       ) -> tuple[dict, dict]:
    """(компоненты входа, тела для записи) вызова метода решателя."""
    names = _SOLVER_ARGS.get(method)
    if names is None:
        comps = {"args": _jsonable(list(args)), "kwargs": _jsonable(dict(kwargs))}
        return comps, {}
    bound: dict = {}
    for i, v in enumerate(args):
        key = names[i] if i < len(names) else "arg%d" % i
        bound[key] = v
    bound.update(kwargs)
    comps: dict = {}
    bodies: dict = {}
    for k, v in bound.items():
        if k == "payload":
            body = canon(v)
            comps["payload_sha256"] = sha256_bytes(body)
            bodies["payload"] = body
        elif k == "problem":
            body = canon(v)
            comps["problem_sha256"] = sha256_bytes(body)
            bodies["problem"] = body
        elif k == "zip_b64":
            try:
                comps["zip_sha256"] = sha256_bytes(base64.b64decode(v))
            except Exception:                              # noqa: BLE001
                comps["zip_sha256"] = None
        elif k == "catalog_b64":
            # Отпечаток каталога идёт отдельным аргументом
            # (catalog_sha256 — как у решателя); байты — в записи
            # построителя.
            continue
        else:
            comps[k] = _jsonable(v)
    return comps, bodies


def _dir_snapshot(out: Path) -> dict:
    out = Path(out)
    got: dict = {}
    if not out.is_dir():
        return got
    for p in out.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(out).as_posix()
        if rel.split("/", 1)[0] in _BUILDER_SKIP_DIRS:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        got[rel] = (st.st_mtime_ns, st.st_size)
    return got


def first_json_difference(a, b, path: str = "$") -> dict | None:
    """Первый расходящийся путь двух JSON-значений при обходе с
    сортировкой ключей: {"path", "source", "replay"} либо None."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b), key=str):
            p = "%s.%s" % (path, k)
            if k not in a:
                return {"path": p, "source": "<ключа нет у источника>",
                        "replay": _short(b[k])}
            if k not in b:
                return {"path": p, "source": _short(a[k]),
                        "replay": "<ключа нет у повтора>"}
            got = first_json_difference(a[k], b[k], p)
            if got:
                return got
        return None
    if isinstance(a, list) and isinstance(b, list):
        for i in range(max(len(a), len(b))):
            p = "%s[%d]" % (path, i)
            if i >= len(a):
                return {"path": p, "source": "<элемента нет у источника>",
                        "replay": _short(b[i])}
            if i >= len(b):
                return {"path": p, "source": _short(a[i]),
                        "replay": "<элемента нет у повтора>"}
            got = first_json_difference(a[i], b[i], p)
            if got:
                return got
        return None
    if a != b or type(a) is not type(b):
        return {"path": path, "source": _short(a), "replay": _short(b)}
    return None


def _short(v, limit: int = 160) -> str:
    try:
        s = json.dumps(v, ensure_ascii=False, sort_keys=True,
                       default=_json_fallback)
    except Exception:                                      # noqa: BLE001
        s = repr(v)
    return s if len(s) <= limit else s[:limit] + "…"


# ------------------------------------------------------------ журнал

class CallJournal:
    """Журнал внешних вызовов одной попытки.

    Создаётся в памяти до занятия кнопки (вызовы до занятия — проверка
    выбранного пейринга — должны попасть в журнал, а каталог выдачи в
    этот момент ещё не очищен); `open(dir)` после очистки каталога
    сбрасывает буфер на диск, дальше записи идут сразу. Отказ до занятия
    — журнал не открыт, на диске ничего нет.
    """

    def __init__(self, attempt_id: str, task: str, *, out_name: str,
                 mode: str = "record", source_attempt: str | None = None,
                 versions: dict | None = None, replayer=None):
        if mode not in MODES:
            raise ValueError("режим журнала %r вне словаря %s" % (mode, MODES))
        self.attempt_id = attempt_id
        self.task = task
        self.out_name = out_name
        self.mode = mode
        self.source_attempt = source_attempt
        self.versions: dict = dict(versions or {})
        self.replayer = replayer
        self.dir: Path | None = None
        self._pending: list[tuple] = []          # ("body", rel, data) | ("line", row)
        self._n = 0
        self.counts = {"solver": 0, "pairing_builder": 0, "llm": 0,
                       "served_from_journal": 0, "unconsumed": 0}
        self.solver_methods: list[str] = []
        self.solver_url: str | None = None
        self.degraded: str | None = None
        self.complete: bool | None = None
        self.terminal_stop: dict | None = None
        self.opened_at = now_iso()
        self.closed_at: str | None = None
        self.closed = False
        self.last_error: dict | None = None
        #: Стенное время счёта попытки (раздел «расход ресурсов» проекта
        #: записи о прогоне): от начала ПЕРВОГО вызова счётной стороны
        #: (решатель, построитель пейринга) до конца ПОСЛЕДНЕГО — ровно
        #: то, что панель ждала счёта. Опросы состояния входят в промежуток
        #: (машина в это время считает), проба здоровья службы — нет.
        self._solve_first_s: float | None = None
        self._solve_last_s: float | None = None

    # ------------------------------------------------------ сведения
    def note_solver_methods(self, names) -> None:
        for n in names:
            if n not in self.solver_methods:
                self.solver_methods.append(n)

    def note_solver_url(self, url: str | None) -> None:
        self.solver_url = _strip_userinfo(url)
        solver = self.versions.setdefault("solver", {})
        if isinstance(solver, dict):
            solver["url"] = self.solver_url

    def note_solve_span(self, elapsed_ms: int) -> None:
        """Отметить промежуток счёта: вызов кончился сейчас и длился
        `elapsed_ms` — отсюда начало первого и конец последнего."""
        try:
            end = time.monotonic()
            start = end - max(0.0, float(elapsed_ms)) / 1000.0
        except (TypeError, ValueError):
            return
        if self._solve_first_s is None or start < self._solve_first_s:
            self._solve_first_s = start
        if self._solve_last_s is None or end > self._solve_last_s:
            self._solve_last_s = end

    def solve_seconds(self) -> float | None:
        """Стенное время счёта попытки в секундах (от первого вызова
        счётной стороны до последнего) либо None — счётных вызовов не
        было."""
        if self._solve_first_s is None or self._solve_last_s is None:
            return None
        return round(max(0.0, self._solve_last_s - self._solve_first_s), 3)

    def next_n(self) -> int:
        self._n += 1
        return self._n

    @property
    def count(self) -> int:
        return self._n

    def rel_dir(self) -> str:
        return "%s/%s" % (self.out_name, DIR_NAME)

    # ------------------------------------------------------ запись
    def _degrade(self, words: str) -> None:
        if self.degraded is None:
            self.degraded = words
        log.warning("журнал внешних вызовов %s: %s", self.attempt_id, words)

    def _write(self, rel: str, data: bytes) -> None:
        try:
            _write_body(self.dir / rel, data)
        except Exception as e:                             # noqa: BLE001
            self._degrade("тело %s не записано: %s" % (rel, str(e)[:200]))

    def _append_line(self, row: dict) -> None:
        line = (json.dumps(row, ensure_ascii=False, sort_keys=True,
                           default=_json_fallback) + "\n").encode("utf-8")
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with (self.dir / INDEX_NAME).open("ab") as fh:
                fh.write(line)
        except Exception as e:                             # noqa: BLE001
            self._degrade("строка индекса №%s не записана: %s"
                          % (row.get("n"), str(e)[:200]))

    def store(self, rel: str, data: bytes) -> dict:
        """Тело файлом (буфер до открытия); >64 КБ — gzip; sha — от
        несжатого. Возвращает {"file", "sha256", "bytes", "encoding"}."""
        sha = sha256_bytes(data)
        size = len(data)
        enc = "raw"
        if size > GZIP_THRESHOLD:
            data = gzip.compress(data, mtime=0)
            rel = rel + ".gz"
            enc = "gzip"
        if self.mode == "replay-journal":
            # Повтор из журнала тел не копирует (ссылки served_from).
            return {"file": None, "sha256": sha, "bytes": size, "encoding": enc}
        if self.dir is None:
            self._pending.append(("body", rel, data))
        else:
            self._write(rel, data)
        return {"file": rel, "sha256": sha, "bytes": size, "encoding": enc}

    def append(self, row: dict) -> dict:
        row = dict(row)
        row.setdefault("at", now_iso())
        kind = row.get("kind")
        if kind in ("solver", "pairing_builder", "llm"):
            self.counts[kind] += 1
        if row.get("served_from"):
            self.counts["served_from_journal"] += 1
        if self.dir is None:
            self._pending.append(("line", row))
        else:
            self._append_line(row)
        return row

    def open(self, directory: Path) -> None:
        """Каталог на диске: манифест (без closed_at), затем буфер."""
        self.dir = Path(directory)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._degrade("каталог журнала не создан: %s" % str(e)[:200])
        self._write_manifest()
        pending, self._pending = self._pending, []
        for item in pending:
            if item[0] == "body":
                self._write(item[1], item[2])
            else:
                self._append_line(item[1])

    def _served(self, fingerprint: str) -> dict | None:
        if self.replayer is None:
            return None
        e = self.replayer.take_served()
        if e is None:
            return None
        if (e.get("input") or {}).get("fingerprint") != fingerprint:
            return None
        return e

    def finish_solver(self, n: int, method: str, comps: dict, bodies: dict,
                      *, response=_ABSENT, error: BaseException | None = None,
                      elapsed_ms: int = 0) -> dict:
        fp = fingerprint_of(comps)
        row: dict = {"n": n, "kind": "solver", "method": method,
                     "elapsed_ms": int(elapsed_ms),
                     "input": {"fingerprint": fp, "components": comps},
                     "ok": error is None}
        if method != "health":
            # Проба здоровья службы — не счёт: в промежуток не входит.
            self.note_solve_span(elapsed_ms)
        if method not in self.solver_methods:
            self.solver_methods.append(method)
        served = self._served(fp)
        if served is not None:
            row["served_from"] = {"attempt": self.source_attempt,
                                  "n": served.get("n")}
            for k in ("body", "body_sha256", "body_encoding", "payload",
                      "payload_sha256", "payload_encoding", "problem",
                      "problem_sha256", "problem_encoding"):
                if k in served:
                    row[k] = served[k]
            if error is not None:
                row["error"] = _error_record(error)
            return self.append(row)
        if error is not None:
            row["error"] = _error_record(error)
            self.last_error = row["error"]
        body = _dump_body({"response": (None if response is _ABSENT
                                        else _jsonable(response)),
                           "error": row.get("error")})
        stem = "%03d-solver.%s" % (n, method)
        meta = self.store(stem + ".json", body)
        row["body"] = meta["file"]
        row["body_sha256"] = meta["sha256"]
        row["body_encoding"] = meta["encoding"]
        for key, data in bodies.items():
            m = self.store("%s.%s.json" % (stem, key), data)
            row[key] = m["file"]
            row[key + "_sha256"] = m["sha256"]
            row[key + "_encoding"] = m["encoding"]
        if error is not None and isinstance(error, ReplayMismatch):
            row["mismatch"] = True
        return self.append(row)

    def finish_builder(self, n: int, comps: dict, out: Path, before: dict,
                       *, catalog: bytes | None = None,
                       payload=None, error: BaseException | None = None,
                       elapsed_ms: int = 0) -> dict:
        fp = fingerprint_of(comps)
        row: dict = {"n": n, "kind": "pairing_builder", "method": "build",
                     "elapsed_ms": int(elapsed_ms),
                     "input": {"fingerprint": fp, "components": comps},
                     "ok": error is None}
        self.note_solve_span(elapsed_ms)
        served = self._served(fp)
        if served is not None:
            row["served_from"] = {"attempt": self.source_attempt,
                                  "n": served.get("n")}
            for k in ("body", "body_sha256", "catalog", "catalog_sha256",
                      "catalog_encoding", "files"):
                if k in served:
                    row[k] = served[k]
            if error is not None:
                row["error"] = _error_record(error)
            return self.append(row)
        if error is not None:
            row["error"] = _error_record(error)
            self.last_error = row["error"]
        base = "%03d-pairing_builder" % n
        out = Path(out)
        # Побочные файлы: появились или изменились за время вызова.
        after = _dir_snapshot(out)
        changed = [rel for rel, sig in after.items() if before.get(rel) != sig]
        own: dict = {}
        files: list[dict] = []
        canonical_payload = (json.dumps(payload, ensure_ascii=False)
                             .encode("utf-8") if payload is not None else None)
        for rel in sorted(changed):
            data = None
            try:
                data = (out / rel).read_bytes()
            except OSError as e:
                self._degrade("побочный файл %s не прочитан: %s" % (rel, e))
                continue
            if rel in _BUILDER_OWN_FILES:
                canon_bytes = (canonical_payload if rel == "builder-payload.json"
                               else catalog)
                if canon_bytes is not None and data == canon_bytes:
                    own[rel] = "canonical"
                    continue
                own[rel] = "stored"
            m = self.store("%s/files/%s" % (base, rel), data)
            files.append({"name": rel, "file": m["file"], "sha256": m["sha256"],
                          "bytes": m["bytes"], "encoding": m["encoding"]})
        for rel in _BUILDER_OWN_FILES:
            if rel not in own:
                own[rel] = "present" if (rel in after and rel in before
                                         and before.get(rel) == after.get(rel)) \
                    else None
        request = {"components": comps, "own_files": own,
                   "error": row.get("error")}
        m = self.store(base + "/request.json", _dump_body(request))
        row["body"] = m["file"]
        row["body_sha256"] = m["sha256"]
        row["body_encoding"] = m["encoding"]
        if catalog is not None:
            mc = self.store(base + "/catalog.json", bytes(catalog))
            row["catalog"] = mc["file"]
            row["catalog_sha256"] = mc["sha256"]
            row["catalog_encoding"] = mc["encoding"]
        row["files"] = files
        return self.append(row)

    def record_llm(self, role: str, exchange_n: int, request_sha256: str,
                   reply_sha256: str | None, error: str | None,
                   elapsed_ms: int, usage: dict | None = None) -> dict:
        """Строка вызова модели. `usage` — расход вызова в токенах
        (провайдер, модель, канал, вход, выход; решение владельца № 1 от
        10.09.2026); канал без расхода даёт строку с `counted: false` и
        причиной словами, отсутствие ключа — журнал прежней версии."""
        comps = {"role": role, "request_sha256": request_sha256}
        fp = fingerprint_of(comps)
        row: dict = {"n": self.next_n(), "kind": "llm", "role": role,
                     "exchange_n": exchange_n,
                     "request_sha256": request_sha256,
                     "reply_sha256": reply_sha256, "error": error,
                     "elapsed_ms": int(elapsed_ms),
                     "usage": dict(usage) if isinstance(usage, dict) else None,
                     "input": {"fingerprint": fp, "components": comps},
                     "ok": error is None}
        served = self._served(fp)
        if served is not None:
            row["served_from"] = {"attempt": self.source_attempt,
                                  "n": served.get("n")}
        return self.append(row)

    # ------------------------------------------------------ итог
    def _manifest_doc(self) -> dict:
        served_from = None
        if self.replayer is not None:
            served_from = {"attempt": self.source_attempt,
                           "index_sha256": self.replayer.index_sha256}
        return {"schema": SCHEMA, "attempt": self.attempt_id,
                "task": self.task, "out": self.out_name,
                "mode": self.mode, "source_attempt": self.source_attempt,
                "opened_at": self.opened_at, "closed_at": self.closed_at,
                "complete": self.complete,
                "degraded": self.degraded,
                "counts": dict(self.counts),
                "solver_methods": list(self.solver_methods),
                "terminal_stop": self.terminal_stop,
                "served_from": served_from,
                "versions": self.versions}

    def _write_manifest(self) -> None:
        if self.dir is None:
            return
        try:
            _write_body(self.dir / MANIFEST_NAME, _dump_body(self._manifest_doc()))
        except Exception as e:                             # noqa: BLE001
            self._degrade("манифест журнала не записан: %s" % str(e)[:200])

    def close(self, complete: bool, delivery: dict | None = None) -> None:
        if self.closed:
            return
        self.closed = True
        self.closed_at = now_iso()
        if isinstance(delivery, dict) \
                and delivery.get("verdict") == CLOCK_TIMEOUT_VERDICT:
            self.terminal_stop = {
                "verdict": CLOCK_TIMEOUT_VERDICT,
                "detail": delivery.get("detail"),
                "extra": {"solver_run_id": delivery.get("solver_run_id")}}
        if self.replayer is not None:
            self.counts["served_from_journal"] = self.replayer.served
            self.counts["unconsumed"] = self.replayer.unconsumed
        self.complete = bool(complete) and self.degraded is None
        if self.dir is not None:
            self._append_line({"kind": "end", "complete": self.complete,
                               "count": self._n, "at": self.closed_at})
            self._write_manifest()

    def manifest(self) -> dict:
        return self._manifest_doc()

    def record_summary(self, rel_dir: str | None = None) -> dict:
        """Запись попытки (момент Н+): каталог относительно рабочего
        каталога попытки, полнота, счётчики, деградация."""
        out = {"dir": rel_dir, "complete": bool(self.complete),
               "counts": dict(self.counts), "degraded": self.degraded,
               "mode": self.mode}
        if self.source_attempt:
            out["source_attempt"] = self.source_attempt
        if self.replayer is not None and self.replayer.mismatch:
            out["mismatch"] = dict(self.replayer.mismatch)
        return out


# ------------------------------------------------------------ обёртки записи

class _SolverProxy:
    """Прозрачная обёртка объекта решателя: перехватчики — на имена,
    которые есть у объекта; невызываемые атрибуты делегируются как есть;
    `base` отдаётся без userinfo; сигнатуры не меняются."""

    def __init__(self, obj, journal: CallJournal):
        object.__setattr__(self, "_obj", obj)
        object.__setattr__(self, "_journal", journal)
        methods = [m for m in _SOLVER_ARGS
                   if callable(getattr(obj, m, None))]
        journal.note_solver_methods(methods)
        journal.note_solver_url(getattr(obj, "base", None))

    def __getattr__(self, name):
        obj = object.__getattribute__(self, "_obj")
        attr = getattr(obj, name)                  # AttributeError — как есть
        if name == "base":
            return _strip_userinfo(attr) if isinstance(attr, str) else attr
        if name.startswith("_") or not callable(attr):
            return attr
        journal = object.__getattribute__(self, "_journal")

        def call(*args, **kwargs):
            comps, bodies = _solver_components(name, args, kwargs)
            n = journal.next_n()
            t0 = time.monotonic()
            try:
                resp = attr(*args, **kwargs)
            except BaseException as e:
                journal.finish_solver(
                    n, name, comps, bodies, error=e,
                    elapsed_ms=int((time.monotonic() - t0) * 1000))
                raise
            journal.finish_solver(
                n, name, comps, bodies, response=resp,
                elapsed_ms=int((time.monotonic() - t0) * 1000))
            return resp
        call.__name__ = name
        return call

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_obj"), name, value)


def wrap_solver(solver, journal: CallJournal | None):
    """Журналируемый решатель (None-журнал — объект как есть)."""
    if solver is None or journal is None:
        return solver
    return _SolverProxy(solver, journal)


def wrap_builder(builder, journal: CallJournal | None, *,
                 select_engine: str | None = None, label: str | None = None,
                 deterministic: bool = False, wd: Path | None = None):
    """Журналируемый построитель `(payload, args, out) -> bytes`."""
    if builder is None or journal is None:
        return builder

    def build(payload, args, out):
        out_p = Path(out)
        before = _dir_snapshot(out_p)
        comps = _builder_components(payload, args, wd, select_engine,
                                    deterministic, label)
        n = journal.next_n()
        t0 = time.monotonic()
        try:
            catalog = builder(payload, args, out)
        except BaseException as e:
            journal.finish_builder(
                n, comps, out_p, before, payload=payload, error=e,
                elapsed_ms=int((time.monotonic() - t0) * 1000))
            raise
        journal.finish_builder(
            n, comps, out_p, before,
            catalog=(bytes(catalog) if isinstance(catalog, (bytes, bytearray))
                     else None),
            payload=payload,
            elapsed_ms=int((time.monotonic() - t0) * 1000))
        return catalog
    build.__name__ = "journaled_build"
    return build


def panel_versions() -> dict:
    """Версия панели для manifest.versions.panel."""
    import _data_paths
    return {"data_root": str(_data_paths.data_root()),
            "platform": platform.platform(),
            "python": platform.python_version()}


def builder_versions(select_engine: str | None, engine_versions: dict | None
                     ) -> dict:
    root = Path(__file__).resolve().parents[2]
    script = root / "_pairing_builder.py"
    try:
        script_sha = sha256_bytes(script.read_bytes())
    except OSError:
        script_sha = None
    return {"script_sha256": script_sha, "select_engine": select_engine,
            "engine_versions": engine_versions,
            "python": platform.python_version()}


# ------------------------------------------------------------ повтор

class ReplayMismatch(ButtonStop):
    """Вход вызова разошёлся с журналом: как подкласс ButtonStop внутри
    оркестратора даёт выдачу-остановку и завершённую попытку; до занятия
    кнопки превращается в отказ 409 словами (reason_code)."""
    reason_code = "journal-mismatch"

    def __init__(self, detail: str, *, n: int | None, method: str,
                 components_differ: list[str], first_json: dict | None = None,
                 exhausted: bool = False):
        info = {"n": n, "method": method,
                "components_differ": list(components_differ),
                "first_json": first_json, "exhausted": exhausted,
                "words": detail}
        super().__init__(MISMATCH_VERDICT, detail, replay_mismatch=info)
        self.n = n
        self.method = method
        self.components_differ = list(components_differ)
        self.first_json = first_json
        self.info = info


def _load_index(path: Path) -> list[dict]:
    rows: list[dict] = []
    for raw in path.read_bytes().decode("utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


class JournalReplayer:
    """Подстановщик записанных ответов: очереди по ключу `(kind, method,
    fingerprint)` в порядке записи; одинаковые входы отдаются в
    записанном порядке; очередь исчерпана — честная остановка."""

    def __init__(self, journal_dir: Path, exchanges_dir: Path | None = None,
                 source_attempt: str | None = None):
        self.dir = Path(journal_dir)
        self.exchanges_dir = Path(exchanges_dir) if exchanges_dir else None
        self.source_attempt = source_attempt
        raw_manifest = read_body(self.dir / MANIFEST_NAME)
        self.manifest = json.loads(raw_manifest.decode("utf-8"))
        index_bytes = (self.dir / INDEX_NAME).read_bytes()
        self.index_sha256 = sha256_bytes(index_bytes)
        rows = _load_index(self.dir / INDEX_NAME)
        self.end = next((r for r in reversed(rows) if r.get("kind") == "end"), None)
        self.entries = [r for r in rows if r.get("kind") != "end"]
        self.queues: dict[tuple, deque] = {}
        for e in self.entries:
            key = (e.get("kind"), e.get("method") or e.get("role"),
                   (e.get("input") or {}).get("fingerprint"))
            self.queues.setdefault(key, deque()).append(e)
        self.total = len(self.entries)
        self.served = 0
        self.mismatch: dict | None = None
        self._last_served: dict | None = None
        self._consumed_ns: set = set()

    @property
    def unconsumed(self) -> int:
        return self.total - self.served

    def take_served(self) -> dict | None:
        e, self._last_served = self._last_served, None
        return e

    def _pending_same_method(self, kind: str, method: str) -> list[dict]:
        return [e for e in self.entries
                if e.get("kind") == kind
                and (e.get("method") or e.get("role")) == method
                and e.get("n") not in self._consumed_ns]

    def next(self, kind: str, method: str, components: dict,
             bodies: dict | None = None) -> dict:
        fp = fingerprint_of(components)
        q = self.queues.get((kind, method, fp))
        if q:
            e = q.popleft()
            self.served += 1
            self._consumed_ns.add(e.get("n"))
            self._last_served = e
            return e
        pending = self._pending_same_method(kind, method)
        if kind == "solver" and method == "status" and not pending \
                and isinstance(self.manifest.get("terminal_stop"), dict):
            ts = self.manifest["terminal_stop"]
            extra = ts.get("extra") if isinstance(ts.get("extra"), dict) else {}
            raise ButtonStop(str(ts.get("verdict")), str(ts.get("detail") or ""),
                             **extra)
        raise self._mismatch(kind, method, components, fp, bodies,
                             exhausted=(q is not None and not pending))

    def _mismatch(self, kind, method, components, fp, bodies, *,
                  exhausted: bool) -> ReplayMismatch:
        pending = self._pending_same_method(kind, method)
        nearest = pending[0] if pending else None
        differ: list[str] = []
        first_json = None
        n_recorded = sum(1 for e in self.entries
                         if e.get("kind") == kind
                         and (e.get("method") or e.get("role")) == method)
        if nearest is None:
            if exhausted or n_recorded:
                words = ("журнал исчерпан: %s(%s) вызван %d раз, записано %d"
                         % (method, fp[:12], n_recorded + 1, n_recorded))
            else:
                words = ("журнал не содержит вызова %s.%s (%s): у источника "
                         "такого вызова не было" % (kind, method, fp[:12]))
        else:
            rec = (nearest.get("input") or {}).get("components") or {}
            parts = []
            for k in sorted(set(rec) | set(components), key=str):
                a, b = rec.get(k, "<нет>"), components.get(k, "<нет>")
                if _jsonable(a) != _jsonable(b):
                    differ.append(k)
                    parts.append("%s: ожидалось %s, получено %s"
                                 % (k, _short(a, 80), _short(b, 80)))
            for key in ("payload", "problem"):
                if key + "_sha256" not in differ or not bodies or key not in bodies:
                    continue
                # Записанное тело: у решателя — файл записи; у построителя
                # payload не дублируется в журнале и берётся из снимка
                # источника (builder-payload.json рядом с журналом).
                old_path = (self.dir / nearest[key]) if nearest.get(key) else (
                    self.dir.parent / "builder-payload.json"
                    if kind == "pairing_builder" and key == "payload" else None)
                if old_path is None or not old_path.is_file():
                    continue
                try:
                    old = json.loads(read_body(old_path).decode("utf-8"))
                    new = json.loads(bodies[key].decode("utf-8"))
                    first_json = first_json_difference(old, new)
                except Exception:                          # noqa: BLE001
                    first_json = None
                break
            words = ("запись №%s (%s) ближайшая невостребованная того же "
                     "метода; разошлись компоненты: %s"
                     % (nearest.get("n"), method,
                        "; ".join(parts) or "отпечаток"))
            if first_json:
                words += " — первое различие тела: %s: %s → %s" % (
                    first_json["path"], first_json["source"],
                    first_json["replay"])
        self.mismatch = {"n": nearest.get("n") if nearest else None,
                         "method": method, "kind": kind,
                         "components_differ": differ, "words": words,
                         "first_json": first_json}
        return ReplayMismatch(words, n=self.mismatch["n"], method=method,
                              components_differ=differ, first_json=first_json,
                              exhausted=exhausted)

    def response(self, entry: dict):
        """Ответ записи как есть; записанное исключение — тем же классом."""
        body = json.loads(read_body(self.dir / entry["body"]).decode("utf-8"))
        if isinstance(body, dict) and body.get("error"):
            _raise_recorded(body["error"])
        return body.get("response") if isinstance(body, dict) else body

    def llm_reply(self, entry: dict) -> str:
        """Байты ответа модели источника (`llm-exchanges/`), decode utf-8 —
        строка возвращается байт в байт, включая `\\r`."""
        from vmp.run import llm_exchanges as lx
        if self.exchanges_dir is None:
            raise RuntimeError("обмены с моделью источника не найдены")
        n = entry.get("exchange_n")
        role = entry.get("role") or "certifier"
        data, err = lx.reply_bytes(self.exchanges_dir, int(n), role)
        if data is None:
            if err is not None:
                raise RuntimeError("записанная ошибка канала модели (обмен %s): %s"
                                   % (n, err))
            raise RuntimeError("ответ модели обмена %s не найден в снимке "
                               "источника" % n)
        return data.decode("utf-8")


class JournalSolver:
    """Подставной решатель: методы ровно из `manifest.solver_methods`;
    `base` — записанный адрес (без userinfo), если он был."""

    def __init__(self, replayer: JournalReplayer):
        self._r = replayer
        versions = replayer.manifest.get("versions") or {}
        url = (versions.get("solver") or {}).get("url") \
            if isinstance(versions.get("solver"), dict) else None
        if isinstance(url, str) and url:
            self.base = url
        for m in replayer.manifest.get("solver_methods") or []:
            setattr(self, str(m), self._make(str(m)))

    def _make(self, method: str):
        def call(*args, **kwargs):
            comps, bodies = _solver_components(method, args, kwargs)
            e = self._r.next("solver", method, comps, bodies)
            return self._r.response(e)
        call.__name__ = method
        return call


class JournalBuilder:
    """Подставной построитель `(payload, args, out) -> bytes`: пишет
    `builder-payload.json` как штатный build, восстанавливает каталог и
    побочные файлы записи байт в байт; записанная остановка — ButtonStop
    теми же словами."""

    def __init__(self, replayer: JournalReplayer, wd: Path | None, *,
                 select_engine: str | None = None, label: str | None = None,
                 deterministic: bool = False):
        self._r = replayer
        self.wd = wd
        self.select_engine = select_engine
        self.label = label
        self.deterministic = bool(deterministic)

    def __call__(self, payload, args, out) -> bytes:
        out = Path(out)
        # Как штатный build: payload файлом ДО обращения к записи — при
        # рассинхроне входа отчёт называет builder-payload.json первым
        # различием с путём JSON, а не «файла нет у повтора».
        ppath = out / "builder-payload.json"
        ppath.write_text(json.dumps(payload, ensure_ascii=False),
                         encoding="utf-8")
        comps = _builder_components(payload, args, self.wd, self.select_engine,
                                    self.deterministic, self.label)
        e = self._r.next("pairing_builder", "build", comps,
                         bodies={"payload": canon(payload)})
        req = json.loads(read_body(self._r.dir / e["body"]).decode("utf-8"))
        own = req.get("own_files") if isinstance(req, dict) else {}
        own = own if isinstance(own, dict) else {}
        catalog = read_body(self._r.dir / e["catalog"]) if e.get("catalog") else None
        if own.get("builder-payload.json") not in ("canonical", "present"):
            # У источника файла не было (стендовый построитель) либо он
            # восстанавливается из побочных файлов ниже.
            ppath.unlink(missing_ok=True)
        for f in e.get("files") or []:
            if not isinstance(f, dict) or not f.get("file"):
                continue
            target = out / str(f["name"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read_body(self._r.dir / f["file"]))
        if catalog is not None and own.get("builder-catalog.json") == "canonical":
            (out / "builder-catalog.json").write_bytes(catalog)
        if isinstance(req, dict) and req.get("error"):
            _raise_recorded(req["error"])
        if catalog is None:
            raise ButtonStop("остановка: построитель пейринга отказал",
                             "в журнале нет каталога записи №%s" % e.get("n"))
        return catalog


class JournalLLM:
    """Подставной канал модели `(prompt) -> str`: ответ — байты обмена
    источника по отпечатку запроса.

    Расход: повтор из журнала модель НЕ зовёт, поэтому токены он не
    тратит. Канал объявляет `last_usage` со строкой «не считается» и
    причиной словами — провайдер и модель берутся из записи источника,
    чтобы в записи повтора было видно, ЧЕЙ ответ подставлен, но токены
    источника повтору не приписываются."""

    #: Причина, по которой у повтора расхода нет (не пробел записи).
    REPLAY_NOTE = "повтор из журнала — модель не вызывалась"

    def __init__(self, replayer: JournalReplayer, role: str = "certifier"):
        self._r = replayer
        self.role = role
        self.last_usage: dict | None = None

    def __call__(self, prompt: str) -> str:
        from vmp.run import llm_exchanges as lx
        comps = {"role": self.role, "request_sha256": lx.request_sha256(prompt)}
        e = self._r.next("llm", self.role, comps)
        src = e.get("usage") if isinstance(e.get("usage"), dict) else {}
        self.last_usage = {"provider": src.get("provider"),
                           "model": src.get("model"),
                           "channel": src.get("channel"),
                           "counted": False, "note": self.REPLAY_NOTE}
        if e.get("error"):
            raise RuntimeError("записанная ошибка канала модели: %s" % e["error"])
        return self._r.llm_reply(e)


# ------------------------------------------------------------ проверка полноты

def _tail_line(path: Path) -> dict | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    lines = [x for x in data.decode("utf-8", "replace").splitlines() if x.strip()]
    if not lines:
        return None
    try:
        row = json.loads(lines[-1])
    except ValueError:
        return None
    return row if isinstance(row, dict) else None


def verify_journal(directory: Path, record: dict, *, deep: bool = False,
                   exchanges_dir: Path | None = None) -> dict:
    """Полнота журнала попытки: лёгкая (`deep=False`: запись попытки,
    наличие двух файлов, хвост индекса) и полная (`deep=True`: тела и
    хеши, побочные файлы построителя, обмены с моделью по exchange_n,
    счётчик строк, отсутствие деградации).
    Итог: {"complete", "reason_code", "words", "counts"}."""
    directory = Path(directory)
    rec_j = record.get("journal") if isinstance(record, dict) else None
    counts = None
    if rec_j is None:
        return {"complete": False, "reason_code": "pre-journal",
                "words": "попытка записана до введения журнала внешних вызовов",
                "counts": None}
    if isinstance(rec_j, dict) and rec_j.get("present") is False:
        return {"complete": False,
                "reason_code": str(rec_j.get("reason_code") or "disabled-by-site"),
                "words": "журнал внешних вызовов не вёлся: %s"
                         % (rec_j.get("reason_code") or "выключен площадкой"),
                "counts": None}
    if isinstance(rec_j, dict):
        counts = rec_j.get("counts")
        if not rec_j.get("complete"):
            return {"complete": False, "reason_code": "journal-incomplete",
                    "words": "запись попытки: журнал неполон%s" % (
                        " — " + str(rec_j.get("degraded"))
                        if rec_j.get("degraded") else ""),
                    "counts": counts}
    man_p = directory / MANIFEST_NAME
    idx_p = directory / INDEX_NAME
    if not man_p.is_file() or not idx_p.is_file():
        return {"complete": False, "reason_code": "journal-incomplete",
                "words": "в снимке попытки нет %s" % (
                    ", ".join(n for n, p in ((MANIFEST_NAME, man_p),
                                             (INDEX_NAME, idx_p))
                              if not p.is_file())),
                "counts": counts}
    tail = _tail_line(idx_p)
    if not tail or tail.get("kind") != "end" or not tail.get("complete"):
        return {"complete": False, "reason_code": "journal-incomplete",
                "words": "индекс журнала не закрыт строкой end с complete=true",
                "counts": counts}
    if not deep:
        return {"complete": True, "reason_code": None, "words": None,
                "counts": counts}
    gaps: list[str] = []
    try:
        manifest = json.loads(read_body(man_p).decode("utf-8"))
    except Exception as e:                                 # noqa: BLE001
        return {"complete": False, "reason_code": "journal-incomplete",
                "words": "manifest.json не читается: %s" % str(e)[:200],
                "counts": counts}
    if manifest.get("degraded"):
        gaps.append("запись неполна: %s" % manifest["degraded"])
    if not manifest.get("complete"):
        gaps.append("manifest.complete = false")
    rows = _load_index(idx_p)
    calls = [r for r in rows if r.get("kind") != "end"]
    if int(tail.get("count") or 0) != len(calls):
        gaps.append("строк-вызовов %d, в строке end записано %s"
                    % (len(calls), tail.get("count")))
    replay_mode = manifest.get("mode") == "replay-journal"
    from vmp.run import llm_exchanges as lx
    ex_rows = lx.read_index(exchanges_dir) if exchanges_dir else []
    ex_by_n = {int(r.get("n")): r for r in ex_rows if r.get("n") is not None}
    for r in calls:
        n = r.get("n")
        kind = r.get("kind")
        if kind == "llm":
            xn = r.get("exchange_n")
            if exchanges_dir is None:
                gaps.append("обмены с моделью не записывались при %d вызовах модели"
                            % len([x for x in calls if x.get("kind") == "llm"]))
                break
            row = ex_by_n.get(int(xn)) if xn is not None else None
            if row is None:
                gaps.append("нет строки обмена №%s в llm-exchanges/index.jsonl" % xn)
                continue
            files = lx.exchange_files(exchanges_dir, int(xn), r.get("role") or "certifier")
            req = files.get("request")
            if req is None or sha256_bytes(req.read_bytes()) != r.get("request_sha256"):
                gaps.append("запрос обмена №%s не совпал по байтам" % xn)
            if r.get("reply_sha256"):
                rep = files.get("reply")
                if rep is None or sha256_bytes(rep.read_bytes()) != r.get("reply_sha256"):
                    gaps.append("ответ обмена №%s не совпал по байтам" % xn)
            elif "error" not in files and not r.get("error"):
                gaps.append("у обмена №%s нет ни ответа, ни ошибки" % xn)
            continue
        if replay_mode and r.get("served_from"):
            continue                # тел нет по построению — ссылки
        body = r.get("body")
        if not body:
            gaps.append("нет тела записи №%s (%s)" % (n, r.get("method")))
            continue
        p = directory / body
        if not p.is_file():
            gaps.append("нет тела записи №%s (%s)" % (n, r.get("method")))
            continue
        try:
            if sha256_bytes(read_body(p)) != r.get("body_sha256"):
                gaps.append("тело записи №%s (%s) не совпало с sha256"
                            % (n, r.get("method")))
        except Exception as e:                             # noqa: BLE001
            gaps.append("тело записи №%s не читается: %s" % (n, str(e)[:100]))
        for key in ("payload", "problem", "catalog"):
            if r.get(key):
                q = directory / r[key]
                if not q.is_file():
                    gaps.append("нет файла %s записи №%s" % (key, n))
                elif sha256_bytes(read_body(q)) != r.get(key + "_sha256"):
                    gaps.append("файл %s записи №%s не совпал с sha256" % (key, n))
        if kind == "pairing_builder":
            for f in r.get("files") or []:
                q = directory / str(f.get("file") or "")
                if not f.get("file") or not q.is_file():
                    gaps.append("нет побочного файла %s записи №%s"
                                % (f.get("name"), n))
                elif sha256_bytes(read_body(q)) != f.get("sha256"):
                    gaps.append("побочный файл %s записи №%s не совпал с sha256"
                                % (f.get("name"), n))
    if gaps:
        return {"complete": False, "reason_code": "journal-incomplete",
                "words": "; ".join(gaps)[:1500],
                "counts": manifest.get("counts") or counts}
    return {"complete": True, "reason_code": None, "words": None,
            "counts": manifest.get("counts") or counts}


def read_manifest(directory: Path) -> dict | None:
    p = Path(directory) / MANIFEST_NAME
    if not p.is_file():
        return None
    try:
        return json.loads(read_body(p).decode("utf-8"))
    except Exception:                                      # noqa: BLE001
        return None
