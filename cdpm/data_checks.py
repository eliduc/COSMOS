# -*- coding: utf-8 -*-
"""Генератор и исполнитель проверок данных (линия данных, пакет ДЛ-2).

Строки свода — русский текст, машинная проверка из текста напрямую не
рождается. Разделение труда (постановка владельца, обсуждение
2026-08-18): ЗАКРЫТЫЙ перечень шаблонов проверок; модель-КОМПИЛЯТОР
предлагает для строки свода спецификацию по шаблону; КОД валидирует
спецификацию (fail-closed: невалидная или неподъёмная строка →
честный статус «не скомпилирована» с причиной) и исполняет её
детерминированно. Блокированные строки свода не компилируются до
ответа авиакомпании.

Набор данных (обмен с заморозкой данных, ДЛ-3):
    {"sources": [{"sid","file","sha256"}],
     "tables": {sid: {лист: {"columns": [...],
                             "rows": [{колонка: строковое значение}]}}}}

Встроенные проверки качества (дубль, пропуск данных, формат) идут
чистым кодом без модели и без спецификаций.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from cdpm import data_report as dr
from cdpm import llm as llm_mod

COMPILE_CHUNK = 12       # строк свода на одно обращение компилятора

# ── Разбор значений: честно и единообразно ──

_DATE_RES = (re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$"),
             re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"))
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})(?::\d{2})?$")
_NUM_RE = re.compile(r"^-?\d+(?:[.,]\d+)?$")


def parse_date(value: str):
    s = (value or "").strip().split(" ")[0].split("T")[0]
    m = _DATE_RES[0].match(s)
    if m:
        d, mo, y = m.groups()
        try:
            return datetime(int(y), int(mo), int(d))
        except ValueError:
            return None
    m = _DATE_RES[1].match(s)
    if m:
        y, mo, d = m.groups()
        try:
            return datetime(int(y), int(mo), int(d))
        except ValueError:
            return None
    return None


def parse_moment(value: str):
    """Дата-время: «дд.мм.гггг чч:мм» или ISO; голая дата — полночь."""
    s = (value or "").strip().replace("T", " ")
    parts = s.split(" ", 1)
    day = parse_date(parts[0])
    if day is None:
        return None
    if len(parts) == 1 or not parts[1].strip():
        return day
    m = _TIME_RE.match(parts[1].strip())
    if not m:
        return None
    try:
        return day.replace(hour=int(m.group(1)), minute=int(m.group(2)))
    except ValueError:            # «25:00» — время суток не читается
        return None


def parse_num(value: str):
    s = (value or "").strip().replace(",", ".")
    return float(s) if _NUM_RE.match((value or "").strip()) else None


# ── Шаблоны проверок (закрытый перечень) ──
# Параметры: sheet — «SID/Лист»; колонки — имена колонок листа.

TEMPLATES: dict[str, tuple[str, ...]] = {
    "обязательное-поле": ("sheet", "column"),
    "значение-существует": ("sheet", "column", "value"),
    "перечень-значений": ("sheet", "column", "allowed"),
    "значение-в-диапазоне": ("sheet", "column"),   # + min и/или max
    "уникальность": ("sheet", "columns"),
    "ссылка-существует": ("sheet", "column", "ref_sheet", "ref_column"),
    "действительность-на-дату": ("sheet", "key_column", "date_column",
                                 "ref_sheet", "ref_key_column",
                                 "ref_until_column"),
    "минимальный-разрыв-часов": ("sheet", "group_column",
                                 "start_column", "end_column",
                                 "min_hours"),
    "максимальная-длительность-часов": ("sheet", "start_column",
                                        "end_column", "max_hours"),
    # 4б моста (2026-08-23): счёт состава группы — «ровно N записей
    # в группе» (численность экипажа рейса/смены), необязательный
    # фильтр по роли (role_column + role_value).
    "счёт-состава-группы": ("sheet", "group_column", "count"),
    # 12а (ступень 3, решение владельца 2026-08-24): выходной —
    # ПРОИЗВОДНОЕ (сутки полного календарного месяца, которых не
    # касается ни одна запись сотрудника); полные месяцы берутся из
    # диапазона данных листа, неполные — вне оценки (семантика
    # шага 1 решателя); запись через полночь занимает обе даты.
    "выходных-в-месяце": ("sheet", "group_column", "start_column",
                          "end_column", "min_days_off"),
    # Формы истории сотрудника (семьи night_series и rolling_rest базы
    # шаблонов) — по листу СМЕН одного человека (табельный номер,
    # начало, конец); прочтение то же, что у решателя (LogicForms) и
    # оракула. Ночная смена — начало в окне минут суток [от, до) с
    # переходом через полночь при от > до; серия — соседние по времени
    # смены сотрудника без дневной между ними; находка — серия длиннее
    # предела (адрес — первая смена серии).
    "серия-ночных-смен": ("sheet", "employee_column", "start_column",
                          "end_column", "night_from_min",
                          "night_to_min", "max_consecutive"),
    # Отдых — промежуток между соседними сменами сотрудника, а также
    # от начала первых суток данных до первой смены и от последней
    # смены до конца последних суток; окна — каждые window_days суток
    # от каждой границы суток внутри диапазона данных сотрудника;
    # находка — окно без отдыха не короче min_rest_min минут (адрес —
    # первая смена, начатая в окне).
    "отдых-в-скользящем-окне": ("sheet", "employee_column",
                                "start_column", "end_column",
                                "window_days", "min_rest_min"),
    # Мягкие формы волны 1 (П-2 этап 2, word-в-word с решателем
    # 2026-08-25). Исполнители питона — НЕЗАВИСИМЫЕ оракулы пар
    # (гейт волны: исполнитель решателя против этих оракулов);
    # находка = ЕДИНИЦА счёта формы (литерал 1). sheet — лист
    # назначений; остальные листы — своими ссылками.
    "штраф-ступенчатый-за-стыковку": ("sheet", "flights_sheet",
                                      "threshold_min", "step_min"),
    "штраф-за-разрыв-смены": ("sheet", "flights_sheet",
                              "duties_sheet", "threshold_min"),
    "однородность-цепочки-ночь-день": ("sheet", "flights_sheet",
                                       "crews_sheet",
                                       "window_from_min",
                                       "window_to_min"),
    "мягкий-предел-дней-подряд": ("sheet", "flights_sheet", "bound"),
    "неудовлетворённая-выполнимая-заявка": ("sheet", "flights_sheet",
                                            "crews_sheet",
                                            "requests_sheet"),
}

WAVE_SOFT_TEMPLATES = ("штраф-ступенчатый-за-стыковку",
                       "штраф-за-разрыв-смены",
                       "однородность-цепочки-ночь-день",
                       "мягкий-предел-дней-подряд",
                       "неудовлетворённая-выполнимая-заявка")

# Необязательная тройка ОБЛАСТИ у «минимальный-разрыв-часов» (4б):
# place_column — колонка места разрыва (аэропорт конца предыдущей
# записи), base_column — база приписки строки, area — «база» либо
# «вне базы» (закрытый словарь ревизии 1.4). Все три вместе или ни
# одной.
_AREA_TRIO = ("place_column", "base_column", "area")


class DataChecksError(ValueError):
    """Честный отказ: причина всегда названа."""


def _table(dataset: dict, sheet_ref: str):
    sid, _, sheet = (sheet_ref or "").partition("/")
    table = (dataset.get("tables", {}).get(sid) or {}).get(sheet)
    return sid, sheet, table


def validate_spec(spec: dict, dataset: dict,
                  svod_rules: dict) -> str | None:
    """Причина невалидности спецификации или None. Fail-closed: любая
    несходимость — отказ с причиной, не догадка."""
    template = spec.get("template")
    if isinstance(template, str) and template.startswith(("история-", "горизонт-")):
        # Встроенные проверки листа «История» (ревизия 1.17) и горизонта
        # (1.18) — не из перечня компилятора; параметр один — лист.
        return None if (spec.get("params") or {}).get("sheet") \
            else "шаблон истории/горизонта: нет параметра 'sheet'"
    if template not in TEMPLATES:
        return f"шаблон {template!r} вне перечня {tuple(TEMPLATES)}"
    params = spec.get("params") or {}
    for field in TEMPLATES[template]:
        if field not in params:
            return f"шаблон {template}: нет параметра {field!r}"
    if template == "значение-в-диапазоне" \
            and "min" not in params and "max" not in params:
        return "диапазон без min и без max"
    if template == "минимальный-разрыв-часов":
        given = [f for f in _AREA_TRIO if f in params]
        if given and len(given) != len(_AREA_TRIO):
            return (f"область разрыва: даны {given}, нужны все три "
                    f"{list(_AREA_TRIO)} либо ни одной")
        if given:
            from cdpm import model as _m
            if _m.canon_area_qualifier(params["area"]) is None:
                return (f"area {params['area']!r} вне закрытого "
                        f"словаря {_m.AREA_QUALIFIERS}")
    if template == "выходных-в-месяце":
        n = params.get("min_days_off")
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            return f"min_days_off не целое ≥ 1: {n!r}"
    if template == "серия-ночных-смен":
        for f in ("night_from_min", "night_to_min"):
            v = params.get(f)
            if not isinstance(v, int) or isinstance(v, bool) \
                    or not 0 <= v < 1440:
                return f"{f} не целое в 0..1439: {v!r}"
        if params["night_from_min"] == params["night_to_min"]:
            return ("окно ночи пусто: night_from_min и night_to_min "
                    "совпадают")
        n = params.get("max_consecutive")
        if not isinstance(n, int) or isinstance(n, bool) or n < 0:
            return f"max_consecutive не целое ≥ 0: {n!r}"
    if template == "отдых-в-скользящем-окне":
        d = params.get("window_days")
        if not isinstance(d, int) or isinstance(d, bool) or d < 1:
            return f"window_days не целое ≥ 1: {d!r}"
        w = params.get("min_rest_min")
        if not isinstance(w, int) or isinstance(w, bool) or w < 1:
            return f"min_rest_min не целое ≥ 1: {w!r}"
        if w > d * 1440:
            return (f"min_rest_min {w} больше окна {d} суток "
                    f"({d * 1440} мин) — требование невыполнимо")
    if template in WAVE_SOFT_TEMPLATES:
        for f in TEMPLATES[template]:
            if f in ("threshold_min", "step_min", "bound",
                     "window_from_min", "window_to_min"):
                v = params.get(f)
                if not isinstance(v, int) or isinstance(v, bool) \
                        or v < 0:
                    return f"{template}: {f} не целое ≥ 0: {v!r}"
        if template == "штраф-ступенчатый-за-стыковку" \
                and params.get("step_min", 0) < 1:
            return "step_min не целое ≥ 1 — шаг ступени обязателен"
    if template == "счёт-состава-группы":
        n = params.get("count")
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            return f"count не целое ≥ 1: {n!r}"
        if ("role_column" in params) != ("role_value" in params):
            return ("фильтр роли: role_column и role_value — только "
                    "вместе")
        if params.get("relation", "eq") not in ("eq", "max", "min"):
            return (f"relation {params.get('relation')!r} вне "
                    f"перечня ('eq', 'max', 'min')")
        if "role_column" in params:
            _sid, _sheet, t = _table(dataset, params.get("sheet"))
            if t is not None \
                    and params.get("role_column") in t["columns"]:
                vals = {(r.get(params["role_column"]) or "").strip()
                        .casefold() for r in t["rows"]}
                want_v = str(params.get("role_value", "")) \
                    .strip().casefold()
                if want_v not in vals:
                    # Живой класс (мини, 12 ложных): «Пилот» из
                    # текста правила против Captain/FirstOfficer в
                    # данных — фильтр, не встречающийся в колонке
                    # НИ РАЗУ, судил бы пустым множеством.
                    return (f"role_value {params['role_value']!r} "
                            f"не встречается в колонке "
                            f"{params['role_column']!r} — словарь "
                            f"значений данных иной")
    hist_keys = history_sheet_keys(dataset)
    for field in ("sheet", "ref_sheet"):
        if field in params:
            sid, sheet, table = _table(dataset, params[field])
            if table is None:
                return f"листа {params[field]!r} нет в наборе данных"
            if (sid, sheet) in hist_keys:
                return (f"лист {params[field]!r} — раздел истории "
                        f"сотрудника (1.17), не цель проверок правил")
            cols = set(table["columns"])
            col_fields = {
                "sheet": ("column", "key_column", "date_column",
                          "group_column", "employee_column",
                          "start_column", "end_column",
                          "place_column", "base_column",
                          "role_column", "distinct_column"),
                "ref_sheet": ("ref_column", "ref_key_column",
                              "ref_until_column"),
            }[field]
            for cf in col_fields:
                if cf in params and params[cf] not in cols:
                    return (f"колонки {params[cf]!r} нет на листе "
                            f"{params[field]!r}")
            if template == "уникальность" and field == "sheet":
                missing = [c for c in params.get("columns", [])
                           if c not in cols]
                if missing:
                    return f"колонок {missing} нет на листе"
    rule_id = (spec.get("basis") or {}).get("rule")
    if rule_id:
        rule = svod_rules.get(rule_id)
        if rule is None:
            return f"строки {rule_id!r} нет в своде"
        if rule.get("kind") not in dr.CHECKABLE_KINDS:
            return (f"строка {rule_id} типа {rule.get('kind')!r} — "
                    f"не основание проверки данных")
        if rule.get("blockers"):
            return (f"строка {rule_id} блокирована вопросом — до "
                    f"ответа авиакомпании не компилируется")
    if spec.get("severity", "КРИТИЧЕСКОЕ") not in dr.SEVERITIES:
        return f"серьёзность {spec.get('severity')!r} вне перечня"
    return None


# ── Исполнители шаблонов: чистый код, счёт записей честный ──

def _findings(rows_bad: list, sid: str, sheet: str, column: str | None,
              text_of, evidence_of) -> list[dict]:
    out = []
    for i, row in rows_bad:
        f = {"target": {"sid": sid, "sheet": sheet, "row": i},
             "text": text_of(row), "evidence": evidence_of(i, row)}
        if column:
            f["target"]["column"] = column
        out.append(f)
    return out


def _wave_flights(dataset: dict, ref: str) -> dict[str, dict]:
    """Рейсы по номеру; времена обязаны читаться (fail-closed)."""
    from datetime import datetime, timedelta
    _sid, _sheet, t = _table(dataset, ref)
    if t is None:
        raise DataChecksError(f"листа рейсов {ref!r} нет")
    out = {}
    for i, r in enumerate(t["rows"], start=2):
        try:
            dep = datetime.strptime(
                f"{r['Дата']} {r['Время вылета']}", "%Y-%m-%d %H:%M")
            arr = datetime.strptime(
                f"{r['Дата']} {r['Время прилёта']}", "%Y-%m-%d %H:%M")
        except (KeyError, ValueError) as e:
            raise DataChecksError(
                f"{ref}!строка {i}: времена рейса не читаются ({e})")
        if arr < dep:
            arr += timedelta(days=1)
        out[str(r.get("Номер рейса"))] = {
            "dep": dep, "arr": arr, "row": i,
            "from": r.get("Вылет"), "to": r.get("Прилёт")}
    return out


def _wave_assigned(rows, flights):
    """Назначенные рейсы по сотруднику, отсортированы по вылету."""
    by_emp: dict[str, list] = {}
    for i, r in enumerate(rows, start=2):
        emp = str(r.get("Табельный номер") or "").strip()
        no = str(r.get("Номер рейса") or "").strip()
        f = flights.get(no)
        if not emp or f is None:
            raise DataChecksError(
                f"назначение строки {i}: сотрудник/рейс не "
                f"разрешается (emp={emp!r}, рейс={no!r})")
        by_emp.setdefault(emp, []).append(dict(f, no=no, arow=i))
    for fs in by_emp.values():
        fs.sort(key=lambda f: f["dep"])
    return by_emp


def _run_wave_soft(template: str, p: dict, dataset: dict,
                   sid: str, sheet: str,
                   rows: list) -> tuple[int, list[dict]]:
    """Исполнители мягких форм волны 1: находка = ЕДИНИЦА счёта
    (литерал 1). Оракулы независимы от кода решателя — гейт волны
    сверяет его исполнителей с этими."""
    flights = _wave_flights(dataset, p["flights_sheet"])
    by_emp = _wave_assigned(rows, flights)
    finds: list[dict] = []

    def _unit(arow: int, text: str, evidence: str):
        finds.append({"target": {"sid": sid, "sheet": sheet,
                                 "row": arow},
                      "text": text, "evidence": evidence})

    if template == "штраф-ступенчатый-за-стыковку":
        thr, step = int(p["threshold_min"]), int(p["step_min"])
        for emp, fs in by_emp.items():
            for a, b in zip(fs, fs[1:]):
                sit = (b["dep"] - a["arr"]).total_seconds() / 60
                if sit >= 600:          # отдых — не стыковка
                    continue
                if sit > thr:
                    units = int((sit - thr) // step)
                    for u in range(units):
                        _unit(b["arow"],
                              f"длинная стыковка: ступень {u + 1}",
                              f"{emp}: {a['no']}→{b['no']} "
                              f"стыковка {int(sit)} мин > {thr}")
    elif template == "штраф-за-разрыв-смены":
        thr = int(p["threshold_min"])
        from datetime import datetime
        _s2, _sh2, duties = _table(dataset, p["duties_sheet"])
        if duties is None:
            raise DataChecksError(
                f"листа смен {p['duties_sheet']!r} нет")
        for i, d in enumerate(duties["rows"], start=2):
            emp = str(d.get("Табельный номер") or "").strip()
            try:
                s0 = datetime.strptime(d["Начало"], "%Y-%m-%d %H:%M")
                s1 = datetime.strptime(d["Конец"], "%Y-%m-%d %H:%M")
            except (KeyError, ValueError) as e:
                raise DataChecksError(
                    f"{p['duties_sheet']}!строка {i}: окно смены "
                    f"не читается ({e})")
            fs = [f for f in by_emp.get(emp, [])
                  if s0 <= f["dep"] and f["arr"] <= s1]
            if any((b["dep"] - a["arr"]).total_seconds() / 60 > thr
                   for a, b in zip(fs, fs[1:])):
                _unit(fs[0]["arow"], "разрыв внутри смены",
                      f"{emp}: смена {d['Начало']}–{d['Конец']} "
                      f"несёт стыковку > {thr} мин")
    elif template == "однородность-цепочки-ночь-день":
        w0, w1 = int(p["window_from_min"]), int(p["window_to_min"])
        from datetime import timedelta
        _s2, _sh2, crews = _table(dataset, p["crews_sheet"])
        if crews is None:
            raise DataChecksError(
                f"листа экипажей {p['crews_sheet']!r} нет")
        base_of = {str(c.get("Табельный номер")): c.get("База")
                   for c in crews["rows"]}

        def _night(f):
            day = f["dep"].replace(hour=0, minute=0)
            for shift in (0, 1):
                a = day + timedelta(days=shift, minutes=w0)
                b = day + timedelta(days=shift, minutes=w1)
                if f["dep"] <= b and f["arr"] >= a:
                    return True
            return False

        for emp, fs in by_emp.items():
            base = base_of.get(emp)
            if not base:
                raise DataChecksError(
                    f"сотрудник {emp}: базы приписки нет — граница "
                    f"цепочки не судится")
            chain, chains = [], []
            for k, f in enumerate(fs):
                chain.append(f)
                nxt = fs[k + 1] if k + 1 < len(fs) else None
                gap_h = ((nxt["dep"] - f["arr"]).total_seconds()
                         / 3600 if nxt else None)
                if f["to"] == base and (gap_h is None
                                        or gap_h >= 10):
                    chains.append(chain)
                    chain = []
            if chain:
                chains.append(chain)
            for ch in chains:
                kinds = {_night(f) for f in ch}
                if kinds == {True, False}:
                    _unit(ch[0]["arow"],
                          "смешение ночных и дневных в цепочке",
                          f"{emp}: цепочка "
                          f"{ch[0]['no']}…{ch[-1]['no']} несёт и "
                          f"ночную, и дневную смену")
    elif template == "мягкий-предел-дней-подряд":
        bound = int(p["bound"])
        from datetime import datetime as _dt
        for emp, fs in by_emp.items():
            days = sorted({f["dep"].date() for f in fs})
            arow_of = {f["dep"].date(): f["arow"] for f in fs}
            run = 0
            prev = None
            for d in days:
                run = run + 1 if (prev is not None
                                  and (d - prev).days == 1) else 1
                if run > bound:
                    _unit(arow_of[d],
                          f"день сверх предела серии ({run} > "
                          f"{bound})",
                          f"{emp}: рабочий день {d} — "
                          f"{run}-й подряд")
                prev = d
    elif template == "неудовлетворённая-выполнимая-заявка":
        _s2, _sh2, crews = _table(dataset, p["crews_sheet"])
        _s3, _sh3, reqs = _table(dataset, p["requests_sheet"])
        if crews is None or reqs is None:
            raise DataChecksError("листов экипажей/заявок нет")
        emps = [str(c.get("Табельный номер"))
                for c in crews["rows"]]
        for i, rq in enumerate(reqs["rows"], start=2):
            emp = str(rq.get("Табельный номер") or "").strip()
            day = str(rq.get("Дата") or "").strip()
            busy = [f for f in by_emp.get(emp, [])
                    if f["dep"].date().isoformat() == day]
            if not busy:
                continue
            others_free = any(
                not any(f["dep"].date().isoformat() == day
                        for f in by_emp.get(o, []))
                for o in emps if o != emp)
            if others_free:
                _unit(busy[0]["arow"],
                      "выполнимая заявка не удовлетворена",
                      f"{emp}: заявка на {day}, назначение есть, "
                      f"свободный сотрудник существует")
    else:                                    # pragma: no cover
        raise DataChecksError(f"неизвестный мягкий шаблон "
                              f"{template!r}")
    return len(rows), finds


def run_spec(spec: dict, dataset: dict) -> tuple[int, list[dict]]:
    """Исполнение спецификации: (записей рассмотрено, находки без
    id/серьёзности/класса — их назначает сборка отчёта). Покрытие окон
    форм истории (1.18.3) отдаёт run_spec_ex."""
    n, finds, _coverage = run_spec_ex(spec, dataset)
    return n, finds


def run_spec_ex(spec: dict, dataset: dict,
                history_ctx: dict | None = None
                ) -> tuple[int, list[dict], dict | None]:
    """Исполнение спецификации с покрытием окон: (записей рассмотрено,
    находки, window_coverage форм истории либо None). `history_ctx` —
    контекст истории (history_context); не передан — строится из
    набора без ответов анкеты (решения о нулевой истории тогда нет)."""
    p = spec["params"]
    template = spec["template"]
    if isinstance(template, str) and template.startswith(("история-", "горизонт-")):
        # Ревизия 1.17: проверки листа «История» живут в своём модуле;
        # 1.18: там же — объявленный горизонт из «Свойств» и присутствие.
        from cdpm import history_section as _hist
        n, finds = _hist.run_check(template, p, dataset)
        return n, finds, None
    if template in HISTORY_FORM_TEMPLATES:
        sid, sheet, table = _table(dataset, p["sheet"])
        ctx = history_ctx if history_ctx is not None \
            else history_context(dataset, None)
        return _run_history_form(template, p, sid, sheet, table["rows"],
                                 ctx)
    n, finds = _run_plain_spec(spec, dataset)
    return n, finds, None


def _run_plain_spec(spec: dict, dataset: dict) -> tuple[int, list[dict]]:
    p = spec["params"]
    template = spec["template"]
    sid, sheet, table = _table(dataset, p["sheet"])
    rows = table["rows"]
    ev = (lambda col: lambda i, row:
          f"{sid}!{col}{i} = «{row.get(col, '')}»")

    if template in WAVE_SOFT_TEMPLATES:
        return _run_wave_soft(template, p, dataset, sid, sheet, rows)

    if template == "значение-существует":
        # Справочное правило «код X есть в реестре»: находка одна —
        # когда значения нет ВОВСЕ (живой прогон 3.3 №2: компилятор
        # без этого шаблона инвертировал смысл через перечень-значений
        # и дал десятки тысяч ложных находок на один код).
        found = any((r.get(p["column"]) or "").strip() == p["value"]
                    for r in rows)
        bad = [] if found else [(2, rows[0] if rows else {})]
        return len(rows), _findings(
            bad, sid, sheet, p["column"],
            lambda r: f"значение «{p['value']}» не встречается в "
                      f"колонке «{p['column']}»",
            lambda i, r: f"{sid}/{sheet}: колонка «{p['column']}» не "
                         f"содержит «{p['value']}» ни в одной строке")

    if template == "обязательное-поле":
        bad = [(i, r) for i, r in enumerate(rows, start=2)
               if not (r.get(p["column"]) or "").strip()]
        return len(rows), _findings(
            bad, sid, sheet, p["column"],
            lambda r: f"пустое обязательное поле «{p['column']}»",
            ev(p["column"]))

    if template == "перечень-значений":
        from cdpm.data_scope import identity_hint
        allowed = set(p["allowed"])
        bad = [(i, r) for i, r in enumerate(rows, start=2)
               if (r.get(p["column"]) or "").strip() not in allowed]
        findings = _findings(
            bad, sid, sheet, p["column"],
            lambda r: f"значение «{r.get(p['column'], '')}» вне "
                      f"перечня {sorted(allowed)}"
                      + identity_hint((r.get(p['column']) or '').strip(), allowed),
            ev(p["column"]))
        for finding, (_, row) in zip(findings, bad):
            finding["scope_value"] = (row.get(p["column"]) or "").strip()
        return len(rows), findings

    if template == "значение-в-диапазоне":
        lo, hi = p.get("min"), p.get("max")
        bad = []
        for i, r in enumerate(rows, start=2):
            v = parse_num(r.get(p["column"], ""))
            if v is None or (lo is not None and v < lo) \
                    or (hi is not None and v > hi):
                bad.append((i, r))
        return len(rows), _findings(
            bad, sid, sheet, p["column"],
            lambda r: f"значение «{r.get(p['column'], '')}» вне "
                      f"диапазона [{lo}; {hi}]",
            ev(p["column"]))

    if template == "уникальность":
        seen: dict[tuple, tuple[int, dict]] = {}
        bad = []
        for i, r in enumerate(rows, start=2):
            key = tuple((r.get(c) or "").strip() for c in p["columns"])
            if key in seen:
                bad.append((i, r))
            else:
                seen[key] = (i, r)
        cols = ", ".join(p["columns"])

        def _kind(r: dict) -> str:
            """Точный повтор строки сворачиваем без потерь, повтор
            ключа при РАЗНОМ содержимом требует решения авиакомпании —
            потребителю (мост А, вопрос сессии COSMOS 2026-08-22)
            нужно различие прямо в находке."""
            key = tuple((r.get(c) or "").strip()
                        for c in p["columns"])
            _first_i, first = seen[key]
            return ("точный повтор строки" if first == r
                    else "повтор ключа при РАЗНОМ содержимом")

        return len(rows), _findings(
            bad, sid, sheet, None,
            lambda r: f"повтор ключа ({cols}) — {_kind(r)}",
            lambda i, r: f"{sid}!строка {i} повторяет строку "
                         f"{seen[tuple((r.get(c) or '').strip() for c in p['columns'])][0]}"
                         f" ({_kind(r)})")

    if template == "ссылка-существует":
        _, _, ref = _table(dataset, p["ref_sheet"])
        known = {(r.get(p["ref_column"]) or "").strip()
                 for r in ref["rows"]}
        bad = [(i, r) for i, r in enumerate(rows, start=2)
               if (r.get(p["column"]) or "").strip() not in known]
        return len(rows), _findings(
            bad, sid, sheet, p["column"],
            lambda r: f"значение «{r.get(p['column'], '')}» не "
                      f"найдено в {p['ref_sheet']}/{p['ref_column']}",
            ev(p["column"]))

    if template == "действительность-на-дату":
        _, _, ref = _table(dataset, p["ref_sheet"])
        until: dict[str, datetime] = {}
        for r in ref["rows"]:
            key = (r.get(p["ref_key_column"]) or "").strip()
            d = parse_date(r.get(p["ref_until_column"], ""))
            if key and d and (key not in until or d > until[key]):
                until[key] = d
        bad = []
        for i, r in enumerate(rows, start=2):
            key = (r.get(p["key_column"]) or "").strip()
            d = parse_date(r.get(p["date_column"], ""))
            u = until.get(key)
            if d is None or u is None or u < d:
                bad.append((i, r))
        return len(rows), _findings(
            bad, sid, sheet, p["key_column"],
            lambda r: f"«{r.get(p['key_column'], '')}» недействителен "
                      f"на {r.get(p['date_column'], '')} по "
                      f"{p['ref_sheet']}",
            lambda i, r: f"{sid}!строка {i}: {p['date_column']}="
                         f"«{r.get(p['date_column'], '')}», предел="
                         f"«{until.get((r.get(p['key_column']) or '').strip())}»")

    if template in ("минимальный-разрыв-часов",
                    "максимальная-длительность-часов"):
        bad = []
        if template == "максимальная-длительность-часов":
            limit = float(p["max_hours"])
            for i, r in enumerate(rows, start=2):
                a = parse_moment(r.get(p["start_column"], ""))
                b = parse_moment(r.get(p["end_column"], ""))
                if a is None or b is None \
                        or (b - a).total_seconds() > limit * 3600 \
                        or b < a:
                    bad.append((i, r))
            text = (lambda r: f"длительность вне лимита "
                              f"{limit} ч или времена не читаются")
        else:
            limit = float(p["min_hours"])
            groups: dict[str, list] = {}
            for i, r in enumerate(rows, start=2):
                groups.setdefault(
                    (r.get(p["group_column"]) or "").strip(),
                    []).append((i, r))
            # Доказательство интервала обязано называть ОБА конца:
            # конец предыдущей записи и начало следующей. Живая сверка
            # 2026-08-19: доказательство одной строкой показывало
            # длительность самого рейса, и сверщик резонно судил
            # «доказательство не о том интервале».
            prev_of: dict[int, tuple[int, dict]] = {}
            for items in groups.values():
                spans = []
                for i, r in items:
                    a = parse_moment(r.get(p["start_column"], ""))
                    b = parse_moment(r.get(p["end_column"], ""))
                    if a is None or b is None:
                        bad.append((i, r))
                    else:
                        spans.append((a, b, i, r))
                spans.sort()
                # 4б: тройка области сужает счёт до разрывов «в
                # базе» либо «вне базы»: место разрыва — из колонки
                # места ПРЕДЫДУЩЕЙ записи, базовость — сравнением с
                # базой приписки той же записи. Пустое место или
                # база — пара не судится (не догадка), это честно
                # сузит счёт, а полнота колонок ловится проверкой
                # «обязательное-поле» отдельно.
                from cdpm import model as _m
                want = _m.canon_area_qualifier(p["area"]) \
                    if "area" in p else None
                for (a1, b1, i1, r1), (a2, b2, i2, r2) in zip(
                        spans, spans[1:]):
                    if want is not None:
                        place = (r1.get(p["place_column"]) or "") \
                            .strip()
                        base = (r1.get(p["base_column"]) or "").strip()
                        if not place or not base:
                            continue
                        at_home = place == base
                        if at_home != (want == "база"):
                            continue
                    if (a2 - b1).total_seconds() < limit * 3600:
                        bad.append((i2, r2))
                        prev_of[i2] = (i1, r1)
            text = (lambda r: f"разрыв меньше {limit} ч или времена "
                              f"не читаются")

            def _gap_evidence(i, r):
                prev = prev_of.get(i)
                if prev is None:       # времена не читаются
                    return (f"{sid}!строка {i}: "
                            f"{p['start_column']}="
                            f"«{r.get(p['start_column'], '')}», "
                            f"{p['end_column']}="
                            f"«{r.get(p['end_column'], '')}»")
                pi, pr = prev
                return (f"{sid}!конец предыдущей (строка {pi}): "
                        f"{p['end_column']}="
                        f"«{pr.get(p['end_column'], '')}» → начало "
                        f"следующей (строка {i}): {p['start_column']}="
                        f"«{r.get(p['start_column'], '')}»")

            return len(rows), _findings(bad, sid, sheet,
                                        p["start_column"], text,
                                        _gap_evidence)
        return len(rows), _findings(
            bad, sid, sheet, p["start_column"], text,
            lambda i, r: f"{sid}!строка {i}: "
                         f"{p['start_column']}=«{r.get(p['start_column'], '')}», "
                         f"{p['end_column']}=«{r.get(p['end_column'], '')}»")

    if template == "счёт-состава-группы":
        need = int(p["count"])
        role_col = p.get("role_column")
        role_val = (str(p.get("role_value")).strip().casefold()
                    if role_col else None)
        groups2: dict[str, list] = {}
        for i, r in enumerate(rows, start=2):
            key = (r.get(p["group_column"]) or "").strip()
            groups2.setdefault(key, []).append((i, r))
        relation2 = p.get("relation", "eq")
        distinct_col = p.get("distinct_column")
        bad = []
        counts: dict[int, tuple[str, int]] = {}
        for key, items in sorted(groups2.items()):
            pool = items
            if role_col:
                pool = [(i, r) for i, r in items
                        if (r.get(role_col) or "").strip()
                        .casefold() == role_val]
            if distinct_col:
                # Счёт РАЗЛИЧНЫХ значений (заявка моста 2026-08-23:
                # дубль человека на рейсе проходил счёт записей).
                got = len({(r.get(distinct_col) or "").strip()
                           for _i, r in pool
                           if (r.get(distinct_col) or "").strip()})
            else:
                got = len(pool)
            violated = (got > need if relation2 == "max"
                        else got < need if relation2 == "min"
                        else got != need)
            if violated:
                i0, r0 = items[0]
                bad.append((i0, r0))
                counts[i0] = (key, got)

        word = {"max": "не больше", "min": "не меньше"} \
            .get(relation2, "требуется")

        unit_word = (f"различных «{distinct_col}»" if distinct_col
                     else "записей")

        def _cnt_evidence(i, r):
            key, got = counts[i]
            role = f" с {role_col}=«{p['role_value']}»" \
                if role_col else ""
            return (f"{sid}!группа {p['group_column']}=«{key}»: "
                    f"{unit_word}{role} {got}, {word} {need}")

        text_of = {"max": f"состав группы больше {need}",
                   "min": f"состав группы меньше {need}"} \
            .get(relation2, f"состав группы не равен {need}")
        return len(rows), _findings(
            bad, sid, sheet, p["group_column"],
            lambda r: text_of, _cnt_evidence)

    if template == "выходных-в-месяце":
        from datetime import date, timedelta
        need = int(p["min_days_off"])
        gc, cs, ce = (p["group_column"], p["start_column"],
                      p["end_column"])
        spans = []
        for i, r in enumerate(rows, start=2):
            a = parse_moment(r.get(cs, ""))
            b = parse_moment(r.get(ce, ""))
            if a is None or b is None or b < a:
                raise DataChecksError(
                    f"{sid}!строка {i}: времена не читаются "
                    f"({cs}=«{r.get(cs, '')}», {ce}=«{r.get(ce, '')}»)"
                    f" — счёт выходных не судится по нечитаемым "
                    f"временам")
            spans.append((i, r, a.date(), b.date()))
        if not spans:
            raise DataChecksError(
                "универсум пуст: лист без записей — полных месяцев "
                "нет")
        lo = min(a for _i, _r, a, _b in spans)
        hi = max(b for _i, _r, _a, b in spans)
        months = []
        y, mth = lo.year, lo.month
        while (y, mth) <= (hi.year, hi.month):
            first = date(y, mth, 1)
            nxt = (date(y + 1, 1, 1) if mth == 12
                   else date(y, mth + 1, 1))
            last = nxt - timedelta(days=1)
            if lo <= first and last <= hi:
                months.append((first, last))
            y, mth = (y + 1, 1) if mth == 12 else (y, mth + 1)
        if not months:
            raise DataChecksError(
                "универсум пуст: диапазон данных "
                f"{lo}–{hi} не накрывает ни одного полного "
                "календарного месяца")
        touched: dict[tuple[str, tuple], set] = {}
        by_emp: dict[str, tuple[int, dict]] = {}
        for i, r, a, b in spans:
            emp = (r.get(gc) or "").strip()
            by_emp.setdefault(emp, (i, r))
            for first, last in months:
                d = max(a, first)
                stop = min(b, last)
                while d <= stop:
                    touched.setdefault((emp, (first, last)),
                                       set()).add(d)
                    d += timedelta(days=1)
        bad = []
        notes: dict[int, str] = {}
        for emp in sorted(by_emp):
            i, r = by_emp[emp]
            for first, last in months:
                total = (last - first).days + 1
                off = total - len(touched.get((emp, (first, last)),
                                              set()))
                if off < need:
                    bad.append((i, r))
                    notes[i] = (f"{emp}: {first.strftime('%Y-%m')} — "
                                f"выходных {off} при норме ≥ {need} "
                                f"(суток {total}, занято "
                                f"{total - off})")
        return len(rows), _findings(
            bad, sid, sheet, gc,
            lambda r: f"выходных в месяце меньше {need}",
            lambda i, r: f"{sid}!{notes.get(i, '')}")

    raise DataChecksError(f"шаблон {template!r} без исполнителя")


# ── Формы истории сотрудника: серия ночных смен, отдых в окне ──

HISTORY_FORM_TEMPLATES = ("серия-ночных-смен", "отдых-в-скользящем-окне")
#: Решения анкеты трёх исходов (1.17.2), при которых нулевая история у
#: сотрудника без записи — явно принятое допущение (зеркало
#: vmp.history_assumption.DECISIONS_CONTINUE).
ZERO_HISTORY_DECISIONS = ("continue_zero", "continue_partial")
_ISO = "%Y-%m-%dT%H:%M"
#: Виды суток календаря истории, целиком свободные от работы.
_REST_DAY_KINDS = ("day_off", "leave", "sick")


def _hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def history_context(dataset: dict, svod: dict | None = None) -> dict:
    """Контекст истории сотрудников для форм истории (1.18.3): раздел
    истории, собранный тем же кодом, что у пакета (лист «История»,
    журнал, календарь, решение анкеты трёх исходов), срез горизонта из
    «Свойств» (planSince) и разрешение нулевой подстановки.

    {"as_of": datetime | None, "as_of_source": str | None,
     "records": {табельный: запись с историей}, "decision": dict | None,
     "zero_allowed": bool} — записи без единого известного поля
    (сотрудник без строки) в records не входят."""
    from cdpm import history_section as _hist
    tables = dataset.get("tables") if isinstance(dataset, dict) else None
    tables = tables if isinstance(tables, dict) else {}
    section = _hist.history_section({"tables": tables}, svod)
    records: dict[str, dict] = {}
    decision = None
    as_of_iso = None
    if section:
        for rec in section.get("employees") or []:
            eid = str(rec.get("employee_id") or "").strip()
            if eid and rec.get("provenance") is not None \
                    and _hist.has_any_value(rec):
                records[eid] = rec
        decision = section.get("user_decision")
        as_of_iso = section.get("as_of")
    else:
        as_of_iso = _hist.plan_since(tables)
    as_of = parse_moment(as_of_iso) if as_of_iso else None
    zero_allowed = bool(isinstance(decision, dict)
                        and decision.get("decision") in ZERO_HISTORY_DECISIONS)
    return {"as_of": as_of,
            "as_of_source": ("planSince листа «Свойства»" if as_of
                             else None),
            "records": records, "decision": decision,
            "zero_allowed": zero_allowed}


def _zero_reason(ctx: dict) -> str:
    d = ctx.get("decision") or {}
    return (f"допущение нулевой истории, решение анкеты "
            f"{d.get('question')} (ответ {d.get('key')}): "
            f"{d.get('decision')}")


def _provenance_text(rec: dict) -> str:
    p = rec.get("provenance") or {}
    if isinstance(p.get("rows"), list):
        return (f"лист «{p.get('sheet')}» ({p.get('sid')}), строки "
                f"{', '.join(str(x) for x in p['rows'])}")
    return f"лист «{p.get('sheet')}» ({p.get('sid')}), строка {p.get('row')}"


def _coverage_of(windows: list[dict], as_of, as_of_source: str) -> dict:
    """window_coverage (1.18.3) в единицах «сотрудник × окно»: счётчики
    — пересчёт по адресному списку."""
    return {"expected": len(windows),
            "evaluated": sum(1 for w in windows
                             if w["status"] == "evaluated"),
            "unknown": sum(1 for w in windows if w["status"] == "unknown"),
            "granularity": "windows", "expected_unit": "windows",
            "horizon_start": as_of.strftime(_ISO),
            "horizon_source": as_of_source,
            "windows": windows}


def _subtract(lo, hi, knowns: list) -> list:
    """Часть [lo, hi), не накрытая известными интервалами (объединение,
    отсортировано)."""
    out = []
    cur = lo
    for a, b in knowns:
        if b <= cur:
            continue
        if a >= hi:
            break
        if a > cur:
            out.append((cur, min(a, hi)))
        cur = max(cur, b)
        if cur >= hi:
            break
    if cur < hi:
        out.append((cur, hi))
    return out


def _duties_by_employee(rows: list, sid: str, ec: str, cs: str,
                        ce: str) -> dict[str, list]:
    """Смены по сотруднику в порядке времени: (строка, запись,
    начало, конец). Fail-closed: нечитаемое время или смена без
    сотрудника — честный отказ, серия и отдых по таким записям не
    судятся (не догадка)."""
    by_emp: dict[str, list] = {}
    for i, r in enumerate(rows, start=2):
        a = parse_moment(r.get(cs, ""))
        b = parse_moment(r.get(ce, ""))
        if a is None or b is None or b < a:
            raise DataChecksError(
                f"{sid}!строка {i}: времена не читаются "
                f"({cs}=«{r.get(cs, '')}», {ce}=«{r.get(ce, '')}») — "
                f"смены с нечитаемыми временами не судятся")
        emp = (r.get(ec) or "").strip()
        if not emp:
            raise DataChecksError(
                f"{sid}!строка {i}: пустой «{ec}» — смена без "
                f"сотрудника не судится")
        by_emp.setdefault(emp, []).append((i, r, a, b))
    for lst in by_emp.values():
        lst.sort(key=lambda x: (x[2], x[3], x[0]))
    return by_emp


def in_night_window(minute_of_day: int, nf: int, nt: int) -> bool:
    """Минута суток в окне ночи [от, до); при от > до окно переходит
    через полночь (то же прочтение, что у решателя)."""
    return (nf <= minute_of_day < nt) if nf <= nt \
        else (minute_of_day >= nf or minute_of_day < nt)


def _run_history_form(template: str, p: dict, sid: str, sheet: str,
                      rows: list, ctx: dict | None = None
                      ) -> tuple[int, list[dict], dict]:
    """Исполнители форм истории сотрудника по листу смен одного
    человека. Находка адресована строке листа; пустой лист —
    «универсум пуст» (считать нечего — не молчаливый ноль).

    Ревизия 1.18.3: окна, зависящие от событий ДО среза горизонта
    (серия ночных смен, начатая до среза; окна отдыха, пересекающие
    срез), судятся адресно — по одному окну на сотрудника (единица
    «сотрудник × окно») — и уходят в window_coverage: доказано
    (история передана либо явно принятое допущение нулевой истории по
    ответу анкеты) или неизвестно (истории и решения нет — с причиной).
    Неизвестное окно находок не даёт, кроме доказанной нижней границы
    нарушения по данным горизонта; окна внутри горизонта судятся как
    прежде. Срез — planSince «Свойств», иначе первые сутки листа."""
    from datetime import timedelta
    ec, cs, ce = (p["employee_column"], p["start_column"],
                  p["end_column"])
    if not rows:
        raise DataChecksError(
            "универсум пуст: лист без записей — смен сотрудников нет")
    by_emp = _duties_by_employee(rows, sid, ec, cs, ce)
    ctx = ctx or {"as_of": None, "records": {}, "decision": None,
                  "zero_allowed": False}
    records: dict = ctx.get("records") or {}
    zero_allowed = bool(ctx.get("zero_allowed"))
    as_of = ctx.get("as_of")
    as_of_source = ctx.get("as_of_source")
    if as_of is None:
        first = min(a for items in by_emp.values() for _i, _r, a, _b in items)
        as_of = datetime(first.year, first.month, first.day)
        as_of_source = ("первые сутки листа (planSince в «Свойствах» не "
                        "объявлен)")
    finds: list[dict] = []
    windows: list[dict] = []

    def _find(row_i: int, text: str, evidence: str):
        finds.append({"target": {"sid": sid, "sheet": sheet,
                                 "row": row_i, "column": cs},
                      "text": text, "evidence": evidence})

    def _no_history_reason(what: str) -> str:
        return (f"у сотрудника нет истории ({what} до среза "
                f"{as_of:%Y-%m-%d %H:%M} неизвестна) и решения по вопросу "
                f"анкеты о трёх исходах нет")

    if template == "серия-ночных-смен":
        nf, nt = int(p["night_from_min"]), int(p["night_to_min"])
        bound = int(p["max_consecutive"])
        for emp in sorted(by_emp):
            ivs = by_emp[emp]
            is_night = [in_night_window(a.hour * 60 + a.minute, nf, nt)
                        for _i, _r, a, _b in ivs]
            leading = 0
            while leading < len(ivs) and is_night[leading]:
                leading += 1
            rec = records.get(emp)
            # Окно на границе среза: серия, начатая до среза.
            carried: int | None = None
            status, outcome = "unknown", None
            if leading == 0:
                status = "evaluated"
                carried = 0
                first_start = ivs[0][2]
                reason = (f"первая смена горизонта "
                          f"({first_start:%Y-%m-%d %H:%M}) не ночная — "
                          f"серия ночных смен до среза прервана, окно "
                          f"доказано данными листа")
            elif ivs[0][2] < as_of:
                reason = (f"смены сотрудника начинаются до среза "
                          f"{as_of:%Y-%m-%d %H:%M} — серия истории на срез "
                          f"с листом не складывается, окно не судится")
            elif rec is not None:
                series = rec.get("series") if isinstance(rec.get("series"),
                                                         dict) else {}
                nights = series.get("consecutive_night_duties")
                if isinstance(nights, int) and not isinstance(nights, bool):
                    carried = nights
                    status = "evaluated"
                    reason = (f"история передана ({_provenance_text(rec)}): "
                              f"серия ночных смен на срез {nights}; с "
                              f"{leading} ночными сменами горизонта — "
                              f"{nights + leading} подряд при пределе "
                              f"{bound}")
                else:
                    reason = (f"история передана ({_provenance_text(rec)}), "
                              f"но серия ночных смен на срез не указана — "
                              f"окно не судится")
            elif zero_allowed:
                carried = 0
                status = "evaluated"
                reason = (f"{_zero_reason(ctx)} — серия ночных смен на срез "
                          f"принята нулевой; {leading} ночных смен горизонта "
                          f"при пределе {bound}")
            else:
                reason = _no_history_reason("серия ночных смен")
            total = (carried or 0) + leading
            if total > bound:
                outcome = "violation"       # доказано и при неизвестном переносе
            elif status == "evaluated":
                outcome = "pass"
            end = ivs[max(leading - 1, 0)][3]
            windows.append({
                "employee": emp, "kind": "серия ночных смен на срезе",
                "start_inclusive": as_of.strftime(_ISO),
                "end_exclusive": max(end, as_of + timedelta(minutes=1))
                .strftime(_ISO),
                "status": status, "outcome": outcome, "reason": reason})
            series_items: list = []
            first_run = True
            for k, item in enumerate(ivs + [None]):
                if item is not None and is_night[k]:
                    series_items.append(item)
                    continue
                carry = carried if (first_run and carried and leading) else 0
                length = len(series_items) + carry
                if series_items and length > bound:
                    i0, _r0, a0, _b0 = series_items[0]
                    i1, _r1, a1, _b1 = series_items[-1]
                    carry_note = (f" ({len(series_items)} в горизонте + "
                                  f"{carry} на срез по истории)"
                                  if carry else "")
                    _find(i0,
                          f"ночных смен подряд {length} больше "
                          f"предела {bound}",
                          f"{sid}!{sheet}: {ec}=«{emp}», серия "
                          f"ночных смен со строки {i0} "
                          f"({a0:%Y-%m-%d %H:%M}) по строку {i1} "
                          f"({a1:%Y-%m-%d %H:%M}) — {length} "
                          f"подряд{carry_note} при пределе {bound}; ночь "
                          f"— начало в окне [{_hm(nf)}, {_hm(nt)})")
                if series_items:
                    first_run = False
                series_items = []
        return len(rows), finds, _coverage_of(windows, as_of, as_of_source)

    # «отдых-в-скользящем-окне»
    from cdpm.data_report import union_spans
    days, need = int(p["window_days"]), int(p["min_rest_min"])
    windows_total = 0
    for emp in sorted(by_emp):
        ivs = by_emp[emp]
        lo = ivs[0][2].date()
        hi = max(b for _i, _r, _a, b in ivs).date()
        day0 = datetime(lo.year, lo.month, lo.day)
        # Промежутки отдыха по листу: от начала данных (или среза, если
        # он раньше — внутри горизонта лист полон) до конца последних
        # суток данных.
        known_from = min(day0, as_of)
        cursor = known_from
        rests: list = []
        for _i, _r, a, b in ivs:
            if a > cursor:
                rests.append((cursor, a))
            if b > cursor:
                cursor = b
        rests.append((cursor, datetime(hi.year, hi.month, hi.day)
                      + timedelta(days=1)))

        def _best(w_from, w_to, spans):
            return max((int((min(r_to, w_to) - max(r_from, w_from))
                            .total_seconds() // 60)
                        for r_from, r_to in spans), default=0)

        d = day0
        while (d + timedelta(days=days - 1)).date() <= hi:
            w_from, w_to = d, d + timedelta(days=days)
            windows_total += 1
            best = _best(w_from, w_to, rests)
            if best < need:
                row_i = next((i for i, _r, a, _b in ivs if a >= w_from),
                             ivs[0][0])
                last_day = (w_to - timedelta(days=1)).date()
                _find(row_i,
                      f"в окне {days} суток нет непрерывного отдыха "
                      f"{need} мин",
                      f"{sid}!{sheet}: {ec}=«{emp}», окно "
                      f"{w_from.date()}–{last_day}: лучший промежуток "
                      f"отдыха {max(best, 0)} мин при норме ≥ {need}; "
                      f"смены окна со строки {row_i}")
            d += timedelta(days=1)
        if days < 2:
            continue           # суточные окна срез не пересекают
        # Окно на границе среза: суточные окна [срез − k, срез − k + W),
        # k = 1..W−1 — часть окна лежит до среза. Известное до среза:
        # отдых от конца последней смены истории до среза, целиком
        # свободные сутки календаря, сама последняя смена (по размаху);
        # при допущении нулевой истории — до среза смен не было.
        rec = records.get(emp)
        known_rest: list = []
        known_occ: list = []
        if rec is not None:
            ld = rec.get("last_duty") if isinstance(rec.get("last_duty"),
                                                    dict) else {}
            last_end = parse_moment(ld.get("end")) if ld.get("end") else None
            span = ld.get("span_min")
            if last_end is not None and last_end < as_of:
                known_rest.append((last_end, as_of))
                if isinstance(span, int) and not isinstance(span, bool) \
                        and span > 0:
                    known_occ.append((last_end - timedelta(minutes=span),
                                      last_end))
            rest_days = 0
            for cell in (rec.get("calendar") or []):
                if not isinstance(cell, dict) \
                        or cell.get("kind") not in _REST_DAY_KINDS:
                    continue
                day = parse_date(str(cell.get("date") or ""))
                if day is not None and day < as_of:
                    known_rest.append((day, min(day + timedelta(days=1),
                                                as_of)))
                    rest_days += 1
            if last_end is None:
                last_note = ("конец последней смены не указан — занятость "
                             "до среза неизвестна")
            elif last_end >= as_of:
                last_note = (f"конец последней смены "
                             f"{last_end:%Y-%m-%d %H:%M} не раньше среза — "
                             f"дефект данных истории, занятость до среза "
                             f"неизвестна")
            else:
                last_note = (f"конец последней смены "
                             f"{last_end:%Y-%m-%d %H:%M}, отдых до среза "
                             f"{int((as_of - last_end).total_seconds() // 60)}"
                             f" мин")
            base = (f"история передана ({_provenance_text(rec)}): {last_note}"
                    + (f"; свободных суток по календарю до среза: "
                       f"{rest_days}" if rest_days else ""))
        elif zero_allowed:
            known_rest.append((as_of - timedelta(days=days), as_of))
            base = (f"{_zero_reason(ctx)} — до среза смен не было, отдых "
                    f"до среза принят непрерывным")
        else:
            base = _no_history_reason("занятость")
        known_pre = union_spans(known_rest + known_occ)
        all_rests = union_spans(known_rest + rests)
        n_pass = n_viol = n_unk = 0
        for k in range(1, days):
            w_from = as_of - timedelta(days=k)
            w_to = w_from + timedelta(days=days)
            best = _best(w_from, w_to, all_rests)
            if best >= need:
                n_pass += 1
                continue
            if _subtract(w_from, known_from, known_pre):
                n_unk += 1        # до начала известного есть неизвестное
                continue
            n_viol += 1
            row_i = next((i for i, _r, a, _b in ivs if a >= w_from),
                         ivs[0][0])
            last_day = (w_to - timedelta(days=1)).date()
            _find(row_i,
                  f"в окне {days} суток нет непрерывного отдыха "
                  f"{need} мин",
                  f"{sid}!{sheet}: {ec}=«{emp}», окно "
                  f"{w_from.date()}–{last_day} пересекает срез "
                  f"{as_of:%Y-%m-%d %H:%M}: лучший промежуток отдыха "
                  f"{max(best, 0)} мин при норме ≥ {need}; {base}; смены "
                  f"окна со строки {row_i}")
        status = "unknown" if n_unk else "evaluated"
        outcome = ("violation" if n_viol
                   else ("pass" if status == "evaluated" else None))
        windows.append({
            "employee": emp,
            "kind": f"окна отдыха из {days} суток, пересекающие срез",
            "start_inclusive": (as_of - timedelta(days=days - 1))
            .strftime(_ISO),
            "end_exclusive": (as_of + timedelta(days=days - 1))
            .strftime(_ISO),
            "daily_windows": days - 1,
            "status": status, "outcome": outcome,
            "reason": (f"{base}; суточных окон на границе среза "
                       f"{days - 1}: прошло {n_pass}, нарушено {n_viol}, "
                       f"неизвестно {n_unk}")})
    if windows_total == 0:
        raise DataChecksError(
            f"универсум пуст: диапазон данных ни у одного сотрудника "
            f"не накрывает окна из {days} суток")
    return len(rows), finds, _coverage_of(windows, as_of, as_of_source)


# ── Встроенные проверки качества: чистый код, без модели ──

def builtin_checks(dataset: dict) -> list[dict]:
    """Дубль полных строк и формат дато-подобных колонок — для каждого
    листа набора; применимость определяется самими данными."""
    specs = []
    for sid, sheets in sorted(dataset.get("tables", {}).items()):
        for sheet, table in sorted(sheets.items()):
            ref = f"{sid}/{sheet}"
            specs.append({
                "id": f"C-дубль-{sid}-{sheet}",
                "basis": {"builtin": "дубль"},
                "template": "уникальность",
                "params": {"sheet": ref, "columns": table["columns"]},
                "statement": f"Строки листа «{sheet}» ({sid}) не "
                             f"повторяются целиком",
                "unit": "строка", "severity": "СЕРЬЁЗНОЕ",
                "status": "скомпилирована"})
            for col in table["columns"]:
                values = [(r.get(col) or "").strip()
                          for r in table["rows"]]
                filled = [v for v in values if v]
                # Прореха плотной колонки: пустая ячейка там, где
                # колонка заполнена в ≥90% строк, — аномалия данных,
                # семантики листа не требует. Живой Лайт run-0040:
                # рейс PL300 без времени вылета — восьмой подложенный
                # дефект — прошёл линию данных (компилятор не
                # предложил обязательное-поле на эту колонку) и
                # остановил конвертер потребителя; классу нужна
                # встроенная проверка, не зависящая от прицела модели.
                if filled and len(filled) < len(values) \
                        and len(filled) >= max(10, len(values) * 0.9):
                    specs.append({
                        "id": f"C-пропуск-{sid}-{sheet}-{col}",
                        "basis": {"builtin": "пропуск данных"},
                        "template": "обязательное-поле",
                        "params": {"sheet": ref, "column": col},
                        "statement": f"Колонка «{col}» листа "
                                     f"«{sheet}» ({sid}) заполнена в "
                                     f"{len(filled)} из "
                                     f"{len(values)} строк — пустые "
                                     f"ячейки аномальны",
                        # КРИТИЧЕСКОЕ: запись с пустым значением
                        # плотной колонки непригодна к использованию
                        # — очистка обязана ВЫЧЕСТЬ строку
                        # (СЕРЬЁЗНОЕ строку не вычитает, и PL300 без
                        # времени вылета уехал бы в пакет снова).
                        "unit": "ячейка", "severity": "КРИТИЧЕСКОЕ",
                        "status": "скомпилирована"})
                dates = [v for v in filled if parse_date(v)]
                if filled and len(dates) >= max(2, len(filled) * 0.8) \
                        and len(dates) < len(filled):
                    specs.append({
                        "id": f"C-формат-{sid}-{sheet}-{col}",
                        "basis": {"builtin": "формат"},
                        "template": "обязательное-поле",   # заглушка
                        "_format_column": col,
                        "params": {"sheet": ref, "column": col},
                        "statement": f"Колонка «{col}» листа «{sheet}» "
                                     f"({sid}) — даты единого формата",
                        "unit": "ячейка", "severity": "СЕРЬЁЗНОЕ",
                        "status": "скомпилирована"})
    # Ревизия 1.17 (A7): лист «История» — встроенные проверки раздела
    # истории сотрудника (табельный в «Экипажах», канон значений, конец
    # последней смены не позже среза, полнота по сотрудникам).
    from cdpm import history_section as _hist
    specs += _hist.check_specs(dataset)
    # Ревизия 1.18 (C-27b): объявленный горизонт из «Свойств» — класс
    # «пропуск данных»: без ключей планирования пользователь обязан
    # видеть «горизонт не объявлен» в отчёте данных, а не в отказе
    # потребителя.
    specs += _hist.horizon_check_specs(dataset)
    return specs


def _run_format_check(spec: dict, dataset: dict):
    p = spec["params"]
    sid, sheet, table = _table(dataset, p["sheet"])
    col = p["column"]
    bad = [(i, r) for i, r in enumerate(table["rows"], start=2)
           if (r.get(col) or "").strip()
           and parse_date(r.get(col, "")) is None]
    return len(table["rows"]), _findings(
        bad, sid, sheet, col,
        lambda r: f"значение «{r.get(col, '')}» не разбирается как "
                  f"дата",
        lambda i, r: f"{sid}!{col}{i} = «{r.get(col, '')}»")


# ── Компилятор: модель предлагает, код валидирует ──

from cdpm import model as m

_PROMPT_COMPILE = """Ты — компилятор проверок данных авиакомпании.
По КАЖДОЙ строке свода предложи машинную проверку по одному из
шаблонов — или откажись с причиной, если строка не проверяется по
имеющимся таблицам. Верни СТРОГО один JSON-объект:

{{"checks": [{{
  "rule": "<id строки свода, ровно как в списке>",
  "template": "<шаблон из перечня>",
  "params": {{<параметры шаблона; листы — в виде "SID/Лист">}},
  "statement": "<что проверяется, одним предложением>",
  "unit": "<единица счёта>",
  "severity": "БЛОКИРУЮЩЕЕ" | "КРИТИЧЕСКОЕ" | "СЕРЬЁЗНОЕ"
}}, …],
 "skipped": [{{"rule": "<id>", "reason": "<почему не проверяется>"}}]}}

Правила:
- ровно один ответ на каждую строку списка: либо в checks, либо в
  skipped; выдумывать листы и колонки нельзя — только из схемы;
- шаблоны и их параметры: {templates};
- «минимальный-разрыв-часов» применяй ТОЛЬКО к событиям ОДНОГО
  человека (группировка по табельному номеру: смены, назначения);
  к общему расписанию рейсов НЕ применяй — соседние рейсы летают
  разные борта и экипажи, их близость не дефект данных;
- у «минимальный-разрыв-часов» есть НЕОБЯЗАТЕЛЬНАЯ тройка области
  (все три или ни одной): place_column — колонка МЕСТА разрыва
  (аэропорт конца предыдущей записи), base_column — колонка базы
  приписки, area — СТРОГО «база» либо «вне базы»; используй её для
  правил, действующих только в базе или только вне базы;
- «счёт-состава-группы» — для правил численности («ровно N …»):
  group_column задаёт ЕДИНИЦУ, чью численность требует правило;
  состав экипажа — группа по РЕЙСУ (одному вылету); смена или
  цепочка ОДНОГО человека группой численности экипажа НЕ является —
  в ней всегда один человек, и счёт по ней считает его рейсы, а не
  экипаж; count — требуемое целое, необязательные role_column +
  role_value сужают счёт до записей этой роли; необязательный
  relation: "eq" (умолчание, ровно N), "max" (не больше N — посадки
  за смену) либо "min" (не меньше N — нормы резерва); необязательный
  distinct_column считает РАЗЛИЧНЫЕ значения колонки (различных
  сотрудников — по табельному номеру), а не записи;
- «серия-ночных-смен» и «отдых-в-скользящем-окне» — ТОЛЬКО к листу
  смен ОДНОГО человека (employee_column — табельный номер,
  start_column/end_column — начало и конец смены); night_from_min и
  night_to_min — минуты суток 0..1439 (окно через полночь законно),
  max_consecutive — целое ≥ 0, window_days — сутки окна ≥ 1,
  min_rest_min — минуты отдыха ≥ 1;
- лист-СВОДКА (каждая строка — готовый ИТОГ с колонками вида
  норма/факт/нехватка, например «Резерв»: строка = база × сутки)
  проверяется сравнением колонки-итога с константой: нормы резерва —
  ТОЛЬКО «значение-в-диапазоне» по колонке «Нехватка» с max 0;
  «счёт-состава-группы» и «значение-существует» к листам-сводкам НЕ
  применяй — счёт строк итогов и поиск числа в колонке не проверяют
  правило;
- если строку честно нельзя проверить этими таблицами — skipped;
- в ответе ТОЛЬКО JSON-объект: без пояснений, без файлов, без плана.

Схема набора данных:
{schema}

Строки свода ({n}):
{rules}""" + m.ID_IMMUTABILITY_RULE


def _schema_text(dataset: dict) -> str:
    lines = []
    hist = history_sheet_keys(dataset)
    for sid, sheets in sorted(dataset.get("tables", {}).items()):
        for sheet, table in sorted(sheets.items()):
            if (str(sid), str(sheet)) in hist:
                # Листы раздела истории (1.17) компилятору не
                # предлагаются: правила свода по ним не судятся.
                lines.append(f"- {sid}/{sheet}: лист раздела истории "
                             f"сотрудника (исходное состояние на начало "
                             f"горизонта) — не цель проверок правил, "
                             f"его читают встроенные проверки истории")
                continue
            lines.append(f"- {sid}/{sheet}: колонки "
                         f"{', '.join(table['columns'])}; строк "
                         f"{len(table['rows'])}")
            # Словарь малокардинальных колонок: компилятор обязан
            # целиться настоящими значениями данных, а не словами из
            # текста правила («Пилот» против Captain/FirstOfficer —
            # живой класс 12 ложных нарушений, 2026-08-23).
            for col in table["columns"]:
                vals = sorted({(r.get(col) or "").strip()
                               for r in table["rows"]} - {""})
                if 0 < len(vals) <= 8 \
                        and all(len(v) <= 24 for v in vals):
                    lines.append(f"    · {col}: значения "
                                 f"{', '.join(vals)}")
    return "\n".join(lines)


#: Листы ПЛОДОВ ПЛАНИРОВАНИЯ: смены (наряды) и назначения экипажа.
#: Их производит решатель; во входной книге их обычно нет, а при
#: план-факте — бывают, и тогда правила по ним проверяются честно.
_SOLUTION_SHEETS = ("смен", "наряд", "назначен", "ротаци", "duti", "roster")


def history_sheet_keys(dataset: dict) -> set[tuple[str, str]]:
    """(sid, лист) листов РАЗДЕЛА ИСТОРИИ (спецификация 1.17): «История»
    и «Утверждённые назначения». Это исходное состояние на начало
    горизонта, а не плоды планирования и не справочник: их читают
    встроенные проверки истории и раздел manifest.history, а правила
    свода по ним не судятся. Живой случай 05.09 (набор H0-КИ): лист
    «Утверждённые назначения» с одним закреплением H01→PL101 сошёл за
    лист назначений, и правило «экипаж — ровно 2 пилота» объявило
    БЛОКИРУЮЩЕЕ нарушение на закреплении одного пилота."""
    from cdpm import history_section as _hist
    # 1.18.2: лист «Присутствие» — тоже раздел истории (свидетельства
    # местоположения), правила свода по нему не судятся.
    aliases = (set(_hist.HISTORY_SHEETS) | set(_hist.APPROVED_SHEETS)
               | set(_hist.JOURNAL_SHEETS) | set(_hist.CALENDAR_SHEETS)
               | set(_hist.PRESENCE_SHEETS))
    keys: set[tuple[str, str]] = set()
    for sid, sheets in (dataset.get("tables") or {}).items():
        if not isinstance(sheets, dict):
            continue
        for name in sheets:
            if str(name).strip().lower() in aliases:
                keys.add((str(sid), str(name)))
    return keys


def _has_solution_sheets(dataset: dict) -> bool:
    hist = history_sheet_keys(dataset)
    for sid, sheets in (dataset.get("tables") or {}).items():
        for name in sheets:
            if (str(sid), str(name)) in hist:
                continue
            low = str(name).casefold()
            if any(mark in low for mark in _SOLUTION_SHEETS):
                return True
    return False


def _judged_on_solution(rule: dict, dataset: dict) -> bool:
    """Правило судится РЕШЕНИЕМ, а не входной книгой: у его семьи
    каталог ОБЪЯВИЛ исполнителя (валидная посадка на семью базы
    шаблонов, `model._executor_covers` — закон 1), и книга не несёт
    плодов планирования, по которым можно было бы проверить его
    соблюдение. Полнота самих справочников остаётся за встроенными
    проверками — они смотрят данные, а не соблюдение ограничений.

    Судит именно ОБЪЯВЛЕНИЕ: подтверждённость исполнителя (закон 2,
    K06-барьер) линию данных не касается — семья без подтверждения
    остановлена барьером со своим следом, а не переведена во «входную
    книгу» (контракт П0 §12.11, находка F2 ревью 12.09.2026)."""
    from cdpm import model as _m
    return _m._executor_covers(rule) and not _has_solution_sheets(dataset)


def compile_checks(svod: dict, dataset: dict, llm,
                   control=None) -> list[dict]:
    """Спецификации проверок для строк свода HARD/DATA/CASE: модель
    предлагает, код валидирует; блокированные и неподъёмные строки —
    честные «не скомпилирована» с причиной. Полнота: каждая строка
    получает ровно один исход."""
    rules = [r for r in svod.get("rules", [])
             if r.get("kind") in dr.CHECKABLE_KINDS]
    svod_rules = {r["id"]: r for r in svod.get("rules", [])}
    specs: list[dict] = []
    seq: dict[str, int] = {}

    def spec_id(rule_id: str) -> str:
        n = seq.get(rule_id, 0)
        seq[rule_id] = n + 1
        return f"C-{rule_id}-{n}"

    todo = []
    for r in rules:
        if r.get("blockers"):
            specs.append({
                "id": spec_id(r["id"]), "basis": {"rule": r["id"]},
                "status": "не скомпилирована",
                "statement": r.get("statement", ""), "unit": "строка",
                "reason": "строка свода блокирована вопросом анкеты — "
                          "до ответа авиакомпании не компилируется"})
        elif _judged_on_solution(r, dataset):
            # Правило ограничивает РЕШЕНИЕ, а плодов планирования
            # (смен, назначений) во входной книге нет: проверять по
            # ней нечего, и попытка кончается ЛОЖНОЙ проверкой. Живой
            # случай 03.09: предел полётной смены скомпилировался по
            # листу рейсов и объявил нарушением все 27 рейсов подряд —
            # «длительность вне лимита 12 ч или времена не читаются»
            # на полуторачасовом рейсе. Ложная критическая находка
            # хуже отсутствия проверки: соблюдение таких правил судит
            # сертификат РЕШЕНИЯ.
            specs.append({
                "id": spec_id(r["id"]), "basis": {"rule": r["id"]},
                "status": "не скомпилирована",
                "statement": r.get("statement", ""), "unit": "строка",
                "reason": "правило судится решением (сертификат): его "
                          "исполняет решатель, а плодов планирования "
                          "во входной книге нет"})
        else:
            todo.append(r)

    schema = _schema_text(dataset)
    templates = json.dumps(
        {k: list(v) for k, v in TEMPLATES.items()}, ensure_ascii=False)
    for start in range(0, len(todo), COMPILE_CHUNK):
        if control:
            control()          # пауза/остановка между чанками (ПС-1)
        batch = todo[start:start + COMPILE_CHUNK]
        listing = "\n".join(
            f"{r['id']} | {r['kind']} | {r['statement']}"
            for r in batch)
        prompt = _PROMPT_COMPILE.format(
            templates=templates, schema=schema, n=len(batch),
            rules=listing)
        reply = None
        for attempt in (1, 2):
            ask = prompt if attempt == 1 else (
                prompt + "\n\nПредыдущий ответ не разобрался. Верни "
                         "СТРОГО один JSON-объект и ничего больше.")
            try:
                reply = llm_mod.parse_json_reply(llm(ask))
                break
            except llm_mod.LLMReplyError:
                continue
        if reply is None:
            # Fail-closed чанком, не смертью прогона: строки чанка —
            # честные «не скомпилирована» (живой прогон 3.3
            # 2026-08-18: канал ответил пояснением вместо JSON и один
            # сбой убил конвейер).
            for r in batch:
                specs.append({
                    "id": spec_id(r["id"]), "basis": {"rule": r["id"]},
                    "status": "не скомпилирована",
                    "statement": r.get("statement", ""),
                    "unit": "строка",
                    "reason": "канал компилятора не дал разборного "
                              "JSON после повтора"})
            continue
        got_checks = {c.get("rule"): c
                      for c in reply.get("checks", [])}
        got_skips = {s.get("rule"): s
                     for s in reply.get("skipped", [])}
        for r in batch:
            rid = r["id"]
            if rid in got_checks:
                c = got_checks[rid]
                spec = {"id": spec_id(rid), "basis": {"rule": rid},
                        "template": c.get("template"),
                        "params": c.get("params") or {},
                        "statement": (c.get("statement")
                                      or r.get("statement", "")),
                        "unit": c.get("unit") or "строка",
                        "severity": c.get("severity", "КРИТИЧЕСКОЕ"),
                        "status": "скомпилирована"}
                reason = validate_spec(spec, dataset, svod_rules)
                if reason:
                    spec.update(status="не скомпилирована",
                                reason=f"спецификация компилятора "
                                       f"отбракована: {reason}")
                    spec.pop("template", None)
                    spec.pop("params", None)
                specs.append(spec)
            elif rid in got_skips:
                specs.append({
                    "id": spec_id(rid), "basis": {"rule": rid},
                    "status": "не скомпилирована",
                    "statement": r.get("statement", ""),
                    "unit": "строка",
                    "reason": f"компилятор: "
                              f"{got_skips[rid].get('reason') or 'без причины'}"})
            else:
                specs.append({
                    "id": spec_id(rid), "basis": {"rule": rid},
                    "status": "не скомпилирована",
                    "statement": r.get("statement", ""),
                    "unit": "строка",
                    "reason": "компилятор не дал ответа по строке — "
                              "неполный пакет"})
    return specs


# ── Сборка отчёта по контракту ДЛ-1 ──

def _partial_reason(coverage: dict) -> str:
    """Причина статуса «частично прогнана» словами: сколько окон на
    границе среза не доказано и у кого; причины по окнам — в
    window_coverage."""
    unknown = [w for w in coverage.get("windows", [])
               if w.get("status") == "unknown"]
    emps = sorted({str(w.get("employee")) for w in unknown})
    return (f"окна на границе среза не доказаны у {len(unknown)} из "
            f"{coverage.get('expected')} (сотрудники: {', '.join(emps)}): "
            f"нет истории и решения анкеты либо история неполна — "
            f"причины по окнам в window_coverage")


def build_data_report(customer: str, dataset: dict,
                      specs: list[dict],
                      sanity_threshold: bool = True,
                      svod: dict | None = None) -> dict:
    """Исполнение спецификаций и сборка машинной модели отчёта 3.3.
    Класс находки — из основания проверки; счётчики пересчитываются
    контрактной функцией. `sanity_threshold=False` отключает свёртку
    лавин (сертификация решений, ступень 1 моста: массовые нарушения
    там — штатный случай, каждое обязано быть в счёте). `svod` — модель
    с ответами анкеты: формы истории (1.18.3) читают из неё решение о
    нулевой истории; без свода решения нет."""
    checks_out: list[dict] = []
    findings_out: list[dict] = []
    n_finding = 0
    history_ctx: dict | None = None
    for spec in specs:
        entry = {"id": spec["id"], "basis": spec["basis"],
                 "statement": spec["statement"], "unit": spec["unit"]}
        if spec.get("template"):
            # Шаблон нужен потребителям отчёта: очистка данных не
            # вычитает строки по РЕЛЯЦИОННЫМ находкам (свойство пары
            # строк, не строки) — находка вехи Б моста, 2026-08-22.
            entry["template"] = spec["template"]
        if spec.get("status") != "скомпилирована":
            entry.update(status="не прогнана", rows_checked=0,
                         reason=spec.get("reason", "не скомпилирована"))
            checks_out.append(entry)
            continue
        # Проверка, которая не судится на ЭТИХ данных (универсум
        # пуст, времена не читаются), — честное «не прогнана:
        # причина», а не обвал всего отчёта: одно месячное правило
        # не вправе валить сертификацию однодневного решения
        # (живой красный Г-S4, 2026-08-24).
        coverage = None
        try:
            if spec.get("basis", {}).get("builtin") == "формат":
                rows_checked, raw = _run_format_check(spec, dataset)
            else:
                if spec.get("template") in HISTORY_FORM_TEMPLATES \
                        and history_ctx is None:
                    history_ctx = history_context(dataset, svod)
                rows_checked, raw, coverage = run_spec_ex(
                    spec, dataset, history_ctx)
        except DataChecksError as e:
            entry.update(status="не прогнана", rows_checked=0,
                         reason=str(e))
            checks_out.append(entry)
            continue
        except (ValueError, TypeError, KeyError) as e:
            # Живой отказ run-0060 (2026-08-27): компилятор отдал
            # нечисловой параметр шаблона ('reg_limit' строкой), и
            # float() валил ВЕСЬ отчёт. Кривая спецификация — «не
            # прогнана» со следом, а не обвал стадии: fail-closed
            # повопросно, остальные проверки живут.
            entry.update(status="не прогнана", rows_checked=0,
                         reason="спецификация неисполнима: %s: %s"
                                % (type(e).__name__, e))
            checks_out.append(entry)
            continue
        if rows_checked <= 0:
            entry.update(status="не прогнана", rows_checked=0,
                         reason="на листе нет записей — проверять "
                                "нечего")
            checks_out.append(entry)
            continue
        if coverage is not None:
            # Ревизия 1.18.3: покрытие окон на границе среза — адресно;
            # неизвестные окна дают «частично прогнана» с причиной,
            # доказанные находки при этом сохраняются.
            entry["window_coverage"] = coverage
        if coverage is not None and coverage["unknown"]:
            entry.update(status="частично прогнана",
                         rows_checked=rows_checked,
                         reason=_partial_reason(coverage))
        else:
            entry.update(status="прогнана", rows_checked=rows_checked)
        checks_out.append(entry)
        # Санитарный порог (живой прогон 3.3 №2): проверка,
        # сработавшая на >20%% записей (при >=50 рассмотренных), почти
        # наверняка неверно нацелена компилятором — её лавина честно
        # сворачивается в ОДНУ мета-находку с долей и примерами.
        if sanity_threshold and rows_checked >= 50 \
                and len(raw) > 0.2 * rows_checked:
            sample = "; ".join(f.get("evidence", "")[:60]
                               for f in raw[:3])
            raw = [{"target": raw[0]["target"],
                    "text": f"проверка сработала на {len(raw)} из "
                            f"{rows_checked} записей "
                            f"({100 * len(raw) // rows_checked}%) — "
                            f"вероятно, неверная нацеленность "
                            f"компилятора; требует уточнения "
                            f"постановки",
                    "evidence": f"примеры: {sample}"}]
        basis = spec.get("basis") or {}
        klass = ("нарушение правила" if basis.get("rule")
                 else basis["builtin"])
        for f in raw:
            n_finding += 1
            finding = {"id": f"F-{n_finding:04d}", "check": spec["id"],
                       "severity": spec.get("severity", "КРИТИЧЕСКОЕ"),
                       "class": klass, **f}
            if basis.get("rule"):
                finding["rule_refs"] = [basis["rule"]]
            findings_out.append(finding)
    doc = {"customer": customer,
           "sources": dataset.get("sources", []),
           "checks": checks_out,
           "findings": findings_out}
    doc["counters"] = dr.metrics_of(doc)
    return doc
