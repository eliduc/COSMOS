"""TDH-aware crew-trajectory validator (audit P0, 2026-07-12, package B).

Reads the SOLVED stage-2 assignment (gerad_stage2_assignment_instN.json:
employee -> pairings -> full leg list + active/TDH split) and validates,
per employee, the full physical route with deadhead legs included:

  - airport continuity across ALL legs (operating + TDH);
  - sit >= 30 min, overlap-free;
  - duties = runs split by rests >= 10 h; span <= 14 h; landings <= 5
    (a TDH ride lands too);
  - monthly operating block <= 85 h (TDH does not count as block);
  - pairing anchoring: each pairing starts/ends at the employee's base.

This replaces the inferential "teleport = TDH" reading of the flight-only
validator with a per-transition check of the actual trajectory.

Usage: python _gerad_trajectory_check.py [inst ...]   (default 1..7)
Output: final-report/gerad_trajectory_check.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import rules_config  # noqa: E402  (single tier-1 rules source)

# Tier-1 rule parameters from rules_config (env CREW_RULES_CONFIG;
# defaults are exactly the historical hardcoded values).
_RULES = rules_config.effective()
MIN_SIT = _RULES["min_sit_min"]
REST = _RULES["min_rest_min"]
MAX_LANDINGS = _RULES["max_landings_per_duty"]
MAX_DUTY = _RULES["max_duty_span_min"]
MAX_BLOCK = _RULES["max_monthly_block_min"]
EPOCH = datetime(2000, 1, 1)


def check_employee_sequence(seq, v, block_limit: int | None = None,
                            equivalent_stations: dict | None = None,
                            max_duty: int | None = None,
                            tz_offset_min: int = 0) -> int:
    """True-rules checks over ONE employee's chronological leg sequence
    (operating + TDH); items are (dep_min, arr_min, dep, arr, is_tdh, pid).
    Mutates the violation counter `v`; returns verified TDH transitions.
    Exposed as the production unit for tests (audit 2026-07-12).

    `block_limit` — ЛИЧНЫЙ месячный лимит налёта сотрудника в минутах;
    None означает «личного нет, действует общий».

    Две поправки прочтения правила (разбор 2026-07-27), обе — расхождение
    с решателем, а не с данными:

    1. Налёт считается ПО КАЛЕНДАРНОМУ МЕСЯЦУ, а не по всему размаху
       прогона. Горизонт набора заказчика захватывает несколько
       календарных месяцев, и суммирование по размаху сравнивало
       налёт за весь горизонт с месячным пределом.
    2. Предел берётся ЛИЧНЫЙ, если он задан в данных сотрудника. В
       наборе заказчика у большинства людей личный предел выше
       общего — сертификат, судивший всех по общему, объявлял
       нарушителями людей, которых решатель законно считал в пределах
       нормы. Ошибался сертификат.
    """
    tdh_transitions = 0
    equiv = equivalent_stations or {}
    cap = block_limit if block_limit and block_limit > 0 else MAX_BLOCK
    # Предел размаха смены — ИЗ ПОСТАНОВКИ, когда вызывающий её знает
    # (правила источника задают ярусы выше предела яруса 1).
    # Первый боевой ростеринг одной из линий: проверка с порогом
    # яруса 1 дала 1315 ложных «duty span>14h» на цепочках, легальных
    # по документу, — оба судьи обязаны судить по одной постановке.
    duty_cap = max_duty if max_duty and max_duty > 0 else MAX_DUTY
    # Календарный месяц налёта — В ПОЯСЕ БАЗЫ (tz_offset_min), как его
    # считают каталог и решатель и как задаёт постановка («часовая
    # зона — местное время базы»). Счёт по UTC относил вечерние участки
    # 31-го числа к другому месяцу: 4 ложных «block>limit» на том же
    # боевом ростеринге — четвёртое расхождение судей за один разбор.
    per_month: dict = {}
    for d, a, _, _, tdh, *_rest in seq:
        if tdh:
            continue
        key = (EPOCH + timedelta(minutes=d + tz_offset_min)
               ).strftime("%Y-%m")
        per_month[key] = per_month.get(key, 0) + (a - d)
    if any(total > cap for total in per_month.values()):
        v["block>limit"] += 1
    for x1, x2 in zip(seq, seq[1:]):
        (d1, a1, dc1, ac1, t1, p1) = x1[:6]
        (d2, a2, dc2, ac2, t2, p2) = x2[:6]
        gap = d2 - a1
        if ac1 != dc2:
            # Равнозначные станции (правило `stationEquivalenceRules`
            # системы-источника): экипаж законно продолжает работу из
            # ДРУГОГО аэропорта той же агломерации, если разрыв не меньше
            # заданного. Прежде сертификат об этом правиле не знал и
            # объявлял такой переезд телепортацией.
            #
            # На наборе заказчика это давало 97 ложных нарушений
            # из 211 — все до единого по объявленным в его данных
            # парам равнозначных станций при разрывах заведомо больше
            # требуемого. Сам источник делает ровно те же переезды.
            need = equiv.get((ac1, dc2))
            if need is None:
                v["discontinuity"] += 1
            elif gap < need:
                v["equivalent station too soon"] += 1
            else:
                # Не нарушение — но и не молчание: послабление обязано
                # быть видно в отчёте, иначе его не отличить от дыры.
                v["info:equivalent station move"] += 1
        elif t1 or t2:
            tdh_transitions += 1
        if gap < 0:
            v["overlap"] += 1
        elif gap < MIN_SIT:
            v["sit<30"] += 1
        # A transition BETWEEN two different pairings of one employee
        # requires a full rest >= 10 h (audit 2026-07-12).
        if p1 != p2 and 0 <= gap < REST:
            v["inter-pairing rest<10h"] += 1
    # Границы смен берутся ОБЪЯВЛЕННЫЕ, если они есть в записи участка
    # (седьмой элемент — ключ смены). Выводить их из порога отдыха
    # нельзя: у самого заказчика 390 из 1484 переходов между сменами
    # короче десяти часов, а 356 и вовсе отрицательные — его наряды
    # несут briefing/debriefing и перекрываются по времени. Восстановление
    # по плоскому порогу склеивало по 25 смен на цепочку и давало у
    # уровня 1 девяносто четыре превышения размаха вместо сорока одного
    # настоящего (разбор 2026-07-27).
    #
    # Плоский порог остаётся запасным путём — для артефактов, где границы
    # не объявлены; отчёт называет применённую модель.
    declared = all(len(x) > 6 and x[6] is not None for x in seq)
    duties = []
    if declared:
        cur_key, duty = object(), None
        for item in seq:
            if item[6] != cur_key:
                cur_key = item[6]
                duty = [item]
                duties.append(duty)
            else:
                duty.append(item)
        v["info:duty_model_declared"] += 0     # модель видна в отчёте
    else:
        duty = [seq[0]]
        duties = [duty]
        for prev, cur in zip(seq, seq[1:]):
            if cur[0] - prev[1] >= REST:
                duty = [cur]
                duties.append(duty)
            else:
                duty.append(cur)
    for du in duties:
        if len(du) > MAX_LANDINGS:
            v["landings>5/duty"] += 1
        # Размах смены считается по РАБОЧИМ участкам: перелёт пассажиром в
        # него не входит.
        #
        # Так читает правило заказчик, и это проверено на его собственных
        # данных (2026-07-27): по его цепочкам смен длиннее 14 часов
        # ПЯТЬ без перегонов и СЕМНАДЦАТЬ с перегонами, а входной аудит,
        # работающий по его прочтению, называет пять. Так же читает наш
        # построитель цепочек. Прежняя версия сертификата включала
        # перегоны и в одиночку расходилась с обоими: на наших ростерах
        # она давала 527 превышений, из которых после исключения
        # перегонов не оставалось НИ ОДНОГО.
        #
        # Более строгое прочтение (перегон — тоже работа) остаётся
        # видимым отдельным счётчиком: правило спорное, и прятать вторую
        # трактовку нельзя.
        work = [x for x in du if not x[4]]
        if work and work[-1][1] - work[0][0] > duty_cap:
            v["duty span>limit"] += 1
        if du[-1][1] - du[0][0] > duty_cap:
            v["info:duty span>limit with positioning"] += 1
    return tdh_transitions


def pairing_crew(p: dict) -> list:
    """Ordered crew slots of a pairing record as (indexInFlight, requiredSkill,
    employeeId). Reads the phase-2 `crew` list (co-located multi-crew) when
    present; else falls back to the legacy scalar `employee` as a single
    (0, 'Captain') slot — so pre-phase-2 artifacts certify unchanged."""
    crew = p.get("crew")
    if crew:
        return [(c.get("indexInFlight", 0), c.get("requiredSkill", "Captain"),
                 c.get("employee")) for c in crew]
    return [(0, "Captain", p.get("employee"))]


def certify_artifact_linkage(traj: dict, catalog: dict, payload: dict,
                             solution: dict) -> None:
    """Exact artifact certificate (audit 2026-07-12, round 6): the
    trajectory must be the catalog's non-pure-deadhead records VERBATIM —
    same pid indexing, same fns AND same active_fns — the active sets must
    partition the catalog's active coverage, and the flight-level solution
    must assign exactly the active legs of assigned pairings to exactly
    those employees. Any deviation (zeroed active_fns, dropped or extra
    pairings, swapped employees) raises ValueError instead of silently
    weakening downstream checks."""
    if not traj.get("pairings"):
        raise ValueError("empty trajectory artifact")
    expected = {}
    for i, rec in enumerate(catalog["selected"]):
        active = rec.get("active_fns", rec["fns"])
        if active:
            expected[f"C_{i:06d}"] = (tuple(rec["fns"]), tuple(active))
    seen = {}
    for p in traj["pairings"]:
        if p["pid"] in seen:
            raise ValueError(f"duplicate pid {p['pid']} in trajectory")
        seen[p["pid"]] = (tuple(p["fns"]), tuple(p["active_fns"]))
    if set(seen) != set(expected):
        raise ValueError(
            f"trajectory/catalog pid mismatch — "
            f"{len(set(expected) - set(seen))} catalog pairings missing, "
            f"{len(set(seen) - set(expected))} extra")
    bad = [pid for pid in expected if seen[pid] != expected[pid]]
    if bad:
        raise ValueError(
            f"{len(bad)} pairings differ from the catalog record "
            f"(fns or active_fns tampered), e.g. {bad[:3]}")
    # Active ownership must be a partition of the catalog's active coverage.
    active_counts = defaultdict(int)
    for fns, active in seen.values():
        for fn in active:
            active_counts[fn] += 1
    multi = {fn: c for fn, c in active_counts.items() if c > 1}
    if multi:
        raise ValueError(f"{len(multi)} legs actively owned more than once")
    cat_active = {fn for _, active in expected.values() for fn in active}
    if set(active_counts) != cat_active:
        raise ValueError("active coverage differs from the catalog")
    raw_fns = {f["flightNumber"] for f in payload["flights"]}
    traj_fns = {fn for fns, _ in seen.values() for fn in fns}
    if not traj_fns <= raw_fns:
        raise ValueError(f"trajectory references "
                         f"{len(traj_fns - raw_fns)} unknown legs")
    # Flight-level solution linkage is REQUIRED (round 6): the assigned
    # SEATS of the solution must be exactly the active legs of assigned
    # pairings, with matching employees. Keyed per (leg, indexInFlight) so a
    # co-located Captain+FO leg (two seats, two crew) links each seat to its
    # own employee; single-crew GERAD keys (fn, 0) — identical to keying by
    # fn alone.
    if solution is None:
        raise ValueError("flight-level solution artifact is required")
    sol_assign = {}
    sol_roles: dict = {}          # место -> роль, для сверки по ролям ниже
    for a in solution.get("flightAssignments", []):
        fn = a["flight"] if not isinstance(a["flight"], dict) \
            else a["flight"].get("flightNumber")
        eid = a.get("employee")
        eid = eid.get("id") if isinstance(eid, dict) else eid
        if eid is None:
            continue
        key = (fn, a.get("indexInFlight", 0))
        sol_roles[key] = str(a.get("requiredSkill"))
        # A duplicate seat key would collapse under last-write-wins, and a
        # collusive row order could then hide a double-seating from the
        # mismatch check below (audit 2026-07-19 hardening — pre-existing, not
        # a phase-2 regression). Fail on the duplicate instead of silently
        # keeping only the last writer.
        if key in sol_assign:
            raise ValueError(
                f"flight-level solution assigns seat {key} twice "
                f"(employees {sol_assign[key]} and {eid}) — a duplicate seat "
                f"key must not be silently collapsed")
        sol_assign[key] = eid
    # Сверка мест идёт ПО РОЛИ НА УЧАСТКЕ, а не по номеру места.
    #
    # Причина (разбор 2026-07-27). Номера мест живут в ДВУХ РАЗНЫХ
    # пространствах, и прежняя версия сверяла их напрямую. Разворот
    # цепочки в поштучное расписание (`expand_to_flight_level`) нумерует
    # ряды ПО СПРОСУ УЧАСТКА: у участка четыре места — ряды 0..3. А
    # экипаж цепочки — это поролевой МАКСИМУМ по всем её участкам, и его
    # номера доходят до 10. Цепочка с экипажем 11 на участке со спросом 4
    # давала расхождение гарантированно: сверялись разные вещи. На наборе
    # заказчика это сделало сертификат невычислимым для ВСЕХ
    # прогонов на наших каталогах — вердикт «не доказано» вместо «законно
    # или нет».
    #
    # Что сверяется теперь. Для каждого активного участка цепочки и
    # каждой роли: люди, посаженные решением, обязаны быть ПОДМНОЖЕСТВОМ
    # экипажа цепочки той же роли, а их число — равно спросу участка по
    # этой роли (но не больше, чем есть в экипаже). Места одного ранга
    # взаимозаменяемы — это свойство предметной области, оно же записано
    # в выгрузке формата вендора, — поэтому требовать совпадения НОМЕРА
    # места было бы требованием сверх постановки.
    #
    # Что при этом по-прежнему ловится: чужой человек на участке, недобор
    # и перебор посаженных, места незаназначенных цепочек с людьми, места
    # вне активного покрытия. Что перестало ловиться: перестановка людей
    # ОДНОГО ранга между местами одного участка — она и не является
    # дефектом.
    demand_of: dict = {}
    for a in payload.get("flightAssignments") or []:
        if not isinstance(a, dict):
            continue
        ref = a.get("flight")
        ref = ref.get("flightNumber") if isinstance(ref, dict) else ref
        if not ref:
            continue
        demand_of.setdefault(ref, Counter())[str(a.get("requiredSkill"))] += 1

    sol_by_leg: dict = {}
    for (fn, idx), eid in sol_assign.items():
        sol_by_leg.setdefault(fn, []).append((sol_roles.get((fn, idx)), eid))

    mism: list = []
    ghost = 0
    for p in traj["pairings"]:
        crew = pairing_crew(p)
        pool = {eid for _, _, eid in crew if eid is not None}
        by_role: dict = {}
        for idx, skill, eid in sorted(crew, key=lambda c: (c[0], str(c[1]))):
            # В ожидание идут ТОЛЬКО занятые места экипажа. Незанятое
            # место (роль в цепочке есть, человека нет) рядов в поштучном
            # расписании не порождает — считать его означало бы объявлять
            # рассинхронизацией обычный недобор. На наборе заказчика это
            # давало ложные расхождения там, где у цепочки нет
            # инструктора, а участок его требует.
            if eid is not None:
                by_role.setdefault(str(skill), []).append(eid)
        for fn in p["active_fns"]:
            got_rows = [(str(role) if role is not None else None, eid)
                        for role, eid in sol_by_leg.get(fn, [])
                        if eid is not None]
            got = [eid for _role, eid in got_rows]
            if not pool:
                # цепочка никому не назначена — мест с людьми быть не может
                ghost += len(got)
                continue
            demand = demand_of.get(fn)
            if demand:
                want_n = sum(min(len(members), demand.get(role, 0))
                             for role, members in by_role.items())
            else:
                # Спрос участка неизвестен (наборы прежнего выпуска и
                # односоставные артефакты): ожидаем весь экипаж цепочки.
                # Молча НЕ ослабляем проверку — прежде отсутствие спроса
                # обнуляло её целиком, и подмена человека проходила.
                want_n = len(pool)
            if len(got) != want_n:
                mism.append(f"{fn}: посажено {len(got)}, ожидалось {want_n}")
                continue
            outside = [e for e in got if e not in pool]
            if outside:
                mism.append(
                    f"{fn}: {len(outside)} человек вне экипажа цепочки")
                continue
            # Сверка ПО РОЛЯМ (внешний аудит R6, P1-02): прежде считались
            # только численность и принадлежность общему пулу, и обмен
            # командира со вторым пилотом между людьми с двумя допусками
            # проходил сертификат. Теперь человек на месте роли обязан
            # числиться в цепочке ИМЕННО в этой роли; места без роли в
            # решении судятся по-прежнему пулом.
            role_bad = []
            for role, eid in got_rows:
                # Места без роли в решении (None) судятся проверкой пула
                # выше. РОЛЬ, КОТОРОЙ ЦЕПОЧКА НЕ ЗНАЕТ, — отказ, а не
                # пропуск (внешний аудит R7, P1-02): пропуск позволял
                # переподписать место выдуманной ролью и обойти сверку.
                if role in (None, "None"):
                    continue
                if role not in by_role:
                    role_bad.append(f"{eid}@{role}(неизвестная роль)")
                elif eid not in by_role[role]:
                    role_bad.append(f"{eid}@{role}")
            if role_bad:
                mism.append(f"{fn}: люди не в своей роли: "
                            + ", ".join(sorted(role_bad)[:4]))
    if mism:
        raise ValueError(
            f"{len(mism)} расхождений между цепочками и поштучным "
            f"расписанием (первые: {'; '.join(mism[:3])})")
    if ghost:
        raise ValueError(
            f"{ghost} seats of UNASSIGNED pairings carry an employee "
            f"in the flight-level solution")
    active_all = {fn for p in traj["pairings"] for fn in p["active_fns"]}
    extra = {fn for fn in sol_by_leg if fn not in active_all}
    if extra:
        raise ValueError(
            f"flight-level solution assigns {len(extra)} legs outside the "
            f"active coverage of assigned pairings")


def load_equivalent_stations(source_rules,
                             strict: bool = False) -> dict:
    """Пары равнозначных станций из настроек системы-источника.

    Принимает путь ЛИБО уже прочитанные байты. Второе — не удобство, а
    закрытие окна подмены (внешний аудит R9, P0-02): отпечаток считался по
    одному чтению файла, а работа шла по ВТОРОМУ чтению того же пути,
    сделанному здесь. Между ними — минуты (у этапа 2 проверка идёт до
    решения задачи, а чтение после), и подмена файла в этом окне
    проходила с нулевым кодом возврата. Вызывающий, уже прочитавший байты
    ради отпечатка, обязан передать ИХ, а не путь.

    Без файла — пустой словарь и прежнее поведение: любая смена станции
    считается разрывом. Правило заказчика не подставляется по умолчанию,
    иначе умолчание выдало бы себя за его данные.

    strict (внешний аудит R8, P0-01): файл БЕЗ раздела равнозначных
    станций оставил бы зашитые пары умолчания другой авиакомпании — в
    строгом режиме это отказ, а не тихая подстановка.
    """
    if not source_rules:
        return {}
    import _cos_rules
    # Единая точка загрузки: опознание вида и отказ при неопознанном
    # документе (см. `_cos_rules.load_bytes`). Принимается и служебный
    # JSON, и документ постановки целиком. Прямой from_parameters
    # разбирал снимок `as_dict()` в ноль ключей и молча возвращал
    # умолчания — здесь это означало бы зашитые пары равнозначных
    # станций вместо переданных заказчиком.
    if isinstance(source_rules, dict):
        rules = _cos_rules.load_document(source_rules,
                                         where="правила источника")
    else:
        raw = (bytes(source_rules)
               if isinstance(source_rules, (bytes, bytearray))
               else Path(source_rules).read_bytes())
        rules = _cos_rules.load_bytes(raw, where="правила источника")
    if strict and any(k.startswith("stationEquivalenceRules")
                      for k in rules.source_keys_missing):
        raise ValueError(
            "правила источника не содержат stationEquivalenceRules — в "
            "строгом режиме зашитые пары умолчания не подставляются")
    # teleportation=None — раздел источником не заявлен: механизм спит
    # (пары не равнозначны — отсутствие только УЖЕСТОЧАЕТ); в строгом
    # режиме сюда не дойти — отказ выше.
    return dict(rules.teleportation or {})


def check_inst(inst: int | str, traj_path: Path | None = None,
               sol_path: Path | None = None,
               catalog_path: Path | None = None,
               payload_path: Path | None = None,
               equivalent_stations: dict | None = None,
               block_limit_override: dict | None = None,
               max_duty_span_min: int | None = None,
               tz_offset_min: int = 0) -> dict:
    # block_limit_override — ЭФФЕКТИВНЫЕ личные пределы налёта, которые
    # видел решатель (этап 2 проставляет документный предел в памяти —
    # файл набора его не несёт); max_duty_span_min — предел размаха
    # смены из постановки. Оба судьи обязаны судить по одной постановке:
    # первый боевой ростеринг дал 798 ложных «block>limit» (файл против
    # памяти) и 1315 ложных превышений смены (ярус 1 против документа).
    # Path parameters default to today's canonical instN locations; the
    # stage-2 generic mode (uploaded instances) passes all four explicitly
    # and inst is the label slug (used only for the report key then).
    import _data_paths; fr = _data_paths.results_dir()
    traj = json.loads(
        Path(traj_path or fr / f"gerad_stage2_assignment_inst{inst}.json")
        .read_text(encoding="utf-8"))
    payload = json.loads(
        Path(payload_path
             or HERE / "data" / "gerad" / "payloads" / f"inst{inst}_p2.json")
        .read_text(encoding="utf-8"))
    catalog = json.loads(
        Path(catalog_path
             or fr / f"gerad_colgen_selected_inst{inst}.json").read_text(encoding="utf-8"))
    sp = Path(sol_path or HERE / "data" / "gerad"
              / f"p2_two_stage_solution_inst{inst}.json")
    solution = (json.loads(sp.read_text(encoding="utf-8"))
                if sp.exists() else None)
    # Пакет B: траектория/каталог живут в wire-пространстве (F100@1/F100@2),
    # опубликованное решение — в исходном. Пересчитываем ту же
    # детерминированную биекцию из payload'а и переводим строки решения в
    # wire по их flightLegId; payload без повторов проходит identity-путём
    # (no-op для канонических артефактов).
    from _leg_rekey import rekey_or_passthrough
    payload, wire_map = rekey_or_passthrough(payload)
    if wire_map and isinstance(solution, dict):
        import copy as _copy
        by_leg = {rec["flightLegId"]: wfn for wfn, rec in wire_map.items()}
        solution = _copy.deepcopy(solution)
        for a in solution.get("flightAssignments", []):
            if isinstance(a, dict) and \
                    str(a.get("flightLegId")) in by_leg:
                a["flight"] = by_leg[str(a["flightLegId"])]
    certify_artifact_linkage(traj, catalog, payload, solution)
    fl = {}
    for f in payload["flights"]:
        from _canonical_time import parse_utc
        dep = parse_utc(f["departureUTCDateTime"])
        arr = parse_utc(f["arrivalUTCDateTime"])
        fl[f["flightNumber"]] = (
            int((dep - EPOCH).total_seconds() // 60),
            int((arr - EPOCH).total_seconds() // 60),
            f["departureAirport"], f["arrivalAirport"])
    home = {e["id"]: e.get("homeAirport") for e in payload["employees"]}
    # Личный месячный предел налёта из данных сотрудника — тот же
    # источник, из которого его берёт решатель. Ноль или отсутствие
    # означает «действует общий предел».
    block_limit: dict = {}
    for e in payload["employees"]:
        try:
            block_limit[e["id"]] = int(e.get("flightTimeLimitMin") or 0)
        except (TypeError, ValueError):
            block_limit[e["id"]] = 0
    for k, val in (block_limit_override or {}).items():
        try:
            block_limit[str(k)] = int(val or 0)
        except (TypeError, ValueError):
            pass

    # ОБЪЯВЛЕННЫЕ границы смен из каталога: (pid, участок) -> ключ смены.
    # Каталог несёт их в duty_fns — это структура, которую построитель
    # ФАКТИЧЕСКИ построил, а не та, которую можно угадать по паузам.
    duty_key_of: dict = {}
    for i, rec in enumerate(catalog.get("selected") or []):
        pid = f"C_{i:06d}"
        for j, group in enumerate(rec.get("duty_fns") or []):
            for fn in group:
                duty_key_of[(pid, fn)] = (pid, j)

    v = defaultdict(int)
    by_emp = defaultdict(list)   # (dep, arr, dep_st, arr_st, is_tdh, pid, duty)
    for p in traj["pairings"]:
        active = set(p["active_fns"])
        first = fl[p["fns"][0]]
        last = fl[p["fns"][-1]]
        # Co-located crew: every crew member of the pairing flies the SAME
        # physical legs, so each is fanned into their own by_emp sequence and
        # anchored at their own base. Single-crew keeps one slot — identical
        # to the pre-phase-2 per-pairing loop, and the "unassigned_pairing"
        # counter is unchanged for one-slot pairings.
        for _idx, _skill, eid in pairing_crew(p):
            if eid is None:
                v["unassigned_pairing"] += 1
                continue
            hb = home.get(eid)
            if hb and (first[2] != hb or last[3] != hb):
                v["pairing_not_anchored_at_home"] += 1
            for fn in p["fns"]:
                d, a, dc, ac = fl[fn]
                # Перелёт пассажиром объявленной смены не имеет — он
                # приписывается смене своей же цепочки по времени, а если
                # объявленных смен нет вовсе, работает запасной путь.
                # Перелёт пассажиром объявленной смены не имеет: он и не
                # образует рабочей смены. Даём ему СОБСТВЕННУЮ группу —
                # иначе один перегон обнулял бы объявленную модель для
                # всего сотрудника, и границы снова выводились бы из
                # порога отдыха (у уровня 1 это давало 103 превышения
                # размаха вместо 66).
                key = duty_key_of.get((p["pid"], fn))
                if key is None and fn not in active:
                    key = (p["pid"], "dh", fn)
                # РАБОЧИЙ участок без объявленной смены остаётся БЕЗ
                # ключа: каталог границ не нёс, и модель обязана честно
                # упасть в запасной путь по порогу отдыха. Прежний
                # безусловный синглтон-ключ объявлял границы, которых
                # никто не строил, — для каталогов без duty_fns (линия
                # колонной генерации) проверки «landings>5» и
                # «duty span>14h» структурно не срабатывали никогда
                # (ложная зелень; противниковая проверка 2026-08-06).
                by_emp[eid].append(
                    (d, a, dc, ac, fn not in active, p["pid"], key))

    tdh_transitions = 0
    for eid, seq in by_emp.items():
        seq.sort()
        tdh_transitions += check_employee_sequence(
            seq, v, block_limit=block_limit.get(eid),
            equivalent_stations=equivalent_stations,
            max_duty=max_duty_span_min,
            tz_offset_min=tz_offset_min)

    # Счётчики с приставкой info: — вторые трактовки спорных правил. Они
    # публикуются, но в счёт нарушений НЕ идут: гейт обязан судить по
    # прочтению заказчика, а не по самому строгому из возможных.
    total = sum(val for key, val in v.items()
                if not str(key).startswith("info:"))
    import time
    return {"inst": inst, "employees": len(by_emp),
            "legs_in_trajectories": sum(len(s) for s in by_emp.values()),
            "tdh_transitions_verified": tdh_transitions,
            "violations_total": total, "violations": dict(sorted(v.items())),
            # Stale-row visibility for subset-run merges (audit round 6).
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}


def main():
    # Правила источника — те же байты, что у этапов 1-2 (внешний аудит
    # R8, P0-01): отпечаток обязателен при файле и сверяется до чтения.
    import argparse
    import hashlib
    ap = argparse.ArgumentParser()
    ap.add_argument("insts", nargs="*", type=int)
    ap.add_argument("--source-rules", type=Path, default=None)
    ap.add_argument("--source-rules-fp", default=None)
    a = ap.parse_args()
    equiv = {}
    if a.source_rules is not None:
        if not a.source_rules_fp:
            print("ОТКАЗ: --source-rules требует --source-rules-fp",
                  flush=True)
            return 2
        # ОДНО чтение: отпечаток и работа идут по одним и тем же байтам
        # (внешний аудит R9, P0-02). Прежде отпечаток считался здесь, а
        # загрузчик читал файл ЗАНОВО — подмена между двумя чтениями
        # проходила с нулевым кодом возврата.
        raw = a.source_rules.read_bytes()
        got = hashlib.sha256(raw).hexdigest()
        if got != a.source_rules_fp:
            print("ОТКАЗ: файл правил источника подменён (%s != %s)"
                  % (got[:16], a.source_rules_fp[:16]), flush=True)
            return 2
        equiv = load_equivalent_stations(raw, strict=True)
    insts = a.insts or list(range(1, 8))
    out = []
    for inst in insts:
        try:
            r = check_inst(inst, equivalent_stations=equiv)
        except FileNotFoundError as e:
            print(f"inst{inst}: нет артефакта траектории ({e})", flush=True)
            continue
        out.append(r)
        print(f"inst{inst}: {r['violations_total']} нарушений "
              f"{r['violations']}  (TDH-переходов проверено: "
              f"{r['tdh_transitions_verified']})", flush=True)
    import _data_paths; p = _data_paths.results_dir() / "gerad_trajectory_check.json"
    # Merge with prior rows (audit 2026-07-12, round 5): a subset run must
    # not shrink the artifact — same rule as the coverage validator.
    try:
        prior = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        prior = []
    keep = [r for r in prior if r.get("inst") not in {n["inst"] for n in out}]
    merged = sorted(keep + out, key=lambda r: r.get("inst", 0))
    p.write_text(json.dumps(merged, indent=1))
    print(f"saved -> {p.name} ({len(merged)} rows)", flush=True)
    # Fail-closed код возврата (внешний аудит R8, P1-06): печать нарушений
    # с кодом 0 делала сертификат справочным — конвейер не видел провала.
    # Жёсткими считаются все счётчики, кроме информационных (info:*) и
    # незакрытых мест (unassigned_pairing — недобор судится покрытием).
    hard = sum(n for r in out
               for k, n in (r.get("violations") or {}).items()
               if not k.startswith("info:") and k != "unassigned_pairing")
    missing = len(insts) - len(out)
    if missing:
        print(f"ОТКАЗ: артефактов траектории нет для {missing} "
              f"наборов из {len(insts)}", flush=True)
        return 2
    if hard:
        print(f"ОТКАЗ: жёстких нарушений в сертификате: {hard}",
              flush=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
