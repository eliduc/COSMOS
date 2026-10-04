# -*- coding: utf-8 -*-
"""Гейт ступени 3: блоки logic пакета против раскладки решателя.

Решатель раскладывает каждое правило с блоком `logic` ровно в одну из
пяти корзин (lastRun.logic отчёта применённого): ПОРОЖДЕНО, ИСПОЛНЯЕТСЯ
СУЩЕСТВУЮЩИМ, НЕ ИСПОЛНЯЕТСЯ, ОТВЕРГНУТО, ВИД ПРАВИЛА. Гейт сверяет эту
раскладку с пакетом и с тем, что ЗАРЕГИСТРИРОВАЛА построенная фабрика
(lastRun.logic.registered — имена от библиотеки, не от намерения):

  1. каждое правило пакета с logic названо в отчёте, и ровно один раз;
     сумма корзин равна blocksSeen, а blocksSeen — числу блоков в модели;
  2. корзина ОТВЕРГНУТО пуста: прогон, который состоялся, не мог нести
     отвергнутое required-правило;
  3. исполнители корзин ПОРОЖДЕНО и ИСПОЛНЯЕТСЯ СУЩЕСТВУЮЩИМ —
     зарегистрированные имена; у каждого — предметов рассмотрено > 0;
  4. отрицательный контроль имён: без порождённых в зарегистрированных
     нет ни одного имени с префиксом «logic »;
  5. required-правило — только в корзинах состояния ПОСЧИТАНО.

Чего модуль не знает: ни словарей, ни таблицы отнесения, ни имён
ограничений — всё это приходит отчётом решателя. Копия здесь была бы
вторым источником правды.
"""

from __future__ import annotations

OK = "сходится"
DEFECT = "дефект"
EMPTY = "нет logic"

BASKETS = ("generated", "executedElsewhere", "ignored", "refused", "notHard")
COUNTED = ("generated", "executedElsewhere")
GENERATED_PREFIX = "logic "


class LogicGateError(ValueError):
    pass


def logic_rules(pkg: dict) -> list[dict]:
    """Правила модели пакета с непустым блоком logic (любого вида).

    Правило с пометкой «исключено до получения данных по решению
    авиакомпании» (структура ВМП 2026-09-02) решатель не исполняет:
    его блок logic не читается и в blocksSeen не считается — гейт
    судит показания той же границей, иначе на чистом прогоне выходит
    ложный дефект «blocksSeen ≠ блоков logic в модели» (аудит №2)."""
    from vmp.registry_check import is_excluded_until_data
    model = (pkg.get("model") or {}) if "model" in pkg else pkg
    out = []
    for r in (model.get("rules") or []):
        if not isinstance(r, dict) or is_excluded_until_data(r):
            continue
        if isinstance(r.get("logic"), dict) and r["logic"]:
            out.append(r)
        elif fitted_history_form(r):
            # Путь А (05.09.2026): правило семьи истории с посадкой и без
            # блока logic решатель исполняет формой ИЗ ПОСАДКИ (kotlin
            # LogicReader.synthesizeFromFit) — гейт считает его тем же
            # правилом с логикой, иначе blocksSeen разошёлся бы с моделью.
            out.append(r)
    return out


def fitted_history_form(rule: dict) -> str | None:
    """Семья истории, из посадки которой решатель строит форму: посадка
    A/B семьи истории, по которой проверка кода собирает план формы
    (`vmp.run.logic_checks.plan_from_fit`) — те же семьи, те же наборы
    параметров, что и у решателя; иначе None.

    Носитель истины один — код проверок (`logic_checks.FAMILY_FORMS` +
    `plan_from_fit`), который агент конвейера пополнения дописывает
    вместе с формой решателя. Прежнее рукописное зеркало таблиц
    решателя (три семьи с их наборами параметров) не знало семьи,
    пришедшей конвейером, и гейт на первом же прогоне Н0-C фазы 2
    (0.4.0, 09.09.2026) останавливал кнопку: «в модели пакета нет блоков
    logic, а решатель видел 1» — при том, что решатель и проверка кода
    правило исполняли; зеркало лежало в зоне, закрытой агенту."""
    fit = rule.get("template_fit")
    if not isinstance(fit, dict) or fit.get("category") not in ("A", "B"):
        return None
    fam = str(fit.get("family") or "")
    from vmp.run import logic_checks as lc
    if fam not in lc.FAMILY_FORMS:
        return None
    try:
        plan = lc.plan_from_fit(fam, rule)
    except Exception:                                    # noqa: BLE001
        return None
    return fam if plan else None


def check(pkg: dict, logic: dict | None) -> list[dict]:
    """Вердикты гейта: по одному на правило с logic плюс общие.

    `pkg` — разобранный пакет (webapp.task_package.load) либо сама
    модель; `logic` — lastRun.logic отчёта применённого (может быть
    None, если пакета в прогоне не было).
    """
    rules = logic_rules(pkg)
    verdicts: list[dict] = []

    def v(verdict, rule, note, **extra):
        d = {"verdict": verdict, "rule": rule, "note": note}
        d.update(extra)
        verdicts.append(d)

    if not rules:
        if logic and int(logic.get("blocksSeen") or 0) > 0:
            v(DEFECT, "*", "в модели пакета нет блоков logic, а решатель видел %s"
              % logic.get("blocksSeen"))
        else:
            v(EMPTY, "*", "в пакете нет ни одного блока logic — гейту нечего сверять")
        return verdicts
    if not isinstance(logic, dict):
        raise LogicGateError(
            "пакет несёт %d блок(ов) logic, а отчёт прогона раздела logic не содержит: "
            "решатель их не судил либо отчёт не от этого прогона" % len(rules))

    seen = int(logic.get("blocksSeen") or 0)
    if seen != len(rules):
        v(DEFECT, "*", "blocksSeen=%d, а блоков logic в модели %d" % (seen, len(rules)))
    baskets = {b: list(logic.get(b) or []) for b in BASKETS}
    total = sum(len(x) for x in baskets.values())
    if total != seen:
        v(DEFECT, "*", "сумма корзин %d не равна blocksSeen %d" % (total, seen))

    where: dict[str, list[str]] = {}
    for b, items in baskets.items():
        for e in items:
            where.setdefault(str(e.get("rule")), []).append(b)

    registered = logic.get("registered")
    reg = set(registered) if isinstance(registered, list) else None
    if reg is None:
        v(DEFECT, "*", "отчёт не несёт registered — имён, зарегистрированных фабрикой; "
          "заявление ПРИНЯТО нечем подтвердить")
        reg = set()

    for r in rules:
        rid = str(r.get("id"))
        kind = r.get("kind")
        enforce = (r.get("logic") or {}).get("enforce") or "required"
        places = where.get(rid, [])
        if len(places) != 1:
            v(DEFECT, rid, "названо в корзинах %s (ожидается ровно одна)" % (places or "ни одной"))
            continue
        b = places[0]
        e = next(x for x in baskets[b] if str(x.get("rule")) == rid)
        if kind != "HARD" and b != "notHard":
            # Волна П-2: мягкая волновая форма (словарь LOGIC_SOFT_KINDS)
            # ЗАКОННО живёт в generated — включатель решателя порождает
            # ей исполнителя (живой урок Г-П4: гейт отставал от волны).
            wave_soft = False
            if kind == "SOFT" and b == "generated":
                from cdpm.model import LOGIC_SOFT_KINDS
                dk = ((r.get("logic") or {}).get("demand")
                      or {}).get("kind")
                wave_soft = dk in LOGIC_SOFT_KINDS
            if not wave_soft:
                v(DEFECT, rid,
                  "правило вида %s попало в корзину %s" % (kind, b))
                continue
        if kind == "HARD" and b == "notHard":
            v(DEFECT, rid, "жёсткое правило названо «вид правила»")
            continue
        if b == "refused":
            v(DEFECT, rid, "ОТВЕРГНУТО в состоявшемся прогоне: %s" % e.get("reason"))
            continue
        if enforce == "required" and b not in COUNTED and kind == "HARD":
            v(DEFECT, rid, "required, а состояние %s: %s" % (e.get("state"), e.get("reason")))
            continue
        if b in COUNTED:
            ids = [str(c) for c in (e.get("constraints") or [])]
            if not ids:
                v(DEFECT, rid, "корзина %s без имени исполнителя" % b)
                continue
            missing = [c for c in ids if c not in reg]
            if missing:
                v(DEFECT, rid, "исполнитель %s не зарегистрирован фабрикой (registered: %d имён)"
                  % (missing, len(reg)))
                continue
            considered = e.get("considered")
            if considered is None or int(considered) <= 0:
                v(DEFECT, rid, "исполнитель %s рассматривает %s предметов — «0 нарушений» "
                  "неотличимо от «считать нечего»" % (ids, considered))
                continue
            v(OK, rid, "%s: %s, предметов рассмотрено %d (%s)"
              % (e.get("state"), ", ".join(ids), int(considered), e.get("consideredMeasure")),
              constraints=ids, declarations=list(e.get("declarations") or []))
        else:
            v(OK, rid, "%s: %s" % (e.get("state"), e.get("reason")),
              declarations=list(e.get("declarations") or []))

    # Отрицательный контроль имён: порождённых нет — и имён logic у фабрики нет.
    generated_ids = {str(c) for e in baskets["generated"] for c in (e.get("constraints") or [])}
    stray = sorted(c for c in reg if c.startswith(GENERATED_PREFIX) and c not in generated_ids)
    if stray:
        v(DEFECT, "*", "фабрика зарегистрировала имена logic, которых отчёт не породил: %s" % stray)
    return verdicts


def report(verdicts: list[dict], considered: int | None = None) -> str:
    """Человекочитаемый отчёт; строка «рассмотрено» — от источника."""
    lines = []
    for x in sorted(verdicts, key=lambda d: (d["verdict"], d["rule"])):
        lines.append("[%-9s] %-32s %s" % (x["verdict"], x["rule"], x["note"]))
        for d in x.get("declarations") or []:
            lines.append("%s %s" % (" " * 12, d))
    counts: dict[str, int] = {}
    for x in verdicts:
        counts[x["verdict"]] = counts.get(x["verdict"], 0) + 1
    rules = {x["rule"] for x in verdicts if x["rule"] != "*"}
    if considered is None:
        considered = len(rules)
    lines.append("")
    lines.append("рассмотрено правил с logic: %d; %s"
                 % (considered, ", ".join("%s: %d" % (k, n) for k, n in sorted(counts.items()))))
    return "\n".join(lines)


def is_green(verdicts: list[dict]) -> bool:
    return all(x["verdict"] != DEFECT for x in verdicts)
