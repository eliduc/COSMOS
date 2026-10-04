"""Выгрузка решения решателя во вход независимого сертификатора.

Мост CDPM -> COSMOS, этап Б. Сертификатор (`vmp/run/roster_certify.py`)
принимает решение как `{"solution", "assignments", "duties"}`, где
`duties` необязательны, но без них все правила о СМЕНАХ ложатся
«не прогнана»: проверить продолжительность смены нельзя, пока не
сказано, где смена начинается.

Границы смен даёт тот, кто их знает. Профиль ограничений по умолчанию
режет назначения на смены там, где разрыв дотягивает до законного
отдыха В ЭТОМ МЕСТЕ — дома (аэропорт прилёта равен базе приписки) или
вне базы, двумя порогами. Пороги — из показаний ТОГО профиля, который
считал (`lastRun.profileReadings` отчёта применённого), а не из яруса
правил (профиль по умолчанию его не читает вовсе) и не из конфигурации
установки. Профили GERAD режут смены по КАЛЕНДАРНОЙ ДАТЕ вылета, а у
GERAD2P смен нет вовсе — выгрузка смен по порогу отдыха для них
неверна, и она отказывает, а не режет по правдоподобному числу.

Первая редакция этого модуля читала именно ярус — и молча резала
смены по 600 минут там, где прогон считал легальность по 480. Обе
величины правдоподобны, обе «из отчёта решателя», и разойтись они
могли только тихо. Тот же класс «число ниоткуда», что вес и
численность экипажа, только вошедший через правильно выглядящий
источник.
"""

from __future__ import annotations

from datetime import datetime, timedelta


#: Имя профиля ограничений по умолчанию в отчёте решателя
#: (TaskModes.PROFILE_DEFAULT) — единственного, чьи смены режутся по отдыху.
PROFILE_DEFAULT = "DEFAULT"


class SolutionExportError(RuntimeError):
    """Выгрузка невозможна — с названной причиной, а не молча."""


def _dt(value: str) -> datetime:
    """Метка времени набора. Наивный UTC — канон реестра."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except (TypeError, ValueError) as e:
        raise SolutionExportError(
            "нечитаемая метка времени %r: %s" % (value, e)) from e


def _flight_times(payload: dict) -> dict:
    """Личность рейса -> (вылет, прилёт) из набора задачи."""
    out: dict[str, tuple[str, str]] = {}
    for f in payload.get("flights") or []:
        fn = f.get("flightNumber")
        dep, arr = f.get("departureUTCDateTime"), f.get("arrivalUTCDateTime")
        if fn and dep and arr:
            out[str(fn)] = (str(dep), str(arr))
    return out


def assignments_of(payload: dict, solved: dict) -> list[dict]:
    """Назначения решения во входном контракте сертификатора.

    Незанятые места пропускаются: сертификатор проверяет то, что
    назначено. Их число не теряется — оно видно в отчёте покрытия.
    """
    times = _flight_times(payload)
    arr_airport, dep_airport = {}, {}
    for f in payload.get("flights") or []:
        fn = f.get("flightNumber")
        for store, key in ((arr_airport, "arrivalAirport"), (dep_airport, "departureAirport")):
            ap = f.get(key)
            code = ap.get("code") if isinstance(ap, dict) else ap
            if fn and code:
                store[str(fn)] = str(code)
    rows: list[dict] = []
    for a in solved.get("flightAssignments") or []:
        emp = a.get("employee")
        if not emp:
            continue
        # Сотрудник приезжает объектом либо ссылкой по идентификатору.
        emp_id = emp.get("id") if isinstance(emp, dict) else emp
        flight = a.get("flight")
        flight_id = flight.get("flightNumber") if isinstance(flight, dict) \
            else flight
        pair = times.get(str(flight_id))
        if pair is None:
            raise SolutionExportError(
                "назначение ссылается на рейс %r, которого нет в наборе"
                % (flight_id,))
        # Аэропорты и роль — необязательные поля контракта сертификатора,
        # но без них его проверки стыковок в базе, отдыха дома и состава
        # экипажа ложатся «не прогнана: нет данных». Они у нас есть.
        row = {
            "employee": str(emp_id),
            "flight": str(flight_id),
            "dep_time": pair[0],
            "arr_time": pair[1],
            "dep_airport": dep_airport.get(str(flight_id)),
            "arr_airport": arr_airport.get(str(flight_id)),
            "role": a.get("requiredSkill"),
        }
        if a.get("chainId") is not None:
            row["pairing_id"] = a["chainId"]
        # Перегон экипажа (этап Г): место пассажира — зачёт по позициям.
        # Флаг едет в строку ЯВНО: потребитель обязан не считать перегон
        # в налёт и посадки, а в смены и отдых — считать; молчание тут
        # превратило бы пассажира в пилота.
        if a.get("deadhead"):
            row["deadhead"] = True
        rows.append(row)
    return rows


def pairings_of(payload: dict, solved: dict) -> list[dict] | None:
    """Export native chain/slot-layer boundaries; never infer chains from rest.

    No labels means no pairing evidence. Once labels are present, every
    operating seat and assigned positioning seat must be labelled and assigned;
    a native chain/slot layer cannot silently be split between employees.
    """
    seats = solved.get("flightAssignments") or []
    if not any(a.get("chainId") is not None for a in seats):
        return None
    times = _flight_times(payload)
    groups: dict[str, dict] = {}
    operating_ids: list[str] = []
    for a in seats:
        if a.get("deadhead") and not a.get("employee"):
            continue
        cid = a.get("chainId")
        emp = a.get("employee")
        emp = emp.get("id") if isinstance(emp, dict) else emp
        if not isinstance(cid, str) or not cid.strip() or emp is None or not str(emp).strip():
            raise SolutionExportError("неполная разметка цепочек: нужны chainId и сотрудник у каждого места")
        flight = a.get("flight")
        fid = flight.get("flightNumber") if isinstance(flight, dict) else flight
        pair = times.get(str(fid))
        if pair is None:
            raise SolutionExportError("цепочка ссылается на рейс вне исходного набора: %r" % fid)
        start, end = _dt(pair[0]), _dt(pair[1])
        if end <= start:
            raise SolutionExportError("цепочка содержит неверный интервал рейса: %r" % fid)
        if not a.get("deadhead"):
            operating_ids.append(str(a.get("crewSlotId") or a.get("id") or ""))
        group = groups.setdefault(cid, {"employee": str(emp), "id": cid,
                                        "start": start, "end": end})
        if group["employee"] != str(emp):
            raise SolutionExportError("цепочка %r разделена между сотрудниками" % cid)
        group["start"] = min(group["start"], start)
        group["end"] = max(group["end"], end)
    source_seats = payload.get("flightAssignments")
    if isinstance(source_seats, list):
        expected = [str(a.get("crewSlotId") or a.get("id") or "")
                    for a in source_seats if not a.get("deadhead")]
        if (not expected or "" in expected or "" in operating_ids
                or len(set(expected)) != len(expected)
                or len(set(operating_ids)) != len(operating_ids)
                or set(expected) != set(operating_ids)):
            raise SolutionExportError("разметка цепочек не покрывает исходные рабочие места точно")
    return [{**g, "start": g["start"].isoformat(), "end": g["end"].isoformat()}
            for _, g in sorted(groups.items())]


def _leg_key(leg: dict):
    """Полный ключ порядка рейсов — тот же, что у решателя (duties()).

    Только по вылету нельзя: при равном вылете «последний рейс» (а с ним
    место отдыха и порог) зависел бы от порядка входа, и стороны могли
    бы нарезать смены по-разному на одном решении.
    """
    return (_dt(leg["dep_time"]), leg.get("dep_airport") or "",
            _dt(leg["arr_time"]), leg.get("arr_airport") or "",
            str(leg.get("flight") or ""))


def duties_from_assignments(assignments: list[dict], min_rest_min,
                            home_of: dict | None = None,
                            coverage: dict | None = None) -> list[dict]:
    """Сегментация назначений на смены по порогу отдыха — В ТОМ МЕСТЕ.

    Разрыв между прилётом предыдущего рейса и вылетом следующего у
    ОДНОГО человека: меньше порога — тот же наряд, не меньше — новая
    смена. Порог зависит от места отдыха: дома (аэропорт прилёта равен
    базе приписки) или вне базы. Ровно так режет решатель — две ручки.

    `min_rest_min` — {"home": N, "outstation": M} либо одно число (обе
    области одним порогом — для тестов и одноручечных входов). `home_of`
    — сотрудник -> код базы приписки. Сотрудник без базы или рейс без
    аэропорта прилёта судится внебазовым порогом — это правило решателя
    (atHome), а не умолчание, и оно СЧИТАЕТСЯ: `coverage` (если передан)
    получает число разрывов, доставшихся каждой ручке, и сотрудников
    без базы. Два РАЗНЫХ порога при базах, не переданных ни для кого, —
    отказ: домашняя ручка не применилась бы ни к одному разрыву, а
    отчёт показал бы её «применённой» — число на пустом множестве.
    """
    if isinstance(min_rest_min, dict):
        home = min_rest_min.get("home")
        out = min_rest_min.get("outstation")
        if not home_of and home != out:
            raise SolutionExportError(
                "два разных порога отдыха (дома %r, вне базы %r), а базы "
                "приписки не переданы ни для кого: домашняя ручка не "
                "применилась бы ни к одному разрыву" % (home, out))
    else:
        home = out = min_rest_min
    if coverage is None:
        coverage = {}
    coverage.update({"home": 0, "outstation": 0, "no_base": [],
                     "no_arrival_airport": 0})
    for label, v in (("дома", home), ("вне базы", out)):
        if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
            raise SolutionExportError(
                "порог отдыха %s обязан быть положительным числом минут, "
                "получено %r; подставлять умолчание здесь нельзя — смены "
                "разъедутся с ростером" % (label, v))
    by_emp: dict[str, list[dict]] = {}
    for a in assignments:
        by_emp.setdefault(a["employee"], []).append(a)
    duties: list[dict] = []
    for emp in sorted(by_emp):
        legs = sorted(by_emp[emp], key=_leg_key)
        base = (home_of or {}).get(emp)
        if not base and len(legs) > 1:
            coverage["no_base"].append(emp)
        n = 1
        duty_id = "%s/D%d" % (emp, n)
        start = _dt(legs[0]["dep_time"])
        end = _dt(legs[0]["arr_time"])
        legs[0]["duty_id"] = duty_id
        last_arr_airport = legs[0].get("arr_airport")
        for leg in legs[1:]:
            dep, arr = _dt(leg["dep_time"]), _dt(leg["arr_time"])
            if not last_arr_airport:
                coverage["no_arrival_airport"] += 1
            at_home = bool(base) and last_arr_airport == base
            coverage["home" if at_home else "outstation"] += 1
            need = timedelta(minutes=home if at_home else out)
            if dep - end >= need:
                duties.append(_duty(duty_id, emp, start, end, base, last_arr_airport))
                n += 1
                duty_id = "%s/D%d" % (emp, n)
                start = dep
            # Привязка назначения к смене — чтобы сертификатор считал
            # состав НА СМЕНУ и посадки ЗА СМЕНУ той же нарезкой, что и мы.
            leg["duty_id"] = duty_id
            end = max(end, arr)
            last_arr_airport = leg.get("arr_airport")
        duties.append(_duty(duty_id, emp, start, end, base, last_arr_airport))
    return duties


def _duty(duty_id, emp, start, end, base, last_arr_airport) -> dict:
    """Смена с местом отдыха после неё: аэропорт прилёта последнего рейса
    и база приписки — по ним сертификатор судит отдых дома / вне дома."""
    d = {"id": duty_id, "employee": emp,
         "start": start.isoformat(), "end": end.isoformat()}
    if base:
        d["base"] = base
    if last_arr_airport:
        d["last_arr_airport"] = last_arr_airport
    return d


def homes_of(payload: dict) -> dict:
    """Сотрудник -> код базы приписки из набора задачи."""
    out: dict = {}
    for e in payload.get("employees") or []:
        eid = e.get("id")
        home = e.get("homeAirport")
        code = home.get("code") if isinstance(home, dict) else home
        if eid and code:
            out[str(eid)] = str(code)
    return out


def certifier_input(payload: dict, solved: dict, min_rest_min,
                    name: str = "решение COSMOS",
                    coverage: dict | None = None,
                    solver_layover_cost: dict | None = None) -> dict:
    """Полный вход сертификатора: назначения плюс реальные смены.

    `coverage` (если передан) получает покрытие ручек отдыха — см.
    [duties_from_assignments]; во вход сертификатора оно не входит (у
    него закрытый перечень полей), а идёт в шапку прогона рядом.
    `solver_layover_cost` — отчёт цены ночёвок решателя ЭТОГО прогона
    явным параметром от сборщика входа (webapp.certify_input, H11-WIRE-1);
    без него — из решения `solved.layoverCost`, как прежде.
    """
    rows = assignments_of(payload, solved)
    homes = homes_of(payload)
    duties = duties_from_assignments(rows, min_rest_min, homes, coverage)
    # Evidence belongs to one employee and the exact native-clock interval,
    # not to a rule, a future duty, or an interval contained in another one.
    # Payload labels keep their shared declared scale; no separate UTC shift.
    from webapp.task_package import duty_consent_index, duty_consent_time
    consents = duty_consent_index(payload.get("employees") or [], error=SolutionExportError)
    if consents:
        for row in rows:
            for name in ("dep_time", "arr_time"):
                duty_consent_time(row[name], where=f"{row['flight']}.{name}", error=SolutionExportError)
        for duty in duties:
            key = (duty["employee"],
                   duty_consent_time(duty["start"], where="duty.start", error=SolutionExportError),
                   duty_consent_time(duty["end"], where="duty.end", error=SolutionExportError))
            fact = consents.get(key)
            if fact is not None:
                duty["extension_consent"] = fact["consent"]
                duty["extension_consent_source"] = fact["source"]
    for r in rows:
        # База приписки — без неё «отдых дома / вне дома» и «принадлежность
        # базе» сертификатор проверить не может: аэропорты рейсов не
        # говорят, где человек живёт.
        r["base"] = homes.get(r["employee"])
    clean = [{k: v for k, v in r.items() if v is not None} for r in rows]
    out = {"solution": name, "assignments": clean, "duties": duties}
    # Патч GPT 05.09.2026 (КН0-DEMO-CHAIN-EXPORT-FIX): границы цепочек
    # решателя (chainId) едут листом «Цепочки» — форма «суток пейринга»
    # сертификатора судит по ним, а не выводит цепочки из отдыха.
    pairings = pairings_of(payload, solved)
    if pairings is not None:
        out["pairings"] = pairings
    # Единая версия 06.09.2026 (W-9/W-10; источник — контракт Б GPT,
    # Test legion 05.09): (1) объявленный плановый горизонт пакета —
    # НЕ размах назначений (None = «не объявлен», честно, не вывод);
    # (2) полный перечень сотрудников с базой, включая людей без
    # назначений, — область учёта ростеринга; (3) контекст цены:
    # задача, контракт, незанятые обязательные места с рейсом и временем;
    # (4) интервалы присутствия из истории (K-1: читатели судят по
    # каноническим интервалам, одна станция на интервал); (5) цена
    # ночёвок решателя как есть. Читатели — сертификатор и сверка
    # цены (группы H11/H13/H14); history/history_policy/history_calendar
    # ниже сохраняются без изменений.
    out["window_horizon"] = declared_window(payload)
    out["window_employees"] = [
        {"employee": str(e.get("id") or ""),
         "base": homes.get(str(e.get("id") or ""))}
        for e in payload.get("employees") or [] if isinstance(e, dict)]
    flight_by_id = {str(f.get("flightNumber")): f for f in payload.get("flights") or []
                    if isinstance(f, dict)}
    unassigned = []
    for seat in solved.get("flightAssignments") or []:
        if not isinstance(seat, dict) or seat.get("employee") or seat.get("deadhead"):
            continue
        flight = seat.get("flight")
        fid = flight.get("flightNumber") if isinstance(flight, dict) else flight
        rec = flight_by_id.get(str(fid)) or {}
        unassigned.append({"crewSlotId": seat.get("crewSlotId") or seat.get("id"),
                           "flight": fid, "employee": None, "station": None,
                           "start": rec.get("departureUTCDateTime"),
                           "end": rec.get("arrivalUTCDateTime")})
    out["cost_context"] = {
        "task": "rostering", "contract": "B",
        "tz_offset_min": 0, "boundary": {"mode": "unknown"},
        "home_source": "payload.employees[].homeAirport",
        "unassigned_required_slots": unassigned,
    }
    out["cost_history_intervals"] = [
        {"employee": str(e.get("id") or ""), **interval}
        for e in payload.get("employees") or [] if isinstance(e, dict)
        for interval in (e.get("history") or {}).get("historyLocationIntervals") or []
        if isinstance(interval, dict)]
    # H11-WIRE-1 (рецензия GPT-024, 06.09.2026): отчёт ночёвок — явным
    # параметром сборщика (решение, иначе показания того же runId);
    # без параметра — из решения. Нет нигде — поля нет (честно).
    layover = (solver_layover_cost if isinstance(solver_layover_cost, dict)
               else solved.get("layoverCost"))
    if isinstance(layover, dict):
        out["solver_layover_cost"] = layover
    # Ревизия 1.17 (ступень L): история сотрудника — строками для листа
    # «История» сертификатора (остатки на начало периода, последняя
    # смена, отдых до горизонта, серии); сотрудник без истории —
    # строка с пустыми полями и признаком «нет данных».
    hist_rows = history_rows(payload)
    if hist_rows is not None:
        out["history"] = hist_rows
        # Политика окон через границу горизонта (семантика раздела
        # history 1.17.2, согласована с CDPM 05.09.2026): ключ — флаги
        # раздела и отсутствие записи, а не строка «нет». continue_zero /
        # assume_zero_when_missing → сотрудники без записи судятся по
        # допущению нулевых остатков; continue_partial либо решения нет
        # → их окна через границу не судятся (третий исход).
        hist = payload.get("history") or {}
        cov = hist.get("coverage") if isinstance(hist.get("coverage"), dict) else {}
        out["history_policy"] = {
            # Срез раздела — сертификатор судит месяц среза у сотрудников
            # без записи по допущению нулевых остатков (continue_zero).
            "as_of": hist.get("as_of"),
            "coverage_kind": cov.get("kind"),
            "assume_zero_when_missing": bool(hist.get("assume_zero_when_missing")),
            "user_decision": (hist.get("user_decision")
                              if isinstance(hist.get("user_decision"), dict) else None),
            "employees_without_history": list(hist.get("employees_without_history") or [])}
        # Ревизия 1.17.2: посуточный календарь до горизонта — лист
        # «Календарь истории» (строка на сотрудника и дату); без
        # календаря листа нет — окна через границу не судятся.
        cal_rows = history_calendar_rows(payload)
        if cal_rows:
            out["history_calendar"] = cal_rows
    return out


def declared_window(payload: dict) -> dict | None:
    """Объявленный плановый горизонт набора — копией, никогда выводом из
    назначений (единая версия 06.09.2026, W-10; C-27b плана: источник —
    manifest.horizon, у набора решателя — planSince/planUntil).

    Явный `payload.window_horizon` — как есть. Иначе — из
    `_coverage.plan_since/plan_until` набора (task_package.to_payload):
    planUntil включительный с точностью до минуты (23:59) → полуоткрытое
    окно [start, end_exclusive) получается прибавлением ОДНОЙ минуты, а
    не выдуманных суток; горизонт не из целых суток либо отсутствующий —
    None (явно «не объявлен», не подстановка). Прилёт за правой границей
    календарь и резерв не расширяет.
    """
    raw = payload.get("window_horizon")
    if isinstance(raw, dict):
        return dict(raw)
    cov = payload.get("_coverage") or {}
    try:
        start = datetime.fromisoformat(str(cov.get("plan_since") or ""))
        end = datetime.fromisoformat(str(cov.get("plan_until") or "")) + timedelta(minutes=1)
    except (ValueError, TypeError):
        return None
    if start >= end or start.time() != datetime.min.time() or end.time() != datetime.min.time():
        return None
    return {"start_inclusive": start.isoformat(), "end_exclusive": end.isoformat(),
            "source": "package data properties planSince/planUntil (inclusive minute)"}


def history_calendar_rows(payload: dict) -> list[dict]:
    """Строки листа «Календарь истории» сертификатора из набора решателя
    (employees[].history.historyCalendar, ревизия 1.17.2): табельный,
    дата, вид (work|reserve|day_off|leave|sick|unknown), ночная."""
    rows: list[dict] = []
    for e in payload.get("employees") or []:
        if not isinstance(e, dict) or not isinstance(e.get("history"), dict):
            continue
        for d in e["history"].get("historyCalendar") or []:
            if not isinstance(d, dict) or not d.get("date"):
                continue
            rows.append({"employee": str(e.get("id") or ""),
                         "date": str(d.get("date")),
                         "kind": str(d.get("kind") or "unknown"),
                         "night": d.get("night")})
    return rows


def history_rows(payload: dict) -> list[dict] | None:
    """Строки истории сотрудников из набора решателя (employees[].history
    ревизии 1.17); None — раздела в наборе нет."""
    if not isinstance(payload.get("history"), dict):
        return None
    rows: list[dict] = []
    for e in payload.get("employees") or []:
        if not isinstance(e, dict):
            continue
        h = e.get("history")
        row = {"employee": str(e.get("id") or ""),
               "as_of": e.get("historyAsOf") or payload["history"].get("as_of"),
               "has_history": isinstance(h, dict)}
        if isinstance(h, dict):
            row.update({
                "month_block_min": h.get("historyMonthBlockMin"),
                "month_work_min": h.get("historyMonthWorkMin"),
                "month_flight_time_min": h.get("historyMonthFlightTimeMin"),
                "month_days_worked": h.get("historyMonthDaysWorked"),
                "month_landings": h.get("historyMonthLandings"),
                "last_duty_end": h.get("historyLastDutyEnd"),
                "last_duty_station": h.get("historyLastDutyStation"),
                "last_duty_night": h.get("historyLastDutyNight"),
                "rest_before_horizon_min": h.get("historyRestBeforeHorizonMin"),
                "consecutive_duty_days": h.get("historyConsecutiveDutyDays"),
                "consecutive_night_duties": h.get("historyConsecutiveNightDuties"),
                "days_since_day_off": h.get("historyDaysSinceDayOff"),
                "location": h.get("historyLocation")})
        rows.append(row)
    return rows


def min_rest_from_applied(applied: dict):
    """Порог отдыха ТОГО ПРОФИЛЯ, которым прогон считал на самом деле.

    ДЕФЕКТ, ради которого функция переписана (найден 2026-08-22, после
    сдачи этапа Б). Прежняя редакция читала `applied` — показания ЯРУСА
    правил (`RulesConfig`). Но профиль ограничений по умолчанию, которым
    и идут прогоны по пакету постановки, ярус НЕ ЧИТАЕТ ВОВСЕ: его пороги
    литеральны, и отдых там 480 минут, а не 600. Ярусные 600 попадали в
    отчёт от ДРУГИХ профилей, сконструированных при старте JVM. Смены
    резались по 600 при прогоне, считавшем по 480, — и разошлись молча:
    оба числа правдоподобны, оба «из отчёта решателя».

    Теперь читается `lastRun.profileReadings` — показания профиля,
    которым прогон СЧИТАЛ, под теми же именами независимо от профиля.
    Нет блока — отказ: угадывать, чем считали, нельзя.

    Второй дефект того же класса (разбор ревизии 3): для профилей GERAD
    функция отдавала ярусный `min_rest_min` как порог нарезки, тогда как
    GERAD режет смены по календарной дате вылета, а GERAD2P смен не
    имеет. Смены выгрузки не совпали бы с теми, по которым считал
    ростер, — молча. Теперь не профиль по умолчанию — отказ с причиной.
    """
    run = (applied or {}).get("lastRun") or {}
    readings = run.get("profileReadings")
    if not isinstance(readings, dict) or not readings:
        raise SolutionExportError(
            "в отчёте применённого нет profileReadings — неизвестно, каким "
            "профилем считал прогон и по какому порогу он резал смены. "
            "Брать показания яруса правил нельзя: профиль по умолчанию их "
            "не читает")
    prof = run.get("rulesProfile")
    if prof != PROFILE_DEFAULT:
        raise SolutionExportError(
            "профиль %r режет смены не по порогу отдыха (GERAD — по "
            "календарной дате вылета, GERAD2P смен не имеет): выгрузка "
            "смен по отдыху для него неверна; сертифицировать можно только "
            "прогон профилем по умолчанию" % (prof,))

    def _pos(key):
        v = readings.get(key)
        try:
            got = int(v)
        except (TypeError, ValueError) as e:
            raise SolutionExportError(
                "нечитаемый %s %r у профиля %r" % (key, v, prof)) from e
        if got <= 0:
            raise SolutionExportError(
                "%s профиля %r равен %d — таким порогом смены не режут"
                % (key, prof, got))
        return got

    # Профиль по умолчанию — две ручки отдыха, обе обязательны.
    if "min_rest_home_min" not in readings and "min_rest_outstation_min" not in readings:
        raise SolutionExportError(
            "профиль %r не сообщил порог отдыха — взять неоткуда" % (prof,))
    if "min_rest_home_min" not in readings or "min_rest_outstation_min" not in readings:
        raise SolutionExportError(
            "профиль %r сообщил только одну из двух ручек отдыха — "
            "резать смены по половине правила нельзя" % (prof,))
    return {"home": _pos("min_rest_home_min"),
            "outstation": _pos("min_rest_outstation_min")}
