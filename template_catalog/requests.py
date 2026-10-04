# -*- coding: utf-8 -*-
"""Закон ключа, номера заявки и ключа запроса на изменение (шаг 6).

ОДИН закон на два продукта и три машины. Номер заявки СОДЕРЖАТЕЛЬНЫЙ:
он считается от того, о чём заявка (раздел, вид, зона, нормализованная
формулировка — либо раздел, запись и набор видов исполнителя), а не
выдаётся счётчиком службы. Причины названы прямо, потому что выбор
неочевиден:

* служба каталога может быть не поднята (заявка рождается на площадке
  и лежит в очереди продукта) — счётчик там взять негде;
* один и тот же пробел, увиденный CDPM при посадке категории C и
  COSMOS при нажатии, обязан дать ОДИН номер без обмена между
  продуктами;
* повторная подача идемпотентна: тот же материал — тот же номер, и
  дубль ловится по номеру, а не по пересказу.

Слабое место названо честно: номер черновика новой записи служба
пересчитать не может (формулировки правила у неё нет) — она принимает
номер подателя по образцу. Номер заявки на вид исполнителя (у которой
черновика нет) служба пересчитывает и судит.

Совпадение номеров у разных ключей — 2⁻⁶⁴ (номер несёт первые 64 бита
ключа десятичными цифрами).

Модуль — часть читателя: только стандартная библиотека, при импорте ни
одного файла и ни одной переменной среды.

Свидетели закона (три, по разным сторонам):
    tests/test_template_catalog_requests_law.py   — читатель
    tests/test_cdpm_catalog_requests.py           — CDPM
    tests/vmp/test_catalog_requests.py            — продукт COSMOS
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata

#: Сколько знаков ключа уходит в ссылку `source.ref` («run-0090#<хвост>»).
#: Хвост непрозрачен: ключ считается от механики и записи, текста правила
#: авиакомпании он не несёт (A01/A05 проходят).
KEY_TAIL = 12

#: Класс документа-источника заявки — ОДИН перечень на формы CDPM,
#: COSMOS и службы. Пока словаря `document_classes` в каталоге нет,
#: перечень живёт здесь; после первой сборки службой значение судится
#: словарём (D01), а перечень остаётся умолчанием форм.
DOCUMENT_CLASSES = (
    "положение о рабочем времени и времени отдыха",
    "коллективный договор",
    "руководство по производству полётов",
    "правила планирования экипажей",
    "регламент службы",
    "документ свода правил авиакомпании",
    "обзор практики авиакомпаний",
)

#: Образец номера заявки: «req-» и ровно 20 десятичных цифр.
REQUEST_ID_RE = re.compile(r"^req-\d{20}$")
#: Образец ключа CR: «cr» и 8 знаков hex (как у очереди изменений COSMOS).
CR_KEY_RE = re.compile(r"^cr[0-9a-f]{8}$")
#: Образец ключа заявки: sha256 строчными hex.
KEY_RE = re.compile(r"^[0-9a-f]{64}$")

#: Незначащие разделители формулировки (закон CDPM
#: `cdpm/model.py:normalized_question_text` — материал ключа обязан быть
#: байт в байт тем же).
_SEPARATORS_RE = re.compile(r"[\s\.\,;:!\?—–\-«»\"']+")


def normalized_text(text: str) -> str:
    """Нормализация формулировки: NFC, регистр и незначащая пунктуация
    номер заявки не меняют. Дословно закон CDPM: расхождение хоть в
    одном символе развело бы номера двух продуктов."""
    text = unicodedata.normalize("NFC", str(text or "")).casefold()
    return _SEPARATORS_RE.sub(" ", text).strip()


def _sha(material: str) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def request_key(section: str, kind: str, zone: str, statement: str) -> str:
    """Ключ заявки на НОВУЮ запись: раздел + вид + зона + нормализованная
    формулировка. Имя записи, предложенное моделью, в ключ намеренно не
    входит: расхождение имён при одном пробеле — главный источник
    дублей."""
    material = "\n".join((str(section or ""), str(kind or ""),
                          str(zone or ""), normalized_text(statement)))
    return _sha(material)


def kind_request_key(section: str, record_id: str, kinds) -> str:
    """Ключ заявки на ВИД исполнителя у существующей записи: раздел,
    запись и НАБОР видов. Порядок видов значения не имеет, состав —
    имеет: заявка на другой набор видов — другая заявка."""
    names = ",".join(sorted({str(k) for k in (kinds or ())}))
    return _sha("kinds\n" + str(section or "") + "\n"
                + str(record_id or "") + "\n" + names)


def manual_key(section: str, record_id: str, draft_canon: str | None) -> str:
    """Ключ заявки, поданной ВРУЧНУЮ с экрана службы: раздел, запись и
    канон черновика (у заявки на вид черновика нет — тогда пусто)."""
    return _sha("manual\n" + str(section or "") + "\n"
                + str(record_id or "") + "\n" + (draft_canon or ""))


def draft_canon(draft) -> str | None:
    """Канон черновика заявки: NFC всех строк, компактный JSON с
    сортировкой ключей, sha256. Черновика нет — None.

    Своя форма канона, а не `template_catalog.canon`: тот принимает
    только тело каталога из трёх разделов, а здесь судится ОДНА запись
    (закон Г5 CDPM, `cdpm/catalog_requests.py:draft_canon`)."""
    if draft is None:
        return None

    def _norm(v):
        if isinstance(v, str):
            return unicodedata.normalize("NFC", v)
        if isinstance(v, dict):
            return {_norm(k): _norm(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_norm(x) for x in v]
        return v

    text = json.dumps(_norm(draft), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return _sha(text)


def request_id_of(key: str) -> str:
    """Номер заявки: 20 десятичных цифр от первых 64 бит ключа."""
    k = str(key or "")
    if not KEY_RE.match(k):
        raise ValueError(f"ключ заявки не по образцу sha256: {key!r}")
    return "req-" + str(int(k[:16], 16)).zfill(20)


def cr_key_of(request_id: str) -> str:
    """Ключ строки очереди изменений COSMOS по номеру заявки: строка CR
    — проекция заявки, а не случайное имя. Один и тот же CR открывается
    на рабочем месте и на Тесте без переноса очереди."""
    rid = str(request_id or "")
    if not REQUEST_ID_RE.match(rid):
        raise ValueError(f"номер заявки не по образцу req-<20 цифр>: "
                         f"{request_id!r}")
    return "cr" + _sha("catalog-request:" + rid)[:8]


def ref_of(run_name: str, key: str) -> str:
    """Ссылка источника заявки: «<имя прогона>#<12 знаков ключа>»."""
    return f"{run_name}#{str(key or '')[:KEY_TAIL]}"


def key_tail_of(ref) -> str | None:
    """Хвост ключа из ссылки источника; ссылки без «#» — None."""
    if not isinstance(ref, str) or "#" not in ref:
        return None
    tail = ref.split("#", 1)[1].strip()
    return tail or None
