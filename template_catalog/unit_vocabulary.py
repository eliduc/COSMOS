"""Attach explicit unit data to a modern draft, without I/O or publication.

Only dictionaries.unit_words changes. The ordinary X21 rule validates this
map; runtime readers still use the selected catalogue, never this draft file.
"""
from __future__ import annotations

import copy

from .validate import _Ctx, _unit_words_check, _uses_schema_070


class UnitVocabularyError(ValueError):
    """The draft does not cover the selected catalogue's exact unit contract."""


def attach(documents: dict, vocabulary: dict) -> dict:
    version = (documents.get("catalog.json") or {}).get("version")
    if not _uses_schema_070(version):
        raise UnitVocabularyError("словарь единиц требует проект каталога 0.7+")
    if (not isinstance(vocabulary, dict)
            or set(vocabulary) != {"form_version", "purpose", "unit_words"}
            or type(vocabulary["form_version"]) is not int
            or vocabulary["form_version"] != 1
            or not isinstance(vocabulary["purpose"], str)
            or not vocabulary["purpose"].strip()
            or not isinstance(vocabulary["unit_words"], dict)
            or not vocabulary["unit_words"]):
        raise UnitVocabularyError("неверная форма проекта словаря единиц")
    dictionaries = documents.get("dictionaries.json")
    if not isinstance(dictionaries, dict):
        raise UnitVocabularyError("dictionaries.json: словари не заданы")
    for name in ("param_units", "condition_units"):
        codes = dictionaries.get(name)
        if (not isinstance(codes, list) or not codes
                or any(not isinstance(code, str) or not code for code in codes)
                or len(set(codes)) != len(codes)):
            raise UnitVocabularyError(f"{name}: нужен непустой перечень уникальных кодов")
    candidate = copy.deepcopy(dictionaries)
    candidate["unit_words"] = copy.deepcopy(vocabulary["unit_words"])
    ctx = _Ctx(candidate, ())
    ctx.catalog_070 = True
    _unit_words_check(ctx, candidate, "dictionaries")
    if ctx.problems:
        raise UnitVocabularyError("; ".join(str(p) for p in ctx.problems))
    result = copy.deepcopy(documents)
    result["dictionaries.json"] = candidate
    return result
