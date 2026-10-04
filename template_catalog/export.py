# -*- coding: utf-8 -*-
"""Выгрузка каталога одним файлом и обратная раскладка по разделам
(§9 п.6): {"catalog": шапка, "data": тело канона, "requests": [...],
"versions": [...]} в канонической сериализации; отпечаток data равен
отпечатку каталога.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from . import canon
from .errors import CatalogFormatError
from .reader import parse_json_text

#: Форма файлов разделов на диске: отступ 1, ключи отсортированы,
#: ensure_ascii=False, перевод строки LF в конце, UTF-8 без BOM.
def dump_json(doc) -> str:
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=1,
                      allow_nan=False) + "\n"


def export_single(catalog) -> str:
    doc = {"catalog": copy.deepcopy(catalog.header),
           "data": canon.normalize(catalog.body()),
           "requests": catalog.requests(),
           "versions": catalog.versions()}
    return json.dumps(doc, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def import_single(text: str, out_dir) -> Path:
    """Раскладывает единый файл по файлам разделов в out_dir."""
    doc = parse_json_text(text, "единый файл выгрузки")
    if not isinstance(doc, dict) or not isinstance(doc.get("catalog"), dict) \
            or not isinstance(doc.get("data"), dict):
        raise CatalogFormatError(
            "единый файл выгрузки: нужны объекты catalog и data")
    data = doc["data"]
    for sec in ("families", "criteria"):
        if not isinstance(data.get(sec), list):
            raise CatalogFormatError(
                f"единый файл выгрузки: в data нет списка {sec!r}")
    if not isinstance(data.get("dictionaries"), dict):
        raise CatalogFormatError(
            "единый файл выгрузки: в data нет объекта dictionaries")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    header = copy.deepcopy(doc["catalog"])
    reqs = doc.get("requests")
    vers = doc.get("versions")
    files = {
        "catalog.json": header,
        "families.json": {"form_version": 1, "families": data["families"]},
        "criteria.json": {"form_version": 1, "criteria": data["criteria"]},
        "dictionaries.json": {"form_version": 1, **data["dictionaries"]},
        "requests.json": {"form_version": 1,
                          "requests": reqs if isinstance(reqs, list) else []},
        "versions.json": {"form_version": 1,
                          "versions": vers if isinstance(vers, list) else []},
    }
    # Байтами, не текстом: write_text на Windows переводил бы «\n» в
    # «\r\n», и форма файлов зависела бы от ОС (§9 п.6 — LF везде).
    for name, body in files.items():
        (out / name).write_bytes(dump_json(body).encode("utf-8"))
    return out
