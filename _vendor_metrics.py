"""Метрики системы заказчика, пересчитанные на НАШЕМ решении.

Заказчик оценивает ростер набором метрик своей системы. Пока эти же
величины не посчитаны на нашем решении по ЕГО определениям, сравнение
«их решение против нашего» невозможно: стороны говорят о разных числах.

Считается на КАНОНИЧЕСКОМ наборе и артефактах этапа 2, а не на формате
источника: любой набор, доведённый до канона, меряется одинаково. Файл
заказчика принимается необязательным справочником — из него берутся
список кодов метрик с описаниями и их собственные значения (колонка для
сравнения), плановое окно и стоимость ночёвки по станциям.

Определения нашей стороны объявлены явно, иначе сопоставимость нечестна:
  * пояс учёта задаётся ключом (`--tz-offset-min`);
  * «полётная смена» = рабочий день внутри назначенной цепочки: окно
    «первый вылет — последний прилёт» по местной дате начала участков;
    окно строится по ВСЕМ участкам дня (включая перелёты пассажиром —
    экипаж в это время на работе), налёт — только по рабочим;
  * «назначение» = человек x смена; «сегмент» = человек x рабочий участок;
  * «ночёвка» = человеко-ночь между последовательными сменами внутри
    цепочки; станция ночёвки = станция прилёта последнего участка дня;
    «на базе» = станция совпадает с базой сотрудника.

Метрики, у которых нет опубликованной формулы заказчика, помечаются
«нужна формула» и НЕ выдумываются: выдуманное число в сравнении хуже,
чем честный пропуск.

Детерминированность: ни одного обращения к языковым моделям и к сети.

Использование:
    python _vendor_metrics.py --payload payload.json \
        --assignment run-assignment.json --solution run-solution.json \
        [--vendor solver.json] [--tz-offset-min 300] \
        [--out-md metrics.md] [--out-json metrics.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from _cli_console import force_utf8_console  # noqa: E402

force_utf8_console()

NEED_FORMULA = "нужна формула/определение"

# Метрики, которые считаются по определению заказчика и потому требуют
# его формулы. Держим списком, а не по месту: так видно, чего не хватает.
UNDEFINED_NOTES = {
    "ASSIGNMENTS_CLOSE_TO_LIMIT": "порог «близости к пределу» не объявлен",
    "ENFORCED_PAIRING_COUNT": "определение «усиленного экипажа» не объявлено",
    "ENFORCED_ASSIGNMENTS": "определение «усиленного экипажа» не объявлено",
    "NIGHT_ASSIGNMENTS": "определение «ночной смены» не объявлено",
    "USED_DEADHEADS_COST": "модель стоимости перегона не объявлена",
}


def _dt(value: str) -> datetime:
    from _canonical_time import parse_utc
    return parse_utc(value)


def _pt(minutes: int) -> str:
    return f"PT{minutes // 60}H{minutes % 60}M"


def _plan_bound(raw) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return _dt(raw)
    except ValueError:
        return None


class Metrics:
    """Пересчёт метрик заказчика на нашем решении."""

    def __init__(self, payload: dict, assignment: dict, solution: dict,
                 vendor: Optional[dict], tz_offset_min: int,
                 plan_from: Optional[datetime],
                 plan_until: Optional[datetime],
                 catalog_units: Optional[int] = None) -> None:
        self.tz = timedelta(minutes=tz_offset_min)
        self.payload = payload
        self.flights = {f["flightNumber"]: f
                        for f in payload.get("flights") or []}
        self.emp_base = {str(e["id"]): e.get("homeAirport") or e.get("base")
                         for e in payload.get("employees") or []}
        vendor = vendor or {}
        params = vendor.get("parameters") or {}
        span = [f["departureUTCDateTime"] for f in self.flights.values()]
        self.plan_from = (plan_from or _plan_bound(params.get("planSince"))
                          or (_dt(min(span)) if span else datetime.min))
        self.plan_until = (plan_until or _plan_bound(params.get("planUntil"))
                           or (_dt(max(span)) if span else datetime.max))
        self.station_cost: Dict[str, float] = {}
        for st in vendor.get("stations") or []:
            if isinstance(st, dict):
                code = st.get("iata") or st.get("code")
                if code is not None and st.get("cost") is not None:
                    self.station_cost[code] = st["cost"]
        self.vendor_metrics = {m["metricCode"]: m
                               for m in (vendor.get("metrics") or [])
                               if isinstance(m, dict) and m.get("metricCode")}
        self.assignment = assignment
        self.solution = solution
        self.catalog_units = catalog_units

    # ---------------- разбор цепочек: дни, окна, ночёвки ----------------

    def _unit_days(self, fns: List[str], active: set) -> List[Tuple]:
        days: Dict[date, dict] = {}
        legs = sorted((self.flights[fn] for fn in fns if fn in self.flights),
                      key=lambda l: l["departureUTCDateTime"])
        for l in legs:
            dep, arr = (_dt(l["departureUTCDateTime"]),
                        _dt(l["arrivalUTCDateTime"]))
            d = (dep + self.tz).date()
            rec = days.setdefault(d, {"lo": dep, "hi": arr, "segs": 0,
                                      "block": 0, "last_arr": None,
                                      "ac": set()})
            rec["lo"] = min(rec["lo"], dep)
            if arr >= rec["hi"]:
                rec["hi"] = arr
                rec["last_arr"] = l["arrivalAirport"]
            if l["flightNumber"] in active:
                rec["segs"] += 1
                rec["block"] += int((arr - dep).total_seconds() // 60)
                if l.get("aircraftType"):
                    rec["ac"].add(l["aircraftType"])
        return sorted(days.items())

    def compute(self) -> Dict[str, Tuple]:
        pairs = [p for p in self.assignment.get("pairings") or []
                 if isinstance(p, dict)]
        windows: List[int] = []
        blocks: List[int] = []
        segs: List[int] = []
        ac_change = 0
        person_nights: List[Tuple[int, Optional[str], Optional[str]]] = []
        duty_objects = 0
        used = 0
        dh_assignments = 0
        closed = one_day = with_dh = 0
        day_hist: Counter = Counter()
        assigned: set = set()
        for p in pairs:
            crew = [str(c["employee"]) for c in (p.get("crew") or [])
                    if isinstance(c, dict) and c.get("employee")]
            if not crew and p.get("employee"):
                crew = [str(p["employee"])]
            if not crew:
                continue
            used += 1
            assigned.update(crew)
            fns = list(p.get("fns") or [])
            active = set(p.get("active_fns") or fns)
            day_list = self._unit_days(fns, active)
            duty_objects += len(day_list)
            day_hist[min(len(day_list), 6)] += 1
            for _d, rec in day_list:
                w = int((rec["hi"] - rec["lo"]).total_seconds() // 60)
                for _e in crew:
                    windows.append(w)
                    blocks.append(rec["block"])
                    segs.append(rec["segs"])
                if len(rec["ac"]) > 1:
                    ac_change += len(crew)
            for (d0, r0), (d1, _r1) in zip(day_list, day_list[1:]):
                nights = (d1 - d0).days
                if nights >= 1:
                    for e in crew:
                        person_nights.append((nights, r0["last_arr"],
                                              self.emp_base.get(e)))
            dh_assignments += (len(fns) - len(active)) * len(crew)
            if len(fns) - len(active):
                with_dh += 1
            legs = sorted((self.flights[fn] for fn in fns
                           if fn in self.flights),
                          key=lambda l: l["departureUTCDateTime"])
            if legs and legs[0]["departureAirport"] == \
                    legs[-1]["arrivalAirport"]:
                closed += 1
            if len(day_list) == 1:
                one_day += 1

        # Недобор мест считается СВЕРКОЙ СО СПРОСОМ НАБОРА, а не по тому,
        # что перечислено в файле решения (аудит 2026-07-26, P0-06).
        # Решение, где незанятые места просто отсутствуют, иначе даёт
        # «ноль незанятых» — благополучие там, где его нет.
        demand: Counter = Counter()
        for a in self.payload.get("flightAssignments") or []:
            if a.get("flight"):
                demand[a["flight"]] += 1
        slots = self.solution.get("flightAssignments") or []
        filled_by_fn: Counter = Counter()
        for a in slots:
            if a.get("employee"):
                filled_by_fn[a["flight"]] += 1
        filled = [a for a in slots if a.get("employee")]
        short: Counter = Counter()
        over: Counter = Counter()
        for fn, need in demand.items():
            gap = need - filled_by_fn.get(fn, 0)
            if gap > 0:
                short[fn] = gap
            elif gap < 0:
                # ПЕРЕБОР мест тоже дефект сверки (повторный аудит
                # 2026-07-26): спрос 1, занято 2 давало «недобор 0, OK» —
                # лишняя строка решения маскировала расхождение артефактов
                # так же, как чужой рейс.
                over[fn] = -gap
        # Рейс, которого нет в спросе набора, но который занят в решении, —
        # признак того, что артефакты от разных прогонов.
        self.alien_flights = sorted(set(filled_by_fn) - set(demand))
        self.overfilled_flights = sorted(over)
        self.overfill_total = sum(over.values())
        self.demand_total = sum(demand.values())
        self.filled_total = sum(filled_by_fn.values())
        self.shortfall_total = sum(short.values())
        in_plan = [f for f in self.flights.values()
                   if self.plan_from <= _dt(f["departureUTCDateTime"])
                   + self.tz <= self.plan_until]
        fn_in_plan = {f["flightNumber"] for f in in_plan}
        unc_in_plan = [fn for fn in short if fn in fn_in_plan]

        nights_total = sum(n for n, _, _ in person_nights)
        on_base = sum(n for n, st, b in person_nights if st and st == b)
        cost = 0.0
        no_cost = 0
        for n, st, _b in person_nights:
            c = self.station_cost.get(st)
            if c:
                cost += n * c
            else:
                no_cost += n
        total_ft, total_wt, n_assign = (sum(blocks), sum(windows),
                                        len(windows))

        wt_b = {"4": 0, "6": 0, "8": 0, "10": 0, "11": 0, "100": 0}
        for w in windows:
            h = w / 60
            key = ("4" if h <= 4 else "6" if h <= 6 else "8" if h <= 8
                   else "10" if h <= 10 else "11" if h <= 11 else "100")
            wt_b[key] += 1
        seg_h: Counter = Counter(min(s, 5) for s in segs)

        emps = len(self.payload.get("employees") or [])
        o: Dict[str, Tuple] = {}
        o["EMPLOYEE_COUNT"] = (emps, "пул сотрудников набора")
        o["FREE_EMPLOYEE"] = (emps - len(assigned), "без единой цепочки")
        o["DUTY_COUNT"] = (duty_objects,
                           "только полётные смены: наземных и резервных "
                           "мероприятий в нашем периметре нет")
        o["FLIGHT_DUTY_COUNT"] = (duty_objects,
                                  "юнит-дни назначенных цепочек")
        o["ASSIGNMENT_COUNT"] = (n_assign, "человек x смена")
        o["FLIGHT_ASSIGNMENT_COUNT"] = (n_assign, "человек x полётная смена")
        o["IGNORED_FLIGHTS"] = ("н/д", "периметр системы заказчика")
        o["UNASSIGNED_FLIGHT_COUNT"] = (
            len(unc_in_plan),
            "плечи периода планирования, где заполнены не все места "
            "(сверка со спросом набора, а не с перечнем в файле решения)")
        o["UNASSIGNED_FLIGHT_RATIO"] = (
            round(len(unc_in_plan) / max(1, len(in_plan)), 3),
            f"от {len(in_plan)} плеч периода")
        o["FLIGHTS_COUNT"] = (len(self.flights), "плечи нашего горизонта")
        o["FLIGHTS_BEFORE_PLAN_SINCE"] = (
            sum(1 for f in self.flights.values()
                if _dt(f["departureUTCDateTime"]) + self.tz
                < self.plan_from), "")
        o["FLIGHTS_AFTER_PLAN_UNTIL"] = (
            sum(1 for f in self.flights.values()
                if _dt(f["departureUTCDateTime"]) + self.tz
                > self.plan_until), "")
        o["RELAY_NIGHTS_TOTAL"] = (
            nights_total, "человеко-ночи между сменами назначенных цепочек")
        o["RELAY_ON_BASE"] = (on_base, "станция ночёвки = база сотрудника")
        o["RELAY_OFF_BASE"] = (nights_total - on_base, "")
        o["RELAY_COST_TOTAL"] = (
            int(cost), "по стоимости станции из справочника заказчика")
        o["RELAY_NIGHTS_NO_COST"] = (no_cost,
                                     "станция без цены в справочнике")
        o["RELAY_SPLIT"] = (0, "разделённые смены не строим")
        o["AC_CHANGE"] = (ac_change,
                          "смена ТИПА судна внутри одного рабочего дня; "
                          "смена борта при одном типе не видна")
        o["USED_DEADHEADS_COUNT"] = (
            dh_assignments, "человек x перелёт пассажиром внутри цепочки")
        o["USED_DEADHEADS_OUR"] = (dh_assignments, "все на своих рейсах")
        o["USED_DEADHEADS_OTHER"] = (0, "сторонние рейсы не используем")
        o["USED_DEADHEADS_GRP"] = (0, "наземных перегонов нет")
        o["FLIGHT_TIME_AVERAGE"] = (_pt(int(total_ft / max(1, n_assign))),
                                    "средний налёт за смену")
        o["WORK_TIME_AVERAGE"] = (_pt(int(total_wt / max(1, n_assign))),
                                  "средняя длительность окна смены")
        o["RATIO_WT_FT_AVERAGE"] = (round(total_wt / max(1, total_ft), 3), "")
        o["LEG_ASSIGNMENTS"] = (
            len(filled) + dh_assignments,
            "человек x участок: рабочие места и перелёты пассажиром")
        o["0_FLIGHT"] = (0, "наземных и резервных мероприятий нет")
        for k in (1, 2, 3, 4):
            o[f"{k}_FLIGHT"] = (seg_h.get(k, 0), "")
        o["5_FLIGHT"] = (seg_h.get(5, 0), "5 и более")
        for code in ("4", "6", "8", "10", "11", "100"):
            o[f"{code}_THRESHOLD_WT"] = (wt_b[code], "")
        o["SPLIT_ASSIGNMENTS"] = (0, "см. RELAY_SPLIT")
        o["EXTENDED_ASSIGNMENTS"] = (0, "продлённые смены выключены")
        o["TOTAL_FT"] = (_pt(total_ft), "сумма налёта по назначениям")
        o["TOTAL_WT"] = (_pt(total_wt), "сумма окон смен по назначениям")
        o["PAIRING_COUNT"] = (
            used, f"назначенные цепочки"
            + (f" из {self.catalog_units} в каталоге"
               if self.catalog_units else ""))
        o["PRODUCTION_DAYS"] = (n_assign, "человеко-дни на сменах")
        for k, code in ((1, "ONEDAY"), (2, "TWODAY"), (3, "THREEDAY"),
                        (4, "FOURDAY"), (5, "FIVEDAY"), (6, "SIXDAY")):
            o[f"{code}_SEQUENCE_COUNT"] = (day_hist.get(k, 0),
                                           "назначенные цепочки такой длины")
        o["CLOSED_PAIRING_COUNT"] = (
            closed, "первый вылет и последний прилёт на одной станции")
        o["ONE_FLIGHT_PAIRING_COUNT"] = (one_day, "цепочки с одним днём")
        o["DH_PAIRING"] = (with_dh, "цепочки с перелётами пассажиром")
        o["FLIGHT_TIME_COST__ALL"] = (0, "правило стоимости налёта выключено")
        for code, why in UNDEFINED_NOTES.items():
            o[code] = (NEED_FORMULA, why)
        return o


    def completeness(self) -> dict:
        """Вердикт полноты ИЗМЕРЕНИЯ (аудит 2026-07-26, P0-06).

        Метрики, посчитанные на неполном или чужом артефакте, выглядят
        как метрики полного решения. Три условия делают измерение
        недостоверным: прогон не прошёл гейт этапа 2; решение ссылается
        на рейсы, которых нет в спросе набора (артефакты от разных
        прогонов); спроса нет вовсе — мерить нечего.

        Недобор мест недостоверности НЕ означает: это честное свойство
        решения, и оно публикуется числом, а не прячется.
        """
        reasons: List[str] = []
        status = str(self.assignment.get("result_status") or "неизвестен")
        if status != "OK":
            reasons.append(
                f"вердикт этапа 2 — {status}, а не OK: решение не прошло "
                f"гейт и не является публикуемым ростером")
        if self.alien_flights:
            reasons.append(
                f"решение занимает {len(self.alien_flights)} рейс(ов), "
                f"которых нет в спросе набора — артефакты, похоже, от "
                f"разных прогонов")
        if self.overfill_total:
            reasons.append(
                f"решение занимает больше мест, чем просит спрос, на "
                f"{self.overfill_total} (рейсов: "
                f"{len(self.overfilled_flights)}) — лишние строки решения "
                f"так же указывают на расхождение артефактов, как чужие "
                f"рейсы")
        if not self.demand_total:
            reasons.append("в наборе нет ни одного места экипажа — "
                           "мерить нечего")
        return {"verdict": "INCOMPLETE" if reasons else "OK",
                "reasons": reasons,
                "stage2_result_status": status,
                "demand_seats": self.demand_total,
                "filled_seats": self.filled_total,
                "shortfall_seats": self.shortfall_total,
                "overfill_seats": self.overfill_total,
                "alien_flights": self.alien_flights[:20]}


def as_markdown(rows: List[dict], title: str, note: str) -> str:
    out = [f"# {title}", "", note, "",
           "| Код | Описание | Заказчик | COSMOS | Примечание |",
           "|---|---|---|---|---|"]
    computed = asked = 0
    for r in rows:
        if r["ours"] == NEED_FORMULA:
            asked += 1
        elif r["ours"] not in ("??", "н/д"):
            computed += 1
        out.append(f"| {r['code']} | {r['description']} | {r['theirs']} | "
                   f"{r['ours']} | {r['note']} |")
    out += ["", f"Вычислено напрямую: {computed} из {len(rows)}; требуют "
                f"формулы заказчика: {asked}."]
    return "\n".join(out) + "\n"


def build_rows(ours: Dict[str, Tuple], vendor_metrics: dict,
               notes: Optional[dict] = None) -> List[dict]:
    """Строки отчёта в порядке метрик заказчика; коды, которых у нас нет,
    называются вслух, а не пропускаются молча."""
    notes = notes or {}
    order = list(vendor_metrics) or sorted(ours)
    rows = []
    for code in order:
        m = vendor_metrics.get(code) or {}
        value, note = ours.get(code, ("??", "НЕ КЛАССИФИЦИРОВАНО"))
        rows.append({
            "code": code,
            "description": (m.get("metricDescription") or "").strip(),
            "theirs": m.get("metricValue"),
            "ours": value,
            "note": notes.get(code, note)})
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--payload", type=Path, required=True)
    ap.add_argument("--assignment", type=Path, required=True,
                    help="артефакт назначений этапа 2 (*-assignment.json)")
    ap.add_argument("--solution", type=Path, required=True,
                    help="развёрнутое решение этапа 2 (*-solution.json)")
    ap.add_argument("--vendor", type=Path, default=None,
                    help="файл заказчика: коды метрик с описаниями и его "
                         "значения, плановое окно, стоимость станций")
    ap.add_argument("--catalog", type=Path, default=None,
                    help="каталог цепочек — только для знаменателя "
                         "«назначено из скольких»")
    ap.add_argument("--notes", type=Path, default=None,
                    help="JSON «код метрики -> примечание»: пояснения, "
                         "относящиеся к конкретному набору")
    ap.add_argument("--tz-offset-min", type=int, default=0,
                    help="пояс учёта рабочего дня, минуты от UTC")
    ap.add_argument("--plan-from", default=None)
    ap.add_argument("--plan-until", default=None)
    ap.add_argument("--title", default="Метрики заказчика на нашем решении")
    ap.add_argument("--allow-incomplete", action="store_true",
                    help="не отказывать, когда измерение неполно (вердикт "
                         "этапа 2 не OK или артефакты от разных прогонов): "
                         "метрики всё равно считаются, но код возврата "
                         "остаётся нулевым — осознанное снятие отказа")
    ap.add_argument("--out-md", type=Path, default=None)
    ap.add_argument("--out-json", type=Path, default=None)
    args = ap.parse_args(argv)

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    assignment = json.loads(args.assignment.read_text(encoding="utf-8"))
    solution = json.loads(args.solution.read_text(encoding="utf-8"))
    vendor = (json.loads(args.vendor.read_text(encoding="utf-8"))
              if args.vendor else None)
    notes = (json.loads(args.notes.read_text(encoding="utf-8"))
             if args.notes else None)
    units = None
    if args.catalog:
        units = len(json.loads(args.catalog.read_text(
            encoding="utf-8")).get("selected") or [])

    m = Metrics(payload, assignment, solution, vendor, args.tz_offset_min,
                _plan_bound(args.plan_from), _plan_bound(args.plan_until),
                units)
    rows = build_rows(m.compute(), m.vendor_metrics, notes)
    comp = m.completeness()
    label = assignment.get("label") or assignment.get("inst") or "прогон"
    note = (f"Прогон: {label}. Периметр нашей стороны — горизонт набора; "
            f"периметр метрик заказчика задаётся его системой. Строки, где "
            f"периметры расходятся, снабжены примечанием.\n\n"
            f"Знаменатель назван явно: спрос набора **{comp['demand_seats']} "
            f"мест**, занято **{comp['filled_seats']}**, недобор "
            f"**{comp['shortfall_seats']}**. Вердикт этапа 2: "
            f"**{comp['stage2_result_status']}**. Полнота измерения: "
            f"**{comp['verdict']}**."
            + ("".join(f"\n* {r}" for r in comp["reasons"])
               if comp["reasons"] else ""))
    md = as_markdown(rows, args.title, note)
    if args.out_md:
        args.out_md.parent.mkdir(parents=True, exist_ok=True)
        args.out_md.write_text(md, encoding="utf-8")
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(
            json.dumps({"completeness": comp,
                        "metrics": {r["code"]: {"their": r["theirs"],
                                                "ours": r["ours"],
                                                "note": r["note"]}
                                    for r in rows}},
                       ensure_ascii=False, indent=1), encoding="utf-8")
    print(md)
    if comp["verdict"] != "OK":
        print("\nПОЛНОТА ИЗМЕРЕНИЯ: INCOMPLETE")
        for r in comp["reasons"]:
            print(f"  — {r}")
        if not args.allow_incomplete:
            print("  Метрики посчитаны, но предъявлять их как измерение "
                  "полного решения нельзя. Ключ --allow-incomplete снимает "
                  "отказ осознанно.")
            return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
