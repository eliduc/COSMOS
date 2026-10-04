# -*- coding: utf-8 -*-
"""Адаптеры стенда каталога шаблонов: мир задачи -> вход исполнителя.

Стендовая задача записи каталога несёт МИР (синтетические коды ST1…ST9,
T1…T9, P<n>, S1…S9) и ожидания по видам исполнителей. Судить исполнителя
можно только на его собственном входе, и форма этого входа у каждого вида
своя: построителю нужен `payload` и `argparse.Namespace`, сертификату —
свод правил и набор таблиц линии данных, решателю — задача
`FlightCrewSchedule`. Перевод мира в эти формы живёт ЗДЕСЬ, в одном месте:
исполнители не меняются, а стенд не заводит второго определения смены,
цепочки или свода.

Модуль продукта: из `tests/` он ничего не импортирует, обратное
допустимо. Числа мира — только из мира; своих порогов у адаптеров нет,
кроме СОБСТВЕННЫХ флагов стенда (`STAND_ARGS`), которые объявлены явно и
названы словами: это ручки стенда, а не параметры каталога.

Две разметки смен — намеренно разные и обе принадлежат исполнителям:

  * `chain_of` — нарезка ПОСТРОИТЕЛЯ: смена продолжается, пока пауза не
    короче порога стыковки (`sit_min`) и короче верха окна
    (`Builder.sit_cap_at`). Так строит смены `_pairing_builder.build_duties`;
  * `world_to_solution` — разметка РЕШЕНИЯ: смена продолжается, пока
    пауза короче верха окна стыковки; низ окна не применяется. Это та же
    граница «стыковка / отдых», которой пользуется сертификат, и короткая
    пауза остаётся стыковкой ВНУТРИ смены — иначе её нарушение не увидел
    бы ни один судья (`fit_checks._turnaround` судит только пары одной
    смены).

Правило «пауза короче пола отдыха — одна смена» здесь не годится: оно
склеило бы смены задачи о полах отдыха и спрятало бы находку «вне базы
короче пола».

Здесь же — таблица `WORLD_TO_PACKAGE_SECTION` (просьба Ж-5): какие
секции машинных форм пакета стенд умеет нести ИЗ МИРА и каким полем мира
каждая питается. Новая секция — строка таблицы, а не правка судей
стенда; семья вне таблицы, чей мир форму объявил, — отказ словами.
"""
from __future__ import annotations

import argparse
import copy
import json
from datetime import date, datetime, timedelta
from pathlib import Path


class StandWorldError(ValueError):
    """Мир задачи не переводится во вход исполнителя — причина словами."""


# --------------------------------------------------------------- флаги стенда

#: Собственный набор флагов построителя. Перечень — ключи `argparse`
#: `_pairing_builder.main`; значения таблицы отдыха ЯВНЫЕ (гейт
#: `Builder.__init__` требует всех четырёх), веса нулевые (вес приходит
#: ручкой мира `stand`), критерий выключен. Это ручки СТЕНДА: параметрами
#: каталога они не являются и в записи не попадают.
STAND_ARGS: dict = {
    "label": "стенд",
    "level": 3,
    "bases": "",                      # базы выводятся из приписок людей
    "budget_s": 300,
    "seed": 7,
    "workers": 8,
    "deterministic_time": None,
    "max_segs": 4,
    "sit_min": 45,
    "sit_max": 300,                   # верх окна стыковки — явная ручка стенда
    "duty_span": 780,
    "pairing_span": 7 * 1440,
    "source_rules": None,
    "source_rules_fp": None,
    "source_rules_strict": False,
    "vmp_package": False,
    "dark_definition": "strict",
    "base_tz_offset_min": None,
    "duty_cap": None,
    "conditions": None,
    "inject_catalog": [],
    "pin_catalog": None,
    "accept_incomplete_search": False,
    "trans_tz_hours": None,
    "briefing_default_min": None,
    "debriefing_default_min": None,
    "rules_config": None,
    "min_rest_sit": 45,
    "rest_base": 660,
    "rest_base_long": 1800,
    "rest_off": 700,
    "rest_off_long": 1300,
    "branch_duty": 3,
    "branch_chain": 4,
    "depth": 6,
    "per_duty": 30,
    "dh_options": 1,
    "rescue_dh": 4,
    "rescue_per_leg": 8,
    "use_cover_checkpoint": None,
    "solver_log": False,
    "no_presolve": False,
    "valve_top_per_leg": 0,
    "greedy_cover": False,
    "dump_pool": None,
    "gap_limit": 0.0,
    "core_min_budget": 60,
    "core_min_wall_s": 300,
    "rescue_s": 120,
    "select_engine": "cpsat",
    "escalate_s": 0,
    "solve_s": 0,
    "method": "enumerate",
    "cg_rounds": 8,
    "cg_top_k": 20000,
    "cg_alpha": 0.5,
    "cg_pass_s": 60,
    "w_night": 0,
    "w_weekend_night": 0,
    "weekend_days": (),
    "w_long_duty": 0,
    "long_duty_threshold": None,
    "w_dh": 0,
    "warm_catalog": None,
    "export_mps": None,
    "export_lp": None,
    "export_only": False,
    "base_day_cap": "",
    "criterion_source": "стенд каталога шаблонов",
    "w_span_day": 0,
    "w_duty": 0,
    "w_chain": 0,
    "w_excess": 0,
    "hard_cover_leg": [],
    "nights_per_layover": 2,
    "source_cost": True,              # ставок источника у мира нет: ветка спит
    "cost_0096": False,
    "cost_rank_deci_work": None,
    "cost_rank_deci_dh": None,
    "cost_rates_param": None,
    "tz_offset_min": 0,
    "cos_rules": None,
}

#: Параметр каталога -> ключ построителя. Кортеж — один параметр питает
#: несколько ключей; пара («ключ», множитель) — перевод единицы.
PARAM_TO_FLAG: dict = {
    "max_duty_duration": "duty_span",
    "min_rest_home": "rest_base",
    "min_rest_outstation": "rest_off",
    "min_turnaround": ("sit_min", "min_rest_sit"),
    "max_pairing_days": ("pairing_span", 1440),
    "max_landings_per_duty": "max_segs",
    "base_tz_offset_min": "tz_offset_min",
    "long_duty_threshold": "long_duty_threshold",
    # Мера 100: потолок продления — жёсткий предел смены построителя (--duty-cap), как на пути производства
    "duty_extension_cap": "duty_cap",
}

#: Ключи мира, идущие в `Namespace` как есть — ручки СТЕНДА, а не
#: параметры каталога (перечень словаря `world_forms.schedule`). Ключ вне
#: перечня — отказ словами: молча принятая ручка меняла бы исход задачи
#: невидимо для записи.
STAND_KNOBS = ("w_night", "w_weekend_night", "w_long_duty", "w_span_day",
               "nights_per_layover")

#: Правила яруса для ветки правил: своих чисел у стенда нет, поля берутся
#: из параметров мира; неназванное правило остаётся значением класса.
RULES_TRANS_TZ_HOURS = 3.0
RULES_TRANS_LEG_MIN = 720
RULES_DH_BOARD_AFTER_DEBRIEF_MIN = 20


# ------------------------------------------- поле мира -> секция пакета

#: Таблица «поле мира → секция пакета» (просьба Ж-5 проекта пакета
#: «Kotlin-исполнители агентом», §5.6). ОДНО место, где записано, какие
#: секции машинных форм пакета (`threshold-forms.json`) стенд умеет
#: НЕСТИ ИЗ МИРА задачи, и каким полем мира каждая из них питается.
#:
#: Зачем таблица, а не условие в сборщике пакета: исполнитель вида
#: `kotlin_form` (секция пакета) судится ПАРОЙ ЗАДАЧ-БЛИЗНЕЦОВ вида
#: `kotlin_constraint` — миры отличаются ровно секцией формы, а числа
#: ограничения обязаны разойтись. Пока знание «дни окна кладутся в
#: цитату посадки» жило условием `if family == "calendar_windows"`
#: внутри сборщика, новая секция требовала ПРАВКИ СТЕНДА; со строкой
#: таблицы стенд не правится — называется поле мира, секция пакета и
#: носитель, и близнецы судят исполнителя тем же путём.
#:
#: Ключ — семья каталога (её правило свода везёт форму в пакет), строка
#: называет ОБА конца: `world_field` — путь поля в мире задачи,
#: `section` — ключ секции в `threshold-forms.json` пакета. `carrier` —
#: имя носителя в `vmp.stand_solver` (`SECTION_CARRIERS`): рукотворного
#: поля `export_solver` сборка пакета не принимает, она его
#: пересчитывает, поэтому мир доезжает до секции через посадку правила.
#:
#: Семья, чей мир объявил форму правила, но которой в таблице НЕТ, —
#: отказ словами «секция … из мира стенда в пакет не собирается»
#: (`section_carrier`), а не молчаливый пропуск: пакет ушёл бы без формы,
#: ограничение решателя осталось бы без фактов, и близнецы дали бы
#: одинаковые числа — зелёную строку по ложной причине.
WORLD_TO_PACKAGE_SECTION: dict = {
    "calendar_windows": {
        "world_field": "rule_form.days",
        "section": "calendar_window_penalties",
        "carrier": "quote_days",
        "note": ("дни окна — словами закрытого словаря дней CDPM в цитату "
                 "посадки правила: рукотворное поле export_solver сборка "
                 "пакета пересчитывает (cdpm.model.assign_soft_families), "
                 "а дни выводит из формулировки и цитаты"),
    },
}

#: Обратный указатель «секция пакета → семья каталога». Считается ИЗ
#: таблицы, а не пишется рядом: две рукописные карты одного отношения
#: рано или поздно разойдутся молча.
PACKAGE_SECTION_FAMILY: dict = {
    row["section"]: family
    for family, row in WORLD_TO_PACKAGE_SECTION.items()}


def package_section_row(family: str) -> dict | None:
    """Строка таблицы «поле мира → секция пакета» семьи либо None."""
    row = WORLD_TO_PACKAGE_SECTION.get(str(family))
    return dict(row) if isinstance(row, dict) else None


def world_section_fields(world: dict) -> list:
    """Поля формы правила, объявленные МИРОМ задачи (`rule_form`).

    Пустой список значит «мир формы правила не несёт»: секцию пакета
    везти неоткуда, и требовать её от таблицы не за что.
    """
    form = world.get("rule_form") if isinstance(world, dict) else None
    if not isinstance(form, dict):
        return []
    return sorted(str(name) for name, value in form.items()
                  if value not in (None, [], {}, ""))


#: Где у мира задачи живёт форма правила: путь строки таблицы назван
#: целиком («rule_form.days»), чтобы читатель видел ОБА конца, а стенд
#: читает из него приставку и хвост.
WORLD_FORM_KEY = "rule_form"


def section_field(family: str) -> str | None:
    """Поле мира, которое несёт секция семьи («days»), либо None.

    Читается ИЗ ПУТИ строки таблицы (`world_field`), а не угадывается:
    поле мира и секция пакета названы одной строкой, и судить объявление
    мира стенд обязан ровно по ней. Путь не формы `rule_form.<имя>` —
    отказ словами: таблица не должна обещать того, чего стенд не умеет
    прочитать.
    """
    row = package_section_row(family)
    if row is None:
        return None
    path = str(row.get("world_field") or "")
    head, _, tail = path.partition(".")
    if head != WORLD_FORM_KEY or not tail:
        raise StandWorldError(
            f"строка таблицы «поле мира → секция пакета» семьи «{family}» "
            f"называет поле мира {path!r}: стенд читает только поля формы "
            f"правила ({WORLD_FORM_KEY}.<имя>) — путь иного вида везти "
            f"нечем")
    return tail


def section_carrier(family: str, world: dict) -> str | None:
    """Имя носителя секции пакета для семьи либо None (везти нечего).

    Отказ словами — два случая, и молчаливого пропуска нет ни в одном:

    1. мир задачи ОБЪЯВИЛ форму правила (`rule_form`), а таблица «поле
       мира → секция пакета» эту семью не называет;
    2. семья В ТАБЛИЦЕ, но мир объявил ПОЛЕ, которого строка не называет
       (`world_field`): носителю такого поля везти нечем, пакет ушёл бы
       без формы, а близнецы вида `kotlin_form` дали бы одинаковые числа
       — зелёную строку по построению.

    Второй случай судится здесь, а не «где-нибудь потом»: до правки
    08.09.2026 строка таблицы считалась исполненной по одному факту
    своего существования, и поле мира не сверялось ни с чем.
    """
    fields = world_section_fields(world)
    row = package_section_row(family)
    if row is None:
        if not fields:
            return None
        raise StandWorldError(
            f"секция пакета для формы правила семьи «{family}» (поля мира: "
            f"{', '.join(fields)}) из мира стенда в пакет не собирается: "
            f"таблица «поле мира → секция пакета» "
            f"(vmp.stand_adapters.WORLD_TO_PACKAGE_SECTION) её не называет "
            f"— назовите там поле мира, секцию threshold-forms.json и "
            f"носителя; правки судей стенда для этого не нужно")
    if not fields:
        return None
    named = section_field(family)
    stray = [name for name in fields if name != named]
    if stray:
        raise StandWorldError(
            f"поле мира {', '.join(stray)} секцией пакета не несётся: "
            f"строка таблицы «поле мира → секция пакета» семьи «{family}» "
            f"называет поле {row.get('world_field')!r} → секцию "
            f"«{row.get('section')}» и больше ничего, а мир задачи объявил "
            f"{', '.join(fields)} — назовите поле строкой таблицы (поле "
            f"мира, секция threshold-forms.json, носитель) либо уберите "
            f"его из мира: молча пакет ушёл бы без формы, и близнецы вида "
            f"kotlin_form дали бы одинаковые числа")
    return str(row.get("carrier") or "")


def _dt(value) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError) as e:
        raise StandWorldError(f"момент мира не читается: {value!r}") from e


def form_of(world: dict) -> str:
    if not isinstance(world, dict) or not world.get("form"):
        raise StandWorldError("мир задачи без поля form")
    return str(world["form"])


def _require_schedule(world: dict) -> dict:
    if form_of(world) != "schedule":
        raise StandWorldError(
            f"вход исполнителя строится только из мира формы «schedule», "
            f"а мир задачи формы «{form_of(world)}»")
    return world


def params_of(world: dict) -> dict:
    p = world.get("params")
    return dict(p) if isinstance(p, dict) else {}


def crews_of(world: dict) -> list:
    return list(world.get("crews") or ())


def unavailable_days_of(world: dict) -> dict | None:
    """Explicit synthetic days, shared by the native and source-table paths.

    Legacy worlds without this carrier keep their historical representation.
    Once any crew declares it, every crew must declare an exact list (including
    an explicit empty list): a missing/invalid value is not "available".
    No date, employee or expectation is inferred from an executor's output.
    """
    crews = crews_of(world)
    if not any(isinstance(c, dict) and "unavailable_days" in c for c in crews):
        return None
    out = {}
    for index, crew in enumerate(crews):
        prefix = f"crews[{index}].unavailable_days"
        if not isinstance(crew, dict):
            raise StandWorldError(f"{prefix}: сотрудник не объект")
        employee = crew.get("id")
        if (not isinstance(employee, str) or not employee
                or employee.strip() != employee or employee in out):
            raise StandWorldError(f"{prefix}: нет однозначного непустого id сотрудника")
        days = crew.get("unavailable_days")
        if not isinstance(days, list):
            raise StandWorldError(f"{prefix}: требуется явно объявленный список дат")
        seen = set()
        for value in days:
            try:
                valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
            except ValueError:
                valid = False
            if not valid or value in seen:
                raise StandWorldError(f"{prefix}: даты YYYY-MM-DD должны быть корректны и уникальны")
            seen.add(value)
        out[employee] = list(days)
    return out


def unavailability_table(world: dict) -> dict | None:
    """The ordinary inclusive «С»–«По» source schema, one row per actual day."""
    days = unavailable_days_of(world)
    if days is None:
        return None
    return {"columns": ["Табельный номер", "С", "По"],
            "rows": [{"Табельный номер": employee, "С": day, "По": day}
                     for employee, values in days.items() for day in values]}


def flights_of(world: dict) -> list:
    return list(world.get("flights") or ())


def is_deadhead(flight: dict) -> bool:
    return bool(flight.get("deadhead"))


# ------------------------------------------------------------- построитель

def _stand_namespace(world: dict) -> argparse.Namespace:
    args = argparse.Namespace(**copy.deepcopy(STAND_ARGS))
    params = params_of(world)
    for name, value in sorted(params.items()):
        flag = PARAM_TO_FLAG.get(name)
        if flag is None:
            continue
        if isinstance(flag, tuple) and len(flag) == 2 \
                and isinstance(flag[1], int):
            setattr(args, flag[0], int(value) * flag[1])
        elif isinstance(flag, tuple):
            for key in flag:
                setattr(args, key, value)
        else:
            setattr(args, flag, value)
    if set((world.get("conditions") or {})) & {"extension_bases", "extension_consent"}:
        # Адресный потолок продления построитель пейринга не исполняет (цепочка безымянна) — как
        # pairing_input._apply_duty_cap: предел — база
        args.duty_cap = None
    days = (world.get("rule_form") or {}).get("days")
    if days:
        args.weekend_days = tuple(days)
    for key, value in sorted((world.get("stand") or {}).items()):
        if key not in STAND_KNOBS:
            raise StandWorldError(
                f"ручка стенда {key!r} не объявлена (объявлены: "
                f"{', '.join(STAND_KNOBS)})")
        setattr(args, key, value)
    return args


def _cos_rules_of(args) -> object:
    """Правила источника ЯВНЫМ конструктором — из УЖЕ РАЗРЕШЁННЫХ флагов.

    Умолчаний заказчика у класса нет, и своих чисел стенд не выдумывает:
    каждое поле берётся из того же набора, которым идёт ветка без правил
    (`STAND_ARGS`, перекрытые параметрами мира). Иначе две ветки одного
    адаптера судили бы разные миры: параметр, названный миром, действовал
    бы в одной и не действовал в другой.

    Поля, к судимым правилам не относящиеся (окна ночи, «тёмная» смена),
    остаются значениями класса.
    """
    import _cos_rules as _cr

    span = int(args.duty_span)
    home, away = int(args.rest_base), int(args.rest_off)
    tier = _cr.LayoverTier(span, home, away, home, away, home, away)
    rules = _cr.CosRules(
        work_time_limit_by_tier=(span, span, span),
        layover_tiers=(tier,),
        min_sit_min=int(args.sit_min),
        max_pairing_days=int(args.pairing_span) // 1440,
        base_tz_offset_min=int(args.tz_offset_min or 0),
        trans_tz_hours=RULES_TRANS_TZ_HOURS,
        trans_leg_min=RULES_TRANS_LEG_MIN,
        dh_board_after_debrief_min=RULES_DH_BOARD_AFTER_DEBRIEF_MIN,
        teleportation={})
    rules.max_landings_per_duty = int(args.max_segs)
    return rules


def world_to_builder(world: dict, *, rules: bool = False):
    """Мир -> (payload, args) построителя.

    `rules=True` — ветка правил источника (её требуют `pairing_days_ok` и
    `pairing_span_ok`: без правил они возвращают истину и ничего не судят).
    Перегонные рейсы В PAYLOAD ПОДАЮТСЯ (иначе `Builder.legs` их не
    знает), но рабочими секторами не становятся: `chain_of` строит
    цепочку по рабочим рейсам, перегоны отдаёт отдельным списком.
    """
    _require_schedule(world)
    args = _stand_namespace(world)
    if rules:
        args.cos_rules = _cos_rules_of(args)
    p = params_of(world)
    seats = int(p.get("crew_size") or 1)
    codes = sorted({str(f[k]) for f in flights_of(world) for k in ("from", "to")})
    employees = []
    for c in crews_of(world):
        employees.append({"id": str(c["id"]), "homeAirport": str(c["base"]),
                          "skills": [str(c.get("rank") or "CP")]})
    flights, assignments = [], []
    for f in flights_of(world):
        fid = str(f["id"])
        flights.append({
            "flightNumber": fid,
            "departureAirport": str(f["from"]),
            "arrivalAirport": str(f["to"]),
            "departureUTCDateTime": _dt(f["dep"]).isoformat(),
            "arrivalUTCDateTime": _dt(f["arr"]).isoformat(),
            "aircraftType": str(f.get("fleet_type") or "T1")})
        if is_deadhead(f):
            continue
        rank = str((crews_of(world) or [{}])[0].get("rank") or "CP")
        for i in range(seats):
            assignments.append({"id": f"{fid}#{i}", "flight": fid,
                                "indexInFlight": i, "requiredSkill": rank})
    payload = {"airports": [{"code": c} for c in codes],
               "employees": employees, "flights": flights,
               "flightAssignments": assignments}
    return payload, args


def _duty_dict(builder, legs: list) -> dict:
    d = {"i": len(builder.duties), "legs": list(legs),
         "dep": legs[0]["dep"], "arr": legs[-1]["arr"],
         "t0": legs[0]["t0"], "t1": legs[-1]["t1"],
         "span": (legs[-1]["t1"] - legs[0]["t0"]).total_seconds() / 60}
    builder.annotate_duty(d)
    return d


def chain_of(builder, world: dict):
    """(цепочка, перегоны) по нарезке ПОСТРОИТЕЛЯ.

    Смена растёт, пока пауза не короче порога стыковки и короче верха
    окна (`sit_cap_at`), тип судна тот же и число участков не превысило
    кап генерации — те же условия, что у `_pairing_builder.build_duties`.
    Предел рабочего времени здесь НЕ применяется намеренно: он сам —
    предмет суда (`Builder.duty_limit_ok`), и просеивать им вход судьи
    значило бы дать проверке отфильтровать собственные данные (смена
    сверх предела просто не вошла бы в `Builder.duties`, и число
    отвергнутых смен всегда было бы нулём).
    """
    builder.build_duties(None)
    args = builder.args
    max_segs = (builder.rules.max_landings_per_duty if builder.rules
                else args.max_segs)
    sit_min = builder.rules.min_sit_min if builder.rules else args.sit_min
    work = sorted((builder.legs[str(f["id"])] for f in flights_of(world)
                   if not is_deadhead(f)), key=lambda l: (l["t0"], l["id"]))
    dh_legs = sorted((builder.legs[str(f["id"])] for f in flights_of(world)
                      if is_deadhead(f)), key=lambda l: (l["t0"], l["id"]))
    chain, cur = [], []
    for leg in work:
        if cur:
            prev = cur[-1]
            sit = (leg["t0"] - prev["t1"]).total_seconds() / 60
            same = (leg["fleet"] == prev["fleet"] and prev["arr"] == leg["dep"]
                    and sit_min <= sit < builder.sit_cap_at(prev["arr"])
                    and len(cur) < max_segs)
            if not same:
                chain.append(_duty_dict(builder, cur))
                cur = []
        cur.append(leg)
    if cur:
        chain.append(_duty_dict(builder, cur))
    return chain, dh_legs


# --------------------------------------------------------------- решение

def _assignments_of(world: dict) -> list:
    """Назначения мира; без них единственный (первый) сотрудник летит
    все рейсы в порядке вылета."""
    given = world.get("assignments")
    if isinstance(given, list) and given:
        return [dict(a) for a in given]
    crews = crews_of(world)
    if not crews:
        raise StandWorldError("в мире нет сотрудников — назначать некому")
    who = str(crews[0]["id"])
    return [{"crew": who, "flight": str(f["id"]), "seat": 0}
            for f in sorted(flights_of(world), key=lambda x: str(x["dep"]))]


def _mark_duties(builder, rows: list) -> None:
    """Разметка смен решения: пауза КОРОЧЕ верха окна стыковки оставляет
    рейсы в одной смене; не короче — новая смена. Низ окна не
    применяется: короткая стыковка остаётся внутри смены и судится
    своим исполнителем. Явный ключ `duty` назначения главнее разметки."""
    by_emp: dict[str, list] = {}
    for row in rows:
        by_emp.setdefault(row["employee"], []).append(row)
    for emp, lst in sorted(by_emp.items()):
        lst.sort(key=lambda r: (r["dep_time"], r["flight"]))
        n = 0
        for i, row in enumerate(lst):
            if row.get("duty_id"):
                continue
            if i:
                prev = lst[i - 1]
                gap = (_dt(row["dep_time"])
                       - _dt(prev["arr_time"])).total_seconds() / 60
                if gap >= builder.sit_cap_at(prev["arr_airport"]):
                    n += 1
            else:
                n = 1
            row["duty_id"] = f"D-{emp}-{n}"


def _world_conditions(world: dict) -> dict:
    """Normalize the stand's explicit yes/no flag, never Python truthiness.

    The catalogue's existing worlds use words; both the rule and solution
    must carry the same boolean. Unknown/ambiguous values are not consent.
    """
    raw = world.get("conditions")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise StandWorldError("conditions: требуется объект условий мира стенда")
    conditions = copy.deepcopy(raw)
    if "extension_consent" in conditions:
        value = conditions["extension_consent"]
        if type(value) is not bool:
            words = {"yes": True, "true": True, "да": True,
                     "no": False, "false": False, "нет": False}
            key = value.strip().lower() if isinstance(value, str) else None
            if key not in words:
                raise StandWorldError(
                    "extension_consent: требуется true/false или явное да/нет")
            value = words[key]
        conditions["extension_consent"] = value
    return conditions


def world_to_solution(world: dict) -> dict:
    """Мир -> решение в форме контракта `roster_certify`."""
    _require_schedule(world)
    payload, args = world_to_builder(world)
    import _pairing_builder as _pb
    builder = _pb.Builder(payload, args)
    by_id = {str(f["id"]): f for f in flights_of(world)}
    base_of = {str(c["id"]): str(c["base"]) for c in crews_of(world)}
    rank_of = {str(c["id"]): str(c.get("rank") or "CP") for c in crews_of(world)}
    rows = []
    for a in _assignments_of(world):
        fid = str(a["flight"])
        f = by_id.get(fid)
        if f is None:
            raise StandWorldError(f"назначение на рейс {fid!r}, которого нет в мире")
        emp = str(a["crew"])
        row = {"employee": emp, "flight": fid,
               "date": _dt(f["dep"]).date().isoformat(),
               "dep_time": _dt(f["dep"]).isoformat(),
               "arr_time": _dt(f["arr"]).isoformat(),
               "dep_airport": str(f["from"]), "arr_airport": str(f["to"]),
               "base": base_of.get(emp, ""), "role": rank_of.get(emp, "CP"),
               "duty_id": str(a.get("duty") or "")}
        if is_deadhead(f):
            row["deadhead"] = True
        rows.append(row)
    if any("deadhead" in r for r in rows):
        for r in rows:
            r.setdefault("deadhead", False)
    _mark_duties(builder, rows)
    consent = _world_conditions(world).get("extension_consent", False)
    duties, pairings = [], []
    by_duty: dict[str, list] = {}
    for row in rows:
        by_duty.setdefault(row["duty_id"], []).append(row)
    for did, lst in sorted(by_duty.items()):
        lst.sort(key=lambda r: r["dep_time"])
        duty = {"employee": lst[0]["employee"], "id": did,
                "start": lst[0]["dep_time"], "end": lst[-1]["arr_time"],
                "base": lst[0]["base"],
                "last_arr_airport": lst[-1]["arr_airport"]}
        if consent:
            duty["extension_consent"] = True
        duties.append(duty)
    duties.sort(key=lambda d: (d["employee"], d["start"]))
    by_emp: dict[str, list] = {}
    for row in rows:
        by_emp.setdefault(row["employee"], []).append(row)
    for emp, lst in sorted(by_emp.items()):
        lst.sort(key=lambda r: r["dep_time"])
        pairings.append({"employee": emp, "id": f"CH-{emp}",
                         "start": lst[0]["dep_time"],
                         "end": max(r["arr_time"] for r in lst)})
    solution = {"solution": "стенд", "assignments": rows, "duties": duties,
                "pairings": pairings}
    horizon = world.get("horizon")
    if isinstance(horizon, dict):
        solution["window_horizon"] = {
            "start_inclusive": _dt(horizon["from"]).replace(
                hour=0, minute=0, second=0, microsecond=0).isoformat(),
            "end_exclusive": _dt(horizon["to"]).replace(
                hour=0, minute=0, second=0, microsecond=0).isoformat()}
    solution["window_employees"] = [
        {"employee": str(c["id"]), "base": str(c["base"])}
        for c in crews_of(world)]
    history = world.get("history")
    if isinstance(history, list) and history:
        as_of = (solution.get("window_horizon") or {}).get("start_inclusive")
        out = []
        for h in history:
            rec = {"employee": str(h.get("crew") or h.get("employee")),
                   "as_of": h.get("as_of") or as_of, "has_history": True}
            for key in ("last_duty_end", "last_duty_station",
                        "last_duty_night", "consecutive_night_duties",
                        "month_days_worked", "month_block_min",
                        "month_work_min", "month_flight_time_min",
                        "month_landings", "rest_before_horizon_min",
                        "consecutive_duty_days", "days_since_day_off",
                        "location"):
                if key in h:
                    rec[key] = h[key]
            out.append(rec)
        solution["history"] = out
    calendar = world.get("history_calendar")
    if isinstance(calendar, list) and calendar:
        solution["history_calendar"] = [dict(x) for x in calendar]
    return solution


# ------------------------------------------------------------- сертификат

#: Значение параметра-перечня в своде — СТРОКА через запятую: исполнитель
#: допуска на тип список отвергает («не прогнана»), а перечень правила
#: авиакомпании приходит именно строкой.
LIST_PARAMS = ("fleet_types",)

#: Параметры-НОРМЫ ПО БАЗАМ: значение мира — карта {код базы: норма}, а
#: канон носителя (решение К-2, `TaskPackage.PER_BASE_RE`) — СТРОКА
#: «ST1:1,ST2:2». Без канона норма приезжает без уточнителя-кода базы, и
#: решатель честно отказывается её исполнять («к какой базе относить —
#: не угадывает»), а стенд получал бы ноль по ложной причине.
MAP_PARAMS = ("reserve_crews",)


def _param_value(name: str, value):
    """Значение параметра мира в форме носителя ВМП."""
    if name in LIST_PARAMS and isinstance(value, (list, tuple)):
        return ",".join(str(x) for x in value)
    if name in MAP_PARAMS and isinstance(value, dict):
        return ",".join(f"{k}:{int(v)}" for k, v in sorted(value.items()))
    return value


def _reference_tables(world: dict) -> dict:
    crews, flights = crews_of(world), flights_of(world)
    unavailability = unavailability_table(world)
    quals = []
    for c in crews:
        for t in (c.get("ratings") or ()):
            quals.append({"Табельный номер": str(c["id"]), "Тип ВС": str(t)})
    out = {"S1": {
        "Экипажи": {"columns": ["Табельный номер", "База приписки"],
                    "rows": [{"Табельный номер": str(c["id"]),
                              "База приписки": str(c["base"])} for c in crews]},
        "Рейсы": {"columns": ["Номер рейса", "Дата", "Время вылета", "Тип ВС"],
                  "rows": [{"Номер рейса": str(f["id"]),
                            "Дата": _dt(f["dep"]).date().isoformat(),
                            "Время вылета": _dt(f["dep"]).strftime("%H:%M"),
                            "Тип ВС": str(f.get("fleet_type") or "T1")}
                           for f in flights]},
        "Допуски": {"columns": ["Табельный номер", "Тип ВС"], "rows": quals}}}
    if unavailability is not None:
        out["S1"]["Отпуска"] = unavailability
    return out


def _rule_of(world: dict, family: str, meta: dict, names: list) -> dict:
    p = params_of(world)
    params = {}
    for name in names:
        if name not in p:
            continue
        params[name] = {"value": _param_value(name, p[name])}
    if family == "duty_extension_penalty":
        for name, value in sorted(_world_conditions(world).items()):
            params[name] = {"value": value}
    rule = {"id": f"R-STAND-{family}", "kind": meta.get("kind") or "HARD",
            "zone": meta.get("zone") or "BOTH",
            "title": meta.get("title") or family,
            "statement": meta.get("statement") or f"стендовое правило семьи {family}",
            "weight": 1, "sources": [],
            "template_fit": {"category": "A", "family": family,
                             "registry_rev": meta.get("registry_rev") or "стенд",
                             "quote": "стенд", "params": params}}
    if family == "calendar_windows":
        rule["export_solver"] = {
            "family": family,
            "days": list((world.get("rule_form") or {}).get("days") or ()),
            "tz": "base-local"}
    return rule


def world_to_certificate(world: dict, families: list, *, meta=None):
    """(свод, набор таблиц) для исполнителей сертификата.

    Свод несёт по правилу на каждую названную семью; параметры правила —
    те параметры мира, которые принадлежат этой семье по каталогу
    (`meta[family]["params"]`). Условия потолка продления кладутся в
    `template_fit.params` правила продления: `roster_certify._cap_rules`
    читает их ТОЛЬКО оттуда, и ключ `conditions` правила остался бы
    непрочитанным — потолок применился бы ко всем сменам, а нулевые
    находки сошлись бы по ложной причине.
    """
    _require_schedule(world)
    meta = meta or {}
    rules = []
    for family in sorted(dict.fromkeys(families)):
        info = dict(meta.get(family) or {})
        rules.append(_rule_of(world, family, info,
                              list(info.get("params") or ())))
    from vmp.run import roster_certify as rc
    solution = world_to_solution(world)
    dataset = rc.build_solution_dataset(
        solution, reference_tables=_reference_tables(world))
    return {"rules": rules}, dataset


# ----------------------------------------------------------------- решатель

#: Поле истории мира -> плоское имя записи истории набора решателя
#: (`webapp.task_package.HISTORY_FIELDS`, класс `EmployeeHistory`). Своих
#: имён у стенда нет: набор решателя один на все пути продукта.
HISTORY_FIELD = {
    "month_block_min": "historyMonthBlockMin",
    "month_work_min": "historyMonthWorkMin",
    "month_flight_time_min": "historyMonthFlightTimeMin",
    "month_days_worked": "historyMonthDaysWorked",
    "month_landings": "historyMonthLandings",
    "last_duty_end": "historyLastDutyEnd",
    "last_duty_station": "historyLastDutyStation",
    "last_duty_work_min": "historyLastDutyWorkMin",
    "last_duty_span_min": "historyLastDutySpanMin",
    "last_duty_night": "historyLastDutyNight",
    "rest_before_horizon_min": "historyRestBeforeHorizonMin",
    "consecutive_duty_days": "historyConsecutiveDutyDays",
    "consecutive_night_duties": "historyConsecutiveNightDuties",
    "days_since_day_off": "historyDaysSinceDayOff",
    "location": "historyLocation",
}


def _history_record(entry: dict) -> dict:
    """Запись истории мира -> плоская запись набора решателя.

    Имена мира (`last_duty_end`) и имена набора (`historyLastDutyEnd`) —
    разные словари, и переводит их эта карта. Незнакомое имя МОЛЧА не
    пропускается: набор решателя разбирается строго, и неизвестное поле
    отвергло бы задачу целиком без объяснения.
    """
    out: dict = {}
    for key, value in sorted(entry.items()):
        if key in ("crew", "employee", "as_of", "has_history"):
            continue
        name = HISTORY_FIELD.get(str(key))
        if name is None:
            raise StandWorldError(
                f"история мира несёт поле {key!r}, которого нет в записи "
                f"истории набора решателя: перевести его не во что "
                f"(известны: {', '.join(sorted(HISTORY_FIELD))})")
        out[name] = value
    return out


def world_to_problem(world: dict) -> dict:
    """Мир -> задача `FlightCrewSchedule` для живого прогона решателя.

    Аэропорт в задаче назван КОДОМ везде, кроме списка `airports`: класс
    `Airport` объявлен `@JsonIdentityInfo` по полю `code`, и второй
    полный объект того же кода разбор задачи отвергает целиком (живой
    ответ решателя — 400 без тела). Полный объект — только один раз, в
    самом списке.

    Прогон решателя отсюда НЕ идёт: обращение к экземпляру — дело
    `vmp.stand_solver` и только командой `--with-solver <url>` по слову
    владельца (правило прогонов).
    """
    _require_schedule(world)
    unavailable = unavailable_days_of(world)
    p = params_of(world)
    seats = int(p.get("crew_size") or 1)
    codes = sorted({str(f[k]) for f in flights_of(world) for k in ("from", "to")})
    employees = []
    hist = {str(h.get("crew") or h.get("employee")): h
            for h in (world.get("history") or ())}
    horizon = world.get("horizon")
    as_of = (_dt(horizon["from"]).isoformat()
             if isinstance(horizon, dict) else None)
    for c in crews_of(world):
        emp = {"id": str(c["id"]), "name": str(c["id"]),
               "homeAirport": str(c["base"]),
               "skills": [str(c.get("rank") or "CP")],
               "typeRatings": [str(t) for t in (c.get("ratings") or ())],
               "preferredDaysOff": list(c.get("preferred_days_off") or ()),
               "seniority": int(c.get("seniority") or 500)}
        if unavailable is not None:
            emp["unavailableDays"] = list(unavailable[str(c["id"])])
        h = hist.get(str(c["id"]))
        if h:
            emp["history"] = _history_record(h)
            emp["historyAsOf"] = str(h.get("as_of") or as_of or "")
        employees.append(emp)
    flights, assignments = [], []
    for f in flights_of(world):
        fid = str(f["id"])
        flights.append({"flightNumber": fid,
                        "departureAirport": str(f["from"]),
                        "departureUTCDateTime": _dt(f["dep"]).isoformat(),
                        "arrivalAirport": str(f["to"]),
                        "arrivalUTCDateTime": _dt(f["arr"]).isoformat(),
                        "aircraftType": str(f.get("fleet_type") or "T1")})
    seat_of = {}
    for a in _assignments_of(world):
        seat_of[(str(a["flight"]), int(a.get("seat") or 0))] = str(a["crew"])
    for f in flights_of(world):
        fid = str(f["id"])
        for i in range(seats):
            assignments.append({
                "id": f"{fid}#{i}", "flight": fid, "indexInFlight": i,
                "requiredSkill": str((crews_of(world) or [{}])[0].get("rank")
                                     or "CP"),
                "employee": seat_of.get((fid, i)),
                "deadhead": is_deadhead(f)})
    problem = {"airports": [{"code": c} for c in codes],
               "employees": employees, "flights": flights,
               "flightAssignments": assignments}
    if isinstance(horizon, dict):
        # Имя поля в обмене — `window_horizon` (`@JsonProperty` класса
        # `FlightCrewSchedule`), поля окна — `start_inclusive` /
        # `end_exclusive` (`AccountingWindow`): тот же вид, что у набора
        # продукта (`webapp.task_package.declared_window_horizon`).
        problem["window_horizon"] = {
            "start_inclusive": _dt(horizon["from"]).isoformat(),
            "end_exclusive": _dt(horizon["to"]).isoformat()}
    return problem


# ------------------------------------------------------- контрольная пара

def cost_pair_to_control_pair(world: dict, tmp_dir, *,
                              expected_total_deci: int) -> Path:
    """Мир формы `cost_pair` -> временный файл-носитель контрольных пар.

    Путь к файлу контура исполнителю критерия не передаётся (он зовёт
    `check_control_pair` без `path`), поэтому пару МИРА судья сверяет
    сам — той же функцией, но по этому файлу."""
    if form_of(world) != "cost_pair":
        raise StandWorldError(
            f"контрольная пара строится из мира формы «cost_pair», "
            f"а мир формы «{form_of(world)}»")
    doc = {"customer": "стенд",
           "pairs": [{"criterion_kind": "rank_normalized_cost",
                      "flight_min": int(world["flight_min"]),
                      "dh_min": int(world["dh_min"]),
                      "crew": int(world["crew"]),
                      "expected_total_deci": int(expected_total_deci)}]}
    path = Path(tmp_dir) / "vmp-criterion-control-pairs.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def cost_pair_payload(world: dict):
    """Мир формы `cost_pair` -> минимальный payload построителя: один
    рабочий рейс длиной `flight_min` и, при `dh_min > 0`, перегон.

    Мир пары рейсов не несёт, а `cost_0096` считает МИНУТЫ ПО УЧАСТКАМ —
    без участков он вернул бы ноль и сверка сошлась бы ни о чём."""
    if form_of(world) != "cost_pair":
        raise StandWorldError(
            f"payload пары строится из мира формы «cost_pair», "
            f"а мир формы «{form_of(world)}»")
    t0 = datetime(2026, 10, 1, 6, 0)
    fl = int(world["flight_min"])
    dh = int(world["dh_min"])
    flights = [{"flightNumber": "F1", "departureAirport": "ST1",
                "arrivalAirport": "ST2",
                "departureUTCDateTime": t0.isoformat(),
                "arrivalUTCDateTime": (t0 + timedelta(minutes=fl)).isoformat(),
                "aircraftType": "T1"}]
    if dh > 0:
        d0 = t0 + timedelta(minutes=fl + 60)
        flights.append({"flightNumber": "D1", "departureAirport": "ST2",
                        "arrivalAirport": "ST1",
                        "departureUTCDateTime": d0.isoformat(),
                        "arrivalUTCDateTime": (d0 + timedelta(minutes=dh)).isoformat(),
                        "aircraftType": "T1"})
    payload = {"airports": [{"code": "ST1"}, {"code": "ST2"}],
               "employees": [{"id": "P1", "homeAirport": "ST1",
                              "skills": ["CP"]}],
               "flights": flights,
               "flightAssignments": [{"id": "F1#0", "flight": "F1",
                                      "indexInFlight": 0,
                                      "requiredSkill": "CP"}]}
    args = argparse.Namespace(**copy.deepcopy(STAND_ARGS))
    return payload, args
