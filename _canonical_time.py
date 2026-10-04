"""Единый разбор канонического времени UTC.

Контракт канона: наивный ISO с секундами, значение уже в UTC. Прежде
каждый читатель делал `fromisoformat(str(v)[:19])` — молча отрезал
смещение зоны и дробные секунды, и строка `10:00+02:00` превращалась в
`10:00` вместо `08:00` (внешний аудит R6, P0-03). Теперь строка со
смещением пересчитывается в UTC честно, а мусор и дробные секунды —
громкая ошибка, не тихий сдвиг рейса.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# Поля рейса, объявленные схемой как метки времени
# (`webapp/schema/canonical_payload.schema.json`, строки 84-89). Перечень
# ведётся ПО СХЕМЕ: добавление метки в схему без добавления сюда обязано
# ломать тест, а не проходить молча.
FLIGHT_TIME_FIELDS = ("departureUTCDateTime", "arrivalUTCDateTime")

# Хранимая форма: наивный ISO с секундами. Смещение и «Z» допустимы на
# ПРИЁМЕ и запрещены в ХРАНЕНИИ.


class NonCanonicalPayload(ValueError):
    """Набор содержит метку времени, не приведённую к наивному UTC.

    Отдельный тип нужен, чтобы граница расчёта могла отличить «данные не
    канонизованы» от любой другой ошибки разбора и отказать внятно.
    """


def parse_utc(value) -> datetime:
    """Каноническая строка -> наивный datetime в UTC.

    Принимает: `YYYY-MM-DDTHH:MM:SS` (уже UTC) и ту же форму со смещением
    (`...+02:00`, `...Z`) — смещение пересчитывается, не отбрасывается.
    Отвергает: не-ISO мусор и дробные секунды (в каноне их нет; молча
    округлять — значит подменять вход).
    """
    s = str(value)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(
            f"время не в каноническом ISO (UTC): {s!r}") from exc
    if dt.microsecond:
        raise ValueError(
            f"дробные секунды вне канона (обрезать их молча нельзя): {s!r}")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def canonical_str(value) -> str:
    """Каноническая ХРАНИМАЯ форма метки: наивный UTC, секунды, без зоны."""
    return parse_utc(value).isoformat(timespec="seconds")


def is_canonical(value) -> bool:
    """Метка уже в хранимой форме?

    Контракт ТОЧНЫЙ (аудит R10, P1-01): `v == canonical_str(v)` — разбор
    плюс побайтовое совпадение с пересериализацией. Прежний предикат
    проверял только хвост `Z|±HH:MM` и принимал `+0300`, `+05:30:15`,
    дробные секунды и вообще мусор: «канонической» объявлялась любая
    строка, которую хвостовая регулярка не узнала. Ошибка разбора —
    честное False, не исключение: вопрос задан о форме, а не о
    допустимости.
    """
    if not isinstance(value, str):
        return False
    try:
        return value == canonical_str(value)
    except (ValueError, NonCanonicalPayload):
        return False


def canonicalize_payload(payload) -> int:
    """Привести КАЖДУЮ объявленную метку набора к наивному UTC, на месте.

    Возвращает число переписанных меток.

    Здесь нет предиката «а есть ли в строке плюс или зет». Именно предикат
    был дефектом: он не видел отрицательного смещения (`-05:00`), и такая
    метка доезжала до расчёта живой (внешний аудит R9, P0-03). Разбор
    вызывается безусловно и сам решает, что делать.

    Идентификатор участка (`flightLegId`) НЕ трогается намеренно: он
    материализуется как номер рейса плюс метка вылета, но является
    ЛИЧНОСТЬЮ, а не формулой — он переживает перенос рейса по времени, и
    переписывание его здесь порвало бы ссылки слотов на участки.
    """
    changed = 0
    for f in (payload.get("flights") or []):
        if not isinstance(f, dict):
            continue
        for key in FLIGHT_TIME_FIELDS:
            v = f.get(key)
            if not isinstance(v, str):
                continue
            canon = canonical_str(v)
            if canon != v:
                f[key] = canon
                changed += 1
    return changed


def non_canonical_marks(payload) -> list[str]:
    """Перечень меток набора, оставшихся не в хранимой форме.

    Возвращает адреса вида `flights[3].departureUTCDateTime`, чтобы отказ
    называл место, а не только факт.
    """
    bad: list[str] = []
    for i, f in enumerate(payload.get("flights") or []):
        if not isinstance(f, dict):
            continue
        for key in FLIGHT_TIME_FIELDS:
            v = f.get(key)
            if isinstance(v, str) and not is_canonical(v):
                bad.append(f"flights[{i}].{key}={v!r}")
    return bad


def assert_canonical(payload, where: str) -> None:
    """Отказать, если набор несёт метку со смещением.

    Ставится в двух местах: постусловием записи (чтобы нельзя было
    положить на диск неканоническое) и на общем входе расчётного слоя
    (чтобы наборы, записанные МИМО приложения — корневыми скриптами,
    ручным копированием, историческими прогонами — не доходили до
    движков). Одной точки записи для этого недостаточно: восемь из девяти
    базовых наборов реестра пишут корневые скрипты.
    """
    bad = non_canonical_marks(payload)
    if bad:
        raise NonCanonicalPayload(
            f"[{where}] набор не канонизован по времени "
            f"({len(bad)} меток со смещением): " + ", ".join(bad[:5]))
