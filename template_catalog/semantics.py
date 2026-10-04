# -*- coding: utf-8 -*-
"""Каноническое семантическое чтение несомой ячейки (выпуск 0.6.0).

ЗАЧЕМ. Совместимость посадки со сменой каталога прежде судили ПЕРЕЧНЕМ
полей записи параметра. Перечень оказался и неполон, и не на том уровне:

* ячейкой посадки законно бывает не только ПАРАМЕТР, но и УСЛОВИЕ записи
  (`cdpm/model.py`, `validate_template_fit`: ключ ячейки — имя из
  `params` ЛИБО из `conditions`), а судья совместимости проецировал
  только `params[]` и смену единицы у условия не видел вовсе;
* смысл посаженного значения живёт НЕ в записи: ячейка хранит сырое
  значение и СЛОВО ответа, а число в каноне получается приведением
  `units.to_canonical(значение, слово, единица)` при каждом чтении, —
  значит правка карты `unit_words` меняет посаженное число при побайтно
  той же записи, том же отпечатке записи и зелёном валидаторе (проба
  «10 ч»: 600 → 10 от правки одного множителя).

Отсюда закон этого модуля: предмет суда — не запись и не её отпечаток, а
КАНОНИЧЕСКОЕ ЧТЕНИЕ ячейки, то есть поля записи под её именем ПЛЮС те
данные словарей, на которые эти поля ссылаются, взятые относительно
конкретного выпуска каталога. Разность отпечатков сама по себе слом
смысла НЕ доказывает: примечание, порядок ключей, неиспользуемая запись
словаря и эквивалентное расширение карты слов чтение не двигают.

ТРИ ИСХОДА, А НЕ ДВА. `readings_compare` отвечает `совместимо`,
`несовместимо` либо `совместимость не доказана`. Неизвестное — всегда
третий исход, никогда первый и никогда второй: неразрешённый выпуск,
неразрешённая словарная зависимость и СУЖЕНИЕ объявленного смысла
(исчезло слово карты, значение уточнителя, основа предмета) доказывают
только то, что доказать нечем. Четвёртое состояние контракта семьи —
`запись ужесточена` — этот модуль не выдаёт НИКОГДА и выдавать не
вправе: оно говорит о ПОКРЫТИИ минимальных полных наборов посаженными
значениями, то есть о полноте, а не о смысле ячейки (§12.15: «набор
говорит, СКОЛЬКО нужно, а не ЧТО означает»); считает его сторона,
которая держит посадку, ПОСЛЕ того как чтения сошлись.

СТОЛКНОВЕНИЕ ИМЁН. Несомые ячейки лежат в ОДНОМ словаре посадки
(`template_fit.params`) — и параметры, и условия. Поэтому имя,
объявленное в записи И параметром, И условием, — противоречие самой
записи: какую из двух ячеек несёт посадка, выпуск не объявляет. Чтение
такой ячейки возвращается БЕЗ ВИДА и БЕЗ ПОЛЕЙ, с названной причиной, а
сравнение даёт `совместимость не доказана` — не «совместимо» и не
«несовместимо». Ни одна сторона не выбирается молча: прежде поиск
ячейки возвращал ПЕРВОЕ совпадение, то есть тихо побеждал параметр.
Валидатор такую запись ловит правилом X02, но чтение считается по ТЕЛУ
ВЫПУСКА и валидатором не пользуется намеренно (выпущенное тело обязано
оставаться читаемым даже тогда, когда сегодняшняя схема его уже судит),
поэтому закон о столкновении живёт здесь, а не в валидаторе.

ЧТО В ЧТЕНИЕ НЕ ВХОДИТ (намеренно): примечания (`note`) — проза;
`param_sets` — полнота, а не смысл; словарные записи, на которые ячейка
не ссылается; порядок ключей и порядок слов. Поэтому правка заметки,
перестановка ключей, правка чужой единицы в карте и добавление синонима
с тем же множителем перепосадки не требуют.

Носимая ссылка на набор (`family_contract.param_set`) предметом этого
модуля НЕ является: идентификатор набора — не ячейка, и чтением ячеек
его сохранность не доказывается (нужен отдельный закон: однозначное
разрешение относительно закреплённого выпуска, сохранение исходной
идентичности и проверяемый переход к текущему набору).

Публичный интерфейс:

    cell_reading(catalog, section, record_id, cell_name) -> dict
    record_reading(catalog, section, record_id) -> dict
    release_record_reading(catalog, section, record_id, *,
                           fingerprint=None, version=None) -> dict
    readings_compare(old, new, *, carried=None) -> dict
    resolve_release(catalog, *, fingerprint=None, version=None) -> Catalog
"""
from __future__ import annotations

import copy
import re
import unicodedata
from pathlib import Path

from . import units
from .errors import CatalogError, UnknownRecord

#: Версии носителей чтения (общий закон 5 контракта П0: у каждого
#: носителя своя `schema_version`; неизвестная мажорная — отказ).
SCHEMA_CELL = "cell_reading/1"
SCHEMA_RECORD = "record_reading/1"

#: Вид ячейки. Перечень ЗАКРЫТ: ключом ячейки посадки законно бывает имя
#: параметра либо имя условия записи, и третьего носителя имён у семьи
#: нет.
CELL_PARAM = "параметр"
CELL_CONDITION = "условие"
CELL_KINDS = (CELL_PARAM, CELL_CONDITION)

#: Состояния контракта семьи (§12.15) — ЗАКРЫТЫЙ перечень имён в одном
#: месте, чтобы стороны сверяли их кодом, а не пересказом. Сравнение
#: чтений выдаёт только три первых; `HARDENED` назван здесь ради полноты
#: перечня и считается вне этого модуля (см. заголовок).
COMPATIBLE = "совместимо"
INCOMPATIBLE = "несовместимо"
UNPROVEN = "совместимость не доказана"
HARDENED = "запись ужесточена"
CONTRACT_STATES = (COMPATIBLE, HARDENED, INCOMPATIBLE, UNPROVEN)

#: Старшинство исходов: доказанная перепосадка старше недоказанности,
#: недоказанность старше совместимости (§12.15, «старшинство при
#: совпадении»).
_RANK = {COMPATIBLE: 0, UNPROVEN: 1, INCOMPATIBLE: 2}

#: Раздел каталога, у записей которого бывают несомые ячейки. Критерии
#: ячеек посадки не несут: посадка садится на СЕМЬЮ.
SECTION_FAMILIES = "families"

#: Словарь ЗНАЧЕНИЙ уточнителя: у `area` значения перечисляет каталог, у
#: `base_code` и `crew_variant` — данные авиакомпании, и каталог их не
#: перечисляет (правило X09 спрашивает непустой словарь только у `area`).
_QUALIFIER_VALUES = {"area": "area_qualifiers"}


class SemanticsError(CatalogError):
    """Отказ чтения словами: не тот раздел, неизвестная ячейка, чтения
    о разных записях. Неизвестное — отказ, а не догадка."""


# ------------------------------------------------------------- помощники
def _catalog(source):
    """Каталог по источнику: уже прочитанный `Catalog` — как есть, всё
    прочее — через `load()`. Своей папки по умолчанию модуль не знает
    намеренно: выпуск ищется рядом с ТЕМ каталогом, который подан."""
    from .reader import Catalog, load                        # noqa: PLC0415
    if isinstance(source, Catalog):
        return source
    return load(source)


def _nfc(s) -> str:
    return unicodedata.normalize("NFC", str(s))


def _stems(words):
    """Основы предмета множеством-перечнем: NFC, строчные, без пустых,
    без повторов, отсортированы. Поля нет (`None`) — `None`: «редакция
    поля не знает» и «основ нет» — разные правды.

    Порядок в чтение не входит: X06 требует строчные основы без
    повторов, то есть МНОЖЕСТВО; перестановка слов применимость клаузы
    не меняет и перепосадки требовать не вправе."""
    if words is None:
        return None
    if not isinstance(words, (list, tuple)):
        return []
    out = {_nfc(w).strip().lower() for w in words
           if isinstance(w, str) and w.strip()}
    return sorted(out)


def _unit_block(dicts: dict, token, where: str, unresolved: list) -> dict:
    """Словарные данные ЕДИНИЦЫ ячейки: членство кода в словаре единиц,
    слово канона, форма значения и нормализованная карта «слово записи →
    множитель».

    Берётся ТОЛЬКО запись своей единицы: чужие записи карты смысла этой
    ячейки не задают, и их правка перепосадки требовать не вправе.

    Карта `unit_words` появилась выпуском 0.6.0. У прежних выпусков её
    нет — это НЕРАЗРЕШЁННАЯ ЗАВИСИМОСТЬ, а не «слов нет»: до 0.6.0 слова
    и множители держал рукописный словарь продукта, и каталог не вправе
    утверждать, что нынешняя карта означает ровно то же. Такое чтение
    даёт `совместимость не доказана`, но не `совместимо`."""
    block = {"token": token, "declared": None, "resolved": True,
             "canon": None, "form": None, "words": None}
    if token is None:
        return block
    codes = set(dicts.get("param_units") or ()) \
        | set(dicts.get("condition_units") or ())
    block["declared"] = str(token) in codes
    rich = dicts.get(units.DICT_KEY)
    if not isinstance(rich, dict) or not rich:
        block["resolved"] = False
        unresolved.append({
            "where": f"dictionaries.{units.DICT_KEY}",
            "why": f"{where}: карты слов единиц в этом выпуске каталога "
                   f"нет — что означает значение в единице {token!r}, "
                   f"выпуск не объявляет"})
        return block
    entry = rich.get(str(token))
    if not isinstance(entry, dict):
        block["resolved"] = False
        unresolved.append({
            "where": f"dictionaries.{units.DICT_KEY}.{token}",
            "why": f"{where}: у единицы {token!r} записи в карте слов нет "
                   f"— канон, форма и множители не объявлены"})
        return block
    block["canon"] = entry.get("canon")
    block["form"] = entry.get("form")
    try:
        block["words"] = dict(units.scale_map(entry, str(token)))
    except CatalogError as e:
        block["resolved"] = False
        unresolved.append({
            "where": f"dictionaries.{units.DICT_KEY}.{token}.scale",
            "why": f"{where}: карта слов единицы неоднозначна — {e}"})
    return block


def _relation_block(dicts: dict, token, key: str) -> dict:
    """Словарные данные ОТНОШЕНИЯ: членство кода в закрытом перечне.

    Содержимое перечня в чтение не входит: перечень называет ЗАКОННЫЕ
    коды, а что значит `min`, задаёт сам код. Появление в перечне нового
    отношения смысла посаженной ячейки не меняет."""
    return {"token": token,
            "declared": None if token is None
            else str(token) in set(dicts.get(key) or ())}


def _qualifier_block(dicts: dict, token) -> dict:
    """Словарные данные УТОЧНИТЕЛЯ: членство вида и ЗНАЧЕНИЯ, которые
    каталог перечисляет.

    Значения входят в чтение потому, что ячейка с уточнителем читается
    СТРУКТУРНО: её клетки названы значениями уточнителя, и исчезнувшее
    значение — ссылка в никуда. У видов, чьи значения перечисляет не
    каталог, а данные авиакомпании (`base_code`, `crew_variant`),
    значений нет — это объявленный факт, а не пробел."""
    block = {"token": token, "declared": None, "values": None}
    if token is None:
        return block
    block["declared"] = str(token) in set(dicts.get("qualifier_kinds") or ())
    key = _QUALIFIER_VALUES.get(str(token))
    if key:
        vals = dicts.get(key)
        block["values"] = sorted({_nfc(v) for v in vals
                                  if isinstance(v, str)}) \
            if isinstance(vals, (list, tuple)) else []
    return block


def _declarations(record: dict) -> dict:
    """{имя: [(вид, поля), …]} по записи семьи — ОДИН перебор на обе
    надобности: и поиск ячейки по имени, и перечень имён записи.

    Перебираются ОБА перечня: имя ячейки посадки законно приходит и из
    `params`, и из `conditions`. Имя с ДВУМЯ объявлениями — столкновение
    имён, и видно оно обязано быть обеим надобностям сразу: прежде поиск
    возвращал первое совпадение (то есть молча побеждал параметр), а
    перечень имён столкновение так же молча схлопывал."""
    out: dict = {}
    for kind, key in ((CELL_PARAM, "params"), (CELL_CONDITION, "conditions")):
        for item in record.get(key) or ():
            if isinstance(item, dict) and item.get("name") is not None:
                out.setdefault(str(item["name"]), []).append((kind, item))
    return out


def _cell_names(record: dict) -> list:
    return sorted(_declarations(record))


def _record_of(cat, section: str, record_id: str) -> dict:
    if str(section) != SECTION_FAMILIES:
        raise SemanticsError(
            f"несомых ячеек у раздела {section!r} не бывает: ключ ячейки "
            f"посадки — имя параметра либо условия ЗАПИСИ СЕМЬИ "
            f"(раздел {SECTION_FAMILIES!r}); критерии ячеек не несут")
    return cat.family(str(record_id))


def _head(cat) -> dict:
    return {"version": cat.version(), "fingerprint": cat.fingerprint()}


# --------------------------------------------------------------- чтение
def cell_reading(catalog, section, record_id, cell_name) -> dict:
    """КАНОНИЧЕСКОЕ ЧТЕНИЕ одной несомой ячейки — всё, чем каталог
    заданного выпуска задаёт СМЫСЛ посаженного в неё значения.

    `catalog` — каталог выпуска (объект `Catalog` либо любой источник
    `load()`); `section` — раздел записи (`families`; у прочих разделов
    несомых ячеек не бывает — отказ словами); `record_id` — запись;
    `cell_name` — имя ячейки: параметра ЛИБО условия записи.

    Возврат (`cell_reading/1`):

    * `kind` — вид ячейки (`параметр` | `условие`): имя, переехавшее из
      условий в параметры, меняет смысл ячейки, даже если единица та же.
      `None` — имя объявлено ОБОИМИ перечнями записи (столкновение
      имён): вида у такой ячейки нет, поля пусты, причина названа в
      `unresolved`;
    * `fields` — поля записи под этим именем, задающие смысл: `unit`,
      `relation`, `qualifier`; заметка в чтение НЕ входит: это проза. У
      УСЛОВИЯ полей смысла ДВА, а не три: по схеме запись условия несёт
      только `name`, `note`, `relation`, `unit` — поля уточнителя у неё
      нет вовсе, поэтому `qualifier` и `subjects.cell` у условия всегда
      `None`, и это объявленный факт схемы, а не «не объявлено»;
    * `dictionaries` — транзитивно используемые ДАННЫЕ словарей: у
      единицы — членство кода, слово канона, форма значения и
      нормализованная карта «слово записи → множитель»; у отношения —
      членство кода; у уточнителя — членство вида и перечисленные
      каталогом значения. Записи словарей, на которые ячейка не
      ссылается, в чтение не входят: их правка перепосадки не требует;
    * `subjects` — основы предмета записи и самого параметра
      (`subject_words`, 0.6.0): ими решается, ВПРАВЕ ли клауза сесть на
      это имя; поля нет в редакции — `None`, а не пустой перечень;
    * `unresolved` — неразрешённые зависимости словами. Непустой
      перечень означает, что выпуск смысла ячейки не объявляет целиком;
      сравнение такого чтения даёт `совместимость не доказана` и
      никогда `совместимо`.

    Неизвестная запись — `UnknownRecord` читателя, неизвестное имя
    ячейки — `SemanticsError` словами: молчаливое пустое чтение
    превращалось бы ниже по течению в «совместимо» без предмета.
    Столкновение имён — НЕ отказ, а чтение с причиной: отказ прятал бы
    противоречие от стороны, которая держит посадку, а ей нужен третий
    исход по этой ячейке, а не исключение посреди обхода записи."""
    cat = _catalog(catalog)
    record = _record_of(cat, section, record_id)
    name = str(cell_name)
    decls = _declarations(record).get(name) or []
    if not decls:
        known = ", ".join(_cell_names(record)) or "—"
        raise SemanticsError(
            f"в записи {section}/{record_id} каталога {cat.version()} нет "
            f"ячейки {name!r}: имя ячейки посадки — параметр либо условие "
            f"записи; есть: {known}")
    dicts = cat.dictionaries()
    unresolved: list = []
    where = f"{section}/{record_id}.{name}"
    if len(decls) > 1:
        # СТОЛКНОВЕНИЕ ИМЁН. Ни одна сторона не выбирается: чтение
        # остаётся без вида и без полей, причина названа словами.
        kind, cell = None, {}
        unresolved.append({
            "where": where,
            "kind": "столкновение имён",
            "why": f"{where}: имя объявлено и параметром, и условием "
                   f"(объявлений — {len(decls)}) — противоречие самой "
                   f"записи; какую из двух ячеек несёт посадка, выпуск "
                   f"не объявляет, и выбрать одну из сторон молча "
                   f"нельзя"})
    else:
        kind, cell = decls[0]
    unit = cell.get("unit")
    qualifier = cell.get("qualifier") if kind == CELL_PARAM else None
    relation_key = "param_relations" if kind == CELL_PARAM \
        else "condition_relations"
    reading = {
        "schema_version": SCHEMA_CELL,
        "section": str(section),
        "record": str(record_id),
        "name": name,
        "kind": kind,
        "catalog": _head(cat),
        "fields": {"unit": unit, "relation": cell.get("relation"),
                   "qualifier": qualifier},
        "dictionaries": {
            "unit": _unit_block(dicts, unit, where, unresolved),
            "relation": _relation_block(dicts, cell.get("relation"),
                                        relation_key),
            "qualifier": _qualifier_block(dicts, qualifier)},
        "subjects": {"record": _stems(record.get("subject_words")),
                     "cell": _stems(cell.get("subject_words"))
                     if kind == CELL_PARAM else None},
        "unresolved": unresolved,
    }
    return reading


def record_reading(catalog, section, record_id) -> dict:
    """Чтение ВСЕХ несомых ячеек записи — параметров И условий.

    Возврат (`record_reading/1`): `cells` — {имя: чтение ячейки},
    `subjects.record` — основы предмета записи (общие для всех ячеек),
    `unresolved` — неразрешённые зависимости уровня записи (у чтения
    выпуска, который не разрешился, перечень непуст, а `cells` пуст).

    Имя, объявленное записью дважды — и параметром, и условием, — стоит
    в `cells` ОДИН раз и несёт чтение со столкновением имён: причина у
    него своя, а до прочих ячеек записи она не дотягивается, потому что
    противоречие у этого имени, а не у записи целиком."""
    cat = _catalog(catalog)
    record = _record_of(cat, section, record_id)
    cells = {name: cell_reading(cat, section, record_id, name)
             for name in _cell_names(record)}
    return {"schema_version": SCHEMA_RECORD,
            "section": str(section), "record": str(record_id),
            "catalog": _head(cat),
            "subjects": {"record": _stems(record.get("subject_words"))},
            "cells": cells, "unresolved": []}


def unresolved_record_reading(section, record_id, why, *, where="catalog",
                              catalog=None) -> dict:
    """Чтение, которого НЕТ: выпуск не разрешился, записи в выпуске не
    нашлось. Форма та же, `cells` пуст, причина названа словами —
    сравнение с таким чтением даёт `совместимость не доказана`.

    Отдельный носитель, а не исключение, намеренно: fail-closed обязан
    быть СТРУКТУРНЫМ. Забыть перехватить исключение легко, и цена ошибки
    — ложное «совместимо»; не заметить пустое чтение с названной
    причиной нельзя."""
    return {"schema_version": SCHEMA_RECORD,
            "section": str(section), "record": str(record_id),
            "catalog": dict(catalog or {"version": None, "fingerprint": None}),
            "subjects": {"record": None},
            "cells": {},
            "unresolved": [{"where": str(where), "why": str(why)}]}


# --------------------------------------------------- разрешение выпуска
def resolve_release(catalog, *, fingerprint=None, version=None):
    """Каталог ВЫПУСКА, относительно которого считается чтение, —
    достоверно или никак.

    Выпуск ищется рядом с ПОДАННЫМ каталогом (`<папка>/releases/<версия>`),
    ярлык версии берётся из его же журнала `versions.json`, а тело
    выпуска ПЕРЕСЧИТЫВАЕТСЯ читателем: запись журнала — не свидетельство
    содержимого папки. Отпечаток действующего каталога разрешается в сам
    действующий каталог.

    Неизвестный отпечаток, отсутствующая папка выпуска, расхождение
    пересчитанного отпечатка с заявленным, каталог без папки (единый
    файл выгрузки) — `SemanticsError` словами. Догадок нет: «похожий»
    выпуск не выпуск."""
    from .reader import load                                 # noqa: PLC0415
    cat = _catalog(catalog)
    want_fp = str(fingerprint or "").strip()
    want_ver = str(version or "").strip()
    if want_ver and (want_ver in (".", "..") or any(c in want_ver for c in "/\\:")):
        raise SemanticsError("версия выпуска должна быть именем внутри releases, не путём")
    if not want_fp and not want_ver:
        raise SemanticsError(
            "выпуск не назван: нужен отпечаток каталога (`fingerprint`) "
            "либо номер версии (`version`) — чтение считается "
            "ОТНОСИТЕЛЬНО выпуска, а не вообще")
    # Version metadata is outside the content fingerprint. Equal content
    # must not silently substitute a different explicitly pinned release.
    if want_fp and want_fp == cat.fingerprint() \
            and (not want_ver or want_ver == str(cat.version())):
        return cat
    if not want_ver:
        for row in cat.versions() or ():
            if isinstance(row, dict) \
                    and str(row.get("fingerprint") or "") == want_fp:
                want_ver = str(row.get("version") or "")
                break
        if not want_ver:
            known = ", ".join(str(r.get("version")) for r in
                              (cat.versions() or ()) if isinstance(r, dict))
            raise SemanticsError(
                f"отпечаток {want_fp[:12]}… не назван журналом выпусков "
                f"каталога {cat.version()}: выпуски журнала — "
                f"{known or '—'}; чужую раскладку выпусков читатель не "
                f"ищет")
    base = getattr(cat, "path", None)
    if base is None or not Path(base).is_dir():
        raise SemanticsError(
            f"у каталога {cat.version()} нет папки выпусков: источник — "
            f"единый файл выгрузки {base}, и тела выпуска {want_ver!r} "
            f"взять неоткуда")
    folder = Path(base) / "releases" / want_ver
    if not folder.is_dir():
        raise SemanticsError(
            f"тела выпуска {want_ver!r} нет: папки {folder} не существует "
            f"— выпуск не разрешён, и совместимость с ним не доказуема")
    try:
        rel = load(folder)
    except CatalogError as e:
        raise SemanticsError(
            f"выпуск {want_ver!r} не читается: {e}") from None
    got = rel.fingerprint()
    if want_fp and got != want_fp:
        raise SemanticsError(
            f"тело выпуска {want_ver!r} не то: пересчитанный отпечаток "
            f"{got[:12]}… не равен заявленному {want_fp[:12]}… — журнал "
            f"версий свидетельством содержимого папки не является")
    if rel.version() != want_ver:
        raise SemanticsError(
            f"в папке {folder} лежит каталог версии {rel.version()!r}, а "
            f"не {want_ver!r}")
    return rel


def release_record_reading(catalog, section, record_id, *,
                           fingerprint=None, version=None) -> dict:
    """Чтение записи ОТНОСИТЕЛЬНО выпуска — и никогда исключение.

    Выпуск не разрешился либо записи в нём нет — чтение с названной
    причиной (`unresolved_record_reading`), то есть
    `совместимость не доказана`. Это точка входа стороны, которая держит
    посадку: она знает отпечаток каталога посадки и обязана получить
    честный третий исход, а не молчаливое «совместимо»."""
    try:
        rel = resolve_release(catalog, fingerprint=fingerprint,
                              version=version)
    except (SemanticsError, CatalogError) as e:
        return unresolved_record_reading(section, record_id, str(e),
                                         where="catalog/releases")
    try:
        return record_reading(rel, section, record_id)
    except UnknownRecord as e:
        return unresolved_record_reading(
            section, record_id, str(e), where=f"{section}/{record_id}",
            catalog=_head(rel))
    except SemanticsError as e:
        return unresolved_record_reading(section, record_id, str(e),
                                         catalog=_head(rel))


# ------------------------------------------------------------ сравнение
def _as_record(reading) -> dict:
    """Чтение ячейки — как чтение записи с одной ячейкой: закон
    сравнения один, а не два."""
    if not isinstance(reading, dict):
        raise SemanticsError(
            f"чтение — объект формы {SCHEMA_RECORD} либо {SCHEMA_CELL}, а "
            f"подано {type(reading).__name__}")
    form = str(reading.get("schema_version") or "")
    if form == SCHEMA_RECORD:
        return reading
    if form == SCHEMA_CELL:
        return {"schema_version": SCHEMA_RECORD,
                "section": reading.get("section"),
                "record": reading.get("record"),
                "catalog": reading.get("catalog") or {},
                "subjects": {"record": (reading.get("subjects")
                                        or {}).get("record")},
                "cells": {str(reading.get("name")): reading},
                "unresolved": list(reading.get("unresolved") or ())}
    raise SemanticsError(
        f"неизвестная форма чтения {form!r}: читатель судит заявленной "
        f"версией (общий закон 5), и догадываться о форме нельзя")


def _diff(cell, kind, where, was, now, verdict, words) -> dict:
    return {"cell": cell, "kind": kind, "where": where, "was": was,
            "now": now, "verdict": verdict, "words": words}


def _citation_unit_words(citations, words):
    """Conservative lexical evidence, using the catalogue's normalized words.

    Longest declared words win (a composite must not become its suffix).
    Missing citations or numeric fragments without an explicit known unit
    are not proof of either sameness or a changed multiplier. This does not
    reconstruct a quotation from today's canonical numeric value.
    """
    if not isinstance(citations, (list, tuple)) or not citations:
        return set(), False
    variants = sorted(words, key=lambda word: (-len(word), word))
    suffix = re.compile(r"\s*(" + "|".join(re.escape(w) for w in variants)
                        + r")(?![^\W\d])") if variants else None
    used, complete = set(), True
    for quote in citations:
        if not isinstance(quote, str) or not quote.strip():
            complete = False
            continue
        text = units._word(quote)
        # Do not lose the second component of compact "16ч30минут".
        # Unlabelled numeric fragments remain conservative unknowns.
        numbers = list(re.finditer(r"\d+(?:[.,]\d+)?", text))
        if not numbers:
            complete = False
        for number in numbers:
            match = suffix.match(text, number.end()) if suffix else None
            if match:
                used.add(match.group(1))
            else:
                complete = False
    return used, complete


def _compare_unit(cell, addr, old: dict, new: dict, out: list, citations=None) -> None:
    """Словарные данные единицы: смена канона, формы или множителя
    объявленного обеими редакциями слова — СМЕНА СМЫСЛА. При наличии
    цитат множители ограничены словами их записи, не всей единицей.
    Без цитат сравнивается контракт целиком. Исчезновение
    слова — СУЖЕНИЕ (посаженное значение могло быть записано именно им,
    а могло и не быть — доказать нечем); появление синонима смысла не
    двигает и перепосадки не требует.

    Неразрешённая сторона (выпуск карты слов не несёт, записи единицы в
    карте нет, карта неоднозначна) сравнению не подлежит ВОВСЕ: «не
    объявлено» и «объявлено иначе» — разные правды, и путать их значило
    бы объявлять доказанной перепосадкой сам факт появления словаря
    единиц выпуском 0.6.0. Причину называет `unresolved` чтения, исход —
    `совместимость не доказана`."""
    if not (old.get("resolved", True) and new.get("resolved", True)):
        return
    if old.get("canon") != new.get("canon"):
        out.append(_diff(cell, "канон единицы", f"{addr}.canon",
                         old.get("canon"), new.get("canon"), INCOMPATIBLE,
                         f"слово канона единицы {old.get('token')!r} "
                         f"сменилось: было {old.get('canon')!r}, стало "
                         f"{new.get('canon')!r} — посаженное число значит "
                         f"уже не то"))
    if old.get("form") != new.get("form"):
        out.append(_diff(cell, "форма значения", f"{addr}.form",
                         old.get("form"), new.get("form"), INCOMPATIBLE,
                         f"форма значения единицы {old.get('token')!r} "
                         f"сменилась: было {old.get('form')!r}, стало "
                         f"{new.get('form')!r}"))
    ow, nw = old.get("words"), new.get("words")
    if not isinstance(ow, dict) or not isinstance(nw, dict):
        return
    selected = set(ow) | set(nw)
    previous_words_changed = (bool(set(ow) - set(nw))
                              or any(ow[w] != nw[w] for w in set(ow) & set(nw)))
    # New synonyms alone cannot change an old value; they require no new
    # citation evidence, even when the original quotation was not retained.
    if citations is not None and previous_words_changed:
        selected, complete = _citation_unit_words(citations, selected)
        if not complete:
            out.append(_diff(cell, "слово цитаты не доказано", f"{addr}.scale",
                             None, None, UNPROVEN,
                             f"карта слов единицы {old.get('token')!r} изменилась, "
                             "но слово записи не установлено по каждой цитате "
                             "несомой ячейки — смена её смысла не доказана"))
    for word in sorted(set(ow) & set(nw) & selected):
        if ow[word] != nw[word]:
            out.append(_diff(cell, "множитель слова",
                             f"{addr}.scale.{word}", ow[word], nw[word],
                             INCOMPATIBLE,
                             f"множитель слова {word!r} у единицы "
                             f"{old.get('token')!r} сменился: было "
                             f"{ow[word]!r}, стало {nw[word]!r} — то же "
                             f"значение читается другим числом"))
    gone = sorted((set(ow) - set(nw)) & selected)
    if gone:
        out.append(_diff(cell, "слово единицы убрано", f"{addr}.scale",
                         gone, None, UNPROVEN,
                         f"слова {', '.join(repr(w) for w in gone)} у "
                         f"единицы {old.get('token')!r} действующий выпуск "
                         f"не объявляет: значение, записанное таким "
                         f"словом, прочесть нечем"))


def _compare_stems(cell, addr, old, new, out, what) -> None:
    """Основы предмета: ими решается, ВПРАВЕ ли клауза сесть. Сужение
    (основа исчезла либо предмет объявлен там, где его не объявляли)
    применимость несомой клаузы под вопрос ставит, а доказать её по
    одному каталогу нечем — третий исход. Расширение применимости не
    отнимает."""
    if old is None and new is None:
        return
    if old is None:
        if new:
            out.append(_diff(cell, "слова предмета", addr, None, list(new),
                             UNPROVEN,
                             f"{what}: прежний выпуск предмета не объявлял, "
                             f"а действующий объявил ({', '.join(new)}) — "
                             f"применимость несомой клаузы этим сужена"))
        return
    if new is None:
        return
    gone = sorted(set(old) - set(new))
    if gone:
        out.append(_diff(cell, "слова предмета", addr, list(old), list(new),
                         UNPROVEN,
                         f"{what}: основы {', '.join(gone)} действующий "
                         f"выпуск не объявляет — клауза, севшая по ним, "
                         f"может быть уже неприменима"))


def _compare_cell(name, old: dict, new: dict, out: list, citations=None) -> None:
    """Одна ячейка: вид, поля записи, словарные данные, предмет.

    Неразрешённые зависимости ячейки называются ПЕРВЫМИ и своим исходом:
    выпуск, который смысла не объявляет, ни совместимости, ни
    несовместимости не доказывает. Причина вправе назвать свой вид
    расхождения (`kind`); не назвала — это словарная зависимость."""
    addr = f"{new.get('section')}/{new.get('record')}.{name}"
    for side, reading in (("прежнего выпуска", old), ("действующего", new)):
        for item in reading.get("unresolved") or ():
            вид = str(item.get("kind") or "зависимость не разрешена")
            out.append(_diff(name, вид, str(item.get("where")), None, None,
                             UNPROVEN,
                             f"чтение ячейки {name} {side} не разрешается: "
                             f"{item.get('why')}"))
    if old.get("kind") is None or new.get("kind") is None:
        # Чтение БЕЗ ВИДА (столкновение имён) — не предмет сравнения
        # полей: они пусты не оттого, что смысл сменился, а оттого, что
        # ячейки не получилось. Причина уже названа строкой выше и своим
        # исходом; вторым отказом об одном не отвечаем, а «несовместимо»
        # по пустым полям было бы прямой ложью.
        if not (old.get("unresolved") or new.get("unresolved")):
            out.append(_diff(name, "вид ячейки не определён", addr,
                             old.get("kind"), new.get("kind"), UNPROVEN,
                             f"чтение ячейки {name} вида не называет и "
                             f"причины не называет — сравнивать нечем"))
        return
    if old.get("kind") != new.get("kind"):
        out.append(_diff(name, "вид ячейки", addr, old.get("kind"),
                         new.get("kind"), INCOMPATIBLE,
                         f"имя {name!r} сменило вид: было "
                         f"{old.get('kind')}, стало {new.get('kind')}"))
    of, nf = old.get("fields") or {}, new.get("fields") or {}
    for key, word in (("unit", "единица"), ("relation", "отношение"),
                      ("qualifier", "уточнитель")):
        if of.get(key) != nf.get(key):
            out.append(_diff(name, word, f"{addr}.{key}", of.get(key),
                             nf.get(key), INCOMPATIBLE,
                             f"у ячейки {name} сменилось поле «{word}»: "
                             f"было {of.get(key)!r}, стало {nf.get(key)!r}"))
    od, nd = old.get("dictionaries") or {}, new.get("dictionaries") or {}
    if of.get("unit") == nf.get("unit"):
        _compare_unit(name, f"dictionaries.{units.DICT_KEY}."
                            f"{nf.get('unit')}",
                      od.get("unit") or {}, nd.get("unit") or {}, out, citations)
    if of.get("qualifier") == nf.get("qualifier"):
        ov = (od.get("qualifier") or {}).get("values")
        nv = (nd.get("qualifier") or {}).get("values")
        if isinstance(ov, list) and isinstance(nv, list):
            gone = sorted(set(ov) - set(nv))
            if gone:
                словарь = _QUALIFIER_VALUES.get(str(nf.get("qualifier")))
                out.append(_diff(
                    name, "значения уточнителя", f"dictionaries.{словарь}",
                    list(ov), list(nv), UNPROVEN,
                    f"значения уточнителя {nf.get('qualifier')!r} "
                    f"{', '.join(gone)} действующий выпуск не перечисляет: "
                    f"клетка нормы под таким значением — ссылка в никуда"))
    _compare_stems(name, f"{addr}.subject_words",
                   (old.get("subjects") or {}).get("cell"),
                   (new.get("subjects") or {}).get("cell"), out,
                   f"слова предмета параметра {name}")


def readings_compare(old, new, *, carried=None, citations=None) -> dict:
    """Сравнение КАНОНИЧЕСКИХ ЧТЕНИЙ прежнего и действующего выпусков.

    `old` — чтение выпуска, которым посажено; `new` — чтение
    действующего выпуска; оба — `record_reading/1` либо `cell_reading/1`.
    `carried` — имена ячеек, которые посадка ФИЗИЧЕСКИ несёт (ключи
    `template_fit.params` ДО фильтра по действующей записи). Подан
    перечень — судятся только эти имена: смысл ячейки, которую посадка
    не несёт, к этой посадке отношения не имеет. Не подан — судятся все
    общие ячейки (сравнение записей выпусков как таковых), а всякая
    исчезнувшая ячейка называется доказанной перепосадкой.

    `citations` — optional {имя: [цитаты всех несомых ячеек этой эпохи]}.
    Для посадки множители судятся по фактически записанным словам,
    не по посторонним синонимам единицы (CDPM-429). Пропуск/нечитаемая
    цитата при изменении карты даёт недоказанность, не выдуманную
    перепосадку. Без этого аргумента сравниваются контракты целиком,
    в том числе контракт ещё не отвеченного вопроса.

    Возврат: `{"verdict": исход, "differences": [...],
    "by_cell": {имя: исход}}`.

    Исход по ячейке:

    * `несовместимо` — объявленный каталогом смысл СМЕНИЛСЯ: другая
      единица, отношение, уточнитель, другой вид ячейки, другой канон
      или форма единицы, другой множитель у слова, объявленного обеими
      редакциями; несомая ячейка из действующей записи убрана (клауза
      (в) §12.15);
    * `совместимость не доказана` — смысл СУЗИЛСЯ или не разрешается:
      исчезло слово карты единиц, значение уточнителя или основа
      предмета; выпуск не разрешён; словарная зависимость не объявлена;
      имя объявлено в записи И параметром, И условием (столкновение
      имён); несомое имя неизвестно обеим редакциям;
    * `совместимо` — ни того, ни другого. Правка заметки, перестановка
      ключей, правка неиспользуемой записи словаря и добавление
      синонима с тем же множителем сюда и попадают.

    Исход целиком — старший по перечню (перепосадка старше
    недоказанности, недоказанность старше совместимости). Состояние
    `запись ужесточена` тут не выдаётся никогда: покрытие наборов —
    полнота, а не смысл, и считается вне этого модуля."""
    if citations is not None and not isinstance(citations, dict):
        raise SemanticsError("цитаты несомых ячеек — объект по именам")
    o, n = _as_record(old), _as_record(new)
    if str(o.get("section")) != str(n.get("section")) \
            or str(o.get("record")) != str(n.get("record")):
        raise SemanticsError(
            f"сравниваются чтения РАЗНЫХ записей: "
            f"{o.get('section')}/{o.get('record')} и "
            f"{n.get('section')}/{n.get('record')} — сравнивать нечего")
    out: list = []
    for side, reading in (("прежнего выпуска", o), ("действующего", n)):
        for item in reading.get("unresolved") or ():
            out.append(_diff(None, "чтение не разрешено",
                             str(item.get("where")), None, None, UNPROVEN,
                             f"чтение {side} не разрешается: "
                             f"{item.get('why')}"))
    # Чтение, которого нет вовсе (выпуск не разрешён), сравнивать не с
    # чем: пустота его полей — не «предмет не объявлен», а отсутствие
    # чтения, и называть её сужением было бы вторым отказом об одном.
    if not (o.get("unresolved") or n.get("unresolved")):
        _compare_stems(None, f"{n.get('section')}/{n.get('record')}"
                             f".subject_words",
                       (o.get("subjects") or {}).get("record"),
                       (n.get("subjects") or {}).get("record"), out,
                       f"слова предмета записи {n.get('record')}")
    oc = o.get("cells") or {}
    nc = n.get("cells") or {}
    if carried is None:
        names = sorted(set(oc) | set(nc))
    else:
        names = sorted({str(x) for x in carried})
    for name in names:
        ocell, ncell = oc.get(name), nc.get(name)
        if ocell is not None and ncell is not None:
            _compare_cell(name, ocell, ncell, out,
                          (citations.get(name) or [None]) if citations is not None else None)
            continue
        if ocell is not None and ncell is None:
            out.append(_diff(name, "ячейка убрана",
                             f"{n.get('section')}/{n.get('record')}.{name}",
                             ocell.get("kind"), None, INCOMPATIBLE,
                             f"имя {name!r}, которое несёт посадка, из "
                             f"действующей записи убрано"))
            continue
        if ocell is None and ncell is not None:
            # Имя появилось: посадка его не несёт — это полнота
            # (недостача либо ужесточение), а не смысл. Молчим.
            continue
        if carried is not None:
            out.append(_diff(name, "ячейка неизвестна",
                             f"{n.get('section')}/{n.get('record')}.{name}",
                             None, None, UNPROVEN,
                             f"имя {name!r}, которое несёт посадка, не "
                             f"объявлено ни прежним выпуском, ни "
                             f"действующим — судить нечем"))
    by_cell: dict = {}
    for name in names:
        if name in oc or name in nc or carried is not None:
            by_cell[name] = COMPATIBLE
    verdict = COMPATIBLE
    for d in out:
        if _RANK[d["verdict"]] > _RANK[verdict]:
            verdict = d["verdict"]
        cell = d.get("cell")
        if cell is None:
            continue
        if _RANK[d["verdict"]] > _RANK[by_cell.get(cell, COMPATIBLE)]:
            by_cell[cell] = d["verdict"]
    # Неразрешённое чтение записи не привязано к ячейке: оно накрывает
    # ВСЕ несомые имена — иначе `by_cell` показывал бы «совместимо» там,
    # где сравнивать было не с чем.
    if any(d["cell"] is None and d["verdict"] == UNPROVEN for d in out):
        for name in list(by_cell):
            if by_cell[name] == COMPATIBLE:
                by_cell[name] = UNPROVEN
    return {"verdict": verdict, "differences": copy.deepcopy(out),
            "by_cell": by_cell}
