"""Таблица покрытия правил заказчика: что COSMOS с каждым из них делает.

Пакет правил отвечает на вопрос «какие правила у заказчика есть». Этот
модуль отвечает на следующий, без которого предъявлять решение нельзя:
по каждому правилу — перенесены ли ДАННЫЕ, есть ли ПРОВЕРКА, учитывает
ли РЕШАТЕЛЬ и применён ли ВЕС в едином счёте. Правило, попавшее в пакет,
но не дошедшее до решателя, иначе выглядит выполненным.

Разделение источников намеренное:
  * что умеет COSMOS (проверка / решатель / вес) — свойство продукта, и
    оно объявлено здесь таблицей `SUPPORT`, а не выводится эвристикой;
  * есть ли данные у конкретного заказчика и чем это подтверждено —
    свойство набора, и подаётся наложением (`--coverage`).
Код без объявленного статуса не пропускается молча: он попадает в отчёт
пометкой «НЕ КЛАССИФИЦИРОВАНО» и в итоговый счётчик.

Детерминированность: ни одного обращения к языковым моделям и к сети.

Использование:
    python _rules_coverage.py --pack pack.json [--coverage overlay.json]
        [--vendor solver.json] [--out coverage.md]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from _cli_console import force_utf8_console  # noqa: E402

force_utf8_console()

UNKNOWN = "?"
OFF_NOTE = "выключено заказчиком (OFF) — не проверяем и не исполняем"

# Что COSMOS делает с ограничением вендора: (проверка, решатель, вес).
# Свойство ПРОДУКТА: меняется вместе с кодом, а не с набором данных.
SUPPORT: Dict[str, Tuple[str, str, str]] = {
    "ASSIGNMENT_CONFLICT": ("+", "+", "+"),
    "ASSIGNMENT_LIMITATIONS": ("-", "-", "-"),
    "ASSIGNMENT_REQUIREMENTS": ("+", "+", "+"),
    "DO_NOT_FLY_WITH": ("+", "+", "+"),
    "DO_NOT_VISIT_DENIED_STATIONS": ("+", "+", "+"),
    "FLIGHT_TIME_LIMIT": ("+", "+", "+"),
    "INVALID_DUTY_SEQUENCE": ("-", "-", "-"),
    "LAYOVER_AFTER_NIGHT_DUTIES": ("-", "-", "-"),
    "LAYOVER_DENIED_STATIONS": ("+", "+", "-"),
    "LEG_LEVEL_LIMITATIONS": ("-", "-", "-"),
    "LEG_LEVEL_REQUIREMENTS": ("+", "+", "+"),
    "NO_USELESS_SEQUENCES": ("-", "-", "-"),
    "PREFER_MINIMUM_CREW_SIZE": ("-", "-", "-"),
    "RPS_COUNT_LIMIT": ("-", "-", "-"),
    "WEEKLY_REST": ("+", "+", "+"),
    "WORK_TIME_LIMIT": ("+", "+", "+"),
    "LAST_DUTY_NOT_TO_BASE": ("-", "-", "-"),
    "LAYOVER_BEFORE_DUTY": ("+", "+", "+"),
    "ANNUAL_FLIGHT_TIME_UNIFORMNESS_RANK": ("-", "-", "-"),
    "ANNUAL_FLIGHT_TIME_UNIFORMNESS_RANK_AND_BASE": ("-", "-", "-"),
    "FLIGHT_TIME_UNIFORMNESS_RANK": ("-", "-", "-"),
    "RELAY_COST_PENALTY": ("-", "-", "-"),
    "UNASSIGNED": ("+", "+", "+"),
    "UNASSIGNED_OPTIONAL": ("-", "-", "-"),
    "CREW_PAIRING_DURATION_CONTROL": ("+", "+", "+"),
    "FLEET_REQUIREMENTS": ("-", "-", "-"),
    "GENDER_ACCOMMODATION": ("-", "-", "-"),
    "LAYOVER_AFTER_DARK_DUTIES": ("-", "-", "-"),
    "OVERQUALIFIED_EMPLOYEE": ("-", "-", "-"),
    "INVALID_LOCKED_DUTY_SEQUENCE": ("-", "-", "-"),
    "LAYOVER_BEFORE_LOCKED_DUTY": ("-", "-", "-"),
    "LOCKED_ASSIGNMENT_CONFLICT": ("-", "-", "-"),
}

# Куда перенесён параметр вендора. Тоже свойство продукта.
PARAMETER_MAP: Dict[str, str] = {
    "planSince": "периметр планирования",
    "planUntil": "периметр планирования",
    "maximumPairingDays": "предел цепочки (решатель и проверка)",
    "layoverRules": "ярусная таблица отдыха (решатель и проверка)",
    "weeklyRestRules": "еженедельный отдых (решатель и проверка)",
    "solvingTimeLimit": "бюджет расчёта",
    "carriers": "фильтр перевозчика",
    "overqualificationPenalties": "матрица перенесена, правило не реализовано",
    "nightDutySequenceRules": "пороги перенесены, определение не объявлено",
    "darkDutyRules": "параметры перенесены, определение не объявлено",
    "stationEquivalenceRules": "не реализовано",
    "acclimatizationPeriod": "определение не объявлено",
    "nightStart": "определение ночи не объявлено",
    "nightEnd": "определение ночи не объявлено",
    "nightDutyRules": "определение ночного наряда не объявлено",
    "dutyEndLocalHour": "уровень построения цепочек",
    "solvingMode": "режим разбиения смен у вендора",
    "maximumWeeklyWorkTime": "нулевое значение = выключено",
    "employeeLimitRules": "нулевое значение = выключено",
    "flightTimeCostCalculationRules": "правило стоимости выключено",
    "transferDeadheadRules": "уровень построения цепочек",
    "crewEnforcementRules": "определение усиления не объявлено",
    "offBaseWorkTimeRatio": "формула не объявлена",
    "relayThreshold": "формула не объявлена",
    "transLegThreshold": "порог трансмеридианного перелёта",
    "unknownStationCostThreshold": "модель стоимости не объявлена",
    "allowedSplitDutySequenceLength": "нулевое значение = выключено",
    "minLayoverAfterSplitDutySequence": "нулевое значение = выключено",
    "extendedDutyCheckWindow": "нулевое значение = выключено",
    "extendedDutyUpperLimit": "нулевое значение = выключено",
    "longVacationThreshold": "уровень построения цепочек",
    "experiencedFeatWord": "метка признака",
    "greenFeatWord": "метка признака",
    "maleFeatWord": "метка признака",
    "femaleFeatWord": "метка признака",
    "mentorTag": "метка признака",
    "experiencedHoursThreshold": "порог признака",
    "greenHoursThreshold": "порог признака",
    "employeePilot": "вид экипажа",
    "ranksForSingleAccomodation": "размещение, определение не объявлено",
}


def vendor_rules(pack: dict) -> List[Tuple[str, Optional[str]]]:
    """Коды ограничений вендора и их уровень веса из Пакета правил.
    Происхождение записано приёмником импорта в поле `note` — читаем
    его, а не угадываем по названию."""
    out: List[Tuple[str, Optional[str]]] = []
    for rule in pack.get("rules") or []:
        note = (rule.get("constraint") or {}).get("note") or ""
        try:
            meta = json.loads(note)
        except (TypeError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("vendor_constraint"):
            out.append((str(meta["vendor_constraint"]),
                        meta.get("weight_level")))
    return out


# Что наложению набора данных позволено менять. Наложение сообщает ФАКТЫ
# О ДАННЫХ («в этом наборе такие поля есть»), а не способность продукта.
# Аудит 2026-07-26 (P2-02): наложение могло переопределить и `check`, и
# `solver`, и `applied` — синтетически неподдерживаемое правило
# превращалось в полностью покрытое, то есть отчёт объявлял поддержку,
# которой в коде нет. Способность приходит только из таблицы SUPPORT.
_OVERLAY_ALLOWED = ("data", "note")


def rows(pack: dict, overlay: Optional[dict] = None) -> List[dict]:
    over = (overlay or {}).get("constraints") or {}
    ignored = sorted({k for o in over.values() if isinstance(o, dict)
                      for k in o if k not in _OVERLAY_ALLOWED})
    if ignored:
        print(f"ВНИМАНИЕ: наложение пытается задать графы способности "
              f"{ignored} — они берутся только из таблицы поддержки и "
              f"проигнорированы", flush=True)
    out: List[dict] = []
    for code, weight in vendor_rules(pack):
        o = over.get(code) or {}
        if weight == "OFF":
            out.append({"code": code, "weight": weight, "data": "n/a",
                        "check": "n/a", "solver": "n/a", "applied": "n/a",
                        "note": o.get("note") or OFF_NOTE,
                        "classified": True})
            continue
        sup = SUPPORT.get(code)
        out.append({
            "code": code, "weight": weight,
            # факт о ДАННЫХ — из наложения
            "data": o.get("data", UNKNOWN),
            # способность ПРОДУКТА — только из таблицы поддержки
            "check": sup[0] if sup else UNKNOWN,
            "solver": sup[1] if sup else UNKNOWN,
            "applied": sup[2] if sup else UNKNOWN,
            "note": o.get("note", "" if sup else "НЕ КЛАССИФИЦИРОВАНО"),
            "classified": sup is not None})
    order = {"HIGH_HIGH": 0, "HIGH": 1, "STD": 2, "LOW": 3, "LOW_LOW": 4,
             "OFF": 5}
    out.sort(key=lambda r: (order.get(r["weight"], 9), r["code"]))
    return out


def totals(table: List[dict]) -> dict:
    t = {"full": 0, "partial": 0, "missing": 0, "off": 0,
         "unclassified": 0, "active": 0}
    for r in table:
        if r["weight"] == "OFF":
            t["off"] += 1
            continue
        t["active"] += 1
        if not r["classified"]:
            t["unclassified"] += 1
        if (r["data"], r["check"], r["solver"], r["applied"]) \
                == ("+", "+", "+", "+"):
            t["full"] += 1
        elif "-" in (r["check"], r["solver"]):
            t["missing"] += 1
        else:
            t["partial"] += 1
    return t


def parameters_table(vendor: dict,
                     overlay: Optional[dict] = None) -> List[Tuple[str, str]]:
    over = (overlay or {}).get("parameters") or {}
    names = sorted(vendor.get("parameters") or {})
    return [(n, over.get(n) or PARAMETER_MAP.get(n, "?? НЕ КЛАССИФИЦИРОВАН"))
            for n in names]


def as_markdown(table: List[dict], t: dict,
                params: Optional[List[Tuple[str, str]]] = None) -> str:
    out = [f"## Ограничения ({len(table)})", "",
           "| Правило | Уровень | Данные | Проверка | Решатель | Вес | "
           "Примечание |",
           "|---|---|---|---|---|---|---|"]
    for r in table:
        out.append(f"| {r['code']} | {r['weight']} | {r['data']} | "
                   f"{r['check']} | {r['solver']} | {r['applied']} | "
                   f"{r['note']} |")
    out += ["", f"Итог по действующим ({t['active']}): полное покрытие "
                f"{t['full']}, частичное {t['partial']}, НЕ покрыто "
                f"{t['missing']}; выключенных {t['off']}."]
    if t["unclassified"]:
        out += ["", f"**Не классифицировано: {t['unclassified']}** — эти "
                    f"правила не отнесены ни к покрытым, ни к непокрытым; "
                    f"их надо разобрать, а не считать выполненными."]
    if params:
        out += ["", f"## Параметры ({len(params)})", "",
                "| Параметр | Статус переноса |", "|---|---|"]
        out += [f"| {n} | {s} |" for n, s in params]
        unknown = sum(1 for _n, s in params if s.startswith("??"))
        out += ["", f"Неклассифицированных параметров: {unknown}."]
    return "\n".join(out) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", type=Path, required=True,
                    help="Пакет правил COSMOS (выход приёмника импорта)")
    ap.add_argument("--coverage", type=Path, default=None,
                    help="наложение по набору: наличие данных и пояснения "
                         "{'constraints': {код: {...}}, 'parameters': {...}}")
    ap.add_argument("--vendor", type=Path, default=None,
                    help="файл заказчика — источник списка параметров")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    pack = json.loads(args.pack.read_text(encoding="utf-8"))
    overlay = (json.loads(args.coverage.read_text(encoding="utf-8"))
               if args.coverage else None)
    table = rows(pack, overlay)
    t = totals(table)
    params = None
    if args.vendor:
        params = parameters_table(
            json.loads(args.vendor.read_text(encoding="utf-8")), overlay)
    md = as_markdown(table, t, params)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
    print(md)
    # Непокрытое и неклассифицированное — сигнал конвейеру, а не сбой.
    return 2 if (t["missing"] or t["unclassified"]) else 0


if __name__ == "__main__":
    sys.exit(main())
