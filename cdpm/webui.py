# -*- coding: utf-8 -*-
"""Автономный браузер-интерфейс CDPM (пакет работ П-УИ).

Решение владельца 2026-08-17: модуль остаётся автономным, но получает
собственный веб-интерфейс. Это отдельное FastAPI-приложение на своём
порту; код веб-приложения COSMOS не импортируется — единственная точка
соприкосновения прежняя: ленивая фабрика `cdpm.llm.cosmos_llm` (выбор
языковой модели по дисциплине контура). Страница — один html без
внешних ресурсов (та же дисциплина внутреннего контура, что у
приложения COSMOS: никаких CDN).

Прогоны живут в каталоге данных вне репозитория (У-04): по умолчанию
`<каталог данных>/cdpm-runs/<номер>/` с подкаталогами `src/` (загруженные
документы), `freeze/` (комплект заморозки), `extract/` (модель, свод,
анкета, отбраковка). Каждый шаг заканчивается гейтом; вердикты гейтов
показываются в интерфейсе как есть.

Список прогонов (просьба владельца 06.09.2026: выбрать несколько и
удалить, переставить вверх/вниз, сортировать по дате создания и
названию). Рядом с прогонами в корне лежат два служебных файла:
`order.json` — ручной порядок списка ({"order": [номера]}) и
`counter.json` — последний выданный номер ({"last": N}), чтобы номер
удалённого прогона никогда не выдавался заново. «Удаление» из списка —
АРХИВ, а не уничтожение: каталог прогона переносится целиком в соседний
каталог `<каталог данных>/cdpm-runs-archive/`; возврат — перенос папки
обратно руками. Прогон, у которого идёт или стоит на паузе стадия, в
архив не уходит (409), и при любом отказе проверки не переносится ничего.

Запуск:
    python -m cdpm.webui --port 8095
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import sys
import functools
import inspect
import threading
import time
import traceback
from contextlib import asynccontextmanager, contextmanager, nullcontext
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.routing import APIRoute

from cdpm import anketa_xlsx as anketa_xlsx_mod
from cdpm import answers as answers_mod
from cdpm import answers_fingerprint as answers_fp_mod
from cdpm import data_checks as data_checks_mod
from cdpm import data_freeze as data_freeze_mod
from cdpm import data_report as data_report_mod
from cdpm import data_review as data_review_mod
from cdpm import delivery_mark as delivery_mark_mod
from cdpm import exchange as exchange_mod
from cdpm import extract, freeze
from cdpm import file_replace as _file_replace
from cdpm import file_replace as _file_replace
from cdpm import family_params as family_params_mod
from cdpm import history_questions as history_questions_mod
from cdpm import llm as llm_mod
from cdpm import mode2 as mode2_mod
from cdpm import model as m_mod
from cdpm import run_passport as passport_mod
from cdpm import run_record as run_record_mod
from cdpm import call_journal, actions_journal
from cdpm import catalog_bridge
from cdpm import catalog_requests as catreq_mod
from cdpm import channel as channel_mod
from cdpm import render as render_mod
from cdpm import providers as providers_mod
from cdpm.gates import (gate_answers, gate_arbiter, gate_data_freeze,
                        gate_data_report, gate_data_review,
                        gate_exchange, gate_extract,
                        gate_freeze, gate_mode2)


def runs_root() -> Path:
    """Корень прогонов: каталог данных вне репозитория (У-04);
    переменная CDPM_RUNS_DIR — для тестов и нестандартных установок."""
    env = (os.environ.get("CDPM_RUNS_DIR") or "").strip()
    if env:
        # strip: значение из cmd-скрипта вида `set VAR=… && …` приходит
        # с хвостовым пробелом — каталог с пробелом на конце на Windows
        # неработоспособен (поймано первым же живым запуском).
        return Path(env)
    import _data_paths
    return _data_paths.data_root() / "cdpm-runs"


def _run_dir(run_id: str) -> Path:
    if not re.fullmatch(r"run-\d{4}", run_id):
        raise HTTPException(400, f"недопустимый номер прогона {run_id!r}")
    path = runs_root() / run_id
    if not path.is_dir():
        raise HTTPException(404, f"прогон {run_id} не существует")
    return path


# ── Список прогонов: дата создания, ручной порядок, счётчик номеров,
# архив (просьба владельца 06.09.2026) ─────────────────────────────────

_RUN_ID_RE = re.compile(r"run-\d{4}")
_ORDER_FILE = "order.json"
_COUNTER_FILE = "counter.json"
_ARCHIVE_DIR_NAME = "cdpm-runs-archive"


def _now_iso() -> str:
    """Момент «сейчас» в ISO 8601 с местным смещением от UTC
    (секунды): так пишется created_at в статус прогона."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _dir_created_at(path: Path) -> str | None:
    """Дата создания по каталогу — для прогонов, рождённых до появления
    created_at в статусе: на Windows st_ctime — время создания, на
    других системах — время смены метаданных, поэтому берётся самое
    раннее из st_ctime/st_mtime. Ошибка — None, список не падает."""
    try:
        st = path.stat()
        # Время рождения каталога, где система его хранит (Windows,
        # macOS, свежие Linux с statx); иначе — самое раннее из
        # ctime/mtime (на Linux оба меняются — точность ниже).
        birth = getattr(st, "st_birthtime", None)
        if birth:
            ts = birth
        else:
            # src/ пишется только при создании прогона и дальше не
            # меняется — его mtime ближе к рождению, чем mtime корня
            # прогона, который меняет каждая стадия.
            candidates = [st.st_ctime, st.st_mtime]
            src = path / "src"
            if src.is_dir():
                candidates.append(src.stat().st_mtime)
            ts = min(candidates)
        return datetime.fromtimestamp(ts).astimezone() \
            .isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return None


def _write_json_atomic(path: Path, doc: dict) -> None:
    """Служебные файлы корня (order.json, counter.json): временный файл
    + замена (`cdpm.file_replace`: на Windows семантика POSIX, окно
    ожидания чужого читателя по времени), рваного JSON у читателя не
    бывает."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    _file_replace.replace_with_retry(tmp, path)


def _existing_run_ids(root: Path) -> list[str]:
    """Номера прогонов в корне — по имени, как и прежний список."""
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.glob("run-*")
                  if _RUN_ID_RE.fullmatch(p.name) and p.is_dir())


def _read_order(root: Path) -> list[str] | None:
    """Ручной порядок из order.json; файла нет или он нечитаем — None
    (порядок по имени). Нечитаемый файл список не ломает."""
    path = root / _ORDER_FILE
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        order = doc.get("order") if isinstance(doc, dict) else None
        if not isinstance(order, list):
            return None
        return [str(x) for x in order]
    except (ValueError, OSError):
        return None


def _effective_order(root: Path) -> list[str]:
    """Действующий порядок списка: записанный руками (только
    существующие прогоны, без повторов), затем не упомянутые — по
    имени. Без order.json — просто по имени."""
    existing = _existing_run_ids(root)
    stored = _read_order(root) or []
    seen: set[str] = set()
    out: list[str] = []
    for rid in stored:
        if rid in existing and rid not in seen:
            out.append(rid)
            seen.add(rid)
    out.extend(rid for rid in existing if rid not in seen)
    return out


def _read_counter(root: Path) -> int:
    """Последний выданный номер прогона; установки без counter.json
    (или с нечитаемым файлом) считаются от нуля — тогда номер даёт
    максимум существующих, как и прежде."""
    path = root / _COUNTER_FILE
    if not path.is_file():
        return 0
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return max(0, int(doc.get("last", 0))) if isinstance(doc, dict) \
            else 0
    except (ValueError, OSError, TypeError):
        return 0


def _raise_counter(root: Path, n: int) -> int:
    """Поднять counter.last до n (никогда не опускать); возвращает
    записанное значение."""
    last = max(_read_counter(root), int(n))
    _write_json_atomic(root / _COUNTER_FILE, {"last": last})
    return last


def _run_busy_words(st: dict) -> str | None:
    """Слова занятости прогона по обеим линиям (правил и данных) и по
    SMT-проверке (своя фоновая нить, ревью 06.09): стадия идёт или стоит
    на паузе — прогон трогать нельзя."""
    for key in ("state", "data_state", "smt_state"):
        cur = st.get(key)
        if _line_busy(cur):
            return str(cur)
    return None


def _status_path(run_dir: Path) -> Path:
    return run_dir / "status.json"


def _state_by_registry(run_dir: Path, state):
    """A07, миграция показа (контракт П0 §1.2): состояние старого цикла
    «аудит-цикл: успех», «аудит остановлен: …» или «сквозной цикл:
    чисто по Б/К …» при живой блокирующей/критической находке реестра
    показывается списку и карточке как «аудит-цикл: не закрыто (по
    реестру): N блокирующих»; status.json и файлы цикла не
    переписываются. Прочие состояния — как записаны.

    Финал сквозного цикла пересуживается наравне с финалом аудит-цикла
    (третье неавтор-ревью, проба P3): «чисто по Б/К» — то же
    утверждение «живых Б/К-споров нет», и старый прогон с
    унаследованной отбраковкой замком кода показывал его зелёным.
    Слова отказа — той же семьи «не закрыто (по реестру)», что и у
    аудит-цикла: панель уже красит её предупреждением
    (api.ts::isStalledState), новых слов состояния не вводится и
    сборка UI не нужна.

    A07-R5: все три пересуживаемых состояния ЗАЯВЛЯЮТ исход цикла
    (`exchange.registry_expected` — половина закона по слову),
    поэтому реестр обязан существовать — его отсутствие здесь не
    «новый прогон», а исчезнувший носитель, и оно отказывает теми же
    словами семьи."""
    if not isinstance(state, str) or not (
            state == "аудит-цикл: успех"
            or state.startswith("аудит остановлен: ")
            or state.startswith("сквозной цикл: чисто по Б/К")):
        return state
    reg_path = Path(run_dir) / "exchange" / "registry.json"
    try:
        # Один загрузчик продукта: законное отсутствие файла (новый
        # прогон) даёт пустой реестр; форма судится (A07-F4) и миграция
        # чтения применяется (A07-F3) до счёта блокеров. A07-R3:
        # «нет файла» отличает от «не прочитать» сам загрузчик —
        # предварительного `is_file()` здесь больше нет, иначе каталог
        # с именем registry.json показывался бы как чистый прогон.
        # A07-R5: сюда доходят ТОЛЬКО состояния, заявившие исход цикла
        # (охрана выше — та же семья слов), и один предикат продукта
        # это подтверждает: исход заявлен — носитель обязан быть.
        # Зовём весь предикат закона, а не его половину по слову:
        # второй половиной (вердикт цикла в каталоге) он тем более
        # истинен, и второго перечня правил в продукте не заводится.
        reg = exchange_mod.load_registry(
            reg_path,
            expect_file=exchange_mod.registry_expected(
                reg_path.parent, state))
    except exchange_mod.RegistryMissing:
        # A07-R5: реестр ИСЧЕЗ после исполненного цикла. Пустой реестр
        # означал бы «живых споров нет», и исчезновение носителя
        # работало бы доказательством чистоты — записанный «успех»
        # оставался бы в списке зелёным. Слова — той же семьи «не
        # закрыто (по реестру)», что и прочие отказы: панель уже красит
        # её предупреждением (api.ts::isStalledState), новых слов
        # состояния не вводится и сборка UI не нужна.
        return (f"аудит-цикл: {exchange_mod.VERDICT_NOT_CLOSED} "
                f"(по реестру): реестр отсутствует после исполненного "
                f"цикла")
    except exchange_mod.RegistryUnavailable:
        # A07-R3: реестр недоступен — судить нечем. Записанный успех
        # показывать нельзя: «не удалось узнать о спорах» — не «споров
        # нет» (fail-closed словами, П0 §0 п. 1). Прежде ввод-вывод
        # возвращал записанное состояние, и прогон с нечитаемым
        # реестром оставался в списке зелёным.
        return (f"аудит-цикл: {exchange_mod.VERDICT_NOT_CLOSED} "
                f"(по реестру): реестр недоступен")
    except exchange_mod.ExchangeError:
        # Форма реестра негодна — судить нечем, и записанный «успех»
        # показывать нельзя (fail-closed словами, П0 §0 п. 1). Слова —
        # той же семьи «не закрыто (по реестру)», что и счёт блокеров:
        # панель уже красит её предупреждением, а не зелёным «готово»
        # (api.ts::isStalledState), новых слов состояния не вводится.
        return (f"аудит-цикл: {exchange_mod.VERDICT_NOT_CLOSED} "
                f"(по реестру): форма реестра не читается")
    except (OSError, ValueError):
        # Непредвиденный отказ чтения — той же семьёй слов: незнание
        # не показывается записанным успехом.
        return (f"аудит-цикл: {exchange_mod.VERDICT_NOT_CLOSED} "
                f"(по реестру): реестр недоступен")
    n = len(exchange_mod.registry_blockers(reg))
    if not n:
        return state
    return (f"аудит-цикл: {exchange_mod.VERDICT_NOT_CLOSED} (по реестру): "
            f"{n} блокирующих")


def _load_model_or_409(path: Path) -> dict:
    """Semantic model read for request paths, before claiming a new stage."""
    try:
        return m_mod.load_model(path)
    except (ValueError, OSError) as error:
        raise HTTPException(409, f"модель не удалось прочитать: {error}") from error


def _read_status(run_dir: Path) -> dict:
    path = _status_path(run_dir)
    if not path.is_file():
        return {"state": "новый"}
    for _ in range(40):
        try:
            # FILE_SHARE_DELETE (cdpm.file_replace): нить стадии заменяет
            # статус, не дожидаясь ручек, которые его читают.
            return json.loads(_file_replace.read_bytes_shared(path)
                              .decode("utf-8"))
        except PermissionError:
            # Площадка без семантики POSIX: файл в момент замены —
            # мгновение спустя чтение пройдёт.
            time.sleep(0.025)
    return json.loads(_file_replace.read_bytes_shared(path).decode("utf-8"))


def _delivery_mark(run_dir: Path) -> dict:
    """Квитанция текущего ZIP; чужое/нечитаемое свидетельство не
    подтверждает текущую доставку и остаётся нетронутым на диске."""
    return delivery_mark_mod.read_current(run_dir)


def _write_status(run_dir: Path, status: dict) -> None:
    """Низкоуровневая атомарная замена файла статуса: статус пишет
    фоновая нить стадии, а читает обработчик опроса — рваного JSON быть
    не должно. На Windows os.replace падает PermissionError, пока
    читатель держит файл открытым, — повтор с паузой, как в
    webapp/data_health (_replace_with_retry): гонка ловилась плавающим
    тестом в полном прогоне 2026-08-17.

    Вызывается ТОЛЬКО из-под замка статуса — помощниками слияния
    `_mutate_status` и замены `_replace_status` (гейт
    test_status_writes_only_through_merge_helpers судит по ast). Прямой
    вызов со снимком, взятым в начале долгой стадии, затирал ключи,
    записанные другими за это время (живой случай run-9009, 04.09)."""
    path = _status_path(run_dir)
    # Временный файл — свой у каждого писателя (pid + нить): два процесса
    # панели над одной папкой (старый и новый при перезапуске — живой
    # плавающий тест test_orphaned_stage_is_stopped_on_server_restart,
    # PermissionError на общем status.json.tmp под Windows) больше не
    # толкаются на одном имени; замена — с ожиданием чужого читателя.
    import threading
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    _file_replace.replace_with_retry(tmp, path)


# ── Замок статуса: запись — слияние по ключам (живой случай run-9009) ──
# Статус прогона — ОДИН файл на две линии (правил и данных) и побочные
# линии (SMT, признак комплекта, название). У каждого ключа свой
# владелец — стадия или приёмник; ЛЮБАЯ запись — слияние по ключам:
# свежий статус перечитывается под замком, меняются только свои ключи,
# файл заменяется атомарно. Прежде фоновая нить брала снимок в начале
# стадии и в конце писала его целиком — загрузка книги данных,
# сделанная во время «Прогона целиком», исчезала из статуса (Prod
# run-9009, 04.09.2026 19:12–19:27: «файлов пока нет» после цикла).
# Замок двухслойный: RLock нити (стадии — нити этого процесса) +
# файловая блокировка status.lock (второй процесс над тем же каталогом
# прогонов — раннер серии, вторая копия сервера). Замена целиком —
# только рождение прогона и сброс (_replace_status).

_STATUS_LOCK = threading.RLock()
# Прогоны, чей status.lock уже держит владелец RLock: вложенный вход из
# той же нити (обратный вызов слияния зовёт слияние) файл второй раз
# не берёт — на Windows второй дескриптор того же процесса ждал бы
# первого до срока.
_STATUS_LOCK_HELD: list[Path] = []
_STATUS_LOCK_TIMEOUT_S = 30.0


def _lock_path(run_dir: Path) -> Path:
    return run_dir / "status.lock"


def _open_lock_fd(run_dir: Path) -> int:
    return os.open(str(_lock_path(run_dir)), os.O_RDWR | os.O_CREAT, 0o644)


def _close_lock_fd(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def _lock_fd(fd: int, timeout_s: float = _STATUS_LOCK_TIMEOUT_S) -> None:
    """Исключительная блокировка первого байта status.lock с ожиданием
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
                    f"status.lock занят другим процессом дольше "
                    f"{timeout_s:g} с — запись статуса отменена") from None
            time.sleep(0.02)


def _unlock_fd(fd: int) -> None:
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _status_guard(run_dir: Path):
    """Замок записи состояния прогона (status.json, files-config.json):
    RLock нити + файловая блокировка status.lock прогона."""
    with _STATUS_LOCK:
        key = Path(run_dir).resolve()
        fd = None
        if key not in _STATUS_LOCK_HELD:
            fd = _open_lock_fd(run_dir)
            try:
                _lock_fd(fd)
            except BaseException:
                _close_lock_fd(fd)
                raise
            _STATUS_LOCK_HELD.append(key)
        try:
            yield
        finally:
            if fd is not None:
                _STATUS_LOCK_HELD.remove(key)
                try:
                    _unlock_fd(fd)
                finally:
                    _close_lock_fd(fd)


_MISSING = object()


def _mutate_status(run_dir: Path, fn) -> dict:
    """Слияние под замком: перечитать → fn(st) правит словарь на месте
    → атомарно записать. Исключение из fn (в т.ч. HTTPException отказа
    занятия линии) отменяет запись. Если fn сама изменила файл
    вложенным входом, поверх свежего файла ложатся только ключи,
    изменённые fn, — потерянного обновления нет и внутри одной нити.
    Возвращает записанный статус."""
    with _status_guard(run_dir):
        before = _read_status(run_dir)
        st = json.loads(json.dumps(before))
        fn(st)
        fresh = _read_status(run_dir)
        if fresh != before:
            for k in set(before) | set(st):
                if before.get(k, _MISSING) != st.get(k, _MISSING):
                    if k in st:
                        fresh[k] = st[k]
                    else:
                        fresh.pop(k, None)
            st = fresh
        _write_status(run_dir, st)
        return st


def _update_status(run_dir: Path, **keys) -> dict:
    """Слияние отдельных ключей: свежий статус перечитывается под
    замком и меняются только свои ключи (ревью ступени 1, 2026-08-22:
    одна стадия затирала ключи другой). Значение None удаляет ключ."""
    def _apply(st: dict) -> None:
        for k, v in keys.items():
            if v is None:
                st.pop(k, None)
            else:
                st[k] = v
    return _mutate_status(run_dir, _apply)


def _replace_status(run_dir: Path, fresh: dict, precheck=None) -> None:
    """Замена статуса ЦЕЛИКОМ — только рождение прогона и сброс.
    `precheck(текущий)` судит предусловие под тем же замком (сброс
    занятого прогона — 409 без окна между проверкой и записью)."""
    with _status_guard(run_dir):
        if precheck is not None:
            precheck(_read_status(run_dir))
        _write_status(run_dir, fresh)


def _line_busy(state) -> bool:
    """Линия занята: стадия идёт либо стоит на паузе — пауза жива и
    продолжится той же нитью, второй запуск родил бы вторую нить."""
    s = str(state or "")
    return s.endswith("идёт") or s.endswith("идут") \
        or s.startswith("на паузе")


# ── Журналы прогона (проект повтора, этап Р1: CDPM-POVTOR-PROEKT-CLD.md) ──

def _state_brief(st: dict | None) -> dict | None:
    """Состояние линий прогона для журнала действий."""
    if not st:
        return None
    return {k: st.get(k) for k in ("state", "data_state", "smt_state",
                                   "catalog_state")
            if st.get(k) is not None}


def _status_if_any(run_dir) -> dict:
    return _read_status(run_dir) if _status_path(run_dir).is_file() else {}


def _journaled(action: str, *keys: str, sync: bool = False):
    """Ручка, меняющая содержание прогона, — действие журнала: строка
    `start`/`sync` до исполнения (параметры — по именам аргументов),
    `refused` при отказе ручки, `end` — у действий без фона сразу, у
    фоновых стадий — из нити (`_start_stage_thread`). Номер действия —
    текущий в контексте запроса; обёртки каналов снимают его."""
    def deco(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # Аргументы по именам сигнатуры: FastAPI зовёт ручку
            # именованными, тесты — и позиционными.
            bound = sig.bind_partial(*args, **kwargs).arguments
            run_dir = _run_dir(bound["run_id"])
            params = {k: bound.get(k) for k in keys}
            before = _state_brief(_status_if_any(run_dir))
            n = actions_journal.note(run_dir, action, params=params,
                                     sync=sync, state_before=before)
            c0 = call_journal.count(run_dir)
            t0 = time.monotonic()
            try:
                out = fn(*args, **kwargs)
            except HTTPException as e:
                actions_journal.refused(run_dir, n, e.status_code,
                                        str(e.detail))
                raise
            except catalog_bridge.CATALOG_ERRORS as e:
                # Каталог шаблонов недоступен: наружу это 503 словами
                # (обработчик отказа приложения), и в журнале действий
                # отказ записан тем же кодом и теми же словами, а не
                # как «500 <repr>».
                actions_journal.refused(
                    run_dir, n, 503,
                    catalog_unavailable_body(e)["error"][:300])
                raise
            except Exception as e:                        # noqa: BLE001
                actions_journal.refused(run_dir, n, 500, repr(e)[:300])
                raise
            finally:
                # Фоновая нить уже сняла номер (`_start_stage_thread`);
                # контекст запроса дальше действие не несёт.
                actions_journal.set_current(None)
            if sync:
                actions_journal.end(
                    run_dir, n, state_after=_state_brief(_status_if_any(run_dir)),
                    duration_s=round(time.monotonic() - t0, 3),
                    calls=[c0, call_journal.count(run_dir)])
                _refresh_run_record(run_dir)
            return out
        return wrapper
    return deco


#: Состояние линии прогона, когда стадия не пошла из-за каталога
#: шаблонов: беда КОНТУРА (установка не из клона, каталог не проходит
#: проверку), а не отказ работы над документами заказчика.
CATALOG_STOP_STATE = "остановка: каталог шаблонов недоступен"


def _stage_failure(refusal_state: str, e, key: str = "state", *,
                   run_dir: Path | None = None) -> dict:
    """Состояние линии и слова для нити стадии при отказе.

    Отказ каталога шаблонов называется отдельно и одинаково у всех
    стадий — «остановка: каталог шаблонов недоступен: <причина>»:
    без словарей не судится ни одно правило, чинить нужно контур
    (проба — `GET /api/catalog` и `python -m cdpm.catalog_bridge`), а не
    прогон. Всякий иной отказ — как прежде: «отказ <стадии>» и слова
    исключения; тихих провалов фоновая работа не оставляет.

    Перехваченное исключение уже не дойдёт до обработчика нити.
    Стек остаётся только в stderr оператора, не в публичном статусе:
    без значений, строк исходника, полного пути и текста исключения."""
    try:
        where = f"{Path(run_dir).name}: " if run_dir is not None else ""
        lines = [f"CDPM: {where}{key}: {refusal_state}: {type(e).__name__}",
                 "Traceback (most recent call last; locations only):"]
        for frame, lineno in traceback.walk_tb(e.__traceback__):
            code = frame.f_code
            lines.append(f"  {Path(code.co_filename).name}:{lineno} "
                         f"in {code.co_name}")
        print("\n".join(lines), file=sys.stderr, flush=True)
    except Exception:                                    # noqa: BLE001
        # Недоступный журнал не должен оставить линию «идёт» вместо отказа.
        pass
    if isinstance(e, catalog_bridge.CATALOG_ERRORS):
        return {key: CATALOG_STOP_STATE,
                "error": (f"{CATALOG_STOP_STATE}: "
                          f"{catalog_bridge.words_of(e)} — стадия не "
                          f"выполнена ({refusal_state})")}
    return {key: refusal_state, "error": str(e)}


def _refresh_run_record(run_dir: Path, *, report_errors: bool = False) -> None:
    """Запись о прогоне (шаг 1 проекта, 10.09.2026): после каждого
    закрытого действия панели переписывается `run-record.json` в папке
    прогона и блок `resources` (токены по ролям и моделям) в статусе.
    Отказ записи действие не роняет — он печатается, а запись догонит
    следующим действием.

    Закрытие записи (состояние «завершён») — событие ДЕРЕВА прогонов
    (шаг 3, 12.1): индекс `run-tree.json` перестраивается тут же, чтобы
    карточка и ручки читали его из файла, а не обходили диск на каждый
    запрос."""
    try:
        from cdpm import run_record as run_record_mod
        rec = run_record_mod.refresh(
            run_dir,
            update_status=lambda res: _update_status(run_dir, resources=res))
    except Exception as e:                                # noqa: BLE001
        print(f"CDPM: {Path(run_dir).name}: запись о прогоне не обновлена "
              f"({str(e)[:200]})", flush=True)
        if report_errors:
            raise
        return
    if (rec or {}).get("results", {}).get("state") == "завершён":
        _rebuild_run_tree(f"{Path(run_dir).name}: запись закрыта")


def _rebuild_run_tree(why: str = "") -> None:
    """Перестроить индекс дерева прогонов по событию (12.1). Отказ
    действие не роняет — он печатается, как отказ записи о прогоне."""
    try:
        from cdpm import run_tree_panel
        run_tree_panel.refresh_index(runs_root(), why)
    except Exception as e:                                # noqa: BLE001
        print(f"CDPM: индекс дерева прогонов не перестроен "
              f"({why}: {str(e)[:200]})", flush=True)


def _start_stage_thread(run_dir: Path, work, after=None) -> None:
    """Фоновая нить стадии: наследует текущее действие журнала и по
    завершении пишет строку `end` (состояние линий, длительность,
    диапазон вызовов журнала внешних вызовов).

    `after` — необязательный обратный вызов ПОСЛЕ строки `end` стадии
    (шаг 6 каталога: автозапуск заявок по готовности модели). Строки
    журнала действий не перемежаются: `after` начинает своё действие,
    когда предыдущее уже закрыто; его отказ стадию не роняет."""
    n = actions_journal.current()
    c0 = call_journal.count(run_dir)
    t0 = time.monotonic()

    @run_record_mod.request_write_budget(None)
    def _run():
        actions_journal.set_current(n)
        err = None
        try:
            work()
        except BaseException as e:                        # noqa: BLE001
            err = repr(e)[:300]
            raise
        finally:
            if n is not None:
                try:
                    actions_journal.end(
                        run_dir, n,
                        state_after=_state_brief(_status_if_any(run_dir)),
                        duration_s=round(time.monotonic() - t0, 3),
                        calls=[c0, call_journal.count(run_dir)], error=err)
                except Exception:                         # noqa: BLE001
                    pass
                _refresh_run_record(run_dir)
            actions_journal.set_current(None)
            if after is not None and err is None:
                try:
                    after()
                except Exception as e:                    # noqa: BLE001
                    print(f"CDPM: {Path(run_dir).name}: продолжение стадии "
                          f"отказало ({str(e)[:200]})", flush=True)
    threading.Thread(target=_run, daemon=True).start()


def _journal_channel(run_dir, fn, role: str, klass: str | None,
                     key_role: str | None = None):
    """Канал стадии под журналом внешних вызовов: сначала журнал каналов
    стадий (отпечаток канала), затем обёртка записи/повтора. `key_role`
    — ЯВНЫЙ ключ записи журнала стадий (шаг 6 каталога: составитель
    заявок без своей конфигурации не должен затирать строку
    обработчика)."""
    _note_stage_channel(run_dir, fn, key_role=key_role)
    return call_journal.wrap(run_dir, fn, role, klass,
                             action_n=actions_journal.current())


def _journal_vision(run_dir, fn):
    """Зрительный канал заморозки под журналом (кроме канала без прогона)."""
    if run_dir is None:
        return fn
    return call_journal.wrap_vision(run_dir, fn,
                                    action_n=actions_journal.current())


def _claim_stage(run_dir: Path, state_key: str = "state",
                 also: tuple = (), **keys) -> None:
    """Занятие линии стадией: проверка занятости и запись «… идёт» —
    ОДНОЙ операцией под замком (между проверкой и записью чужого
    запроса не бывает: два одновременных нажатия не родят две нити).
    Предусловия с человеческими отказами остаются у ручек; здесь —
    последний рубеж. `also` — ключи других линий, которые тоже должны
    быть свободны."""
    def _apply(st: dict) -> None:
        for key in (state_key, *also):
            cur = st.get(key)
            if _line_busy(cur):
                raise HTTPException(409, f"прогон занят другой стадией: "
                                         f"{cur}")
        for k, v in keys.items():
            if v is None:
                st.pop(k, None)
            else:
                st[k] = v
    _mutate_status(run_dir, _apply)


# ── Восстановление статусов после гибели процесса (ПС-1) ──
# Стадии работают в фоновых потоках ЭТОГО процесса; если процесс умер
# посреди стадии (сбой, перезагрузка машины — случилось 2026-08-19 на
# живой сверке run-0002), статус остаётся «… идёт» навсегда, и
# повторный запуск стадии отвечает 409 «прогон занят». На старте
# сервера осиротевшие «… идёт/идут» переводятся в честное
# «остановлено: <стадия>» — остановка не отказ, error не трогается.

_ORPHAN_STATES = {"применение ответов идёт":
                      "остановлено: применение ответов",
                  "чтение обязательств идёт": "остановлено: чтение обязательств",
                  "сверка формализации идёт":
                      "остановлено: сверка формализации",
                  "извлечение идёт": "остановлено: извлечение",
                  "аудит-цикл идёт": "остановлено: аудит-цикл",
                  "арбитраж идёт": "остановлено: арбитраж",
                  "сквозной цикл идёт": "остановлено: сквозной цикл"}
_ORPHAN_DATA_STATES = {"проверки идут": "остановлено: проверки данных",
                       "сверка идёт": "остановлено: сверка спорного"}
# Побочные линии со своими ключами состояния (ревью ступени 1
# 2026-08-22: гибель процесса оставляла «сертификация идёт» навсегда,
# и каждый следующий запуск получал 409 без пути восстановления).
_ORPHAN_SIDE_STATES = {
    "smt_state": {"SMT-проверка идёт": "остановлено: SMT-проверка"},
    # Шаг 6 каталога шаблонов: побочная линия заявок в каталог.
    "catalog_state": {"заявки в каталог идут":
                      "остановлено: заявки в каталог"},
}


def recover_orphaned_runs() -> list[str]:
    """Обход каталога прогонов: осиротевшие состояния «… идёт/идут»
    (обе линии — правил и данных) → «остановлено: <стадия>».
    Возвращает список переводов для журнала сервера."""
    recovered: list[str] = []
    root = runs_root()
    if not root.is_dir():
        return recovered
    for run_dir in sorted(root.glob("run-*")):
        if not re.fullmatch(r"run-\d{4}", run_dir.name) \
                or not run_dir.is_dir():
            continue
        try:
            st = _read_status(run_dir)
        except (ValueError, OSError) as e:
            print(f"CDPM: {run_dir.name}: статус не прочитан ({e}) — "
                  f"прогон пропущен", flush=True)
            continue
        changed: dict = {}
        for key, mapping in (("state", _ORPHAN_STATES),
                             ("data_state", _ORPHAN_DATA_STATES),
                             *_ORPHAN_SIDE_STATES.items()):
            old = st.get(key)
            new = mapping.get(old) if isinstance(old, str) else None
            # Гибель процесса НА ПАУЗЕ: пауза — занятость линии
            # (второй запуск отвечает 409), поэтому сирота-пауза тоже
            # переводится в честное «остановлено».
            if new is None and isinstance(old, str) \
                    and old.startswith("на паузе: "):
                new = "остановлено: " + old[len("на паузе: "):]
            if new:
                changed[key] = new
                recovered.append(f"{run_dir.name}: «{old}» → «{new}»")
        if changed:
            _update_status(run_dir, **changed)
    return recovered


def catalog_probe_line() -> str:
    """Строка журнала старта о каталоге шаблонов (шаг 7: каталог
    обязателен). Панель СТАРТУЕТ в любом случае — но молча стартовать
    без словарей нельзя: с этой строки видно, будет ли контур работать,
    ещё до первого нажатия."""
    got = catalog_bridge.probe()
    if got["ok"]:
        return (f"каталог шаблонов: {got['version']} "
                f"{got['fingerprint'][:12]} ({got['path']}); "
                f"предупреждений {got['warnings']}")
    return (f"каталог шаблонов недоступен: {got['error']} — прогоны "
            f"будут отказывать словами (проба: GET /api/catalog, "
            f"python -m cdpm.catalog_bridge)")


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    for line in recover_orphaned_runs():
        print(f"CDPM: осиротевшая стадия остановлена — {line}",
              flush=True)
    # Проба готовности каталога при старте: отметка в журнал процесса.
    # Отказ панель не роняет (иначе площадка ушла бы в цикл
    # перезапусков) — он называется словами здесь и на каждом рубеже.
    print(f"CDPM: {catalog_probe_line()}", flush=True)
    yield


class _RecordBudgetRoute(APIRoute):
    """One record budget for direct and indirect HTTP call paths."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def with_record_budget(request):
            with run_record_mod.request_write_budget(
                    run_record_mod.REQUEST_WRITE_WINDOW_S) as warnings:
                response = await handler(request)
            if warnings:
                response.headers["X-CDPM-Record-Warning"] = "record-refresh-busy"
                response.headers["X-CDPM-Record-Runs"] = ",".join(sorted({
                    name for name in warnings if re.fullmatch(r"run-\d{4}", name)}))
            return response
        return with_record_budget


app = FastAPI(title="CDPM — модуль обработки данных авиакомпании",
              docs_url=None, redoc_url=None, lifespan=_lifespan)
app.router.route_class = _RecordBudgetRoute


def _record_busy_response(_request, exc):
    return JSONResponse(status_code=503, content={"detail": {
        "code": run_record_mod.RecordWriteBusy.code, "error": str(exc)}},
        headers={"Retry-After": "1"})


app.add_exception_handler(run_record_mod.RecordWriteBusy, _record_busy_response)


# ── Каталог шаблонов недоступен: ОДИН перевод в 503 на всю панель ──
# Ручек, которым нужны словари, десятки (форма, свод, выгрузка, заявки,
# вопросы условий); россыпь `try/except` по ним стареет вразнобой и
# однажды выпустит наружу трассировку. Поэтому перевод один — обработчик
# отказа приложения: любая синхронная ручка, дошедшая до словаря без
# каталога, отдаёт 503 с машинным кодом `catalog-unavailable` и словами
# причины (образец — COSMOS `/api/template-base`, webapp/app.py).
def catalog_unavailable_body(e) -> dict:
    """Тело отказа: слова причины и машинный код — без секретов."""
    return {"error": f"каталог шаблонов недоступен: "
                     f"{catalog_bridge.words_of(e)}",
            "code": catalog_bridge.CatalogRequired.code}


def catalog_unavailable(e) -> HTTPException:
    """Отказ ручки словами (503 — беда контура, не порок запроса)."""
    return HTTPException(503, detail=catalog_unavailable_body(e))


def _catalog_unavailable_response(_request, exc):
    return JSONResponse(status_code=503,
                        content={"detail": catalog_unavailable_body(exc)})


for _exc_class in catalog_bridge.CATALOG_ERRORS:
    app.add_exception_handler(_exc_class, _catalog_unavailable_response)


@app.get("/api/catalog")
def api_catalog():
    """Проба каталога шаблонов, которым работает панель (шаг 7): 200
    всегда — и когда каталога нет. Признак, версия, отпечаток, путь,
    число предупреждений и слова; ключей и адресов служб здесь нет.

    Та же правда, что у команды `python -m cdpm.catalog_bridge` в venv
    площадки: чек-лист раската сверяет их между контурами."""
    return catalog_bridge.probe()


# Фабрика языковой модели; тесты подменяют её своей функцией.
llm_factory = llm_mod.cosmos_llm

# Фабрика зрительного канала (ПИ-1); тесты подменяют.
vision_llm_factory = llm_mod.vision_channel


def _vision_role_models(run_dir=None) -> tuple:
    """Модели роли «vision» из раскладки (пер-прогонной либо
    площадочной — заморозка идёт до настройки прогона, поэтому
    обычно действует площадочный дефолт): (cloud_model, local_model)."""
    try:
        agents = providers_mod.load_agents(run_dir) if run_dir \
            else providers_mod.load_agents(Path("."))
    except Exception:                                     # noqa: BLE001
        agents = {}
    ch = (agents.get("vision") or {}).get("channels") or {}

    def _m(c):
        m = (c or {}).get("model")
        return None if m in (None, "", "(по умолчанию)") else m
    return _m(ch.get("cloud")), _m(ch.get("local"))


def _vision_for_images(cloud_images: bool, run_dir=None,
                       scope: str = "local-only",
                       scope_reading: str = ""):
    """Канал зрения для заморозки изображений (ПИ-1).

    Допуск моделей — ПРИЗНАК КОМПЛЕКТА, и он известен уже при создании
    прогона (03.09), поэтому зрение при заморозке судится тем же
    действующим допуском, что и все прочие стадии (аудит 04.09, A12):
    `scope` — действующий допуск комплекта. Внутри допуска действует
    правило строжайшего доступного класса: локальная зрительная
    модель, если настроена; иначе облако — только по явному параметру
    запроса `cloud_images=true` И только при допуске «any»; при допуске
    «local-only» просьба облака — честный отказ словами, не вызов
    облака; иначе честный отказ (тихая деградация запрещена политикой
    классов данных). Модель — из роли «vision» раскладки (2.09),
    иначе ключи настроек."""
    cloud_model, local_model = _vision_role_models(run_dir)
    # Модели роли передаются только когда заданы: фабрику подменяют
    # тесты и прежние обёртки с сигнатурой (scope, requested).
    extra = {k: v for k, v in (("cloud_model", cloud_model),
                               ("local_model", local_model)) if v}
    if local_model or llm_mod.local_vision_available():
        return _journal_vision(run_dir, vision_llm_factory(
            scope, requested="local", **extra))
    if cloud_images:
        if scope == "local-only":
            raise llm_mod.LLMCallError(
                "распознавание изображений облаком (cloud_images=true) "
                "запрещено признаком комплекта: допуск моделей «только "
                "локально» (local-only" + (f": {scope_reading}"
                                           if scope_reading else "")
                + ") запрещает облако для изображений; локальная "
                "зрительная модель не настроена — настройте её (ключ "
                "local_vision_model в настройках CDPM) либо задайте "
                "комплекту допуск any (класс данных synthetic/open или "
                "явный llm_scope=any с основанием)")
        return _journal_vision(run_dir, vision_llm_factory(
            scope, requested="cloud", **extra))
    raise llm_mod.LLMCallError(
        "распознавание изображений локальной моделью недоступно: "
        "зрительная модель не настроена; либо настройте её (ключ "
        "local_vision_model в настройках CDPM), либо явно разрешите "
        "облако для изображений параметром cloud_images=true")


def _scope_violation_words(ks: dict, err: str) -> str:
    """Отказ дисциплины контура COSMOS (ScopeViolation) словами — ПО
    ЗНАЧЕНИЮ действующего допуска комплекта (A12): при «только
    локально» источник запрета — признак комплекта; при ином допуске
    признак облака не запрещает, и отказ так и называется — отказ
    дисциплины контура при данном допуске (слова «запрещено признаком
    комплекта» были бы ложью)."""
    effective, reading = ks.get("effective"), ks.get("reading") or ""
    if effective == "local-only":
        return ("распознавание изображений облаком запрещено признаком "
                "комплекта (допуск моделей «только локально», local-only"
                + (f": {reading}" if reading else "") + "): " + err)
    return ("отказ дисциплины контура COSMOS при допуске комплекта "
            f"{effective}" + (f" ({reading})" if reading else "")
            + ": " + err)


def exchange_llm_factory(scope: str, requested: str):
    """Канал для роли аудит-цикла (аудитор/обработчик просят каналы
    независимо); тесты подменяют. Таймаут выше обычного: запрос роли
    несёт сотни клауз и весь свод — на живом прогоне UniA 2026-08-20
    три подряд 300-секундных таймаута уронили раунд 2. Контур —
    эффективный допуск ПРИЗНАКА комплекта, разрешённый вызывающей
    стороной (_run_scope): площадка не прижимает (2026-09-03)."""
    return llm_mod.cosmos_llm(scope, requested=requested, timeout_s=600.0)


# ── Пауза / продолжение / остановка идущих стадий (ПС-1) ──
# Управление кооперативное: длинная стадия зовёт контрольную точку
# между порциями работы (чанками, раундами); идущий запрос к модели не
# прерывается — дорабатывает до точки. Команда живёт в control.json
# прогона и действует на все идущие стадии прогона.

class StopRequested(RuntimeError):
    """Стадия остановлена пользователем — это не отказ."""


def _control_path(run_dir: Path) -> Path:
    return run_dir / "control.json"


def _read_command(run_dir: Path) -> str | None:
    path = _control_path(run_dir)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")) \
            .get("command")
    except (ValueError, PermissionError):
        return None


def _make_checkpoint(run_dir: Path, stage: str, running_state: str,
                     state_key: str = "state"):
    """Контрольная точка стадии: «pause» — честное состояние
    «на паузе: стадия» и ожидание; «resume» — возврат состояния
    «… идёт»; «stop» — StopRequested (вызывающий пишет
    «остановлено: стадия»)."""
    def checkpoint() -> None:
        command = _read_command(run_dir)
        if command == "stop":
            raise StopRequested(stage)
        if command != "pause":
            return
        _update_status(run_dir, **{state_key: f"на паузе: {stage}"})
        while True:
            time.sleep(0.5)
            command = _read_command(run_dir)
            if command == "stop":
                raise StopRequested(stage)
            if command != "pause":
                break
        _update_status(run_dir, **{state_key: running_state})
    return checkpoint


@app.post("/api/runs/{run_id}/control")
@_journaled("control", "command", sync=True)
def run_control(run_id: str, command: str):
    """Команда идущим стадиям прогона: pause | resume | stop."""
    run_dir = _run_dir(run_id)
    if command not in ("pause", "resume", "stop"):
        raise HTTPException(400, f"недопустимая команда {command!r}")
    _control_path(run_dir).write_text(
        json.dumps({"command": command}, ensure_ascii=False),
        encoding="utf-8")
    return {"run": run_id, "command": command}


#: Заголовок Cloudflare Access с почтой вошедшего: панель за краем
#: Access знает, КТО объявляет прогон, — умолчание поля «кто запускает».
ACCESS_EMAIL_HEADER = "Cf-Access-Authenticated-User-Email"


def _who_declares(request=None, created_by: str = "") -> str:
    """Кто объявляет прогон (`declaration.created_by`, 11.1): имя из
    формы, иначе почта Cloudflare Access, иначе «панель CDPM»."""
    if (created_by or "").strip():
        return (created_by or "").strip()
    if request is not None:
        try:
            email = (request.headers.get(ACCESS_EMAIL_HEADER) or "").strip()
        except Exception:                                 # noqa: BLE001
            email = ""
        if email:
            return email
    return run_record_mod.PANEL_ACTOR


def _form_or_query(form_value: str, query_value: str) -> str:
    """Одно имя — два транспорта (поле формы multipart у раннера, строка
    запроса у формы панели): поле формы главнее, пустые — как не переданные."""
    return (form_value or "").strip() or (query_value or "").strip()


@app.post("/api/runs")
async def create_run(request: Request,
                     files: list[UploadFile],
                     cloud_images: bool = False,
                     customer: str = "",
                     data_class: str = "",
                     llm_scope: str = "",
                     llm_scope_basis: str = "",
                     purpose: str = "",
                     created_by: str = "",
                     answer_policy: str = "",
                     planned_stages: str = "",
                     stop_after: str = "",
                     expectations_kind: str = "",
                     expectations_ref: str = "",
                     record_run_id: str = "",
                     purpose_form: str = Form("", alias="purpose"),
                     created_by_form: str = Form("", alias="created_by"),
                     answer_policy_form: str = Form("", alias="answer_policy"),
                     planned_stages_form: str = Form("", alias="planned_stages"),
                     stop_after_form: str = Form("", alias="stop_after"),
                     expectations_kind_form: str = Form("", alias="expectations_kind"),
                     expectations_ref_form: str = Form("", alias="expectations_ref"),
                     record_run_id_form: str = Form("", alias="record_run_id"),
                     declaration_json: str = Form("", alias="declaration"),
                     declaration_sha256_form: str = Form("", alias="declaration_sha256")):
    """Новый прогон: HTTP-слой — файлы читаются в байты, всё остальное
    делает `_create_run_from_bytes` (та же функция — у водителя повтора).

    Поля объявления идут ДВУМЯ транспортами под одними именами (договор с
    COSMOS 18:1x): раннер наборов шлёт их полями формы multipart рядом с
    файлами, форма панели — строкой запроса; поле формы главнее.

    Объявление прогона (шаг 2, 11.1) — необязательные параметры формы:
    цель, кто объявляет, политика ответов, план этапов и остановка,
    ожидания и номер записи хозяина (раннер передаёт свой `run_id` —
    тогда голова его, а файл записи ведёт панель-хранитель)."""
    items = [(Path(up.filename or "").name, await up.read())
             for up in (files or [])]
    return _create_run_from_bytes(
        items, cloud_images=cloud_images, customer=customer,
        data_class=data_class, llm_scope=llm_scope,
        llm_scope_basis=llm_scope_basis,
        declaration={"purpose": _form_or_query(purpose_form, purpose),
                     "created_by": _who_declares(
                         request, _form_or_query(created_by_form, created_by)),
                     "answer_policy": _form_or_query(answer_policy_form,
                                                     answer_policy),
                     "planned_stages": _form_or_query(planned_stages_form,
                                                      planned_stages),
                     "stop_after": _form_or_query(stop_after_form, stop_after),
                     "expectations_kind": _form_or_query(expectations_kind_form,
                                                         expectations_kind),
                     "expectations_ref": _form_or_query(expectations_ref_form,
                                                        expectations_ref),
                     "record_run_id": _form_or_query(record_run_id_form,
                                                     record_run_id),
                     # Вариант «б» (договор с COSMOS 21:4x): хозяин шлёт
                     # голову целиком — JSON объявления и её отпечаток.
                     "declaration_json": (declaration_json or "").strip(),
                     "declaration_sha256": (declaration_sha256_form or "").strip()})


def _passport_at_birth(created_at: str, replay: dict | None) -> dict:
    """Паспорт при рождении: окружение панели; у потомка повтора — режим,
    источник, отпечаток паспорта источника, снимок made_by (2.15)."""
    out = {"environment": passport_mod.environment(), "at": created_at}
    if replay:
        out["replay"] = {k: replay.get(k) for k in (
            "mode", "source_run", "source_dir", "source_passport_sha256",
            "source_made_by", "actions_upto", "started_at",
            # Р3: прогон до журнала — цепочка восстановлена по артефактам.
            "reconstructed", "actions")}
        out["replay"].update({"finished_at": None, "verdict": None,
                              "report": None})
    return out


def _layout_gate_for(run_dir, cloud_allowed: bool) -> dict:
    """Признак раскладки ролей при известном допуске: источник
    действующей раскладки и открыт ли облачный класс. Отдельная функция
    — гейт «Новый прогон» судит раскладку ДО рождения папки прогона (у
    несуществующего каталога действует площадочный носитель)."""
    agents, source = providers_mod._load_agents_with_source(Path(run_dir))
    undeclared = providers_mod.cloud_undeclared_roles(agents) \
        if source != "none" else []
    blocked = bool(cloud_allowed) and (source == "none" or bool(undeclared))
    out = {"source": source, "cloud_gate": "blocked" if blocked else "open"}
    if undeclared:
        out["undeclared_roles"] = undeclared
    if blocked:
        out["detail"] = _layout_refusal(run_dir,
                                        undeclared[0] if undeclared else None,
                                        "cloud", source=source,
                                        eff=(undeclared[0] if undeclared
                                             else None))
    return out


#: Каталог, которого нет: раскладка ролей до рождения папки прогона
#: судится площадочным носителем (пер-прогонного agents.json ещё нет).
_NO_RUN_DIR = "_declaration-preview"


#: Слова причины, когда среду COSMOS не спрашивали ВОВСЕ: голову
#: объявил хозяин целиком, и его модели COSMOS уже в ней.
COSMOS_NOT_ASKED_OWNER = ("среда COSMOS не спрашивалась: голову объявил "
                          "хозяин целиком")


def _declaration_preflight(decl: dict, ks: dict, *,
                           ask_cosmos: bool = True) -> dict:
    """Предполётная сверка гейта «Новый прогон» (11.1) — ДО рождения
    папки, ДО заморозки и до первого вызова модели (зрение при заморозке
    — тоже вызов):

    (а) слова объявления — из словарей (`run_record.words_problems`);
    (б) среда объявима: каталог шаблонов с версией и отпечатком,
        привязки продукта CDPM, коммит кода
        (`run_record.environment_problems`) и раскладка моделей не
        «blocked» при допуске, разрешающем облако;
    (в) документы и признак комплекта судит вызывающая сторона (как
        сегодня).

    Здесь же — ОДИН на запрос опрос среды COSMOS (`channel.environment`,
    предел 5 с, без повторов): его модели идут в голову рядом с
    моделями панели, и спросить их надо ДО отпечатка объявления —
    после старта голова неизменна. COSMOS не ответил — это НЕ проблема
    гейта (панель объявляет прогон своими моделями и честно пишет
    `models_note`), поэтому слова причины едут отдельным ключом, а не в
    `problems`.

    `ask_cosmos=False` — среду НЕ спрашивать вовсе (волна 16): голову
    объявил хозяин ЦЕЛИКОМ (раннер наборов прислал `declaration_json`),
    и она уже несёт свои модели обеих систем — панель кладёт её КАК
    ЕСТЬ, а ответ опроса не использует нигде. Спрашивать не за кого:
    при недоступном COSMOS это были бы 5 с ожидания у кнопки на ровном
    месте (предел канала), а при доступном — лишний вызов, чей ответ
    всё равно выбрасывается.

    Возвращает объявимую часть среды и слова проблем: гейт отказывает
    400 — без папки прогона и без расхода номера."""
    purpose = (decl.get("purpose") or "").strip() or run_record_mod.DEFAULT_PURPOSE
    policy = ((decl.get("answer_policy") or "").strip()
              or run_record_mod.DEFAULT_ANSWER_POLICY)
    stages = run_record_mod.parse_stages(decl.get("planned_stages"))
    stop_after = ((decl.get("stop_after") or "").strip()
                  or run_record_mod.DEFAULT_STOP_AFTER)
    env = passport_mod.environment()
    preview_dir = runs_root() / _NO_RUN_DIR
    layout = _layout_gate_for(preview_dir,
                              cloud_allowed=ks.get("effective") == "any")
    problems = run_record_mod.words_problems(purpose, policy, stages,
                                             stop_after)
    problems += run_record_mod.environment_problems(env)
    if layout.get("cloud_gate") == "blocked":
        problems.append("раскладка моделей не объявлена: "
                        + str(layout.get("detail") or ""))
    agents = providers_mod.load_agents(preview_dir)
    if ask_cosmos:
        cosmos_env, why = channel_mod.environment(scope=ks.get("effective"))
    else:
        cosmos_env, why = None, COSMOS_NOT_ASKED_OWNER
    return {"purpose": purpose, "answer_policy": policy,
            "planned_stages": stages, "stop_after": stop_after,
            "created_by": ((decl.get("created_by") or "").strip()
                           or run_record_mod.PANEL_ACTOR),
            "record_run_id": (decl.get("record_run_id") or "").strip() or None,
            "expectations": {
                "kind": (decl.get("expectations_kind") or "").strip() or None,
                "ref": (decl.get("expectations_ref") or "").strip() or None},
            "environment": env, "layout": layout,
            "models": run_record_mod.layout_models(agents),
            "cosmos_environment": cosmos_env,
            "cosmos_environment_detail": why,
            "problems": problems}


def _full_head(declaration: dict, decl: dict) -> dict | None:
    """Полная голова от хозяина (вариант «б», договор с COSMOS 21:4x):
    поле формы `declaration` — JSON объявления целиком, рядом —
    `declaration_sha256` хозяина. Судится ДО рождения папки: (1) словарь
    COSMOS (схема как данные) и слова; (2) `run_id` равен `record_run_id`,
    если тот назван; (3) пересчёт отпечатка присланной головы равен
    присланному — иначе «отпечаток объявления не сходится» (дрейф
    словаря между копиями); (4) сверка «объявлено / на деле» по ролям
    панели: каталог, привязки CDPM, модели ролей CDPM, коммит cdpm.
    Слова проблем ложатся в `decl["problems"]`; при пустых проблемах
    голова принимается КАК ЕСТЬ, а короткие поля объявления (цель, актор,
    политика ответов, план, остановка, ожидания, номер) берутся из неё."""
    raw = (declaration or {}).get("declaration_json") or ""
    try:
        head = json.loads(raw)
    except ValueError as e:
        decl["problems"].append(f"объявление (declaration): JSON не разобран: {e}")
        return None
    problems = run_record_mod.declaration_problems(head)
    if problems:
        decl["problems"] += problems
        return None
    wanted = (decl.get("record_run_id") or "").strip()
    if wanted and str(head.get("run_id")) != wanted:
        decl["problems"].append(
            f"объявление: run_id {head.get('run_id')!r} не равен record_run_id "
            f"{wanted!r}")
    sent = ((declaration or {}).get("declaration_sha256") or "").strip().lower()
    mine = run_record_mod.declaration_sha256(head)
    if sent and sent != mine:
        decl["problems"].append(
            "отпечаток объявления не сходится: прислан "
            f"{sent[:12]}…, пересчёт панели {mine[:12]}… — дрейф словаря между "
            "копиями")
    decl["problems"] += run_record_mod.environment_mismatch(
        head, decl.get("environment") or {}, run_record_mod._bindings(),
        decl.get("models") or [])
    if decl["problems"]:
        return None
    decl.update({
        "purpose": head.get("purpose"),
        "created_by": head.get("created_by") or decl.get("created_by"),
        "answer_policy": head.get("answer_policy") or decl.get("answer_policy"),
        "planned_stages": list(head.get("planned_stages") or []),
        "stop_after": head.get("stop_after"),
        "expectations": dict(head.get("expectations") or {}),
        "record_run_id": str(head.get("run_id")),
    })
    return head


def _declare_run(run_dir: Path, decl: dict, created_at: str, customer: str,
                 kit: dict, ks: dict, items: list,
                 replay: dict | None = None) -> dict:
    """Голова прогона на диск (11.1): полное объявление
    (`declaration.json`) и `run-record.json` в состоянии «объявлен» —
    раньше заморозки и любого вызова модели. Документы правил объявлены
    отпечатками БАЙТОВ загрузки: заморозка их только подтверждает."""
    agents = providers_mod.load_agents(run_dir)
    declaration = run_record_mod.new_declaration(
        run_dir,
        created_at=created_at,
        created_by=decl["created_by"],
        purpose=decl["purpose"],
        airline={"name": (customer or "").strip() or None,
                 "data_class": kit.get("data_class"),
                 "llm_scope": ks.get("effective") or kit.get("llm_scope")},
        rules_documents=run_record_mod.rules_documents_of(items),
        planned_stages=decl["planned_stages"],
        stop_after=decl["stop_after"],
        expectations=decl["expectations"],
        answer_policy=decl["answer_policy"],
        environment=decl["environment"],
        models=run_record_mod.layout_models(agents),
        run_id=decl.get("record_run_id"),
        replay=replay,
        # Среда COSMOS спрошена предполётной сверкой — ОДИН раз на
        # запрос и ДО отпечатка: её модели ложатся в голову рядом с
        # моделями панели, а её молчание — словами в `models_note`.
        cosmos_environment=decl.get("cosmos_environment"))
    return run_record_mod.declare(run_dir, declaration)


def _create_run_from_bytes(items: list, cloud_images: bool = False,
                           customer: str = "", data_class: str = "",
                           llm_scope: str = "", llm_scope_basis: str = "",
                           replay: dict | None = None,
                           declaration: dict | None = None):
    """Новый прогон: загрузка документов + заморозка + гейт заморозки.

    Изображения (jpg/jpeg/png, ПИ-1) распознаются зрительной моделью
    прямо при заморозке; `cloud_images=true` — явное разрешение облака
    для изображений, когда локальной зрительной модели нет (правило
    строжайшего доступного класса — см. `_vision_for_images`), и оно
    действует только в пределах допуска комплекта: действующий допуск
    вычисляется по признаку комплекта ДО заморозки, при «local-only»
    облако для сканов — отказ словами (A12).
    `customer` — название авиакомпании: задаётся на первом экране
    (замечание владельца 2026-08-21) и хранится в статусе; извлечение
    берёт его отсюда, если ему не передали явно."""
    kit_problems = validate_kit_scope(data_class or None, llm_scope or None,
                                      llm_scope_basis)
    if kit_problems:
        raise HTTPException(400, "признак комплекта: " + "; ".join(kit_problems))
    kit = {"data_class": data_class or None,
           "llm_scope": llm_scope or None,
           "llm_scope_basis": (llm_scope_basis or "").strip() or None}
    # Действующий допуск моделей — по признаку комплекта, ДО заморозки:
    # зрение при заморозке судится им же, как и все прочие стадии.
    ks = kit_scope({"kit": kit})
    # Допуск «any» (синтетические/открытые данные либо явное согласие
    # авиакомпании) сам разрешает облако для изображений — отдельная
    # просьба cloud_images=true остаётся как явный флаг интерфейса, но
    # не обязана повторять то, что уже сказал класс данных (живой урок
    # 05.09: ручная заморозка синтетического набора с jpg отказывала
    # «зрительная модель не настроена… разрешите облако параметром»).
    # Для local-only ничего не меняется: облако по-прежнему запрещено.
    cloud_images = bool(cloud_images) or ks.get("effective") == "any"
    # Без документов прогон не рождается (04.09 на Проде: две формы ушли
    # пустыми и оставили прогоны «отказ заморозки, файлов 0»).
    if not items or not any((name or "").strip() for name, _b in items):
        raise HTTPException(400, "документы правил не переданы — добавьте "
                                 "хотя бы один файл")
    # Гейт «Новый прогон» (шаг 2 проекта «Запись о прогоне», 11.1):
    # прогон не создаётся без объявления, и объявление судится ДО
    # рождения папки — отказ не расходует номер и не оставляет следов.
    # Голову объявил хозяин целиком (поле формы `declaration`) — среду
    # COSMOS не спрашиваем вовсе (волна 16): его модели уже стоят в
    # присланной голове, панель кладёт её КАК ЕСТЬ и ответ опроса не
    # использует. Спрашивать не за кого, а при недоступном COSMOS это
    # было бы до 5 с ожидания у кнопки хозяина на ровном месте.
    decl = _declaration_preflight(
        declaration or {}, ks,
        ask_cosmos=not bool((declaration or {}).get("declaration_json")))
    # Вариант «б» (договор с COSMOS 21:4x): хозяин прислал голову
    # целиком — она судится словарём, отпечатком и сверкой «объявлено /
    # на деле» по ролям панели и ложится в declaration.json КАК ЕСТЬ.
    head = None
    if (declaration or {}).get("declaration_json"):
        head = _full_head(declaration, decl)
    if decl["problems"]:
        raise HTTPException(400, "прогон не объявлен: "
                                 + "; ".join(decl["problems"]))
    root = runs_root()
    root.mkdir(parents=True, exist_ok=True)
    nums = [int(p.name.split("-")[1]) for p in root.glob("run-*")
            if re.fullmatch(r"run-\d{4}", p.name)]
    # Номер — следующий за максимумом существующих И за последним
    # выданным (counter.json): номер ушедшего в архив прогона заново не
    # выдаётся (просьба владельца 06.09.2026).
    next_num = max([*nums, _read_counter(root)], default=0) + 1
    run_id = f"run-{next_num:04d}"
    run_dir = root / run_id
    src = run_dir / "src"
    src.mkdir(parents=True)
    _raise_counter(root, next_num)
    if replay:
        # Потомок повтора (проект повтора, Р2): режим журнала — до первого
        # вызова модели (зрение при заморозке), раскладка ролей — копия
        # источника, чтобы заморозка судилась той же ролью «vision».
        call_journal.set_mode(run_dir, replay["mode"],
                              replay.get("source_dir"))
        call_journal.open_journal(run_dir, mode=replay["mode"],
                                  source_run=replay.get("source_run"))
        if replay.get("agents_json"):
            (run_dir / "agents.json").write_bytes(replay["agents_json"])
    created_at = _now_iso()
    saved: list[Path] = []
    entries: list[dict] = []
    for name, body in items:
        if not name:
            raise HTTPException(400, "файл без имени")
        if Path(name).suffix.lower() not in freeze.SUPPORTED:
            raise HTTPException(
                400, f"{name}: формат не входит в первую очередь CDPM "
                     f"(поддержаны: {', '.join(freeze.SUPPORTED)})")
        target = src / name
        target.write_bytes(body)
        saved.append(target)
        entries.append(actions_journal.file_entry(name, body, f"src/{name}"))
    # Голова записи о прогоне (11.1) — СРАЗУ после рождения папки и
    # записи документов: до заморозки и до первого вызова модели.
    # Раскладка ролей берётся у папки прогона (у потомка повтора она уже
    # своя — agents.json копии источника). Номер записи — хозяина:
    # раннер передаёт свой `record_run_id`, и тогда голова его, а файл
    # записи ведёт панель-ХРАНИТЕЛЬ.
    if head is not None:
        # Голова хозяина — КАК ЕСТЬ (ничего не дописывать: после старта
        # она неизменна; факты среды панели — записью этапа create).
        run_record_mod.declare(run_dir, head)
    else:
        _declare_run(run_dir, decl, created_at=created_at, customer=customer,
                     kit=kit, ks=ks, items=items, replay=replay)
    # Журнал действий (проект повтора, Р1): рождение прогона — первое
    # действие; вызовы зрения при заморозке относятся к нему.
    action_n = actions_journal.note(
        run_dir, "create",
        params={"customer": (customer or "").strip() or None,
                "data_class": data_class or None,
                "llm_scope": llm_scope or None,
                "llm_scope_basis": kit["llm_scope_basis"],
                "cloud_images": bool(cloud_images)},
        files=entries)
    t_birth = time.monotonic()
    from webapp.llm_backend import ScopeViolation

    def _refusal_words(e: BaseException) -> str:
        """Слова отказа зрительного канала: ScopeViolation (отказ
        дисциплины контура COSMOS, не наследует LLMCallError)
        формулируется ПО ЗНАЧЕНИЮ допуска — «запрещено признаком
        комплекта» только при local-only, иначе это отказ дисциплины
        контура при данном допуске (A12); LLMCallError — как есть."""
        if isinstance(e, ScopeViolation):
            return _scope_violation_words(ks, str(e))
        return str(e)

    vision_refusal = ""
    try:
        vision = None
        if any(p.suffix.lower() in freeze.IMAGE_SUPPORTED
               for p in saved):
            vision = _vision_for_images(cloud_images, run_dir,
                                        scope=ks["effective"],
                                        scope_reading=ks["reading"])
        elif any(p.suffix.lower() == ".pdf" for p in saved):
            # PDF: зрение нужно только страницам-сканам, что видно
            # лишь при разборе — канал строится, если доступен;
            # недоступен — текстовые PDF проходят, а страница-скан
            # получит честный отказ заморозки с номером страницы, и
            # причина, по которой канал не построен (в том числе
            # допуск комплекта), дописывается к отказу словами.
            try:
                vision = _vision_for_images(cloud_images, run_dir,
                                            scope=ks["effective"],
                                            scope_reading=ks["reading"])
            except (llm_mod.LLMCallError, ScopeViolation) as e:
                vision = None
                vision_refusal = _refusal_words(e)
        freeze.freeze(saved, run_dir / "freeze", vision_llm=vision)
    except (freeze.FreezeError, llm_mod.LLMCallError, ScopeViolation) as e:
        err = _refusal_words(e)
        if isinstance(e, freeze.FreezeError) and vision is None \
                and vision_refusal:
            # Страница-скан без канала: настоящая причина — почему
            # канал не построен — а не только «канал не передан».
            err = f"{err}; канал зрения не построен: {vision_refusal}"
        _replace_status(run_dir, {"state": "отказ заморозки",
                                  "error": err, "kit": kit,
                                  "created_at": created_at,
                                  "purpose": decl["purpose"],
                                  "passport": _passport_at_birth(created_at, replay),
                                  **({"title": replay["title"]}
                                     if replay and replay.get("title") else {})})
        actions_journal.end(run_dir, action_n,
                            state_after={"state": "отказ заморозки"},
                            duration_s=round(time.monotonic() - t_birth, 3),
                            calls=[0, call_journal.count(run_dir)], error=err)
        _refresh_run_record(run_dir)
        return JSONResponse({"run": run_id, "state": "отказ заморозки",
                             "error": err}, status_code=422)
    problems, counters = gate_freeze.check_kit(run_dir / "freeze", saved)
    # Смешанные страницы PDF без распознавания / рисунки без текста —
    # предупреждение статуса (сверка с оригиналом обязательна; A2.3).
    freeze_warnings = freeze.mixed_warnings(json.loads(
        (run_dir / "freeze" / "manifest.json").read_text(encoding="utf-8")))
    _replace_status(run_dir, {
        "state": "заморожено" if not problems else "красный гейт "
                                                   "заморозки",
        "files": [p.name for p in saved],
        "freeze_counters": counters,
        "freeze_problems": problems,
        **({"freeze_warnings": freeze_warnings} if freeze_warnings else {}),
        **({"customer": customer.strip()}
           if (customer or "").strip() else {}),
        # Признак допуска моделей — У КОМПЛЕКТА (решение владельца
        # 2026-09-03): хранится с прогоном от создания, не выбирается
        # на запуск.
        "kit": kit,
        # Дата создания — для сортировки списка прогонов (06.09.2026).
        "created_at": created_at,
        # Цель прогона — из объявления (гейт «Новый прогон», 11.1).
        "purpose": decl["purpose"],
        # Паспорт прогона (слово владельца 07.09.2026): окружение панели
        # на момент рождения — код, спецификация, решатели, каталог;
        # у потомка повтора — режим и источник (проект повтора, Р2).
        "passport": _passport_at_birth(created_at, replay),
        **({"title": replay["title"]} if replay and replay.get("title") else {}),
    })
    # S06-R2: ранний признак раскладки — в статусе с момента создания:
    # допуск разрешает облако, а раскладка не объявлена → «blocked» и
    # слова отказа видны до старта первой облачной стадии.
    gate = _layout_gate(run_dir)

    def _with_layout(st: dict) -> None:
        st["layout"] = gate
    _mutate_status(run_dir, _with_layout)
    actions_journal.end(run_dir, action_n,
                        state_after=_state_brief(_read_status(run_dir)),
                        duration_s=round(time.monotonic() - t_birth, 3),
                        calls=[0, call_journal.count(run_dir)])
    _refresh_run_record(run_dir)
    # Голова записи — в ответе рождения (договор с COSMOS 21:3x, живая
    # находка run-0135): раннер кладёт отпечаток ХРАНИТЕЛЯ в свои append,
    # а не отпечаток собственного паспортного объявления — две головы на
    # один номер расходились бы по построению (409 declaration-mismatch).
    head = run_record_mod.read_declaration(run_dir) or {}
    return {"run": run_id, **_read_status(run_dir),
            "record": {"run_id": (head.get("declaration") or {}).get("run_id"),
                       "declaration_sha256": head.get("declaration_sha256")}}


# ── Политика обработки файлов данных (решение владельца 2026-08-18) ──
# По умолчанию строжайшая: «local» — только локальная модель.
# «cloud-country» — облачная модель внутри страны: собственного канала
# у неё пока НЕТ, до подключения такой файл обрабатывается локальной
# моделью (fail-closed: строже, не мягче). «cloud» — облачная модель.
FILE_POLICIES = ("local", "cloud-country", "cloud")


def _files_config_path(run_dir: Path) -> Path:
    return run_dir / "files-config.json"


def _default_file_policy(status: dict) -> str:
    """Умолчание политики незаписанного файла — по допуску моделей
    комплекта (cdpm.kit.kit_scope, решение владельца 03.09.2026:
    синтетические и открытые данные — любые модели, включая облачные):
    допуск «any» → «cloud», иначе строжайшее «local». Живой урок 05.09
    (репетиция демо): прогон синтетического набора, созданный руками в
    панели, получал «local» у всех файлов, слот облака у обработчика
    оказывался «недоступен», извлечение уходило в незапущенную локальную
    модель и отказывало — пользователь должен был переключать шестерёнку
    у каждого файла, хотя класс данных уже всё сказал."""
    try:
        return "cloud" if kit_scope(status).get("effective") == "any" else "local"
    except Exception:                                     # noqa: BLE001
        return "local"


def _read_files_config(run_dir: Path) -> dict:
    """Политика и примечание КАЖДОГО файла прогона (документы правил +
    операционные данные); незаписанный файл получает умолчание по
    допуску комплекта (_default_file_policy): «local», если комплект
    не разрешает облако."""
    stored = {}
    path = _files_config_path(run_dir)
    if path.is_file():
        stored = json.loads(path.read_text(encoding="utf-8")) \
            .get("files", {})
    st = _read_status(run_dir)
    default = _default_file_policy(st)
    files = list(st.get("files", [])) + list(st.get("data_files", []))
    return {f: {"processing": stored.get(f, {}).get("processing",
                                                    default),
                "note": stored.get(f, {}).get("note", "")}
            for f in files}


def _channel_for_policy(scope: str, run_dir: Path, policy: str):
    """Канал обработчика для файла: слот класса политики из
    конфигурации роли («cloud» / «local» / «cloud-country»)."""
    return _channel_for(scope, run_dir, "applier", None,
                        klass=policy, fallback_role="worker")


@app.get("/api/runs/{run_id}/files-config")
def files_config(run_id: str):
    return {"run": run_id,
            "files": _read_files_config(_run_dir(run_id)),
            "policies": list(FILE_POLICIES)}


@app.put("/api/runs/{run_id}/files-config")
@_journaled("files-config", "body", sync=True)
def put_files_config(run_id: str, body: dict):
    """Политика и примечание файлов — СЛИЯНИЕМ по файлам под замком
    прогона: запись, не пришедшая в теле, остаётся (интерфейс шлёт
    то, что видел; снимок без файла данных не должен снимать его
    политику). Запись убирает только каскад удаления файла."""
    run_dir = _run_dir(run_id)
    got = (body or {}).get("files", {})
    with _status_guard(run_dir):
        st = _read_status(run_dir)
        known = set(st.get("files", [])) | set(st.get("data_files", []))
        problems: list[str] = []
        for name, cfg in got.items():
            if name not in known:
                problems.append(f"файла {name!r} нет в прогоне")
                continue
            if not isinstance(cfg, dict):
                problems.append(f"{name}: конфигурация не объект")
                continue
            if cfg.get("processing", "local") not in FILE_POLICIES:
                problems.append(f"{name}: политика "
                                f"{cfg.get('processing')!r} вне перечня "
                                f"{FILE_POLICIES}")
            if not isinstance(cfg.get("note", ""), str):
                problems.append(f"{name}: примечание не строка")
        if problems:
            raise HTTPException(400, "; ".join(problems))
        path = _files_config_path(run_dir)
        stored = json.loads(path.read_text(encoding="utf-8")) \
            .get("files", {}) if path.is_file() else {}
        for name, cfg in got.items():
            stored[name] = {"processing": cfg.get("processing", "local"),
                            "note": cfg.get("note", "")}
        path.write_text(
            json.dumps({"files": stored}, ensure_ascii=False, indent=2)
            + "\n", encoding="utf-8")
    return {"run": run_id, "files": _read_files_config(run_dir),
            "policies": list(FILE_POLICIES)}


@app.post("/api/runs/{run_id}/extract")
@_journaled("extract", "customer", "scope")
def start_extract(run_id: str, customer: str = "",
                  scope: str | None = None):
    """Извлечение — в фоновом потоке: на настоящих документах это
    минуты работы языковой модели; интерфейс опрашивает статус.

    Канал КАЖДОГО файла выбирается его политикой (шестерёнка у файла в
    блоке «Файлы данных»); перекрёстная стадия поиска противоречий
    смешивает строки всех файлов и идёт по строжайшей политике
    прогона."""
    run_dir = _run_dir(run_id)
    if scope not in (None, "any", "local-only"):
        raise HTTPException(400, f"недопустимый scope {scope!r}")
    status = _read_status(run_dir)
    # Название авиакомпании задаётся на первом экране и живёт в
    # статусе; явный параметр главнее сохранённого.
    customer = (customer or "").strip() \
        or (status.get("customer") or "").strip()
    if not customer:
        raise HTTPException(400, "не задано название авиакомпании")
    if status.get("state") == "извлечение идёт":
        raise HTTPException(409, "извлечение уже идёт")
    # Класс-дефект прод-демо run-0001 (30.08): извлечение при
    # бегущем цикле молча стартовало параллельную стадию — вторая
    # половина гонки состояния. Пауза — та же занятость (нить жива).
    if _line_busy(status.get("state")):
        raise HTTPException(409, f"извлечение при «{status['state']}» "
                                 f"запрещено: остановите стадию или "
                                 f"дождитесь её конца")
    if not (run_dir / "freeze" / "manifest.json").is_file():
        raise HTTPException(409, "прогон не заморожен")
    # Вторая половина того же класса: ПЕРЕизвлечение рождает НОВЫЙ
    # свод — обмены, реестр и регресс-токены старого свода обязаны
    # уйти в архив ЦЕЛИКОМ (перенос токенов сквозь ревизии законен
    # только внутри одного свода; живой случай run-0001: токены
    # старого свода судили новый — сторож отката честно валил цикл).
    _archive_exchange_for_reextract(run_dir)
    _claim_stage(run_dir, state="извлечение идёт", customer=customer,
                 scope=_run_scope(run_dir, scope))
    _control_path(run_dir).unlink(missing_ok=True)
    control = _make_checkpoint(run_dir, "извлечение",
                               "извлечение идёт")

    def _work():
        # Свои ключи стадии копятся здесь и в конце СЛИВАЮТСЯ в статус
        # (не снимок целиком — см. замок статуса).
        own: dict = {}
        try:
            # РВ-140 п. 1: ответы прежней модели — снимком ДО извлечения
            # (оно пересобирает модель с нуля); перенос — после.
            from cdpm import answer_carry as _carry_mod
            carry_items = _carry_mod.snapshot_file(
                run_dir / "extract" / "model.json")
            cfg = _read_files_config(run_dir)
            # Второй замок кэша (A11, «ремень и подтяжки»): отпечаток
            # раскладки ролей, политики файлов, допуска и моделей
            # умолчания лежит в extract-cache/meta.json; расхождение —
            # кэш сносится целиком до первого запроса, о чём статус
            # говорит словами.
            cache_note = _reset_stale_extract_cache(
                run_dir, _extract_cache_fingerprint(
                    run_dir, _run_scope(run_dir, scope), cfg))
            if cache_note:
                own["extract_cache_note"] = cache_note
                _update_status(run_dir, extract_cache_note=cache_note)
            manifest = json.loads(
                (run_dir / "freeze" / "manifest.json")
                .read_text(encoding="utf-8"))
            # Извлечение — роль обработчика (worker) слотом класса
            # политики файла; роль applier здесь не при чём (прогон №2
            # обратной задачи H0, 2.09: извлечение шло каналом
            # applier=opus-5 вопреки раскладке владельца «извлечение —
            # sonnet-5»; applier — применение ответов и линия данных).
            channels = {p: _channel_for(scope, run_dir, "extractor",
                                        None, klass=p)
                        for p in {c["processing"] for c in cfg.values()}}
            llm_by_sid = {
                f["sid"]: channels[cfg.get(f["file"], {})
                                   .get("processing", "local")]
                for f in manifest.get("files", [])}
            # Общие проходы видят данные всего прогона, поэтому все
            # их каналы, включая сверщика logic, берут строжайший слот.
            run_class = _strictest_class(run_dir)
            cross = _channel_for(scope, run_dir, "extractor", None,
                                 klass=run_class)
            # Прогресс чанков (директива владельца 2026-08-20) — в
            # status.json, интерфейс показывает его на блоке.
            def _progress(stage, done, total, rules):
                _update_status(run_dir, progress={
                    "stage": stage, "done": done,
                    "total": total, "rules": rules})

            # Слой 2 (перекрёстное чтение, план «прямой ход» 2.7.2):
            # второй читатель — роль second_reader (родитель auditor),
            # обязательно другого семейства, чем извлечение; иначе слой
            # пропускается с записанной причиной, не молча.
            # Второй читатель — облачный слот: документы с политикой
            # «только локально» / «внутри страны» ему не показываются,
            # поэтому слой 2 идёт лишь при единой облачной политике файлов.
            _classes = {c.get("processing", "local") for c in cfg.values()} or {"local"}
            if _classes != {"cloud"}:
                # Облачные роли здесь не используются: их семейства
                # (в том числе умолчания отсутствующих слотов) не причина.
                _indep = [f"политика файлов {sorted(_classes)}: второе чтение "
                          f"облачным читателем невозможно"]
            else:
                _agents_now = providers_mod.load_agents(run_dir)
                _indep = list(providers_mod.independence_problems(_agents_now))
            second = None if _indep else _channel_for(
                scope, run_dir, "second_reader", klass="cloud")
            summary = extract.run_extract(
                run_dir / "freeze", run_dir / "extract",
                _channel_for(scope, run_dir, "extractor", klass=run_class), customer,
                llm_by_sid=llm_by_sid, cross_llm=cross,
                control=control,
                cache_dir=run_dir / "extract-cache",
                progress=_progress, second_llm=second,
                second_note="; ".join(_indep) if _indep else None)
            # Ступень 3: целевой проход производства logic + рубеж 1
            # (обратный пересказ сверщиком) — общий промпт кандидатов
            # производит блоки вероятностно, узкий вопрос по правилу
            # надёжен; сверщик — независимая роль (наследует аудитора).
            summary["logic"] = extract.produce_logic(
                run_dir / "extract",
                _channel_for(scope, run_dir, "extractor", klass=run_class),
                verifier=_channel_for(scope, run_dir, "logic_verifier",
                                      klass=run_class),
                control=control,
                cache_dir=run_dir / "extract-cache",
                # Соль — конфигурация ДЕЙСТВУЮЩЕЙ роли извлечения
                # (своя, иначе родитель worker), не «worker» вслепую
                # (аудит 04.09, A11); отпечаток самого канала проход
                # добавляет сам.
                cache_salt=_role_config_salt(run_dir, "extractor"),
                progress=_progress)
            # Excel-книга анкеты для заполнения — сразу после
            # извлечения, как у всех последующих стадий (прежде до
            # первого цикла её на диске не было).
            published = m_mod.load_model(run_dir / "extract" / "model.json")
            # РВ-140 п. 1: ответы авиакомпании переносятся по месту в
            # документе (id, отпечаток издания, живые строки); каждый —
            # «перенесён» либо «задан заново» с причиной, молча — ни один.
            carry_fate = _carry_mod.carry(published, carry_items,
                                          data_line_done=False,
                                          run_dir=run_dir)
            _carry_mod.write(run_dir / "extract", carry_items, carry_fate)
            if carry_items:
                own["answers_carry"] = _carry_mod.summary(carry_fate)
            m_mod.publish_model(published, run_dir / "extract" / "model.json")
            if carry_items:
                # Свод и анкета — из модели ПОСЛЕ переноса (гейт
                # извлечения сверяет их с моделью).
                (run_dir / "extract" / "svod.md").write_text(
                    render_mod.render_svod(published), encoding="utf-8")
                (run_dir / "extract" / "anketa.md").write_text(
                    render_mod.render_anketa(published), encoding="utf-8")
            anketa_xlsx_mod.render_anketa_xlsx(
                published, run_dir / "extract" / "anketa.xlsx")
            problems, counters = gate_extract.check(
                run_dir / "freeze", run_dir / "extract")
            own.update(
                state="извлечено" if not problems else "красный гейт "
                                                       "извлечения",
                extract_summary=summary,
                error=None,
                extract_counters=counters,
                extract_problems=problems)
        except StopRequested:
            own.update(state="остановлено: извлечение", error=None)
        except Exception as e:                             # noqa: BLE001
            own.update(**_stage_failure("отказ извлечения", e,
                                        run_dir=run_dir))
        _update_status(run_dir, progress=None, **own)

    _start_stage_thread(run_dir, _work)
    return {"run": run_id, "state": "извлечение идёт"}


@app.post("/api/runs/{run_id}/answers")
async def upload_answers(run_id: str, file: UploadFile,
                         scope: str | None = None):
    """Приём книги ответов: HTTP-слой — файл читается в байты, всё
    остальное делает `_upload_answers_bytes` (та же функция — у
    водителя повтора)."""
    return _upload_answers_bytes(run_id, Path(file.filename or "").name,
                                 await file.read(), scope)


def _upload_answers_bytes(run_id: str, name: str, data: bytes,
                          scope: str | None = None):
    """Приёмник ответов анкеты (интерактивный цикл, ИР-1): заполненная
    Excel-книга анкеты → разбор (нечёткие ответы развилок — модели по
    строжайшему классу файлов прогона) → машинное применение
    однозначного → ревизия свода и анкеты следующей ревизии + гейт.
    История ответов — в answers/rev-<N>/ прогона."""
    run_dir = _run_dir(run_id)
    if scope not in (None, "any", "local-only"):
        raise HTTPException(400, f"недопустимый scope {scope!r}")
    if Path(name).suffix.lower() != ".xlsx":
        raise HTTPException(400, f"{name}: ответы принимаются "
                                 f"заполненной книгой анкеты (.xlsx)")
    status = _read_status(run_dir)
    if _line_busy(status.get("state")) \
            or _line_busy(status.get("data_state")):
        raise HTTPException(409, "прогон занят другой стадией")
    model_path = run_dir / "extract" / "model.json"
    if not model_path.is_file():
        raise HTTPException(409, "сначала извлечение: отвечать не на "
                                 "что — анкеты нет")
    doc = _load_model_or_409(model_path)
    revision, rev_dir = answers_mod.reserve_revision_directory(
        run_dir / "answers", int(doc.get("meta", {}).get("revision", 1)))
    book_path = rev_dir / "anketa-otvety.xlsx"
    with book_path.open("xb") as book_file:
        book_file.write(data)
    try:
        reading = answers_mod.read_book(book_path, doc)
    except Exception as e:                                 # noqa: BLE001
        raise HTTPException(400, f"книга не читается: {e}")
    raw_map, read_problems = reading.answers, reading.problems
    # Отказанные чтением ответы (чужая ревизия книги, переизданный
    # вопрос, противоречащие дубли; аудит 04.09, A3.1/A3.2) — тоже
    # ответы: ложатся в модель «не разобран» с причиной и краснят
    # гейт, а не пропадают в 400.
    refused = reading.refused
    if not raw_map and not refused:
        raise HTTPException(400, "в книге нет ни одного ответа — "
                                 "колонка «ОТВЕТ АВИАКОМПАНИИ» пуста")
    klass = _strictest_class(run_dir)
    actions_journal.note(
        run_dir, "answers", params={"scope": scope},
        files=[actions_journal.file_entry(
            name, data, f"answers/rev-{revision:03d}/anketa-otvety.xlsx")],
        state_before=_state_brief(status))
    _claim_stage(run_dir, also=("data_state",),
                 state="применение ответов идёт")
    _control_path(run_dir).unlink(missing_ok=True)
    control = _make_checkpoint(run_dir, "применение ответов",
                               "применение ответов идёт")

    def _work():
        own: dict = {}
        try:
            # Две независимые роли (2.09): разборщик свободных ответов
            # и внесение ответов патчами — свои модели, наследуют
            # applier → worker.
            matcher = _channel_for(scope, run_dir, "answer_matcher",
                                   None, klass=klass)
            applier = _channel_for(scope, run_dir, "answer_applier",
                                   None, klass=klass)
            summary = answers_mod.apply_answers(doc, raw_map, matcher,
                                                control=control,
                                                refused=refused,
                                                received_at=reading.received_at,
                                                book_sha256=reading.book_sha256,
                                                revision=revision)
            # Ответы, требующие правки текста строк, вносит
            # обработчик модельными патчами под контрактом (ИР-2).
            summary.update(answers_mod.apply_worker_patches(
                doc, applier, control=control))
            summary["awaiting"] = answers_mod.remaining_awaiting(doc)
            summary["book_sha256"] = reading.book_sha256
            # Отпечаток ревизии ПО СОДЕРЖАНИЮ (11.7 проекта «Запись о
            # прогоне»): корень дерева прогонов судится парами «вопрос
            # → ответ», а не байтами книги (пересохранённая книга с теми
            # же ответами — тот же корень). Считается один раз здесь;
            # запись о прогоне берёт готовое число из сводки ревизии.
            summary["content_sha256"] = \
                answers_fp_mod.book_content_sha256(book_path)
            summary["read_problems"] = read_problems
            # Волна условий: темы файла условий проверяются и на
            # пути ответов (живой брак dobor2 2026-08-27: недостающая
            # тема размаха командировки требовала полной стадии
            # данных только ради постановки вопроса). Дедупликация —
            # внутри ensure; вопросы попадают в переиздаваемую ниже
            # анкету этой же ревизии.
            summary["conditions_questions"] = \
                m_mod.ensure_conditions_questions(doc)
            # П12 (2026-09-02): след носителей контура — какие файлы
            # тем прочитаны и применены ли к этому заказчику.
            summary["contour_carriers"] = m_mod.contour_carriers_report(
                (doc.get("meta") or {}).get("customer"))
            # Миграция классификации и на пути ответов (живой круг
            # полноты run-0002: целевая «минимизировать ночей вне
            # базы» = неполная форма мягкого штрафа — обязана стать
            # правилом ДО сквозной проверки весов, без
            # переизвлечения).
            summary["restored_soft"] = \
                m_mod.restore_soft_penalty_rules(doc)
            # CASE-сироты (живой стоп run-0023 на гейте запуска,
            # 1.09): записи листа наследуют вид разрешённой строки-
            # носителя; строки-запросы закрываются применённой
            # целевой. Детерминированно, fail-closed — см. model.
            summary["case_orphans"] = \
                m_mod.resolve_case_orphans(doc)
            # P-N-SHD 2026-08-30 (живой корень прод-демо): правило,
            # ставшее мягким заплатой ПОСЛЕ применения вопроса
            # целевой, оставалось без пути к весу — сквозной проход
            # обеих частей строителем GOAL на каждом пути ответов.
            # РАНЬШЕ вопроса критерия: тот якорится на Q-3a-GOAL,
            # каскад обязан завершаться за один вызов.
            summary["conditions_questions"] += \
                m_mod.ensure_goal_questions_sweep(doc)
            # П-3 пакета посадки: B-посадки заплат обмена получают
            # вопрос-подтверждение на пути ответов (структурный ключ).
            summary["fit_questions"] = \
                m_mod.ensure_fit_questions(doc)
            # Полнота параметров семьи (09.09.2026): после ответов —
            # новые недостающие параметры спрашиваются, лишние вопросы
            # снимаются кодом (идемпотентно).
            summary["family_param_questions"] = \
                family_params_mod.ensure_family_param_questions(doc)
            # Вопрос машинного критерия обязан вставать и на пути
            # ответов (живой пробел run-0064 ночи демо: GOAL отвечен,
            # а критерий ставила только ручка пересбора — МГ честно
            # держала пейринг на feasible_soft).
            summary["conditions_questions"] += \
                m_mod.ensure_criterion_question(doc)
            # Ремень (а) ночи демо 2026-08-29: value-ответы тем
            # носителя вносятся в правила детерминированно, без
            # модели (двойная вариативность LLM-вноса за вечер).
            summary["topic_params"] = \
                m_mod.apply_topic_value_answers(doc)
            # Семьи мягких правил — классификатором, на том же пути.
            summary["soft_families"] = \
                m_mod.assign_soft_families(doc)
            # Ярлыки кодом (2026-09-02): сквозной проход по своду —
            # своды, собранные до детерминизации, самолечатся здесь,
            # а не краснеют на контракте (ревью пакета r54).
            summary["traits_derived"] = \
                m_mod.derive_all_rule_traits(doc, "путь ответов")
            bad = m_mod.validate_schema(doc) + m_mod.validate_model(doc)
            if bad:
                raise RuntimeError("применённая модель не проходит "
                                   "контракт: " + "; ".join(bad[:3]))
            out = run_dir / "extract"
            m_mod.publish_model(doc, out / "model.json")
            (out / "svod.md").write_text(
                render_mod.render_svod(doc), encoding="utf-8")
            (out / "anketa.md").write_text(
                render_mod.render_anketa(doc), encoding="utf-8")
            anketa_xlsx_mod.render_anketa_xlsx(doc,
                                               out / "anketa.xlsx")
            summary_path = out / "answers-summary.json"
            summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2)
                + "\n", encoding="utf-8")
            (rev_dir / "answers-summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2)
                + "\n", encoding="utf-8")
            problems, counters = gate_answers.check(
                out / "model.json", summary_path,
                out / "svod.md", out / "anketa.md",
                out / "anketa.xlsx")
            own.update(
                state="ответы применены" if not problems
                else "красный гейт ответов",
                error=None,
                answers_summary={
                    **{k: summary[k] for k in
                       ("revision", "received", "applied",
                        "awaiting", "unparsed")},
                    **({"conflicted": summary["conflicted"]}
                       if summary.get("conflicted") else {}),
                    "worker_applied": summary.get("worker_applied", 0),
                    "worker_rejected": len(
                        summary.get("worker_rejected", [])),
                    # A3.2: замечания чтения книги видны в статусе
                    # прогона, не только в скачиваемой сводке.
                    "read_problems": len(read_problems),
                    "refused": len(summary.get("refused", []))},
                answers_problems=problems,
                answers_read_problems=list(read_problems),
                answers_counters=counters)
        except StopRequested:
            own.update(state="остановлено: применение ответов",
                       error=None)
        except Exception as e:                             # noqa: BLE001
            own.update(**_stage_failure("отказ применения ответов", e))
        _update_status(run_dir, **own)

    _start_stage_thread(run_dir, _work)
    # «Получено» = принятые к разбору + отклонённые чтением — то же
    # число, что в сводке стадии (answers_summary.received).
    return {"run": run_id, "state": "применение ответов идёт",
            "revision": revision,
            "received": len(raw_map) + len(refused),
            "accepted": len(raw_map), "refused": len(refused),
            "class": klass}


@app.post("/api/runs/{run_id}/third-party")
async def upload_third_party(run_id: str, file: UploadFile):
    """Приём файла формализации третьей стороны: HTTP-слой — файл в
    байты, остальное делает `_upload_third_party_bytes`."""
    return _upload_third_party_bytes(run_id, Path(file.filename or "").name,
                                     await file.read())


def _upload_third_party_bytes(run_id: str, name: str, data: bytes):
    """Режим 2 (РМ2): приём файла формализации ТРЕТЬЕЙ СТОРОНЫ.
    Файл нормализуется той же заморозкой, что документы; сверка
    запускается отдельной точкой /mode2."""
    run_dir = _run_dir(run_id)
    suffix = Path(name).suffix.lower()
    if suffix not in (".md", ".docx", ".doc", ".xlsx", ".json"):
        raise HTTPException(400, f"{name}: формализация принимается "
                                 f"в md / docx / doc / xlsx / json")
    status = _read_status(run_dir)
    if _line_busy(status.get("state")):
        raise HTTPException(409, "прогон занят другой стадией")
    tp_dir = run_dir / "third-party"
    tp_dir.mkdir(parents=True, exist_ok=True)
    path = tp_dir / name
    path.write_bytes(data)
    try:
        entry = mode2_mod.load_third_party(path)
    except Exception as e:                                 # noqa: BLE001
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"{name}: файл не нормализуется: {e}")
    claims = len(mode2_mod._claims_of(entry))
    actions_journal.note(
        run_dir, "third-party", sync=True,
        files=[actions_journal.file_entry(name, data, f"third-party/{name}")],
        state_before=_state_brief(status))
    _update_status(run_dir, third_party_file=name,
                   third_party_claims=claims)
    return {"run": run_id, "file": name, "claims": claims}


@app.post("/api/runs/{run_id}/mode2")
@_journaled("mode2", "scope")
def start_mode2(run_id: str, scope: str | None = None):
    """Режим 2: сверка формализации третьей стороны со сводом и
    источниками — в фоновом потоке; вопросы части 4 добавляются в
    модель, свод и анкета перерендериваются, аудиторский md-отчёт."""
    run_dir = _run_dir(run_id)
    if scope not in (None, "any", "local-only"):
        raise HTTPException(400, f"недопустимый scope {scope!r}")
    status = _read_status(run_dir)
    if _line_busy(status.get("state")) \
            or _line_busy(status.get("data_state")):
        raise HTTPException(409, "прогон занят другой стадией")
    if not (run_dir / "extract" / "model.json").is_file():
        raise HTTPException(409, "сначала извлечение: сверка идёт "
                                 "против выверенного свода")
    tp_name = status.get("third_party_file")
    if not tp_name or not (run_dir / "third-party" / tp_name).is_file():
        raise HTTPException(409, "сначала загрузите файл формализации "
                                 "третьей стороны")
    klass = _strictest_class(run_dir)
    _claim_stage(run_dir, also=("data_state",),
                 state="сверка формализации идёт", mode2_class=klass)
    _control_path(run_dir).unlink(missing_ok=True)
    control = _make_checkpoint(run_dir, "сверка формализации",
                               "сверка формализации идёт")

    def _work():
        own: dict = {}
        try:
            svod = m_mod.loads_model(
                (run_dir / "extract" / "model.json")
                .read_text(encoding="utf-8"))
            entry = mode2_mod.load_third_party(
                run_dir / "third-party" / tp_name)
            llm = _channel_for(scope, run_dir, "mode2_checker", None,
                               klass=klass)
            report, questions = mode2_mod.run_mode2(
                svod, entry, llm, control=control)
            added = mode2_mod.apply_questions(svod, questions)
            bad = m_mod.validate_schema(svod) + m_mod.validate_model(svod)
            if bad:
                raise RuntimeError("модель с частью 4 не проходит "
                                   "контракт: " + "; ".join(bad[:3]))
            out = run_dir / "extract"
            m_mod.publish_model(svod, out / "model.json")
            (out / "svod.md").write_text(
                render_mod.render_svod(svod), encoding="utf-8")
            (out / "anketa.md").write_text(
                render_mod.render_anketa(svod), encoding="utf-8")
            anketa_xlsx_mod.render_anketa_xlsx(svod,
                                               out / "anketa.xlsx")
            tp_dir = run_dir / "third-party"
            (tp_dir / "third-party-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2)
                + "\n", encoding="utf-8")
            (tp_dir / "third-party-report.md").write_text(
                mode2_mod.render_report_md(report), encoding="utf-8")
            problems, counters = gate_mode2.check(
                out / "model.json",
                tp_dir / "third-party-report.json",
                tp_dir / "third-party-report.md")
            own.update(
                state="формализация сверена" if not problems
                else "красный гейт сверки формализации",
                error=None,
                mode2_summary={
                    "claims": report["claims_total"],
                    "checked": report["checked"],
                    "by_verdict": report["by_verdict"],
                    "part4_added": added,
                    "skipped": len(report["skipped"])},
                mode2_problems=problems,
                mode2_counters=counters)
        except StopRequested:
            own.update(state="остановлено: сверка формализации",
                       error=None)
        except Exception as e:                             # noqa: BLE001
            own.update(**_stage_failure("отказ сверки формализации", e))
        _update_status(run_dir, **own)

    _start_stage_thread(run_dir, _work)
    return {"run": run_id, "state": "сверка формализации идёт",
            "class": klass}


def _record_brief(run_dir: Path) -> dict:
    """Поля ЗАПИСИ о прогоне для строки списка — с диска, без
    пересборки записи (экран «Все прогоны обеих систем» читает список
    на каждый показ, а прогонов в каталоге сотни).

    `record_id`, `contour`, `created_by` — из ГОЛОВЫ прогона
    (`declaration.json`: объявление до старта); `golden` — признак
    золотого прогона из журнала приписок хранителя (там его носитель,
    см. `cdpm/run_tree_panel.py`); `cosmos_run` — номер прогона COSMOS
    из квитанции последней доставки пакета. Носителя нет — null:
    ничего не достраивается и не угадывается."""
    head = run_record_mod.read_declaration(run_dir)
    decl = head["declaration"] if head else {}
    contour = decl.get("contour")
    return {
        "record_id": decl.get("run_id"),
        "contour": contour.get("name") if isinstance(contour, dict) else None,
        "created_by": decl.get("created_by"),
        "golden": run_record_mod.appended(run_dir)["golden"],
        "cosmos_run": _delivery_mark(run_dir).get("cosmos_run"),
    }


@app.get("/api/runs")
def list_runs(brief: bool = False):
    """Список прогонов в действующем порядке (order.json, иначе по
    имени); у каждого — created_at (из статуса, иначе по каталогу) и
    position (0-based). Прогон с нечитаемым статусом список не ломает:
    показывается с пустым состоянием.

    Кроме состояния панели строка несёт поля записи о прогоне
    (`_record_brief`: номер записи, площадка, кто объявил, золотой
    прогон, номер прогона COSMOS) — их читает экран «Все прогоны обеих
    систем»: COSMOS зовёт эту ручку ТОЛЬКО на чтение.

    `?brief=1` (РВ-145, слово владельца 04.10): без готовности линии
    правил — `_readiness_of` грузит и пересчитывает модель КАЖДОГО прогона
    (Prod: 57 прогонов — 22 с, а ORBITA ждёт ответа 3 с).
    Экрану обеих систем готовность не нужна: поле `readiness` — null, прочие
    поля те же."""
    root = runs_root()
    out = []
    for position, run_id in enumerate(_effective_order(root)):
        p = root / run_id
        try:
            st = _read_status(p)
        except (ValueError, OSError):
            st = {}
        ready = None if brief else _readiness_of(p)
        created_at = st.get("created_at") or _dir_created_at(p)
        out.append({"run": p.name,
                    "state": _state_by_registry(p, st.get("state")),
                    "title": st.get("title"),
                    "customer": st.get("customer"),
                    "data_state": st.get("data_state"),
                    "readiness": {"state": ready["state"],
                                  "label": ready["label"]}
                    if ready else None,
                    "files": st.get("files", []),
                    "created_at": created_at,
                    "position": position,
                    "passport_summary": passport_mod.summary_of(st),
                    # Проект повтора (Р2): потомок → источник, источник → потомки.
                    "replay_of": _replay_of_brief(st),
                    "replays": st.get("replays") or [],
                    **_record_brief(p)})
    return out


@app.get("/api/runs/order")
def get_runs_order():
    """Действующий порядок списка: {"order": [номера]} — только
    существующие прогоны; не упомянутые в order.json — в конце по имени."""
    return {"order": _effective_order(runs_root())}


@app.put("/api/runs/order")
def put_runs_order(body: dict = Body(...)):
    """Ручной порядок списка (перестановка вверх/вниз): {"order":
    [номера]}. Каждый номер — по образцу run-NNNN и существующий (иначе
    400, ничего не записано); прогоны, не названные в теле, встают в
    конец по имени. Файл order.json пишется атомарно; ответ — действующий
    порядок."""
    order = body.get("order") if isinstance(body, dict) else None
    if not isinstance(order, list):
        raise HTTPException(400, "в теле запроса нужен список order")
    root = runs_root()
    existing = set(_existing_run_ids(root))
    cleaned: list[str] = []
    for rid in order:
        rid = str(rid)
        if not _RUN_ID_RE.fullmatch(rid):
            raise HTTPException(400, f"недопустимый номер прогона {rid!r}")
        if rid not in existing:
            raise HTTPException(400, f"прогон {rid} не существует — "
                                     f"порядок не записан")
        if rid not in cleaned:
            cleaned.append(rid)
    root.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(root / _ORDER_FILE, {"order": cleaned})
    return {"order": _effective_order(root)}


def _archive_root() -> Path:
    """Каталог архива — сосед корня прогонов: `<каталог
    данных>/cdpm-runs-archive` (при CDPM_RUNS_DIR — рядом с ним)."""
    return runs_root().parent / _ARCHIVE_DIR_NAME


@app.post("/api/runs/archive")
def archive_runs(body: dict = Body(...)):
    """Архив выбранных прогонов («удаление» из списка): {"runs":
    [номера]}. Сначала ВСЕ проверки, потом переносы (fail-closed):
    каждый номер — по образцу и существующий (404), ни у одного не идёт
    и не стоит на паузе стадия (409 с номерами занятых) — при любом
    отказе не переносится ничего. Затем каталог каждого прогона
    переносится целиком в архив (занятое имя получает хвост
    «-ГГГГммдд-ЧЧММСС»), номера снимаются из order.json, counter.last
    поднимается до наибольшего ушедшего номера — номер заново не
    выдаётся. Ответ: {archived, archive_dir, left}."""
    runs = body.get("runs") if isinstance(body, dict) else None
    if not isinstance(runs, list) or not runs:
        raise HTTPException(400, "в теле запроса нужен непустой список runs")
    root = runs_root()
    ids: list[str] = []
    for rid in runs:
        rid = str(rid)
        if not _RUN_ID_RE.fullmatch(rid):
            raise HTTPException(400, f"недопустимый номер прогона {rid!r}")
        if not (root / rid).is_dir():
            raise HTTPException(404, f"прогон {rid} не существует — "
                                     f"в архив ничего не перенесено")
        if rid not in ids:
            ids.append(rid)
    # Проверка занятости и переносы — под общим замком состояния: стадия
    # не стартует между «не занят» и переносом каталога (ревью 06.09).
    # Файловый замок прогона не берётся: его файл лежит внутри каталога
    # и мешал бы переименованию.
    with _STATUS_LOCK:
        return _archive_ids(root, ids)


def _archive_ids(root: Path, ids: list[str]) -> dict:
    """Тело архива (см. archive_runs): все проверки, затем переносы."""
    busy: list[str] = []
    unreadable: list[str] = []
    for rid in ids:
        try:
            st = _read_status(root / rid)
        except (ValueError, OSError):
            # Статус не прочитан — занятость неизвестна: fail-closed,
            # такой прогон в архив не уходит (ревью 06.09).
            unreadable.append(rid)
            continue
        words = _run_busy_words(st)
        if words:
            busy.append(f"{rid} ({words})")
    if unreadable:
        raise HTTPException(409, "у прогонов не прочитан статус — занятость "
                                 "неизвестна, ничего не перенесено: "
                                 + ", ".join(unreadable))
    if busy:
        raise HTTPException(409, "прогоны заняты стадией — остановите её "
                                 "перед архивом, ничего не перенесено: "
                                 + ", ".join(busy))
    archive = _archive_root()
    archive.mkdir(parents=True, exist_ok=True)
    archived: list[str] = []
    failure: str | None = None
    for rid in ids:
        dest = archive / rid
        if dest.exists():
            dest = archive / f"{rid}-{time.strftime('%Y%m%d-%H%M%S')}"
        try:
            # Только атомарное переименование в пределах тома (архив —
            # сосед корня): shutil.move при занятом файле уходил бы в
            # копирование с удалением и оставлял выпотрошенный каталог
            # (ревью 06.09). Занятый файл → OSError, каталог цел.
            os.rename(root / rid, dest)
        except OSError as e:
            failure = f"{rid}: перенос в архив не удался ({e})"
            break
        archived.append(rid)
    # Служебные файлы правятся по фактически ушедшим — и при срыве
    # переноса тоже, чтобы список и счётчик не разошлись с каталогом.
    if archived:
        stored = _read_order(root)
        if stored is not None:
            _write_json_atomic(root / _ORDER_FILE, {
                "order": [x for x in stored if x not in archived]})
        _raise_counter(root, max(int(x.split("-")[1]) for x in archived))
    if failure:
        raise HTTPException(500, failure + (
            "; уже в архиве: " + ", ".join(archived) if archived else ""))
    return {"archived": archived,
            "archive_dir": str(archive.resolve()),
            "left": len(_existing_run_ids(root))}


# ── Запись о прогоне: объявление, хранитель, ручки (шаг 2, 11.1–11.3) ──

@app.get("/api/declaration-preview")
def declaration_preview(request: Request, data_class: str = "",
                        llm_scope: str = "", llm_scope_basis: str = "",
                        purpose: str = "", created_by: str = "",
                        answer_policy: str = "", planned_stages: str = "",
                        stop_after: str = ""):
    """Объявимая часть среды и слова проблем гейта — ДО кнопки
    «Заморозить» (11.4): площадка, каталог с версией и отпечатком,
    привязки COSMOS, модели по ролям действующей раскладки, код,
    библиотеки, словари цели / политики ответов / этапов плана.
    Признак комплекта передаётся теми же именами, что форме создания:
    допуск решает, судится ли раскладка облачных ролей.

    Модели — ОБЕИХ систем (договор с COSMOS 11.09.2026): свои строки со
    словом `system: "cdpm"` и строки COSMOS (сертификатор, читатель
    ИДАК) со словом `system: "cosmos"` — их даёт ручка среды COSMOS,
    спрошенная предполётной сверкой. COSMOS не ответил — его строк нет,
    а причина названа отдельным ключом `cosmos`: недоступность COSMOS —
    не проблема гейта, прогон объявляется и без неё. Раннер наборов
    читает отсюда только блок `code` (свой `cdpm_commit`), и дублей
    моделей у него не возникает."""
    kit_problems = validate_kit_scope(data_class or None, llm_scope or None,
                                      llm_scope_basis)
    kit = {"data_class": data_class or None, "llm_scope": llm_scope or None,
           "llm_scope_basis": (llm_scope_basis or "").strip() or None}
    ks = kit_scope({"kit": kit})
    decl = _declaration_preflight(
        {"purpose": purpose, "created_by": created_by,
         "answer_policy": answer_policy, "planned_stages": planned_stages,
         "stop_after": stop_after}, ks)
    env = decl["environment"]
    cosmos_env = decl.get("cosmos_environment")
    cosmos_rows, models_note = run_record_mod.cosmos_models(cosmos_env)
    cosmos_commit = run_record_mod.cosmos_commit_of(cosmos_env)
    problems = [f"признак комплекта: {p}" for p in kit_problems] \
        + list(decl["problems"])
    declaration = {
        "contour": run_record_mod._contour(),
        "airline": {"data_class": ks.get("data_class"),
                    "llm_scope": ks.get("effective"),
                    "reading": ks.get("reading")},
        # Каталог — в форме словаря записи {name, version, fingerprint,
        # path}, как в голове: хозяин, объявляющий голову целиком,
        # берёт её отсюда, и сверка «объявлено / на деле» сходится.
        "catalog": {"name": "template-catalog",
                    "version": (env.get("catalog") or {}).get("version"),
                    "fingerprint": (env.get("catalog") or {}).get("fingerprint"),
                    "path": os.environ.get("CREW_TEMPLATE_CATALOG") or None},
        "bindings": run_record_mod._bindings(),
        "models": list(decl["models"]) + cosmos_rows,
        # Код — в форме словаря записи (cdpm_commit / cosmos_commit /
        # runner_fingerprint), как в голове: форма панели читает её.
        # Коммит COSMOS — от самого COSMOS, если он ответил.
        "code": {"cdpm_commit": (env.get("code") or {}).get("commit"),
                 "cosmos_commit": (cosmos_commit
                                   or (env.get("code") or {}).get("commit")),
                 "runner_fingerprint": None},
        "libraries": run_record_mod._libraries(env),
        "layout": decl["layout"],
    }
    if models_note:
        # Ключ — ТОЛЬКО со словами: пустого `models_note` в голове не
        # бывает (нормализатор словаря такое поле опускает), и
        # предпросмотр показывает ровно ту голову, что ляжет на диск.
        declaration["models_note"] = models_note
    # Форма ответа — договор с формой панели и COSMOS (раздел 11.4;
    # письмо 16:5x): {ok, words[], declaration{…}, defaults{…},
    # choices{…}} плюс `cosmos{asked, detail}` (11.09.2026).
    return {
        "ok": not problems,
        "words": problems,
        "declaration": declaration,
        # Спрошен ли COSMOS и что ответил — сведения формы, а не гейт.
        "cosmos": {"asked": bool(cosmos_env),
                   "detail": decl.get("cosmos_environment_detail") or ""},
        "defaults": {
            "purpose": decl["purpose"],
            "created_by": _who_declares(request, created_by),
            "answer_policy": decl["answer_policy"],
            "planned_stages": decl["planned_stages"],
            "stop_after": decl["stop_after"],
        },
        "choices": {
            "purposes": list(run_record_mod.PURPOSES),
            "answer_policies": list(run_record_mod.ANSWER_POLICIES),
            "stages": list(run_record_mod.PANEL_STAGES),
        },
    }


def _record_or_404(run_dir: Path) -> dict:
    rec = run_record_mod.stitch(run_dir)
    if rec is None:
        raise HTTPException(404, f"записи о прогоне {run_dir.name} нет: "
                                 "прогон объявлен до шага 2 проекта — "
                                 "запись появится после первого действия")
    return rec


@app.get("/api/runs/{run_id}/record")
def get_run_record(run_id: str, download: int = 0):
    """ЕДИНАЯ запись о прогоне (11.2): голова (своя объявленная либо
    чужая — переходное правило «два летописца»), журнал панели и чужие
    записи хранителя по времени, итоги. `?download=1` — файлом
    `run-record.json`."""
    run_dir = _run_dir(run_id)
    rec = _record_or_404(run_dir)
    if download:
        body = (json.dumps(rec, ensure_ascii=False, indent=1)
                + "\n").encode("utf-8")
        return Response(body, media_type="application/json",
                        headers={"Content-Disposition":
                                 'attachment; filename="run-record.json"'})
    return rec


@app.post("/api/runs/{run_id}/record/refresh")
def refresh_run_record_only(run_id: str):
    """Retry only the derived record, never extraction/append/other actions."""
    _refresh_run_record(_run_dir(run_id), report_errors=True)
    return {"ok": True, "run": run_id}


@app.post("/api/runs/{run_id}/record/append")
def append_run_record(run_id: str, body: dict = Body(...)):
    """Хранитель записи (11.2): чужая система дописывает записи этапов и
    итоги в запись прогона. Тело — `{"schema":
    "cosmos-run-record-append/v1", "record_id", "declaration_sha256",
    "system", "entries": [journal_entry…], "results": {...}}`
    (`results` необязателен).

    Отказы словами: 404 — прогона или записи нет; 409
    `declaration-mismatch` — номер записи либо отпечаток объявления не
    равен голове; 422 `entry-invalid` — хоть одна запись не по словарю
    (не принято НИЧЕГО); 400 — тело не разобрано. Идемпотентность — по
    ключу (система, этап, подэтап): повтор заменяет прежнюю запись."""
    run_dir = _run_dir(run_id)
    if not isinstance(body, dict):
        raise HTTPException(400, "тело не разобрано: ожидался объект")
    schema = str(body.get("schema") or "")
    if schema != run_record_mod.APPEND_SCHEMA:
        raise HTTPException(400, f"тело не разобрано: схема {schema!r} — "
                                 f"ожидалась {run_record_mod.APPEND_SCHEMA}")
    system = str(body.get("system") or "").strip()
    if not system:
        raise HTTPException(400, "тело не разобрано: система не названа "
                                 "(`system`)")
    entries = body.get("entries")
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise HTTPException(400, "тело не разобрано: `entries` — список "
                                 "записей журнала")
    results = body.get("results")
    if results is not None and not isinstance(results, dict):
        raise HTTPException(400, "тело не разобрано: `results` — объект")
    # Итоги могут ехать и без записей этапов (вердикты после нажатий,
    # выдачи): тело пустое, только если нет ни того, ни другого.
    if not entries and not results:
        raise HTTPException(400, "тело пустое: ни записей журнала (`entries`), "
                                 "ни итогов (`results`)")
    head = run_record_mod.record_head(run_dir)
    if head is None:
        raise HTTPException(404, f"записи о прогоне {run_id} нет: "
                                 "прогон не объявлен")
    got_id = str(body.get("record_id") or "")
    got_sha = str(body.get("declaration_sha256") or "")
    if got_id != head["run_id"] or got_sha != head["declaration_sha256"]:
        return JSONResponse(status_code=409, content={
            "ok": False, "code": "declaration-mismatch",
            "words": (f"запись прогона {run_id} объявлена под номером "
                      f"{head['run_id']} с отпечатком "
                      f"{head['declaration_sha256'][:12]}… (хозяин "
                      f"{head['owner']}); прислано {got_id!r} / "
                      f"{got_sha[:12] or '—'}… — голова после старта "
                      "неизменна, дописывать нечего")})
    problems = run_record_mod.entry_problems(entries) if entries else []
    if problems:
        return JSONResponse(status_code=422, content={
            "ok": False, "code": "entry-invalid", "words": problems[:20]})
    got = run_record_mod.append(run_dir, system, entries, results)
    _refresh_run_record(run_dir)
    # Приём чужих записей — событие дерева (12.1): чужой этап меняет и
    # состояние прогона, и его вердикты, а по ним судятся ветви.
    _rebuild_run_tree(f"{run_id}: приняты записи системы {system}")
    return {"ok": True, "accepted": got["accepted"],
            "replaced": got["replaced"], "record": _record_or_404(run_dir)}


# ── Дерево прогонов: индекс, карточка прогона, сравнение, золотой
# (шаг 3 проекта «Запись о прогоне», раздел 12; ядро — vmp/run_tree.py,
# половина панели — cdpm/run_tree_panel.py) ────────────────────────────

def _access_email(request=None) -> str:
    """Почта вошедшего из заголовка Cloudflare Access либо пусто.
    Умолчания «панель CDPM» здесь нет: золотой прогон подтверждает
    ЧЕЛОВЕК, и безымянная подпись — отказ, а не подпись службы."""
    if request is None:
        return ""
    try:
        return (request.headers.get(ACCESS_EMAIL_HEADER) or "").strip()
    except Exception:                                     # noqa: BLE001
        return ""


def _human_only(request) -> None:
    """Ручки золотого подтверждает ЧЕЛОВЕК: запрос с заголовком машинного
    канала (X-Channel-Token) — 403 (договор с COSMOS 23:1x; у панели
    токена канала нет, заголовок сам по себе — признак машины)."""
    if request is not None and request.headers.get("X-Channel-Token"):
        raise HTTPException(403, "золотой прогон подтверждает человек — "
                                 "машинному каналу ручка не отвечает")


def _golden_status(code: str) -> int:
    """Повод отказать в золотом признаке → номер отказа. Слова поводов
    — у модуля дерева (`run_tree_panel.golden_problems`); номера здесь:
    чего не сказал человек — 400, чего не позволяют прогон и площадка —
    409."""
    from cdpm import run_tree_panel
    # Договор с COSMOS 23:1x: чего не сказал человек — 422, чего не
    # позволяют площадка и прогон — 409.
    return 422 if code in run_tree_panel.GOLDEN_HUMAN_CODES else 409


@app.get("/api/run-tree")
def get_run_tree(rebuild: int = 0):
    """Индекс дерева прогонов панели целиком (`cosmos-run-tree/v1`:
    roots[], orphans[], refusals[], built_at). `?rebuild=1` —
    перестроить перед ответом; без него индекс читается ФАЙЛОМ, обхода
    диска на запрос не делается (12.1)."""
    from cdpm import run_tree_panel
    return run_tree_panel.load_or_build(runs_root(), rebuild=bool(rebuild))


@app.post("/api/run-tree/rebuild")
def rebuild_run_tree():
    """Перестроить индекс дерева прогонов (12.1). Ответ — сводка
    сборки: корней, прогонов, без корня, отказов обхода и момент."""
    from cdpm import run_tree_panel
    from vmp import run_tree as rt
    tree = run_tree_panel.build_index(runs_root())
    nodes = rt.nodes(tree)
    # Форма ответа — договор с COSMOS 23:1x: {ok, tree, roots, counts,
    # golden_pending, index_file}; golden_pending — завершённые прогоны
    # площадки prod без подписи золотого (ждут слова владельца).
    pending = [n.get("run_id") for n in nodes
               if str(n.get("contour") or "") == run_tree_panel.GOLDEN_CONTOUR
               and n.get("state") == "завершён"
               and not ((n.get("golden") or {}).get("confirmed")
                        if isinstance(n.get("golden"), dict) else n.get("golden"))]
    return {"ok": True, "tree": tree, "built_at": tree.get("built_at"),
            "roots": [{"root_sha256": r.get("root_sha256"),
                       "airline": r.get("airline"),
                       "runs": len(r.get("runs") or [])}
                      for r in (tree.get("roots") or []) if isinstance(r, dict)],
            "counts": {"roots": len(tree.get("roots") or []),
                       "runs": len(nodes),
                       "orphans": len(tree.get("orphans") or []),
                       "refusals": len(tree.get("refusals") or [])},
            "golden_pending": pending,
            "index_file": str(run_tree_panel.index_path(runs_root()))}


@app.get("/api/run-tree/compare")
def compare_run_tree(a: str = "", b: str = ""):
    """Сравнение двух прогонов дерева (12.2): что менялось в объявлении
    и что изменилось в итогах. Узла нет — 404 словами."""
    from cdpm import run_tree_panel
    from vmp import run_tree as rt
    if not a.strip() or not b.strip():
        raise HTTPException(400, "нужны номера двух прогонов: ?a=<номер>"
                                 "&b=<номер>")
    try:
        return run_tree_panel.compare(runs_root(), a.strip(), b.strip())
    except rt.RunTreeError as e:
        raise HTTPException(404, str(e))


@app.get("/api/runs/{run_id}/tree")
def get_run_tree_node(run_id: str):
    """Дерево вокруг прогона панели (12.2): сам узел, путь от корня,
    соседние ветви (с `edge.differs` — чем отличается), золотой прогон
    корня, сравнение с золотым и с предыдущим, сравнение с ожиданиями
    набора из записи."""
    from cdpm import run_tree_panel
    from vmp import run_tree as rt
    run_dir = _run_dir(run_id)
    record = run_record_mod.stitch(run_dir)
    try:
        return run_tree_panel.tree_of(runs_root(), run_id, record)
    except rt.RunTreeError as e:
        raise HTTPException(404, str(e))


@app.post("/api/runs/{run_id}/golden")
def set_run_golden(run_id: str, body: dict = Body(default=None),
                   request: Request = None):
    """Назвать прогон ЗОЛОТЫМ — прогоном-образцом, с которым по
    умолчанию сравниваются новые ветви (12.2, решение владельца 6.3 от
    10.09.2026: признак ставится ТОЛЬКО подтверждением человека после
    приёмки на Prod).

    Тело `{by, words}`: кто подтвердил и какими словами. Пустое «кто» —
    почта Cloudflare Access вошедшего; машинного пути у ручки нет —
    подтверждает человек. Отказы словами: 400 — не сказано, кто
    подтвердил либо какими словами; 409 — прогон не завершён либо
    площадка не `prod`. Ответ — запись о прогоне целиком."""
    from cdpm import run_tree_panel
    from vmp import run_tree as rt
    run_dir = _run_dir(run_id)
    body = body if isinstance(body, dict) else {}
    _human_only(request)
    by = str(body.get("by") or "").strip() or _access_email(request)
    words = str(body.get("words") or "").strip()
    record = _record_or_404(run_dir)
    problems = run_tree_panel.golden_problems(
        record, by=by, words=words,
        contour=run_record_mod._contour()["name"], marking=True)
    if problems:
        raise HTTPException(_golden_status(problems[0]["code"]),
                            "; ".join(p["words"] for p in problems))
    try:
        run_tree_panel.mark_golden(run_dir, by=by, words=words)
    except (rt.RunTreeError, ValueError) as e:
        raise HTTPException(409, str(e))
    # Действие журнала — только СОСТОЯВШЕЕСЯ (потому и записывается
    # здесь, а не декоратором `_journaled`): строка `refused` сделала бы
    # завершённый прогон «остановленным по правилу»
    # (`run_record._record_state` судит по последнему исходу журнала), а
    # нажатие, ничего не изменившее, права менять состояние прогона не
    # имеет — отказ человек получает словами.
    before = _state_brief(_status_if_any(run_dir))
    n = actions_journal.note(run_dir, "golden", params={"by": by,
                                                        "words": words},
                             sync=True, state_before=before)
    actions_journal.end(run_dir, n, state_after=before, duration_s=0.0)
    actions_journal.set_current(None)
    _refresh_run_record(run_dir)
    _rebuild_run_tree(f"{run_id}: прогон назван золотым")
    return _record_or_404(run_dir)


@app.post("/api/runs/{run_id}/golden/unmark")
def unset_run_golden(run_id: str, body: dict = Body(default=None),
                     request: Request = None):
    """Снять признак золотого прогона (12.2): подтверждение отзывает
    ТОТ ЖЕ человек, что подтверждал, — иначе 409 словами. Слова
    подтверждения снимаются вместе с признаком, а строка журнала
    хранителя остаётся: кто и когда подтверждал, видно всегда.

    Площадка судится та же, что у подтверждения (`prod`): признак
    живёт на приёмочной площадке, там же и отзывается. Прогон не
    золотой — 409 «снимать нечего»."""
    from cdpm import run_tree_panel
    from vmp import run_tree as rt
    run_dir = _run_dir(run_id)
    body = body if isinstance(body, dict) else {}
    _human_only(request)
    by = str(body.get("by") or "").strip() or _access_email(request)
    record = _record_or_404(run_dir)
    problems = run_tree_panel.golden_problems(
        record, by=by, words="",
        contour=run_record_mod._contour()["name"], marking=False)
    if problems:
        raise HTTPException(_golden_status(problems[0]["code"]),
                            "; ".join(p["words"] for p in problems))
    try:
        run_tree_panel.unmark_golden(run_dir)
    except (rt.RunTreeError, ValueError) as e:
        raise HTTPException(409, str(e))
    # Журнал действий — как у подтверждения: только состоявшееся
    # снятие, отказ человек получает словами.
    before = _state_brief(_status_if_any(run_dir))
    n = actions_journal.note(run_dir, "golden-unmark", params={"by": by},
                             sync=True, state_before=before)
    actions_journal.end(run_dir, n, state_after=before, duration_s=0.0)
    actions_journal.set_current(None)
    _refresh_run_record(run_dir)
    _rebuild_run_tree(f"{run_id}: признак золотого снят")
    return _record_or_404(run_dir)


@app.put("/api/runs/{run_id}/title")
def set_run_title(run_id: str, body: dict):
    """Человеческое название прогона (УИ-13, решение владельца
    2026-08-20): хранится в status.json, пустая строка убирает
    название — снова показывается номер прогона."""
    run_dir = _run_dir(run_id)
    title = str(body.get("title") or "").strip()
    if len(title) > 120:
        raise HTTPException(400, "название длиннее 120 символов")
    _update_status(run_dir, title=title or None)
    return {"run": run_id, "title": title or None}


def _readiness_of(run_dir: Path) -> dict | None:
    """Состояние готовности линии правил — свежим счётом по текущей
    модели (СГ-1); прогона без извлечения не касается."""
    path = run_dir / "extract" / "model.json"
    if not path.is_file():
        return None
    try:
        doc = m_mod.load_model(path)
        got = m_mod.readiness(doc)
        # ЦФ-1: матрица корректности 3×2 (режимы × задачи) — рядом
        # с прежним состоянием а/б/в.
        got["matrix"] = m_mod.readiness_matrix(doc)
        # Термин владельца (демо сквозного потока, 2026-08-29):
        # ПВМП = предварительный ВМП — модель ДО закрытия вопросов
        # анкеты; ВМП — когда открытых вопросов нет. Машинный
        # признак, не украшение: судится теми же is_open_question.
        questions = m_mod.current_questions(doc)
        open_q = sum(1 for q in questions if m_mod.is_open_question(q, doc=doc))
        got["vmp_state"] = "ПВМП" if open_q else "ВМП"
        got["vmp_open_questions"] = open_q
        # Метрика «Вопросов» — ВСЕ вопросы действующей анкеты, в том
        # числе рождённые линией данных (вопрос истории), а не только
        # извлечённые из документов (замечание владельца на репетиции
        # 05.09: панель показывала «Вопросов 2», в анкете было три).
        got["vmp_questions_total"] = len(questions)
        return got
    except (ValueError, OSError):
        return None


@app.get("/api/runs/{run_id}")
def run_status(run_id: str):
    run_dir = _run_dir(run_id)
    readiness = _readiness_of(run_dir)
    st = _read_status(run_dir)
    from cdpm import replay as replay_mod
    out = {"run": run_id, **st, "readiness": readiness,
           # A07, миграция показа: слова состояния старого цикла
           # пересуживаются реестром (status.json не переписывается).
           "state": _state_by_registry(run_dir, st.get("state")),
           "llm_scope": kit_scope(st),
           # Паспорт прогона — рецепт воспроизведения (07.09.2026).
           "passport": passport_mod.passport_of(run_dir, st),
           # Повтор прогона (проект повтора, Р2): доступность кнопок,
           # паспорт повтора у потомка, ход водителя.
           "replay": {**replay_mod.availability(run_dir, st),
                      "of": replay_mod.replay_of(st),
                      "progress": st.get("replay_progress")}}
    # Четыре принципа ВМП (владелец 2026-09-02): покрытие клауз
    # (замок биекции) и блок 4П по машинным фактам модели — для
    # карточки прогона; без модели — null.
    model_path = run_dir / "extract" / "model.json"
    if model_path.is_file():
        from cdpm import coverage as cov
        from cdpm import principles as pr
        try:
            doc = m_mod.load_model(model_path)
            # A11 (аудит 11.09.2026): индикатор «Биекция» судится теми
            # же фактами, что замок выпуска, — сводке даются комплект
            # заморозки (висячие якоря) и измеритель на текущем своде
            # (содержание), а не только адреса клауз. И тем же СВОДОМ:
            # копией после машинного лечения сборкой (package_doc) —
            # иначе панель и выпуск расходятся на строках, которые
            # лечение снимает (ревью A11, п. 3). Канонический
            # model.json прогона не переписывается.
            doc_pkg, heal_note = cov.package_doc(doc)
            kit_entries = cov.run_kit(run_dir)
            measured = (cov.run_measure(run_dir, kit_entries, doc_pkg)
                        if kit_entries is not None else None)
            coverage = cov.run_report(run_dir, kit_entries=kit_entries,
                                      doc=doc_pkg, measured=measured)
            out["coverage"] = coverage
            out["principles"] = pr.summary(
                doc, coverage, readiness, kit_entries=kit_entries,
                measure_report=measured[0] if measured else None,
                facts_doc=doc_pkg,
                facts_notes=[heal_note] if heal_note else None)
        except Exception as e:                             # noqa: BLE001
            out["principles_error"] = str(e)[:200]
    # Напоминание о пробелах каталога шаблонов (шаг 6): счётчики для
    # блока «Итог»; составления и подачи чтение карточки не делает.
    try:
        rows = _catalog_rows(run_dir, _catalog_model_doc(run_dir))
        out["catalog_requests"] = {
            **catreq_mod.counts_of(rows, catreq_mod.read_index(run_dir)),
            "document_class": _catalog_document_class(run_dir)}
    except Exception as e:                                 # noqa: BLE001
        out["catalog_requests"] = {"error": str(e)[:200]}
    return out


def _obligations_location(run_id: str):
    from cdpm import obligations as ob
    run_dir = _run_dir(run_id)
    if not run_dir.resolve().is_relative_to(runs_root().resolve()):
        raise HTTPException(400, "путь прогона выходит за каталог прогонов")
    try:
        return run_dir, ob._local_file(run_dir.resolve(), ob.LEDGER_REL)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _obligations_write_path(run_dir: Path):
    """Write only the selected ledger, never a resolved alias to another file.

    Read-side containment permits local source links. Publication must not
    reuse their resolved targets (model/freeze/source or another directory).
    Checks are repeated under the writer lock and just before replacement;
    this is not a transaction against hostile concurrent filesystem changes.
    """
    from cdpm import obligations as ob
    root = run_dir.resolve()
    path = root / ob.LEDGER_REL
    for part in (run_dir, path.parent, path):
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise HTTPException(400, "запись ведомости через ссылку или перенаправленный каталог запрещена")
    if path.resolve() != path:
        raise HTTPException(400, "путь записи ведомости перенаправлен")
    return path


def _obligations_candidate(run_dir: Path, text: str):
    """Judge an explicit import against real files, without publishing it."""
    from cdpm import obligations as ob
    try:
        if not isinstance(text, str):
            raise ValueError("text: ожидался исходный JSON строкой")
        ledger = m_mod.loads_strict(text)
        if not isinstance(ledger, dict):
            raise ValueError("ведомость: ожидался объект")
        units = ob._list(ob._object(ledger.get("snapshot"), "snapshot").get("units"),
                         "snapshot.units")
        answers = [ob._answer_identity(ob._object(unit, "snapshot.unit").get("source"))
                   for unit in units if isinstance(unit, dict) and unit.get("kind") == "answer"]
        inputs = ob._run_inputs(run_dir, answer_sources=answers)
        report = ob.evaluate(ledger, **inputs)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise HTTPException(422, f"ведомость не сохранена: {exc}") from exc
    if report["form"] != "valid":
        raise HTTPException(422, {"message": "ошибка формы ведомости", "report": report})
    if report["freshness"] != "current":
        raise HTTPException(409, {"message": "снимок ведомости устарел или не разрешается",
                                  "report": report})
    return ledger, report


@app.get("/api/runs/{run_id}/obligations")
def get_obligations(run_id: str, download: int = 0):
    """Opt-in A12; exact stored JSON is editable even when its report is stale."""
    import hashlib
    from cdpm import obligations as ob
    run_dir, path = _obligations_location(run_id)
    try:
        raw = _file_replace.read_bytes_shared(path) if path.exists() else None
        if download:
            if raw is None:
                raise HTTPException(404, "ведомость ещё не сохранена")
            return Response(raw, media_type="application/json", headers={
                "Content-Disposition": 'attachment; filename="obligations.json"',
                "Cache-Control": "no-store"})
        report = ob.run_report(run_dir)
        # Never splice a report for one revision onto another editable body.
        if raw != (_file_replace.read_bytes_shared(path) if path.exists() else None):
            raise HTTPException(409, "ведомость изменилась при чтении; обновите страницу")
        template, preparation_error = None, None
        if raw is None:
            try:
                snapshot = ob.capture_run_snapshot(run_dir)
                template = {"schema_version": ob.SCHEMA, "snapshot": snapshot,
                            "scope": {"included": [u["id"] for u in snapshot["units"]], "excluded": []},
                            "readers": [], "comparisons": [], "links": [], "expert_review": None}
            except ValueError as exc:
                preparation_error = str(exc)
        return {"run": run_id, "report": report,
                "sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
                "text": raw.decode("utf-8") if raw is not None else None,
                "template": template, "preparation_error": preparation_error}
    except (OSError, UnicodeError) as exc:
        raise HTTPException(409, f"ведомость не читается: {exc}") from exc


@app.post("/api/runs/{run_id}/obligations/prepare")
def prepare_obligations(run_id: str, body: dict = Body(...)):
    """Explicit authoring aid: seal only supplied lists, not their independence.

    Exact snapshot/answer sources may be supplied by the author. Nothing is
    discovered, extracted, refreshed, saved, approved or inferred here.
    """
    from cdpm import obligations as ob
    run_dir, _ = _obligations_location(run_id)
    try:
        if not isinstance(body, dict) or set(body) != {"text"} or not isinstance(body["text"], str):
            raise ValueError("ожидалось только поле text с исходным JSON")
        ledger = ob._object(m_mod.loads_strict(body["text"]), "ledger")
        # The exact input snapshot digest is public; authors must themselves
        # state which snapshot they read. Do not rebind old results here.
        ledger["readers"] = [ob.seal_reader(reader)
                             for reader in ob._list(ledger.get("readers"), "readers")]
        text = json.dumps(ledger, ensure_ascii=False, indent=2, allow_nan=False)
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    _, report = _obligations_candidate(run_dir, text)
    return {"text": text, "report": report}


@app.put("/api/runs/{run_id}/obligations")
def put_obligations(run_id: str, body: dict = Body(...)):
    """Strict JSON import, compare-and-replace, no model/gate/approval changes."""
    import hashlib
    import tempfile
    run_dir, _ = _obligations_location(run_id)
    path = _obligations_write_path(run_dir)
    if not isinstance(body, dict) or set(body) != {"text", "expected_sha256"}:
        raise HTTPException(422, "ожидались text и expected_sha256 (null только для нового файла)")
    expected = body["expected_sha256"]
    if expected is not None and (not isinstance(expected, str) or
                                 re.fullmatch(r"[0-9a-f]{64}", expected) is None):
        raise HTTPException(422, "expected_sha256: нужен SHA-256 предыдущего файла или null")
    _obligations_candidate(run_dir, body["text"])
    staging = None
    try:
        with _status_guard(run_dir):
            if _obligations_write_path(run_dir) != path:
                raise HTTPException(400, "путь записи ведомости изменился")
            old = _file_replace.read_bytes_shared(path) if path.exists() else None
            actual = hashlib.sha256(old).hexdigest() if old is not None else None
            if expected != actual:
                raise HTTPException(409, "ведомость изменилась; прочитайте актуальную версию")
            # Rejudge after acquiring the writer lock. Later source changes
            # are still detected by every read; this is not a run transaction.
            _obligations_candidate(run_dir, body["text"])
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix=".obligations-", suffix=".json",
                                              dir=path.parent, delete=False) as temporary:
                staging = Path(temporary.name)
            staging.write_bytes(body["text"].encode("utf-8"))
            if _obligations_write_path(run_dir) != path:
                raise HTTPException(400, "путь записи ведомости изменился")
            _file_replace.replace_with_retry(staging, path)
    except OSError as exc:
        raise HTTPException(503, f"ведомость не опубликована: {exc}") from exc
    finally:
        if staging is not None:
            try:
                staging.unlink(missing_ok=True)
            except OSError:
                pass  # Never hide the primary write/replace failure.
    return get_obligations(run_id)


@app.post("/api/runs/{run_id}/obligations/read")
def read_obligations(run_id: str, body: dict = Body(...)):
    """Explicit two-reader proposal; neither a publication nor an approval."""
    from cdpm import obligations as ob
    run_dir, _ = _obligations_location(run_id)
    # Validate containment/aliases before the action journal writes anything.
    _obligations_write_path(run_dir)
    if (not isinstance(body, dict)
            or not {"approval", "expected_sha256"} <= set(body) <= {"approval", "expected_sha256", "answer_sources"}
            or body["approval"] is not True):
        raise HTTPException(422, "требуются явное approval=true и expected_sha256 ведомости")
    expected = body["expected_sha256"]
    if expected is not None and (not isinstance(expected, str) or
                                 re.fullmatch(r"[0-9a-f]{64}", expected) is None):
        raise HTTPException(422, "expected_sha256: нужен SHA-256 ведомости или null")
    try:
        selected = ob._list(body.get("answer_sources", []), "answer_sources")
        answers = [ob._answer_identity(source) for source in selected]
        if answers != selected:
            raise ValueError("answer_sources: требуется точная форма kind/question/version без дополнительных полей")
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return _read_obligations_action(run_id, expected, answers)


@_journaled("obligations-read", "expected_sha256", "answer_sources", sync=True)
def _read_obligations_action(run_id: str, expected_sha256: str | None, answer_sources: list):
    import hashlib
    import uuid
    from cdpm import obligations as ob, obligation_readers

    run_dir, path = _obligations_location(run_id)
    token = uuid.uuid4().hex
    running = "чтение обязательств идёт"
    other_lines = ("data_state", "smt_state", "catalog_state")

    def ledger_bytes():
        return _file_replace.read_bytes_shared(path) if path.exists() else None

    def context():
        # Settings are only compared in memory, never returned or journaled:
        # a provider/key/layout/policy change invalidates this operation.
        return {"files": _read_files_config(run_dir),
                "kit": kit_scope(_read_status(run_dir)),
                "agents": providers_mod.load_agents(run_dir),
                "settings": providers_mod.load_settings()}

    try:
        with _status_guard(run_dir):
            previous = _read_status(run_dir)
            for key in ("state", *other_lines):
                if _line_busy(previous.get(key)):
                    raise HTTPException(409, f"прогон занят: {previous.get(key)}")
            if _read_command(run_dir) in ("stop", "pause"):
                raise HTTPException(409, "снимите stop/pause перед явным новым чтением")
            old = ledger_bytes()
            actual = hashlib.sha256(old).hexdigest() if old is not None else None
            if actual != expected_sha256:
                raise HTTPException(409, "ведомость изменилась; обновите страницу перед чтением")
            inputs = ob._run_inputs(run_dir, answer_sources=answer_sources)
            # Resolves precisely the selected edition/version, including
            # answer validity, before building any channel. Never pick latest.
            snapshot = ob.capture_snapshot(**inputs)
            answer_units = {u["id"] for u in snapshot["units"] if u["kind"] == "answer"}
            captured = context()
            scope = _run_scope(run_dir, None)
            manifest = m_mod.loads_strict((run_dir / "freeze" / "manifest.json").read_bytes())
            classes = {}
            for entry in manifest["files"]:
                # A frozen file without a current policy is not silently cloud.
                policy = captured["files"].get(entry["file"], {}).get("processing", "local")
                if policy not in FILE_POLICIES:
                    raise HTTPException(422, f"неверная политика файла {entry['file']!r}")
                classes[entry["sid"]] = policy
            answer_class = None
            if answer_sources:
                if (not manifest["files"] or any(entry["file"] not in captured["files"]
                                                for entry in manifest["files"])):
                    raise HTTPException(409, "для выбранных ответов нельзя определить политику всех замороженных файлов")
                # Answers may combine facts from several source files. Reuse
                # the existing cross-file policy, never a cloud default.
                answer_class = _strictest_class(run_dir)
            _claim_stage(run_dir, also=other_lines, state=running,
                         obligations_reading_token=token)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise HTTPException(409, f"входы чтения обязательств не разрешаются: {exc}") from exc

    outcome = "отказ"

    def checkpoint():
        control()
        try:
            with _status_guard(run_dir):
                if _obligations_write_path(run_dir) != path:
                    raise HTTPException(409, "путь ведомости изменился во время чтения")
                st = _read_status(run_dir)
                if st.get("obligations_reading_token") != token:
                    raise HTTPException(409, "владение стадией чтения изменилось")
                if st.get("state") != running:
                    raise HTTPException(409, "состояние стадии чтения изменилось")
                if any(_line_busy(st.get(key)) for key in other_lines):
                    raise HTTPException(409, "другая линия прогона занята во время чтения")
                if (ob._run_inputs(run_dir, answer_sources=answer_sources) != inputs or context() != captured
                        or ledger_bytes() != old):
                    raise HTTPException(409, "исходники, модель, ведомость или политика изменились во время чтения")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(409, f"входы чтения изменились или не читаются: {exc}") from exc

    channels = {}

    def resolve_channel(role, unit):
        if unit.get("kind") == "document" and unit.get("sid") in classes:
            klass = classes[unit["sid"]]
        elif unit.get("kind") == "answer" and unit.get("id") in answer_units and answer_class is not None:
            klass = answer_class
        else:
            raise HTTPException(422, "чтение A12: источник вне замороженных клауз и явно выбранных точных ответов")
        key = (role, klass)
        if key not in channels:
            # Preserve the existing scope refusal, without returning a raw
            # transport exception as HTTP 500 to this synchronous action.
            from webapp.llm_backend import ScopeViolation
            try:
                fn = _channel_for(scope, run_dir, role, klass=key[1], key_role=role)
            except ScopeViolation as exc:
                raise HTTPException(409, f"допуск комплекта запрещает канал чтения: {exc}") from exc
            raw = llm_mod.channel_identity(fn)
            identity = m_mod.loads_strict(raw) if raw else {}
            actual = identity.get("effective") or identity
            local = (actual.get("provider") == "local"
                     and actual.get("channel", identity.get("transport")) == "local")
            if not local and captured["kit"].get("data_class") not in ("synthetic", "open"):
                # Existing channels do not provide lossless clause masking.
                # A free-text kit basis is not a structured unmasked-text grant.
                raise HTTPException(409, "A12 masking unsupported: для proprietary/unknown исходных клауз "
                                    "разрешён только фактический локальный канал; немаскированная внешняя "
                                    "передача новым действием не поддерживается")
            channels[key] = fn
        return channels[key]

    try:
        control = _make_checkpoint(run_dir, "чтение обязательств", running)
        result = obligation_readers.read_independently(
            inputs=inputs, resolve_channel=resolve_channel, checkpoint=checkpoint,
            cancellation_errors=(StopRequested,))
        checkpoint()
        text = result.ledger_bytes.decode("utf-8")
        _, report = _obligations_candidate(run_dir, text)
        checkpoint()
        outcome = "кандидат подготовлен, не сохранён"
        return {"run": run_id, "saved": False, "expected_sha256": expected_sha256,
                "text": text, "report": report, "evidence": result.evidence}
    except StopRequested as exc:
        outcome = "остановлено"
        raise HTTPException(409, "чтение обязательств остановлено; кандидат не сохранён") from exc
    except obligation_readers.ObligationReadError as exc:
        # The existing page renders JSON as text. Keep failed attempts visible
        # without publishing a partial ledger or overwriting saved evidence.
        detail = {"message": f"чтение обязательств не завершено: {exc}", "evidence": exc.evidence}
        raise HTTPException(422, detail) from exc
    except (OSError, ValueError, TypeError, KeyError, llm_mod.LLMCallError) as exc:
        raise HTTPException(422, f"чтение обязательств не завершено: {exc}") from exc
    finally:
        def release(st):
            if st.get("obligations_reading_token") == token:
                st.pop("obligations_reading_token", None)
                if st.get("state") in (running, "на паузе: чтение обязательств"):
                    if "state" in previous:
                        st["state"] = previous["state"]
                    else:
                        st.pop("state", None)
                st["obligations_reading"] = {"state": outcome, "saved": False}
        primary = sys.exc_info()[0]
        try:
            _mutate_status(run_dir, release)
        except OSError as exc:
            if primary is None:
                raise HTTPException(503, f"не удалось освободить стадию чтения: {exc}") from exc
            print(f"CDPM: чтение обязательств: не удалось освободить стадию: {exc}", flush=True)


@app.get("/runs/{run_id}/obligations", response_class=HTMLResponse)
def obligations_page(run_id: str):
    """Small server-rendered authoring surface, separate from release gates."""
    import html
    from cdpm import obligations as ob
    view = get_obligations(run_id)
    report = view["report"]
    text = view["text"]
    if text is None:
        text = json.dumps(view["template"], ensure_ascii=False, indent=2) if view["template"] else ""
    def shown(value):
        return html.escape(json.dumps(value, ensure_ascii=False, indent=2))
    # Render claims and evidence separately, including every condition,
    # exception and review field. Imported text never becomes active HTML.
    try:
        ledger = m_mod.loads_strict(text) if text else {}
        if not isinstance(ledger, dict):
            ledger = {}
    except ValueError:
        ledger = {}
    blocks = "".join(f"<h2>{title}</h2><pre>{shown(value)}</pre>" for title, value in [
        ("Пробелы и состояние доказательств", report),
        ("Читательские списки: обязательства, условия и исключения", ledger.get("readers", [])),
        ("Связи с полями ВМП", ledger.get("links", [])),
        ("Сопоставления независимых списков", ledger.get("comparisons", [])),
        ("Предметное рассмотрение (запись автора, не проверенная подпись)", ledger.get("expert_review"))])
    snapshot_sha = ob.fingerprint(ledger["snapshot"]) if isinstance(ledger.get("snapshot"), dict) else ""
    reviewed_content_sha = ob.review_content_sha256(ledger)
    return HTMLResponse("""<!doctype html><html lang="ru"><meta charset="utf-8">
<title>A12 — атомарные обязательства</title><style>
body{font:16px sans-serif;max-width:80em;margin:2em auto;padding:0 1em}
pre,textarea{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f4f4;padding:1em}
textarea{width:95%;height:25em}button,a{margin:.4em} .notice{border:1px solid #b78;padding:1em}
</style><h1>A12 — атомарные обязательства: """ + html.escape(run_id) + """</h1>
<p><a href="/legacy">Прогоны</a> <a href="?">Обновить отчёт</a>
<a href="/api/runs/""" + run_id + """/obligations?download=1">Экспорт сохранённого JSON</a></p>
<p class="notice">Локальная необязательная ведомость. Связь с полем — заявленное соответствие,
не доказательство смысловой полноты. Совпадение списков, пустой список и открытый вопрос
не доказывают полноту. Независимость читателей и полномочия эксперта автоматически не подтверждаются.
Ни импорт, ни отпечаток не являются приёмкой. Существующие гейты не меняются.</p>
<p>Для новой ведомости шаблон содержит только снимок документов; readers пуст: это ещё не
валидная ведомость. Добавьте два действительно выполненных чтения, их обязательства и связи.
Источники ответов задаются точными edition/version identities, а не последним ответом.</p>
<h2>Два новых чтения исходных клауз</h2>
<p class="notice">Явное действие вызывает настроенные роли extractor и second_reader по политике
каждого файла и допуску комплекта; фактические семейства должны различаться. Читаются текущие
замороженные клаузы документов и только явно выбранные точные версии ответов; текст ВМП читателям
не передаётся. Для ответов действует строжайшая политика файлов прогона. Это предложения, не доказательство
смысловой полноты и не экспертная приёмка. Для proprietary/unknown внешняя передача этим действием
не поддерживается: lossless-маскирования клауз нет; используйте локальные каналы. Для synthetic/open
действуют обычные допуски и раскладка. Запросы, ответы и личности каналов записываются в журнал.
Существующая ведомость и экспертный акт не заменяются; кандидат ниже требуется отдельно перенести
в редактор и явно сохранить. До сохранения он существует только в ответе и на этой странице.</p>
<label for="answer-sources">Точные источники ответов (JSON-список; [] — без ответов):</label>
<textarea id="answer-sources" aria-label="Выбранные точные источники ответов">[]</textarea>
<p>Каждый элемент имеет вид <code>{"kind":"answer","question":{"id":"Q-123","revision":1,
"fingerprint":"точный отпечаток издания"},"version":{"fingerprint":"точный отпечаток версии ответа"}}</code>.
Это пример формы, не выбранный ответ. Используйте существующие identities: последнее издание или ответ
автоматически не выбираются. Неизвестный, изменённый или недействующий источник будет отклонён.</p>
<label><input id="reading-approval" type="checkbox">Разрешаю два чтения текущих исходных клауз и выбранных ответов
настроенными каналами с указанными ограничениями, включая не более одного адресного уточнения
на неоднозначную единицу каждым читателем</label>
<p>Точные уникальные цитаты получают проверяемые байтовые адреса. При неоднозначности допускается
только одно уточнение тем же читателем; исходный ответ и уточнение сохраняются в уликах.
Это не дополнительное независимое чтение. Изменение содержания или повторная неоднозначность — отказ без кандидата.</p>
<button id="read-obligations">Получить два чтения и сопоставление (без сохранения)</button>
<textarea id="reading-candidate" readonly aria-label="Новый кандидат ведомости"></textarea>
<button id="use-candidate" disabled>Перенести кандидат в редактор (ещё не сохранить)</button>
<p>input_sha256 текущего показанного снимка: <code>""" + snapshot_sha + """</code>.
Не переносите старое чтение на новый снимок простой заменой отпечатка.</p>
<p>reviewed_content_sha256 сохранённого показанного содержания: <code>""" + reviewed_content_sha + """</code>.
Он охватывает всю ведомость, кроме expert_review. После правки списков, связей или сопоставлений
старое рассмотрение устаревает. Его отпечаток не обновляется автоматически и сам по себе не является приёмкой.</p>
<details><summary>Формат ручного ввода</summary><p>У читателя: id, actor, method, version,
input_sha256, exposure {other_reader, model} (boolean/null), reviewed_units, obligations.
У обязательства: id, subject, statement, modality (requirement/prohibition/permission/unknown),
conditions и exceptions [{id,text}], sources [{unit,fragments:[{start,end,quote}]}], depends_on.
Границы фрагмента — байты UTF-8 [start,end), не номера символов.
Связи: reader, obligation, model_sha256, status (declared/disputed/open_question), author, reason,
targets [{collection,row_id,pointer,component}]; component = statement / conditions/ID / exceptions/ID.
Рассмотрение необязательно: expert_review=null. Явная запись: reviewer, decision
(accepted/rejected/open), reason, snapshot_sha256, scope_sha256, reviewed_content_sha256, issues.
Сопоставления: id, left/right {reader,obligations:[ID]}, status (agreed/disputed), author, reason.</p></details>
<h2>Строгий JSON импорт</h2><input id="source" type="file" accept=".json,application/json">
<textarea id="ledger" aria-label="JSON ведомости">""" + html.escape(text) + """</textarea>
<p><button id="prepare">Пересчитать отпечатки введённых списков и проверить (без сохранения)</button>
<button id="save">Проверить и сохранить эту версию</button></p><pre id="result" role="status">""" + html.escape(view["preparation_error"] or "") + """</pre>
""" + blocks + """<script>
const api='/api/runs/""" + run_id + """/obligations';
let expected=""" + json.dumps(view["sha256"]) + """;
const area=document.getElementById('ledger'), result=document.getElementById('result');
document.getElementById('source').onchange=async e=>{if(e.target.files[0]) area.value=await e.target.files[0].text()};
async function send(prepare){
  try{
    const body=prepare?{text:area.value}:{text:area.value,expected_sha256:expected};
    const response=await fetch(api+(prepare?'/prepare':''),{method:prepare?'POST':'PUT',
      headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const got=await response.json(); result.textContent=JSON.stringify(got.report||got,null,2);
    if(response.ok){area.value=got.text;if(!prepare){expected=got.sha256;location.reload()}}
  }catch(error){result.textContent=String(error)}
}
document.getElementById('prepare').onclick=()=>send(true);
document.getElementById('save').onclick=()=>send(false);
document.getElementById('read-obligations').onclick=async()=>{
  const button=document.getElementById('read-obligations');
  if(!document.getElementById('reading-approval').checked){result.textContent='Требуется явное разрешение';return}
  button.disabled=true; document.getElementById('use-candidate').disabled=true;
  document.getElementById('reading-candidate').value='';
  try{
    const response=await fetch(api+'/read',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({approval:true,expected_sha256:expected,
        answer_sources:JSON.parse(document.getElementById('answer-sources').value)})});
    const got=await response.json(); result.textContent=JSON.stringify(got,null,2);
    if(response.ok){document.getElementById('reading-candidate').value=got.text;
      document.getElementById('use-candidate').disabled=false}
  }catch(error){result.textContent=String(error)}finally{button.disabled=false}
};
document.getElementById('use-candidate').onclick=()=>{
  if(confirm('Заменить только текст редактора новым кандидатом? Сохранённая ведомость и экспертный акт пока не изменятся.'))
    area.value=document.getElementById('reading-candidate').value;
};
</script></html>""", headers={"Cache-Control": "no-store"})


@app.get("/api/runs/{run_id}/kit-scope")
def get_kit_scope(run_id: str):
    return kit_scope(_read_status(_run_dir(run_id)))


@app.put("/api/runs/{run_id}/kit-scope")
@_journaled("kit-scope", "body", sync=True)
def put_kit_scope(run_id: str, body: dict):
    """Признак допуска моделей у комплекта прогона: класс данных,
    явный llm_scope и основание (для проприетарного класса с допуском
    any — обязательно). Правится до стадий с моделями; форма
    проверяется тем же кодом, что при создании."""
    run_dir = _run_dir(run_id)
    unknown = set(body) - {"data_class", "llm_scope", "llm_scope_basis"}
    if unknown:
        raise HTTPException(400, "неизвестные поля признака: %s" % sorted(unknown))
    data_class = body.get("data_class") or None
    llm_scope = body.get("llm_scope") or None
    basis = body.get("llm_scope_basis") or None
    problems = validate_kit_scope(data_class, llm_scope, basis)
    if problems:
        raise HTTPException(400, "признак комплекта: " + "; ".join(problems))
    _update_status(run_dir, kit={"data_class": data_class, "llm_scope": llm_scope,
                                 "llm_scope_basis": basis})
    # S06-R2 (GPT-060 P2): допуск изменился — гейт раскладки пересчитан.
    _update_status(run_dir, layout=_layout_gate(run_dir))
    return kit_scope(_read_status(run_dir))


@app.get("/api/providers")
def list_providers():
    return {"providers": providers_mod.list_providers()}


@app.get("/api/settings")
def get_settings():
    """Ключи наружу не отдаются: только маски по провайдерам.

    Здесь же — адрес службы каталога шаблонов (`CREW_CATALOG_URL`) и
    отметка каталога, которым сейчас работает панель. Адрес в артефакты
    (реестр нажатия, паспорт, пакет) не пишется — ссылки строит
    интерфейс: переехавшая служба не должна делать прежние артефакты
    лживыми. Переменной нет — `null`, и интерфейс говорит словами, что
    служба не настроена, вместо мёртвой ссылки.
    """
    masks = {p["name"]: p["key_masked"]
             for p in providers_mod.list_providers()}
    key = providers_mod.provider_key("cloud")
    # Отметка каталога — через один помощник моста (шаг 7: `stamp()` без
    # каталога поднимает отказ). Здесь настройки, а не проба: форма
    # ключа прежняя (два поля либо null), слова причины отдаёт
    # `GET /api/catalog`.
    got = catalog_bridge.stamp_or_error()
    stamp = None if got.get("error") else {
        "version": got["version"], "fingerprint": got["fingerprint"]}
    return {"keys_masked": masks,
            "claude_api_key_masked": (providers_mod._mask(key)
                                      if key else None),
            "catalog_url": (os.environ.get("CREW_CATALOG_URL") or "").strip()
                           or None,
            "catalog": stamp,
            # РВ-141 (слово владельца 04.10): номер версии продукта внизу
            # интерфейса — одна настройка площадки на все компоненты;
            # литерала в коде нет, не задана — null и подписи нет.
            "product_version": (os.environ.get("CREW_PRODUCT_VERSION")
                                or "").strip() or None}


@app.put("/api/settings")
def put_settings(body: dict):
    """Сохранение или удаление ключа провайдера. Форма: {"provider":
    "openai", "api_key": "…"}; историческая форма {"claude_api_key":
    "…"} принимается как провайдер cloud."""
    unknown = set(body) - {"provider", "api_key", "claude_api_key"}
    if unknown:
        raise HTTPException(400, f"неизвестные поля {sorted(unknown)}")
    if "claude_api_key" in body:
        providers_mod.set_provider_key("cloud",
                                       body.get("claude_api_key") or "")
        return get_settings()
    provider = body.get("provider")
    if provider not in providers_mod.known_providers() \
            or provider in ("auto", "local"):
        raise HTTPException(400, f"провайдер {provider!r} не принимает "
                                 f"ключ")
    providers_mod.set_provider_key(provider, body.get("api_key") or "")
    return get_settings()


@app.post("/api/providers/{provider}/check")
def check_provider(provider: str, scope: str | None = None):
    """Проверка связи с провайдером коротким запросом."""
    if provider not in providers_mod.known_providers() \
            or provider == "auto":
        raise HTTPException(404, f"неизвестный провайдер {provider!r}")
    return providers_mod.check_provider(provider, scope or "any")


# МП-1: живой перечень моделей ключа, свежие первыми. Кэш короткий —
# перечень провайдера меняется редко, а шестерёнка блока дёргает
# точку при каждом открытии.
_MODELS_CACHE: dict[str, tuple[float, list[str]]] = {}
_MODELS_CACHE_TTL_S = 600.0


@app.get("/api/providers/{provider}/models")
def provider_models(provider: str):
    """Живые модели ключа провайдера: {models: [свежие первыми],
    latest: <новейшая>}. Без ключа или при отказе — 502 с причиной."""
    import time as _time
    from cdpm import external_llm
    # «Облако (Claude)» ходит через CLI и в SPECS внешних каналов не
    # входит, но живой перечень моделей у него есть (МП-1).
    if provider != "cloud" and provider not in external_llm.SPECS:
        raise HTTPException(404, f"живой перечень для {provider!r} "
                                 f"недоступен")
    hit = _MODELS_CACHE.get(provider)
    if hit and _time.monotonic() - hit[0] < _MODELS_CACHE_TTL_S:
        models = hit[1]
    else:
        key = providers_mod.provider_key(provider) or ""
        try:
            if provider == "cloud":
                models = external_llm.ranked_anthropic(key)
            else:
                models = external_llm.ranked_models(provider, key=key)
        except Exception as e:                             # noqa: BLE001
            raise HTTPException(502, str(e)[:300])
        _MODELS_CACHE[provider] = (_time.monotonic(), models)
    return {"models": models[:40],
            "latest": models[0] if models else None}


@app.get("/api/runs/{run_id}/agents")
def get_agents(run_id: str):
    return providers_mod.load_agents(_run_dir(run_id))


@app.put("/api/runs/{run_id}/agents")
@_journaled("agents", "body", sync=True)
def put_agents(run_id: str, body: dict):
    """Настройка ролей прогона — ПОЛНЫЙ снимок раскладки: тело
    запроса замещает пер-прогонный agents.json целиком (роль, которой
    в теле нет, из прогона уходит). Интерфейс так и работает: попап
    роли шлёт всю действующую раскладку (GET /agents — площадочное
    умолчание, пока своей нет) с правкой одной роли поверх, поэтому
    первая правка прогона уже несёт копию площадочного носителя. S06-R2
    (ревизия 1.18.5): частичное тело по API (одна роль) делает прочие
    облачные роли «не объявленными» — их стадии откажут по гейту
    раскладки словами; вызывающий по API обязан слать снимок целиком,
    как интерфейс."""
    run_dir = _run_dir(run_id)
    problems = providers_mod.save_agents(run_dir, body)
    if problems:
        raise HTTPException(400, "; ".join(problems))
    # S06-R2 (GPT-060 P2 STATUS-LAYOUT): признак раскладки в статусе —
    # пересчитывается после каждой правки ролей, не остаётся снимком
    # момента создания прогона.
    _update_status(run_dir, layout=_layout_gate(run_dir))
    return providers_mod.load_agents(run_dir)


@app.get("/api/agents/roles")
def agents_roles():
    """Перечень LLM-ролей конвейера (полная независимость выбора
    моделей, 2.09): роль, название, что делает, родитель наследования.
    Порядок — по ходу конвейера."""
    order = ("vision", "extractor", "logic_verifier", "auditor",
             "replier", "arbiter", "catalog_composer", "answer_matcher",
             "answer_applier", "data_compiler", "data_reviewer",
             "smt_translator", "mode2_checker", "worker", "applier")
    return {"roles": [
        {"role": r, "label": providers_mod.ROLE_META[r][0],
         "purpose": providers_mod.ROLE_META[r][1],
         "parent": providers_mod.ROLE_PARENT.get(r)}
        for r in order if r in providers_mod.ROLE_META]}


@app.get("/api/runs/{run_id}/agents/effective")
def agents_effective(run_id: str):
    """Фактическая раскладка прогона: для каждой роли — чья
    конфигурация действует (своя / родителя / умолчание канала) и
    модели её слотов. Таблица «узел → модель» владельцу до старта
    (правило llm-approval-before-runs)."""
    run_dir = _run_dir(run_id)
    agents = providers_mod.load_agents(run_dir)
    out = []
    for r in providers_mod.ROLES:
        eff = providers_mod.effective_role(agents, r)
        ch = ((agents.get(eff) or {}).get("channels") or {}) if eff \
            else {}
        out.append({
            "role": r, "label": providers_mod.ROLE_META[r][0],
            "effective": eff,
            "inherited": bool(eff and eff != r),
            "channels": {k: {"provider": (v or {}).get("provider"),
                             "model": (v or {}).get("model"),
                             "effort": (v or {}).get("effort")}
                         for k, v in ch.items()},
        })
    return {"run": run_id, "roles": out}


from cdpm.kit import (DATA_CLASSES, DATA_CLASS_RU, LLM_SCOPES,  # noqa: F401
                      kit_scope, validate_kit_scope)


def _run_scope(run_dir, requested: str | None) -> str:
    """Единая горловина допуска: эффективный контур ПРИЗНАКА комплекта
    прогона; просьба запроса — только сверка (расхождение — отказ
    словами, не источник)."""
    ks = kit_scope(_read_status(run_dir))
    effective = ks["effective"]
    if requested not in (None, "", effective):
        raise HTTPException(
            400, "просьба scope=%s расходится с признаком комплекта (%s: %s); "
                 "источник допуска — признак комплекта, измените его ручкой "
                 "kit-scope" % (requested, effective, ks["reading"]))
    return effective


def _channel_for(scope: str, run_dir, role: str,
                 fallback_requested: str | None = None,
                 klass: str = "cloud",
                 effort_default: str | None = None,
                 fallback_role: str | None = None,
                 key_role: str | None = None):
    """Канал роли для КЛАССА данных (слот конфигурации роли): блок
    обрабатывает каждый вход моделью слота, соответствующего признаку
    входа. Без конфигурации роли: локальный класс и класс «внутри
    страны» (канала пока нет — строже) идут локальным каналом, облачный
    — прежними швами (подменяемыми тестами). `effort_default` —
    умолчание усилия для настроенной роли (УСК-1). Контур площадки
    решает признак комплекта (_run_scope) — единая горловина
    всех LLM-каналов webui."""
    scope = _run_scope(run_dir, scope)
    agents = providers_mod.load_agents(run_dir)
    # Полная независимость ролей (2.09): роль без своей конфигурации
    # наследует родителя по цепочке (extractor → worker и т.д.);
    # явный fallback_role вызывающей стороны — прежний контракт.
    eff = providers_mod.effective_role(agents, role)
    if eff is None and fallback_role and agents.get(fallback_role):
        eff = fallback_role
    if eff:
        try:
            fn = providers_mod.role_channel(
                scope, run_dir, eff, fallback_requested, klass,
                effort_default=effort_default)
        except providers_mod.LayoutUndeclared as e:
            # S06-R2 (GPT-060 P1): роль есть, облачного слота нет — не
            # молчаливое облако, а отказ словами до вызова модели.
            raise HTTPException(409, _layout_refusal(
                run_dir, role, klass, source=e.config_source, eff=eff))
        return _journal_channel(run_dir, fn, role, klass,
                                key_role=key_role)
    # Канал без конфигурации роли тоже несёт отпечаток (A11): роль,
    # класс политики файла и «конфигурации нет» — соль кэшей стадий.
    # Ревизия 1.18.4: источник конфигурации «none», просьбы слота нет
    # (requested пуст), действующие провайдер/модель/вид канала — по
    # факту построенного канала.
    if klass != "cloud":
        raw = exchange_llm_factory(scope, "local")
        fn = llm_mod.stamp_identity(
            raw, role=role, klass=klass, role_config=None,
            config_source="none", requested={},
            effective=providers_mod.effective_channel(raw, "local"))
        return _journal_channel(run_dir, fn, role, klass,
                                key_role=key_role)
    # S06-R2 (план единой версии §3.7, ревизия 1.18.5): облачный класс
    # без объявленной раскладки ролей НЕ строится — ранний отказ словами
    # до первого вызова модели, а не молчаливая облачная Claude по
    # умолчанию канала. Прежняя ветка «облако без конфигурации → cloud»
    # (живой отказ арбитража 2026-08-18: «авто» выбирал локальную
    # модель) снята: умолчание площадки объявляется носителем
    # agents-default.json, умолчание прогона — agents.json; без них
    # модель облачной стадии никем не объявлена. Порядок отказов: допуск
    # «только локально» — старший (отказ дисциплины контура словами
    # признака комплекта, как прежде), раскладка судится только там,
    # где облако допуском разрешено.
    if scope != "local-only":
        # GPT-062 P2: слова отказа называют настоящий источник раскладки
        # (роль отсутствует в объявленной раскладке ≠ раскладки нет).
        raise HTTPException(409, _layout_refusal(
            run_dir, role, klass,
            source=providers_mod.agents_source(run_dir), eff=None))
    raw = exchange_llm_factory(scope, fallback_requested or "cloud")
    fn = llm_mod.stamp_identity(
        raw, role=role, klass=klass, role_config=None,
        config_source="none", requested={},
        effective=providers_mod.effective_channel(
            raw, fallback_requested or "cloud"))
    return _journal_channel(run_dir, fn, role, klass,
                            key_role=key_role)


def _layout_gate(run_dir) -> dict:
    """Ранний признак раскладки ролей прогона (S06-R2): источник
    действующей раскладки и открыт ли облачный класс. «none» при
    допуске, разрешающем облако, — облачные стадии будут отклонены
    словами (`_layout_refusal`) до первого вызова модели; пишется в
    статус при создании прогона, чтобы отказ был виден ДО старта, а
    не на стадии. Локальный класс раскладки не требует.

    Сам разбор — в `_layout_gate_for`: его же зовёт гейт «Новый прогон»
    ДО рождения папки прогона (допуск уже известен, статуса ещё нет)."""
    try:
        cloud_allowed = kit_scope(_read_status(run_dir)).get("effective") == "any"
    except Exception:                                     # noqa: BLE001
        cloud_allowed = False
    return _layout_gate_for(run_dir, cloud_allowed)


def _layout_refusal(run_dir, role: str | None, klass: str,
                    source: str = "none", eff: str | None = None) -> str:
    """Слова отказа S06-R2: что не объявлено, где объявить. Два случая:
    раскладки нет вовсе (источник none) и раскладка есть, но у роли не
    объявлен облачный слот (GPT-060 P1)."""
    who = (f"облачный канал роли «{role}» для класса «{klass}»"
           if role else "облачные каналы ролей")
    if source != "none":
        origin = ("настройка ролей прогона (agents.json)"
                  if source == "run-explicit"
                  else "носитель площадки agents-default.json")
        if eff is None:
            gap = (f"роль «{role or '…'}» в ней не настроена — ни своей "
                   "конфигурации, ни родителя по цепочке наследования")
        else:
            acting = (f"действует конфигурация роли «{eff}», "
                      if eff != role else "")
            gap = (f"{acting}у роли «{role or '…'}» не объявлен слот класса "
                   "«cloud» — ни провайдера, ни модели")
        return (f"раскладка моделей ролей объявлена (источник {source}: "
                f"{origin}), но {gap}; {who} не строится (S06-R2: модель "
                "облачной стадии должна быть объявлена, умолчания канала "
                "нет). Объявите слот «cloud» у роли на схеме (узел роли → "
                "модель) или в носителе площадки.")
    dpath = providers_mod._agents_default_path()
    where = (f"носителя площадки {dpath.name} в каталоге данных "
             f"({dpath.parent}) нет или он пуст" if dpath is not None
             else "носитель площадки agents-default.json отключён "
                  "(CDPM_AGENTS_DEFAULT пуст)")
    return (f"раскладка моделей ролей не объявлена (источник none): {where}, "
            f"у прогона нет agents.json — {who} не строится (S06-R2: "
            "модель облачной стадии должна быть объявлена, умолчания "
            "канала нет). Объявите раскладку: носитель площадки "
            "agents-default.json либо настройка ролей прогона на схеме "
            "(узел роли → модель); локальный класс раскладки не требует.")


def _note_stage_channel(run_dir, fn, key_role: str | None = None) -> None:
    """Журнал каналов стадий в статусе прогона (ревизия 1.18.4,
    таблица S06): ключ `stage_channels` — по каждой паре «роль/класс
    политики» источник конфигурации роли (config_source), просьба
    слота (requested), действующие провайдер/модель/вид канала
    (effective) и стадия, которая канал построила (занятая линия
    статуса). Запись — слиянием под замком статуса; канал без
    отпечатка (подменные функции тестов) и каталог без статуса
    (прогона ещё нет) журнала не получают."""
    raw = llm_mod.channel_identity(fn)
    if not raw or not _status_path(run_dir).is_file():
        return
    try:
        ident = json.loads(raw)
    except ValueError:
        return
    if not isinstance(ident, dict) or not ident.get("role"):
        return
    # Ключ записи — по ДЕЙСТВУЮЩЕЙ роли отпечатка; явный `key_role`
    # (шаг 6 каталога) даёт дочерней роли собственную строку журнала,
    # чтобы наследующая конфигурацию роль не перезаписывала строку
    # родителя (и через неё — `made_by.models` манифеста пакета).
    key = f"{key_role or ident['role']}/{ident.get('klass')}"
    entry = {k: ident.get(k) for k in
             ("config_source", "requested", "effective",
              "provider", "model")}
    # Паспорт прогона (07.09.2026): параметры модели, которыми шла
    # стадия, и коммит панели — для воспроизведения «заново».
    for k in ("effort", "temperature", "think", "max_tokens"):
        if ident.get(k) is not None:
            entry[k] = ident.get(k)
    entry["code"] = passport_mod.code_short()

    def _apply(st: dict) -> None:
        busy = [str(st.get(k)) for k in ("state", "data_state",
                                         "catalog_state")
                if _line_busy(st.get(k))]
        stage = busy[0] if busy else None
        if stage:
            for suffix in (" идёт", " идут"):
                if stage.endswith(suffix):
                    stage = stage[:-len(suffix)]
                    break
        entry["stage"] = stage
        journal = dict(st.get("stage_channels") or {})
        journal[key] = entry
        st["stage_channels"] = journal
    _mutate_status(run_dir, _apply)


# Порядок строгости классов данных: строже — меньше.
_CLASS_ORDER = {"local": 0, "cloud-country": 1, "cloud": 2}


def _strictest_class(run_dir) -> str:
    """Класс строжайшей политики среди файлов прогона: стадии,
    смешивающие содержимое всех файлов (противоречия, аудит-цикл,
    арбитраж), идут каналами этого класса.

    РВ-119/РВ-138 (04.10.2026): без настройки файлов прогона класс берётся
    из признака комплекта по классу данных исходных файлов
    (`kit.kit_scope`: синтетика и открытые — «любые модели», значит
    облако; прочее — локально), а не тихим «local» кода — стенд вне
    панели уходил в локальный канал без модели (Stage 03.10 19:4x)."""
    cfg = _read_files_config(run_dir)
    if not cfg:
        return _default_file_policy(_read_status(Path(run_dir)))
    return min((c["processing"] for c in cfg.values()),
               key=_CLASS_ORDER.__getitem__)


def _sync_canon(run_dir: Path, doc: dict, origin: str) -> list[str]:
    """Финальная модель итерации становится КАНОНИЧЕСКОЙ (ИР-3):
    extract/model.json + свод + анкета + Excel-книга перерендериваются;
    контракт обязателен. Возвращает нарушения (пусто = чисто)."""
    # Правило с исполнителем-ручкой шаблона не несёт logic с
    # enforce=required (блокер прохода 06.09.2026): сведение к post-check
    # до контракта — модель на диске и пакет одинаковы.
    m_mod.reconcile_logic_with_template_fit(doc)
    # Полнота параметров семьи (09.09.2026): посадки цикла окончательны
    # здесь — недостающие параметры семей спрашиваются, вопросы, ставшие
    # лишними после заплат, снимаются кодом до издания анкеты.
    family_params_mod.ensure_family_param_questions(doc)
    problems = m_mod.validate_schema(doc) + m_mod.validate_model(doc)
    if problems:
        return [f"{origin}: финальная модель не проходит контракт: "
                + p for p in problems]
    out = run_dir / "extract"
    m_mod.publish_model(doc, out / "model.json")
    (out / "svod.md").write_text(render_mod.render_svod(doc),
                                 encoding="utf-8")
    (out / "anketa.md").write_text(render_mod.render_anketa(doc),
                                   encoding="utf-8")
    anketa_xlsx_mod.render_anketa_xlsx(doc, out / "anketa.xlsx")
    return []


def _arbiter_summary(rep: dict) -> dict:
    """Сводка арбитража для статуса прогона (обе точки вызова —
    отдельный арбитраж и сквозной цикл): счётчики по вердиктам и
    исполнению; неисполненные вердикты — счётчиком и перечнем «тупик →
    причина», чтобы оператор видел не только «исполнений 0» и красный
    гейт, но и причину по каждому тупику (аудит A3.5, 2026-09-04).

    Так же показываются споры БЕЗ однозначного решения арбитра (A09,
    контракт П0 §1.3 и §12.2): счётчик «не разрешено» и перечень
    «тупик → причина». Сумма трёх счётчиков by_verdict меньше числа
    тупиков ровно на число неразрешённых СПОРОВ ЦИКЛА — спор не
    исчезает и не становится «первым из двух». Перечень «не разрешено»
    бывает длиннее: в нём же лишние идентификаторы ответа арбитра и
    записи без формы — спорами цикла они не являются, поэтому в счёт
    «рассмотрено» не входят, а считаются отдельно
    (`unresolved_foreign`, ревью A09, находка 6).

    Третий живой исход — «ждёт ответа владельца» (§12.8 + ревью A09 №2,
    находка 3): спор, по которому «аудитор прав» уже передан владельцу
    вопросом анкеты, арбитру заново не предъявляется, пока владелец не
    ответил. Счётчик и перечень «тупик → причина» — своими полями:
    иначе спор исчезал бы из панели вовсе."""
    unenforced = rep.get("unenforced") or []
    unresolved = rep.get("unresolved") or []
    awaiting = rep.get("awaiting_owner") or []
    return {
        "deadlocks": rep["deadlocks"],
        "unresolved_foreign": rep.get("unresolved_foreign", 0),
        "by_verdict": rep["by_verdict"],
        "enforced": rep["enforced"],
        "unenforced": len(unenforced),
        "unenforced_deadlocks": [
            {"deadlock": u.get("deadlock"), "reason": u.get("reason")}
            for u in unenforced],
        "unresolved": len(unresolved),
        "unresolved_deadlocks": [
            {"deadlock": u.get("deadlock"), "reason": u.get("reason")}
            for u in unresolved],
        "awaiting_owner": len(awaiting),
        "awaiting_owner_deadlocks": [
            {"deadlock": a.get("deadlock"), "question": a.get("question"),
             "reason": a.get("reason")} for a in awaiting],
        "added_questions": len(rep["added_questions"]),
        "patch_rejections": len(rep["patch_rejections"])}


def _role_config_salt(run_dir, role: str) -> str:
    """Соль кэша по конфигурации ДЕЙСТВУЮЩЕЙ роли (своя, иначе
    родитель по цепочке): имя действующей роли + её конфигурация
    целиком; без конфигурации — пометка «умолчание канала»."""
    agents = providers_mod.load_agents(run_dir)
    eff = providers_mod.effective_role(agents, role)
    return json.dumps({"role": role, "effective": eff,
                       "config": (agents.get(eff) or {}) if eff else None},
                      ensure_ascii=False, sort_keys=True)


def _extract_cache_fingerprint(run_dir, scope: str,
                               files_cfg: dict | None = None) -> str:
    """Отпечаток условий, от которых зависят ответы моделей в кэше
    извлечения: раскладка ролей прогона, политика каждого файла,
    допуск моделей, модели умолчания площадки (облачная и локальная).
    Ключей и иных секретов в отпечатке нет."""
    import hashlib
    settings = providers_mod.load_settings()
    try:
        from webapp import llm_backend
        # РВ-119: отпечаток соли — модель площадки либо None, без отказа.
        local_default = llm_backend.ollama_model_or_none()
    except Exception:                                     # noqa: BLE001
        local_default = None
    cfg = files_cfg if files_cfg is not None else _read_files_config(run_dir)
    body = {
        "scope": scope,
        "agents": providers_mod.load_agents(run_dir),
        # Ревизия 1.18.4: та же раскладка из другого источника
        # (площадочное умолчание ↔ настройка прогона) — другой ключ.
        "agents_source": providers_mod.agents_source(run_dir),
        "files": {name: (c or {}).get("processing")
                  for name, c in sorted(cfg.items())},
        "cloud_default_model": settings.get("cloud_default_model"),
        "local_default_model": local_default,
    }
    return hashlib.sha256(json.dumps(body, ensure_ascii=False,
                                     sort_keys=True).encode("utf-8")
                          ).hexdigest()


def _reset_stale_extract_cache(run_dir, fingerprint: str) -> str | None:
    """Кэш извлечения без meta.json либо с иным отпечатком — сносится
    целиком до первого запроса и заводится заново под текущий
    отпечаток. Возвращает пометку словами для статуса (None — кэш
    свежий или его не было)."""
    cache = run_dir / "extract-cache"
    meta = cache / "meta.json"
    stored = None
    if meta.is_file():
        try:
            stored = json.loads(meta.read_text(encoding="utf-8")) \
                .get("fingerprint")
        except (ValueError, AttributeError):
            stored = None
    note = None
    if cache.is_dir() and any(p.name != "meta.json" for p in cache.iterdir()) \
            and stored != fingerprint:
        _rmtree_strict(cache)
        # Одна строка статуса — объясняет стоимость переизвлечения.
        note = ("кэш извлечения сброшен: изменилась раскладка/настройки "
                "моделей" + (" (кэш прежней версии без отпечатка)"
                             if stored is None else ""))
    cache.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"fingerprint": fingerprint},
                               ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return note


def _archive_exchange_for_reextract(run_dir: Path) -> str | None:
    """ПЕРЕизвлечение: непустой exchange уходит в exchange-rev-NNN
    ЦЕЛИКОМ — без переноса реестра и регресс-токенов (они судят
    прежний свод; новый свод начинает историю с нуля). Отличие от
    _archive_finished_exchange принципиально: та архивация живёт
    внутри ОДНОГО свода и токены переносит намеренно.

    В архив кладётся МЕТКА обрыва истории: имя каталога у обеих
    архиваций одно, а содержание разное — здесь лежат обмены ПРЕЖНЕГО
    свода. Спор нового свода судится только по своей истории, и досье
    отсекает такой архив по реестру: реестр в архиве ЕСТЬ (уезжает
    вместе с каталогом), но в НОВЫЙ цикл он намеренно не переносится
    — новый свод заводит свои находки со своими идентификаторами, и
    реестр прежнего свода их не знает
    (`exchange._same_dispute_history`, пятое неавтор-ревью A09);
    метка — второй замок и слова намерения для человека, открывшего
    каталог."""
    ex_dir = run_dir / "exchange"
    if not ex_dir.is_dir() or not any(ex_dir.iterdir()):
        return None
    n = 1
    while (run_dir / f"exchange-rev-{n:03d}").exists():
        n += 1
    archive = run_dir / f"exchange-rev-{n:03d}"
    ex_dir.rename(archive)
    (archive / exchange_mod.HISTORY_BREAK_FILE).write_text(
        json.dumps({"kind": "переизвлечение",
                    "note": exchange_mod.HISTORY_BREAK_NOTE},
                   ensure_ascii=False, indent=1),
        encoding="utf-8")
    return archive.name


def _archive_finished_exchange(run_dir: Path) -> str | None:
    """Итерация (ИР-3): завершённый цикл архивируется в
    exchange-rev-NNN, реестр копируется в новый цикл — требование
    арбитра («обработчик прав» повторно не поднимается) действует
    сквозь ревизии. Незавершённый цикл не трогается: его раунды
    возобновляются с диска.

    Метки обрыва истории здесь НЕТ и быть не должно: это тот же свод,
    архив — его собственная история, и досье спора читает его
    содержание (реестр переносится, находка узнаётся по паре
    «идентификатор + ключ»)."""
    ex_dir = run_dir / "exchange"
    if not (ex_dir / "verdict.json").is_file():
        return None
    n = 1
    while (run_dir / f"exchange-rev-{n:03d}").exists():
        n += 1
    archive = run_dir / f"exchange-rev-{n:03d}"
    ex_dir.rename(archive)
    ex_dir.mkdir()
    # Сквозь ревизии переносятся И реестр (требование арбитра), И
    # регресс-токены (АЦ-4/Ж2, 2026-08-21): без токенов следующий круг
    # мог незаметно откатить исправление, принятое в прошлом — в том
    # числе арбитражное.
    for name in ("registry.json", "tokens.json"):
        src = archive / name
        if src.is_file():
            (ex_dir / name).write_text(
                src.read_text(encoding="utf-8"), encoding="utf-8")
    return archive.name


def _exchange_verdict_words(summary: dict) -> str:
    """Слова вердикта цикла для состояния прогона: «не закрыто» несёт
    число блокирующих находок реестра из сводки verdict.json (A07:
    панель показывает «не закрыто: N блокирующих» вместо «успех»);
    прочие вердикты — словом как есть."""
    verdict = str(summary.get("verdict"))
    if verdict == exchange_mod.VERDICT_NOT_CLOSED:
        n = (summary.get("registry_verdict") or {}).get("count") or 0
        return f"{verdict}: {n} блокирующих"
    return verdict


@app.post("/api/runs/{run_id}/exchange")
@_journaled("exchange", "auditor", "worker", "scope", "max_rounds", "continue_from_stop")
def start_exchange(run_id: str, auditor: str = "cloud",
                   worker: str = "cloud", scope: str | None = None,
                   max_rounds: int = 5,
                   continue_from_stop: bool = False):
    """Аудит-цикл «обработчик — аудитор» — в фоновом потоке; интерфейс
    опрашивает статус и читает артефакты обменов по мере появления."""
    run_dir = _run_dir(run_id)
    for name, value in (("auditor", auditor), ("worker", worker)):
        if value not in ("local", "cloud", "auto"):
            raise HTTPException(400, f"недопустимый канал {name}: "
                                     f"{value!r}")
    if scope not in (None, "any", "local-only"):
        raise HTTPException(400, f"недопустимый scope {scope!r}")
    if not (1 <= max_rounds <= 20):
        raise HTTPException(400, "лимит обменов — от 1 до 20")
    status = _read_status(run_dir)
    if _line_busy(status.get("state")):
        raise HTTPException(409, "прогон занят другой стадией")
    if not (run_dir / "extract" / "model.json").is_file():
        raise HTTPException(409, "сначала извлечение: цикл аудирует "
                                 "готовый свод")
    # Цикл смешивает содержимое всех файлов — каналы обеих ролей
    # строятся слотом СТРОЖАЙШЕГО класса политики файлов прогона.
    klass = _strictest_class(run_dir)
    archived = _archive_finished_exchange(run_dir)
    _claim_stage(run_dir, state="аудит-цикл идёт",
                 exchange={"auditor": auditor, "worker": worker,
                           "max_rounds": max_rounds, "class": klass,
                           **({"archived": archived}
                              if archived else {})})
    _control_path(run_dir).unlink(missing_ok=True)
    control = _make_checkpoint(run_dir, "аудит-цикл",
                               "аудит-цикл идёт")

    # ОСТ-1 (решение владельца 2026-08-21): ручной цикл останавливается
    # на первом обмене с Б=0, К=0 при С>0 и спрашивает человека;
    # «продолжить» = повторный запуск с continue_from_stop=true —
    # остановка снова сработает только на НОВОМ таком обмене.
    n_prev_rounds = len(list(
        (run_dir / "exchange" / "rounds").glob("round-*"))) \
        if (run_dir / "exchange" / "rounds").is_dir() else 0

    def _work():
        own: dict = {}
        try:
            summary = exchange_mod.run_exchange(
                run_dir / "freeze", run_dir / "extract",
                run_dir / "exchange",
                _channel_for(scope, run_dir, "auditor", auditor,
                             klass=klass),
                # УСК-1 (отмашка владельца 2026-08-21): ответы
                # обработчика — механика патчей, низкое усилие по
                # умолчанию; явная настройка роли главнее.
                _channel_for(scope, run_dir, "replier", worker,
                             klass=klass, effort_default="low"),
                max_rounds, control=control,
                bk_stop=True,
                bk_stop_after=(n_prev_rounds if continue_from_stop
                               else 0))
            problems, counters = gate_exchange.check(
                run_dir / "exchange")
            if not problems:
                # ИР-3: финальная версия цикла — канонический свод.
                final = m_mod.loads_model(
                    (run_dir / "exchange" / "versions"
                     / summary["final_version"])
                    .read_text(encoding="utf-8"))
                problems = _sync_canon(run_dir, final, "аудит-цикл")
            verdict = summary["verdict"]
            if verdict == "остановлен по Б/К" and not problems:
                s_n = (summary.get("history") or [{}])[-1] \
                    .get("weighty", [0, 0, 0])[2]
                # A07: остановка «по Б/К» возможна только при пустом
                # реестре блокирующих — слова называют и это.
                state = (f"аудит остановлен: Б 0, К 0, С {s_n}, живых "
                         f"блокирующих в реестре 0 — продолжить аудит?")
            else:
                state = ("аудит-цикл: " + _exchange_verdict_words(summary)) \
                    if not problems else "красный гейт аудит-цикла"
            own.update(
                state=state,
                exchange_summary=summary,
                error=None,
                exchange_counters=counters,
                exchange_problems=problems)
        except StopRequested:
            own.update(state="остановлено: аудит-цикл", error=None)
        except Exception as e:                             # noqa: BLE001
            own.update(**_stage_failure("отказ аудит-цикла", e))
        _update_status(run_dir, **own)

    _start_stage_thread(run_dir, _work, after=_catalog_auto_after(
        run_dir, run_id, _CATALOG_AFTER_EXCHANGE))
    return {"run": run_id, "state": "аудит-цикл идёт"}


@app.post("/api/runs/{run_id}/questions/{qid}/reconsider")
@_journaled("reconsider", "qid", sync=True)
def reconsider_question(run_id: str, qid: str):
    """Право авиакомпании ПЕРЕДУМАТЬ (живой прогон нано 31.08:
    принятый ответ сделал заявки-пожелания мягким правилом, а
    исполнителя нет — а/к хочет пересмотреть). Пере-ответ поверх
    применённого запрещён штатно; честный путь — ОТКРЫТЫЙ
    вопрос-пересмотр следующей ревизии (прецеденты доборов GOAL-T и
    критерия): часть и привязки наследуются, прежний ответ виден в
    тексте; новый ответ применяется существующими механиками
    (обработчик патчами под контрактом)."""
    run_dir = _run_dir(run_id)
    status = _read_status(run_dir)
    if "идёт" in str(status.get("state") or ""):
        raise HTTPException(409, "прогон занят стадией")
    model_path = run_dir / "extract" / "model.json"
    if not model_path.is_file():
        raise HTTPException(409, "свода нет — сначала извлечение")
    doc = m_mod.load_model(model_path)
    q = next((x for x in doc.get("questions", [])
              if x.get("id") == qid), None)
    if q is None:
        raise HTTPException(404, f"вопроса {qid!r} нет")
    # Аудит 04.09, п. A3.4: пересмотр снимает следствия прежнего ответа
    # по журналу (строки — к виду «до», развилка возвращается),
    # блокирует строки вопросом -R; ответ на -R применяется с реальным
    # эффектом. Ответ без журнала — -R с честной пометкой.
    try:
        opened = answers_mod.open_revision(doc, qid)
    except answers_mod.AnswersError as exc:
        raise HTTPException(409, str(exc))
    rid_new = opened["created"]
    problems = m_mod.validate_schema(doc) + m_mod.validate_model(doc)
    if problems:
        raise HTTPException(500, "вопрос-пересмотр не проходит "
                                 "контракт: " + "; ".join(problems))
    out = run_dir / "extract"
    m_mod.publish_model(doc, out / "model.json")
    (out / "svod.md").write_text(render_mod.render_svod(doc),
                                 encoding="utf-8")
    (out / "anketa.md").write_text(render_mod.render_anketa(doc),
                                   encoding="utf-8")
    anketa_xlsx_mod.render_anketa_xlsx(doc, out / "anketa.xlsx")
    return {"run": run_id, **opened}


@app.post("/api/runs/{run_id}/kind-migrations")
@_journaled("kind-migrations", "migrations", sync=True)
def apply_kind_migrations(run_id: str,
                          migrations: list = Body(...)):
    """Операторские миграции ВИДА строк (живой прогон нано 31.08:
    аудитор клаузам верные строки дефектом вида не считает — судьи
    нет, а форма kind_patch есть). Власть оператора — как ответ
    анкеты: решение человека, машинно гейтованное той же формой
    (закрытый перечень переходов, контракт целиком), со следом
    происхождения в note. ВСЁ-ИЛИ-НИЧЕГО: любой отказ — 409 со
    списком причин, канон не тронут."""
    from cdpm import exchange as exchange_mod
    run_dir = _run_dir(run_id)
    status = _read_status(run_dir)
    if "идёт" in str(status.get("state") or ""):
        raise HTTPException(409, "прогон занят стадией")
    model_path = run_dir / "extract" / "model.json"
    if not model_path.is_file():
        raise HTTPException(409, "свода нет — сначала извлечение")
    if not isinstance(migrations, list) or not migrations:
        raise HTTPException(400, "тело — непустой список миграций")
    doc = m_mod.load_model(model_path)
    applied: list[str] = []
    reasons_all: list[str] = []
    for i, kp in enumerate(migrations):
        if not isinstance(kp, dict):
            reasons_all.append(f"[{i}]: миграция не объект")
            continue
        reasons = exchange_mod._apply_kind_patch(doc, kp)
        if reasons:
            reasons_all += [f"[{i}] {kp.get('rule_id')}: {r}"
                            for r in reasons]
        else:
            rid = str(kp.get("rule_id"))
            rule = next(r for r in doc["rules"]
                        if r.get("id") == rid)
            rule["note"] = (str(rule.get("note") or "")
                            .replace("заплатой обмена",
                                     "операторским решением "
                                     "(панель)"))
            applied.append(rid)
    if reasons_all:
        raise HTTPException(409, "миграции не применены "
                                 "(всё-или-ничего): "
                            + "; ".join(reasons_all))
    sync_problems = _sync_canon(run_dir, doc,
                                "операторские миграции вида")
    if sync_problems:
        raise HTTPException(409, "; ".join(sync_problems))
    return {"run": run_id, "applied": applied}


_EXCHANGE_SRC_RE = re.compile(r"exchange(?:-rev-\d{3})?$")


@app.post("/api/runs/{run_id}/exchange/void")
@_journaled("exchange-void", sync=True)
def void_unfinished_exchange(run_id: str):
    """Снятие НЕЗАВЕРШЁННОГО аудит-цикла в архив (живой случай BA
    31.08: после красного гейта, вылеченного правкой кода судьи,
    повторный запуск породил цикл по УСТАРЕВШЕМУ канону — его раунды
    возобновлялись бы с диска вечно). Архив exchange-void-NNN, без
    переноса чего-либо; завершённый цикл (verdict.json) снимается
    только штатной архивацией нового запуска — 409."""
    run_dir = _run_dir(run_id)
    status = _read_status(run_dir)
    if "идёт" in str(status.get("state") or ""):
        raise HTTPException(409, "прогон занят стадией — снимать "
                                 "нечего пока цикл жив")
    ex_dir = run_dir / "exchange"
    if not ex_dir.is_dir() or not any(ex_dir.iterdir()):
        raise HTTPException(409, "живого цикла нет — снимать нечего")
    if (ex_dir / "verdict.json").is_file():
        raise HTTPException(409, "цикл ЗАВЕРШЁН — он снимается "
                                 "только штатной архивацией нового "
                                 "запуска, не сносом")
    n = 1
    while (run_dir / f"exchange-void-{n:03d}").exists():
        n += 1
    void = run_dir / f"exchange-void-{n:03d}"
    ex_dir.rename(void)
    return {"run": run_id, "voided": void.name}


@app.post("/api/runs/{run_id}/exchange/resync")
@_journaled("exchange-resync", "src", sync=True)
def resync_exchange_canon(run_id: str, src: str = "exchange"):
    """Дожатие хвоста ЗАВЕРШЁННОГО цикла (живой случай BA 31.08):
    красный гейт остановил канонизацию fail-closed; после правки
    кода СУДЬИ дожать канон было нечем — только новым циклом по
    устаревшему канону. Ручка гонит гейт по каталогу цикла (живому
    или архиву exchange-rev-NNN) и при зелёном записывает канон;
    реестр и регресс-токены переносятся в свежий пустой exchange —
    требование арбитра действует сквозь ревизии (та же дисциплина,
    что штатная архивация)."""
    run_dir = _run_dir(run_id)
    status = _read_status(run_dir)
    if "идёт" in str(status.get("state") or ""):
        raise HTTPException(409, "прогон занят стадией")
    if not _EXCHANGE_SRC_RE.fullmatch(src):
        raise HTTPException(400, f"недопустимый источник {src!r}: "
                                 "exchange либо exchange-rev-NNN")
    src_dir = run_dir / src
    if not (src_dir / "verdict.json").is_file():
        raise HTTPException(409, f"{src}: цикл не завершён — "
                                 "пересинхронизировать нечего")
    summary = json.loads((src_dir / "verdict.json")
                         .read_text(encoding="utf-8"))
    problems, counters = gate_exchange.check(src_dir)
    if problems:
        raise HTTPException(409, "гейт цикла красный — канон не "
                                 "трогаю: " + "; ".join(problems))
    final = m_mod.loads_model(
        (src_dir / "versions" / summary["final_version"])
        .read_text(encoding="utf-8"))
    sync_problems = _sync_canon(run_dir, final,
                                f"пересинхронизация из {src}")
    if sync_problems:
        raise HTTPException(409, "; ".join(sync_problems))
    ex_dir = run_dir / "exchange"
    if src != "exchange":
        if ex_dir.is_dir() and any(ex_dir.iterdir()):
            raise HTTPException(409, "живой exchange непуст — "
                                     "сначала снимите его (void) "
                                     "либо завершите штатно")
        ex_dir.mkdir(exist_ok=True)
        for name in ("registry.json", "tokens.json"):
            p = src_dir / name
            if p.is_file():
                (ex_dir / name).write_text(
                    p.read_text(encoding="utf-8"), encoding="utf-8")
    _update_status(run_dir,
                   state="аудит-цикл: " + _exchange_verdict_words(summary),
                   exchange_summary=summary, error=None,
                   exchange_counters=counters, exchange_problems=[])
    out = {"run": run_id, "src": src,
           "verdict": summary.get("verdict"),
           "canon_rules": len(final.get("rules", []))}
    # Ревью пакета посадки: архив до контракта посадки молча
    # регрессировал канон к модели без посадок — деградация теперь
    # названа словами в ответе ручки (оператор решает осознанно).
    if not (final.get("meta") or {}).get("fit_contract"):
        out["warning"] = ("канон источника БЕЗ контракта посадки "
                          "(fit_contract): посадки правил утрачены — "
                          "источник старше пакета посадки")
    return out


def _bk_clean_tail(history: list[dict]) -> int | None:
    """С-1 (анализ Лайта-v3): чистоту по Б/К доказывает последний
    ПОЛНЫЙ раунд; чистые инкрементальные ПОСЛЕ него её не опровергают.
    Прежняя проверка требовала полноты именно от последнего раунда —
    круг, кончившийся чистым инкрементальным хвостом, помечался
    «лимит кругов» вместо «чисто по Б/К».

    В2-5 (состязательная проверка 2026-08-21): раунд, принявший
    правки, менял свод ПОСЛЕ своего аудита — финализировать «чисто»
    можно только когда последний полный раунд и весь хвост после него
    чисты по Б/К И НЕ патчили свод: иначе итоговая версия никем не
    аудирована. Возвращает индекс последнего полного раунда либо
    None."""
    last_full_i = None
    for i, h in enumerate(history):
        if h.get("mode", "полный") == "полный":
            last_full_i = i
    if last_full_i is None:
        return None
    ok = all(h["weighty"][0] == 0 and h["weighty"][1] == 0
             and not h.get("patched", False)
             for h in history[last_full_i:])
    return last_full_i if ok else None


def _read_registry(run_dir: Path, *, expect_file: bool | None = None) -> dict:
    """Реестр находок цикла с диска ОДНИМ загрузчиком продукта
    (`exchange.load_registry`): нет файла — пустой реестр; старая форма
    без schema_version читается как прежде; неизвестная версия и
    неполная новая форма — отказ словами (A07-F4), не догадка.
    Миграция чтения (A07-F3) — там же: старая техническая отбраковка
    замком кода живая, файл на диске чтением не переписывается.

    A07-R5: требование НОСИТЕЛЯ. `expect_file=None` (умолчание) —
    судит ОДИН предикат продукта `exchange.registry_expected(каталог
    обмена, состояние прогона)`: цикл исполнен (слово состояния
    заявило исход ЛИБО в каталоге лежит вердикт цикла) — реестр обязан
    быть, и его отсутствие называется отказом, а не читается пустым
    реестром («новый прогон»). Половины слова здесь мало: прогон,
    пересуженный после стадии ответов, стоит на «ответы применены»,
    хотя цикл отработал раньше. Явное `True`/`False` — для мест, где
    исход известен точнее каталога и состояния (внутри сквозного цикла
    круг ЗАВЕРШЁН здесь же, а вердикт ещё не записан)."""
    exdir = run_dir / "exchange"
    if expect_file is None:
        expect_file = exchange_mod.registry_expected(
            exdir, _read_status(run_dir).get("state"))
    return exchange_mod.load_registry(exdir / "registry.json",
                                      expect_file=expect_file)


def _live_findings(run_dir: Path, *,
                   expect_file: bool | None = None) -> list[dict]:
    """Живые находки реестра цикла (В2-5): бэклог и страховка финала
    читают РЕЕСТР, а не remarks.json раундов — замечания, отклонённые
    до хвоста (аудитору запрещено повторять их без нового довода) или
    уже исправленные, иначе теряются/задваиваются.

    Закон живых находок один — `exchange.LIVE_STATES` и
    `exchange.live_findings` (A07, контракт П0 §1.2, 12.09.2026): тот
    же предикат у цикла, гейта и раннера H0; прежний перечень панели
    («открыто», «отклонено», «тупик») не знал «патч отбракован» и
    «отклонено кодом». Ключ, решённый арбитром, гасит ВСЕ находки
    этого ключа (эхо решённого спора не всплывает)."""
    return exchange_mod.live_findings(
        _read_registry(run_dir, expect_file=expect_file))


@app.post("/api/runs/{run_id}/full-cycle")
@_journaled("full-cycle", "auditor", "worker", "scope", "max_rounds", "max_megacycles")
def start_full_cycle(run_id: str, auditor: str = "cloud",
                     worker: str = "cloud", scope: str | None = None,
                     max_rounds: int = 5, max_megacycles: int = 3):
    """СКВ-1 (директива владельца 2026-08-21): сквозной процесс одной
    кнопкой — аудит-цикл → при тупиках арбитраж с исполнением →
    архив ревизии (ИР-3) → новый аудит-цикл → … Останов: в последнем
    обмене нет блокирующих и критических (Б=0 и К=0), либо исчерпан
    лимит кругов, либо стоп ПС-1. Пауза/стоп действуют на всю
    цепочку."""
    run_dir = _run_dir(run_id)
    for name, value in (("auditor", auditor), ("worker", worker)):
        if value not in ("local", "cloud", "auto"):
            raise HTTPException(400, f"недопустимый канал {name}: "
                                     f"{value!r}")
    if scope not in (None, "any", "local-only"):
        raise HTTPException(400, f"недопустимый scope {scope!r}")
    if not (1 <= max_rounds <= 20):
        raise HTTPException(400, "лимит обменов — от 1 до 20")
    if not (1 <= max_megacycles <= 10):
        raise HTTPException(400, "лимит кругов — от 1 до 10")
    status = _read_status(run_dir)
    if _line_busy(status.get("state")):
        raise HTTPException(409, "прогон занят другой стадией")
    if not (run_dir / "extract" / "model.json").is_file():
        raise HTTPException(409, "сначала извлечение: цикл аудирует "
                                 "готовый свод")
    klass = _strictest_class(run_dir)
    _claim_stage(run_dir, state="сквозной цикл идёт",
                 full_cycle={"auditor": auditor, "worker": worker,
                             "max_rounds": max_rounds,
                             "max_megacycles": max_megacycles,
                             "cycle": 1, "phase": "цикл",
                             "class": klass})
    _control_path(run_dir).unlink(missing_ok=True)
    control = _make_checkpoint(run_dir, "сквозной цикл",
                               "сквозной цикл идёт")

    def _mark(cycle: int, phase: str) -> None:
        def _apply(st: dict) -> None:
            fc = dict(st.get("full_cycle") or {})
            fc.update(cycle=cycle, phase=phase)
            st["full_cycle"] = fc
        _mutate_status(run_dir, _apply)

    def _work():
        own: dict = {}
        outcome, problems = None, []

        def _arbitrate() -> list[str]:
            """Арбитраж текущего цикла; возвращает проблемы гейта."""
            rep = exchange_mod.run_arbiter(
                run_dir / "freeze", run_dir / "exchange",
                _channel_for(scope, run_dir, "arbiter", klass=klass),
                _channel_for(scope, run_dir, "replier", klass=klass),
                control=control)
            probs, a_counters = gate_arbiter.check(run_dir / "exchange")
            # Канон обновляется ТОЛЬКО зелёным гейтом (как у всех
            # стадий): неисполненный вердикт — красный гейт, принятые
            # заплаты ждут в model-arbiter.json повторного арбитража,
            # который судит лишь оставшиеся «тупик» на той же базе.
            if not probs:
                final = m_mod.loads_model(
                    (run_dir / "exchange" / "versions"
                     / "model-arbiter.json").read_text(encoding="utf-8"))
                probs = _sync_canon(run_dir, final, "арбитраж")
            own.update(
                arbiter_summary=_arbiter_summary(rep),
                arbiter_counters=a_counters,
                arbiter_problems=probs)
            return probs

        try:
            # Достройка прежней ревизии: если на диске лежит
            # ЗАВЕРШЁННЫЙ цикл с тупиками без арбитража — сквозной
            # процесс начинает с арбитража, не архивируя его (иначе
            # тупики уехали бы в архив неразобранными).
            v_path = run_dir / "exchange" / "verdict.json"
            if v_path.is_file():
                v = json.loads(v_path.read_text(encoding="utf-8"))
                if v.get("deadlocks") and not (
                        run_dir / "exchange" / "arbiter.json"
                        ).is_file():
                    _mark(0, "арбитраж прежней ревизии")
                    problems = _arbitrate()
                    if problems:
                        outcome = "красный гейт арбитража"
            for mega in range(1, max_megacycles + 1):
                if outcome:
                    break
                _mark(mega, "цикл")
                _archive_finished_exchange(run_dir)
                summary = exchange_mod.run_exchange(
                    run_dir / "freeze", run_dir / "extract",
                    run_dir / "exchange",
                    _channel_for(scope, run_dir, "auditor", auditor,
                                 klass=klass),
                    _channel_for(scope, run_dir, "replier", worker,
                                 klass=klass,
                                 effort_default="low"),
                    max_rounds, control=control)
                problems, counters = gate_exchange.check(
                    run_dir / "exchange")
                if not problems:
                    final = m_mod.loads_model(
                        (run_dir / "exchange" / "versions"
                         / summary["final_version"])
                        .read_text(encoding="utf-8"))
                    problems = _sync_canon(run_dir, final,
                                           "аудит-цикл")
                own.update(exchange_summary=summary,
                           exchange_counters=counters,
                           exchange_problems=problems)
                if problems:
                    outcome = "красный гейт аудит-цикла"
                    break
                hist = summary.get("history") or []
                tail_i = _bk_clean_tail(hist)
                # A07-R5: круг ЗАВЕРШЁН здесь же (вердикт получен,
                # гейт зелёный) — исход заявлен, хотя состояние
                # прогона ещё «сквозной цикл идёт». Требуем носитель
                # ЯВНО: иначе исчезнувший реестр дал бы пустой перечень
                # живых находок, и мегакруг объявил бы «чисто по Б/К»
                # ровно там, где судить нечем.
                reg = _read_registry(run_dir, expect_file=True)
                live = exchange_mod.live_findings(reg)
                # В2-5: живой Б/К-спор (в т.ч. тупик, который аудитору
                # запрещено повторять — метрика хвоста его не видит, и
                # «патч отбракован» — правка не встала) блокирует финал
                # «чисто»: цикл идёт дальше/в арбитраж. Закон один —
                # exchange.registry_blockers (A07): тот же, что даёт
                # циклу вердикт «не закрыто».
                live_bk = exchange_mod.registry_blockers(reg)
                if tail_i is not None and not live_bk:
                    # АЦ-4/Г2: «серьёзные» не крутят круги вечно —
                    # ОСТАТОК (живые находки реестра, включая тупики)
                    # уходит пакетом в бэклог.
                    serious = [f for f in live
                               if f.get("severity") == "СЕРЬЁЗНОЕ"]
                    n_serious = len(serious)
                    if serious:
                        (run_dir / "exchange"
                         / "backlog-serious.json").write_text(
                            json.dumps(serious,
                                       ensure_ascii=False,
                                       indent=1),
                            encoding="utf-8")
                    outcome = (f"сквозной цикл: чисто по Б/К "
                               f"(кругов: {mega})"
                               + (f"; серьёзных в бэклоге: "
                                  f"{n_serious}" if n_serious
                                  else ""))
                    break
                deadlocks = summary.get("deadlocks") or []
                if deadlocks:
                    _mark(mega, "арбитраж")
                    problems = _arbitrate()
                    if problems:
                        outcome = "красный гейт арбитража"
                        break
            if outcome is None:
                outcome = (f"сквозной цикл: лимит кругов "
                           f"({max_megacycles})")
            own.update(state=outcome, error=None, error_trace=None)
        except StopRequested:
            own.update(state="остановлено: сквозной цикл", error=None,
                       error_trace=None)
        except Exception as e:                             # noqa: BLE001
            # Отказ без трассировки недиагностируем (живой урок UniA
            # run-0051: «unhashable dict» без места падения).
            own.update(**_stage_failure("отказ сквозного цикла", e),
                       error_trace=traceback.format_exc())
        _update_status(run_dir, **own)

    _start_stage_thread(run_dir, _work, after=_catalog_auto_after(
        run_dir, run_id, _CATALOG_AFTER_FULL_CYCLE))
    return {"run": run_id, "state": "сквозной цикл идёт"}


@app.get("/api/runs/{run_id}/exchange")
def exchange_status(run_id: str):
    """Живое состояние цикла: обмены с метриками читаются из каталога
    цикла по мере появления, реестр — счётом по состояниям."""
    run_dir = _run_dir(run_id)
    ex_dir = run_dir / "exchange"
    rounds = []
    rdir = ex_dir / "rounds"
    if rdir.is_dir():
        for p in sorted(rdir.glob("round-*")):
            item = {"round": int(p.name.split("-")[1])}
            for name, key in (("metrics.json", "metrics"),
                              ("remarks.json", "remarks"),
                              ("response.json", "response")):
                f = p / name
                if f.is_file():
                    got = json.loads(f.read_text(encoding="utf-8"))
                    if key == "metrics":
                        item["weighty"] = got.get("weighty")
                        item["E"] = got.get("E")
                    elif key == "remarks":
                        item["remarks"] = len(got.get("remarks", []))
                    else:
                        item["answers"] = len(got.get("answers", []))
            rounds.append(item)
    registry = {}
    reg_path = ex_dir / "registry.json"
    # Один загрузчик продукта (A07-F4: форма судится до показа; A07-F3:
    # старая техническая отбраковка замком кода читается живой —
    # счётчик состояний и сводка блокирующих судят уже мигрированный
    # реестр, файл на диске не переписывается).
    # Отказ формы — СЛОВАМИ в теле ответа, а не 500 (третье
    # неавтор-ревью, проба P4): прогон с негодным реестром прежде ронял
    # ручку, и панель показывала «Internal Server Error» вместо
    # названной причины. Fail-closed: счётчик пуст, сводки блокеров
    # нет, вердикт по реестру — отказ теми же словами, что у списка
    # прогонов (`_state_by_registry`).
    registry_error = None
    # A07-R3: недоступный реестр (каталог вместо файла, отказ доступа)
    # — такой же названный отказ 200-м ответом, как негодная форма, а
    # не пустой реестр и не 500.
    registry_unavailable = False
    # A07-R5: исчезнувший реестр после ИСПОЛНЕННОГО цикла — тот же
    # названный отказ 200-м ответом. Судит один предикат продукта:
    # слово состояния заявило исход ЛИБО в каталоге обмена лежит
    # вердикт цикла. У прогона без цикла (новый, «идёт» до первого
    # вердикта) требования носителя нет — пустой реестр законен.
    registry_missing = False
    try:
        reg = exchange_mod.load_registry(
            reg_path,
            expect_file=exchange_mod.registry_expected(
                ex_dir, _read_status(run_dir).get("state")))
    except exchange_mod.RegistryMissing as e:
        reg, registry_error = {}, str(e)
        registry_unavailable = registry_missing = True
    except exchange_mod.RegistryUnavailable as e:
        reg, registry_error, registry_unavailable = {}, str(e), True
    except exchange_mod.ExchangeError as e:
        reg, registry_error = {}, str(e)
    for f in reg.get("findings", []):
        registry[f["state"]] = registry.get(f["state"], 0) + 1
    # A07 (контракт П0 §1.2, миграция показа): записанный вердикт
    # старого цикла судится реестром при чтении — «успех» при живой
    # блокирующей/критической находке показывается как «не закрыто (по
    # реестру)»; файлы истории не переписываются.
    stored_verdict = None
    vp = ex_dir / "verdict.json"
    if vp.is_file():
        try:
            stored_verdict = json.loads(
                vp.read_text(encoding="utf-8")).get("verdict")
        except (OSError, ValueError):
            stored_verdict = None
    registry_verdict = exchange_mod.registry_verdict_of(reg) if reg else None
    verdict_by_registry = exchange_mod.verdict_by_registry(
        stored_verdict, reg) if stored_verdict is not None else None
    if registry_error is not None:
        registry_verdict = None
        verdict_by_registry = (
            f"{exchange_mod.VERDICT_NOT_CLOSED} (по реестру): "
            + ("реестр отсутствует после исполненного цикла"
               if registry_missing
               else "реестр недоступен" if registry_unavailable
               else "форма реестра не читается"))
    # Прогресс текущего раунда — счётом кэш-файлов чанков (директива
    # владельца 2026-08-20: индикация прогресса на блоках). Итог
    # аудита известен из заморозки (клауз / AUDIT_CHUNK), итог ответов
    # — из remarks.json раунда.
    progress = None
    if rdir.is_dir() and (run_dir / "status.json").is_file():
        st = _read_status(run_dir)
        if st.get("state") in ("аудит-цикл идёт",
                               "сквозной цикл идёт"):
            newest = sorted(rdir.glob("round-*"))
            if newest:
                cur = newest[-1]
                n_round = int(cur.name.split("-")[1])
                # Честный ход раунда пишет сам аудит (учитывает
                # инкрементальные пропуски чанков и не смешивает
                # кэш-файлы разных хэшей — прежний счёт давал «3/2»).
                ap = cur / "audit-progress.json"
                if ap.is_file():
                    got_ap = json.loads(ap.read_text(encoding="utf-8"))
                    a_done = got_ap.get("done", 0)
                    a_total = got_ap.get("total", 0)
                else:
                    clauses = (st.get("freeze_counters") or {}).get(
                        "clauses") or 0
                    a_total = ((clauses + exchange_mod.AUDIT_CHUNK - 1)
                               // exchange_mod.AUDIT_CHUNK) \
                        if clauses else 0
                    a_done = len(list(
                        (cur / "audit-cache").glob("*.json"))) \
                        if (cur / "audit-cache").is_dir() else 0
                phase = "аудит"
                done, total = a_done, a_total
                if (cur / "remarks.json").is_file():
                    got = json.loads((cur / "remarks.json")
                                     .read_text(encoding="utf-8"))
                    n_rem = len(got.get("remarks", []))
                    r_total = ((n_rem + exchange_mod.RESPOND_CHUNK - 1)
                               // exchange_mod.RESPOND_CHUNK)
                    r_done = len(list((cur / "respond-cache")
                                      .glob("*.json"))) \
                        if (cur / "respond-cache").is_dir() else 0
                    phase = "ответы"
                    done, total = r_done, r_total
                progress = {"round": n_round, "phase": phase,
                            "done": done, "total": total}
    verdict = None
    v_path = ex_dir / "verdict.json"
    if v_path.is_file():
        verdict = json.loads(v_path.read_text(encoding="utf-8"))
    st = _read_status(run_dir)
    return {"run": run_id, "state": st.get("state"),
            "config": st.get("exchange"),
            "error": st.get("error"),
            "gate_problems": st.get("exchange_problems"),
            "progress": progress,
            "rounds": rounds, "registry": registry, "verdict": verdict,
            "registry_verdict": registry_verdict,
            "verdict_by_registry": verdict_by_registry,
            # Причина отказа формы реестра — рядом со словами вердикта
            # (None, когда реестр читается): панель показывает её как
            # есть, не догадываясь.
            "registry_error": registry_error}


@app.post("/api/runs/{run_id}/arbiter")
@_journaled("arbiter", "scope")
def start_arbiter(run_id: str, scope: str | None = None):
    """Арбитраж тупиков аудит-цикла третьей независимой моделью
    (решение владельца 2026-08-18): по каждому тупику один из трёх
    вердиктов — «обработчик прав», «аудитор прав» (исполнение
    обязательно), «в анкету» (развилка двух чтений для авиакомпании).
    Сравнения с эталоном в продукте НЕТ — судья остался внутренним
    инструментом разработки (cdpm.judge, только командная строка)."""
    run_dir = _run_dir(run_id)
    if scope not in (None, "any", "local-only"):
        raise HTTPException(400, f"недопустимый scope {scope!r}")
    status = _read_status(run_dir)
    if _line_busy(status.get("state")):
        raise HTTPException(409, "прогон занят другой стадией")
    verdict_path = run_dir / "exchange" / "verdict.json"
    if not verdict_path.is_file():
        raise HTTPException(409, "сначала аудит-цикл: арбитр разбирает "
                                 "его тупики")
    verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
    if not verdict.get("deadlocks"):
        raise HTTPException(409, "тупиков нет — арбитраж не требуется")
    # Арбитраж читает материалы всех файлов — класс строжайшей
    # политики, как у цикла.
    klass = _strictest_class(run_dir)
    _claim_stage(run_dir, state="арбитраж идёт", arbiter_class=klass)
    _control_path(run_dir).unlink(missing_ok=True)
    control = _make_checkpoint(run_dir, "арбитраж", "арбитраж идёт")

    def _work():
        own: dict = {}
        try:
            rep = exchange_mod.run_arbiter(
                run_dir / "freeze", run_dir / "exchange",
                _channel_for(scope, run_dir, "arbiter", klass=klass),
                _channel_for(scope, run_dir, "replier", klass=klass),
                control=control)
            problems, counters = gate_arbiter.check(run_dir / "exchange")
            if not problems:
                # ИР-3: модель после арбитража — канонический свод.
                final = m_mod.loads_model(
                    (run_dir / "exchange" / "versions"
                     / "model-arbiter.json").read_text(encoding="utf-8"))
                problems = _sync_canon(run_dir, final, "арбитраж")
            summary = _arbiter_summary(rep)
            # A09: зелёный гейт при неразрешённых спорах — НЕ «всё
            # решено»: форма отчёта цела, но споры остались живыми, и
            # состояние обязано это сказать, а не молчать словом
            # «готов» (ревью A09, находка 3). Слова дописываются ПОСЛЕ
            # «арбитраж готов»: триггеры и водители сверяют состояние
            # началом строки (state.startswith), и автоподача заявок
            # каталога после арбитража продолжает срабатывать.
            n_unresolved = summary.get("unresolved") or 0
            n_awaiting = summary.get("awaiting_owner") or 0
            if problems:
                state_words = "красный гейт арбитража"
            elif n_unresolved:
                state_words = (f"арбитраж готов: не разрешено "
                               f"{n_unresolved}")
            elif n_awaiting:
                # §12.8: спор передан владельцу и живёт — «готово» без
                # оговорки значило бы, что делать нечего.
                state_words = (f"арбитраж готов: ждут ответа владельца "
                               f"{n_awaiting}")
            else:
                state_words = "арбитраж готов"
            own.update(
                state=state_words,
                error=None,
                arbiter_summary=summary,
                arbiter_counters=counters,
                arbiter_problems=problems)
        except StopRequested:
            own.update(state="остановлено: арбитраж", error=None)
        except Exception as e:                             # noqa: BLE001
            own.update(**_stage_failure("отказ арбитража", e))
        _update_status(run_dir, **own)

    _start_stage_thread(run_dir, _work, after=_catalog_auto_after(
        run_dir, run_id, _CATALOG_AFTER_ARBITER))
    return {"run": run_id, "state": "арбитраж идёт"}


@app.get("/api/runs/{run_id}/arbiter")
def arbiter_status(run_id: str):
    """Состояние арбитража: тупики цикла (до и после), вердикты с
    основаниями, счётчики по трём исходам, споры без однозначного
    решения (`report.unresolved`, A09), споры, ждущие ответа владельца
    (`report.awaiting_owner`, §12.8), и отбраковка исполнения."""
    run_dir = _run_dir(run_id)
    st = _read_status(run_dir)
    deadlocks = []
    v_path = run_dir / "exchange" / "verdict.json"
    if v_path.is_file():
        for f in json.loads(
                v_path.read_text(encoding="utf-8")).get("deadlocks", []):
            d = dict(f)
            # Ключ находки — каноническая строка JSON
            # [класс, строка, вопрос, якорь]: интерфейс показывает
            # человеку класс дефекта и адрес, не сырой ключ.
            try:
                kc = json.loads(f.get("key") or "null") or []
            except ValueError:
                kc = []
            d.setdefault("defect_class", kc[0] if kc else None)
            d.setdefault("rule", kc[1] if len(kc) > 1 else None)
            deadlocks.append(d)
    report = None
    rep_path = run_dir / "exchange" / "arbiter.json"
    if rep_path.is_file():
        report = json.loads(rep_path.read_text(encoding="utf-8"))
    return {"run": run_id, "state": st.get("state"),
            "deadlocks": deadlocks,
            "summary": st.get("arbiter_summary"),
            "gate_problems": st.get("arbiter_problems"),
            "error": st.get("error"),
            "report": report}


# ── Линия данных 3.3: заморозка данных → проверки → отчёт ──

# Ключи статуса, которые пишут стадии ПОВЕРХ замороженных данных
# (проверки 3.3 и сверка спорного): при изменении состава данных они
# больше не соответствуют данным и снимаются вместе с отчётом.
_DATA_OUTPUT_KEYS = ("data_class", "data_summary", "data_problems",
                     "data_counters", "data_review_summary",
                     "data_review_problems", "data_review_counters")


def _data_line_busy(status: dict) -> str | None:
    """Стадия линии данных, которая идёт или стоит на паузе (пауза
    продолжится и прочитает набор) — состав данных менять нельзя."""
    state = status.get("data_state") or ""
    if state in ("проверки идут", "сверка идёт") \
            or state.startswith("на паузе"):
        return state
    return None


def _invalidate_data_outputs(run_dir: Path) -> None:
    """Состав данных изменился — отчёт проверок, сводка сверки и
    «очищенные данные» (они строятся из отчёта на лету) больше не
    соответствуют данным: каталог отчёта удаляется, ключи стадий
    снимаются. Экспорт очищенных данных после этого честно отвечает
    409 «отчёта нет», а не отдаёт книгу по чужим находкам.

    Занятость линии данных судится ТОЙ ЖЕ операцией под замком, что
    снимает ключи (между проверкой и записью чужого старта не бывает).
    Порядок: сначала ключи статуса (и запись), потом каталоги —
    если удаление каталога на Windows сорвётся (открытый файл
    отчёта), статус уже не обещает отчёта, а повтор операции
    доудалит каталог."""
    def _apply(st: dict) -> None:
        busy = _data_line_busy(st)
        if busy:
            raise HTTPException(409, "линия данных занята другой "
                                     "стадией: " + busy)
        for key in _DATA_OUTPUT_KEYS:
            st.pop(key, None)
    _mutate_status(run_dir, _apply)
    for name in ("data-report", "data-review"):
        _rmtree_strict(run_dir / name, action="изменение данных")


def _refreeze_data(run_dir: Path) -> tuple[dict, dict]:
    """Пере-заморозка по ФАКТИЧЕСКОМУ составу data-src/: набор данных,
    манифест (SID пересчитываются по алфавиту имён), гейт заморозки.
    Возвращает (ключи статуса линии данных для слияния — None снимает
    ключ, счётчики). Пустой состав — заморозки нет вовсе: набор с
    нулём источников проходил бы гейт и пускал проверки «ни по чему»."""
    src_dir = run_dir / "data-src"
    files = sorted(p for p in src_dir.iterdir() if p.is_file()) \
        if src_dir.is_dir() else []
    if not files:
        _rmtree_strict(run_dir / "data-freeze",
                       action="изменение данных")
        return ({"data_files": [], "data_state": None,
                 "data_freeze_counters": None,
                 "data_freeze_problems": None},
                {"files": 0, "sheets": 0, "rows": 0})
    try:
        counters = data_freeze_mod.freeze_data(
            files, run_dir / "data-freeze")
        problems, gate_counters = gate_data_freeze.check(
            run_dir / "data-freeze", src_dir)
    except data_freeze_mod.DataFreezeError as e:
        raise HTTPException(400, str(e))
    keys = dict(
        data_files=[p.name for p in files],
        data_state="данные заморожены" if not problems
        else "красный гейт заморозки данных",
        data_freeze_counters=gate_counters,
        data_freeze_problems=problems)
    return keys, {**counters, "problems": problems}


def _drop_files_config_entry(run_dir: Path, name: str) -> bool:
    """Запись политики файла — из files-config.json (иначе она лежит
    призраком и при повторной загрузке файла с тем же именем молча
    воскрешает старую политику). Под замком прогона: параллельная
    правка политики другого файла не воскрешает запись."""
    with _status_guard(run_dir):
        path = _files_config_path(run_dir)
        if not path.is_file():
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
        files = payload.get("files", {})
        if name not in files:
            return False
        del files[name]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)
                        + "\n", encoding="utf-8")
        return True


@app.post("/api/runs/{run_id}/data")
async def upload_data(run_id: str, files: list[UploadFile]):
    """Загрузка книг данных: HTTP-слой — файлы в байты, остальное делает
    `_upload_data_bytes` (та же функция — у водителя повтора)."""
    incoming = [(Path(f.filename or "").name, await f.read())
                for f in (files or [])]
    return _upload_data_bytes(run_id, incoming)


def _upload_data_bytes(run_id: str, incoming: list):
    """Загрузка операционных данных прогона (xlsx / csv / json) +
    заморозка
    с манифестом SHA-256 + гейт заморозки данных. Состав данных
    изменился — выходы стадий поверх данных (отчёт проверок, сверка,
    очищенные данные) снимаются: см. `_invalidate_data_outputs`."""
    run_dir = _run_dir(run_id)
    status = _read_status(run_dir)
    busy = _data_line_busy(status)
    if busy:
        raise HTTPException(409, "линия данных занята другой стадией: "
                                 + busy)
    src_dir = run_dir / "data-src"
    for name, _body in incoming:
        if Path(name).suffix.lower() not in data_freeze_mod.DATA_FORMATS:
            raise HTTPException(
                400, f"{name}: формат не поддерживается — данные "
                     f"принимаются в {data_freeze_mod.DATA_FORMATS}")
    # Формат проверен у ВСЕХ файлов до первой записи: отказ по одному
    # файлу не оставляет на диске половину комплекта.
    n = actions_journal.note(
        run_dir, "data", sync=True,
        files=[actions_journal.file_entry(name, body, f"data-src/{name}")
               for name, body in incoming],
        state_before=_state_brief(status))
    # Действие закрывается записью об окончании, как у всех действий
    # панели (живая находка 10.09.2026, run-0033 на Test: запись «data»
    # без окончания читалась в записи о прогоне как «сбой/оборвано», и
    # state_from_journal словаря давал прогону «сбой»).
    t0 = time.monotonic()
    calls_before = call_journal.count(run_dir)
    try:
        _invalidate_data_outputs(run_dir)
        src_dir.mkdir(parents=True, exist_ok=True)
        for name, body in incoming:
            (src_dir / name).write_bytes(body)
        keys, got = _refreeze_data(run_dir)
        st = _update_status(run_dir, **keys)
    except Exception as e:                                # noqa: BLE001
        actions_journal.end(run_dir, n, state_after=_state_brief(_read_status(run_dir)),
                            duration_s=round(time.monotonic() - t0, 3),
                            calls=[calls_before, call_journal.count(run_dir)],
                            error=str(e)[:300])
        _refresh_run_record(run_dir)
        raise
    actions_journal.end(run_dir, n, state_after=_state_brief(st),
                        duration_s=round(time.monotonic() - t0, 3),
                        calls=[calls_before, call_journal.count(run_dir)])
    _refresh_run_record(run_dir)
    problems = got.pop("problems", [])
    return {"run": run_id, "state": st.get("data_state"),
            "counters": got, "problems": problems}


@app.delete("/api/runs/{run_id}/data/{filename}")
@_journaled("data-delete", "filename", sync=True)
def delete_data_file(run_id: str, filename: str):
    """Удаление файла операционных данных — каскадом по ВСЕМ местам,
    где он живёт (живой случай 2026-08-23: замена POLYARIS-DATA.xlsx
    на эталонах потребовала ручной правки четырёх мест, а файл без
    записи в конфигурации получал строжайшее умолчание «local» и
    уводил перегон в локальный канал):

    1. сам файл в data-src/;
    2. запись политики в files-config.json;
    3. элемент data_files (и счётчики заморозки) в status.json;
    4. источник (SID) в data-freeze/dataset.json + manifest.json —
       пере-заморозкой по оставшимся файлам.

    Выходы стадий поверх данных (отчёт проверок, сверка, очищенные
    данные) снимаются — данные им больше не соответствуют. Пока идёт
    или стоит на паузе стадия линии данных — отказ 409."""
    run_dir = _run_dir(run_id)
    name = Path(filename).name
    if not name or name != filename or name in (".", ".."):
        raise HTTPException(400, f"недопустимое имя файла {filename!r}")
    status = _read_status(run_dir)
    busy = _data_line_busy(status)
    if busy:
        raise HTTPException(409, "линия данных занята стадией — "
                                 "остановите её перед удалением "
                                 "файла: " + busy)
    path = run_dir / "data-src" / name
    if name not in status.get("data_files", []) and not path.is_file():
        raise HTTPException(404, f"файла данных {name!r} в прогоне "
                                 f"{run_id} нет")
    # Сначала снимаются выходы поверх данных, потом исчезает файл:
    # срыв на первом шаге не оставляет набор без оригинала.
    _invalidate_data_outputs(run_dir)
    removed = {"file": False, "files_config": False}
    if path.is_file():
        path.unlink()
        removed["file"] = True
    removed["files_config"] = _drop_files_config_entry(run_dir, name)
    keys, got = _refreeze_data(run_dir)
    st = _update_status(run_dir, **keys)
    problems = got.pop("problems", [])
    return {"run": run_id, "deleted": name, "removed": removed,
            "state": st.get("data_state"),
            "files": st.get("data_files", []),
            "counters": got, "problems": problems}


@app.post("/api/runs/{run_id}/data-run")
@_journaled("data-run", "scope")
def start_data_run(run_id: str, scope: str | None = None):
    """Проверки данных: компиляция спецификаций из строк свода
    (модель-компилятор — слот обработчика по СТРОЖАЙШЕМУ классу
    политики файлов прогона), детерминированное исполнение, отчёт 3.3
    и его гейт — в фоновом потоке."""
    run_dir = _run_dir(run_id)
    if scope not in (None, "any", "local-only"):
        raise HTTPException(400, f"недопустимый scope {scope!r}")
    status = _read_status(run_dir)
    if _line_busy(status.get("data_state")) \
            or _line_busy(status.get("state")):
        raise HTTPException(409, "прогон занят другой стадией")
    if not (run_dir / "extract" / "model.json").is_file():
        raise HTTPException(409, "сначала извлечение правил: проверки "
                                 "данных генерируются из строк свода")
    if not (run_dir / "data-freeze" / "dataset.json").is_file():
        raise HTTPException(409, "сначала загрузка данных: проверять "
                                 "нечего")
    klass = _strictest_class(run_dir)
    customer = status.get("customer") or "авиакомпания"
    _claim_stage(run_dir, state_key="data_state", also=("state",),
                 data_state="проверки идут", data_class=klass)
    _control_path(run_dir).unlink(missing_ok=True)
    control = _make_checkpoint(run_dir, "проверки данных",
                               "проверки идут",
                               state_key="data_state")

    def _work():
        own: dict = {}
        try:
            svod = m_mod.loads_model(
                (run_dir / "extract" / "model.json")
                .read_text(encoding="utf-8"))
            dataset = data_freeze_mod.load_dataset(
                run_dir / "data-freeze")
            llm = _channel_for(scope, run_dir, "data_compiler", None,
                               klass=klass, fallback_role="worker")
            specs = data_checks_mod.compile_checks(svod, dataset, llm,
                                                   control=control)
            specs += data_checks_mod.builtin_checks(dataset)
            # Свод — ради ответов анкеты: формы истории (1.18.3) читают
            # из них решение о нулевой истории у сотрудников без записи.
            doc = data_checks_mod.build_data_report(
                customer, dataset, specs, svod=svod)
            out = run_dir / "data-report"
            out.mkdir(parents=True, exist_ok=True)
            data_report_mod.dump_report(doc, out / "data-report.json")
            (out / "data-report.md").write_text(
                data_report_mod.render_data_report(doc),
                encoding="utf-8")
            (out / "checks.json").write_text(
                json.dumps({"checks": specs}, ensure_ascii=False,
                           indent=1) + "\n", encoding="utf-8")
            problems, counters = gate_data_report.check(
                out / "data-report.json",
                run_dir / "extract" / "model.json",
                out / "data-report.md")
            # Хвост Лайта 16: значения данных вне закрытого
            # перечня свода — вопрос части 3 следующей
            # анкеты (без блокировки правила).
            if not problems:
                model_doc = m_mod.load_model(
                    run_dir / "extract" / "model.json")
                created_q = m_mod.ensure_data_scope_questions(
                    model_doc, doc)
                # Правило без данных (решение владельца 2026-09-02):
                # правил, чьим проверкам не хватает данных книги, —
                # спросить авиакомпанию «считать ли до получения
                # данных?» (а/б); носители заблокированы до ответа,
                # дедупликация по канонному id.
                created_q += m_mod.ensure_missing_data_questions(
                    model_doc, doc)
                # Три исхода по истории сотрудников (1.17.2, решение
                # владельца 05.09): при зависимых строках ростеринга и
                # неполной/отсутствующей истории — вопрос части 3б
                # (дополнить/ввести либо продолжить по частичным/с нулями).
                # Состояние судится по ОЧИЩЕННЫМ данным — тем же, из которых
                # экспорт строит раздел пакета (строки с блокирующими
                # находками вычтены; иначе полнота вопроса и пакета расходятся).
                from cdpm import export_clean_data as _clean_mod
                _clean = json.loads(_clean_mod.export_json(dataset, doc))
                created_q += history_questions_mod.ensure_history_questions(
                    model_doc, {"tables": _clean.get("tables") or {}})
                # Ревизия 1.10 (живой случай одной из линий): снятый
                # конфликтом носитель с условием в формулировке —
                # вопрос-пересмотр «условное правило?» следующей
                # ревизии.
                created_q += m_mod.ensure_reinstate_questions(
                    model_doc)
                # Волна условий (этап 5б): темы файла условий, чьи
                # таблицы легли в модель обрывками, — вопросы анкеты
                # (ответ вносит обработчик; миграция таблицу не
                # угадывает).
                created_q += m_mod.ensure_conditions_questions(
                    model_doc)
                # Живой урок кнопки №15: конфликт чисел стыковки —
                # вопрос авиакомпании, не наихудшая догадка.
                created_q += m_mod.ensure_sit_conflict_question(
                    model_doc)
                # РВ-140 п. 1: ответы на вопросы линии данных (История),
                # снятые снимком до переизвлечения, — теперь, когда линия
                # данных родила вопросы заново.
                from cdpm import answer_carry as _carry_mod
                carried_dl = _carry_mod.carry_pending(
                    run_dir / "extract", model_doc)
                # Судьба ответов — и в статус (ревью GPT 07:30: иначе
                # статус оставался «ждёт линии данных»).
                _carry_now = _carry_mod.file_summary(run_dir / "extract")
                if _carry_now is not None:
                    _update_status(run_dir, answers_carry=_carry_now)
                if created_q or carried_dl:
                    bad_q = (m_mod.validate_schema(model_doc)
                             + m_mod.validate_model(model_doc))
                    if bad_q:
                        raise RuntimeError(
                            "вопросы по данным не прошли "
                            "контракт: " + "; ".join(bad_q[:3]))
                    ex_dir = run_dir / "extract"
                    m_mod.publish_model(model_doc,
                                     ex_dir / "model.json")
                    (ex_dir / "anketa.md").write_text(
                        render_mod.render_anketa(model_doc),
                        encoding="utf-8")
                    anketa_xlsx_mod.render_anketa_xlsx(
                        model_doc, ex_dir / "anketa.xlsx")
            m = data_report_mod.metrics_of(doc)
            own.update(
                data_state="отчёт готов" if not problems
                else "красный гейт отчёта данных",
                error=None,
                data_summary={
                    "findings": len(doc["findings"]),
                    "checks_run": m["checks_run"],
                    "checks_skipped": m["checks_skipped"],
                    "rows": m["rows_total"],
                    "by_severity": m["by_severity"],
                    "by_class": m["by_class"]},
                data_problems=problems,
                data_counters=counters)
        except StopRequested:
            own.update(data_state="остановлено: проверки данных",
                       error=None)
        except Exception as e:                             # noqa: BLE001
            own.update(**_stage_failure("отказ проверок данных", e,
                                       key="data_state"))
        _update_status(run_dir, **own)

    _start_stage_thread(run_dir, _work)
    return {"run": run_id, "state": "проверки идут", "class": klass}


@app.post("/api/runs/{run_id}/data-review")
@_journaled("data-review", "scope", "checks")
def start_data_review(run_id: str, scope: str | None = None,
                      checks: str | None = None):
    """Сверка спорного (ДЛ-7): модель-сверщик раскладывает находки
    готового отчёта 3.3 на три вердикта — в фоновом потоке, слот
    обработчика по СТРОЖАЙШЕМУ классу политики файлов прогона.
    `checks` сужает сверку до названных проверок (id через запятую)."""
    run_dir = _run_dir(run_id)
    if scope not in (None, "any", "local-only"):
        raise HTTPException(400, f"недопустимый scope {scope!r}")
    status = _read_status(run_dir)
    if _line_busy(status.get("data_state")) \
            or _line_busy(status.get("state")):
        raise HTTPException(409, "прогон занят другой стадией")
    report_path = run_dir / "data-report" / "data-report.json"
    if not report_path.is_file():
        raise HTTPException(409, "сначала проверки данных: сверять "
                                 "нечего — отчёта 3.3 нет")
    check_ids = [c.strip() for c in (checks or "").split(",")
                 if c.strip()] or None
    klass = _strictest_class(run_dir)
    _claim_stage(run_dir, state_key="data_state", also=("state",),
                 data_state="сверка идёт", data_class=klass)
    _control_path(run_dir).unlink(missing_ok=True)
    control = _make_checkpoint(run_dir, "сверка спорного",
                               "сверка идёт",
                               state_key="data_state")

    def _work():
        own: dict = {}
        try:
            svod = m_mod.loads_model(
                (run_dir / "extract" / "model.json")
                .read_text(encoding="utf-8"))
            report = data_report_mod.load_report(report_path)
            llm = _channel_for(scope, run_dir, "data_reviewer", None,
                               klass=klass, fallback_role="worker")
            summary = data_review_mod.run_review(
                svod, report, llm, check_ids, control=control)
            out = run_dir / "data-report"
            data_report_mod.dump_report(report,
                                        out / "data-report.json")
            (out / "data-report.md").write_text(
                data_report_mod.render_data_report(report),
                encoding="utf-8")
            (out / "review-summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2)
                + "\n", encoding="utf-8")
            problems, counters = gate_data_review.check(
                out / "data-report.json",
                out / "review-summary.json",
                run_dir / "extract" / "model.json",
                out / "data-report.md")
            own.update(
                data_state="спорное сверено" if not problems
                else "красный гейт сверки",
                error=None,
                data_review_summary={
                    "candidates": summary["candidates"],
                    "reviewed": summary["reviewed"],
                    "by_verdict": summary["by_verdict"],
                    "skipped": len(summary["skipped"])},
                data_review_problems=problems,
                data_review_counters=counters)
        except StopRequested:
            own.update(data_state="остановлено: сверка спорного",
                       error=None)
        except Exception as e:                             # noqa: BLE001
            own.update(**_stage_failure("отказ сверки спорного", e,
                                       key="data_state"))
        _update_status(run_dir, **own)

    _start_stage_thread(run_dir, _work)
    return {"run": run_id, "state": "сверка идёт", "class": klass}


@app.get("/api/runs/{run_id}/data")
def data_status(run_id: str):
    """Состояние линии данных: файлы, заморозка, сводка отчёта."""
    run_dir = _run_dir(run_id)
    st = _read_status(run_dir)
    return {"run": run_id,
            "state": st.get("data_state"),
            "files": st.get("data_files", []),
            "class": st.get("data_class"),
            "freeze_counters": st.get("data_freeze_counters"),
            "freeze_problems": st.get("data_freeze_problems"),
            "summary": st.get("data_summary"),
            "gate_problems": st.get("data_problems"),
            "review_summary": st.get("data_review_summary"),
            "review_problems": st.get("data_review_problems"),
            "error": st.get("error")}


_EXCHANGE_ROUND_FILES = {
    "замечания": "remarks.json",
    "ответы": "response.json",
    "метрика": "metrics.json",
    "отбраковка-аудита": "audit-rejections.json",
    "отбраковка-патчей": "patch-rejections.json",
}


@app.get("/api/runs/{run_id}/exchange/rounds/{round_no}/{alias}")
def exchange_round_file(run_id: str, round_no: int, alias: str):
    run_dir = _run_dir(run_id)
    name = _EXCHANGE_ROUND_FILES.get(alias)
    if name is None:
        raise HTTPException(404, f"неизвестный артефакт {alias!r}; "
                                 f"перечень: "
                                 f"{sorted(_EXCHANGE_ROUND_FILES)}")
    path = run_dir / "exchange" / "rounds" / f"round-{round_no:02d}" \
        / name
    if not path.is_file():
        raise HTTPException(404, "артефакт обмена ещё не построен")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


def _md_html(text: str) -> HTMLResponse:
    import markdown
    return HTMLResponse(
        "<meta charset='utf-8'><style>body{font-family:sans-serif;"
        "max-width:60em;margin:2em auto;padding:0 1em}table{border-"
        "collapse:collapse}td,th{border:1px solid #999;padding:4px "
        "8px;vertical-align:top}</style>" +
        markdown.markdown(text, extensions=["tables"]))


@app.get("/api/runs/{run_id}/exchange/svod/{version}")
def exchange_svod_version(run_id: str, version: int):
    run_dir = _run_dir(run_id)
    path = run_dir / "exchange" / "versions" \
        / f"svod-v{version:02d}.md"
    if not path.is_file():
        raise HTTPException(404, "версия свода не существует")
    return _md_html(path.read_text(encoding="utf-8"))


@app.get("/api/runs/{run_id}/exchange/anketa/{version}")
def exchange_anketa_version(run_id: str, version: int):
    """Анкета после обмена (пара к своду той же версии). Прогоны,
    сыгранные до этой точки, анкету версий не писали — она честно
    рендерится из модели версии."""
    run_dir = _run_dir(run_id)
    vdir = run_dir / "exchange" / "versions"
    path = vdir / f"anketa-v{version:02d}.md"
    if path.is_file():
        return _md_html(path.read_text(encoding="utf-8"))
    model_path = vdir / f"model-v{version:02d}.json"
    if not model_path.is_file():
        raise HTTPException(404, "версия анкеты не существует")
    doc = _load_model_or_409(model_path)
    return _md_html(render_mod.render_anketa(doc))


@app.get("/api/runs/{run_id}/export-clean-data")
def export_clean_data(run_id: str, fmt: str = "xlsx"):
    """ЭКС-3: очищенные операционные данные (нижний контур) —
    книга минус строки с адресными Б/К-находками отчёта проверок;
    отчёт очистки прилагается листом/секцией. Без отчёта проверок
    «очищенных» данных не существует — честный отказ."""
    from cdpm import export_clean_data as clean_mod
    run_dir = _run_dir(run_id)
    data_path = run_dir / "data-freeze" / "dataset.json"
    report_path = run_dir / "data-report" / "data-report.json"
    if not data_path.is_file():
        raise HTTPException(409, "книга операционных данных не "
                                 "загружена")
    if not report_path.is_file():
        raise HTTPException(409, "отчёта проверок данных нет — "
                                 "сначала прогоните проверки (3.3): "
                                 "очистка обоснована только их "
                                 "находками")
    dataset = json.loads(data_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if fmt == "json":
        body = clean_mod.export_json(dataset, report)
        return Response(
            body, media_type="application/json; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename='
                     f'"{run_id}-clean-data.json"'})
    if fmt != "xlsx":
        raise HTTPException(400, "fmt — xlsx или json")
    body = clean_mod.export_xlsx(dataset, report)
    return Response(
        body,
        media_type="application/vnd.openxmlformats-officedocument"
                   ".spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f'attachment; filename="{run_id}-clean-data.xlsx"'})


def _z3_problem() -> str | None:
    """Почему SMT-решатель недоступен на площадке (None — доступен).
    Помощник переехал в `cdpm.smt_verify` (11.09.2026): одни слова у
    кнопки панели и у гейта выпуска пакета — один носитель."""
    from cdpm import smt_verify as smt_mod
    return smt_mod.z3_problem()


def _smt_gate_channel(run_dir):
    """Канал переводчика SMT для РУБЕЖА непротиворечивости при выпуске
    пакета постановки (`export_task_package.consistency_of`): та же
    роль, тот же класс данных и тот же журнал вызовов, что у кнопки
    «Проверить непротиворечивость», — вызов гейта считается в записи о
    прогоне наравне с остальными вызовами моделей."""
    from cdpm import smt_verify as smt_mod
    return _channel_for(None, run_dir, smt_mod.TRANSLATOR_ROLE, None,
                        klass=_strictest_class(run_dir))


def _smt_gate_scope(run_dir):
    """ОБЛАСТЬ проверки непротиворечивости при выпуске пакета
    (`export_task_package.smt_check_scope`) — действие журнала прогона.

    Имя действия — то же «smt-verify», что у кнопки панели: один
    носитель, и в записи о прогоне проверка читается ОДНОЙ стадией,
    кем бы она ни была начата; параметр `origin` называет начавшего.
    Ручки выпуска пакета (`export_for_solver`) сами действием не
    журналируются — без этой области вызов переводчика ложился в
    журнал вызовов без номера действия и в записи о прогоне не
    считался (живая находка run-0163 11.09.2026: 26 сочтённых вызовов
    из 27).

    Каталог без `status.json` — НЕ прогон панели (стенд сборщика, гейт
    производителя, проверка замороженного пакета): журналу нечего
    вести, записи о прогоне нет, область пустая и проверка идёт, как
    шла."""
    run_dir = Path(run_dir)
    if not _status_path(run_dir).is_file():
        return nullcontext()
    return _smt_gate_action(run_dir)


@contextmanager
def _smt_gate_action(run_dir: Path):
    """Само действие журнала по дисциплине `_journaled` для
    синхронного действия: строка `sync` до проверки, `refused` при
    отказе рубежа, `end` с диапазоном вызовов и обновлением записи о
    прогоне при успехе. Номер действия обёртка канала снимает из
    контекста при ПОСТРОЕНИИ канала — канал строится внутри области."""
    n = actions_journal.note(
        run_dir, "smt-verify",
        params={"scope": None, "origin": actions_journal.SMT_GATE_ORIGIN},
        sync=True, state_before=_state_brief(_status_if_any(run_dir)))
    c0 = call_journal.count(run_dir)
    t0 = time.monotonic()
    try:
        yield n
    except HTTPException as e:
        actions_journal.refused(run_dir, n, e.status_code, str(e.detail))
        raise
    except Exception as e:                                # noqa: BLE001
        # Отказ рубежа выходит наружу 409 со словами и машинным кодом
        # (`_task_package_refusal`) — тем же кодом он записан и здесь.
        actions_journal.refused(run_dir, n, 409,
                                str(getattr(e, "detail", None) or e)[:300],
                                code=getattr(e, "code", None),
                                items=getattr(e, "items", None))
        raise
    finally:
        actions_journal.set_current(None)
    actions_journal.end(
        run_dir, n, state_after=_state_brief(_status_if_any(run_dir)),
        duration_s=round(time.monotonic() - t0, 3),
        calls=[c0, call_journal.count(run_dir)])
    _refresh_run_record(run_dir)


@app.post("/api/runs/{run_id}/smt-verify")
@_journaled("smt-verify", "scope")
def start_smt_verify(run_id: str, scope: str | None = None):
    """СМТ-1: машинная проверка непротиворечивости свода через Z3 —
    в фоновом потоке (перевод правил в SMT делает языковая модель,
    вердикт и ядро конфликта — решатель). Результат — в статусе
    (smt_state/smt_report); состояние прогона не занимается: проверка
    только читает канонический свод."""
    from cdpm import smt_verify as smt_mod
    run_dir = _run_dir(run_id)
    model_path = run_dir / "extract" / "model.json"
    if not model_path.is_file():
        raise HTTPException(409, "модель ещё не построена — сначала "
                                 "извлечение")
    status = _read_status(run_dir)
    if status.get("smt_state") == "SMT-проверка идёт":
        raise HTTPException(409, "SMT-проверка уже идёт")
    if scope not in (None, "any", "local-only"):
        raise HTTPException(400, f"недопустимый scope {scope!r}")
    # Дефект с демо 06.09 (Prod run-9013): на площадке без пакета
    # z3-solver фоновая нить падала с «No module named 'z3'», и отказ
    # читался только из статуса. Наличие решателя проверяется ДО старта
    # — отказ словами в ответе и в статусе, состояние прогона не
    # занимается.
    z3_problem = _z3_problem()
    if z3_problem:
        _update_status(run_dir, smt_state="отказ SMT-проверки",
                       smt_report={"status": z3_problem})
        raise HTTPException(409, z3_problem)
    try:
        model_bytes = model_path.read_bytes()
        doc = m_mod.loads_model(model_bytes)
    except (ValueError, OSError) as error:
        _update_status(run_dir, smt_state="отказ SMT-проверки")
        raise HTTPException(409, f"модель не удалось прочитать: {error}") from error
    _update_status(run_dir, smt_state="SMT-проверка идёт")
    try:
        llm = _channel_for(scope, run_dir, "smt_translator", None,
                           klass=_strictest_class(run_dir))
    except Exception as e:                                # noqa: BLE001
        _update_status(run_dir, smt_state="отказ SMT-проверки")
        raise HTTPException(409, f"канал не построился: {e}") \
            from None

    def _work():
        # Точечное обновление своих ключей: снимок в начале долгой
        # стадии затирал результаты параллельных стадий (ревью
        # ступени 1, 2026-08-22).
        try:
            # Отпечаток свода в отчёте (11.09.2026): им гейт выпуска
            # пакета узнаёт, о ТОМ ЛИ своде вердикт кнопки, и не
            # переспрашивает модель зря.
            report = smt_mod.verify(
                doc, llm,
                model_sha256=smt_mod.model_fingerprint(model_bytes))
            smt_mod.write_report(run_dir, report)
            _update_status(
                run_dir, smt_state="SMT-проверка готова",
                smt_report={k: report[k] for k in
                            ("status", "verdict", "core",
                             "n_rules", "n_translated",
                             "n_untranslatable", "untranslated",
                             # A01-F2: записи вне формы в ответе
                             # переводчика — позиция и вид.
                             "malformed")
                            if k in report})
        except Exception as e:                             # noqa: BLE001
            _update_status(run_dir, smt_state="отказ SMT-проверки",
                           smt_report={"status": str(e)[:300]})

    _start_stage_thread(run_dir, _work)
    return {"run": run_id, "smt_state": "SMT-проверка идёт"}


# ── Заявки в каталог шаблонов из посадок категории C (шаг 6 каталога,
# часть CDPM; проект KATALOG-SHAG-6-CDPM-ZAYAVKI-PROEKT-CLD.md) ────────
# Побочная линия `catalog_state`: составление и подача не занимают
# основную линию правил, но одна операция за раз (_claim_stage). В
# `catalog/**` панель не пишет никогда — запись каталога у COSMOS.

_CATALOG_MODES = ("auto", "all", "one", "confirm", "withdraw", "resend",
                  "deliver", "recheck")
#: Параметры действия журнала `catalog-request` (§1.4 проекта).
_CATALOG_KEYS = ("rule_ids", "request_ids", "document_class", "mode",
                 "confirm_service_c", "trigger", "auto_setting")
#: Итоговые состояния стадий, обновивших канонический свод: после них
#: строки перестают меняться, и заявка рождается автоматически (§1.2).
_CATALOG_AFTER_EXCHANGE = ("аудит-цикл: ", "аудит остановлен: ")
_CATALOG_AFTER_FULL_CYCLE = ("сквозной цикл: ",)
_CATALOG_AFTER_ARBITER = ("арбитраж готов",)


def _plain(v, default=None):
    """Значение аргумента ручки без обёртки FastAPI: эту ручку зовут и
    напрямую (автозапуск после стадии, водитель повтора) — тогда
    умолчание `Body(...)` осталось бы объектом-описателем."""
    from fastapi import params as _fp
    return default if isinstance(v, (_fp.Param, _fp.Body)) else v


def _catalog_model_doc(run_dir: Path):
    path = run_dir / "extract" / "model.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


#: Отказ строки-кандидата, когда живые находки прочитать нечем
#: (третье неавтор-ревью, проба P4): спор посадки судится реестром, а
#: негодная форма реестра — незнание, не «споров нет».
CATALOG_REGISTRY_REFUSAL = ("форма реестра находок обмена не читается — "
                            "спор посадки судить нечем, заявка отказана")
#: A07-R3: тот же отказ, когда реестр НЕДОСТУПЕН (каталог вместо файла,
#: отказ доступа, сбой чтения). Прежде ошибка чтения давала пустой
#: перечень находок, и оспоренная строка становилась кандидатом заявки
#: — незнание работало доказательством отсутствия спора.
CATALOG_REGISTRY_UNAVAILABLE = ("реестр находок обмена недоступен — "
                                "спор посадки судить нечем, заявка "
                                "отказана")
#: A07-R5: тот же отказ, когда реестр ИСЧЕЗ после заявленного исхода
#: цикла. Пустой перечень находок означал бы «споров посадки нет», и
#: исчезновение носителя отправляло бы оспоренную строку заявкой в
#: каталог шаблонов — незнание работало бы доказательством чистоты.
CATALOG_REGISTRY_MISSING = ("реестр находок обмена отсутствует после "
                            "исполненного цикла — спор посадки судить "
                            "нечем, заявка отказана")


def _is_registry_refusal(reason) -> bool:
    """Отказ строки-кандидата вызван нечитаемым реестром (форма,
    недоступность либо исчезновение после исполненного цикла) — один
    предикат для кнопки, автоподачи и панели."""
    words = str(reason or "")
    return words.startswith(CATALOG_REGISTRY_REFUSAL) \
        or words.startswith(CATALOG_REGISTRY_UNAVAILABLE) \
        or words.startswith(CATALOG_REGISTRY_MISSING)


def _catalog_rows(run_dir: Path, doc) -> list:
    """Кандидаты заявки — свежим счётом по текущей модели; спор посадки
    судится ЖИВЫМИ находками реестра обмена (ядро заявок `webui` не
    импортирует — находки приходят параметром).

    Негодная форма реестра (A07-F4) — отказ СЛОВАМИ, а не пустой
    перечень находок: пустой перечень означал «споров посадки нет», и
    оспоренная строка уезжала заявкой в каталог шаблонов (fail-open).
    Строки возвращаются все, но ни одна не кандидат — панель показывает
    у каждой «не кандидат: …» тем же полем `reason`, что и у прочих
    отказов (RunPage.tsx), автоподача видит «кандидатов нет», а кнопка
    строки отказывает теми же словами. Сборка UI не нужна."""
    if not doc:
        return []
    refusal = None
    try:
        # A07-R5: цикл исполнен — реестр обязан быть; судит один
        # предикат продукта `exchange.registry_expected` (слово
        # состояния ИЛИ вердикт цикла в каталоге обмена), общий для
        # строки, автоподачи и кнопки — через `_read_registry`. Прогон
        # без цикла требования носителя не несёт.
        findings = _live_findings(run_dir)
    except exchange_mod.RegistryMissing as e:
        # A07-R5: реестр исчез после исполненного цикла — отказ теми же
        # словами, что и недоступность, но с названной причиной.
        findings, refusal = [], f"{CATALOG_REGISTRY_MISSING}: {e}"
    except exchange_mod.RegistryUnavailable as e:
        # A07-R3: реестр недоступен — отказ теми же словами, что и
        # негодная форма. Пустой перечень находок означал бы «споров
        # посадки нет», и оспоренная строка уезжала бы заявкой.
        findings, refusal = [], f"{CATALOG_REGISTRY_UNAVAILABLE}: {e}"
    except exchange_mod.ExchangeError as e:
        findings, refusal = [], f"{CATALOG_REGISTRY_REFUSAL}: {e}"
    except (OSError, ValueError) as e:
        findings, refusal = [], f"{CATALOG_REGISTRY_UNAVAILABLE}: {e}"
    rows = catreq_mod.candidates(doc, run_dir, findings)
    if refusal is not None:
        for row in rows:
            row["candidate"] = False
            row["reason"] = refusal
    return rows


def _catalog_document_class(run_dir: Path, requested=None) -> str:
    """Класс документа: просьба аналитика, иначе запомненный в статусе,
    иначе умолчание ВРЕМЕННОГО перечня (словаря `document_classes` в
    каталоге пока нет — вопрос COSMOS К10)."""
    st = _read_status(run_dir)
    kept = (st.get("catalog_requests") or {}).get("document_class") \
        if isinstance(st.get("catalog_requests"), dict) else None
    value = requested or kept or catreq_mod.DEFAULT_DOCUMENT_CLASS
    if value not in catreq_mod.DOCUMENT_CLASSES:
        raise HTTPException(
            400, f"класс документа {value!r} вне перечня CDPM: "
                 + "; ".join(catreq_mod.DOCUMENT_CLASSES))
    return value


def _last_catalog_action(run_dir: Path) -> dict | None:
    """Источник и время последнего составления — ИЗ ЖУРНАЛА ДЕЙСТВИЙ
    (в индексе их нет: индекс детерминирован и входит в сверку повтора
    байт в байт)."""
    last = None
    for a in actions_journal.actions(run_dir):
        if a.get("action") == "catalog-request" and not a.get("refused"):
            last = a
    if last is None:
        return None
    p = last.get("params") or {}
    return {"trigger": p.get("trigger"), "mode": p.get("mode"),
            "auto_setting": p.get("auto_setting"), "at": last.get("at"),
            "catalog_state": (last.get("state_after") or {})
            .get("catalog_state")}


def _catalog_todo(rows: list, idx_rows: dict, retry: bool = False) -> list:
    """Строки, для которых составление имеет смысл: без записи в
    индексе, с изменившейся формулировкой либо (по кнопке) с прежним
    отказом составления."""
    out = []
    for row in rows:
        if not row["candidate"]:
            continue
        got = idx_rows.get(row["rule_id"])
        if not isinstance(got, dict):
            out.append(row)
        elif got.get("key") != row["key"]:
            out.append(row)
        elif retry and got.get("composed") == catreq_mod.COMPOSED_FAILED:
            out.append(row)
    return out


def _catalog_auto_reason(run_dir: Path, st: dict, rows: list) -> str | None:
    """Почему автозапуска не было (None — сработает). Причина
    ВЫЧИСЛЯЕТСЯ при чтении: в индексе её нет."""
    from cdpm import replay as replay_mod
    if not catreq_mod.auto_enabled():
        return f"автоподача выключена ({catreq_mod.ENV_AUTO}=0)"
    if replay_mod.replay_of(st):
        return "потомок повтора — действие приходит от водителя"
    blocked = catreq_mod.blocked_words()
    if blocked:
        return blocked
    idx_rows = (catreq_mod.read_index(run_dir).get("rows") or {})
    if not _catalog_todo(rows, idx_rows):
        # A07 (четвёртое неавтор-ревью, находка 4): когда КАЖДАЯ строка
        # отказана негодной формой реестра, «кандидатов нет» — ложь по
        # той же причине, по какой кандидатов обнулили: судить спор
        # посадки нечем. Панель показывает отказ теми же словами, что и
        # строка.
        refused = [r.get("reason") for r in rows
                   if not r.get("candidate")
                   and _is_registry_refusal(r.get("reason"))]
        if rows and len(refused) == len(rows):
            return refused[0]
        return "кандидатов нет"
    for key in ("state", "catalog_state"):
        if _line_busy(st.get(key)):
            return f"линия занята: {st.get(key)}"
    return None


@app.get("/api/runs/{run_id}/catalog-requests")
def catalog_requests_status(run_id: str):
    """Данные раздела «Пробелы каталога шаблонов» блока «Итог»:
    кандидаты текущей модели, заявки ОТ ИНДЕКСА (заявка живёт дольше
    строки C), состояние канала, признак автоподачи и последнее
    составление — из журнала действий."""
    run_dir = _run_dir(run_id)
    st = _read_status(run_dir)
    doc = _catalog_model_doc(run_dir)
    rows = _catalog_rows(run_dir, doc)
    # Рубеж каталога назван у места: состояния заявок читаются ИЗ
    # каталога, и без него разделу нечего показывать — 503 словами
    # (тот же перевод, что у обработчика отказа приложения).
    try:
        cat = catalog_bridge.catalog()
    except catalog_bridge.CATALOG_ERRORS as e:
        raise catalog_unavailable(e) from None
    outbox = catreq_mod.outbox_root()
    index = catreq_mod.read_index(run_dir)
    service = catreq_mod.service_configured()
    return {
        "run": run_id,
        "candidates": rows,
        "requests": catreq_mod.states(run_dir, cat, outbox, rows),
        "counts": catreq_mod.counts_of(rows, index),
        "document_classes": list(catreq_mod.DOCUMENT_CLASSES),
        "document_class": _catalog_document_class(run_dir),
        "channel": {"service": service, "queue": str(outbox),
                    "url": catreq_mod.service_url() or None,
                    "transitional": not service,
                    "pending": (catreq_mod.deliverable(run_dir, outbox)
                                if service else []),
                    # Слова канала — по §8 п.6 контракта COSMOS:
                    # переходный канал назван поимённо, а не «стадия 4».
                    "words": (f"доставка в службу каталога настроена: "
                              f"{catreq_mod.service_url()}"
                              f"{catreq_mod.SERVICE_REQUESTS_PATH}"
                              if service else catreq_mod.service_off_words())},
        "catalog": catalog_bridge.stamp(),
        "auto": {"enabled": catreq_mod.auto_enabled(),
                 "skipped_reason": _catalog_auto_reason(run_dir, st, rows)},
        "last_action": _last_catalog_action(run_dir),
        "line": st.get("catalog_state"),
        "blocked": catreq_mod.blocked_words(),
    }


@app.post("/api/runs/{run_id}/catalog-requests")
@_journaled("catalog-request", *_CATALOG_KEYS)
def submit_catalog_requests(run_id: str,
                            rule_ids: list | None = Body(None),
                            request_ids: list | None = Body(None),
                            document_class: str | None = Body(None),
                            mode: str = Body("all"),
                            confirm_service_c: bool = Body(False),
                            trigger: str = "button",
                            auto_setting: str | None = None):
    """Одно действие журнала `catalog-request` для кнопки, автозапуска и
    повтора. Ручка исполняет ЛЮБОЙ `mode` безусловно: условия
    автозапуска судит только `after` стадии (иначе воспроизведённое
    действие с `mode: "auto"` отказало бы под
    `CDPM_CATALOG_REQUESTS_AUTO=0`). Её рубежи — занятость линий и
    доступность каталога."""
    run_dir = _run_dir(run_id)
    rule_ids = _plain(rule_ids) or []
    request_ids = _plain(request_ids) or []
    document_class = _plain(document_class)
    mode = _plain(mode, "all") or "all"
    confirm_service_c = bool(_plain(confirm_service_c, False))
    if mode not in _CATALOG_MODES:
        raise HTTPException(400, f"режим {mode!r} вне перечня: "
                                 + ", ".join(_CATALOG_MODES))
    replay = trigger == "replay"
    outbox = catreq_mod.outbox_root()
    doc_class = _catalog_document_class(run_dir, document_class)
    if document_class:
        _update_status(run_dir, catalog_requests={"document_class": doc_class})
    idx_rows = dict(catreq_mod.read_index(run_dir).get("rows") or {})

    # ── доставка в службу каталога: индекс НЕ трогается (§1.3 — он
    # детерминирован и сверяется повтором байт в байт); момент, код и
    # слова службы ложатся квитанцией в `channel/catalog-requests/` ──
    if mode == "deliver":
        busy = _read_status(run_dir).get("catalog_state")
        if _line_busy(busy):
            raise HTTPException(409, f"прогон занят другой стадией: {busy}")
        got = catreq_mod.deliver(run_dir, request_ids or None, outbox,
                                 replay=replay)
        if got.get("refusal") and not replay:
            # Отказ доставки — строка `refused` журнала (её пишет
            # обёртка `_journaled`), а не молчаливый успех.
            raise HTTPException(409, got["refusal"])
        n = actions_journal.current()
        if n is not None:
            actions_journal.end(
                run_dir, n,
                state_after=_state_brief(_status_if_any(run_dir)))
            _refresh_run_record(run_dir)
        return {"run": run_id, "mode": mode,
                "delivered": got["delivered"], "refused": got["refused"],
                "note": got.get("note"), "words": got.get("refusal"),
                "requests": [{"request_id": r.get("request_id"),
                              "delivered": bool(r.get("delivered")),
                              "http": r.get("http"),
                              "refusal": r.get("refusal"),
                              "words": r.get("words"),
                              "state": r.get("state_words")}
                             for r in got["receipts"]]}

    # ── сверка карантина со службой (§4.3): подачи НЕТ, очередь и
    # индекс не трогаются — спрашивается карточка заявки по ключу ──
    if mode == "recheck":
        busy = _read_status(run_dir).get("catalog_state")
        if _line_busy(busy):
            raise HTTPException(409, f"прогон занят другой стадией: {busy}")
        got = catreq_mod.recheck(run_dir, request_ids or None, outbox,
                                 replay=replay)
        if got.get("refusal") and not replay:
            raise HTTPException(409, got["refusal"])
        n = actions_journal.current()
        if n is not None:
            actions_journal.end(
                run_dir, n,
                state_after=_state_brief(_status_if_any(run_dir)))
            _refresh_run_record(run_dir)
        return {"run": run_id, "mode": mode,
                "confirmed": got["confirmed"],
                "unconfirmed": got["unconfirmed"],
                "note": got.get("note"), "words": got.get("refusal"),
                "requests": [{"request_id": r.get("request_id"),
                              "outcome": r.get("outcome"),
                              "delivered": bool(r.get("delivered")),
                              "http": r.get("http"),
                              "known": r.get("known"),
                              "words": r.get("words"),
                              "state": r.get("state_words")}
                             for r in got["receipts"]]}

    # ── синхронные режимы: модель не зовётся, линия не занимается ──
    if mode in ("withdraw", "resend"):
        busy = _read_status(run_dir).get("catalog_state")
        if _line_busy(busy):
            # Индекс пишут обе стороны: составление в фоне и отзыв в
            # запросе — их записи не должны затирать друг друга.
            raise HTTPException(409, f"прогон занят другой стадией: {busy}")
        try:
            cat = catalog_bridge.catalog()
        except catalog_bridge.CATALOG_ERRORS as e:
            raise catalog_unavailable(e) from None
        touched = []
        for rid in [str(x) for x in request_ids]:
            if mode == "withdraw":
                got = catreq_mod.withdraw(rid, run_dir, outbox, cat,
                                          replay=replay)
                composed = catreq_mod.COMPOSED_WITHDRAWN
            else:
                got = catreq_mod.resend(rid, run_dir, outbox, cat,
                                        replay=replay)
                composed = catreq_mod.COMPOSED_SUBMITTED
            if not got.get("ok"):
                raise HTTPException(409, got.get("words") or "отказано")
            for rule_id, row in list(idx_rows.items()):
                if isinstance(row, dict) and row.get("request_id") == rid:
                    # Слова очереди в индекс не идут: он сверяется
                    # байт в байт (§1.3–1.4 проекта).
                    idx_rows[rule_id] = catreq_mod.index_row(
                        row.get("key"), rid, composed, None,
                        row.get("document_class") or doc_class)
                    touched.append(rule_id)
        catreq_mod.write_index(run_dir, {"form": 1, "rows": idx_rows})
        n = actions_journal.current()
        if n is not None:
            actions_journal.end(
                run_dir, n,
                state_after=_state_brief(_status_if_any(run_dir)))
            _refresh_run_record(run_dir)
        return {"run": run_id, "mode": mode, "rows": touched}

    # ── фоновые режимы: составление, гейт, подача ──
    doc = _catalog_model_doc(run_dir)
    if doc is None:
        raise HTTPException(409, "модель ещё не построена — сначала "
                                 "извлечение")
    # Каталог нужен ВСЕМ фоновым режимам, подтверждению тоже (K08):
    # Г5 судит по каталогу и у «Подтвердить и подать» — прежде
    # подтверждение обходило этот рубеж и подавало вслепую.
    blocked = catreq_mod.blocked_words()
    if blocked:
        raise HTTPException(409, blocked)
    rows = _catalog_rows(run_dir, doc)
    by_rule = {r["rule_id"]: r for r in rows}
    todo: list = []
    if mode in ("auto", "all"):
        todo = _catalog_todo(rows, idx_rows, retry=(mode == "all"))
    elif mode == "one":
        for rule_id in [str(x) for x in rule_ids]:
            row = by_rule.get(rule_id)
            if row is None:
                raise HTTPException(404, f"строки {rule_id} с посадкой C в "
                                         f"модели нет")
            # A07 (четвёртое неавтор-ревью, находка 3): подтверждение
            # «это пробел каталога, не брак посадки» снимает СПОР
            # посадки, а не незнание — при негодной форме реестра спор
            # судить нечем, и подтверждать нечего: отказ теми же
            # словами, что и у строки.
            if not row["candidate"] and _is_registry_refusal(
                    row.get("reason")):
                raise HTTPException(409, f"{rule_id}: {row['reason']}")
            if not row["candidate"] and not confirm_service_c:
                raise HTTPException(
                    409, f"{rule_id}: {row['reason']} — заявить можно только "
                         f"с подтверждением «это пробел каталога, не брак "
                         f"посадки»")
            todo.append(row)
    if mode != "confirm" and not todo:
        raise HTTPException(409, "незаявленных кандидатов нет")
    if mode == "confirm" and not request_ids:
        raise HTTPException(400, "подтверждение требует номеров заявок")

    st = _read_status(run_dir)
    _claim_stage(run_dir, "catalog_state", also=("state",),
                 catalog_state=catreq_mod.LINE_RUNNING)

    source_dir = None
    if replay:
        from cdpm import replay as replay_mod
        rep = replay_mod.replay_of(st) or {}
        source_dir = Path(rep["source_dir"]) if rep.get("source_dir") \
            else run_dir.parent / str(rep.get("source_run") or "")

    def _work():
        counts = {"submitted": 0, "held": 0, "failed": 0}
        refusal = None
        try:
            if mode == "confirm":
                _catalog_confirm(run_dir, outbox, idx_rows, request_ids,
                                 doc_class, counts, replay)
            else:
                _catalog_compose(run_dir, doc, st, todo, idx_rows, doc_class,
                                 outbox, counts, replay, source_dir)
        except HTTPException as e:                        # noqa: BLE001
            refusal = str(e.detail)[:300]
        except Exception as e:                            # noqa: BLE001
            refusal = str(e)[:300]
        finally:
            try:
                catreq_mod.write_index(run_dir, {"form": 1, "rows": idx_rows})
            except OSError as e:                          # noqa: BLE001
                refusal = refusal or f"индекс заявок не записан: {e}"
            _update_status(
                run_dir,
                catalog_state=catreq_mod.line_outcome(counts, refusal))

    _start_stage_thread(run_dir, _work)
    return {"run": run_id, "mode": mode,
            "catalog_state": catreq_mod.LINE_RUNNING,
            "rows": [r["rule_id"] for r in todo]}


def _catalog_confirm(run_dir: Path, outbox, idx_rows: dict, request_ids,
                     doc_class: str, counts: dict, replay: bool) -> None:
    """«Подтвердить и подать» удержанный черновик: модель не зовётся,
    файл переносится из `held/` и публикуется в очередь.

    Закон Г5 ОДИН для составления и для подтверждения (K08, контракт П0
    §4.2): между удержанием черновика и подтверждением владелец каталога
    мог решить, и подтверждение судит каталог ЗАНОВО, на момент нажатия
    кнопки. Запись «отклонена» — отказ словами решения и путём
    пересмотра (черновик остаётся удержанным: решение пересматривают на
    экране каталога, и подтвердить можно после). Иное состояние —
    сведение: запись ложится в папку прогона, а в очередь заявка
    повторно не публикуется — та же заявка не подаётся дважды."""
    # Каталог обязателен так же, как при составлении: без него Г5 не
    # судит, а подать «вслепую» — значит повторно опубликовать номер,
    # который каталог мог уже закрыть.
    try:
        cat = catalog_bridge.catalog()
    except catalog_bridge.CATALOG_ERRORS as e:
        raise HTTPException(503, catalog_unavailable_body(e)["error"]) from None
    held_dir = catreq_mod.run_requests_dir(run_dir) / catreq_mod.HELD_DIR_NAME
    for rid in [str(x) for x in request_ids]:
        path = held_dir / f"{rid}.json"
        if not path.is_file():
            raise HTTPException(404, f"удержанного черновика {rid} нет")
        request = json.loads(path.read_bytes().decode("utf-8"))
        known = catreq_mod.known_of(request, cat)
        if (known or {}).get("closed"):
            raise HTTPException(409, known["words"])
        got = catreq_mod.enqueue(request, run_dir, outbox, replay=replay,
                                 known=known)
        # Сведение Г5 не теряется на пути «удержан → подтверждён»:
        # почему заявка в очередь не ушла, видно в строке индекса. Слова
        # КАТАЛОГА (известный номер) потомок повтора воспроизводит —
        # они от записи; слова ОЧЕРЕДИ — нет (тот же закон, что у
        # составления: индекс сверяется байт в байт, §1.3).
        note = got.get("words") if known or not replay else None
        path.unlink(missing_ok=True)
        counts["submitted"] += 1
        for rule_id, row in list(idx_rows.items()):
            if isinstance(row, dict) and row.get("request_id") == rid:
                idx_rows[rule_id] = catreq_mod.index_row(
                    row.get("key"), rid, catreq_mod.COMPOSED_CONFIRMED, note,
                    row.get("document_class") or doc_class)


def _catalog_compose(run_dir: Path, doc: dict, st: dict, todo: list,
                     idx_rows: dict, doc_class: str, outbox, counts: dict,
                     replay: bool, source_dir) -> None:
    """Составление черновиков моделью, гейт, запись и подача.

    Модель зовётся один раз на строку плюс не более двух повторов со
    словами отказа; жёсткий отказ после повторов — «черновик не
    составлен», мягкое замечание — удержание до подтверждения аналитика
    (не тупик)."""
    keys = {r["request_id"]: r["key"] for r in todo}
    by_request: dict = {}
    for r in todo:
        by_request.setdefault(r["request_id"], []).append(r["rule_id"])

    def _all_failed(words: str) -> None:
        for rid, rule_ids in by_request.items():
            counts["failed"] += 1
            for rule_id in rule_ids:
                idx_rows[rule_id] = catreq_mod.index_row(
                    keys.get(rid), rid, catreq_mod.COMPOSED_FAILED, words,
                    doc_class)

    # Каталог обязателен (шаг 7): без него составлять заявку не из чего
    # — все черновики строки помечаются несостоявшимися СЛОВАМИ, линия
    # прогона получает ту же причину (нить ловит отказ выше).
    try:
        cat = catalog_bridge.catalog()
    except catalog_bridge.CATALOG_ERRORS as e:
        words = catalog_unavailable_body(e)["error"]
        _all_failed(words)
        raise HTTPException(503, words) from None
    # Класс данных канала — СТРОЖАЙШИЙ среди файлов прогона: задание
    # несёт формулировку и пробел из документов заказчика.
    try:
        llm = _channel_for(None, run_dir, catreq_mod.ROLE,
                           klass=_strictest_class(run_dir),
                           key_role=catreq_mod.ROLE)
    except HTTPException as e:
        _all_failed(str(e.detail)[:300])
        raise
    except Exception as e:                                # noqa: BLE001
        _all_failed(str(e)[:300])
        raise
    ctx = catreq_mod.run_context(run_dir, doc, st, cat)
    inherit = catreq_mod.inherited_stamps(source_dir) if source_dir else {}
    now = _now_iso()
    today = now[:10]
    # Две строки с одним ключом сводятся к одной заявке ДО гейта.
    pending: dict = {}
    statements: dict = {}
    for row in todo:
        rid = row["request_id"]
        statements.setdefault(rid, [row["statement"]]
                              + list((ctx.get("clauses") or {})
                                     .get(row["rule_id"]) or ()))
        pending.setdefault(rid, {"row": row, "refusals": []})
    results: dict = {}
    for attempt in range(3):
        if not pending:
            break
        batch, failed_now = [], {}
        for rid, item in pending.items():
            fields, words = catreq_mod.call_composer(
                llm, item["row"], cat, item["refusals"])
            if fields is None:
                failed_now[rid] = words
                continue
            stamps = inherit.get(rid) or {}
            draft = catreq_mod.build_draft(
                item["row"], fields, draft_id=fields["id"],
                document_class=doc_class,
                origin_date=stamps.get("origin_date") or today)
            request = catreq_mod.assemble_request(
                item["row"], draft, document_class=doc_class,
                run_name=run_dir.name,
                submitted_at=stamps.get("submitted_at") or now)
            if stamps.get("ref"):
                request["source"]["ref"] = stamps["ref"]
            item["request"] = request
            batch.append(request)
        verdicts = catreq_mod.gate(batch, cat, ctx, statements) if batch else {}
        last = attempt == 2
        for rid in list(pending):
            item = pending[rid]
            known = None
            if rid in failed_now:
                hard, soft = list(failed_now[rid]), []
            else:
                v = verdicts.get(rid) or {}
                hard = list(v.get("hard") or ())
                soft = list(v.get("soft") or ())
                known = v.get("known")
            if not hard and not soft:
                results[rid] = ("ok", item["request"], [], known)
                del pending[rid]
            elif last or (known or {}).get("closed"):
                # Номер отклонён владельцем каталога (Г5, K08): повтор
                # модели решения не снимет — отказ сразу, словами решения.
                results[rid] = (("failed", item.get("request"), hard, known)
                                if hard else
                                ("held", item["request"], soft, known))
                del pending[rid]
            else:
                item["refusals"] = hard + soft
    for rid, (kind, request, words, known) in sorted(results.items()):
        if kind == "ok":
            # Номер уже известен каталогу (K08): своя папка и строка
            # индекса — да, повторная публикация в очередь — нет.
            got = catreq_mod.enqueue(request, run_dir, outbox, replay=replay,
                                     known=known)
            composed = catreq_mod.COMPOSED_SUBMITTED
            # Слова ОЧЕРЕДИ потомок повтора не воспроизводит — очередь
            # он не трогает; слова КАТАЛОГА (известный номер) — да: они
            # от записи, а не от файлов, и у источника с потомком одни
            # (индекс сверяется байт в байт, §1.3).
            note = got.get("words") if known or not replay else None
            counts["submitted"] += 1
        elif kind == "held":
            catreq_mod.enqueue(request, run_dir, outbox, replay=replay,
                               held=True)
            composed, note = catreq_mod.COMPOSED_HELD, "; ".join(words)
            counts["held"] += 1
        else:
            composed, note = catreq_mod.COMPOSED_FAILED, "; ".join(words)
            counts["failed"] += 1
        for rule_id in by_request.get(rid, ()):
            idx_rows[rule_id] = catreq_mod.index_row(
                keys.get(rid), rid, composed, note, doc_class)


def _catalog_auto_after(run_dir: Path, run_id: str, prefixes: tuple):
    """Автозапуск заявок ПОСЛЕ строки `end` стадии, обновившей
    канонический свод (аудит-цикл, сквозной цикл, арбитраж). Все
    условия автозапуска — ЗДЕСЬ: ручка исполняет любой `mode`
    безусловно. Красный гейт, отказ стадии и остановка — не триггер."""
    def _after() -> None:
        try:
            st = _read_status(run_dir)
        except (OSError, ValueError):
            return
        state = str(st.get("state") or "")
        if not any(state.startswith(p) for p in prefixes):
            return
        doc = _catalog_model_doc(run_dir)
        rows = _catalog_rows(run_dir, doc)
        if _catalog_auto_reason(run_dir, st, rows) is not None:
            return
        submit_catalog_requests(
            run_id, rule_ids=None, request_ids=None, document_class=None,
            mode="auto", confirm_service_c=False, trigger="auto",
            auto_setting=os.environ.get(catreq_mod.ENV_AUTO))
    return _after



def _rmtree_strict(path: Path, action: str = "сброс") -> None:
    """Удаление каталога с повторами и честным отказом: ignore_errors
    МОЛЧА оставлял часть дерева на Windows (живой сброс владельца
    2026-08-21: в панели остались «Обменов 4» от пустых каталогов
    раундов). Общий для сброса прогона и удаления файла данных."""
    import shutil
    import stat
    if not path.exists():
        return
    for _attempt in range(3):
        def _onexc(_fn, p, _exc):
            try:
                os.chmod(p, stat.S_IWRITE)
            except OSError:
                pass
        shutil.rmtree(path, onexc=_onexc)
        if not path.exists():
            return
        time.sleep(0.3)
    raise HTTPException(
        500, f"{action} не удалил {path.name} — каталог занят "
             f"другим процессом; закройте открытые файлы прогона "
             f"и повторите")


@app.post("/api/runs/{run_id}/reset")
@_journaled("reset", sync=True)
def reset_run(run_id: str):
    """Сброс прогона к исходному состоянию (решение владельца
    2026-08-21): остаются исходные файлы (правил и данных), заморозка,
    конфигурация моделей блоков, политика файлов, название
    авиакомпании и название прогона; результаты всех стадий
    (извлечение, циклы, арбитраж, линия данных) удаляются."""
    run_dir = _run_dir(run_id)

    def _busy_of(st: dict) -> list[str]:
        return [s for s in (st.get("state"), st.get("data_state"),
                            st.get("smt_state")) if _line_busy(s)]
    status = _read_status(run_dir)
    busy = _busy_of(status)
    if busy:
        raise HTTPException(409, "прогон занят стадией — остановите "
                                 "её перед сбросом: " + busy[0])

    for name in ("extract", "extract-cache", "exchange"):
        _rmtree_strict(run_dir / name)
    for p in run_dir.glob("exchange-rev-*"):
        _rmtree_strict(p)
    # Линия данных: исходники и заморозка данных остаются, выходы
    # проверок и сверок удаляются.
    for name in ("data-report", "data-review", "mode2", "answers"):
        _rmtree_strict(run_dir / name)
    _control_path(run_dir).unlink(missing_ok=True)
    # Признак допуска моделей — свойство КОМПЛЕКТА, как исходные
    # файлы: сброс результатов стадий его не снимает (03.09).
    kept_keys = ("files", "freeze_counters", "freeze_problems",
                 "freeze_warnings", "customer", "title", "data_files",
                 "data_freeze_counters", "kit",
                 # Факты рождения прогона сброс не меняет (паспорт, 07.09).
                 "created_at", "passport")
    fresh = {k: status[k] for k in kept_keys if k in status}
    fresh["state"] = "заморожено"
    if status.get("data_files"):
        fresh["data_state"] = "данные заморожены"

    def _still_free(cur: dict) -> None:
        got = _busy_of(cur)
        if got:
            raise HTTPException(409, "прогон занят стадией — остановите "
                                     "её перед сбросом: " + got[0])
    _replace_status(run_dir, fresh, precheck=_still_free)
    return {"run": run_id, **_read_status(run_dir)}


def _replay_of_brief(st: dict) -> dict | None:
    born = st.get("passport") if isinstance(st.get("passport"), dict) else {}
    rep = born.get("replay") if isinstance(born.get("replay"), dict) else None
    if not rep:
        return None
    return {"mode": rep.get("mode"), "source_run": rep.get("source_run"),
            "verdict": rep.get("verdict")}


@app.post("/api/runs/{run_id}/replay")
def start_replay(run_id: str, body: dict = Body(...)):
    """Повтор прогона (проект повтора, Р2): рождение потомка и водитель в
    фоне; 202 с номером потомка. Источник не меняется (только указатель
    на потомка в его статусе) — действием журнала не считается."""
    from cdpm import replay as replay_mod
    mode = (body or {}).get("mode")
    if mode not in replay_mod.MODES:
        raise HTTPException(400, "режим повтора вне словаря: journal, fresh")
    run_dir = _run_dir(run_id)
    try:
        got = replay_mod.start(run_dir, mode)
    except replay_mod.ReplayRefused as e:
        return JSONResponse({"detail": str(e), "reason_code": e.code},
                            status_code=409)
    return JSONResponse(got, status_code=202)


@app.get("/api/runs/{run_id}/replay-report")
def replay_report(run_id: str):
    """Отчёт повтора потомка (journal/replay-report.json)."""
    from cdpm import replay as replay_mod
    run_dir = _run_dir(run_id)
    st = _read_status(run_dir)
    rep = replay_mod.replay_of(st)
    if not rep:
        return JSONResponse({"detail": f"прогон {run_id} не является повтором",
                             "reason_code": "not-a-replay"}, status_code=404)
    report = replay_mod.read_report(run_dir)
    if report is None:
        return JSONResponse({"detail": "отчёт повтора ещё не записан: "
                             + ("повтор идёт" if st.get("replay_progress")
                                else "водитель не завершил повтор"),
                             "reason_code": "report-missing"}, status_code=404)
    return {"run": run_id, "source_run": rep.get("source_run"),
            "mode": rep.get("mode"), "report": report}


@app.get("/api/runs/{run_id}/channel")
def channel_delivery(run_id: str):
    """Квитанция последней доставки пакета по каналу ВМП+МГ
    (channel/delivery.json — та же отметка, что возвращает ручка
    export/task-package-send) — для кнопки «Передать в COSMOS» в узле
    «Итог» (владелец на репетиции 05.09: «где онлайн-передача, я её
    не вижу»). Без доставок — sent=false и состояние канала словами."""
    from cdpm import channel as _chan
    run_dir = _run_dir(run_id)
    configured = _chan.channel_enabled()
    mark = _delivery_mark(run_dir)
    if mark:
        # Saved extensions are receipt data, not the requested run or live
        # channel configuration. Keep this response envelope locally owned.
        return {**mark, "run": run_id, "configured": configured}
    return {"run": run_id, "sent": False, "configured": configured,
            "detail": "доставок по каналу ещё не было"
                      + ("" if configured else
                         "; канал выключен (CREW_CHANNEL_URL/"
                         "CREW_CHANNEL_TOKEN не заданы) — пакет файлом")}


def _task_package_refusal(e) -> JSONResponse:
    """Отказ выпуска пакета наружу: 409 телом `{detail, code}`.

    `detail` остаётся СТРОКОЙ — её читают и панель, и раннер наборов
    (`answer.get("detail")`); `code` — добавочный ключ машинного кода
    отказа (рубеж непротиворечивости: `smt-contradiction`,
    `smt-unavailable`, `smt-incomplete`; рубеж пересмотра K04 —
    `family-incomplete` со словами «пересмотр открыт»), у прежних
    отказов он пустой;
    `items` (контракт П0 §1.4, 12.09.2026) — перечень идентификаторов,
    к которым относится отказ (непереведённые правила у
    `smt-incomplete`, `<семья>/<параметр>` у открытого пересмотра — та
    же грамматика, что у полноты семьи K01), у прочих отказов пустой.
    Читатель, который кода не знает, читает ответ как раньше."""
    return JSONResponse({"detail": str(e),
                         "code": getattr(e, "code", None),
                         "items": list(getattr(e, "items", None) or [])},
                        status_code=409)


def _base_url(request=None) -> str | None:
    """Адрес панели, которым выпущен пакет: адрес текущего запроса (на
    площадках `http://127.0.0.1:<порт панели>`). Переменную
    `CDPM_PUBLIC_URL` читает сам сборщик пакета — она старше."""
    if request is None:
        return None
    try:
        return str(request.base_url).rstrip("/")
    except Exception:                                     # noqa: BLE001
        return None


@app.get("/api/runs/{run_id}/export/{fmt}")
def export_for_solver(run_id: str, fmt: str, request: Request = None,
                      preset_id: str = "",
                      job_type: str = "roster",
                      division: str = "FD"):
    """ЭКС-1: скачивание итоговой модели в формате решателя (Rave /
    MiniZinc / CSV / JSON). Экспортируется КАНОНИЧЕСКАЯ модель (после
    последней завершённой стадии); данных должно быть достаточно хотя
    бы для одной задачи.

    МЕР-1: формат «meridian» — ДРУГАЯ природа (спецификация
    COS-MERIDIAN v1.17 описывает обмен операционными ДАННЫМИ, правила
    в ней — только ссылка presetId): выгружается книга данных прогона
    как dataset; неполнота по обязательным полям — честный отказ с
    перечнем недостающего."""
    from cdpm import export_solver
    run_dir = _run_dir(run_id)
    if fmt == "task-package":
        # Мост А (2026-08-22): пакет постановки задачи COSMOS —
        # спецификация docs/spec-postanovka-cosmos-v1.md.
        from cdpm import export_task_package
        try:
            body = export_task_package.export_zip(run_dir, _base_url(request))
        except export_task_package.TaskPackageError as e:
            return _task_package_refusal(e)
        return Response(
            body, media_type="application/zip",
            headers={"Content-Disposition":
                     f'attachment; filename='
                     f'"{run_id}-task-package.zip"'})
    if fmt == "task-package-send":
        # Канал ВМП+МГ (поручение владельца 2026-08-29): сборка →
        # собственный гейт (красный пакет по каналу не едет) →
        # доставка приёмнику COSMOS; отметка — в записи прогона.
        # Канал выключен/недоступен = честная строка, пакет файлом.
        import json as _json2
        import tempfile as _tempfile
        import time as _time

        from cdpm import channel as _chan
        from cdpm import export_task_package
        from cdpm.gates import gate_task_package as _gate
        try:
            files = export_task_package.build_package(run_dir,
                                                      _base_url(request))
        except export_task_package.TaskPackageError as e:
            return _task_package_refusal(e)
        body = export_task_package.write_zip(files)
        out_dir = run_dir / "channel"
        pkg_path = out_dir / "task-package.zip"
        staging = None
        try:
            out_dir.mkdir(exist_ok=True)
            # P0 §3.6, line 4: a failed new publication must not destroy
            # the previous package or its delivery mark. Each attempt owns
            # a distinct same-directory file, judged before atomic replacement.
            with _tempfile.NamedTemporaryFile(prefix=".task-package-", suffix=".zip",
                    dir=out_dir, delete=False) as temporary:
                staging = Path(temporary.name)
            staging.write_bytes(body)
            problems, _c = _gate.check(staging)
            if problems:
                raise HTTPException(
                    409, "пакет не прошёл гейт перед отправкой: "
                         + "; ".join(problems))
            _file_replace.replace_with_retry(staging, pkg_path)
        except OSError as error:
            raise HTTPException(
                409, f"пакет не опубликован перед отправкой: {error}") from error
        finally:
            if staging is not None:
                try:
                    staging.unlink(missing_ok=True)
                except OSError:
                    # An abandoned unique staging file is not authoritative.
                    # Cleanup must not hide the gate/IO refusal or interruption;
                    # the next attempt creates and judges its own staging file.
                    pass
        manifest = _json2.loads(files["manifest.json"])
        mark = _chan.send_task_package(
            body, manifest.get("spec_version"), run_dir.name)
        mark["at"] = _time.strftime("%Y-%m-%d %H:%M:%S")
        mark["package_file"] = str(pkg_path)
        # Напоминание о незаявленных пробелах каталога (шаг 6): только
        # счётчики в ответе — содержимое пакета не меняется, выпуск
        # ничего не составляет и не подаёт (Ф-1.7).
        try:
            mark["catalog_requests"] = catreq_mod.counts_of(
                _catalog_rows(run_dir, _catalog_model_doc(run_dir)),
                catreq_mod.read_index(run_dir))
        except Exception as e:                            # noqa: BLE001
            mark["catalog_requests"] = {"error": str(e)[:200]}
        try:
            delivery_mark_mod.write_current(run_dir, mark)
        except BaseException:
            # The ZIP is already published. Rebuild derived evidence against
            # its actual SHA even though the historical mark is unchanged.
            # A secondary refresh failure must not hide the publication error.
            try:
                _refresh_run_record(run_dir, report_errors=True)
            except BaseException:
                pass
            raise
        # Итоги записи о прогоне: отметка доставки пакета ложится в хвост
        # run-record.json сразу, а не следующим действием.
        _refresh_run_record(run_dir)
        return mark
    if fmt == "meridian":
        from cdpm import export_meridian
        data_path = run_dir / "data-freeze" / "dataset.json"
        if not data_path.is_file():
            raise HTTPException(409, "книга операционных данных не "
                                     "загружена — формат Meridian "
                                     "выгружает данные, не правила")
        if job_type not in ("roster", "pairing"):
            raise HTTPException(400, "job_type — roster или pairing")
        if division not in ("FD", "CC"):
            raise HTTPException(400, "division — FD или CC")
        tables = json.loads(data_path.read_text(encoding="utf-8")) \
            .get("tables", {})
        st = _read_status(run_dir)
        try:
            body = export_meridian.export_meridian(
                tables, preset_id=preset_id, dataset_type=job_type,
                division=division,
                description=f"Экспорт CDPM {run_id} "
                            f"({st.get('customer') or ''})".strip())
        except export_meridian.MeridianExportError as e:
            raise HTTPException(409, str(e)) from None
        return Response(
            body, media_type="application/json; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename='
                     f'"{run_id}-meridian-dataset.json"'})
    model_path = run_dir / "extract" / "model.json"
    if not model_path.is_file():
        raise HTTPException(409, "модель ещё не построена — сначала "
                                 "извлечение")
    doc = _load_model_or_409(model_path)
    try:
        body, ext, mime = export_solver.export(doc, fmt)
    except export_solver.ExportUnavailable as e:
        raise HTTPException(501, str(e)) from None
    except export_solver.ExportNotReady as e:
        raise HTTPException(409, str(e)) from None
    except ValueError as e:
        raise HTTPException(404, str(e)) from None
    fname = f"{run_id}-rules{ext}"
    return Response(
        body, media_type=f"{mime}; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="{fname}"'})


@app.get("/api/runs/{run_id}/exchange/arbiter-svod")
def exchange_arbiter_svod(run_id: str):
    path = _run_dir(run_id) / "exchange" / "versions" \
        / "svod-arbiter.md"
    if not path.is_file():
        raise HTTPException(404, "свода после арбитража нет")
    return _md_html(path.read_text(encoding="utf-8"))


@app.get("/api/runs/{run_id}/exchange/arbiter-anketa")
def exchange_arbiter_anketa(run_id: str):
    path = _run_dir(run_id) / "exchange" / "versions" \
        / "anketa-arbiter.md"
    if not path.is_file():
        raise HTTPException(404, "анкеты после арбитража нет")
    return _md_html(path.read_text(encoding="utf-8"))


_XLSX_MIME = ("application/vnd.openxmlformats-officedocument"
              ".spreadsheetml.sheet")

_ALLOWED_FILES = {
    "манифест": ("freeze", "manifest.json"),
    "свод": ("extract", "svod.md"),
    "анкета": ("extract", "anketa.md"),
    # Excel-книга анкеты для заполнения авиакомпанией — путь человека к
    # ответам (живой вопрос 04.09: «анкета» отдавала html-представление,
    # а книгу для колонки «ОТВЕТ АВИАКОМПАНИИ» скачать было неоткуда).
    # Переиздаётся каждой стадией вместе с md-анкетой (_sync_canon,
    # ответы, режим 2, проверки данных) — это всегда текущая ревизия.
    "анкета-xlsx": ("extract", "anketa.xlsx"),
    "модель": ("extract", "model.json"),
    "отбраковка": ("extract", "rejections.json"),
    "сводка-ответов": ("extract", "answers-summary.json"),
    "отчёт-сверки-формализации": ("third-party",
                                  "third-party-report.md"),
    "отчёт-сверки-формализации-модель": ("third-party",
                                         "third-party-report.json"),
    "манифест-данных": ("data-freeze", "manifest.json"),
    "отчёт-данных": ("data-report", "data-report.md"),
    "отчёт-данных-модель": ("data-report", "data-report.json"),
    "проверки-данных": ("data-report", "checks.json"),
    "сводка-сверки": ("data-report", "review-summary.json"),
}


@app.get("/api/runs/{run_id}/file/{alias}")
def run_file(run_id: str, alias: str):
    """Артефакты прогона — по закрытому перечню имён: произвольные пути
    наружу не отдаются."""
    run_dir = _run_dir(run_id)
    if alias not in _ALLOWED_FILES:
        raise HTTPException(404, f"неизвестный артефакт {alias!r}; "
                                 f"перечень: {sorted(_ALLOWED_FILES)}")
    sub, name = _ALLOWED_FILES[alias]
    path = run_dir / sub / name
    if alias == "анкета-xlsx" and not path.is_file() \
            and (run_dir / "extract" / "model.json").is_file():
        # Прогоны, извлечённые до появления книги на этой стадии:
        # книга — чистая функция модели, достраивается по требованию.
        published = m_mod.load_model(run_dir / "extract" / "model.json")
        m_mod.publish_model(published, run_dir / "extract" / "model.json")
        anketa_xlsx_mod.render_anketa_xlsx(published, path)
    if not path.is_file():
        raise HTTPException(404, f"артефакт {alias} ещё не построен")
    if name.endswith(".xlsx"):
        return FileResponse(str(path), media_type=_XLSX_MIME,
                            filename=f"anketa-{run_id}.xlsx")
    text = path.read_text(encoding="utf-8")
    if name.endswith(".md"):
        import markdown
        return HTMLResponse(
            "<meta charset='utf-8'><style>body{font-family:sans-serif;"
            "max-width:60em;margin:2em auto;padding:0 1em}table{border-"
            "collapse:collapse}td,th{border:1px solid #999;padding:4px "
            "8px;vertical-align:top}</style>" +
            markdown.markdown(text, extensions=["tables"]))
    return JSONResponse(json.loads(text))


@app.get("/api/runs/{run_id}/effective-parameters")
def run_effective_parameters(run_id: str):
    """A hash-bound read-only view; never rebuild, heal or export the model."""
    import hashlib
    from cdpm import parameter_review
    path = _run_dir(run_id) / "extract" / "model.json"
    try:
        raw = _file_replace.read_bytes_shared(path)
    except FileNotFoundError:
        raise HTTPException(404, "модели нет — сначала извлечение")
    except OSError as error:
        raise HTTPException(503, f"модель пока не удалось прочитать: {error}")
    try:
        projection = parameter_review.from_model(m_mod.loads_model(raw))
        return JSONResponse({"run": run_id, "snapshot_at": _now_iso(),
                             "model_sha256": hashlib.sha256(raw).hexdigest(),
                             **projection}, headers={"Cache-Control": "no-store"})
    except (ValueError, TypeError) as error:
        raise HTTPException(409, f"параметры модели не удалось прочитать: {error}")


def _run_form(run_id: str) -> tuple:
    """Форма ВМП текущего свода прогона (решение владельца 2026-09-02:
    форма — каноническая форма выдачи/приёмки). Строится по требованию
    из одного снимка extract/model.json; возвращает (форма, проверки
    формы strict, расхождения обратного построения, подложка A12).
    Подложка не является частью канонической формы и не меняет её гейты."""
    from cdpm import obligation_field_view, obligations
    from cdpm import vmp_form as vf
    run_dir = _run_dir(run_id)
    model_path = run_dir / "extract" / "model.json"
    try:
        raw = _file_replace.read_bytes_shared(model_path)
    except FileNotFoundError:
        raise HTTPException(404, "свода нет — сначала извлечение")
    except OSError as error:
        raise HTTPException(503, f"свод пока не удалось прочитать: {error}")
    doc = m_mod.loads_model(raw)
    form = vf.to_form(doc)
    # The exact frozen source is an admissible quote anchor, just as at
    # package export. A normalized statement need not repeat its wording.
    clause_texts = None
    if (run_dir / "freeze" / "manifest.json").is_file():
        try:
            clause_texts = {(str(entry["sid"]), str(clause["anchor"])):
                           str(clause.get("text") or "")
                           for entry in extract._load_kit(run_dir / "freeze")
                           for clause in entry.get("clauses", [])}
        except (OSError, ValueError, KeyError, TypeError):
            # Unavailable evidence is not an empty successful check; the
            # validator still requires proof from the statement/answer.
            clause_texts = None
    report = obligations.run_report(run_dir, model_bytes=raw, expected_model=doc)
    field_view = obligation_field_view.from_report(model_bytes=raw, report=report)
    return form, vf.validate_form(form, strict=True, clause_texts=clause_texts), \
        vf.canonical_diff(doc, vf.from_form(form)), field_view


def _obligations_form_html(run_id: str, view: dict) -> str:
    """Render only the separate field evidence; imported claims are plain text."""
    from html import escape
    from urllib.parse import quote

    def text(value):
        return escape(str(value), quote=True)

    def structured(value):
        return text(json.dumps(value, ensure_ascii=False, sort_keys=True))

    states = {
        "current": "связи сверены с указанным снимком модели",
        "not_recorded": "ведомость обязательств не записана",
        "stale_or_unresolved": "связи устарели или их привязка не разрешена",
        "unavailable": "ведомость или исходные данные недоступны / невалидны",
    }
    status = view["status"]
    out = ["<section id='obligation-field-evidence'><h2>Обратные ссылки обязательств у полей ВМП</h2>",
           "<p>Отдельная подложка A12, не часть канонической формы. "
           "Связи являются заявленным соответствием, не семантическим покрытием "
           "и не экспертной приёмкой.</p>",
           f"<p>Состояние: <strong>{text(status)}</strong> — {text(states.get(status, status))}.<br>",
           f"SHA-256 снимка модели: <code>{text(view['model_sha256'])}</code><br>",
           f"Смысл: <code>{text(view['meaning'])}</code>; независимость: "
           f"<code>{text(view['independence'])}</code>.</p>",
           f"<p><a href='/runs/{escape(quote(run_id, safe=''), quote=True)}/obligations'>"
           "Открыть ведомость A12: исходные обязательства, счётчики и связи</a></p>"]
    fields = view["fields"]
    if fields:
        out.append("<table><thead><tr><th>Коллекция / строка</th><th>Точное поле модели</th>"
                   "<th>Значение</th><th>Читатель / обязательство</th><th>Компонент</th>"
                   "<th>Состояние обязательства / соответствие</th></tr></thead><tbody>")
        for field in fields:
            for link in field["links"]:
                out.append(
                    f"<tr><td>{text(field['collection'])} / {text(field['row_id'])}</td>"
                    f"<td><code>{text(field['pointer'])}</code></td>"
                    f"<td><pre>{structured(field['value'])}</pre></td>"
                    f"<td>{text(link['reader'])} / {text(link['obligation'])}</td>"
                    f"<td>{text(link['component'])}</td>"
                    f"<td>{text(link['status'])}<br>{text(link['correspondence'])}</td></tr>")
        out.append("</tbody></table>")
    else:
        out.append("<p>Нет текущих адресных связей. Это не подтверждение отсутствия обязательств.</p>")
    out.extend(["<h3>Экспертное рассмотрение</h3><pre>" + structured(view["expert_review"]) + "</pre>",
                "<h3>Диагностика привязки</h3><pre>" + structured(view["issues"]) + "</pre></section>"])
    return "".join(out)


@app.get("/api/runs/{run_id}/form")
def run_form(run_id: str):
    """Форма ВМП (JSON) + проверки формы приёмки и тождество проекции."""
    form, problems, lost, obligations = _run_form(run_id)
    return JSONResponse({"form": form, "problems": problems,
                         "lossless": not lost, "lost": lost,
                         "obligations": obligations}, headers={"Cache-Control": "no-store"})


@app.get("/api/runs/{run_id}/form.md")
def run_form_md(run_id: str):
    """Форма ВМП документом (для авиакомпании): общий раздел, части
    «Пейринг» и «Ростеринг» с разделами а/б/в; в конце — проверки формы."""
    from cdpm import vmp_form as vf
    import markdown
    form, problems, lost, obligations = _run_form(run_id)
    text = vf.render_form_md(form)
    if problems or lost:
        text += "\n\n## Проверки формы приёмки\n\n" + "\n".join(
            f"- {p}" for p in (problems + [f"проекция с потерями: {x}"
                                           for x in lost]))
    else:
        text += "\n\n## Проверки формы приёмки\n\nЧиста: нарушений нет, " \
                "проекция без потерь.\n"
    return HTMLResponse(
        "<meta charset='utf-8'><style>body{font-family:sans-serif;"
        "max-width:60em;margin:2em auto;padding:0 1em}table{border-"
        "collapse:collapse}td,th{border:1px solid #999;padding:4px "
        "8px;vertical-align:top}</style>" +
        markdown.markdown(text, extensions=["tables"]) +
        _obligations_form_html(run_id, obligations), headers={"Cache-Control": "no-store"})


@app.get("/api/runs/{run_id}/map-projection")
def map_projection(run_id: str):
    """Пакет трёх работ владельца 2026-08-30, п.1: карта постановки
    в панели CDPM. Детерминированная проекция data-freeze в формат
    общего движка NetMap (станции/рёбра/сотрудники) + координаты
    общей точки webapp.geo. Без книги данных — честный отказ."""
    from cdpm import map_projection as mp
    run_dir = _run_dir(run_id)
    data_path = run_dir / "data-freeze" / "dataset.json"
    if not data_path.is_file():
        raise HTTPException(409, "книга данных не загружена — карте "
                                 "постановки не из чего строиться")
    tables = json.loads(data_path.read_text(encoding="utf-8")) \
        .get("tables", {})
    # Класс данных комплекта решает источник координат (05.09):
    # синтетика — только книга, справочник по коду не применяется.
    kit = _read_status(run_dir).get("kit") or {}
    data_class = kit.get("data_class") if isinstance(kit, dict) else None
    try:
        return mp.build_map_projection(tables, data_class=data_class)
    except mp.MapProjectionError as e:
        raise HTTPException(409, str(e)) from None


@app.get("/net-map.js")
def net_map_js():
    """Общий движок карты — ТЕМ ЖЕ ФАЙЛОМ из webapp/static (решение
    пакета: движок один на обе панели, копий-форков нет)."""
    path = Path(__file__).resolve().parent.parent \
        / "webapp" / "static" / "net-map.js"
    if not path.is_file():
        raise HTTPException(404, "net-map.js не найден в webapp/static")
    return Response(path.read_text(encoding="utf-8"),
                    media_type="application/javascript; charset=utf-8")


@app.get("/geo/world-110m.json")
def world_topology():
    """Мировая подложка карты — тем же файлом из webapp/static/geo
    (ленивая загрузка движка по NetMap.worldUrl)."""
    path = Path(__file__).resolve().parent.parent \
        / "webapp" / "static" / "geo" / "world-110m.json"
    if not path.is_file():
        raise HTTPException(404, "world-110m.json не найден")
    return Response(path.read_bytes(),
                    media_type="application/json")


@app.post("/api/runs/{run_id}/conditions-questions")
@_journaled("conditions-questions", sync=True)
def refresh_conditions_questions(run_id: str):
    """Пересбор вопросов условий расчёта БЕЗ полной стадии данных
    (живой случай run-0060: новая тема волны появилась кодом, а все
    прежние вопросы уже отвечены — анкета без полей ввода, подать
    повторную книгу невозможно, полная стадия ради одного вопроса —
    полчаса). Дедупликация внутри ensure; свод и анкета переиздаются
    той же дисциплиной, что путь ответов."""
    run_dir = _run_dir(run_id)
    model_path = run_dir / "extract" / "model.json"
    if not model_path.is_file():
        raise HTTPException(409, "свода нет — сначала извлечение")
    doc = m_mod.load_model(model_path)
    before = copy.deepcopy(doc)
    created = m_mod.ensure_conditions_questions(doc)
    # P-N-SHD 2026-08-30: та же дисциплина, что путь ответов —
    # миграция классификации, затем сквозной проход весов РАНЬШЕ
    # вопроса критерия (тот якорится на Q-3a-GOAL — каскад обязан
    # завершаться за один вызов, идемпотентность судится повтором).
    restored = m_mod.restore_soft_penalty_rules(doc)
    created += m_mod.ensure_goal_questions_sweep(doc)
    created += m_mod.ensure_criterion_question(doc)
    created += m_mod.ensure_pairing_clarification_questions(doc)
    created += family_params_mod.ensure_family_param_questions(doc)
    families = m_mod.assign_soft_families(doc)
    # Ярлыки кодом (2026-09-02): сквозной проход — своды до
    # детерминизации самолечатся на этом пути (ревью пакета r54).
    traits = m_mod.derive_all_rule_traits(doc, "ручка вопросов условий")
    # Synchronizers also close obsolete questions and restore legal values.
    # Their returned IDs count additions, not all mutations. Persist those
    # transitions even when this call creates no question at all.
    if created or families or restored or traits or doc != before:
        bad = m_mod.validate_schema(doc) + m_mod.validate_model(doc)
        if bad:
            raise HTTPException(500, "вопросы условий не прошли "
                                     "контракт: " + "; ".join(bad[:3]))
        out = run_dir / "extract"
        m_mod.publish_model(doc, out / "model.json")
        (out / "svod.md").write_text(render_mod.render_svod(doc),
                                     encoding="utf-8")
        (out / "anketa.md").write_text(render_mod.render_anketa(doc),
                                       encoding="utf-8")
        anketa_xlsx_mod.render_anketa_xlsx(doc, out / "anketa.xlsx")
    return {"run": run_id, "created": created,
            "soft_families": families, "restored_soft": restored}


_PAGE = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<title>CDPM — обработка данных авиакомпании</title>
<style>
body{font-family:sans-serif;max-width:60em;margin:2em auto;padding:0 1em}
h1{font-size:1.4em} fieldset{margin:1em 0;border:1px solid #bbb}
button{padding:.4em 1em;margin:.2em 0} pre{background:#f4f4f4;
padding:.8em;overflow-x:auto;white-space:pre-wrap}
.ok{color:#06660d}.bad{color:#a11}.muted{color:#666}
table{border-collapse:collapse;width:100%}
td,th{border:1px solid #999;padding:4px 8px;text-align:left}
a{margin-right:1em}
</style></head><body>
<h1>CDPM — модуль обработки данных авиакомпании</h1>
<p class="muted">Автономный контур: документы правил (doc / docx / md /
xlsx / pdf / json / jpg / png) → заморозка с манифестом SHA-256 →
извлечение правил языковой моделью → формализованный свод и анкета.
Каждый шаг под гейтом.</p>

<fieldset><legend>1. Новый прогон: документы правил</legend>
<input type="file" id="files" multiple>
<button onclick="doFreeze()">Заморозить</button>
<div id="freeze_out"></div></fieldset>

<fieldset><legend>2. Извлечение</legend>
Прогон: <select id="run_sel"></select>
Авиакомпания: <input id="customer" placeholder="название для шапки">
Допуск моделей комплекта: <span id="scope">(признак прогона; правится ручкой kit-scope)</span>
<button onclick="doExtract()">Извлечь</button>
<div id="extract_out"></div></fieldset>

<fieldset><legend>3. Прогоны и артефакты</legend>
<button onclick="refresh()">Обновить</button>
<div id="runs"></div></fieldset>

<script>
async function refresh(){
  const runs = await (await fetch('api/runs')).json();
  const sel = document.getElementById('run_sel');
  sel.innerHTML = runs.map(r =>
    `<option value="${r.run}">${r.run} — ${r.state}</option>`).join('');
  sel.selectedIndex = runs.length - 1;
  document.getElementById('runs').innerHTML = '<table><tr><th>Прогон'
    + '</th><th>Состояние</th><th>Документы</th><th>Артефакты</th></tr>'
    + runs.map(r => `<tr><td>${r.run}</td><td>${r.state}</td>`
      + `<td>${(r.files||[]).join('<br>')}</td>`
      + `<td><a href="api/runs/${r.run}/file/свод" target="_blank">свод`
      + `</a><a href="api/runs/${r.run}/file/анкета" target="_blank">`
      + `анкета</a><a href="api/runs/${r.run}/file/анкета-xlsx">`
      + `анкета (Excel для заполнения)</a>`
      + `<a href="api/runs/${r.run}/file/манифест" `
      + `target="_blank">манифест</a><a href="api/runs/${r.run}/file/`
      + `отбраковка" target="_blank">отбраковка</a>`
      + `<a href="/runs/${r.run}/obligations" target="_blank">A12: обязательства</a></td></tr>`).join('')
    + '</table>';
}
async function doFreeze(){
  const fd = new FormData();
  for (const f of document.getElementById('files').files)
    fd.append('files', f);
  const el = document.getElementById('freeze_out');
  el.innerHTML = 'заморозка…';
  const resp = await fetch('api/runs', {method:'POST', body:fd});
  const got = await resp.json();
  const c = got.freeze_counters || {};
  el.innerHTML = resp.ok && got.state === 'заморожено'
    ? `<p class="ok">${got.run}: заморожено — файлов ${c.files}, `
      + `клауз ${c.clauses}, оригиналов сверено ${c.originals}</p>`
    : `<p class="bad">${got.state || 'ошибка'}: `
      + `${got.error || (got.freeze_problems||[]).join('; ')}</p>`;
  refresh();
}
async function doExtract(){
  const run = document.getElementById('run_sel').value;
  const customer = document.getElementById('customer').value;
  const el = document.getElementById('extract_out');
  const resp = await fetch(`api/runs/${run}/extract?customer=`
    + encodeURIComponent(customer), {method:'POST'});
  if (!resp.ok){
    el.innerHTML = `<p class="bad">${(await resp.json()).detail}</p>`;
    return;
  }
  el.innerHTML = 'извлечение идёт…';
  const timer = setInterval(async () => {
    const st = await (await fetch(`api/runs/${run}`)).json();
    if (st.state === 'извлечение идёт') return;
    clearInterval(timer);
    const s = st.extract_summary || {};
    el.innerHTML = st.state === 'извлечено'
      ? `<p class="ok">${run}: извлечено — правил ${s.rules}, вопросов `
        + `${s.questions}, отбраковано ${s.rejected}</p>`
      : `<p class="bad">${st.state}: ${st.error || ''} `
        + `${(st.extract_problems||[]).join('; ')}</p>`;
    refresh();
  }, 1500);
}
refresh();
</script></body></html>"""


# Новый интерфейс (cdpm-ui, визуальный язык CodeForge): собранная
# статика коммитится в репозиторий (решение владельца — вариант А:
# установка не требует Node). Старый одностраничник остаётся по /legacy
# до паритета страниц.
DIST_DIR = Path(__file__).resolve().parents[1] / "cdpm-ui" / "dist"


class _NoCacheIndexStatic(StaticFiles):
    """index.html не кэшируется браузером (иначе после пересборки
    пользователь видит старый интерфейс до жёсткой перезагрузки —
    поймано владельцем 2026-08-17); хэшированные ресурсы из assets/
    кэшировать можно и нужно."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        path = getattr(resp, "path", "") or ""
        if str(path).endswith("index.html"):
            resp.headers["Cache-Control"] = "no-cache"
        return resp


if (DIST_DIR / "index.html").is_file():
    app.mount("/ui", _NoCacheIndexStatic(directory=str(DIST_DIR),
                                         html=True), name="ui")


@app.get("/", response_class=HTMLResponse)
def page():
    if (DIST_DIR / "index.html").is_file():
        return RedirectResponse("/ui/")
    return _PAGE


@app.get("/legacy", response_class=HTMLResponse)
def legacy_page():
    return _PAGE


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")   # У-14: печать кириллицы
    ap = argparse.ArgumentParser(
        description="Автономный браузер-интерфейс CDPM")
    ap.add_argument("--port", type=int, default=8095)
    ap.add_argument("--host", default="127.0.0.1",
                    help="по умолчанию только эта машина: входа и "
                         "паролей у интерфейса нет, публикуемое "
                         "умолчание обязано быть безопасным "
                         "(подготовка публикации 2026-08-22). Для "
                         "доверенной сети — явно 0.0.0.0 (так делает "
                         "start_cdpm.bat владельца)")
    args = ap.parse_args(argv)
    import uvicorn
    # МП-2: ключ «cloud» из настроек CDPM — каналу выполнения (иначе
    # канал молча уезжает в агентный CLI при заданном ключе).
    if providers_mod.sync_cloud_key_to_env():
        print("канал «cloud»: ключ из настроек CDPM прокинут "
              "Messages API")
    print(f"CDPM: http://{args.host}:{args.port}/ ; прогоны — "
          f"{runs_root().resolve()}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
