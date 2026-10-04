# -*- coding: utf-8 -*-
"""Канон и отпечаток каталога шаблонов (§9 схемы записи).

Тело канона — ровно три раздела: families, criteria, dictionaries;
form_version файлов в канон не входит. Нормализация: записи по id;
списки-множества записи (complements, executor_kinds, check_templates,
computable_by) сортируются; params — по name, param_sets — по id,
conditions — по name, stand_tasks — по id, rule_form.fields и
instance_form.fields — по name; в словарях сортируются все
списки-перечни, кроме lexicon_order. НЕ сортируются последовательности-
данные: subject_words, history, param_sets[].params, lexicon_order,
rule_form.fields[].values, semantics.rates_form.fields. Все строки — NFC.
Числа — только целые: дробное или экспоненциальное число — ValueError с
адресом. Сериализация — json без пробелов, ключи отсортированы,
ensure_ascii=False; отпечаток — sha256 (64 hex) байтов UTF-8.

Отсюда КАРТЫ (объекты) устойчивы к перестановке ключей сами по себе:
`reads_history` записи и `violation_units` словарей отпечатка не сдвинут,
в каком бы порядке ни легли их ключи в файле, — сортировать их отдельно
незачем.

Единственное вычисление отпечатка для всех сторон — fingerprint_of().
"""
from __future__ import annotations

import hashlib
import json
import unicodedata

FINGERPRINT_SECTIONS = ("families", "criteria", "dictionaries")

#: Списки-множества записи (сортируются каноном).
_RECORD_SET_LISTS = ("complements", "executor_kinds", "check_templates",
                     "computable_by")
#: Списки объектов записи с ключом сортировки.
_RECORD_KEYED_LISTS = (("params", "name"), ("param_sets", "id"),
                       ("conditions", "name"), ("stand_tasks", "id"))
#: Формы с полем fields, сортируемым по name.
_FORM_FIELDS = ("rule_form", "instance_form")
#: Ключи словарей, чьи списки — последовательности-данные.
_DICT_KEEP_ORDER = ("lexicon_order",)


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _scalar(v, where: str):
    if v is None or isinstance(v, bool) or isinstance(v, int):
        return v
    if isinstance(v, float):
        raise ValueError(f"{where}: число с дробной частью или экспонентой "
                         f"({v!r}) — в каталоге только целые")
    if isinstance(v, str):
        try:
            v.encode("utf-8")
        except UnicodeEncodeError as e:
            raise ValueError(f"{where}: строка не кодируется в UTF-8 "
                             f"({e.reason})") from None
        return _nfc(v)
    raise ValueError(f"{where}: недопустимый тип значения "
                     f"{type(v).__name__}")


def _deep(v, where: str):
    """NFC и проверка чисел рекурсивно; порядок сохраняется."""
    if isinstance(v, dict):
        return {_scalar(str(k), where + "/<ключ>"): _deep(x, f"{where}.{k}")
                for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_deep(x, f"{where}[{i}]") for i, x in enumerate(v)]
    return _scalar(v, where)


def _key(x) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True)


def _by(field: str):
    return lambda d: (str(d.get(field)) if isinstance(d, dict) else _key(d))


def _normalize_record(rec, where: str):
    rec = _deep(rec, where)
    if not isinstance(rec, dict):
        return rec
    out = dict(rec)
    for k in _RECORD_SET_LISTS:
        if isinstance(out.get(k), list):
            out[k] = sorted(out[k], key=_key)
    for k, field in _RECORD_KEYED_LISTS:
        if isinstance(out.get(k), list):
            out[k] = sorted(out[k], key=_by(field))
    for k in _FORM_FIELDS:
        form = out.get(k)
        if isinstance(form, dict) and isinstance(form.get("fields"), list):
            form = dict(form)
            form["fields"] = sorted(form["fields"], key=_by("name"))
            out[k] = form
    return out


def _normalize_dictionaries(dicts, where: str):
    dicts = _deep(dicts, where)

    def walk(v, path):
        if isinstance(v, dict):
            return {k: walk(x, path + (k,)) for k, x in v.items()}
        if isinstance(v, list):
            items = [walk(x, path + ("[]",)) for x in v]
            if path and path[0] in _DICT_KEEP_ORDER:
                return items
            return sorted(items, key=_key)
        return v

    return walk(dicts, ())


def normalize(body: dict) -> dict:
    """Нормализованное тело канона; дробное число → ValueError с адресом."""
    if not isinstance(body, dict):
        raise ValueError("тело канона — объект с разделами "
                         + ", ".join(FINGERPRINT_SECTIONS))
    extra = sorted(set(body) - set(FINGERPRINT_SECTIONS))
    if extra:
        raise ValueError(f"тело канона несёт лишние разделы: {extra}")
    out: dict = {}
    for sec in ("families", "criteria"):
        recs = body.get(sec)
        if not isinstance(recs, list):
            raise ValueError(f"{sec}: раздел обязан быть списком записей")
        normed = [_normalize_record(r, f"{sec}[{i}]")
                  for i, r in enumerate(recs)]
        out[sec] = sorted(normed, key=_by("id"))
    dicts = body.get("dictionaries")
    if not isinstance(dicts, dict):
        raise ValueError("dictionaries: раздел обязан быть объектом словарей")
    dicts = {k: v for k, v in dicts.items() if k != "form_version"}
    out["dictionaries"] = _normalize_dictionaries(dicts, "dictionaries")
    return out


def canonical_json(body: dict) -> str:
    return json.dumps(normalize(body), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def canonical_bytes(body: dict) -> bytes:
    return canonical_json(body).encode("utf-8")


def fingerprint_of(body: dict) -> str:
    """sha256 (64 hex) канона — единственное вычисление для всех сторон."""
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def record_fingerprint(record: dict) -> str:
    """Content identity of one record, using the same catalogue normalizer.

    This is a lookup/integrity identity, never a semantic verdict. Notes
    change it; externally referenced dictionary changes need not change it.
    """
    if not isinstance(record, dict):
        raise ValueError("запись каталога — объект")
    text = json.dumps(_normalize_record(record, "record"), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
