# -*- coding: utf-8 -*-
"""Расчёт по пакету постановки в COSMOS (решение владельца 04.09.2026:
работа CDPM заканчивается выпуском и экспортом ВМП; всё, что связано с
решением задач пейринга и ростеринга, живёт в COSMOS).

Кнопка «пакет → пейринг → ростеринг → сертификат → выгрузка»
(vmp.run.button.orchestrate) нажимается на ТЕКУЩЕМ пакете постановки
(webapp.task_channel.current_package: файл руками либо пакет, принятый
каналом/загрузкой). Артефакты нажатий — в каталоге данных
`package-runs/<sha пакета>/` (тот же состав каталогов, что у прогона:
button-pairing/, button/, registry/), состояние — state.json там же.

Система прогонов COSMOS (проект 07.09.2026, разделы 5, 7, 17): рабочий
каталог пары (прогон, пакет) берётся ОДНИМ разрешением
(`resolve_current` / `workdir_of`); запись попытки несёт паспортные
поля (параметры нажатия, класс воспроизводимости, сертификатор, код,
каталог шаблонов, решатель, версии движков, обмены с моделью, журнал
внешних вызовов, копия реестра); повтор попытки — обычное нажатие в
режиме `replay` («из журнала» — записанные ответы внешних сторон и гейт
байт в байт; «заново» — внешние стороны заново и сравнение по итогам).

Решатель выбирается ТОЛЬКО здесь: движок точной фазы отбора пейринга
(cpsat | cplex | highs) пробрасывается построителю как есть; код одинаков
для любого движка (контракт независимости от решателя). Обязательство
не передавать заказчикам решения движков разработки — договорённость
команды, не ветка кода.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import NamedTuple

import _data_paths

RUNS_DIR_NAME = "package-runs"
STATE_NAME = "state.json"
#: Маркер держателя рабочего каталога (раздел 2.3 проекта прогонов).
HOLDER_NAME = "holder.json"
ENGINES = ("cpsat", "cplex", "highs")
TASKS = ("pairing", "rostering")

log = logging.getLogger("cosmos.package_runs")

#: Артефакты нажатий по закрытому перечню имён (тот же словарь алиасов,
#: что был у панели CDPM, — раннеры и люди привыкли к нему).
ALLOWED_FILES = {
    "выдача-кнопки": ("button", "delivery.json"),
    "журнал-кнопки": ("button", "log.json"),
    "сертификат-кнопки": ("button", "certificate.json"),
    "сертификат-кнопки-текст": ("button", "certificate.md"),
    "пакет-кнопки": ("button", "package.zip"),
    "решение-кнопки": ("button", "solved.json"),
    "решение-кнопки-ростер": ("button", "solution.json"),
    "выдача-кнопки-пейринга": ("button-pairing", "delivery.json"),
    "журнал-кнопки-пейринга": ("button-pairing", "log.json"),
    "сертификат-кнопки-пейринга": ("button-pairing", "certificate.json"),
    "сертификат-кнопки-пейринга-текст": ("button-pairing",
                                         "certificate.md"),
    "пакет-кнопки-пейринга": ("button-pairing", "package.zip"),
    "каталог-цепочек": ("button-pairing", "builder-catalog.json"),
    "пробелы-шаблонов-пейринга": ("button-pairing",
                                  "vmp-rule-gaps.json"),
    # Квитанция подачи заявок в каталог шаблонов (шаг 6): какие пробелы
    # относительно записей стали заявками на вид исполнителя, с какими
    # номерами и дошли ли они до службы.
    "заявки-каталога-пейринга": ("button-pairing",
                                 "catalog-requests.json"),
    # Ведомость исполнения правил ВМП (внешний аудит 04.09.2026, A1):
    # правило → параметры → исполнитель → фактически переданные
    # построителю значения → место исполнения.
    "ведомость-исполнения-пейринга": ("button-pairing",
                                      "execution-manifest.json"),
    "выдача-кнопки-на-пейринге": ("button-on-pairing", "delivery.json"),
    "журнал-кнопки-на-пейринге": ("button-on-pairing", "log.json"),
    "сертификат-кнопки-на-пейринге": ("button-on-pairing",
                                      "certificate.json"),
    # 06.09.2026 (демо: ростеринг на утверждённом пейринге — штатный
    # путь): у выдачи «на пейринге» те же артефакты, что у штатной.
    "сертификат-кнопки-на-пейринге-текст": ("button-on-pairing",
                                            "certificate.md"),
    "решение-кнопки-на-пейринге": ("button-on-pairing", "solved.json"),
    "решение-кнопки-ростер-на-пейринге": ("button-on-pairing",
                                          "solution.json"),
    "пакет-кнопки-на-пейринге": ("button-on-pairing", "package.zip"),
    "реестр-нажатия": ("registry", "vmp-executors-registry.json"),
    # Система прогонов (07.09.2026, разделы 7.6, 7.8, 17.3): копия реестра
    # в снимке попытки, обмены с моделью, показания и статус решателя,
    # журнал внешних вызовов, отчёт повтора — по одному алиасу на каталог.
    "реестр-попытки": ("registry", "vmp-executors-registry.json"),
    "обмены-с-моделью": ("button/llm-exchanges", "index.jsonl"),
    "обмены-с-моделью-пейринга": ("button-pairing/llm-exchanges",
                                  "index.jsonl"),
    "обмены-с-моделью-на-пейринге": ("button-on-pairing/llm-exchanges",
                                     "index.jsonl"),
    "показания-решателя": ("button", "applied-rules.json"),
    "показания-решателя-на-пейринге": ("button-on-pairing",
                                       "applied-rules.json"),
    "статус-прогона-решателя": ("button", "solver-status.json"),
    "статус-прогона-решателя-на-пейринге": ("button-on-pairing",
                                            "solver-status.json"),
    "журнал-внешних-вызовов": ("button/external-calls", "index.jsonl"),
    "журнал-внешних-вызовов-пейринга": ("button-pairing/external-calls",
                                        "index.jsonl"),
    "журнал-внешних-вызовов-на-пейринге": ("button-on-pairing/external-calls",
                                           "index.jsonl"),
    "отчёт-повтора": ("replay", "report.json"),
}

#: Алиасы, которые страница показывает ссылками для каждой задачи.
TASK_LINKS = {
    "pairing": ("выдача-кнопки-пейринга", "каталог-цепочек",
                "пакет-кнопки-пейринга",
                "сертификат-кнопки-пейринга-текст",
                "сертификат-кнопки-пейринга", "журнал-кнопки-пейринга",
                "ведомость-исполнения-пейринга"),
    "rostering": ("выдача-кнопки", "решение-кнопки-ростер",
                  "решение-кнопки", "пакет-кнопки",
                  "сертификат-кнопки-текст", "сертификат-кнопки",
                  "журнал-кнопки"),
    "rostering_on_pairing": ("выдача-кнопки-на-пейринге",
                             "решение-кнопки-ростер-на-пейринге",
                             "решение-кнопки-на-пейринге",
                             "пакет-кнопки-на-пейринге",
                             "сертификат-кнопки-на-пейринге-текст",
                             "сертификат-кнопки-на-пейринге",
                             "журнал-кнопки-на-пейринге"),
}


#: Подсказка после неудачи ростеринга НА выбранном пейринге (единая
#: версия 06.09.2026, W-3): неудача на каталоге — свойство каталога.
PAIRING_RETRY_NOTE = (
    "Неудача назначения выбранного каталога не доказывает невозможность "
    "исходной задачи. Пересмотрите выбор пейринга либо расширьте каталог "
    "новым запуском пейринга, затем явно выберите новый прогон.")


def roster_delivery_dir(wd: Path) -> Path | None:
    """Каталог ПОСЛЕДНЕЙ выдачи ростеринга: штатной (button/) либо «на
    утверждённом пейринге» (button-on-pairing/) — по времени
    delivery.json; ни одной — None. Страница, карта ростера и сверка с
    документами судят по нему (06.09.2026)."""
    best, best_t = None, -1.0
    for name in ("button", "button-on-pairing"):
        p = wd / name / "delivery.json"
        try:
            t = p.stat().st_mtime
        except OSError:
            continue
        if t > best_t:
            best, best_t = wd / name, t
    return best

_lock = threading.Lock()


class PackageRunError(RuntimeError):
    """Отказ нажатия словами (нет пакета, кнопка идёт, кривой параметр).

    `reason_code` (система прогонов, раздел 17.2) — машинный код отказа
    для ручек повтора (`run-busy`, `journal-mismatch`,
    `workdir-not-prepared`, `source-would-be-trimmed`, …); прежние
    вызовы без кода не меняются (`press-refused`)."""

    def __init__(self, msg: str, reason_code: str | None = None):
        super().__init__(msg)
        self.reason_code = reason_code


def runs_root() -> Path:
    return Path(_data_paths.data_root()) / RUNS_DIR_NAME


def current() -> dict | None:
    """Текущий пакет постановки (единый резолвер канала)."""
    from webapp import task_channel
    return task_channel.current_package()


def workdir_for(sha: str) -> Path:
    if not sha or not all(c in "0123456789abcdef" for c in sha):
        raise PackageRunError("отпечаток пакета не похож на sha256")
    return runs_root() / sha


def _state_path(wd: Path) -> Path:
    return wd / STATE_NAME


def read_state(wd: Path) -> dict:
    p = _state_path(wd)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(wd: Path, st: dict) -> dict:
    """Запись состояния на диск (вызывающий держит _lock)."""
    st["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    wd.mkdir(parents=True, exist_ok=True)
    target = _state_path(wd)
    # Временный файл — со своим суффиксом (два писателя не делят одно
    # имя), замена — с повторами при PermissionError: на Windows
    # os.replace падает «Access is denied», пока карточка прогона или
    # схема читают state.json (живой отказ повтора 07.09.2026: попытка
    # «повтор заново» упала на state.tmp → state.json). Та же формула,
    # что у хранилища прогонов (cosmos_runs._write_json_atomic).
    tmp = target.with_name("%s.%s.tmp" % (target.name, uuid.uuid4().hex[:12]))
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    for _ in range(40):
        try:
            os.replace(tmp, target)
            return st
        except PermissionError:
            time.sleep(0.025)
    os.replace(tmp, target)
    return st


def _mutate_state(wd: Path, fn) -> dict:
    """Чтение → правка → запись состояния ОДНИМ захватом замка: fn(st)
    правит словарь на месте и вправе отказать исключением — тогда на
    диск ничего не пишется. Через это идёт занятие кнопки (аудит A10,
    04.09.2026): проверка «свободна» и запись «занята» не разделяются."""
    with _lock:
        st = read_state(wd)
        fn(st)
        return _write_state(wd, st)


def _update_state(wd: Path, **fields) -> dict:
    return _mutate_state(wd, lambda st: st.update(fields))


# ── Кто нажал: актор попытки (разбор 09.09.2026) ──
# На Prod 09.09.2026 ростер был нажат вторым актором из браузера, и
# разобрать по записям, кто это, было нельзя: попытка называла номер
# процесса службы и время, но не того, кто нажал. С этой версии личность
# нажавшего (её отдаёт обработчик личности ручки) записывается в попытку,
# видна в списке попыток и называется в отказе «занято».

#: Слова актора, когда нажатие пришло без личности (внутренний вызов:
#: раннер набора, стенд, служебный запуск) — поле никогда не пустое.
ACTOR_UNKNOWN = "неизвестен: вызов без личности"
#: Слова актора у попытки, записанной ДО этой версии: актора тогда не
#: писали, и молчание записи не выдаётся за «нажали без личности».
ACTOR_PRE_VERSION = "неизвестен: попытка до записи актора"


def _token_fingerprint(token: str | None) -> str:
    """Отпечаток токена канала — 12 знаков sha256; САМ ТОКЕН и его части
    в запись не попадают (два разных канала по отпечатку различимы,
    обратного хода нет). Токена нет — «без отпечатка»."""
    import hashlib
    got = (token or "").strip()
    if not got:
        return "без отпечатка"
    return hashlib.sha256(got.encode("utf-8")).hexdigest()[:12]


def actor_words(identity: dict | None, *, channel_token: str | None = None,
                mode: str | None = None) -> str:
    """Кто нажал — одной строкой для записи попытки.

    `identity` — то, что вернул обработчик личности ручки
    (`webapp.app._user_or_channel`: словарь `{"email", "role"}`);
    машинный вход по токену канала приходит как `{"email": "channel",
    "role": "machine"}`. Формы записи: «user:<почта>» — человек в
    панели, «dev:<почта>» — режим разработчика площадки
    (`CREW_DEV_USER`), «channel:<отпечаток токена>» — машинный канал.
    Личности нет — `ACTOR_UNKNOWN` словами, пустой строки не бывает.
    `mode` — режим входа площадки; не назван — читается у `webapp.auth`
    (недоступен — считаем не режимом разработчика)."""
    if not isinstance(identity, dict) or not identity.get("email"):
        return ACTOR_UNKNOWN
    email = " ".join(str(identity["email"]).split())
    if identity.get("role") == "machine" or email == "channel":
        return "channel:" + _token_fingerprint(channel_token)
    if mode is None:
        try:
            from webapp import auth as _auth
            mode = _auth.get_auth_mode()
        except Exception:                                  # noqa: BLE001
            mode = None
    return ("dev:" if mode == "dev" else "user:") + email


def _actor_of(actor: str | None) -> str:
    """Актор для записи попытки: строка без лишних пробелов и длины;
    ничего не передано — `ACTOR_UNKNOWN` словами, не пусто."""
    got = " ".join(str(actor or "").split())
    return got[:200] or ACTOR_UNKNOWN


# ── Занятость кнопки и сторож сирот (аудит A10, 04.09.2026) ──
# Класс ошибки прежнего кода — «проверил одно поле, а меняю другое»:
# защита судила по тексту состояния «кнопка: идёт», а запись хода
# перезаписывала тот же текст (done не трогая), так что после первого
# шага повторное нажатие проходило и чистило каталог идущего расчёта;
# проверка и запись «идёт» шли двумя захватами замка — два одновременных
# нажатия проходили оба. Лечение: занятость судится ТОЛЬКО по флагу
# done (False = занята), текст хода живёт в отдельном ключе progress,
# занятие — одной операцией под замком (образец — _claim_stage панели
# CDPM). Живые нити ведутся реестром процесса: запись done=False без
# живой нити — сирота (гибель процесса посреди расчёта), её при старте
# и при первом обращении помечают «прервано перезапуском», иначе кнопка
# заперта навсегда. В состоянии хранятся pid и started_at нажатия.

#: Живые нити по линиям: ключ (каталог, линия) → Thread либо None
#: («занята, нить ещё стартует» — окно между занятием и запуском нити
#: тоже занято). Линии: "press" — кнопка, "export" — экспорт модели.
_RUNNING: dict[tuple[str, str], threading.Thread | None] = {}

#: Замок РЕЕСТРА живых нитей — отдельный от _lock (замка состояния на
#: диске). Правка реестра идёт только под ним, и «моя ли это запись» с
#: её снятием — ОДНОЙ операцией. Прежде проверка и снятие были двумя
#: операциями без замка, и между ними успевало занять ту же линию
#: следующее нажатие: нить прошлого нажатия снимала ЖИВУЮ запись
#: нового, после чего сторож объявлял идущий расчёт сиротой при живой
#: нити ТОГО ЖЕ процесса (дефект 09.09.2026 на местной службе: нажатие
#: «ростер» сразу за пейрингом получило «прервано перезапуском», а
#: решатель досчитал и записал вердикт поверх пометки).
#: Порядок захвата — только _lock → _reg_lock, никогда наоборот.
_reg_lock = threading.Lock()

#: Линии занятости: имя → (ключ флага «идёт», ключ pid, ключ момента
#: старта ПРОЦЕССА при занятии, ключ времени занятия, ключ пометки
#: «держит другой живой процесс», слова прерывания, ключ ошибки).
_LINES = {
    "press": dict(busy=lambda st: st.get("done") is False,
                  pid="pid", started="started_at",
                  pid_created="pid_created", holder="holder_note",
                  interrupted=dict(state="кнопка: прервано перезапуском",
                                   done=True, progress=None, verdict=None),
                  error="error", label="расчёт по кнопке"),
    "export": dict(busy=lambda st: st.get("export_mps_state")
                   == "экспорт идёт",
                   pid="export_mps_pid", started="export_mps_started_at",
                   pid_created="export_mps_pid_created",
                   holder="export_mps_holder_note",
                   interrupted=dict(export_mps_state=
                                    "экспорт: прерван перезапуском"),
                   error="export_mps_error", label="экспорт модели"),
}


_ABSENT = object()


def _line_key(wd: Path, line: str) -> tuple[str, str]:
    """Ключ реестра живых нитей — ОДНОЙ функцией и на запись, и на
    чтение. Рабочий каталог приходит из разных мест (resolve_current,
    workdir_of, workdir_for, cosmos_runs.workdir_of_package, состояние
    нажатия), и форма пути у них разная: относительный корень данных из
    переменной окружения, прямые и обратные косые, регистр буквы диска.
    Ключом по «сырой» строке одна и та же линия выглядела бы у писателя
    и у читателя разными — то есть живая нить «не найдена»."""
    return (os.path.normcase(os.path.abspath(str(wd))), line)


def _register_line(key: tuple[str, str], thread: threading.Thread) -> None:
    """Нить объявляет себя владельцем линии (под замком реестра)."""
    with _reg_lock:
        _RUNNING[key] = thread


def _unregister_line(key: tuple[str, str], thread) -> None:
    """Снять ТОЛЬКО свою запись: проверка и снятие — одной операцией
    под замком реестра. Занятие следующего нажатия, случившееся между
    ними, не теряется."""
    with _reg_lock:
        if _RUNNING.get(key, _ABSENT) is thread:
            _RUNNING.pop(key, None)


def _registry_note() -> str:
    """Короткая опись реестра живых нитей для пометки сироты: линия и
    12 знаков имени рабочего каталога (полных путей — не даём)."""
    with _reg_lock:
        keys = sorted("%s:%s" % (line, os.path.basename(path)[:12])
                      for path, line in _RUNNING)
    return ", ".join(keys) or "пусто"


def _line_alive(wd: Path, line: str) -> bool:
    with _reg_lock:
        t = _RUNNING.get(_line_key(wd, line), _ABSENT)
    if t is _ABSENT:
        return False
    return t is None or t.is_alive()


# ── Жив ли процесс с таким номером (разбор 09.09.2026) ──
# Прежде чужой номер процесса в записи означал смерть: линия объявлялась
# осиротевшей, и следующее нажатие чистило её каталог. Живой чужой
# процесс службы бывает в двух штатных случаях — перезапуск с перекрытием
# (старый ещё считает, новый уже отвечает) и две службы на разных портах
# с ОДНИМ каталогом данных: тогда линия ЗАНЯТА, а не осиротела. Проверка
# — без новых зависимостей (psutil в установке продукта нет): POSIX —
# сигнал 0, Windows — OpenProcess + GetExitCodeProcess через ctypes.

#: Код «процесс ещё работает» у GetExitCodeProcess (Windows).
_STILL_ACTIVE = 259
#: Право «спросить о процессе» — достаточно и для чужого владельца.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
#: «Отказано в доступе» (Windows): процесс есть, спросить о нём нельзя.
_ERROR_ACCESS_DENIED = 5
#: Разница эпох FILETIME (01.01.1601) и Unix (01.01.1970), секунды.
_FILETIME_EPOCH_DELTA = 11644473600.0
_kernel32_cache: list = []


def _kernel32():
    """Библиотека kernel32 с объявленными типами (иначе описатель
    процесса на 64 разряда обрезается int-ом по умолчанию); не Windows
    либо ctypes недоступен — None."""
    if _kernel32_cache:
        return _kernel32_cache[0]
    lib = None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            lib = ctypes.WinDLL("kernel32", use_last_error=True)
            lib.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL,
                                        wintypes.DWORD)
            lib.OpenProcess.restype = wintypes.HANDLE
            lib.GetExitCodeProcess.argtypes = (wintypes.HANDLE,
                                               ctypes.POINTER(wintypes.DWORD))
            lib.GetExitCodeProcess.restype = wintypes.BOOL
            lib.GetProcessTimes.argtypes = (
                wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME))
            lib.GetProcessTimes.restype = wintypes.BOOL
            lib.CloseHandle.argtypes = (wintypes.HANDLE,)
            lib.CloseHandle.restype = wintypes.BOOL
        except Exception:                                  # noqa: BLE001
            lib = None
    _kernel32_cache.append(lib)
    return lib


def _pid_int(pid) -> int | None:
    try:
        got = int(pid)
    except (TypeError, ValueError):
        return None
    return got if got > 0 else None


def _pid_alive(pid) -> bool:
    """Жив ли процесс с таким номером. Номер не разобран — False. Отказ
    в правах — True: процесс есть, он чужого пользователя (толковать
    отказ как смерть значило бы чистить чужой каталог)."""
    got = _pid_int(pid)
    if got is None:
        return False
    if os.name != "nt":
        try:
            os.kill(got, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    lib = _kernel32()
    if lib is None:
        return False
    import ctypes
    from ctypes import wintypes
    ctypes.set_last_error(0)
    handle = lib.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, got)
    if not handle:
        # Отказ в правах — процесс ЕСТЬ (служебные процессы и процессы
        # другого пользователя): толковать отказ как смерть значило бы
        # чистить каталог живого расчёта.
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        if not lib.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        lib.CloseHandle(handle)


def _pid_created_at(pid) -> str | None:
    """Момент старта процесса с таким номером по часам машины
    («%Y-%m-%dT%H:%M:%S»); узнать нельзя — None (нет прав, нет /proc,
    ctypes недоступен). По нему судится переиспользование номера ОС."""
    got = _pid_int(pid)
    if got is None:
        return None
    if os.name != "nt":
        try:
            born = os.stat("/proc/%d" % got).st_ctime
        except OSError:
            return None
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(born))
    lib = _kernel32()
    if lib is None:
        return None
    import ctypes
    from ctypes import wintypes
    handle = lib.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, got)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        rest = (wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME())
        if not lib.GetProcessTimes(handle, ctypes.byref(creation),
                                   ctypes.byref(rest[0]), ctypes.byref(rest[1]),
                                   ctypes.byref(rest[2])):
            return None
    finally:
        lib.CloseHandle(handle)
    ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
    born = ticks / 10_000_000.0 - _FILETIME_EPOCH_DELTA
    if born <= 0:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(born))


def _foreign_holder(st: dict, line: str) -> dict | None:
    """Чужой ЖИВОЙ процесс службы, держащий линию, — либо None.

    Номера процессов операционная система переиспользует, поэтому живой
    номер признаётся держателем только тогда, когда он не опровергнут
    временем: записанный при занятии момент старта процесса
    (`pid_created`) обязан совпасть с моментом старта живого процесса, а
    если записи нет (попытка прежней версии) — момент старта процесса не
    должен быть ПОЗЖЕ момента занятия линии (иначе номер достался другой
    программе после гибели службы). Момент старта не узнать — верим
    живому номеру: ошибиться в сторону «занято» безопаснее, чем чистить
    каталог идущего расчёта."""
    spec = _LINES[line]
    pid = _pid_int(st.get(spec["pid"]))
    if pid is None or pid == os.getpid() or not _pid_alive(pid):
        return None
    born = _pid_created_at(pid)
    want = st.get(spec.get("pid_created") or "")
    started = st.get(spec["started"])
    if want and born and str(want) != str(born):
        return None
    if not want and born and started and str(started) < str(born):
        return None
    return {"pid": pid, "created": born, "started": started}


def _foreign_holder_words(held: dict) -> str:
    """Пометка «линию держит другой живой процесс службы» — словами."""
    return ("линию держит другой живой процесс службы %s, начат %s; "
            "дождитесь итога или остановите тот процесс"
            % (held["pid"], held.get("started") or "время не записано"))


def holder_words(st: dict, line: str) -> str:
    """Кто держит линию — словами: чужой живой процесс службы либо
    попытка со своим актором и временем начала (отказ 409 и состояние
    страницы называют одно и то же)."""
    spec = _LINES[line]
    note = st.get(spec["holder"])
    if note:
        return str(note)
    started = st.get(spec["started"]) or "время не записано"
    if line != "press":
        return ("занято процессом службы %s (начат %s)"
                % (st.get(spec["pid"]) or "неизвестен", started))
    rec = _attempt_record(st, st.get("attempt_id"))
    actor = (rec or {}).get("actor") or st.get("actor") or ACTOR_UNKNOWN
    return ("занято попыткой %s, актор %s (начата %s)"
            % (st.get("attempt_id") or "без идентификатора", actor, started))


def _orphan_fields(wd: Path, st: dict, line: str) -> dict | None:
    """Поля пометки сироты, если запись линии занята, а живого держателя
    нет: свой pid без нити — нить погибла, не записав итог; чужой pid —
    только когда процесса с таким номером уже нет (либо номер достался
    другой программе). Живая линия и линия под чужим живым процессом
    службы → None (второе — не сирота, а занятость: разбор 09.09.2026)."""
    spec = _LINES[line]
    if not spec["busy"](st):
        return None
    if st.get(spec["pid"]) == os.getpid():
        if _line_alive(wd, line):
            return None
    elif _foreign_holder(st, line) is not None:
        return None
    started = st.get(spec["started"]) or "время старта не записано"
    # Хвост со сторожевой диагностикой: чей это процесс сейчас и что
    # лежит в реестре живых нитей — по пометке видно, ПОЧЕМУ линия
    # сочтена осиротевшей (разбор дефекта 09.09.2026).
    words = ("%s прерван перезапуском службы (процесс %s, начат %s): "
             "итога нет, нажмите снова [сторож: служба %d, реестр живых "
             "нитей — %s]"
             % (spec["label"], st.get(spec["pid"]) or "неизвестен",
                started, os.getpid(), _registry_note()))
    return dict(spec["interrupted"], **{spec["error"]: words})


#: Каталог попыток нажатия внутри каталога пакета (аудит A10, ступень M):
#: attempts/<id>/<button-каталог> — снимок артефактов попытки.
ATTEMPTS_DIR = "attempts"
#: Предел попыток — настройка площадки CREW_ATTEMPTS_KEEP (система
#: прогонов, раздел 7.5): умолчание 20 — прежнее; 0 — без предела.
#: Имя константы остаётся для совместимости (= умолчание).
ATTEMPTS_KEEP = 20
ATTEMPTS_KEEP_ENV = "CREW_ATTEMPTS_KEEP"


def attempts_keep() -> int:
    """Предел хранимых попыток: целое ≥ 0 из CREW_ATTEMPTS_KEEP (0 — без
    предела); кривое значение — умолчание с заметкой в журнале службы."""
    raw = os.environ.get(ATTEMPTS_KEEP_ENV)
    if raw is None or not raw.strip():
        return ATTEMPTS_KEEP
    try:
        got = int(raw.strip())
        if got < 0:
            raise ValueError("отрицательное значение")
        return got
    except ValueError:
        log.warning("%s=%r не разобран как целое ≥ 0 — действует умолчание %d",
                    ATTEMPTS_KEEP_ENV, raw, ATTEMPTS_KEEP)
        return ATTEMPTS_KEEP


def _new_attempt_id() -> str:
    """Идентификатор попытки нажатия: момент старта + случайный хвост —
    читается человеком, различим при двух нажатиях в одну секунду."""
    import secrets
    return time.strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)


def _attempt_record(st: dict, attempt_id: str | None) -> dict | None:
    for rec in st.get("attempts") or ():
        if isinstance(rec, dict) and rec.get("id") == attempt_id:
            return rec
    return None


def attempt_view(rec: dict, holder: str | None = None) -> dict:
    """Запись попытки для выдачи наружу: копия с дописанными полями
    прежних версий — держатель рабочего каталога у попыток без ключа
    `run` и актор словами у попыток без ключа `actor` (пустым это поле
    наружу не уходит: молчание записи названо кодом словами)."""
    out = dict(rec)
    if "run" not in out and holder:
        out["run"] = holder
    if not out.get("actor"):
        out["actor"] = ACTOR_PRE_VERSION
    return out


def _mark_attempt(st: dict, attempt_id: str | None, **fields) -> None:
    """Правка записи попытки в состоянии (на месте; вызывающий держит
    замок либо идёт через _mutate_state)."""
    rec = _attempt_record(st, attempt_id)
    if rec is not None:
        rec.update(fields)


def _sweep_orphans(wd: Path, st: dict) -> list[str]:
    """Пометка сирот всех линий в словаре состояния (на месте, вызывающий
    держит _lock); возвращает строки для журнала службы.

    Здесь же обновляется пометка «линию держит другой живой процесс
    службы» (ключ `holder` линии): она выводится из состояния при каждом
    чтении и потому не залёживается — умерший чужой процесс на следующем
    же обращении становится сиротой, а не вечным держателем."""
    out = []
    for line in _LINES:
        spec = _LINES[line]
        held = _foreign_holder(st, line) if spec["busy"](st) else None
        if held:
            st[spec["holder"]] = _foreign_holder_words(held)
        else:
            st.pop(spec["holder"], None)
        fields = _orphan_fields(wd, st, line)
        if fields:
            st.update(fields)
            if line == "press":
                # A10 (ступень M): прерванная попытка помечается по ID —
                # её запись и каталог не выдают себя за итог.
                _mark_attempt(st, st.get("attempt_id"),
                              state="прервано перезапуском", done=True,
                              finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                              error=fields.get(_LINES[line]["error"]))
            out.append("%s: %s → «%s»"
                       % (wd.name[:12], _LINES[line]["label"],
                          fields[_LINES[line]["error"]]))
    return out


def _read_state_swept(wd: Path) -> dict:
    """Состояние с пометкой сирот (первое обращение после перезапуска
    чинит запись на диске)."""
    with _lock:
        st = read_state(wd)
        if st and _sweep_orphans(wd, st):
            _write_state(wd, st)
        return st


def _state_dirs(root: Path) -> list[Path]:
    """Рабочие каталоги с state.json: базовые `package-runs/<sha>/` и
    вторые держатели `package-runs/<sha>/runs/<run>/` (раздел 7.6)."""
    out: list[Path] = []
    if not root.is_dir():
        return out
    for wd in sorted(root.iterdir()):
        if not wd.is_dir():
            continue
        if (wd / STATE_NAME).is_file():
            out.append(wd)
        runs = wd / "runs"
        if runs.is_dir():
            for sub in sorted(runs.iterdir()):
                if sub.is_dir() and (sub / STATE_NAME).is_file():
                    out.append(sub)
    return out


def recover_orphans() -> list[str]:
    """Сторож сирот при старте службы: по всем каталогам нажатий записи
    «занята» без живой нити помечаются «прервано перезапуском».
    Возвращает строки для журнала службы."""
    root = runs_root()
    if not root.is_dir():
        return []
    lines: list[str] = []
    for wd in _state_dirs(root):
        with _lock:
            st = read_state(wd)
            got = _sweep_orphans(wd, st)
            if got:
                _write_state(wd, st)
        lines.extend(got)
    return lines


def _claim_line(wd: Path, line: str, busy_words: str, also: tuple = (),
                **fields) -> dict:
    """Занятие линии: проверка «свободна» (по флагу, с пометкой сироты)
    и запись «занята» — одной операцией под замком. Занята живой нитью
    либо чужим живым процессом службы — PackageRunError словами, на диск
    ничего не пишется; слова называют держателя (`holder_words`), чтобы
    отказ 409 читался «занято попыткой user:…» либо «линию держит другой
    живой процесс службы …». `also` — линии, которые тоже обязаны быть
    свободны (их каталог занятие трогает)."""
    spec = _LINES[line]

    def _apply(st: dict) -> None:
        _sweep_orphans(wd, st)
        if spec["busy"](st):
            raise PackageRunError("%s: %s" % (busy_words,
                                              holder_words(st, line)),
                                  reason_code="run-busy")
        for other in also:
            if _LINES[other]["busy"](st):
                raise PackageRunError("%s ещё идёт — дождитесь его конца: %s"
                                      % (_LINES[other]["label"],
                                         holder_words(st, other)),
                                      reason_code="run-busy")
        st.update(fields)
        st[spec["pid"]] = os.getpid()
        st[spec["started"]] = time.strftime("%Y-%m-%dT%H:%M:%S")
        # Момент старта СВОЕГО процесса — в записи занятия: по нему
        # чужая служба отличит живой процесс-держатель от чужой
        # программы, которой операционная система отдала тот же номер.
        st[spec["pid_created"]] = _pid_created_at(os.getpid())
        st.pop(spec["holder"], None)
        # Реестр правится под своим замком (порядок _lock → _reg_lock):
        # запись «занята, нить ещё стартует» появляется ДО того, как
        # «занята» ляжет на диск, — читатель между этими мигами видит
        # линию живой, а не осиротевшей.
        with _reg_lock:
            _RUNNING[_line_key(wd, line)] = None
    return _mutate_state(wd, _apply)


def _run_line(wd: Path, line: str, work, start_thread: bool) -> None:
    """Запуск работы занятой линии: нить регистрируется в реестре
    живых; итог линии обязан записать сам work (done/флаг)."""
    key = _line_key(wd, line)

    def _guarded():
        # Нить регистрирует себя сама (до этого в реестре None — «занята,
        # стартует») и снимает ТОЛЬКО свою запись ОДНОЙ операцией под
        # замком реестра: следующее занятие той же линии её не теряет.
        me = threading.current_thread()
        _register_line(key, me)
        try:
            work()
        finally:
            _unregister_line(key, me)
    if start_thread:
        threading.Thread(target=_guarded, daemon=True).start()
    else:
        _guarded()


def _release_line(wd: Path, line: str, **fields) -> None:
    """Освобождение линии до старта работы (отказ между занятием и
    запуском нити) — итог словами, реестр чист. Снимается ТОЛЬКО своя
    заявка «нить ещё стартует» (значение None): живую нить, взявшую
    линию, освобождение чужого отказа не трогает."""
    _unregister_line(_line_key(wd, line), None)
    _update_state(wd, **fields)


def package_scope(pkg_path: Path) -> str:
    """Допуск моделей — по признаку КОМПЛЕКТА в манифесте пакета:
    kit.llm_scope — ДЕЙСТВУЮЩИЙ круг, который CDPM вычисляет одним
    правилом (явный признак → он; иначе умолчание по классу данных) и
    кладёт в пакет (дополнение к 1.14 от 04.09.2026). Здесь правило не
    пересчитывается — двух реализаций быть не должно; нет ключа или
    он не из словаря — только локальные модели (правило владельца:
    «нет признака — только локальные»)."""
    import io
    import zipfile
    try:
        man = json.loads(zipfile.ZipFile(io.BytesIO(pkg_path.read_bytes()))
                         .read("manifest.json"))
    except Exception:                                    # noqa: BLE001
        return "local-only"
    kit = man.get("kit") if isinstance(man.get("kit"), dict) else {}
    scope = kit.get("llm_scope")
    return scope if scope in ("any", "local-only") else "local-only"


#: Настройки установки: модель и канал компилятора проверок сертификата
#: при допуске «any» (решения владельца 04.09: «Должна быть модель
#: claude sonnet 5»; «Давай CLI с моделью» — агентный CLI подписки с
#: ЯВНОЙ моделью флагом --model; API — по настройке, при ключе COSMOS).
CERTIFIER_MODEL_ENV = "CREW_CERTIFIER_MODEL"
CERTIFIER_CHANNEL_ENV = "CREW_CERTIFIER_CHANNEL"
CERTIFIER_CHANNELS = ("cli", "api")


def certifier_model() -> str | None:
    return (os.environ.get(CERTIFIER_MODEL_ENV) or "").strip() or None


def certifier_channel() -> str:
    got = (os.environ.get(CERTIFIER_CHANNEL_ENV) or "cli").strip().lower()
    return got if got in CERTIFIER_CHANNELS else got


def certifier_identity(scope: str) -> dict:
    """Канал и модель компилятора проверок сертификата — что именно
    сертифицирует (правило владельца о согласованных моделях: карточка
    серии обязана назвать модель). При допуске «any»: модель — только
    из настройки CREW_CERTIFIER_MODEL (без неё — error, нажатие
    отказывает до старта); канал — CREW_CERTIFIER_CHANNEL: «cli»
    (умолчание) — агентный CLI подписки с флагом --model, нужен
    найденный CLI; «api» — Messages API, нужен ключ API в настройках
    COSMOS (тот, по которому канал реально выбирает API). При
    «local-only» — локальная модель установки."""
    from webapp import llm_backend
    if scope == "any":
        model = certifier_model()
        channel = certifier_channel()
        out = {"scope": scope, "backend": "cloud", "channel": channel,
               "model": model}
        if not model:
            out["error"] = ("модель сертификатора площадки не задана "
                            "(%s): при допуске «any» канал строится "
                            "только с названной моделью"
                            % CERTIFIER_MODEL_ENV)
            return out
        if channel not in CERTIFIER_CHANNELS:
            out["error"] = ("канал сертификатора %r вне словаря %s (%s)"
                            % (channel, "/".join(CERTIFIER_CHANNELS),
                               CERTIFIER_CHANNEL_ENV))
            return out
        if channel == "api":
            # Ключ «cloud» из настроек CDPM — каналу выполнения COSMOS
            # (только в память процесса, как делает панель CDPM на
            # старте): веб-приложение COSMOS само его не прокидывает, и
            # без этого канал молча уезжал бы в CLI.
            try:
                from cdpm import providers as providers_mod
                providers_mod.sync_cloud_key_to_env()
            except Exception:                              # noqa: BLE001
                pass
            try:
                from webapp import settings as _settings
                has_key = bool(_settings.has_api_key())
            except Exception:                              # noqa: BLE001
                has_key = False
            if not has_key:
                out["error"] = ("ключ API облака не задан в настройках "
                                "COSMOS — канал api невозможен")
        else:
            try:
                cli = llm_backend.resolve_claude_cli()
            except Exception:                              # noqa: BLE001
                cli = None
            if not cli:
                out["error"] = ("агентный CLI не найден на площадке — "
                                "канал cli невозможен")
        return out
    try:
        model = llm_backend.ollama_model()
    except Exception:                                      # noqa: BLE001
        model = None
    return {"scope": scope, "backend": "local", "channel": "local",
            "model": model}


def certifier_passport(identity: dict) -> dict:
    """Расширенная запись сертификатора попытки (раздел 8.4): к
    `certifier_identity` — `provider` («anthropic» при backend cloud,
    «ollama» при local), `effort`/`temperature` (каналы cli/api их не
    задают → null, код `channel-does-not-set`; local → 0.2, код
    `ollama-default`). Форма согласована с made_by.models CDPM. Шапка
    сертификата и запись попытки берутся из ЭТОГО словаря (правило одной
    переменной, раздел 17.2)."""
    out = dict(identity or {})
    backend = out.get("backend")
    if backend == "cloud":
        out.setdefault("provider", "anthropic")
        out.setdefault("effort", None)
        out.setdefault("temperature", None)
        out.setdefault("note_code", "channel-does-not-set")
    elif backend == "local":
        out.setdefault("provider", "ollama")
        out.setdefault("effort", None)
        out.setdefault("temperature", 0.2)
        out.setdefault("note_code", "ollama-default")
    else:
        out.setdefault("provider", None)
        out.setdefault("effort", None)
        out.setdefault("temperature", None)
        out.setdefault("note_code", None)
    return out


def cloud_certifier_channel(model: str, channel: str,
                            timeout_s: float = 300.0,
                            attempts: int = 3, backoff_s: float = 15.0):
    """Вызываемый канал сертификатора для допуска «any»: «cli» — CLI
    подписки с флагом --model (ровно эта модель, без подстановок);
    «api» — Messages API той же моделью. Три попытки с паузой, как у
    cosmos_llm; отказ — словами с каналом и моделью.

    Расход: после каждой попытки канал кладёт разобранный usage трубы в
    свой атрибут `last_usage` — писатель обменов снимает его оттуда и
    сводит в `resources.models[]` записи попытки (решение владельца № 1
    от 10.09.2026: считаем токены по каждой модели, не деньги). Канал
    cli токенов не отдаёт — там стоит «не считается» с причиной."""
    from webapp import llm_backend

    def _call(prompt: str) -> str:
        last: dict = {}
        for i in range(attempts):
            if channel == "cli":
                try:
                    text = llm_backend._cli_generate(prompt, timeout_s,
                                                     model=model)
                    _call.last_usage = llm_backend.peek_last_usage()
                    if text:
                        return text
                    last = {"error": "пустой ответ CLI"}
                except Exception as e:                     # noqa: BLE001
                    _call.last_usage = llm_backend.peek_last_usage()
                    last = {"error": str(e)}
            else:
                got = llm_backend.complete(prompt, backend="cloud",
                                           timeout_s=timeout_s, model=model)
                _call.last_usage = got.get("usage")
                if got.get("ok"):
                    return got["text"]
                last = got
            if i + 1 < attempts:
                time.sleep(backoff_s * (i + 1))
        raise RuntimeError("канал сертификатора %s/%s отказал после %d "
                           "попыток: %s" % (channel, model, attempts,
                                             last.get("error")))
    _call.last_usage = None
    return _call


def _clear_press_artifacts(out: Path) -> None:
    """Артефакты прежнего нажатия того же каталога убираются ДО старта:
    остановка кнопки перезаписывает выдачу, а старый сертификат и
    решение остались бы лежать как живые (заявка CDPM 04.09)."""
    if out.is_dir():
        import shutil
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)


#: Адрес решателя — ТОЛЬКО явная настройка площадки CREW_SOLVER_URL.
#: Умолчания нет намеренно (решение владельца 04.09, «полная симметрия
#: контуров»): подстановка порта конкретного контура (8099 = решатель
#: Prod) уводила Test и Stage считать на чужом решателе молча; без
#: явной строки — честный отказ словами, расчёт не начинается.
SOLVER_URL_ENV = "CREW_SOLVER_URL"


def solver_url() -> str:
    got = os.environ.get(SOLVER_URL_ENV, "").strip()
    if not got:
        raise PackageRunError(
            "адрес решателя не задан установкой площадки (%s) — "
            "укажите его в пусковом файле; умолчания нет, чтобы контур "
            "не считал на чужом решателе" % SOLVER_URL_ENV,
            reason_code="solver-url-missing")
    return got


def approved_pairing(wd: Path) -> dict | None:
    """Прогон пейринга этого пакета по выдаче нажатия pairing в рабочем
    каталоге (button-pairing/delivery.json): прогон у решателя, вердикт,
    отпечаток каталога, число цепочек. None — выдачи нет. Шаг 3 плана
    05.09.2026: второе нажатие называет прогон явно и сверяется с этим."""
    p = Path(wd) / "button-pairing" / "delivery.json"
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    return {"solver_run_id": (d.get("schedule") or {}).get("solver_run_id"),
            "verdict": d.get("verdict"),
            "catalog_sha256": d.get("catalog_sha256"),
            "chains": d.get("chains"),
            "approved": d.get("verdict") == "пейринг построен"}


# ── Явный выбор попытки пейринга для ростеринга (единая версия
# 06.09.2026, W-4/W-5; источник — шаг 3 GPT на Test legion) ──
# Кандидаты — ЗАВЕРШЁННЫЕ попытки pairing со снимком артефактов
# (attempts/<id>/button-pairing, аудит A10 ступень M), любая из
# сохранённых, не только последняя; порядок списка выбором не является.
# До занятия кнопки выбранный прогон проверяется по своему снимку:
# байты пакета, SHA каталога, состав и данные рейсов против текущего
# пакета, разбиение обязательных рейсов, запись прогона у решателя.
# Отказ — словами до счёта (fail-closed), подмены «последним» нет.

def _replay_mode_of(rec: dict) -> str | None:
    """Режим повтора записи попытки (`replay.of.mode`) либо None."""
    of = (rec.get("replay") or {}).get("of") if isinstance(rec.get("replay"), dict) else None
    return of.get("mode") if isinstance(of, dict) else None


def _pairing_candidates(wd: Path, st: dict) -> list[dict]:
    """Именованные завершённые попытки пейринга (новые первыми); порядок
    списка НЕ означает выбор — прогон называется явно. Попытки-повторы
    из журнала пропускаются: их выдача несёт solver_run_id источника, и
    без пропуска выбор нашёл бы два совпадения на один прогон (17.2)."""
    out = []
    for rec in reversed(st.get("attempts") or []):
        if not isinstance(rec, dict) or rec.get("task") != "pairing" or not rec.get("done"):
            continue
        if _replay_mode_of(rec) == "journal":
            continue
        attempt = str(rec.get("id") or "")
        if not attempt or not all(c.isalnum() or c in "-_" for c in attempt):
            continue
        root = wd / ATTEMPTS_DIR / attempt / "button-pairing"
        try:
            delivery = json.loads((root / "delivery.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(delivery, dict):
            continue
        run_id = (delivery.get("schedule") or {}).get("solver_run_id")
        if not run_id or run_id == "None" or not delivery.get("catalog_sha256"):
            continue
        out.append({"pairing_run_id": str(run_id), "attempt_id": attempt,
                    "catalog_sha256": delivery["catalog_sha256"],
                    "package_fingerprint": (delivery.get("trace") or {}).get("package_fingerprint"),
                    "chains": delivery.get("chains"),
                    "coverage": delivery.get("coverage"),
                    "verdict": delivery.get("verdict"),
                    "approved": delivery.get("verdict") == "пейринг построен",
                    "finished_at": rec.get("finished_at"),
                    "engine": rec.get("engine")})
    return out


def _flight_composition(payload: dict) -> dict[str, dict]:
    """Рейсы набора по идентификатору — состав и данные целиком."""
    flights = payload.get("flights")
    if not isinstance(flights, list) or not flights:
        raise PackageRunError("состав рейсов выбранного пейринга пуст")
    out = {}
    for flight in flights:
        if not isinstance(flight, dict) or not flight.get("flightNumber"):
            raise PackageRunError("в составе рейсов нет идентификатора")
        number = str(flight["flightNumber"])
        if number in out:
            raise PackageRunError("повтор идентификатора рейса: %s" % number)
        out[number] = flight
    return out


def validate_pairing_selection(wd: Path, package_path: Path, package_sha: str,
                               pairing_run_id: str, solver) -> dict:
    """Проверяет байты пакета/каталога и полный состав рейсов ДО запуска.

    Источник — снимок ЯВНО названного прогона, не последний каталог.
    Запись прогона у решателя (status runId) связывает его неизменяемые
    цепочки со снимком: runId/задача/состояние «готов», отпечаток пакета
    = sha256(manifest.json) текущего пакета = отпечаток снимка; SHA
    каталога у решателя (catalogSha256) сверяется, если решатель его
    сообщает, иначе показание честно «не подтверждено решателем» (в
    статусе прогона решателя main этого поля нет; Г-П4 сверяет SHA при
    регистрации каталога) — не отказ и не зелёный.
    """
    import hashlib
    import zipfile
    from webapp import task_package

    matches = [x for x in _pairing_candidates(wd, read_state(wd))
               if x["pairing_run_id"] == pairing_run_id]
    if len(matches) != 1:
        raise PackageRunError("выбранный прогон пейринга %r не найден однозначно "
                              "среди попыток этого пакета; выберите прогон явно"
                              % pairing_run_id)
    selected = dict(matches[0])
    root = wd / ATTEMPTS_DIR / selected["attempt_id"] / "button-pairing"
    try:
        if _sha256(package_path) != package_sha or _sha256(root / "package.zip") != package_sha:
            raise PackageRunError("пакет выбранного пейринга не совпадает с текущим пакетом")
        raw = (root / "builder-catalog.json").read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != selected["catalog_sha256"]:
            raise PackageRunError("SHA каталога выбранного пейринга изменился")
        catalog = json.loads(raw)
        previous = _flight_composition(json.loads(
            (root / "builder-payload.json").read_text(encoding="utf-8")))
        current_flights = _flight_composition(task_package.to_payload(
            task_package.load(package_path, run_gate=False)))
        if previous != current_flights:
            raise PackageRunError("состав или данные рейсов выбранного пейринга "
                                  "не совпадают с текущим пакетом")
        obligations = {number for number, flight in current_flights.items()
                       if not flight.get("transportOnly")}
        covered = []
        chains = catalog.get("selected")
        if not isinstance(chains, list) or not chains:
            raise PackageRunError("выбранный каталог не содержит цепочек")
        for chain in chains:
            if not isinstance(chain, dict):
                raise PackageRunError("неверная запись цепочки в выбранном каталоге")
            all_legs = chain.get("fns") or chain.get("active_fns")
            if (not isinstance(all_legs, list) or not all_legs
                    or any(str(x) not in current_flights for x in all_legs)):
                raise PackageRunError("выбранный каталог ссылается на чужие рейсы")
            legs = chain.get("active_fns")
            if legs is None:
                legs = [x for x in all_legs if str(x) in obligations]
            if not isinstance(legs, list) or not legs:
                raise PackageRunError("пустая цепочка в выбранном каталоге")
            if not set(map(str, legs)).issubset(set(map(str, all_legs))):
                raise PackageRunError("рабочие рейсы отсутствуют в маршруте цепочки")
            covered.extend(str(x) for x in legs)
        if len(covered) != len(set(covered)) or set(covered) != obligations:
            raise PackageRunError("выбранный каталог не является полным разбиением "
                                  "рейсов текущего пакета. " + PAIRING_RETRY_NOTE)
        with zipfile.ZipFile(package_path) as zf:
            fingerprint = hashlib.sha256(zf.read("manifest.json")).hexdigest()
        remote = solver.status(pairing_run_id)
        if (not isinstance(remote, dict) or remote.get("runId") != pairing_run_id
                or remote.get("task") != "pairing" or remote.get("state") != "готов"):
            raise PackageRunError("выбранный прогон отсутствует у решателя либо "
                                  "не является завершённым пейрингом")
        if remote.get("packageFingerprint") != fingerprint or selected["package_fingerprint"] != fingerprint:
            raise PackageRunError("отпечаток пакета выбранного прогона решателя не совпадает")
        if "catalogSha256" in remote:
            if remote.get("catalogSha256") != digest:
                raise PackageRunError("решатель не подтвердил SHA выбранного каталога; "
                                      "повторите пейринг и выберите новый прогон")
            solver_catalog = {"confirmed": True, "catalog_sha256": digest}
        else:
            solver_catalog = {"confirmed": None,
                              "note": "статус прогона решателя не сообщает "
                                      "catalogSha256 — SHA каталога сверен по снимку "
                                      "попытки и выдаче, решателем не подтверждён"}
        selected.update(package_sha256=package_sha, flight_count=len(current_flights),
                        obligation_flight_count=len(obligations),
                        solver_catalog=solver_catalog,
                        verified=True)
        return selected
    except PackageRunError:
        raise
    except Exception as e:                                    # noqa: BLE001
        # Рассинхрон повтора из журнала до занятия кнопки (ReplayMismatch)
        # переносит свой код отказа (раздел 17.2): ручка отдаёт 409 с ним.
        raise PackageRunError("выбранный пейринг не проверен: %s" % str(e)[:500],
                              reason_code=getattr(e, "reason_code", None)) from None


# ── Резолвер: текущий пакет = пакет текущего прогона (раздел 5.3) ──

class Resolved(NamedTuple):
    """Одно разрешение текущего пакета: запись резолвера, sha, путь
    пакета, рабочий каталог, запись прогона (None — легаси и стенды)."""
    cur: dict
    sha: str
    path: Path
    wd: Path
    run: dict | None


def _cosmos_runs():
    """Модуль хранилища прогонов — ленивый импорт: cosmos_runs сам
    импортирует package_runs внутри функций, цикла на уровне модулей нет."""
    from webapp import cosmos_runs as _cr
    return _cr


def _storage_ready() -> bool:
    """Заведено ли хранилище прогонов (storage.json на месте, раздел 3.1);
    выключенное или не заведённое — False, ни байта нового на диске."""
    try:
        return _cosmos_runs().storage_state() == "ready"
    except OSError:
        return False


def _holder_run(wd: Path) -> str | None:
    """Прогон-держатель рабочего каталога по holder.json (чтение)."""
    p = Path(wd) / HOLDER_NAME
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if isinstance(doc, dict) and doc.get("kind") == "run" and doc.get("run"):
        return str(doc["run"])
    return None


def _rel_workdir(wd: Path) -> str:
    """Рабочий каталог относительно каталога данных (разделитель `/`);
    вне каталога данных (стенды с подменой) — абсолютный путь."""
    try:
        return Path(wd).resolve().relative_to(
            Path(_data_paths.data_root()).resolve()).as_posix()
    except (ValueError, OSError):
        return Path(wd).as_posix()


def workdir_of(cur: dict | None, sha: str) -> Path:
    """Рабочий каталог пары (прогон, пакет): `data_root()/<run.workdir>`
    при записи прогона у резолвера, иначе `workdir_for(sha)` (сигнатура
    с одним аргументом сохранена — её подменяют стенды)."""
    run = cur.get("run") if isinstance(cur, dict) else None
    if isinstance(run, dict) and run.get("workdir"):
        return Path(_data_paths.data_root()) / str(run["workdir"])
    return workdir_for(sha)


def resolve_current() -> Resolved | None:
    """Одно разрешение текущего пакета; None — пакета нет либо файл
    утерян (потребители честно считают «пакета нет»)."""
    cur = current()
    if cur is None:
        return None
    path = cur.get("path")
    if not path or not Path(path).is_file():
        return None
    sha = cur.get("sha") or cur.get("sha256") or _sha256(Path(path))
    run = cur.get("run") if isinstance(cur.get("run"), dict) else None
    return Resolved(cur, sha, Path(path), workdir_of(cur, sha), run)


# ── Один слот текущего пакета (живой случай 11.09.2026) ──
# Слот текущего пакета площадки ОДИН. Пока по нему идёт незаконченная
# попытка нажатия, подменять пакет нельзя: попытка досчитывает ОДИН
# пакет, а площадка уже показывает ДРУГОЙ — выдача, сертификат и сверка
# с ИДАК расходятся с тем, что видит человек. Живой случай 11.09.2026:
# чужой сценарий подал новый пакет между нажатиями, и разобрать, к
# какому пакету относится результат, стало нельзя. Лечение: приём
# нового пакета при живой попытке отказывает СЛОВАМИ, а не подменяет
# слот молча.

#: Код отказа приёма пакета при занятом слоте.
CODE_SLOT_BUSY = "slot-busy"


def current_press_hold() -> dict | None:
    """Незавершённая попытка нажатия по ТЕКУЩЕМУ пакету — «слот занят».

    Завершённая попытка слот освобождает: и та, что дошла до итога, и
    та, что кончилась явным отказом, — обе ставят `done`. Осиротевшая
    (служба умерла посреди счёта) освобождает тоже: состояние читается
    СО СТОРОЖЕМ сирот, тем же, что у страницы нажатия, — иначе слот
    запирался бы навсегда после падения службы.

    Возврат: `{package_sha, attempt_id, actor, started_at, words}` либо
    None — слот свободен."""
    res = resolve_current()
    if res is None:
        return None
    st = _read_state_swept(res.wd)
    if not _LINES["press"]["busy"](st):
        return None
    rec = _attempt_record(st, st.get("attempt_id"))
    return {"package_sha": res.sha,
            "attempt_id": st.get("attempt_id") or "без идентификатора",
            "actor": (rec or {}).get("actor") or st.get("actor")
            or ACTOR_UNKNOWN,
            "started_at": st.get("started_at") or "время не записано",
            "words": holder_words(st, "press")}


# ── Паспортные поля попытки (раздел 7.3) ──

#: Ключи параметров нажатия, обязательные для повтора (17.2:
#: «допустимо, если параметры записаны»).
PARAM_KEYS = ("mode", "objective_id", "spent_limit_s",
              "unimproved_spent_limit_s", "pairing_run_id", "moves",
              "accepted_uncovered", "poll_s", "deterministic")

#: Предел счёта кнопки по умолчанию, секунды — ОДНО место (его же
#: называет объявление прогона от пакета: «решатели с параметрами
#: кнопки по умолчанию», проект записи о прогоне, раздел 10).
PRESS_DEFAULT_SPENT_LIMIT_S = 60


def _code_identity() -> dict:
    """`build_identity()["source"]`: срез кода панели при нажатии."""
    try:
        from webapp import build_identity as _bi
        src = _bi.build_identity().get("source") or {}
        out = {"commit": src.get("commit"), "commit_short": src.get("commit_short"),
               "tree_dirty": src.get("tree_dirty"),
               "dirty_paths": src.get("dirty_paths")}
        if not out["commit"]:
            out["reason_code"] = "no-git"
        return out
    except Exception as e:                                 # noqa: BLE001
        return {"commit": None, "commit_short": None, "tree_dirty": None,
                "dirty_paths": None, "reason_code": "no-git",
                "error": str(e)[:200]}


def _catalog_stamp() -> dict:
    """Версия и отпечаток каталога шаблонов (vmp.catalog_access.stamp в try)."""
    try:
        from vmp import catalog_access as _ca
        st = _ca.stamp()
        return {"version": st.get("version"), "fingerprint": st.get("fingerprint")}
    except Exception as e:                                 # noqa: BLE001
        return {"version": None, "fingerprint": None,
                "reason_code": "catalog-unavailable", "error": str(e)[:300]}


def _catalog_manifest_check(pkg_path: Path) -> dict:
    """Сторож дрейфа каталога шаблонов ПЕРЕД НАЖАТИЕМ (§8 схемы
    каталога, шаг 3): ключ пакета `manifest.catalog` против каталога,
    которым идёт продукт. Слова и исход судит дверь
    (`vmp.catalog_access.manifest_check`) — здесь только чтение
    манифеста и перевод отказов среды в честную пометку: нечитаемый
    пакет и недоступный каталог судят свои рубежи (гейт пакета Ш-1,
    Ш-1п2), сторож на них не подменяется."""
    from vmp import catalog_access as _ca
    import zipfile
    try:
        with zipfile.ZipFile(Path(pkg_path)) as zf:
            man = json.loads(zf.read("manifest.json"))
    except Exception as e:                                 # noqa: BLE001
        return {"state": None, "words": None,
                "reason_code": "manifest-unreadable", "error": str(e)[:200]}
    try:
        return _ca.manifest_check(man if isinstance(man, dict) else {})
    except Exception as e:                                 # noqa: BLE001
        return {"state": None, "words": None,
                "reason_code": "catalog-unavailable", "error": str(e)[:300]}


def _engine_versions() -> dict | None:
    """Версии движков пейринга того же интерпретатора (importlib.metadata)."""
    from importlib import metadata as _md
    out: dict = {}
    for name in ("ortools", "highspy"):
        try:
            out[name] = _md.version(name)
        except Exception:                                  # noqa: BLE001
            out[name] = None
    return out or None


def _solver_build() -> tuple[dict | None, str]:
    """Сборка решателя на машине панели — блок `solver` окружения
    паспорта (`cosmos_runs.environment()`, раздел 8.1; кэш по ключу
    настроек); нет файлов сборки — (None, «no-build-files»)."""
    solver = (_cosmos_runs().environment() or {}).get("solver") or {}
    build = solver.get("build")
    return (build if isinstance(build, dict) else None,
            str(solver.get("version_source_code")
                or ("build-files" if build else "no-build-files")))


def _registry_summary(path: Path) -> dict | None:
    """Копия реестра в снимке попытки: источник, sha256 содержимого БЕЗ
    `created` (канонический JSON), блок catalog (раздел 17.3)."""
    p = Path(path)
    if not p.is_file():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {"source": "derived", "sha256": None, "catalog": None,
                "reason_code": "registry-unreadable"}
    if not isinstance(doc, dict):
        return None
    from vmp.run import call_journal as _cj
    body = dict(doc)
    body.pop("created", None)
    cat = doc.get("catalog") if isinstance(doc.get("catalog"), dict) else None
    return {"source": "derived",
            "sha256": _cj.canon_sha256(body),
            "catalog": ({"version": cat.get("version"),
                         "fingerprint": cat.get("fingerprint")} if cat else None)}


def _delivery_summary(delivery: dict | None) -> dict | None:
    """Сводка выдачи для списка (момент Н+): список не читает артефакты."""
    if not isinstance(delivery, dict):
        return None
    sched = delivery.get("schedule") if isinstance(delivery.get("schedule"), dict) else {}
    unc = delivery.get("uncovered")
    gate = delivery.get("certificate_gate")
    return {"verdict": delivery.get("verdict"),
            "certified": delivery.get("certified"),
            "result": delivery.get("result"),
            "mode": delivery.get("mode"),
            "solver_run_id": sched.get("solver_run_id"),
            "score": sched.get("score"),
            "chains": delivery.get("chains"),
            "findings": delivery.get("findings"),
            "uncovered": len(unc) if isinstance(unc, list) else unc,
            "certificate_gate": len(gate) if isinstance(gate, list) else gate}


def attempt_kind(rec: dict) -> str:
    """pairing | rostering | rostering_on_pairing — по записи попытки."""
    if (rec or {}).get("task") == "pairing":
        return "pairing"
    if (rec or {}).get("pairing_selection") or str(
            (rec or {}).get("catalog") or "").endswith("button-on-pairing"):
        return "rostering_on_pairing"
    return "rostering"


def _out_name_of(rec: dict) -> str:
    """Каталог выдачи попытки по её записи (хвост `catalog` либо вид)."""
    cat = str((rec or {}).get("catalog") or "")
    tail = cat.rsplit("/", 1)[-1]
    if tail.startswith("button"):
        return tail
    return {"pairing": "button-pairing", "rostering": "button",
            "rostering_on_pairing": "button-on-pairing"}[attempt_kind(rec)]


#: Движок пейринга → пакет интерпретатора, чья версия и есть версия
#: движка (у CP-SAT и HiGHS решатель — библиотека того же процесса;
#: версию CPLEX называет только его собственный журнал прогона).
_PAIRING_ENGINE_PACKAGE = {"cpsat": "ortools", "highs": "highspy"}

def _reader_role_code() -> str:
    """Ключ роли читателя ИДАК — из ОДНОГО словаря ролей продукта
    (vmp.run.llm_exchanges): двух написаний одной роли быть не должно.
    Импорт ленивый — модуль обменов тянется только под нажатием."""
    from vmp.run import llm_exchanges as _lx
    return _lx.ROLE_CODE_SOURCE_READER


def _package_manifest(pkg_path: Path) -> dict:
    """Манифест пакета постановки (чтение zip); нечитаемый пакет —
    пустой словарь (его судят свои рубежи, не происхождение)."""
    import zipfile
    try:
        with zipfile.ZipFile(Path(pkg_path)) as zf:
            man = json.loads(zf.read("manifest.json"))
    except Exception:                                      # noqa: BLE001
        return {}
    return man if isinstance(man, dict) else {}


def _lineage(sha: str, manifest: dict, selection: dict | None) -> dict:
    """Происхождение попытки (проект «Запись о прогоне», разделы 1 и
    3.2.3): отпечаток пакета, прогон-источник CDPM, попытка-родитель
    (пейринг, на котором идёт ростеринг), номер записи о прогоне и
    отпечаток её объявления из манифеста пакета (CDPM кладёт их с
    10.09.2026). Пакет без номера — поле пустое с кодом причины, а не
    отсутствует: читатель не гадает."""
    man = manifest if isinstance(manifest, dict) else {}
    record_id = man.get("record_id")
    out = {"package_sha256": sha,
           "source_run": man.get("source_run"),
           "parent_attempt": (selection or {}).get("attempt_id")
           if isinstance(selection, dict) else None,
           "record_id": record_id if record_id else None,
           # Отпечаток объявления записи (договор с CDPM 10.09.2026:
           # манифест несёт record_id и declaration_sha256) — по нему
           # попытка привязывается к неизменной голове записи.
           "declaration_sha256": man.get("declaration_sha256") or None}
    if not out["source_run"]:
        out["source_run_code"] = "manifest-without-source-run"
    if not out["record_id"]:
        out["record_id_code"] = "package-without-record-id"
    if isinstance(selection, dict) and selection.get("pairing_run_id"):
        out["parent_pairing_run_id"] = selection.get("pairing_run_id")
        if not out["parent_attempt"]:
            out["parent_attempt_code"] = "pairing-snapshot-missing"
    return out


def _solvers_block(task: str, engine: str, params: dict,
                   engine_versions: dict | None) -> list[dict]:
    """Решатели попытки с параметрами — ФАКТ нажатия, а не объявление
    (раздел 3.1 `solvers[]` проекта записи о прогоне): задача, движок,
    версия и отпечаток сборки, пределы времени, набор ходов и
    воспроизводимый профиль. Одна попытка COSMOS ведёт одну задачу,
    поэтому перечень из одной строки; форма перечня — ради записи о
    прогоне, где строк столько, сколько задач прошло."""
    det = params.get("deterministic") if isinstance(
        params.get("deterministic"), dict) else {}
    unc = params.get("accepted_uncovered")
    row: dict = {"task": task,
                 "spent_limit_s": params.get("spent_limit_s"),
                 "unimproved_spent_limit_s":
                     params.get("unimproved_spent_limit_s"),
                 "moves": params.get("moves"),
                 "seed": det.get("seed"),
                 "step_limit": det.get("step_limit"),
                 "unimproved_step_limit": det.get("unimproved_step_limit"),
                 "deterministic_time": det.get("deterministic_time"),
                 "accepted_uncovered": list(unc) if isinstance(unc, list)
                 else ([] if unc in (None, "") else [unc])}
    if not det:
        # Умолчание решателя ростеринга — стенные часы и seed 0 (тот же
        # код, что в рецепте попытки): «пусто» здесь означает профиль
        # Д1 не задан, а не «параметр неизвестен».
        row["profile_code"] = "wall-clock"
    if task == "pairing":
        pkg = _PAIRING_ENGINE_PACKAGE.get(engine)
        version = (engine_versions or {}).get(pkg) if pkg else None
        row.update(engine=engine, version=version,
                   version_source_code="interpreter-package" if pkg
                   else "engine-version-in-solver-log",
                   build_sha256=None,
                   build_source_code="in-process-engine")
    else:
        build, build_code = _solver_build()
        build = build if isinstance(build, dict) else {}
        row.update(engine="timefold",
                   version=build.get("timefold_version"),
                   version_source_code=build_code,
                   build_sha256=build.get("app_jar_sha256"),
                   build_source_code=build_code,
                   matches_manifest=build.get("matches_manifest"))
    return [row]


def _resources(recorder, journal) -> dict:
    """Расход попытки (решение владельца № 1 от 10.09.2026): токены по
    каждой задействованной модели с ролью и каналом плюс стенное время
    счёта. Денег здесь нет намеренно — цена берётся при просмотре по
    прейскуранту того дня. Пробел называется кодом, а не нулём."""
    models: list[dict] = []
    code = None
    if recorder is None:
        code = "no-model-calls"
    else:
        try:
            models = recorder.resources()
        except Exception as e:                             # noqa: BLE001
            models, code = [], "resources-unreadable"
            log.warning("расход моделей попытки не сведён: %s", str(e)[:200])
        if not models:
            code = "no-model-calls"
    out: dict = {"models": models, "solver_seconds": None}
    if code:
        out["models_code"] = code
    if journal is None:
        out["solver_seconds_code"] = "no-call-journal"
        return out
    try:
        out["solver_seconds"] = journal.solve_seconds()
    except Exception:                                      # noqa: BLE001
        out["solver_seconds"] = None
    if out["solver_seconds"] is None:
        out["solver_seconds_code"] = "no-solver-calls"
    return out


def _reproducibility(task: str, deterministic: dict | None) -> dict:
    """Класс воспроизводимости попытки (решение 10 проекта): профиль Д1 —
    `deterministic`; умолчание — стенные часы (`wall-clock`)."""
    if deterministic:
        return {"class": "deterministic", "basis_code": "profile-d1",
                "workers": 1 if task == "pairing" else None}
    return {"class": "wall-clock", "basis_code": "wall-clock-limit",
            "workers": 8 if task == "pairing" else None}


def _write_pseudo_holder(wd: Path, sha: str) -> None:
    """Псевдодержатель «файл» базового каталога (раздел 2.3): пишется
    при заведённом хранилище прогонов, если маркера ещё нет — одним
    писателем маркера `cosmos_runs.claim_holder` (атомарный захват;
    занятый маркер не трогается)."""
    try:
        _cosmos_runs().claim_holder(Path(wd), sha, None, "file")
    except OSError as e:
        log.warning("holder.json не записан в %s: %s", wd, e)


# ── Повтор попытки (раздел 17.2): цель, доступность, обрезка ──

_TRIM_WORDS = ("источник — среди самых старых попыток при пределе площадки "
               "%s=%d: повтор обрезал бы собственный источник; поднимите "
               "предел или снимите его (0)")


def _check_attempt_id(attempt: str | None) -> str:
    attempt = str(attempt or "")
    if not attempt or not all(c.isalnum() or c in "-_" for c in attempt):
        raise PackageRunError("идентификатор попытки не похож на свой",
                              reason_code="press-refused")
    return attempt


def _package_candidates(wd: Path, sha: str, src_out: Path | None,
                        extra: list | None = None) -> list[Path]:
    got: list[Path] = []
    for p in list(extra or []):
        if p:
            got.append(Path(p))
    st_path = read_state(wd).get("package_path")
    if st_path:
        got.append(Path(str(st_path)))
    try:
        from webapp import task_channel as _tc
        box = _tc.inbox_dir()
        if box is not None and sha:
            got.append(Path(box) / sha / "package.zip")
    except Exception:                                      # noqa: BLE001
        pass
    if src_out is not None:
        got.append(Path(src_out) / "package.zip")
    return got


def _package_bytes_path(sha: str, candidates: list[Path], *,
                        verify: bool = True) -> Path | None:
    """Первый путь, чьи байты дают ожидаемый sha256; None — байты утеряны.
    verify=False — только существование файла (лёгкая проверка карточки,
    раздел 17.2: ни одного чтения тел; отпечаток сверяется под запуском —
    replay_target и полная проверка перед занятием)."""
    seen: set[str] = set()
    for p in candidates:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        try:
            if p.is_file() and (not verify or _sha256(p) == sha):
                return p
        except OSError:
            continue
    return None


def _would_trim_source(wd: Path, attempt: str, st: dict | None = None) -> str | None:
    """Рубеж (1) обрезки: моделирует обрезку по ОБОИМ спискам (записи
    попыток с добавленной новой и каталоги attempts/* с добавленным
    новым именем); источник попал бы в хвост — слова отказа."""
    keep = attempts_keep()
    if keep <= 0:
        return None
    st = st if st is not None else read_state(wd)
    ids = [str(r.get("id")) for r in (st.get("attempts") or [])
           if isinstance(r, dict)]
    ids.append("￿-new")            # новая запись — в конец
    if len(ids) > keep and attempt in ids[:-keep]:
        return _TRIM_WORDS % (ATTEMPTS_KEEP_ENV, keep)
    root = Path(wd) / ATTEMPTS_DIR
    names = sorted(p.name for p in root.iterdir() if p.is_dir()) \
        if root.is_dir() else []
    names.append("￿-new")          # имя новой попытки — по времени, последним
    if len(names) > keep and attempt in names[:-keep]:
        return _TRIM_WORDS % (ATTEMPTS_KEEP_ENV, keep)
    return None


def replay_target(run: str | None, attempt: str) -> tuple[Resolved, dict, Path]:
    """Цель повтора: (разрешение, запись попытки-источника, корень её
    снимка). При названном прогоне — по `cosmos_runs.resolve_attempt`
    (мягкая зависимость: без хранилища — отказ словами); без прогона —
    по текущему пакету (легаси и стенды). Байты пакета — путь записи
    прогона / инбокса при совпавшем sha, иначе `package.zip` снимка
    попытки, иначе отказ `package-bytes-missing`. Повтор не требует,
    чтобы прогон был текущим."""
    attempt = _check_attempt_id(attempt)
    extra: list = []
    if run:
        cr = _cosmos_runs()
        try:
            sha, wd, record = cr.resolve_attempt(run, attempt)
            doc = cr.read_run(run)
        except cr.RunsError as e:
            # Хранилище выключено / прогона нет / попытка неизвестна —
            # его код и слова как есть (ручка отдаёт 409 с reason_code).
            raise PackageRunError(str(e)[:500],
                                  reason_code=e.code or "attempt-unknown") from None
        wd = Path(wd)
        run_info = {"run": str(run), "title": doc.get("title"),
                    "workdir": _rel_workdir(wd), "implicit": False}
        # Байты пакета — по записи packages[] прогона (путь канала либо
        # запасной путь инбокса); утеря файла — ниже по снимку попытки.
        for pkg in doc.get("packages") or []:
            if isinstance(pkg, dict) and pkg.get("sha") == sha:
                got = cr.package_path_of(pkg)
                if got:
                    extra.append(Path(str(got)))
                break
    else:
        # Без прогона — по текущему пакету (легаси и стенды); утерянный
        # файл пакета не мешает: рабочий каталог известен по sha, байты
        # берутся из снимка попытки.
        cur0 = current()
        sha = (cur0 or {}).get("sha") or (cur0 or {}).get("sha256")
        path0 = (cur0 or {}).get("path")
        if cur0 is None or (not sha and not (path0 and Path(path0).is_file())):
            raise PackageRunError(
                "текущего пакета постановки нет — повтор без прогона идёт "
                "по текущему пакету", reason_code="package-bytes-missing")
        if not sha:
            sha = _sha256(Path(path0))
        wd = workdir_of(cur0, sha)
        run_info = cur0.get("run") if isinstance(cur0.get("run"), dict) else None
        if path0:
            extra.append(Path(path0))
        record = _attempt_record(read_state(wd), attempt)
    if not isinstance(record, dict):
        raise PackageRunError("попытка %s неизвестна %s" % (
            attempt, ("в прогоне %s" % run) if run else "на этом пакете"),
            reason_code="attempt-unknown")
    sha = str(record.get("package_sha") or sha)
    src_root = wd / ATTEMPTS_DIR / attempt
    src_out = src_root / _out_name_of(record)
    pkg = _package_bytes_path(sha, _package_candidates(wd, sha, src_out, extra))
    if pkg is None:
        raise PackageRunError(
            "байты пакета %s… не найдены ни во входном каталоге, ни в снимке "
            "попытки %s" % (sha[:12], attempt), reason_code="package-bytes-missing")
    cur = {"path": str(pkg), "sha": sha,
           "source": {"kind": "replay", "at": None, "sha": sha,
                      "attempt": attempt},
           "run": run_info}
    return Resolved(cur, sha, pkg, wd, run_info), record, src_root


def replay_availability(wd: Path, record: dict, run_doc: dict | None = None, *,
                        deep: bool = False, solver_given: bool = False,
                        needs_certifier: bool = True) -> dict:
    """Доступность двух кнопок повтора у попытки (раздел 17.2):
    `{"journal": {"available", "reason_code", "reason_words"}, "fresh": {…}}`.
    `deep=False` — легко (запись, два stat, хвост индекса, существование
    файла пакета без хеша, чтение среды без побочных действий;
    `certifier_identity` НЕ зовётся); `deep=True` — под запуском (хеши
    тел и байтов пакета, `certifier_identity` один раз — только когда
    нужна живая сертификация, то есть у «заново»).
    `solver_given` / `needs_certifier` — стендовые уточнения: подставной
    решатель снимает `solver-url-missing`, отсутствие собственной
    сертификации — зонд сертификатора."""
    from vmp.run import call_journal as _cj
    from vmp.run import llm_exchanges as _lx
    wd = Path(wd)
    rec = record if isinstance(record, dict) else {}
    aid = str(rec.get("id") or "")

    def _no(code: str, words: str | None = None) -> dict:
        return {"available": False, "reason_code": code, "reason_words": words}

    ok = {"available": True, "reason_code": None, "reason_words": None}
    common: tuple | None = None
    src_root = wd / ATTEMPTS_DIR / aid
    src_out = src_root / _out_name_of(rec)
    st = read_state(wd)
    if not rec.get("done"):
        common = ("attempt-running", "попытка %s ещё идёт" % aid)
    elif not aid or not src_root.is_dir():
        common = ("snapshot-missing",
                  "снимка попытки %s/%s нет" % (ATTEMPTS_DIR, aid))
    else:
        sha = str(rec.get("package_sha") or st.get("package_sha") or "")
        if not sha or _package_bytes_path(
                sha, _package_candidates(wd, sha, src_out), verify=deep) is None:
            common = ("package-bytes-missing",
                      "байты пакета %s… не найдены ни во входном каталоге, "
                      "ни в снимке попытки" % sha[:12])
    if common is None and _line_alive(wd, "press"):
        common = ("run-busy", "в рабочем каталоге идёт нажатие")
    if common is None:
        params = rec.get("params")
        missing = list(PARAM_KEYS) if not isinstance(params, dict) \
            else [k for k in PARAM_KEYS if k not in params]
        if missing:
            common = ("params-not-recorded",
                      "параметры нажатия не записаны: " + ", ".join(missing))
    if common is None and rec.get("task") == "rostering":
        sel = rec.get("pairing_selection")
        if isinstance(sel, dict) and sel.get("attempt_id"):
            pd = wd / ATTEMPTS_DIR / str(sel["attempt_id"]) / "button-pairing"
            if not (pd / "delivery.json").is_file():
                common = ("pairing-source-missing",
                          "снимок попытки пейринга %s обрезан — выбранный "
                          "пейринг проверить нечем" % sel["attempt_id"])
    if common is None:
        words = _would_trim_source(wd, aid, st)
        if words:
            common = ("source-would-be-trimmed", words)
    if common is not None:
        return {"journal": _no(*common), "fresh": _no(*common)}
    # Только «из журнала».
    journal = dict(ok)
    rec_j = rec.get("journal")
    of_mode = _replay_mode_of(rec)
    if rec_j is None:
        journal = _no("pre-journal",
                      "попытка записана до введения журнала внешних вызовов")
    elif isinstance(rec_j, dict) and rec_j.get("present") is False:
        journal = _no(str(rec_j.get("reason_code") or "disabled-by-site"),
                      "журнал внешних вызовов не вёлся")
    elif of_mode == "journal" or (isinstance(rec_j, dict)
                                  and rec_j.get("mode") == "replay-journal"):
        src_of = ((rec.get("replay") or {}).get("of") or {}).get("source_attempt") \
            if isinstance(rec.get("replay"), dict) else None
        journal = _no("replay-of-replay",
                      "в журнале повтора тел нет — повторяйте попытку-источник %s"
                      % (src_of or rec_j.get("source_attempt") or "?"))
    else:
        ex_dir = src_out / _lx.DIR_NAME
        v = _cj.verify_journal(src_out / _cj.DIR_NAME, rec, deep=deep,
                               exchanges_dir=ex_dir if ex_dir.is_dir() else None)
        if not v.get("complete"):
            journal = _no(str(v.get("reason_code") or "journal-incomplete"),
                          v.get("words"))
    # Только «заново».
    fresh = dict(ok)
    if not solver_given and not (os.environ.get(SOLVER_URL_ENV) or "").strip():
        fresh = _no("solver-url-missing",
                    "адрес решателя не задан (%s)" % SOLVER_URL_ENV)
    elif deep and needs_certifier:
        ident = certifier_identity(str(rec.get("llm_scope") or "local-only"))
        if ident.get("error"):
            fresh = _no("certifier-unavailable", ident["error"])
    return {"journal": journal, "fresh": fresh}


def _journal_note(wd: Path, attempt_id: str, head: dict | None,
                  *, task: str | None = None) -> None:
    """Этап попытки — в летопись прогона (`webapp/run_journal.py`).

    Правило раздела 10 проекта записи о прогоне: ЛЕТОПИСЬ НЕ ЛОМАЕТ
    НАЖАТИЕ. Любая беда журнала или сшивки с хозяином записи —
    предупреждение словами в записи попытки (`record_note`), а не отказ
    кнопки: расчёт уже сделан, и терять его из-за недоступной панели
    нельзя."""
    words: list = []
    try:
        from webapp import run_journal
        rec = _attempt_record(read_state(wd), attempt_id)
        if rec is None:
            return
        words = list(run_journal.note_attempt(wd, rec, head).get("words") or [])
    except Exception as e:                                 # noqa: BLE001
        words = ["запись этапа в летопись прогона не легла: %s: %s"
                 % (type(e).__name__, str(e)[:300])]
        log.warning("летопись прогона (%s): %s", task or "нажатие", words[0])
    if not words:
        return
    try:
        _mutate_state(wd, lambda st: _mark_attempt(st, attempt_id,
                                                   record_note=words))
    except Exception:                                      # noqa: BLE001
        pass


def _record_head(wd: Path, manifest: dict) -> dict | None:
    """Голова записи о прогоне у этого пакета (гейт «от пакета»); гейт
    недоступен либо прогон не объявлен — None, летопись ложится только
    в файлы COSMOS."""
    try:
        from webapp import run_gate
        return run_gate.head_for(wd, manifest)
    except Exception as e:                                 # noqa: BLE001
        log.warning("голова записи о прогоне не прочитана: %s", str(e)[:200])
        return None


def press(task: str, *, engine: str | None = None,
          mode: str | None = None, objective_id: str | None = None,
          spent_limit_s: int = PRESS_DEFAULT_SPENT_LIMIT_S,
          unimproved_spent_limit_s: int | None = None,
          pairing_run_id: str | None = None,
          accepted_uncovered: list[str] | None = None,
          poll_s: float | None = None,
          moves: str | None = None,
          deterministic: dict | None = None,
          actor: str | None = None,
          solver=None, llm=None, pairing_builder=None,
          start_thread: bool = True,
          replay: dict | None = None, **fakes) -> dict:
    """Нажатие кнопки на текущем пакете. Возвращает состояние; работа
    идёт в фоновом потоке (start_thread=False — синхронно, для тестов).
    solver / llm / pairing_builder / fakes — подмены для тестов.

    actor — КТО нажал (готовые слова ручки: «user:<почта>»,
    «channel:<отпечаток токена>», «dev:<почта>» — их строит
    `actor_words` по личности запроса). Пишется в запись попытки и в
    состояние линии, отдаётся списком попыток и называется в отказе
    «занято». Внутренний вызов без личности (раннер, стенд) — поле
    словами «неизвестен: вызов без личности», пустым оно не бывает.

    deterministic — профиль Д1 (`{"seed", "step_limit",
    "unimproved_step_limit", "deterministic_time"}`), пробрасывается в
    оркестратор и построитель; без него — стенные часы (умолчание не
    меняется). replay — режим повтора (`{"mode": "journal" | "fresh",
    "run", "source_attempt"}`, раздел 17.2): попытка заводится в рабочем
    каталоге и на пакете попытки-источника с её параметрами; «из
    журнала» подставляет записанные ответы внешних сторон, «заново»
    вызывает их снова; отчёт сравнения — в снимке попытки. В режиме
    повтора возвращается состояние повтора, а не текущего пакета."""
    import math
    from vmp.run import button as btn
    from vmp.run import call_journal as cj
    from vmp.run import llm_exchanges as lx

    # ── Режим повтора: цель и параметры — из записи попытки-источника ──
    replay_mode: str | None = None
    src_rec: dict | None = None
    src_root: Path | None = None
    res: Resolved | None = None
    if replay is not None:
        replay_mode = str((replay or {}).get("mode") or "")
        if replay_mode not in ("journal", "fresh"):
            raise PackageRunError("режим повтора %r вне словаря: journal, fresh"
                                  % replay_mode, reason_code="press-refused")
        if replay_mode == "journal" and (solver is not None or llm is not None
                                         or pairing_builder is not None):
            raise PackageRunError(
                "в режиме повтора из журнала внешние стороны подставляются "
                "журналом — аргументы solver, llm, pairing_builder не принимаются",
                reason_code="press-refused")
        res, src_rec, src_root = replay_target(replay.get("run"),
                                               replay.get("source_attempt"))
        needs_cert = ("certify_fn" not in fakes) or ("pairing_certify_fn" not in fakes)
        # Единственная полная проверка — здесь, под запуском (ручка её не
        # повторяет); зонд сертификатора — только у «заново»: из журнала
        # модель не вызывается, CLI и ключ API не нужны (17.2).
        avail = replay_availability(res.wd, src_rec, None, deep=True,
                                    solver_given=solver is not None,
                                    needs_certifier=(needs_cert and llm is None
                                                     and replay_mode == "fresh"))
        got = avail[replay_mode]
        if not got["available"]:
            raise PackageRunError(got.get("reason_words") or got["reason_code"],
                                  reason_code=got["reason_code"])
        params = src_rec.get("params") or {}
        task = str(src_rec.get("task") or "")
        engine = src_rec.get("engine")
        mode = params.get("mode")
        objective_id = params.get("objective_id")
        spent_limit_s = (params.get("spent_limit_s")
                         if params.get("spent_limit_s") is not None else 60)
        unimproved_spent_limit_s = params.get("unimproved_spent_limit_s")
        pairing_run_id = params.get("pairing_run_id")
        accepted_uncovered = params.get("accepted_uncovered")
        poll_s = params.get("poll_s")
        moves = params.get("moves")
        deterministic = params.get("deterministic") or None
    if task not in TASKS:
        raise PackageRunError("недопустимая задача %r: кнопка знает %s"
                              % (task, ", ".join(TASKS)))
    engine = (engine or "cpsat").strip().lower()
    if engine not in ENGINES:
        raise PackageRunError("недопустимый движок %r: %s"
                              % (engine, ", ".join(ENGINES)))
    # W-6 (единая версия 06.09.2026): пробелы — не выбор; пустой
    # pairingRunId = сквозной ростеринг (холодный путь остаётся штатным,
    # GPT-011), НЕ подстановка последнего пейринга.
    pairing_run_id = (pairing_run_id or "").strip() or None
    if pairing_run_id and task != "rostering":
        raise PackageRunError("pairingRunId применим только к задаче "
                              "rostering")
    if res is None:
        # Одно разрешение текущего пакета (раздел 5.3): рабочий каталог
        # пары (прогон, пакет) берётся здесь и больше не пересчитывается.
        res = resolve_current()
        if res is None:
            raise PackageRunError("текущего пакета постановки нет — "
                                  "загрузите пакет из CDPM")
    cur, sha, pkg_path, wd, run = res
    run_name = run.get("run") if isinstance(run, dict) else None
    selection: dict | None = None
    package_from: str | None = None
    matches: list[dict] = []
    approved: dict | None = None
    if pairing_run_id:
        # Шаг 3 плана 05.09.2026: ростеринг НА пейринге идёт только по
        # ЯВНО названному и проверенному прогону пейринга этого же
        # пакета — молчаливой подстановки «последнего пейринга» нет.
        # Соответствие (W-4): прогон — у выдачи нажатия pairing в
        # рабочем каталоге пакета ЛИБО у любой сохранённой попытки
        # pairing (снимок attempts/<id>/button-pairing, не только
        # последней); вердикт «пейринг построен» (каталог
        # зарегистрирован). Пакет байт-в-байт и отпечатки сверяет
        # кнопка (Ш-1) и решатель (четвёртое предусловие); по снимку
        # попытки байты, каталог и состав рейсов сверяются ниже до
        # занятия кнопки (W-5) — здесь отказ раньше и словами.
        approved = approved_pairing(wd)
        candidates = _pairing_candidates(wd, read_state(wd))
        if approved is None and not candidates:
            raise PackageRunError(
                "pairingRunId %s: на этом пакете нет выдачи нажатия pairing — "
                "сначала нажатие задачи pairing" % pairing_run_id)
        matches = [c for c in candidates if c["pairing_run_id"] == str(pairing_run_id)]
        live = (approved is not None
                and str(approved.get("solver_run_id") or "") == str(pairing_run_id))
        if not matches and not live:
            known = ", ".join(c["pairing_run_id"] for c in candidates) or "нет"
            raise PackageRunError(
                "pairingRunId %s не соответствует прогону пейринга этого "
                "пакета (%s, вердикт «%s»; сохранённые попытки: %s): подстановки "
                "нет — назовите прогон утверждённого пейринга из попыток этого "
                "пакета либо нажмите pairing заново"
                % (pairing_run_id, (approved or {}).get("solver_run_id"),
                   (approved or {}).get("verdict"), known))
        verdict = matches[0]["verdict"] if matches else (approved or {}).get("verdict")
        if verdict != "пейринг построен":
            raise PackageRunError(
                "прогон пейринга %s не утверждён: вердикт «%s» — ростеринг на "
                "нём не идёт" % (pairing_run_id, verdict))
        if matches and not live:
            # Прежняя (не последняя) попытка: байты пакета и выдача
            # пейринга — из её снимка, не из button-pairing/ (W-4).
            package_from = "%s/%s/button-pairing" % (ATTEMPTS_DIR,
                                                      matches[0]["attempt_id"])
    # Ранний отказ занятой кнопки — до дорогих проверок; последний рубеж
    # — атомарное занятие ниже (аудит A10): между этой проверкой и
    # занятием чужое нажатие возможно, и его отсечёт замок. Слова
    # называют держателя: попытку с её актором либо чужой живой процесс
    # службы (разбор 09.09.2026).
    _early = _read_state_swept(wd)
    if _LINES["press"]["busy"](_early):
        raise PackageRunError("кнопка уже идёт: %s"
                              % holder_words(_early, "press"),
                              reason_code="run-busy")
    # Сторож дрейфа каталога шаблонов (§8 схемы каталога, шаг 3): пакет
    # объявляет ключом manifest.catalog, каким каталогом посажены его
    # правила. Разница старшего числа версии (на 0.x — и среднего),
    # несравнимая версия и одна версия у двух выпусков с разными
    # данными — отказ ДО занятия кнопки: попытки нет, расчёт не
    # начинается. Прочие расхождения — предупреждение: они едут в
    # паспорт попытки (template_catalog.manifest_check) и в журнал
    # кнопки, нажатие идёт.
    from vmp import catalog_access as _ca_press
    catalog_check = _catalog_manifest_check(pkg_path)
    if catalog_check.get("state") == _ca_press.CHECK_STOP:
        raise PackageRunError(catalog_check["words"],
                              reason_code=_ca_press.STOP_REASON_CODE)
    # Каталог артефактов этой задачи — как у оркестратора; прежнее
    # нажатие той же задачи убирается целиком, у пейринга — и экспорт
    # модели по нему.
    out_name = ("button-on-pairing" if pairing_run_id
                else ("button" if task == "rostering" else "button-%s" % task))
    # A10 (ступень M): у каждого нажатия — идентификатор попытки; запись
    # попытки ведётся в состоянии (attempts), артефакты по окончании
    # снимаются в attempts/<id>/, статус и файлы доступны по ID.
    attempt_id = _new_attempt_id()
    source_id = str(src_rec.get("id")) if src_rec else None
    label = "package-%s" % sha[:12]
    # ── Журнал внешних вызовов (17.1) и подстановщик повтора (17.2) ──
    # Журнал живёт в памяти до занятия кнопки (вызовы до занятия —
    # проверка выбранного пейринга — попадают в него), открывается после
    # очистки каталога выдачи. Выключатели площадки в режимах повтора не
    # действуют: журнал повтора — его доказательство.
    journal_on = replay is not None or cj.enabled()
    exchanges_on = replay is not None or lx.enabled()
    replayer = None
    if replay_mode == "journal":
        src_out = src_root / _out_name_of(src_rec)
        try:
            replayer = cj.JournalReplayer(src_out / cj.DIR_NAME,
                                          src_out / lx.DIR_NAME,
                                          source_attempt=source_id)
        except Exception as e:                             # noqa: BLE001
            raise PackageRunError("журнал попытки %s не читается: %s"
                                  % (source_id, str(e)[:300]),
                                  reason_code="journal-incomplete") from None
        solver = cj.JournalSolver(replayer)
    elif solver is None:
        solver = btn.SolverHttp(solver_url())
    journal: cj.CallJournal | None = None
    if journal_on:
        journal = cj.CallJournal(
            attempt_id, task, out_name=out_name,
            mode=("replay-journal" if replay_mode == "journal"
                  else "replay-fresh" if replay_mode == "fresh" else "record"),
            source_attempt=source_id, replayer=replayer)
        # Перехват решателя — до проверки выбранного пейринга (внутри —
        # solver.status); обёртка прозрачна для стендов.
        solver = cj.wrap_solver(solver, journal)
    if pairing_run_id:
        if matches:
            # W-5: проверка выбранного пейринга по снимку попытки ДО
            # занятия кнопки — пакет, каталог, состав рейсов, разбиение,
            # запись прогона у решателя; отказ словами, счёт не начат.
            # Остановка внешней стороны до занятия (в том числе
            # рассинхрон повтора из журнала) — отказ с её кодом, попытки нет.
            try:
                selection = validate_pairing_selection(wd, pkg_path, sha,
                                                       str(pairing_run_id), solver)
            except btn.ButtonStop as e:
                raise PackageRunError(
                    "выбранный пейринг не проверен: %s" % str(e)[:500],
                    reason_code=getattr(e, "reason_code", None) or "press-refused"
                ) from None
        else:
            # Выдача есть, снимка попытки нет (нажатие до ввода попыток
            # A10 либо снимок утерян): показание честно «по снимку не
            # проверено» — не зелёное; байты пакета сверяет кнопка (Ш-1),
            # отпечатки — решатель.
            selection = {"pairing_run_id": str(pairing_run_id), "attempt_id": None,
                         "catalog_sha256": (approved or {}).get("catalog_sha256"),
                         "chains": (approved or {}).get("chains"),
                         "verified": False,
                         "note": "снимка попытки пейринга нет — пакет, каталог и "
                                 "состав рейсов по снимку не сверены; пакет "
                                 "байт-в-байт сверяет кнопка (Ш-1), отпечатки — "
                                 "решатель"}
    # Подмены сертификации: ключ, присутствующий в fakes (даже None —
    # «пропустить»), старше боевой реализации; канал модели строится
    # только когда боевая сертификация действительно нужна.
    own_certify = "certify_fn" not in fakes
    own_pairing_certify = "pairing_certify_fn" not in fakes
    if replay_mode == "journal":
        # Одна переменная сертификатора (17.2): шапка сертификата и
        # запись попытки — из записи источника; живая настройка площадки
        # не читается, CLI и ключ API не нужны.
        scope = str(src_rec.get("llm_scope") or package_scope(pkg_path))
        certifier = dict(src_rec.get("certifier") or {})
        if own_certify or own_pairing_certify:
            llm = cj.JournalLLM(replayer, role="certifier")
    else:
        scope = package_scope(pkg_path)
        certifier = certifier_passport(certifier_identity(scope))
        if llm is None and (own_certify or own_pairing_certify):
            if certifier.get("error"):
                raise PackageRunError(certifier["error"],
                                      reason_code="certifier-unavailable")
            # Компилятор проверок сертификата: при допуске «any» — канал
            # площадки (cli по умолчанию / api) с названной моделью; при
            # «local-only» — локальная модель через дисциплину COSMOS.
            if scope == "any":
                llm = cloud_certifier_channel(certifier["model"],
                                              certifier["channel"])
            else:
                from cdpm import llm as llm_mod
                llm = llm_mod.cosmos_llm(scope)
    # Обмены с моделью (7.4): обёртка — только когда своя сертификация
    # нужна; при выключенных обменах журнал считает вызовы без тел.
    # `recorder` — та же обёртка обоих случаев: она же считает расход
    # модели в токенах (решение владельца № 1), поэтому расход попытки
    # есть и при выключенных телах обменов.
    exchanges = None
    recorder = None
    if llm is not None and (own_certify or own_pairing_certify):
        if exchanges_on:
            exchanges = lx.wrap(llm, role="certifier",
                                out=wd / out_name / lx.DIR_NAME, journal=journal)
            recorder = exchanges
            llm = exchanges
        elif journal is not None:
            recorder = lx.wrap(llm, role="certifier",
                               out=wd / out_name / lx.DIR_NAME,
                               journal=journal, bodies=False)
            llm = recorder
    if pairing_builder is None and task == "pairing":
        if replay_mode == "journal":
            pairing_builder = cj.JournalBuilder(
                replayer, wd, select_engine=engine, label=label,
                deterministic=bool(deterministic))
        else:
            pairing_builder = btn.subprocess_pairing_builder(
                label, deterministic=bool(deterministic), select_engine=engine)
    if task == "pairing" and journal is not None:
        pairing_builder = cj.wrap_builder(
            pairing_builder, journal, select_engine=engine, label=label,
            deterministic=bool(deterministic), wd=wd)
    # ── Паспортные поля попытки (7.3), момент Н ──
    engine_versions = _engine_versions() if task == "pairing" else None
    if task == "pairing":
        solver_rec: dict = {"select_engine": engine}
    else:
        solver_rec = {"url": cj._strip_userinfo(getattr(solver, "base", None)),
                      "engine": "timefold"}
    params = {"mode": mode, "objective_id": objective_id,
              "spent_limit_s": spent_limit_s,
              "unimproved_spent_limit_s": unimproved_spent_limit_s,
              "pairing_run_id": pairing_run_id, "moves": moves,
              "accepted_uncovered": accepted_uncovered, "poll_s": poll_s,
              "deterministic": deterministic}
    reproducibility = _reproducibility(task, deterministic)
    # Решатели с параметрами и происхождение входов — момент Н (проект
    # «Запись о прогоне», разделы 3.1 и 3.2.3): и то и другое известно
    # ДО счёта и после старта не меняется.
    solvers = _solvers_block(task, engine, params, engine_versions)
    manifest = _package_manifest(pkg_path)
    lineage = _lineage(sha, manifest, selection)
    # Голова записи о прогоне читается ДО счёта (её объявили раньше
    # нажатия) и держится нитью: после конца попытки летопись пишется
    # по ней, не перечитывая пакет.
    record_head = _record_head(wd, manifest)
    replay_of = ({"mode": replay_mode, "source_attempt": source_id,
                  "report": None} if replay is not None else None)
    passport = {"run": run_name, "workdir": _rel_workdir(wd),
                # Кто нажал (разбор 09.09.2026): актор попытки едет и в
                # состояние линии, и в запись попытки одной переменной —
                # список попыток и отказ «занято» называют одно и то же.
                "actor": _actor_of(actor),
                "params": params, "reproducibility": reproducibility,
                "code": _code_identity(),
                # Каталог продукта, исход сверки с ключом пакета и та
                # самая МЕРКА, по которой исход вынесен (вторая сторона
                # сверки): без неё запись «сошлось» стояла бы под
                # непроверяемой отметкой. С шага 7-Б источник мерки
                # всегда `catalog` — режим среза базы снят; поле
                # оставлено ради записей прежних прогонов (`slice`).
                # state = null — сверки не было, и причина названа
                # кодом.
                "template_catalog": {**_catalog_stamp(),
                                     "manifest_check": catalog_check.get("state"),
                                     "manifest_words": catalog_check.get("words"),
                                     "manifest_check_reason":
                                         catalog_check.get("reason_code"),
                                     "manifest_check_against":
                                         catalog_check.get("product")},
                "solver": solver_rec, "engine_versions": engine_versions,
                # Решатели с параметрами (объявление словаря записи о
                # прогоне) и происхождение входов попытки; расход
                # ресурсов дописывается в момент Н+ (счёт уже был).
                "solvers": solvers, "lineage": lineage,
                "resources": None,
                "exchanges": None, "journal": None,
                "replay": {"of": replay_of}}
    if journal is not None:
        build, build_code = _solver_build()
        journal.versions.update({
            "solver": {"url": journal.solver_url, "build": build,
                       "version_source_code": build_code},
            "pairing_builder": (cj.builder_versions(engine, engine_versions)
                                if task == "pairing" else None),
            "llm": {k: certifier.get(k) for k in
                    ("model", "channel", "provider", "backend", "scope")},
            "panel": cj.panel_versions()})
    extra = {}
    if task == "pairing":
        extra = dict(export_mps_state=None, export_mps_error=None,
                     export_mps_files=None, export_mps_manifest=None)
    # Занятие кнопки — одной операцией под замком (аудит A10): «свободна»
    # судится по done, запись «занята» — тут же; занята — отказ словами.
    # Чистка каталога идёт ТОЛЬКО после занятия: каталог идущего расчёта
    # чужое нажатие не трогает; нажатие «Пейринг» убирает и экспорт
    # модели — при живом экспорте оно ждёт его конца.
    _claim_line(wd, "press", "кнопка уже идёт",
                also=("export",) if task == "pairing" else (),
                package_sha=sha, package_path=str(pkg_path),
                task=task, engine=engine if task == "pairing" else None,
                # W-3/W-4: выбранный пейринг и следующие шаги — в
                # состоянии с момента занятия (пусто у штатных путей).
                pairing_selection=selection,
                next_actions=[], selection_note=None,
                llm_scope=scope, certifier=certifier,
                state="кнопка: идёт", done=False, progress=None,
                error=None, verdict=None, result=None, uncovered=[],
                certificate_gate=[], attempt_id=attempt_id,
                **passport, **extra)
    keep = attempts_keep()

    def _register(st: dict) -> None:
        lst = [r for r in (st.get("attempts") or []) if isinstance(r, dict)]
        lst.append({"id": attempt_id, "task": task,
                    "pairing_selection": selection,
                    "engine": engine if task == "pairing" else None,
                    "started_at": st.get("started_at"),
                    "state": "идёт", "done": False, "verdict": None,
                    "error": None, "finished_at": None, "catalog": None,
                    "package_sha": sha, "llm_scope": scope,
                    "certifier": certifier, **passport})
        if keep > 0 and len(lst) > keep:
            trimmed = lst[:-keep]
            # Рубеж (2) обрезки под замком: повтор не уничтожает свой
            # источник — отказ до записи, попытки нет.
            if source_id and any(r.get("id") == source_id for r in trimmed):
                raise PackageRunError(_TRIM_WORDS % (ATTEMPTS_KEEP_ENV, keep),
                                      reason_code="source-would-be-trimmed")
            st["attempts_trimmed"] = (st.get("attempts_trimmed") or 0) + len(trimmed)
            lst = lst[-keep:]
        st["attempts"] = lst
    try:
        _mutate_state(wd, _register)
    except PackageRunError as e:
        _release_line(wd, "press", state="кнопка: отказ", done=True,
                      error=str(e)[:500])
        raise
    # Псевдодержатель «файл» базового каталога при заведённом хранилище
    # прогонов (раздел 2.3): последующий приём того же sha каналом видит
    # каталог занятым. Без хранилища — ни байта нового (стенды).
    if run is None and (cur.get("source") or {}).get("kind") == "file" \
            and _storage_ready():
        _write_pseudo_holder(wd, sha)
    try:
        _clear_press_artifacts(wd / out_name)
        if task == "pairing":
            import shutil
            shutil.rmtree(wd / "export-mps", ignore_errors=True)
    except Exception as e:                                # noqa: BLE001
        _release_line(wd, "press", state="кнопка: отказ", done=True,
                      error="каталог нажатия не подготовлен: %s"
                            % str(e)[:500])
        raise PackageRunError("каталог нажатия не подготовлен: %s"
                              % str(e)[:300],
                              reason_code="workdir-not-prepared") from None
    if journal is not None:
        journal.open(wd / out_name / cj.DIR_NAME)

    def _log(step, status, detail):
        # Ход — отдельным ключом; state остаётся «кнопка: идёт» до
        # конца (аудит A10: текст хода не подменяет признак занятости).
        _update_state(wd, progress="%s — %s" % (step, status))

    def _stamp(report):
        # Шапка сертификата: кто сертифицировал (канал, модель, допуск)
        # — та же переменная, что в записи попытки (17.2).
        if isinstance(report, dict):
            report["certifier"] = dict(certifier)
        return report

    # A8 (аудит 04.09.2026): штатный путь сертифицирует СТРОГО —
    # сертификат допустимости только при полном покрытии правил;
    # явный аргумент, чтобы намерение читалось на месте вызова.
    def _certify(doc, solution):
        from vmp.run import roster_certify as rc
        return _stamp(rc.certify(doc, solution, llm, strict_coverage=True))

    def _certify_pairing(doc, catalog, fmap):
        from vmp.run import roster_certify as rc
        return _stamp(rc.certify_pairing(doc, catalog, fmap, llm,
                                         strict_coverage=True))

    kwargs = dict(task=task, pairing_run_id=pairing_run_id,
                  accepted_uncovered=accepted_uncovered,
                  mode=mode, objective_id=objective_id,
                  spent_limit_s=spent_limit_s,
                  unimproved_spent_limit_s=unimproved_spent_limit_s,
                  package_path=pkg_path, log=_log)
    if moves:
        # Набор ходов решателя — только явно (шаг 4 плана 05.09.2026);
        # известность имени судит решатель словами.
        kwargs["moves"] = str(moves)
    if deterministic:
        # Профиль Д1 — только явно; умолчание (стенные часы) не меняется.
        kwargs["deterministic"] = dict(deterministic)
    if pairing_run_id:
        # W-3: запись выбора и подсказка — в выдачу через оркестратор
        # (selected_pairing_fields), не дописыванием файла после него.
        kwargs["pairing_selection"] = selection
        kwargs["selection_note"] = PAIRING_RETRY_NOTE
    if package_from:
        kwargs["package_from"] = package_from
    if poll_s is not None:
        kwargs["poll_s"] = poll_s
    if task == "pairing":
        kwargs["pairing_builder"] = pairing_builder
    kwargs["certify_fn"] = fakes.pop("certify_fn") if not own_certify \
        else _certify
    kwargs["pairing_certify_fn"] = fakes.pop("pairing_certify_fn") \
        if not own_pairing_certify else _certify_pairing
    kwargs.update(fakes)
    if replay_mode == "journal":
        # Часы панели в повторе не участвуют: число опросов status
        # детерминировано очередью журнала (остановка по часам — из
        # manifest.terminal_stop).
        kwargs["timeout_s"] = math.inf
        kwargs["sleep"] = lambda *_: None

    def _snapshot() -> str | None:
        """Снимок артефактов попытки: attempts/<id>/<каталог нажатия> +
        копия реестра нажатия (registry/); хвост истории — по пределу
        площадки, никогда не убирая источник повтора и свой снимок."""
        import shutil
        src = wd / out_name
        if not src.is_dir():
            return None
        dst = wd / ATTEMPTS_DIR / attempt_id / out_name
        shutil.rmtree(dst, ignore_errors=True)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dst)
        reg = wd / "registry" / "vmp-executors-registry.json"
        if reg.is_file():
            (dst.parent / "registry").mkdir(exist_ok=True)
            shutil.copy2(reg, dst.parent / "registry" / reg.name)
        # Хвост истории попыток — каталоги старше предела убираются.
        root = wd / ATTEMPTS_DIR
        names = sorted(p.name for p in root.iterdir() if p.is_dir())
        if keep > 0:
            for old in names[:-keep]:
                if old == attempt_id or (source_id and old == source_id):
                    # Рубеж (3): нажатие не уничтожает свой вход и снимок.
                    log.warning("обрезка попыток: каталог %s сохранён "
                                "(источник или снимок текущего нажатия)", old)
                    continue
                shutil.rmtree(root / old, ignore_errors=True)
        return "%s/%s/%s" % (ATTEMPTS_DIR, attempt_id, out_name)

    rel_attempt = "%s/%s/%s" % (ATTEMPTS_DIR, attempt_id, out_name)

    def _after_fields(delivery: dict | None) -> dict:
        """Поля момента Н+: сводка выдачи, обмены, журнал, копия реестра,
        число потоков построителя."""
        if exchanges is not None:
            exch = exchanges.summary("%s/%s" % (rel_attempt, lx.DIR_NAME))
        elif llm is not None and not exchanges_on:
            exch = {"present": False, "reason_code": "disabled-by-site"}
        else:
            exch = {"present": False, "reason_code": "no-model-calls"}
        if journal is not None:
            jour = journal.record_summary("%s/%s" % (rel_attempt, cj.DIR_NAME))
        else:
            jour = {"present": False, "reason_code": "disabled-by-site"}
        repro = dict(reproducibility)
        bi = wd / ATTEMPTS_DIR / attempt_id / out_name / "builder-input.json"
        if task == "pairing" and bi.is_file():
            try:
                args = list(json.loads(bi.read_text(encoding="utf-8")).get("args") or [])
                if "--workers" in args:
                    repro["workers"] = int(args[args.index("--workers") + 1])
            except (OSError, ValueError, IndexError):
                pass
        return {"summary": _delivery_summary(delivery), "exchanges": exch,
                "journal": jour, "reproducibility": repro,
                # Расход попытки: токены по каждой модели и стенное
                # время счёта (решение владельца № 1 от 10.09.2026).
                "resources": _resources(recorder, journal),
                "registry": _registry_summary(
                    wd / ATTEMPTS_DIR / attempt_id / "registry"
                    / "vmp-executors-registry.json")}

    def _work():
        delivery = None
        try:
            delivery = btn.orchestrate(wd, solver, **kwargs)
            # Журналы закрываются ДО снимка: их последние строки — часть
            # снимка попытки.
            if journal is not None:
                journal.close(complete=True, delivery=delivery)
            if exchanges is not None:
                exchanges.close(complete=True)
            done = list(read_state(wd).get("tasks_done") or [])
            if task not in done:
                done.append(task)
            try:
                catalog = _snapshot()
            except Exception as e:                        # noqa: BLE001
                catalog = "снимок не снят: %s" % str(e)[:200]
            fin = time.strftime("%Y-%m-%dT%H:%M:%S")
            n_plus = _after_fields(delivery)
            replay_field = None
            if replay_of is not None:
                report_summary = None
                try:
                    from vmp.run import replay_compare as rcmp
                    new_rec = {"id": attempt_id, "task": task,
                               "pairing_selection": selection,
                               "catalog": catalog, "certifier": certifier,
                               **passport, **n_plus}
                    report = rcmp.run(replay_mode, wd, source_id, attempt_id,
                                      src_rec, new_rec, run=run_name)
                    report_summary = rcmp.summary_of(report)
                except Exception as e:                    # noqa: BLE001
                    report_summary = {
                        "verdict": "not-comparable",
                        "words": "сравнение не проведено: %s: %s"
                                 % (type(e).__name__, str(e)[:300]),
                        "first_difference": None, "files_compared": None,
                        "outcomes_differ": [], "environment_differs": []}
                replay_field = {"of": dict(replay_of, report=report_summary)}

            def _finish(st: dict) -> None:
                st.update(state="кнопка: %s" % delivery.get("verdict"),
                          done=True, progress=None, error=None,
                          tasks_done=done,
                          verdict=delivery.get("verdict"),
                          mode=delivery.get("mode"),
                          mode_note=delivery.get("mode_note"),
                          # A8: три статуса и перечень непроверенного —
                          # в состоянии страницы, а не только в файле.
                          result=delivery.get("result"),
                          uncovered=delivery.get("uncovered") or [],
                          certificate_gate=delivery.get("certificate_gate") or [],
                          # W-3: следующие шаги после неудачи на выбранном
                          # каталоге и запись выбора — из выдачи.
                          next_actions=delivery.get("next_actions") or [],
                          selection_note=delivery.get("selection_note"),
                          pairing_selection=delivery.get("pairing_selection")
                          or selection,
                          # A7: допущение о непереданной истории — словами
                          assumptions=delivery.get("assumptions"),
                          exchanges=n_plus["exchanges"],
                          journal=n_plus["journal"])
                fields = dict(state="завершено", done=True,
                              verdict=delivery.get("verdict"),
                              finished_at=fin, catalog=catalog, **n_plus)
                if replay_field is not None:
                    fields["replay"] = replay_field
                    st["replay"] = replay_field
                _mark_attempt(st, attempt_id, **fields)
            _mutate_state(wd, _finish)
            # Этап журнала записи о прогоне — после того, как попытка
            # записана целиком: летопись читает её же поля.
            _journal_note(wd, attempt_id, record_head, task=task)
        except BaseException as e:                        # noqa: BLE001
            # Любой исход нити пишет done=True — иначе кнопка заперта
            # до сторожа сирот (аудит A10).
            if journal is not None and not journal.closed:
                journal.close(complete=False)
            if exchanges is not None and not exchanges.closed:
                exchanges.close(complete=False)
            # Слова ошибки — без учётных данных адресов (сообщение httpx
            # несёт полный URL запроса; правило 17.1, ревью 07.09).
            err = cj.strip_userinfo_text(str(e))[:800]
            jour = (journal.record_summary(None) if journal is not None
                    else {"present": False, "reason_code": "disabled-by-site"})
            # Отказавшая попытка тоже тратила токены и время счёта —
            # расход пишется и здесь, иначе он пропал бы из записи.
            res_fail = _resources(recorder, journal)

            def _fail(st: dict) -> None:
                st.update(state="кнопка: отказ", done=True,
                          progress=None, error=err, journal=jour)
                if selection:
                    # W-3: отказ на выбранном каталоге — не доказательство
                    # невозможности постановки; следующие шаги словами.
                    st.update(next_actions=["reselect_pairing", "expand_catalog"],
                              selection_note=PAIRING_RETRY_NOTE)
                _mark_attempt(st, attempt_id, state="отказ", done=True,
                              error=err, journal=jour, resources=res_fail,
                              finished_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
            _mutate_state(wd, _fail)
            # Отказавшая попытка — тоже этап прогона: исход «сбой» либо
            # «отказ по правилу» со словами. Молчание записи о неудаче
            # было бы неправдой о прогоне.
            _journal_note(wd, attempt_id, record_head, task=task)
            if not isinstance(e, Exception):
                raise

    _run_line(wd, "press", _work, start_thread)
    if replay is not None:
        # Состояние ПОВТОРА, а не текущего пакета (прогон может быть не
        # текущим): ручка кладёт то же в 202.
        st = read_state(wd)
        return {"attempt_id": attempt_id, "run": run_name,
                "workdir": _rel_workdir(wd),
                "state": st.get("state"), "done": st.get("done"),
                "mode": replay_mode, "source_attempt": source_id,
                "task": task}
    return status()


def attempt_status(wd: Path, st: dict, attempt_id: str) -> dict:
    """Состояние ОДНОЙ попытки нажатия по ID (аудит A10, ступень M):
    запись из состояния и перечень файлов её снимка; неизвестный ID —
    отказ словами."""
    rec = _attempt_record(st, attempt_id)
    if rec is None:
        raise PackageRunError("попытка %r неизвестна на этом пакете" % attempt_id)
    files: list[str] = []
    root = wd / ATTEMPTS_DIR / attempt_id
    if root.is_dir():
        files = sorted(str(p.relative_to(root)).replace("\\", "/")
                       for p in root.rglob("*") if p.is_file())
    aliases = [a for a, (sub, name) in ALLOWED_FILES.items()
               if (root / sub / name).is_file()]
    return {"attempt": attempt_view(rec), "files": files, "aliases": aliases,
            "current": st.get("attempt_id") == attempt_id}


def status(attempt: str | None = None) -> dict:
    """Состояние кнопки на текущем пакете + ссылки артефактов каждой
    выполненной задачи. attempt — состояние одной попытки по ID."""
    res = resolve_current()
    if res is None:
        return {"present": False}
    cur, sha, pkg_path, wd, run = res
    # Первое обращение после перезапуска службы помечает сирот (аудит
    # A10): запись «занята» без живой нити — «прервано перезапуском».
    st = _read_state_swept(wd)
    if attempt:
        return {"present": True, "package_sha": sha,
                **attempt_status(wd, st, attempt)}
    files = {}
    for task, aliases in TASK_LINKS.items():
        have = [a for a in aliases
                if (wd / ALLOWED_FILES[a][0] / ALLOWED_FILES[a][1]).is_file()]
        if have:
            files[task] = have
    # Критерий оптимизации по задачам — из матрицы готовности пакета
    # (та же клетка, что судит запуск): без критерия страница
    # предупреждает ДО нажатия, что кнопки поведут задачи в режиме
    # допустимости с целевой из штрафов (решение владельца 04.09).
    no_criterion: dict[str, bool] = {}
    source_run = None
    try:
        import zipfile as _zf
        _man = json.loads(_zf.ZipFile(cur["path"]).read("manifest.json"))
        source_run = _man.get("source_run")
        _cols = (_man.get("readiness_matrix") or {}).get("columns") or {}
        for _task in ("pairing", "rostering"):
            _crit = ((_cols.get(_task) or {}).get("grounds") or {}) \
                .get("criterion") or {}
            if _crit.get("defined") is False:
                no_criterion[_task] = True
    except Exception:                                      # noqa: BLE001
        no_criterion = {}
    # Попыткам легаси без ключа run дописывается держатель рабочего
    # каталога из holder.json (чтение); новые записи несут run сами.
    holder = _holder_run(wd)
    attempts = []
    for rec in reversed(st.get("attempts") or []):
        if isinstance(rec, dict):
            rec = attempt_view(rec, holder)
        attempts.append(rec)
    run_out = (dict(run) if isinstance(run, dict)
               else ({"run": holder, "title": None,
                      "workdir": _rel_workdir(wd), "implicit": True}
                     if holder else None))
    # Пакеты того же прогона (sha) — из его записи; без прогона — None.
    packages_in_run = None
    if run_out and run_out.get("run"):
        try:
            doc = _cosmos_runs().read_run(str(run_out["run"]))
            packages_in_run = [str(p.get("sha")) for p in doc.get("packages") or []
                               if isinstance(p, dict) and p.get("sha")]
        except Exception:                                  # noqa: BLE001
            packages_in_run = None
    out = {"present": True, "package_sha": sha,
           "no_criterion": no_criterion,
           # Прогон-источник пакета (manifest.source_run): схема судит
           # по нему, к ЭТОМУ ли пакету относится протокол сверки с
           # документами (06.09.2026: снимок прежнего пакета не должен
           # светиться «выполнено» у нового).
           "source_run": source_run,
           # Система прогонов (7.6): прогон COSMOS рабочего каталога,
           # сам каталог (относительно каталога данных) и пакеты прогона.
           "run": run_out,
           "workdir": _rel_workdir(wd),
           "packages_in_run": packages_in_run,
           "task": st.get("task"), "engine": st.get("engine"),
           "state": st.get("state"), "done": st.get("done"),
           # Ход идущего расчёта («Ш-2 — построитель запущен») —
           # отдельно от состояния; занятость — только по done
           # (аудит A10). pid/started_at — кто и когда занял кнопку.
           "progress": st.get("progress"),
           "pid": st.get("pid"), "started_at": st.get("started_at"),
           # Кто нажал последним (разбор 09.09.2026) и не держит ли
           # линию другой ЖИВОЙ процесс службы: пометка выводится при
           # каждом чтении состояния, не залёживается.
           "actor": st.get("actor") or ACTOR_UNKNOWN,
           "holder_note": st.get("holder_note"),
           "error": st.get("error"), "verdict": st.get("verdict"),
           # Режим последней задачи словами (Ш-1к кнопки): без критерия
           # в пакете человек читает здесь, что искалось допустимое
           # решение с целевой из штрафов.
           "mode": st.get("mode"), "mode_note": st.get("mode_note"),
           # A8: трёхчастный результат последней задачи (допустимость /
           # стоимость / оптимизация, полнота покрытия, «поиск не
           # завершён») и перечень непроверенных правил — страница
           # показывает три статуса, а не одно слово.
           "result": st.get("result"),
           "uncovered": st.get("uncovered") or [],
           "certificate_gate": st.get("certificate_gate") or [],
           # A7 (очередь 3): заявление об истории сотрудника — допущение
           # «остаток на начало периода = 0» с перечнем правил.
           "assumptions": st.get("assumptions"),
           # A10 (ступень M): попытка нажатия — по ID; перечень
           # последних попыток с итогами и снимками артефактов.
           "attempt_id": st.get("attempt_id"),
           "attempts": attempts,
           "attempts_trimmed": st.get("attempts_trimmed") or 0,
           "tasks_done": st.get("tasks_done") or [],
           "llm_scope": st.get("llm_scope"), "files": files,
           # Шаг 3 (05.09.2026): прогон утверждённого пейринга — для
           # ЯВНОГО второго нажатия (ростеринг на пейринге), не для
           # молчаливой подстановки.
           "pairing_run": approved_pairing(wd),
           # W-4: все сохранённые попытки пейринга со снимком — выбор
           # ЛЮБОЙ из них явный (порядок списка выбором не является);
           # W-3: выбранный пейринг последнего нажатия и следующие шаги
           # после неудачи на его каталоге.
           "pairing_candidates": _pairing_candidates(wd, st),
           "pairing_selection": st.get("pairing_selection"),
           "next_actions": st.get("next_actions") or [],
           "selection_note": st.get("selection_note"),
           "certifier": st.get("certifier"),
           # Кто сертифицирует СЛЕДУЮЩЕЕ нажатие (настройки площадки
           # сейчас); certifier выше — кто сертифицировал последнее.
           "certifier_now": certifier_identity(package_scope(Path(cur["path"]))),
           "engines": list(ENGINES),
           # Какая выдача ростеринга последняя: «cold» (штатная по
           # рейсо-местам) либо «on-pairing» (на утверждённом пейринге);
           # фронт берёт по ней сертификат, выдачу и номер прогона.
           "roster_path": (
               {"button": "cold", "button-on-pairing": "on-pairing"}
               .get(_rd.name) if (_rd := roster_delivery_dir(wd)) else None),
           "roster_map": bool(_rd) and (_rd / "solution.json").is_file(),
           "export_mps_state": st.get("export_mps_state"),
           "export_mps_error": st.get("export_mps_error"),
           "export_mps_files": st.get("export_mps_files") or [],
           "export_mps_manifest": st.get("export_mps_manifest")}
    return out


def artifact_path(alias: str, attempt: str | None = None,
                  wd: Path | None = None) -> tuple[Path, str]:
    """Путь артефакта по алиасу на текущем пакете; отказ словами.
    attempt — артефакт из снимка попытки по ID (аудит A10, ступень M).
    wd — явный рабочий каталог (ручки области прогона, раздел 9.2)."""
    if alias not in ALLOWED_FILES:
        raise PackageRunError("неизвестный артефакт %r; перечень: %s"
                              % (alias, ", ".join(sorted(ALLOWED_FILES))))
    if wd is None:
        res = resolve_current()
        if res is None:
            raise PackageRunError("текущего пакета постановки нет")
        wd = res.wd
    sub, name = ALLOWED_FILES[alias]
    wd = Path(wd)
    if attempt:
        if not all(c.isalnum() or c in "-_" for c in attempt):
            raise PackageRunError("идентификатор попытки не похож на свой")
        path = wd / ATTEMPTS_DIR / attempt / sub / name
        if not path.is_file():
            raise PackageRunError("артефакт %s в снимке попытки %s не найден"
                                  % (alias, attempt))
        return path, name
    path = wd / sub / name
    if not path.is_file():
        raise PackageRunError("артефакт %s ещё не построен" % alias)
    return path, name


def export_mps(start_thread: bool = True, builder_factory=None) -> dict:
    """Экспорт точной модели покрытия (MPS/LP + справочник переменных +
    манифест) для внешнего CPLEX — по артефактам ПОСЛЕДНЕГО нажатия
    «Пейринг» на текущем пакете с теми же аргументами профиля: модель
    идентична боевой, счёт не запускается (--export-only). Файлы — в
    package-runs/<sha>/export-mps/, перечень — в состоянии."""
    from vmp.run import button as btn
    from vmp.run import mps_manifest as mmf
    res = resolve_current()
    if res is None:
        raise PackageRunError("текущего пакета постановки нет")
    sha, wd = res.sha, res.wd
    src = wd / "button-pairing"
    missing = [n for n in ("builder-payload.json", "builder-input.json",
                           "package.zip") if not (src / n).is_file()]
    if missing:
        raise PackageRunError("экспортировать нечего: нет "
                              + ", ".join(missing)
                              + " — сначала нажатие «Пейринг»")
    # Занятие линии экспорта — той же операцией под замком, что у
    # кнопки (аудит A10); сирота «экспорт идёт» помечается там же.
    # Экспорт читает артефакты нажатия — при живом расчёте ждёт его.
    _claim_line(wd, "export", "экспорт уже идёт", also=("press",),
                export_mps_state="экспорт идёт",
                export_mps_error=None, export_mps_files=None,
                export_mps_manifest=None)
    factory = builder_factory or btn.subprocess_pairing_builder

    def _work():
        try:
            payload = json.loads((src / "builder-payload.json")
                                 .read_text(encoding="utf-8"))
            args = list(json.loads((src / "builder-input.json")
                                   .read_text(encoding="utf-8"))["args"])
            out = wd / "export-mps"
            out.mkdir(parents=True, exist_ok=True)
            mps, lp = out / "model.mps", out / "model.lp"
            args += ["--export-only", "--export-mps", str(mps),
                     "--export-lp", str(lp)]
            builder = factory("export-%s" % sha[:12])
            try:
                builder(payload, args, out)
            except btn.ButtonStop:
                pass    # счёт оборван намеренно; судим по файлам
            map_p = Path(str(mps) + ".map.json")
            if not mps.is_file() or not map_p.is_file():
                raise RuntimeError(
                    "построитель не выгрузил модель (нет model.mps либо "
                    "справочника .map.json) — журнал построителя в "
                    "export-mps/")
            man = mmf.build_export(
                out, mps_path=mps, lp_path=(lp if lp.is_file() else None),
                map_path=map_p,
                package_sha256=_sha256(src / "package.zip"),
                source_label="экспорт кнопки COSMOS на пакете %s"
                             % sha[:12])
            names = [f["name"] for f in man["files"]] \
                + [man["map"]["name"], "export-manifest.json"]
            _update_state(wd, export_mps_state="экспорт готов",
                          export_mps_files=names,
                          export_mps_manifest={
                              "included": man["included"],
                              "excluded": man["excluded"],
                              "solvers": man["solvers"],
                              "judge": man["judge"],
                              "pool": man["pool"],
                              "obligations": man["obligations"]})
        except BaseException as e:                        # noqa: BLE001
            _update_state(wd, export_mps_state="экспорт: отказ",
                          export_mps_error=str(e)[:500])
            if not isinstance(e, Exception):
                raise

    _run_line(wd, "export", _work, start_thread)
    return status()


def export_file(name: str) -> Path:
    """Файл готового экспорта — только из перечня манифеста."""
    res = resolve_current()
    if res is None:
        raise PackageRunError("текущего пакета постановки нет")
    wd = res.wd
    allowed = read_state(wd).get("export_mps_files") or []
    if name not in allowed:
        raise PackageRunError("файла %r нет в готовом экспорте; перечень: %s"
                              % (name, allowed))
    path = wd / "export-mps" / name
    if not path.is_file():
        raise PackageRunError("файл %s не найден на диске" % name)
    return path


def roster_map() -> dict:
    from vmp.run import roster_map as rmp
    res = resolve_current()
    if res is None:
        raise PackageRunError("текущего пакета постановки нет")
    cur, wd = res.cur, res.wd
    _rd = roster_delivery_dir(wd)
    path = (_rd or (wd / "button")) / "solution.json"
    if not path.is_file():
        raise PackageRunError("решения ростеринга ещё нет — нажмите "
                              "«Ростеринг»")
    # Координаты и класс данных — из ТЕКУЩЕГО пакета (книга авиакомпании
    # первична, справочник только у open/proprietary), как у карты
    # постановки; пакет не читается — карта без координат, только схема.
    known: dict | None = None
    data_class: str | None = None
    try:
        from . import task_package, geo as geo_mod
        pkg = task_package.load(cur["path"], run_gate=False)
        payload = task_package.to_payload(pkg)
        known = geo_mod.known_from_airports(payload.get("airports"))
        kit = (pkg.get("manifest") or {}).get("kit") or {}
        data_class = kit.get("data_class") if isinstance(kit, dict) else None
    except Exception:                                      # noqa: BLE001
        known, data_class = None, None
    try:
        return rmp.build_roster_map(json.loads(
            path.read_text(encoding="utf-8")),
            known=known, data_class=data_class)
    except rmp.RosterMapError as e:
        raise PackageRunError(str(e)) from None


# ── Выгрузки решений в известных форматах (слово владельца 06.09.2026) ──
# Сбор артефактов последней выдачи (либо снимка попытки) в разобранные
# объекты для чистых функций vmp/export_solutions.py; диск читается
# только здесь. Выгрузки не хранятся — строятся по требованию.

def _read_json(path: Path, what: str):
    from vmp import export_solutions as xs
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise xs.ExportNotFound("%s не найден: %s" % (what, path.name)) from None
    except ValueError as e:
        raise xs.ExportRefused("%s повреждён (%s): %s" % (what, path.name, e)) from None


def _read_optional(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _package_parts(pkg: Path) -> tuple[dict, str]:
    """(manifest.json, sha256 байт model.json) из пакета выдачи."""
    import hashlib
    import zipfile
    from vmp import export_solutions as xs
    try:
        with zipfile.ZipFile(pkg) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            names = zf.namelist()
            model_sha = (hashlib.sha256(zf.read("model.json")).hexdigest()
                         if "model.json" in names else "")
    except (OSError, KeyError, ValueError, zipfile.BadZipFile) as e:
        raise xs.ExportRefused("пакет выдачи не читается (%s): %s"
                               % (pkg.name, e)) from None
    if not isinstance(manifest, dict):
        manifest = {}
    return manifest, model_sha


def _payload_from_package(pkg: Path) -> dict:
    from vmp import export_solutions as xs
    try:
        from webapp import task_package
        return task_package.to_payload(task_package.load(pkg, run_gate=False))
    except Exception as e:                                  # noqa: BLE001
        raise xs.ExportRefused("данные пакета не восстановлены из package.zip: %s"
                               % str(e)[:300]) from None


def _export_root(attempt: str | None,
                 wd: Path | None = None) -> tuple[Path, str, str | None]:
    """Корень артефактов: каталог пакета либо снимок попытки. wd —
    явный рабочий каталог (ручки области прогона, раздел 9.2)."""
    from vmp import export_solutions as xs
    if wd is None:
        res = resolve_current()
        if res is None:
            raise xs.ExportNotFound("текущего пакета постановки нет — загрузите "
                                    "пакет из CDPM")
        sha, wd = res.sha, res.wd
    else:
        wd = Path(wd)
        sha = str(read_state(wd).get("package_sha") or wd.name)
    if attempt:
        if not all(c.isalnum() or c in "-_" for c in attempt):
            raise xs.ExportBadRequest("идентификатор попытки не похож на свой")
        root = wd / ATTEMPTS_DIR / attempt
        if not root.is_dir():
            raise xs.ExportNotFound("снимка попытки %s на этом пакете нет"
                                    % attempt)
        return root, sha, attempt
    return wd, sha, None


def _pairing_catalog_for_roster(wd: Path, delivery: dict) -> tuple[dict | None, str | None]:
    """Каталог пейринга того же прогона: снимок попытки из
    delivery.pairing_selection.attempt_id (в выдаче он есть прямо) с
    проверкой SHA каталога, иначе текущий button-pairing/ при совпадении
    прогона и SHA; не найден — (None, пометка словами)."""
    import hashlib
    run_id = str(delivery.get("pairing_run_id") or "")
    sel = delivery.get("pairing_selection") if isinstance(
        delivery.get("pairing_selection"), dict) else {}
    want_sha = delivery.get("pairing_catalog_sha256") or sel.get("catalog_sha256")
    candidates = []
    if sel.get("attempt_id"):
        candidates.append(wd / ATTEMPTS_DIR / str(sel["attempt_id"]) / "button-pairing")
    candidates.append(wd / "button-pairing")
    for d in candidates:
        p = d / "builder-catalog.json"
        if not p.is_file():
            continue
        try:
            raw = p.read_bytes()
            dl = json.loads((d / "delivery.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        got_run = str((dl.get("schedule") or {}).get("solver_run_id") or "")
        if run_id and got_run != run_id:
            continue
        digest = hashlib.sha256(raw).hexdigest()
        if want_sha and digest != want_sha:
            continue
        try:
            return json.loads(raw), None
        except ValueError:
            continue
    return None, ("каталог пейринга прогона %s не найден (снимок попытки и "
                  "button-pairing не совпали по прогону/SHA) — идентификаторы "
                  "цепочек по метке решателя, отдых после цепочки по порогам"
                  % (run_id or "?"))


def export_sources(solution: str, *, attempt: str | None = None,
                   path: str | None = None, wd: Path | None = None):
    """PairingSources / RosterSources по артефактам последней выдачи
    (либо снимка попытки `attempt`); ростер — новейшая из выдач
    button/ и button-on-pairing/ либо явный `path` (cold | on-pairing).
    wd — явный рабочий каталог (ручки области прогона).
    Отказы — словами (ExportNotFound / ExportRefused / ExportBadRequest)."""
    from vmp import export_solutions as xs
    if solution not in ("pairing", "roster"):
        raise xs.ExportNotFound("решение %r неизвестно; перечень: pairing, roster"
                                % solution)
    root, sha, attempt = _export_root(attempt, wd=wd)
    where = ("в снимке попытки %s" % attempt) if attempt else "на этом пакете"
    if solution == "pairing":
        d = root / "button-pairing"
        if not (d / "delivery.json").is_file():
            raise xs.ExportNotFound(
                "решения пейринга ещё нет — нажмите «Пейринг»" if not attempt
                else "%s нет артефактов пейринга" % where)
        delivery = _read_json(d / "delivery.json", "выдача пейринга")
        catalog = _read_json(d / "builder-catalog.json", "каталог цепочек")
        pkg = d / "package.zip"
        if not pkg.is_file():
            raise xs.ExportNotFound("%s нет пакета выдачи пейринга (package.zip)" % where)
        manifest, model_sha = _package_parts(pkg)
        payload = _read_optional(d / "builder-payload.json")
        if not isinstance(payload, dict):
            payload = _payload_from_package(pkg)
        return xs.PairingSources(
            delivery=delivery, catalog=catalog, payload=payload,
            manifest=manifest, rules=_read_optional(d / "source-rules.json"),
            certificate=_read_optional(d / "certificate.json"),
            model_sha256=model_sha, package_sha=sha, attempt=attempt)
    if path is not None and path not in xs.ROSTER_PATHS:
        raise xs.ExportBadRequest("path %r вне словаря: %s"
                                  % (path, ", ".join(xs.ROSTER_PATHS)))
    if path:
        d = root / xs.ROSTER_PATHS[path]
    else:
        d = roster_delivery_dir(root)
    if d is None or not (d / "delivery.json").is_file():
        raise xs.ExportNotFound(
            "решения ростеринга ещё нет — нажмите «Ростеринг»" if not attempt
            else "%s нет артефактов ростеринга" % where)
    delivery = _read_json(d / "delivery.json", "выдача ростеринга")
    solution_doc = _read_json(d / "solution.json", "решение ростера")
    pkg = d / "package.zip"
    if not pkg.is_file():
        raise xs.ExportNotFound("%s нет пакета выдачи ростеринга (package.zip)" % where)
    manifest, model_sha = _package_parts(pkg)
    solved = _read_optional(d / "solved.json")
    payload = solved if isinstance(solved, dict) and solved.get("flights") \
        else _payload_from_package(pkg)
    # Каталог пейринга — по рабочему каталогу ПАКЕТА того же прогона
    # (снимки попыток лежат там же), даже когда сам ростер читается из
    # снимка.
    wd_pkg = Path(wd) if wd is not None else (root if not attempt else root.parent.parent)
    cat, cat_note = _pairing_catalog_for_roster(wd_pkg, delivery)
    return xs.RosterSources(
        delivery=delivery, solution=solution_doc, solved=solved,
        payload=payload, manifest=manifest,
        certificate=_read_optional(d / "certificate.json"),
        model_sha256=model_sha, package_sha=sha, attempt=attempt,
        path={"button": "cold", "button-on-pairing": "on-pairing"}.get(d.name),
        pairing_catalog=cat, pairing_catalog_note=cat_note)


def export_index(wd: Path | None = None) -> dict:
    """Указатель доступности выгрузок для панели «Решения»: по каждому
    решению и формату — ok либо причина словами. wd — явный рабочий
    каталог (ручки области прогона)."""
    from vmp import export_solutions as xs
    if wd is None:
        res = resolve_current()
        if res is None:
            return {"present": False}
        sha, wd = res.sha, res.wd
    else:
        wd = Path(wd)
        sha = str(read_state(wd).get("package_sha") or wd.name)
    base = "/api/task-package/current/export"
    out = {"present": True, "package_sha": sha,
           "roster_path": ({"button": "cold", "button-on-pairing": "on-pairing"}
                           .get(_rd.name) if (_rd := roster_delivery_dir(wd)) else None),
           "solutions": {}}
    for kind in ("pairing", "roster"):
        try:
            src = export_sources(kind, wd=wd)
        except xs.ExportError as e:
            out["solutions"][kind] = {
                "present": False, "reason": str(e),
                "formats": {f: {"ok": False, "reason": str(e)}
                            for f in xs.FORMATS[kind]}}
            continue
        rec = xs.availability(src, kind)
        for fmt, info in rec["formats"].items():
            info["href"] = "%s/%s/%s" % (base, kind, fmt)
        out["solutions"][kind] = rec
    return out


def export_solution(solution: str, fmt: str, *, attempt: str | None = None,
                    path: str | None = None, employee: str | None = None,
                    allow_uncertified: bool = False, utc: bool = False,
                    roles: str | None = None, dataset_id: str | None = None,
                    preset_id: str | None = None, wd: Path | None = None):
    """Файл выгрузки: (байты, имя ASCII, имя UTF-8, media type, meta)."""
    from vmp import export_solutions as xs
    src = export_sources(solution, attempt=attempt, path=path, wd=wd)
    opts = xs.Options(utc=utc, roles=roles, dataset_id=dataset_id,
                      preset_id=preset_id, allow_uncertified=allow_uncertified,
                      employee=employee)
    return xs.export(src, fmt, opts)


def _journal_source_check(wd: Path, task: str, attempt_id: str | None,
                          snap: dict, actor: str | None) -> None:
    """Этап «сверка с ИДАК» — в летопись прогона. Беда летописи не
    отменяет сверки: снимок с привязкой уже на месте, а причина едет
    словами в журнал службы (правило раздела 10 проекта записи)."""
    try:
        from webapp import run_gate, run_journal
        head = run_gate.head_for(wd, _current_manifest(wd))
        run_journal.note_source_check(wd, task, attempt_id, snap, actor, head)
    except Exception as e:                                 # noqa: BLE001
        log.warning("сверка с ИДАК не легла в летопись прогона: %s: %s",
                    type(e).__name__, str(e)[:300])


def _current_manifest(wd: Path) -> dict:
    """Манифест пакета этого рабочего каталога: из записи состояния
    (путь пакета) — сверка идёт после нажатия, и путь уже записан."""
    path = read_state(wd).get("package_path")
    return _package_manifest(Path(str(path))) if path else {}


def note_source_check(wd: Path, task: str, solver_run_id: str | None,
                      catalog_sha256: str | None, snap: dict,
                      actor: str | None = None) -> str | None:
    """Привязка сверки по ИДАК к попытке (раздел 7.7): попытка ищется по
    `delivery.schedule.solver_run_id` (ростеринг) либо по
    `delivery.catalog_sha256` (пейринг) среди записей state.json, и через
    _mutate_state ей дописывается `source_check`. Возвращает id попытки
    либо None (не найдена — сверка остаётся на площадке как прежде).

    Расход читателя ИДАК (`resources.models[]` снимка) дописывается в
    `resources.models` попытки: сверка идёт ОТДЕЛЬНЫМ вызовом, вне
    попытки нажатия, и её токены видны там же, где токены сертификатора
    (решение владельца № 1 от 10.09.2026). Сверок у одной попытки бывает
    несколько (повторная сверка тех же клауз идёт из кэша компиляции), и
    расход роли СКЛАДЫВАЕТСЯ, а не заменяется: вызовы и токены — сумма,
    `counted` — сосчитаны ли ВСЕ слагаемые, причина — объединение причин
    без повторов. Замена теряла живой расход: вторая, кэшированная
    сверка обнуляла строку первой. Летопись этапов не складывает ничего
    — у каждой сверки свой этап со своим расходом.

    Повторная привязка ТОГО ЖЕ снимка (та же задача и тот же момент)
    расход не удваивает: сверка одна — и расход у неё один, поэтому
    строки роли заменяются, как прежде.

    `actor` — кто запустил сверку (слова `actor_words` ручки): он едет в
    запись этапа «сверка с ИДАК» летописи прогона (`run_journal`). Этап
    пишется и тогда, когда попытка не нашлась: сверка была, и запись о
    прогоне обязана её назвать.

    ОТКАЗАВШАЯ сверка (`status: error`) свод сверки попытки НЕ подменяет
    (11.09.2026). Этап летописи у неё свой — со «сбоем» и кодом
    `source-check-failed`, — и расход читателя в попытку тоже едет: он
    потрачен. А вот `source_check` попытки остаётся прежним: отказ
    счётчиков не приносит, и запись без счётчиков читается «сверка
    прошла, нарушений нет» (оценка ИДАК узла прогона судит именно их) —
    зелёная оценка от несостоявшейся сверки хуже её отсутствия. Прошлая
    сверка той же выдачи от отказа повторной не становится ложью:
    выдача не менялась."""
    wd = Path(wd)
    st = read_state(wd)
    want_id = None
    for rec in reversed(st.get("attempts") or []):
        if not isinstance(rec, dict) or not rec.get("done"):
            continue
        if task == "pairing" and rec.get("task") != "pairing":
            continue
        if task != "pairing" and rec.get("task") != "rostering":
            continue
        cat = str(rec.get("catalog") or "")
        d = _read_optional(wd / cat / "delivery.json") if cat else None
        if not isinstance(d, dict):
            continue
        if task == "pairing":
            if catalog_sha256 and d.get("catalog_sha256") == catalog_sha256:
                want_id = rec["id"]
                break
        elif solver_run_id and str((d.get("schedule") or {})
                                   .get("solver_run_id")) == str(solver_run_id):
            want_id = rec["id"]
            break
    _journal_source_check(wd, task, want_id, snap, actor)
    if want_id is None:
        return None
    snap = snap if isinstance(snap, dict) else {}
    # Сверка ОТКАЗАЛА: свод попытки не подменяется (см. описание), но
    # расход читателя в попытку едет — он потрачен.
    failed = str(snap.get("status") or "") == "error"
    counts = snap.get("counts") if isinstance(snap.get("counts"), dict) else None
    if counts is None and isinstance(snap.get("summary"), dict):
        counts = snap["summary"]
    reader = snap.get("reader") or snap.get("model")
    res = snap.get("resources") if isinstance(snap.get("resources"), dict) \
        else None
    reader_models = [m for m in ((res or {}).get("models") or [])
                     if isinstance(m, dict)]
    record = {"task": task, "at": snap.get("at") or snap.get("finished_at")
              or time.strftime("%Y-%m-%dT%H:%M:%S"),
              "reader": reader, "reader_channel": snap.get("reader_channel")
              or snap.get("channel"),
              "counts": counts,
              "resources": res if res is not None
              else {"models": [], "models_code": "snapshot-without-resources"},
              "snapshot": snap.get("snapshot")
              or ("source-check/%s/last.json" % task)}

    reader_role = _reader_role_code()

    def _sum_rows(rows: list) -> list:
        """Сложить строки расхода, описывающие ОДНО и то же (роль,
        модель, канал): вызовы и токены — сумма, `counted` — сосчитаны
        ли все слагаемые, причина — объединение причин без повторов.

        `counted` складывается «и», а не «или» (так же, как в своде по
        дереву прогонов): половина расхода, выданная за целое, хуже
        честного «не считается». Счётчик, которого не назвал никто, в
        сумме не появляется — отсутствующий счётчик не ноль."""
        from webapp import llm_backend as _lb
        fields = _lb.USAGE_KEYS + ("calls", "uncounted_calls")
        order: list = []
        groups: dict = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = (row.get("role_code") or row.get("role"),
                   row.get("model"), row.get("channel"))
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)
        out: list = []
        for key in order:
            part = groups[key]
            if len(part) == 1:
                out.append(dict(part[0]))
                continue
            acc: dict = {name: None for name in
                         ("role", "role_code", "provider", "model", "channel")}
            notes: list = []
            counted = True
            for row in part:
                for name in tuple(acc):
                    if acc[name] in (None, "") \
                            and row.get(name) not in (None, ""):
                        acc[name] = row[name]
                for name in fields:
                    try:
                        value = int(row[name])
                    except (KeyError, TypeError, ValueError):
                        continue
                    acc[name] = (acc.get(name) or 0) + value
                counted = counted and bool(row.get("counted"))
                note = str(row.get("note") or "").strip()
                if note and note not in notes:
                    notes.append(note)
            acc["counted"] = counted
            if notes:
                acc["note"] = "; ".join(notes)
            out.append(acc)
        return out

    def _apply(st: dict) -> None:
        was = (_attempt_record(st, want_id) or {}).get("source_check")
        # Тот же снимок, привязанный второй раз, сам с собой не
        # складывается: сверка одна — и расход у неё один.
        again = (isinstance(was, dict) and was.get("task") == record["task"]
                 and was.get("at") == record["at"])
        if not failed:
            _mark_attempt(st, want_id, source_check=record)
        rec = _attempt_record(st, want_id)
        if rec is None or not reader_models:
            return
        cur = rec.get("resources") if isinstance(rec.get("resources"), dict) \
            else {"models": [], "solver_seconds": None,
                  "solver_seconds_code": "no-call-journal"}
        rows = [m for m in (cur.get("models") or []) if isinstance(m, dict)]
        keep = [m for m in rows if m.get("role_code") != reader_role]
        mine = [] if again else [m for m in rows
                                 if m.get("role_code") == reader_role]
        cur["models"] = keep + _sum_rows(
            mine + [dict(m) for m in reader_models])
        cur.pop("models_code", None)
        rec["resources"] = cur
    _mutate_state(wd, _apply)
    return want_id


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()
