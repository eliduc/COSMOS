# -*- coding: utf-8 -*-
"""Запись о прогоне — половина CDPM (шаги 1 и 2 проекта «Запись о
прогоне», слово владельца 10.09.2026 «Начинай»; проект —
final-report/PROEKT-ZAPIS-O-PROGONE-CLD.md, словарь полей — один на обе
системы: `vmp/run_record.py` + `vmp/run_record.schema.json` у COSMOS;
CDPM от `vmp/**` не зависит и пишет запись по тем же именам; схему
читает ДАННЫМИ — `entry_problems`).

Что даёт модуль:
- `models_summary(run_dir)` — расход моделей по журналу вызовов
  (`journal/calls/index.jsonl`, поле `usage` записи вызова): строки
  роль × провайдер × модель × этап — вызовов, токены на вход/выход,
  кэш, рассуждение, машинное время; вызовы без сведений о расходе —
  отдельным счётчиком `not_counted_calls`, а не нулём (решение владельца:
  считать в ТОКЕНАХ по каждой модели; цены в запись не пишутся).
- `answers_revisions(run_dir)` — ревизии книг ответов с отпечатками и
  источником (книга набора / человек) — часть корня дерева прогонов.
- Гейт «Новый прогон» (шаг 2): словари объявления (`PURPOSES`,
  `ANSWER_POLICIES`, `PANEL_STAGES`), `words_problems` — слова вне
  словарей, `environment_problems` — среда необъявима (каталог,
  привязки CDPM, коммит кода). Гейт судит ДО рождения папки: отказ не
  оставляет ни папки, ни израсходованного номера.
- `declare(run_dir, declaration)` — голова прогона: `declaration.json`
  (полное объявление по словарю) и `run-record.json` в состоянии
  «объявлен», раньше заморозки и первого вызова модели. После старта
  голова неизменна: растут только ревизии ответов (в отпечаток
  объявления они не входят).
- `build(run_dir)` — запись целиком: голова из `declaration.json` (не
  реконструируется; у прогонов до шага 2 — из паспорта Р1–Р3 и
  заморозки), журнал исполнения (тело) из счётчиков этапов и журнала
  действий ПЛЮС чужие записи хранителя, итоги (хвост) из отметки
  доставки пакета и вердиктов чужих систем.
- `append(run_dir, system, entries, results)` — панель-ХРАНИТЕЛЬ
  принимает записи этапов чужой системы (COSMOS) в
  `journal/record-appends.jsonl`; идемпотентность по ключу
  (система, этап, подэтап).
- `stitch(run_dir)` — единая запись для ручки `GET …/record`: своя
  голова либо чужая (переходное правило «два летописца»), чужой журнал
  из файла и записи `append`.
- `refresh(run_dir)` — переписать `run-record.json` в папке прогона и
  положить `resources` в `status.json`; зовётся при закрытии каждого
  действия панели (журнал действий) и при выпуске пакета.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from cdpm import file_replace as _file_replace
from cdpm import delivery_mark as delivery_mark_mod

#: Версия формы записи (словарь COSMOS `cosmos-run-record/v1`).
RECORD_SCHEMA = "cosmos-run-record/v1"
RECORD_FILE = "run-record.json"
#: Голова записи, объявленная ДО старта (шаг 2): полное объявление и его
#: отпечаток. Хозяин головы — тот, кто объявил прогон (панель либо
#: раннер, передавший `record_run_id`); хранитель файла — панель.
DECLARATION_FILE = "declaration.json"
#: Чужие записи этапов и итогов, принятые хранителем (`append`).
APPENDS_FILE = ("journal", "record-appends.jsonl")
#: Версия формы тела `POST …/record/append` (договор с COSMOS,
#: раздел 11.2 проекта).
APPEND_SCHEMA = "cosmos-run-record-append/v1"
#: Схема словаря COSMOS — читается ДАННЫМИ (кодом `vmp/**` не
#: импортируется): по ней судятся присланные записи журнала.
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "vmp" / "run_record.schema.json"

#: Актор записи панели (`declaration.created_by`): по нему панель отличает
#: свою запись в папке прогона от чужой — раннер наборов объявляет прогон
#: до старта и пишет свою запись в ту же папку после заморозки (актор
#: «раннер наборов H0»). Хозяин файла — тот, кто объявил прогон.
PANEL_ACTOR = "панель CDPM"
#: Кто пишет голову записи в этой папке прогона.
PRODUCER = "cdpm"

#: Система-владелец роли в строке моделей ГОЛОВЫ (`declaration.models[].
#: system`, словарь COSMOS `SYSTEMS` = cdpm | runner | cosmos; договор
#: 11.09.2026). Имена повторены здесь, а не импортированы: CDPM от
#: `vmp/**` кодом не зависит и пишет запись по ТЕМ ЖЕ именам.
SYSTEM_CDPM = "cdpm"
SYSTEM_COSMOS = "cosmos"
#: Строки головы, которые судит сверка «объявлено / на деле» панели:
#: свои модели и модели записей ДО 11.09.2026, где системы не было
#: вовсе (её отсутствие словарь читает как `cdpm`).
_PANEL_SYSTEMS = (None, "", SYSTEM_CDPM)


REQUEST_WRITE_WINDOW_S = 0.25
_WRITE_BUDGET = ContextVar("cdpm_record_write_budget", default=None)


class RecordWriteBusy(PermissionError):
    """Interactive record write did not finish; no action is retried."""
    code = "record-refresh-busy"

    def __init__(self, path: Path):
        super().__init__(f"Файл записи {path.name} занят другим процессом. "
                         "Повторите только обновление записи позже; "
                         "само действие повторно не запускайте.")


@contextmanager
def request_write_budget(window_s: float | None = None):
    """A request shares one short record-write budget; None restores background.

    The mutable warning list crosses FastAPI's worker context copy. A stage
    explicitly enters None, even on runtimes which inherit thread contexts.
    This budget covers derived run-record replacement, not every operation in
    a request. Mandatory declaration writes retain the normal retry policy:
    refreshing a derived record cannot safely substitute for a declaration.
    """
    state = (None if window_s is None else
             {"window_s": max(0.0, float(window_s)), "deadline": None,
              "warnings": []})
    token = _WRITE_BUDGET.set(state)
    try:
        yield state["warnings"] if state is not None else []
    finally:
        _WRITE_BUDGET.reset(token)


def _read_json(path: Path):
    """Документ JSON как данные либо None (файла нет, не читается, не
    разбирается). Чтение — `cdpm.file_replace.read_bytes_shared` (на
    Windows FILE_SHARE_DELETE): запись о прогоне, голову и статус
    заменяет писатель с семантикой POSIX (`_write_json`,
    `webui._write_status`), и открытый читатель ему не мешает. Обычный
    `open()` ронял замену «Access is denied» (WinError 5), и обновление
    записи вместе с блоком `resources` статуса терялось (находка при
    разборе плавающего теста повтора, 12.09.2026). Миг замены на
    площадке без семантики POSIX (запасной `os.replace`) — чтение
    повторяется, как у манифеста журнала вызовов."""
    for attempt in range(6):
        try:
            return json.loads(_file_replace.read_bytes_shared(path)
                              .decode("utf-8"))
        except PermissionError:
            time.sleep(0.01 * (attempt + 1))
        except (OSError, ValueError):
            return None
    return None


def _sha256_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


# ------------------------------------------------------------------ токены
def _calls_index(run_dir: Path) -> list[dict]:
    p = Path(run_dir) / "journal" / "calls" / "index.jsonl"
    out: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    except OSError:
        return []
    return out


def models_summary(run_dir) -> dict:
    """Сводка расхода моделей: `{"models": [...], "calls": n,
    "counted_calls": n, "not_counted_calls": n, "unknown_calls": n}`.
    Строка сводки — роль × провайдер × модель × этап; провайдер и
    модель — из `usage` записи, при их отсутствии — из отпечатка канала
    (`identity`)."""
    rows: dict[tuple, dict] = {}
    calls = counted = not_counted = unknown = 0
    for rec in _calls_index(run_dir):
        if rec.get("served_from") == "journal" and not rec.get("usage"):
            # Повтор из журнала без расхода источника — не вызов модели.
            continue
        calls += 1
        usage = rec.get("usage") if isinstance(rec.get("usage"), dict) else None
        ident = rec.get("identity") if isinstance(rec.get("identity"), dict) else {}
        provider = (usage or {}).get("provider") or ident.get("provider")
        model = (usage or {}).get("model") or ident.get("model")
        key = (str(rec.get("role") or ""), str(provider or ""),
               str(model or ""), str(rec.get("stage") or ""))
        row = rows.setdefault(key, {
            "role": key[0], "provider": key[1] or None,
            "model": key[2] or None, "stage": key[3] or None,
            "calls": 0, "counted_calls": 0, "not_counted_calls": 0,
            "unknown_calls": 0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
            "thinking_tokens": 0, "elapsed_ms": 0, "errors": 0,
            "reasons": []})
        row["calls"] += 1
        row["elapsed_ms"] += int(rec.get("elapsed_ms") or 0)
        if rec.get("error"):
            row["errors"] += 1
        if usage is None:
            unknown += 1
            row["unknown_calls"] += 1
            continue
        if not usage.get("counted"):
            not_counted += 1
            row["not_counted_calls"] += 1
            reason = usage.get("reason")
            if reason and reason not in row["reasons"]:
                row["reasons"].append(str(reason))
            continue
        counted += 1
        row["counted_calls"] += 1
        for k in ("input_tokens", "output_tokens", "cache_read_tokens",
                  "cache_write_tokens", "thinking_tokens"):
            v = usage.get(k)
            if isinstance(v, int) and not isinstance(v, bool):
                row[k] += v
    models = [rows[k] for k in sorted(rows)]
    return {"unit": "tokens", "models": models, "calls": calls,
            "counted_calls": counted, "not_counted_calls": not_counted,
            "unknown_calls": unknown}


# ----------------------------------------------------------------- ответы
def _actions(run_dir: Path) -> list[dict]:
    p = Path(run_dir) / "journal" / "actions.jsonl"
    out: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    except OSError:
        return []
    return out


def _book_of_revision(rev_dir: Path, files: list[dict]) -> Path | None:
    """Книга ответов ревизии — первый файл `.xlsx` в её папке. Прочие
    файлы (пометки, выгрузки) книгой не считаются."""
    for f in files:
        name = str(f.get("name") or "")
        if name.lower().endswith(".xlsx"):
            return rev_dir / name
    return None


def _content_sha256(summary: dict, book: Path | None) -> str | None:
    """Отпечаток ревизии ответов ПО СОДЕРЖАНИЮ (пункт 11.7 проекта,
    канон — `cdpm/answers_fingerprint.py`): пары «вопрос → ответ», а не
    байты книги. Посчитанный на пути применения отпечаток лежит в
    сводке ревизии (`content_sha256`) — по нему книга второй раз не
    открывается; у ревизий, снятых до 11.7, он считается по книге.
    Книги нет («человек») либо она не читается — отпечатка нет."""
    got = summary.get("content_sha256")
    if isinstance(got, str) and len(got) == 64:
        return got
    if book is None:
        return None
    from cdpm import answers_fingerprint
    return answers_fingerprint.cached_book_content_sha256(book)


def answers_revisions(run_dir) -> list[dict]:
    """Ревизии книг ответов: `{revision, sha256, book_sha256, source,
    at, files[]}`. `sha256` — отпечаток СОДЕРЖАНИЯ ревизии (пары
    «вопрос → ответ», пункт 11.7: две книги с разными байтами и одними
    ответами дают один корень дерева); `book_sha256` — отпечаток байтов
    книги, он остаётся рядом для узнавания файла. Источник — «книга»
    (файл книги подан действием `answers` с файлами) либо «человек»
    (ответ введён на панели без файла); момент — из журнала действий,
    при его отсутствии — по файлу."""
    run_dir = Path(run_dir)
    by_rev: dict[int, dict] = {}
    adir = run_dir / "answers"
    if adir.is_dir():
        for d in sorted(adir.iterdir()):
            if not d.is_dir() or not d.name.startswith("rev-"):
                continue
            try:
                rev = int(d.name[4:])
            except ValueError:
                continue
            files = []
            for f in sorted(d.iterdir()):
                if f.is_file() and f.name != "answers-summary.json":
                    files.append({"name": f.name, "sha256": _sha256_file(f),
                                  "bytes": f.stat().st_size})
            summary = _read_json(d / "answers-summary.json") or {}
            book = _book_of_revision(d, files)
            by_rev[rev] = {"revision": rev,
                           "sha256": _content_sha256(summary, book),
                           "book_sha256": summary.get("book_sha256")
                           or (files[0]["sha256"] if files else None),
                           "source": "книга" if files else "человек",
                           "at": None, "files": files,
                           "applied": summary.get("applied"),
                           "unparsed": summary.get("unparsed")}
    # Момент и источник из журнала действий: по порядку действий `answers`.
    seq = [a for a in _actions(run_dir) if a.get("action") == "answers"]
    revs = sorted(by_rev)
    for rev, act in zip(revs, seq):
        by_rev[rev]["at"] = act.get("at")
        if act.get("files"):
            by_rev[rev]["source"] = "книга"
    return [by_rev[r] for r in revs]


# ------------------------------------------------------------------ запись
def _files_block(status: dict, run_dir: Path) -> list[dict]:
    """Файлы правил с отпечатками — из манифеста заморозки, иначе из
    журнала действия `create`."""
    man = _read_json(run_dir / "freeze" / "manifest.json") or {}
    out = []
    items = man.get("files") if isinstance(man.get("files"), list) else []
    for it in items:
        if isinstance(it, dict):
            # Манифест заморозки: {sid, file, sha256, clause_count}.
            out.append({"name": it.get("file") or it.get("name"),
                        "sha256": it.get("sha256"), "bytes": it.get("bytes"),
                        "sid": it.get("sid"),
                        "clauses": it.get("clause_count")})
    if out:
        return out
    for a in _actions(run_dir):
        if a.get("action") == "create":
            return [{"name": f.get("name"), "sha256": f.get("sha256"),
                     "bytes": f.get("bytes")} for f in (a.get("files") or [])
                    if isinstance(f, dict)]
    return [{"name": n, "sha256": None, "bytes": None}
            for n in (status.get("files") or [])]


def _data_files_block(status: dict, run_dir: Path) -> list[dict]:
    man = _read_json(run_dir / "data-freeze" / "manifest.json") or {}
    items = man.get("files") if isinstance(man.get("files"), list) else []
    # Манифест заморозки данных: {sid, file, sha256, rows_seen}.
    out = [{"name": it.get("file") or it.get("name"), "sha256": it.get("sha256"),
            "bytes": it.get("bytes"), "sid": it.get("sid"),
            "rows": it.get("rows_seen")} for it in items if isinstance(it, dict)]
    if out:
        return out
    return [{"name": n, "sha256": None, "bytes": None}
            for n in (status.get("data_files") or [])]


def _models_block(status: dict, run_dir: Path) -> list[dict]:
    """Раскладка моделей по ролям: из `stage_channels` статуса (запрошено
    / действует), иначе из agents.json."""
    out = []
    sc = status.get("stage_channels") if isinstance(status.get("stage_channels"), dict) else {}
    for key, rec in sorted(sc.items()):
        if not isinstance(rec, dict):
            continue
        role = key.split("/", 1)[0]
        eff = rec.get("effective") if isinstance(rec.get("effective"), dict) else {}
        out.append({"role": role, "stage": rec.get("stage"),
                    "provider": eff.get("provider") or rec.get("provider"),
                    "model": eff.get("model") or rec.get("model"),
                    "channel": eff.get("channel"),
                    "config_source": rec.get("config_source")})
    if out:
        return out
    agents = _read_json(run_dir / "agents.json") or {}
    for role, cfg in sorted(agents.items()) if isinstance(agents, dict) else []:
        if not isinstance(cfg, dict):
            continue
        chans = cfg.get("channels") if isinstance(cfg.get("channels"), dict) else {}
        for klass, ch in sorted(chans.items()):
            if isinstance(ch, dict):
                out.append({"role": role, "stage": None,
                            "provider": ch.get("provider"),
                            "model": ch.get("model"), "channel": klass,
                            "config_source": "agents.json"})
    return out


#: Действие панели → этап записи и ключи счётчиков статуса.
_ACTION_STAGES = {
    "create": ("freeze", "заморозка", "freeze_counters", "freeze_problems"),
    "data": ("data_freeze", "заморозка данных", "data_freeze_counters",
             "data_freeze_problems"),
    "data-run": ("data", "линия данных", "data_counters", "data_problems"),
    "extract": ("extract", "извлечение", "extract_counters", "extract_problems"),
    "full-cycle": ("exchange", "аудит-цикл", "exchange_counters",
                   "exchange_problems"),
    "answers": ("answers", "ответы", "answers_counters", "answers_problems"),
    "catalog-request": ("catalog_requests", "заявки в каталог", None, None),
    # Кнопка «Проверить непротиворечивость» и РУБЕЖ выпуска пакета
    # (11.09.2026) пишут одно действие «smt-verify» — и в записи о
    # прогоне читаются одной стадией, кем бы проверка ни была начата.
    "smt-verify": ("smt_verify", "SMT-проверка", None, None),
    "files-config": ("files_config", "политика файлов", None, None),
    "agents": ("agents", "раскладка ролей", None, None),
    "replay": ("replay", "повтор", None, None),
}


def action_stage_words(action) -> str | None:
    """Слова стадии действия панели («извлечение», «SMT-проверка») —
    те же, какими стадия называет себя в занятой линии статуса и в
    журнале каналов стадий. Действия нет в словаре — None: имя стадии
    не выдумывается.

    Читается журналом внешних вызовов: вызов синхронного действия
    (рубеж выпуска пакета, заявки в каталог) линию статуса не занимает,
    и назвать его стадию больше нечем — словарь действий здесь ОДИН на
    запись и на журнал вызовов."""
    row = _ACTION_STAGES.get(str(action or ""))
    return row[1] if row else None


#: Слова словаря COSMOS (vmp/run_record.py; закрытые перечни схемы).
OUTCOMES = ("успех", "предупреждение", "отказ по правилу", "сбой",
            "остановлен человеком", "не начат")
STATES = ("объявлен", "идёт", "завершён", "остановлен по правилу", "сбой",
          "остановлен человеком")
CONTOURS = ("local", "test", "stage", "prod")
ANSWER_SOURCE_BOOK = "книга набора"
ANSWER_SOURCE_HUMAN = "человек"


def _problems_count(v) -> int:
    if isinstance(v, list):
        return len(v)
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    return 0


def _int_or_none(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _resource_models_for_action(index: list[dict], n) -> list[dict]:
    """Расход вызовов одного действия (`action_n` записи вызова) —
    строки словаря `resource_model`: роль × провайдер × модель."""
    rows: dict[tuple, dict] = {}
    for r in index:
        if r.get("action_n") != n:
            continue
        if r.get("served_from") == "journal" and not r.get("usage"):
            continue
        u = r.get("usage") if isinstance(r.get("usage"), dict) else None
        ident = r.get("identity") if isinstance(r.get("identity"), dict) else {}
        provider = (u or {}).get("provider") or ident.get("provider")
        model = (u or {}).get("model") or ident.get("model")
        key = (str(r.get("role") or ""), str(provider or ""), str(model or ""))
        row = rows.setdefault(key, {
            "role": key[0] or None, "model": key[2] or None,
            "provider": key[1] or None, "calls": 0, "input_tokens": 0,
            "output_tokens": 0, "counted": True, "note": None,
            "_uncounted": 0, "_reasons": []})
        row["calls"] += 1
        if u and u.get("counted"):
            row["input_tokens"] += int(u.get("input_tokens") or 0)
            row["output_tokens"] += int(u.get("output_tokens") or 0)
        else:
            row["_uncounted"] += 1
            reason = (u or {}).get("reason") or "канал расход не сообщил"
            if reason not in row["_reasons"]:
                row["_reasons"].append(str(reason))
    out = []
    for key in sorted(rows):
        row = rows[key]
        unc, reasons = row.pop("_uncounted"), row.pop("_reasons")
        if unc:
            row["counted"] = unc < row["calls"]
            row["note"] = (f"не считается: {unc} из {row['calls']} вызовов — "
                           + "; ".join(reasons))
        out.append(row)
    return out


def journal_entries(run_dir, status: dict | None = None,
                    actor: str | None = None) -> list[dict]:
    """Журнал исполнения — СПИСОК записей этапов по журналу действий
    панели (словарь COSMOS `journal_entry`): `stage`, `substage`,
    `started_at`, `finished_at`, `duration_machine_s`,
    `duration_waiting_human_s`, `outcome`, `reason_code`, `words`,
    `actor`, `artifacts[]`, `counters{}`, `resources{}`. Ожидание
    человека — у действия `answers`: от закрытия предыдущего действия до
    подачи ответов. Действие без записи об окончании: последнее — ещё
    идёт и в журнал не попадает (попадёт закрытием), прочие — «сбой»
    (оборвано).

    `actor` — кто объявил прогон (`declaration.created_by`, раздел 11.5
    проекта): честно по объявлению. Личность каждого действия отдельно —
    когда у панели появится вход по личности."""
    run_dir = Path(run_dir)
    status = status if isinstance(status, dict) else (
        _read_json(run_dir / "status.json") or {})
    acts = _actions(run_dir)
    starts = [a for a in acts if a.get("kind") in ("start", "sync") and a.get("action")]
    ends = {a.get("n"): a for a in acts if a.get("kind") == "end"}
    refused = {a.get("n"): a for a in acts if a.get("kind") == "refused"}
    index = _calls_index(run_dir)
    out: list[dict] = []
    prev_end_at = None
    for a in starts:
        n = a.get("n")
        action = str(a.get("action"))
        stage, _title, ckey, pkey = _ACTION_STAGES.get(
            action, (action, action, None, None))
        end = ends.get(n)
        ref = refused.get(n)
        if end is None and ref is None and a.get("kind") == "sync":
            # Синхронное действие, записанное одной строкой (журналы до
            # 10.09.2026: загрузка книги данных без записи об окончании) —
            # закрытое, не оборванное: конец = момент записи, без
            # длительности. Живая находка run-0033 на Test.
            end = {"at": a.get("at"), "duration_s": None, "error": None}
        if ref is not None:
            outcome, reason_code, words = "отказ по правилу", str(ref.get("status")), \
                str(ref.get("detail") or "")[:300]
        elif end is None:
            if a is starts[-1]:
                continue                       # ещё идёт — запишется закрытием
            outcome, reason_code, words = "сбой", "aborted", \
                "действие оборвано: записи об окончании нет"
        elif end.get("error"):
            outcome, reason_code, words = "сбой", "error", str(end.get("error"))[:300]
        else:
            problems = _problems_count(status.get(pkey)) if pkey else 0
            outcome = "предупреждение" if problems else "успех"
            reason_code, words = ("problems" if problems else None), None
        finished_at = (end or ref or {}).get("at")
        waiting = None
        if action == "answers" and prev_end_at and a.get("at"):
            waiting = _seconds_between(prev_end_at, a.get("at"))
        artifacts = [{"kind": "file", "path": f.get("stored") or f.get("name"),
                      "sha256": f.get("sha256")}
                     for f in (a.get("files") or []) if isinstance(f, dict)]
        duration = (end or {}).get("duration_s")
        out.append({
            "stage": stage, "substage": action,
            "started_at": a.get("at"), "finished_at": finished_at,
            "duration_machine_s": (float(duration) if isinstance(duration, (int, float))
                                   and duration >= 0 else None),
            "duration_waiting_human_s": waiting,
            "outcome": outcome, "reason_code": reason_code, "words": words,
            # 11.5: актор — кто объявил прогон; личность действия — когда
            # у панели появится вход по личности.
            "actor": actor,
            "artifacts": artifacts,
            "counters": (dict(status.get(ckey)) if ckey and isinstance(status.get(ckey), dict)
                         else {}),
            "resources": {"models": _resource_models_for_action(index, n),
                          "solver_seconds": None},
            # Система-летописец (словарь COSMOS 17063f3c): записи панели.
            "system": "cdpm",
        })
        if finished_at:
            prev_end_at = finished_at
    return out


def _seconds_between(a: str, b: str) -> float | None:
    from datetime import datetime
    try:
        ta = datetime.fromisoformat(str(a))
        tb = datetime.fromisoformat(str(b))
    except ValueError:
        return None
    sec = round((tb - ta).total_seconds(), 3)
    return sec if sec >= 0 else None


def canonical(obj) -> bytes:
    """Канонический JSON словаря COSMOS: sort_keys, без пробелов, UTF-8."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _shas(items) -> list[str]:
    return sorted(str(x.get("sha256")) for x in items
                  if isinstance(x, dict) and x.get("sha256"))


def root_sha256(rules: list[dict], data: list[dict]) -> str:
    """Отпечаток КОРНЯ дерева прогонов по документам правил и данным —
    известен ДО старта (алгоритм словаря COSMOS 13:3x): sha256
    канонического JSON {"rules": sorted sha, "data": sorted sha}. Ветви
    одного корня — прогоны с одним root_sha256 и разными объявлениями."""
    return hashlib.sha256(canonical(
        {"rules": _shas(rules), "data": _shas(data)})).hexdigest()


def root_answers_sha256(rules: list[dict], data: list[dict],
                        answers: list[dict]) -> str:
    """Корень дерева С ОТВЕТАМИ на анкеты (решение владельца 10.09:
    корень — ИДАК, данные и ответы): ставится, когда ответы закрыты
    (у наборов с книгой ответов — сразу)."""
    return hashlib.sha256(canonical(
        {"rules": _shas(rules), "data": _shas(data),
         "answers": _shas(answers)})).hexdigest()


def declaration_sha256(declaration: dict) -> str:
    """Отпечаток объявления — РОВНО по словарю COSMOS
    (`vmp.run_record.declaration_sha256`, `DECLARATION_SHA_EXCLUDED =
    ("answers",)`): без ревизий ответов (растут по ходу) и без самого
    отпечатка; `created_at` входит. Ничего сверх словаря из отпечатка не
    выводится — иначе пересчёт той же головы кодом COSMOS давал бы другое
    число. Поэтому `lineage.root_answers_sha256` у объявленной панелью
    головы стоит null и после старта не меняется: ответы к моменту
    объявления неизвестны, корень с ответами читатели дерева считают по
    `answers[]` записи (функция словаря `root_answers_sha256`)."""
    body = {k: v for k, v in declaration.items()
            if k not in ("declaration_sha256", "answers")}
    return hashlib.sha256(canonical(body)).hexdigest()


def _timing(entries: list[dict]) -> dict:
    machine = sum(float(e.get("duration_machine_s") or 0) for e in entries)
    waiting = sum(float(e.get("duration_waiting_human_s") or 0) for e in entries)
    return {"machine_s": round(machine, 3), "waiting_human_s": round(waiting, 3),
            "total_s": round(machine + waiting, 3)}


def _catalog_path() -> str | None:
    """Canonical local location when minting a declaration, not when reading it.

    F-1.7 / CDPM-426: honour the bridge's empty-setting rule and the reader's
    directory/header/version selectors. A single-file export keeps its own
    location; saved declarations never pass through this function again.
    """
    from cdpm.catalog_bridge import catalog_path
    from template_catalog import reader

    raw = catalog_path()
    if raw is None:
        return None
    if reader.SEMVER_RE.match(raw):
        path = reader._resolve_version(raw)
    else:
        path = Path(raw)
        # Match reader.load: only an existing header file denotes its folder.
        if path.is_file() and path.name == "catalog.json":
            path = path.parent
    return path.resolve().as_posix()


def _contour() -> dict:
    name = (os.environ.get("CREW_CONTOUR") or "local").strip().lower()
    if name not in CONTOURS:
        name = "local"
    services = {}
    for env, key in (("CDPM_CATALOG_SERVICE_URL", "catalog_service"),
                     ("CREW_CHANNEL_URL", "cosmos_channel"),
                     ("CREW_CATALOG_URL", "catalog_links")):
        v = (os.environ.get(env) or "").strip()
        if v:
            services[key] = v
    return {"name": name, "services": services}


def _rules_documents(status: dict, run_dir: Path) -> list[dict]:
    """`rules_document {name, kind, sha256, size}`: размер — из журнала
    действия `create` (байты загрузки), отпечаток — оттуда же либо из
    манифеста заморозки."""
    by_name: dict[str, dict] = {}
    for a in _actions(run_dir):
        if a.get("action") == "create":
            for f in (a.get("files") or []):
                if isinstance(f, dict) and f.get("name"):
                    by_name[str(f["name"])] = {"sha256": f.get("sha256"),
                                               "size": _int_or_none(f.get("bytes"))}
            break
    for it in _files_block(status, run_dir):
        name = it.get("name")
        if not name:
            continue
        rec = by_name.setdefault(str(name), {"sha256": None, "size": None})
        rec["sha256"] = rec.get("sha256") or it.get("sha256")
    out = []
    for name in sorted(by_name):
        suffix = Path(name).suffix.lower().lstrip(".")
        out.append({"name": name, "kind": suffix or None,
                    "sha256": by_name[name].get("sha256"),
                    "size": by_name[name].get("size")})
    return out


def _data_files(status: dict, run_dir: Path) -> list[dict]:
    """`data_file {name, sheets, rows, sha256}`: строки — из манифеста
    заморозки данных (`rows_seen`), листы — из счётчика заморозки, когда
    файл один."""
    man = _read_json(run_dir / "data-freeze" / "manifest.json") or {}
    items = [it for it in (man.get("files") or []) if isinstance(it, dict)]
    sheets_total = _int_or_none((status.get("data_freeze_counters") or {}).get("sheets")) \
        if isinstance(status.get("data_freeze_counters"), dict) else None
    out = []
    for it in items:
        out.append({"name": str(it.get("file") or it.get("name") or ""),
                    "sheets": sheets_total if len(items) == 1 else None,
                    "rows": _int_or_none(it.get("rows_seen")),
                    "sha256": it.get("sha256")})
    if out:
        return out
    return [{"name": str(n), "sheets": None, "rows": None, "sha256": None}
            for n in (status.get("data_files") or [])]


def _answers(run_dir: Path) -> list[dict]:
    out = []
    for r in answers_revisions(run_dir):
        out.append({"revision": r.get("revision"), "sha256": r.get("sha256"),
                    "source": (ANSWER_SOURCE_BOOK if r.get("source") == "книга"
                               else ANSWER_SOURCE_HUMAN if r.get("source") == "человек"
                               else None),
                    "at": r.get("at")})
    return out


def _models(status: dict, run_dir: Path) -> list[dict]:
    """`model {role, model, provider, channel, effort}` — раскладка по
    ролям: действующие каналы этапов (`stage_channels`), усилие — из
    agents.json роли, если названо."""
    agents = _read_json(run_dir / "agents.json") or {}
    efforts: dict[str, str | None] = {}
    if isinstance(agents, dict):
        for role, cfg in agents.items():
            if isinstance(cfg, dict):
                eff = cfg.get("effort")
                if eff is None:
                    chans = cfg.get("channels") if isinstance(cfg.get("channels"), dict) else {}
                    for ch in chans.values():
                        if isinstance(ch, dict) and ch.get("effort"):
                            eff = ch.get("effort")
                            break
                efforts[str(role)] = str(eff) if eff else None
    out = []
    seen = set()
    for row in _models_block(status, run_dir):
        key = (row.get("role"), row.get("model"), row.get("provider"), row.get("channel"))
        if key in seen:
            continue
        seen.add(key)
        out.append({"role": str(row.get("role") or ""), "model": row.get("model"),
                    "provider": row.get("provider"), "channel": row.get("channel"),
                    "effort": efforts.get(str(row.get("role") or ""))})
    return out


#: Переменная раннера COSMOS с путём к привязкам (vmp/run_passport.py).
BINDINGS_ENV = "CREW_COSMOS_BINDINGS"
#: Поля строки привязок сверх трёх обязательных (§12.11 п. 5): ЧЕМ
#: судили поддержку — конкретные байты файла и версия продукта,
#: выпустившего его. Имена — те же, что в симметричной правке паспорта
#: COSMOS (`vmp/run_passport.bindings_block`).
BINDINGS_EXTRA = ("sha256", "product_version", "generated_from")
#: Имя вложенной строки о привязках ВТОРОГО продукта (CDPM).
BINDINGS_CDPM_KEY = "cdpm"
#: ЗАКРЫТЫЙ перечень имён, которые CDPM пишет в строку `bindings`:
#: сверх трёх обязательных панель не выдумывает имён, а берёт их из
#: общего словаря объявления (см. `_bindings`).
BINDINGS_KEYS = (("catalog_version", "catalog_fingerprint", "path")
                 + BINDINGS_EXTRA + (BINDINGS_CDPM_KEY,))


def _bindings_facts(paths) -> dict:
    """Факты одного файла привязок: `{catalog_version,
    catalog_fingerprint, path, sha256, product_version,
    generated_from}` по первому из `paths`, который читается и назвался
    каталогом. Нет файла — все поля пусты (паспорт не выдумывает)."""
    for p in paths:
        b = _read_json(p) if p.is_file() else None
        if not isinstance(b, dict) or not (b.get("catalog_version")
                                           or b.get("catalog_fingerprint")):
            continue
        try:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:                                   # pragma: no cover
            digest = None
        return {"catalog_version": b.get("catalog_version"),
                "catalog_fingerprint": b.get("catalog_fingerprint"),
                "path": p.resolve().as_posix(), "sha256": digest,
                "product_version": b.get("product_version"),
                "generated_from": b.get("generated_from")}
    return {"catalog_version": None, "catalog_fingerprint": None,
            "path": None, "sha256": None, "product_version": None,
            "generated_from": None}


def bindings_evidence() -> dict:
    """Привязки ОБОИХ продуктов, которыми судится поддержка записи
    каталога (K06-барьер, контракт П0 §12.11 п. 5: «паспорт = рецепт» —
    строка обязана называть конкретные байты): `{"COSMOS": {…},
    "CDPM": {…}}`, у каждого — путь, SHA-256 содержимого,
    версия/отпечаток каталога и версия продукта.

    Файлы привязок — ДАННЫЕ продуктов (как каталог): CDPM читает их по
    пути площадки (`CREW_COSMOS_BINDINGS` / `CDPM_BINDINGS_FILE`) либо
    из дерева рядом с пакетом, `vmp/**` не импортируя."""
    repo = Path(__file__).resolve().parents[1]
    out = {}
    for product, env_name, parts in (
            ("COSMOS", BINDINGS_ENV, ("vmp", "bindings.json")),
            ("CDPM", CDPM_BINDINGS_ENV, ("cdpm", "bindings.json"))):
        cands = []
        envp = (os.environ.get(env_name) or "").strip()
        if envp:
            cands.append(Path(envp))
        cands.append(repo.joinpath(*parts))
        out[product] = _bindings_facts(cands)
    return out


def _declaration_names(ref: str) -> set:
    """Имена, которые словарь объявления COSMOS (`vmp/
    run_record.schema.json`, читается ДАННЫМИ) допускает у части `ref`.
    Словарь закрыт (`additionalProperties: false`), схемы в установке
    может не быть — тогда имён не знаем и пишем прежние три."""
    schema = _read_json(SCHEMA_PATH)
    part = ((schema or {}).get("$defs") or {}).get(ref) \
        if isinstance(schema, dict) else None
    props = (part or {}).get("properties") if isinstance(part, dict) else None
    return set(props) if isinstance(props, dict) else set()


def _bindings() -> dict:
    """Строка объявления `bindings` — привязки, под которые выпущен
    каталог прогона: три поля о файле COSMOS (`catalog_version`,
    `catalog_fingerprint`, `path`) плюс — КОГДА ИХ ЗНАЕТ ОБЩИЙ СЛОВАРЬ —
    `sha256`, `product_version`, `generated_from` того же файла и
    вложенная строка `cdpm` о втором носителе поддержки
    (`cdpm/bindings.json`).

    ШОВ (§12.11 п. 5): словарь объявления один на обе системы и
    закрыт (`additionalProperties: false` у `$defs/bindings`), а
    необязательные имена в него добавляет СИММЕТРИЧНАЯ правка COSMOS
    (`vmp/run_record.schema.json`, `vmp/run_passport.bindings_block`) —
    зона CDPM её не трогает. Поэтому строка объявления следует словарю:
    как только имена в нём появятся, паспорт понесёт байты привязок
    обоих продуктов без единой правки здесь; до того запись остаётся
    годной по словарю, а полные факты доступны `bindings_evidence()`
    (их читает сверка «объявлено / на деле»)."""
    facts = bindings_evidence()
    allowed = _declaration_names("bindings")
    out = {"catalog_version": facts["COSMOS"]["catalog_version"],
           "catalog_fingerprint": facts["COSMOS"]["catalog_fingerprint"],
           "path": facts["COSMOS"]["path"]}
    # Необязательные имена пишутся только со ЗНАЧИМЫМ значением: `null`
    # в голове записи развёл бы отпечаток «объявлено при старте» с
    # пересчётом панели (у COSMOS те же имена — в OMITTED_WHEN_EMPTY).
    for key in BINDINGS_EXTRA:
        if key in allowed and facts["COSMOS"].get(key) is not None:
            out[key] = facts["COSMOS"][key]
    if BINDINGS_CDPM_KEY in allowed:
        cdpm = {k: v for k, v in facts["CDPM"].items() if v is not None}
        if cdpm:
            out[BINDINGS_CDPM_KEY] = cdpm
    return out


def _libraries(env: dict) -> dict:
    pk = env.get("packages") if isinstance(env.get("packages"), dict) else {}
    packages = [{"name": str(k), "version": str(v)} for k, v in sorted(pk.items())]
    return {"python": env.get("python"),
            "packages_sha256": (hashlib.sha256(canonical(packages)).hexdigest()
                                if packages else None),
            "packages": packages}


def _record_state(status: dict, entries: list[dict], delivered: bool,
                  foreign: list[dict] | tuple = ()) -> str:
    """Состояние прогона по журналу. Чужие исходы (записи `append`
    другой системы) СТАРШЕ панельных: расчёт COSMOS упал — прогон в
    сбое, даже если панель свою часть закончила. Порядок старшинства:
    сбой > остановлен человеком > отказ по правилу > правило панели."""
    outcomes = [e.get("outcome") for e in foreign if isinstance(e, dict)]
    if "сбой" in outcomes:
        return "сбой"
    if "остановлен человеком" in outcomes:
        return "остановлен человеком"
    if "отказ по правилу" in outcomes:
        return "остановлен по правилу"
    st = str(status.get("state") or "")
    # Отказ по правилу у ПОСЛЕДНЕГО действия останавливает прогон, только
    # пока пакет не доставлен: отказанное нажатие кнопки (золотой, повтор,
    # заявка) на завершённом прогоне права менять его состояние не имеет
    # (находка исполнителя шага 3, 10.09.2026).
    if entries and entries[-1].get("outcome") == "отказ по правилу" \
            and not delivered:
        return "остановлен по правилу"
    if "сбой" in st or "отказ" in st:
        return "сбой"
    if delivered:
        return "завершён"
    return "идёт" if entries else "объявлен"


def _legacy_declaration(run_dir: Path, status: dict) -> dict:
    """Голова прогона, рождённого ДО гейта «Новый прогон» (шаг 2):
    штамп при создании — паспорт Р1–Р3, заморозка, раскладка ролей.
    Прогоны с `declaration.json` этой ветки не знают: их голова
    объявлена до старта и не реконструируется."""
    passport = status.get("passport") if isinstance(status.get("passport"), dict) else {}
    env = passport.get("environment") if isinstance(passport.get("environment"), dict) else {}
    catalog = env.get("catalog") if isinstance(env.get("catalog"), dict) else {}
    code = env.get("code") if isinstance(env.get("code"), dict) else {}
    kit = status.get("kit") if isinstance(status.get("kit"), dict) else {}
    rep = passport.get("replay") if isinstance(passport.get("replay"), dict) else {}
    rules = _rules_documents(status, run_dir)
    data = _data_files(status, run_dir)
    answers = _answers(run_dir)
    sources = {a.get("source") for a in answers}
    declaration = {
        "run_id": run_dir.name,
        "created_at": _stable_created_at(run_dir, status, passport),
        "created_by": PANEL_ACTOR,              # актор действия — шаг 2 (журнал)
        "purpose": (status.get("purpose") if status.get("purpose") in
                    ("приёмка", "демонстрация", "регресс", "исследование", "рабочий")
                    else ("регресс" if kit else "рабочий")),
        "contour": _contour(),
        "airline": {"name": status.get("customer"),
                    "data_class": status.get("data_class") or kit.get("data_class"),
                    "llm_scope": status.get("scope") or kit.get("llm_scope")},
        "rules_documents": rules,
        "data_files": data,
        "answers": answers,
        "catalog": {"name": "template-catalog",
                    "version": catalog.get("version"),
                    "fingerprint": catalog.get("fingerprint"),
                    "path": _catalog_path()},
        "catalog_extension": None,
        "bindings": _bindings(),
        "models": _models(status, run_dir),
        "solvers": [],                          # решатели — у COSMOS
        "code": {"cdpm_commit": code.get("commit"),
                 "cosmos_commit": code.get("commit"),   # одно дерево
                 "runner_fingerprint": None},
        "libraries": _libraries(env),
        "planned_stages": [],                   # план этапов — шаг 2 (гейт)
        "stop_after": None,
        "expectations": {"kind": None, "ref": None},
        "lineage": {
            "root_sha256": root_sha256(rules, data),
            # Корень с ответами у реконструированной головы не ставится:
            # ответы растут по ходу, а голова (и её отпечаток) обязана
            # быть устойчивой; читатели дерева считают его по answers[]
            # функцией словаря root_answers_sha256.
            "root_answers_sha256": None,
            "parent_run": str(rep["source_run"]) if rep.get("source_run") else None,
            "branch_of": ("replay:" + str(rep.get("mode"))) if rep.get("source_run") else None,
            # Пакет-ВХОД частичного прогона (у COSMOS); у полного прогона
            # панели пакет — выдача (results.deliveries), а не происхождение:
            # иначе отпечаток объявления менялся бы после отправки пакета и
            # расходился с declaration_sha256 в манифесте.
            "package_sha256": None,
            "source_run": str(rep["source_run"]) if rep.get("source_run") else None,
        },
        "answer_policy": ("книга" if ANSWER_SOURCE_BOOK in sources
                          else "человек" if ANSWER_SOURCE_HUMAN in sources else None),
    }
    return declaration


def build(run_dir) -> dict:
    """Запись целиком по словарю COSMOS `cosmos-run-record/v1`
    (vmp/run_record.schema.json — закрытые словари, ничего сверх).

    Голова — из `declaration.json`, если он есть (объявление ДО старта,
    шаг 2): она НЕ реконструируется, растут только ревизии ответов и
    производный от них корень дерева. Тело — журнал панели плюс чужие
    записи хранителя (`append`), итоги — вердикты панели, доставки и
    вердикты чужих систем."""
    run_dir = Path(run_dir)
    status = _read_json(run_dir / "status.json") or {}
    delivery = delivery_mark_mod.read_current(run_dir)
    head = read_declaration(run_dir)
    if head is not None:
        declaration = _declared_head(head["declaration"], run_dir)
    else:
        declaration = _legacy_declaration(run_dir, status)
    entries = journal_entries(run_dir, status,
                              actor=declaration.get("created_by"))
    joined = appended(run_dir)
    journal = _merge_journal(entries, joined["entries"])
    delivered = bool(delivery and delivery.get("sent"))
    verdicts = {"cdpm_state": status.get("state"),
                "data_state": status.get("data_state"),
                "package": (delivery.get("verdict") if delivery else None),
                "readiness_matrix": (delivery.get("mg") if delivery else None),
                "cosmos_run": (delivery.get("cosmos_run") if delivery else None)}
    # Вердикты чужой системы — под её именем (`verdicts.cosmos`): словарь
    # вердиктов свободный, но панельные ключи чужая система не занимает.
    verdicts.update(joined["verdicts"])
    deliveries = ([{"kind": "task-package", "path": "channel/task-package.zip",
                    "sha256": delivery.get("sha")}] if delivered else [])
    for d in joined["deliveries"]:
        if not _has_delivery(deliveries, d):
            deliveries.append(d)
    results = {
        "verdicts": verdicts,
        "deliveries": deliveries,
        "comparison": {"expectations": dict(joined["expectations"] or {}),
                       "previous_run": {}, "golden": {}},
        # Признак золотого прогона — из журнала приписок хранителя
        # (12.2): голова неизменна, а `run-record.json` панель
        # пересобирает после каждого действия — в файле признаку не
        # удержаться (см. cdpm/run_tree_panel.py).
        "golden": joined["golden"] or empty_golden(),
        "timing": _timing(journal),
        "state": _record_state(status, entries, delivered,
                               joined["entries"]),
    }
    record = {"schema": RECORD_SCHEMA, "declaration": declaration,
              "declaration_sha256": declaration_sha256(declaration),
              "journal": journal, "results": results}
    results["comparison"] = _comparison(run_dir, record)
    return record


def _comparison(run_dir: Path, record: dict) -> dict:
    """Сравнение записи с предыдущим прогоном и с золотым (12.4): при
    ЗАКРЫТИИ записи (состояние «завершён») хранитель кладёт в неё
    `{run_id, words[]}` по индексу дерева — карточка показывает их без
    сборки дерева.

    Ожидания набора (`expectations`) пишет РАННЕР через `record/append`
    — панель их только хранит. Отказ дерева запись не роняет: сравнения
    просто нет, и оно появится после ближайшей перестройки индекса."""
    out = dict((record.get("results") or {}).get("comparison") or {})
    out.setdefault("expectations", {})
    out.setdefault("previous_run", {})
    out.setdefault("golden", {})
    if (record.get("results") or {}).get("state") != STATES[2]:
        return out
    try:
        from cdpm import run_tree_panel
        got = run_tree_panel.comparison(Path(run_dir).parent, record, run_dir)
    except Exception:                                     # noqa: BLE001
        return out
    out["previous_run"] = got.get("previous_run") or {}
    out["golden"] = got.get("golden") or {}
    return out


def _now_with_zone() -> str:
    from datetime import datetime
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _stable_created_at(run_dir: Path, status: dict, passport: dict) -> str:
    """Момент рождения реконструированной головы — УСТОЙЧИВЫЙ, не часы
    (находка COSMOS 10.09.2026 19:4x: у папки без status.json
    `created_at` брался с часов, отпечаток объявления — а с ним и
    манифест пакета — плыл через границу секунды, ломая байт-идентичность
    повторного экспорта, Ф-1.7). Порядок: статус → паспорт → первое
    действие журнала → время рождения папки; двух вызовов над одной папкой
    с разным итогом быть не может."""
    for candidate in (status.get("created_at"), passport.get("at")):
        if candidate:
            return str(candidate)
    acts = _actions(run_dir)
    for a in acts:
        if a.get("at"):
            return str(a["at"])
    from datetime import datetime
    try:
        st = Path(run_dir).stat()
        born = getattr(st, "st_birthtime", None) or st.st_ctime
        return (datetime.fromtimestamp(born).astimezone()
                .replace(microsecond=0).isoformat())
    except OSError:
        return "1970-01-01T00:00:00+00:00"


def on_disk(run_dir) -> dict | None:
    """Запись, лежащая в папке прогона, как данные: словарь со схемой
    записи, номером прогона в объявлении и отпечатком объявления. Файл
    другой формы (мусор, обрывок) записью не считается."""
    rec = _read_json(Path(run_dir) / RECORD_FILE)
    if not isinstance(rec, dict) or rec.get("schema") != RECORD_SCHEMA:
        return None
    d = rec.get("declaration")
    if not isinstance(d, dict) or not d.get("run_id") \
            or not rec.get("declaration_sha256"):
        return None
    return rec


def foreign_record(run_dir) -> dict | None:
    """Чужая запись в папке прогона — с ГОЛОВОЙ не того, кто объявил
    прогон в этой папке: номер записи в файле не равен номеру
    объявления (`declaration.json`), а у прогона без объявления — имени
    папки при акторе панели.

    Два летописца одного прогона (живая находка run-0129..0131 на
    6eebd870): раннер наборов объявляет прогон ДО старта и после
    заморозки пишет свою запись в папку прогона; панель переписывала её
    после каждого действия, и отпечаток объявления в манифесте пакета
    (панельный, снят до выпуска) расходился с файлом (раннерским,
    записан после). Хозяин файла — тот, кто объявил прогон: чужую запись
    панель не трогает, свою половину она строит по журналам в любой
    момент (`build`), а единую запись отдаёт сшивкой (`stitch`).
    Переходное правило шага 2 (11.3): когда раннер объявит прогон через
    панель (`record_run_id`), номер файла совпадёт с номером объявления
    — и панель станет ХРАНИТЕЛЕМ файла при чужом хозяине головы."""
    rec = on_disk(run_dir)
    if rec is None:
        return None
    d = rec["declaration"]
    head = read_declaration(run_dir)
    if head is not None:
        own = str(head["declaration"].get("run_id"))
        return None if str(d.get("run_id")) == own else rec
    if d.get("created_by") == PANEL_ACTOR and d.get("run_id") == Path(run_dir).name:
        return None
    return rec


def refresh(run_dir, update_status=None) -> dict:
    """Переписать `run-record.json` (если файл не занят чужой записью —
    см. `foreign_record`) и (по желанию) отдать панели блок `resources`
    для статуса — богатую сводку роль × провайдер × модель × этап
    (`models_summary`), которая шире словаря записи. Возвращает единую
    запись (сшивку) после вызова."""
    run_dir = Path(run_dir)
    rec = build(run_dir)
    foreign = foreign_record(run_dir)
    if foreign is None:
        _write_json(run_dir / RECORD_FILE, rec)
    if update_status is not None:
        update_status(models_summary(run_dir))
    return rec if foreign is None else _stitch_foreign(run_dir, foreign)


def record_id_of(run_dir) -> str:
    """Номер записи для манифеста пакета (`record_id`): номер ОБЪЯВЛЕНИЯ
    — чужой записи из папки прогона (переходное правило) либо головы,
    объявленной до старта (у прогона раннера это его номер, даже когда
    файл ведёт панель-хранитель); прогон без объявления — имя папки.
    Имя прогона панели пакет всегда несёт в `source_run` (при повторе из
    журнала — имя источника)."""
    foreign = foreign_record(run_dir)
    if foreign is not None:
        return str(foreign["declaration"]["run_id"])
    head = read_declaration(run_dir)
    if head is not None:
        return str(head["declaration"]["run_id"])
    return Path(run_dir).name


def declaration_sha256_of(run_dir) -> str | None:
    """Отпечаток объявления для манифеста пакета — той же записи, чей
    номер несёт `record_id`: чужой из файла, своей — из свежесобранной
    (объявление без answers[] — они растут ревизиями)."""
    foreign = foreign_record(run_dir)
    if foreign is not None:
        return str(foreign["declaration_sha256"])
    try:
        return build(run_dir)["declaration_sha256"]
    except Exception:                                    # noqa: BLE001
        return None


# ══════════════════════════ голова: объявление ДО старта (шаг 2, 11.1)

#: Цели прогона (закрытый перечень словаря COSMOS).
PURPOSES = ("приёмка", "демонстрация", "регресс", "исследование", "рабочий")
#: Цель по умолчанию у прогона человека: панель заводят работать.
DEFAULT_PURPOSE = "рабочий"
#: Кто отвечает на анкеты: человек за панелью либо книга ответов набора.
ANSWER_POLICIES = ("человек", "книга")
DEFAULT_ANSWER_POLICY = "человек"
#: Полный путь панели — план этапов по умолчанию (11.1).
PANEL_STAGES = ("create", "data", "extract", "data_run", "cycle", "answers",
                "package")
DEFAULT_STOP_AFTER = "package"
#: Привязки ПРОДУКТА CDPM (каким кодом исполняются шаблоны каталога):
#: файл обязан читаться — иначе среда прогона необъявима. Путь
#: переопределяется переменной (тесты, нестандартные установки).
CDPM_BINDINGS_ENV = "CDPM_BINDINGS_FILE"


def _write_json(path: Path, doc) -> None:
    """Атомарная запись документа записи: сначала во временный файл,
    затем замена — читатель никогда не видит половину файла.

    Временный файл — свой у каждого писателя (`<имя>.<pid>-<нить>.tmp`,
    как у статуса прогона `webui._write_status`): два процесса панели
    над одной папкой прогонов не толкаются на одном имени. Замена —
    `cdpm.file_replace.replace_with_retry`: на Windows семантика POSIX
    (читатели через `_read_json` — `on_disk`, голова, сшивка, карточка
    дерева, ручки записи — открывают файл с FILE_SHARE_DELETE и
    писателю не мешают), чужого читателя (антивирус, индексатор, чужой
    процесс с обычным `open()`; так же читает обход индекса дерева
    `vmp.run_tree.build` → `vmp.run_record.load` — сторона COSMOS)
    писатель ждёт окно по времени и сдаётся PermissionError словами.
    У производной run-record.json в HTTP-запросе — общий короткий
    бюджет; обязательное объявление и фоновые записи сохраняют обычный.
    Прежний `os.replace`
    с общим `<имя>.tmp` отказывал сразу, пока кто-нибудь держал
    `run-record.json` открытым: `refresh` падал, и блок `resources`
    статуса тоже не обновлялся (находка 12.09.2026 при разборе
    плавающего теста повтора)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    budget = _WRITE_BUDGET.get() if path.name == RECORD_FILE else None
    window_s = None
    if budget is not None:
        if budget["deadline"] is None:
            budget["deadline"] = time.monotonic() + budget["window_s"]
        window_s = max(0.0, budget["deadline"] - time.monotonic())
    try:
        _file_replace.replace_with_retry(tmp, path, window_s=window_s)
    except PermissionError as e:
        if budget is not None:
            budget["warnings"].append(path.parent.name)
            raise RecordWriteBusy(path) from e
        raise
    finally:
        # Only this writer's own temporary file; never remove the target.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass  # Preserve the primary write failure if cleanup is blocked.


def parse_stages(value) -> list[str]:
    """План этапов из строки формы (список через запятую) либо из
    готового списка; пустое значение — умолчание панели."""
    if value is None or value == "":
        return list(PANEL_STAGES)
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value]
    else:
        items = [part.strip() for part in str(value).split(",")]
    out = [it for it in items if it]
    return out or list(PANEL_STAGES)


def words_problems(purpose: str, answer_policy: str, planned_stages,
                   stop_after: str | None) -> list[str]:
    """(а) Слова объявления — из словарей: цель, политика ответов, этапы
    плана и условие остановки. Иначе гейт отказывает словами, ДО рождения
    папки прогона."""
    problems = []
    if purpose not in PURPOSES:
        problems.append("цель прогона %r вне словаря %s"
                        % (purpose, list(PURPOSES)))
    if answer_policy not in ANSWER_POLICIES:
        problems.append("политика ответов %r вне словаря %s"
                        % (answer_policy, list(ANSWER_POLICIES)))
    unknown = [s for s in planned_stages if s not in PANEL_STAGES]
    if unknown:
        problems.append("этапы плана %s вне перечня панели %s"
                        % (unknown, list(PANEL_STAGES)))
    if stop_after is not None and stop_after not in PANEL_STAGES:
        problems.append("остановка после %r вне перечня панели %s"
                        % (stop_after, list(PANEL_STAGES)))
    elif stop_after is not None and planned_stages \
            and stop_after not in planned_stages:
        problems.append("остановка после %r не входит в план этапов %s"
                        % (stop_after, list(planned_stages)))
    return problems


def cdpm_bindings_path() -> Path:
    """Путь привязок продукта CDPM (`cdpm/bindings.json`)."""
    env = (os.environ.get(CDPM_BINDINGS_ENV) or "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "bindings.json"


def environment_problems(env: dict | None) -> list[str]:
    """(б) Среда объявима: каталог шаблонов известен (версия и отпечаток
    в паспорте среды), привязки продукта CDPM читаются, коммит кода
    известен. Раскладку моделей судит панель (`_layout_gate`): она знает
    допуск комплекта."""
    env = env if isinstance(env, dict) else {}
    catalog = env.get("catalog") if isinstance(env.get("catalog"), dict) else {}
    code = env.get("code") if isinstance(env.get("code"), dict) else {}
    problems = []
    if not (catalog.get("version") and catalog.get("fingerprint")):
        problems.append(
            "каталог шаблонов не объявим: версии и отпечатка в паспорте "
            "среды нет%s — прогон объявляется каталогом, а не «тем, что "
            "в дереве»" % (": " + str(catalog["error"])[:200]
                           if catalog.get("error") else ""))
    if not code.get("commit"):
        problems.append("коммит кода панели неизвестен — прогон "
                        "невоспроизводим: объявить нечего")
    path = cdpm_bindings_path()
    bindings = _read_json(path) if path.is_file() else None
    if not isinstance(bindings, dict) or not bindings.get("bindings"):
        problems.append(
            "привязки CDPM не читаются (%s): каким кодом исполняются "
            "шаблоны каталога — не объявлено; выпустите файл "
            "`python cdpm/bindings_build.py --out cdpm/bindings.json`"
            % path)
    return problems


#: Слова ГОЛОВЫ, когда среду COSMOS при объявлении спросить не удалось.
#: Те же слова, что у раннера наборов (`run_local_pipeline.
#: MODELS_NOTE_NO_COSMOS`): голова панели и голова раннера говорят об
#: одном одинаково — иначе читатель дерева прогонов разбирал бы две
#: формулировки одного факта.
COSMOS_NOT_ASKED_NOTE = "COSMOS не опрошен при объявлении"
#: Слова ГОЛОВЫ, когда COSMOS ответил, но моделей не назвал.
COSMOS_SILENT_NOTE = "COSMOS не назвал моделей при объявлении"


def layout_models(agents: dict | None) -> list[dict]:
    """Раскладка моделей по ролям для головы записи — из ДЕЙСТВУЮЩЕЙ
    раскладки (`providers._load_agents_with_source`): строка на каждый
    объявленный слот роли (`model {role, model, provider, channel,
    effort, system}`; `channel` — класс слота: cloud / local /
    cloud-country).

    Система строки — `cdpm` словом (договор с COSMOS 11.09.2026): голова
    однородна, и рядом с моделями панели в ней стоят модели COSMOS со
    своим словом. Поля в строках прежних записей нет вовсе, и его
    отсутствие словарь читает как `cdpm` — совместимость полная."""
    out = []
    if not isinstance(agents, dict):
        return out
    for role in sorted(agents):
        cfg = agents.get(role)
        if not isinstance(cfg, dict):
            continue
        chans = cfg.get("channels") if isinstance(cfg.get("channels"), dict) else {}
        role_effort = cfg.get("effort")
        for klass in sorted(chans):
            ch = chans.get(klass)
            if not isinstance(ch, dict):
                continue
            if not (ch.get("provider") or ch.get("model")):
                continue
            out.append({"role": str(role), "model": ch.get("model"),
                        "provider": ch.get("provider"), "channel": str(klass),
                        "effort": (str(ch.get("effort")) if ch.get("effort")
                                   else str(role_effort) if role_effort
                                   else None),
                        "system": SYSTEM_CDPM})
    return out


def cosmos_models(environment: dict | None) -> tuple[list[dict], str | None]:
    """Модели COSMOS строками ГОЛОВЫ и слова о неполноте раскладки —
    зеркало `cosmos_head_models` раннера наборов.

    Голову чеканит ХОЗЯИН записи, и своих моделей COSMOS (сертификатор,
    читатель ИДАК) панель не знает: до 11.09.2026 они в голову не
    попадали вовсе (находка К5). Ответ ручки среды даёт их строками;
    ответа нет — пустой перечень и СЛОВА причины: роль не теряется
    молча.

    Поля строки — поля `declaration.models[]` словаря записи, система —
    `cosmos` принудительно (чужую роль панель своей не назовёт). Всё
    лишнее (например `words` у роли, которую сейчас назвать нельзя) не
    копируется: словарь закрыт (`additionalProperties: false`)."""
    if not isinstance(environment, dict):
        return [], COSMOS_NOT_ASKED_NOTE
    rows = []
    for row in (environment.get("models") or []):
        if not isinstance(row, dict) or not str(row.get("role") or "").strip():
            continue
        rows.append({"role": row.get("role"), "model": row.get("model"),
                     "provider": row.get("provider"),
                     "channel": row.get("channel"),
                     "effort": row.get("effort"),
                     "system": SYSTEM_COSMOS})
    return (rows, None) if rows else ([], COSMOS_SILENT_NOTE)


def cosmos_commit_of(environment: dict | None) -> str:
    """Коммит COSMOS из ответа ручки среды (`code.cosmos_commit`) либо
    пусто: не ответил — в голове стоит коммит панели, как прежде («одно
    дерево»), и выдумывать чужой коммит панель не станет."""
    env = environment if isinstance(environment, dict) else {}
    code = env.get("code") if isinstance(env.get("code"), dict) else {}
    return str(code.get("cosmos_commit") or "")


def rules_documents_of(items) -> list[dict]:
    """`rules_document {name, kind, sha256, size}` из байтов загрузки —
    отпечатки документов правил известны ДО заморозки (гейт судит
    объявление раньше первого вызова модели)."""
    out = []
    for name, body in items or []:
        name = str(name or "")
        if not name:
            continue
        out.append({"name": name,
                    "kind": Path(name).suffix.lower().lstrip(".") or None,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "size": len(body)})
    return sorted(out, key=lambda d: d["name"])


#: Имена объявления по словарю COSMOS (`DECLARATION_FIELDS` у vmp) —
#: запасная проверка формы присланной головы, когда проверяльщика схемы
#: в установке нет.
DECLARATION_KEYS = (
    "run_id", "created_at", "created_by", "purpose", "contour", "airline",
    "rules_documents", "data_files", "answers", "catalog", "catalog_extension",
    "bindings", "models", "solvers", "code", "libraries", "planned_stages",
    "stop_after", "expectations", "lineage", "answer_policy")


def declaration_problems(head) -> list[str]:
    """Слова о ПРИСЛАННОЙ голове (полное объявление хозяина — вариант
    «б» договора с COSMOS 21:4x: раннер шлёт голову целиком полем формы
    `declaration`): форма — по словарю COSMOS (схема как данные, часть
    `declaration`), слова — из перечней. Пусто — голова принимается как
    есть."""
    if not isinstance(head, dict):
        return ["объявление не словарь"]
    problems: list[str] = []
    schema = _read_json(SCHEMA_PATH)
    validator = None
    if isinstance(schema, dict) and isinstance(schema.get("$defs"), dict):
        try:
            import jsonschema
            validator = jsonschema.Draft202012Validator(
                {"$defs": schema["$defs"], "$ref": "#/$defs/declaration"})
        except Exception:                                 # noqa: BLE001
            validator = None
    if validator is not None:
        for e in validator.iter_errors(head):
            where = "/".join(str(p) for p in e.absolute_path)
            problems.append("объявление%s: %s"
                            % (("/" + where) if where else "", e.message))
    else:
        missing = [k for k in DECLARATION_KEYS if k not in head]
        if missing:
            problems.append("объявление: нет имён %s" % missing)
    if not str(head.get("run_id") or "").strip():
        problems.append("объявление: номер прогона (run_id) не назван")
    if head.get("purpose") not in PURPOSES:
        problems.append("объявление: цель %r вне перечня %s"
                        % (head.get("purpose"), list(PURPOSES)))
    contour = head.get("contour") if isinstance(head.get("contour"), dict) else {}
    if contour.get("name") not in CONTOURS:
        problems.append("объявление: площадка %r вне перечня %s"
                        % (contour.get("name"), list(CONTOURS)))
    if head.get("answer_policy") not in (None, *ANSWER_POLICIES):
        problems.append("объявление: политика ответов %r вне перечня %s"
                        % (head.get("answer_policy"), list(ANSWER_POLICIES)))
    stages = head.get("planned_stages")
    if not isinstance(stages, list) or not all(isinstance(s, str) and s
                                               for s in stages):
        problems.append("объявление: план этапов — не список слов")
    return problems


#: Имена строки привязок, которые обязана объявить ЛЮБАЯ голова: без
#: них не сказано, под какой каталог выпущены привязки.
_BINDINGS_REQUIRED = ("catalog_version", "catalog_fingerprint")
#: Имена строки привязок, которые сверяются (в этом порядке). `path`
#: сюда НЕ входит: один и тот же файл законно лежит по разным путям у
#: раннера и у панели, а сверяются его содержание и происхождение.
_BINDINGS_COMPARED = _BINDINGS_REQUIRED + BINDINGS_EXTRA


def _bindings_mismatch(label: str, said: dict, mine: dict) -> list[str]:
    """Сверка одной строки привязок «объявлено / на деле». Обязательные
    имена сверяются всегда (молчание головы — расхождение); прочие
    (`sha256`, `product_version`, `generated_from`) — только когда
    голова их ОБЪЯВИЛА: словарь объявления растёт правкой COSMOS
    (§12.11 п. 5), и голова прежнего словаря о новых именах молчит
    законно. Слова называют КАКОЙ ФАЙЛ разошёлся — прежняя подпись
    «привязки CDPM» стояла у фактов файла COSMOS (находка F8
    неавторского ревью 12.09.2026)."""
    problems: list[str] = []
    said = said if isinstance(said, dict) else {}
    mine = mine if isinstance(mine, dict) else {}
    for key in _BINDINGS_COMPARED:
        value = mine.get(key)
        if not value:
            continue
        if key not in _BINDINGS_REQUIRED and key not in said:
            continue
        if said.get(key) != value:
            problems.append("%s: %s объявлено %r, на деле %r"
                            % (label, key, said.get(key), value))
    return problems


def environment_mismatch(head: dict, env: dict, bindings: dict,
                         panel_models: list[dict]) -> list[str]:
    """Сверка «объявлено / на деле» присланной головы по ролям ПАНЕЛИ
    (11.1-б): каталог шаблонов (версия, отпечаток), привязки ОБОИХ
    продуктов (COSMOS `vmp/bindings.json` и CDPM `cdpm/bindings.json` —
    носители поддержки K06, §12.11 п. 5; каждый назван своим именем),
    модели ролей CDPM из действующей раскладки, коммит cdpm. Расхождение
    — слова; гейт по ним отказывает до рождения папки.

    Модели судятся ТОЛЬКО свои: строки головы со словом системы `cdpm`
    (и строки без слова — записи до 11.09.2026). Модели COSMOS в голове
    — дело COSMOS: их сверяет его собственный гейт «от пакета», а панель
    о чужой раскладке ничего не знает и не вправе отказывать по ней."""
    problems: list[str] = []
    env = env if isinstance(env, dict) else {}
    cat = env.get("catalog") if isinstance(env.get("catalog"), dict) else {}
    hcat = head.get("catalog") if isinstance(head.get("catalog"), dict) else {}
    if cat.get("version") and hcat.get("version") != cat.get("version"):
        problems.append("каталог шаблонов: объявлена версия %r, на деле %r"
                        % (hcat.get("version"), cat.get("version")))
    if cat.get("fingerprint") and hcat.get("fingerprint") != cat.get("fingerprint"):
        problems.append("каталог шаблонов: отпечаток объявления не равен "
                        "отпечатку каталога площадки")
    bindings = bindings if isinstance(bindings, dict) else {}
    hb = head.get("bindings") if isinstance(head.get("bindings"), dict) else {}
    problems += _bindings_mismatch("привязки COSMOS (vmp/bindings.json)",
                                   hb, bindings)
    hb_cdpm = hb.get(BINDINGS_CDPM_KEY)
    my_cdpm = bindings.get(BINDINGS_CDPM_KEY)
    if isinstance(my_cdpm, dict) and isinstance(hb_cdpm, dict):
        problems += _bindings_mismatch("привязки CDPM (cdpm/bindings.json)",
                                       hb_cdpm, my_cdpm)
    want = {(m.get("role"), m.get("channel")): (m.get("model"), m.get("provider"))
            for m in (panel_models or []) if isinstance(m, dict)}
    got = {(m.get("role"), m.get("channel")): (m.get("model"), m.get("provider"))
           for m in (head.get("models") or []) if isinstance(m, dict)
           and m.get("system") in _PANEL_SYSTEMS}
    if want and not got:
        problems.append("модели ролей CDPM не объявлены, а раскладка панели их знает")
    for key, val in sorted(want.items(), key=lambda kv: str(kv[0])):
        if key in got and got[key] != val:
            problems.append("модель роли %s (%s): объявлено %s/%s, на деле %s/%s"
                            % (key[0], key[1], got[key][0], got[key][1],
                               val[0], val[1]))
    code = env.get("code") if isinstance(env.get("code"), dict) else {}
    hcode = head.get("code") if isinstance(head.get("code"), dict) else {}
    mine, theirs = str(code.get("commit") or ""), str(hcode.get("cdpm_commit") or "")
    if mine and theirs and not (mine.startswith(theirs) or theirs.startswith(mine)):
        problems.append("код CDPM: объявлен коммит %s, на деле %s"
                        % (theirs[:12], mine[:12]))
    return problems


def new_declaration(run_dir, *, created_at: str, created_by: str,
                    purpose: str, airline: dict, rules_documents: list[dict],
                    planned_stages, stop_after: str | None,
                    expectations: dict, answer_policy: str,
                    environment: dict, models: list[dict] | None = None,
                    run_id: str | None = None,
                    replay: dict | None = None,
                    cosmos_environment: dict | None = None) -> dict:
    """Полное объявление прогона по словарю `cosmos-run-record/v1`
    (11.1). Номер записи — `run_id` хозяина (раннер передаёт свой) либо
    имя папки прогона; данные и ответы к моменту объявления пусты —
    они приходят этапами, и корень дерева с ответами ставится, когда
    ответы закрыты.

    `cosmos_environment` — ответ ручки среды COSMOS
    (`channel.environment`, опрошенной ДО чеканки): его модели ложатся в
    голову РЯДОМ с моделями панели словом `system: "cosmos"`, а коммит
    COSMOS берётся из его же блока `code`. Ответа нет — модели панели
    как прежде, а неполнота названа полем `models_note`: молчать о
    пропавших ролях нельзя (договор с COSMOS 11.09.2026).

    Пустого `models_note` в голове НЕ БЫВАЕТ: ключ ставится только при
    непустых словах. Нормализатор словаря такое поле опускает
    (`OMITTED_WHEN_EMPTY`), и голова с `models_note: null` разошлась бы
    с собой же по отпечатку — панель считает его по сырой голове, а
    COSMOS по нормализованной."""
    run_dir = Path(run_dir)
    env = environment if isinstance(environment, dict) else {}
    catalog = env.get("catalog") if isinstance(env.get("catalog"), dict) else {}
    code = env.get("code") if isinstance(env.get("code"), dict) else {}
    rep = replay if isinstance(replay, dict) else {}
    rules = list(rules_documents or [])
    data: list[dict] = []
    cosmos_rows, models_note = cosmos_models(cosmos_environment)
    cosmos_commit = cosmos_commit_of(cosmos_environment)
    declaration = {
        "run_id": str(run_id or run_dir.name),
        "created_at": created_at,
        "created_by": created_by,
        "purpose": purpose,
        "contour": _contour(),
        "airline": {"name": airline.get("name"),
                    "data_class": airline.get("data_class"),
                    "llm_scope": airline.get("llm_scope")},
        "rules_documents": rules,
        # Книга данных приходит линией данных, ответы — ревизиями: голова
        # объявляется ДО них и после старта не переписывается.
        "data_files": data,
        "answers": [],
        "catalog": {"name": "template-catalog",
                    "version": catalog.get("version"),
                    "fingerprint": catalog.get("fingerprint"),
                    "path": _catalog_path()},
        "catalog_extension": None,
        "bindings": _bindings(),
        # Модели ОБЕИХ систем: свои (`system: cdpm`) и названные COSMOS
        # (`system: cosmos`) — голова однородна и полна.
        "models": list(models or []) + cosmos_rows,
        "solvers": [],                          # решатели — у COSMOS
        "code": {"cdpm_commit": code.get("commit"),
                 # Коммит COSMOS — от самого COSMOS, если он ответил;
                 # не ответил — коммит панели, как прежде («одно дерево»).
                 "cosmos_commit": cosmos_commit or code.get("commit"),
                 "runner_fingerprint": None},
        "libraries": _libraries(env),
        "planned_stages": list(planned_stages),
        "stop_after": stop_after,
        "expectations": {"kind": expectations.get("kind"),
                         "ref": expectations.get("ref")},
        "lineage": {
            "root_sha256": root_sha256(rules, data),
            "root_answers_sha256": None,
            "parent_run": str(rep["source_run"]) if rep.get("source_run") else None,
            "branch_of": ("replay:" + str(rep.get("mode"))) if rep.get("source_run") else None,
            "package_sha256": None,
            "source_run": str(rep["source_run"]) if rep.get("source_run") else None,
        },
        "answer_policy": answer_policy,
    }
    if models_note:
        # Ключ ставится ТОЛЬКО со словами: пустое поле нормализатор
        # словаря опускает, и голова разошлась бы сама с собой по
        # отпечатку. Место — рядом с моделями, о которых он говорит
        # (на отпечаток порядок ключей не влияет — счёт канонический).
        ordered: dict = {}
        for key, value in declaration.items():
            ordered[key] = value
            if key == "models":
                ordered["models_note"] = models_note
        declaration = ordered
    return declaration


def declare(run_dir, declaration: dict) -> dict:
    """Записать голову прогона: `declaration.json` (объявление и его
    отпечаток) и `run-record.json` в состоянии «объявлен». Зовётся
    сразу после рождения папки и записи документов — ДО заморозки и до
    первого вызова модели."""
    run_dir = Path(run_dir)
    _write_json(run_dir / DECLARATION_FILE,
                {"schema": RECORD_SCHEMA, "declaration": declaration,
                 "declaration_sha256": declaration_sha256(declaration)})
    return refresh(run_dir)


def read_declaration(run_dir) -> dict | None:
    """Голова прогона из `declaration.json` как данные: `{schema,
    declaration, declaration_sha256}`. Файл другой формы головой не
    считается — прогон судится прежним законом (голова-штамп)."""
    doc = _read_json(Path(run_dir) / DECLARATION_FILE)
    if not isinstance(doc, dict) or doc.get("schema") != RECORD_SCHEMA:
        return None
    d = doc.get("declaration")
    if not isinstance(d, dict) or not d.get("run_id"):
        return None
    return doc


def _declared_head(declaration: dict, run_dir: Path) -> dict:
    """Объявленная голова с ревизиями ответов: голова НЕ
    реконструируется, растут только `answers` и производный от них
    корень дерева `lineage.root_answers_sha256` (в отпечаток объявления
    ни то, ни другое не входит)."""
    out = json.loads(json.dumps(declaration, ensure_ascii=False))
    # Растут только ревизии ответов (в отпечаток не входят); корень с
    # ответами lineage.root_answers_sha256 остаётся таким, каким объявлен
    # (у панели — null, у раннера с книгой — его): голова после старта не
    # меняется ни в одном поле, входящем в отпечаток словаря.
    out["answers"] = _answers(run_dir)
    return out


# ═════════════════ хранитель: чужие записи этапов и итогов (11.2, 11.3)

def appends_path(run_dir) -> Path:
    return Path(run_dir).joinpath(*APPENDS_FILE)


def _append_rows(run_dir) -> list[dict]:
    out: list[dict] = []
    try:
        text = appends_path(run_dir).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def entry_key(system: str, entry: dict) -> list:
    """Ключ идемпотентности записи: (система, этап, подэтап). Подэтап
    несёт номер попытки — повтор того же ключа ЗАМЕНЯЕТ прежнюю запись
    (последнее слово — свежее), а не множит строки."""
    return [str(system), str(entry.get("stage") or ""),
            "" if entry.get("substage") is None else str(entry.get("substage"))]


def empty_golden() -> dict:
    """Признак золотого прогона по умолчанию: не подтверждён."""
    return {"confirmed": False, "by": None, "at": None, "words": None}


def _golden_row(value) -> dict:
    """Признак золотого из строки журнала приписок — по словарю
    (четыре поля, ничего сверх)."""
    v = value if isinstance(value, dict) else {}
    return {"confirmed": bool(v.get("confirmed")),
            "by": v.get("by") or None, "at": v.get("at") or None,
            "words": v.get("words") or None}


def appended(run_dir) -> dict:
    """Чужие записи хранителя как единый вид: `{entries, verdicts,
    deliveries, systems, golden, expectations}`. Повтор ключа заменяет
    прежнюю запись, место записи в порядке — по первому приёму ключа.

    `golden` — признак золотого прогона (шаг 3, 12.2): подтверждение
    владельца ложится строкой итогов, и последняя строка старше
    прежних; снятие признака — тоже строка (`confirmed: false`), а не
    стирание прежней. `expectations` — сравнение с ожиданиями набора
    (12.4): его пишет РАННЕР через `record/append`, панель его только
    хранит и показывает."""
    entries: dict[tuple, dict] = {}
    order: dict[tuple, int] = {}
    verdicts: dict[str, dict] = {}
    deliveries: list[dict] = []
    systems: list[str] = []
    golden: dict | None = None
    expectations: dict | None = None
    for i, row in enumerate(_append_rows(run_dir)):
        system = str(row.get("system") or "")
        if system and system not in systems:
            systems.append(system)
        entry = row.get("entry")
        if isinstance(entry, dict):
            key = tuple(row.get("key") or entry_key(system, entry))
            order.setdefault(key, i)
            # Пометка системы-летописца (словарь COSMOS 17063f3c:
            # journal[].system ∈ cdpm|runner|cosmos): записи append несут
            # слово отправителя, если сами его не назвали.
            entry = dict(entry)
            if system and not entry.get("system"):
                entry["system"] = system
            entries[key] = entry
        res = row.get("results")
        if isinstance(res, dict):
            v = res.get("verdicts")
            if isinstance(v, dict) and system:
                verdicts[system] = {**verdicts.get(system, {}), **v}
            for d in (res.get("deliveries") or []):
                if isinstance(d, dict) and not _has_delivery(deliveries, d):
                    deliveries.append(d)
            if isinstance(res.get("golden"), dict):
                golden = _golden_row(res["golden"])
            cmp_row = res.get("comparison")
            if isinstance(cmp_row, dict) \
                    and isinstance(cmp_row.get("expectations"), dict):
                expectations = cmp_row["expectations"]
    rows = sorted(entries.items(), key=lambda kv: order[kv[0]])
    return {"entries": [e for _k, e in rows], "verdicts": verdicts,
            "deliveries": deliveries, "systems": systems,
            "golden": golden, "expectations": expectations}


def _has_delivery(items: list[dict], d: dict) -> bool:
    """Выдача уже названа: по отпечатку, а при его отсутствии — по виду
    и пути (повтор доставки записи не множит)."""
    for it in items:
        if not isinstance(it, dict):
            continue
        if d.get("sha256") and it.get("sha256") == d.get("sha256"):
            return True
        if not d.get("sha256") and it.get("kind") == d.get("kind") \
                and it.get("path") == d.get("path"):
            return True
    return False


def _moment_key(v):
    """Ключ сортировки журнала по времени: разобранная отметка — по
    часам, неразобранная (её нет) — в конец."""
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return (1, 0.0)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return (0, dt.timestamp())


def _merge_journal(panel: list[dict], foreign: list[dict]) -> list[dict]:
    """Единый журнал: записи панели и чужие записи по времени начала,
    при равном времени — по порядку приёма. Чужая запись с тем же
    этапом и подэтапом СТАРШЕ панельной (последнее слово — свежее)."""
    keys = {(e.get("stage"), e.get("substage")) for e in foreign
            if isinstance(e, dict)}
    rows = [(i, e) for i, e in enumerate(panel)
            if (e.get("stage"), e.get("substage")) not in keys]
    rows += [(10 ** 6 + i, e) for i, e in enumerate(foreign)]
    rows.sort(key=lambda r: (_moment_key(r[1].get("started_at")), r[0]))
    return [e for _i, e in rows]


def entry_problems(entries) -> list[str]:
    """Слова о записях, не сошедшихся со словарём `journal_entry` схемы
    COSMOS (`vmp/run_record.schema.json` — читается ДАННЫМИ). Схемы или
    проверяльщика в установке нет — судится форма записи: обязательные
    имена, исход из перечня, этап словами."""
    problems: list[str] = []
    if not isinstance(entries, list) or not entries:
        return ["записей журнала нет: тело без `entries`"]
    schema = _read_json(SCHEMA_PATH)
    validator = None
    if isinstance(schema, dict) and isinstance(schema.get("$defs"), dict):
        try:
            import jsonschema
            validator = jsonschema.Draft202012Validator(
                {"$defs": schema["$defs"], "$ref": "#/$defs/journal_entry"})
        except Exception:                                 # noqa: BLE001
            validator = None
    for i, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            problems.append("запись %d: не объект" % i)
            continue
        if validator is not None:
            for e in validator.iter_errors(entry):
                where = "/".join(str(p) for p in e.absolute_path)
                problems.append("запись %d%s: %s"
                                % (i, ("/" + where) if where else "", e.message))
            continue
        problems += _entry_form_problems(i, entry)
    return problems


#: Имена записи журнала (словарь COSMOS `journal_entry`) — запасная
#: проверка формы, когда проверяльщика схемы в установке нет.
_ENTRY_KEYS = ("stage", "substage", "started_at", "finished_at",
               "duration_machine_s", "duration_waiting_human_s", "outcome",
               "reason_code", "words", "actor", "artifacts", "counters",
               "resources", "system")


def _entry_form_problems(i: int, entry: dict) -> list[str]:
    problems = []
    missing = [k for k in _ENTRY_KEYS if k not in entry and k != "system"]
    if missing:
        problems.append("запись %d: нет имён %s" % (i, missing))
    extra = [k for k in entry if k not in _ENTRY_KEYS]
    if extra:
        problems.append("запись %d: имена вне словаря %s" % (i, extra))
    if not str(entry.get("stage") or ""):
        problems.append("запись %d: этап не назван" % i)
    if entry.get("outcome") not in OUTCOMES:
        problems.append("запись %d: исход %r вне перечня %s"
                        % (i, entry.get("outcome"), list(OUTCOMES)))
    return problems


def append(run_dir, system: str, entries: list[dict],
           results: dict | None = None) -> dict:
    """Принять записи чужой системы: строки `{received_at, system, key,
    entry}` и `{received_at, system, results}` в
    `journal/record-appends.jsonl`. Возвращает `{accepted, replaced}` —
    сколько записей принято и сколько заменило прежние по ключу."""
    run_dir = Path(run_dir)
    path = appends_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    known = {tuple(row["key"]) for row in _append_rows(run_dir)
             if isinstance(row.get("key"), list)}
    at = _now_with_zone()
    lines: list[dict] = []
    accepted = replaced = 0
    for entry in entries:
        key = entry_key(system, entry)
        if tuple(key) in known:
            replaced += 1
        known.add(tuple(key))
        lines.append({"received_at": at, "system": str(system), "key": key,
                      "entry": entry})
        accepted += 1
    if isinstance(results, dict) and results:
        lines.append({"received_at": at, "system": str(system),
                      "results": results})
    with open(path, "ab") as f:
        for line in lines:
            f.write((json.dumps(line, ensure_ascii=False) + "\n")
                    .encode("utf-8"))
    return {"accepted": accepted, "replaced": replaced}


def record_head(run_dir) -> dict | None:
    """Голова записи прогона для сверки с присланным `append`:
    `{run_id, declaration_sha256, owner}` — чужая из файла (переходное
    правило), своя объявленная либо голова прогона до шага 2. Записи
    нет вовсе — None (ручки отвечают 404)."""
    run_dir = Path(run_dir)
    foreign = foreign_record(run_dir)
    if foreign is not None:
        return {"run_id": str(foreign["declaration"]["run_id"]),
                "declaration_sha256": str(foreign["declaration_sha256"]),
                "owner": str(foreign["declaration"].get("created_by")
                             or "чужая система")}
    head = read_declaration(run_dir)
    if head is not None:
        d = head["declaration"]
        return {"run_id": str(d["run_id"]),
                "declaration_sha256": str(head["declaration_sha256"]),
                "owner": str(d.get("created_by") or PANEL_ACTOR)}
    if (run_dir / RECORD_FILE).is_file():
        rec = build(run_dir)
        return {"run_id": str(rec["declaration"]["run_id"]),
                "declaration_sha256": str(rec["declaration_sha256"]),
                "owner": PANEL_ACTOR}
    return None


def _stitch_foreign(run_dir: Path, foreign: dict) -> dict:
    """Сшивка при чужой голове (11.3): голова и журнал чужого файла плюс
    записи `append`; файл при этом не переписывается."""
    joined = appended(run_dir)
    journal = foreign.get("journal")
    journal = [e for e in journal if isinstance(e, dict)] \
        if isinstance(journal, list) else []
    journal = _merge_journal(journal, joined["entries"])
    results = foreign.get("results")
    results = dict(results) if isinstance(results, dict) else {}
    verdicts = results.get("verdicts")
    verdicts = dict(verdicts) if isinstance(verdicts, dict) else {}
    verdicts.update(joined["verdicts"])
    deliveries = [d for d in (results.get("deliveries") or [])
                  if isinstance(d, dict)]
    for d in joined["deliveries"]:
        if not _has_delivery(deliveries, d):
            deliveries.append(d)
    results["verdicts"] = verdicts
    results["deliveries"] = deliveries
    comparison = results.get("comparison")
    comparison = dict(comparison) if isinstance(comparison, dict) else {}
    for key in ("expectations", "previous_run", "golden"):
        if not isinstance(comparison.get(key), dict):
            comparison[key] = {}
    if joined["expectations"]:
        comparison["expectations"] = joined["expectations"]
    results["comparison"] = comparison
    # Признак золотого — у ХРАНИТЕЛЯ (журнал приписок): чужую запись
    # панель не переписывает, а подтверждение владельца принимает она.
    if joined["golden"] is not None:
        results["golden"] = joined["golden"]
    elif not isinstance(results.get("golden"), dict):
        results["golden"] = empty_golden()
    results["timing"] = _timing(journal)
    state = results.get("state")
    if state not in STATES:
        # Чужая запись без состояния (обрывок раннера) — состояние по
        # журналу сшивки.
        state = _record_state({}, journal, bool(deliveries),
                              joined["entries"])
    else:
        # Состояние хозяина остаётся, но чужой исход старше: расчёт
        # COSMOS упал — прогон в сбое, что бы ни стояло в файле.
        heavier = _record_state({}, [], False, joined["entries"])
        if heavier in ("сбой", "остановлен человеком", "остановлен по правилу"):
            state = heavier
    results["state"] = state
    record = {"schema": RECORD_SCHEMA,
              "declaration": foreign["declaration"],
              "declaration_sha256": foreign["declaration_sha256"],
              "journal": journal, "results": results}
    results["comparison"] = _comparison(run_dir, record)
    return record


def stitch(run_dir) -> dict | None:
    """ЕДИНАЯ запись прогона для ручки `GET …/record`: своя голова с
    журналом панели и чужими записями либо (переходное правило) чужая
    голова с чужим журналом из файла и записями `append`. Записи нет —
    None."""
    run_dir = Path(run_dir)
    foreign = foreign_record(run_dir)
    if foreign is not None:
        return _stitch_foreign(run_dir, foreign)
    if read_declaration(run_dir) is None \
            and not (run_dir / RECORD_FILE).is_file():
        return None
    return build(run_dir)
