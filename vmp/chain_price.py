# -*- coding: utf-8 -*-
"""Одна цена цепочки для построителя и проверяющего (аудит A6, 05.09.2026).

Канон ВМП (02.09.2026): целевая = критерий + Σ (мера × вес) по мягким
правилам части «Пейринг». До аудита A6 эта сумма складывалась только в
ветке коэффициентов построителя; при критерии «нормированная стоимость
по рангам» (0096) и при ставках источника штрафы за ночёвку, окно дней
недели и продлённую смену молча выпадали из цены — класс ошибки
«потеря слагаемого цели без следа». Второй дефект того же аудита —
два определения ночи: построитель считал дневной промежуток между
сменами вне базы за ночь (max(1, …)), сертификатор и решатель — за
ноль.

Здесь живёт ОДНО определение мер и ОДНА композиция цены; построитель
зовёт её из всех точек ценообразования (эмиссия кандидата, оценка
колонок, нижняя оценка, закреплённые цепочки), сертификатор — для
независимого пересчёта мер по решению.

Единица цепочки — ПЕРИОД: смена вместе с примыкающими перегонами
(разметка построителя, `periods_of`); границы периода — по перелётам
(первый вылет, последний прилёт), место отдыха — аэропорт прилёта
последнего перелёта периода.

Определение ночи (единое с сертификатором ростеринга и решателем):
период окончен вне базы → ночей = календарных дней по местному времени
базы от даты окончания периода до даты начала следующего; тот же день
= 0 (дневной промежуток — не ночёвка; непрерывная смена через полночь
ночи не создаёт). Имя определения — `calendar_date_transition`
(NIGHT_DEFINITION): известный интервал отдыха пересекает местную
календарную дату; это именно календарное определение ВМП, не
астрономическая ночь и не норматив авиакомпании.

Контракт Б (план 05.09.2026, согласован владельцем, аудитором, COSMOS и
CDPM; единая версия 06.09.2026, строки P-401…P-414): цена цепочки —
известная часть (known_subtotal) на объявленной ОБЛАСТИ УЧЁТА, а не
полная цена и не нижняя оценка. Область учёта (scope_template) —
сериализуемый объект: версия, определение ночи, пояс, универсум
приписок (eligible_bases) и признак его полноты, граница цепочки; та
же область лежит в подписи каталога (config_sig.price_scope), и по ней
сертификатор пересчитывает цепочку независимо. Доказанная ночь —
только вне ПОЛНОГО универсума приписок до следующего периода той же
цепочки; станция внутри универсума (приписка экипажа неизвестна),
станция вне неполного универсума, пустое место отдыха, перекрытие
периодов и открытый конец цепочки — адресные неопределённые события
(unknown_events) с причинами и идентичностью (event_id), не зависящей
от разбиения на цепочки. Дом, текущее место и станция первого вылета —
разные вещи: старт цепочки базой приписки не считается. Хвост цепочки
ни автоматической ночи, ни нуля не получает.

Неизвестная семья мягкого правила — отказ словами: штраф, для которого
нет меры, иначе выпал бы из цели молча.

Перечень мягких семей части «Пейринг» рукой здесь НЕ ПИШЕТСЯ (урок
09.09.2026: первая семья конвейера пополнения сломала четыре таких
перечня сразу): его отдаёт носитель — `soft_families()`, читающая
записи каталога шаблонов (`kind`, `zone`, `violation_unit`). Единица
меры остаётся у формы: исполнитель называет своё слово по коду единицы
(`unit_words()`), а не по имени семьи.

Слово единицы нарушения — тоже ОДИН НОСИТЕЛЬ (каталог 0.5.0,
11.09.2026): словарь `violation_units` стал картой «код → слово», и
рукописной карты перевода в модуле не осталось — прежний `UNIT_WORDS`
был вторым носителем того же знания и расходился со словарём молча.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable

#: Версия модели цены — часть подписи постановки каталога: смена
#: определения ночи или композиции меняет цену при тех же ключах.
#: chain-price/1 → 2-contract-b: определение ночи стало контрактом Б
#: (хвостовая ночь не начисляется, область учёта явная) — каталоги
#: прежней версии сертификатор как текущие не пересчитывает.
VERSION = "chain-price/2-contract-b"
#: Версия формы области учёта (scope_template) и имя определения ночи.
SCOPE_VERSION = "night-accounting/1"
NIGHT_DEFINITION = "calendar_date_transition"

#: Зоны записи каталога, входящие в часть «Пейринг».
_PAIRING_ZONES = ("PAIRING", "BOTH")

#: Кэш карты единиц: (экземпляр каталога, карта). Ключ — сам экземпляр
#: двери, как у перечня мягких семей: читатель отдаёт новый объект,
#: когда файлы каталога сменились.
_UNITS_CACHE: tuple | None = None

#: Кэш перечня мягких семей: (экземпляр каталога, перечень). Ключ — сам
#: экземпляр двери: читатель отдаёт НОВЫЙ объект, когда файлы каталога
#: сменились, и сравнение по тождеству ловит смену без второго обхода
#: файлов. Обход двери стоит ~200 мкс — в точках ценообразования его
#: быть не должно, поэтому мера семьи (measure) носителя не спрашивает
#: вовсе: она различает семьи своими ветвями, а равенство «ветви ≡
#: перечню носителя» держит сторож (tests/vmp/test_family_carriers.py).
_SOFT_CACHE: tuple | None = None

_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
             "friday": 4, "saturday": 5, "sunday": 6,
             "понедельник": 0, "вторник": 1, "среда": 2, "четверг": 3,
             "пятница": 4, "суббота": 5, "воскресенье": 6}


class ChainPriceError(ValueError):
    """Цена цепочки не вычисляется — причина словами."""


def _extension_minutes(value, name, rule_id) -> int:
    if type(value) is not int or not 0 < value <= 2147483647:
        raise ChainPriceError(
            "правило продления %s: %s должен быть целым числом минут > 0 "
            "в диапазоне Int; получено %r" % (rule_id, name, value))
    return value


def extension_alias_params(rule: dict) -> dict:
    """Читает оба существующих носителя alias, не выбирая молча один.

    Исходные ячейки остаются неизменны. Здесь не создаётся HARD-база;
    равенство базе проверяется только потребителем, который её знает.
    """
    rid = rule.get("id")
    fit = rule.get("template_fit") or {}
    if not isinstance(fit, dict):
        raise ChainPriceError("правило продления %s: long_duty_threshold — некорректная посадка" % rid)
    if "schema_version" in fit:
        from webapp.threshold_gate import ThresholdGateError, projected_params
        try:
            projected = projected_params(rule)
        except ThresholdGateError as exc:
            raise ChainPriceError(str(exc)) from exc
        # This executor has one unconditional base. A valid conditional cell
        # cannot be consumed as that base by dropping its scope.
        if any(p.get("name") == "long_duty_threshold" and
               (p.get("condition") is not None or p.get("qualifier") is not None)
               for p in projected):
            raise ChainPriceError("правило продления %s: условный long_duty_threshold "
                                  "нельзя читать как общую базу" % rid)
    params = fit.get("params") or {}
    if not isinstance(params, dict) or not isinstance(rule.get("params") or [], list):
        raise ChainPriceError("правило продления %s: long_duty_threshold — некорректный носитель" % rid)
    cells = [params["long_duty_threshold"]] if "long_duty_threshold" in params else []
    cells += [cell for cell in (rule.get("params") or [])
              if isinstance(cell, dict) and cell.get("name") == "long_duty_threshold"]
    aliases = []
    for cell in cells:
        if (not isinstance(cell, dict) or "pending_revision" in cell
                or cell.get("status") == "pending"):
            raise ChainPriceError("правило продления %s: long_duty_threshold — "
                                  "неполная ячейка или открытый пересмотр" % rid)
        aliases.append(_extension_minutes(cell.get("value"), "long_duty_threshold", rid))
    if len(set(aliases)) > 1:
        raise ChainPriceError("правило продления %s: носители long_duty_threshold "
                              "расходятся: %s" % (rid, aliases))
    return {"long_duty_threshold": aliases[0]} if aliases else {}


def extension_threshold(base, params: dict, rule_id=None) -> int:
    """Порог штрафа — применённая HARD-база, alias не отдельная настройка.

    Отсутствующий alias допустим; объявленный обязан точно совпадать с
    базой. Не заимствует provider default при отсутствующей базе.
    """
    # Applied HARD thresholds historically allow an exactly integral numeric
    # value; the alias follows the package loader's integer-cell contract.
    if type(base) is float and math.isfinite(base) and base.is_integer():
        base = int(base)
    threshold = _extension_minutes(base, "базовый предел max_duty_duration", rule_id)
    if "long_duty_threshold" in params:
        alias = _extension_minutes(params["long_duty_threshold"], "long_duty_threshold", rule_id)
        if alias != threshold:
            raise ChainPriceError(
                "правило продления %s: long_duty_threshold=%s не совпадает "
                "с базовым пределом max_duty_duration=%s"
                % (rule_id, alias, threshold))
    return threshold


def unit_words(cat=None) -> dict:
    """Единицы нарушения словами: {код единицы: русское слово}.

    НОСИТЕЛЬ ОДИН — словарь каталога `violation_units`, который с
    выпуска 0.5.0 сам стал картой «код → слово». Рукописной карты
    перевода в COSMOS больше нет: прежний `UNIT_WORDS` был вторым
    носителем того же знания — новый код единицы приходилось дописывать
    в двух местах, и перевод расходился со словарём молча.

    Каталог недоступен, словарь не карта либо у кода пусто слово —
    отказ словами: единицу называют словом, а не подгоняют под число.
    `cat` — экземпляр, который зовущий уже прочитал: обход двери стоит
    около 0,2 мс, и цена цепочки читает карту ОДИН раз на цепочку, а не
    на каждое мягкое правило.
    """
    global _UNITS_CACHE
    if cat is None:
        from vmp import catalog_access as ca
        try:
            cat = ca.current()
        except ca.CATALOG_ERRORS as e:
            raise ChainPriceError(
                "словарь единиц нарушения не прочитан: %s" % e) from e
    hit = _UNITS_CACHE
    if hit is not None and hit[0] is cat:
        return dict(hit[1])
    try:
        vu = cat.dictionaries().get("violation_units")
    except Exception as e:                               # noqa: BLE001
        raise ChainPriceError(
            "словарь единиц нарушения не прочитан: %s" % e) from e
    if not isinstance(vu, dict) or not vu:
        raise ChainPriceError(
            "словарь единиц нарушения каталога — не карта «код → слово» "
            "(дано %s): слово единицы брать негде, а второй карты "
            "перевода в COSMOS нет" % type(vu).__name__)
    out: dict = {}
    for code, word in vu.items():
        if not (isinstance(word, str) and word.strip()):
            raise ChainPriceError(
                "единица нарушения %r объявлена каталогом без слова: "
                "единицу называют словом, а не подгоняют под число"
                % (code,))
        out[str(code)] = word.strip()
    _UNITS_CACHE = (cat, out)
    return dict(out)


def soft_families() -> dict:
    """Мягкие семьи части «Пейринг»: {семья: единица меры словом}.

    НОСИТЕЛЬ — запись каталога шаблонов: `kind == "SOFT"` и зона записи
    `PAIRING` либо `BOTH`; единица — код `violation_unit` записи,
    переведённый в слово картой каталога (`unit_words`). Второго
    перечня в COSMOS нет:
    ведомость исполнения (`vmp.execution_manifest`), транслятор
    построителя (`vmp.pairing_input`) и сама цена спрашивают отсюда.

    Свидетель со стороны решателя — таблица
    `kotlin_tables.family_to_constraint()`; закон паритета L14 держит
    равенство «мягкие семьи каталога ≡ семьям решателя», поэтому цена
    читает ОДИН носитель, а не пересечение двух, и сторож перечней
    проверяет это равенство отдельно.

    Порядок — каталожный (по идентификатору): перечень строк и отказов
    не должен зависеть от порядка обхода. Каталог недоступен либо
    единица семьи не переведена — отказ словами, не пустой перечень:
    молчащий перечень и есть класс ошибки 09.09.2026.
    """
    global _SOFT_CACHE
    from vmp import catalog_access as ca
    try:
        cat = ca.current()
    except ca.CATALOG_ERRORS as e:
        raise ChainPriceError(
            "перечень мягких семей части «Пейринг» не прочитан: %s" % e) from e
    hit = _SOFT_CACHE
    if hit is not None and hit[0] is cat:
        return dict(hit[1])
    words = unit_words(cat)
    out: dict = {}
    for fid, meta in sorted(cat.families_meta().items()):
        if meta.get("kind") != "SOFT" \
                or meta.get("zone") not in _PAIRING_ZONES:
            continue
        word = words.get(meta.get("violation_unit"))
        if word is None:
            raise ChainPriceError(
                "мягкая семья %s каталога объявлена с единицей нарушения "
                "%r — слова для неё у цены цепочки нет; единицу называют "
                "словом, а не подгоняют под число"
                % (fid, meta.get("violation_unit")))
        out[fid] = word
    _SOFT_CACHE = (cat, out)
    return dict(out)


def __getattr__(name: str):
    """Прежние имена перечня (`UNITS`, `FAMILIES`) — значением носителя.

    Модуль их НЕ ХРАНИТ: замороженная при импорте копия разошлась бы с
    каталогом молча, а именно это и чинится. Внутри модуля перечень
    зовут функцией — обращение к глобальному имени сюда не приходит.
    """
    if name == "UNITS":
        return soft_families()
    if name == "FAMILIES":
        return tuple(soft_families())
    if name == "UNIT_WORDS":
        # Прежнее имя карты единиц — ЗНАЧЕНИЕМ КАТАЛОГА (0.5.0): рукописной
        # карты в модуле нет, и замороженная при импорте копия разошлась
        # бы со словарём молча — ровно то, что выпуск и чинил.
        return unit_words()
    raise AttributeError(
        "модуль %s не несёт имени %r" % (__name__, name))


@dataclass(frozen=True)
class Period:
    """Период цепочки: границы по перелётам, место отдыха после него."""
    start: datetime
    end: datetime
    rest_at: str
    fns: tuple[str, ...] = ()


def weekday_set(days: Iterable) -> set[int]:
    """Дни окна: имена (en/ru) либо числа 0..6 (понедельник = 0);
    неизвестное имя — отказ."""
    out: set[int] = set()
    for d in days or ():
        if isinstance(d, bool):
            raise ChainPriceError("день окна не число и не имя: %r" % (d,))
        if isinstance(d, int):
            if not 0 <= d <= 6:
                raise ChainPriceError("день окна вне 0..6: %r" % (d,))
            out.add(d)
            continue
        key = str(d).strip().lower()
        if key.isdigit() and 0 <= int(key) <= 6:
            out.add(int(key))
        elif key in _WEEKDAYS:
            out.add(_WEEKDAYS[key])
        else:
            raise ChainPriceError("день окна не распознан: %r" % (d,))
    return out


# ---------------------------------------------------------------------------
# Область учёта (контракт Б)
# ---------------------------------------------------------------------------

#: Виды (причины) неопределённого вклада в цену цепочки. Имена main
#: (rest_at_base, open_end) остаются первой причиной события — их
#: читают сертификат пейринга и сводка каталога (by_kind).
UNCERTAIN_REST_AT_BASE = "отдых на базе: приписка экипажа неизвестна"
UNCERTAIN_OPEN_END = "открытый конец: продолжение неизвестно"
UNCERTAIN_NOTES = {
    "rest_at_base": UNCERTAIN_REST_AT_BASE,
    "open_end": UNCERTAIN_OPEN_END,
    "missing_location": "место отдыха не указано: ночь не судится",
    "incomplete_home_universe": ("универсум приписок неполон: станция вне "
                                 "объявленных баз ночёвкой не доказана"),
    "overlapping_periods": "периоды перекрываются: отдых между ними не судится",
}


def scope_template(bases: Iterable[str] = (), tz: timedelta = timedelta(0), *,
                   eligible_bases_complete: bool = False,
                   boundary: dict | None = None) -> dict:
    """Сериализуемая область учёта цены — одна у построителя (в
    config_sig.price_scope), у пересчёта сертификатора и у каждой
    расшифровки (accounting_scope). Базы — универсум приписок (все
    homeAirport сотрудников постановки), не базы поиска/замыкания;
    полнота универсума — утверждение вызывателя, и только при ней
    станция вне универсума доказывает ночёвку. Граница цепочки —
    только «unknown»: производителя доказательств продолжения ни у
    построителя, ни у сертификата нет (строка P-403 единой версии)."""
    if isinstance(bases, (str, bytes)):
        raise ChainPriceError(
            "область учёта: универсум приписок — коллекция кодов станций, "
            "не строка: %r" % (bases,))
    raw = list(bases or ())
    if any(not isinstance(b, str) or not b.strip() for b in raw):
        raise ChainPriceError(
            "область учёта: пустой или нестроковый код станции в универсуме "
            "приписок: %r" % (raw,))
    homes = sorted({b.strip() for b in raw})
    if not isinstance(eligible_bases_complete, bool):
        raise ChainPriceError(
            "область учёта: eligible_bases_complete не булево: %r"
            % (eligible_bases_complete,))
    if eligible_bases_complete and not homes:
        raise ChainPriceError(
            "область учёта: пустой универсум приписок полным быть не может")
    if not isinstance(tz, timedelta):
        raise ChainPriceError("область учёта: сдвиг пояса не timedelta: %r" % (tz,))
    minutes = tz.total_seconds() / 60
    if not float(minutes).is_integer():
        raise ChainPriceError("область учёта: сдвиг пояса не в целых минутах")
    if boundary is not None and (not isinstance(boundary, dict)
                                 or boundary.get("mode") != "unknown"
                                 or set(boundary) != {"mode"}):
        raise ChainPriceError(
            "область учёта: граница цепочки только {mode: unknown} — режимы "
            "границы (продолжение, закрытая область) не поддерживаются: %r"
            % (boundary,))
    return {"version": SCOPE_VERSION, "contract": "B",
            "night_definition": NIGHT_DEFINITION,
            "tz_offset_min": int(minutes),
            "eligible_bases": homes,
            "eligible_bases_complete": eligible_bases_complete,
            "boundary": {"mode": "unknown"}}


def _event_id(period: Period, until: datetime | None, night: str | None) -> str:
    # Одно и то же событие отдыха получает один ключ независимо от того,
    # в какой цепочке лежит следующий период: индекс цепочки в ключ не
    # входит (сверка каталог ↔ пересчёт ↔ сертификат по событиям).
    raw = [list(period.fns), period.end.isoformat(),
           until.isoformat() if until else None, period.rest_at or None, night]
    return hashlib.sha256(json.dumps(raw, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()[:24]


def assess_nights(periods: list[Period], bases: Iterable[str] = (),
                  tz: timedelta = timedelta(0), *,
                  eligible_bases_complete: bool = False,
                  boundary: dict | None = None) -> dict:
    """Известные ночёвки, адресные неопределённые события и точная
    область учёта цепочки: {"price_model", "accounting_scope" (с
    periods), "known_events", "unknown_events", "complete",
    "unknown_count", "nights"}. Событие — {event_id, period, fns, from,
    until, station, night, source[, reasons]}; JSON-совместимо.

    Внутренний промежуток (до следующего периода той же цепочки) даёт
    известную ночь на каждую календарную дату между окончанием и
    началом, только если станция вне ПОЛНОГО универсума приписок; иначе
    — неопределённое событие на каждую такую дату с причиной:
    rest_at_base (станция в универсуме — приписка неизвестна),
    incomplete_home_universe (станция вне неполного универсума),
    missing_location (место отдыха пусто); перекрытие периодов —
    overlapping_periods (отдых между ними не судится). Хвост цепочки
    (период с максимальным окончанием) — open_end: ни ночь, ни ноль.
    unknown_count — число неразрешённых событий, не выдуманное число
    ночей."""
    seq = sorted(periods, key=lambda p: (p.start, p.end))
    scope = scope_template(bases, tz, eligible_bases_complete=eligible_bases_complete,
                           boundary=boundary)
    scope["periods"] = [{"start": p.start.isoformat(), "end": p.end.isoformat(),
                         "rest_at": p.rest_at or None, "fns": list(p.fns)}
                        for p in seq]
    homes = set(scope["eligible_bases"])
    known: list[dict] = []
    unknown: list[dict] = []

    def reasons_at(station: str) -> list[str]:
        if not station:
            return ["missing_location"]
        if station in homes:
            return ["rest_at_base"]
        if not eligible_bases_complete:
            return ["incomplete_home_universe"]
        return []

    def event(i, p, until, night, source, reasons=()):
        row = {"event_id": _event_id(p, until, night), "period": i,
               "fns": list(p.fns), "from": p.end.isoformat(),
               "until": until.isoformat() if until else None,
               "station": p.rest_at or None,
               "date": (p.end + tz).date().isoformat(),
               "night": night, "source": source}
        if reasons:
            row["reasons"] = list(dict.fromkeys(reasons))
        return row

    for i, p in enumerate(seq):
        if not isinstance(p.start, datetime) or not isinstance(p.end, datetime):
            raise ChainPriceError("границы периода не datetime: %r" % (p,))
        if p.end < p.start:
            raise ChainPriceError("период окончен раньше начала: %r" % (p,))
        if i + 1 >= len(seq):
            continue
        nxt = seq[i + 1]
        if nxt.start < p.end:
            # Отдых между перекрывающимися периодами не выдумывается
            # (перегон внутри смены у разметки построителя).
            unknown.append(event(i, p, nxt.start, None, "internal",
                                 ["overlapping_periods"]))
            continue
        end_day = (p.end + tz).date()
        count = ((nxt.start + tz).date() - end_day).days
        for k in range(count):
            night = (end_day + timedelta(days=k)).isoformat()
            reasons = reasons_at(p.rest_at)
            (unknown if reasons else known).append(
                event(i, p, nxt.start, night, "internal", reasons))
    if seq:
        # Хвост — период с максимальным окончанием, не последний по старту.
        i, tail = max(enumerate(seq), key=lambda x: x[1].end)
        reasons = ["open_end"] + ([] if tail.rest_at else ["missing_location"])
        unknown.append(event(i, tail, None, None, "boundary", reasons))
    return {"price_model": VERSION, "accounting_scope": scope,
            "known_events": known, "unknown_events": unknown,
            "complete": not unknown, "unknown_count": len(unknown),
            "nights": len(known)}


def legacy_uncertain(events: Iterable[dict]) -> list[dict]:
    """Свод адресных неопределённых событий в форму main — одна запись
    на (период, вид): {"period", "station", "kind", "date", "note",
    "reasons", "event_ids"[, "nights_if_away"]}; её читают сертификат
    пейринга (число событий) и сводка каталога (by_kind)."""
    out: dict[tuple, dict] = {}
    for e in events or ():
        if not isinstance(e, dict):
            continue
        reasons = list(e.get("reasons") or ["?"])
        kind = str(reasons[0])
        key = (e.get("period"), kind)
        rec = out.get(key)
        if rec is None:
            rec = {"period": e.get("period"), "station": e.get("station") or "",
                   "kind": kind, "date": e.get("date"),
                   "note": UNCERTAIN_NOTES.get(kind, kind),
                   "reasons": reasons, "event_ids": []}
            out[key] = rec
        rec["event_ids"].append(e.get("event_id"))
        if e.get("night") is not None:
            rec["nights_if_away"] = rec.get("nights_if_away", 0) + 1
    return list(out.values())


def layover_nights(periods: list[Period], bases: Iterable[str] = (),
                   tz: timedelta = timedelta(0), *,
                   open_end: bool = False,
                   eligible_bases_complete: bool = False) -> list[dict]:
    """ДОКАЗАННЫЕ внутренние ночёвки цепочки по определению ВМП (контракт
    Б): записи известных событий assess_nights с night как date —
    по одной на календарную ночь между окончанием периода и началом
    СЛЕДУЮЩЕГО периода цепочки, когда место отдыха — станция вне
    ПОЛНОГО универсума приписок (при любой приписке экипажа это
    ночёвка вне базы). Отдых в тот же день ночи не даёт. Отдых на
    объявленной базе, станция вне неполного универсума, пустое место
    отдыха и открытый конец цепочки сюда НЕ входят — это неопределённый
    вклад (uncertain_events / assess_nights), он не объявляется нулём
    и не начисляется автоматически. open_end оставлен для
    совместимости вызывателей: хвост цепочки и без него не начисляется."""
    res = assess_nights(periods, bases, tz,
                        eligible_bases_complete=eligible_bases_complete)
    return [dict(x, night=date.fromisoformat(x["night"]))
            for x in res["known_events"]]


def uncertain_events(periods: list[Period], bases: Iterable[str] = (),
                     tz: timedelta = timedelta(0), *,
                     eligible_bases_complete: bool = False) -> list[dict]:
    """Неопределённый вклад цепочки в форме main (legacy_uncertain):
    события, чью стоимость цепочка в отрыве от назначения людей
    установить не может — отдых на объявленной базе до периода другого
    дня (rest_at_base, nights_if_away), станция вне неполного
    универсума (incomplete_home_universe), пустое место отдыха
    (missing_location), перекрытие периодов (overlapping_periods) и
    открытый конец (open_end). Судится в ростеринге по фактической
    приписке и известной последовательности назначений."""
    res = assess_nights(periods, bases, tz,
                        eligible_bases_complete=eligible_bases_complete)
    return legacy_uncertain(res["unknown_events"])


# ---------------------------------------------------------------------------
# Меры и цена
# ---------------------------------------------------------------------------

def measure(rule: dict, periods: list[Period], bases: Iterable[str] = (),
            tz: timedelta = timedelta(0), *, open_end: bool = False,
            nights: list[dict] | None = None,
            assessment: dict | None = None,
            eligible_bases_complete: bool = False,
            boundary: dict | None = None,
            units: dict | None = None) -> dict:
    """Мера мягкого правила на цепочке: {"family", "unit", "measure",
    "evidence", "unknown_events", "measure_complete"}. Семья без
    исполнителя — отказ словами. Неопределённость — по семьям: у окна
    дней недели неизвестная ночь вне окна (известная дата не в днях
    окна) меру неполной не делает; событие без даты (открытый конец,
    перекрытие) — неполной делает. Переданный список ночей (nights)
    полноты не доказывает — область учёта берётся из assessment либо
    строится заново.

    Семью различают ВЕТВИ исполнителей, а не перечень: мера считается в
    каждой точке ценообразования, и обход двери каталога здесь стоил бы
    дороже самой меры. Равенство «ветви ≡ мягким семьям носителя»
    (soft_families) держит сторож перечней, а не порядок обхода.

    `units` — карта единиц (`unit_words()`), прочитанная зовущим: слово
    единицы живёт в каталоге (0.5.0), а дверь стоит около 0,2 мс, и
    цена цепочки читает карту ОДИН раз на цепочку, а не на каждое
    мягкое правило. Ключа нет — мера читает карту сама."""
    words = unit_words() if units is None else units
    fam = rule.get("family")
    if fam in ("layover_penalty", "calendar_windows"):
        if assessment is None:
            assessment = assess_nights(
                periods, bases, tz, eligible_bases_complete=eligible_bases_complete,
                boundary=boundary)
        known = assessment["known_events"]
        unknown = assessment["unknown_events"]
        if fam == "layover_penalty":
            picked = list(known)
            unresolved = list(unknown)
        else:
            days = weekday_set(rule.get("days") or ())
            if not days:
                raise ChainPriceError(
                    "окно дней недели у правила %s не задано (days)"
                    % rule.get("rule_id"))
            picked = [x for x in known
                      if date.fromisoformat(x["night"]).weekday() in days]
            unresolved = [x for x in unknown if x.get("night") is None
                          or date.fromisoformat(x["night"]).weekday() in days]
        return {"family": fam, "unit": words["night"],
                "measure": len(picked),
                "evidence": picked, "unknown_events": unresolved,
                "measure_complete": not unresolved}
    if fam != "duty_extension_penalty":
        raise ChainPriceError(
            "мягкое правило %s семьи %r не имеет меры у цены цепочки — "
            "его штраф выпал бы из цели молча; композиция целевой "
            "неполна" % (rule.get("rule_id"), fam))
    # duty_extension_penalty: период длиннее базового порога продления
    params = rule.get("params") or {}
    thr = params.get("long_duty_threshold")
    if isinstance(thr, dict):
        thr = thr.get("value")
    if isinstance(thr, bool) or not isinstance(thr, (int, float)):
        raise ChainPriceError(
            "порог продления long_duty_threshold у правила %s не задан"
            % rule.get("rule_id"))
    ev = []
    for i, p in enumerate(periods):
        span = (p.end - p.start).total_seconds() / 60.0
        if span > thr:
            ev.append({"period": i, "span_min": int(span),
                       "threshold_min": thr})
    return {"family": fam, "unit": words["duty"], "measure": len(ev),
            "evidence": ev, "unknown_events": [], "measure_complete": True}


def _weight(rule: dict) -> int:
    w = rule.get("weight")
    if isinstance(w, bool) or not isinstance(w, int) or w < 0:
        raise ChainPriceError(
            "вес мягкого правила %s не целое ≥ 0: %r"
            % (rule.get("rule_id"), w))
    return w


def _finite(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _price_level_unknown(rows: list[dict], crew, criterion_kind) -> list[dict]:
    """Неопределённые события цены — объединение по строкам с
    идентичностью event_id и списком семей. В ветке коэффициентов
    (criterion_kind = weights) строка с нулевым весом цену неполной не
    делает (правила нет — платить нечего); при ставках источника и
    0096 стоянки/ночи живут в самом критерии, и неопределённость хвоста
    остаётся у цены при любом весе (строка P-407)."""
    weighted_only = criterion_kind == "weights"
    unresolved: dict[str, dict] = {}
    for row in rows:
        if not crew or (weighted_only and not row.get("weight")):
            continue
        for ev in row.get("unknown_events") or ():
            rec = unresolved.setdefault(ev["event_id"], dict(ev, families=[]))
            rec["families"].append(row["family"])
    return [dict(v, families=sorted(v["families"])) for v in unresolved.values()]


def price(criterion: float, periods: list[Period], rules: list[dict],
          bases: Iterable[str] = (), tz: timedelta = timedelta(0), crew: int = 1,
          *, open_end: bool = False,
          criterion_kind: str | None = None,
          eligible_bases_complete: bool = False,
          boundary: dict | None = None) -> dict:
    """Цена цепочки = критерий (уже на весь состав) + Σ мера × вес ×
    состав по мягким правилам части — на ОБЛАСТИ УЧЁТА «доказанные
    внутренние события» (контракт Б). Расшифровка: {"price_model",
    "accounting_scope" (+ criterion_kind), "certainty": {status,
    complete, unknown_count}, "known_events", "unknown_events"
    (объединение по строкам, families), "criterion", "criterion_kind",
    "crew", "nights", "soft": [строки], "soft_total", "cost",
    "known_subtotal" (= cost), "full_cost" (None при неполной цене),
    "cost_kind": complete_scope | known_subtotal, "is_lower_bound":
    False, а также форма main: "uncertain" (legacy_uncertain) и
    "cost_certainty": full | partial}. Строка — {rule_id, family, unit,
    measure, weight, crew, contribution, evidence, unknown_events,
    measure_complete}. cost — известная часть: не полная цена (при
    partial) и не нижняя оценка."""
    if isinstance(crew, bool) or not isinstance(crew, int) or crew < 0:
        raise ChainPriceError("численность состава не целое ≥ 0: %r" % (crew,))
    if not _finite(criterion):
        raise ChainPriceError("критерий не конечное число: %r" % (criterion,))
    assessment = assess_nights(periods, bases, tz,
                               eligible_bases_complete=eligible_bases_complete,
                               boundary=boundary)
    assessment["accounting_scope"]["criterion_kind"] = criterion_kind
    # Карта единиц — ОДИН раз на цепочку: слово единицы живёт в каталоге
    # (0.5.0), дверь стоит около 0,2 мс, и читать её на каждое мягкое
    # правило значило бы платить за одно и то же знание вчетверо.
    units = unit_words() if rules else None
    rows: list[dict] = []
    seen: set[str] = set()
    for rule in rules or ():
        fam = rule.get("family")
        if fam in seen:
            raise ChainPriceError(
                "две записи семьи %s в мягких правилах части — какой вес "
                "действует, решает производитель постановки" % fam)
        seen.add(str(fam))
        m = measure(rule, periods, bases, tz, assessment=assessment,
                    units=units)
        w = _weight(rule)
        rows.append({"rule_id": rule.get("rule_id"), "family": fam,
                     "unit": m["unit"], "measure": m["measure"],
                     "weight": w, "crew": crew,
                     "contribution": m["measure"] * w * crew,
                     "evidence": m["evidence"],
                     "unknown_events": m["unknown_events"],
                     "measure_complete": m["measure_complete"]})
    soft_total = sum(r["contribution"] for r in rows)
    cost = criterion + soft_total
    if isinstance(criterion, int) and float(cost).is_integer():
        cost = int(cost)
    unknown = _price_level_unknown(rows, crew, criterion_kind)
    complete = not unknown
    return {"price_model": VERSION,
            "accounting_scope": assessment["accounting_scope"],
            "certainty": {"status": "complete" if complete else "partial",
                          "complete": complete, "unknown_count": len(unknown)},
            "known_events": assessment["known_events"],
            "unknown_events": unknown,
            "criterion": criterion, "criterion_kind": criterion_kind,
            "crew": crew, "nights": assessment["nights"], "soft": rows,
            "soft_total": soft_total, "cost": cost,
            "known_subtotal": cost,
            "full_cost": cost if complete else None,
            "cost_kind": "complete_scope" if complete else "known_subtotal",
            "is_lower_bound": False,
            "uncertain": legacy_uncertain(unknown),
            "cost_certainty": "full" if complete else "partial"}


_BASE_UNSET = object()


def rules_from_weights(*, w_night: int = 0, w_weekend_night: int = 0,
                       weekend_days=(), w_long_duty: int = 0,
                       long_duty_threshold=None,
                       base_duty_threshold=_BASE_UNSET,
                       rule_ids: dict | None = None) -> list[dict]:
    """Мягкие правила части из ключей построителя (--w-night,
    --w-weekend-night/--weekend-days, --w-long-duty/--long-duty-threshold).
    Семья присутствует, когда у неё есть параметры действия: ночёвка —
    всегда (вес 0 = правила нет, но строка честно это показывает), окно
    — при заданных днях, продление — при заданном пороге. rule_ids —
    {семья: id правила ВМП} от транслятора (без них id = None)."""
    ids = rule_ids or {}
    out = [{"rule_id": ids.get("layover_penalty"), "family": "layover_penalty",
            "weight": int(w_night or 0), "params": {}, "days": []}]
    days = list(weekend_days or ())
    if days or w_weekend_night:
        out.append({"rule_id": ids.get("calendar_windows"),
                    "family": "calendar_windows",
                    "weight": int(w_weekend_night or 0), "params": {},
                    "days": days})
    if long_duty_threshold is not None or w_long_duty:
        # Legacy low-level price diagnostics may supply the threshold alone.
        # Package/builder/signature paths always supply the actual HARD base.
        if base_duty_threshold is not _BASE_UNSET:
            alias = ({"long_duty_threshold": long_duty_threshold}
                     if long_duty_threshold is not None else {})
            long_duty_threshold = extension_threshold(
                base_duty_threshold, alias, ids.get("duty_extension_penalty"))
        out.append({"rule_id": ids.get("duty_extension_penalty"),
                    "family": "duty_extension_penalty",
                    "weight": int(w_long_duty or 0),
                    "params": {"long_duty_threshold": long_duty_threshold},
                    "days": []})
    return out


def parse_rule_ids(text) -> dict:
    """`--soft-rule-ids family=id,family=id` → {семья: id}; чужая семья
    или запись без «=» — отказ словами."""
    if text is None or text == "":
        return {}
    if isinstance(text, dict):
        items = list(text.items())
    else:
        items = []
        for part in str(text).split(","):
            part = part.strip()
            if not part:
                continue
            if "=" not in part:
                raise ChainPriceError(
                    "--soft-rule-ids: запись без «=»: %r" % part)
            fam, rid = part.split("=", 1)
            items.append((fam.strip(), rid.strip()))
    out: dict = {}
    known = soft_families()
    for fam, rid in items:
        if fam not in known:
            raise ChainPriceError(
                "--soft-rule-ids: семья %r без меры у цены цепочки" % fam)
        out[fam] = rid
    return out


def periods_from_legs(groups: list[list[dict]]) -> list[Period]:
    """Периоды из групп перелётов [{id, t0, t1, arr}]: границы — первый
    вылет и последний прилёт группы, место отдыха — прилёт последнего
    по времени перелёта. Пустая группа — отказ."""
    out: list[Period] = []
    for g in groups:
        legs = sorted(g, key=lambda l: (l["t0"], l["t1"]))
        if not legs:
            raise ChainPriceError("период без перелётов")
        out.append(Period(start=legs[0]["t0"],
                          end=max(l["t1"] for l in legs),
                          rest_at=str(legs[-1].get("arr") or ""),
                          fns=tuple(str(l["id"]) for l in legs)))
    out.sort(key=lambda p: (p.start, p.end))
    return out


def nights_by_period_dates(end: date, next_start: date | None, *,
                           open_end: bool = False) -> int | None:
    """Число известных переходов даты (та же арифметика, что в
    assess_nights) — для сторон, у которых есть только даты. Нет
    продолжения — None: неизвестно, а не 0 и не 1 (контракт Б)."""
    if next_start is None:
        return None
    return max(0, (next_start - end).days)


# ---------------------------------------------------------------------------
# Подпись каталога → контекст цены (сертификатор)
# ---------------------------------------------------------------------------

def pricing_context_from_signature(sig: dict) -> dict:
    """Объявленный контекст цены СТРОГО из подписи постановки каталога
    (config_sig): {"bases", "tz", "eligible_bases_complete",
    "boundary"} — аргументы price()/scope_template(). Отказ словами:
    чужая или отсутствующая price_model (каталог другой модели цены как
    текущий не пересчитывается — stale/missing price_model),
    отсутствующая или несовместимая область (price_scope: версия,
    контракт, определение ночи), неполный контекст, расхождение пояса
    подписи и области, неканоническая область (скрытые поля).
    Универсум приписок из строки --bases НЕ выводится. Подпись — не
    доказательство полноты универсума в источнике: проверяющий сверяет
    объявленный контекст со своим чтением пакета."""
    if not isinstance(sig, dict) or not sig.get("price_model"):
        raise ChainPriceError(
            "подпись без модели цены (missing price_model): пересчёт по "
            "контракту Б невозможен")
    if sig.get("price_model") != VERSION:
        raise ChainPriceError(
            "устаревшая модель цены в подписи (stale price_model %r ≠ %s): "
            "каталог требует переоценки, как текущий не пересчитывается"
            % (sig.get("price_model"), VERSION))
    scope = sig.get("price_scope")
    if (not isinstance(scope, dict) or scope.get("version") != SCOPE_VERSION
            or scope.get("contract") != "B"
            or scope.get("night_definition") != NIGHT_DEFINITION):
        raise ChainPriceError(
            "подпись без области учёта цены либо область несовместима "
            "(price_scope: версия %s, контракт B, определение ночи %s)"
            % (SCOPE_VERSION, NIGHT_DEFINITION))
    required = ("tz_offset_min", "eligible_bases", "eligible_bases_complete",
                "boundary")
    absent = [k for k in required if k not in scope]
    if absent:
        raise ChainPriceError(
            "область учёта цены (price_scope) неполна: нет %s" % ", ".join(absent))
    offset = scope["tz_offset_min"]
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise ChainPriceError(
            "сдвиг пояса области учёта (price_scope.tz_offset_min) не целое: %r"
            % (offset,))
    if "tz_offset_min" in sig and sig["tz_offset_min"] != offset:
        raise ChainPriceError(
            "пояс области учёта цены %s ≠ поясу подписи прогона %s"
            % (offset, sig["tz_offset_min"]))
    if not isinstance(scope["eligible_bases"], (list, tuple, set)):
        raise ChainPriceError(
            "универсум приписок области учёта (price_scope.eligible_bases) не список")
    ctx = {"bases": list(scope["eligible_bases"]),
           "tz": timedelta(minutes=offset),
           "eligible_bases_complete": scope["eligible_bases_complete"],
           "boundary": scope["boundary"]}
    if scope_template(**ctx) != scope:
        raise ChainPriceError(
            "область учёта цены (price_scope) неканонична или несёт скрытые поля")
    return ctx


def soft_rules_from_signature(sig: dict) -> list[dict]:
    """Мягкие правила части из подписи постановки каталога
    (config_sig построителя) — для НЕЗАВИСИМОГО пересчёта мер
    сертификатором: та же композиция, что у построителя, но входы —
    из подписи, а не из чисел цепочек. Подпись обязана быть текущей
    модели с областью учёта (pricing_context_from_signature — отказ
    словами иначе). При ставках источника (price_criterion =
    source_rates) ночёвка оплачена критерием — вес семьи
    layover_penalty ноль, как и у построителя. Ветка берётся из
    price_criterion подписи, не из флага source_cost: флаг ключа не
    равен действующей ветке (ставок источника может не быть). Id
    правил ВМП строк — те же, что у каталога (soft_rule_ids)."""
    pricing_context_from_signature(sig)
    source = sig.get("price_criterion") == "source_rates"
    return rules_from_weights(
        w_night=0 if source else int(sig.get("w_night") or 0),
        w_weekend_night=int(sig.get("w_weekend_night") or 0),
        weekend_days=sig.get("weekend_days") or (),
        w_long_duty=int(sig.get("w_long_duty") or 0),
        long_duty_threshold=sig.get("long_duty_threshold"),
        base_duty_threshold=sig.get("duty_span"),
        rule_ids=parse_rule_ids(sig.get("soft_rule_ids")))


# ---------------------------------------------------------------------------
# Сверка расшифровки с независимым пересчётом
# ---------------------------------------------------------------------------

def comparison_record(pr: dict) -> dict:
    """Запись независимого пересчёта для сверки — всё, кроме суммы
    критерия: модель, ночи, область учёта, определённость, события
    (известные и неопределённые), вид цены и по семьям — меры, веса,
    evidence, неопределённые события строки и её полнота. Проверяющий
    считает price(0, …) по исходным периодам и подписи и отдаёт эту
    запись; копия из каталога независимой не является. Поле
    "uncertain" — число неопределённых событий цены (читатель —
    сертификат пейринга)."""
    rows = [r for r in (pr.get("soft") or []) if isinstance(r, dict)]
    return {"price_model": pr.get("price_model"), "nights": pr.get("nights"),
            "accounting_scope": pr.get("accounting_scope"),
            "certainty": pr.get("certainty"),
            "known_events": pr.get("known_events"),
            "unknown_events": pr.get("unknown_events"),
            "cost_kind": pr.get("cost_kind"),
            "uncertain": (pr.get("certainty") or {}).get("unknown_count"),
            "measures": {r.get("family"): r.get("measure") for r in rows},
            "weights": {r.get("family"): r.get("weight") for r in rows},
            # id правил ВМП строк — те же у каталога и пересчёта (P-411)
            "rule_ids": {r.get("family"): r.get("rule_id") for r in rows},
            "evidence": {r.get("family"): r.get("evidence") for r in rows},
            "row_unknown_events": {r.get("family"): r.get("unknown_events")
                                   for r in rows},
            "row_complete": {r.get("family"): r.get("measure_complete")
                             for r in rows}}


_RECORD_KEYS = ("price_model", "accounting_scope", "certainty", "known_events",
                "unknown_events", "cost_kind", "weights", "rule_ids", "evidence",
                "row_unknown_events", "row_complete")


def arithmetic_problems(price: dict, *, tol: float = 1e-6) -> list[str]:
    """Арифметика расшифровки (любая модель цены): вклад = мера × вес ×
    состав, цена = критерий + Σ вкладов; числа конечны."""
    problems: list[str] = []
    rows = price.get("soft")
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        return ["расшифровка цены без строк мягких правил"]
    crew = price.get("crew")
    total = 0
    for r in rows:
        fam = r.get("family")
        m, w, c = r.get("measure"), r.get("weight"), r.get("contribution")
        if not all(_finite(x) for x in (m, w, c)):
            problems.append("семья %s: мера/вес/вклад не конечные числа" % fam)
            continue
        if _finite(crew) and abs(m * w * crew - c) > tol:
            problems.append("семья %s: вклад %s ≠ мера %s × вес %s × состав %s"
                            % (fam, c, m, w, crew))
        total += c
    crit, cost = price.get("criterion"), price.get("cost")
    if not _finite(crit) or not _finite(cost):
        problems.append("цена/критерий не конечное число")
    elif abs(crit + total - cost) > tol:
        problems.append("цена %s ≠ критерий %s + Σ вкладов %s" % (cost, crit, total))
    return problems


def numeric_comparison(price: dict, recomputed: dict | None, *,
                       tol: float = 1e-6) -> list[str]:
    """Числовая сверка с пересчётом формы {nights, measures: {семья:
    мера}} — принимается, но области учёта и событий не доказывает
    (см. reconcile_pairing: без записи области — несравнимо)."""
    problems: list[str] = []
    if not isinstance(recomputed, dict):
        return problems
    meas = recomputed.get("measures")
    if isinstance(meas, dict):
        for r in (price.get("soft") or ()):
            if not isinstance(r, dict):
                continue
            fam, m = r.get("family"), r.get("measure")
            if fam in meas and _finite(m) and _finite(meas[fam]) \
                    and abs(meas[fam] - m) > tol:
                problems.append("семья %s: мера каталога %s, пересчёт %s"
                                % (fam, m, meas[fam]))
    if "nights" in recomputed and isinstance(price.get("nights"), int) \
            and recomputed["nights"] != price["nights"]:
        problems.append("ночёвок в каталоге %s, пересчёт %s"
                        % (price["nights"], recomputed["nights"]))
    return problems


def compare_rows(price: dict, recomputed: dict | None = None, *,
                 tol: float = 1e-6) -> list[str]:
    """Сверка расшифровки цены цепочки — самопроверка и сверка с
    независимым пересчётом. Возвращает список расхождений словами
    (пустой — сошлось).

    Самопроверка (текущая модель): арифметика (arithmetic_problems),
    область учёта текущей версии, состав — целое ≥ 0, семьи без дублей
    и чужих, меры/веса неотрицательны, длина evidence = мере,
    неопределённые события строки присутствуют и measure_complete им
    соответствует, неопределённые события цены = собранные из строк
    (подделка «скрытая неопределённость → полная цена» ловится),
    certainty / cost_kind / known_subtotal / full_cost / is_lower_bound
    согласованы, nights = число известных событий. Расшифровка чужой
    модели — расхождение «устаревшая модель» плюс арифметика.

    Сверка с пересчётом: запись comparison_record (или расшифровка
    price(0, …), из которой она берётся) сверяется по области учёта,
    событиям, определённости, весам, evidence и мерам по семьям;
    числовая форма {nights, measures} сверяется числами (полноту
    области она не доказывает — это судит reconcile_pairing). Без
    пересчёта — только самопроверка."""
    problems: list[str] = []
    stale = price.get("price_model") != VERSION
    if stale:
        problems.append("устаревшая или отсутствующая модель цены (%r ≠ %s)"
                        % (price.get("price_model"), VERSION))
    problems += arithmetic_problems(price, tol=tol)
    if stale:
        return problems + numeric_comparison(price, recomputed, tol=tol)
    scope = price.get("accounting_scope")
    if not isinstance(scope, dict) or scope.get("version") != SCOPE_VERSION:
        problems.append("область учёта цены (accounting_scope) отсутствует или устарела")
    rows = price.get("soft")
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        return problems
    crew = price.get("crew")
    if isinstance(crew, bool) or not isinstance(crew, int) or crew < 0:
        problems.append("состав цены не целое неотрицательное число")
    families: set = set()
    known = soft_families()
    weighted_only = price.get("criterion_kind") == "weights"
    expected_unknown: dict[str, dict] = {}
    for row in rows:
        fam = row.get("family")
        if fam in families or fam not in known:
            problems.append("семья %s дублируется или неизвестна" % fam)
        families.add(fam)
        m, w = row.get("measure"), row.get("weight")
        if not _finite(m) or not _finite(w):
            continue
        if m < 0 or w < 0:
            problems.append("семья %s: отрицательная мера или вес" % fam)
        evidence, unknown = row.get("evidence"), row.get("unknown_events")
        if not isinstance(evidence, list) or len(evidence) != m:
            problems.append("семья %s: мера не совпадает с адресами событий (evidence)" % fam)
        if not isinstance(unknown, list):
            problems.append("семья %s: неопределённые события строки скрыты" % fam)
            continue
        if row.get("measure_complete") is not (not unknown):
            problems.append("семья %s: ложная полнота меры (measure_complete)" % fam)
        if crew and (w or not weighted_only):
            for ev in unknown:
                if not isinstance(ev, dict) or not ev.get("event_id") or not ev.get("reasons"):
                    problems.append("семья %s: неопределённое событие без адреса/причины" % fam)
                    continue
                entry = expected_unknown.setdefault(ev["event_id"], dict(ev, families=[]))
                entry["families"].append(fam)
    expected = [dict(e, families=sorted(e["families"])) for e in expected_unknown.values()]
    if price.get("unknown_events") != expected:
        problems.append("неопределённые вклады цены скрыты или не совпадают со строками")
    complete = not expected
    if (price.get("certainty") or {}) != {"status": "complete" if complete else "partial",
                                          "complete": complete,
                                          "unknown_count": len(expected)}:
        problems.append("ложный статус определённости цены (certainty)")
    if price.get("cost_certainty") != ("full" if complete else "partial"):
        problems.append("ложный статус определённости цены (cost_certainty)")
    if price.get("cost_kind") != ("complete_scope" if complete else "known_subtotal"):
        problems.append("известная часть (known_subtotal) выдана за полную цену")
    cost = price.get("cost")
    if price.get("known_subtotal") != cost or \
            price.get("full_cost") != (cost if complete else None):
        problems.append("known_subtotal/full_cost не соответствуют области учёта")
    if price.get("is_lower_bound") is not False:
        problems.append("необоснованное объявление нижней оценки")
    if not isinstance(price.get("known_events"), list) or \
            price.get("nights") != len(price["known_events"]):
        problems.append("число известных ночёвок не совпадает с событиями")
    if recomputed is None:
        return problems
    if not isinstance(recomputed, dict):
        return problems + ["пересчёт не запись сверки: %r" % (type(recomputed).__name__,)]
    if "accounting_scope" not in recomputed and "soft" not in recomputed:
        return problems + numeric_comparison(price, recomputed, tol=tol)
    rec = comparison_record(recomputed) if "soft" in recomputed else recomputed
    own = comparison_record(price)
    for key in _RECORD_KEYS:
        if key not in rec or rec[key] != own[key]:
            problems.append("пересчёт: не совпадает %s (область/события/определённость)" % key)
    measures = rec.get("measures")
    if not isinstance(measures, dict):
        problems.append("пересчёт без адресной карты мер")
        measures = {}
    if measures != own["measures"]:
        for fam in sorted(families | set(measures), key=str):
            a, b = own["measures"].get(fam), measures.get(fam)
            if a != b:
                problems.append("семья %s: мера каталога %s, пересчёт %s" % (fam, a, b))
    if rec.get("nights") != price.get("nights"):
        problems.append("ночёвок в каталоге %s, пересчёт %s"
                        % (price.get("nights"), rec.get("nights")))
    return problems


def aggregate_certainty(prices: Iterable[dict]) -> dict:
    """Сводка определённости цены каталога по расшифровкам выбранных
    цепочек — одна у построителя (каталог и отчёт) и у сертификатора
    (проверка сводки): чужая/отсутствующая модель у цепочки —
    неизвестно (missing_or_stale_price_scope), несогласованная
    определённость — inconsistent_price_certainty, иначе накопление
    адресных неопределённых событий с индексом цепочки. Поля main
    сохранены: certainty full|partial, chains_with_uncertain,
    uncertain_events (число событий формы main), by_kind, reading;
    новые: unknown_count/unknown_events (адресные), known_subtotal,
    full_cost (None при partial), cost_kind, complete_chains,
    scope_fingerprint (sha256 областей учёта)."""
    items = list(prices)
    unknown: list[dict] = []
    scopes: list = []
    subtotal: float | int | None = 0
    complete_count = 0
    chains_with_uncertain = 0
    legacy_events = 0
    kinds: dict[str, int] = {}
    for i, pr in enumerate(items):
        if not isinstance(pr, dict) or pr.get("price_model") != VERSION:
            unknown.append({"chain": i, "reasons": ["missing_or_stale_price_scope"]})
            chains_with_uncertain += 1
            subtotal = None
            continue
        scopes.append(pr.get("accounting_scope"))
        if subtotal is not None:
            subtotal = subtotal + pr["cost"] if _finite(pr.get("cost")) else None
        events = pr.get("unknown_events")
        complete = (pr.get("certainty") or {}).get("complete") is True
        if (not isinstance(events, list) or any(not isinstance(e, dict) for e in events)
                or complete != (not events)):
            unknown.append({"chain": i, "reasons": ["inconsistent_price_certainty"]})
            chains_with_uncertain += 1
        elif complete:
            complete_count += 1
        else:
            chains_with_uncertain += 1
            unknown.extend(dict(e, chain=i) for e in events)
        for e in (pr.get("uncertain") or ()):
            legacy_events += 1
            k = str((e or {}).get("kind") or "?")
            kinds[k] = kinds.get(k, 0) + 1
    complete = bool(items) and not unknown and subtotal is not None
    fp = hashlib.sha256(json.dumps(scopes, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"price_model": VERSION, "scope_version": SCOPE_VERSION,
            "night_definition": NIGHT_DEFINITION,
            "certainty": "full" if complete else "partial",
            "status": "complete" if complete else "partial", "complete": complete,
            "chains": len(items), "complete_chains": complete_count,
            "chains_with_uncertain": chains_with_uncertain,
            "uncertain_events": legacy_events, "by_kind": kinds,
            "unknown_count": len(unknown), "unknown_events": unknown,
            "scope_fingerprint": fp, "known_subtotal": subtotal,
            "full_cost": subtotal if complete else None,
            "cost_kind": "complete_scope" if complete else "known_subtotal",
            "reading": ("цена каталога = критерий + доказанные внутренние ночёвки "
                        "по определению ВМП на объявленной области учёта; отдых "
                        "на базе, станция вне неполного универсума приписок и "
                        "открытый конец — неопределённый вклад, судится в "
                        "ростеринге по фактической приписке; при неопределённом "
                        "вкладе цена — известная часть, не полная цена и не "
                        "нижняя оценка")}
