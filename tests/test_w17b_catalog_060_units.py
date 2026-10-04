# -*- coding: utf-8 -*-
"""Заготовка волны 17б: единицы каталога 0.6.0 — словарь `unit_words` и
единственный преобразователь `template_catalog.units.to_canonical`.

ID аудита: A06 (направление и единицы каталога 0.6.0), K02 (разбор
ответа по единицам — читает ту же карту), A05 (ведомость исполнения
сверяет значения после приведения к канону).

Контракт (oracle):
* `P0-CONTRACTS-v1.2-20260911.md` §3.3 (б): «канон — коды `param_units`
  как есть; новый словарь `unit_words` — по целевой единице слово →
  множитель к канону (`duration_minutes`: минута 1, час 60, сутки 1440,
  «чч:мм» — разбор; `count`: штука/раз/день/посадка 1;
  `time_of_day_minutes`: «чч:мм» от полуночи; `credit_hours`: кредитный
  час 1) и одна функция
  `template_catalog.units.to_canonical(value, word_or_code, target_unit)`»;
* `P0-COSMOS-SIDE-v1-20260911.md` раздел «0-бис», строка «Слова единиц и
  приведение (В-4, В-6)»: «одна функция … (модуль моей зоны в 0.6.0;
  обе стороны импортируют `template_catalog`) … `vmp` лишь обёртка над
  `template_catalog.units`, своей формулы не держит»;
* `P0-COSMOS-SIDE-v1-20260911.md` §2.2 (таблица слов и множителей) и
  §3.3, правило X21 («каждый код `param_units` ∪ `condition_units` имеет
  запись в `unit_words` … лишних кодов нет»);
* `P0-CONTRACTS-v1.2-20260911.md` §11.2 п. 8 (домен знака: умолчание
  `nonneg` для всех параметров 0.5.0) и §11.3 («`to_canonical` —
  единственный преобразователь»).

Ожидаемое поведение (числа — из таблицы §3.3 контракта):
«1 час» → 60, «12:00» → 720, «сутки» → 1440 при `duration_minutes`;
«3 суток» при `count` → 3 (слово «суток» у счётчика множителя не даёт);
«10,5 часов» → 630; «10:90» — отказ словами (минут больше 59); «-600» —
отказ (домен `nonneg`); неизвестное слово — отказ, а не число.

Заготовка КРАСНАЯ на d5af1f21: модуля `template_catalog/units.py` нет, в
словарях каталога 0.5.0 ключа `unit_words` нет. Ни одного skip/xfail:
красный цвет и есть заявленное состояние до реализации.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import template_catalog as tc                                     # noqa: E402

CATALOG = ROOT / "catalog"
VMP_UNITS = ROOT / "vmp" / "units.py"

#: Множители канона длительности — таблица §3.3 контракта.
DURATION_SCALE = {"минута": 1, "час": 60, "сутки": 1440}

#: Отказ словами: контракт формы исключения не называет, поэтому тест
#: принимает любой отказ читателя каталога либо ValueError (так уже
#: устроен канон: `canon._scalar` бросает ValueError с адресом).
REFUSALS = (ValueError, tc.CatalogError)


def _units():
    """Модуль-преобразователь читателя каталога.

    Импорт внутри теста, а не в шапке модуля: до реализации 0.6.0 модуля
    нет, и каждый тест обязан краснеть сам — сбор набора ломать нельзя."""
    from template_catalog import units                    # noqa: PLC0415
    return units


def _to_canonical(value, word_or_code, target_unit):
    return _units().to_canonical(value, word_or_code, target_unit)


def test_unit_words_covers_every_unit_code_of_the_catalog():
    """A06/X21, контракт §3.3 (б) и сторона COSMOS §3.3: карта единиц —
    один носитель слов и множителей; коды карты — коды словарей
    `param_units` ∪ `condition_units`, лишних нет."""
    dicts = tc.load(CATALOG).dictionaries()
    assert "unit_words" in dicts, (
        "в словарях каталога нет карты unit_words (контракт §3.3 (б): "
        "новый словарь выпуска 0.6.0)")
    words = dicts["unit_words"]
    assert isinstance(words, dict), "unit_words — карта, а не перечень"
    codes = set(dicts["param_units"]) | set(dicts["condition_units"])
    assert set(words) == codes, (
        f"карта единиц и словари кодов разошлись: нет записи у "
        f"{sorted(codes - set(words))}, лишние коды {sorted(set(words) - codes)}")
    for code, entry in sorted(words.items()):
        assert isinstance(entry, dict), f"{code}: запись карты — объект"
        for key in ("canon", "form", "words"):
            assert entry.get(key) not in (None, "", []), \
                f"unit_words.{code}.{key} пусто (X21)"
    scale = (words.get("duration_minutes") or {}).get("scale") or {}
    for word, mult in DURATION_SCALE.items():
        assert scale.get(word) == mult, (
            f"множитель слова «{word}» у duration_minutes — {scale.get(word)!r}, "
            f"контракт §3.3 требует {mult}")


@pytest.mark.parametrize("value, word, target, want", [
    (1, "минута", "duration_minutes", 1),
    (1, "час", "duration_minutes", 60),
    ("12:00", "чч:мм", "duration_minutes", 720),
    (1, "сутки", "duration_minutes", 1440),
    (3, "суток", "count", 3),
    ("10,5", "часов", "duration_minutes", 630),
    ("12:00", "чч:мм", "time_of_day_minutes", 720),
])
def test_to_canonical_follows_the_words_and_multipliers_of_the_contract(
        value, word, target, want):
    """A06/K02, контракт §3.3 (б): приведение к канону целевой единицы —
    одной функцией и по словам каталога; канон длительности и времени
    суток — целые минуты, счётчик — штуки без множителя."""
    got = _to_canonical(value, word, target)
    assert got == want, (
        f"to_canonical({value!r}, {word!r}, {target!r}) = {got!r}, "
        f"контракт требует {want!r}")
    assert isinstance(got, int) and not isinstance(got, bool), (
        f"канон {target} — целое число, а дано {type(got).__name__}")


@pytest.mark.parametrize("value, word, target, mark", [
    ("10:90", "чч:мм", "duration_minutes", "10:90"),
    (-600, "минута", "duration_minutes", "-600"),
    (1, "парсек", "duration_minutes", "парсек"),
    (1, "час", "минуты", "минуты"),
])
def test_to_canonical_refuses_with_words(value, word, target, mark):
    """A06/K02, общий закон 1 контракта (fail-closed словами): минуты
    больше 59 в «чч:мм», отрицательное значение при домене `nonneg`
    (§11.2 п. 8), слово вне карты и целевая единица вне `param_units` —
    отказ, а не число."""
    with pytest.raises(REFUSALS) as поймано:
        _to_canonical(value, word, target)
    assert mark in str(поймано.value), (
        f"отказ не называет предмет ({mark}): {поймано.value!r}")


def test_to_canonical_is_the_only_converter_and_vmp_holds_no_formula():
    """A05/A06, контракт §11.3 и «0-бис» (строка «Слова единиц и
    приведение»): преобразователь один — в читателе каталога; `vmp`
    своей формулы не держит, а импортирует его."""
    units = _units()
    assert callable(getattr(units, "to_canonical", None)), (
        "template_catalog.units.to_canonical — единственный "
        "преобразователь по контракту §11.3")
    if VMP_UNITS.is_file():
        text = VMP_UNITS.read_text(encoding="utf-8")
        assert "template_catalog" in text and "units" in text, (
            f"{VMP_UNITS} не импортирует template_catalog.units — "
            f"вторая формула единиц запрещена контрактом «0-бис»")
        for число in ("1440", "* 60", "*60"):
            assert число not in text.replace("to_canonical", ""), (
                f"{VMP_UNITS} несёт собственный множитель {число!r}: "
                f"множители живут в карте unit_words каталога")
