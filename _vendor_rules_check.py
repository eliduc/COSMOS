"""Сертификат паритета с правилами вендора (Ступень 2, 2026-07-24).

Детерминированные ПРОВЕРЯТЕЛИ вендорских правил поверх любого ростера
уровня рейсов. Работают ТОЛЬКО по уже импортированным артефактам:
canonical payload (+vendorConstraints на сотрудниках), каталог юнитов,
Пакет правил (значения параметров вендора берутся из note-записей пакета)
— сырой солвер-файл не нужен. Ни сети, ни моделей.

Правила OFF у вендора не проверяются (уважение весов). Проверки,
реализованные приближением, помечены в сертификате явно.

Usage:
    python _vendor_rules_check.py --payload P.json --roster R.json
        --catalog C.json --rules PACK.json [--label имя]
        [--out certificate.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from _solver_to_payload import _pt_minutes          # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def _dt(s: str) -> datetime:
    from _canonical_time import parse_utc
    return parse_utc(s)


def load_pack_values(pack: dict) -> Tuple[dict, dict]:
    """Пакет правил -> (vendor_parameters, weight_levels)."""
    params: dict = {}
    weights: dict = {}
    for r in pack.get("rules") or []:
        c = (r or {}).get("constraint") or {}
        if c.get("kind") != "note":
            continue
        try:
            obj = json.loads(c.get("note") or "{}")
        except json.JSONDecodeError:
            continue
        if "vendor_parameter" in obj:
            params[obj["vendor_parameter"]] = obj.get("value")
        if "vendor_constraint" in obj:
            weights[obj["vendor_constraint"]] = obj.get("weight_level")
    return params, weights


def build_timeline(payload: dict, roster: dict, catalog: dict):
    """Ростер -> по-сотруднику: юниты (интервалы) и активные участки."""
    fl = {f["flightNumber"]: f for f in payload["flights"]}
    unit_of_fn: Dict[str, int] = {}
    units = catalog.get("selected") or []
    for i, u in enumerate(units):
        for fn in u.get("active_fns") or []:
            unit_of_fn[fn] = i
    emp_units: Dict[str, set] = defaultdict(set)
    emp_legs: Dict[str, List[dict]] = defaultdict(list)
    unit_crew: Dict[int, set] = defaultdict(set)
    unassigned = 0
    for a in roster.get("flightAssignments") or []:
        emp = a.get("employee")
        if not emp:
            unassigned += 1
            continue
        fn = a.get("flight")
        if fn not in fl:
            continue
        emp_legs[emp].append(fl[fn])
        ui = unit_of_fn.get(fn)
        if ui is not None:
            emp_units[emp].add(ui)
            unit_crew[ui].add(emp)
    unit_span: Dict[int, Tuple[datetime, datetime]] = {}
    for i, u in enumerate(units):
        fns = u.get("fns") or []
        if fns and all(f in fl for f in fns):
            unit_span[i] = (_dt(fl[fns[0]]["departureUTCDateTime"]),
                            max(_dt(fl[f]["arrivalUTCDateTime"])
                                for f in fns))
    return (fl, units, emp_units, emp_legs, unit_crew, unit_span,
            unassigned)


def run_checks(payload: dict, roster: dict, catalog: dict,
               vparams: dict, weights: dict) -> dict:
    (fl, units, emp_units, emp_legs, unit_crew, unit_span,
     unassigned) = build_timeline(payload, roster, catalog)
    vc = {e["id"]: e.get("vendorConstraints") or {}
          for e in payload.get("employees") or []}
    res: Dict[str, dict] = {}

    def rule(code: str, violations: int, checked: int,
             approximation: bool = False, note: str = "",
             skipped: int = 0):
        res[code] = {"weight_level": weights.get(code, "n/a"),
                     "violations": violations, "checked": checked,
                     "approximation": approximation}
        # Пропуски из-за отсутствия данных — структурное поле, а не текст
        # примечания: по нему считается вердикт полноты, а текст читает
        # только человек.
        if skipped:
            res[code]["skipped"] = skipped
        if note:
            res[code]["note"] = note

    def enabled(code: str) -> bool:
        return str(weights.get(code, "STD")) != "OFF"

    # 1. ASSIGNMENT_CONFLICT — пересечение участков одного сотрудника.
    if enabled("ASSIGNMENT_CONFLICT"):
        conf = 0
        for emp, legs in emp_legs.items():
            iv = sorted((_dt(l["departureUTCDateTime"]),
                         _dt(l["arrivalUTCDateTime"])) for l in legs)
            for (a1, b1), (a2, b2) in zip(iv, iv[1:]):
                if a2 < b1:
                    conf += 1
        rule("ASSIGNMENT_CONFLICT", conf, len(emp_legs))

    # 2. Отдых между юнитами. Калибровка 2026-07-24: требуемый отдых ПОСЛЕ
    #    юнита несёт сам каталог (minRestAfterMin — ступенчатая таблица
    #    вендора, 98.9% совпадений с их материализованными значениями между
    #    пейрингами). Без поля — прежняя нижняя граница layoverAfterGround
    #    с честным флагом приближения.
    lag = _pt_minutes((vparams.get("layoverRules") or {})
                      .get("layoverAfterGround")) or 720
    has_exact_rest = any(isinstance(u.get("minRestAfterMin"), int)
                         and u["minRestAfterMin"] > 0 for u in units)
    if enabled("LAYOVER_BEFORE_DUTY"):
        v = 0
        pairs = 0
        for emp, uids in emp_units.items():
            spans = sorted((unit_span[u], u) for u in uids
                           if u in unit_span)
            for ((a1, b1), u1), ((a2, b2), u2) in zip(spans, spans[1:]):
                pairs += 1
                need = (units[u1].get("minRestAfterMin")
                        if has_exact_rest else lag) or lag
                if a2 >= b1 and (a2 - b1) < timedelta(minutes=need):
                    v += 1
        rule("LAYOVER_BEFORE_DUTY", v, pairs,
             approximation=not has_exact_rest,
             note=("требование из каталога (таблица порогов вендора)"
                   if has_exact_rest
                   else f"порог {lag} мин (layoverAfterGround)"))

    # 3. WEEKLY_REST — «twoPerWeek», КАЛИБРОВАНО 2026-07-24 по собственному
    #    учёту вендора (их черновик: суммарный недобор 70 888 мин против
    #    −76 089 их жёсткой оценки): в каждой календарной неделе
    #    (понедельник 00:00 местного времени базы), целиком лежащей в
    #    горизонте, СУММА периодов отдыха >= requiredWeeklyRest (48ч),
    #    причём засчитываются только периоды >= minDuration (24ч) в
    #    границах недели. Занятость — окна busyWindows юнитов (внутренние
    #    суточные ночёвки пейринга — отдых); без окон — интервал юнита.
    wr = vparams.get("weeklyRestRules") or {}
    need_total = _pt_minutes(wr.get("requiredWeeklyRest")) or 2880
    need_min = _pt_minutes(wr.get("minDurationPerWeeklyRest")) or 1440
    tz_min = int(vparams.get("_tzOffsetMin") or 0)
    if enabled("WEEKLY_REST"):
        epoch = datetime(1970, 1, 1)
        hzn_lo = min((_dt(f["departureUTCDateTime"])
                      for f in payload["flights"]), default=None)
        hzn_hi = max((_dt(f["arrivalUTCDateTime"])
                      for f in payload["flights"]), default=None)
        v = 0
        weeks_checked = 0
        shortfall_total = 0
        for emp, uids in emp_units.items():
            windows: List[List[int]] = []
            for u in uids:
                rec = units[u]
                bw = rec.get("busyWindows")
                if isinstance(bw, list) and bw:
                    windows += [[int(w[0]), int(w[1])] for w in bw
                                if isinstance(w, list) and len(w) == 2]
                elif u in unit_span:
                    a, b = unit_span[u]
                    windows.append([
                        int((a - epoch).total_seconds() // 60),
                        int((b - epoch).total_seconds() // 60)])
            if not windows:
                continue
            windows.sort()
            merged: List[List[int]] = []
            for w in windows:
                if merged and w[0] <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], w[1])
                else:
                    merged.append(list(w))
            first_busy, last_busy = merged[0][0], merged[-1][1]
            # понедельник 00:00 местного времени недели первого окна
            f_loc = epoch + timedelta(minutes=first_busy + tz_min)
            monday = f_loc.date() - timedelta(days=f_loc.isoweekday() - 1)
            wk_lo = int((datetime.combine(monday, datetime.min.time())
                         - epoch).total_seconds() // 60) - tz_min
            week = 7 * 24 * 60
            hlo = int((hzn_lo - epoch).total_seconds() // 60) \
                if hzn_lo else 0
            hhi = int((hzn_hi - epoch).total_seconds() // 60) \
                if hzn_hi else 0
            while wk_lo <= last_busy:
                wk_hi = wk_lo + week
                if (wk_lo >= hlo and wk_hi <= hhi
                        and wk_hi > first_busy and wk_lo < last_busy):
                    weeks_checked += 1
                    got = 0
                    cursor = wk_lo
                    for w in merged:
                        if w[1] <= wk_lo or w[0] >= wk_hi:
                            continue
                        if w[0] > cursor:
                            rest = min(w[0], wk_hi) - cursor
                            if rest >= need_min:
                                got += rest
                        cursor = max(cursor, min(w[1], wk_hi))
                    if cursor < wk_hi:
                        rest = wk_hi - cursor
                        if rest >= need_min:
                            got += rest
                    if got < need_total:
                        v += 1
                        shortfall_total += need_total - got
                wk_lo = wk_hi
        rule("WEEKLY_REST", v, weeks_checked,
             note=f"сумма периодов >= {need_min // 60}ч не меньше "
                  f"{need_total // 60}ч на полную неделю горизонта; "
                  f"суммарный недобор {shortfall_total} мин")

    # 3-бис. LEG_LEVEL_REQUIREMENTS / ASSIGNMENT_REQUIREMENTS (доимпорт
    # 2026-07-25): участковый код — каждому члену экипажа участка;
    # местовый код «тип_роль» — сотруднику места. Валидность — окнами
    # допусков сотрудника на интервале участка. Без данных — честный
    # статус «нет данных», а не ноль.
    emp_by_id = {e["id"]: e for e in payload.get("employees") or []}
    epoch0 = datetime(1970, 1, 1)

    def _holds(emp_id: str, code: str, lo: int, hi: int) -> bool:
        ws = (emp_by_id.get(emp_id, {})
              .get("qualificationWindows") or {}).get(code) or []
        return any(len(w) == 2 and w[0] <= lo and hi <= w[1] for w in ws)

    has_leg_req = any(f.get("requiredQualifications")
                      for f in payload["flights"])
    if enabled("LEG_LEVEL_REQUIREMENTS"):
        if not has_leg_req:
            rule("LEG_LEVEL_REQUIREMENTS", 0, 0,
                 note="нет данных: требования участков не импортированы")
        else:
            v = 0
            checked = 0
            for a in roster.get("flightAssignments") or []:
                emp2 = a.get("employee")
                f2 = fl.get(a.get("flight"))
                if not emp2 or f2 is None:
                    continue
                reqs = f2.get("requiredQualifications") or []
                if not reqs:
                    continue
                lo = int((_dt(f2["departureUTCDateTime"]) - epoch0)
                         .total_seconds() // 60)
                hi = int((_dt(f2["arrivalUTCDateTime"]) - epoch0)
                         .total_seconds() // 60)
                for code in reqs:
                    checked += 1
                    if not _holds(emp2, code, lo, hi):
                        v += 1
            rule("LEG_LEVEL_REQUIREMENTS", v, checked,
                 note="участковый код — каждому члену экипажа")

    if enabled("ASSIGNMENT_REQUIREMENTS"):
        # местовый код выводим из типа судна участка и роли места
        # (тождество проверено на всех 11640 строках источника)
        v = 0
        checked = 0
        skipped_no_windows = 0
        for a in roster.get("flightAssignments") or []:
            emp2 = a.get("employee")
            f2 = fl.get(a.get("flight"))
            if not emp2 or f2 is None:
                continue
            ac = f2.get("aircraftType")
            role = a.get("requiredSkill")
            if not ac or not role:
                continue
            code = f"{ac}_{role}"
            if not (emp_by_id.get(emp2, {})
                    .get("qualificationWindows")):
                skipped_no_windows += 1
                continue
            lo = int((_dt(f2["departureUTCDateTime"]) - epoch0)
                     .total_seconds() // 60)
            hi = int((_dt(f2["arrivalUTCDateTime"]) - epoch0)
                     .total_seconds() // 60)
            checked += 1
            if not _holds(emp2, code, lo, hi):
                v += 1
        rule("ASSIGNMENT_REQUIREMENTS", v, checked,
             skipped=skipped_no_windows,
             note=f"код места = тип судна_роль; пропущено без окон "
                  f"допусков: {skipped_no_windows}")

    # 4. FLIGHT_TIME_LIMIT — налёт за календарный месяц: точные месячные
    #    кредиты каталога (flightTimeCredit вендора), без поля — блок-
    #    минуты участков.
    has_exact_block = any(isinstance(u.get("blockMinutesByMonth"), dict)
                          and u["blockMinutesByMonth"] for u in units)
    if enabled("FLIGHT_TIME_LIMIT"):
        v = 0
        checked = 0
        if has_exact_block:
            for emp, uids in emp_units.items():
                # семантика вендора (калибровка 2026-07-24): PT0S или
                # отсутствие лимита = правило для сотрудника НЕ задано
                lim = _pt_minutes(vc.get(emp, {}).get("flightTimeLimit"))
                if not lim:
                    continue
                per_month: Counter = Counter()
                for u in uids:
                    for mk, mins in (units[u].get("blockMinutesByMonth")
                                     or {}).items():
                        per_month[mk] += int(mins)
                checked += len(per_month)
                v += sum(1 for m, tot in per_month.items() if tot > lim)
        else:
            for emp, legs in emp_legs.items():
                per_month = Counter()
                for l in legs:
                    d0 = _dt(l["departureUTCDateTime"])
                    per_month[d0.strftime("%Y-%m")] += int(
                        (_dt(l["arrivalUTCDateTime"]) - d0)
                        .total_seconds() // 60)
                lim = _pt_minutes(vc.get(emp, {}).get("flightTimeLimit")) \
                    or 6000
                checked += len(per_month)
                v += sum(1 for m, tot in per_month.items() if tot > lim)
        rule("FLIGHT_TIME_LIMIT", v, checked,
             note="месячные кредиты налёта из каталога"
                  if has_exact_block else "")

    # 5. WORK_TIME_LIMIT — рабочее время за месяц. Калибровка 2026-07-24:
    #    точные месячные кредиты рабочего времени несёт каталог
    #    (workMinutesByMonth из кредитов вендора; на их черновике — 1
    #    нарушитель из 686, их собственный). Без поля — прежняя оценка
    #    дневными окнами с флагом приближения.
    has_exact_work = any(isinstance(u.get("workMinutesByMonth"), dict)
                         and u["workMinutesByMonth"] for u in units)
    if enabled("WORK_TIME_LIMIT"):
        v = 0
        checked = 0
        if has_exact_work:
            for emp, uids in emp_units.items():
                # семантика вендора: PT0S/отсутствие = лимита нет
                lim = _pt_minutes(vc.get(emp, {}).get("workTimeLimit"))
                if not lim:
                    continue
                per_month: Counter = Counter()
                for u in uids:
                    for mk, mins in (units[u].get("workMinutesByMonth")
                                     or {}).items():
                        per_month[mk] += int(mins)
                checked += len(per_month)
                v += sum(1 for m, tot in per_month.items() if tot > lim)
        else:
            for emp, legs in emp_legs.items():
                per_day = defaultdict(list)
                for l in legs:
                    d0 = _dt(l["departureUTCDateTime"])
                    per_day[d0.date()].append(
                        (d0, _dt(l["arrivalUTCDateTime"])))
                per_month = Counter()
                for day, ivs in per_day.items():
                    lo = min(a for a, b in ivs)
                    hi = max(b for a, b in ivs)
                    per_month[day.strftime("%Y-%m")] += int(
                        (hi - lo).total_seconds() // 60)
                lim = _pt_minutes(vc.get(emp, {}).get("workTimeLimit")) \
                    or 8640
                checked += len(per_month)
                v += sum(1 for m, tot in per_month.items() if tot > lim)
        rule("WORK_TIME_LIMIT", v, checked,
             approximation=not has_exact_work,
             note=("месячные кредиты рабочего времени из каталога"
                   if has_exact_work
                   else "рабочее время оценено дневными окнами"))

    # 6. maximumPairingDays — длительность юнита.
    mp = vparams.get("maximumPairingDays")
    if isinstance(mp, int) and mp > 0:
        v = sum(1 for (a, b) in unit_span.values()
                if (b - a) > timedelta(days=mp))
        rule("CREW_PAIRING_DURATION_CONTROL", v, len(unit_span))

    # 7. DO_NOT_FLY_WITH — пара несовместимых в одном юните. Списки
    # вендора ОДНОСТОРОННИЕ — считаем неупорядоченные пары на юнит.
    if enabled("DO_NOT_FLY_WITH"):
        pairs = set()
        for ui, crew in unit_crew.items():
            for emp in crew:
                for b in (set(vc.get(emp, {}).get("doNotFlyWith") or [])
                          & crew):
                    if b != emp:
                        pairs.add((ui, tuple(sorted((emp, b)))))
        rule("DO_NOT_FLY_WITH", len(pairs), len(unit_crew))

    # 8. DO_NOT_VISIT_DENIED_STATIONS — юнит сотрудника через запретную
    #    станцию.
    if enabled("DO_NOT_VISIT_DENIED_STATIONS"):
        v = 0
        for emp, uids in emp_units.items():
            denied = set(vc.get(emp, {}).get("stationsDenied") or [])
            if not denied:
                continue
            for ui in uids:
                for fn in (units[ui].get("fns") or []):
                    l = fl.get(fn)
                    if l and (l["departureAirport"] in denied
                              or l["arrivalAirport"] in denied):
                        v += 1
                        break
        rule("DO_NOT_VISIT_DENIED_STATIONS", v, len(emp_units))

    # 9. LAYOVER_DENIED_STATIONS — ночёвка (>8ч между участками юнита)
    #    в запретной станции.
    if enabled("LAYOVER_DENIED_STATIONS"):
        v = 0
        for emp, uids in emp_units.items():
            denied = set(vc.get(emp, {}).get("layoverDenied") or [])
            if not denied:
                continue
            for ui in uids:
                fns = units[ui].get("fns") or []
                legs = [fl[f] for f in fns if f in fl]
                for l1, l2 in zip(legs, legs[1:]):
                    gap = _dt(l2["departureUTCDateTime"]) \
                        - _dt(l1["arrivalUTCDateTime"])
                    if gap > timedelta(hours=8) \
                            and l1["arrivalAirport"] in denied:
                        v += 1
        rule("LAYOVER_DENIED_STATIONS", v, len(emp_units))

    # 10. UNASSIGNED — незанятые требуемые места (информативно).
    if enabled("UNASSIGNED"):
        rule("UNASSIGNED", unassigned,
             len(roster.get("flightAssignments") or []))

    not_impl = sorted(c for c in weights
                      if c not in res and str(weights[c]) != "OFF")
    return {"rules": res,
            "employees_with_work": len(emp_legs),
            "not_implemented": not_impl,
            "off_respected": sorted(c for c in weights
                                    if str(weights[c]) == "OFF")}


# Объявленная ОБЩАЯ шкала сравнения решений (2026-07-24): файл вендора
# задаёт УРОВНИ важности правил, но не численные веса — поэтому шкала
# объявляется здесь явно и применяется СИММЕТРИЧНО обоим решениям.
# Сравнение лексикографическое: сначала число жёстких нарушений, при
# равенстве — взвешенный мягкий штраф.
LEVEL_TIER = {
    "HIGH_HIGH": ("hard", 1),
    "HIGH": ("hard", 1),
    "STD": ("soft", 1000),
    "LOW": ("soft", 100),
    "LOW_LOW": ("soft", 10),
    "OFF": (None, 0),
}


def common_score(cert_rules: dict) -> dict:
    """Единый счёт по объявленной шкале: (жёсткие, взвешенные мягкие)."""
    hard = 0
    soft = 0
    for code, r in cert_rules.items():
        tier, w = LEVEL_TIER.get(str(r.get("weight_level")),
                                 ("soft", 1000))
        v = int(r.get("violations") or 0)
        if tier == "hard":
            hard += v
        elif tier == "soft":
            soft += v * w
    return {"hard_violations": hard, "weighted_soft_penalty": soft,
            "scale": {k: {"tier": t, "weight": w}
                      for k, (t, w) in LEVEL_TIER.items()}}


def completeness(cert: dict, weights: dict) -> dict:
    """Вердикт ПОЛНОТЫ сертификата (аудит 2026-07-26, P0-06).

    «Ноль нарушений» — утверждение о том, что проверено. Пока не сказано,
    что именно осталось непроверенным, такой сертификат читается как
    доказательство соответствия, которым он не является. Полнота
    нарушается тремя способами, и каждый называется отдельно:

      * включённое ЖЁСТКОЕ правило, для которого проверятеля нет вовсе;
      * правило, которое не получило ни одной проверки (`checked = 0`) —
        ноль нарушений из нуля проверок не подтверждает ничего;
      * проверки, пропущенные из-за отсутствия данных (например, места
        без окон допусков): часть ростера осталась вне проверки.
    """
    reasons: List[str] = []
    hard_unimplemented = sorted(
        c for c in cert.get("not_implemented") or []
        if LEVEL_TIER.get(str(weights.get(c)), (None, 0))[0] == "hard")
    if hard_unimplemented:
        reasons.append("жёсткие правила без проверятеля: "
                       + ", ".join(hard_unimplemented))
    zero_checked = sorted(c for c, r in (cert.get("rules") or {}).items()
                          if not r.get("checked"))
    if zero_checked:
        reasons.append("правила без единой проверки: "
                       + ", ".join(zero_checked))
    skipped = {c: r["skipped"] for c, r in (cert.get("rules") or {}).items()
               if r.get("skipped")}
    if skipped:
        reasons.append("проверки, пропущенные из-за отсутствия данных: "
                       + ", ".join(f"{c} ({n})"
                                   for c, n in sorted(skipped.items())))
    return {"verdict": "INCOMPLETE" if reasons else "OK",
            "reasons": reasons,
            "hard_rules_unimplemented": hard_unimplemented,
            "rules_without_checks": zero_checked,
            "checks_skipped_for_missing_data": skipped}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="сертификат паритета с правилами вендора")
    ap.add_argument("--payload", type=Path, required=True)
    ap.add_argument("--roster", type=Path, required=True)
    ap.add_argument("--catalog", type=Path, required=True)
    ap.add_argument("--rules", type=Path, required=True)
    ap.add_argument("--label", default="roster")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--tz-offset-min", type=int, default=0,
                    help="смещение местного времени базы в минутах "
                         "(границы недель еженедельного отдыха); "
                         "для Базовыйа 300")
    args = ap.parse_args(argv)

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    roster = json.loads(args.roster.read_text(encoding="utf-8"))
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    pack = json.loads(args.rules.read_text(encoding="utf-8"))
    vparams, weights = load_pack_values(pack)
    vparams["_tzOffsetMin"] = args.tz_offset_min
    cert = run_checks(payload, roster, catalog, vparams, weights)
    cert = {"label": args.label, "payload": args.payload.name,
            "roster": args.roster.name, "rules_pack": args.rules.name,
            **cert}
    cert["common_score"] = common_score(cert["rules"])
    cert["completeness"] = completeness(cert, weights)
    print(f"=== Сертификат паритета: {args.label} ===")
    for code, r in sorted(cert["rules"].items()):
        approx = " (приближение)" if r.get("approximation") else ""
        print(f"  {code:34s} [{r['weight_level']:9s}] "
              f"нарушений {r['violations']:6d} / проверено "
              f"{r['checked']}{approx}")
    print(f"  не реализовано проверятелей: {len(cert['not_implemented'])}; "
          f"выключенных вендором (OFF) уважено: "
          f"{len(cert['off_respected'])}")
    cs = cert["common_score"]
    print(f"  ЕДИНЫЙ СЧЁТ: жёстких нарушений {cs['hard_violations']}, "
          f"взвешенный мягкий штраф {cs['weighted_soft_penalty']}")
    comp = cert["completeness"]
    print(f"  ПОЛНОТА: {comp['verdict']}")
    for r in comp["reasons"]:
        print(f"    — {r}")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(cert, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        print(f"  сертификат -> {args.out}")
    # Коды возврата — сигнал конвейеру:
    #   0 — полный сертификат без жёстких нарушений;
    #   2 — есть жёсткие нарушения;
    #   3 — сертификат НЕПОЛОН: часть правил не проверена, и «ноль
    #       нарушений» ничего не доказывает. Раньше во всех трёх случаях
    #       возвращался 0 (аудит 2026-07-26, P0-06).
    if cs["hard_violations"]:
        return 2
    return 3 if comp["verdict"] != "OK" else 0


if __name__ == "__main__":
    sys.exit(main())
