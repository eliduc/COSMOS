# -*- coding: utf-8 -*-
"""Обмены с моделью-сертификатором в снимке попытки нажатия (система
прогонов COSMOS, проект 07.09.2026, раздел 7.4).

Обёртка `wrap(llm, role, out, journal)` возвращает вызываемое той же
формы `(prompt) -> str`: на каждый вызов пишет запрос и ответ (либо
ошибку) файлами в каталог `out` и строку в `index.jsonl`; при заданном
журнале внешних вызовов (vmp.run.call_journal.CallJournal) дописывает в
него строку `kind: llm` с номером обмена — тела остаются здесь (одна
правда о байтах ответа модели).

Тела пишутся и читаются ТОЛЬКО байтами (`write_bytes`/`read_bytes`):
текстовый режим на Windows подменял бы `\\n` на `\\r\\n`, и отпечаток
файла разошёлся бы со строкой ответа модели, которая всегда несёт
переводы строк. Отпечатки `request_sha256`/`reply_sha256` — от этих байтов.

Каталог создаётся при первом вызове: у попытки без вызовов модели
каталога нет (код `no-model-calls`). Выключатель площадки
`CREW_LLM_EXCHANGES=0` — обёртка не ставится (код `disabled-by-site`);
в режимах повтора выключатель не действует — обмены повтора являются
его доказательством.

Расход модели (решение владельца № 1 от 10.09.2026): после каждого вызова
обёртка снимает разобранный usage канала — сперва атрибутом `last_usage`
самого канала (подставные каналы стендов и повтор из журнала), затем из
трубы `webapp.llm_backend` (та кладёт расход последнего вызова потока).
Строка индекса и строка журнала вызовов несут `usage` вызова, а
`resources(...)` сводит их по паре «провайдер + модель + канал» для записи
попытки. Денег обёртка не считает: цены на момент прогона и на момент
просмотра различаются.

Исключение канала модели пробрасывается как есть.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

INDEX_NAME = "index.jsonl"
DIR_NAME = "llm-exchanges"
ENV_SWITCH = "CREW_LLM_EXCHANGES"
#: Канал, не назвавший расхода вовсе (подставной канал стенда, повтор из
#: журнала): вызов посчитан, токены — нет, и причина стоит словами.
NO_USAGE_NOTE = "канал модели расхода не отдаёт"

#: Роли моделей COSMOS словами — один словарь на весь продукт (раздел
#: 3.1 `models[]` проекта «Запись о прогоне»). Ключ роли в именах файлов
#: обменов и в отпечатке вызова остаётся прежним («certifier»): его
#: сверяет повтор из журнала, и переименование обесценило бы записи
#: прежних попыток. Слова — только в своде расхода и в карточке.
ROLE_CODE_CERTIFIER = "certifier"
ROLE_CODE_SOURCE_READER = "source_check_reader"
ROLE_CERTIFIER = "сертификатор"
ROLE_SOURCE_READER = "читатель ИДАК"
ROLE_WORDS = {ROLE_CODE_CERTIFIER: ROLE_CERTIFIER,
              ROLE_CODE_SOURCE_READER: ROLE_SOURCE_READER}


def role_words(role: str | None) -> str | None:
    """Слова роли по её ключу; неизвестный ключ — как есть."""
    if role is None:
        return None
    return ROLE_WORDS.get(str(role), str(role))

log = logging.getLogger("cosmos.llm_exchanges")


def enabled() -> bool:
    """Выключатель площадки: умолчание — включено; `0`/`off`/`false`/`no`
    — обмены не пишутся (код `disabled-by-site`)."""
    got = (os.environ.get(ENV_SWITCH) or "").strip().lower()
    return got not in ("0", "off", "false", "no")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def request_sha256(prompt: str) -> str:
    """Отпечаток запроса — от байтов UTF-8 строки промпта (тот же, что у
    файла запроса на диске и у ключа подстановщика повтора)."""
    return _sha256(str(prompt).encode("utf-8"))


def file_names(n: int, role: str) -> dict:
    stem = "%03d-%s" % (n, role)
    return {"request": stem + ".request.txt", "reply": stem + ".reply.txt",
            "error": stem + ".error.txt"}


class Recorder:
    """Записывающая обёртка канала модели: вызываемое `(prompt) -> str`.

    `out_dir` — каталог обменов (создаётся при первом вызове);
    `journal` — журнал внешних вызовов попытки (необязательно).
    """

    def __init__(self, llm, out_dir: Path, role: str = "certifier",
                 journal=None, bodies: bool = True):
        self._llm = llm
        self.dir = Path(out_dir)
        self.role = str(role or "certifier")
        self.journal = journal
        #: bodies=False — обмены выключены площадкой (CREW_LLM_EXCHANGES=0):
        #: тела не пишутся, но журнал внешних вызовов считает вызовы модели
        #: (полнота журнала честно называет пробел словами).
        self.bodies = bool(bodies)
        self.count = 0
        self.errors = 0
        self.degraded: str | None = None
        self.closed = False
        self.complete: bool | None = None
        #: Расход по вызовам в порядке вызовов (строки словаря usage
        #: трубы каналов); сводка по моделям — `resources()`.
        self.usage: list[dict] = []

    # ---------------------------------------------------------- запись
    def _write(self, name: str, data: bytes) -> None:
        if not self.bodies:
            return
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / name).write_bytes(data)
        except OSError as e:
            words = "обмен %s не записан: %s" % (name, str(e)[:200])
            if self.degraded is None:
                self.degraded = words
            log.warning("llm-exchanges: %s", words)

    def _index(self, row: dict) -> None:
        if not self.bodies:
            return
        line = (json.dumps(row, ensure_ascii=False, sort_keys=True)
                + "\n").encode("utf-8")
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with (self.dir / INDEX_NAME).open("ab") as fh:
                fh.write(line)
        except OSError as e:
            words = "строка индекса обменов не записана: %s" % str(e)[:200]
            if self.degraded is None:
                self.degraded = words
            log.warning("llm-exchanges: %s", words)

    # ---------------------------------------------------------- расход
    def _clear_usage(self) -> None:
        """Сбросить расход ДО вызова — и у канала, и у трубы: несняток
        прошлого вызова не приписывается этому."""
        if getattr(self._llm, "last_usage", None) is not None:
            try:
                self._llm.last_usage = None
            except AttributeError:
                pass
        try:
            from webapp import llm_backend as _lb
            _lb.take_last_usage()
        except Exception:                                  # noqa: BLE001
            pass

    def _take_usage(self) -> dict | None:
        """Расход только что сделанного вызова: атрибут `last_usage`
        канала (подставные каналы стендов, повтор из журнала) старше
        трубы; ни того ни другого — None, и свод честно скажет
        «не считается» словами."""
        got = getattr(self._llm, "last_usage", None)
        if isinstance(got, dict):
            return dict(got)
        try:
            from webapp import llm_backend as _lb
            got = _lb.take_last_usage()
        except Exception:                                  # noqa: BLE001
            return None
        return dict(got) if isinstance(got, dict) else None

    def _note_usage(self, n: int, usage: dict | None) -> dict:
        """Строка расхода вызова для индекса, журнала и свода; канал без
        usage — «не считается» с причиной словами."""
        row = dict(usage) if isinstance(usage, dict) else {
            "provider": None, "model": None, "channel": None,
            "counted": False, "note": NO_USAGE_NOTE}
        row.setdefault("counted", False)
        row["n"] = n
        row["role"] = self.role
        self.usage.append(row)
        return row

    def __call__(self, prompt: str) -> str:
        self.count += 1
        n = self.count
        names = file_names(n, self.role)
        req = str(prompt).encode("utf-8")
        req_sha = _sha256(req)
        self._write(names["request"], req)
        self._clear_usage()
        t0 = time.monotonic()
        error = None
        reply_sha = None
        try:
            reply = self._llm(prompt)
        except BaseException as e:                         # noqa: BLE001
            elapsed = int((time.monotonic() - t0) * 1000)
            error = "%s: %s" % (type(e).__name__, str(e)[:800])
            self.errors += 1
            usage = self._note_usage(n, self._take_usage())
            self._write(names["error"], error.encode("utf-8"))
            self._index({"n": n, "role": self.role, "at": _now_iso(),
                         "request_sha256": req_sha, "reply_sha256": None,
                         "elapsed_ms": elapsed, "error": error,
                         "usage": usage})
            self._journal_line(n, req_sha, None, error, elapsed, usage)
            raise
        elapsed = int((time.monotonic() - t0) * 1000)
        usage = self._note_usage(n, self._take_usage())
        text = reply if isinstance(reply, str) else (
            "" if reply is None else str(reply))
        body = text.encode("utf-8")
        reply_sha = _sha256(body)
        self._write(names["reply"], body)
        self._index({"n": n, "role": self.role, "at": _now_iso(),
                     "request_sha256": req_sha, "reply_sha256": reply_sha,
                     "elapsed_ms": elapsed, "error": None, "usage": usage})
        self._journal_line(n, req_sha, reply_sha, None, elapsed, usage)
        return reply

    def _journal_line(self, n: int, req_sha: str, reply_sha: str | None,
                      error: str | None, elapsed: int,
                      usage: dict | None = None) -> None:
        if self.journal is None:
            return
        try:
            self.journal.record_llm(self.role, n, req_sha, reply_sha, error,
                                    elapsed, usage=usage)
        except TypeError:
            # Журнал прежней версии (без расхода) — строка без него.
            try:
                self.journal.record_llm(self.role, n, req_sha, reply_sha,
                                        error, elapsed)
            except Exception as e:                         # noqa: BLE001
                log.warning("llm-exchanges: строка журнала внешних вызовов "
                            "не записана: %s", e)
        except Exception as e:                             # noqa: BLE001
            log.warning("llm-exchanges: строка журнала внешних вызовов не "
                        "записана: %s", e)

    def resources(self) -> list[dict]:
        """Свод расхода попытки по паре «модель + канал» для записи
        попытки (`resources.models[]`): роль, провайдер, модель, канал,
        число вызовов и токены. Канал без usage — `counted: false` с
        причиной словами (решение владельца № 1: считаем токены, не
        деньги — цена берётся на момент просмотра). Вызовов не было —
        пустой перечень."""
        from webapp import llm_backend as _lb
        order: list[tuple] = []
        groups: dict[tuple, list[dict]] = {}
        for row in self.usage:
            key = (row.get("role") or self.role, row.get("provider"),
                   row.get("model"), row.get("channel"))
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)
        out: list[dict] = []
        for role, provider, model, channel in order:
            got = _lb.merge_usage(groups[(role, provider, model, channel)])
            out.append({"role": role_words(role), "role_code": role,
                        "provider": provider, "model": model,
                        "channel": channel, **got})
        return out

    # ---------------------------------------------------------- итог
    def close(self, complete: bool = True) -> None:
        """Последняя строка индекса `{"kind": "end", ...}` — только когда
        обмены были (каталог без вызовов не создаётся)."""
        if self.closed:
            return
        self.closed = True
        self.complete = bool(complete) and self.degraded is None
        if self.count == 0:
            return
        self._index({"kind": "end", "complete": self.complete,
                     "count": self.count, "at": _now_iso()})

    def summary(self, rel_dir: str) -> dict:
        """Запись для попытки (момент Н+): каталог относительно рабочего
        каталога, число обменов, полнота; без вызовов — `present: false`."""
        if not self.bodies:
            return {"present": False, "reason_code": "disabled-by-site",
                    "count": self.count}
        if self.count == 0:
            return {"present": False, "reason_code": "no-model-calls"}
        out = {"dir": rel_dir, "count": self.count,
               "complete": (self.complete if self.complete is not None
                            else self.degraded is None)}
        if self.errors:
            out["errors"] = self.errors
        if self.degraded:
            out["degraded"] = self.degraded
        return out


def wrap(llm, role: str = "certifier", out: Path | None = None,
         journal=None, bodies: bool = True) -> Recorder:
    """Обёртка канала модели: пишет обмены в `out` (каталог обменов
    попытки) и строки `kind: llm` в `journal`; bodies=False — только
    строки журнала (обмены выключены площадкой)."""
    if out is None:
        raise ValueError("wrap: каталог обменов out обязателен")
    return Recorder(llm, Path(out), role=role, journal=journal, bodies=bodies)


def read_index(out_dir: Path) -> list[dict]:
    """Строки `index.jsonl` (без строки end); каталога нет — пусто."""
    p = Path(out_dir) / INDEX_NAME
    if not p.is_file():
        return []
    rows: list[dict] = []
    for raw in p.read_bytes().decode("utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("kind") != "end":
            rows.append(row)
    return rows


def read_end(out_dir: Path) -> dict | None:
    """Строка end индекса обменов (последняя), либо None."""
    p = Path(out_dir) / INDEX_NAME
    if not p.is_file():
        return None
    lines = [x for x in p.read_bytes().decode("utf-8").splitlines() if x.strip()]
    if not lines:
        return None
    try:
        row = json.loads(lines[-1])
    except ValueError:
        return None
    return row if isinstance(row, dict) and row.get("kind") == "end" else None


def exchange_files(out_dir: Path, n: int, role: str) -> dict:
    """Файлы обмена `n` роли `role` в каталоге обменов (существующие)."""
    names = file_names(n, role)
    d = Path(out_dir)
    return {k: (d / v) for k, v in names.items() if (d / v).is_file()}


def reply_bytes(out_dir: Path, n: int, role: str) -> tuple[bytes | None, str | None]:
    """(байты ответа, слова ошибки) обмена `n`: ответ читается байтами."""
    files = exchange_files(out_dir, n, role)
    if "reply" in files:
        return files["reply"].read_bytes(), None
    if "error" in files:
        return None, files["error"].read_bytes().decode("utf-8", "replace")
    return None, None
