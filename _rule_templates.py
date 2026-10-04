"""Библиотека шаблонов правил (Ярус 2б, 2026-07-24).

Внутренняя семантика шаблона — формализм школы GERAD (исследовательский
центр анализа решений, Монреаль), фактический академический стандарт
машинного представления норм: каждое правило описывается как РЕСУРС с
окном допустимых значений и функцией продления вдоль последовательности
активностей (Irnich, Desaulniers «Shortest Path Problems with Resource
Constraints», Springer 2005). Правило становится данными, а не кодом.

Запись шаблона:
  resource   — имя ресурса (что накапливается/проверяется);
  unit       — единица: minutes | count | days;
  level      — уровень применения: leg | duty | pairing | day | week |
               month | rolling28 | year | roster;
  bound      — какая граница осмысленна: min | max;
  extension  — функция продления словами (как ресурс растёт вдоль
               последовательности активностей);
  tier1      — имя исполняемого параметра Яруса 1, если шаблон на него
               ложится БЕЗ потери смысла (иначе None: правило остаётся
               записью вида «resource» — честный Ярус 2б).

Модуль детерминированный: без моделей, без сети, без ввода-вывода.
"""
from __future__ import annotations

import math
import re
from typing import Optional

# --------------------------------------------------------------- шаблоны

TEMPLATES: dict[str, dict] = {
    "min_rest": {
        "title": "Минимальный отдых между нарядами",
        "resource": "непрерывный отдых перед нарядом",
        "unit": "minutes", "level": "duty", "bound": "min",
        "extension": "перед каждым нарядом ресурс = длительность перерыва "
                     "от конца предыдущего наряда; окно снизу",
        "tier1": "min_rest_min",
    },
    "max_duty_span": {
        "title": "Максимальная длительность наряда",
        "resource": "длительность наряда от явки до окончания",
        "unit": "minutes", "level": "duty", "bound": "max",
        "extension": "внутри наряда ресурс растёт с каждым участком; "
                     "окно сверху",
        "tier1": "max_duty_span_min",
    },
    "max_pairing_span": {
        "title": "Максимальная длительность цепочки рейсов",
        "resource": "длительность цепочки от первого вылета до "
                    "возвращения на базу",
        "unit": "minutes", "level": "pairing", "bound": "max",
        "extension": "ресурс растёт вдоль всей цепочки; окно сверху",
        "tier1": "max_pairing_span_min",
    },
    "max_monthly_block": {
        "title": "Месячный предел полётного времени",
        "resource": "полётные минуты календарного месяца",
        "unit": "minutes", "level": "month", "bound": "max",
        "extension": "каждый участок добавляет свои полётные минуты "
                     "месяцу своего вылета; окно сверху",
        "tier1": "max_monthly_block_min",
    },
    "min_sit": {
        "title": "Минимальная стыковка между участками",
        "resource": "пауза между прилётом и следующим вылетом",
        "unit": "minutes", "level": "leg", "bound": "min",
        "extension": "на каждой стыковке ресурс = пауза; окно снизу",
        "tier1": "min_sit_min",
    },
    "max_landings_per_duty": {
        "title": "Максимум посадок в наряде",
        "resource": "число посадок в наряде",
        "unit": "count", "level": "duty", "bound": "max",
        "extension": "каждый участок добавляет одну посадку; окно сверху",
        "tier1": "max_landings_per_duty",
    },
    "weekly_rest": {
        "title": "Еженедельный непрерывный отдых",
        "resource": "самый длинный непрерывный отдых недели",
        "unit": "minutes", "level": "week", "bound": "min",
        "extension": "по каждой неделе ресурс = максимум непрерывного "
                     "отдыха; окно снизу; вне Яруса 1",
        "tier1": None,
    },
    "max_work_time_month": {
        "title": "Месячный предел рабочего времени",
        "resource": "рабочие минуты календарного месяца (явка-окончание "
                    "по дням)",
        "unit": "minutes", "level": "month", "bound": "max",
        "extension": "каждый рабочий день добавляет окно от первого "
                     "вылета до последнего прилёта; окно сверху; вне "
                     "Яруса 1 (в решателе — личное поле сотрудника)",
        "tier1": None,
    },
    "max_block_rolling28": {
        "title": "Предел полётного времени за 28 подряд идущих дней",
        "resource": "полётные минуты скользящего окна 28 дней",
        "unit": "minutes", "level": "rolling28", "bound": "max",
        "extension": "окно скользит по дням; каждый участок добавляет "
                     "полётные минуты всем окнам, его накрывающим; "
                     "вне Яруса 1",
        "tier1": None,
    },
    "max_block_year": {
        "title": "Годовой предел полётного времени",
        "resource": "полётные минуты календарного года",
        "unit": "minutes", "level": "year", "bound": "max",
        "extension": "каждый участок добавляет полётные минуты своему "
                     "году; окно сверху; вне Яруса 1",
        "tier1": None,
    },
}

# Синонимы канонических имён (только ЯВНЫЕ идентификаторы — никакого
# угадывания смысла по свободному тексту).
_CANON = {}
for _tid in TEMPLATES:
    _CANON[_tid] = _tid
    _CANON[_tid + "_min"] = _tid
_CANON.update({
    "min_rest_min": "min_rest",
    "max_duty_span_min": "max_duty_span",
    "max_pairing_span_min": "max_pairing_span",
    "max_monthly_block_min": "max_monthly_block",
    "min_sit_min": "min_sit",
})


def canonical_template(name: str) -> Optional[str]:
    """Каноническое имя шаблона по явному идентификатору (или None)."""
    return _CANON.get(str(name or "").strip().lower())


# ------------------------------------------------- единицы и окна (общие)

_UNIT_FACTORS = {
    "min": 1, "mins": 1, "minute": 1, "minutes": 1,
    "мин": 1, "минут": 1, "минуты": 1, "минута": 1,
    "h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60,
    "ч": 60, "час": 60, "часа": 60, "часов": 60,
    "d": 1440, "day": 1440, "days": 1440,
    "д": 1440, "дн": 1440, "дней": 1440, "сут": 1440, "сутки": 1440,
}
_COUNT_UNITS = {"count", "шт", "раз", "посадки", "посадок", "landings",
                "sectors", "секторов", "сектора", ""}

_TIME_RE = re.compile(r"^(\d{1,4}):([0-5]\d)$")


def value_to_minutes(value, unit: str) -> Optional[int]:
    """Число + единица -> минуты; запись Ч:ММ понимается как время.
    None = преобразование невозможно (честный отказ, не догадка)."""
    unit_key = str(unit or "").strip().lower()
    s = str(value).strip() if value is not None else ""
    m = _TIME_RE.match(s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    try:
        num = float(s.replace(",", "."))
    except ValueError:
        return None
    if not (num == num) or num in (float("inf"), float("-inf")):
        return None
    factor = _UNIT_FACTORS.get(unit_key)
    if factor is None:
        return None
    out = num * factor
    if out != int(out) or out < 0 or int(out) > 2_147_483_647:
        return None
    return int(out)


def value_to_count(value) -> Optional[int]:
    s = str(value).strip() if value is not None else ""
    try:
        num = float(s.replace(",", "."))
    except ValueError:
        return None
    # ревью 2026-07-24: inf/nan доходили до int() и роняли импорт
    if not math.isfinite(num):
        return None
    if num != int(num) or num < 0 or int(num) > 2_147_483_647:
        return None
    return int(num)


def is_count_unit(unit: str) -> bool:
    return str(unit or "").strip().lower() in _COUNT_UNITS


def looks_like_time(value) -> bool:
    """Значение записано как Ч:ММ (единица очевидна из формы записи)."""
    return bool(_TIME_RE.match(str(value or "").strip()))


_WINDOWS = {
    "": "", "none": "",
    "month": "month", "месяц": "month", "мес": "month",
    "28d": "rolling28", "28 дней": "rolling28", "rolling28": "rolling28",
    "4 недели": "rolling28", "28 days": "rolling28",
    "year": "year", "год": "year",
    "week": "week", "неделя": "week", "7 дней": "week",
    "day": "day", "сутки": "day", "день": "day",
    "duty": "duty", "наряд": "duty",
    "pairing": "pairing", "пейринг": "pairing", "цепочка": "pairing",
    "leg": "leg", "участок": "leg",
}


def normalize_window(window: str) -> Optional[str]:
    """Окно применения -> канонический уровень ('' = не задано);
    None = не распознано."""
    return _WINDOWS.get(str(window or "").strip().lower())


def resource_constraint(template_id: str, minutes_or_count: int) -> dict:
    """Запись вида «ресурс» по шаблону (Ярус 2б)."""
    t = TEMPLATES[template_id]
    window = ({"min": minutes_or_count, "max": None}
              if t["bound"] == "min"
              else {"min": None, "max": minutes_or_count})
    return {"kind": "resource", "resource": t["resource"],
            "window": window, "unit": t["unit"], "level": t["level"],
            "extension": t["extension"]}
