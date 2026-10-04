# -*- coding: utf-8 -*-
"""Стенд искусственных ВМП: ступени с заранее написанным приговором.

Зеркало лестницы для второй половины границы (решение владельца
2026-09-03): лестница судила «документы → ВМП», стенд судит
«ВМП → задача». Материал выдуман (авиакомпания «Ладога-Тест», базы
LED и KGD, внебазовый порт ARH, тип судна ЛТ-100), ответ написан
вместе с материалом и коду проверок (vmp/formulation_check.py) не
известен: ступень описывает, ЧТО обязана сказать проверка — класс,
уровень, строку и поле, — а не как она это находит.

Каждая ступень — полный пакет в том виде, в каком его получает
приёмник: форма ВМП, машинная модель, данные набора, манифест,
целевые. Строки формы и модели строятся из одной записи, поэтому
расходиться не могут.

Ступени:
  С0  здоровая постановка              → принята, находок нет
  С1а противоречие жёстких (один вид)  → пара строк поимённо
  С1б противоречие жёстких (два вида)  → пара строк поимённо
  С2  пустая допустимость              → только решателем (материал)
  С3  коды баз и типов судов вне данных
  С4  критерий без метрики / метрика не той задачи
  С5  критерий без ставки / ставки без версии
  С6  единица размера нарушения не объявлена / не своей семьи
  С7  ярусы вразнобой
  С8  вырожденность (предупреждения; постановка принята)
  С9  пакет без формы
"""
from __future__ import annotations

import copy
import hashlib
import json

CUSTOMER = "Ладога-Тест"
SOURCES = [{"sid": "S1", "file": "pravila-ladoga-test.md",
            "sha256": hashlib.sha256(b"ladoga-test").hexdigest()}]
DAYS = ("2026-10-01", "2026-10-02", "2026-10-03")   # горизонт 3 суток


def _src(anchor: str) -> list[dict]:
    return [{"sid": "S1", "anchor": anchor}]


# ------------------------------------------------------------ записи

class Row:
    """Одна запись ВМП; из неё строятся и строка формы, и правило
    модели (или целевая)."""

    def __init__(self, kind: str, rid: str, title: str, statement: str,
                 anchor: str, zone: str = "PAIRING", family=None,
                 params=(), weight=None, unit=None, no_penalty=False,
                 scope=None, role=None, machine=None, **crit):
        self.kind, self.id, self.title = kind, rid, title
        self.statement, self.anchor, self.zone = statement, anchor, zone
        self.family, self.params = family, list(params)
        self.weight, self.unit, self.no_penalty = weight, unit, no_penalty
        self.scope, self.role, self.machine = scope, role, machine
        self.crit = crit

    # --- модель ---------------------------------------------------
    def model_rule(self) -> dict:
        r = {"id": self.id, "title": self.title, "statement": self.statement,
             "zone": self.zone, "kind": self.kind, "sources": _src(self.anchor)}
        if self.params:
            r["params"] = [
                {"name": p["name"], "value": p["value"], "unit": p["unit"],
                 "relation": p["relation"],
                 "as_written": p.get("quote") or str(p["value"]),
                 **({"qualifier": p["qualifier"]} if p.get("qualifier") else {})}
                for p in self.params]
        if self.family:
            r["template_fit"] = {
                "family": self.family, "category": "A",
                "params": {p["name"]: {"value": p["value"],
                                       "quote": p.get("quote") or str(p["value"])}
                           for p in self.params}}
        if self.weight is not None:
            r["weight"] = self.weight
        if self.scope:
            r["scope"] = dict(self.scope)
        if self.machine:
            r.update(copy.deepcopy(self.machine))
        return r

    def model_objective(self) -> dict:
        o = {"id": self.id, "title": self.title, "statement": self.statement,
             "zone": self.zone, "sources": _src(self.anchor)}
        o.update(copy.deepcopy(self.crit))
        return o

    # --- форма ----------------------------------------------------
    def form_row(self) -> dict:
        row = {"id": self.id, "title": self.title, "statement": self.statement,
               "sources": _src(self.anchor)}
        if self.family:
            row["template"] = {"family": self.family, "category": "A"}
        else:
            row["template"] = None
            row["gap"] = "шаблон стенда не требуется"
        if self.params:
            row["params"] = [{"name": p["name"], "value": p["value"],
                              "unit": p["unit"],
                              "quote": p.get("quote") or str(p["value"])}
                             for p in self.params]
        if self.kind == "SOFT" and not self.no_penalty:
            row["penalty"] = {"coefficient": self.weight,
                              "violation_unit": self.unit}
        if self.scope:
            row["scope"] = dict(self.scope)
        machine = {"kind": self.kind}
        if self.machine:
            machine.update(copy.deepcopy(self.machine))
        row["machine"] = machine
        return row

    def form_definition(self) -> dict:
        row = {"id": self.id, "title": self.title, "statement": self.statement,
               "sources": _src(self.anchor),
               "role": self.role or ("registry" if self.kind == "DATA"
                                     else "term")}
        machine = {"kind": self.kind}
        if self.machine:
            machine.update(copy.deepcopy(self.machine))
        if self.params:
            machine["params"] = self.model_rule()["params"]
        row["machine"] = machine
        return row

    def form_criterion(self) -> dict:
        c = {"id": self.id, "title": self.title, "statement": self.statement,
             "sources": _src(self.anchor)}
        for k in ("metric", "direction", "kind", "rate", "version",
                  "effective_from", "tier", "tolerance"):
            if k in self.crit:
                c[k] = copy.deepcopy(self.crit[k])
        c.setdefault("tier", 1)
        c["machine"] = {"zone": self.zone}
        return c


def P(name, value, unit, relation, qualifier=None, quote=None) -> dict:
    p = {"name": name, "value": value, "unit": unit, "relation": relation}
    if qualifier:
        p["qualifier"] = qualifier
    if quote:
        p["quote"] = quote
    return p


# ------------------------------------------------------------ мир С0

def base_rows() -> dict:
    """Здоровая постановка: определения, параметры мира, части."""
    return {
        "definitions": [
            Row("DEF", "D-BASES", "Базы приписки",
                "Базы приписки экипажей: LED, KGD.", "п.1.1", zone="BOTH"),
            Row("DEF", "D-FLEET", "Типы воздушных судов",
                "Парк: воздушные суда типа ЛТ-100.", "п.1.2", zone="BOTH"),
        ],
        "world": [
            Row("HARD", "W-CREW", "Состав экипажа",
                "Экипаж воздушного судна состоит из 2 пилотов.", "п.2.1",
                zone="BOTH", family="crew_composition",
                params=[P("crew_size", 2, "count", "eq", quote="2 пилотов")],
                scope={"pairing": "состав каждой цепочки",
                       "rostering": "состав каждого назначения"}),
            # Семья флота (владелец 2026-09-03): допуск по типу судна,
            # перечень типов — параметр-код; коды из документа.
            Row("HARD", "W-FLEET", "Допуск по типу судна",
                "Рейс на судне типа ЛТ-100 выполняет только пилот с "
                "действующим допуском на ЛТ-100.", "п.2.2",
                zone="BOTH", family="fleet_exclusions",
                params=[P("fleet_types", "ЛТ-100", "code_list", "eq",
                          quote="ЛТ-100")],
                scope={"pairing": "каждый участок цепочки",
                       "rostering": "каждое назначение"}),
        ],
        "pairing_hard": [
            Row("HARD", "H-DUTY", "Предел смены",
                "Продолжительность лётной смены не более 13 часов (780 минут).",
                "п.3.1", family="duty_limits",
                params=[P("max_duty_duration", 780, "duration_minutes", "max",
                          quote="780 минут")]),
            Row("HARD", "H-TURN", "Стыковка",
                "Минимальное время стыковки между рейсами 45 минут.",
                "п.3.2", family="connection_windows",
                params=[P("min_turnaround", 45, "duration_minutes", "min",
                          quote="45 минут")]),
        ],
        "pairing_soft": [
            Row("SOFT", "S-LAYOVER", "Ночёвки вне базы",
                "Ночёвка вне базы нежелательна: штраф 10 за каждую ночь.",
                "п.4.1", family="layover_penalty", weight=10, unit="night"),
        ],
        "pairing_criteria": [
            Row("OBJ", "O-P1", "Критерий пейринга",
                "Минимум ночей вне базы; цена ночи 100 условных единиц.",
                "п.5.1", zone="PAIRING", metric="layover_nights_total",
                direction="minimize", tier=1,
                rate={"value": 100, "per": "ночь вне базы",
                      "as_written": "100 условных единиц за ночь"}),
        ],
        "rostering_hard": [
            Row("HARD", "H-REST-HOME", "Отдых дома",
                "Отдых между сменами в базе не менее 12 часов (720 минут).",
                "п.6.1", zone="ROSTERING", family="rest_floors",
                params=[P("min_rest_home", 720, "duration_minutes", "min",
                          qualifier="база", quote="720 минут")]),
            Row("HARD", "H-REST-OUT", "Отдых вне базы",
                "Отдых между сменами вне базы не менее 10 часов (600 минут).",
                "п.6.2", zone="ROSTERING", family="rest_floors",
                params=[P("min_rest_outstation", 600, "duration_minutes",
                          "min", qualifier="вне базы", quote="600 минут")]),
        ],
        "rostering_soft": [
            Row("SOFT", "S-BALANCE", "Равномерность нагрузки",
                "Разброс налёта между пилотами нежелателен: штраф 5 за час.",
                "п.7.1", zone="ROSTERING", family="balanced_workload",
                weight=5, unit="hour"),
        ],
        "rostering_criteria": [
            Row("OBJ", "O-R1", "Критерий ростеринга",
                "Минимум разброса кредитных часов; цена часа 20 условных "
                "единиц.", "п.8.1", zone="ROSTERING",
                metric="credit_hours_spread", direction="minimize", tier=1,
                rate={"value": 20, "per": "час разброса",
                      "as_written": "20 условных единиц за час"}),
        ],
    }


def base_data(*, crew_at=(("LED", 4), ("KGD", 4)), aircraft="ЛТ-100",
              type_column=True) -> dict:
    crew_rows = []
    n = 0
    for base, k in crew_at:
        for _ in range(k):
            n += 1
            crew_rows.append({"Табельный номер": "E%02d" % n,
                              "Фамилия И.О.": "Пилот %02d" % n,
                              "Должность": "КВС" if n % 2 else "Второй пилот",
                              "База": base})
    flights = []
    legs = (("LT101", "LED", "ARH", "08:00", "10:00"),
            ("LT102", "ARH", "LED", "11:00", "13:00"),
            ("LT201", "KGD", "LED", "09:00", "11:00"),
            ("LT202", "LED", "KGD", "14:00", "16:00"))
    for day in DAYS:
        for num, dep, arr, t1, t2 in legs:
            row = {"Номер рейса": num, "Дата": day, "Вылет": dep,
                   "Прилёт": arr, "Время вылета": t1, "Время прилёта": t2}
            if type_column:
                row["Тип ВС"] = aircraft
            flights.append(row)
    fcols = ["Номер рейса", "Дата", "Вылет", "Прилёт", "Время вылета",
             "Время прилёта"] + (["Тип ВС"] if type_column else [])
    return {"customer": CUSTOMER, "tables": {"D1": {
        "Рейсы": {"columns": fcols, "rows": flights},
        "Экипажи": {"columns": ["Табельный номер", "Фамилия И.О.",
                                "Должность", "База"], "rows": crew_rows},
        # Колонки листа свойств — как читает конвертер приёмника
        # (webapp.task_package.to_payload): «ключ», «значение».
        "Свойства": {"columns": ["ключ", "значение"],
                     "rows": [{"ключ": "planSince",
                               "значение": DAYS[0] + "T00:00"},
                              {"ключ": "planUntil",
                               "значение": DAYS[-1] + "T23:59"}]},
    }}}


# ------------------------------------------------------------ сборка

def assemble(rows: dict, data: dict | None, *, with_form: bool = True,
             name: str = "С0") -> dict:
    """Пакет приёмника (словарь как у webapp.task_package.load) из
    записей ступени."""
    model_rules = [r.model_rule() for r in rows["definitions"]]
    model_rules += [r.model_rule() for r in rows["world"]]
    for part in ("pairing", "rostering"):
        model_rules += [r.model_rule() for r in rows[part + "_hard"]]
        model_rules += [r.model_rule() for r in rows[part + "_soft"]]
    objectives = []
    seen = set()
    for part in ("pairing", "rostering"):
        for r in rows[part + "_criteria"]:
            if r.id not in seen:
                seen.add(r.id)
                objectives.append(r.model_objective())
    model = {"meta": {"customer": CUSTOMER, "mode": 1, "cdpm_version": "стенд",
                      "revision": 1},
             "sources": copy.deepcopy(SOURCES), "rules": model_rules,
             "questions": [], "objectives": objectives}
    form = None
    if with_form:
        form = {"form_version": 1, "customer": CUSTOMER,
                "currency": "условные единицы",
                "sources": copy.deepcopy(SOURCES),
                "common": {"definitions": [r.form_definition()
                                           for r in rows["definitions"]],
                           "world": [r.form_row() for r in rows["world"]]}}
        for part in ("pairing", "rostering"):
            form[part] = {
                "hard": [r.form_row() for r in rows[part + "_hard"]],
                "soft": [r.form_row() for r in rows[part + "_soft"]],
                "criteria": [r.form_criterion()
                             for r in rows[part + "_criteria"]]}
        form["withdrawn"], form["open_questions"] = [], []
        form["answered_questions"] = []
    objective = {part: {"objectives": [
        {"id": r.id, "title": r.title, "direction": r.crit.get("direction"),
         "metric": r.crit.get("metric"), "statement": r.statement}
        for r in rows[part + "_criteria"]]} for part in ("pairing", "rostering")}
    manifest = {"spec_version": "1.12", "source_run": "vmp-stand/" + name,
                "customer": CUSTOMER, "files": []}
    return {"path": "vmp-stand/" + name, "manifest": manifest,
            "objective": objective, "model": model,
            "clean_data": data, "form": form}


def _find(rows: dict, rid: str) -> Row:
    for group in rows.values():
        for r in group:
            if r.id == rid:
                return r
    raise KeyError(rid)


def _expect(cls, level, rid, field, part=None) -> dict:
    return {"class": cls, "level": level, "id": rid, "field": field,
            "part": part}


# ------------------------------------------------------------ ступени

def step_C0():
    rows = base_rows()
    return assemble(rows, base_data(), name="С0"), {
        "title": "здоровая постановка",
        "verdict": "accepted", "findings": []}


def step_C1a():
    rows = base_rows()
    rows["rostering_hard"].append(
        Row("HARD", "H-REST-HOME-2", "Отдых дома (таблица норм)",
            "Отдых между сменами в базе не менее 11 часов (660 минут).",
            "табл.6 стр.2", zone="ROSTERING", family="rest_floors",
            params=[P("min_rest_home", 660, "duration_minutes", "min",
                      qualifier="база", quote="660 минут")]))
    return assemble(rows, base_data(), name="С1а"), {
        "title": "противоречие жёстких одного вида",
        "verdict": "rejected",
        "findings": [_expect("contradiction", "stop", "H-REST-HOME",
                             "params.min_rest_home")]}


def step_C1b():
    rows = base_rows()
    rows["definitions"].append(
        Row("DATA", "R-DATA-REST", "Норма отдыха дома из файла норм",
            "Отдых в базе: 12:30.", "нормы.xlsx стр.4", zone="ROSTERING",
            params=[P("min_rest_home", 750, "duration_minutes", "min",
                      qualifier="база", quote="12:30")]))
    return assemble(rows, base_data(), name="С1б"), {
        "title": "противоречие жёстких двух видов",
        "verdict": "rejected",
        "findings": [_expect("contradiction", "stop", "H-REST-HOME",
                             "params.min_rest_home")]}


def step_C2():
    """Пустая допустимость: правила по отдельности здоровы, но состав
    экипажа 3 при рейсах из KGD и двух пилотах в KGD — ни одно
    расписание жёстким не удовлетворяет. Проверки постановки этого
    класса не видят по построению; приговор выносит решатель, и его
    ступень исполняется отдельным прогоном по отмашке владельца."""
    rows = base_rows()
    w = _find(rows, "W-CREW")
    w.statement = "Экипаж воздушного судна состоит из 3 пилотов."
    w.params = [P("crew_size", 3, "count", "eq", quote="3 пилотов")]
    return assemble(rows, base_data(crew_at=(("LED", 4), ("KGD", 2))),
                    name="С2"), {
        "title": "пустая допустимость (только решателем)",
        "verdict": "accepted", "findings": [],
        "requires_solver": True, "solver_verdict": "INFEASIBLE"}


def step_C3():
    rows = base_rows()
    _find(rows, "D-BASES").statement = "Базы приписки экипажей: LED, KGD, MMK."
    _find(rows, "D-FLEET").statement = \
        "Парк: воздушные суда типов ЛТ-100 и ЛТ-200."
    rows["pairing_hard"].append(
        Row("HARD", "H-DUTY-MMK", "Предел смены в базе MMK",
            "Для смен из базы MMK продолжительность не более 12 часов "
            "(720 минут).", "п.3.3", family="duty_limits",
            params=[P("max_duty_duration", 720, "duration_minutes", "max",
                      qualifier="база MMK", quote="720 минут")]))
    # Параметр-перечень типов правила допуска называет тип, которого в
    # книге нет: связь «правило ↔ данные» через параметр, не определение.
    rows["world"].append(
        Row("HARD", "W-FLEET-X", "Допуск по типу судна ЛТ-300",
            "Рейс на судне типа ЛТ-300 выполняет только пилот с допуском "
            "на ЛТ-300.", "п.2.3", zone="BOTH", family="fleet_exclusions",
            params=[P("fleet_types", "ЛТ-100, ЛТ-300", "code_list", "eq",
                      quote="ЛТ-300")],
            scope={"pairing": "каждый участок цепочки",
                   "rostering": "каждое назначение"}))
    return assemble(rows, base_data(), name="С3"), {
        "title": "коды баз и типов судов вне данных",
        "verdict": "rejected",
        "findings": [
            _expect("code_not_in_data", "stop", "D-BASES", "statement"),
            _expect("code_not_in_data", "stop", "D-FLEET", "statement"),
            _expect("code_not_in_data", "stop", "H-DUTY-MMK",
                    "params.max_duty_duration.qualifier"),
            _expect("code_not_in_data", "stop", "W-FLEET-X",
                    "params.fleet_types")]}


def step_C4():
    rows = base_rows()
    del _find(rows, "O-P1").crit["metric"]
    _find(rows, "O-R1").crit["metric"] = "total_pairing_cost"
    return assemble(rows, base_data(), name="С4"), {
        "title": "критерий без метрики; метрика не той задачи",
        "verdict": "rejected",
        "findings": [
            _expect("criterion_no_metric", "stop", "O-P1", "metric", "pairing"),
            _expect("criterion_no_metric", "stop", "O-R1", "metric",
                    "rostering")]}


def step_C5():
    rows = base_rows()
    del _find(rows, "O-P1").crit["rate"]
    _find(rows, "O-R1").crit["kind"] = "rank_normalized_cost"
    _find(rows, "O-R1").crit["rate"] = {"rates": {"flight": 3, "work": 2}}
    return assemble(rows, base_data(), name="С5"), {
        "title": "критерий без ставки; ставки без версии",
        "verdict": "rejected",
        "findings": [
            _expect("criterion_no_rate", "stop", "O-P1", "rate", "pairing"),
            _expect("criterion_no_rate", "stop", "O-R1", "version",
                    "rostering")]}


def step_C6():
    rows = base_rows()
    _find(rows, "S-LAYOVER").unit = None
    _find(rows, "S-BALANCE").unit = "night"
    rows["pairing_soft"].append(
        Row("SOFT", "S-EXTRA", "Продлённая смена",
            "Смена длиннее 10 часов нежелательна.", "п.4.2",
            family="duty_extension_penalty", no_penalty=True))
    return assemble(rows, base_data(), name="С6"), {
        "title": "единица размера нарушения не объявлена / чужая",
        "verdict": "rejected",
        "findings": [
            _expect("violation_unit", "stop", "S-LAYOVER",
                    "penalty.violation_unit", "pairing"),
            _expect("violation_unit", "stop", "S-EXTRA", "penalty", "pairing"),
            _expect("violation_unit", "stop", "S-BALANCE",
                    "penalty.violation_unit", "rostering")]}


def step_C7():
    rows = base_rows()
    rows["pairing_criteria"].append(
        Row("OBJ", "O-P2", "Второй критерий пейринга",
            "Минимум стоимости перегонов; цена 50 за перегон.", "п.5.2",
            zone="PAIRING", metric="positioning_cost", direction="minimize",
            tier=1, rate={"value": 50, "per": "перегон"}))
    rows["rostering_criteria"] += [
        Row("OBJ", "O-R2", "Второй критерий ростеринга",
            "Минимум непокрытых мест; цена 500 за место.", "п.8.2",
            zone="ROSTERING", metric="uncovered_seats", direction="minimize",
            tier=2, rate={"value": 500, "per": "место"}),
        Row("OBJ", "O-R3", "Третий критерий ростеринга",
            "Минимум нарушений выходных; цена 30 за нарушение.", "п.8.3",
            zone="ROSTERING", metric="preferred_days_off_violations",
            direction="minimize", tier=4, rate={"value": 30, "per": "нарушение"},
            tolerance={"value": 5, "unit": "percent"}),
    ]
    return assemble(rows, base_data(), name="С7"), {
        "title": "ярусы вразнобой",
        "verdict": "rejected",
        "findings": [
            _expect("tiers_disordered", "stop", "O-P1", "tier", "pairing"),
            _expect("tiers_disordered", "stop", "O-R2", "tolerance",
                    "rostering"),
            _expect("tiers_disordered", "stop", "O-R3", "tier", "rostering")]}


def step_C8():
    rows = base_rows()
    _find(rows, "S-LAYOVER").weight = 0
    _find(rows, "S-BALANCE").weight = 0
    rows["pairing_soft"].append(
        Row("SOFT", "S-EXTRA", "Продлённая смена",
            "Смена длиннее 10 часов нежелательна: штраф 4 за смену.", "п.4.2",
            family="duty_extension_penalty", weight=4, unit="duty"))
    rows["pairing_hard"].append(
        Row("HARD", "H-SPAN", "Предел длительности цепочки",
            "Цепочка не длиннее 10 суток (14400 минут).", "п.3.4",
            family="span_accumulators",
            params=[P("max_pairing_span", 14400, "duration_minutes", "max",
                      quote="14400 минут")]))
    return assemble(rows, base_data(), name="С8"), {
        "title": "вырожденность: нулевые веса, порог длиннее горизонта",
        "verdict": "accepted",
        "findings": [
            _expect("degenerate", "warn", "S-LAYOVER", "penalty.coefficient",
                    "pairing"),
            _expect("degenerate", "warn", "S-BALANCE", "penalty.coefficient",
                    "rostering"),
            _expect("degenerate", "warn", "H-SPAN", "params.max_pairing_span")]}


def step_C9():
    rows = base_rows()
    return assemble(rows, base_data(), with_form=False, name="С9"), {
        "title": "пакет без формы ВМП",
        "verdict": "rejected",
        "findings": [_expect("no_form", "stop", None, "vmp-form.json")]}


STEPS = (("С0", step_C0), ("С1а", step_C1a), ("С1б", step_C1b),
         ("С2", step_C2), ("С3", step_C3), ("С4", step_C4), ("С5", step_C5),
         ("С6", step_C6), ("С7", step_C7), ("С8", step_C8), ("С9", step_C9))


def build_all() -> list[tuple[str, dict, dict]]:
    return [(name, *fn()) for name, fn in STEPS]


def write_step(out_dir, name: str, pkg: dict, expectation: dict) -> None:
    """Каталог ступени: члены пакета файлами + expectation.json."""
    from pathlib import Path
    d = Path(out_dir) / name
    d.mkdir(parents=True, exist_ok=True)
    members = {"manifest.json": pkg["manifest"], "model.json": pkg["model"],
               "objective.json": pkg["objective"],
               "clean-data.json": pkg["clean_data"],
               "vmp-form.json": pkg["form"]}
    for fname, body in members.items():
        p = d / fname
        if body is None:
            if p.exists():
                p.unlink()
            continue
        p.write_text(json.dumps(body, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    (d / "expectation.json").write_text(
        json.dumps(expectation, ensure_ascii=False, indent=1), encoding="utf-8")
