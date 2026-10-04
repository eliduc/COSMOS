# -*- coding: utf-8 -*-
"""Стенд каталога шаблонов: адаптеры мира и прогон задач.

Два свидетеля, намеренно независимых:

  * ЖИВОЙ КАТАЛОГ — задачи читаются из записей каталога, как их прогонит
    команда `python -m vmp.stand_tasks`; исходы обязаны совпасть с
    `vmp/stand_results.json`, и ни один `failed` не живёт вне явного
    `KNOWN_FAILED`;
  * ОБРАЗЦОВЫЕ МИРЫ этого файла — та же таблица §5.2, но своя: она
    падает от регресса ИСПОЛНИТЕЛЯ и не зависит от того, что сейчас
    лежит в посеве каталога. Расхождение двух свидетелей — вопрос к
    посеву, а не повод править числа.

Числа ожиданий под расчёт не подгоняются: расхождение — либо дефект
входа стенда (правится мир задачи), либо дефект исполнителя (доклад
владельцу и строка `KNOWN_FAILED` по его слову).
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parents[2]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

sa = pytest.importorskip("vmp.stand_adapters")
st = pytest.importorskip("vmp.stand_tasks")
ca = pytest.importorskip("vmp.catalog_access")


@pytest.fixture(autouse=True)
def _launcher_env(monkeypatch):
    """Команды этого модуля судят иное, чем окружение: здесь они идут
    «как из пусковика». Закон окружения (РВ-138, исправление 3) судит
    отдельный модуль `test_launcher_env.py` — на настоящих переменных."""
    from vmp import launcher_env
    monkeypatch.setattr(launcher_env, "missing", lambda env=None: [])


# ------------------------------------------------------------------- миры

#: Мир-1 §5.2: один сотрудник базы ST1, два рейса туда-обратно с ночёвкой
#: вне базы. 2026-10-01 — четверг; сдвиг шкалы 0.
WORLD_1 = {
    "form": "schedule",
    "crews": [{"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T1"]}],
    "flights": [
        {"id": "F1", "from": "ST1", "to": "ST2", "dep": "2026-10-01T06:00",
         "arr": "2026-10-01T08:00", "fleet_type": "T1"},
        {"id": "F2", "from": "ST2", "to": "ST1", "dep": "2026-10-02T10:00",
         "arr": "2026-10-02T12:00", "fleet_type": "T1"}],
    "params": {"max_duty_duration": 780, "min_rest_home": 660,
               "min_rest_outstation": 700, "min_turnaround": 45,
               "max_pairing_days": 7, "base_tz_offset_min": 0},
    "stand": {"w_night": 1, "nights_per_layover": 2},
    "history": None,
    "horizon": {"from": "2026-10-01", "to": "2026-10-31"},
}


def _flight(fid, frm, to, dep, arr, fleet="T1", deadhead=False):
    row = {"id": fid, "from": frm, "to": to, "dep": dep, "arr": arr,
           "fleet_type": fleet}
    if deadhead:
        row["deadhead"] = True
    return row


def _daily(prefix, frm, to, first_day, days, start, end, fleet="T1"):
    """Ежедневные рейсы: по одному в сутки, время местное шкалы меток."""
    out = []
    day = datetime.fromisoformat(first_day).date()
    for i in range(days):
        d = (day + timedelta(days=i)).isoformat()
        out.append(_flight(f"{prefix}{i + 1}", frm if i % 2 == 0 else to,
                           to if i % 2 == 0 else frm,
                           f"{d}T{start}", f"{d}T{end}", fleet))
    return out


def _calendar(first_day, days, kind="выходной", night=False, crew="P1"):
    """Посуточный календарь до горизонта: сутки без записи делают окно
    через границу горизонта НЕИЗВЕСТНЫМ (не выдержанным), поэтому мир,
    который судит скользящее окно, обязан назвать эти сутки словами."""
    d0 = datetime.fromisoformat(first_day).date()
    return [{"employee": crew, "date": (d0 + timedelta(days=i)).isoformat(),
             "kind": kind, "night": night} for i in range(days)]


def world(**patch) -> dict:
    """Мир-1 с отличиями: `params`/`stand` сливаются, прочее замещается."""
    out = copy.deepcopy(WORLD_1)
    for key, value in patch.items():
        if key in ("params", "stand") and isinstance(value, dict):
            out[key] = dict(out.get(key) or {}, **value)
        else:
            out[key] = copy.deepcopy(value)
    return out


_NIGHTS = [_flight("N1", "ST1", "ST2", "2026-10-02T23:00", "2026-10-03T01:00"),
           _flight("N2", "ST2", "ST1", "2026-10-03T23:00", "2026-10-04T01:00"),
           _flight("N3", "ST1", "ST2", "2026-10-04T23:00", "2026-10-05T01:00")]


#: Стендовые задачи §5.2: {секция/запись: [задача]}. Форма задачи — форма
#: записи каталога (`$defs/stand_task`): {id, world, expected, note}.
TASKS: dict = {
    "families/layover_penalty": [{
        "id": "st-layover-1", "world": world(),
        "expected": {
            "builder_method": {"value": 1, "unit": "night", "symbol": "measure"},
            "check_template": {"template": "мера-мягкого-правила",
                               "params": {}, "findings": 1},
            "kotlin_constraint": {"constraint": "Layover nights away from home",
                                  "matches": 1, "amount": 1}},
        "note": "ночёвка вне базы между сменами доказана: станция вне "
                "полного универсума приписок"}],
    "families/calendar_windows": [{
        "id": "st-window-1",
        "world": world(rule_form={"days": ["thursday"]}),
        "expected": {
            "builder_method": {"value": 1, "unit": "night", "symbol": "measure"},
            "check_template": {"template": "мера-мягкого-правила",
                               "params": {"days": ["thursday"]}, "findings": 1},
            "kotlin_constraint": {"constraint": "Calendar window layover penalty",
                                  "matches": 1, "amount": 1}},
        "note": "ночь начата в день окна; на пятнице находок нет"}],
    "families/balanced_workload": [{
        "id": "st-spread-1",
        "world": world(
            crews=[{"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T1"]},
                   {"id": "P2", "base": "ST1", "rank": "CP", "ratings": ["T1"]}],
            flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                             "2026-10-01T11:00"),
                     _flight("F2", "ST2", "ST1", "2026-10-02T06:00",
                             "2026-10-02T11:00")],
            params={"workload_spread_threshold": 5}),
        "expected": {
            "check_template": {"template": "мера-мягкого-правила",
                               "params": {"workload_spread_threshold": 5},
                               "findings": 0, "measure": 2},
            "kotlin_constraint": {"constraint": "Credit hours spread over threshold",
                                  "matches": 1, "amount": 2}},
        "note": "сотрудник без назначений входит в разброс; мера — величина, "
                "не счёт находок"}],
    "families/duty_extension_penalty": [
        {"id": "st-extension-1",
         "world": world(
             flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                              "2026-10-01T21:00"),
                      _flight("F2", "ST2", "ST1", "2026-10-02T10:00",
                              "2026-10-02T12:00")],
             params={"duty_extension_cap": 960, "long_duty_threshold": 720},
             conditions={"extension_bases": ["ST1"],
                         "extension_consent": "yes"}),
         "expected": {
             "builder_method": {"value": 1, "unit": "duty", "symbol": "measure"},
             "check_template": {"template": "мера-мягкого-правила",
                                "params": {"long_duty_threshold": 720},
                                "findings": 1},
             "kotlin_constraint": {"constraint": "Duty extension over threshold",
                                   "matches": 1, "amount": 1},
             "check_code": {"symbol": "apply_extension_caps", "findings": 0}},
         "note": "свод несёт два правила: базовый предел дополняющей семьи "
                 "даёт спецификацию, потолок и условия — правило продления"},
        {"id": "st-extension-2",
         "world": world(
             flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                              "2026-10-01T21:00"),
                      _flight("F2", "ST2", "ST1", "2026-10-02T10:00",
                              "2026-10-02T12:00")],
             params={"duty_extension_cap": 960, "long_duty_threshold": 720},
             conditions={"extension_bases": ["ST2"],
                         "extension_consent": "yes"}),
         "expected": {
             "check_code": {"symbol": "apply_extension_caps", "findings": 1}},
         "note": "база сотрудника не названа условиями продления: предел "
                 "строки базовый, потолок адресный"}],
    "families/time_scale": [{
        "id": "st-scale-0", "world": world(),
        "expected": {
            "builder_method": {"value": 0, "unit": "minute",
                               "symbol": "resolve_time_scale"},
            "check_template": {"template": "параметр-мира-применён",
                               "params": {"base_tz_offset_min": 0},
                               "findings": 0}},
        "note": "слабейшая запись: у параметра мира построчного исхода нет "
                "по замыслу"}],
    "families/crew_composition": [{
        "id": "st-crew-1",
        "world": world(
            flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                             "2026-10-01T08:00")],
            params={"crew_size": 2},
            assignments=[{"crew": "P1", "flight": "F1", "seat": 0}]),
        "expected": {
            "kotlin_constraint": {"constraint": "Crew size", "matches": 1,
                                  "amount": 1},
            "builder_method": {"value": 2, "unit": "person",
                               "symbol": "Builder.crew_size"}},
        "note": "второе место рейса пусто: спрос цепочки — два человека"}],
    "families/reserve_floors": [{
        "id": "st-reserve-1",
        "world": world(
            flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                             "2026-10-01T08:00")],
            params={"reserve_crews": {"ST1": 1}},
            horizon={"from": "2026-10-01", "to": "2026-10-02"}),
        "expected": {
            "kotlin_constraint": {"constraint": "Reserve crews", "matches": 1,
                                  "amount": 1}},
        "note": "клетка база × роль × сутки без свободного экипажа"}],
    "families/duty_limits": [
        {"id": "st-duty-1",
         "world": world(
             flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                              "2026-10-01T21:00"),
                      _flight("F2", "ST2", "ST1", "2026-10-02T10:00",
                              "2026-10-02T12:00")],
             params={"max_landings_per_duty": 4}),
         "expected": {
             "builder_method": {"value": 1, "unit": "duty",
                                "symbol": "Builder.duty_limit_ok"},
             "kotlin_constraint": {"constraint": "Max duty period (rest-segmented)",
                                   "matches": 1, "amount": 120},
             "check_code": {"symbol": "_landings_per_duty", "findings": 0}},
         "note": "пакет без потолка продления: смена сверх базового предела"},
        {"id": "st-duty-2",
         "world": world(
             flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                              "2026-10-01T07:00"),
                      _flight("F2", "ST2", "ST1", "2026-10-01T07:45",
                              "2026-10-01T08:45"),
                      _flight("F3", "ST1", "ST2", "2026-10-01T09:30",
                              "2026-10-01T10:30"),
                      _flight("F4", "ST2", "ST1", "2026-10-01T11:15",
                              "2026-10-01T12:15"),
                      _flight("F5", "ST1", "ST2", "2026-10-01T13:00",
                              "2026-10-01T14:00")],
             params={"max_landings_per_duty": 4}),
         "expected": {
             "kotlin_constraint": {"constraint": "Max landings per duty",
                                   "matches": 1, "amount": 1},
             "check_code": {"symbol": "_landings_per_duty", "findings": 1}},
         "note": "разметка решения держит пять посадок в одной смене: паузы "
                 "короче верха окна стыковки"},
        {"id": "st-duty-3",
         "world": world(
             flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                              "2026-10-01T08:00"),
                      _flight("F2", "ST2", "ST1", "2026-10-08T10:00",
                              "2026-10-08T12:00")]),
         "expected": {
             "builder_method": {"value": 1, "unit": "chain",
                                "symbol": "Builder.pairing_days_ok"},
             "check_code": {"symbol": "_pairing_days", "findings": 1}},
         "note": "лист «Цепочки» строится из поля pairings решения; без него "
                 "проверка честно отвечает «не прогнана»"}],
    "families/rest_floors": [{
        "id": "st-rest-1",
        "world": world(
            flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                             "2026-10-01T08:00"),
                     _flight("F2", "ST2", "ST1", "2026-10-01T18:00",
                             "2026-10-01T20:00")],
            history=[{"crew": "P1", "last_duty_end": "2026-10-01T00:00",
                      "last_duty_station": "ST1"}]),
        "expected": {
            "builder_method": {"value": 700, "unit": "minute",
                               "symbol": "Builder.rest_floor_after"},
            "check_code": {"symbol": "_rest_floor", "findings": 2},
            "kotlin_constraint": {"constraint": "Rest before horizon (history)",
                                  "matches": 1, "amount": 300}},
        "note": "два пола отдыха: дома по истории и вне базы между сменами"}],
    "families/connection_windows": [{
        "id": "st-sit-1",
        "world": world(
            flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                             "2026-10-01T08:00"),
                     _flight("F2", "ST2", "ST1", "2026-10-01T08:20",
                             "2026-10-01T10:20")]),
        "expected": {
            "builder_method": {"value": 1, "unit": "chain",
                               "symbol": "Builder.periods_tiers"},
            "kotlin_constraint": {"constraint": "Minimum turnaround",
                                  "matches": 1, "amount": 25},
            "check_code": {"symbol": "_turnaround", "findings": 1}},
        "note": "в решении оба рейса — ОДНА смена: пауза короче верха окна "
                "стыковки, и её судит исполнитель стыковки; нарезка "
                "построителя дала бы две смены и ноль находок"}],
    "families/deadhead_rules": [{
        "id": "st-dh-1",
        "world": world(
            flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                             "2026-10-01T08:00"),
                     _flight("D1", "ST2", "ST1", "2026-10-01T08:30",
                             "2026-10-01T09:30", deadhead=True),
                     _flight("F2", "ST1", "ST3", "2026-10-01T09:40",
                             "2026-10-01T11:40"),
                     _flight("D2", "ST3", "ST1", "2026-10-01T12:00",
                             "2026-10-01T13:00", deadhead=True)],
            params={"max_deadheads_per_flight": 1, "max_deadheads_per_day": 1}),
        "expected": {
            "builder_method": {"value": 1, "unit": "chain",
                               "symbol": "Builder.adjacent_dh_ok"},
            "kotlin_constraint": {"constraint": "Deadheads per day",
                                  "matches": 1, "amount": 1}},
        "note": "перегон примыкает к работе короче порога стыковки"}],
    "families/span_accumulators": [{
        "id": "st-span-1",
        "world": world(
            flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                             "2026-10-01T08:00"),
                     _flight("F2", "ST2", "ST3", "2026-10-08T10:00",
                             "2026-10-08T12:00"),
                     _flight("F3", "ST3", "ST1", "2026-10-15T10:00",
                             "2026-10-15T12:00")],
            params={"max_monthly_block": 300},
            horizon={"from": "2026-10-01", "to": "2026-11-01"}),
        "expected": {
            "kotlin_constraint": {"constraint": "Max monthly flight hours",
                                  "matches": 1, "amount": 60},
            "check_code": {"symbol": "_monthly_block", "findings": 1},
            "builder_method": {"value": 1, "unit": "chain",
                               "symbol": "Builder.pairing_span_ok"}},
        "note": "налёт известного подытога сверх предела; размах цепочки "
                "сверх предела суток"}],
    "families/fleet_exclusions": [{
        "id": "st-fleet-1",
        "world": world(
            flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                             "2026-10-01T08:00", fleet="T2"),
                     _flight("F2", "ST2", "ST1", "2026-10-02T10:00",
                             "2026-10-02T12:00")],
            params={"fleet_types": ["T2"]}),
        "expected": {
            "kotlin_constraint": {"constraint": "Type rating required",
                                  "matches": 1, "amount": 1},
            "check_code": {"symbol": "_fleet_qualification", "findings": 1}},
        "note": "исходная книга S1: «Рейсы» и «Допуски» — свидетели типа и "
                "допуска, значение перечня в своде — строка"}],
    "families/night_series": [
        {"id": "st-night-1",
         "world": world(
             flights=list(_NIGHTS),
             params={"max_consecutive_night_duties": 2,
                     "night_duty_start": 1320, "night_duty_end": 360},
             history=[{"crew": "P1", "consecutive_night_duties": 0}],
             horizon={"from": "2026-10-02", "to": "2026-11-01"}),
         "expected": {
             "check_code": {"symbol": "_night_series", "findings": 1},
             "kotlin_logic_form": {"form": "CONSECUTIVE_NIGHT_DUTIES_MAX",
                                   "matches": 1, "amount": None}},
         "note": "серия из истории пуста, три ночные смены подряд"},
        {"id": "st-night-2",
         "world": world(
             flights=list(_NIGHTS),
             params={"max_night_duties_in_window": 2, "night_window_days": 3,
                     "night_duty_start": 1320, "night_duty_end": 360},
             history=None,
             history_calendar=_calendar("2026-09-30", 2),
             horizon={"from": "2026-10-02", "to": "2026-11-01"}),
         "expected": {
             "check_code": {"symbol": "_night_window", "findings": 1},
             "kotlin_logic_form": {"form": "NIGHT_DUTIES_IN_ROLLING_WINDOW_MAX",
                                   "matches": 1, "amount": None}},
         "note": "ровно одно окно с тремя ночными сменами; сутки до "
                 "горизонта названы посуточным календарём"}],
    "families/rolling_rest": [{
        "id": "st-roll-1",
        "world": world(
            flights=_daily("W", "ST1", "ST2", "2026-10-03", 7, "06:00", "20:00"),
            params={"weekly_rest_required": 2160, "weekly_rest_period": 10080},
            history=[{"crew": "P1", "last_duty_end": "2026-09-25T00:00"}],
            horizon={"from": "2026-10-02", "to": "2026-11-01"}),
        "expected": {
            "check_code": {"symbol": "_rolling_rest", "findings": 1},
            "kotlin_logic_form": {"form": "REST_IN_ROLLING_WINDOW",
                                  "matches": 1, "amount": None}},
        "note": "одна ячейка области окончаний окна без непрерывного "
                "отдыха; конец последней смены истории до горизонта "
                "открывает известную занятость"}],
    "families/days_off_floor": [
        {"id": "st-off-1",
         "world": world(
             flights=_daily("O", "ST1", "ST2", "2026-10-01", 31, "06:00", "12:00"),
             params={"min_days_off": 4},
             history=[{"crew": "P1", "month_days_worked": 0}],
             horizon={"from": "2026-10-01", "to": "2026-11-01"}),
         "expected": {
             "check_code": {"symbol": "_days_off", "findings": 1},
             "kotlin_logic_form": {"form": "DAYS_OFF_PER_MONTH_MIN",
                                   "matches": 1, "amount": None}},
         "note": "в месяце нет ни одного выходного"},
        {"id": "st-off-2",
         "world": world(
             flights=_daily("O", "ST1", "ST2", "2026-10-02", 6, "06:00", "12:00"),
             params={"min_days_off_in_window": 2, "days_off_window_days": 7},
             history=None,
             history_calendar=_calendar("2026-09-25", 6),
             horizon={"from": "2026-10-01", "to": "2026-10-08"}),
         "expected": {
             "check_code": {"symbol": "_days_off_window", "findings": 1},
             "kotlin_logic_form": {"form": "DAYS_OFF_IN_ROLLING_WINDOW_MIN",
                                   "matches": 1, "amount": None}},
         "note": "ровно одно окно горизонта с нехваткой выходных; сутки до "
                 "горизонта названы посуточным календарём, иначе окна через "
                 "границу остались бы неизвестными"}],
    "criteria/min_person_days": [{
        "id": "st-mpd-1",
        "world": world(criterion={"kind": "min_person_days",
                                  "rate_per_duty": 7}),
        "expected": {
            "builder_method": {"value": 14, "unit": "criterion",
                               "symbol": "_exec_min_person_days"}},
        "note": "два календарных дня командировки по ставке дня"}],
    "criteria/rank_normalized_cost": [{
        "id": "st-rnc-1",
        "world": {"form": "cost_pair", "flight_min": 240, "dh_min": 0,
                  "crew": 3, "rates_deci": {"flight": 12, "work": 8}},
        "expected": {
            "builder_method": {"value": 14400, "unit": "deci",
                               "symbol": "_exec_rank_normalized_cost"}},
        "note": "контрольная пара мира сверяется судьёй по временному файлу; "
                "файл контура исполнителю не передаётся"}],
    "criteria/layover_nights_total": [{
        "id": "st-lnt-1", "world": world(),
        "expected": {"kotlin_metric": {"value": 1, "matches": 1,
                                       "refusal": False}},
        "note": "ночь вне базы одна"}],
    "criteria/positioning_cost": [{
        "id": "st-pos-1", "world": world(),
        "expected": {"kotlin_metric": {"value": 1, "matches": 1,
                                       "refusal": False}},
        "note": "первое назначение вылетает не из базы приписки"}],
    "criteria/uncovered_seats": [{
        "id": "st-unc-1",
        "world": world(
            flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                             "2026-10-01T08:00")],
            params={"crew_size": 2},
            assignments=[{"crew": "P1", "flight": "F1", "seat": 0}]),
        "expected": {"kotlin_metric": {"value": 1, "matches": 1,
                                       "refusal": False}},
        "note": "одно место рейса без сотрудника"}],
    "criteria/preferred_days_off_violations": [{
        "id": "st-pdo-1",
        "world": world(
            crews=[{"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T1"],
                    "preferred_days_off": ["2026-10-01"], "seniority": 500}]),
        "expected": {"kotlin_metric": {"value": 70, "matches": 1,
                                       "refusal": False}},
        "note": "вклад взвешен стажем: вес стажа 7 × 10 за назначение"}],
    "criteria/credit_hours_spread": [{
        "id": "st-chs-1",
        "world": world(
            crews=[{"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T1"]},
                   {"id": "P2", "base": "ST1", "rank": "CP", "ratings": ["T1"]}],
            flights=[_flight("F1", "ST1", "ST2", "2026-10-01T06:00",
                             "2026-10-01T11:00"),
                     _flight("F2", "ST2", "ST1", "2026-10-02T06:00",
                             "2026-10-02T11:00")],
            params={"workload_spread_threshold": 5}),
        "expected": {"kotlin_metric": {"value": 7, "matches": 1,
                                       "refusal": False}},
        "note": "целые часы разброса по всему штату"}],
    "criteria/total_pairing_cost": [{
        "id": "st-tpc-1",
        "world": world(criterion={"kind": "min_person_days",
                                  "rate_per_duty": 7},
                       stand={"w_night": 1}),
        "expected": {
            "builder_method": {"value": 15, "unit": "criterion",
                               "symbol": "Builder.price"},
            "kotlin_metric": {"value": None, "matches": None,
                              "refusal": True}},
        "note": "цена цепочки: критерий плюс мера ночёвки на вес правила"}],
}


# ------------------------------------------------------- каталог с задачами

class TaskCatalog:
    """Живой каталог с ПОДСТАВЛЕННЫМИ задачами образцовых миров.

    Второй свидетель ходит ровно тем же путём, что и прогон на живом
    каталоге — через `families_full()` и `criteria()`, — но берёт задачи
    из таблицы этого файла, а не из посева. Так регресс исполнителя
    виден независимо от того, что сейчас лежит в посеве."""

    def __init__(self, cat, tasks: dict):
        self._cat = cat
        self._tasks = tasks

    def _inject(self, section, record):
        rec = copy.deepcopy(record)
        rec["stand_tasks"] = copy.deepcopy(
            self._tasks.get(f"{section}/{record.get('id')}") or [])
        return rec

    def families_full(self, *a, **kw):
        return [self._inject("families", r)
                for r in self._cat.families_full(*a, **kw)]

    def criteria(self, *a, **kw):
        live = self._cat.criteria(*a, **kw)
        out = {cid: self._inject("criteria", rec) for cid, rec in live.items()}
        for key in self._tasks:
            section, _, cid = key.partition("/")
            if section == "criteria" and cid not in out:
                out[cid] = {"id": cid, "status": "есть",
                            "stand_tasks": copy.deepcopy(self._tasks[key])}
        return dict(sorted(out.items()))

    def version(self):
        return self._cat.version()

    def fingerprint(self):
        return self._cat.fingerprint()

    def dictionaries(self):
        return self._cat.dictionaries()


@pytest.fixture(scope="module")
def catalog():
    ca = pytest.importorskip("vmp.catalog_access")
    try:
        return ca.current()
    except Exception as e:                                   # noqa: BLE001
        pytest.skip("каталог шаблонов недоступен: %s" % e)


@pytest.fixture(scope="module")
def live_rows(catalog, tmp_path_factory):
    """Прогон задач ЖИВОГО каталога — то же, что делает команда."""
    return st.run_tasks(catalog=catalog,
                        tmp_dir=tmp_path_factory.mktemp("stand-live"))


@pytest.fixture(scope="module")
def reference_rows(catalog, tmp_path_factory):
    """Прогон образцовых миров этого файла."""
    return st.run_tasks(catalog=TaskCatalog(catalog, TASKS),
                        tmp_dir=tmp_path_factory.mktemp("stand-ref"))


def _live_tasks(catalog) -> dict:
    out = {}
    for section, record in st._records(catalog):
        for task in record.get("stand_tasks") or ():
            out[(f"{section}/{record['id']}", task["id"])] = task
    return out


RESULTS_FILE = Path(st.REPO) / "vmp" / "stand_results.json"


def _results_document():
    if not RESULTS_FILE.is_file():
        pytest.skip("файла результатов стенда нет: первый прогон не записан")
    return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))


# ------------------------------------------------------- живой каталог

def test_live_catalog_pure_kinds_run_without_red_rows(live_rows):
    """Все чистые виды живого каталога прогнаны; `failed` — только из
    явного перечня `KNOWN_FAILED`, каждый исход со словами."""
    pure = [r for r in live_rows if r["executor_kind"] in st.PURE_KINDS]
    assert pure, "в записях живого каталога нет задач чистых видов"
    bad = [f"{r['record']} {r['task']} {r['executor_kind']}: {r['note']}"
           for r in st.unexpected_failures(pure)]
    assert not bad, (
        "красные строки стенда вне KNOWN_FAILED (мир задачи правится "
        "посевом каталога, ожидание под расчёт не подгоняется):\n"
        + "\n".join(bad))
    for r in pure:
        assert r["note"], f"{r['record']} {r['task']}: исход без слов"


def test_live_catalog_solver_kinds_are_skipped_with_a_reason(live_rows):
    """Kotlin-виды без --with-solver — skipped, причина названа."""
    solver = [r for r in live_rows if r["executor_kind"] in st.SOLVER_KINDS]
    assert solver, "Kotlin-видов в задачах живого каталога нет"
    for r in solver:
        assert r["result"] == "skipped", (r["record"], r["task"], r["note"])
        assert "решатель не зван" in r["note"]


def test_every_declared_expectation_has_a_row(live_rows, catalog):
    """Каждое объявленное ожидание дало строку: молчаливо пропущенных
    видов нет."""
    seen = {(r["record"], r["task"], r["executor_kind"]) for r in live_rows}
    want = {(record, tid, kind)
            for (record, tid), task in _live_tasks(catalog).items()
            for kind in task["expected"]}
    assert want - seen == set()


def test_results_file_matches_the_live_run(live_rows, catalog):
    """vmp/stand_results.json — итог этого же прогона и не устарел.

    Чистые виды сверяются строка в строку. Kotlin-виды в файле сняты
    ЖИВЫМ решателем (`--with-solver`), а прогон этого теста идёт без
    ключа — их исходы здесь не воспроизводимы по построению, поэтому
    сверяется адресность (запись, задача, вид, ожидание) и требуется
    свидетель живого прогона: шапка `solver` с адресом. Файл, где
    Kotlin-вид зелен БЕЗ такого свидетеля, — файл, который лжёт."""
    doc = _results_document()
    stale = st.stale_reasons(doc, catalog)
    assert stale == [], "результаты стенда устарели: " + "; ".join(stale)
    keys = ("record", "task", "executor_kind", "result", "got", "expected")
    ident = ("record", "task", "executor_kind", "expected")

    def rows(source, fields, only):
        return [json.dumps({k: r[k] for k in fields}, ensure_ascii=False,
                           sort_keys=True) for r in source
                if only(r["executor_kind"])]

    pure = (lambda kind: kind in st.PURE_KINDS)
    solver = (lambda kind: kind in st.SOLVER_KINDS)
    assert rows(doc["results"], keys, pure) == rows(live_rows, keys, pure)
    assert rows(doc["results"], ident, solver) == rows(live_rows, ident,
                                                       solver)
    judged = [r for r in doc["results"]
              if r["executor_kind"] in st.SOLVER_KINDS
              and r["result"] != "skipped"]
    if judged:
        url = (doc.get("solver") or {}).get("url")
        assert url, (
            "Kotlin-виды судимы, а свидетеля живого прогона в шапке нет")
        for row in judged:
            # Прогона нет только там, где решатель ОТКАЗАЛ до прогона
            # (метрика с объявленным отказом); всюду иначе строка
            # обязана назвать прогон, которым получена, — ПОЛЕМ строки.
            refused = bool((row.get("got") or {}).get("refusal")) \
                if isinstance(row.get("got"), dict) else False
            assert row["run_id"] or refused, row
            if row["run_id"]:
                assert row.get("package_fingerprint"), row
            # А в СЛОВАХ строки изменчивых значений быть не должно:
            # `note` уезжает в `stand_verified` привязок, и адрес службы
            # с идентификатором прогона сделали бы файл привязок каноном
            # ПРОГОНА — перепрогон менял бы два десятка строк при
            # побайтово тех же числах.
            assert url not in row["note"], row
            assert not row["run_id"] or row["run_id"] not in row["note"], row
            fp = str(row.get("package_fingerprint") or "")
            assert not fp or fp[:12] not in row["note"], row


def test_results_file_addresses_records_and_kinds(catalog):
    """Форма §5.5: каждая строка адресует запись, задачу и вид словаря
    stand_task_kinds; у failed и skipped есть слова."""
    doc = _results_document()
    kinds = set(catalog.dictionaries().get("stand_task_kinds") or ())
    tasks = _live_tasks(catalog)
    assert kinds, "в словарях каталога нет stand_task_kinds"
    for row in doc["results"]:
        assert (row["record"], row["task"]) in tasks, row
        assert row["executor_kind"] in kinds, row
        assert row["result"] in ("passed", "failed", "skipped"), row
        if row["result"] in ("failed", "skipped"):
            assert row["note"], row


# ---------------------------------------------------- образцовые миры

def test_reference_worlds_hold_for_every_executor(reference_rows):
    """Образцовые миры §5.2: исполнители дают ровно объявленное число.

    Набор не зависит от посева — он падает от регресса ИСПОЛНИТЕЛЯ
    (построителя, сертификата, проверок кода)."""
    pure = [r for r in reference_rows if r["executor_kind"] in st.PURE_KINDS]
    bad = [f"{r['record']} {r['task']} {r['executor_kind']}: {r['note']}"
           for r in st.unexpected_failures(pure)]
    assert not bad, ("образцовые миры разошлись с исполнителями:\n"
                     + "\n".join(bad))


def test_reference_worlds_cover_the_declared_scope(reference_rows):
    """Покрытие: семьи пейринга, семьи истории и оба критерия
    построителя."""
    records = {r["record"] for r in reference_rows
               if r["executor_kind"] in st.PURE_KINDS}
    for record in ("families/duty_limits", "families/rest_floors",
                   "families/connection_windows", "families/deadhead_rules",
                   "families/span_accumulators", "families/layover_penalty",
                   "families/night_series", "families/rolling_rest",
                   "families/days_off_floor", "criteria/min_person_days",
                   "criteria/rank_normalized_cost"):
        assert record in records, record


def test_measure_units_are_confirmed_by_the_word():
    """Единица меры мягкого правила — слово исполнителя цены, а не
    подгонка под число. Карта «слово → код» считается ИЗ СЛОВАРЯ
    КАТАЛОГА (0.5.0): рукописной карты у стенда нет."""
    import vmp.chain_price as cp
    единицы = st.measure_units()
    seen = 0
    for record, tasks in TASKS.items():
        family = record.split("/", 1)[1]
        for task in tasks:
            want = task["expected"].get("builder_method") or {}
            if want.get("symbol") != "measure":
                continue
            assert want["unit"] == единицы[cp.UNITS[family]], record
            seen += 1
    assert seen >= 3


def test_размер_нарушения_выбирает_число_ожидания():
    """Чем судится штраф, объявляет ЗАПИСЬ (`violation_size`), а не вид
    исполнителя: `count` — число единиц (matches), `magnitude` —
    величина превышения (amount). Жёсткая семья размера не несёт и меры
    не объявляет."""
    assert st.violation_measure("count") == "matches"
    assert st.violation_measure("magnitude") == "amount"
    assert st.violation_measure(None) is None
    assert set(st.VIOLATION_MEASURE) == set(
        ca.current().dictionaries()["violation_sizes"]), (
        "слова размера нарушения разошлись со словарём каталога")


def test_расхождение_размера_и_исполнителя_отказ_словами():
    """Расхождение поля записи и чисел исполнителя — отказ СЛОВАМИ, а не
    тихое совпадение двух чисел."""
    exp = {"constraint": "X", "matches": 1, "amount": 1}
    assert st.violation_size_refusal(None, exp, {"matches": 1, "amount": 9}) \
        is None, "жёсткая семья размера не объявляет — судить нечего"
    assert st.violation_size_refusal("count", exp,
                                     {"matches": 1, "amount": 1}) is None
    why = st.violation_size_refusal("count", exp, {"matches": 1, "amount": 3})
    assert why and "count" in why and "amount=3" in why
    why = st.violation_size_refusal("magnitude", exp,
                                    {"matches": 1, "amount": None})
    assert why and "magnitude" in why and "не измерена" in why
    why = st.violation_size_refusal("вымысел", exp,
                                    {"matches": 1, "amount": 1})
    assert why and "violation_sizes" in why
    why = st.violation_size_refusal("magnitude", {"constraint": "X",
                                                  "matches": 1},
                                    {"matches": 1, "amount": 2})
    assert why and "amount" in why and "не под запись" in why


def test_ожидание_мягкой_семьи_несёт_число_размера_нарушения():
    """Данные каталога и поле сходятся: у каждой мягкой семьи размер
    нарушения назван, и ожидание её стендовой задачи вида
    `kotlin_constraint` несёт то число, которым запись велела судить."""
    meta = ca.current().families_meta()
    seen = 0
    for record, tasks in TASKS.items():
        section, _, rid = record.partition("/")
        if section != "families" or meta.get(rid, {}).get("kind") != "SOFT":
            continue
        size = meta[rid]["violation_size"]
        assert size is not None, rid
        field = st.violation_measure(size)
        assert field, (rid, size)
        for task in tasks:
            want = task["expected"].get("kotlin_constraint")
            if not want:
                continue
            assert want.get(field) is not None, (record, size, field)
            seen += 1
    assert seen >= 3, "мягких семей с ожиданием ограничения нет — сторож слеп"


def test_logic_symbols_are_the_executor_names_of_logic_checks(monkeypatch):
    """Судьи логических форм стенда — ИМЕНА исполнителей планов
    `vmp.run.logic_checks._EXECUTORS`, носитель один. Новая форма,
    дописанная агентом CR в logic_checks (путь ему открыт), судится
    стендом без правки стенда (путь агенту закрыт): 09.09.2026 символ
    `_duty_series` кандидата duty_series_limit получал «судьи символа у
    стенда нет» от перечня в стенде — по построению, не по делу."""
    from vmp.run import logic_checks as lc
    got = st._logic_symbols()
    assert got == frozenset(fn.__name__ for fn in lc._EXECUTORS.values())
    assert {"_night_series", "_night_window", "_rolling_rest", "_days_off",
            "_days_off_window"} <= got
    assert "_no_such_form" not in got

    def _no_such_form(rid, rule, plan, dataset, counter):
        raise AssertionError("не должен вызываться")
    monkeypatch.setitem(lc._EXECUTORS, "NO_SUCH_FORM", _no_such_form)
    assert "_no_such_form" in st._logic_symbols()


# ----------------------------------------------------- миры и адаптеры

_STATION = r"^ST[1-9]$"
_FLEET = r"^T[1-9]$"
_PERSON = r"^P[1-9][0-9]*$"


def _assert_synthetic(world_doc, where):
    import re
    if world_doc.get("form") != "schedule":
        return
    for f in world_doc.get("flights") or ():
        assert re.match(_STATION, f["from"]) and re.match(_STATION, f["to"]), where
        assert re.match(_FLEET, f["fleet_type"]), where
    for c in world_doc.get("crews") or ():
        assert re.match(_PERSON, c["id"]) and re.match(_STATION, c["base"]), where
        for t in c.get("ratings") or ():
            assert re.match(_FLEET, t), where


def test_live_worlds_use_only_synthetic_codes(catalog):
    """Коды миров живого каталога синтетические: ST1…ST9, T1…T9, P<n>."""
    for where, task in _live_tasks(catalog).items():
        _assert_synthetic(task["world"], where)


def test_reference_worlds_use_only_synthetic_codes():
    """То же — у образцовых миров этого файла."""
    for record, tasks in TASKS.items():
        for task in tasks:
            _assert_synthetic(task["world"], (record, task["id"]))


def test_code_fingerprint_ignores_line_endings(tmp_path):
    """Отпечаток кода не зависит от перевода строки: файл результатов не
    может устареть от перехода CRLF на LF и обратно."""
    lf, crlf = tmp_path / "lf.py", tmp_path / "crlf.py"
    lf.write_bytes(b"a = 1\nb = 2\n")
    crlf.write_bytes(b"a = 1\r\nb = 2\r\n")
    assert st.code_fingerprint(lf) == st.code_fingerprint(crlf)


def test_results_document_round_trip(live_rows, catalog, tmp_path):
    """Документ результатов формы §5.5 и суд его свежести."""
    doc = st.results_document(live_rows, catalog,
                              at="2026-09-08T00:00:00+00:00", commit="0" * 40)
    assert doc["form_version"] == 1
    assert doc["catalog_fingerprint"] == catalog.fingerprint()
    assert set(doc["code"]) == set(st.CODE_FILES)
    assert st.stale_reasons(doc, catalog) == []
    path = tmp_path / "stand_results.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=1,
                               sort_keys=True), encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["results"] == doc["results"]
    assert st.stale_reasons(dict(doc, catalog_fingerprint="0" * 64), catalog)


def test_kind_without_a_judge_is_skipped_with_words(catalog, tmp_path):
    """Вид без судьи — skipped со словами, а не молчаливый passed."""
    task = {"id": "st-probe-1", "world": copy.deepcopy(WORLD_1),
            "expected": {"kotlin_knob": {"param": "max_duty_duration"}},
            "note": "проба вида без судьи"}
    rows = st.run_tasks(catalog=TaskCatalog(catalog,
                                            {"families/duty_limits": [task]}),
                        tmp_dir=tmp_path, records={"duty_limits"})
    assert [r["result"] for r in rows] == ["skipped"]
    assert "стендом не судится" in rows[0]["note"]


@pytest.mark.parametrize("kind,expected,mark", [
    ("builder_method", {"value": 1, "unit": "duty",
                        "symbol": "Builder.duty_limit_okk"}, "судим"),
    ("check_code", {"symbol": "_landings_per_dutyy", "findings": 1},
     "судим"),
    ("check_template", {"template": "мера-мягкого-правилаа", "findings": 0},
     "шаблон вне словаря"),
])
def test_unknown_symbol_of_a_judged_kind_is_failed(catalog, tmp_path,
                                                   kind, expected, mark):
    """Неизвестный символ у СУДИМОГО вида — `failed`, а не зелёный
    `skipped`.

    Символы ожиданий читатель не проверяет (X05 судит лишь виды),
    поэтому опечатка в посеве раньше давала зелёный `skipped` и уходила
    в `stand_verified`: ожидание записи оказывалось неисполнимым молча.
    `skipped` остаётся только у видов БЕЗ судьи и у Kotlin-видов без
    живого решателя."""
    task = {"id": "st-probe-3", "world": copy.deepcopy(WORLD_1),
            "expected": {kind: expected},
            "note": f"опечатка в ожидании ({mark})"}
    rows = st.run_tasks(catalog=TaskCatalog(catalog,
                                            {"families/duty_limits": [task]}),
                        tmp_dir=tmp_path, records={"duty_limits"})
    assert [r["result"] for r in rows] == ["failed"], rows
    assert "неисполним" in rows[0]["note"], rows[0]["note"]
    assert st.unexpected_failures(rows) == rows, \
        "красная строка обязана быть видна гейту"


def test_data_template_of_another_product_is_still_skipped(catalog, tmp_path):
    """Шаблон СЛОВАРЯ, исполняемый линией данных CDPM, остаётся
    законным `skipped`: отличие «чужой исполнитель» от «опечатки»
    судится по словарю каталога, а не по молчанию."""
    task = {"id": "st-probe-4", "world": copy.deepcopy(WORLD_1),
            "expected": {"check_template": {
                "template": "максимальная-длительность-часов",
                "findings": 0}},
            "note": "исполнитель шаблона — линия данных CDPM"}
    rows = st.run_tasks(catalog=TaskCatalog(catalog,
                                            {"families/duty_limits": [task]}),
                        tmp_dir=tmp_path, records={"duty_limits"})
    assert [r["result"] for r in rows] == ["skipped"], rows
    assert "исполнитель шаблона" in rows[0]["note"]


def test_expectation_symbols_are_named_by_the_bindings(catalog):
    """Символ ожидания задачи — символ строки «есть» ТОЙ ЖЕ записи в
    привязках.

    Второй свидетель символа: читатель ожиданий не проверяет, а
    судья стенда узнаёт символ по имени. Разойдись посев привязок и
    посев задач — стенд судил бы не тот код, о котором говорит запись."""
    path = Path(st.REPO) / "vmp" / "bindings.json"
    if not path.is_file():
        pytest.skip("привязок нет: гейт символов их и проверяет")
    doc = json.loads(path.read_text(encoding="utf-8"))
    present = {}
    for rec in doc["bindings"]:
        present[(rec["section"], rec["id"])] = {
            str(ex["symbol"]).split(".")[-1] for ex in rec["executors"]
            if ex.get("state") == "есть" and ex.get("symbol")}
    bad = []
    for section, record in st._records(catalog):
        known = present.get((section, str(record["id"]))) or set()
        for task in record.get("stand_tasks") or ():
            for kind, exp in (task.get("expected") or {}).items():
                symbol = (exp or {}).get("symbol")
                if symbol and str(symbol).split(".")[-1] not in known:
                    bad.append(f"{section}/{record['id']} {task['id']} "
                               f"{kind}: символ {symbol!r} не назван строкой "
                               f"«есть» записи (есть: {sorted(known)})")
    assert not bad, "\n".join(bad)


def test_results_without_a_commit_are_refused_by_words(catalog, monkeypatch):
    """Файл результатов не рождается таким, каким его отвергнет дверь.

    Без git (не рабочее дерево, git не в пути) прежний код молча ставил
    `commit: null`; строка уезжала в `stand_verified`, и файл отвергала
    уже схема читателя. Отказ — на записи и словами."""
    monkeypatch.setattr(st, "_head_commit", lambda repo=None: None)
    with pytest.raises(st.StandRunError) as e:
        st.results_document([], catalog)
    assert "--commit" in str(e.value)
    doc = st.results_document([], catalog, commit="a" * 40)
    assert doc["commit"] == "a" * 40
    with pytest.raises(st.StandRunError):
        st.results_document([], catalog, commit="не-хеш")


def test_expected_value_without_an_answer_is_a_mismatch(catalog, tmp_path):
    """Ожидание с числом, которого исполнитель не дал, — расхождение, а
    не молчаливый пропуск."""
    task = {"id": "st-probe-2",
            "world": copy.deepcopy(TASKS["families/time_scale"][0]["world"]),
            "expected": {"check_template": {
                "template": "параметр-мира-применён",
                "params": {"base_tz_offset_min": 0},
                "findings": 0, "measure": 3}},
            "note": "у параметра мира меры нет по замыслу"}
    rows = st.run_tasks(catalog=TaskCatalog(catalog,
                                            {"families/time_scale": [task]}),
                        tmp_dir=tmp_path, records={"time_scale"})
    assert [r["result"] for r in rows] == ["failed"]
    assert "исполнитель значения не дал" in rows[0]["note"]


def test_world_to_problem_has_solver_shape():
    """Форма задачи решателя строится без обращения к экземпляру.

    Аэропорт назван КОДОМ везде, кроме списка `airports`: класс `Airport`
    объявлен `@JsonIdentityInfo` по полю `code`, и второй полный объект
    того же кода разбор задачи отвергает целиком (живой ответ решателя —
    400 без тела, находка первого живого прогона 08.09)."""
    problem = sa.world_to_problem(WORLD_1)
    assert set(problem) >= {"airports", "employees", "flights",
                            "flightAssignments"}
    assert problem["airports"] == [{"code": "ST1"}, {"code": "ST2"}]
    assert problem["employees"][0]["homeAirport"] == "ST1"
    assert problem["flights"][0]["arrivalAirport"] == "ST2"
    assert len(problem["flightAssignments"]) == len(WORLD_1["flights"])


def test_two_duty_markings_are_different_on_purpose():
    """Нарезка построителя и разметка решения расходятся там, где это
    решает исход: короткая пауза — две смены у построителя и одна у
    решения."""
    import _pairing_builder as _pb
    w = TASKS["families/connection_windows"][0]["world"]
    payload, args = sa.world_to_builder(w)
    chain, _dh = sa.chain_of(_pb.Builder(payload, args), w)
    assert len(chain) == 2
    solution = sa.world_to_solution(w)
    assert len({r["duty_id"] for r in solution["assignments"]}) == 1


def test_rules_branch_reads_the_same_thresholds_as_the_flag_branch():
    """Ветка правил строится из тех же разрешённых флагов: параметр мира
    действует в обеих ветках одинаково, своих чисел стенд не заводит."""
    w = world(params={"max_pairing_days": 3, "min_turnaround": 55})
    _payload, args = sa.world_to_builder(w, rules=True)
    assert args.cos_rules.max_pairing_days == 3
    assert args.cos_rules.min_sit_min == 55
    assert args.cos_rules.work_time_limit_by_tier[0] == args.duty_span
    assert args.cos_rules.layover_tiers[0].on_base_min == args.rest_base
    assert args.cos_rules.layover_tiers[0].off_base_min == args.rest_off


# ── Ключ --records (шаг 6 каталога: гейт конвейера судит ОДНУ запись) ──

def test_ключ_records_сужает_прогон_до_записи(tmp_path, capsys):
    """Агент, дописавший исполнителя одной записи, судит стендом ЕЁ:
    прогон всего каталога занял бы минуты и утопил бы строку записи в
    общем итоге. Сужение — по именам записей, итог печатается."""
    code = st.main(["--records", "duty_limits", "--tmp-dir", str(tmp_path)])
    out = capsys.readouterr().out
    # Исход самого стенда (0 либо 1) — предмет других тестов файла;
    # здесь судится СУЖЕНИЕ: чужих записей в прогоне нет.
    assert code in (0, 1), out
    rows = [ln for ln in out.splitlines()
            if ln and not ln.startswith(("запись", "итог", "красные"))]
    assert rows, out
    assert all(ln.split()[0] == "families/duty_limits" for ln in rows), out
    assert "итог стенда: задач" in out
    # Тот же прогон без ключа судит весь каталог — строк заведомо больше.
    st.main(["--tmp-dir", str(tmp_path / "все")])
    full = capsys.readouterr().out
    assert len(full.splitlines()) > len(out.splitlines())


def test_records_без_единой_задачи_это_отказ(tmp_path, capsys):
    """Опечатка в имени записи не имеет права выглядеть зелёным нулём:
    «прогнали ничего» у гейта конвейера — отказ словами."""
    code = st.main(["--records", "нет_такой_записи",
                    "--tmp-dir", str(tmp_path)])
    assert code == 2
    assert "не дали ни одной стендовой задачи" in capsys.readouterr().out


def test_records_с_write_results_отказ(tmp_path, capsys):
    """Файл результатов — свидетельство ВСЕГО каталога (из него растут
    строки stand_verified привязок): сузив прогон и переписав файл, мы
    молча стёрли бы исходы прочих записей."""
    target = tmp_path / "stand-results.json"
    code = st.main(["--records", "duty_limits", "--write-results",
                    str(target), "--tmp-dir", str(tmp_path)])
    assert code == 2
    assert "--records" in capsys.readouterr().out
    assert not target.exists()


def test_каталог_кандидата_судится_стендом(tmp_path, capsys, catalog):
    """Гейт CR каталога прогоняет стенд на ВРЕМЕННОМ каталоге «живой
    каталог ⊕ запись кандидата» (`template_catalog.candidates`): судятся
    задачи кандидата, живой каталог не трогается."""
    from template_catalog import candidates as tcc
    record = {k: v for k, v in catalog.family("duty_limits").items()
              if k != "history"}
    candidate = {"form_version": 1, "section": "families",
                 "id": "duty_limits", "record": record,
                 "note": "кандидат стенда теста", "by": "тест",
                 "saved_at": "2026-09-12T10:15:00+03:00",
                 "request_id": "req-03707195193482806055",
                 "cr_key": "cr575b6c6a"}
    folder = tcc.materialize(Path(catalog.path), candidate,
                             tmp_path / "каталог-гейта")
    assert (folder / "families.json").is_file()
    live_before = (Path(catalog.path) / "families.json").read_bytes()
    code = st.main(["--catalog", str(folder), "--records", "duty_limits",
                    "--tmp-dir", str(tmp_path / "tmp")])
    out = capsys.readouterr().out
    assert code in (0, 1), out
    rows = [ln for ln in out.splitlines()
            if ln and not ln.startswith(("запись", "итог", "красные"))]
    assert rows and all(ln.split()[0] == "families/duty_limits"
                       for ln in rows), out
    assert "итог стенда: задач" in out
    assert (Path(catalog.path) / "families.json").read_bytes() == live_before


# --------------------------------------------- ветка --with-solver

class _FakeSolver:
    """Двойник решателя для ветки `--with-solver` (сети нет).

    Ветку целиком — сбор пакета, прогон, разбор — проверяет
    `tests/vmp/test_stand_solver.py` настоящим HTTP-сервером; здесь
    судится только КОМАНДА: ключ больше не отказывает, Kotlin-виды
    перестают быть `skipped`, а исход и его слова доезжают до строки
    результата вместе с адресом и прогоном."""

    def __init__(self, breakdown: dict):
        self.breakdown = breakdown
        self.started: list = []
        self.healths = 0

    def health(self) -> bool:
        # Живость службы спрашивается ДО первого прогона серии: молчащая
        # служба не порождает два десятка одинаковых ожиданий.
        self.healths += 1
        return True

    def start(self, problem, zip_b64, mode, objective_id, limit_s,
              task="rostering", **kw):
        self.started.append({"mode": mode, "objective_id": objective_id,
                             "limit_s": limit_s, "task": task, "kw": kw})
        return {"runId": "run-двойник", "packageFingerprint": "a" * 64}

    def status(self, run_id):
        return {"state": "готов"}

    def score_breakdown(self, run_id, problem):
        return self.breakdown


def _fake_breakdown(constraint: str, matches: int, soft: int) -> dict:
    return {"score": f"0hard/{soft}soft", "generated": [],
            "constraints": {constraint: {
                "score": f"0hard/{soft}soft", "hard": 0, "soft": soft,
                "matches": matches, "weight": "0hard/-1soft"}}}


def _solver_task(kind="kotlin_constraint"):
    return {"id": "st-probe-solver", "world": copy.deepcopy(WORLD_1),
            "expected": {kind: {"constraint": "Layover nights away from home",
                                "matches": 1, "amount": 1}},
            "note": "проба ветки живого решателя"}


def _run_with_fake(catalog, tmp_path, monkeypatch, solver, task=None):
    ss = pytest.importorskip("vmp.stand_solver")
    monkeypatch.setattr(ss, "solver_client", lambda url, **kw: solver)
    tasks = {"families/layover_penalty": [task or _solver_task()]}
    return st.run_tasks(catalog=TaskCatalog(catalog, tasks),
                        solver_url="http://стенд-двойник",
                        tmp_dir=tmp_path, records={"layover_penalty"})


def test_ключ_with_solver_судит_kotlin_виды(catalog, tmp_path, monkeypatch):
    """С ключом Kotlin-вид больше не `skipped`: он судится живым
    решателем, а строка ПОЛЯМИ называет прогон и отпечаток его пакета.

    В `note` их нет по замыслу: он уезжает в `stand_verified` привязок,
    и идентификатор прогона сделал бы файл привязок каноном прогона, а
    не каталога. Печатная таблица дописывает их словами."""
    solver = _FakeSolver(_fake_breakdown(
        "Layover nights away from home", 1, -1))
    rows = _run_with_fake(catalog, tmp_path, monkeypatch, solver)
    assert [r["result"] for r in rows] == ["passed"], rows
    assert rows[0]["run_id"] == "run-двойник"
    assert rows[0]["package_fingerprint"] == "a" * 64
    assert "run-двойник" not in rows[0]["note"]
    assert "http://стенд-двойник" not in rows[0]["note"]
    assert "прогон run-двойник" in st._table(rows)
    # Живость службы спрошена ОДИН раз и до прогона.
    assert solver.healths == 1
    # Прогон крошечный; профиль Д1 не задаётся (находка D1_FINDING).
    assert solver.started[0]["limit_s"] == 1
    assert "deterministic" not in solver.started[0]["kw"]


def test_расхождение_живого_решателя_это_failed(catalog, tmp_path,
                                                monkeypatch):
    """Число решателя не сошлось с ожиданием записи — красная строка с
    обоими числами; ожидание под расчёт не подгоняется."""
    solver = _FakeSolver(_fake_breakdown(
        "Layover nights away from home", 0, 0))
    rows = _run_with_fake(catalog, tmp_path, monkeypatch, solver)
    assert [r["result"] for r in rows] == ["failed"], rows
    assert "получено 0, ожидалось 1" in rows[0]["note"]
    assert st.unexpected_failures(rows) == rows


def test_несостоявшееся_звено_решателя_это_failed_а_не_skipped(
        catalog, tmp_path, monkeypatch):
    """Ограничения нет в разборе — `failed` со словами отказа: молчаливым
    пропуском неподтверждённое ожидание не прикрывается."""
    solver = _FakeSolver(_fake_breakdown("Crew size", 0, 0))
    rows = _run_with_fake(catalog, tmp_path, monkeypatch, solver)
    assert [r["result"] for r in rows] == ["failed"], rows
    assert "нет в разборе счёта" in rows[0]["note"]


def test_без_ключа_ветка_решателя_не_трогается(catalog, tmp_path,
                                               monkeypatch):
    """Без `--with-solver` стенд к решателю не обращается вовсе."""
    ss = pytest.importorskip("vmp.stand_solver")

    def _forbidden(*a, **kw):
        raise AssertionError("стенд позвал решателя без ключа")

    monkeypatch.setattr(ss, "solver_client", _forbidden)
    rows = st.run_tasks(
        catalog=TaskCatalog(catalog,
                            {"families/layover_penalty": [_solver_task()]}),
        tmp_dir=tmp_path, records={"layover_penalty"})
    assert [r["result"] for r in rows] == ["skipped"]
    assert "решатель не зван" in rows[0]["note"]
    assert rows[0]["run_id"] is None
    assert rows[0]["package_fingerprint"] is None


# ------------------- ранний обрыв серии и бюджет (просьба Ж-3)

class _FallingSolver(_FakeSolver):
    """Служба жива, пакет принимает, а прогон РОНЯЕТ.

    «Упал» — ОТВЕТ живой службы о неудаче ЭТОГО пакета: ранний обрыв на
    него не ставится (правка по ревью 08.09.2026). Падение приходит
    первым же опросом и времени не стоит, а три подряд у одной записи —
    не случайность, а корреляция миров задач: обрыв закрыл бы словами
    «решатель не отвечает» два десятка ЧУЖИХ строк.
    """

    def __init__(self):
        super().__init__(_fake_breakdown("Layover nights away from home",
                                         1, -1))
        self.state_calls = 0

    def status(self, run_id):
        self.state_calls += 1
        return {"state": "упал", "failure": "проба отказа стенда"}


class _SilentSolver(_FakeSolver):
    """Служба ответила на живость и приняла пакет, а СОСТОЯНИЕ прогона не
    отдаёт: ответа об этом прогоне нет вовсе — случай раннего обрыва."""

    def __init__(self):
        super().__init__(_fake_breakdown("Layover nights away from home",
                                         1, -1))

    def status(self, run_id):
        raise ConnectionError("состояние не отдано: проба молчания")


class _UnreachableSolver(_FakeSolver):
    """Живость служба показала, а на ПОСТАНОВКЕ прогона молчит.

    Самый буквальный случай «решатель не отвечает»: служба умерла
    посреди серии. С боевым клиентом каждая такая строка ждала бы
    потолок клиента (120 с), поэтому ранний обрыв обязан считать и её.
    """

    def __init__(self):
        super().__init__(_fake_breakdown("Layover nights away from home",
                                         1, -1))

    def start(self, problem, zip_b64, mode, objective_id, limit_s,
              task="rostering", **kw):
        self.started.append({"mode": mode, "objective_id": objective_id,
                             "limit_s": limit_s, "task": task, "kw": kw})
        raise ConnectionError("All connection attempts failed")


class _Ticks:
    """Часы с наперёд заданными показаниями; после последнего значение
    держится — тест не должен падать из-за лишнего взгляда на часы."""

    def __init__(self, values):
        self._values = list(values)
        self._i = 0

    def __call__(self):
        value = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return value


def _solver_tasks(n: int) -> dict:
    """`n` живых задач одной записи: столько же прогонов у здоровой
    службы, и ровно на них видно, где серия обрывается."""
    out = []
    for i in range(n):
        task = _solver_task()
        task["id"] = "st-probe-%d" % (i + 1)
        out.append(task)
    return {"families/layover_penalty": out}


def _run_series(catalog, tmp_path, monkeypatch, solver, tasks, **kw):
    ss = pytest.importorskip("vmp.stand_solver")
    monkeypatch.setattr(ss, "solver_client", lambda url, **k: solver)
    monkeypatch.setattr(ss, "PROBE_WAIT_S", 0.01)
    monkeypatch.setattr(ss, "WAIT_S", 0.01)
    monkeypatch.setattr(ss, "POLL_S", 0.0)
    return st.run_tasks(catalog=TaskCatalog(catalog, tasks),
                        solver_url="http://стенд-двойник", tmp_dir=tmp_path,
                        records={"layover_penalty"}, **kw)


def test_три_отказа_подряд_обрывают_серию_словами(catalog, tmp_path,
                                                  monkeypatch):
    """Просьба Ж-3. После трёх отказов ПОДРЯД БЕЗ ОТВЕТА службы
    остальные живые строки красные СЛОВАМИ и БЕЗ новых прогонов.

    Считать некому — а стенд без обрыва ставил бы прогон на каждую
    строку и ждал каждый полный потолок, набивая очередь чужой службы.
    """
    solver = _SilentSolver()
    rows = _run_series(catalog, tmp_path, monkeypatch, solver,
                       _solver_tasks(6))
    assert [r["result"] for r in rows] == ["failed"] * 6
    assert len(solver.started) == st.READY_MISS_LIMIT, (
        "прогонов ровно столько, сколько отказов до обрыва")
    assert st.READY_MISS_WORDS in rows[-1]["note"]
    assert "прогоны прекращены после 3 отказов подряд" in rows[-1]["note"]
    # Первые три строки красные СВОИМИ словами отказа, а не словами
    # обрыва: прослеживаемость до прогона у них своя.
    assert "состояние прогона не отдано" in rows[0]["note"]
    assert st.READY_MISS_WORDS not in rows[0]["note"]


def test_служба_молчит_на_постановке_обрыв_срабатывает(catalog, tmp_path,
                                                       monkeypatch):
    """Правка по ревью 08.09.2026. Служба умерла посреди серии: ПОСТАНОВКА
    прогона не проходит вовсе (соединение не встало) — ранний обрыв
    обязан считать и это.

    Прежде транспортный отказ приходил без признака, `note_failure`
    считал его ответом живой службы и ОБНУЛЯЛ счётчик: обрыв не
    срабатывал в самом буквальном случае «решатель не отвечает», а
    каждая оставшаяся строка ждала бы потолок клиента (120 с).
    """
    solver = _UnreachableSolver()
    rows = _run_series(catalog, tmp_path, monkeypatch, solver,
                       _solver_tasks(6))
    assert [r["result"] for r in rows] == ["failed"] * 6
    assert len(solver.started) == st.READY_MISS_LIMIT, (
        "после трёх молчаний постановок больше не было")
    assert st.READY_MISS_WORDS in rows[-1]["note"]
    assert "All connection attempts failed" in rows[0]["note"]


def test_упавшие_прогоны_серию_не_обрывают(catalog, tmp_path, monkeypatch):
    """Правка по ревью 08.09.2026. «Упал» — ОТВЕТ живой службы о неудаче
    ЭТОГО пакета, и серию он не обрывает.

    Три упавших подряд у записи с задачами на одном шаблоне мира — не
    случайность, а корреляция миров: прежде они объявляли службу мёртвой
    словами «решатель не отвечает» (ложь: она отвечала) и закрывали без
    прогона все остальные живые строки — свидетельство терялось.
    """
    solver = _FallingSolver()
    rows = _run_series(catalog, tmp_path, monkeypatch, solver,
                       _solver_tasks(6))
    assert [r["result"] for r in rows] == ["failed"] * 6
    assert len(solver.started) == 6, "прогон поставлен каждой строке"
    for row in rows:
        assert "прогон упал" in row["note"]
        assert st.READY_MISS_WORDS not in row["note"], (
            "слова «решатель не отвечает» лгали бы о живой службе")
        assert row["run_id"], "красная строка прослеживается до прогона"


def test_ответ_службы_счётчик_обрыва_сбрасывает(catalog, tmp_path,
                                                monkeypatch):
    """Просьба Ж-3 и правка по ревью. Любой ОТВЕТ службы — пакет,
    отвергнутый её гейтом, и прогон, упавший на своём пакете — серию не
    обрывает и счётчик молчаний обнуляет. Иначе три честных красных
    строки подряд объявляли бы мёртвой службу, которая отвечает.
    """
    gate = st.SolverGate("http://стенд-двойник")
    ss = pytest.importorskip("vmp.stand_solver")
    for _ in range(2):
        gate.note_failure(ss.StandSolverError("нет ответа", unanswered=True))
    assert gate.state != "dead" and gate.reason is None
    gate.note_failure(ss.StandSolverError("решатель отверг пакет"))
    gate.note_failure(ss.StandSolverError("нет ответа", unanswered=True))
    assert gate.state != "dead", "счётчик сброшен отказом гейта"
    gate.note_failure(ss.StandSolverError("прогон упал", run_failed=True))
    for _ in range(2):
        gate.note_failure(ss.StandSolverError("нет ответа", unanswered=True))
    assert gate.state != "dead", "счётчик сброшен упавшим прогоном"
    gate.note_failure(ss.StandSolverError("нет ответа", unanswered=True))
    assert gate.state == "dead" and st.READY_MISS_WORDS in gate.reason
    # Дошедший до «готов» прогон счётчик тоже обнуляет: живая служба
    # после трёх неудач подряд не должна считаться мёртвой навсегда.
    fresh = st.SolverGate("http://стенд-двойник")
    fresh.note_failure(ss.StandSolverError("нет ответа", unanswered=True))
    fresh.seen_ready()
    for _ in range(2):
        fresh.note_failure(ss.StandSolverError("нет ответа", unanswered=True))
    assert fresh.state != "dead"


def test_бюджет_стенда_закрывает_живые_строки_словами(catalog, tmp_path,
                                                      monkeypatch, capsys):
    """Просьба Ж-3. Бюджет исчерпан — оставшиеся живые строки `failed`
    словами «бюджет стенда исчерпан», а прогонов у них нет.

    Так стенд укладывается в предел команды САМ: без ключа худший случай
    упирается в потолок конвейера кодом 124 без диагноза.
    """
    solver = _FakeSolver(_fake_breakdown("Layover nights away from home",
                                         1, -1))
    ss = pytest.importorskip("vmp.stand_solver")
    monkeypatch.setattr(ss, "solver_client", lambda url, **k: solver)
    # Часы: заведение шлюза (0), первая строка — суд бюджета и её
    # остаток (0 и 0: в бюджете, остаток целый), дальше 9 с при бюджете
    # 5 — бюджет исчерпан.
    gate = st.SolverGate("http://стенд-двойник", budget_s=5.0,
                         clock=_Ticks([0.0, 0.0, 0.0, 9.0]))
    monkeypatch.setattr(st, "SolverGate", lambda url, **kw: gate)
    rows = st.run_tasks(catalog=TaskCatalog(catalog, _solver_tasks(3)),
                        solver_url="http://стенд-двойник", tmp_dir=tmp_path,
                        records={"layover_penalty"})
    assert [r["result"] for r in rows] == ["passed", "failed", "failed"]
    assert len(solver.started) == 1, "после бюджета прогонов не ставили"
    for row in rows[1:]:
        assert row["note"].startswith(st.BUDGET_WORDS)
        assert row["run_id"] is None
    assert st.partial_rows(rows) == rows[1:]


def test_ключ_budget_s_доезжает_до_шлюза_проводкой_команды(catalog, tmp_path,
                                                           monkeypatch,
                                                           capsys):
    """Правка по ревью 08.09.2026. Проводка `main --budget-s` →
    `run_tasks(budget_s=…)` → `SolverGate(budget_s=…)` судится ЦЕЛИКОМ.

    Прежние свидетели бюджета подменяли сам класс `SolverGate` лямбдой,
    игнорирующей kwargs, и подсовывали шлюз, собранный вручную: обрыв
    проводки в `run_tasks` они бы не заметили. Здесь подменены ЧАСЫ, а
    ключ идёт настоящим путём команды.
    """
    ss = pytest.importorskip("vmp.stand_solver")
    solver = _FakeSolver(_fake_breakdown("Layover nights away from home",
                                         1, -1))
    monkeypatch.setattr(ss, "solver_client", lambda url, **k: solver)
    monkeypatch.setattr(st, "_catalog",
                        lambda path=None: TaskCatalog(catalog,
                                                      _solver_tasks(3)))
    # Часы команды: заведение шлюза (0), затем время за пределом — ни
    # одного прогона поставить нельзя.
    monkeypatch.setattr(st.time, "monotonic", _Ticks([0.0, 99.0]))
    code = st.main(["--kinds", "kotlin_constraint",
                    "--with-solver", "http://стенд-двойник",
                    "--budget-s", "5",
                    "--tmp-dir", str(tmp_path / "tmp")])
    out = capsys.readouterr().out
    assert code == 1, out
    assert st.BUDGET_WORDS in out
    assert "предел --budget-s = 5 с" in out
    assert solver.started == [], "бюджет команды прогонов не разрешил"


def test_остаток_бюджета_уезжает_в_строку(catalog, tmp_path, monkeypatch):
    """Правка по ревью 08.09.2026. Бюджет ограничивает строку В ПОЛЁТЕ, а
    не только решение «ставить ли прогон».

    Прежде `over_budget` судился ТОЛЬКО перед постановкой, и строка,
    начатая у самой границы, шла своими прежними потолками (клиент 120 с
    на каждое из трёх обращений, ожидание «готов» 60 с) — до ~360 с
    сверх предела. Обещание «стенд укладывается в предел команды сам»
    держалось на удаче.
    """
    ss = pytest.importorskip("vmp.stand_solver")
    solver = _FakeSolver(_fake_breakdown("Layover nights away from home",
                                         1, -1))
    monkeypatch.setattr(ss, "solver_client", lambda url, **k: solver)
    # Часы: заведение шлюза (0), суд бюджета в blocked (5.0 — ровно
    # предел, ещё не «сверх»), остаток строки (5.0 → 0 с).
    gate = st.SolverGate("http://стенд-двойник", budget_s=5.0,
                         clock=_Ticks([0.0, 5.0, 5.0]))
    assert gate.over_budget() is None and gate.left_s() == 0.0
    monkeypatch.setattr(st, "SolverGate", lambda url, **kw: gate)
    rows = st.run_tasks(catalog=TaskCatalog(catalog, _solver_tasks(1)),
                        solver_url="http://стенд-двойник", tmp_dir=tmp_path,
                        records={"layover_penalty"})
    assert [r["result"] for r in rows] == ["failed"]
    assert rows[0]["note"].startswith(st.BUDGET_WORDS)
    assert "посреди строки" in rows[0]["note"]
    assert st.partial_rows(rows) == rows, "это частичная строка"
    assert solver.started == [], "остатка не хватило даже на постановку"


def test_частичные_строки_доезжают_до_файла_результатов(catalog, tmp_path,
                                                        monkeypatch, capsys):
    """Просьба Ж-3. Файл результатов пишется с ЧАСТИЧНЫМИ строками
    словами, а не молча: «строк меньше» без объяснения читалось бы как
    «задач меньше», и упор в бюджет исчез бы из свидетельства."""
    solver = _FakeSolver(_fake_breakdown("Layover nights away from home",
                                         1, -1))
    ss = pytest.importorskip("vmp.stand_solver")
    monkeypatch.setattr(ss, "solver_client", lambda url, **k: solver)
    gate = st.SolverGate("http://стенд-двойник", budget_s=0.000001)
    monkeypatch.setattr(st, "SolverGate", lambda url, **kw: gate)
    out_file = tmp_path / "stand.json"
    code = st.main(["--kinds", "kotlin_constraint",
                    "--with-solver", "http://стенд-двойник",
                    "--budget-s", "0.000001",
                    "--commit", "0" * 40,
                    "--write-results", str(out_file),
                    "--tmp-dir", str(tmp_path / "tmp")])
    out = capsys.readouterr().out
    assert code == 1, out
    assert st.BUDGET_WORDS in out and "частичный" in out
    doc = json.loads(out_file.read_text(encoding="utf-8"))
    spent = [r for r in doc["results"]
             if str(r["note"]).startswith(st.BUDGET_WORDS)]
    assert spent and all(r["result"] == "failed" for r in spent)
    assert solver.started == [], "прогонов не ставили вовсе"


def test_ключи_живого_прогона_без_адреса_решателя_отказ(tmp_path, capsys):
    """Ключи `--budget-s`, `--solver-identity`, `--deterministic` без
    `--with-solver` — отказ кодом 2: ключ, который ничего не делает,
    читается как сделанная работа."""
    for key, value in (("--budget-s", "10"), ("--solver-identity", "a" * 64),
                       ("--deterministic", None)):
        argv = ["--kinds", "kotlin_constraint", "--tmp-dir",
                str(tmp_path / "tmp"), key]
        if value is not None:
            argv.append(value)
        assert st.main(argv) == 2, key
        assert "только с --with-solver" in capsys.readouterr().out


# --------------------- личность артефакта решателя (просьба Ж-4)

def test_ключ_solver_identity_уезжает_в_шапку_результатов(catalog, tmp_path,
                                                          monkeypatch,
                                                          capsys):
    """Просьба Ж-4. Отпечаток артефакта решателя уезжает шапкой
    `solver.artifact_sha256`: привязка «адрес → артефакт» живёт В ФАЙЛЕ
    результатов, а не только в свидетельстве кандидата."""
    ss = pytest.importorskip("vmp.stand_solver")
    monkeypatch.setattr(ss, "solver_client", lambda url, **k: _FakeSolver(
        _fake_breakdown("Layover nights away from home", 1, -1)))
    # Свидетельство ВСЕГО каталога (`--records` с `--write-results`
    # законно не сочетаются), но каталог здесь — с подставленными
    # задачами одной записи: судится шапка файла, а не числа записей.
    monkeypatch.setattr(st, "_catalog",
                        lambda path=None: TaskCatalog(catalog,
                                                      _solver_tasks(1)))
    out_file = tmp_path / "stand.json"
    sha = "b" * 64
    code = st.main(["--kinds", "kotlin_constraint",
                    "--with-solver", "http://стенд-двойник",
                    "--solver-identity", sha, "--commit", "0" * 40,
                    "--write-results", str(out_file),
                    "--tmp-dir", str(tmp_path / "tmp")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert sha in out
    doc = json.loads(out_file.read_text(encoding="utf-8"))
    assert doc["solver"]["artifact_sha256"] == sha
    assert doc["solver"]["url"] == "http://стенд-двойник"
    assert st.IDENTITY_NOTE not in doc["solver"]["note"]


def test_без_ключа_артефакт_null_и_причина_словами():
    """Просьба Ж-4. Без ключа поле `null`, и это НЕ молчание: причина
    названа словами в `note` шапки — иначе `null` читался бы как
    «артефакт тот же»."""
    head = st.solver_header("http://стенд-двойник")
    assert head["artifact_sha256"] is None
    assert st.IDENTITY_NOTE in head["note"]
    assert st.PACKAGE_PER_TASK in head["note"]
    assert st.solver_header(None) is None


def test_кривой_отпечаток_артефакта_отказ_словами(tmp_path, capsys):
    """Шапка результатов не должна лгать о личности артефакта: значение
    не формы sha256 — отказ кодом 2, а не запись как есть."""
    code = st.main(["--kinds", "kotlin_constraint",
                    "--with-solver", "http://стенд-двойник",
                    "--solver-identity", "не-отпечаток",
                    "--tmp-dir", str(tmp_path / "tmp")])
    assert code == 2
    assert "64 знака" in capsys.readouterr().out


def test_отпечаток_заглавными_принимается_и_канонится(catalog, tmp_path,
                                                      monkeypatch, capsys):
    """Правка по ревью 08.09.2026. `Get-FileHash` PowerShell — обычный
    способ снять отпечаток на площадке — печатает ЗАГЛАВНЫЕ знаки, а
    `hashlib.hexdigest()` конвейера строчные. Один и тот же отпечаток
    отвергать по регистру нечестно: ключ принимает оба, а в файл уезжает
    канон (строчные).
    """
    ss = pytest.importorskip("vmp.stand_solver")
    monkeypatch.setattr(ss, "solver_client", lambda url, **k: _FakeSolver(
        _fake_breakdown("Layover nights away from home", 1, -1)))
    monkeypatch.setattr(st, "_catalog",
                        lambda path=None: TaskCatalog(catalog,
                                                      _solver_tasks(1)))
    out_file = tmp_path / "stand.json"
    sha = "0402CF28" + "A" * 56
    code = st.main(["--kinds", "kotlin_constraint",
                    "--with-solver", "http://стенд-двойник",
                    "--solver-identity", sha, "--commit", "0" * 40,
                    "--write-results", str(out_file),
                    "--tmp-dir", str(tmp_path / "tmp")])
    out = capsys.readouterr().out
    assert code == 0, out
    doc = json.loads(out_file.read_text(encoding="utf-8"))
    assert doc["solver"]["artifact_sha256"] == sha.lower()
    assert sha.lower() in out and sha not in out
    assert st.canon_sha256(None) is None


# ---------------------------- переключатель профиля Д1

def test_ключ_deterministic_включает_профиль_и_называет_его_словами(
        catalog, tmp_path, monkeypatch, capsys):
    """Переключатель Д1 у команды: умолчание ВЫКЛ, ключ включает.

    Включают его ПОСЛЕ правки остановки на стороне решателя; до неё
    слова строк и итога честно говорят, что признак остановки стенду
    ещё не назван (заготовка `stand_solver.D1_STOP_SIGN`)."""
    ss = pytest.importorskip("vmp.stand_solver")
    solver = _FakeSolver(_fake_breakdown("Layover nights away from home",
                                         1, -1))
    monkeypatch.setattr(ss, "solver_client", lambda url, **k: solver)
    rows = st.run_tasks(catalog=TaskCatalog(catalog, _solver_tasks(1)),
                        solver_url="http://стенд-двойник", tmp_dir=tmp_path,
                        records={"layover_penalty"}, deterministic=True)
    assert [r["result"] for r in rows] == ["passed"], rows
    assert solver.started[0]["kw"]["deterministic"] == ss.DETERMINISTIC
    assert "профиль Д1" in rows[0]["note"]
    # Двойник — «служба без правки Д1»: признака остановки в его ответе
    # нет, и поля у строки нет тоже.
    assert "termination" not in rows[0]
    assert st.termination_counts(rows) == {None: 1}


class _StoppingSolver(_FakeSolver):
    """Решатель С ПРАВКОЙ Д1: ответ о состоянии несёт `termination`."""

    def __init__(self, kind="wall_clock_safety", reproducible=False):
        super().__init__(_fake_breakdown("Layover nights away from home",
                                         1, -1))
        self.stop = {"version": 1, "kind": kind, "mode": "deterministic",
                     "reproducible": reproducible, "search_steps": 1,
                     "elapsed_ms": 1017,
                     "words": "остановлен СТРАХОВОЧНЫМ пределом"}

    def status(self, run_id):
        return {"state": "готов", "termination": dict(self.stop)}


def test_итог_команды_не_лжёт_о_сборке_решателя(catalog, tmp_path,
                                                monkeypatch, capsys):
    """Правка по ревью 08.09.2026. Итог команды под `--deterministic`
    судит СТРОКИ прогонов, а не ключ.

    Прежде он печатал `d1_words()` без ответа службы и утверждал «сборка
    решателя без правки Д1» ДАЖЕ когда каждая строка прогона несла
    признак остановки от правленого решателя — слова итога лгали ровно в
    том случае, ради которого ключ заведён.
    """
    ss = pytest.importorskip("vmp.stand_solver")
    solver = _StoppingSolver()
    monkeypatch.setattr(ss, "solver_client", lambda url, **k: solver)
    monkeypatch.setattr(st, "_catalog",
                        lambda path=None: TaskCatalog(catalog,
                                                      _solver_tasks(2)))
    code = st.main(["--kinds", "kotlin_constraint",
                    "--with-solver", "http://стенд-двойник",
                    "--deterministic",
                    "--tmp-dir", str(tmp_path / "tmp")])
    out = capsys.readouterr().out
    assert code == 0, out
    # Проводка ключа: `main` → `run_tasks` → запрос прогона с профилем.
    assert solver.started[0]["kw"]["deterministic"] == ss.DETERMINISTIC
    assert "профиль Д1 задан ключом --deterministic" in out
    assert "без правки Д1" not in out, (
        "служба ответила признаком остановки — итог обязан это видеть")
    assert "wall_clock_safety — строк 2" in out
    assert ss.TERMINATION_KINDS["wall_clock_safety"] in out


def test_итог_называет_старую_сборку_числом_строк(catalog, tmp_path,
                                                  monkeypatch, capsys):
    """Служба БЕЗ правки Д1 поля не даёт — итог говорит это числом строк,
    а не догадкой о сборке: судятся строки прогонов."""
    ss = pytest.importorskip("vmp.stand_solver")
    solver = _FakeSolver(_fake_breakdown("Layover nights away from home",
                                         1, -1))
    monkeypatch.setattr(ss, "solver_client", lambda url, **k: solver)
    monkeypatch.setattr(st, "_catalog",
                        lambda path=None: TaskCatalog(catalog,
                                                      _solver_tasks(2)))
    code = st.main(["--kinds", "kotlin_constraint",
                    "--with-solver", "http://стенд-двойник",
                    "--deterministic",
                    "--tmp-dir", str(tmp_path / "tmp")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "поля termination нет — строк 2" in out
    assert "без правки Д1" in out


def test_без_ключа_deterministic_прогон_идёт_стенными_часами(catalog,
                                                             tmp_path,
                                                             monkeypatch):
    """Умолчание переключателя — ВЫКЛ, и оно побайтово прежнее: пустого
    параметра `deterministic` в запросе прогона нет вовсе."""
    ss = pytest.importorskip("vmp.stand_solver")
    solver = _FakeSolver(_fake_breakdown("Layover nights away from home",
                                         1, -1))
    monkeypatch.setattr(ss, "solver_client", lambda url, **k: solver)
    rows = _run_with_fake(catalog, tmp_path, monkeypatch, solver)
    assert "deterministic" not in solver.started[0]["kw"]
    assert "профиль Д1" not in rows[0]["note"]


def test_команда_с_ключом_решателя_печатает_таблицу(catalog, tmp_path,
                                                    capsys, monkeypatch):
    """Команда с ключом больше не отказывает кодом 2: она прогоняет
    Kotlin-виды и печатает исходы."""
    ss = pytest.importorskip("vmp.stand_solver")
    monkeypatch.setattr(ss, "solver_client", lambda url, **kw: _FakeSolver(
        _fake_breakdown("Layover nights away from home", 1, -1)))
    code = st.main(["--records", "layover_penalty",
                    "--kinds", "kotlin_constraint",
                    "--with-solver", "http://стенд-двойник",
                    "--tmp-dir", str(tmp_path / "tmp")])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "kotlin_constraint" in out and "passed" in out
    assert "http://стенд-двойник" in out
