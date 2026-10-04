# -*- coding: utf-8 -*-
"""Прогоны COSMOS — список расчётов по пакетам постановки (слова
владельца 07.09.2026: «А почему в COSMOS нет системы прогонов как в
CDPM?»; «информация, хранящаяся о каждом прогоне, должна давать
возможность его однозначно воспроизвести»).

Устройство (проект final-report/COSMOS-PROGONY-PROEKT-CLD.md):

* прогон — ЗАПИСЬ-УКАЗАТЕЛЬ над существующими носителями: байты пакета
  остаются во входном каталоге канала (task-inbox/<sha>/package.zip),
  нажатия — в каталогах нажатий (package-runs/<sha>/…); ни один
  существующий файл не переезжает, оформление легаси пишет только новые
  файлы (обратная совместимость без потерь);
* попытка принадлежит паре (прогон, пакет) через рабочий каталог: первый
  держатель sha работает в package-runs/<sha>/, каждый следующий прогон с
  тем же sha — в package-runs/<sha>/runs/<run>/; держатель объявлен
  маркером holder.json внутри рабочего каталога (захват атомарный);
* хранилище живёт и умирает вместе с инбоксом: runs_root() — None при
  выключенном входном каталоге (CREW_TASK_INBOX пуст) или пустом
  CREW_COSMOS_RUNS_DIR; читающие пути НИКОГДА не пишут на диск и не
  оформляют легаси — оформление идёт только из пишущих действий;
* один указатель текущего: cosmos-runs/current.json — правда после
  инициализации хранилища, task-inbox/current.json — зеркало, пишется
  последним и только из прогона;
* канал не отказывает: нет текущего прогона — прогон создаётся сам;
* паспорт — только чтение с диска, коды вместо прозы (чего нет —
  null с кодом причины; слова к кодам — в интерфейсе).

Замки: RLock процесса + файловый замок cosmos-runs/.lock. Порядок
замков зафиксирован: _RUN_LOCK никогда не берётся, пока держится
package_runs._lock, и наоборот — состояние нажатий здесь только читается.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import platform
import re
import threading
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

import _data_paths

SCHEMA_STORAGE = "cosmos-runs/v1"
SCHEMA_RUN = "cosmos-run/v1"
SCHEMA_HOLDER = "cosmos-workdir-holder/v1"

RUNS_DIR_NAME = "cosmos-runs"
ARCHIVE_DIR_NAME = "cosmos-runs-archive"
STORAGE_FILE = "storage.json"
COUNTER_FILE = "counter.json"
ORDER_FILE = "order.json"
CURRENT_FILE = "current.json"
LOCK_FILE = ".lock"
ADOPTION_LOG = "adoption.jsonl"
HOLDER_FILE = "holder.json"
PACKAGE_RUNS_DIR = "package-runs"
ATTEMPTS_DIR = "attempts"

RUN_ID_RE = re.compile(r"run-\d{4}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
ATTEMPT_ID_RE = re.compile(r"[A-Za-z0-9_-]+")
TITLE_MAX = 120
LOCK_TIMEOUT_S = 30.0

#: Пакеты Python, версии которых входят в паспорт окружения.
PACKAGES_OF_INTEREST = ("ortools", "highspy", "z3-solver", "openpyxl",
                        "jsonschema", "httpx", "fastapi")

#: Виды состояния прогона (коды; слова — у клиента, cruns.state.*) и
#: русские слова для поиска и инструментов (поле state записи списка).
STATE_KINDS = ("new", "accepted", "blocked", "lost", "working", "done",
               "stalled", "refused", "interrupted")
STATE_WORDS = {"new": "новый", "accepted": "пакет принят",
               "blocked": "запуск запрещён", "lost": "пакет утерян",
               "working": "идёт расчёт", "done": "готово",
               "stalled": "остановился", "refused": "отказ",
               "interrupted": "прервано"}
STATE_BUCKETS = {"new": "new", "accepted": "new", "blocked": "new",
                 "working": "work", "done": "done", "stalled": "done",
                 "refused": "fail", "interrupted": "fail", "lost": "fail"}

class RunsError(RuntimeError):
    """Отказ действия над прогонами словами; code — машинный код причины
    (storage-off, env-package, current-busy, …), status — код HTTP;
    cause — уточнение причины у кода storage-off (inbox-off |
    runs-dir-empty), чтобы слова отказа называли настоящую переменную."""

    def __init__(self, message: str, *, code: str | None = None,
                 status: int = 409, cause: str | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.cause = cause


#: Слова отказа storage-off по причине (ревью реализации 07.09: пустой
#: CREW_COSMOS_RUNS_DIR при живом инбоксе назывался словами про
#: CREW_TASK_INBOX — отказ лгал о причине).
STORAGE_OFF_WORDS = {
    "inbox-off": "входной каталог канала отключён (CREW_TASK_INBOX пуст) — "
                 "прогоны не действуют",
    "runs-dir-empty": "хранилище прогонов выключено (CREW_COSMOS_RUNS_DIR "
                      "задан пустым) — прогоны не действуют",
}


# ------------------------------------------------------------ корни

def data_root() -> Path:
    return Path(_data_paths.data_root())


def runs_root() -> Path | None:
    """Корень хранилища прогонов: CREW_COSMOS_RUNS_DIR (пусто —
    выключено), иначе <каталог данных>/cosmos-runs; при выключенном
    входном каталоге канала прогоны выключены (None)."""
    env = os.environ.get("CREW_COSMOS_RUNS_DIR")
    if env is not None:
        return Path(env) if env.strip() else None
    from webapp import task_channel
    if task_channel.inbox_dir() is None:
        return None
    return data_root() / RUNS_DIR_NAME


def storage_off_cause() -> str | None:
    """Причина выключенного хранилища: «runs-dir-empty» — CREW_COSMOS_RUNS_DIR
    задан пустым; «inbox-off» — входной каталог канала отключён
    (CREW_TASK_INBOX пуст); хранилище действует — None. Та же логика, что у
    runs_root(), но с именем причины для слов отказа."""
    env = os.environ.get("CREW_COSMOS_RUNS_DIR")
    if env is not None:
        return None if env.strip() else "runs-dir-empty"
    from webapp import task_channel
    return "inbox-off" if task_channel.inbox_dir() is None else None


def _storage_off_error() -> RunsError:
    cause = storage_off_cause() or "inbox-off"
    return RunsError(STORAGE_OFF_WORDS[cause], code="storage-off", cause=cause)


def archive_root() -> Path | None:
    root = runs_root()
    return None if root is None else root.parent / ARCHIVE_DIR_NAME


def package_runs_root() -> Path:
    """Корень каталогов нажатий — тот же, что у package_runs (без
    переопределения): <каталог данных>/package-runs."""
    return data_root() / PACKAGE_RUNS_DIR


def storage_state() -> str:
    """«off» — корня нет; «uninitialized» — маркера storage.json нет;
    «ready» — хранилище заведено."""
    root = runs_root()
    if root is None:
        return "off"
    return "ready" if (root / STORAGE_FILE).is_file() else "uninitialized"


def env_package() -> str | None:
    """Пакет «файлом руками» (CREW_TASK_PACKAGE) — при нём текущий
    прогон не действует."""
    return (os.environ.get("CREW_TASK_PACKAGE") or "").strip() or None


def _now_iso() -> str:
    """Момент «сейчас» в ISO 8601 с местным смещением от UTC (секунды)
    — образец created_at в CDPM."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _normalize_iso(at) -> str | None:
    try:
        return datetime.fromisoformat(str(at)).astimezone() \
            .isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------ чтение/запись

_RUN_LOCK = threading.RLock()
_HELD: list[Path] = []


def _read_json_retry(path: Path):
    """Чтение JSON с повтором при PermissionError (40 × 25 мс — файл в
    момент атомарной замены на Windows); кодировка utf-8-sig (снимки с
    BOM). Под _RUN_LOCK, чтобы архив под тем же замком не застал
    внутрипроцессного читателя посреди переноса."""
    with _RUN_LOCK:
        last: Exception | None = None
        for _ in range(40):
            try:
                return json.loads(path.read_text(encoding="utf-8-sig"))
            except PermissionError as e:
                last = e
                time.sleep(0.025)
        assert last is not None
        raise last


def _read_json_opt(path: Path):
    """Необязательный JSON: нет файла или он кривой — None."""
    try:
        return _read_json_retry(path)
    except (OSError, ValueError):
        return None


def _write_json_atomic(path: Path, doc) -> None:
    """Единственная атомарная запись файлов хранилища: временный файл с
    uuid-суффиксом + замена с 40 повторами при PermissionError."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name("%s.%s.tmp" % (path.name, uuid.uuid4().hex[:12]))
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    for _ in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.025)
    os.replace(tmp, path)


def _lock_fd(fd: int, timeout_s: float = LOCK_TIMEOUT_S) -> None:
    """Исключительная блокировка первого байта файла замка с ожиданием
    (msvcrt на Windows, flock на POSIX); по истечении срока —
    TimeoutError словами, вечного зависания нет."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "хранилище прогонов занято другим процессом дольше "
                    "%g с — действие отменено" % timeout_s) from None
            time.sleep(0.02)


def _unlock_fd(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextlib.contextmanager
def storage_lock():
    """Замок хранилища: RLock нити + файловый замок cosmos-runs/.lock;
    вложенный вход той же нити файл второй раз не берёт. Выключенное
    хранилище — RunsError storage-off словами."""
    root = runs_root()
    if root is None:
        raise _storage_off_error()
    with _RUN_LOCK:
        key = Path(os.path.abspath(str(root)))
        fd = None
        if key not in _HELD:
            root.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(root / LOCK_FILE),
                         os.O_RDWR | os.O_CREAT, 0o644)
            try:
                _lock_fd(fd)
            except BaseException:
                os.close(fd)
                raise
            _HELD.append(key)
        try:
            yield root
        finally:
            if fd is not None:
                _HELD.remove(key)
                try:
                    _unlock_fd(fd)
                finally:
                    os.close(fd)


def _root_or_raise() -> Path:
    root = runs_root()
    if root is None:
        raise _storage_off_error()
    return root


def _run_path(run: str) -> Path:
    """Файл записи прогона — единственное место, где названо его имя."""
    return _root_or_raise() / run / "run.json"


def check_run_id(run) -> str:
    run = str(run or "")
    if not RUN_ID_RE.fullmatch(run):
        raise RunsError("недопустимый номер прогона %r" % run, status=400,
                        code="bad-run-id")
    return run


def check_attempt_id(attempt) -> str:
    attempt = str(attempt or "")
    if not ATTEMPT_ID_RE.fullmatch(attempt):
        raise RunsError("идентификатор попытки не похож на свой",
                        status=400, code="bad-attempt-id")
    return attempt


def read_run(run: str) -> dict:
    """Запись прогона активного корня; нет каталога — RunsError 404,
    кривая запись — RunsError run-unreadable."""
    root = _root_or_raise()
    if not (root / run).is_dir():
        raise RunsError("прогон %s не существует" % run, status=404,
                        code="not-found")
    try:
        doc = _read_json_retry(_run_path(run))
    except (OSError, ValueError) as e:
        raise RunsError("запись прогона %s не читается: %s"
                        % (run, str(e)[:200]), code="run-unreadable") from None
    if not isinstance(doc, dict):
        raise RunsError("запись прогона %s не читается: не словарь" % run,
                        code="run-unreadable")
    doc.setdefault("run", run)
    doc.setdefault("packages", [])
    return doc


def _mutate_run(run: str, fn) -> dict:
    """Единственный писатель записи прогона: перечитать под замком →
    fn(doc) правит свои ключи → атомарная замена."""
    with storage_lock():
        doc = read_run(run)
        fn(doc)
        _write_json_atomic(_run_path(run), doc)
        return doc


# ------------------------------------------------------- номера, порядок

def _existing_run_ids(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.glob("run-*")
                  if RUN_ID_RE.fullmatch(p.name) and p.is_dir())


def _read_counter(root: Path) -> int:
    doc = _read_json_opt(root / COUNTER_FILE)
    try:
        return max(0, int(doc.get("last", 0))) if isinstance(doc, dict) else 0
    except (TypeError, ValueError):
        return 0


def _raise_counter(root: Path, n: int) -> int:
    """Поднять counter.last до n (никогда не опускать)."""
    last = max(_read_counter(root), int(n))
    _write_json_atomic(root / COUNTER_FILE, {"last": last})
    return last


def _allocate_run_dir(root: Path) -> tuple[str, Path]:
    """Номер под замком: max(каталоги ∪ counter.last) + 1, mkdir без
    exist_ok — занятое имя даёт следующий номер; счётчик поднимается."""
    nums = [int(x.split("-")[1]) for x in _existing_run_ids(root)]
    n = max([*nums, _read_counter(root)], default=0) + 1
    while True:
        run = "run-%04d" % n
        try:
            (root / run).mkdir(parents=True, exist_ok=False)
            break
        except FileExistsError:
            n += 1
    _raise_counter(root, n)
    return run, root / run


def _write_new_run(run: str, run_dir: Path, doc: dict) -> None:
    """Первая запись нового прогона; срыв записи убирает пустой каталог,
    чтобы обрыв не оставлял «прогон без записи» (номер уже поднят —
    заново не выдаётся)."""
    try:
        _write_json_atomic(_run_path(run), doc)
    except BaseException:
        try:
            run_dir.rmdir()
        except OSError:
            pass
        raise


def _read_order(root: Path) -> list[str] | None:
    doc = _read_json_opt(root / ORDER_FILE)
    if not isinstance(doc, dict) or not isinstance(doc.get("order"), list):
        return None
    return [str(x) for x in doc["order"]]


def effective_order() -> list[str]:
    """Ручной порядок (только существующие, без повторов), затем прочие
    по имени; без order.json — по имени."""
    root = runs_root()
    if root is None:
        return []
    existing = _existing_run_ids(root)
    seen: set[str] = set()
    out: list[str] = []
    for rid in _read_order(root) or []:
        if rid in existing and rid not in seen:
            out.append(rid)
            seen.add(rid)
    out.extend(rid for rid in existing if rid not in seen)
    return out


def set_order(order) -> dict:
    """Ручной порядок списка (дословно CDPM): каждый номер по образцу и
    существующий, иначе 400 и ничего не записано; повторы схлопываются."""
    if not isinstance(order, list):
        raise RunsError("в теле запроса нужен список order", status=400,
                        code="bad-order")
    with storage_lock() as root:
        existing = set(_existing_run_ids(root))
        cleaned: list[str] = []
        for rid in order:
            rid = str(rid)
            if not RUN_ID_RE.fullmatch(rid):
                raise RunsError("недопустимый номер прогона %r" % rid,
                                status=400, code="bad-run-id")
            if rid not in existing:
                raise RunsError("прогон %s не существует — порядок не "
                                "записан" % rid, status=400,
                                code="not-found")
            if rid not in cleaned:
                cleaned.append(rid)
        _write_json_atomic(root / ORDER_FILE, {"order": cleaned})
        return {"order": effective_order()}


# ----------------------------------------------------- текущий прогон

def _current_run_id(root: Path) -> str | None:
    doc = _read_json_opt(root / CURRENT_FILE)
    run = doc.get("run") if isinstance(doc, dict) else None
    if isinstance(run, str) and RUN_ID_RE.fullmatch(run) \
            and (root / run).is_dir():
        return run
    return None


def _write_current_pointer(root: Path, run: str | None, by: str) -> None:
    _write_json_atomic(root / CURRENT_FILE,
                       {"run": run, "at": _now_iso(), "by": by})


def current_run_id() -> str | None:
    root = runs_root()
    if root is None or storage_state() != "ready":
        return None
    return _current_run_id(root)


def current_run_state() -> tuple[str | None, dict | None, str | None]:
    """(номер текущего прогона, его запись, слова отказа чтения) —
    чтение без побочных действий. Хранилище не заведено, указателя нет
    или он ведёт в никуда — (None, None, None); запись есть, но не
    читается — (run, None, <слова>): читатели называют код
    run-unreadable и номер прогона, а не молчат «пакета нет» (ревью
    реализации 07.09)."""
    run = current_run_id()
    if run is None:
        return None, None, None
    try:
        return run, read_run(run), None
    except RunsError as e:
        return run, None, str(e)


def current_run() -> dict | None:
    """Запись текущего прогона; хранилище не заведено, указателя нет,
    он ведёт в никуда или запись не читается — None (причина —
    current_run_state())."""
    return current_run_state()[1]


def _unreadable_run(run: str, error: str | None) -> dict:
    """Форма «прогон с отказом чтения» для чипа шапки, переключателя и
    GET current: номер есть, состояние неизвестно, причина кодом и словами
    (та же форма, что у записи списка с error_code)."""
    return {"run": run, "title": None, "state_kind": None,
            "error_code": "run-unreadable", "error": error}


def _current_pkg(doc: dict | None) -> dict | None:
    if not doc:
        return None
    sha = doc.get("current_package")
    for pkg in doc.get("packages") or []:
        if isinstance(pkg, dict) and pkg.get("sha") == sha:
            return pkg
    return None


def package_path_of(pkg: dict) -> Path | None:
    """Файл пакета записи: путь, как его записал канал; нет файла —
    запасной путь во входном каталоге (переезд корня данных); иначе None
    (код package-bytes-missing у читателей)."""
    path = pkg.get("path")
    if isinstance(path, str) and path and Path(path).is_file():
        return Path(path)
    from webapp import task_channel
    box = task_channel.inbox_dir()
    if box is not None:
        alt = box / str(pkg.get("sha") or "") / task_channel.PACKAGE_NAME
        if alt.is_file():
            return alt
    return None


def workdir_of_package(pkg: dict) -> Path:
    return data_root() / str(pkg.get("workdir") or
                             "%s/%s" % (PACKAGE_RUNS_DIR, pkg.get("sha")))


def workdir_of(cur: dict | None, sha: str) -> Path:
    """Рабочий каталог пары (прогон, пакет) по резолвуту текущего пакета:
    cur.run.workdir относительно каталога данных; без прогона —
    package_runs.workdir_for(sha) (одним аргументом: его подменяют
    стенды)."""
    run = (cur or {}).get("run")
    if isinstance(run, dict) and run.get("workdir"):
        return data_root() / str(run["workdir"])
    from webapp import package_runs as pr
    return pr.workdir_for(sha)


def current_package_record() -> tuple[dict | None, dict | None]:
    """(запись текущего прогона, запись его текущего пакета) — для
    резолвера канала; (doc, None) у прогона без пакета."""
    doc = current_run()
    if doc is None:
        return None, None
    return doc, _current_pkg(doc)


def _write_mirror_for(run: str | None, repeat: bool | None = None) -> str | None:
    """Зеркало task-inbox/current.json — ПОСЛЕДНИМ шагом и только из
    прогона: содержимое как у канала (repeat — повторный приём уже
    принятого sha, как писал канал); у текущего прогона без пакета
    зеркало удаляется. Сбой — заметка словами (не исключение)."""
    from webapp import task_channel
    box = task_channel.inbox_dir()
    if box is None:
        return None
    try:
        doc = read_run(run) if run else None
        pkg = _current_pkg(doc)
        target = box / task_channel.CURRENT_NAME
        if pkg is None:
            target.unlink(missing_ok=True)
            return None
        path = pkg.get("path") or str(box / str(pkg.get("sha"))
                                      / task_channel.PACKAGE_NAME)
        mirror = {"sha": pkg.get("sha"), "path": path,
                  "received_at": pkg.get("last_received_at")
                  or pkg.get("received_at"),
                  "spec_version": pkg.get("spec_version"),
                  "source_run": pkg.get("source_run"),
                  "source": pkg.get("source")}
        if repeat or (repeat is None and int(pkg.get("receipts") or 1) > 1):
            mirror["repeat"] = True
        box.mkdir(parents=True, exist_ok=True)
        task_channel._write_json_atomic(target, mirror)
        return None
    except (OSError, RunsError) as e:
        return "зеркало указателя канала не записано: %s" % str(e)[:200]


def _set_current_locked(root: Path, run: str, by: str) -> str | None:
    _write_current_pointer(root, run, by)
    return _write_mirror_for(run)


def _refuse_switch(target: str | None, *, check_env: bool = True) -> None:
    """Отказы смены текущего прогона: пакет задан переменной
    CREW_TASK_PACKAGE (текущий прогон не действует) либо в текущем
    прогоне идёт нажатие."""
    if check_env and env_package():
        raise RunsError("пакет задан переменной CREW_TASK_PACKAGE — текущий "
                        "прогон не действует", code="env-package")
    root = _root_or_raise()
    cur = _current_run_id(root)
    if cur is None or cur == target:
        return
    try:
        busy = busy_of(read_run(cur))
    except RunsError:
        busy = None
    if busy:
        raise RunsError("в текущем прогоне идёт нажатие (%s · %s) — "
                        "дождитесь его конца, текущий прогон не сменён"
                        % (busy.get("task") or "?",
                           busy.get("progress") or "идёт"),
                        code="current-busy")


def set_current(run: str, by: str) -> dict:
    """PUT current: указатель и зеркало (без пакета — зеркало удаляется);
    400/404/409 словами."""
    run = check_run_id(run)
    with storage_lock() as root:
        ensure_storage(by)
        read_run(run)
        _refuse_switch(run)
        note = _set_current_locked(root, run, by)
        out = current_state()
        if note:
            out["mirror_note"] = note
        return out


def _clean_title(title) -> str | None:
    title = str(title or "").strip()
    if len(title) > TITLE_MAX:
        raise RunsError("название длиннее %d символов" % TITLE_MAX,
                        status=400, code="title-too-long")
    return title or None


def set_title(run: str, title) -> dict:
    run = check_run_id(run)
    clean = _clean_title(title)

    def _fn(doc: dict) -> None:
        doc["title"] = clean
    _mutate_run(run, _fn)
    return {"run": run, "title": clean}


# ---------------------------------------------------------- создание

def create_run(title=None, *, origin: str = "manual",
               created_by: str = "user", by: str | None = None,
               make_current: bool = True) -> dict:
    """Новый прогон: номер под замком, запись с паспортом окружения при
    рождении; make_current — указатель и зеркало (с отказами
    env-package / current-busy)."""
    clean = _clean_title(title)
    by = by or created_by
    with storage_lock() as root:
        ensure_storage(by)
        if make_current:
            _refuse_switch(None)
        run, run_dir = _allocate_run_dir(root)
        now = _now_iso()
        doc = {"schema": SCHEMA_RUN, "run": run, "title": clean,
               "created_at": now, "created_by": created_by,
               "origin": origin, "current_package": None, "packages": [],
               "passport": {"environment": environment(), "at": now},
               "adoption": None, "notes": []}
        _write_new_run(run, run_dir, doc)
        note = None
        if make_current:
            note = _set_current_locked(root, run, by)
        out = dict(doc)
        out["current"] = bool(make_current)
        if note:
            out["mirror_note"] = note
        return out


# ------------------------------------------------- рабочие каталоги

def _holder_path(wd: Path) -> Path:
    return wd / HOLDER_FILE


def read_holder(wd: Path) -> dict | None:
    doc = _read_json_opt(_holder_path(wd))
    return doc if isinstance(doc, dict) else None


def claim_holder(wd: Path, sha: str, run: str | None, kind: str) -> bool:
    """Атомарный захват маркера держателя рабочего каталога
    (open(..., "x")); занят — False, маркер не трогается."""
    wd.mkdir(parents=True, exist_ok=True)
    doc = {"schema": SCHEMA_HOLDER, "sha": sha, "run": run, "kind": kind,
           "at": _now_iso()}
    try:
        fd = os.open(str(_holder_path(wd)),
                     os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False, indent=1) + "\n")
    return True


def _base_taken_by_other_run(root: Path, run: str, sha: str) -> bool:
    """Базовый каталог без маркера, но записанный рабочим каталогом
    другого прогона (оформленный пакет без нажатий — маркера ещё нет)."""
    base_rel = "%s/%s" % (PACKAGE_RUNS_DIR, sha)
    for rid in _existing_run_ids(root):
        if rid == run:
            continue
        doc = _read_json_opt(_run_path(rid))
        for pkg in (doc.get("packages") if isinstance(doc, dict) else None) or []:
            if isinstance(pkg, dict) and pkg.get("sha") == sha \
                    and pkg.get("workdir") == base_rel:
                return True
    return False


def _choose_workdir(root: Path, run: str, sha: str) -> str:
    """Рабочий каталог пары (прогон, sha): базовый package-runs/<sha>,
    если он свободен (захват маркера), иначе package-runs/<sha>/runs/<run>."""
    base = package_runs_root() / sha
    base_rel = "%s/%s" % (PACKAGE_RUNS_DIR, sha)
    holder = read_holder(base)
    if holder is None and not _base_taken_by_other_run(root, run, sha):
        if claim_holder(base, sha, run, "run"):
            return base_rel
        holder = read_holder(base)
    if holder is not None and holder.get("run") == run:
        return base_rel
    sub = base / "runs" / run
    claim_holder(sub, sha, run, "run")
    return "%s/runs/%s" % (base_rel, run)


# ------------------------------------------------------- присоединение

def _zip_manifest(path: Path) -> tuple[dict | None, str | None]:
    """(manifest.json пакета, sha256 его байтов = packageFingerprint
    решателя); пакет не читается — (None, None)."""
    try:
        with zipfile.ZipFile(path) as zf:
            raw = zf.read("manifest.json")
        man = json.loads(raw)
        return (man if isinstance(man, dict) else None,
                hashlib.sha256(raw).hexdigest())
    except Exception:                                    # noqa: BLE001
        return None, None


def _package_entry(sha: str, path: Path | None, *, source: str,
                   brief: dict | None, manifest: dict | None,
                   manifest_sha256: str | None, at: str,
                   note_code: str | None = None) -> dict:
    man = manifest if isinstance(manifest, dict) else {}
    brief = brief if isinstance(brief, dict) else {}
    return {
        "sha": sha,
        "path": str(path) if path is not None else None,
        "workdir": None,
        "source": source,
        "source_run": brief.get("source_run") or man.get("source_run"),
        "spec_version": brief.get("spec_version") or man.get("spec_version"),
        "revision": man.get("revision"),
        "customer": man.get("customer"),
        "verdict": brief.get("verdict"),
        "mg": brief.get("mg") if isinstance(brief.get("mg"), dict) else {},
        "received_at": at, "last_received_at": at, "receipts": 1,
        "manifest_sha256": manifest_sha256,
        # Запись о прогоне (проект 10.09.2026, разделы 4 и 10): номер
        # записи хозяина, отпечаток её головы и адрес ручек записи —
        # прямо в записи пакета. Пакеты прежних выпусков этих ключей не
        # несут: у них здесь пусто, и читатель (гейт «от пакета») судит
        # прогон по манифесту, а не гадает.
        "record_id": man.get("record_id") or None,
        "declaration_sha256": man.get("declaration_sha256") or None,
        "record_api": man.get("record_api") or None,
        "made_by": man.get("made_by") if isinstance(man.get("made_by"), dict)
        else None,
        "kit": man.get("kit") if isinstance(man.get("kit"), dict) else None,
        "data_fingerprint": man.get("data_fingerprint")
        if isinstance(man.get("data_fingerprint"), dict) else None,
        "files_count": len(man.get("files") or []) if isinstance(
            man.get("files"), list) else None,
        "note_code": note_code,
    }


def attach_package(sha: str, path, *, source: str, brief: dict | None = None,
                   manifest: dict | None = None,
                   by: str | None = None, adopt_exclude: bool = True) -> dict:
    """Пакет — в ТЕКУЩИЙ прогон (канал не отказывает: нет текущего —
    прогон создаётся сам, origin auto-<источник>). Повтор того же sha в
    тот же прогон — receipts += 1 и пакет снова текущий. Возврат:
    {"run", "workdir", "created", "repeat"[, "mirror_note"]} либо
    {"run": None, "error_code", "error"} — приём при этом не рушится.

    adopt_exclude — исключить sha из оформления прежних данных, если
    хранилище заводится этим же действием. Верно ТОЛЬКО для первичного
    приёма: его каталог task-inbox/<sha> уже лежит с verdict.json до
    присоединения, и без исключения пакет оказался бы в двух прогонах.
    Повторный приём уже принятого пакета (adopt_exclude=False) оформляет
    sha его собственным прогоном по времени первого принятого приёма —
    иначе повтор на незаведённой площадке подменял бы происхождение и
    дату получения и ломал «по одному прогону на принятый пакет» (ревью
    реализации 07.09)."""
    sha = str(sha or "").lower()
    by = by or source
    if not SHA_RE.fullmatch(sha):
        return {"run": None, "error_code": "bad-sha",
                "error": "отпечаток пакета не похож на sha256"}
    p = Path(str(path or ""))
    if not p.is_file():
        return {"run": None, "error_code": "package-bytes-missing",
                "error": "файла пакета нет на диске (%s) — прогон не "
                         "тронут" % (p.name or "путь пуст")}
    try:
        with storage_lock() as root:
            ensure_storage(by, exclude_sha=sha if adopt_exclude else None)
            created = False
            run = _current_run_id(root)
            if run is None:
                origin = "auto-receive" if source == "channel" \
                    else "auto-" + source
                doc = create_run(None, origin=origin,
                                 created_by=source, by=by, make_current=False)
                run = doc["run"]
                created = True
                _write_current_pointer(root, run, by)
            now = _now_iso()
            got: dict = {}

            def _fn(doc: dict) -> None:
                pkgs = [x for x in (doc.get("packages") or [])
                        if isinstance(x, dict)]
                hit = next((x for x in pkgs if x.get("sha") == sha), None)
                if hit is not None:
                    hit["receipts"] = int(hit.get("receipts") or 1) + 1
                    hit["last_received_at"] = now
                    if not hit.get("path") or not Path(hit["path"]).is_file():
                        hit["path"] = str(p)
                    hit["note_code"] = None
                    got.update(workdir=hit.get("workdir"), repeat=True)
                else:
                    man, man_sha = _zip_manifest(p)
                    entry = _package_entry(
                        sha, p, source=source, brief=brief,
                        manifest=manifest if isinstance(manifest, dict)
                        else man, manifest_sha256=man_sha, at=now)
                    entry["workdir"] = _choose_workdir(root, doc["run"], sha)
                    pkgs.append(entry)
                    got.update(workdir=entry["workdir"], repeat=False)
                doc["packages"] = pkgs
                doc["current_package"] = sha
            _mutate_run(run, _fn)
            out = {"run": run, "workdir": got.get("workdir"),
                   "created": created, "repeat": bool(got.get("repeat"))}
            note = _write_mirror_for(
                run, repeat=bool(got.get("repeat"))
                or bool((brief or {}).get("already_accepted")))
            if note:
                out["mirror_note"] = note
            return out
    except RunsError as e:
        return {"run": None, "error_code": e.code or "runs-error",
                "error": str(e)}
    except (OSError, TimeoutError, ValueError) as e:
        return {"run": None, "error_code": "run-not-written",
                "error": str(e)[:300]}


# ------------------------------------------------------------ архив

def _busy_line(wd: Path, st: dict) -> tuple[str, str | None] | None:
    """(линия, ход) занятого рабочего каталога с живой нитью, иначе None."""
    from webapp import package_runs as pr
    lines = getattr(pr, "_LINES", {})
    alive = getattr(pr, "_line_alive", None)
    for line in ("press", "export"):
        spec = lines.get(line)
        if not spec:
            continue
        try:
            if spec["busy"](st) and (alive is None or alive(wd, line)):
                return line, (st.get("progress") if line == "press"
                              else st.get("export_mps_state"))
        except Exception:                                # noqa: BLE001
            continue
    return None


def _source_check_snapshots() -> list[tuple[str | None, dict]]:
    """Снимки сверки по ИДАК площадки: (задача | None, снимок)."""
    root = data_root() / "source-check"
    out = []
    for task in (None, "pairing", "rostering"):
        p = (root / task / "last.json") if task else (root / "last.json")
        doc = _read_json_opt(p) if p.is_file() else None
        if isinstance(doc, dict):
            out.append((task, doc))
    return out


def _source_check_running_for(run: str) -> dict | None:
    for task, doc in _source_check_snapshots():
        if doc.get("status") == "running" and doc.get("cosmos_run") == run \
                and doc.get("server_pid") == os.getpid():
            return {"task": "source-check:%s" % (task or "all"),
                    "progress": "сверка по ИДАК идёт",
                    "sha12": str(doc.get("package_sha") or "")[:12]}
    return None


def busy_of(doc: dict) -> dict | None:
    """Занятость прогона: любой его рабочий каталог с занятой линией
    press/export и живой нитью либо идущая сверка по ИДАК этого прогона."""
    from webapp import package_runs as pr
    for pkg in doc.get("packages") or []:
        if not isinstance(pkg, dict):
            continue
        wd = workdir_of_package(pkg)
        st = pr.read_state(wd)
        if not st:
            continue
        hit = _busy_line(wd, st)
        if hit:
            line, progress = hit
            return {"task": (st.get("task") if line == "press" else "export"),
                    "progress": progress, "sha12": str(pkg.get("sha"))[:12],
                    "line": line}
    return _source_check_running_for(str(doc.get("run") or ""))


def archive(ids) -> dict:
    """Архив выбранных прогонов (fail-closed): все проверки, потом
    переносы os.rename в cosmos-runs-archive; текущий и занятые
    отказывают словами; order.json чистится, counter поднимается."""
    if not isinstance(ids, list) or not ids:
        raise RunsError("в теле запроса нужен непустой список runs",
                        status=400, code="bad-runs")
    with storage_lock() as root:
        clean: list[str] = []
        for rid in ids:
            rid = str(rid)
            if not RUN_ID_RE.fullmatch(rid):
                raise RunsError("недопустимый номер прогона %r" % rid,
                                status=400, code="bad-run-id")
            if not (root / rid).is_dir():
                raise RunsError("прогон %s не существует — в архив ничего "
                                "не перенесено" % rid, status=404,
                                code="not-found")
            if rid not in clean:
                clean.append(rid)
        unreadable: list[str] = []
        busy: list[str] = []
        current = _current_run_id(root)
        for rid in clean:
            try:
                doc = read_run(rid)
            except RunsError:
                unreadable.append(rid)
                continue
            b = busy_of(doc)
            if b:
                busy.append("%s (%s · %s)" % (rid, b.get("task") or "?",
                                              b.get("progress") or "идёт"))
        if unreadable:
            raise RunsError("у прогонов не прочитана запись прогона — "
                            "ничего не перенесено: " + ", ".join(unreadable),
                            code="run-unreadable")
        if busy:
            raise RunsError("прогоны заняты нажатием — дождитесь его конца, "
                            "ничего не перенесено: " + ", ".join(busy),
                            code="run-busy")
        if current in clean:
            raise RunsError("%s — текущий прогон: сначала сделайте текущим "
                            "другой, ничего не перенесено" % current,
                            code="current-run")
        arch = root.parent / ARCHIVE_DIR_NAME
        arch.mkdir(parents=True, exist_ok=True)
        archived: list[str] = []
        failure: str | None = None
        for rid in clean:
            dest = arch / rid
            if dest.exists():
                dest = arch / ("%s-%s" % (rid, time.strftime("%Y%m%d-%H%M%S")))
            try:
                os.rename(root / rid, dest)
            except OSError as e:
                failure = "%s: перенос в архив не удался (%s)" % (rid, e)
                break
            archived.append(rid)
        if archived:
            stored = _read_order(root)
            if stored is not None:
                _write_json_atomic(root / ORDER_FILE, {
                    "order": [x for x in stored if x not in archived]})
            _raise_counter(root, max(int(x.split("-")[1]) for x in archived))
        if failure:
            raise RunsError(failure + ("; уже в архиве: " + ", ".join(archived)
                                       if archived else ""), status=500,
                            code="archive-failed")
        return {"archived": archived, "archive_dir": str(arch.resolve()),
                "left": len(_existing_run_ids(root))}


# ---------------------------------------------- оформление легаси

def _inbox_candidates(exclude: str | None) -> tuple[list[dict], list[dict]]:
    """(принятые пакеты инбокса, пропущенные с причиной)."""
    from webapp import task_channel
    box = task_channel.inbox_dir()
    cands: list[dict] = []
    skipped: list[dict] = []
    if box is None or not box.is_dir():
        return cands, skipped
    for d in sorted(box.iterdir()):
        if not d.is_dir() or not SHA_RE.fullmatch(d.name):
            continue
        if d.name == exclude:
            continue
        pkg = d / task_channel.PACKAGE_NAME
        verdict = d / task_channel.VERDICT_NAME
        if not verdict.is_file():
            skipped.append({"sha12": d.name[:12],
                            "reason_code": "receipt-incomplete"})
            continue
        if not pkg.is_file():
            skipped.append({"sha12": d.name[:12],
                            "reason_code": "package-bytes-missing"})
            continue
        cands.append({"sha": d.name, "path": pkg, "verdict_path": verdict,
                      "source": "channel", "from": "task-inbox"})
    return cands, skipped


#: Виды источника пакета, которые несут зеркало канала и строки журнала
#: приёмов (task_channel.receive: «channel» — доставка каналом, «upload» —
#: загрузка кнопкой); всё прочее оформление считает каналом.
_RECEIPT_SOURCES = ("channel", "upload")


def _first_accepted_receipts() -> dict[str, dict]:
    """sha → первая ПРИНЯТАЯ строка журнала приёмов (repeat false, вердикт
    OK/MISMATCH): {"at", "source"}; отказы без каталога не считаются.
    Вид источника несут строки с 07.09 (ключ source); у прежних строк его
    нет — None."""
    from webapp import task_channel
    box = task_channel.inbox_dir()
    out: dict[str, dict] = {}
    if box is None:
        return out
    p = box / task_channel.RECEIPTS_NAME
    try:
        lines = p.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            row = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("repeat"):
            continue
        if row.get("verdict") not in ("OK", "MISMATCH"):
            continue
        sha = str(row.get("sha") or "")
        if sha and sha not in out and row.get("at"):
            src = row.get("source")
            out[sha] = {"at": str(row["at"]),
                        "source": src if src in _RECEIPT_SOURCES else None}
    return out


def _read_mirror() -> dict | None:
    """Зеркало указателя канала task-inbox/current.json (чтение); нет или
    кривое — None."""
    from webapp import task_channel
    box = task_channel.inbox_dir()
    doc = _read_json_opt(box / task_channel.CURRENT_NAME) if box is not None \
        else None
    return doc if isinstance(doc, dict) else None


def _file_mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone() \
            .isoformat(timespec="seconds")
    except OSError:
        return None


def adoption_plan(exclude: str | None = None) -> dict:
    """План оформления легаси (только чтение): кандидаты по времени
    первого принятого приёма, пропуски с причинами, уже оформленные."""
    from webapp import package_runs as pr
    root = runs_root()
    inbox_cands, skipped_inbox = _inbox_candidates(exclude)
    inbox_shas = {c["sha"] for c in inbox_cands}
    if exclude:
        inbox_shas.add(exclude)
    pr_root = package_runs_root()
    file_cands: list[dict] = []
    skipped_dirs = 0
    if pr_root.is_dir():
        for d in sorted(pr_root.iterdir()):
            if not d.is_dir() or not SHA_RE.fullmatch(d.name) \
                    or d.name in inbox_shas:
                continue
            if not (d / pr.STATE_NAME).is_file():
                skipped_dirs += 1
                continue
            st = pr.read_state(d)
            path = st.get("package_path")
            pp = Path(str(path)) if isinstance(path, str) and path else None
            file_cands.append({
                "sha": d.name, "path": pp if pp and pp.is_file() else None,
                "verdict_path": None, "source": "file", "from": "package-runs",
                "note_code": None if pp and pp.is_file()
                else "package-bytes-missing",
                "state": st})
    receipts = _first_accepted_receipts()
    # Вид источника принятого пакета: строка журнала приёмов (ключ source
    # у строк с 07.09), у пакета зеркала — само зеркало; иначе умолчание
    # «channel» (ревью реализации 07.09: загруженный рукой пакет после
    # оформления показывался как «канал приёма»).
    mirror = _read_mirror() or {}
    mirror_sha = str(mirror.get("sha") or "")
    mirror_source = mirror.get("source") if mirror.get("source") in _RECEIPT_SOURCES \
        else None
    # уже оформленные: маркер держателя в базовом каталоге либо sha в
    # записи какого-то прогона активного корня
    adopted: set[str] = set()
    if root is not None:
        for rid in _existing_run_ids(root):
            doc = _read_json_opt(_run_path(rid))
            for pkg in (doc.get("packages") if isinstance(doc, dict)
                        else None) or []:
                if isinstance(pkg, dict) and pkg.get("sha"):
                    adopted.add(str(pkg["sha"]))
    plan: list[dict] = []
    for c in inbox_cands + file_cands:
        sha = c["sha"]
        base = pr_root / sha
        holder = read_holder(base)
        c["adopted"] = sha in adopted or (holder is not None
                                          and holder.get("kind") == "run")
        row = receipts.get(sha) or {}
        if c["from"] == "task-inbox":
            known = row.get("source") or (mirror_source if sha == mirror_sha
                                          else None)
            if known:
                c["source"] = known
        at = row.get("at")
        if not at and c.get("path"):
            at = _file_mtime_iso(c["path"])
        if not at and c.get("state"):
            first = next((r.get("started_at") for r in
                          (c["state"].get("attempts") or [])
                          if isinstance(r, dict) and r.get("started_at")),
                         None)
            at = first or c["state"].get("started_at")
        c["at"] = at
        c["at_norm"] = _normalize_iso(at) if at else None
        c["has_state"] = (base / pr.STATE_NAME).is_file()
        c["attempts"] = len([r for r in (
            (c.get("state") or pr.read_state(base)).get("attempts") or [])
            if isinstance(r, dict)]) if c["has_state"] else 0
        plan.append(c)
    plan.sort(key=lambda c: (c["at_norm"] or c["at"] or "~", c["sha"]))
    return {"candidates": plan, "skipped_inbox": skipped_inbox,
            "skipped_dirs": skipped_dirs}


def legacy_summary() -> dict | None:
    """Сводка легаси для списка при незаведённом хранилище (чтение)."""
    if storage_state() != "uninitialized":
        return None
    inbox_cands, _skipped = _inbox_candidates(None)
    mirror = _read_mirror()
    cur_sha = mirror.get("sha") if mirror else None
    pr_root = package_runs_root()
    inbox_shas = {c["sha"] for c in inbox_cands}
    without = 0
    if pr_root.is_dir():
        from webapp import package_runs as pr
        without = len([d for d in pr_root.iterdir()
                       if d.is_dir() and SHA_RE.fullmatch(d.name)
                       and d.name not in inbox_shas
                       and not (d / pr.STATE_NAME).is_file()])
    return {"packages": len(inbox_cands),
            "current_sha12": str(cur_sha)[:12] if cur_sha else None,
            "dirs_without_state": without}


def _adopt(root: Path, by: str, exclude: str | None) -> dict:
    """Оформление легаси прогонами (пишущий путь под замком): по одному
    прогону на принятый пакет, порядок — по первому принятому приёму;
    state.json не трогаются; маркер держателя — в существующий базовый
    каталог."""
    plan = adoption_plan(exclude)
    created: list[dict] = []
    notes: list[str] = []
    for c in plan["candidates"]:
        if c["adopted"]:
            continue
        sha = c["sha"]
        brief = _read_json_opt(c["verdict_path"]) if c.get("verdict_path") else None
        if c.get("verdict_path") and not isinstance(brief, dict):
            notes.append("%s: вердикт приёма не читается" % sha[:12])
            brief = None
        man, man_sha = _zip_manifest(c["path"]) if c.get("path") else (None, None)
        now = _now_iso()
        run, run_dir = _allocate_run_dir(root)
        entry = _package_entry(
            sha, c.get("path"), source=c["source"], brief=brief,
            manifest=man, manifest_sha256=man_sha,
            at=c["at_norm"] or now, note_code=c.get("note_code"))
        entry["workdir"] = "%s/%s" % (PACKAGE_RUNS_DIR, sha)
        base = package_runs_root() / sha
        doc = {"schema": SCHEMA_RUN, "run": run, "title": None,
               "created_at": c["at_norm"] or now, "created_by": "adoption",
               "origin": "adoption", "current_package": sha,
               "packages": [entry],
               "passport": {"environment": environment(), "at": now},
               "adoption": {"from": c["from"], "at": now,
                            "notes": ([] if c["at_norm"] or not c["at"]
                                      else ["время приёма не разобрано: %s"
                                            % c["at"]])},
               "notes": []}
        _write_new_run(run, run_dir, doc)
        if base.is_dir():
            claim_holder(base, sha, run, "run")
        row = {"at": now, "run": run, "sha": sha, "from": c["from"],
               "attempts": c.get("attempts", 0), "by": by}
        with open(root / ADOPTION_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        created.append({"run": run, "sha12": sha[:12], "from": c["from"],
                        "attempts": c.get("attempts", 0)})
    # указатель текущего — держатель sha из зеркала канала
    mirror = _read_mirror()
    mirror_sha = str(mirror.get("sha") or "") if mirror else ""
    holder_run = None
    if mirror_sha:
        for rid in _existing_run_ids(root):
            d = _read_json_opt(_run_path(rid))
            if isinstance(d, dict) and any(
                    isinstance(p, dict) and p.get("sha") == mirror_sha
                    for p in d.get("packages") or []):
                holder_run = rid
                break
    if _current_run_id(root) is None:
        _write_current_pointer(root, holder_run, by)
    ids = _existing_run_ids(root)
    if ids:
        _raise_counter(root, max(int(x.split("-")[1]) for x in ids))
    return {"runs_created": len(created), "created": created,
            "packages_adopted": len(created),
            "skipped_inbox": plan["skipped_inbox"],
            "skipped_dirs": plan["skipped_dirs"],
            "current_sha12": mirror_sha[:12] or None,
            "current_run": _current_run_id(root),
            "notes": notes}


def ensure_storage(by: str = "init", *, exclude_sha: str | None = None) -> dict:
    """Инициализация хранилища ТОЛЬКО из пишущих путей: заведено —
    дёшево (один stat); иначе оформление легаси по состоянию
    (идемпотентно, обрыв посередине безопасен) и storage.json ПОСЛЕДНИМ."""
    with storage_lock() as root:
        if (root / STORAGE_FILE).is_file():
            # Уже заведено — ничего не оформлялось: сводка честно «0»
            # (кнопка «Оформить прогоны» и CLI показывают число).
            return {"initialized": False, "state": "ready",
                    "adoption": {"runs_created": 0}}
        summary = _adopt(root, by, exclude_sha)
        _write_json_atomic(root / STORAGE_FILE, {
            "schema": SCHEMA_STORAGE, "initialized_at": _now_iso(),
            "by": by, "adoption": summary})
        return {"initialized": True, "state": "ready", "adoption": summary}


# ------------------------------------------------------- попытки прогона

def _aliases() -> dict:
    """Словарь «алиас → (каталог, файл)» артефактов снимка попытки —
    один на панель: package_runs.ALLOWED_FILES (разделы 7.6, 17.3)."""
    from webapp import package_runs as pr
    return dict(pr.ALLOWED_FILES)


def _attempt_kind(rec: dict) -> str:
    task = str(rec.get("task") or "")
    if task == "rostering" and rec.get("pairing_selection"):
        return "rostering_on_pairing"
    return task or "?"


def _attempt_out_name(rec: dict) -> str:
    cat = str(rec.get("catalog") or "")
    if cat.startswith(ATTEMPTS_DIR + "/"):
        parts = cat.split("/")
        if len(parts) >= 3 and parts[2]:
            return parts[2]
    kind = _attempt_kind(rec)
    return {"pairing": "button-pairing",
            "rostering_on_pairing": "button-on-pairing"}.get(kind, "button")


def _run_of_record(doc: dict, holder: dict | None, rec: dict) -> str | None:
    """Прогон попытки: ключ записи (новые попытки), иначе держатель
    рабочего каталога по holder.json, иначе сам прогон (инвариант 2.4:
    членство — каталог, ключ run — информационная копия)."""
    if rec.get("run"):
        return str(rec["run"])
    if isinstance(holder, dict) and holder.get("run"):
        return str(holder["run"])
    return doc.get("run")


def attempts_of(doc: dict) -> list[dict]:
    """Попытки всех рабочих каталогов прогона (членство — каталог),
    новые первыми; каждая — с package_sha, workdir, kind, run."""
    from webapp import package_runs as pr
    rows: list[tuple] = []
    for idx, pkg in enumerate(doc.get("packages") or []):
        if not isinstance(pkg, dict):
            continue
        wd = workdir_of_package(pkg)
        st = pr.read_state(wd)
        holder = read_holder(wd)
        for rec in st.get("attempts") or []:
            if not isinstance(rec, dict) or not rec.get("id"):
                continue
            r = dict(rec)
            r["package_sha"] = pkg.get("sha")
            r["workdir"] = pkg.get("workdir")
            r["kind"] = _attempt_kind(rec)
            r["run"] = _run_of_record(doc, holder, rec)
            r["_alive"] = bool(rec.get("done") is False
                               and _busy_line(wd, st) is not None
                               and st.get("attempt_id") == rec.get("id"))
            rows.append((str(rec.get("started_at") or ""), idx,
                         str(rec.get("id")), r))
    rows.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return [t[3] for t in rows]


def resolve_attempt(run: str, attempt: str) -> tuple[str, Path, dict]:
    """(sha, рабочий каталог, запись попытки) по обходу
    packages[].workdir прогона; не найдено — RunsError 404."""
    run = check_run_id(run)
    attempt = check_attempt_id(attempt)
    from webapp import package_runs as pr
    doc = read_run(run)
    for pkg in doc.get("packages") or []:
        if not isinstance(pkg, dict):
            continue
        wd = workdir_of_package(pkg)
        st = pr.read_state(wd)
        for rec in st.get("attempts") or []:
            if isinstance(rec, dict) and rec.get("id") == attempt:
                out = dict(rec)
                # Попытке легаси без ключа run — держатель каталога
                # (чтение holder.json), как в списке и карточке.
                out["run"] = _run_of_record(doc, read_holder(wd), rec)
                return str(pkg.get("sha")), wd, out
    raise RunsError("попытка %s неизвестна в прогоне %s" % (attempt, run),
                    status=404, code="attempt-unknown")


def attempt_file(run: str, attempt: str, alias: str) -> tuple[Path, str]:
    """Артефакт снимка попытки по алиасу (словарь алиасов); нет —
    RunsError 404 словами."""
    aliases = _aliases()
    if alias not in aliases:
        raise RunsError("неизвестный артефакт %r; перечень: %s"
                        % (alias, ", ".join(sorted(aliases))), status=404,
                        code="alias-unknown")
    _sha, wd, rec = resolve_attempt(run, attempt)
    sub, name = aliases[alias]
    path = wd / ATTEMPTS_DIR / str(rec["id"]) / sub / name
    if not path.is_file():
        raise RunsError("артефакт %s в снимке попытки %s не найден"
                        % (alias, attempt), status=404,
                        code="artifact-missing")
    return path, name


def exchanges_dir(wd: Path, rec: dict) -> Path:
    return wd / ATTEMPTS_DIR / str(rec["id"]) / _attempt_out_name(rec) \
        / "llm-exchanges"


def _replay_availability(wd: Path, rec: dict, doc: dict) -> dict:
    """Доступность кнопок повтора — легко (deep=False, раздел 17.2:
    запись, два stat, хвост индекса; ни тел, ни зонда сертификатора)
    через package_runs.replay_availability; сбой проверки — честный отказ
    словами, карточка не падает."""
    from webapp import package_runs as pr
    of = (rec.get("replay") or {}).get("of") if isinstance(
        rec.get("replay"), dict) else None
    try:
        out = pr.replay_availability(wd, rec, doc, deep=False)
    except Exception as e:                               # noqa: BLE001
        na = {"available": False, "reason_code": "replay-unavailable",
              "reason_words": str(e)[:200]}
        return {"journal": dict(na), "fresh": dict(na), "of": of}
    out = dict(out or {})
    out.setdefault("of", of)
    return out


# ------------------------------------------------------- состояние

def _launch_of(doc: dict, pkg: dict | None, is_current: bool) -> dict | None:
    if pkg is None:
        return None
    if is_current and not env_package():
        try:
            from webapp import flow
            live = flow.readiness_state()
        except Exception:                                # noqa: BLE001
            live = {}
        cols = live.get("columns") if isinstance(live, dict) else None
        if isinstance(cols, dict) and cols:
            return {"pairing": bool((cols.get("pairing") or {})
                                    .get("launch_allowed")),
                    "rostering": bool((cols.get("rostering") or {})
                                      .get("launch_allowed")),
                    "as_of": "live"}
    mg = pkg.get("mg") if isinstance(pkg.get("mg"), dict) else {}
    return {"pairing": bool((mg.get("pairing") or {}).get("launch_allowed")),
            "rostering": bool((mg.get("rostering") or {}).get("launch_allowed")),
            "as_of": "received"}


def state_of(doc: dict, attempts: list[dict] | None = None,
             launch: dict | None = None) -> str:
    """Вид состояния прогона — выводится при чтении, не хранится."""
    pkgs = [p for p in (doc.get("packages") or []) if isinstance(p, dict)]
    if not pkgs:
        return "new"
    cur = _current_pkg(doc)
    if cur is not None and package_path_of(cur) is None:
        return "lost"
    attempts = attempts_of(doc) if attempts is None else attempts
    if not attempts:
        if launch is None:
            launch = _launch_of(doc, cur, doc.get("run") == current_run_id())
        if launch and not launch.get("pairing") and not launch.get("rostering"):
            return "blocked"
        return "accepted"
    last = attempts[0]
    state = str(last.get("state") or "")
    if last.get("done") is False:
        return "working" if last.get("_alive") else "interrupted"
    if "прервано" in state:
        return "interrupted"
    if last.get("error"):
        return "refused"
    verdict = str(last.get("verdict") or "")
    if verdict.startswith(("остановка", "ожидает")):
        return "stalled"
    return "done"


def _grade_vmp(wd: Path, task: str) -> str | None:
    from webapp import flow, package_runs as pr
    if task == "pairing":
        d = wd / "button-pairing"
    else:
        d = pr.roster_delivery_dir(wd)
        if d is None:
            return None
    delivery = _read_json_opt(d / "delivery.json")
    return flow.grade_delivery(delivery) if isinstance(delivery, dict) else None


def _grade_idak(doc: dict, pkg: dict | None, attempts: list[dict],
                task: str) -> str | None:
    run = str(doc.get("run") or "")
    sha = str((pkg or {}).get("sha") or "")
    for t, snap in _source_check_snapshots():
        if t != task or snap.get("status") != "done":
            continue
        if snap.get("package_sha") and sha and snap["package_sha"] != sha:
            continue
        if snap.get("cosmos_run") and snap["cosmos_run"] != run:
            continue
        counts = snap.get("counts") if isinstance(snap.get("counts"), dict) else {}
        return "bad" if int(counts.get("violated") or 0) > 0 else "ok"
    for rec in attempts:
        sc = rec.get("source_check")
        if isinstance(sc, dict) and rec.get("task") == task:
            counts = sc.get("counts") if isinstance(sc.get("counts"), dict) else {}
            return "bad" if int(counts.get("violated") or 0) > 0 else "ok"
    return None


def _checks_of(doc: dict, pkg: dict | None, attempts: list[dict]) -> dict:
    vmp = {"pairing": None, "rostering": None}
    if pkg is not None:
        wd = workdir_of_package(pkg)
        for task in ("pairing", "rostering"):
            try:
                vmp[task] = _grade_vmp(wd, task)
            except Exception:                            # noqa: BLE001
                vmp[task] = None
    return {"vmp": vmp,
            "idak": {task: _grade_idak(doc, pkg, attempts, task)
                     for task in ("pairing", "rostering")}}


def _attempts_summary(attempts: list[dict]) -> dict:
    last = attempts[0] if attempts else None
    out = {"total": len(attempts),
           "pairing": len([a for a in attempts if a.get("task") == "pairing"]),
           "rostering": len([a for a in attempts
                             if a.get("task") == "rostering"]),
           "running": len([a for a in attempts if a.get("_alive")]),
           "last": None}
    if last is not None:
        out["last"] = {"id": last.get("id"), "task": last.get("task"),
                       "kind": last.get("kind"),
                       "verdict": last.get("verdict"),
                       "progress": None, "error": last.get("error"),
                       "finished_at": last.get("finished_at"),
                       "started_at": last.get("started_at")}
        of = (last.get("replay") or {}).get("of") if isinstance(
            last.get("replay"), dict) else None
        if isinstance(of, dict):
            rep = of.get("report") if isinstance(of.get("report"), dict) else {}
            out["last"]["replay_of"] = {"mode": of.get("mode"),
                                        "source_attempt": of.get("source_attempt"),
                                        "verdict": rep.get("verdict")}
    return out


def list_record(doc: dict, position: int, current: str | None) -> dict:
    """Запись списка прогонов (раздел 9.3 проекта)."""
    run = str(doc.get("run"))
    is_current = run == current
    pkg = _current_pkg(doc)
    attempts = attempts_of(doc)
    launch = _launch_of(doc, pkg, is_current)
    busy = busy_of(doc)
    summary = _attempts_summary(attempts)
    if busy and summary["last"] and busy.get("line") == "press":
        summary["last"]["progress"] = busy.get("progress")
    kind = state_of(doc, attempts, launch)
    package = None
    if pkg is not None:
        package = {"sha": pkg.get("sha"), "sha12": str(pkg.get("sha"))[:12],
                   "source_run": pkg.get("source_run"),
                   "source": pkg.get("source"),
                   "spec_version": pkg.get("spec_version"),
                   "verdict": pkg.get("verdict"),
                   "received_at": pkg.get("last_received_at")
                   or pkg.get("received_at"),
                   "launch": launch,
                   "lost": package_path_of(pkg) is None}
    return {"run": run, "title": doc.get("title"), "current": is_current,
            "position": position, "created_at": doc.get("created_at"),
            "origin": doc.get("origin"),
            "state_kind": kind, "state": STATE_WORDS.get(kind, kind),
            "bucket": STATE_BUCKETS.get(kind),
            "package": package,
            "packages_count": len([p for p in doc.get("packages") or []
                                   if isinstance(p, dict)]),
            "attempts": summary,
            "checks": _checks_of(doc, pkg, attempts),
            "busy": ({"task": busy.get("task"), "progress": busy.get("progress"),
                      "sha12": busy.get("sha12")} if busy else None),
            "passport_summary": summary_of(doc, attempts)}


def list_runs() -> dict:
    """GET /api/cosmos-runs — только чтение: записи по действующему
    порядку, текущий, состояние хранилища, сводка легаси."""
    state = storage_state()
    out = {"runs": [], "current": None,
           "storage": {"state": state, "legacy": None},
           "env_package": bool(env_package()),
           "inbox_disabled": state == "off"}
    if state == "off":
        # причина выключения — словами у клиента по коду (cruns.inbox_off /
        # cruns.runs_dir_off), не одна фраза про CREW_TASK_INBOX на обе
        out["storage"]["cause"] = storage_off_cause()
        return out
    root = runs_root()
    if state == "uninitialized":
        out["storage"]["legacy"] = legacy_summary()
    current = _current_run_id(root) if state == "ready" else None
    out["current"] = current
    for pos, rid in enumerate(effective_order()):
        try:
            doc = read_run(rid)
        except RunsError as e:
            out["runs"].append({"run": rid, "title": None,
                                "current": rid == current, "position": pos,
                                "created_at": None, "origin": None,
                                "state_kind": None, "state": None,
                                "error_code": "run-unreadable",
                                "error": str(e), "package": None,
                                "packages_count": 0, "attempts": None,
                                "checks": None, "busy": None,
                                "passport_summary": None})
            continue
        try:
            out["runs"].append(list_record(doc, pos, current))
        except Exception as e:                           # noqa: BLE001
            out["runs"].append({"run": rid, "title": doc.get("title"),
                                "current": rid == current, "position": pos,
                                "created_at": doc.get("created_at"),
                                "origin": doc.get("origin"),
                                "state_kind": None, "state": None,
                                "error_code": "run-unreadable",
                                "error": str(e)[:200], "package": None,
                                "packages_count": 0, "attempts": None,
                                "checks": None, "busy": None,
                                "passport_summary": None})
    return out


def _hint_of(run: str | None, doc: dict | None) -> str | None:
    """Код подсказки по уже прочитанному состоянию текущего прогона."""
    if env_package():
        return "env-package"
    state = storage_state()
    if state == "off":
        return "storage-off"
    if state == "uninitialized":
        return "storage-not-initialized"
    if run is None:
        return "no-current-run"
    if doc is None:
        return "run-unreadable"
    return None


def hint_code() -> str | None:
    """Подсказка схеме и карточке: env-package | storage-off |
    storage-not-initialized | no-current-run | run-unreadable | null.
    run-unreadable — указатель есть, запись прогона не читается: схема и
    карточка называют причину, а не показывают «пакета нет»."""
    run, doc, _err = current_run_state()
    return _hint_of(run, doc)


def current_state() -> dict:
    """GET /api/cosmos-runs/current; нечитаемая запись текущего прогона —
    run с error_code run-unreadable и словами, package null."""
    run, doc, err = current_run_state()
    out = {"run": doc, "package": _current_pkg(doc),
           "storage": {"state": storage_state()},
           "env_package": bool(env_package()),
           "hint_code": _hint_of(run, doc)}
    if out["storage"]["state"] == "off":
        out["storage"]["cause"] = storage_off_cause()
    if run is not None and doc is None:
        out["run"] = _unreadable_run(run, err)
    return out


def implicit_run_of(sha: str) -> dict | None:
    """Неявный прогон пакета «файлом»: хранилище заведено и маркер
    базового каталога называет прогон (kind run)."""
    if not sha or storage_state() != "ready":
        return None
    holder = read_holder(package_runs_root() / sha)
    if not isinstance(holder, dict) or holder.get("kind") != "run" \
            or not holder.get("run"):
        return None
    run = str(holder["run"])
    title = None
    try:
        title = read_run(run).get("title")
    except RunsError:
        pass
    return {"run": run, "title": title,
            "workdir": "%s/%s" % (PACKAGE_RUNS_DIR, sha), "implicit": True}


def current_run_summary() -> dict | None:
    """Верхнеуровневый run для /api/flow: {"run", "title", "state_kind",
    "package_sha12", "packages_count", "implicit", "busy_previous"}."""
    if env_package():
        from webapp import task_channel
        cur = task_channel.current_package() or {}
        run = cur.get("run")
        if not isinstance(run, dict):
            return None
        try:
            doc = read_run(str(run["run"]))
        except RunsError:
            return None
        return {"run": doc["run"], "title": doc.get("title"),
                "state_kind": state_of(doc),
                "package_sha12": str(cur.get("sha") or "")[:12],
                "packages_count": len(doc.get("packages") or []),
                "implicit": True, "busy_previous": None}
    run, doc, err = current_run_state()
    if run is None:
        return None
    if doc is None:
        # Запись текущего прогона не читается: чип шапки показывает номер
        # и отказ словами (runs_hint_code run-unreadable), не «прогона нет».
        return dict(_unreadable_run(run, err), package_sha12=None,
                    packages_count=0, implicit=False, busy_previous=None)
    pkg = _current_pkg(doc)
    busy = busy_of(doc)
    prev = None
    if busy and busy.get("line") == "press" and pkg is not None \
            and busy.get("sha12") != str(pkg.get("sha"))[:12]:
        prev = {"sha12": busy.get("sha12"), "task": busy.get("task"),
                "progress": busy.get("progress")}
    return {"run": doc["run"], "title": doc.get("title"),
            "state_kind": state_of(doc),
            "package_sha12": str((pkg or {}).get("sha") or "")[:12] or None,
            "packages_count": len([p for p in doc.get("packages") or []
                                   if isinstance(p, dict)]),
            "implicit": False, "busy_previous": prev}


# ------------------------------------------------------------ карточка

def _files_of_attempt(wd: Path, rec: dict, run: str) -> list[dict]:
    root = wd / ATTEMPTS_DIR / str(rec["id"])
    base = "/api/cosmos-runs/%s/attempts/%s" % (run, rec["id"])
    out = []
    if not root.is_dir():
        return out
    for alias, (sub, name) in _aliases().items():
        if (root / sub / name).is_file():
            out.append({"alias": alias, "href": "%s/file/%s" % (base, alias)})
    return out


def run_card(run: str) -> dict:
    """GET /api/cosmos-runs/{id}: запись прогона + состояние, пакеты
    (live_mg у текущего), попытки всех рабочих каталогов со ссылками
    области прогона, паспорт."""
    run = check_run_id(run)
    doc = read_run(run)
    current = current_run_id()
    is_current = run == current
    pkg = _current_pkg(doc)
    attempts = attempts_of(doc)
    launch = _launch_of(doc, pkg, is_current)
    kind = state_of(doc, attempts, launch)
    packages = []
    for p in doc.get("packages") or []:
        if not isinstance(p, dict):
            continue
        q = dict(p)
        q["lost"] = package_path_of(p) is None
        q["current"] = p.get("sha") == doc.get("current_package")
        if is_current and q["current"] and not env_package():
            try:
                from webapp import flow
                q["live_mg"] = flow.readiness_state()
            except Exception:                            # noqa: BLE001
                q["live_mg"] = None
        packages.append(q)
    rows = []
    base = "/api/cosmos-runs/%s/attempts" % run
    for a in attempts:
        r = {k: v for k, v in a.items() if not k.startswith("_")}
        wd = data_root() / str(a.get("workdir"))
        r["files"] = _files_of_attempt(wd, a, run)
        r["exports"] = {"index_href": "%s/%s/export" % (base, a["id"])}
        r["exchanges_href"] = ("%s/%s/exchanges" % (base, a["id"])
                               if (exchanges_dir(wd, a) / "index.jsonl").is_file()
                               else None)
        if not isinstance(r.get("journal"), dict):
            r["journal"] = {"present": False, "reason_code": "pre-journal"}
        r["replay"] = _replay_availability(wd, a, doc)
        r["replay_report_href"] = ("%s/%s/replay-report" % (base, a["id"])
                                   if isinstance(r.get("replay", {}).get("of"), dict)
                                   else None)
        rows.append(r)
    out = dict(doc)
    out.update(state_kind=kind, state=STATE_WORDS.get(kind, kind),
               current=is_current, packages=packages, attempts=rows,
               busy=busy_of(doc), passport=passport_of(doc, attempts))
    return out


# ------------------------------------------------------------ паспорт

_ENV_CACHE: dict = {"key": None, "value": None}


def _env_key() -> tuple:
    try:
        from vmp import catalog_access as ca
        cp = ca.catalog_path()
    except Exception:                                    # noqa: BLE001
        cp = None
    return (cp, os.environ.get("CREW_SOLVER_URL"),
            os.environ.get("CREW_CERTIFIER_MODEL"),
            os.environ.get("CREW_CERTIFIER_CHANNEL"))


def forget() -> None:
    """Сброс кэша окружения (стенды с per-test каталогом данных)."""
    _ENV_CACHE["key"] = None
    _ENV_CACHE["value"] = None


def _dist_version(name: str) -> str | None:
    try:
        import importlib.metadata as md
        return md.version(name)
    except Exception:                                    # noqa: BLE001
        return None


def _pairing_engines_available() -> list[str]:
    out = []
    if importlib.util.find_spec("ortools") is not None:
        out.append("cpsat")
    if importlib.util.find_spec("cplex") is not None \
            or importlib.util.find_spec("docplex") is not None:
        out.append("cplex")
    if importlib.util.find_spec("highspy") is not None:
        out.append("highs")
    return out


def _solver_environment() -> dict:
    from webapp import build_identity as bi
    ident = bi.build_identity()
    sol = ident.get("solver") if isinstance(ident.get("solver"), dict) else {}
    have_files = bool(sol.get("jar_present") or sol.get("app_jar_sha256")
                      or sol.get("timefold_version"))
    build = None
    if have_files:
        build = {"launcher_jar_sha256": sol.get("jar_sha256"),
                 "app_jar_sha256": sol.get("app_jar_sha256"),
                 "manifest_commit_short": sol.get("manifest_commit_short"),
                 "matches_manifest": sol.get("matches_manifest"),
                 "timefold_version": sol.get("timefold_version"),
                 "quarkus_version": sol.get("quarkus_version"),
                 "config_sha256": sol.get("config_sha256")}
    # Адрес — без учётных данных (user:pass@), как в записи попытки и в
    # манифесте журнала: паспорт прогона хранится в run.json и отдаётся
    # карточкой и списком — секретам в нём не место (правило 17.1).
    from vmp.run.call_journal import _strip_userinfo
    url = (os.environ.get("CREW_SOLVER_URL") or "").strip() or None
    return {"engine": "timefold",
            "url": _strip_userinfo(url) if url else None,
            "version_endpoint": None,
            "version_endpoint_code": "no-version-endpoint",
            "version_source_code": "build-files" if have_files
            else "no-build-files",
            "build": build}


def environment() -> dict:
    """Окружение при рождении прогона (момент С): код, пакеты, каталог
    шаблонов, решатель по файлам сборки, движки пейринга, настройка
    сертификатора площадки, предел попыток. Кэш по ключу настроек."""
    key = _env_key()
    if _ENV_CACHE["key"] == key and _ENV_CACHE["value"] is not None:
        return json.loads(json.dumps(_ENV_CACHE["value"]))
    from webapp import build_identity as bi
    from webapp import package_runs as pr
    ident = bi.build_identity()
    src = ident.get("source") if isinstance(ident.get("source"), dict) else {}
    code = {"commit": src.get("commit"), "commit_short": src.get("commit_short"),
            "tree_dirty": src.get("tree_dirty"),
            "dirty_paths": src.get("dirty_paths")}
    if not code["commit"]:
        code = {"commit": None, "commit_short": None, "tree_dirty": None,
                "dirty_paths": None, "reason_code": "no-git"}
    try:
        from vmp import catalog_access as ca
        st = ca.stamp()
        catalog = {"version": st.get("version"),
                   "fingerprint": st.get("fingerprint"),
                   "path": st.get("path")}
    except Exception as e:                               # noqa: BLE001
        catalog = {"version": None, "fingerprint": None,
                   "reason_code": "catalog-unavailable",
                   "error": str(e)[:300]}
    keep = pr.attempts_keep()
    value = {
        "product": "COSMOS",
        "code": code,
        "rules_contract_fingerprint": ident.get("rules_contract_fingerprint"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {n: _dist_version(n) for n in PACKAGES_OF_INTEREST},
        "catalog": catalog,
        "solver": _solver_environment(),
        "pairing_engines": {"available": _pairing_engines_available()},
        "certifier_setting": {
            "model": (os.environ.get("CREW_CERTIFIER_MODEL") or "").strip()
            or None,
            "channel": (os.environ.get("CREW_CERTIFIER_CHANNEL") or "cli")
            .strip().lower() or "cli"},
        "attempts_keep": keep,
    }
    _ENV_CACHE["key"] = key
    _ENV_CACHE["value"] = value
    return json.loads(json.dumps(value))


def inputs_of(doc: dict) -> list[dict]:
    """Входы прогона (момент П) по отпечаткам: по каждой записи пакета —
    отпечатки, манифест как есть, файлы манифеста из zip, вердикт приёма."""
    out = []
    for pkg in doc.get("packages") or []:
        if not isinstance(pkg, dict):
            continue
        path = package_path_of(pkg)
        man, man_sha = _zip_manifest(path) if path else (None, None)
        files = [{"name": f.get("name"), "sha256": f.get("sha256"),
                  "bytes": f.get("bytes")}
                 for f in ((man or {}).get("files") or [])
                 if isinstance(f, dict)]
        out.append({
            "sha256": pkg.get("sha"),
            "manifest_sha256": pkg.get("manifest_sha256") or man_sha,
            "source_run": pkg.get("source_run"),
            "spec_version": pkg.get("spec_version"),
            "revision": pkg.get("revision"), "customer": pkg.get("customer"),
            "received_at": pkg.get("received_at"), "source": pkg.get("source"),
            "receipts": pkg.get("receipts"),
            "made_by": pkg.get("made_by"),
            "made_by_code": None if pkg.get("made_by")
            else "not-declared-by-package",
            "kit": pkg.get("kit"),
            "data_fingerprint": pkg.get("data_fingerprint"),
            "catalog": None, "catalog_code": "not-declared-by-package",
            "files": files, "files_count": pkg.get("files_count"),
            "verdict": pkg.get("verdict"), "mg": pkg.get("mg"),
            "note_code": "package-bytes-missing" if path is None
            else pkg.get("note_code")})
    return out


def _certifier_of(rec: dict, certificate: dict | None) -> dict:
    cert = dict(rec.get("certifier") or {}) if isinstance(
        rec.get("certifier"), dict) else {}
    if not cert and isinstance(certificate, dict) \
            and isinstance(certificate.get("certifier"), dict):
        cert = dict(certificate["certifier"])
    if cert and "provider" not in cert:
        cert["provider"] = ("anthropic" if cert.get("backend") == "cloud"
                            else ("ollama" if cert.get("backend") == "local"
                                  else None))
    if cert and "note_code" not in cert:
        cert.setdefault("effort", None)
        cert.setdefault("temperature", 0.2 if cert.get("backend") == "local"
                        else None)
        cert["note_code"] = ("ollama-default" if cert.get("backend") == "local"
                             else "channel-does-not-set")
    return cert


def _cplex_version_from_logs(out: Path) -> str | None:
    try:
        logs = sorted(out.glob("select-*-cplex.log"))
    except OSError:
        return None
    for p in logs:
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            continue
        m = re.search(r"CPLEX[^\n]*?(\d+\.\d+(?:\.\d+)*)", head)
        if m:
            return m.group(1)
    return None


def _budget_from_args(args) -> int | None:
    if not isinstance(args, list):
        return None
    for i, a in enumerate(args):
        if str(a) == "--budget-s" and i + 1 < len(args):
            try:
                return int(float(args[i + 1]))
            except (TypeError, ValueError):
                return None
    return None


def attempt_recipe(wd: Path, rec: dict, pkg: dict | None = None) -> dict:
    """Рецепт попытки (раздел 8.3): Н из записи, Н+ из снимка; чего нет
    на диске — null с кодом; параметры старых попыток реконструируются
    по артефактам с перечнем params_reconstructed. С 10.09.2026 в
    `attempt` едет и `actor` — кто нажал (раздел 12ц руководства 03)."""
    rec = dict(rec)
    attempt = str(rec.get("id"))
    kind = _attempt_kind(rec)
    out_name = _attempt_out_name(rec)
    root = wd / ATTEMPTS_DIR / attempt
    out = root / out_name
    delivery = _read_json_opt(out / "delivery.json")
    delivery = delivery if isinstance(delivery, dict) else {}
    certificate = _read_json_opt(out / "certificate.json")
    certificate = certificate if isinstance(certificate, dict) else None
    binput = _read_json_opt(out / "builder-input.json")
    binput = binput if isinstance(binput, dict) else {}
    params = rec.get("params") if isinstance(rec.get("params"), dict) else None
    reconstructed: list[str] = []
    if params is None:
        params = {}
        budget = _budget_from_args(binput.get("args"))
        if budget is not None:
            params["spent_limit_s"] = budget
            reconstructed.append("spent_limit_s")
        if delivery.get("mode") is not None:
            params["mode"] = delivery.get("mode")
            reconstructed.append("mode")
        obj = delivery.get("objective") or delivery.get("objective_id")
        if obj is not None:
            params["objective_id"] = obj
            reconstructed.append("objective_id")
        if delivery.get("pairing_run_id"):
            params["pairing_run_id"] = delivery.get("pairing_run_id")
            reconstructed.append("pairing_run_id")
        params = params or None
    repro = rec.get("reproducibility") if isinstance(
        rec.get("reproducibility"), dict) else {"class": None,
                                                "reason_code": "pre-passport"}
    schedule = delivery.get("schedule") if isinstance(
        delivery.get("schedule"), dict) else {}
    pairing = None
    rostering = None
    if kind == "pairing":
        bcat = _read_json_opt(out / "builder-catalog.json")
        bcat = bcat if isinstance(bcat, dict) else {}
        checks = sorted(out.glob("pairing_builder_check_*.json"))
        check = _read_json_opt(checks[0]) if checks else None
        check = check if isinstance(check, dict) else {}
        status = {"solver_status": bcat.get("solver_status")
                  or check.get("solver_status"),
                  "ip_status": bcat.get("ip_status") or check.get("ip_status"),
                  "bound": bcat.get("bound") or check.get("bound")}
        pairing = {"select_engine": rec.get("engine"),
                   "engine_versions": rec.get("engine_versions"),
                   "cplex": ({"version": _cplex_version_from_logs(out)}
                             if rec.get("engine") == "cplex" else None),
                   "args": binput.get("args"),
                   "provenance": binput.get("provenance"),
                   "config_sig": bcat.get("config_sig"),
                   "status": status,
                   "catalog_sha256": delivery.get("catalog_sha256"),
                   "solver_run_id": schedule.get("solver_run_id"),
                   "chains": delivery.get("chains"),
                   "coverage": delivery.get("coverage")}
    else:
        sstatus = _read_json_opt(out / "solver-status.json")
        sstatus = sstatus if isinstance(sstatus, dict) else None
        applied = _read_json_opt(out / "applied-rules.json")
        solution = _read_json_opt(out / "solution.json")
        det = (params or {}).get("deterministic")
        rostering = {
            "engine": "timefold", "engine_code": "engine-only-timefold",
            "solver_url": (rec.get("solver") or {}).get("url")
            if isinstance(rec.get("solver"), dict) else None,
            "solver_run_id": schedule.get("solver_run_id"),
            "score": schedule.get("score"),
            "request": {"spentLimitSeconds": (params or {}).get("spent_limit_s"),
                        "unimprovedSpentLimitSeconds":
                            (params or {}).get("unimproved_spent_limit_s"),
                        "moves": (params or {}).get("moves"),
                        "mode": (params or {}).get("mode"),
                        "objective_id": (params or {}).get("objective_id"),
                        "pairingRunId": (params or {}).get("pairing_run_id"),
                        "deterministic": det},
            "seed": ({"value": det.get("seed"), "source_code": "profile-d1"}
                     if isinstance(det, dict) else
                     {"value": 0, "source_code": "solver-default-seed-0"}),
            "profile": (sstatus or {}).get("profile"),
            "moves": (sstatus or {}).get("moves"),
            "seeded": (sstatus or {}).get("seeded"),
            "deadheads": (sstatus or {}).get("deadheads"),
            # solver-status.json пишется кнопкой только по выходу из опроса
            # состоянием «готов»: у попытки текущей версии без файла счёт до
            # «решён» не дошёл (решатель недоступен, пакет отвергнут, «упал»,
            # превышено время ожидания) — код not-solved, а «до паспорта» —
            # только у записи без params (попытка до версии).
            "solver_status_code": (None if sstatus is not None
                                   else "pre-passport"
                                   if not isinstance(rec.get("params"), dict)
                                   else "not-solved"),
            "thresholds_applied": applied if isinstance(applied, (dict, list))
            else None,
            "weights": (solution.get("thresholds") if isinstance(solution, dict)
                        else None),
            "pairing_run_id": delivery.get("pairing_run_id"),
            "pairing_catalog_sha256": delivery.get("pairing_catalog_sha256")}
    exchanges = rec.get("exchanges") if isinstance(rec.get("exchanges"), dict) \
        else ({"present": True, "dir": "%s/%s/%s/llm-exchanges"
               % (ATTEMPTS_DIR, attempt, out_name)}
              if (out / "llm-exchanges" / "index.jsonl").is_file()
              else {"present": False, "reason_code": "pre-passport"})
    of = (rec.get("replay") or {}).get("of") if isinstance(
        rec.get("replay"), dict) else None
    certifier = _certifier_of(rec, certificate)
    certifier["exchanges"] = exchanges
    certifier["calls_code"] = ("replayed-from-journal"
                               if isinstance(of, dict) and of.get("mode") == "journal"
                               else "live")
    sc = rec.get("source_check") if isinstance(rec.get("source_check"), dict) \
        else None
    reader = None
    if sc:
        reader = {"reader": sc.get("reader"),
                  "reader_channel": sc.get("reader_channel"),
                  "compiled_cache": sc.get("compiled_cache")}
    else:
        sha = str((pkg or {}).get("sha") or rec.get("package_sha") or "")
        run = str(rec.get("run") or "")
        for t, snap in _source_check_snapshots():
            if t != rec.get("task") or snap.get("status") != "done":
                continue
            if not sha or snap.get("package_sha") != sha:
                continue
            if snap.get("cosmos_run") and run and snap["cosmos_run"] != run:
                continue
            reader = {"reader": snap.get("reader"),
                      "reader_channel": snap.get("reader_channel"),
                      "compiled_cache": None}
            break
        if reader is None:
            reader = {"reader": None, "reader_channel": None,
                      "reason_code": "snapshot-of-other-package"
                      if any(t == rec.get("task") for t, _s in
                             _source_check_snapshots()) else None}
    made_by = (pkg or {}).get("made_by") if isinstance(
        (pkg or {}).get("made_by"), dict) else None
    registry = rec.get("registry") if isinstance(rec.get("registry"), dict) \
        else None
    reg_copy = root / "registry" / "vmp-executors-registry.json"
    if registry is None:
        if reg_copy.is_file():
            reg = _read_json_opt(reg_copy)
            registry = {"source": "snapshot-copy", "sha256": None,
                        "catalog": (reg.get("catalog") if isinstance(reg, dict)
                                    else None)}
        else:
            live = _read_json_opt(wd / "registry" / "vmp-executors-registry.json")
            registry = {"source": None, "sha256": None,
                        "catalog": (live.get("catalog") if isinstance(live, dict)
                                    else None),
                        "reason_code": "no-snapshot-copy",
                        "fallback_code": "latest-press-registry"
                        if isinstance(live, dict) else None}
    cert_body = (certificate or {}).get("certificate") if isinstance(
        (certificate or {}).get("certificate"), dict) else {}
    findings = delivery.get("findings")
    n_find = len(findings) if isinstance(findings, list) else findings
    artifacts = {
        "delivery": "delivery.json", "log": "log.json",
        "certificate": "certificate.json", "certificate_md": "certificate.md",
        "solution": "solution.json", "solved": "solved.json",
        "package": "package.zip", "builder_catalog": "builder-catalog.json",
        "builder_input": "builder-input.json",
        "execution_manifest": "execution-manifest.json",
        "solver_status": "solver-status.json",
        "applied_rules": "applied-rules.json",
        "exchanges_index": "llm-exchanges/index.jsonl",
        "call_journal": "external-calls/index.jsonl"}
    art_out = {}
    for key, name in artifacts.items():
        p = out / name
        art_out[key] = ("%s/%s/%s/%s" % (ATTEMPTS_DIR, attempt, out_name, name)
                        if p.is_file() else None)
    art_out["registry"] = ("%s/%s/registry/vmp-executors-registry.json"
                           % (ATTEMPTS_DIR, attempt) if reg_copy.is_file()
                           else None)
    art_out["replay_report"] = ("%s/%s/replay/report.json" % (ATTEMPTS_DIR, attempt)
                                if (root / "replay" / "report.json").is_file()
                                else None)
    return {
        "attempt": {"id": attempt, "task": rec.get("task"), "kind": kind,
                    "started_at": rec.get("started_at"),
                    "finished_at": rec.get("finished_at"),
                    "state": rec.get("state"), "verdict": rec.get("verdict"),
                    "run": rec.get("run"),
                    # Кто нажал (10.09.2026): рецепт — часть паспорта
                    # прогона, и актор попытки читается ровно там же,
                    # где параметры и окружение. Записи прежних версий
                    # актора не несут — здесь None, слова о причине
                    # даёт запись попытки.
                    "actor": rec.get("actor"),
                    "journal": rec.get("journal") if isinstance(
                        rec.get("journal"), dict)
                    else {"present": False, "reason_code": "pre-journal"},
                    "replay": {"of": of}},
        "package": {"sha": rec.get("package_sha") or (pkg or {}).get("sha"),
                    "manifest_sha256": (delivery.get("trace") or {})
                    .get("package_fingerprint")
                    if isinstance(delivery.get("trace"), dict) else None,
                    "source_run": (pkg or {}).get("source_run")},
        # Происхождение входов и решатели с параметрами — рецепт повтора
        # без них неполон (проект «Запись о прогоне», разделы 3.1 и
        # 3.2.3); записи прежних версий их не несут — код причины.
        "lineage": rec.get("lineage") if isinstance(rec.get("lineage"), dict)
        else {"package_sha256": rec.get("package_sha")
              or (pkg or {}).get("sha"),
              "source_run": (pkg or {}).get("source_run"),
              "parent_attempt": None, "record_id": None,
              "reason_code": "pre-lineage"},
        "solvers": rec.get("solvers") if isinstance(rec.get("solvers"), list)
        else None,
        "solvers_code": None if isinstance(rec.get("solvers"), list)
        else "pre-solvers",
        # Расход попытки в токенах по каждой модели плюс стенное время
        # счёта (решение владельца № 1 от 10.09.2026): денег здесь нет —
        # цена считается при просмотре по прейскуранту того дня.
        "resources": rec.get("resources") if isinstance(
            rec.get("resources"), dict)
        else {"models": [], "models_code": "pre-resources",
              "solver_seconds": None,
              "solver_seconds_code": "pre-resources"},
        "params": params, "params_reconstructed": reconstructed,
        "params_code": None if rec.get("params") is not None else "pre-passport",
        "reproducibility": repro,
        "pairing": pairing, "rostering": rostering,
        "models": {"certifier": certifier, "source_check_reader": reader,
                   "arbiter": {"model": None, "reason_code": "none-in-cosmos"},
                   "cdpm": (made_by.get("models") if made_by else None)},
        "code": rec.get("code") if isinstance(rec.get("code"), dict)
        else {"commit": None, "commit_short": None, "reason_code": "pre-passport"},
        "template_catalog": rec.get("template_catalog") if isinstance(
            rec.get("template_catalog"), dict)
        else {"version": None, "fingerprint": None, "reason_code": "pre-passport"},
        "registry": registry,
        "checks": {"vmp": {"certified": delivery.get("certified"),
                           "certificate_gate": delivery.get("certificate_gate"),
                           "counters": cert_body.get("counters"),
                           "findings": n_find,
                           "uncovered": delivery.get("uncovered")},
                   "idak": sc},
        "artifacts": art_out,
    }


def _attempt_passport_row(a: dict) -> dict:
    cert = _certifier_of(a, None)
    return {"id": a.get("id"), "task": a.get("task"), "kind": a.get("kind"),
            "package_sha": a.get("package_sha"), "started_at": a.get("started_at"),
            "finished_at": a.get("finished_at"), "verdict": a.get("verdict"),
            "state": a.get("state"), "engine": a.get("engine"),
            "params": a.get("params") if isinstance(a.get("params"), dict)
            else None,
            "params_code": None if isinstance(a.get("params"), dict)
            else "pre-passport",
            "reproducibility": a.get("reproducibility") if isinstance(
                a.get("reproducibility"), dict)
            else {"class": None, "reason_code": "pre-passport"},
            "certifier": cert or None,
            "code": a.get("code") if isinstance(a.get("code"), dict) else None,
            "template_catalog": a.get("template_catalog") if isinstance(
                a.get("template_catalog"), dict) else None,
            "solver": a.get("solver") if isinstance(a.get("solver"), dict)
            else None,
            "engine_versions": a.get("engine_versions"),
            "exchanges": a.get("exchanges") if isinstance(a.get("exchanges"), dict)
            else {"present": False, "reason_code": "pre-passport"},
            "journal": a.get("journal") if isinstance(a.get("journal"), dict)
            else {"present": False, "reason_code": "pre-journal"},
            "checks": {"vmp": {"verdict": a.get("verdict")},
                       "idak": a.get("source_check") if isinstance(
                           a.get("source_check"), dict) else None},
            "replay": (a.get("replay") or {}).get("of") if isinstance(
                a.get("replay"), dict) else None}


def passport_of(doc: dict, attempts: list[dict] | None = None) -> dict:
    """Паспорт прогона (раздел 8.5): окружение при рождении, входы,
    сводки попыток, модели, журналы, обрезка, пробелы кодами."""
    from webapp import package_runs as pr
    attempts = attempts_of(doc) if attempts is None else attempts
    born = doc.get("passport") if isinstance(doc.get("passport"), dict) else {}
    env = born.get("environment") if isinstance(born.get("environment"), dict) \
        else None
    rows = [_attempt_passport_row(a) for a in attempts]
    models: list[dict] = []
    seen: set[tuple] = set()

    def _add(role, provider, model, channel, source):
        key = (role, provider, model, channel)
        if model and key not in seen:
            seen.add(key)
            models.append({"role": role, "provider": provider, "model": model,
                           "channel": channel, "source": source})
    for r in rows:
        c = r.get("certifier") or {}
        _add("certifier", c.get("provider"), c.get("model"), c.get("channel"),
             "attempt")
        idak = r["checks"].get("idak") or {}
        if idak.get("reader"):
            _add("source_check_reader", None, idak.get("reader"),
                 idak.get("reader_channel"), "attempt")
    for pkg in doc.get("packages") or []:
        mb = pkg.get("made_by") if isinstance(pkg, dict) else None
        for m in ((mb or {}).get("models") or []):
            if isinstance(m, dict):
                _add("cdpm:%s" % m.get("role"), m.get("provider"),
                     m.get("model"), m.get("channel"), "package")
    n_exch = 0
    n_att_exch = 0
    for a in attempts:
        ex = a.get("exchanges")
        if isinstance(ex, dict) and ex.get("count"):
            n_att_exch += 1
            n_exch += int(ex.get("count") or 0)
    snaps = 0
    run = str(doc.get("run") or "")
    for _t, snap in _source_check_snapshots():
        if snap.get("cosmos_run") == run:
            snaps += 1
    trimmed = 0
    for pkg in doc.get("packages") or []:
        if isinstance(pkg, dict):
            st = pr.read_state(workdir_of_package(pkg))
            trimmed += int(st.get("attempts_trimmed") or 0)
    gaps: list[str] = []
    if env is None:
        gaps.append("pre-passport")
    elif isinstance(env.get("catalog"), dict) and env["catalog"].get("reason_code"):
        gaps.append(env["catalog"]["reason_code"])
    if env and isinstance(env.get("solver"), dict) \
            and env["solver"].get("version_source_code") == "no-build-files":
        gaps.append("no-build-files")
    if any(r.get("params_code") for r in rows):
        gaps.append("pre-passport")
    if any(isinstance(p, dict) and not p.get("made_by")
           for p in doc.get("packages") or []):
        gaps.append("not-declared-by-package")
    if any(isinstance(p, dict) and package_path_of(p) is None
           for p in doc.get("packages") or []):
        gaps.append("package-bytes-missing")
    return {"created": env, "created_at": doc.get("created_at"),
            "inputs": inputs_of(doc), "attempts": rows,
            "models_summary": models,
            "journals": {"attempts_with_exchanges": n_att_exch,
                         "model_exchanges": n_exch,
                         "source_check_snapshots": snaps},
            "attempts_trimmed": trimmed,
            "gaps": sorted(set(gaps))}


def summary_of(doc: dict, attempts: list[dict] | None = None) -> dict:
    """Сводка паспорта для списка (раздел 8.5): модели, решатели
    ([{task, engine, version, version_of, version_code}]), код, каталог,
    state_code (ok | no-attempts | pre-passport)."""
    attempts = attempts_of(doc) if attempts is None else attempts
    born = doc.get("passport") if isinstance(doc.get("passport"), dict) else {}
    env = born.get("environment") if isinstance(born.get("environment"), dict) \
        else {}
    newest = attempts[0] if attempts else None
    cert = _certifier_of(newest, None) if newest else {}
    cert_words = ("%s (%s)" % (cert.get("model"), cert.get("channel"))
                  if cert.get("model") else None)
    sc_words = None
    for a in attempts:
        sc = a.get("source_check")
        if isinstance(sc, dict) and sc.get("reader"):
            sc_words = "%s (%s)" % (sc.get("reader"),
                                    sc.get("reader_channel") or "?")
            break
    cdpm_words = None
    pkg = _current_pkg(doc)
    mb = (pkg or {}).get("made_by") if isinstance((pkg or {}).get("made_by"),
                                                   dict) else None
    if mb and isinstance(mb.get("models"), list) and mb["models"]:
        cdpm_words = ", ".join("%s: %s" % (m.get("role"), m.get("model"))
                               for m in mb["models"] if isinstance(m, dict))
    # Решатели — кодами и полями, слова собирает клиент (cruns.task.*,
    # cruns.note.*): русская фраза сервера показывалась во всех языках
    # (ревью реализации 07.09). version_of — библиотека, чью версию несёт
    # запись (ortools у движков пейринга); version_code — причина пробела.
    solvers: list[dict] = []
    pairing = next((a for a in attempts if a.get("task") == "pairing"), None)
    if pairing is not None:
        ev = pairing.get("engine_versions") if isinstance(
            pairing.get("engine_versions"), dict) else {}
        ortools = ev.get("ortools") or (env.get("packages") or {}).get("ortools")
        solvers.append({"task": "pairing", "engine": pairing.get("engine") or None,
                        "version": ortools or None,
                        "version_of": "ortools" if ortools else None,
                        "version_code": None})
    rostering = next((a for a in attempts if a.get("task") == "rostering"), None)
    if rostering is not None:
        tf = ((env.get("solver") or {}).get("build") or {}).get("timefold_version")
        solvers.append({"task": "rostering", "engine": "timefold",
                        "version": tf or None, "version_of": None,
                        "version_code": None if tf else "no-build-files"})
    code = None
    if newest and isinstance(newest.get("code"), dict):
        code = newest["code"].get("commit_short")
    if not code:
        code = (env.get("code") or {}).get("commit_short")
    catalog = None
    if newest and isinstance(newest.get("template_catalog"), dict):
        catalog = newest["template_catalog"].get("version")
    if not catalog:
        catalog = (env.get("catalog") or {}).get("version")
    if newest is None:
        state_code = "no-attempts"
    elif isinstance(newest.get("params"), dict):
        state_code = "ok"
    else:
        state_code = "pre-passport"
    return {"models": {"certifier": cert_words, "source_check": sc_words,
                       "cdpm": cdpm_words},
            "solvers": solvers, "code": code, "catalog": catalog,
            "state_code": state_code}


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    """python -m webapp.cosmos_runs init [--data-dir …] [--dry-run]:
    оформление легаси прогонами под файловым замком; --dry-run печатает
    план и ничего не пишет."""
    import argparse
    ap = argparse.ArgumentParser(description="Прогоны COSMOS")
    sub = ap.add_subparsers(dest="cmd")
    p_init = sub.add_parser("init", help="завести хранилище прогонов")
    p_init.add_argument("--data-dir", default=None)
    p_init.add_argument("--dry-run", action="store_true")
    ns = ap.parse_args(argv)
    if ns.cmd != "init":
        ap.print_help()
        return 2
    if ns.data_dir:
        os.environ["CREW_DATA_DIR"] = str(ns.data_dir)
    if ns.dry_run:
        state = storage_state()
        plan = adoption_plan(None) if state != "off" else {"candidates": [],
                                                          "skipped_inbox": [],
                                                          "skipped_dirs": 0}
        doc = {"state": state, "dry_run": True,
               "would_create": [{"sha12": c["sha"][:12], "from": c["from"],
                                 "at": c.get("at_norm") or c.get("at"),
                                 "attempts": c.get("attempts", 0)}
                                for c in plan["candidates"]
                                if not c.get("adopted")],
               "skipped_inbox": plan["skipped_inbox"],
               "skipped_dirs": plan["skipped_dirs"]}
        print(json.dumps(doc, ensure_ascii=False, indent=1))
        return 0
    try:
        got = ensure_storage("cli")
    except RunsError as e:
        print(json.dumps({"error": str(e), "code": e.code}, ensure_ascii=False))
        return 1
    print(json.dumps(got, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":                                # pragma: no cover
    raise SystemExit(main())
