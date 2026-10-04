# -*- coding: utf-8 -*-
"""Единственный преобразователь единиц каталога (контракт 0.7.0+).

Канон — КОД единицы из словаря `param_units` (`duration_minutes`,
`time_of_day_minutes`, `count`, `credit_hours`, …); слова записи и их
множители живут в одном носителе — карте `unit_words` словарей каталога.
Контракт требует единого носителя для CDPM, ведомости COSMOS и стендов
(П0 §3.3 (б), §11.3; сторона COSMOS «0-бис», «Слова единиц и приведение»).
Наличие модуля само по себе не доказывает подключение всех потребителей.

Одна функция наружу:

    to_canonical(value, word_or_code, target_unit) -> int | float

`value` — число либо строка записи («10,5», «12:00», «1.5 часа»);
`word_or_code` — слово записи («час», «суток», «чч:мм») либо сам код
целевой единицы; `target_unit` — код канона. Возврат — число в каноне
целевой единицы: целые минуты у длительности и времени суток, целые
штуки у счётчика, десятичные у кредитных часов.

Всякая неясность — ОТКАЗ словами, а не догадка (общий закон 1 контракта):
слово вне карты, целевая единица вне словаря, две величины в одной записи
(«12 ч. (600 минут)» — не «взять первую»), минуты больше 59 в «чч:мм»,
дробь там, где канон целый, отрицательное значение при домене `nonneg`
(ответ CDPM §11.2 п. 8: все параметры каталога 0.5.0 неотрицательны).
Округления нет нигде: 10,5 часа — это 630 минут, а 10,5 минуты — отказ.

Исключение — `CatalogError` (подкласс `ValueError` тут не нужен: обе
стороны ловят `template_catalog.CatalogError`, тесты принимают и
`ValueError`).
"""
from __future__ import annotations

import math
import re
import unicodedata
from decimal import Decimal, InvalidOperation, localcontext
from fractions import Fraction

from .errors import CatalogError

#: Формы значения канонической единицы (поле `form` карты `unit_words`).
#: Перечень закрыт: форма, которой тут нет, — не форма, а описка.
UNIT_FORMS = ("integer_nonneg", "integer_0_1439_tz", "decimal_nonneg",
              "decimal_positive", "code", "code_list", "boolean")
#: Формы, чей канон — ЦЕЛОЕ число (дробь после умножения — отказ).
INTEGER_FORMS = ("integer_nonneg", "integer_0_1439_tz")
#: Формы, к числу не приводимые (обозначения, перечни, флаг).
NON_NUMERIC_FORMS = ("code", "code_list", "boolean")
#: Слово-форма «часы и минуты через двоеточие»: множителя не имеет —
#: разбор даёт минуты сам.
CLOCK_WORD = "чч:мм"
#: Ключ карты слов в словарях каталога.
DICT_KEY = "unit_words"

_CLOCK_RE = re.compile(r"^(\d{1,3}):(\d{1,2})$")
_NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


class UnitsError(CatalogError):
    """Отказ приведения к канону: слово, единица, форма или значение."""


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", str(s))


def _word(s) -> str:
    """Нормализация слова записи: NFC, строчные, без хвостовой точки и
    лишних пробелов. «Ч.» и «ч» — одно слово; «Часов» и «часов» — тоже."""
    out = " ".join(_nfc(s).strip().lower().split())
    while out.endswith("."):
        out = out[:-1].strip()
    return out


def unit_words(source=None) -> dict:
    """Карта `unit_words` словарей каталога; нет карты — отказ словами.

    Своего словаря у модуля нет намеренно: единственный носитель слов и
    множителей — каталог (контракт §3.3 (б))."""
    from . import current                                  # noqa: PLC0415
    dicts = current(source, validated=True).dictionaries()
    words = dicts.get(DICT_KEY)
    if not isinstance(words, dict) or not words:
        raise UnitsError(
            f"в словарях каталога нет карты {DICT_KEY} — слова единиц "
            f"читаются только из каталога 0.7.0 и старше; своего словаря "
            f"у преобразователя нет")
    return words


def canon_word(target_unit: str, source=None) -> str:
    """Слово канона целевой единицы («минута», «штука», …) — для слов
    отказа и подсказок анкеты."""
    return str(_entry(target_unit, unit_words(source)).get("canon") or
               target_unit)


def _entry(target_unit, words: dict) -> dict:
    code = _nfc(target_unit).strip()
    entry = words.get(code)
    if not isinstance(entry, dict):
        raise UnitsError(
            f"целевая единица {target_unit!r} не код словаря param_units: "
            f"известны {', '.join(sorted(words))}")
    return entry


def scale_map(entry: dict, code: str) -> dict:
    """Effective word multipliers shared by conversion and semantic reading.

    Historical records are readable without today's schema, but ambiguous
    normalized words must not silently select whichever key was last.
    """
    declared, scale = entry.get("words"), entry.get("scale")
    if not isinstance(declared, (list, tuple)) or not isinstance(scale, dict):
        raise UnitsError(f"{DICT_KEY}.{code}: слова и множители имеют неверную форму")
    if any(not isinstance(word, str) or not _word(word)
           for word in list(declared) + list(scale)):
        raise UnitsError(f"{DICT_KEY}.{code}: слово единицы не является непустой строкой")
    result = {}
    for raw in sorted(set(declared) | set(scale)):
        word, mult = _word(raw), scale.get(raw, 1)
        if (not isinstance(mult, (int, float)) or isinstance(mult, bool)
                or mult <= 0 or (isinstance(mult, float) and not math.isfinite(mult))):
            raise UnitsError(f"{DICT_KEY}.{code}.scale.{raw}: множитель не положительное число")
        if word in result and result[word] != mult:
            raise UnitsError(f"{DICT_KEY}.{code}: слово {word!r} после нормализации "
                             "имеет разные множители")
        result[word] = mult
    return result


def _multiplier(entry: dict, code: str, word_or_code: str, words: dict):
    """Множитель к канону по слову записи; слова нет в карте — отказ."""
    word = _word(word_or_code)
    scale = scale_map(entry, code)
    if word == _word(code):
        return 1, word                       # значение уже в каноне
    known = set(scale)
    if word not in known and word not in scale:
        if word in {_word(c) for c in words}:
            raise UnitsError(
                f"единица {word_or_code!r} — код другой единицы каталога; "
                f"перевода {word_or_code!r} → {code!r} карта {DICT_KEY} не "
                f"объявляет, и догадываться о нём нельзя")
        raise UnitsError(
            f"слово единицы {word_or_code!r} не объявлено картой "
            f"{DICT_KEY}.{code}: известны {', '.join(sorted(known))}")
    mult = scale.get(word, 1)
    if not isinstance(mult, (int, float)) or isinstance(mult, bool) \
            or mult <= 0 or (isinstance(mult, float) and not math.isfinite(mult)):
        raise UnitsError(
            f"{DICT_KEY}.{code}.scale.{word}: множитель {mult!r} не "
            f"положительное число")
    return mult, word


def _decimal(raw: str, shown) -> Decimal:
    try:
        return Decimal(raw.replace(",", "."))
    except InvalidOperation:
        raise UnitsError(f"значение {shown!r}: не число") from None


def _number(value, code: str, entry: dict, word: str):
    """(число Decimal, признак «разобрано как чч:мм») из значения записи."""
    if isinstance(value, bool):
        raise UnitsError(f"значение {value!r}: флаг — не число")
    if isinstance(value, int):
        return Decimal(value), False
    if isinstance(value, float):
        return Decimal(str(value)), False
    if isinstance(value, Decimal):
        return value, False
    if not isinstance(value, str):
        raise UnitsError(f"значение {value!r}: ни число, ни запись словами")
    text = " ".join(_nfc(value).strip().split())
    clock = _CLOCK_RE.match(text)
    if clock:
        if CLOCK_WORD not in {_word(w) for w in entry.get("words") or ()}:
            raise UnitsError(
                f"значение {value!r}: форма {CLOCK_WORD} не объявлена для единицы {code}")
        hours, minutes = int(clock.group(1)), int(clock.group(2))
        if minutes > 59:
            raise UnitsError(
                f"значение {value!r}: в записи «чч:мм» минут больше 59 "
                f"({minutes}) — это не время и не длительность")
        return Decimal(hours * 60 + minutes), True
    found = _NUMBER_RE.findall(text)
    if not found:
        raise UnitsError(f"значение {value!r}: числа в записи нет")
    if len(found) > 1:
        raise UnitsError(
            f"значение {value!r}: в одной записи две величины "
            f"({', '.join(found)}) — какая из них норма, решает автор "
            f"правила, а не преобразователь; выбор первой запрещён")
    tail = _word(text.replace(found[0], " ", 1).replace("(", " ")
                 .replace(")", " "))
    if tail:
        scale = scale_map(entry, code)
        known = set(scale)
        if tail not in known and tail not in scale:
            raise UnitsError(
                f"значение {value!r}: слово {tail!r} рядом с числом не "
                f"объявлено картой {DICT_KEY}.{code}")
        if scale.get(tail, 1) != scale.get(word, 1):
            raise UnitsError(
                f"значение {value!r}: слово записи {tail!r} и объявленная "
                f"единица {word!r} дают разные множители — две правды об "
                f"одной величине")
    return _decimal(found[0], value), False


def _canon_number(total: Fraction, form: str, code: str, value, word: str):
    """Проверка формы канона и возврат числа нужного вида."""
    if form in NON_NUMERIC_FORMS:
        raise UnitsError(
            f"единица {code!r} — {form}: приведение записи {value!r} к "
            f"числу не определено")
    if form not in UNIT_FORMS:
        raise UnitsError(
            f"{DICT_KEY}.{code}.form: форма {form!r} не из перечня "
            f"{', '.join(UNIT_FORMS)}")
    if form == "decimal_positive":
        if total <= 0:
            raise UnitsError(
                f"значение {value!r} ({word}) в каноне {code} = {total} — "
                f"канон требует положительного числа")
    elif total < 0:
        raise UnitsError(
            f"значение {value!r} ({word}) в каноне {code} = {total} — "
            f"домен параметров каталога неотрицательный (nonneg): "
            f"знаковых единиц в каталоге нет")
    if form in INTEGER_FORMS:
        if total.denominator != 1:
            raise UnitsError(
                f"значение {value!r} ({word}) в каноне {code} = {total} — "
                f"канон целый, а округлять величину правила нельзя")
        out = int(total)
        if form == "integer_0_1439_tz" and not 0 <= out <= 1439:
            raise UnitsError(
                f"значение {value!r} в каноне {code} = {out} — время суток "
                f"считается от полуночи и лежит в 0…1439 минут")
        return out
    if total.denominator == 1:
        return int(total)
    try:
        out = float(total)
    except (OverflowError, ValueError):
        raise UnitsError(f"значение {value!r}: не представимо конечным числом") from None
    if not math.isfinite(out) or (out == 0 and total != 0):
        raise UnitsError(f"значение {value!r}: теряется при представлении числом")
    return out


def to_canonical(value, word_or_code, target_unit, *, source=None, exact=False):
    """Значение записи → число в каноне целевой единицы.

    `to_canonical(1, "час", "duration_minutes")` = 60,
    `to_canonical("12:00", "чч:мм", "time_of_day_minutes")` = 720,
    `to_canonical(3, "суток", "count")` = 3 (у счётчика слово «сутки»
    множителя не даёт — считаются сутки, а не минуты),
    `to_canonical("10,5", "часов", "duration_minutes")` = 630.

    `exact=True` сохраняет точный Decimal для сверки типизированных
    носителей; обычный публичный результат остаётся int/float. Форма,
    область и отказ непредставимого числа проверяются в обоих режимах.

    Общая точка преобразования, предусмотренная контрактом §11.3."""
    words = unit_words(source)
    code = _nfc(target_unit).strip()
    entry = _entry(code, words)
    mult, word = _multiplier(entry, code, word_or_code, words)
    number, clock = _number(value, code, entry, word)
    if word == CLOCK_WORD and not clock:
        raise UnitsError(f"значение {value!r}: объявлена форма {CLOCK_WORD}, "
                         "но запись не содержит часов и минут через двоеточие")
    if clock and word not in (CLOCK_WORD, _word(code)):
        raise UnitsError(f"значение {value!r}: запись часов и минут не согласна "
                         f"с объявленной единицей {word_or_code!r}")
    if not number.is_finite():
        raise UnitsError(f"значение {value!r}: требуется конечное число")
    # Decimal multiplication obeys the caller's precision: even multiplying
    # by one can round a fractional minute to an integer, or alter a large
    # integer. Keep the exact decimal ratio until the canonical-form check.
    # The public int/float boundary is applied only after that check.
    total = Fraction(number)
    if not clock:
        total *= Fraction(Decimal(str(mult)))
    checked = _canon_number(total, str(entry.get("form")), code, value, word)
    if not exact:
        return checked
    if isinstance(checked, int):
        return Decimal(checked)
    factor = Decimal(1) if clock else Decimal(str(mult))
    # Do not round an exact typed carrier at the public float boundary.
    # A finite decimal product needs at most the sum of coefficient lengths.
    with localcontext() as context:
        context.prec = max(1, len(number.as_tuple().digits)
                           + len(factor.as_tuple().digits))
        return number * factor
