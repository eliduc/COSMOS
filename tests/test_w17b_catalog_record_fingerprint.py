# -*- coding: utf-8 -*-
"""Заготовка волны 17б: отпечаток ОДНОЙ записи каталога.

ID аудита: K01 (контракт семьи в посадке), A05 (один носитель истины у
параметра правила).

Контракт (oracle):
* `P0-CONTRACTS-v1.2-20260911.md` §11.1, строка «Контракт семьи в
  шапке»: `family_contract = {family_id, catalog_version,
  catalog_fingerprint, record_fingerprint, param_set}`, где
  `record_fingerprint` — «идентичность СОДЕРЖАНИЯ записи семьи
  (`template_catalog.canon.record_fingerprint`)»; по нему CDPM судит
  совместимость смены каталога (K01);
* `P0-COSMOS-SIDE-v1-20260911.md` раздел «0-бис» (действующая редакция),
  строка «Контракт семьи в посадке»: «`record_fingerprint` =
  `template_catalog.canon.record_fingerprint(record)` = sha256
  канонических байтов записи (новая функция моей зоны в 0.6.0; поля
  fingerprint в families.json не будет)».

Ожидаемое поведение взято ИЗ КОНТРАКТА, а не из будущей реализации:

1. отпечаток — sha256 канонических байтов ОДНОЙ записи. Канон записи у
   каталога один (`template_catalog.canon`): списки-множества и списки с
   ключом сортируются, строки в NFC, JSON компактный с сортировкой
   ключей. Ручной расчёт теста собран только из ПУБЛИЧНЫХ частей канона
   (`canon.normalize` над телом из одной записи), своей формулы тест не
   заводит;
2. отпечаток судит СОДЕРЖАНИЕ: смена единицы параметра его меняет,
   перестановка ключей записи и порядок параметров — нет;
3. форма — 64 знака шестнадцатеричной строки в нижнем регистре, вызов
   детерминирован.

Заготовка КРАСНАЯ на d5af1f21: функции `record_fingerprint` в
`template_catalog.canon` сегодня нет (её пишет волна каталога 0.6.0).
Оговорка об oracle: контракт называет «канонические байты ЗАПИСИ», и
ручной расчёт ниже читает эти слова буквально — канон записи без обёртки
разделами. Если реализация выберет обёртку, расхождение обязано
обсуждаться как правка контракта, а не как правка теста.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from template_catalog import canon                                # noqa: E402


def _family_record(*, unit: str = "duration_minutes") -> dict:
    """Синтетическая запись семьи: формы полей — как у записей каталога
    0.5.0 (`catalog/families.json`), содержание — пробное."""
    return {
        "id": "proba_semya_w17b",
        "class": "core",
        "title": "Проба семьи волны 17б",
        "subject": "смена",
        "subject_words": ["смена", "рабочий день"],
        "complements": [],
        "kind": "HARD",
        "zone": "PAIRING",
        "nature": "порог",
        "nature_confirmed_by": "владелец",
        "violation_unit": "minute",
        "violation_size": "count",
        "reads_history": None,
        "params": [
            {"name": "max_duty_duration", "unit": unit, "relation": "max",
             "qualifier": None, "tz": None},
            {"name": "min_turnaround", "unit": "duration_minutes",
             "relation": "min", "qualifier": "area", "tz": None},
        ],
        "param_sets": [{"id": "все",
                        "params": ["max_duty_duration", "min_turnaround"]}],
        "conditions": [],
        "rule_form": None,
        "semantics": {"formula": None, "rates_form": None,
                      "text": "порог длительности смены, проба"},
        "executor_kinds": ["kotlin_knob"],
        "check_templates": [],
        "acceptance": None,
        "origin": {"kind": "инвентаризация", "date": "2026-09-12",
                   "confirmed_by": "владелец", "note": None},
        "note": None,
        "stand_tasks": [],
        "status": "есть",
        "history": [],
    }


def _manual_fingerprint(record: dict) -> str:
    """Ручной расчёт по контракту: sha256 канонических байтов записи.

    Канон записи берётся у самого каталога (`canon.normalize` над телом
    из одной записи) — так тест не заводит второй формулы нормализации,
    а сериализация повторяет `canon.canonical_json`."""
    normed = canon.normalize({"families": [copy.deepcopy(record)],
                              "criteria": [], "dictionaries": {}})
    one = normed["families"][0]
    text = json.dumps(one, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fingerprint(record: dict) -> str:
    """Отпечаток записи функцией читателя — та, которую называет
    контракт (`template_catalog.canon.record_fingerprint`)."""
    assert hasattr(canon, "record_fingerprint"), (
        "в template_catalog.canon нет record_fingerprint: контракт "
        "«0-бис» (строка «Контракт семьи в посадке») требует одну "
        "функцию отпечатка записи в читателе каталога")
    return canon.record_fingerprint(record)


def test_record_fingerprint_is_sha256_of_the_canonical_record_bytes():
    """K01/A05, контракт «0-бис»: отпечаток = sha256 канонических байтов
    записи; ручной расчёт и функция читателя дают одно и то же."""
    rec = _family_record()
    got = _fingerprint(rec)
    assert got == _manual_fingerprint(rec), (
        "отпечаток записи не равен sha256 её канонических байтов")


def test_record_fingerprint_is_deterministic_and_64_hex():
    """K01, контракт §11.1: отпечаток — неизменяемая идентичность
    содержания записи, значит форма и повтор вызова предсказуемы."""
    rec = _family_record()
    first, second = _fingerprint(rec), _fingerprint(rec)
    assert first == second, "два вызова дали разные отпечатки"
    assert len(first) == 64 and first == first.lower(), first
    assert all(c in "0123456789abcdef" for c in first), first


def test_record_fingerprint_ignores_key_and_param_order():
    """K01, контракт «0-бис» + канон §9: отпечаток судит содержание, а
    не порядок — иначе перештамповка контракта семьи (11.3) ловила бы
    перестановку ключей вместо смены записи."""
    rec = _family_record()
    shuffled = {k: copy.deepcopy(rec[k]) for k in reversed(list(rec))}
    shuffled["params"] = list(reversed(shuffled["params"]))
    assert _fingerprint(shuffled) == _fingerprint(rec), (
        "перестановка ключей записи или порядка параметров сдвинула "
        "отпечаток")


def test_record_fingerprint_changes_when_a_param_unit_changes():
    """K01/A05, контракт §11.1 («по нему CDPM судит совместимость смены
    каталога»): смена единицы параметра — смена содержания записи."""
    minutes = _family_record(unit="duration_minutes")
    counts = _family_record(unit="count")
    assert _fingerprint(minutes) != _fingerprint(counts), (
        "смена единицы параметра отпечаток записи не сдвинула — "
        "перепосадка не будет замечена")


def test_record_fingerprint_refuses_a_record_that_is_not_canonical():
    """K01, канон §9 (`canon._scalar`): дробное число в каталоге
    запрещено; отпечаток такой записи не выдаётся молча."""
    rec = _family_record()
    rec["params"][0]["value"] = 1.5
    with pytest.raises(ValueError):
        _fingerprint(rec)
