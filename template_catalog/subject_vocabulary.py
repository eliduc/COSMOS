"""Pure preparation of a parameter vocabulary for a modern catalogue draft.

No file I/O, publication or fallback reader. Runtime subjects are read only
from families[].params[].subject_words by the ordinary validated Catalog.
"""
from __future__ import annotations

import copy
import unicodedata

from .validate import _uses_schema_070


class SubjectVocabularyError(ValueError):
    """Draft data do not cover exactly the declared parameter contract."""


def attach(documents: dict, vocabulary: dict) -> dict:
    """Return a copy with explicit words attached to every existing parameter.

    The caller prepares the modern draft and remains responsible for normal
    catalogue validation/fingerprinting. No version or numeric contract changes
    are made here; partial coverage, duplicates and stale addresses are refused.
    """
    version = (documents.get("catalog.json") or {}).get("version")
    if not _uses_schema_070(version):
        raise SubjectVocabularyError("словарь предметов требует проект каталога 0.7+")
    if (not isinstance(vocabulary, dict)
            or set(vocabulary) != {"form_version", "purpose", "families"}
            or type(vocabulary["form_version"]) is not int
            or vocabulary["form_version"] != 1
            or not isinstance(vocabulary["purpose"], str)
            or not vocabulary["purpose"].strip()
            or not isinstance(vocabulary["families"], dict)):
        raise SubjectVocabularyError("неверная форма проекта словаря предметов")
    words = vocabulary["families"]
    families = (documents.get("families.json") or {}).get("families")
    if not isinstance(families, list):
        raise SubjectVocabularyError("families.json: список семей не задан")
    expected = {}
    for family in families:
        if not isinstance(family, dict) or not isinstance(family.get("id"), str):
            raise SubjectVocabularyError("неверная запись семьи")
        fid = family["id"]
        if fid in expected:
            raise SubjectVocabularyError(f"повтор семьи {fid}")
        params = family.get("params")
        if not isinstance(params, list):
            raise SubjectVocabularyError(f"{fid}: список параметров не задан")
        names = []
        for param in params:
            if not isinstance(param, dict) or not isinstance(param.get("name"), str):
                raise SubjectVocabularyError(f"{fid}: неверная запись параметра")
            names.append(param["name"])
        if len(names) != len(set(names)):
            raise SubjectVocabularyError(f"{fid}: повтор параметра")
        expected[fid] = set(names)
    required = {fid for fid, names in expected.items() if names}
    if set(words) != required:
        raise SubjectVocabularyError("семьи словаря не равны семьям с параметрами: "
                                     f"нет {sorted(required - set(words))}; "
                                     f"лишние {sorted(set(words) - required)}")
    for fid, params in words.items():
        if not isinstance(params, dict) or set(params) != expected[fid]:
            raise SubjectVocabularyError(f"{fid}: словарь не покрывает точный набор параметров")
        for name, phrases in params.items():
            if (not isinstance(phrases, list) or not phrases
                    or any(not isinstance(p, str) or not p.strip() for p in phrases)):
                raise SubjectVocabularyError(f"{fid}.{name}: нужен непустой список слов")
            normalized = [unicodedata.normalize("NFC", " ".join(p.split())).lower()
                          for p in phrases]
            if phrases != normalized or len(phrases) != len(set(phrases)):
                raise SubjectVocabularyError(f"{fid}.{name}: слова должны быть каноничны и уникальны")
    result = copy.deepcopy(documents)
    for family in result["families.json"]["families"]:
        for param in family["params"]:
            param["subject_words"] = copy.deepcopy(words[family["id"]][param["name"]])
    return result
