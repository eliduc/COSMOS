"""Convert the vendor SOLVER export (format:5) into the canonical payload
using the solver file as the ONLY source («отдельное решение», 2026-07-24:
данные берутся исключительно из солвер-файла — Meridian-выгрузка не
участвует вовсе).

Fully deterministic application code: no model calls, no network —
customer data never leaves the machine. Reuses the proven helpers of the
Meridian adapter (_meridian_to_payload) — both are covered by the
root-adapter «zero model/network egress» guard pattern.

Source-format facts (format:5):
  * legs[]: id '<fn>$dd.mm.yyyy$<n>' (materialized deadheads carry the
    'dh%' prefix and are NOT operating demand), zoned datetimes
    '...+HH:MM[Zone]', acType, stations by iata;
  * employees[]: ranks {fleet -> rank} = qualification map; features[]
    dated clearances; birthday is PII and is dropped;
  * employeeAssignments[]: duty + sequence references — the vendor's own
    pairing structure (sequenceId groups duties into rotations);
  * ground duties (duty.ground == true) + their locked assignments give
    per-employee unavailability;
  * leg2Duties[]: leg -> duty with crewRole (STD/ENF/DBL operating;
    ONLY/FIRST/LAST_DH — crew rides as passengers).

No window clipping: per the standing instruction («ничего обрезать
нельзя») the FULL data span of the file is converted.

Usage:
    python _solver_to_payload.py <solver.json>
        [--out payload.json] [--check check.json]
        [--emit-catalog catalog.json] [--catalog-label LABEL]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _meridian_to_payload import (  # noqa: E402 — проверенные помощники
    QUAL_CODE_MAX, SCHEMA_PATH, SourceError, _code, _expand_signature,
    _load_json, _pick_signature, _signature, _text, classify_qualification,
    parse_leg_id, parse_zoned_to_utc, run_sanity_checks)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEADHEAD_PREFIX = "dh%"
OP_ROLES = ("STD", "ENF", "DBL")
DH_ROLES = ("ONLY_DH", "FIRST_DH", "LAST_DH")

# Страна юрисдикции Пакета правил: формат значения повторяет
# webapp/registry.py (COUNTRY_RE) — модуль остаётся автономным CLI без
# импорта webapp; равенство паттернов держит тест-страж в
# tests/test_rules_import.py. Значение подаёт оператор: солвер-файл
# юрисдикцию не несёт, а пакет с пустой страной действовал бы на ВСЕ
# наборы (webapp/rule_packs.pack_applies_to).
_COUNTRY_RE = re.compile(r"^(?:INTL|[A-Z]{2})$")


def _sig_max(sigs: List[Tuple]) -> Tuple:
    acc: Dict[str, int] = {}
    for sig in sigs:
        for role, n in sig:
            acc[role] = max(acc.get(role, 0), n)
    return tuple(sorted(acc.items()))


def _zoned(raw: object, what: str) -> datetime:
    dt = parse_zoned_to_utc(raw)
    if dt is None:
        raise SourceError(f"{what}: unparseable zoned datetime {raw!r}")
    if dt.second != 0:
        raise SourceError(f"{what}: nonzero seconds in {raw!r}")
    return dt


def convert(solver: dict, source_name: str, check_rel: str,
            ) -> Tuple[dict, dict, list, list]:
    """Solver export -> (payload, check, errors, catalog_units)."""
    errors: List[str] = []
    check: dict = {"source_mode": "solver-only"}

    legs_raw = solver.get("legs")
    emps_raw = solver.get("employees")
    if not isinstance(legs_raw, list) or not isinstance(emps_raw, list):
        errors.append("solver: 'legs'/'employees' must be lists")
        return {}, check, errors, []

    def _plan_date(key: str) -> Optional[date]:
        raw = str((solver.get("parameters") or {}).get(key) or "")[:10]
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None

    # ---------------- structure maps -------------------------------------
    duties_by_id: Dict[str, dict] = {}
    for d in solver.get("duties") or []:
        if isinstance(d, dict) and isinstance(d.get("id"), str):
            duties_by_id[d["id"]] = d
    seats_by_duty: Dict[str, List[str]] = defaultdict(list)
    ground_by_emp: Dict[str, List[dict]] = defaultdict(list)
    seq_id_of_full: Dict[str, str] = {}
    for s in solver.get("sequences") or []:
        if (isinstance(s, dict) and isinstance(s.get("id"), str)
                and isinstance(s.get("sequenceId"), str)):
            seq_id_of_full[s["id"]] = s["sequenceId"]
    seq_votes: Dict[str, Counter] = defaultdict(Counter)
    for ea in solver.get("employeeAssignments") or []:
        if not isinstance(ea, dict):
            continue
        did = ea.get("duty")
        if not isinstance(did, str):
            continue
        rank = ea.get("preferredRank")
        if isinstance(rank, str) and rank:
            seats_by_duty[did].append(rank)
        full = ea.get("sequence")
        if isinstance(full, str) and full in seq_id_of_full:
            seq_votes[did][seq_id_of_full[full]] += 1
        emp = ea.get("employee")
        duty = duties_by_id.get(did)
        if (isinstance(emp, str) and emp and isinstance(duty, dict)
                and duty.get("ground")):
            ground_by_emp[emp].append(duty)
    seq_of_duty: Dict[str, str] = {}
    for did, cnt in seq_votes.items():
        top = max(cnt.values())
        seq_of_duty[did] = min(s for s, n in cnt.items() if n == top)

    duty_of_leg: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for row in solver.get("leg2Duties") or []:
        if (isinstance(row, dict) and isinstance(row.get("leg"), str)
                and isinstance(row.get("duty"), str)):
            duty_of_leg[row["leg"]].append(
                (row["duty"], str(row.get("crewRole") or "")))

    # ---------------- брифинг и дебрифинг участка --------------------------
    # У системы-источника это ДАННЫЕ, а не выводимое правило: пять
    # кандидатов на ключ (станция вылета, тип судна, место смены в цепочке
    # и их сочетания) оставляют неоднозначность, а по участку её нет —
    # 2084 участка в роли первого, 0 расхождений; столько же в роли
    # последнего, тоже 0. Без них рабочее время смены посчитать нельзя, а
    # по нему определяются и предел смены, и признак ночной смены.
    def _brief_min(v) -> int | None:
        if not isinstance(v, dict):
            return None
        m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?",
                         str(v.get("duration") or "").strip())
        if not m:
            return None
        h, mi = (int(x) if x else 0 for x in m.groups())
        return h * 60 + mi

    brief_of_leg: Dict[str, int] = {}
    debrief_of_leg: Dict[str, int] = {}
    _by_duty: Dict[str, List[Tuple[str, str]]] = defaultdict(list)
    for _lid, _rows in duty_of_leg.items():
        for _did, _role in _rows:
            _by_duty[_did].append((_lid, _role))
    # Порядок — по МОМЕНТУ вылета, а не по строке. Строка вида
    # '...+HH:MM[Zone]' сортируется лексикографически, и участок,
    # которого нет в списке рейсов, получал пустой ключ, становился первым
    # и забирал себе брифинг смены; настоящий первый участок оставался без
    # него. Участки без разбираемого времени в выбор не входят вовсе.
    _leg_start: Dict[str, datetime] = {}
    for l in legs_raw:
        if not isinstance(l, dict):
            continue
        t = parse_zoned_to_utc(l.get("start"))
        if t is not None:
            _leg_start[str(l.get("id"))] = t
    for _did, _items in _by_duty.items():
        _du = duties_by_id.get(_did)
        if not isinstance(_du, dict) or _du.get("ground"):
            continue
        _op = [(l, r) for l, r in _items
               if r not in DH_ROLES and l in _leg_start]
        if not _op:
            continue
        _op.sort(key=lambda x: _leg_start[x[0]])
        _b, _db = _brief_min(_du.get("briefing")), _brief_min(_du.get("debriefing"))
        if _b is not None:
            brief_of_leg.setdefault(_op[0][0], _b)
        if _db is not None:
            debrief_of_leg.setdefault(_op[-1][0], _db)

    # Строки участков по идентификатору: у материализованного перегона
    # (приставка `dh%`) своя цена, и именно она у источника означает
    # стоимость перелёта экипажа пассажиром.
    raw_by_id: Dict[str, dict] = {str(l.get("id")): l for l in legs_raw
                                  if isinstance(l, dict)}

    # ---------------- legs -> flights ------------------------------------
    flights: List[dict] = []
    leg_meta: List[dict] = []
    excluded = Counter()
    seen = set()
    dh_cost_known = 0
    for i, leg in enumerate(legs_raw):
        if not isinstance(leg, dict) or not isinstance(leg.get("id"), str):
            errors.append(f"legs[{i}]: not an object with string id")
            continue
        leg_id = leg["id"]
        if leg_id in seen:
            errors.append(f"legs[{i}]: duplicate id {leg_id!r}")
            continue
        seen.add(leg_id)
        if leg_id.startswith(DEADHEAD_PREFIX):
            excluded["deadhead"] += 1
            continue
        try:
            dep = _zoned(leg.get("start"), f"leg {leg_id} start")
            arr = _zoned(leg.get("end"), f"leg {leg_id} end")
            fn_raw, leg_date, leg_no = parse_leg_id(leg_id)
        except SourceError as exc:
            errors.append(str(exc))
            continue
        if not (timedelta(0) < arr - dep <= timedelta(hours=20)):
            errors.append(f"leg {leg_id}: duration {arr - dep} out of "
                          f"(0, 20h]")
            continue
        dep_st = leg.get("departureStation")
        arr_st = leg.get("arrivalStation")
        if not (isinstance(dep_st, str) and dep_st.strip()
                and isinstance(arr_st, str) and arr_st.strip()):
            errors.append(f"leg {leg_id}: missing stations")
            continue
        fn_ascii = _code(str(leg.get("flightCode") or fn_raw))
        flight_number = f"{fn_ascii}-{leg_date.strftime('%Y%m%d')}-{leg_no}"
        dep_s = dep.strftime("%Y-%m-%dT%H:%M:%S")
        flight = {
            "flightNumber": flight_number,
            "flightLegId": f"{flight_number}|{dep_s}",
            "sourceLegId": leg_id,
            "departureAirport": _code(dep_st),
            "departureUTCDateTime": dep_s,
            "arrivalAirport": _code(arr_st),
            "arrivalUTCDateTime": arr.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        ac = leg.get("acType")
        if isinstance(ac, str) and ac.strip() and ac != "UNKNOWN":
            flight["aircraftType"] = _code(ac)
        # Требования участка (2026-07-25, доимпорт по программе полного
        # паритета): форма [["код"]], семантика «каждому члену экипажа» —
        # проверена на закреплениях планировщиков (98.9% соответствия).
        req_codes = []
        for inner in (leg.get("requirements") or []):
            if isinstance(inner, list):
                req_codes += [str(c) for c in inner if c]
            elif inner:
                req_codes.append(str(inner))
        if req_codes:
            flight["requiredQualifications"] = sorted(set(req_codes))
        # Стоимость перелёта экипажа пассажиром на этом участке — ДАННЫЕ
        # источника. Проверено: сумма по его решению воспроизводит его
        # метрику «стоимость перегонов» ТОЧНО, 6 875 000 из 6 875 000.
        #
        # Цену несёт строка материализованного перегона (приставка `dh%`).
        # Цена самого участка ценой перегона НЕ является: она одинакова у
        # всех 4401 операционных участков (5000), то есть означает что-то
        # другое. Подставлять её участку без строки перегона нельзя —
        # расчёт избегал бы перегонов в 2,5 раза сильнее источника.
        dh_leg = raw_by_id.get(DEADHEAD_PREFIX + leg_id)
        cost_src = (dh_leg or {}).get("cost")
        if isinstance(cost_src, (int, float)) and not isinstance(cost_src, bool):
            flight["deadheadCost"] = int(cost_src)
            flight["deadheadCostKnown"] = True
            dh_cost_known += 1
        b = brief_of_leg.get(leg_id)
        if b is not None:
            flight["briefingMin"] = b
        db = debrief_of_leg.get(leg_id)
        if db is not None:
            flight["debriefingMin"] = db
        flights.append(flight)
        leg_meta.append({"legId": leg_id, "flight": flight})
    check["legs_total"] = len(legs_raw)
    check["legs_converted"] = len(flights)
    check["legs_excluded"] = dict(excluded)
    # Участку, который источник ни разу не использовал как перегон, цены
    # перегона в данных нет. Подставляется преобладающая по данным — и
    # число таких участков считается, чтобы вывод нельзя было прочитать
    # в отрыве от того, на чём он стоит.
    _dh_rates = Counter(f["deadheadCost"] for f in flights
                        if f.get("deadheadCostKnown"))
    _dh_fallback = _dh_rates.most_common(1)[0][0] if _dh_rates else None
    _dh_filled = 0
    if _dh_fallback is not None:
        for f in flights:
            if not f.get("deadheadCostKnown"):
                f["deadheadCost"] = _dh_fallback
                f["deadheadCostKnown"] = False
                _dh_filled += 1
    check["legs_with_deadhead_cost_from_data"] = dh_cost_known
    check["legs_with_deadhead_cost_filled"] = _dh_filled
    check["deadhead_cost_fallback"] = _dh_fallback
    check["deadhead_cost_rates"] = dict(_dh_rates)
    check["legs_with_briefing"] = sum(
        1 for f in flights if f.get("briefingMin") is not None)
    check["legs_with_debriefing"] = sum(
        1 for f in flights if f.get("debriefingMin") is not None)

    # ---------------- seats + owners -------------------------------------
    seat_source = Counter()
    ambiguous = 0
    resolved: List[Optional[Tuple]] = []
    owners: List[Optional[Tuple[str, str]]] = []
    for meta in leg_meta:
        cands = []
        for did, role in sorted(duty_of_leg.get(meta["legId"], [])):
            if role in OP_ROLES:
                sig = _signature(seats_by_duty.get(did, []))
                if sig:
                    cands.append((did, sig))
        if cands:
            sig, amb, winner = _pick_signature(cands)
            if amb:
                ambiguous += 1
            seat_source["solver_duty"] += 1
            resolved.append(sig)
            owners.append(("sequence", seq_of_duty[winner])
                          if winner in seq_of_duty else ("solver", winner))
        else:
            resolved.append(None)
            owners.append(None)
    # aircraft-type mode fill for the structureless tail (counted).
    mode_by_ac: Dict[str, Tuple] = {}
    freq: Dict[str, Counter] = defaultdict(Counter)
    for meta, sig in zip(leg_meta, resolved):
        ac = meta["flight"].get("aircraftType")
        if sig and ac:
            freq[ac][sig] += 1
    for ac, cnt in freq.items():
        mode_by_ac[ac] = min(s for s, n in cnt.items()
                             if n == max(cnt.values()))
    unresolved = []
    fill = Counter()
    for idx, (meta, sig) in enumerate(zip(leg_meta, resolved)):
        if sig is not None:
            continue
        mode = mode_by_ac.get(meta["flight"].get("aircraftType") or "")
        if mode is None:
            unresolved.append(meta["legId"])
        else:
            resolved[idx] = mode
            owners[idx] = ("mode", meta["legId"])
            fill[meta["flight"].get("aircraftType")] += 1
            seat_source["actype_mode"] += 1
    if unresolved:
        errors.append(f"seats: {len(unresolved)} leg(s) with no derivable "
                      f"crew composition — see check artifact")
    check["seat_source"] = dict(seat_source)
    check["seat_signature_ambiguous_legs"] = ambiguous
    check["seat_fill_by_aircraft_type"] = dict(fill)
    check["seat_unresolved_total"] = len(unresolved)
    check["seat_unresolved"] = unresolved[:20]

    assignments: List[dict] = []
    for meta, sig in zip(leg_meta, resolved):
        if sig is None:
            continue
        fl = meta["flight"]
        for i, rank in enumerate(_expand_signature(
                tuple((_code(r), n) for r, n in sig))):
            slot_id = f"{fl['flightLegId']}#{i}"
            assignments.append({
                "id": slot_id, "crewSlotId": slot_id,
                "flight": fl["flightNumber"],
                "flightLegId": fl["flightLegId"],
                "indexInFlight": i, "requiredSkill": rank,
                "employee": None,
            })

    # ---------------- catalog units --------------------------------------
    groups: Dict[Tuple[str, str], dict] = {}
    for meta, sig, owner in zip(leg_meta, resolved, owners):
        if sig is None or owner is None:
            continue
        g = groups.setdefault(owner, {"legs": [], "sigs": []})
        g["legs"].append(meta["flight"])
        g["sigs"].append(sig)
    riders: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    flight_by_src = {m["legId"]: m["flight"] for m in leg_meta}
    rider_unmapped = 0
    for row in solver.get("leg2Duties") or []:
        if not isinstance(row, dict):
            continue
        lid, did = row.get("leg"), row.get("duty")
        role = str(row.get("crewRole") or "")
        if role not in DH_ROLES or not isinstance(lid, str) \
                or not isinstance(did, str):
            continue
        plain = lid[len(DEADHEAD_PREFIX):] \
            if lid.startswith(DEADHEAD_PREFIX) else lid
        fl = flight_by_src.get(plain)
        if fl is None:
            rider_unmapped += 1
            continue
        key = (("sequence", seq_of_duty[did])
               if did in seq_of_duty else ("solver", did))
        riders[key].append(fl)
    catalog_units: List[dict] = []
    # Ключ юнита запоминается при постройке: разбирать служебное имя
    # обратно по двоеточию — значит держать контракт на строке.
    unit_key_of: Dict[str, Tuple[str, str]] = {}
    varying = 0
    rider_count = 0
    for key, g in groups.items():
        active = {fl["flightNumber"] for fl in g["legs"]}
        ride = [fl for fl in riders.get(key, [])
                if fl["flightNumber"] not in active]
        rider_count += len(ride)
        legs = sorted(g["legs"] + ride,
                      key=lambda f: (f["departureUTCDateTime"],
                                     f["flightNumber"]))
        if len(set(g["sigs"])) > 1:
            varying += 1
        seats_list = _expand_signature(
            tuple((_code(r), n) for r, n in _sig_max(g["sigs"])))
        active_legs = [fl for fl in legs if fl["flightNumber"] in active]
        # Требования юнита (2026-07-25): участковые коды — каждому члену;
        # местовые «тип_роль» — по роли места на каждый тип судна юнита.
        unit_req = sorted({c for fl in active_legs
                           for c in fl.get("requiredQualifications") or []})
        unit_acs = sorted({fl.get("aircraftType") for fl in active_legs
                           if fl.get("aircraftType")})
        seat_codes = [sorted({f"{ac}_{role}" for ac in unit_acs})
                      for role in seats_list]
        rec_unit = {
            "fns": [fl["flightNumber"] for fl in legs],
            "active_fns": [fl["flightNumber"] for fl in active_legs],
            "cost": 0,
            "seats": seats_list,
            "sourceUnit": f"{key[0]}:{key[1]}",
            # Ротация решателя — целая командировка; голый наряд без
            # ротации и одиночный участок по моде типа судна — достройка,
            # у которой нет ни начала, ни конца поездки. Ср.
            # _meridian_to_payload: контракт каталога один на оба адаптера.
            "kind": "chain" if key[0] == "sequence" else "fill",
        }
        unit_key_of[rec_unit["sourceUnit"]] = key
        if unit_req:
            rec_unit["requiredCodesAll"] = unit_req
        if any(seat_codes):
            rec_unit["seatCodes"] = seat_codes
        catalog_units.append(rec_unit)
    # ---------------- горизонт планирования (2026-07-24, по методологии
    # авиакомпании): спрос = рейсы месяца планирования + хвосты пейрингов,
    # НАЧИНАЮЩИХСЯ в месяце (включая начатые в предыдущем). Юниты,
    # начинающиеся ПОСЛЕ конца месяца, и бесструктурные рейсы вне месяца —
    # ЗА ГОРИЗОНТОМ: игнорируются самим вендором и исключаются из спроса
    # (счётчик, не дефект покрытия).
    plan_from = _plan_date("planSince")
    plan_to = _plan_date("planUntil")
    if plan_from and plan_to:
        keep_units: List[dict] = []
        beyond_units = 0
        beyond_fns: set = set()
        for u in catalog_units:
            first_dep = date.fromisoformat(
                next(f for f in flights
                     if f["flightNumber"] == u["fns"][0])
                ["departureUTCDateTime"][:10])
            built_by = unit_key_of[u["sourceUnit"]][0]
            starts_after = first_dep > plan_to
            lone_outside = (built_by == "mode"
                            and not (plan_from <= first_dep <= plan_to))
            if starts_after or lone_outside:
                beyond_units += 1
                beyond_fns.update(u["active_fns"])
                continue
            keep_units.append(u)
        if beyond_fns:
            catalog_units = keep_units
            flights = [f for f in flights
                       if f["flightNumber"] not in beyond_fns]
            assignments = [a for a in assignments
                           if a["flight"] not in beyond_fns]
            payload_fns = {f["flightNumber"] for f in flights}
            # пассажирские члены юнитов не должны ссылаться на исключённое
            for u in catalog_units:
                u["fns"] = [fn for fn in u["fns"] if fn in payload_fns]
        check["beyond_horizon_units"] = beyond_units
        check["beyond_horizon_legs"] = len(beyond_fns)
        check["legs_converted"] = len(flights)

    catalog_units.sort(key=lambda u: (u["fns"][0], u["sourceUnit"]))

    # Целость цепочки: сколько участков её ротации в юнит не попало —
    # досталось другому владельцу (участок числится в нескольких нарядах,
    # а владелец у него один) или ушло за горизонт планирования. Считается
    # ПОСЛЕ отсева за горизонт, иначе потеря окажется невидимой. По
    # урезанной цепочке нельзя судить замкнутость на базу: начало или конец
    # поездки она потеряла при стыковке, а не по вине заказчика.
    src_trip_legs: Dict[str, set] = defaultdict(set)
    for row in solver.get("leg2Duties") or []:
        if not isinstance(row, dict):
            continue
        lid, did = row.get("leg"), row.get("duty")
        if not (isinstance(lid, str) and isinstance(did, str)):
            continue
        sid = seq_of_duty.get(did)
        if sid is not None:
            src_trip_legs[sid].add(lid[len(DEADHEAD_PREFIX):]
                                   if lid.startswith(DEADHEAD_PREFIX) else lid)
    src_by_fn = {m["flight"]["flightNumber"]: m["legId"] for m in leg_meta}
    for u in catalog_units:
        if u["kind"] != "chain":
            continue
        have = {src_by_fn[fn] for fn in u["fns"] if fn in src_by_fn}
        sid = unit_key_of[u["sourceUnit"]][1]
        u["sourceLegsMissing"] = len(src_trip_legs.get(sid, set()) - have)

    # ---------------- Ярус 2в (2026-07-24): точные поля юнитов -----------
    # Кредиты рабочего и полётного времени вендора по месяцам, требуемый
    # отдых ПОСЛЕ юнита (ступенчатая таблица layoverRules, откалибрована по
    # материализованным значениям вендора: 98.9% совпадений между
    # пейрингами) и окна занятости для еженедельного отдыха. Юниты без
    # структуры нарядов (mode) полей не получают — прежний путь.
    duties_exact: Dict[str, dict] = {}
    for du in solver.get("duties") or []:
        if not isinstance(du, dict):
            continue
        d0 = parse_zoned_to_utc(du.get("start"))
        d1 = parse_zoned_to_utc(du.get("end"))
        if d0 is None or d1 is None or d1 <= d0:
            continue
        duties_exact[str(du.get("id"))] = {
            "start": d0, "end": d1,
            "dur": int((d1 - d0).total_seconds() // 60),
            "ground": bool(du.get("ground")),
            "rest": bool(du.get("rest")),
            "work_credit": _pt_minutes(du.get("workTimeCredit")) or 0,
            "flight_credit": _pt_minutes(du.get("flightTimeCredit")) or 0,
            "last_arr": du.get("lastArrivalStation"),
        }
    unit_duties: Dict[Tuple[str, str], list] = defaultdict(list)
    for did, sid in seq_of_duty.items():
        if did in duties_exact:
            unit_duties[("sequence", sid)].append(duties_exact[did])
    for key in groups:
        if key[0] == "solver" and key[1] in duties_exact:
            unit_duties[key].append(duties_exact[key[1]])
    for lst in unit_duties.values():
        lst.sort(key=lambda x: x["start"])
    base_counter = Counter(e.get("base") for e in emps_raw
                           if isinstance(e, dict) and e.get("base"))
    home_base = base_counter.most_common(1)[0][0] if base_counter else None
    tz_by_station = {s.get("iata"): s.get("timeZone")
                     for s in solver.get("stations") or []
                     if isinstance(s, dict)}
    def _tz_hours(code, ref):
        from zoneinfo import ZoneInfo
        z = tz_by_station.get(code)
        if not z:
            return None
        try:
            return ZoneInfo(z).utcoffset(ref).total_seconds() / 3600
        except Exception:                                  # noqa: BLE001
            return None

    def _rest_after_min(last_duty, ref) -> int:
        if last_duty["ground"]:
            return 720                                     # PT12H
        on = last_duty["last_arr"] == home_base
        boff = _tz_hours(home_base, ref)
        soff = _tz_hours(last_duty["last_arr"], ref)
        trans = (boff is not None and soff is not None
                 and abs(soff - boff) >= 3)
        if last_duty["dur"] <= 760:                        # ярус 1 таблицы
            return 1440 if trans else (720 if on else 760)
        if trans:
            return 2880 if on else 1440
        return 2160 if on else 1480

    def _month_key(dt: datetime, base_off_h: float) -> str:
        return (dt + timedelta(hours=base_off_h)).strftime("%Y-%m")

    exact_units = 0
    if duties_exact and home_base:
        ref = datetime.combine(
            min(x["start"] for x in duties_exact.values()).date(),
            datetime.min.time()) + timedelta(days=14)
        base_off = _tz_hours(home_base, ref) or 0.0
        by_key = {tuple(u["sourceUnit"].split(":", 1)): u
                  for u in catalog_units}
        epoch = datetime(1970, 1, 1)
        for key, dulist in unit_duties.items():
            rec = by_key.get(key)
            if rec is None:
                continue
            workers = [x for x in dulist if not x["rest"]]
            if not workers:
                continue
            work_m: Dict[str, int] = {}
            block_m: Dict[str, int] = {}
            windows: List[List[int]] = []
            for x in workers:
                mk = _month_key(x["start"], base_off)
                work_m[mk] = work_m.get(mk, 0) + x["work_credit"]
                if x["flight_credit"]:
                    block_m[mk] = block_m.get(mk, 0) + x["flight_credit"]
                windows.append([
                    int((x["start"] - epoch).total_seconds() // 60),
                    int((x["end"] - epoch).total_seconds() // 60)])
            windows.sort()
            merged: List[List[int]] = []
            for w in windows:
                if merged and w[0] <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], w[1])
                else:
                    merged.append(list(w))
            if any(work_m.values()):
                rec["workMinutesByMonth"] = dict(sorted(work_m.items()))
            if any(block_m.values()):
                rec["blockMinutesByMonth"] = dict(sorted(block_m.items()))
            rec["busyWindows"] = merged
            rec["minRestAfterMin"] = _rest_after_min(workers[-1], ref)
            exact_units += 1
    check["catalog_units_exact_fields"] = exact_units

    fl_by_fn = {f["flightNumber"]: f for f in flights}
    check["catalog_units"] = len(catalog_units)
    check["catalog_units_closed"] = sum(
        1 for u in catalog_units
        if u["fns"] and fl_by_fn[u["fns"][0]]["departureAirport"]
        == fl_by_fn[u["fns"][-1]]["arrivalAirport"])
    check["catalog_units_varying_composition"] = varying
    check["catalog_rider_legs_attached"] = rider_count
    check["catalog_riders_unmapped"] = rider_unmapped
    check["catalog_units_by_kind"] = dict(Counter(
        u["sourceUnit"].split(":")[0] for u in catalog_units))
    chain_units = [u for u in catalog_units if u["kind"] == "chain"]
    check["catalog_chains_complete"] = sum(
        1 for u in chain_units if not u["sourceLegsMissing"])
    check["catalog_chains_truncated"] = sum(
        1 for u in chain_units if u["sourceLegsMissing"])
    check["catalog_units_fill"] = len(catalog_units) - len(chain_units)

    # ---------------- employees ------------------------------------------
    # PII minimization: birthday / doNotFlyWith / studentFor dropped.
    # Эффективный диапазон = ОБЪЕДИНЕНИЕ планового окна файла
    # (parameters.planSince/planUntil, формат '...Z[UTC]') и дат рейсов —
    # недоступности до первого рейса не должны срезаться.
    bounds: List[date] = [d for d in (_plan_date("planSince"),
                                      _plan_date("planUntil")) if d]
    if flights:
        bounds.append(min(date.fromisoformat(
            f["departureUTCDateTime"][:10]) for f in flights))
        bounds.append(max(date.fromisoformat(
            f["arrivalUTCDateTime"][:10]) for f in flights))
    eff_from = min(bounds) if bounds else date(2000, 1, 1)
    eff_to = max(bounds) if bounds else date(2000, 1, 1)
    check["effective_span"] = [eff_from.isoformat(), eff_to.isoformat()]

    def _plan_dt_date(v) -> Optional[date]:
        """Дата из '2026-08-01T00:00:00Z[UTC]' и подобных; None если нет."""
        try:
            return date.fromisoformat(str(v or "")[:10])
        except ValueError:
            return None

    dropped_windows: Counter = Counter()
    # Коды допусков, на которые ссылаются требования (2026-07-25):
    # участковые («Допуск СНГ»/«Допуск МВЛ») + местовые «тип_роль»
    # (проверено: требование места == f"{acType}_{rank}" на всех 11640
    # строках источника). Только для них переносятся датированные окна.
    def _norm_q(s: str) -> str:
        """Нормализация кода допуска ДЛЯ СОПОСТАВЛЕНИЯ: гомоглифы и все
        пробелы (в источнике встречаются 'A320  _CA' и кириллическая С)."""
        import re as _re
        return _re.sub(r"\s+", "", _code(str(s or "")))

    fl_by_fn_req = {f["flightNumber"]: f for f in flights}
    referenced_codes: Dict[str, str] = {}    # норм -> канонический код
    for f in flights:
        for c in f.get("requiredQualifications") or []:
            referenced_codes[_norm_q(c)] = c
    for a in assignments:
        ac_ref = (fl_by_fn_req.get(a["flight"]) or {}).get("aircraftType")
        if ac_ref:
            canon = f"{ac_ref}_{a['requiredSkill']}"
            referenced_codes.setdefault(_norm_q(canon), canon)
    check["required_codes_referenced"] = len(referenced_codes)
    employees: List[dict] = []
    qualifications: List[dict] = []
    quals_by_kind = Counter()
    quals_undated = 0
    quals_expired = 0
    placeholder_ranks = Counter()
    not_allocable = 0
    ground_types = Counter()
    seen_emp = set()
    for i, e in enumerate(emps_raw):
        if not isinstance(e, dict) or not isinstance(e.get("id"), str):
            errors.append(f"employees[{i}]: not an object with string id")
            continue
        emp_id = e["id"]
        if emp_id in seen_emp:
            errors.append(f"employees[{i}]: duplicate id {emp_id!r}")
            continue
        seen_emp.add(emp_id)
        if e.get("allocable") is False:
            not_allocable += 1
            continue
        base = e.get("base")
        if not isinstance(base, str) or not base.strip():
            errors.append(f"employee {emp_id}: missing base")
            continue
        ranks = e.get("ranks") if isinstance(e.get("ranks"), dict) else {}
        skills: set = set()
        ratings: set = set()
        for fleet, rank in ranks.items():
            if not (isinstance(fleet, str) and isinstance(rank, str)
                    and rank.strip()):
                continue
            if rank.strip().startswith("<") and rank.strip().endswith(">"):
                placeholder_ranks[rank.strip()] += 1
                continue
            skills.add(_code(rank))
            ratings.add(_code(fleet))

        unavailable: set = set()
        for duty in ground_by_emp.get(emp_id, ()):
            d0 = parse_zoned_to_utc(duty.get("start"))
            d1 = parse_zoned_to_utc(duty.get("end"))
            if d0 is None or d1 is None:
                errors.append(f"employee {emp_id}: ground duty with "
                              f"unparseable dates")
                continue
            if d1 < d0:
                errors.append(f"employee {emp_id}: ground duty ends "
                              f"before it starts")
                continue
            ground_types[str(duty.get("group") or
                             ("SBY" if duty.get("sby") else "GROUND"))
                         .split("::")[0]] += 1
            end_d = d1.date()
            if end_d > d0.date() and d1.time() == d1.time().min:
                end_d -= timedelta(days=1)
            day = max(d0.date(), eff_from)
            stop = min(end_d, eff_to)
            while day <= stop:
                unavailable.add(day.isoformat())
                day += timedelta(days=1)

        emp_rec = {
            "id": emp_id, "name": emp_id,
            "homeAirport": _code(base),
            "skills": sorted(skills),
            "typeRatings": sorted(ratings),
            "unavailableDays": sorted(unavailable),
        }
        # «Импортировать абсолютно все данные» (2026-07-24): операционные
        # ограничения сотрудника — расширение канона (additionalProperties
        # допускает), только табельные номера и коды, без персональных
        # данных. Валидаторы паритета читают их отсюда.
        extras: dict = {}
        for src_key, dst_key in (("workTimeLimit", "workTimeLimit"),
                                 ("flightTimeLimit", "flightTimeLimit"),
                                 ("splitShiftsLimit", "splitShiftsLimit"),
                                 ("groundReserveLimit",
                                  "groundReserveLimit"),
                                 ("annualFlightTimeQuote",
                                  "annualFlightTimeQuote")):
            v = e.get(src_key)
            if isinstance(v, (str, int, float)) and not isinstance(v, bool):
                extras[dst_key] = v
        # Списки могут быть строками ИЛИ записями со сроком действия
        # {code, activeSince, activeUntil} — вторую форму адаптер молча
        # ронял, и проверка «запрещённые станции» работала на ПУСТЫХ
        # данных (перепроверка 2026-07-24, дефект закрыт). Берётся код;
        # запись учитывается, если её срок действия пересекает окно
        # данных; коды, не являющиеся станциями набора (страновые «=XX»,
        # цифровые), сохраняются и отдельно считаются как
        # неинтерпретируемые — молча их отбрасывать нельзя.
        for src_key in ("doNotFlyWith", "stationsDenied", "layoverDenied"):
            v = e.get(src_key)
            if not isinstance(v, list) or not v:
                continue
            codes: List[str] = []
            for x in v:
                if isinstance(x, (str, int)):
                    codes.append(str(x))
                    continue
                if not isinstance(x, dict):
                    continue
                code = x.get("code") or x.get("station") or x.get("iata")
                if not isinstance(code, str) or not code:
                    continue
                since = _plan_dt_date(x.get("activeSince"))
                until = _plan_dt_date(x.get("activeUntil"))
                if since and eff_to and since > eff_to:
                    dropped_windows[src_key] += 1
                    continue
                if until and eff_from and until < eff_from:
                    dropped_windows[src_key] += 1
                    continue
                codes.append(code)
            if codes:
                extras[src_key] = sorted(set(codes))
        if extras:
            emp_rec["vendorConstraints"] = extras
        # Исполняемые решателем персональные ограничения (2026-07-24):
        # списки несовместимости и помесячные лимиты — типизированными
        # полями сотрудника, решатель читает их напрямую.
        if extras.get("doNotFlyWith"):
            emp_rec["doNotFlyWith"] = extras["doNotFlyWith"]
        # Запрещённые станции — исполняемым полем сотрудника: решатель
        # не должен ставить человека на цепочку, посещающую такую
        # станцию (правило вендора DO_NOT_VISIT_DENIED_STATIONS).
        if extras.get("stationsDenied"):
            emp_rec["stationsDenied"] = extras["stationsDenied"]
        if extras.get("layoverDenied"):
            emp_rec["layoverDenied"] = extras["layoverDenied"]
        wtl = _pt_minutes(e.get("workTimeLimit"))
        if wtl:
            emp_rec["workTimeLimitMin"] = wtl
        ftl_min = _pt_minutes(e.get("flightTimeLimit"))
        if ftl_min:
            emp_rec["flightTimeLimitMin"] = ftl_min
        # Датированные окна допусков (2026-07-25): только коды, на
        # которые ссылаются требования участков и мест — решатель и
        # проверятели читают их напрямую. Минуты эпохи UTC; отсутствие
        # границы = открытое окно (кодируется большим значением).
        if referenced_codes:
            windows: Dict[str, list] = {}
            for f in e.get("features") or []:
                if not isinstance(f, dict):
                    continue
                canon = referenced_codes.get(_norm_q(f.get("code")))
                if canon is None:
                    continue
                a = parse_zoned_to_utc(f.get("activeSince"))
                b = parse_zoned_to_utc(f.get("activeUntil"))
                epoch0 = datetime(1970, 1, 1)
                lo = int((a - epoch0).total_seconds() // 60) if a else 0
                hi = (int((b - epoch0).total_seconds() // 60)
                      if b else 4_000_000_000 // 60)
                if hi > lo:
                    windows.setdefault(canon, []).append([lo, hi])
            if windows:
                for lst in windows.values():
                    lst.sort()
                emp_rec["qualificationWindows"] = windows
        employees.append(emp_rec)

        fleet_names = set(ranks) | {_code(f) for f in ranks}
        for f in e.get("features") or []:
            if not isinstance(f, dict):
                continue
            code_raw = f.get("code")
            if not (isinstance(code_raw, str) and code_raw):
                continue
            raw_to = str(f.get("activeUntil") or "")[:10]
            try:
                valid_to = (raw_to if len(raw_to) == 10
                            and date.fromisoformat(raw_to) else None)
            except ValueError:
                valid_to = None
            if not valid_to:
                quals_undated += 1
                continue
            if date.fromisoformat(valid_to) < eff_from:
                quals_expired += 1
                continue
            code = _text(code_raw)[:QUAL_CODE_MAX]
            qual = {"employee": emp_id,
                    "kind": classify_qualification(code_raw, fleet_names),
                    "code": code, "validTo": valid_to}
            raw_from = str(f.get("activeSince") or "")[:10]
            try:
                if len(raw_from) == 10 and date.fromisoformat(raw_from):
                    qual["validFrom"] = raw_from
            except ValueError:
                pass
            qualifications.append(qual)
            quals_by_kind[qual["kind"]] += 1

    check["employees_total"] = len(employees)
    check["employees_not_allocable_skipped"] = not_allocable
    check["placeholder_ranks_skipped"] = dict(placeholder_ranks)
    check["ground_activity_types"] = dict(ground_types)
    check["qualifications_by_kind"] = dict(quals_by_kind)
    check["qualifications_skipped_undated"] = quals_undated
    check["qualifications_skipped_expired"] = quals_expired
    check["pii_dropped_fields"] = ["birthday", "doNotFlyWith",
                                  "studentFor", "rankString"]

    # ---------------- airports -------------------------------------------
    referenced = ({f["departureAirport"] for f in flights}
                  | {f["arrivalAirport"] for f in flights}
                  | {e["homeAirport"] for e in employees})
    st_by_code: Dict[str, dict] = {}
    for st in solver.get("stations") or []:
        if isinstance(st, dict) and isinstance(st.get("iata"), str):
            st_by_code.setdefault(_code(st["iata"]), st)
    airports = []
    geo = 0
    tz_known = 0
    cost_known = cost_unknown = 0
    _uc = (solver.get("parameters") or {}).get("unknownStationCostThreshold")
    unknown_cost = (int(_uc) if isinstance(_uc, (int, float))
                    and not isinstance(_uc, bool) else None)
    for code in sorted(referenced):
        st = st_by_code.get(code) or {}
        nm = st.get("name") or st.get("city")
        ap = {"code": code,
              "name": _text(nm) if isinstance(nm, str) and nm.strip()
              else code}
        lat, lon = st.get("latitude"), st.get("longitude")
        if (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
                and not isinstance(lat, bool) and not isinstance(lon, bool)
                and (lat or lon)):
            ap["latitude"] = float(lat)
            ap["longitude"] = float(lon)
            geo += 1
        # Часовой пояс станции (разбор 2026-07-27). Без него не считаются
        # правила, заданные в МЕСТНОМ времени, а их у заказчика четыре:
        # ночная смена (22:00-06:00), «тёмная» смена (23:00-07:00), начало
        # следующей смены после ночной не ранее 06:00 и окончание смены
        # перед отпуском до 20:00. Раньше пояс отбрасывался, и эти правила
        # были непроверяемы в принципе — не «не реализованы», а нечем
        # реализовать.
        tzid = st.get("timeZone")
        if isinstance(tzid, str) and tzid.strip():
            ap["timeZone"] = tzid.strip()
            tz_known += 1
        # Стоимость ночёвки на станции — ДАННЫЕ источника, а не наш
        # коэффициент. Без неё цель расчёта приходится придумывать, и
        # сравнение с чужим решением теряет смысл: у источника перегон
        # пассажиром дороже ночёвки в 12 раз, у нас по умолчанию втрое.
        #
        # Значение, равное порогу неизвестной станции
        # (`unknownStationCostThreshold`), означает «ставки нет» — оно
        # переносится как ноль и пересчитывается отдельно, иначе
        # сторожевое число попало бы в цель как настоящая цена.
        cost = st.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            if unknown_cost is not None and int(cost) >= unknown_cost:
                ap["layoverCost"] = 0
                ap["layoverCostKnown"] = False
                cost_unknown += 1
            else:
                ap["layoverCost"] = int(cost)
                ap["layoverCostKnown"] = True
                cost_known += 1
        airports.append(ap)
    check["airports_referenced"] = len(airports)
    check["airports_with_timezone"] = tz_known
    check["airports_with_layover_cost"] = cost_known
    check["airports_without_layover_cost"] = cost_unknown
    # Персональные запреты станций: сколько сотрудников их несёт и сколько
    # кодов не является станцией набора (страновые «=XX», цифровые —
    # решателем не исполняются, но и не теряются молча).
    _station_codes = {a["code"] for a in airports}
    _denied_codes = {c for e2 in employees
                     for c in e2.get("stationsDenied") or []}
    check["denied_stations_employees"] = sum(
        1 for e2 in employees if e2.get("stationsDenied"))
    check["denied_stations_codes"] = len(_denied_codes)
    check["denied_stations_codes_not_in_network"] = len(
        _denied_codes - _station_codes)
    check["denied_stations_records_outside_window"] = \
        dropped_windows.get("stationsDenied", 0)
    check["airports_with_geo"] = geo

    payload = {
        "formatVersion": "2",
        "provenance": {
            "adapter": "solver",
            "source": source_name,
            "convertedAt": datetime.now(timezone.utc)
                                   .strftime("%Y-%m-%dT%H:%M:%SZ"),
            "conversionCheck": check_rel,
        },
        "airports": airports,
        "employees": employees,
        "flights": flights,
        "flightAssignments": assignments,
    }
    if qualifications:
        payload["qualifications"] = qualifications
    return payload, check, errors, catalog_units


# ------------------------------------------------- vendor rules / solution

_PT_RE = None


def _pt_minutes(raw: object) -> Optional[int]:
    """'PT100H'/'PT12H30M'/'PT600S' -> минуты (None если не разобрать)."""
    global _PT_RE
    import re as _re
    if _PT_RE is None:
        _PT_RE = _re.compile(
            r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$")
    m = _PT_RE.match(str(raw or ""))
    if not m or not any(m.groups()):
        return None
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = float(m.group(3) or 0)
    return int(h * 60 + mi + s / 60)


# Русские формулировки главных вендорских позиций; остальным — честный
# генерический шаблон (Ярус 2а: структурная запись, исполнителя нет).
_RULE_RU = {
    "WEEKLY_REST": "Каждую неделю сотруднику положен установленный "
                   "непрерывный отдых (вендор: 48 часов, дважды в неделю).",
    "WORK_TIME_LIMIT": "Суммарное рабочее время сотрудника за плановый "
                       "период не превышает лимита.",
    "FLIGHT_TIME_LIMIT": "Суммарный налёт сотрудника за плановый период "
                         "не превышает лимита.",
    "ASSIGNMENT_CONFLICT": "Сотрудник не может занимать два места, "
                           "пересекающихся во времени.",
    "DO_NOT_FLY_WITH": "Сотрудники из списка несовместимости не летают "
                       "в одном экипаже.",
    "UNASSIGNED": "Каждое требуемое место экипажа должно быть занято.",
    "LAYOVER_BEFORE_DUTY": "Перед нарядом соблюдается минимальный отдых.",
    "LAYOVER_AFTER_NIGHT_DUTIES": "После последовательности ночных "
                                  "нарядов соблюдается увеличенный отдых.",
    "LAYOVER_AFTER_DARK_DUTIES": "После «тёмных» нарядов соблюдается "
                                 "увеличенный отдых.",
    "DO_NOT_VISIT_DENIED_STATIONS": "Сотрудник не назначается на участки "
                                    "через запрещённые ему станции.",
    "LAYOVER_DENIED_STATIONS": "Ночёвки сотрудника не планируются в "
                               "запрещённых ему станциях.",
    "GENDER_ACCOMMODATION": "Размещение экипажа на ночёвке учитывает пол.",
    "PREFER_MINIMUM_CREW_SIZE": "Предпочтителен минимально достаточный "
                                "состав экипажа.",
    "STUDENT_WITHOUT_MENTOR": "Стажёр не летает без наставника.",
    "WEEKLY_WORK_TIME_LIMIT": "Недельное рабочее время не превышает "
                              "лимита.",
    "maximumPairingDays": "Пейринг длится не более установленного числа "
                          "суток.",
    "solvingTimeLimit": "Бюджет времени решателя вендора.",
    "nightStart": "Начало ночного окна по местному времени.",
    "nightEnd": "Конец ночного окна по местному времени.",
    "acclimatizationPeriod": "Период акклиматизации после смены поясов.",
}


def build_locked_units(solver: dict, catalog_units: List[dict]) -> dict:
    """Закреплённые планировщиками назначения («залоченные», 2026-07-24):
    строки employeeAssignments с locked=true И сотрудником проецируются на
    юниты каталога (sequence/solver). Наряды отдыха и юниты вне каталога
    пропускаются со счётчиками — приёмник ничего не придумывает."""
    duties_by_id = {d["id"]: d for d in solver.get("duties") or []
                    if isinstance(d, dict) and isinstance(d.get("id"), str)}
    seq_id_of_full = {s["id"]: s["sequenceId"]
                      for s in solver.get("sequences") or []
                      if isinstance(s, dict)
                      and isinstance(s.get("id"), str)
                      and isinstance(s.get("sequenceId"), str)}
    seq_votes: Dict[str, Counter] = defaultdict(Counter)
    for ea in solver.get("employeeAssignments") or []:
        if not isinstance(ea, dict):
            continue
        did, full = ea.get("duty"), ea.get("sequence")
        if isinstance(did, str) and isinstance(full, str) \
                and full in seq_id_of_full:
            seq_votes[did][seq_id_of_full[full]] += 1
    seq_of_duty = {did: min(s for s, n in cnt.items()
                            if n == max(cnt.values()))
                   for did, cnt in seq_votes.items()}
    unit_keys = {u["sourceUnit"] for u in catalog_units}
    per_unit: Dict[str, set] = defaultdict(set)
    skipped_rest = 0
    skipped_outside = 0
    rows = 0
    for ea in solver.get("employeeAssignments") or []:
        if not isinstance(ea, dict) or not ea.get("locked"):
            continue
        emp, did = ea.get("employee"), ea.get("duty")
        if not (isinstance(emp, str) and emp and isinstance(did, str)):
            continue
        duty = duties_by_id.get(did) or {}
        if duty.get("rest"):
            skipped_rest += 1
            continue
        key = (f"sequence:{seq_of_duty[did]}" if did in seq_of_duty
               else f"solver:{did}")
        if key not in unit_keys:
            skipped_outside += 1
            continue
        per_unit[key].add(emp)
        rows += 1
    return {
        "units": [{"sourceUnit": k, "employees": sorted(v)}
                  for k, v in sorted(per_unit.items())],
        "locked_rows": rows,
        "skipped_rest_rows": skipped_rest,
        "skipped_outside_catalog": skipped_outside,
    }


def build_rule_pack(solver: dict, source_name: str, *,
                    country: Optional[str] = None) -> dict:
    """40 параметров + 48 весов -> импортируемый Пакет правил COSMOS
    (Ярус 2а): каждая позиция — правило с происхождением; три позиции с
    достоверным пересчётом несут исполняемое ограничение Яруса 1, прочие
    — структурные записи (kind=note) со всеми исходными значениями.

    Страну юрисдикции функция НЕ знает — её подаёт вызывающий из своего
    канала данных (поле запроса, флаг CLI); без неё поле остаётся пустым,
    а обязательность обеспечивают точки входа (_rules_import, CLI)."""
    params = solver.get("parameters") or {}
    weights = solver.get("constraintWeightOverrides") or {}
    src = {"document": f"Экспорт солвера вендора ({source_name})",
           "section": "parameters / constraintWeightOverrides"}
    rules: List[dict] = []

    def note_rule(title: str, statement: str, note_obj: dict) -> dict:
        return {"title": title[:120], "statement": statement[:500],
                "source": src,
                "constraint": {"kind": "note",
                               "note": json.dumps(
                                   note_obj, ensure_ascii=False,
                                   sort_keys=True)[:2000]}}

    # Достоверные пересчёты в Ярус 1 (исполняемые сегодня).
    mp_days = params.get("maximumPairingDays")
    if isinstance(mp_days, int) and mp_days > 0:
        rules.append({
            "title": "Максимальная длительность пейринга",
            "statement": f"Пейринг длится не более {mp_days} суток "
                         f"(вендор: maximumPairingDays).",
            "source": src,
            "constraint": {"kind": "parameter",
                           "parameter": "max_pairing_span_min",
                           "value": mp_days * 1440}})
    ftl = None
    for e in solver.get("employees") or []:
        if isinstance(e, dict):
            ftl = _pt_minutes(e.get("flightTimeLimit"))
            if ftl:
                break
    if ftl:
        rules.append({
            "title": "Месячный лимит налёта",
            "statement": f"Налёт сотрудника за план не превышает "
                         f"{ftl // 60} ч (вендор: flightTimeLimit; блок-"
                         f"часы как приближение налёта).",
            "source": src,
            "constraint": {"kind": "parameter",
                           "parameter": "max_monthly_block_min",
                           "value": ftl}})
    lag = _pt_minutes((params.get("layoverRules") or {})
                      .get("layoverAfterGround"))
    if lag:
        rules.append({
            "title": "Минимальный отдых между нарядами",
            "statement": f"Между нарядами соблюдается отдых не менее "
                         f"{lag // 60} ч (вендор: layoverAfterGround — "
                         f"нижняя граница порогов лейовера).",
            "source": src,
            "constraint": {"kind": "parameter",
                           "parameter": "min_rest_min", "value": lag}})

    for key in sorted(params):
        stmt = _RULE_RU.get(key, f"Параметр вендора «{key}» (структурная "
                                 f"запись Яруса 2а; исполнителя в COSMOS "
                                 f"пока нет).")
        rules.append(note_rule(f"Параметр: {key}", stmt,
                               {"vendor_parameter": key,
                                "value": params.get(key)}))
    for code in sorted(weights):
        level = str(weights.get(code))
        stmt = _RULE_RU.get(code, f"Ограничение вендора «{code}» уровня "
                                  f"{level} (структурная запись Яруса 2а).")
        rules.append(note_rule(
            f"Ограничение: {code} [{level}]",
            stmt + f" Уровень веса вендора: {level}.",
            {"vendor_constraint": code, "weight_level": level,
             "enforced_in_cosmos": False}))
    return {"name": f"Правила вендора ({source_name[:40]})",
            "country": (country or ""),
            "notes": "Автоимпорт из солвер-файла (format:5), "
                     "детерминированный; 3 правила исполняемы Ярусом 1, "
                     "остальные — структурные записи Яруса 2а.",
            "rules": rules}


def build_vendor_solution(solver: dict, payload: dict) -> dict:
    """Решение вендора как канонический ростер поверх НАШЕГО payload:
    занятое место-на-наряде покрывает все операционные участки наряда;
    ряды зеркалят спрос payload (роль к роли, в пределах спроса участка).
    Незанятые места остаются employee=null — сравнение честное."""
    duty_map = {d["id"]: d for d in solver.get("duties") or []
                if isinstance(d, dict) and isinstance(d.get("id"), str)}
    legs_of_duty: Dict[str, List[str]] = defaultdict(list)
    for r in solver.get("leg2Duties") or []:
        if (isinstance(r, dict) and str(r.get("crewRole")) in OP_ROLES
                and isinstance(r.get("leg"), str)
                and isinstance(r.get("duty"), str)
                and not r["leg"].startswith(DEADHEAD_PREFIX)):
            legs_of_duty[r["duty"]].append(r["leg"])
    fn_of_src = {f["sourceLegId"]: f["flightNumber"]
                 for f in payload["flights"]}
    filled: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for ea in solver.get("employeeAssignments") or []:
        if not isinstance(ea, dict):
            continue
        did, emp = ea.get("duty"), ea.get("employee")
        rank = ea.get("preferredRank")
        duty = duty_map.get(did)
        if not (isinstance(emp, str) and emp and isinstance(rank, str)
                and duty and not duty.get("ground")):
            continue
        for lid in legs_of_duty.get(did, ()):
            fn = fn_of_src.get(lid)
            if fn:
                filled[(fn, _code(rank))].append(emp)
    assignments = []
    used: Dict[Tuple[str, str], int] = defaultdict(int)
    vendor_rows = 0
    for a in payload["flightAssignments"]:
        key = (a["flight"], a["requiredSkill"])
        pool = filled.get(key) or []
        i = used[key]
        emp = pool[i] if i < len(pool) else None
        used[key] += 1
        row = dict(a)
        row["employee"] = emp
        if emp:
            vendor_rows += 1
        assignments.append(row)
    return {"airports": payload["airports"],
            "employees": payload["employees"],
            "flights": payload["flights"],
            "flightAssignments": assignments,
            "vendor_filled_rows": vendor_rows}


# --------------------------------------------------------------------- CLI

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Solver export (format:5) -> canonical payload, "
                    "solver file as the ONLY source")
    ap.add_argument("solver_file", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--check", type=Path)
    ap.add_argument("--emit-catalog", type=Path, default=None)
    ap.add_argument("--catalog-label", default=None)
    ap.add_argument("--emit-rules", type=Path, default=None,
                    help="выпустить Пакет правил (импортируемый JSON) из "
                         "parameters+constraintWeightOverrides")
    ap.add_argument("--rules-country", default=None,
                    help="страна юрисдикции Пакета правил (INTL или код "
                         "ISO-3166 из двух букв); обязательна вместе с "
                         "--emit-rules — солвер-файл страну не несёт")
    ap.add_argument("--emit-vendor-solution", type=Path, default=None,
                    help="выпустить решение вендора как канонический "
                         "ростер поверх нашего payload (для сравнения)")
    ap.add_argument("--emit-metrics", type=Path, default=None,
                    help="выпустить 60 метрик вендора отдельным артефактом")
    ap.add_argument("--emit-locked", type=Path, default=None,
                    help="выпустить закреплённые планировщиками назначения "
                         "(locked) как файл для режима залоченных этапа 2")
    args = ap.parse_args(argv)

    # Отказ ДО конвертации: пакет без страны действовал бы на все
    # наборы (тихое расширение области действия), поэтому страну обязан
    # явно подать оператор — значение из данных, не из кода.
    rules_country: Optional[str] = None
    if args.emit_rules is not None:
        rules_country = str(args.rules_country or "").strip().upper()
        if not _COUNTRY_RE.match(rules_country):
            print("ОТКАЗ: --emit-rules требует --rules-country (INTL или "
                  "код ISO-3166 из двух заглавных букв): солвер-файл "
                  "страну юрисдикции не несёт, а Пакет правил без страны "
                  "действовал бы на все наборы.", flush=True)
            return 2

    src: Path = args.solver_file
    if not src.is_file():
        print(f"ERROR: solver file not found: {src}", flush=True)
        return 2
    out_path = args.out or src.with_name(src.stem + "_payload.json")
    check_path = args.check or src.with_name(
        src.stem + "_conversion_check.json")
    print("=== Solver (format:5) -> canonical payload ===", flush=True)
    print(f"  input:  {src}", flush=True)
    try:
        check_rel = str(check_path.resolve().relative_to(HERE)) \
            .replace("\\", "/")
    except ValueError:
        check_rel = str(check_path)

    def _write_check(check, errors, sanity, ok):
        body = {"source": src.name,
                "generatedAt": datetime.now(timezone.utc)
                                       .strftime("%Y-%m-%dT%H:%M:%SZ"),
                **check, "errors": errors[:50],
                "errors_total": len(errors), **(sanity or {}), "ok": ok}
        check_path.parent.mkdir(parents=True, exist_ok=True)
        check_path.write_text(json.dumps(body, ensure_ascii=False,
                                         indent=1), encoding="utf-8")
        print(f"  check:  {check_path}", flush=True)

    try:
        solver = _load_json(src, "solver")
        payload, check, errors, units = convert(solver, src.name, check_rel)
    except SourceError as exc:
        _write_check({}, [str(exc)], None, False)
        print(f"  ERROR: {exc}", flush=True)
        return 1
    except Exception as exc:            # noqa: BLE001 — чек всегда
        import traceback
        traceback.print_exc()
        _write_check({}, [f"internal error: "
                          f"{exc.__class__.__name__}: {exc}"], None, False)
        print(f"  ERROR: internal error: {exc}", flush=True)
        return 1

    if errors:
        _write_check(check, errors, None, False)
        print(f"  ERROR: {len(errors)} problem(s):", flush=True)
        for e in errors[:20]:
            print(f"    {e}", flush=True)
        return 1
    if not payload.get("flights"):
        _write_check(check, ["no operating legs"], None, False)
        return 3
    sanity = run_sanity_checks(payload)
    if any(v for v in sanity.values()):
        _write_check(check, [], sanity, False)
        print("  ERROR: sanity violations (see check artifact)", flush=True)
        return 3
    import jsonschema
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as exc:
        _write_check(check, [f"self-validation: {exc.message}"], sanity,
                     False)
        print(f"  ERROR: self-invalid output: {exc.message}", flush=True)
        return 4

    if args.emit_catalog is not None:
        active_all = [fn for u in units for fn in u["active_fns"]]
        fns_set = {f["flightNumber"] for f in payload["flights"]}
        if (len(active_all) != len(set(active_all))
                or set(active_all) != fns_set
                or not all(fn in fns_set
                           for u in units for fn in u["fns"])):
            _write_check(check, ["catalog: active units do not partition "
                                 "the converted flights"], sanity, False)
            return 3
        doc = {"inst": None,
               "label": args.catalog_label or out_path.stem,
               "profile": "solver-sequences",
               "run_id": datetime.now(timezone.utc)
                                 .strftime("solver-%Y%m%dT%H%M%SZ"),
               "ip_status": "VENDOR_CATALOG", "ip_objective": None,
               "source": src.name, "selected": units}
        args.emit_catalog.parent.mkdir(parents=True, exist_ok=True)
        args.emit_catalog.write_text(json.dumps(doc, ensure_ascii=False,
                                                indent=1),
                                     encoding="utf-8")
        print(f"  catalog: {args.emit_catalog} ({len(units)} units)",
              flush=True)

    if args.emit_rules is not None:
        pack = build_rule_pack(solver, src.name, country=rules_country)
        args.emit_rules.parent.mkdir(parents=True, exist_ok=True)
        args.emit_rules.write_text(json.dumps(pack, ensure_ascii=False,
                                              indent=1), encoding="utf-8")
        print(f"  rules:   {args.emit_rules} "
              f"({len(pack['rules'])} правил)", flush=True)
    if args.emit_vendor_solution is not None:
        vsol = build_vendor_solution(solver, payload)
        args.emit_vendor_solution.parent.mkdir(parents=True, exist_ok=True)
        args.emit_vendor_solution.write_text(
            json.dumps(vsol, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print(f"  vendor-solution: {args.emit_vendor_solution} "
              f"({vsol['vendor_filled_rows']} занятых рядов)", flush=True)
    if args.emit_metrics is not None:
        metrics = solver.get("metrics") or []
        args.emit_metrics.parent.mkdir(parents=True, exist_ok=True)
        args.emit_metrics.write_text(
            json.dumps({"source": src.name, "metrics": metrics},
                       ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  metrics: {args.emit_metrics} ({len(metrics)})",
              flush=True)

    if args.emit_locked is not None:
        locked_doc = build_locked_units(solver, units)
        args.emit_locked.parent.mkdir(parents=True, exist_ok=True)
        args.emit_locked.write_text(
            json.dumps(locked_doc, ensure_ascii=False, indent=1),
            encoding="utf-8")
        print(f"  locked: {args.emit_locked} "
              f"({len(locked_doc['units'])} юнитов, "
              f"{locked_doc['locked_rows']} строк; вне каталога "
              f"{locked_doc['skipped_outside_catalog']})", flush=True)

    _write_check(check, [], sanity, True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"  payload: {out_path}", flush=True)
    print(f"  {len(payload['flights'])} flights, "
          f"{len(payload['employees'])} employees, "
          f"{len(payload['flightAssignments'])} crew slots, "
          f"{len(payload['airports'])} airports, "
          f"{len(payload.get('qualifications') or [])} qualifications",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
