# -*- coding: utf-8 -*-
"""Приём данных авиакомпании: выгрузка -> канонический набор COSMOS.

Модуль контура заказчика, а не разовый скрипт: тем же кодом пользуется
и веб-приложение, и командная строка, поэтому расхождение между тем, что
загрузили через браузер, и тем, что посчитали вручную, невозможно.

ГЛАВНОЕ ИСПРАВЛЕНИЕ (2026-08-11). Из расписания исключаются участки с
идентификатором `dh%…`. Это перегоны ИЗ РЕШЕНИЯ ИНТЕГРАТОРА, и у каждого
из них (в наборе их сотни) есть оригинал с тем же маршрутом, временем и
датой. Прежде они попадали в расписание наравне с настоящими рейсами, и
расчёт покрывал один физический рейс дважды: задача была раздута на
сотни фантомных рейсов.

Причина ошибки поучительна. Сначала они исключались — верно, но по
неверному основанию (признак `deadhead`). Затем основание было признано
негодным («разметка интегратора, которую велено игнорировать») и вместе
с ним отменён верный вывод. Исключать их нужно не из-за флага, а потому
что это ДУБЛИКАТЫ. Поэтому здесь стоит не флаг, а проверка на совпадение
маршрута и времени — она ловит класс, а не конкретный случай.

ИСПРАВЛЕНИЕ 2026-08-12: расхождения имён на шве «приёмник — решатель».
Проверка фактом (минимальный набор + живой решатель этапа 2) показала:
решатель ПРИМЕНЯЕТ персональные ограничения, но только из полей с
каноническими именами. Приёмник же писал три условия в поля, которых
не читает никто:

  * запреты станций уезжали в `undesiredAirports` (мягкое предпочтение,
    решателем не читается) вместо исполняемого `stationsDenied` — у
    сотен сотрудников терялись живые запреты станций, присутствующих
    в расписании месяца;
  * допуски со сроками уезжали в `qualifications` (форма календаря)
    без исполняемой формы `qualificationWindows` — решатель не видел
    ни одного окна;
  * требования участков уезжали в `requirements`, которое не читает
    ни построитель цепочек (ждёт `requiredQualifications`), ни кто-либо
    ещё — каталог выпускал пустые `requiredCodesAll`.

Правило то же, что и с дубликатами: условие, попавшее не в то поле, не
вызывает ошибки — оно просто не действует, и результат неотличим от
верного. Поэтому канонические имена здесь зеркалят контракт вендорского
адаптера `_solver_to_payload` (единственный другой писатель этих полей),
а отчёт приёма считает перенесённые и отброшенные записи по классам.
"""
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
                r"([+-]\d{2}):(\d{2})(?:\[([^\]]+)\])?$")


class IngestError(ValueError):
    """Данные непригодны. Отказ громкий: молчаливый пропуск участка
    означал бы расчёт по неполному расписанию."""


# Цены цели (решение владельца 2026-08-12, «перенос цен до ростеринга»).
#
# Сторожевое число «неизвестная станция» у интегратора: его настройки
# несут unknownStationCostThreshold = 1 000 000, и ставка такой станции
# ставкой не является — в стоимость она не входит (иначе сторожевое
# число стало бы запретом летать туда), станция помечается
# layoverCostKnown=false — то же поведение, что у вендорского адаптера.
UNKNOWN_STATION_COST = 1_000_000

# Носитель норм контура данных: нормы состава кабинного экипажа по типам
# воздушного судна и (опционально) тариф-допущение перегона без цены.
# Это данные конкретной авиакомпании, а не свойства механизма, поэтому в
# коде их нет: приёмник без носителя отказывает словами, а не считает
# чужими числами. Тариф-допущение — цена рейсов, которым интегратор цену
# не назначил; без него такой рейс падал бы на запасной вес решателя —
# многократно «дешевле» настоящих цен, и решатель предпочитал бы именно
# такие перегоны. Ответ авиакомпании заменяет допущение нормой.
NORMS_FILE = "airline-ingest-norms.json"
NORMS_ENV = "AIRLINE_INGEST_NORMS"
_NORMS_FORM = "form_version/customer/crew_norms[/dh_tariff_assumed]"


def load_norms(path=None):
    """Нормы контура: env AIRLINE_INGEST_NORMS (пустое значение =
    «носителя нет», непустое = явный путь), иначе
    <корень данных>/airline-ingest-norms.json (см. _data_paths.data_root:
    перенос корня переменной CREW_DATA_DIR переносит и носитель).

    Нет файла или файл кривой — IngestError словами, до любого счёта:
    нормы состава приёмнику ОБЯЗАТЕЛЬНЫ (в отличие от носителей-сонь
    вроде контрольных пар, чьё отсутствие лишь усыпляет механизм)."""
    if path is None:
        override = os.environ.get(NORMS_ENV)
        if override is not None:
            if not override.strip():
                raise IngestError(
                    "носитель норм контура не задан (переменная %s "
                    "пуста); приёмник выгрузки авиакомпании без норм "
                    "состава не работает; форма файла: %s"
                    % (NORMS_ENV, _NORMS_FORM))
            path = Path(override)
        else:
            import _data_paths
            path = _data_paths.data_root() / NORMS_FILE
    p = Path(path)
    if not p.is_file():
        raise IngestError(
            "носитель норм контура не найден: %s; приёмник выгрузки "
            "авиакомпании без норм состава не работает; форма файла: %s"
            % (p, _NORMS_FORM))
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IngestError("носитель норм контура %s не разобран как "
                          "JSON: %s" % (p, exc)) from exc
    if not isinstance(doc, dict) or not doc.get("customer") \
            or not isinstance(doc.get("form_version"), int) \
            or isinstance(doc.get("form_version"), bool):
        raise IngestError(
            "носитель норм контура %s без обязательных полей customer "
            "и form_version — форма конвенции 2026-08-29: %s"
            % (p, _NORMS_FORM))
    crew_raw = doc.get("crew_norms")
    if not isinstance(crew_raw, dict) or not crew_raw:
        raise IngestError(
            "носитель норм контура %s: crew_norms должен быть непустым "
            "словарём «тип судна -> список пар [роль, число мест]»" % p)
    crew = {}
    for ac, spec in crew_raw.items():
        if not isinstance(ac, str) or not ac.strip() \
                or not isinstance(spec, list) or not spec:
            raise IngestError(
                "носитель норм контура %s: тип %r — норма должна быть "
                "непустым списком пар [роль, число мест]" % (p, ac))
        pairs = []
        for pair in spec:
            role, n = (tuple(pair)
                       if isinstance(pair, (list, tuple))
                       and len(pair) == 2 else (None, None))
            if not isinstance(role, str) or not role.strip() \
                    or not isinstance(n, int) or isinstance(n, bool) \
                    or n <= 0:
                raise IngestError(
                    "носитель норм контура %s: тип %r — каждая пара "
                    "это [непустая роль, положительное целое число "
                    "мест], получено %r" % (p, ac, pair))
            pairs.append((role, n))
        crew[ac] = tuple(pairs)
    tariff = doc.get("dh_tariff_assumed")
    if tariff is not None and (isinstance(tariff, bool)
                               or not isinstance(tariff, (int, float))
                               or tariff <= 0):
        raise IngestError(
            "носитель норм контура %s: dh_tariff_assumed задан, но не "
            "положительное число: %r" % (p, tariff))
    return {"customer": doc["customer"], "crew_norms": crew,
            "dh_tariff_assumed": tariff, "path": str(p)}


def to_utc(s):
    """Время по смещению ИЗ САМОЙ МЕТКИ, а не по имени зоны."""
    m = TS.match(s or "")
    if not m:
        return None, None
    base, oh, om, zone = m.groups()
    dt = datetime.fromisoformat(base)
    sign = 1 if int(oh) >= 0 else -1
    off = timedelta(hours=int(oh), minutes=int(om) * sign)
    return (dt - off).isoformat(timespec="seconds"), zone


_EPOCH0 = datetime(1970, 1, 1)
# Открытая верхняя граница окна — то же значение, что у вендорского
# адаптера (_solver_to_payload): решатель сравнивает Long-минуты.
_OPEN_HI = 4_000_000_000 // 60


def _epoch_min(s):
    """Минуты эпохи UTC из метки источника; None, если не разобрана.

    Источник пишет и смещение базы вида `+HH:MM[Zone]`, и `Z[UTC]` —
    вторая форма не подходит под TS, поэтому Z приводится к смещению
    до разбора."""
    if not isinstance(s, str) or not s:
        return None
    utc, _zone = to_utc(s.replace("Z[", "+00:00[")
                        .replace("Z", "+00:00", 1)
                        if "Z" in s else s)
    if not utc:
        return None
    return int((datetime.fromisoformat(utc) - _EPOCH0).total_seconds() // 60)


def _active_codes(items, h_lo, h_hi, stats):
    """Коды записей «код + срок действия» либо строк, действующие в
    горизонте [h_lo, h_hi) (минуты эпохи).

    Запись с окном, не пересекающим горизонт, отбрасывается СО СЧЁТОМ
    (stats["вне срока"]): молчаливое ужесточение так же недопустимо, как
    молчаливая потеря. Голая строка — код без срока, действует всегда."""
    out = []
    for x in items or ():
        if isinstance(x, dict):
            code = x.get("code") or x.get("station") or x.get("id")
            if code is None:
                continue
            lo = _epoch_min(x.get("activeSince"))
            hi = _epoch_min(x.get("activeUntil"))
            if lo is not None and h_hi is not None and lo >= h_hi:
                stats["вне срока"] = stats.get("вне срока", 0) + 1
                continue
            if hi is not None and h_lo is not None and hi <= h_lo:
                stats["вне срока"] = stats.get("вне срока", 0) + 1
                continue
            out.append(str(code))
        elif x is not None:
            out.append(str(x))
    return out


def crew_for(ac, norms):
    """Норма состава по типу судна из таблицы носителя. Совпадение — по
    самому ДЛИННОМУ подходящему префиксу (общее правило сортировки, а не
    зашитый порядок: у типов вида B787-9/B787 длинный обязан судиться
    раньше короткого, иначе применилась бы чужая норма МОЛЧА). Зачистка
    суффикса "(*)" — нормализация формата выгрузки, механизм."""
    a = (ac or "").strip().replace("(*)", "").strip()
    for prefix in sorted(norms, key=len, reverse=True):
        if a.startswith(prefix):
            return norms[prefix]
    return None


def is_solution_artifact(leg_id):
    """Участок принадлежит чужому РЕШЕНИЮ, а не расписанию."""
    return str(leg_id or "").startswith("dh%")


def convert(raw, month=None):
    """Выгрузка авиакомпании -> (канонический набор, отчёт)."""
    # Носитель норм контура грузится ДО любого счёта: отказ по данным
    # обязан случиться раньше, чем начнётся разбор выгрузки.
    norms = load_norms()
    crew_norms = norms["crew_norms"]
    dh_tariff = norms["dh_tariff_assumed"]
    try:
        d = json.loads(raw.decode("utf-8-sig")
                       if isinstance(raw, (bytes, bytearray)) else raw)
    except ValueError as exc:
        raise IngestError("файл не разобран как JSON: %s" % exc) from exc
    if not isinstance(d, dict) or "legs" not in d:
        raise IngestError(
            "это не выгрузка авиакомпании: нет раздела legs. Канонический "
            "набор COSMOS загружается как «канонический формат».")

    legs = d["legs"]
    if month is None:
        counts = Counter((x.get("start") or "")[:7] for x in legs
                         if x.get("start"))
        month = counts.most_common(1)[0][0] if counts else ""
    rep = {"legs_total": len(legs), "month": month}
    # Цены перегонов интегратора: строка `dh%<id>` несёт цену перегона
    # НА УЧАСТКЕ <id> (совпадение по идентификатору — 567 из 567 строк
    # выгрузки). Сами dh-строки из расписания отклоняются как дубликаты,
    # но их цена — данные цели и переносится оригиналу.
    dh_price = {str(x["id"])[3:]: int(x["cost"])
                for x in legs
                if is_solution_artifact(x.get("id"))
                and isinstance(x.get("cost"), (int, float))}
    rej = Counter()
    seen_addr: dict = {}
    flights, used_ap = [], set()
    for x in legs:
        if (x.get("start") or "")[:7] != month:
            rej["вне горизонта"] += 1
            continue
        if is_solution_artifact(x.get("id")):
            rej["перегон из решения интегратора"] += 1
            continue
        dep, zdep = to_utc(x.get("start"))
        arr, _ = to_utc(x.get("end"))
        if not dep or not arr:
            rej["время не разобрано"] += 1
            continue
        if not x.get("departureStation") or not x.get("arrivalStation"):
            rej["нет станции"] += 1
            continue
        # Адрес участка в каноническом наборе — flightNumber, и он
        # обязан быть УНИКАЛЬНЫМ. Конвенция COSMOS: «<номер>-<ГГГГММДД>-
        # <порядковый>»; из неё `_true_fn_of` восстанавливает
        # коммерческий номер, поэтому правила, судящие по номеру
        # (чартер, лоукостер, перечни рейсов), продолжают работать.
        # Голый код рейса за месяц повторяется у каждого ежедневного
        # рейса — такой набор проходит расчёт, но не проходит схему.
        code = str(x.get("flightCode") or "").strip()
        day = dep[:10].replace("-", "")
        seq = seen_addr[(code, day)] = seen_addr.get((code, day), 0) + 1
        addr = "%s-%s-%d" % (code, day, seq)
        # Цена перегона: своя (из dh-строки интегратора) либо
        # тариф-допущение из носителя норм; тариф не задан — рейсы
        # без цены пересчитываются после цикла и дают отказ словами.
        dh_cost = dh_price.get(str(x.get("id")))
        flights.append({
            "flightNumber": addr,
            "commercialFlightNumber": code,
            "departureAirport": x["departureStation"],
            "departureUTCDateTime": dep,
            "arrivalAirport": x["arrivalStation"],
            "arrivalUTCDateTime": arr,
            "aircraftType": x.get("acType"),
            "flightLegId": x.get("id"),
            "sourceInstanceId": x.get("id"),
            "allocable": bool(x.get("allocable")),
            "departureLocalZone": zdep,
            "deadheadCost": (dh_cost if dh_cost is not None
                             else dh_tariff),
            # Каноническое имя — requiredQualifications (семантика
            # «каждому члену экипажа», как у вендорского адаптера):
            # его читают построитель цепочек (requiredCodesAll каталога)
            # и валидатор паритета. Прежнее имя requirements не читал
            # никто — требование было заявлено и не действовало.
            "requiredQualifications": sorted(
                {str(r) for g in (x.get("requirements") or [])
                 for r in g if r}),
        })
        used_ap.add(x["departureStation"])
        used_ap.add(x["arrivalStation"])
    # Тариф-допущение опционален, но не молчалив: если он не задан, а
    # рейсы без цены интегратора есть, конверсия отказывает СО СЧЁТОМ.
    # Не задан и рейсов без цены нет — ветка спит, все цены из данных.
    if dh_tariff is None:
        no_price = sum(1 for f in flights if f["deadheadCost"] is None)
        if no_price:
            raise IngestError(
                "у %d рейсов нет цены перегона от интегратора, а "
                "тариф-допущение (dh_tariff_assumed) в носителе норм "
                "не задан — задайте поле или получите цены от "
                "интегратора" % no_price)
    rep["addresses"] = len({f["flightNumber"] for f in flights})
    rep["rejected"] = dict(rej)
    rep["flights"] = len(flights)

    # --- сотрудники и недоступность
    # Горизонт месяца в минутах эпохи — им решается, действует ли
    # датированная запись (запрет станции, несовместимость) в расчёте.
    if month:
        y, m = int(month[:4]), int(month[5:7])
        h_lo = int((datetime(y, m, 1) - _EPOCH0).total_seconds() // 60)
        nxt = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)
        h_hi = int((nxt - _EPOCH0).total_seconds() // 60)
    else:
        h_lo = h_hi = None
    # Коды, на которые ссылается СПРОС месяца, — только для них строятся
    # исполняемые окна допусков (окна кодов, которых никто не спрашивает,
    # решателю не нужны). Спрос двух видов:
    #   * требования РЕЙСОВ («Допуск СНГ», «Допуск МВЛ») — каждому члену
    #     экипажа участка;
    #   * требования МЕСТ «тип_роль» (например «A320  _CA») — их строит
    #     построитель цепочек из типа судна и роли места, и у сотрудников
    #     эти же коды лежат в допусках СО СРОКАМИ. Первый прогон
    #     ростеринга (2026-08-12) заблокировал все 7852 места именно
    #     потому, что окна строились только для требований рейсов, —
    #     тот же класс «условие не в том поле», третий случай.
    referenced = {q for f in flights for q in f["requiredQualifications"]}
    for f in flights:
        spec = crew_for(f["aircraftType"], crew_norms) or ()
        for role, _n in spec:
            referenced.add("%s_%s" % (f["aircraftType"], role))
    referenced = sorted(referenced)
    duty_by_id = {x["id"]: x for x in d.get("duties") or []}
    unavail = defaultdict(set)
    for a in d.get("employeeAssignments") or []:
        du = duty_by_id.get(a.get("duty"))
        if du and a.get("employee") and (du.get("ground") or du.get("sick")):
            s, _ = to_utc(du.get("end") or du.get("start") or "")
            if s:
                unavail[str(a["employee"])].add(s[:10])
    employees = []
    denied_stats: dict = {}
    dnf_stats: dict = {}
    windows_built = 0
    for e in d.get("employees") or []:
        ranks = e.get("ranks") or {}
        feats = {f.get("code") for f in (e.get("features") or [])}
        skills = sorted({v for v in ranks.values() if v and v != "<нет>"})
        rec = {
            "id": str(e["id"]), "name": str(e["id"]),
            "homeAirport": e.get("base"),
            "skills": skills,
            "typeRatings": sorted(ranks.keys()),
            "unavailableDays": sorted(unavail.get(str(e["id"]), ())),
            "isReserve": False,
            "gender": ("FEMALE" if "FEMALE" in feats
                       else "MALE" if "MALE" in feats else None),
            # Форма календаря соответствия (v2) — оставлена для экранов;
            # исполняемая форма для решателя — qualificationWindows ниже.
            "qualifications": [
                {"code": f.get("code"), "from": f.get("activeSince"),
                 "until": f.get("activeUntil")}
                for f in (e.get("features") or [])],
            # Запреты станций — исполняемое поле stationsDenied (жёсткое
            # правило решателя), а не undesiredAirports (предпочтение,
            # которое решатель не читает). Действующие в горизонте коды;
            # отброшенные по сроку считаются. Коды, не являющиеся
            # станциями (страновые «=XX», цифровые), сохраняются — их
            # молчаливый сброс был бы потерей условия; классы считает
            # отчёт ниже.
            "stationsDenied": _active_codes(
                e.get("stationsDenied"), h_lo, h_hi, denied_stats),
            "doNotFlyWith": _active_codes(
                e.get("doNotFlyWith"), h_lo, h_hi, dnf_stats),
            # Роль не выдумывается: сотрудник без ранга помечается
            # недоступным к назначению, а не наделяется чужой ролью.
            "allocable": bool(e.get("allocable")) and bool(skills),
            "excludedReason": (None if skills
                               else "ранг в источнике не задан"),
        }
        # Исполняемые окна допусков: только коды, на которые ссылаются
        # требования участков; минуты эпохи UTC; отсутствие границы —
        # открытое окно. Именно эту форму читает решатель
        # (Employee.qualificationWindows -> holdsQualification).
        windows: dict = {}
        for f in e.get("features") or []:
            code = str(f.get("code") or "")
            if code not in referenced:
                continue
            lo = _epoch_min(f.get("activeSince"))
            hi = _epoch_min(f.get("activeUntil"))
            lo = 0 if lo is None else lo
            hi = _OPEN_HI if hi is None else min(hi, _OPEN_HI)
            if hi > lo:
                windows.setdefault(code, []).append([lo, hi])
        if windows:
            for lst in windows.values():
                lst.sort()
            rec["qualificationWindows"] = windows
            windows_built += 1
        if not rec["stationsDenied"]:
            del rec["stationsDenied"]
        if not rec["doNotFlyWith"]:
            del rec["doNotFlyWith"]
        employees.append(rec)
    rep["employees"] = len(employees)
    rep["employees_allocable"] = sum(1 for e in employees if e["allocable"])
    # Отчёт о перенесённых ограничениях: сколько сотрудников несут
    # запреты, какие классы кодов среди запретов (жёсткому правилу
    # решателя сопоставимы только коды-станции), сколько окон допусков
    # построено и остались ли требования вовсе без носителей.
    denied_kinds: Counter = Counter()
    for e in employees:
        for c in e.get("stationsDenied") or ():
            if c.startswith("="):
                denied_kinds["страновой"] += 1
            elif c.isdigit():
                denied_kinds["цифровой"] += 1
            else:
                denied_kinds["станция"] += 1
    rep["stations_denied"] = {
        "сотрудников": sum(1 for e in employees
                           if e.get("stationsDenied")),
        **dict(denied_kinds), **denied_stats}
    rep["do_not_fly_with"] = {
        "сотрудников": sum(1 for e in employees if e.get("doNotFlyWith")),
        **dnf_stats}
    rep["qualification_windows"] = {
        "кодов в требованиях": len(referenced),
        "сотрудников с окнами": windows_built,
        "требования без носителей": sorted(
            set(referenced) - {c for e in employees
                               for c in (e.get("qualificationWindows")
                                         or {})}),
    }

    smap = {s["iata"]: s for s in (d.get("stations") or []) if s.get("iata")}
    airports, missing = [], []
    rates_known = rates_unknown = 0
    for code in sorted(used_ap):
        s = smap.get(code)
        if not s:
            missing.append(code)
            continue
        ap = {
            "code": code, "name": s.get("name") or code,
            "latitude": s.get("latitude"), "longitude": s.get("longitude"),
            "timeZone": s.get("timeZone"),
            "countryCode": s.get("countryCode"),
            "taxiTimeInMinutes": {}, "languages": ["ru"]}
        # Гостиничная ставка станции — в цель пейринга. Сторожевое число
        # «неизвестная станция» ставкой не является: known=false, и
        # стоимость такой стоянки считается отдельно, а не запрещает
        # летать туда (см. UNKNOWN_STATION_COST).
        cost = s.get("cost")
        if isinstance(cost, (int, float)) and cost < UNKNOWN_STATION_COST:
            ap["layoverCost"] = int(cost)
            ap["layoverCostKnown"] = True
            rates_known += 1
        else:
            ap["layoverCost"] = 0
            ap["layoverCostKnown"] = False
            rates_unknown += 1
        airports.append(ap)
    if missing:
        raise IngestError("аэропорты не найдены в справочнике: %s"
                          % ", ".join(sorted(missing)[:5]))
    rep["airports"] = len(airports)
    rep["цены"] = {
        "перегоны с ценой интегратора": sum(
            1 for f in flights if str(
                f.get("flightLegId")) in dh_price),
        "перегоны по тарифу-допущению": sum(
            1 for f in flights if str(
                f.get("flightLegId")) not in dh_price),
        "тариф-допущение": (dh_tariff if dh_tariff is not None
                            else "не задан (все цены из данных)"),
        "ставки станций известны": rates_known,
        "ставки неизвестны (сторожевое)": rates_unknown,
    }

    # --- спрос на экипаж по нормам состава
    seats, unknown = [], Counter()
    for f in flights:
        spec = crew_for(f["aircraftType"], crew_norms)
        if spec is None:
            unknown[f["aircraftType"]] += 1
            continue
        idx = 0
        for role, n in spec:
            for _ in range(n):
                # Место — самостоятельная сущность: у него собственный
                # идентификатор и позиция в составе. Без них набор
                # проходит расчёт, но не проходит схему — расхождение,
                # дожившее до первой загрузки через интерфейс.
                seats.append({
                    "id": "%s#%d" % (f["flightNumber"], idx),
                    "flight": f["flightNumber"],
                    "indexInFlight": idx,
                    "requiredSkill": role})
                idx += 1
    if unknown:
        raise IngestError("нет нормы состава для типов: %s"
                          % ", ".join(str(k) for k in unknown)[:120])
    rep["seats"] = len(seats)
    rep["seats_by_role"] = dict(Counter(s["requiredSkill"] for s in seats))
    # Роли носителя норм против словаря рангов выгрузки: роль, которой
    # не держит ни один сотрудник (включая рассинхрон написания вплоть
    # до гомоглифов), видна в отчёте — но расчёта НЕ запрещает (правило
    # «не ограничивать сверх ВМП»): непокрываемые места ниже по
    # конвейеру судит решатель, а не приёмник.
    held = {s for e in employees for s in e["skills"]}
    rep["роли норм без единого держателя ранга"] = sorted(
        {role for spec in crew_norms.values() for role, _n in spec}
        - held)

    payload = {"airports": airports, "employees": employees,
               "flights": flights, "flightAssignments": seats}
    check(payload, rep)
    return payload, rep


def check(payload, rep):
    """Гейт приёма. Каждая проверка сообщает, сколько рассмотрела."""
    fl = payload["flights"]
    if not fl:
        raise IngestError("в горизонте нет ни одного рейса")
    ids = [f["flightLegId"] for f in fl]
    if len(set(ids)) != len(ids):
        raise IngestError("идентификатор участка не уникален")
    nums = {str(f["flightNumber"]) for f in fl}
    cross = nums & set(ids)
    if cross:
        raise IngestError("номер рейса совпал с идентификатором другого: %s"
                          % sorted(cross)[:3])
    # Дубликаты по СУЩЕСТВУ: один физический рейс не может быть в
    # расписании дважды под разными идентификаторами. Именно этой
    # проверки не было, и сотни перегонов чужого решения прошли молча.
    key = Counter((f["departureAirport"], f["arrivalAirport"],
                   f["departureUTCDateTime"]) for f in fl)
    dup = [k for k, n in key.items() if n > 1]
    if dup:
        raise IngestError(
            "%d рейсов задвоены по маршруту и времени, например %s "
            "— в расписании один физический рейс не может стоять дважды"
            % (len(dup), dup[0]))
    bad_t = [f for f in fl
             if f["arrivalUTCDateTime"] <= f["departureUTCDateTime"]]
    if bad_t:
        raise IngestError("прилёт не позже вылета у %d рейсов" % len(bad_t))
    addrs = [f["flightNumber"] for f in fl]
    if len(set(addrs)) != len(addrs):
        raise IngestError("адрес рейса (flightNumber) не уникален")
    per = Counter(s["flight"] for s in payload["flightAssignments"])
    if set(per) != set(addrs):
        raise IngestError("места созданы не для всех рейсов: %d из %d"
                          % (len(per), len(ids)))
    rep["gate"] = {
        "рейсов": len(fl),
        "уникальных идентификаторов": len(set(ids)),
        "различных номеров": len(nums),
        "задвоенных по маршруту и времени": 0,
        "рейсов с местами": len(per),
    }
    return rep
