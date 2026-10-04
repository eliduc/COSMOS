# -*- coding: utf-8 -*-
"""Читатель каталога шаблонов (template_catalog).

Каталог шаблонов — данные (JSON по разделам) о семьях правил и видах
критериев: механика словами, параметры с единицами и отношениями,
словари, заявки, версия и отпечаток. Читатель зависит только от
стандартной библиотеки Python ≥ 3.11 (и, необязательно, от jsonschema
в validate()); импортов из cdpm, vmp, webapp, tools — ни одного. При
импорте файлы не читаются, переменные среды не трогаются.

Публичный интерфейс:
    load(source=None, *, strict=False) -> Catalog
    Catalog: version, is_draft, fingerprint, declared_fingerprint,
             families, families_meta, families_full, family, params,
             conditions, lexicon, subjects, criteria, criterion, dictionaries,
             check_templates, requests, request, versions, validate,
             ensure_valid
    families/family/params/lexicon/subjects/criteria/dictionaries/version/
    fingerprint/validate — обёртки над load(source) для одного вызова
    fingerprint_of, canonical_bytes, canonical_json, validate_path
    Problem, CatalogError, CatalogNotFound, CatalogFormatError,
    CatalogInvalid, UnknownRecord
    DEFAULT_CATALOG_DIR, FINGERPRINT_SECTIONS, SECTION_FILES, __version__
    подмодули: canon, export, manifest, bindings, validate, reader,
               writer, diff, requests (закон ключа заявки, шаг 6),
               candidates (файл-кандидат записи, шаг 6),
               growth (рост каталога: синтетическая запись режима роста
               и заготовка кандидата предполётной проверки),
               receipts (закон квитанции заявки receipt/1 — один на
               клиента COSMOS и клиента CDPM, волна 17б)
"""
from __future__ import annotations

from . import candidates, growth, receipts, requests
from .canon import canonical_bytes, canonical_json, fingerprint_of
from .errors import (CatalogError, CatalogFormatError, CatalogInvalid,
                     CatalogNotFound, UnknownRecord)
from .reader import (DEFAULT_CATALOG_DIR, FINGERPRINT_SECTIONS,
                     SECTION_FILES, Catalog, load, validate_path)
from .requests import (CR_KEY_RE, DOCUMENT_CLASSES, REQUEST_ID_RE, cr_key_of,
                       draft_canon, kind_request_key, manual_key, ref_of,
                       request_id_of, request_key)
from .validate import Problem

__version__ = "0.1.0"

__all__ = [
    "load", "Catalog", "Problem", "CatalogError", "CatalogNotFound",
    "CatalogFormatError", "CatalogInvalid", "UnknownRecord",
    "fingerprint_of", "canonical_bytes", "canonical_json", "validate_path",
    "DEFAULT_CATALOG_DIR", "FINGERPRINT_SECTIONS", "SECTION_FILES",
    "__version__", "families", "family", "params", "lexicon", "criteria",
    "dictionaries", "version", "fingerprint", "validate",
    "current", "forget_current", "subject_words", "subjects", "complements",
    "requests", "candidates", "growth", "receipts", "DOCUMENT_CLASSES",
    "REQUEST_ID_RE",
    "CR_KEY_RE", "request_key", "kind_request_key", "manual_key",
    "draft_canon", "request_id_of", "cr_key_of", "ref_of",
]


# Обёртки одного вызова: load(source) и делегирование. Файлы читаются
# только при вызове, не при импорте.
def families(source=None, status=("есть",)) -> dict:
    return load(source).families(status)


def family(id: str, source=None) -> dict:
    return load(source).family(id)


def params(source=None, status=("есть",)) -> dict:
    return load(source).params(status)


def lexicon(source=None) -> dict:
    return load(source).lexicon()


def subjects(family: str, source=None) -> dict[str, tuple[str, ...]]:
    """Предметы параметров выбранной семьи — данные выбранного каталога."""
    return load(source).subjects(family)


def criteria(source=None, status=("есть",)) -> dict:
    return load(source).criteria(status)


def dictionaries(source=None) -> dict:
    return load(source).dictionaries()


def version(source=None) -> str:
    return load(source).version()


def fingerprint(source=None) -> str:
    return load(source).fingerprint()


def validate(source=None, *, forbidden_terms=()) -> list:
    return load(source).validate(forbidden_terms=forbidden_terms)


# Один экземпляр на процесс с перечитыванием по изменению файлов
# (просьба CDPM к шагу 2, 07.09.2026: family_subject_words зовётся на
# каждую клаузу — без кэша это класс зависаний, который уже был у
# реестра). Ключ кэша — папка каталога; отметка — имена, размеры и
# mtime всех JSON папки: любая правка данных на диске даёт новый
# экземпляр при следующем обращении, без перезапуска процесса.
_CURRENT: dict = {}


def _stamp_of(folder) -> tuple:
    from pathlib import Path
    out = []
    for p in sorted(Path(folder).glob("*.json")):
        try:
            st = p.stat()
        except OSError:
            continue
        out.append((p.name, st.st_mtime_ns, st.st_size))
    return tuple(out)


def current(source=None, *, strict: bool = False,
            validated: bool = False) -> Catalog:
    """Каталог как load(source), но один экземпляр на процесс: повторный
    вызов отдаёт тот же объект, пока файлы папки каталога не менялись
    (имя, размер, mtime); после изменения — перечитывание. Единый файл
    выгрузки (не папка) кэшу не подлежит и читается каждый раз.

    `validated=True` (Ш2 шага 7) добавляет к этому вердикт годности:
    экземпляр проверяется ОДИН РАЗ (`Catalog.ensure_valid()`), негодный
    при каждом обращении отказывает теми же словами. Признак НЕ входит
    в ключ кэша намеренно: дверь COSMOS и мост CDPM обязаны держать
    один экземпляр на процесс, иначе они разойдутся в том, какой
    каталог сейчас действует."""
    from pathlib import Path
    src_key = ("source", str(source), strict)
    folder = _CURRENT.get(src_key)
    if folder is None:
        cat = load(source, strict=strict)
        folder = getattr(cat, "path", None)
        if folder is None or not Path(folder).is_dir():
            if validated:
                cat.ensure_valid()
            return cat  # единый файл выгрузки — без кэша
        _CURRENT[src_key] = folder
        _CURRENT[(str(folder), strict)] = (_stamp_of(folder), cat)
        if validated:
            cat.ensure_valid()
        return cat
    key = (str(folder), strict)
    stamp = _stamp_of(folder)
    hit = _CURRENT.get(key)
    if hit is not None and hit[0] == stamp:
        if validated:
            hit[1].ensure_valid()
        return hit[1]
    cat = load(folder, strict=strict)
    _CURRENT[key] = (stamp, cat)
    if validated:
        cat.ensure_valid()
    return cat


def forget_current() -> None:
    """Сброс кэша current() (тесты, смена каталога в процессе)."""
    _CURRENT.clear()


def subject_words(family: str, source=None) -> tuple:
    """Слова-носители предмета семьи кортежем; у семьи без слов
    (null в записи) и у неизвестной семьи — пустой кортеж: лексический
    контракт семью не судит (согласовано с CDPM 07.09.2026)."""
    words = current(source).lexicon().get("subject_words") or {}
    return tuple(words.get(str(family)) or ())


def complements(source=None) -> dict:
    """Дополняющие семьи: {семья: кортеж семей} по записям каталога."""
    comps = current(source).lexicon().get("complements") or {}
    return {str(k): tuple(v or ()) for k, v in comps.items()}
