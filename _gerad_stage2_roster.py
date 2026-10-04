"""Phase 2, config «двухэтапная-тендер», STAGE 2: Timefold rostering over
the CG-selected pairings under the pairing-aware personalized profile
(rules=GERAD2P), then expansion to a flight-level roster and UNIFORM
scoring under rules=GERAD2 — the same scale the «прямая» config is
measured on.

Pipeline per instance:
  1. read final-report/gerad_colgen_selected_instN.json  (CG + CP-SAT
     selection, dumped by _gerad_colgen.py --dump-selected)
  2. build a synthetic payload: one Flight per pairing (span = first dep ..
     last arr; legIds = real leg ids; blockMinutes = summed leg durations),
     one crew slot per pairing; employees from instN_p2.json (vacations +
     preferred flights)
  3. solve on an isolated JVM: rules=GERAD2P, LA, fixed seed
  4. expand pairing assignments to per-leg assignments; compute the same
     personalization metrics as _gerad_phase2_direct.py
  5. uniform score: POST the expanded flight-level roster to
     /schedules/score?rules=GERAD2 (and score the «прямая» solution too if
     present, for a same-scale pair of numbers)

Usage (numeric GERAD mode, backward compatible):
  python _gerad_stage2_roster.py [inst_from] [inst_to] [budget_s] [seed]
Usage (generic mode — any uploaded canonical payload + its stage-1 catalog):
  python _gerad_stage2_roster.py --payload <path> --catalog <path>
                                 --label <slug> [budget_s] [seed]
In generic mode every artifact is named by <label> instead of instN
(gerad_stage2_assignment_<label>.json, p2_two_stage_solution_<label>.json)
and the journal row carries {"label": slug, "inst": null}; the gate flow
(coverage + trajectory certificate + 3-verdict gate + diagnostics + exit
codes) is IDENTICAL to the numeric mode.
"""
from __future__ import annotations

import _data_paths
import copy
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx  # noqa: E402
from flight_crew_scheduling.benchmark import KOTLIN_SOLVER_JAR  # noqa: E402
from _compr_isolated import IsolatedJvmFleet, ts  # noqa: E402
from _gerad_phase2_direct import personalization_metrics, _rid  # noqa: E402

SCRATCH = Path(__file__).resolve().parent / "data" / "gerad"
import os  # noqa: E402
PORT = int(os.environ.get("STAGE2_PORT", "9096"))

_JVM_PHASE_RE = re.compile(
    r"(Construction Heuristic|Local Search) phase \(\d+\) ended.*?"
    r"best score \([^)]+\)")


def _poll_solver_progress(port: int, label: str,
                          stop_evt: threading.Event) -> None:
    """Писатель серии схождения этапа 2: раз в 5 с забирает у
    изолированной JVM снимок улучшений (/schedules/progress — события
    самого решателя, не парсинг лога) и дописывает НОВЫЕ события в
    машинный след прогона. Нижней границы у локального поиска нет —
    события несут только инкумбент (hard/soft). Строго best-effort:
    любая ошибка завершает поток и не касается решения."""
    from _opt_progress import ProgressWriter
    writer = ProgressWriter(label, "roster")
    seen = 0
    while not stop_evt.is_set():
        stop_evt.wait(5.0)
        try:
            r = httpx.get(f"http://127.0.0.1:{port}/schedules/progress",
                          timeout=5.0)
            if r.status_code != 200:
                continue
            events = r.json()
        except Exception:                              # noqa: BLE001
            continue
        for ev in events[seen:]:
            writer.emit(incumbent=ev.get("soft"), hard=ev.get("hard"),
                        t=(ev.get("tMillis") or 0) / 1000.0)
        seen = max(seen, len(events))


def _tail_jvm_log(port: int, stop_evt: threading.Event) -> None:
    """Echo Timefold phase-boundary lines from the isolated JVM log to
    stdout WHILE the blocking solve runs, so the evolution live feed can
    show stage-2 progress (heuristic built / local search done + best
    score). The sync solve endpoint exposes no per-step trajectory, so
    these two phase samples are the honest live signal without a Kotlin
    change. Strictly best-effort and additive: any error ends the tail
    and never touches the solve. Runs as a daemon; dies with the process."""
    path = HERE / f"_iso_jvm_{port}.log"
    seen: set[str] = set()
    pos = 0
    deadline = time.monotonic() + 90
    while not path.exists() and time.monotonic() < deadline \
            and not stop_evt.is_set():
        stop_evt.wait(0.5)
    while not stop_evt.is_set():
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(pos)
                for line in f:
                    m = _JVM_PHASE_RE.search(line)
                    if m and m.group(0) not in seen:
                        seen.add(m.group(0))
                        print(m.group(0), flush=True)
                pos = f.tell()
        except OSError:
            pass
        stop_evt.wait(1.0)


def build_synthetic_payload(payload: dict, selected: list[dict]) -> dict:
    """One synthetic flight per selected pairing, crewed by ONE slot PER
    REQUIRED ROLE (C01/C02 phase 2, co-located crew): a pairing whose legs
    each need Captain+FirstOfficer gets a Captain slot AND an FO slot, so the
    GERAD2P solver (requiredSkill hard + overlap hard) seats two distinct,
    skill-matched crew who fly the whole rotation together. Single-crew GERAD
    (uniform one-Captain signature) emits exactly one Captain slot — the
    synthetic payload is then byte-identical to the pre-phase-2 pipeline.

    Covering catalogs (base-anchored space) may overlap: a leg belongs to
    ONE owning pairing (active_fns) and rides as a TDH deadhead in the
    others. Span (busy time) comes from ALL legs; legIds and blockMinutes
    (real flying) from the ACTIVE legs only. Pairings whose every leg is
    owned elsewhere would be pure-deadhead crews — skipped."""
    from _leg_identity import crew_roles
    roles = crew_roles(payload) or [(0, "Captain")]
    single = len(roles) == 1
    # навыки сотрудников — для посадки залоченных на совместимые места;
    # счётчики размещения публикуются вызывающим кодом
    build_synthetic_payload._emp_skills = {
        str(e.get("id")): set(e.get("skills") or [])
        for e in payload.get("employees") or []}
    build_synthetic_payload._locked_placed = 0
    build_synthetic_payload._locked_unplaced = 0
    flights_by_fn = {f["flightNumber"]: f for f in payload["flights"]}
    airports = payload["airports"]
    syn_flights = []
    syn_assignments = []
    skipped = 0
    for i, rec in enumerate(selected):
        fns = rec["fns"]
        active = rec.get("active_fns", fns)
        if not active:
            skipped += 1
            continue
        legs = [flights_by_fn[fn] for fn in fns]
        first, last = legs[0], legs[-1]
        block = 0
        # Точный помесячный налёт (2026-07-24): карта «месяц -> минуты»
        # по фактическим датам участков — пейринг, пересекающий границу
        # месяца, делит налёт между месяцами, а не относится целиком к
        # месяцу своего начала.
        block_by_month: dict = {}
        day_windows: dict = {}
        for fn in active:
            l = flights_by_fn[fn]
            from datetime import datetime
            from _canonical_time import parse_utc
            dep = parse_utc(l["departureUTCDateTime"])
            arr = parse_utc(l["arrivalUTCDateTime"])
            minutes = int((arr - dep).total_seconds() // 60)
            block += minutes
            key = dep.strftime("%Y-%m")
            block_by_month[key] = block_by_month.get(key, 0) + minutes
            # Рабочее время (2026-07-24): дневное окно «первый вылет —
            # последний прилёт» по участкам пейринга, суммируется в месяц
            # дня — зеркалит расчёт сертификата паритета.
            d0, d1 = day_windows.get(dep.date(), (dep, arr))
            day_windows[dep.date()] = (min(d0, dep), max(d1, arr))
        work_by_month: dict = {}
        for day, (d0, d1) in day_windows.items():
            key = day.strftime("%Y-%m")
            work_by_month[key] = work_by_month.get(key, 0) + int(
                (d1 - d0).total_seconds() // 60)
        pid = f"C_{i:06d}"
        # Допуски: цепочка может идти на РАЗНЫХ типах судов — решателю
        # нужны ВСЕ типы её активных участков, иначе проверяется только
        # первый (дефект найден перепроверкой 2026-07-24). Одинаковый
        # тип у всех участков -> список из одного значения, постановка
        # прежняя.
        ac_types = sorted({flights_by_fn[fn].get("aircraftType")
                           for fn in active
                           if flights_by_fn[fn].get("aircraftType")})
        # Станции цепочки (для правила «запрещённые станции»): все
        # аэропорты её участков, а не только концы синтетического рейса.
        station_codes = sorted({
            code for fn in active
            for code in (flights_by_fn[fn].get("departureAirport"),
                         flights_by_fn[fn].get("arrivalAirport"))
            if isinstance(code, str) and code})
        syn_flight = {
            "flightNumber": pid,
            "departureAirport": first["departureAirport"],
            "departureUTCDateTime": first["departureUTCDateTime"],
            "arrivalAirport": last["arrivalAirport"],
            "arrivalUTCDateTime": last["arrivalUTCDateTime"],
            "aircraftType": (ac_types[0] if ac_types
                             else first.get("aircraftType", "A320")),
            "aircraftTypes": ac_types,
            "stationCodes": station_codes,
            "legIds": list(active),
            "blockMinutes": block,
            "blockMinutesByMonth": dict(sorted(block_by_month.items())),
            "workMinutesByMonth": dict(sorted(work_by_month.items())),
        }
        # Ярус 2в (2026-07-24): ТОЧНЫЕ значения из данных источника,
        # если каталог их несёт (кредиты вендора вместо оценок по окнам
        # рейсов; требуемый отдых после юнита; окна занятости для
        # еженедельного отдыха). Каталоги без этих полей — прежний путь.
        for exact in ("blockMinutesByMonth", "workMinutesByMonth"):
            v = rec.get(exact)
            if isinstance(v, dict) and v:
                syn_flight[exact] = {str(k): int(m)
                                     for k, m in sorted(v.items())}
        if isinstance(rec.get("minRestAfterMin"), int) \
                and rec["minRestAfterMin"] > 0:
            syn_flight["minRestAfterMin"] = rec["minRestAfterMin"]
        bw = rec.get("busyWindows")
        if isinstance(bw, list) and bw:
            syn_flight["busyWindows"] = [[int(w[0]), int(w[1])]
                                         for w in bw
                                         if isinstance(w, list)
                                         and len(w) == 2]
        # Требования допусков (2026-07-25): участковые коды юнита —
        # каждому члену экипажа (валидность окнами сотрудника).
        rq = rec.get("requiredCodesAll")
        if isinstance(rq, list) and rq:
            syn_flight["requiredCodesAll"] = [str(c) for c in rq]
        syn_flights.append(syn_flight)
        # Путь Б (2026-07-24): каталог с ПЕР-ЗАПИСНЫМ составом мест
        # («seats» — упорядоченный список ролей юнита, у вендорных нарядов
        # состав свой у каждого). Запись с seats минует глобальную
        # сигнатуру целиком; записи БЕЗ seats идут прежней веткой —
        # существующие каталоги дают байт-идентичную синтетику.
        rec_seats = rec.get("seats")
        if isinstance(rec_seats, list) and rec_seats:
            # Режим «залоченных» пейрингов (2026-07-24): каталожная запись
            # может нести lockedEmployees — уже закреплённых планировщиками
            # людей. Каждый сажается на первое свободное место своей роли
            # и пинится (@PlanningPin): решатель достраивает только дыры.
            locked_pool = list(rec.get("lockedEmployees") or [])
            emp_skills = build_synthetic_payload._emp_skills  # см. ниже
            seat_taken: set = set()
            seat_emp: dict = {}
            for emp_id in locked_pool:
                skills = emp_skills.get(str(emp_id)) or set()
                placed = False
                for idx, skill in enumerate(rec_seats):
                    if idx in seat_taken:
                        continue
                    if str(skill) in skills:
                        seat_taken.add(idx)
                        seat_emp[idx] = str(emp_id)
                        placed = True
                        break
                if not placed:
                    build_synthetic_payload._locked_unplaced += 1
            seat_codes = rec.get("seatCodes") or []
            for idx, skill in enumerate(rec_seats):
                a = {
                    "id": f"{i + 1}:{idx}",
                    "flight": pid,
                    "indexInFlight": idx,
                    "requiredSkill": str(skill),
                }
                # местовые допуски «тип_роль» (2026-07-25)
                if idx < len(seat_codes) and seat_codes[idx]:
                    a["requiredCodes"] = [str(c) for c in seat_codes[idx]]
                if idx in seat_emp:
                    a["employee"] = seat_emp[idx]
                    a["pinned"] = True
                    build_synthetic_payload._locked_placed += 1
                syn_assignments.append(a)
            continue
        for idx, skill in roles:
            # single-crew keeps the historical id `str(i+1)` so the GERAD
            # synthetic payload (and thus the seeded solve) is byte-identical.
            a = {
                "id": str(i + 1) if single else f"{i + 1}:{idx}",
                "flight": pid,
                "indexInFlight": idx,
                "requiredSkill": skill,
            }
            # Disruption repair (2026-07-16): a merged repair catalog carries
            # frozen crew on its untouched pairings — the employee rides along
            # and @PlanningPin stops Timefold from moving it, so the solver
            # re-crews ONLY the rebuilt neighbourhood. Repair operates on the
            # single-crew pipeline, so the frozen employee pins the sole slot;
            # multi-crew catalogs carry no per-role pin and stay cold-start.
            if single and rec.get("pinned") and rec.get("employee"):
                a["employee"] = str(rec["employee"])
                a["pinned"] = True
            syn_assignments.append(a)
    if skipped:
        print(f"  (skipped {skipped} pure-deadhead pairings)", flush=True)
    # Отсев непригодных к назначению (решение владельца 2026-08-12,
    # простой способ): сотрудник с ЯВНЫМ allocable=false решателю не
    # передаётся — поля в домене решателя нет, и без отсева он ставил бы
    # такого человека наравне с годными. Сотрудник без поля (все прежние
    # наборы) проходит как раньше — синтетика байт-идентична. Закреплённые
    # планировщиками (pinned/locked) остаются: «доступен к назначению» —
    # про НОВЫЕ назначения решателем, а не про отмену человеческих решений;
    # выкинуть закреплённого — значит отдать решателю пин на сотрудника,
    # которого нет в наборе.
    pinned_ids = {str(a["employee"]) for a in syn_assignments
                  if a.get("employee")}
    employees = []
    excluded = 0
    for e in payload.get("employees") or []:
        if e.get("allocable") is False and str(e.get("id")) not in pinned_ids:
            excluded += 1
            continue
        employees.append(e)
    build_synthetic_payload._excluded_not_allocable = excluded
    return {
        "airports": airports,
        "employees": employees,
        "flights": syn_flights,
        "flightAssignments": syn_assignments,
    }


def apply_monthly_limit(payload: dict, limit_min) -> int:
    """Месячный предел налёта из документа постановки — персональными
    лимитами сотрудников (решение владельца 2026-08-12).

    Свод задаёт норму по категориям состава, решатель применяет
    персональные `flightTimeLimitMin`; документное значение проставляется
    только сотрудникам БЕЗ персонального лимита в данных — норму
    конкретного человека документ не перетирает. Возвращает, скольким
    проставлено; limit_min None/0 — набор не меняется ни байтом."""
    if not limit_min:
        return 0
    applied = 0
    for e in payload.get("employees") or []:
        if isinstance(e, dict) and not e.get("flightTimeLimitMin"):
            e["flightTimeLimitMin"] = int(limit_min)
            applied += 1
    return applied


def catalog_composition_mode(selected: list) -> tuple[bool, bool]:
    """(composition_mode, mixed?) для каталога: записи с непустым active
    (pure-deadhead записи синтетика пропускает и в критерии не участвуют)
    считаются «с составом», когда несут непустой СПИСОК seats. Все несут ->
    (True, False); никакая -> (False, False); часть -> (False, True) —
    смешанный каталог должен отвергаться до солва (ревью 2026-07-24)."""
    active_recs = [r for r in selected
                   if isinstance(r, dict)
                   and (r.get("active_fns", r.get("fns")) or [])]
    if not active_recs:
        return False, False
    seated = [isinstance(r.get("seats"), list) and bool(r.get("seats"))
              for r in active_recs]
    if all(seated):
        return True, False
    if any(seated):
        return False, True
    return False, False


def _crew_of_pairing(solved: dict) -> dict:
    """pid -> ordered list of (indexInFlight, requiredSkill, employeeId) crew
    slots the solver seated on that synthetic pairing-flight. Single-crew
    GERAD yields one (0, 'Captain', eid) per pairing."""
    from collections import defaultdict
    crew: dict = defaultdict(list)
    for a in solved["flightAssignments"]:
        pid = _rid(a.get("flight"), "flightNumber")
        crew[pid].append((a.get("indexInFlight", 0),
                          a.get("requiredSkill", "Captain"),
                          _rid(a.get("employee"), "id")))
    for pid in crew:
        crew[pid].sort(key=lambda t: (t[0], str(t[1])))
    return crew


def expand_to_flight_level(payload: dict, solved: dict) -> dict:
    """Expand pairing->crew assignments into a flight-level roster JSON. Each
    active leg of a pairing gets ONE assignment PER crew slot (co-located
    phase 2): a Captain+FO pairing emits both a Captain and an FO row per
    leg. Single-crew GERAD emits one Captain row per leg — byte-identical to
    the pre-phase-2 expansion.

    Phase 3: the emitted rows MIRROR THE DEMAND's identity namespace — the
    fn -> leg id map is built from the payload's flightAssignments (the
    coverage demand side), NOT from the flights table. A payload whose
    flights are materialized but whose slots are not must yield UNstamped
    supply rows, or the coverage join diverges and a complete roster reads
    as missing (review 2026-07-18, findings 3/15)."""
    legs_of = {f["flightNumber"]: f.get("legIds") or []
               for f in solved["flights"]}
    crew = _crew_of_pairing(solved)
    leg_id_of: dict = {}
    demand_of: dict = {}      # fn -> ordered demanded skills (payload slots)
    for a in payload.get("flightAssignments") or []:
        if not isinstance(a, dict):
            continue
        ref = a.get("flight")
        ref = ref.get("flightNumber") if isinstance(ref, dict) else ref
        if not ref:
            continue
        if a.get("flightLegId"):
            leg_id_of.setdefault(ref, a["flightLegId"])
        demand_of.setdefault(ref, []).append(
            (a.get("indexInFlight", 0), str(a.get("requiredSkill"))))

    assignments = []
    counter = 1
    for pid, fns in legs_of.items():
        seats = crew.get(pid) or [(0, "Captain", None)]
        for fn in fns:
            demand = sorted(demand_of.get(fn) or [])
            if demand and len(seats) > len(demand):
                # Путь Б (ревью 2026-07-24): состав юнита-пейринга —
                # поролевой максимум по его участкам, поэтому на «лёгком»
                # участке экипаж шире спроса. Ряды выпускаются ПО СПРОСУ
                # участка: каждому требуемому месту (в его порядке) —
                # первый свободный член экипажа той же роли; лишние члены
                # экипажа на этом участке рядов не порождают (едут
                # сверхштатно, это фиксирует запись пейринга). indexInFlight
                # и crewSlotId зеркалят СПРОС — покрытие сходится один в
                # один. Для юнитов, чей экипаж не шире спроса (все прежние
                # режимы: len(seats) == len(demand)), ветка не задействуется
                # — расширение байт-идентично прежнему.
                pool = list(seats)
                rows = []
                for d_idx, d_skill in demand:
                    take = next((s for s in pool if s[1] == d_skill), None)
                    if take is None:
                        continue           # спрос шире экипажа — место пусто
                    pool.remove(take)
                    rows.append((d_idx, d_skill, take[2]))
            else:
                rows = seats
            for idx, skill, eid in rows:
                a = {
                    "id": str(counter),
                    "flight": fn,
                    "indexInFlight": idx,
                    "requiredSkill": skill,
                    "employee": eid,
                }
                if fn in leg_id_of:
                    a["flightLegId"] = leg_id_of[fn]
                    a["crewSlotId"] = f"{leg_id_of[fn]}#{idx}"
                assignments.append(a)
                counter += 1
    return {
        "airports": payload["airports"],
        "employees": payload["employees"],
        "flights": payload["flights"],
        "flightAssignments": assignments,
    }


def _rules_provenance() -> dict:
    """Чем считали: действующие правила, их отпечаток и личность прогона.

    Каталог первого этапа несёт эти поля с 2026-07-26 (P1-16), а журнал
    второго — не нёс, и сверить половины было нечем: цепочки могли быть
    построены по одним нормам, а люди рассажены по другим. Формула
    отпечатка ТА ЖЕ, что у построителя цепочек, иначе сравнение
    невозможно."""
    import hashlib
    try:
        import rules_config
        effective = rules_config.load()
    except Exception:                            # noqa: BLE001
        return {"rules_fingerprint": None, "rules_effective": None,
                "run_id": os.environ.get("CREW_RUN_ID") or None}
    return {
        "rules_fingerprint": hashlib.sha256(
            json.dumps(effective, sort_keys=True).encode()).hexdigest()[:16],
        "rules_effective": effective,
        "run_id": os.environ.get("CREW_RUN_ID") or None,
    }


def _declared_minus_applied(declared_csv, rules_value) -> list:
    """Объявленные ресурсные правила, которых НЕТ среди применённых.

    Сравнение по множеству имён: документ с одним правилом закрывает одно
    правило, а не всю декларацию. Нечитаемое значение считается пустым —
    тогда весь объявленный список честно остаётся неприменённым.

    `rules_value` — то, что лежит в CREW_RESOURCE_RULES. Третий аудит
    2026-07-26 (§5.1): в продуктовом потоке `run_one` кладёт туда
    СЕРИАЛИЗОВАННЫЙ JSON-документ (`os.environ[...] = json.dumps(rr)`), а
    первая версия этой сверки читала значение как ПУТЬ к файлу — и даже
    реально применённый еженедельный отдых объявлялся неприменённым, так
    что корректный прогон не мог получить OK. Мой тест был зелёным, потому
    что передавал функции путь руками — helper и тест были согласованы
    друг с другом, а не с настоящим транспортом. Теперь принимаются оба
    вида (сначала JSON-документ, затем путь), а сквозной тест проходит
    цепочку транспорта, а не helper."""
    declared = {r.strip() for r in (declared_csv or "").split(",")
                if r.strip()}
    if not declared:
        return []
    applied: set = set()
    raw = (rules_value or "").strip() if isinstance(rules_value, str) else ""
    doc = None
    if raw.startswith("{"):
        try:
            doc = json.loads(raw)                 # продуктовый транспорт
        except json.JSONDecodeError:
            doc = None
    elif raw:
        try:
            doc = json.loads(Path(raw).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            doc = None                # битый файл = ничего не применено
    if isinstance(doc, dict):
        applied = {str(k) for k in doc}
    return sorted(declared - applied)


def check_consumer_attestation(att: dict, effective: dict) -> dict:
    """Свидетельство ПОТРЕБИТЕЛЯ: что решатель применил на самом деле.

    Пятый аудит, пакет B. До сих пор доказательством служил отпечаток
    конфигурации — он подтверждает, что значения решателю ПЕРЕДАЛИ.
    Применил ли он их, отпечаток не знает: параметр, который никто не
    читает, даёт ровно такой же отпечаток, как работающий. Разница
    существенна там, где ошибиться дороже всего, — в правиле, которое
    заказчик считает соблюдённым.

    Теперь решатель отвечает за себя сам: он возвращает ФАКТИЧЕСКИЕ
    чтения параметров своими констрейнт-провайдерами. Здесь эти чтения
    сверяются с действующей конфигурацией.

    Возвращает разбор; вызывающий решает, отказывать ли. Три разных
    исхода намеренно РАЗЛИЧАЮТСЯ, потому что означают разное:

    * `not_applied` — профиль заявил параметр применяемым, а не прочитал
      его ни разу. Это и есть «передали, но не применили»;
    * `mismatched` — прочитал ДРУГОЕ значение, чем действующее. Хуже
      первого: правило работает, но не то, которое утвердил заказчик;
    * `extra` — прочитал параметр, которого профиль не заявлял.
      Информационно: контракт профиля отстал от кода.
    """
    def _int_or_none(v):
        """Целое или None. Мусор в ответе решателя не имеет права ронять
        сверку: она стоит ПОСЛЕ многочасового решения и ПЕРЕД публикацией
        артефактов, и исключение здесь потеряло бы весь прогон. Нечитаемое
        значение — это «применено неизвестно что», то есть расхождение, а
        не повод падать."""
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    out = {"ok": True, "not_applied": [], "mismatched": [], "extra": [],
           "profile": att.get("profile"), "applied": {}}
    if not isinstance(att, dict) or att.get("schema") != \
            "cosmos-applied-rules/v1":
        out["ok"] = False
        out["error"] = ("решатель не вернул свидетельство о применённых "
                        "правилах")
        return out
    applied = att.get("applied") if isinstance(att.get("applied"), dict) else {}
    declared = [str(k) for k in (att.get("declared") or [])
                if isinstance(k, (str, int))]
    out["applied"] = dict(applied)
    params = (effective or {}).get("parameters", effective or {})
    if not isinstance(params, dict):
        params = {}
    for key in declared:
        if key not in applied:
            out["not_applied"].append(key)
            continue
        got, want = _int_or_none(applied[key]), _int_or_none(params.get(key))
        if got is None:
            out["mismatched"].append(
                {"key": key, "applied": str(applied[key])[:40],
                 "effective": want})
        elif want is not None and got != want:
            out["mismatched"].append(
                {"key": key, "applied": got, "effective": want})
    out["extra"] = sorted(str(k) for k in set(applied) - set(declared))
    out["ok"] = not out["not_applied"] and not out["mismatched"]
    return out


def check_resource_attestation(att: dict, sent: dict | None) -> dict:
    """То же для ресурсных правил яруса 2в: решатель отдаёт РАЗОБРАННЫЕ
    им числа и счётчик фактических вычислений. Отпечаток файла доказывал
    доставку; здесь доказывается разбор и применение."""
    out = {"ok": True, "declared": [], "not_applied": [], "mismatched": [],
           "evaluations": 0}
    rr = (att or {}).get("resourceRules")
    if not isinstance(rr, dict):
        rr = {}
    try:
        out["evaluations"] = int(rr.get("weeklyRestEvaluations") or 0)
    except (TypeError, ValueError):
        out["evaluations"] = -1          # нечитаемо, но не повод падать
    if not isinstance(sent, dict) or not sent:
        return out                       # ничего не объявляли — нечего ждать
    out["declared"] = sorted(sent)
    got = rr.get("weeklyRest")
    for name in out["declared"]:
        if name != "weeklyRest":
            # решатель знает только weeklyRest; остальное объявлено, но
            # потребителя у него нет — это «не применено», а не «ок»
            out["not_applied"].append(name)
            continue
        if not isinstance(got, dict):
            out["not_applied"].append(name)
            continue
        wanted = sent.get("weeklyRest")
        for field, value in (wanted if isinstance(wanted, dict) else {}).items():
            if field not in got:
                continue
            try:
                same = int(got[field]) == int(value)
                pair = {"key": f"weeklyRest.{field}",
                        "applied": int(got[field]), "sent": int(value)}
            except (TypeError, ValueError):
                same = False
                pair = {"key": f"weeklyRest.{field}",
                        "applied": str(got[field])[:40],
                        "sent": str(value)[:40]}
            if not same:
                out["mismatched"].append(pair)
    out["ok"] = not out["not_applied"] and not out["mismatched"]
    return out


class RulesMismatch(SystemExit):
    """Каталог посчитан под одними правилами, ростер запускают под другими."""


def _assert_same_rules(sel_doc: dict, sel_path) -> None:
    """Громкий отказ ДО старта решателя при расхождении правил.

    Цепочки строятся по одним нормам, люди рассаживаются по другим —
    расхождение тихое, а результат нелегален по построению. Проверять
    можно только каталоги, которые отпечаток несут: прежние выпуски его не
    имеют, и обесценивать уже посчитанные наборы правка не должна.

    Имя поля: построитель пишет `config_sig` — читаем ЕГО. Первая версия
    этой проверки читала `config_signature`, которого в настоящем каталоге
    нет, и потому не срабатывала НИКОГДА; тест был зелёным, потому что
    подставлял выдуманное поле руками (повторный аудит 2026-07-26).
    Контракт закреплён тестом на НАСТОЯЩЕМ выходе построителя. Второе имя
    оставлено на чтение для каталогов, выпущенных за день жизни дефекта."""
    if not isinstance(sel_doc, dict):
        return
    sig = sel_doc.get("config_sig") or sel_doc.get("config_signature") or {}
    if not isinstance(sig, dict):
        return
    catalog_fp = sig.get("rules_fingerprint")
    if not catalog_fp:
        return                                   # каталог прежнего выпуска
    ours = _rules_provenance().get("rules_fingerprint")
    if ours and ours != catalog_fp:
        raise RulesMismatch(
            f"ОТКАЗ: каталог {sel_path.name} посчитан по правилам с "
            f"отпечатком {catalog_fp}, а этап 2 запускается с отпечатком "
            f"{ours}. Цепочки и рассадка разошлись бы по нормам молча. "
            f"Пересоберите каталог под действующими правилами или "
            f"запустите этап 2 под правилами каталога.")


def _assert_relaxed_authorization(auth: dict, cov: dict,
                                  sel_path) -> None:
    """Свидетельство утверждения ослабленного каталога владельцем.

    Принимается ТОЛЬКО полное свидетельство: путь и SHA-256
    утверждённого пакета авторизации, кем/когда утверждено и объём
    согласия (число непокрытых). Файл пакета обязан существовать и
    совпадать с отпечатком байт-в-байт — свидетельство едет вместе с
    публикацией, задним числом его не подделать и не потерять."""
    import hashlib as _hl
    from pathlib import Path as _P
    need = ("package_path", "package_sha256", "approved_by",
            "approved_at", "scope_uncovered")
    missing = [k for k in need if not auth.get(k)]
    if missing:
        raise RulesMismatch(
            f"ОТКАЗ: каталог {sel_path.name} несёт вердикт "
            f"RELAXED_COVER со свидетельством утверждения без полей "
            f"{', '.join(missing)} — неполное свидетельство не "
            f"принимается.")
    if int(auth["scope_uncovered"]) != int(cov.get("uncovered") or -1):
        raise RulesMismatch(
            f"ОТКАЗ: свидетельство утверждения каталога "
            f"{sel_path.name} выдано на {auth['scope_uncovered']} "
            f"непокрытых, а каталог несёт {cov.get('uncovered')} — "
            f"объём согласия не совпадает с фактом.")
    pkg = _P(str(auth["package_path"]))
    if not pkg.is_absolute():
        pkg = _P(__file__).resolve().parent / pkg
    if not pkg.is_file():
        raise RulesMismatch(
            f"ОТКАЗ: пакет утверждения {pkg} не найден — публикация "
            f"без предъявимого свидетельства не принимается.")
    fact = _hl.sha256(pkg.read_bytes()).hexdigest()
    if fact != str(auth["package_sha256"]).lower():
        raise RulesMismatch(
            f"ОТКАЗ: пакет утверждения {pkg.name} изменён после "
            f"публикации каталога (отпечаток {fact[:12]} против "
            f"{str(auth['package_sha256'])[:12]} в свидетельстве).")
    print("ВНИМАНИЕ: каталог %s несёт вердикт RELAXED_COVER, "
          "опубликован со свидетельством утверждения владельцем "
          "(%s, %s; пакет %s @ %s; непокрытых %s — поимённо в пакете) "
          "— ростер будет считаться без них."
          % (sel_path.name, auth["approved_by"], auth["approved_at"],
             pkg.name, fact[:12], auth["scope_uncovered"]),
          flush=True)


def _assert_publishable(sel_doc: dict, sel_path) -> None:
    """Отказ на каталоге, который построитель публиковать отказался.

    Вердикт покрытия лежит ВНУТРИ каталога, поэтому переименование файла
    `.pending.json` в чистое имя гейт не обходит: 2026-07-28 ровно так —
    переименованием — на этап 2 уехал каталог с восемью достижимыми, но
    не построенными участками, и этап 2 молча его принял. Осознанный
    запуск на неполном каталоге возможен, но только вслух: переменная
    окружения CREW_ACCEPT_INCOMPLETE_CATALOG=1, и факт остаётся в
    вердикте самого каталога.
    """
    if not isinstance(sel_doc, dict):
        return
    cov = sel_doc.get("coverage")
    if not isinstance(cov, dict):
        return                                   # каталог прежнего выпуска
    verdict = cov.get("verdict")
    if verdict in ("OK", "INFEASIBLE_INPUT"):
        return
    if verdict != "INCOMPLETE_SEARCH":
        # RELAXED_COVER (ослабленный выбор или урезанные цепочки, внешний
        # аудит R6, P0-05) и любой неизвестный вердикт — отказ БЕЗ обхода
        # переменными окружения: согласие на неполноту перебора не
        # является согласием на изменённые цепочки. ЕДИНСТВЕННЫЙ законный
        # путь для RELAXED_COVER — публикация с машинным свидетельством
        # утверждения владельцем (блок authorization с отпечатком
        # утверждённого пакета; пишет tools/publish_authorized_catalog.py
        # после личного слова владельца).
        if verdict == "RELAXED_COVER" \
                and isinstance(sel_doc.get("authorization"), dict):
            _assert_relaxed_authorization(
                sel_doc["authorization"], cov, sel_path)
            return
        raise RulesMismatch(
            f"ОТКАЗ: каталог {sel_path.name} несёт вердикт "
            f"{verdict!r} — такие каталоги на этап 2 не принимаются "
            f"(ослабленное покрытие или урезанные цепочки; пересоберите "
            f"каталог точным покрытием либо опубликуйте со "
            f"свидетельством утверждения).")
    sig = sel_doc.get("config_sig")
    if isinstance(sig, dict) and sig.get("accept_incomplete_search"):
        # согласие дано вслух при выпуске (--accept-incomplete-search) и
        # приехало в отпечатке самого каталога — второго согласия не нужно
        print("ВНИМАНИЕ: каталог %s несёт вердикт INCOMPLETE_SEARCH, "
              "опубликован явным ключом при выпуске (%s участков без "
              "доказательства невозможности) — ростер будет "
              "считаться без них." % (sel_path.name, cov.get("not_built")),
              flush=True)
        return
    if os.environ.get("CREW_ACCEPT_INCOMPLETE_CATALOG") == "1":
        print("ВНИМАНИЕ: каталог %s с вердиктом INCOMPLETE_SEARCH принят "
              "по CREW_ACCEPT_INCOMPLETE_CATALOG=1 — %s участков без "
              "доказательства невозможности, ростер будет считаться "
              "без них." % (sel_path.name, cov.get("not_built")),
              flush=True)
        return
    raise RulesMismatch(
        f"ОТКАЗ: каталог {sel_path.name} несёт вердикт INCOMPLETE_SEARCH "
        f"({cov.get('not_built')} участков без доказательства "
        f"невозможности: поиск не завершён или вариант не найден) — "
        f"построитель его не публиковал. "
        f"Достройте каталог или запустите с "
        f"CREW_ACCEPT_INCOMPLETE_CATALOG=1, если неполнота осознанна.")


def allowed_missing_from_catalog(sel_doc: dict, payload: dict,
                                 wire_map: dict) -> tuple[set, list]:
    """Какие недостающие участки гейт вправе простить (аудит A4,
    04.09.2026): ТОЛЬКО доказанные — запись proven_impossible_proofs с
    известным видом доказательства (graph_unreachable / escalation_proved)
    и отпечатком, равным отпечатку данных ЭТОГО payload'а. Голое имя в
    proven_impossible_legs (каталог прежнего выпуска, линия без
    доказательств) или доказательство с других данных не прощается —
    причина словами во втором элементе возврата.

    Класс ошибки A4: прежде прощение шло по списку имён, который
    построитель наполнял отрицательным ответом эвристического зонда, — и
    неполный перебор превращался в публикуемый INFEASIBLE_INPUT_OR_MODEL.

    Возврат: (множество leg_id исходного пространства, отклонённые)."""
    from _coverage_validator import proven_exclusions
    from _leg_rekey import wire_to_leg_ids
    pe = proven_exclusions(sel_doc if isinstance(sel_doc, dict) else {},
                           payload)
    return wire_to_leg_ids(pe["allowed"], wire_map or {}), pe["rejected"]


def gate_result_status(stage2_score, unassigned: int, cov: dict,
                       allowed_missing: set,
                       trajectory_violations: int | None = None,
                       composition_violations: int | None = None,
                       rules_not_applied: list | None = None) -> str:
    """Promotion gate (audit 2026-07-12, rounds 5-6). Three outcomes:

      OK — hard = 0, unassigned = 0, coverage complete (no missing,
        duplicate or unknown legs), the full-trajectory validator is
        clean AND every ENABLED crew-composition rule is satisfied
        (audit 2026-07-19 P0-02: a warning was incompatible with the
        semantics of OK; None = rules disabled / not applicable).
        Only this is a publishable roster.

      INFEASIBLE_INPUT_OR_MODEL — the assigned part is clean and the ONLY
        defect is missing legs each individually PROVEN impossible in the
        aligned space. Round-6 audit: proven impossibility means the input
        or the chosen space is incompatible with the rules — that is a
        property of the problem, NEVER a valid full roster, so it must not
        be OK.

      INFEASIBLE_INCUMBENT — anything else (hard violations, unassigned
        pairings, unexplained missing legs, duplicates, unknown legs,
        trajectory violations, unparseable score). Diagnostics, not a
        result.
    """
    # Правило, объявленное в Пакете правил, но не дошедшее до движка,
    # делает «ноль жёстких нарушений» утверждением о другой задаче
    # (аудит 2026-07-26, P0-04): решатель о правиле не знал.
    if rules_not_applied:
        return "INFEASIBLE_INCUMBENT"
    try:
        hard_part = int(str(stage2_score).split("hard")[0])
    except (ValueError, AttributeError):
        hard_part = -1
    missing = set(cov["missing_legs"])
    clean_assigned = (
        hard_part == 0 and unassigned == 0
        and cov["duplicates"] == 0 and cov["unknown_flights"] == 0
        and (trajectory_violations is None or trajectory_violations == 0)
        and (composition_violations is None or composition_violations == 0))
    if not clean_assigned:
        return "INFEASIBLE_INCUMBENT"
    if not missing:
        return "OK"
    if missing <= allowed_missing:
        return "INFEASIBLE_INPUT_OR_MODEL"
    return "INFEASIBLE_INCUMBENT"


def parse_args(argv: list[str]) -> dict:
    """Two CLI modes, one parser.

    numeric (100% backward compatible, positional as always):
        [inst_from] [inst_to] [budget_s] [seed]
    generic (uploaded instances):
        --payload <path> --catalog <path> --label <slug> [budget_s] [seed]

    Returns {"mode": "numeric", lo, hi, budget_s, seed} or
    {"mode": "generic", payload_path, catalog_path, label, budget_s, seed}.
    Loud failures: partial generic flags, non-slug labels and labels that
    would clobber the numeric instN artifacts are argparse errors (exit 2).
    """
    import argparse
    ap = argparse.ArgumentParser(
        prog="_gerad_stage2_roster.py",
        description=("Stage-2 rostering over a CG-selected pairing catalog "
                     "with the promotion gate (coverage + trajectory "
                     "certificate + 3-verdict gate)."),
        epilog=("numeric mode: %(prog)s [inst_from] [inst_to] [budget_s] "
                "[seed]  |  generic mode: %(prog)s --payload P.json "
                "--catalog C.json --label my-slug [budget_s] [seed]"))
    ap.add_argument("pos", nargs="*", type=int, metavar="N",
                    help=("numeric mode: [inst_from] [inst_to] [budget_s] "
                          "[seed]; generic mode: [budget_s] [seed]"))
    ap.add_argument("--payload", type=Path, metavar="PATH",
                    help="generic mode: canonical payload JSON")
    ap.add_argument("--catalog", type=Path, metavar="PATH",
                    help=("generic mode: selected-pairings dump "
                          "(gerad_colgen_selected_*.json / best catalog)"))
    ap.add_argument("--catalog-fp", metavar="SHA", default=None,
                    help=("sha256 СОДЕРЖИМОГО каталога этапа 1 (аудит R10, "
                          "отсрочка №3): каталог ехал голым путём, и "
                          "подмена файла между постановкой задачи и чтением "
                          "была невидима; расхождение — отказ"))
    ap.add_argument("--label", metavar="SLUG",
                    help="generic mode: artifact name slug [a-z0-9-]+")
    ap.add_argument("--locked", type=Path, metavar="PATH", default=None,
                    help=("generic mode: закреплённые назначения "
                          "{units: [{sourceUnit, employees: [...]}]} — "
                          "пинятся, решатель достраивает только дыры"))
    ap.add_argument("--resource-rules", type=Path, metavar="PATH",
                    default=None,
                    help=("generic mode: ресурсные правила (Ярус 2в) для "
                          "решателя — JSON с блоком weeklyRest; границы "
                          "горизонта дополняются из payload"))
    ap.add_argument("--source-rules", type=Path, metavar="PATH",
                    default=None,
                    help=("настройки системы-источника: из них сертификат "
                          "маршрута берёт пары равнозначных станций. Без "
                          "ключа переезд между разными аэропортами "
                          "считается разрывом маршрута"))
    ap.add_argument("--resource-rules-fp", metavar="SHA", default=None,
                    help=("отпечаток содержимого файла ресурсных правил, "
                          "снятый в момент СТАРТА задачи: расхождение с "
                          "прочитанным = файл подменён между стартом и "
                          "чтением — отказ (R4 §4.2)"))
    ap.add_argument("--source-rules-fp", metavar="SHA", default=None,
                    help=("отпечаток файла правил источника (внешний аудит "
                          "R8, P0-01): этап 2 обязан судить по ТЕМ ЖЕ "
                          "байтам, что этап 1; расхождение — отказ"))
    ap.add_argument("--source-rules-strict", action="store_true",
                    help=("отсутствующий stationEquivalenceRules — отказ, "
                          "а не зашитые пары умолчания"))
    a = ap.parse_args(argv)
    flags = (a.payload, a.catalog, a.label)
    if any(x is not None for x in flags):
        if any(x is None for x in flags):
            ap.error("generic mode requires ALL of --payload, --catalog "
                     "and --label")
        if not re.fullmatch(r"[a-z0-9-]+", a.label):
            ap.error(f"--label must match [a-z0-9-]+, got '{a.label}'")
        if re.fullmatch(r"inst[1-7]", a.label):
            ap.error("--label 'inst1'..'inst7' is reserved for the numeric "
                     "GERAD artifacts — pick a different slug")
        if len(a.pos) > 2:
            ap.error("generic mode takes at most [budget_s] [seed] "
                     "positional arguments")
        if a.resource_rules is not None and not a.resource_rules_fp:
            # R5-03: отпечаток при файле ОБЯЗАТЕЛЕН — необязательный страж
            # отключается любым ручным вызовом, и подмена файла снова тиха.
            ap.error("--resource-rules требует --resource-rules-fp: файл "
                     "без отпечатка не принимается")
        if a.source_rules is not None and not a.source_rules_fp:
            # Внешний аудит R8 (P0-01): тот же принцип для правил
            # источника — путь без отпечатка допускал бы подмену между
            # стартом задачи и чтением.
            ap.error("--source-rules требует --source-rules-fp: файл "
                     "без отпечатка не принимается")
        if not a.catalog_fp:
            # Аудит R10 (отсрочка №3): каталог — часть постановки, и без
            # контент-отпечатка его подмена невидима; тот же принцип, что
            # у правил (отпечаток обязателен, атомарно с путём).
            ap.error("--catalog требует --catalog-fp (sha256 содержимого "
                     "каталога): файл без отпечатка не принимается")
        return {"mode": "generic", "payload_path": a.payload,
                "catalog_path": a.catalog, "label": a.label,
                "catalog_fp": a.catalog_fp,
                "locked_path": a.locked,
                "resource_rules_path": a.resource_rules,
                "resource_rules_fp": a.resource_rules_fp,
                "source_rules_path": a.source_rules,
                "source_rules_fp": a.source_rules_fp,
                "source_rules_strict": a.source_rules_strict,
                "budget_s": a.pos[0] if len(a.pos) > 0 else 600,
                "seed": a.pos[1] if len(a.pos) > 1 else 7}
    if len(a.pos) > 4:
        ap.error("numeric mode takes at most [inst_from] [inst_to] "
                 "[budget_s] [seed]")
    if a.source_rules is not None and not a.source_rules_fp:
        ap.error("--source-rules требует --source-rules-fp: файл "
                 "без отпечатка не принимается")
    return {"mode": "numeric",
            "lo": a.pos[0] if len(a.pos) > 0 else 1,
            "hi": a.pos[1] if len(a.pos) > 1 else 7,
            "source_rules_path": a.source_rules,
            "source_rules_fp": a.source_rules_fp,
            "source_rules_strict": a.source_rules_strict,
            "budget_s": a.pos[2] if len(a.pos) > 2 else 600,
            "seed": a.pos[3] if len(a.pos) > 3 else 7}


def artifact_paths(label: str) -> dict[str, Path]:
    """Canonical artifact locations for one stage-2 run, named by label.

    Numeric runs use label = 'instN', so the historical artifact names are
    unchanged; generic runs get gerad_stage2_assignment_<label>.json and
    p2_two_stage_solution_<label>.json."""
    return {
        "solution": _data_paths.gerad_data_dir() / f"p2_two_stage_solution_{label}.json",
        "assignment": (_data_paths.results_dir()
                       / f"gerad_stage2_assignment_{label}.json"),
    }


def journal_key(row: dict) -> tuple:
    """Journal merge key, label-or-inst: generic rows carry
    {"label": slug, "inst": null}; numeric rows keep "inst" and have no
    label. The two namespaces never collide (instN labels are rejected in
    generic mode)."""
    label = row.get("label")
    if label is not None:
        return ("label", label)
    return ("inst", row.get("inst"))


def merge_journal(prior, row: dict) -> list:
    """Replace the prior row with the same label-or-inst key, append ours."""
    if not isinstance(prior, list):
        prior = []
    return [x for x in prior
            if not (isinstance(x, dict) and journal_key(x) == journal_key(row))
            ] + [row]


def journal_write(results_path: Path, row: dict) -> None:
    """Read -> merge -> replace общего журнала под МЕЖПРОЦЕССНЫМ локом.

    Аудит 2026-07-19 (P1-08): три писателя (stage2 ×2 сайта, multirole
    lane) делали read->merge->replace без блокировки, при том что jobs
    разрешает параллельные прогоны разных экземпляров: строки терялись
    (lost update — чтение до чужой записи), а tmp.replace падал
    PermissionError под чужим коротким read-хэндлом Windows УЖЕ ПОСЛЕ
    публикации артефактов вердикта. Лок-файл (O_CREAT|O_EXCL)
    сериализует писателей; чтение prior происходит ВНУТРИ лока; replace
    ретраится, пережидая читателей; сталый лок упавшего писателя
    (старше 120 с) взламывается."""
    lock = results_path.with_suffix(results_path.suffix + ".lock")
    # Дедлайн ожидания ОБЯЗАН превышать порог взлома сталого лока (120 с):
    # иначе после краха держателя все конкуренты успевали отвалиться по
    # таймауту раньше, чем лок будет взломан (ревью пакета 2026-07-19).
    deadline = time.time() + 180.0
    fd = None
    while fd is None:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):
            # PermissionError: Windows отдаёт ACCESS_DENIED, когда лок в
            # состоянии pending-delete (unlink при ещё открытом чужом
            # хэндле) — это «занято, повторить», не фатальная ошибка.
            try:
                if time.time() - lock.stat().st_mtime > 120.0:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.time() > deadline:
                raise TimeoutError(
                    f"журнал занят дольше 180 с: {lock} — писатель завис?")
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        try:
            prior = json.loads(results_path.read_text(encoding="utf-8"))
        except Exception:
            prior = []
        merged = merge_journal(prior, row)
        tmp = results_path.with_suffix(f".tmp-{os.getpid()}")
        tmp.write_text(json.dumps(merged, indent=1))
        for attempt in range(20):
            try:
                tmp.replace(results_path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.1)
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def run_one(label: str, payload_path: Path, sel_path: Path,
            budget: int, seed: int, inst: int | None,
            results_path: Path,
            locked_path: Path | None = None,
            resource_rules_path: Path | None = None,
            resource_rules_fp: str | None = None,
            source_rules_path: Path | None = None,
            source_rules_fp: str | None = None,
            source_rules_strict: bool = False,
            catalog_fp: str | None = None) -> str:
    """Solve + expand + gate ONE instance; returns the gate result_status.

    inst is the GERAD instance number for numeric runs (enables the
    direct-uniform rescoring block and keeps journal rows keyed by "inst");
    None for generic runs (journal rows carry "label", direct rescoring is
    skipped — there is no p2_direct_solution for uploads)."""
    # Каталог читается ОДИН раз байтами; контент-отпечаток (когда передан)
    # сверяется с ТЕМИ ЖЕ байтами, что идут в разбор (аудит R10, отсрочка
    # №3): подмена файла между постановкой задачи и чтением — отказ.
    _sel_raw = sel_path.read_bytes()
    if catalog_fp:
        import hashlib as _hl0
        _got = _hl0.sha256(_sel_raw).hexdigest()
        if _got != catalog_fp:
            raise RulesMismatch(
                f"ОТКАЗ: каталог {sel_path.name} не совпал с "
                f"контент-отпечатком постановки ({_got[:16]} != "
                f"{catalog_fp[:16]}) — файл подменён между постановкой "
                f"задачи и чтением.")
    sel_doc = json.loads(_sel_raw.decode("utf-8"))
    _assert_same_rules(sel_doc, sel_path)
    _assert_publishable(sel_doc, sel_path)
    # Правила источника — ТЕ ЖЕ БАЙТЫ на всех этапах (внешний аудит R8,
    # P0-01): отпечаток переданного файла сверяется с прочитанным, а если
    # каталог этапа 1 несёт свой отпечаток — то и с ним. Расхождение или
    # каталог с правилами при этапе 2 без правил — отказ: половины одного
    # прогона судили бы по разным постановкам.
    import hashlib as _hl
    cat_sig = sel_doc.get("config_sig") or {}
    cat_fp = cat_sig.get("source_rules_fp")
    if source_rules_path is not None:
        _sr_raw = Path(source_rules_path).read_bytes()
        got = _hl.sha256(_sr_raw).hexdigest()
        if source_rules_fp and got != source_rules_fp:
            raise RulesMismatch(
                f"ОТКАЗ: файл правил источника {Path(source_rules_path).name} "
                f"не совпал с отпечатком команды ({got[:16]} != "
                f"{source_rules_fp[:16]}) — файл подменён между стартом "
                f"задачи и чтением.")
        if cat_fp and got != cat_fp:
            raise RulesMismatch(
                f"ОТКАЗ: правила источника этапа 2 ({got[:16]}) не те, по "
                f"которым построен каталог ({cat_fp[:16]}) — рассадка и "
                f"сертификат разошлись бы с постановкой цепочек.")
    elif cat_fp:
        raise RulesMismatch(
            "ОТКАЗ: каталог построен по правилам источника "
            f"({cat_fp[:16]}), а этап 2 запущен без --source-rules — "
            "сертификат судил бы по другой модели. Передайте тот же файл.")
    selected = sel_doc["selected"]
    payload_orig = json.loads(payload_path.read_text(encoding="utf-8"))
    # Месячный предел налёта из документа постановки (решение владельца
    # 2026-08-12): значение читается из ТЕХ ЖЕ байт, что сверены
    # отпечатком выше, и проставляется персональными лимитами ДО
    # перекеивания и синтетики. Документ без ключа — набор не меняется.
    monthly_limit_min = None
    monthly_limit_applied = 0
    _doc_rules = None
    if source_rules_path is not None:
        from _cos_rules import load_bytes as _load_src_rules
        _doc_rules = _load_src_rules(_sr_raw, where=str(source_rules_path))
        monthly_limit_min = _doc_rules.monthly_flight_time_limit_min
        monthly_limit_applied = apply_monthly_limit(
            payload_orig, monthly_limit_min)
        if monthly_limit_applied:
            print(f"  месячный предел налёта из документа: "
                  f"{monthly_limit_min} мин ({monthly_limit_min / 60:.0f} ч)"
                  f" проставлен {monthly_limit_applied} сотрудникам",
                  flush=True)
    # Режим «залоченных» (2026-07-24): закреплённые назначения ложатся
    # на записи каталога по sourceUnit — синтетика их запинит.
    if locked_path is not None:
        locked_doc = json.loads(locked_path.read_text(encoding="utf-8"))
        by_unit = {u["sourceUnit"]: list(u.get("employees") or [])
                   for u in locked_doc.get("units") or []}
        matched = 0
        for rec in selected:
            emps = by_unit.get(rec.get("sourceUnit"))
            if emps:
                rec["lockedEmployees"] = emps
                matched += 1
        print(f"  locked: юнитов с закреплениями {matched} "
              f"из {len(by_unit)} в файле", flush=True)
    # C01/C02 phase 1 — the fail-closed gate: a payload the flat single-crew
    # pipeline cannot faithfully represent (multi-seat/multi-role legs, or a
    # repeated leg identity that would collapse) must NEVER be gated OK. Record
    # the verdict and return WITHOUT solving; the missing assignment artifact
    # makes the job fail honestly (jobs.py) rather than publish a false OK.
    from _leg_identity import assert_representable, GeradPipelineUnsupported
    from _leg_rekey import (rekey_or_passthrough, restore_solution,
                            wire_to_leg_ids)

    def _refuse(status: str, reasons: list) -> str:
        print(f"  !! {status}: {reasons}", flush=True)
        row = {"inst": inst,
               "legs": len(payload_orig.get("flights") or []),
               "result_status": status, "unsupported_reasons": reasons,
               "seed": seed, "budget_s": budget}
        if inst is None:
            row["label"] = label
        journal_write(results_path, row)
        print(f"  saved -> {results_path.name} [{status}]", flush=True)
        return status

    # Путь Б: каталог, несущий состав мест на КАЖДОЙ записи, снимает
    # требование глобальной однородной сигнатуры (C01) — состав приходит
    # из самого каталога. Критерий строгий (ревью 2026-07-24, P2):
    # смешанный каталог (часть записей с seats, часть без) — честный
    # отказ ДО солва, а не сжигание бюджета над фолбэком '(0, Captain)'.
    # C02 (коллизии идентичности участков) остаётся безусловно фатальным.
    composition_catalog, mixed_catalog = catalog_composition_mode(selected)
    if mixed_catalog:
        return _refuse(
            "INFEASIBLE_INPUT_UNSUPPORTED",
            ["каталог смешанный: часть записей несёт валидный список "
             "seats, часть — нет; пересоберите каталог целиком"])
    try:
        assert_representable(payload_orig, "stage2.run_one",
                             allow_heterogeneous_seats=composition_catalog)
    except GeradPipelineUnsupported as exc:
        return _refuse("INFEASIBLE_INPUT_UNSUPPORTED", exc.reasons)
    # Пакет B: wire-пространство для солвера/каталога (повторные номера ->
    # F100@1/F100@2, детерминированная функция payload'а). `payload` дальше
    # по функции = WIRE; публикуемое решение восстанавливается в исходное
    # пространство перед записью. Каталог обязан быть от ТОГО ЖЕ payload'а:
    # штамп wire_map сверяется с пересчитанным (stale-каталог = отказ).
    payload, wire_map = rekey_or_passthrough(payload_orig)
    cat_wm = sel_doc.get("wire_map") or {}
    if wire_map and cat_wm != wire_map:
        return _refuse(
            "INFEASIBLE_INPUT_UNSUPPORTED",
            ["каталог stage-1 не от этого payload'а (wire_map расходится "
             "или отсутствует) — перегоните stage-1"])
    syn = build_synthetic_payload(payload, selected)
    locked_placed = getattr(build_synthetic_payload, "_locked_placed", 0)
    locked_unplaced = getattr(build_synthetic_payload,
                              "_locked_unplaced", 0)
    if locked_placed or locked_unplaced:
        print(f"  locked: закреплено мест {locked_placed}, "
              f"не размещено {locked_unplaced}", flush=True)
    excluded_na = getattr(build_synthetic_payload,
                          "_excluded_not_allocable", 0)
    if excluded_na:
        print(f"  исключено из назначения: {excluded_na} "
              f"(непригодны, allocable=false)", flush=True)
    # Ярус 2в: ресурсные правила уезжают решателю через окружение
    # изолированной JVM; границы горизонта дополняются из payload.
    if resource_rules_path is not None:
        _rr_body = resource_rules_path.read_text(encoding="utf-8")
        if resource_rules_fp:
            import hashlib as _hl
            _got = _hl.sha256(_rr_body.encode()).hexdigest()
            # сравнение по префиксу заявленной длины: полный sha256 у
            # продуктового запуска, допускаем и исторический короткий
            if _got[:len(resource_rules_fp)] != resource_rules_fp:
                # Файл подменён между стартом задачи и чтением (R4 §4.2):
                # содержимое не то, под которое задача стартовала.
                raise SystemExit(
                    f"ОТКАЗ: ресурсные правила изменились после старта "
                    f"задачи (отпечаток {_got}, ожидался "
                    f"{resource_rules_fp}) — прогон отклонён")
        rr = json.loads(_rr_body)
        wr = rr.get("weeklyRest")
        if isinstance(wr, dict):
            from datetime import datetime, timezone as _tz
            from _canonical_time import parse_utc
            deps = [parse_utc(f["departureUTCDateTime"])
                    for f in payload["flights"]]
            arrs = [parse_utc(f["arrivalUTCDateTime"])
                    for f in payload["flights"]]
            epoch = datetime(1970, 1, 1)
            wr.setdefault("horizonFromEpochMin", int(
                (min(deps) - epoch).total_seconds() // 60))
            wr.setdefault("horizonToEpochMin", int(
                (max(arrs) - epoch).total_seconds() // 60))
        os.environ["CREW_RESOURCE_RULES"] = json.dumps(
            rr, ensure_ascii=False)
        print(f"  resource-rules: {json.dumps(rr)[:120]}", flush=True)
    print(f"=== phase2 two-stage STAGE-2 (GERAD2P): {label} "
          f"({len(payload['flights'])} legs -> {len(syn['flights'])} "
          f"pairings), LA seed {seed}, {budget}s ===", flush=True)

    t0 = time.monotonic()
    # Свидетельство потребителя снимается внутри окна изолированной JVM;
    # объявляем здесь, чтобы разбор ниже не зависел от того, каким путём
    # выполнение дошло до гейта.
    attestation: dict = {}
    # Live stage-2 progress for the evolution feed: tail the isolated JVM
    # log for Timefold phase-boundary best scores while the solve blocks.
    _tail_stop = threading.Event()
    threading.Thread(target=_tail_jvm_log, args=(PORT, _tail_stop),
                     daemon=True).start()
    # Серия схождения для графика: события улучшений самого решателя
    # (/schedules/progress новой сборки JVM). Поток best-effort: у старой
    # сборки без точки прогресса он молчит, не трогая ни решение, ни хвост.
    threading.Thread(target=_poll_solver_progress,
                     args=(PORT, label, _tail_stop), daemon=True).start()
    with IsolatedJvmFleet([PORT], KOTLIN_SOLVER_JAR, heap_xmx="8g") as fl:
        with httpx.Client(timeout=budget * 2 + 120.0) as cli:
            r = cli.post(f"http://127.0.0.1:{PORT}/schedules/single",
                         json=copy.deepcopy(syn),
                         params={"acceptor": "LA",
                                 "spentLimitSeconds": budget,
                                 "randomSeed": seed,
                                 "rules": "GERAD2P"})
            r.raise_for_status()
            _tail_stop.set()          # solve done — stop the log tail
            solved = r.json()
            stage2_score = solved.get("score")

            # Свидетельство ПОТРЕБИТЕЛЯ (пятый аудит, пакет B) снимается
            # СРАЗУ после решения и ДО оценок другими профилями: иначе в
            # записи чтений смешаются параметры двух профилей, и «что
            # применил GERAD2P» станет неотличимо от «что прочитал
            # GERAD2 при пересчёте на общей шкале».
            try:
                ra = cli.get(
                    f"http://127.0.0.1:{PORT}/schedules/applied-rules",
                    params={"rules": "GERAD2P"})
                attestation = ra.json() if ra.status_code == 200 else {}
            except Exception as exc:                      # noqa: BLE001
                attestation = {"error": str(exc)}

            expanded = expand_to_flight_level(payload, solved)
            m = personalization_metrics(expanded)
            # Uniform scale: score the expanded roster under GERAD2.
            # ВТОРИЧНАЯ метрика не имеет права ронять первичный результат
            # (2026-08-12): таймаут этой оценки уронил прогон с идеальным
            # решением ДО записи артефактов — решение погибло вместе с
            # процессом. Ошибка оценки — честная строка в отчёте, а не
            # исключение.
            try:
                r2 = cli.post(f"http://127.0.0.1:{PORT}/schedules/score",
                              json=expanded, params={"rules": "GERAD2"})
                uniform = (r2.text if r2.status_code == 200
                           else f"HTTP {r2.status_code}")
            except Exception as exc:                       # noqa: BLE001
                uniform = f"оценка не снята: {type(exc).__name__}"
            # Same-scale number for the «прямая» roster, if we have it.
            # Generic runs have no p2_direct_solution artifact -> None.
            direct_uniform = None
            if inst is not None:
                dsol = _data_paths.gerad_data_dir() / f"p2_direct_solution_inst{inst}.json"
                if dsol.exists():
                    try:
                        r3 = cli.post(
                            f"http://127.0.0.1:{PORT}/schedules/score",
                            json=json.loads(dsol.read_text("utf-8")),
                            params={"rules": "GERAD2"})
                        direct_uniform = (r3.text if r3.status_code == 200
                                          else f"HTTP {r3.status_code}")
                    except Exception as exc:               # noqa: BLE001
                        direct_uniform = (f"оценка не снята: "
                                          f"{type(exc).__name__}")
    wall = time.monotonic() - t0
    # Promotion pattern (audit 2026-07-12, round 6): artifacts are
    # written as *.pending.*, validated (coverage + full trajectory
    # certificate), stamped with the gate verdict, and only then
    # published. INFEASIBLE_INCUMBENT additionally lands in
    # runs/stage2-diagnostics/. A published artifact is a MEASUREMENT
    # RECORD carrying result_status — only result_status == "OK" rows
    # are results.
    paths = artifact_paths(label)
    sol_final = paths["solution"]
    sol_pending = sol_final.with_suffix(".pending.json")
    # Аудит 2026-07-19 (P1-09): происхождение артефакта доказывает run_id,
    # проштампованный самим писателем (CREW_RUN_ID от jobs), а не время
    # файла. Ручные CLI-прогоны без env не меняют байт-форму артефактов.
    # Пакет B: публикуемое решение — в ИСХОДНОМ пространстве номеров
    # (restore_solution); wire-имена остаются внутренней кухней солвера.
    expanded_pub = restore_solution(expanded, wire_map)
    _run_id = os.environ.get("CREW_RUN_ID")
    if _run_id and isinstance(expanded_pub, dict):
        expanded_pub["run_id"] = _run_id
    sol_pending.write_text(json.dumps(expanded_pub), encoding="utf-8")
    # Persist the SOLVED pairing->crew assignment with the full leg list and
    # the active/TDH split (independently verifiable). Phase 2: a pairing may
    # carry N crew (one per role); `crew` lists them all, while the scalar
    # `employee` (first/sole slot) stays for backward-compatible readers.
    crew_of = _crew_of_pairing(solved)   # pid -> [(idx, skill, eid)]

    def _pairing_record(i, rec):
        pid = f"C_{i:06d}"
        slots = crew_of.get(pid) or [(0, "Captain", None)]
        return {"pid": pid,
                "employee": slots[0][2],
                "crew": [{"indexInFlight": idx, "requiredSkill": skill,
                          "employee": eid} for idx, skill, eid in slots],
                "fns": rec["fns"],
                "active_fns": rec.get("active_fns", rec["fns"])}

    traj = {"inst": inst, "seed": seed, "budget_s": budget,
            "stage2_score": stage2_score,
            "catalog": {"file": sel_path.name,
                        "ip_objective": None, "source": None},
            "pairings": [_pairing_record(i, rec)
                         for i, rec in enumerate(selected)
                         if rec.get("active_fns", rec["fns"])]}
    if inst is None:
        traj["label"] = label
    ap_final = paths["assignment"]
    ap_pending = ap_final.with_suffix(".pending.json")
    ap_pending.write_text(json.dumps(traj))

    from _coverage_validator import check as coverage_check
    # Покрытие сверяется в ИСХОДНОМ пространстве (спрос payload'а против
    # восстановленного решения) — джойн по leg_id/slot_key точен.
    cov = coverage_check(payload_orig, expanded_pub)
    # Аудит 2026-07-19 (P0-02): включённое правило состава, которое солвер
    # не умеет держать (языковой микс — Kotlin-констрейнта нет), могло быть
    # нарушено при статусе OK. Теперь одиночный stage-2 гейт сам строит
    # сертификат состава по расширенному ростеру и подаёт его в вердикт
    # (fail-closed: не построился — тоже брак). Kotlin по-прежнему держит
    # резерв/стаж со-локационных пар при решении; здесь — независимая
    # проверка результата.
    import rules_config as _rc
    _r = _rc.effective()
    comp_report = None
    comp_violations = None
    # Ревью пакета (2026-07-19, HIGH): lane независимого multirole видит ОДНУ
    # роль (role_views режет экипаж) — языковой микс участка может покрывать
    # член ДРУГОЙ роли, и пер-lane сертификат ложно валил легальный слитый
    # ростер. Для lane'ов точка истины состава — слияние multirole
    # (fail-closed в decide_verdict); здесь состав гейтуют только полные
    # (не-lane) прогоны.
    _is_multirole_lane = "--role--" in label
    if not _is_multirole_lane and any(
            _r.get(k) for k in ("comp_no_two_reserves",
                                "comp_min_pair_seniority_weight",
                                "comp_language_mix")):
        from _crew_composition import check_composition
        try:
            comp_report = check_composition(payload, expanded, rules=_r)
            comp_violations = comp_report["violations_total"]
        except Exception as exc:   # noqa: BLE001 — сертификат обязан быть
            comp_report = {"certificate_error": str(exc)}
            comp_violations = -1
        if comp_violations:
            print(f"  !! правила состава экипажа нарушены: "
                  f"{comp_report}", flush=True)
    sel_dump = sel_doc
    traj["catalog"]["ip_objective"] = sel_dump.get("ip_objective")
    traj["catalog"]["source"] = sel_dump.get("source")
    # Пакет B: proven_impossible_legs каталога — wire-имена, а missing_legs
    # покрытия — leg_id исходного пространства; без перевода
    # INFEASIBLE_INPUT_OR_MODEL ложно деградировал бы в INCUMBENT.
    # Аудит A4 (04.09.2026): прощаются ТОЛЬКО доказанные исключения —
    # запись доказательства с отпечатком данных этого payload'а.
    allowed_missing, _proofs_rejected = allowed_missing_from_catalog(
        sel_dump, payload_orig, wire_map)
    if _proofs_rejected:
        print("  ВНИМАНИЕ: %d имён из proven_impossible_legs каталога НЕ "
              "приняты как доказательства (недостача по ним не прощается): "
              "%s" % (len(_proofs_rejected),
                      "; ".join(f"{r['leg']}: {r['why']}"
                                for r in _proofs_rejected[:5])),
              flush=True)
    # Full-trajectory certificate + true-rules violations feed the gate
    # (round 6: the gate must consume the trajectory validator). Generic
    # runs pass all four artifact paths explicitly.
    from _gerad_trajectory_check import check_inst as traj_check
    # ГЕЙТ сертификата (пакет «умолчания-в-данные»): предел размаха
    # смены и пояс базы судья берёт из документа постановки — поле None
    # означает, что документ правила не несёт. Отказ словами ДО вызова
    # судьи, а не TypeError от max(None) и не тихий ноль пояса.
    if _doc_rules is not None:
        _traj_lacking = [n for n, v in (
            ("work_time_limit_by_tier", _doc_rules.work_time_limit_by_tier),
            ("base_tz_offset_min", _doc_rules.base_tz_offset_min))
            if v is None]
        if _traj_lacking:
            raise RulesMismatch(
                "ОТКАЗ: файл правил источника не несёт "
                f"{', '.join(_traj_lacking)} — сертификат траекторий "
                "судит предел размаха смены и месяц налёта по этим "
                "правилам документа; расчёт по умолчанию запрещён.")
    try:
        from _gerad_trajectory_check import load_equivalent_stations
        traj_report = traj_check(inst if inst is not None else label,
                                 traj_path=ap_pending, sol_path=sol_pending,
                                 catalog_path=sel_path,
                                 payload_path=payload_path,
                                 # Байты, ПРОЧИТАННЫЕ ОДИН РАЗ вместе с
                                 # отпечатком (внешний аудит R9, P0-02).
                                 # Прежде сюда шёл путь, и загрузчик читал
                                 # файл заново — уже ПОСЛЕ решения задачи,
                                 # то есть окно подмены измерялось минутами.
                                 equivalent_stations=(
                                     load_equivalent_stations(
                                         _sr_raw if source_rules_path
                                         else None,
                                         strict=source_rules_strict)),
                                 # Оба судьи — по одной постановке
                                 # (первый боевой прогон ростеринга):
                                 # эффективные личные пределы — те, что
                                 # видел решатель (документный лимит
                                 # проставлен в памяти, файла он не
                                 # касался); предел размаха смены — из
                                 # ярусов документа, а не ярус-1.
                                 block_limit_override={
                                     str(e.get("id")):
                                         e.get("flightTimeLimitMin") or 0
                                     for e in payload_orig.get("employees")
                                     or []},
                                 max_duty_span_min=(
                                     max(_doc_rules.work_time_limit_by_tier)
                                     if source_rules_path is not None
                                     else None),
                                 # Месяц налёта — в поясе базы, как у
                                 # каталога и решателя (постановка:
                                 # «часовая зона — местное время базы»).
                                 tz_offset_min=(
                                     _doc_rules.base_tz_offset_min
                                     if source_rules_path is not None
                                     else 0))
        traj_violations = traj_report["violations_total"]
    except (ValueError, FileNotFoundError, KeyError) as exc:
        traj_report = {"certificate_error": str(exc)}
        traj_violations = -1   # certificate failure fails closed
    # Заявленное правило, не дошедшее до движка, — подмена постановки
    # (аудит 2026-07-26, P0-04). Решатель отчитается «ноль жёстких», хотя
    # правила он просто не знал; один раз это уже стоило «рекордного»
    # ростера, который сертификат разнёс 595 нарушениями. Гейт обязан
    # видеть такой прогон как неполный, а не как результат.
    #
    # Сверка ПОЭЛЕМЕНТНАЯ (повторный аудит 2026-07-26): первая версия
    # обнуляла весь список при наличии ЛЮБОГО файла ресурсных правил —
    # объявленные «еженедельный отдых + посадки» при файле с одним
    # отдыхом давали OK, а посадки исчезали из проверки молча.
    declared_not_applied = _declared_minus_applied(
        os.environ.get("CREW_RULES_DECLARED_RESOURCES"),
        os.environ.get("CREW_RESOURCE_RULES"))
    traj["resource_rules_applied"] = bool(
        os.environ.get("CREW_RESOURCE_RULES"))
    traj["resource_rules_declared_not_applied"] = declared_not_applied
    # Отсев непригодных — след в артефакте, а не только в журнале
    # выполнения: приёмка ростера обязана видеть, сколько людей и почему
    # не участвовало в назначении.
    traj["employees_excluded_not_allocable"] = excluded_na
    # Месячный предел из документа — тем же следом: сколько минут и
    # скольким проставлен (None/0 — документ значения не заявлял).
    traj["monthly_limit_from_source"] = {
        "minutes": monthly_limit_min, "applied_to": monthly_limit_applied}

    # Свидетельство ПОТРЕБИТЕЛЯ (пятый аудит, пакет B). Сверка выше
    # доказывает, что правило ДОШЛО до движка; эта — что движок его
    # ПРОЧИТАЛ и прочитал именно то значение. Расхождение попадает в тот
    # же список «не применено», то есть гейт видит прогон неполным, а не
    # результатом: «ноль жёстких» под чужими значениями — утверждение о
    # другой задаче ровно так же, как под отсутствующим правилом.
    _sent_rr = None
    try:
        _raw_rr = (os.environ.get("CREW_RESOURCE_RULES") or "").strip()
        if _raw_rr.startswith("{"):
            _sent_rr = json.loads(_raw_rr)
    except json.JSONDecodeError:
        _sent_rr = None
    att_tier1 = check_consumer_attestation(
        attestation, (_rules_provenance() or {}).get("rules_effective") or {})
    att_res = check_resource_attestation(attestation, _sent_rr)
    traj["consumer_attestation"] = {"tier1": att_tier1,
                                    "resource_rules": att_res}
    not_applied = list(declared_not_applied)
    not_applied += [f"ярус1:{k}" for k in att_tier1["not_applied"]]
    not_applied += [f"ярус1:{d['key']}(применено {d['applied']}, "
                    f"действует {d['effective']})"
                    for d in att_tier1["mismatched"]]
    not_applied += [f"ресурс:{k}" for k in att_res["not_applied"]]
    not_applied += [f"ресурс:{d['key']}(применено {d['applied']}, "
                    f"передано {d['sent']})" for d in att_res["mismatched"]]
    if att_tier1.get("error"):
        not_applied.append(f"ярус1:{att_tier1['error']}")
    if not_applied != declared_not_applied:
        print(f"  !! свидетельство потребителя: {not_applied}", flush=True)
    declared_not_applied = not_applied
    traj["resource_rules_declared_not_applied"] = declared_not_applied
    result_status = gate_result_status(
        stage2_score, m["unassigned"], cov, allowed_missing,
        trajectory_violations=traj_violations,
        composition_violations=comp_violations,
        rules_not_applied=declared_not_applied)
    traj["result_status"] = result_status
    # Исключения из покрытия остаются ВИДИМЫМИ в итоге (аудит A4):
    # частичное покрытие ≠ выполненный план — complete=false и поимённый
    # список доказанно исключённых, отдельно недоказанные.
    _missing_set = set(cov["missing_legs"])
    traj["coverage_gate"] = {
        "complete": not _missing_set,
        "excluded_proven": sorted(_missing_set & allowed_missing),
        "missing_unproven": sorted(_missing_set - allowed_missing),
        "proofs_rejected": _proofs_rejected}
    traj["trajectory_check"] = traj_report
    if comp_report is not None:
        traj["composition_check"] = comp_report
    if _run_id:
        traj["run_id"] = _run_id
    # Пакет B: траектория остаётся в wire-пространстве (сертификат сверяет
    # её с каталогом verbatim); штамп биекции — для читателей и провенанса.
    if wire_map:
        traj["wire_map"] = wire_map
    ap_pending.write_text(json.dumps(traj))
    # P0-1 (аудит документации 2026-07-20): канонические файлы обновляются
    # ТОЛЬКО при OK; любой не-OK прогон публикуется диагностическими
    # копиями, прежний годный ростер остаётся активным и целым. Lane'ы
    # multirole — исключение: их канонические файлы суть IPC оркестратора
    # (читаются слиянием при любом вердикте) и никогда не промотируются.
    pub_ap, pub_sol, _auto_promote = publish_stage2_artifacts(
        label, result_status, ap_pending, sol_pending, ap_final, sol_final)
    if pub_ap == ap_final:
        print(f"  assignment trajectory -> {ap_final.name} "
              f"[{result_status}]", flush=True)
    else:
        print(f"  result_status={result_status} — канонические артефакты "
              f"НЕ обновлены, активен предыдущий ростер; диагностика -> "
              f"{pub_ap.name}", flush=True)
    # Active-roster manifest (аудит-2 P1-06/P1-13): per-run манифест с
    # хэшами входов/артефактов/сертификатов ПОСЛЕ публикации; обычный
    # stage-2 и repair авто-промотируются (семантика «последняя публикация
    # активна» сохранена), lane'ы multirole — только per-run запись (их
    # промоция — осознанное действие над родительской меткой).
    try:
        from _roster_manifest import build_run_manifest, publish
        _mode = ("repair" if (sel_dump.get("source") == "disruption-repair")
                 else "stage2")
        _manifest = build_run_manifest(
            label=label, mode=_mode, result_status=result_status,
            payload_path=payload_path, rules_snapshot=_r,
            assignment_path=pub_ap, solution_path=pub_sol,
            catalog_path=sel_path,
            trajectory_report=traj_report,
            composition_report=comp_report,
            inst=inst, seed=seed, budget_s=budget)
        publish(_manifest, auto_promote=_auto_promote)
    except Exception as exc:   # noqa: BLE001 — манифест не должен ронять
        print(f"  !! манифест ростера не записан: {exc}", flush=True)
    row = {"inst": inst, "legs": len(payload["flights"]),
           "pairings": len(syn["flights"]),
           "result_status": result_status,
           "trajectory_violations": traj_violations,
           "missing_legs": cov["missing"],
           "excluded_proven": traj["coverage_gate"]["excluded_proven"],
           "stage2_score": stage2_score,
           "expanded_uniform_gerad2": uniform,
           "direct_uniform_gerad2": direct_uniform,
           "wall_s": round(wall, 1), "seed": seed,
           "budget_s": budget, **m}
    # По каким правилам считали (P1-10) и какой это был прогон (P1-13).
    # Раньше журнал этапа 2 не записывал ни того, ни другого: каталог
    # первого этапа отпечаток правил нёс, а ростер — нет, и сверить
    # половины было нечем.
    row.update(_rules_provenance())
    if inst is None:
        row["label"] = label
    if result_status == "INFEASIBLE_INPUT_OR_MODEL":
        # Публикуемый вердикт (решение владельца 2026-08-12): рассадка
        # безупречна, непокрыто только доказуемо непокрываемое — это
        # наряд с оговоркой данных, а не отказ.
        print(f"  result_status={result_status} — опубликован: рассадка "
              f"чистая, непокрыто {cov['missing']} доказуемо "
              f"непокрываемых рейсов (свойство данных)", flush=True)
    elif result_status != "OK":
        print(f"  !! result_status={result_status} — не наряд; "
              f"trajectory_violations={traj_violations}, "
              f"missing={cov['missing']}", flush=True)
    print(f"  stage2={stage2_score}  uniform(GERAD2)={uniform}  "
          f"direct-uniform={direct_uniform}", flush=True)
    print(f"  pref_hits={m['pref_hits']}/{m['pref_attainable_legs']}  "
          f"vacation_violations={m['vacation_violations']}  "
          f"unassigned={m['unassigned']}  ({wall:.0f}s)", flush=True)

    journal_write(results_path, row)
    print(f"  saved -> {results_path.name}", flush=True)
    return result_status


def publish_stage2_artifacts(label: str, result_status: str,
                             ap_pending, sol_pending, ap_final, sol_final):
    """P0-1: публикация результата этапа 2 с гейтом вердикта.

    Белый список публикации (решение владельца 2026-08-12, вечер):
    OK и INFEASIBLE_INPUT_OR_MODEL — по образцу каталога пейринга
    (OK | INFEASIBLE_INPUT). Второй вердикт означает: рассаженная часть
    безупречна, непокрыты только доказуемо непокрываемые рейсы — это
    свойство ДАННЫХ, а не недоработка расчёта; на одном из наборов
    заказчика (рейсы без парного плеча) полного ростера не существует,
    и без публикации
    этого вердикта активный ростер был бы недостижим вовсе. Имя
    вердикта остаётся честным в артефакте и журнале — «OK» он не
    становится.

    Публикуемый (не-lane): pending атомарно заменяют канонические
    файлы; прогон авто-промотируется. Lane multirole ('--role--' в
    метке): канонические файлы пишутся при ЛЮБОМ вердикте (IPC
    оркестратора слияния), но никогда не промотируются. Любой
    непубликуемый: таймстампованные диагностические копии в
    runs/stage2-diagnostics, канонические файлы НЕ трогаются, промоции
    нет — активным остаётся прежний годный ростер.

    Возврат: (assignment_path, solution_path, auto_promote) — пути,
    которые несёт манифест прогона."""
    _is_lane = "--role--" in label
    publishable = result_status in ("OK", "INFEASIBLE_INPUT_OR_MODEL")
    diag_ap = diag_sol = None
    if not publishable:
        diag = _data_paths.results_dir() / "runs" / "stage2-diagnostics"
        diag.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        diag_ap = diag / f"{label}-{stamp}-assignment.json"
        diag_sol = diag / f"{label}-{stamp}-solution.json"
        diag_ap.write_bytes(ap_pending.read_bytes())
        diag_sol.write_bytes(sol_pending.read_bytes())
    if publishable or _is_lane:
        ap_pending.replace(ap_final)
        sol_pending.replace(sol_final)
        return ap_final, sol_final, (publishable and not _is_lane)
    ap_pending.unlink(missing_ok=True)
    sol_pending.unlink(missing_ok=True)
    return diag_ap, diag_sol, False


def main():
    cfg = parse_args(sys.argv[1:])
    results_path = _data_paths.results_dir() / "gerad_phase2_two_stage.json"
    n_not_ok = 0
    if cfg["mode"] == "generic":
        missing = [p for p in (cfg["payload_path"], cfg["catalog_path"])
                   if not p.exists()]
        if missing:
            for p in missing:
                print(f"!! required input does not exist: {p}", flush=True)
            return 2
        status = run_one(cfg["label"], cfg["payload_path"],
                         cfg["catalog_path"], cfg["budget_s"], cfg["seed"],
                         inst=None, results_path=results_path,
                         locked_path=cfg.get("locked_path"),
                         resource_rules_path=cfg.get(
                             "resource_rules_path"),
                         resource_rules_fp=cfg.get("resource_rules_fp"),
                         source_rules_path=cfg.get("source_rules_path"),
                         source_rules_fp=cfg.get("source_rules_fp"),
                         source_rules_strict=bool(
                             cfg.get("source_rules_strict")),
                         catalog_fp=cfg.get("catalog_fp"))
        n_not_ok = 0 if status == "OK" else 1
    else:
        for inst in range(cfg["lo"], cfg["hi"] + 1):
            sel_path = (_data_paths.results_dir()
                        / f"gerad_colgen_selected_inst{inst}.json")
            if not sel_path.exists():
                print(f"inst{inst}: no selected-pairings dump yet — skipped",
                      flush=True)
                continue
            payload_path = SCRATCH / "payloads" / f"inst{inst}_p2.json"
            status = run_one(f"inst{inst}", payload_path, sel_path,
                             cfg["budget_s"], cfg["seed"], inst=inst,
                             results_path=results_path,
                             source_rules_path=cfg.get("source_rules_path"),
                             source_rules_fp=cfg.get("source_rules_fp"),
                             source_rules_strict=bool(
                                 cfg.get("source_rules_strict")))
            if status != "OK":
                n_not_ok += 1
    # Round-6 audit: a run with any non-OK instance must not exit 0.
    return min(n_not_ok, 125)


if __name__ == "__main__":
    sys.exit(main())
