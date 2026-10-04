"""Сравнение способов решения (директива владельца 2026-08-24).

Классический способ: (а) пейринг, (б) после утверждения — ростеринг на
нём (цепочки зафиксированы, pairingRunId). Сквозной способ: оптимизация
сразу ростеринга объединённым прогоном, пейринг решается по пути.

Правило вердикта (дословно по директиве): сквозной побеждает, ТОЛЬКО
если его ростеринг лучше классического И его пейринг приемлем. Порог
приемлемости — НАСТРОЙКА (решение владельца), умолчание 5%: стоимость
пейринга сквозного не хуже классической более чем на этот процент.

Модуль — расчётный образец: та же формула зашивается в сводную страницу
кнопки производителя данных (порог — настройкой кнопки). Все входы —
числа из машинных источников (счёт прогона, повкладовый счёт целевой
пейринга), никаких умолчаний внутри сравнения.

Сравнение счетов ростеринга — лексикографическое, как у решателя:
сначала жёсткие (ближе к нулю — лучше), затем мягкие. Способ с жёсткими
нарушениями недопустим; допустимый побеждает недопустимого независимо
от мягких; оба недопустимы — победителя нет.

Стоимости пейринга — МИНИМИЗИРУЕМЫЕ величины (>= 0). Из счёта решателя
стоимость получается модулем мягких: cost_from_score. Классическая
стоимость 0 — особый случай: относительный порог не определён,
приемлемо только ровно 0 у сквозного.
"""
from __future__ import annotations

# Умолчание порога приемлемости пейринга, процентов (решение владельца
# 2026-08-24). Действующее значение приходит из настроек потребителя.
DEFAULT_MARGIN_PCT = 5.0

WAY_CLASSICAL = "классический"
WAY_THROUGH = "сквозной"
WAY_NONE = "ни один"


class WayCompareError(ValueError):
    """Вход сравнения не читается — отказ, не догадка."""


# --------------------------------------------------- зонная метрика Г-П3
#
# Словарь исполнителей зоны pairing (согласован обеими сторонами
# 2026-08-24, ревью решателя word-в-word): rule_id компоненты целевой
# пейринга пакета → имя ограничения в повкладовом счёте (byName) и
# ЛИТЕРАЛЬНЫЙ вес провайдера. Вклад ограничения в повкладовом идёт
# литеральным весом (в feasibility-hard веса пакета не применяются —
# деление на литерал учитывает это); зонная стоимость компоненты =
# (|вклад| / литерал) × вес компоненты из пакета. Компонента без
# записи в словаре — честная строка «исполнителя нет» (кандидат
# волны конвейера шаблонов П-2), её вклад в зонную метрику не входит.
# ЛЕГАСИ-таблица (умолчание параметра executors): пакет ревизии 1.14
# несёт словарь исполнителей СЕКЦИЕЙ rule_maps.zone_executors
# манифеста — вызывающий читает её (webapp.rule_maps.zone_executors)
# и подаёт параметром executors; таблица остаётся для пакетов без
# секции.
ZONE_EXECUTORS = {
    # «Штраф за ночёвки вне базы» (нано; та же метрика у целевой
    # «ночи вне базы» полного) → мягкое ограничение провайдера с
    # литеральным весом 40 за ночь.
    "R-S1-табл-3-строка-6-кол-3": {
        "constraint": "Layover nights away from home", "literal": 40},
    # Мини (перевыпуск): та же компонента живёт под строкой-7 — миры
    # перевыпускаются отдельно, id гуляют (добавка подтверждена
    # решателем word-в-word по mini-task-package 2026-08-25; ревизия
    # «сопоставление по составу компоненты» — отдельным заходом).
    "R-S1-табл-3-строка-7-кол-3": {
        "constraint": "Layover nights away from home", "literal": 40},
    # Полный (run-0047): длинные стыковки, разрыв смены, однородность
    # ночных/дневных — именованных мягких исполнителей у пакетного
    # провайдера НЕТ (ревью 2026-08-24); кандидаты волны П-2.
}


def zone_cost(by_name: dict, components: list,
              executors: dict | None = None) -> dict:
    """Зонная стоимость пейринга решения по повкладовому счёту.

    by_name — карта ограничений повкладового (имя → {soft, ...});
    components — компоненты зоны pairing из objective.json пакета
    ({rule_id, title, weight}); executors — словарь исполнителей
    (умолчание — согласованный ZONE_EXECUTORS).

    Возвращает {cost, counted: [...], uncounted: [...]}: counted —
    посчитанные компоненты с единицами и вкладом, uncounted — честный
    перечень «исполнителя нет». Целочисленность единиц обязательна:
    |вклад| % литерал != 0 — расхождение литерала, отказ словами."""
    executors = ZONE_EXECUTORS if executors is None else executors
    cost = 0.0
    counted: list[dict] = []
    uncounted: list[dict] = []
    for comp in components or []:
        rid = comp.get("rule_id")
        weight = comp.get("weight")
        entry = executors.get(rid)
        if not entry:
            uncounted.append({
                "rule_id": rid, "title": comp.get("title"),
                "reason": "исполнителя нет — кандидат волны "
                          "конвейера шаблонов (П-2)"})
            continue
        row = (by_name or {}).get(entry["constraint"]) or {}
        soft = row.get("soft") or 0
        contrib = abs(int(soft))
        literal = int(entry["literal"])
        if contrib % literal != 0:
            raise WayCompareError(
                "литерал исполнителя разошёлся: вклад %d ограничения "
                "%r не кратен литеральному весу %d — словарь зоны "
                "устарел" % (contrib, entry["constraint"], literal))
        units = contrib // literal
        if not isinstance(weight, (int, float)) or weight <= 0:
            raise WayCompareError(
                "вес компоненты зоны %r не положительное число: %r"
                % (rid, weight))
        cost += units * float(weight)
        counted.append({
            "rule_id": rid, "title": comp.get("title"),
            "constraint": entry["constraint"], "units": units,
            "literal": literal, "component_weight": weight,
            "cost": units * float(weight)})
    return {"cost": cost, "counted": counted, "uncounted": uncounted}


def parse_score(score: str) -> tuple[int, int]:
    """«-8674hard/-747803soft» → (-8674, -747803)."""
    try:
        hard_s, soft_s = str(score).split("hard/")
        return int(hard_s), int(soft_s.replace("soft", ""))
    except (ValueError, AttributeError) as e:
        raise WayCompareError("счёт не читается: %r" % (score,)) from e


def cost_from_score(score: str) -> float:
    """Стоимость (минимизируемая) из счёта решателя: модуль мягких."""
    _, soft = parse_score(score)
    return float(abs(soft))


def _better_rostering(a: tuple[int, int], b: tuple[int, int]) -> bool:
    """Счёт a строго лучше b: сначала жёсткие, затем мягкие (больше — лучше)."""
    return a > b


def compare(classical_rostering_score: str,
            through_rostering_score: str,
            classical_pairing_cost: float,
            through_pairing_cost: float,
            margin_pct: float = DEFAULT_MARGIN_PCT) -> dict:
    """Машинный вердикт сравнения способов.

    Возвращает {winner, reasons, pairing_acceptable, rostering_through_better,
    margin_pct, inputs} — числа и словесные причины, без обрезания фактов.
    """
    if margin_pct < 0:
        raise WayCompareError("порог приемлемости отрицателен: %r" % margin_pct)
    if classical_pairing_cost < 0 or through_pairing_cost < 0:
        raise WayCompareError(
            "стоимость пейринга отрицательна (ожидается минимизируемая "
            "величина >= 0): классическая %r, сквозная %r"
            % (classical_pairing_cost, through_pairing_cost))
    cl = parse_score(classical_rostering_score)
    th = parse_score(through_rostering_score)
    reasons: list[str] = []

    cl_feasible = cl[0] == 0
    th_feasible = th[0] == 0
    if not cl_feasible and not th_feasible:
        reasons.append("оба способа недопустимы по жёстким: классический %d, "
                       "сквозной %d" % (cl[0], th[0]))
        winner = WAY_NONE
        return _out(winner, reasons, False, False, margin_pct,
                    classical_rostering_score, through_rostering_score,
                    classical_pairing_cost, through_pairing_cost)

    # Приемлемость пейринга сквозного: не хуже классики более чем на порог.
    if classical_pairing_cost == 0:
        pairing_ok = through_pairing_cost == 0
        reasons.append(
            "классическая стоимость пейринга 0 — относительный порог не "
            "определён, приемлемо только ровно 0 (сквозная: %g)"
            % through_pairing_cost)
    else:
        limit = classical_pairing_cost * (1.0 + margin_pct / 100.0)
        pairing_ok = through_pairing_cost <= limit
        reasons.append(
            "пейринг сквозного %g против классического %g, порог %g%% "
            "(допустимо до %g) — %s"
            % (through_pairing_cost, classical_pairing_cost, margin_pct,
               limit, "приемлем" if pairing_ok else "НЕ приемлем"))

    through_better = _better_rostering(th, cl)
    reasons.append("ростеринг сквозного %s против классического %s — %s"
                   % (through_rostering_score, classical_rostering_score,
                      "лучше" if through_better else "не лучше"))

    if not cl_feasible and th_feasible:
        # Классика недопустима: сквозной побеждает при приемлемом пейринге;
        # неприемлемый пейринг оставляет вопрос человеку — победителя нет.
        winner = WAY_THROUGH if pairing_ok else WAY_NONE
        reasons.append("классический способ недопустим по жёстким (%d)" % cl[0])
    elif through_better and pairing_ok:
        winner = WAY_THROUGH
    else:
        winner = WAY_CLASSICAL
    return _out(winner, reasons, pairing_ok, through_better, margin_pct,
                classical_rostering_score, through_rostering_score,
                classical_pairing_cost, through_pairing_cost)


def _out(winner, reasons, pairing_ok, through_better, margin_pct,
         cl_score, th_score, cl_cost, th_cost) -> dict:
    return {
        "winner": winner,
        "reasons": reasons,
        "pairing_acceptable": pairing_ok,
        "rostering_through_better": through_better,
        "margin_pct": margin_pct,
        "inputs": {
            "classical_rostering_score": cl_score,
            "through_rostering_score": th_score,
            "classical_pairing_cost": cl_cost,
            "through_pairing_cost": th_cost,
        },
    }
