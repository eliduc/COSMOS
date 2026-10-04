# -*- coding: utf-8 -*-
"""Словарь видов целевой пейринга (приёмная часть COSMOS, перенос ВМП).

Закрытый перечень: критерий оптимизации приходит В ПАКЕТЕ (данные
авиакомпании, спецификация 1.9), исполнитель вида — код COSMOS. Три
исхода судьи apply_criterion:

  1. вид известен и исполним — аргументы построителя дополняются,
     собственные коэффициенты способа ЯВНО обнуляются (чистота цели,
     Г-П4 п. 7.1: целевая построителя РАВНА критерию авиакомпании);
  2. вид известен СПЕЦИФИКАЦИИ, но исполнителя нет — это ПРОБЕЛ
     ШАБЛОНА (этап 2, узел vmp-boundary): возвращается CriterionGap,
     маршрут решает vmp.gaps (извещение пользователя), а вид заявки
     каталога — vmp.catalog_requests (шаг 6): заявка на вид
     исполнителя у записи критерия либо правка записи;
  3. вид неизвестен и спецификации — отказ CriteriaError: либо брак
     выпуска ВМП (зона CDPM), либо словарь спецификации отстал —
     в обоих случаях молчаливое продолжение запрещено.

Вид 0096 «нормированная стоимость по рангам» (заявка одной из линий)
объявлен в спецификации БЕЗ исполнителя намеренно: его исполнитель —
первый живой прогон механизма этапа 2, не ручная реализация (решение
пары, лист 01/02 переноса). Семантику нормировки CDPM сверяет с
линией-заявителем; машинный носитель ранга в ВМП — вопрос (в) листа 02.

SPEC — с шага 2 каталога шаблонов (07.09.2026) ПРОЕКЦИЯ каталога
(vmp.template_registry.criteria_spec: записи критериев «есть», у которых
исполнимость вида объявлена computable_by ∋ "builder_method"); исполнитель
вида — код (EXECUTORS). Объект SPEC один на экземпляр каталога (модульный
__getattr__): потребители и тесты правят его на месте, при смене файлов
каталога — новый объект. Без каталога — отказ словами
(catalog_access.CATALOG_ERRORS), coverage() переводит его в «невычислима с
причиной».
"""
from dataclasses import dataclass


class CriteriaError(ValueError):
    """Вид критерия вне словаря спецификации либо брак его данных."""


@dataclass(frozen=True)
class CriterionGap:
    """Пробел шаблона: вид известен спецификации, исполнителя нет."""
    kind: str
    title: str
    source_rule: str | None
    needs: str  # чего не хватает, человеческим языком


def _deci(rate, name: str) -> int:
    """Ставка пакета -> целые деци-единицы построителя, fail-closed:
    деци-форма выражает только ставки, кратные 0,1 — прочее требует
    расширения расчётной ветки, а не молчаливого округления."""
    import math
    from vmp.objective_projection import positive_finite_rate
    if not positive_finite_rate(rate):
        raise CriteriaError(
            f"ставка {name} критерия не положительное конечное число: {rate!r}")
    scaled = float(rate) * 10
    if not math.isfinite(scaled):
        raise CriteriaError(f"ставка {name}={rate!r} переполняет деци-форму построителя")
    # Judge the decimal number carried by JSON, not a rounded binary
    # product. A tolerance can turn positive rates into zero or silently
    # accept a non-tenth; float(int) also loses large integer digits.
    # as_integer_ratio avoids dependence on the global Decimal context.
    from decimal import Decimal
    numerator, denominator = Decimal(str(rate)).as_integer_ratio()
    d, remainder = divmod(numerator * 10, denominator)
    if remainder:
        raise CriteriaError(
            f"ставка {name}={rate!r} не выражается целыми "
            f"деци-единицами построителя (кратность 0,1) — требуется "
            f"расширение расчётной ветки, молча не округляется")
    return d


def _exec_rank_normalized_cost(criterion: dict, args: list,
                               prov: dict):
    """Нормированная стоимость по рангам — обезличенный шаблон.

    Принцип владельца 2026-08-29: механика в коде, ВСЯ конкретика —
    параметрами из ВМП. Код валидирует ФОРМУ (метка, положительные
    ставки, кратность деци, полная версия ставок политики
    MUST_BE_EXTERNAL) и передаёт построителю вычисленные
    деци-коэффициенты: рабочая минута перелёта = 10*(полёт + работа),
    минута перегона = 10*работа. Значения ставок кодом не
    проверяются — их носитель ответ анкеты ВМП; контрольная пара
    исполнителя — файлом контура данных (vmp/template_registry)."""
    if not str(criterion.get("label") or "").strip():
        raise CriteriaError(
            "метка критерия (label) пуста — вид требует явную метку "
            "семантики из данных пакета")
    rates = criterion.get("rates")
    if not isinstance(rates, dict):
        raise CriteriaError("блок rates критерия отсутствует "
                            "(политика MUST_BE_EXTERNAL: ставки — "
                            "данные пакета)")
    d_fl = _deci(rates.get("flight"), "flight")
    d_wk = _deci(rates.get("work"), "work")
    ver = criterion.get("rates_version")
    need = ("source", "version", "effective_from")
    if not isinstance(ver, dict) or not all(ver.get(k) for k in need):
        raise CriteriaError(
            "версия ставок неполна: политика MUST_BE_EXTERNAL "
            "требует rates_version с полями source, version, "
            "effective_from")
    deci_work = d_fl + d_wk
    deci_dh = d_wk
    from vmp import template_registry as tr
    tr.check_control_pair("rank_normalized_cost",
                          {"deci_work": deci_work,
                           "deci_dh": deci_dh})
    args += ["--cost-0096",
             "--cost-rank-deci-work", str(deci_work),
             "--cost-rank-deci-dh", str(deci_dh),
             "--cost-rates-param",
             "%s/v%s" % (ver.get("source"), ver.get("version")),
             "--criterion-source",
             f"критерий пакета: {criterion.get('source_rule')}"]
    prov["--cost-0096"] = {
        "value": True,
        "from": f"критерий пакета: {criterion.get('source_rule')} "
                f"(ставки {ver.get('source')} v{ver.get('version')} "
                f"с {ver.get('effective_from')})",
        "note": "целевая построителя РАВНА критерию; веса w-* не "
                "подаются — расчётная ветка их не читает"}
    prov["--cost-rank-deci-work"] = {
        "value": deci_work,
        "from": f"10*(flight {rates.get('flight')!r} + work "
                f"{rates.get('work')!r}) из блока rates пакета"}
    prov["--cost-rank-deci-dh"] = {
        "value": deci_dh,
        "from": f"10*work {rates.get('work')!r} из блока rates "
                f"пакета"}


def _exec_min_person_days(criterion: dict, args: list, prov: dict):
    """Минимум человеко-дней командировки.

    Семантика (живой урок первого канонического прогона 2026-08-26):
    «человеко-день командировки» — КАЛЕНДАРНЫЙ день в отъезде (от
    первого вылета до последнего прилёта, дни отдыха внутри цепочки и
    перегоны включены) → ключ --w-span-day. Цена за смену (--w-duty)
    делала простойные дни бесплатными — оптимизатор клеил ночёвки.
    Поле пакета rate_per_duty читается как ставка за день;
    переименование в rate_per_day — следующей ревизией спецификации."""
    rate = criterion.get("rate_per_duty")
    if not isinstance(rate, int) or isinstance(rate, bool) or rate <= 0:
        raise CriteriaError(
            f"ставка критерия не целое > 0: {rate!r}")
    src = f"критерий пакета: {criterion.get('source_rule')}"
    args += ["--w-span-day", str(rate), "--w-duty", "0",
             "--w-night", "0", "--w-dh", "0", "--w-chain", "0",
             # Источник цены в отчёте построителя — критерий, не
             # умолчания (карточка серии печатает это поле; серия H0
             # 3 сентября 2026 читала «умолчания построителя» при
             # весах критерия).
             "--criterion-source", src]
    prov["--w-span-day"] = {"value": rate, "from": src}
    prov["--criterion-source"] = {"value": src, "from": src}
    for k in ("--w-duty", "--w-night", "--w-dh", "--w-chain"):
        prov[k] = {"value": 0,
                   "from": "чистота цели (Г-П4 п. 7.1): целевая "
                           "равна критерию"}


# Спецификация видов: закрытый перечень того, что ВМП вправе нести —
# проекция каталога шаблонов (записи критериев «есть» с исполнителем-
# построителем в computable_by). needs заполняется только у видов без
# исполнителя (текст извещения). Обезличенная семантика ТИПА (принцип
# владельца 2026-08-29): формула — механика; ставки, ранги и контрольные
# пары — данные контура (ответ анкеты ВМП + файл-носитель
# vmp-criterion-control-pairs.json, см. template_registry).
_SPEC_CACHE: tuple | None = None       # (экземпляр каталога, объект SPEC)


def _spec() -> dict:
    """SPEC — проекция каталога шаблонов; один объект на экземпляр
    каталога (потребители и тесты правят его на месте), новый — при
    смене файлов каталога."""
    global _SPEC_CACHE
    from vmp import catalog_access as _ca
    from vmp import template_registry as _tr
    cat = _ca.current()
    hit = _SPEC_CACHE
    if hit is not None and hit[0] is cat:
        return hit[1]
    with _ca.LOCK:
        hit = _SPEC_CACHE
        if hit is not None and hit[0] is cat:
            return hit[1]
        spec = _tr.criteria_spec(cat)
        _SPEC_CACHE = (cat, spec)
        return spec


def __getattr__(name):
    if name == "SPEC":
        return _spec()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | {"SPEC"})


# Исполнители: вид → функция (criterion, args, prov). Вид из SPEC без
# записи здесь — пробел шаблона (CriterionGap), не отказ.
EXECUTORS = {
    "min_person_days": _exec_min_person_days,
    "rank_normalized_cost": _exec_rank_normalized_cost,
}

# Prepared in COSMOS 100c180d; integrated here with strict handling of
# explicitly malformed directions. This describes executor capability,
# not a substitute catalogue of criterion directions.
EXECUTOR_DIRECTION = "minimize"


def check_direction(criterion: dict) -> str:
    """Check catalogue, declared direction and executor before any mutation.

    An absent legacy field is derived from the catalogue. An explicitly
    empty/malformed field is not absence. Catalogue 0.5 without direction
    fails closed as required by contract 3.3 / 12.9.
    """
    from vmp import catalog_access as _ca
    from vmp import objective_projection as _op
    kind = criterion.get("kind")
    try:
        direction = _op.criterion_direction(_ca.current(), str(kind))
    except (_op.ObjectiveProjectionError, *_ca.CATALOG_ERRORS) as e:
        raise CriteriaError(str(e)) from None
    if "direction" in criterion:
        declared = criterion["direction"]
        if not isinstance(declared, str) or not declared.strip():
            raise CriteriaError(
                f"направление критерия {kind!r} (direction) "
                "должно быть непустой строкой")
        if declared.strip().lower() != direction:
            raise CriteriaError(
                f"направление критерия {kind!r}: пакет требует "
                f"{declared!r}, каталог объявил {direction} — "
                "ревизия постановки (контракт §3.3)")
    if direction != EXECUTOR_DIRECTION and kind in EXECUTORS:
        raise CriteriaError(
            f"исполнитель вида {kind!r} поддерживает только "
            f"{EXECUTOR_DIRECTION}, каталог объявил {direction} — "
            "молчаливой минимизации чужой цели нет")
    return direction


def apply_criterion(criterion: dict, args: list,
                    prov: dict) -> CriterionGap | None:
    """Судья словаря: исполнить критерий либо назвать пробел.

    Возвращает None (исполнено) или CriterionGap (вид известен
    спецификации, исполнителя нет — маршрут vmp.gaps). Неизвестный
    вид — отказ CriteriaError."""
    spec = _spec()
    kind = criterion.get("kind")
    if kind not in spec:
        known = "', '".join(sorted(spec))
        raise CriteriaError(
            f"вид критерия {kind!r} вне перечня ('{known}')")
    fn = EXECUTORS.get(kind)
    if fn is None:
        return CriterionGap(
            kind=kind, title=spec[kind]["title"],
            source_rule=criterion.get("source_rule"),
            needs=spec[kind]["needs"] or "исполнитель не реализован")
    # Preserve the gap route: no executor means no weights are applied.
    check_direction(criterion)
    fn(criterion, args, prov)
    return None


def coverage(criterion: dict | None) -> dict:
    """Вычислимость целевой ПЕЙРИНГА судит тот, кто её исполняет —
    построитель через этот словарь, а не отчёт покрытия решателя
    ростеринга (там «total_pairing_cost не вычислима на объединённой
    модели» — ответ о себе; кнопка CDPM принимала его за вердикт по
    пейрингу и отступала на выполнимость: серия H0 3 сентября 2026,
    OPTIMAL по нулевой целевой). Возврат: {computable, kind, executor,
    reason}; исполнитель прогоняется всухую — брак данных критерия
    тоже даёт «невычислима» с причиной, а не отказ на старте."""
    if not isinstance(criterion, dict) or not criterion.get("kind"):
        return {"computable": False, "kind": None, "executor": None,
                "reason": "пакет не несёт objectives.pairing.criterion — "
                          "критерий пейринга не объявлен"}
    kind = criterion.get("kind")
    from vmp import catalog_access as _ca
    try:
        spec = _spec()
    except _ca.CATALOG_ERRORS as e:
        # Словарь видов — проекция каталога: без каталога целевая
        # невычислима с причиной, а не отказ на старте.
        return {"computable": False, "kind": kind, "executor": None,
                "reason": "каталог шаблонов недоступен: %s" % e}
    if kind not in spec:
        return {"computable": False, "kind": kind, "executor": None,
                "reason": "вид критерия %r вне словаря спецификации" % (kind,)}
    fn = EXECUTORS.get(kind)
    if fn is None:
        return {"computable": False, "kind": kind, "executor": None,
                "reason": "пробел шаблона: вид известен спецификации, "
                          "исполнителя у построителя нет — %s"
                          % (spec[kind]["needs"] or "исполнитель не реализован")}
    try:
        check_direction(criterion)
    except CriteriaError as e:
        return {"computable": False, "kind": kind,
                "executor": "vmp.criteria.%s" % fn.__name__,
                "reason": "направление критерия: %s" % e}
    try:
        fn(criterion, [], {})
    except CriteriaError as e:
        return {"computable": False, "kind": kind,
                "executor": "vmp.criteria.%s" % fn.__name__,
                "reason": "данные критерия негодны: %s" % e}
    except Exception as e:                                # noqa: BLE001
        # Любой иной отказ исполнителя (например, контрольная пара
        # контура не сошлась — ControlPairError) — невычислима с
        # причиной, а не исключение у потребителя отчёта.
        return {"computable": False, "kind": kind,
                "executor": "vmp.criteria.%s" % fn.__name__,
                "reason": "исполнитель отказал: %s" % e}
    return {"computable": True, "kind": kind,
            "executor": "vmp.criteria.%s" % fn.__name__,
            "reason": "исполнитель вида у построителя есть, данные "
                      "критерия прошли форму; целевая построителя равна "
                      "критерию"}
