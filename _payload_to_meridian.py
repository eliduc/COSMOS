"""Выгрузка решения COSMOS в формат системы-источника («Меридиан»,
format:1) — обратная сторона адаптера импорта `_meridian_to_payload.py`.

Работает на КАНОНИЧЕСКОМ наборе и каталоге цепочек, а не на формате
источника: любой набор, доведённый до канона, выгружается одинаково.
Файл источника принимается необязательным справочником — из него берутся
заголовочные поля и полные записи участков, которых в каноне нет
(перевозчик, свойства рейса, участки вне окна импорта). Без него участок
собирается из канона, и это честно считается в отчёте.

Наши решения — уровня ПОСТРОЕНИЯ ЦЕПОЧЕК, поэтому:
  * duties/pairings заполняются нашими цепочками;
  * места экипажа (complements) перечислены по спросу участков, но БЕЗ
    табельных номеров — назначение людей это отдельный этап;
  * справочник сотрудников и наземные мероприятия НЕ включаются: в них
    персональные данные, а для структуры цепочек они не нужны.

Роль участка в наряде: «Р» — рабочий, «D/H» — перелёт пассажиром.
Перелёты пассажиром собираются в отдельный наряд цепочки: рабочей смены
они не образуют, а из командировки их выбрасывать нельзя — именно они
привязывают цепочку к базе.

Громкие отказы (дом. правило): участок цепочки, которого нет в наборе;
наряд, ссылающийся на участки вне командировки; потерянные операционные
участки; цепочка без единого участка — всё это собирается и валит прогон,
отчёт проверки пишется в любом случае.

Участок, отработанный дважды, отказом НЕ является — намеренно: в исходных
данных заказчика двойное покрытие встречается штатно, и фатальный отказ
заблокировал бы законную выгрузку. Оно считается и публикуется числом
(`legs_operated_more_than_once`) с предупреждением. Оговорка третьего
аудита (§P2-01) остаётся в силе: модель пока не различает вхождения и
роли и не доказывает непересекающийся спрос — это открытый остаток, а не
свойство корректности.

Детерминированность: ни одного обращения к языковым моделям и к сети.

Использование:
    python _payload_to_meridian.py --payload payload.json \
        --catalog catalog.json --level 3 --out solution.json \
        [--source meridian.json] [--check check.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _meridian_to_payload import (        # noqa: E402  общий словарь мест
    DEADHEAD_DUTY_ROLE, _expand_signature)

from _cli_console import force_utf8_console  # noqa: E402

force_utf8_console()

OPERATING_DUTY_ROLE = "Р"                 # рабочий участок наряда

LEVEL_NOTE = {
    1: "цепочки заказчика сохранены, достроено недостающее",
    2: "смены заказчика сохранены, цепочки перестроены",
    3: "свободное построение смен и цепочек",
}


def _dt(value: str) -> datetime:
    from _canonical_time import parse_utc
    return parse_utc(value)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S")


def _buffer_tables(flights: Dict[str, dict]) -> Tuple[dict, dict, int, int]:
    """Таблицы брифинга и дебрифинга участка с запасными значениями.

    У системы-источника длительность подготовки и разбора — ДАННЫЕ
    участка, а не выводимое правило (проверено: 2084 участка в роли
    первого, ноль расхождений). Здесь они собираются в том же порядке
    предпочтения, что и в построителе: значение участка, затем наиболее
    частое по станции, затем наиболее частое по всему набору.
    """
    brief: Dict[str, int] = {}
    debrief: Dict[str, int] = {}
    by_dep: Dict[str, Counter] = defaultdict(Counter)
    by_arr: Dict[str, Counter] = defaultdict(Counter)
    all_b: Counter = Counter()
    all_d: Counter = Counter()
    for fn, f in flights.items():
        b, d = f.get("briefingMin"), f.get("debriefingMin")
        if b is not None:
            brief[fn] = int(b)
            by_dep[f["departureAirport"]][int(b)] += 1
            all_b[int(b)] += 1
        if d is not None:
            debrief[fn] = int(d)
            by_arr[f["arrivalAirport"]][int(d)] += 1
            all_d[int(d)] += 1
    b_fb = all_b.most_common(1)[0][0] if all_b else 0
    d_fb = all_d.most_common(1)[0][0] if all_d else 0
    brief["__by_station__"] = {s: c.most_common(1)[0][0]
                               for s, c in by_dep.items()}   # type: ignore
    debrief["__by_station__"] = {s: c.most_common(1)[0][0]
                                 for s, c in by_arr.items()}  # type: ignore
    return brief, debrief, b_fb, d_fb


def _work_period(group: List[str], flights: Dict[str, dict],
                 brief: dict, debrief: dict,
                 b_fb: int, d_fb: int,
                 conditions=None) -> Tuple[datetime, datetime]:
    """Рабочее время смены: от брифинга перед первым рабочим участком до
    конца разбора после последнего.

    Прежде выгрузка писала границами смены вылет и прилёт, из-за чего
    поле `workTimeCredit` содержало размах полётов, а не рабочее время
    (замечание авиакомпании от 31.07.2026, П.2.4/2.5/2.8/2.9). Расчёт эти
    буферы учитывал всегда — не отражала их именно выгрузка.

    При переданных условиях расчёта буферы поднимаются до пола документа
    (`Conditions.briefing_floor`) — ТЕМ ЖЕ методом, что и в построителе.
    Без этого выгрузка занижала границы нарядов относительно расчёта:
    станционное значение 45 минут против пола 60 — и проверка решения
    находила нарушения, которых в расчёте не было.
    """
    first, last = flights[group[0]], flights[group[-1]]
    b = brief.get(group[0])
    if b is None:
        b = brief["__by_station__"].get(first["departureAirport"], b_fb)
    d = debrief.get(group[-1])
    if d is None:
        d = debrief["__by_station__"].get(last["arrivalAirport"], d_fb)
    if conditions is not None:
        b = conditions.briefing_floor(
            b, first["departureAirport"] == conditions.base)
        d = conditions.debriefing_floor(
            d, last["arrivalAirport"] == conditions.base)
    from datetime import timedelta
    return (_dt(first["departureUTCDateTime"]) - timedelta(minutes=int(b)),
            _dt(last["arrivalUTCDateTime"]) + timedelta(minutes=int(d)))


def _minutes(t0: datetime, t1: datetime) -> int:
    return int((t1 - t0).total_seconds() // 60)


def export(payload: dict, catalog: dict, level: int,
           source: Optional[dict] = None,
           id_prefix: Optional[str] = None,
           note: Optional[str] = None,
           conditions=None) -> Tuple[dict, dict, List[str]]:
    """Собрать документ формата источника. Возвращает (документ, отчёт
    проверки, список ошибок). Ошибки не бросаются: отчёт нужен и при
    отказе."""
    errors: List[str] = []
    check: dict = {"level": level, "source_used": source is not None}

    flights = {f["flightNumber"]: f for f in payload.get("flights") or []}
    # Спрос мест участка — из канонических мест экипажа: сколько мест
    # какой роли требует рейс.
    demand: Dict[str, Counter] = defaultdict(Counter)
    for a in payload.get("flightAssignments") or []:
        if a.get("flight") and a.get("requiredSkill"):
            demand[a["flight"]][a["requiredSkill"]] += 1
    # Состав экипажа участка ПО ИСХОДНОМУ ФАЙЛУ — запасной источник, когда
    # канонический набор спроса на участок не несёт (разбор 2026-07-27).
    #
    # Так бывает у граничных участков: командировка задевает сутки за
    # краем окна конверсии, спроса на них в каноне нет, и наряд выпускался
    # с ПУСТЫМ составом. Пустой состав у рабочего наряда — утверждение
    # «рейс выполняет никто»; обратный адаптер честно отказывался такой
    # файл читать, и решения трёх уровней свободы нельзя было вернуть в
    # конвейер для расстановки экипажа.
    #
    # Берём состав заказчика для этого же участка: не выдумываем, а
    # возвращаем то, что он сам про этот рейс написал.
    src_comp_of_leg: Dict[str, Counter] = defaultdict(Counter)
    if source is not None:
        _duty_by_id = {str(d.get("dutyId")): d
                       for d in (source.get("duties") or [])
                       if isinstance(d, dict)}
        for leg in source.get("legs") or []:
            roles = leg.get("dutyCrewTypes")
            if not isinstance(roles, dict):
                continue
            acc: Counter = Counter()
            for did, role in roles.items():
                if str(role) != OPERATING_DUTY_ROLE:
                    continue
                du = _duty_by_id.get(str(did))
                for c in (du or {}).get("complements") or []:
                    rank = c.get("complementRank")
                    if rank:
                        acc[str(rank)] += 1
            if acc:
                src_comp_of_leg[str(leg.get("legId"))] = acc

    # Мост назад к идентификаторам источника; без него участок теряет
    # связь с исходным файлом, и это надо назвать вслух.
    src_of_fn: Dict[str, str] = {}
    no_source_id = 0
    for fn, fl in flights.items():
        sid = fl.get("sourceLegId")
        if isinstance(sid, str) and sid:
            src_of_fn[fn] = sid
        else:
            src_of_fn[fn] = fn
            no_source_id += 1
    check["legs_without_source_id"] = no_source_id

    prefix = id_prefix or f"COSMOS-L{level}"
    leg_roles: Dict[str, Dict[str, str]] = defaultdict(dict)
    operated: Counter = Counter()
    duties: List[dict] = []
    pairings: List[dict] = []
    rider_legs = 0
    complements_total = 0
    comps_from_source = 0
    comps_provenance: Counter = Counter()
    deadhead_legs: set = set()
    brief_tbl, debrief_tbl, brief_fb, debrief_fb = _buffer_tables(flights)
    # Тип экипажа выгрузки: берётся из справочника источника, иначе не
    # выдумывается (формат допускает null).
    crew_type = (source or {}).get("division")

    units = catalog.get("selected") or []
    for i, unit in enumerate(units):
        fns = [fn for fn in (unit.get("fns") or [])]
        unknown = [fn for fn in fns if fn not in flights]
        if unknown:
            errors.append(f"цепочка {i}: участков нет в наборе: "
                          f"{', '.join(sorted(unknown)[:5])}")
            continue
        active = list(unit.get("active_fns") or fns)
        # Разбивка по сменам обязана лежать ВНУТРИ командировки. Ссылка на
        # чужой участок — это рассогласованный каталог: раньше она давала
        # KeyError уже в середине сборки, то есть падение вместо вердикта
        # и без отчёта проверки (аудит 2026-07-26, P2-01).
        alien = sorted({fn for g in (unit.get("duty_fns") or [])
                        for fn in g if fn not in set(fns)})
        if alien:
            errors.append(f"цепочка {i}: границы смен ссылаются на участки "
                          f"вне командировки: {', '.join(alien[:5])}")
            continue
        groups = [list(g) for g in (unit.get("duty_fns") or [active]) if g]
        if not groups:
            errors.append(f"цепочка {i}: ни одного рабочего участка")
            continue
        # Рабочие участки, потерянные разбивкой, — тихая потеря работы:
        # юнит выгрузится с меньшим спросом, чем несёт каталог.
        lost = sorted(set(active) - {fn for g in groups for fn in g})
        if unit.get("duty_fns") and lost:
            errors.append(f"цепочка {i}: рабочие участки вне границ смен: "
                          f"{', '.join(lost[:5])}")
            continue
        pid = f"{prefix}-{i:05d}"

        def _key(fn: str) -> datetime:
            return _dt(flights[fn]["departureUTCDateTime"])

        groups = [sorted(g, key=_key) for g in groups]
        groups.sort(key=lambda g: _key(g[0]))
        # Состав экипажа — свойство ВСЕЙ командировки, а не отдельной
        # смены: один экипаж везёт её целиком. Построитель так и считает
        # (сумма поролевых максимумов по всем участкам плюс надбавка за
        # ярус) и эту численность оплачивает в цели, а выгрузка прежде
        # пересобирала места ЗАНОВО по каждой смене из поучасткового
        # спроса — без агрегирования по цепочке и без надбавки. Из-за
        # этого выгрузка систематически занижала состав и давала разное
        # число мест у смен одной командировки (замечание авиакомпании от
        # 31.07.2026, П.1.6 и пункт 5 раздела о формате).
        unit_seats = [str(r) for r in (unit.get("seats") or [])]
        if unit_seats:
            comps_src = "каталог"
        else:
            # Каталог без списка мест — собираем из спроса по ВСЕЙ
            # командировке (поролевые максимумы), а не по смене.
            need_all: Counter = Counter()
            for fn in active:
                got = demand.get(fn) or {}
                if not got:
                    got = src_comp_of_leg.get(src_of_fn.get(fn, fn)) or {}
                    if got:
                        comps_from_source += 1
                for role, n in got.items():
                    need_all[role] = max(need_all[role], n)
            unit_seats = _expand_signature(tuple(sorted(need_all.items())))
            comps_src = "спрос цепочки"
        comps_provenance[comps_src] += 1
        chain_comps = [{"complementRank": rank, "employeeId": None,
                        "locked": False, "requirements": []}
                       for rank in unit_seats]

        recs: List[dict] = []
        for j, group in enumerate(groups):
            did = f"{pid}-D{j:02d}"
            block = 0
            for fn in group:
                fl = flights[fn]
                block += _minutes(_dt(fl["departureUTCDateTime"]),
                                  _dt(fl["arrivalUTCDateTime"]))
                leg_roles[src_of_fn[fn]][did] = OPERATING_DUTY_ROLE
                operated[fn] += 1
            # один и тот же состав у всех смен командировки
            comps = [dict(c) for c in chain_comps]
            if not comps:
                # Рабочий наряд без единого места экипажа — утверждение
                # «рейс выполняет никто». Молчать нельзя: именно такой
                # наряд делал выгрузку нечитаемой обратным адаптером.
                errors.append(
                    f"наряд {did}: рабочая смена без единого места экипажа "
                    f"({len(group)} участок(ов)) — ни канон, ни исходный "
                    f"файл состава не дают")
            complements_total += len(comps)
            # Границы смены — по РАБОЧЕМУ времени: подготовка перед первым
            # рабочим участком и разбор после последнего. Прежде здесь
            # стояли вылет и прилёт, из-за чего `workTimeCredit` содержал
            # размах полётов.
            w0, w1 = _work_period(group, flights, brief_tbl, debrief_tbl,
                                  brief_fb, debrief_fb, conditions)
            recs.append({
                "dutyId": did, "pairingId": pid, "numberOfDuty": j + 1,
                "start": _iso(w0), "end": _iso(w1),
                "assignmentType": "FLIGHT",
                "crewType": crew_type,
                "firstDepartureStation":
                    flights[group[0]]["departureAirport"],
                "lastArrivalStation": flights[group[-1]]["arrivalAirport"],
                "stationsVisited": sorted(
                    {flights[fn]["departureAirport"] for fn in group}
                    | {flights[fn]["arrivalAirport"] for fn in group}),
                "flightTimeCredit": str(block),
                "workTimeCredit": str(_minutes(w0, w1)),
                "complements": comps, "restOverride": 0, "split": False})
        # Перелёты пассажиром принадлежат СМЕНЕ, а не отдельному наряду:
        # в формате источника перегон обозначается флагом `deadhead` на
        # участке, а тип наряда остаётся «FLIGHT» (замечание авиакомпании
        # от 31.07.2026, пункт 4 раздела о формате). Отдельный наряд был
        # нашей конструкцией и порождал смены с пустым списком мест.
        active_set = set(active)
        ride = sorted((fn for fn in fns if fn not in active_set), key=_key)
        for fn in ride:
            rider_legs += 1
            rl = _dt(flights[fn]["departureUTCDateTime"])
            # ближайшая по времени смена командировки
            best = min(range(len(recs)),
                       key=lambda k: abs((rl - _dt(
                           flights[groups[k][0]]["departureUTCDateTime"])
                       ).total_seconds()))
            leg_roles[src_of_fn[fn]][recs[best]["dutyId"]] = \
                DEADHEAD_DUTY_ROLE
            deadhead_legs.add(src_of_fn[fn])
            # наряд расширяется перегоном по краям
            rec = recs[best]
            r0, r1 = rl, _dt(flights[fn]["arrivalUTCDateTime"])
            if _iso(r0) < rec["start"]:
                rec["start"] = _iso(r0)
            if _iso(r1) > rec["end"]:
                rec["end"] = _iso(r1)
        recs.sort(key=lambda d: d["start"])
        duties.extend(recs)
        pairings.append({
            "pairingId": pid, "start": recs[0]["start"],
            "end": recs[-1]["end"], "primaryPairing": True, "locked": False,
            "flightMinutesCredit": str(sum(int(d["flightTimeCredit"])
                                           for d in recs)),
            "workMinutesCredit": str(sum(int(d["workTimeCredit"])
                                         for d in recs)),
            # пустой ОБЪЕКТ, а не пустой массив — требование формата
            "requirements": {}, "restOverride": 0})

    # Участок, отработанный двумя цепочками, — не дефект выгрузки:
    # формат источника допускает несколько нарядов на участке, и в самих
    # данных заказчика такие участки есть. Для нашего решения это лишние
    # затраты, а не нарушение, поэтому считаем и называем, но не валим.
    twice = sorted(fn for fn, n in operated.items() if n > 1)
    check["legs_operated_more_than_once"] = len(twice)

    # ---------------- участки ------------------------------------------
    # Со справочником источника выгружаются ВСЕ его участки (в том числе
    # не попавшие в наше решение — с пустой ролью), без него — участки
    # набора: иначе выгрузка молча потеряла бы часть расписания.
    out_legs: List[dict] = []
    if source is not None:
        for leg in source.get("legs") or []:
            if not isinstance(leg, dict) or not isinstance(leg.get("legId"),
                                                           str):
                continue
            rec = dict(leg)
            rec["dutyCrewTypes"] = leg_roles.get(leg["legId"], {})
            out_legs.append(rec)
    else:
        for fn, fl in sorted(flights.items(),
                             key=lambda kv: (kv[1]["departureUTCDateTime"],
                                             kv[0])):
            sid = src_of_fn[fn]
            out_legs.append({
                "legId": sid,
                "flightNumber": fl.get("flightNumber"),
                "departureStation": fl["departureAirport"],
                "arrivalStation": fl["arrivalAirport"],
                "start": fl["departureUTCDateTime"],
                "end": fl["arrivalUTCDateTime"],
                # Флаг `deadhead` УЧАСТКА означает «участок не выполняется
                # вовсе» (обратный адаптер такие участки исключает из
                # расписания), а НЕ «этим участком кто-то едет
                # пассажиром». Роль пассажира выражается ролью «D/H» в
                # `dutyCrewTypes` соответствующей смены — так же, как в
                # решении источника.
                "deadhead": False,
                "fleet": fl.get("fleet"),
                "dutyCrewTypes": leg_roles.get(sid, {})})

    placed = {sid for sid, roles in leg_roles.items() if roles}
    missing_in_out = placed - {l["legId"] for l in out_legs}
    if missing_in_out:
        errors.append(f"{len(missing_in_out)} участок(ов) цепочек нет в "
                      f"справочнике источника — выгрузка потеряла бы роли")

    # Справочник станций выгружается всегда: без него документ нельзя
    # прочитать обратно штатным импортом, то есть заказчик получил бы
    # файл, который наша же система не принимает.
    src = source or {}
    if src.get("airports"):
        out_airports = list(src["airports"])
    else:
        out_airports = [{"IATA": a["code"],
                         "name": a.get("name") or a["code"],
                         "city": a.get("name") or a["code"]}
                        for a in sorted(payload.get("airports") or [],
                                        key=lambda x: x["code"])
                        if a.get("code")]
    check["airports"] = len(out_airports)

    plan_since = src.get("planSince")
    plan_until = src.get("planUntil")
    if not plan_since and flights:
        plan_since = min(f["departureUTCDateTime"] for f in flights.values())
    if not plan_until and flights:
        plan_until = max(f["arrivalUTCDateTime"] for f in flights.values())
    text = note or LEVEL_NOTE.get(level, "")
    doc = {
        "format": src.get("format", 1),
        # Идентификатор набора обязан совпадать с входным датасетом:
        # верификатор заказчика связывает выгрузку с постановкой именно по
        # нему. Своя строка ставится только когда справочника нет вовсе.
        "datasetId": src.get("datasetId") or f"cosmos-level{level}",
        "description": (f"COSMOS, уровень свободы {level}: {text}. "
                        f"Решение уровня построения цепочек: места экипажа "
                        f"перечислены по спросу, табельные номера не "
                        f"назначены (отдельный этап)."),
        "division": src.get("division"),
        "type": src.get("type", "pairing"),
        "created": src.get("created"),
        "planSince": plan_since, "planUntil": plan_until,
        "presetId": src.get("presetId"),
        "airports": out_airports,
        "legs": out_legs, "duties": duties, "pairings": pairings,
        "groundActivities": [], "employees": []}

    check["pairings"] = len(pairings)
    check["duties"] = len(duties)
    check["legs_total"] = len(out_legs)
    check["legs_with_role"] = sum(1 for l in out_legs if l["dutyCrewTypes"])
    check["legs_operated"] = len(operated)
    check["rider_legs"] = rider_legs
    check["complements_total"] = complements_total
    check["complements_taken_from_source"] = comps_from_source
    check["complements_provenance"] = dict(comps_provenance)
    check["deadhead_legs_flagged"] = len(deadhead_legs)
    check["units_in_catalog"] = len(units)
    # Без справочника источника выгрузка ЗАВЕДОМО неполна по формату:
    # у участков нет типа судна, перевозчика и свойств рейса, у документа
    # нет идентификаторов набора и пресета, у станций — расширенных полей.
    # Именно так была собрана поставка, вызвавшая замечания заказчика от
    # 31.07.2026, поэтому пропуск справочника объявляется громко, а не
    # остаётся незаметным ключом командной строки.
    check["source_reference_used"] = source is not None
    if source is None:
        check["format_incomplete_without_source"] = True
        print("ВНИМАНИЕ: выгрузка собрана БЕЗ справочника источника "
              "(--source). Формат будет неполным: у участков нет типа "
              "судна и перевозчика, у документа нет идентификаторов "
              "набора и пресета, у станций — расширенных полей.",
              flush=True)
    return doc, check, errors


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--payload", type=Path, required=True)
    ap.add_argument("--catalog", type=Path, required=True,
                    help="каталог цепочек COSMOS (этап 1 или импорт)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--level", type=int, default=3, choices=(1, 2, 3),
                    help="уровень свободы, которым построены цепочки")
    ap.add_argument("--source", type=Path, default=None,
                    help="выгрузка источника — необязательный справочник "
                         "заголовка и полных записей участков")
    ap.add_argument("--check", type=Path, default=None)
    ap.add_argument("--id-prefix", default=None,
                    help="приставка идентификаторов цепочек "
                         "(по умолчанию COSMOS-L<уровень>)")
    ap.add_argument("--note", default=None,
                    help="пояснение уровня в описании документа")
    ap.add_argument("--conditions", type=Path, default=None,
                    help="условия расчёта: буферы нарядов поднимаются до "
                         "пола документа тем же методом, что в построителе")
    args = ap.parse_args(argv)

    def _write_check(check: dict, errors: List[str]) -> None:
        """Отчёт проверки пишется ВСЕГДА, в том числе при внутреннем сбое:
        иначе вызывающий видит ненулевой код и не видит причины."""
        check["errors"] = errors[:50]
        check["errors_total"] = len(errors)
        check["ok"] = not errors
        if args.check:
            args.check.parent.mkdir(parents=True, exist_ok=True)
            args.check.write_text(
                json.dumps(check, ensure_ascii=False, indent=1),
                encoding="utf-8")
            print(f"  отчёт:  {args.check}", flush=True)

    payload = json.loads(args.payload.read_text(encoding="utf-8"))
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    source = (json.loads(args.source.read_text(encoding="utf-8"))
              if args.source else None)
    conditions = None
    if args.conditions:
        import _conditions
        conditions = _conditions.load(args.conditions)
        print("этап: буферы нарядов — пол документа из "
              f"{args.conditions.name}", flush=True)
    try:
        doc, check, errors = export(payload, catalog, args.level, source,
                                    args.id_prefix, args.note, conditions)
    except Exception as exc:                       # noqa: BLE001 — страховка
        import traceback
        traceback.print_exc()
        _write_check({}, [f"внутренняя ошибка: "
                          f"{exc.__class__.__name__}: {exc}"])
        print(f"  ОШИБКА: внутренняя ошибка: {exc}", flush=True)
        return 1
    _write_check(check, errors)
    if errors:
        print(f"  ОШИБКА: {len(errors)} — выгрузка не записана:", flush=True)
        for e in errors[:20]:
            print(f"    {e}", flush=True)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, ensure_ascii=False),
                        encoding="utf-8")
    print(f"уровень {args.level}: цепочек {check['pairings']}, нарядов "
          f"{check['duties']}, участков с ролью {check['legs_with_role']} "
          f"из {check['legs_total']}; -> {args.out.name} "
          f"({args.out.stat().st_size // 1024} КБ)", flush=True)
    if check["legs_operated_more_than_once"]:
        print(f"  участков, отработанных более одной цепочкой: "
              f"{check['legs_operated_more_than_once']} — это лишние "
              f"затраты решения, не дефект выгрузки", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
