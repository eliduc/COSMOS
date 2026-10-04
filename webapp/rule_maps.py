"""Чтение секции rule_maps пакета постановки (ревизия 1.14, пакет Б).

Секция rule_maps — ключ верхнего уровня manifest.json пакета: карты
«правило свода → исполнитель», собранные стороной CDPM из носителя её
контура (cdpm-rule-maps.json). Закон потребителя: пакет С подсекцией —
значения читаются ИЗ ПАКЕТА; пакета БЕЗ секции или без нужной
подсекции (в том числе пакеты спецификации до 1.14) — законная
ЛЕГАСИ-ветка: прежние зашитые таблицы кода потребителя. Судится
НАЛИЧИЕ ПОДСЕКЦИИ: сборщик копирует из носителя только имеющиеся
подсекции, и их состав от пакета к пакету законно разный.

Кривая форма — отказ словами, а не догадка: каждая функция принимает
тип ошибки вызывающего (error=...), чтобы отказ шёл его законом
(TaskPackageRefused, WayCompareError, SystemExit инструмента, ...).

Никаких зашитых координат здесь нет и быть не может: значения — только
из manifest["rule_maps"] переданного манифеста.
"""
from __future__ import annotations


class RuleMapsError(ValueError):
    """Секция rule_maps не читается — отказ, не догадка."""


#: Ключи подсекции acceptance_rules (правила приёмки порождённых форм):
#: days_off — список правил о минимуме выходных, остальные — по одному
#: правилу на форму. Перечень закрыт: чужой ключ — кривая форма.
ACCEPTANCE_KEYS = ("days_off", "consecutive", "rest", "rolling", "night")


def _section(manifest: dict, error) -> dict | None:
    """Секция rule_maps манифеста; нет секции — None (легаси-ветка)."""
    maps = (manifest or {}).get("rule_maps")
    if maps is None:
        return None
    if not isinstance(maps, dict) or not maps:
        raise error(
            "секция rule_maps манифеста пуста или не объект — форма "
            "нарушена: %r" % (maps,))
    return maps


def crew_size_rules(manifest: dict, error=RuleMapsError):
    """Правила-носители численности экипажа из карты пакета.

    Возвращает кортеж идентификаторов правил либо None (секции или
    подсекции нет — легаси-ветка вызывающего). Кривая форма — отказ
    словами типом error."""
    maps = _section(manifest, error)
    if maps is None or "crew_size_rules" not in maps:
        return None
    got = maps["crew_size_rules"]
    if not isinstance(got, list) or not got:
        raise error(
            "rule_maps.crew_size_rules: ожидается непустой список "
            "идентификаторов правил, получено %r" % (got,))
    out = []
    for x in got:
        if not isinstance(x, str) or not x.strip():
            raise error(
                "rule_maps.crew_size_rules: идентификатор правила не "
                "непустая строка: %r" % (x,))
        out.append(x.strip())
    return tuple(out)


def zone_executors(manifest: dict, error=RuleMapsError):
    """Словарь исполнителей зоны pairing из карты пакета.

    Возвращает {id правила: {"constraint": имя ограничения,
    "literal": целое > 0}} либо None (легаси-ветка). Кривая форма —
    отказ словами типом error."""
    maps = _section(manifest, error)
    if maps is None or "zone_executors" not in maps:
        return None
    got = maps["zone_executors"]
    if not isinstance(got, dict) or not got:
        raise error(
            "rule_maps.zone_executors: ожидается непустой объект "
            "{правило: {constraint, literal}}, получено %r" % (got,))
    out: dict[str, dict] = {}
    for rid, entry in got.items():
        if not isinstance(rid, str) or not rid.strip():
            raise error(
                "rule_maps.zone_executors: идентификатор правила не "
                "непустая строка: %r" % (rid,))
        if not isinstance(entry, dict):
            raise error(
                "rule_maps.zone_executors[%s]: запись не объект: %r"
                % (rid, entry))
        constraint = entry.get("constraint")
        literal = entry.get("literal")
        if not isinstance(constraint, str) or not constraint.strip():
            raise error(
                "rule_maps.zone_executors[%s]: имя ограничения "
                "(constraint) не непустая строка: %r" % (rid, constraint))
        if (not isinstance(literal, int) or isinstance(literal, bool)
                or literal <= 0):
            raise error(
                "rule_maps.zone_executors[%s]: литеральный вес (literal) "
                "не целое > 0: %r" % (rid, literal))
        out[rid.strip()] = {"constraint": constraint, "literal": literal}
    return out


def acceptance_rules(manifest: dict, error=RuleMapsError):
    """Правила приёмки порождённых форм из карты пакета.

    Возвращает {"days_off": кортеж id, "consecutive": id, "rest": id,
    "rolling": id, "night": id} либо None (легаси-ветка). Перечень
    ключей закрыт; кривая форма — отказ словами типом error."""
    maps = _section(manifest, error)
    if maps is None or "acceptance_rules" not in maps:
        return None
    got = maps["acceptance_rules"]
    if not isinstance(got, dict):
        raise error(
            "rule_maps.acceptance_rules: ожидается объект с ключами %s, "
            "получено %r" % (", ".join(ACCEPTANCE_KEYS), got))
    unknown = sorted(set(got) - set(ACCEPTANCE_KEYS))
    if unknown:
        raise error(
            "rule_maps.acceptance_rules: неизвестные ключи %s — перечень "
            "закрыт: %s" % (", ".join(map(repr, unknown)),
                            ", ".join(ACCEPTANCE_KEYS)))
    missing = [k for k in ACCEPTANCE_KEYS if k not in got]
    if missing:
        raise error(
            "rule_maps.acceptance_rules: нет ключей %s — подсекция "
            "обязана нести все правила приёмки" % ", ".join(missing))
    days = got["days_off"]
    if (not isinstance(days, list) or not days
            or not all(isinstance(x, str) and x.strip() for x in days)):
        raise error(
            "rule_maps.acceptance_rules.days_off: ожидается непустой "
            "список непустых строк, получено %r" % (days,))
    out: dict = {"days_off": tuple(x.strip() for x in days)}
    for k in ACCEPTANCE_KEYS[1:]:
        v = got[k]
        if not isinstance(v, str) or not v.strip():
            raise error(
                "rule_maps.acceptance_rules.%s: ожидается непустая "
                "строка, получено %r" % (k, v))
        out[k] = v.strip()
    return out
