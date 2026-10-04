# -*- coding: utf-8 -*-
"""Рост каталога шаблонов: синтетическая запись и заготовка кандидата.

Каталог шаблонов РАСТЁТ: конвейер заявок добавляет в него новые семьи и
критерии. Судьи и тесты, написанные на неподвижный каталог, ломаются на
самом росте, а не на проверяемом правиле — 09.09.2026 четыре попытки
агента из пяти сорвались именно так (жёсткое число записей в ожидании
теста, рукописные перечни семей у судей, копия каталога без заявки-
свидетеля). Носитель у роста один — этот модуль:

* СИНТЕТИЧЕСКАЯ ЗАПИСЬ (`probe_family`, `probe_criterion`) — семья и
  критерий, которых в живом каталоге нет и не будет. Их подмешивает в
  свои копии набор тестов, когда включён режим роста
  (`COSMOS_TEST_CATALOG_GROWN`), и тогда каждый судья обязан либо
  остаться зелёным, либо назвать пробел покрытия СЛОВАМИ («исполнителя
  нет»), а не сломаться на числе записей.
* ЗАГОТОВКА КАНДИДАТА (`draft_candidate`) — запись, какой её видит
  предполётная проверка задания агенту: параметры с единицами и
  объявленные виды исполнителей есть, самих исполнителей ещё нет.
* ЗАЯВКА-СВИДЕТЕЛЬ (`probe_request`) — принятая заявка, подтверждающая
  единицы имён новой записи. Без неё копия каталога «не собрана» по
  построению: единицу имени, которого не знают ни словарь CDPM, ни
  таблицы решателя, подтверждает только черновик заявки.

Три режима роста названы прямо, потому что честны все и проверяют
разное:

``extension``
    Семья-РАСШИРЕНИЕ с объявленным видом исполнителя, но БЕЗ
    исполнителя — так выглядит кандидат до работы агента. Ожидаемый
    вердикт судей: «исполнителя нет».
``clone``
    КЛОН существующей семьи: те же виды исполнителей и те же строки
    привязок (символы образца). Так выглядит запись, покрытие которой
    уже написано, и все судьи обязаны остаться зелёными.
``draft``
    Заготовка НАСТОЯЩЕЙ заявки: значение переменной — путь к файлу
    кандидата либо его JSON. Так предполётная проверка задания агенту
    прогоняет набор тестов на той записи, которую предстоит
    реализовать.

Двух проектных решений режим не скрывает, потому что оба назвал КОД.

Повторить имена параметров образца клон не вправе: правило X02
читателя запрещает одно имя в двух записях, а карта `Catalog.params()`
плоская — «имя → семья», и при повторе имя молча досталось бы
последней записи. «Одно имя — одна семья» здесь не допущение судей, а
закон каталога.

Своих имён параметров у синтетической записи тоже нет: единицу имени,
которого не знают ни словарь CDPM, ни таблицы решателя, подтверждает
только черновик принятой заявки — и такая заявка-свидетель меняла бы
очередь КАЖДОЙ копии каталога, то есть помощник роста стал бы вторым
предметом суда вместо пробы. Семьи без параметров в каталоге законны.

Модуль — часть читателя: только стандартная библиотека, при импорте ни
одного файла и ни одной переменной среды.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import os

from .requests import cr_key_of, request_id_of, request_key

#: Разделы каталога, к которым относится запись.
SECTIONS = ("families", "criteria")

# --------------------------------------------------------- режим роста
#: Переменная среды режима роста каталога в наборе тестов. Без неё
#: копии каталога прежние — обычный гейт продукта не меняется.
MODE_ENV = "COSMOS_TEST_CATALOG_GROWN"
#: Семья-расширение без исполнителей (умолчание режима).
MODE_EXTENSION = "extension"
#: Клон существующей семьи с её исполнителями.
MODE_CLONE = "clone"
#: ЗАГОТОВКА настоящей заявки: значение переменной — путь к файлу
#: кандидата либо сам его JSON. Так предполётная проверка задания агенту
#: прогоняет набор тестов копий каталога на ТОЙ ЖЕ записи, которую
#: предстоит реализовать, а не на выдуманной.
MODE_DRAFT = "draft"
MODES = (MODE_EXTENSION, MODE_CLONE)
#: Значения переменной, означающие «режим включён, режим по умолчанию».
_MODE_ON = ("1", "on", "yes", "true", "да", "grown", "рост")
#: Значения, означающие «режим выключен» (пустое значение — тоже).
_MODE_OFF = ("", "0", "off", "no", "false", "нет")


class GrowthError(ValueError):
    """Негодное значение режима роста либо негодная заготовка записи."""


def mode(environ=None) -> str | None:
    """Режим роста каталога по переменной среды: ``None`` (режима нет),
    ``"extension"``, ``"clone"`` либо ``"draft"`` (значение переменной —
    путь к файлу заготовки кандидата либо сам её JSON). Неизвестное
    значение — отказ словами, а не тихое умолчание: молчание превратило
    бы опечатку в «режим выключен», и прогон «в режиме роста» ничего бы
    не проверял."""
    env = os.environ if environ is None else environ
    value = str(env.get(MODE_ENV, "") or "").strip()
    raw = value.casefold()
    if raw in _MODE_OFF:
        return None
    if raw in _MODE_ON:
        return MODE_EXTENSION
    if raw in MODES:
        return raw
    if draft_of_value(value) is not None:
        return MODE_DRAFT
    raise GrowthError(
        f"{MODE_ENV}={value!r}: режим роста каталога — одно из "
        f"{list(MODES)}, «1» (умолчание — {MODE_EXTENSION}) либо путь к "
        f"файлу заготовки кандидата (её JSON)")


#: Разобранные заготовки по значению переменной: `mode()` зовут десятки
#: раз за прогон, и читать файл каждый раз незачем.
_DRAFT_CACHE: dict = {}


def draft_of_value(value: str):
    """Файл-кандидат по значению переменной режима: путь к файлу либо
    сам JSON. Не разобралось — ``None`` (значение не заготовка)."""
    key = str(value or "")
    if not key:
        return None
    if key in _DRAFT_CACHE:
        return _DRAFT_CACHE[key]
    doc = None
    text = None
    if key.lstrip().startswith("{"):
        text = key
    else:
        try:
            with open(key, encoding="utf-8-sig") as fh:
                text = fh.read()
        except OSError:
            text = None
    if text:
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict) and parsed.get("section") in SECTIONS \
                and isinstance(parsed.get("record"), dict):
            doc = parsed
    _DRAFT_CACHE[key] = doc
    return doc


def draft_of_env(environ=None):
    """Файл-кандидат режима «заготовка» из переменной среды."""
    env = os.environ if environ is None else environ
    return draft_of_value(str(env.get(MODE_ENV, "") or "").strip())


# ------------------------------------------------- синтетическая запись
#: Идентификатор синтетической семьи. Приставка «zz_» — говорящая: в
#: отсортированном каталоге такая запись всегда последняя, и ни одна
#: живая семья так не называется.
PROBE_FAMILY_ID = "zz_probe_growth"
#: Идентификатор синтетического критерия.
PROBE_CRITERION_ID = "zz_probe_growth_rate"
#: Приставка синтетических имён (полей формы экземпляра критерия).
PROBE_PARAM_PREFIX = "zz_probe_"
#: Виды исполнителей семьи логических форм — форма решателя плюс
#: проверка кода: тот класс записей, на котором 09.09.2026 сорвались
#: четыре попытки агента. У записи каталога это поле `executor_kinds`,
#: у строк посева и привязок — `kind` каждой строки.
LOGIC_FORM_KINDS = frozenset({"kotlin_logic_form", "check_code"})


def logic_form_ids(kinds_of) -> tuple[str, ...]:
    """Идентификаторы семей логических форм по ПРИЗНАКУ — оба вида
    исполнителей из `LOGIC_FORM_KINDS` — в обратном алфавитном порядке.

    ``kinds_of`` — пары ``(id семьи, виды исполнителей)``. Порядок один
    и тот же для записей каталога (`executor_kinds`) и для строк посева
    или привязок (`kind` строк), поэтому образец клона выбирается
    одинаково по любой стороне — и запись, и строки клона берутся у
    одной семьи. Имена семей в этом файле не пишутся, а пакет читателя
    остаётся самостоятельным (без vmp): рост каталога меняет записи,
    а не помощник роста (сторожа tests/test_single_carriers.py и
    tests/test_template_catalog_reader.py)."""
    ids = []
    for fid, kinds in kinds_of:
        # Ровно оба вида (сверх видов, ключ которых — имя параметра: они
        # уходят вместе с параметрами). Семья с ДРУГИМИ видами — например
        # с шаблоном проверки, у которого исполнителя нет, — образцом не
        # годится: клон унаследовал бы строку «нет», а клон обязан
        # показывать «покрытие есть».
        declared = {str(k) for k in (kinds or ())} - set(_PARAM_KEYED_KINDS)
        if declared == set(LOGIC_FORM_KINDS):
            ids.append(str(fid))
    return tuple(sorted(set(ids), reverse=True))
#: Пометка, по которой синтетическую запись узнают на любом экране.
PROBE_MARK = "синтетическая, для проверки роста каталога"
#: Кто заводит синтетическую запись (значение словаря `actors`).
PROBE_BY = "сессия COSMOS"


def _today() -> str:
    """Дата истории синтетической записи — сегодняшняя: база заморозки
    базовых записей осталась в прошлом, и запись «после заморозки» —
    ровно то, чем растёт каталог."""
    return _dt.date.today().isoformat()


def _origin(date: str) -> dict:
    return {"kind": "синтетика", "date": date, "confirmed_by": PROBE_BY,
            "note": "запись помощника проверки роста каталога; данных "
                    "авиакомпании не содержит"}


def _history(date: str) -> list:
    return [{"by": PROBE_BY, "date": date, "kind": "создание",
             "note": "запись заведена помощником проверки роста каталога",
             "ref": None}]


def probe_family(*, mode_name: str = MODE_EXTENSION, source: dict | None = None,
                 date: str | None = None) -> dict:
    """Синтетическая семья режима роста — запись целиком.

    ``mode_name="extension"``: семья-РАСШИРЕНИЕ с объявленным видом
    `check_code`, без задач стенда и без слов-носителей. Исполнителя у
    неё нет — судьи обязаны сказать это словами, а не сломаться.

    ``mode_name="clone"``: копия записи `source` (её подставляет
    `probe_records` из каталога) с новым идентификатором. Виды
    исполнителей, вид, зона и шаблоны проверок — образца: покрытие у
    такой записи есть. Параметров нет ни у той, ни у другой (пояснение
    ниже по коду), природа клона — «расписание» (правило L15).
    """
    date = date or _today()
    if mode_name == MODE_CLONE:
        if not isinstance(source, dict) or not source.get("id"):
            raise GrowthError(
                "режим клона требует записи-образца: ни одной семьи "
                "логических форм (виды исполнителей %s) в каталоге нет"
                % ", ".join(sorted(LOGIC_FORM_KINDS)))
        rec = copy.deepcopy(source)
        rec["id"] = PROBE_FAMILY_ID
        # Имён параметров у синтетической записи НЕТ — ни своих, ни
        # образца. Повторить имена образца нельзя (X02: одно имя — одна
        # запись), а своё имя потребовало бы заявки-СВИДЕТЕЛЯ в очереди
        # КАЖДОЙ копии: единицу имени, которого не знают ни словарь CDPM,
        # ни таблицы решателя, подтверждает только черновик принятой
        # заявки. Такая заявка меняла бы очередь всех стендов службы, и
        # помощник роста стал бы вторым предметом суда вместо пробы.
        # Семьи без параметров в каталоге законны (их две среди живых).
        rec["params"] = []
        rec["param_sets"] = []
        rec["conditions"] = []
        rec["complements"] = []
        # Виды, ключ которых — имя параметра, вместе с параметрами и
        # уходят: объявленный вид без строки привязок сделал бы копию
        # красной по построению.
        rec["executor_kinds"] = [k for k in (rec.get("executor_kinds") or [])
                                 if k not in _PARAM_KEYED_KINDS]
        rec["subject_words"] = None
        rec["rule_form"] = None
        # Природа выводится ИЗ КОДА в обе стороны (правило L15 закона
        # паритета): семьями истории код зовёт те, чью форму знают
        # таблицы решателя, семьями людей и параметров мира — те, что
        # названы перечнями сертификата ростера. Синтетической записи код
        # не знает ни с одной стороны, и любая природа, кроме
        # «расписание», была бы у неё ложью — красной по делу, а не по
        # росту каталога.
        rec["nature"] = "schedule"
        # Три поля 0.5.0 у клона — по его собственной природе и виду, а
        # не по образцу: природа клона «расписание», значит истории он не
        # читает (X19) и свидетеля природы у него нет (X17), а размер
        # нарушения задан ровно у мягкой записи (X16).
        rec["reads_history"] = None
        rec["nature_confirmed_by"] = "none"
        if rec.get("kind") != "SOFT":
            rec["violation_size"] = None
        elif not rec.get("violation_size"):
            rec["violation_size"] = "count"
        # Задачи стенда образца названы по его символам и его мирам: у
        # клона они судили бы чужого исполнителя. Пустой перечень —
        # предупреждение X07 «запись «есть» без стендовых задач», и это
        # ЗАЯВЛЕННАЯ причина, а не поломка.
        rec["stand_tasks"] = []
        rec["status"] = "есть"
        rec["class"] = "extension"
        rec["title"] = f"{PROBE_MARK}: клон записи каталога"
        rec["subject"] = "проверка роста каталога шаблонов"
        rec["note"] = PROBE_MARK
        rec["origin"] = _origin(date)
        rec["history"] = _history(date)
        rec["acceptance"] = None
        return rec
    if mode_name != MODE_EXTENSION:
        raise GrowthError(f"режим роста {mode_name!r} неизвестен")
    return {
        "id": PROBE_FAMILY_ID,
        "class": "extension",
        "title": f"{PROBE_MARK}: семья-расширение без исполнителя",
        "subject": "проверка роста каталога шаблонов",
        "subject_words": None,
        "complements": [],
        "kind": "HARD",
        "zone": "ROSTERING",
        # Природа НЕ «история»: правило X13 требует у семьи истории вид
        # `kotlin_logic_form`, а расширение без исполнителя обязано
        # оставаться на одном объявленном виде.
        "nature": "schedule",
        # Свидетеля природы у синтетической записи нет: кода она не
        # знает ни с одной стороны, а её происхождение — «синтетика», не
        # заявка, и правило X18 её не касается.
        "nature_confirmed_by": "none",
        # Истории семья-расширение не читает (природа «расписание»), а
        # размер нарушения задан ровно у мягкой записи — эта жёсткая.
        "reads_history": None,
        "violation_unit": None,
        "violation_size": None,
        # Параметров нет: см. пояснение в ветке клона выше — своё имя
        # потребовало бы заявки-свидетеля в очереди каждой копии.
        "params": [],
        "param_sets": [],
        "conditions": [],
        "rule_form": None,
        "semantics": {
            "measure": "ничего: запись синтетическая",
            "scope": "мира у записи нет",
            "text": "Запись заведена помощником проверки роста каталога и "
                    "смысла правила не несёт. Она нужна там, где судья "
                    "обязан назвать пробел покрытия словами.",
            "unit": "запись"},
        "executor_kinds": ["check_code"],
        "check_templates": [],
        "acceptance": None,
        "origin": _origin(date),
        "note": PROBE_MARK,
        "status": "есть",
        "history": _history(date),
        "stand_tasks": [],
    }


def probe_criterion(*, date: str | None = None) -> dict:
    """Синтетический критерий режима роста — запись целиком.

    Критерий у обоих режимов один: исполнителя у него нет ни в
    расширении, ни в клоне (вид объявлен, строки привязок «нет»), а
    формы, по которой его можно было бы клонировать, у критериев нет —
    их считает построитель."""
    date = date or _today()
    return {
        "id": PROBE_CRITERION_ID,
        "class": "extension",
        "title": f"{PROBE_MARK}: критерий без исполнителя",
        "zone": "PAIRING",
        "needs": None,
        "semantics": {
            "formula": None,
            "rates_form": None,
            "text": "Критерий заведён помощником проверки роста каталога и "
                    "смысла меры не несёт."},
        "instance_form": {
            "fields": [{"name": PROBE_PARAM_PREFIX + "rate", "required": True,
                        "note": "синтетическая ставка, значения не имеет"}],
            "note": None},
        "computable_by": ["builder_method"],
        "refusal": "критерий синтетический — вычислять его нечем и незачем",
        "acceptance": None,
        "origin": _origin(date),
        "note": PROBE_MARK,
        "status": "есть",
        "history": _history(date),
        "stand_tasks": [],
    }


def clone_source(families_full) -> dict | None:
    """Запись-образец режима клона среди записей каталога."""
    by_id = {str(r.get("id")): r for r in (families_full or ())
             if isinstance(r, dict)}
    for fid in logic_form_ids((fid, r.get("executor_kinds"))
                              for fid, r in by_id.items()):
        rec = by_id.get(fid)
        if isinstance(rec, dict) and rec.get("status", "есть") == "есть":
            return rec
    return None


def probe_records(mode_name: str, families_full=None, *,
                  date: str | None = None, environ=None) -> tuple[dict, dict]:
    """({id семьи: запись}, {id критерия: запись}) для режима роста.

    В режиме «заготовка» это ОДНА запись настоящей заявки — та, которую
    предстоит реализовать: набор тестов копий каталога прогоняется на
    ней, а не на выдуманной."""
    if mode_name == MODE_DRAFT:
        doc = draft_of_env(environ)
        if not isinstance(doc, dict):
            raise GrowthError("режим «заготовка» включён, а заготовки в "
                              "переменной среды нет")
        rec = record_of_draft(doc.get("record") or {},
                              record_id=str(doc.get("id")))
        rec.setdefault("history", _history(date or _today()))
        return (({str(doc.get("id")): rec}, {})
                if doc.get("section") == "families"
                else ({}, {str(doc.get("id")): rec}))
    src = clone_source(families_full) if mode_name == MODE_CLONE else None
    return ({PROBE_FAMILY_ID: probe_family(mode_name=mode_name, source=src,
                                           date=date)},
            {PROBE_CRITERION_ID: probe_criterion(date=date)})


def probe_ids(environ=None) -> set:
    """Идентификаторы синтетических записей — по ним их узнают судьи и
    помощники копий. В режиме «заготовка» это имя настоящей записи."""
    doc = draft_of_env(environ)
    if isinstance(doc, dict) and doc.get("id"):
        return {str(doc["id"])}
    return {PROBE_FAMILY_ID, PROBE_CRITERION_ID}


def probe_executor_rows(mode_name: str, section: str, rid: str,
                        live_rows: dict | None = None) -> list | None:
    """Строки исполнителей синтетической записи для файла привязок.

    Режим клона у СЕМЬИ отдаёт строки образца (символы существуют, гейт
    привязок их находит) — покрытие у клона есть. Всюду ещё — ``None``:
    строки «нет» по объявленным видам пишет тот, кто собирает файл
    привязок копии, и судья обязан сказать «исполнителя нет».

    `live_rows` — карта ``{(раздел, id): строка}`` живого файла
    привязок: читателю каталога файл продукта неизвестен, его подаёт
    вызывающий."""
    if mode_name != MODE_CLONE or section != "families" \
            or str(rid) != PROBE_FAMILY_ID:
        return None
    rows = live_rows or {}
    kinds_of = [(fid, [e.get("kind") for e in (row.get("executors") or [])
                       if isinstance(e, dict)])
                for (sec, fid), row in rows.items()
                if sec == "families" and isinstance(row, dict)]
    for fid in logic_form_ids(kinds_of):
        row = rows.get(("families", fid))
        if isinstance(row, dict) and row.get("executors"):
            return _clone_rows(row["executors"])
    return None


#: Виды строк, ключ которых — ИМЯ ПАРАМЕТРА записи: у синтетической
#: записи параметров нет, и такая строка была бы без ключа.
_PARAM_KEYED_KINDS = ("kotlin_knob",)


def _clone_rows(executors) -> list:
    return [_renamed_row(e) for e in (executors or [])
            if isinstance(e, dict)
            and str(e.get("kind")) not in _PARAM_KEYED_KINDS]


#: Заметка строк посева синтетической записи: откуда взялась строка.
PROBE_SEED_NOTE = ("строка посева синтетической записи проверки роста "
                   "каталога")
#: Имя посева привязок, лежащего РЯДОМ с судимой папкой каталога.
#: Каталог называется ключом `--catalog`, привязки — ключом `--bindings`,
#: а посев до 10.09.2026 назвать было нечем: судья, пересобирающий
#: привязки сам, читал посев ДЕРЕВА и отказывал на любой записи, которой
#: в дереве ещё нет. Имя — контракт помощника копий и закона паритета.
BESIDE_SEED = "vmp-bindings-seed.json"


def probe_seed_records(mode_name: str, live_records: dict | None = None, *,
                       environ=None) -> dict:
    """Строки ПОСЕВА привязок синтетических записей:
    ``{"<раздел>/<id>": [строки]}``.

    Посев — третий носитель знания об исполнителях (после записи
    каталога и файла привязок), и синтетической записи он обязан знать
    ровно столько же: генератор привязок отказывает словами «запись
    каталога в посеве не названа» у любой записи, которой в посеве нет.

    Режим клона отдаёт строки образца с переименованными параметрами
    (покрытие есть), расширение — одну строку «нет» по объявленному
    виду (исполнителя нет, и это сказано строкой, а не молчанием)."""
    rows = live_records or {}
    if mode_name == MODE_DRAFT:
        doc = draft_of_env(environ) or {}
        rec = doc.get("record") or {}
        section = str(doc.get("section") or "families")
        addr = f"{section}/{doc.get('id')}"
        kinds_field = ("executor_kinds" if section == "families"
                       else "computable_by")
        # Строки посева, которые у записи УЖЕ есть, остаются: заявка на
        # вид исполнителя у существующей записи не отменяет её прежних
        # исполнителей. Дописываются строки «нет» только по видам, о
        # которых посев ещё молчит.
        have = [copy.deepcopy(r) for r in (rows.get(addr) or [])
                if isinstance(r, dict)]
        known = {str(r.get("kind")) for r in have}
        return {addr: have + [
            {"kind": k, "state": "нет", "file": None, "symbol": None,
             "params": [], "note": PROBE_SEED_NOTE + " — исполнителя нет"}
            for k in (rec.get(kinds_field) or ["check_code"])
            if str(k) not in known]}
    fam_addr = f"families/{PROBE_FAMILY_ID}"
    crit_addr = f"criteria/{PROBE_CRITERION_ID}"
    fam = None
    if mode_name == MODE_CLONE:
        kinds_of = [(addr.split("/", 1)[1],
                     [r.get("kind") for r in lst if isinstance(r, dict)])
                    for addr, lst in rows.items()
                    if isinstance(addr, str) and addr.startswith("families/")
                    and isinstance(lst, list)]
        for fid in logic_form_ids(kinds_of):
            src = rows.get(f"families/{fid}")
            if src:
                fam = _clone_rows(src)
                break
    if fam is None:
        fam = [{"kind": "check_code", "state": "нет", "file": None,
                "symbol": None, "params": [],
                "note": PROBE_SEED_NOTE + " — исполнителя нет"}]
    return {
        fam_addr: fam,
        crit_addr: [{"kind": "builder_method", "state": "нет", "file": None,
                     "symbol": None, "params": [],
                     "note": PROBE_SEED_NOTE + " — исполнителя нет"}],
    }


def _renamed_row(executor) -> dict:
    """Строка исполнителя образца БЕЗ имён параметров.

    Параметров у синтетической записи нет, и строка не вправе обещать
    чтение чужих имён: гейт привязок сказал бы «строка называет
    параметр, которого запись не называет». Символ, файл и форма
    остаются образца — покрытие у клона настоящее."""
    out = copy.deepcopy(executor) if isinstance(executor, dict) else {}
    if isinstance(out.get("params"), list):
        out["params"] = []
    out.pop("param", None)
    return out


# ------------------------------------------------- заявка-свидетель
#: Формулировка, от которой считается номер синтетической заявки. Текст
#: постоянный: номер заявки обязан быть один и тот же на любой машине.
PROBE_STATEMENT = ("синтетическая заявка помощника проверки роста "
                   "каталога шаблонов")


def probe_request_id() -> str:
    """Номер синтетической заявки — законом читателя, а не литералом."""
    return request_id_of(request_key("families", "HARD", "ROSTERING",
                                     PROBE_STATEMENT))


def probe_request(section: str, record: dict, *, date: str | None = None,
                  request_id: str | None = None) -> dict:
    """Заявка-СВИДЕТЕЛЬ синтетической записи: принятая заявка в
    состоянии «исполнитель заявлен» с черновиком записи.

    Свидетель нужен по делу, а не для формы: единицу имени параметра,
    которого не знают ни словарь CDPM, ни таблицы решателя,
    подтверждает только черновик принятой заявки (правила L05/L05b
    закона паритета). Без неё копия каталога в режиме роста «не
    собрана» по построению."""
    date = date or _today()
    rid = request_id or probe_request_id()
    return {
        "request_id": rid,
        "state": "исполнитель заявлен",
        "section": section,
        "record_id": str(record.get("id")),
        "submitted_at": f"{date}T00:00:00+00:00",
        "submitted_by": "COSMOS",
        "source": {"kind": "вручную", "document_class": None,
                   "run_class": None, "ref": None},
        "draft": draft_of(record),
        "executor_kinds_requested": None,
        "decision": {"by": "владелец", "at": f"{date}T00:00:00+00:00",
                     "words": "заявка помощника проверки роста каталога: "
                              "принята для проверки судей, к выпуску не "
                              "предлагается"},
        "cr_key": cr_key_of(rid),
    }


def probe_requests(mode_name: str, families_full=None, *,
                   date: str | None = None) -> list:
    """Заявки-свидетели синтетических записей режима роста.

    Свидетель нужен ровно тем записям, у которых есть ИМЕНА
    ПАРАМЕТРОВ: единицу имени подтверждает словарь CDPM, таблицы
    решателя либо черновик принятой заявки. У синтетического критерия
    параметров нет — лишней заявки в очередь копии не кладётся."""
    if mode_name == MODE_DRAFT:
        # У заготовки настоящей заявки свидетель СВОЙ — заявка, по
        # которой открыт CR; выдумывать второй помощник не вправе.
        return []
    fams, crits = probe_records(mode_name, families_full, date=date)
    out = []
    for section, recs in (("families", fams), ("criteria", crits)):
        for rec in recs.values():
            if rec.get("params"):
                out.append(probe_request(section, rec, date=date))
    return out


# --------------------------------------------- заготовка кандидата CR
#: Поля, которых у ЧЕРНОВИКА заявки быть не должно (правило R03
#: читателя: историю и состояние пишет сборка службой).
DRAFT_STRIPPED = ("status", "history", "stand_tasks")


def draft_of(record: dict) -> dict:
    """Черновик заявки по записи: запись без состояния, истории и задач
    стенда."""
    out = {k: copy.deepcopy(v) for k, v in (record or {}).items()
           if k not in DRAFT_STRIPPED}
    return out


def record_of_draft(draft: dict, *, record_id: str | None = None) -> dict:
    """Запись по черновику заявки: черновик ⊕ состояние «есть» ⊕ пустой
    перечень задач стенда. Историю кандидат не несёт — её пишет сборка.

    Ровно так выглядит запись ДО работы агента: параметры с единицами и
    объявленные виды исполнителей есть, исполнителей и задач стенда
    нет. Судьи предполётной проверки судят именно её."""
    if not isinstance(draft, dict) or not draft:
        raise GrowthError("черновик записи пуст — заготовку строить не из "
                          "чего")
    rec = {k: copy.deepcopy(v) for k, v in draft.items()
           if k not in ("status", "history")}
    if record_id:
        rec["id"] = str(record_id)
    rec["status"] = "есть"
    rec.setdefault("stand_tasks", [])
    if not isinstance(rec.get("stand_tasks"), list):
        rec["stand_tasks"] = []
    return rec


def draft_candidate(*, section: str, record_id: str, draft: dict,
                    request_id: str, cr_key: str, note: str | None = None,
                    by: str = "контур самоулучшения",
                    saved_at: str | None = None) -> dict:
    """Файл-кандидат ЗАГОТОВКИ записи: то, что предполётная проверка
    кладёт во временный каталог вместо работы агента.

    Форма — та же, что у кандидата конвейера (схема
    `template_catalog/schemas/candidate.schema.json`): предполётная
    проверка судит ровно тот файл, который позже напишет агент, и не
    заводит второй формы."""
    stamp = saved_at or (_dt.datetime.now(_dt.timezone.utc)
                         .replace(microsecond=0).isoformat())
    return {
        "form_version": 1,
        "section": str(section),
        "id": str(record_id),
        "record": record_of_draft(draft, record_id=record_id),
        "note": note or ("заготовка записи предполётной проверки: "
                         "исполнителей ещё нет"),
        "by": by,
        "saved_at": stamp,
        "request_id": str(request_id),
        "cr_key": str(cr_key),
    }


#: Слова ожидаемых пробелов заготовки — одни и те же у предполётной
#: проверки и у задания агенту.
GAP_NO_EXECUTOR = "исполнителя нет: вид {kind} объявлен записью, строки «есть» в привязках нет"
GAP_NO_STAND_TASKS = "задач стенда нет: запись «есть» без стендовых задач (предупреждение X07)"
GAP_NO_SUBJECT_WORDS = "слов-носителей предмета нет: лексический контракт запись не судит"


def expected_gaps(record: dict, section: str = "families") -> list:
    """Пробелы, которые у заготовки ОБЯЗАНЫ быть: их закрывает агент, и
    судья, назвавший их словами, работает верно.

    Всё, чего в этом перечне нет, — находка предполётной проверки:
    либо запись негодна, либо судья спотыкается о рост каталога."""
    rec = record or {}
    kinds_field = "executor_kinds" if section == "families" else "computable_by"
    out = [GAP_NO_EXECUTOR.format(kind=k)
           for k in (rec.get(kinds_field) or ())]
    if not rec.get("stand_tasks"):
        out.append(GAP_NO_STAND_TASKS)
    if section == "families" and not rec.get("subject_words"):
        out.append(GAP_NO_SUBJECT_WORDS)
    return out
