# -*- coding: utf-8 -*-
"""Приёмка каталога цепочек: соответствие данным и правилам постановки.

Проверки идут ПРОТИВ исходного набора и загруженного документа, а не
против внутреннего отчёта построителя: отчёт может ошибаться вместе с
решением. Каждая проверка сообщает, сколько объектов рассмотрела — ноль
нарушений при нуле рассмотренного считается отказом, а не успехом.

Модуль, а не скрипт: тем же кодом пользуется кнопка в браузере и
командная строка, поэтому «принято на экране» и «принято в консоли» не
могут разойтись.

Три ловушки, на которых эта приёмка уже ошибалась и которые здесь
закрыты явно:
  * каталог адресует рейсы wire-именами (`ZZ613@1`) — без разрешения
    через `wire_map` проверки не находят рейсов и молча дают нули;
  * правило стыковки относится к РАБОЧИМ участкам, посадка на перевозку
    живёт по своему правилу;
  * длительность цепочки считается затронутыми календарными датами в
    зоне базы, а не длительностью в сутках.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import _cos_rules


class AuditError(RuntimeError):
    """Приёмку выполнить нельзя — с названной причиной, а не молча."""


def base_of(pay, base=None):
    """База приписки — ИЗ ДАННЫХ, а не из литерала в коде.

    Раньше здесь стоял код аэропорта константой, и приёмка сравнивала
    с ним первый вылет и последний прилёт каждой цепочки. Обезличивание
    набора поменяло эту константу — проверки «цепочка начинается на
    базе» и «цепочка заканчивается на базе» перевернулись бы почти на
    всех цепочках, причём правдоподобно: числа остались бы числами.
    Литерал в коде, решающий правильность на данных заказчика, — тот же
    класс «число ниоткуда», что вес и численность экипажа.

    Источник истины — сами люди: у сотрудника есть база приписки.
    Явно переданная база побеждает; нет ни явной, ни выводимой — отказ,
    а не подстановка.
    """
    if base:
        return str(base), "задана явно"
    homes = Counter(
        str(e.get("homeAirport") or "").strip()
        for e in (pay.get("employees") or [])
        if str(e.get("homeAirport") or "").strip())
    if not homes:
        raise AuditError(
            "база приписки не выводится: в наборе нет сотрудников с полем "
            "homeAirport. Подставлять код аэропорта из кода нельзя — "
            "передайте базу явно")
    top, count = homes.most_common(1)[0]
    total = sum(homes.values())
    if len(homes) == 1:
        return top, "выведена из набора (все %d сотрудников)" % total
    return top, ("выведена из набора: %s у %d из %d сотрудников, "
                 "прочие базы: %s" % (top, count, total,
                                      ", ".join(sorted(set(homes) - {top}))))


def _dt(s):
    return datetime.fromisoformat(s)


def audit(catalog, payload, rules_source, base=None):
    """Приёмка. На вход — пути либо готовые объекты.

    `base` — база приписки; по умолчанию выводится из набора (см.
    [base_of]), а не берётся литералом.
    """
    cat = (json.loads(Path(catalog).read_text(encoding="utf-8"))
           if isinstance(catalog, (str, Path)) else catalog)
    pay = (json.loads(Path(payload).read_text(encoding="utf-8"))
           if isinstance(payload, (str, Path)) else payload)
    rules = (_cos_rules.load_bytes(Path(rules_source).read_bytes(),
                                   where=Path(rules_source).name)
             if isinstance(rules_source, (str, Path)) else rules_source)
    base, base_source = base_of(pay, base)

    by_leg = {f["flightLegId"]: f for f in pay["flights"]
              if f.get("flightLegId")}
    wire = cat.get("wire_map") or {}
    F = {}
    if wire:
        # Построитель цепочек адресует рейсы wire-именами (`ZZ613@1`) и
        # прикладывает карту. Без её разрешения проверки не находят
        # рейсов и молча дают нули.
        for name, ref in wire.items():
            lid = ref.get("flightLegId") if isinstance(ref, dict) else ref
            if by_leg.get(lid):
                F[name] = by_leg[lid]
        if len(F) != len(wire):
            raise ValueError(
                "не все wire-имена разрешились в данные (%d из %d) — "
                "приёмка была бы недостоверной" % (len(F), len(wire)))
    else:
        # Линия колонок карты не пишет: там рейсы адресованы прямо —
        # полем `flightNumber` канонического набора. Оба ключа берутся
        # сразу, потому что наборы прежних прогонов ссылались на
        # идентификатор экземпляра. Строгость сохраняется: ниже всё
        # равно требуется, чтобы разрешилась КАЖДАЯ ссылка.
        # ТОЛЬКО адрес рейса. Запасной ключ «на всякий случай» здесь
        # уже был и удвоил словарь ссылок, отчего проверка покрытия
        # объявила непокрытыми тысячи рейсов, которых не существует.
        # Несовпадение ссылок должно быть громким отказом, а не молча
        # компенсироваться вторым ключом.
        F = {f["flightNumber"]: f for f in pay["flights"]}
    sel = cat["selected"]
    checks: list[dict] = []

    def add(name, bad, seen, sample=None, level="FAIL"):
        checks.append({"name": name, "violations": bad, "seen": seen,
                       "sample": sample or [], "level": level,
                       "ok": (bad == 0 and seen > 0),
                       "idle": seen == 0})

    all_fns = [fn for u in sel for fn in u["fns"]]
    if sum(1 for fn in all_fns if fn in F) != len(all_fns):
        raise ValueError("часть ссылок на рейсы не разрешилась — нули были "
                         "бы ложными")
    ghost = sorted(set(all_fns) - set(F))
    add("нет ссылок на рейсы вне данных", len(ghost), len(all_fns), ghost[:3])

    work = [fn for u in sel for fn in u["active_fns"]]
    dup = [fn for fn, n in Counter(work).items() if n > 1]
    add("рейс не покрыт рабочим участком дважды", len(dup), len(work),
        dup[:3])
    missing = sorted(set(F) - set(work))
    add("покрытие рейсов расписания", len(missing), len(F), missing[:3],
        level="WARN")

    only_dh = base_start = base_end = disc = tlv = teleports = pairs = 0
    short, type_conn, span_bad, horizon_bad = [], [], [], []
    off = timedelta(minutes=rules.base_tz_offset_min)
    for u in sel:
        act = set(u["active_fns"])
        seq = sorted(((fn, F[fn]) for fn in u["fns"] if fn in F),
                     key=lambda x: x[1]["departureUTCDateTime"])
        legs = [x[1] for x in seq]
        if not legs:
            continue
        if not act:
            only_dh += 1
        if legs[0]["departureAirport"] != base:
            base_start += 1
        if legs[-1]["arrivalAirport"] != base:
            base_end += 1
        for (fa, a), (fb, b) in zip(seq, seq[1:]):
            gap = (_dt(b["departureUTCDateTime"])
                   - _dt(a["arrivalUTCDateTime"])).total_seconds() / 60
            frm, to = a["arrivalAirport"], b["departureAirport"]
            if frm != to:
                need = rules.equivalent_station_gap(frm, to)
                if need is not None and gap >= need:
                    teleports += 1
                else:
                    disc += 1
            if frm == "TLV" and gap >= 600:
                tlv += 1
            if fa not in act or fb not in act:
                continue
            pairs += 1
            if gap < rules.min_sit_min and frm == to:
                short.append([a["flightLegId"], b["flightLegId"], int(gap)])
            if a.get("aircraftType") != b.get("aircraftType") and 0 <= gap < 120:
                type_conn.append([a["flightLegId"], b["flightLegId"],
                                  int(gap)])
        days = rules.pairing_days(_dt(legs[0]["departureUTCDateTime"]),
                                  _dt(legs[-1]["arrivalUTCDateTime"]))
        if days > rules.max_pairing_days:
            span_bad.append([u["sourceUnit"], days])
        m0 = (_dt(legs[0]["departureUTCDateTime"]) + off).strftime("%Y-%m")
        m1 = (_dt(legs[-1]["arrivalUTCDateTime"]) + off).strftime("%Y-%m")
        if m0 != m1[:4] + "-" + m1[5:7] and m1 < m0:
            horizon_bad.append(u["sourceUnit"])

    n = len(sel)
    add("цепочка не состоит только из перевозок", only_dh, n)
    add("цепочка начинается на базе", base_start, n)
    add("цепочка заканчивается на базе", base_end, n)
    add("маршрут непрерывен (с равнозначными станциями)", disc, pairs,
        ["равнозначных переходов: %d" % teleports])
    add("стыковка не меньше %d мин" % rules.min_sit_min, len(short), pairs,
        short[:3])
    add("смена типа ВС — не меньше 120 мин", len(type_conn), pairs,
        type_conn[:3])
    add("нет отдыха в TLV", tlv, pairs)
    add("цепочка не длиннее %d календарных дат" % rules.max_pairing_days,
        len(span_bad), n, span_bad[:3])
    add("цепочки внутри горизонта", len(horizon_bad), n)

    plc = getattr(rules, "dh_limit_counts", "placements") == "placements"
    per_flight = Counter(
        fn for u in sel for fn in u["fns"]
        if fn not in u["active_fns"] and fn in F
        and not (plc and F[fn]["arrivalAirport"] == base))
    by_dest = getattr(rules, "dh_day_scope", "network") == "destination"
    per_day = Counter()
    for fn, k in per_flight.items():
        day = (_dt(F[fn]["departureUTCDateTime"]) + off).date()
        per_day[(day, F[fn]["arrivalAirport"]) if by_dest else day] += k
    lim_f, lim_d = rules.max_dh_per_flight, rules.max_dh_per_day
    if lim_f is not None:
        over = [[fn, k] for fn, k in per_flight.items() if k > lim_f]
        add("не более %d забросок на рейс" % lim_f, len(over),
            max(len(per_flight), 1), sorted(over, key=lambda x: -x[1])[:3])
    if lim_d is not None:
        over = [[str(k), v] for k, v in per_day.items() if v > lim_d]
        add("не более %d забросок в день (%s)"
            % (lim_d, "направление" if by_dest else "сеть"), len(over),
            max(len(per_day), 1), sorted(over, key=lambda x: -x[1])[:3])

    relay = seen613 = 0
    for u in sel:
        act = set(u["active_fns"])
        w = [F[fn] for fn in act if fn in F]
        if not rules.relay_forbidden([x["flightNumber"] for x in w],
                                     [x.get("aircraftType") for x in w]):
            continue
        seen613 += 1
        legs = sorted((F[fn] for fn in u["fns"] if fn in F),
                      key=lambda x: x["departureUTCDateTime"])
        for a, b in zip(legs, legs[1:]):
            g = (_dt(b["departureUTCDateTime"])
                 - _dt(a["arrivalUTCDateTime"])).total_seconds() / 60
            if a["arrivalAirport"] != base and g >= rules.min_off_base_rest():
                relay += 1
                break
    if rules.no_relay_rules:
        add("рейсы перечня без эстафет (отдых от %d мин)"
            % rules.min_off_base_rest(), relay, seen613)

    fails = [c["name"] for c in checks
             if (c["violations"] and c["level"] == "FAIL") or c["idle"]]
    return {
        "checks": checks,
        # База и способ её получения — в отчёте, чтобы проверки о базе
        # нельзя было прочитать, не зная, с чем сравнивали.
        "base": base,
        "base_source": base_source,
        "chains": len(sel),
        "flight_refs": len(all_fns),
        "objects": sum(c["seen"] for c in checks),
        "passed": not fails,
        "failed": fails,
    }


def main(argv=None):
    import sys
    a = argv if argv is not None else sys.argv[1:]
    if len(a) < 3:
        print("использование: pairing_audit.py <каталог> <набор> <правила>")
        return 2
    res = audit(a[0], a[1], a[2])
    for c in res["checks"]:
        mark = "OK  " if c["ok"] else ("WARN" if c["level"] == "WARN"
                                       and not c["idle"] else "FAIL")
        print("  [%s] %-50s нарушений: %-5d рассмотрено: %-6d %s"
              % (mark, c["name"], c["violations"], c["seen"],
                 c["sample"][:2] if c["sample"] else ""))
    print()
    print("  цепочек: %d | проверок: %d | объектов: %d"
          % (res["chains"], len(res["checks"]), res["objects"]))
    print("  база приписки: %s (%s)" % (res["base"], res["base_source"]))
    print("  ПРИЁМКА " + ("ПРОЙДЕНА" if res["passed"]
                          else "НЕ ПРОЙДЕНА: " + ", ".join(res["failed"])))
    return 0 if res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
