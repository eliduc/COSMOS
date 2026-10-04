"""Входной аудит: структура заказчика против его же правил.

Прогоняется ДО любого расчёта. Смысл в том, что расхождение данных с
правилом имеет ровно две причины, и различить их надо до постановки
задачи:

* доля нарушений мала — данные действительно вне нормы: исключения,
  ручные решения, выданные разрешения. Это принимается как данность;
* доля нарушений велика — под подозрением НАША трактовка правила.
  Калибровать надо формулировку, а не чинить данные заказчика.

Практическое следствие, ради которого аудит и существует: правило, уже
нарушенное во входных данных, нельзя объявлять жёстким ограничением —
задача либо станет неразрешимой, либо её решение окажется недопустимым.

Работает на каноническом наборе и каталоге структуры заказчика (его
выдаёт импорт выгрузки системы планирования с отметкой «сохранить
структуру»), поэтому не зависит от формата источника: любой набор,
доведённый до канона, проверяется одинаково.

Периметр проверки задаётся самим каталогом. Замкнутость на базу и размах
— свойства целой командировки, поэтому судятся только записи с
`kind` = `chain` и `sourceLegsMissing` = 0. Достройки адаптера (`kind` =
`fill`: одиночный участок по моде типа судна, голый наряд без ротации)
начала и конца командировки не имеют вовсе; урезанная цепочка потеряла
их при стыковке, а не по вине заказчика. Правила рабочей структуры —
стыковка внутри смены и отдых между сменами — применяются ко всем
записям: смены у них настоящие.

Детерминированность: ни одного обращения к языковым моделям и к сети.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from _cli_console import force_utf8_console  # noqa: E402

force_utf8_console()

HERE = Path(__file__).resolve().parent
import _data_paths
FINAL_REPORT = _data_paths.results_dir(create=False)

# пороги вердикта: доля нарушений, ниже которой считаем виноватыми
# данные, и доля, выше которой считаем виноватой свою трактовку
SHARE_DATA_FAULT = 0.05
SHARE_OUR_FAULT = 0.20


def _dt(value: str) -> datetime:
    from _canonical_time import parse_utc
    return parse_utc(value)


def verdict(bad: int, total: int) -> str:
    if total == 0:
        return "нечего проверять"
    if bad == 0:
        return "данные соответствуют"
    share = bad / total
    if share < SHARE_DATA_FAULT:
        return "данные вне нормы: исключения или ручные решения"
    if share < SHARE_OUR_FAULT:
        return "требует разбора"
    return "под подозрением наша трактовка правила"


def _kinds_phrase(by_kind: dict) -> str:
    return ", ".join(f"{k} — {n}" for k, n in by_kind.items()) or "пусто"


def _by_kind(units: list[dict]) -> dict:
    """Разрез каталога по опознавательной метке `sourceUnit` — чем
    построен юнит. Для решения о том, судить ли его, служит поле `kind`,
    а не эта метка: разбор служебного имени по двоеточию ломается от
    любого переименования."""
    out: dict[str, int] = {}
    for u in units:
        tag = str(u.get("sourceUnit") or "?").split(":")[0]
        out[tag] = out.get(tag, 0) + 1
    return dict(sorted(out.items()))


class Audit:
    """Проверка структуры цепочек заказчика. Правила те же, что исполняет
    построитель цепочек, — иначе аудит и расчёт говорили бы на разных
    языках и расхождение нельзя было бы истолковать."""

    def __init__(self, payload: dict, args: argparse.Namespace) -> None:
        self.args = args
        self.bases = set(filter(None, args.bases.split(","))) if args.bases \
            else {e.get("homeAirport") for e in payload.get("employees") or []
                  if e.get("homeAirport")}
        self.legs: dict[str, dict] = {}
        for f in payload.get("flights") or []:
            self.legs[f["flightNumber"]] = {
                "dep": f["departureAirport"], "arr": f["arrivalAirport"],
                "t0": _dt(f["departureUTCDateTime"]),
                "t1": _dt(f["arrivalUTCDateTime"])}
        # часовой пояс станции нужен для трансмеридианного порога отдыха;
        # если справочника нет, признак просто не срабатывает
        self.tz_of: dict[str, float] = {}
        for a in payload.get("airports") or []:
            code = a.get("code") or a.get("iata")
            off = a.get("utcOffsetHours", a.get("timeZoneOffsetHours"))
            if code is not None and off is not None:
                try:
                    self.tz_of[code] = float(off)
                except (TypeError, ValueError):
                    pass
        self.rows: dict[str, list[int]] = {}

    def _count(self, rule: str, bad: bool) -> None:
        row = self.rows.setdefault(rule, [0, 0])
        row[1] += 1
        if bad:
            row[0] += 1

    def rest_needed(self, station: str, duty_span_min: float,
                    when: datetime) -> int:
        """Требуемый отдых после смены.

        Ярусная таблица (на базе / вне базы / со сменой поясов, отдельно
        после длинной смены) — это НОРМА КОНКРЕТНОЙ авиакомпании, а не
        свойство продукта: она подаётся файлом (`--rest-table`) вместе с
        набором. Без неё действует единый порог из правил уровня 1, и
        отчёт об этом говорит — подставлять чужие числа нельзя."""
        a = self.args
        if a.rest_table is None:
            return int(a.min_rest_min)
        t = a.rest_table
        long_duty = duty_span_min > a.duty_span
        if station in self.bases:
            return int(t["base_long"] if long_duty else t["base"])
        tz_shift = t.get("tz_shift_hours")
        if tz_shift is not None:
            base_off = next((self.tz_of[b] for b in sorted(self.bases)
                             if b in self.tz_of), None)
            here = self.tz_of.get(station)
            if (base_off is not None and here is not None
                    and abs(here - base_off) >= float(tz_shift)):
                return int(t["shift_long"] if long_duty else t["shift"])
        return int(t["off_long"] if long_duty else t["off"])

    def chain_of(self, unit: dict) -> list[list[dict]]:
        """Смены цепочки как списки плеч, по порядку. Перегоны пассажиром
        в структуру рабочего дня не входят."""
        groups: list[list[dict]] = []
        active = set(unit.get("active_fns") or unit.get("fns") or [])
        for group in (unit.get("duty_fns")
                      or [unit.get("active_fns") or unit.get("fns") or []]):
            legs = [self.legs[x] for x in group
                    if x in self.legs and x in active]
            if legs:
                groups.append(sorted(legs, key=lambda x: x["t0"]))
        groups.sort(key=lambda g: g[0]["t0"])
        return groups

    def trip_of(self, unit: dict) -> list[dict]:
        """Вся командировка юнита по времени, вместе с перелётами
        пассажиром. Именно они привязывают цепочку к базе: экипаж
        уезжает из базы и возвращается в неё пассажирским местом, а к
        рабочей структуре смены такой перелёт не относится. Судить
        замкнутость по одним рабочим участкам — значит объявлять
        нарушением каждую командировку с позиционным перелётом по краю."""
        return sorted((self.legs[x] for x in (unit.get("fns") or [])
                       if x in self.legs), key=lambda x: x["t0"])

    @staticmethod
    def judged_as_chain(unit: dict) -> bool:
        """Замкнутость и размах — свойства целой командировки. Достройка
        (`kind` = fill) начала и конца командировки не имеет вовсе, а
        урезанная цепочка потеряла их при стыковке, а не по вине
        заказчика. Каталог без пометки вида судится целиком — старые
        каталоги переживают перезапуск импорта, и менять их разбор молча
        нельзя."""
        kind = unit.get("kind")
        if kind is None:
            return True
        return kind == "chain" and not unit.get("sourceLegsMissing")

    def run(self, catalog: dict) -> dict:
        a = self.args
        units = catalog.get("selected") or []
        # Все правила заводятся заранее: правило, которое не на чем было
        # проверить, обязано сказать «нечего проверять», а не исчезнуть из
        # отчёта — иначе непроверенное читается как соблюдённое.
        for rule in (f"Размах цепочки больше {a.pairing_span // 1440} суток",
                     "Цепочка не замкнута на базу",
                     f"Стыковка внутри смены меньше {a.min_rest_sit} минут",
                     "Отдых между сменами ниже ярусной таблицы"):
            self.rows.setdefault(rule, [0, 0])
        marked = [u for u in units if u.get("kind") is not None]
        self.kind_marked = len(marked) == len(units) and bool(units)
        self.fill_units = sum(1 for u in units if u.get("kind") == "fill")
        self.chains_truncated = sum(
            1 for u in units
            if u.get("kind") == "chain" and u.get("sourceLegsMissing"))
        self.chains_judged = 0
        for unit in units:
            groups = self.chain_of(unit)
            if not groups:
                continue
            if self.judged_as_chain(unit):
                self.chains_judged += 1
                trip = self.trip_of(unit) or [groups[0][0], groups[-1][-1]]
                span = (trip[-1]["t1"] - trip[0]["t0"]).total_seconds() / 60
                self._count(
                    f"Размах цепочки больше {a.pairing_span // 1440} суток",
                    span > a.pairing_span)
                self._count("Цепочка не замкнута на базу",
                            trip[0]["dep"] not in self.bases
                            or trip[-1]["arr"] not in self.bases)
            for g in groups:
                for x, y in zip(g, g[1:]):
                    sit = (y["t0"] - x["t1"]).total_seconds() / 60
                    self._count(
                        f"Стыковка внутри смены меньше {a.min_rest_sit} минут",
                        sit < a.min_rest_sit)
            for g1, g2 in zip(groups, groups[1:]):
                end1, start2 = g1[-1]["t1"], g2[0]["t0"]
                gap = (start2 - end1).total_seconds() / 60
                span1 = (end1 - g1[0]["t0"]).total_seconds() / 60
                need = self.rest_needed(g1[-1]["arr"], span1, end1)
                self._count("Отдых между сменами ниже ярусной таблицы",
                            gap < need)
        # Периметр проверки — часть вердикта: число нарушений, прочитанное
        # в отрыве от того, что именно судилось, вводит в заблуждение.
        report = {"bases": sorted(self.bases), "units": len(units),
                  "units_by_kind": _by_kind(units),
                  "units_fill": self.fill_units,
                  "chains_judged": self.chains_judged,
                  "chains_truncated": self.chains_truncated,
                  "kind_marked": self.kind_marked,
                  # Какой моделью отдыха судили: ярусная таблица — норма
                  # заказчика, единый порог — умолчание продукта. Не сказать
                  # об этом значит выдать одно за другое.
                  "rest_model": getattr(a, "rest_model", None),
                  "rules": []}
        for rule, (bad, total) in self.rows.items():
            report["rules"].append({
                "rule": rule, "violations": bad, "checked": total,
                "share": round(bad / total, 4) if total else 0.0,
                "verdict": verdict(bad, total)})
        report["hard_candidates_rejected"] = [
            r["rule"] for r in report["rules"] if r["violations"] > 0]
        # Пустой периметр — это НЕ «нарушений нет». Ноль проверок даёт ноль
        # нарушений, и конвейер, читающий только код возврата, прочтёт это
        # как «всё чисто» (аудит 2026-07-26). Признак выносится в отчёт, а
        # код возврата для этого случая отдельный.
        report["nothing_checked"] = all(r["checked"] == 0
                                        for r in report["rules"])
        report["rules_unchecked"] = [r["rule"] for r in report["rules"]
                                     if r["checked"] == 0]
        return report


def audit(payload: dict, catalog: dict,
          args: argparse.Namespace) -> dict:
    """Проверить структуру каталога против правил. Возвращает отчёт."""
    return Audit(payload, args).run(catalog)


def as_markdown(report: dict, title: str) -> str:
    out = [f"### {title}", "",
           "| Правило | Нарушений | Проверено | Доля | Вердикт |",
           "|---|---:|---:|---:|---|"]
    for r in report["rules"]:
        out.append(f"| {r['rule']} | {r['violations']} | {r['checked']} | "
                   f"{r['share']:.1%} | {r['verdict']} |")
    if report.get("rest_model"):
        out += ["", f"Модель отдыха: {report['rest_model']}."
                    + ("" if "ярусная" in report["rest_model"] else
                       " Ярусная норма авиакомпании не подана — подайте её "
                       "файлом (`--rest-table`), иначе отдых судится "
                       "единым порогом продукта.")]
    out += ["", f"Периметр: юнитов в каталоге {report['units']} "
                f"({_kinds_phrase(report['units_by_kind'])}); замкнутость и "
                f"размах судятся по {report['chains_judged']} целым "
                f"цепочкам. Не судятся: достроек "
                f"{report['units_fill']} — у них нет ни начала, ни конца "
                f"командировки; урезанных цепочек "
                f"{report['chains_truncated']} — часть их участков "
                f"досталась другому юниту или отсечена окном импорта."]
    if not report.get("kind_marked"):
        out += ["", "Каталог собран импортом без пометки вида юнита: "
                    "судятся ВСЕ записи, включая достройки. Числа "
                    "замкнутости на таком каталоге завышены — "
                    "перезапустите импорт выгрузки."]
    if report.get("nothing_checked"):
        out += ["", "**Проверять было нечего**: ни одно правило не получило "
                    "ни одной проверки. Это НЕ означает «нарушений нет» — "
                    "периметр пуст. Проверьте, что каталог относится к "
                    "этому набору и что участки цепочек в нём есть."]
    elif report.get("rules_unchecked"):
        out += ["", "Правила, по которым проверять было нечего: "
                + ", ".join(f"«{r}»" for r in report["rules_unchecked"])
                + ". Их «ноль нарушений» ничего не подтверждает."]
    if report["hard_candidates_rejected"]:
        out += ["", "Эти правила **нельзя объявлять жёсткими** на данном "
                    "наборе: они уже нарушены во входных данных, и задача "
                    "с ними станет неразрешимой."]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--payload", type=Path, required=True)
    ap.add_argument("--catalog", type=Path, required=True,
                    help="каталог структуры заказчика (base catalog)")
    ap.add_argument("--label", default="input-audit")
    ap.add_argument("--bases", default="")
    ap.add_argument("--out", type=Path, default=None)
    # Пределы берутся из действующего набора правил — того же, что
    # исполняет решатель; флаг остаётся перекрытием.
    ap.add_argument("--pairing-span", type=int, default=None)
    ap.add_argument("--duty-span", type=int, default=None)
    ap.add_argument("--min-rest-sit", type=int, default=None)
    ap.add_argument("--rules-config", type=Path, default=None,
                    help="файл правил уровня 1; по умолчанию — путь из "
                         "переменной окружения CREW_RULES_CONFIG")
    ap.add_argument("--rest-table", type=Path, default=None,
                    help="ярусная таблица отдыха авиакомпании (JSON: "
                         "base, base_long, off, off_long, shift, "
                         "shift_long, tz_shift_hours). Это НОРМА "
                         "заказчика: без файла действует единый порог из "
                         "правил уровня 1, и отчёт об этом сообщает")
    args = ap.parse_args(argv)

    import rules_config
    effective = (rules_config.load_from(str(args.rules_config))
                 if args.rules_config else rules_config.load())
    for key, arg in (("max_pairing_span_min", "pairing_span"),
                     ("max_duty_span_min", "duty_span"),
                     ("min_sit_min", "min_rest_sit")):
        if getattr(args, arg) is None:
            setattr(args, arg, int(effective[key]))
    args.min_rest_min = int(effective["min_rest_min"])
    args.rules_effective = effective
    if args.rest_table is not None:
        table = json.loads(args.rest_table.read_text(encoding="utf-8"))
        need = ("base", "base_long", "off", "off_long", "shift", "shift_long")
        missing = [k for k in need if k not in table]
        if missing:
            print(f"ОШИБКА: в таблице отдыха нет полей: "
                  f"{', '.join(missing)}", flush=True)
            return 1
        args.rest_table = table
    args.rest_model = ("ярусная таблица авиакомпании"
                       if args.rest_table is not None else
                       f"единый порог правил уровня 1: "
                       f"{args.min_rest_min} мин")

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    report = audit(payload, catalog, args)
    print(as_markdown(report, f"Входной аудит: {args.label}"))
    out = args.out or (FINAL_REPORT / f"input_audit_{args.label}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\nотчёт -> {out}")
    # Коды возврата — сигнал конвейеру, а не ошибка выполнения:
    #   0 — проверено, нарушений нет;
    #   2 — есть правила, нарушенные уже во входных данных;
    #   3 — периметр пуст: проверять было нечего. Отдельный код нужен
    #       потому, что ноль проверок даёт ноль нарушений, и код 0 здесь
    #       читался бы как «всё чисто» (аудит 2026-07-26).
    if report.get("nothing_checked"):
        return 3
    return 2 if report["hard_candidates_rejected"] else 0


if __name__ == "__main__":
    sys.exit(main())
