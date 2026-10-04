# -*- coding: utf-8 -*-
"""Негативные тесты режима условий заказчика (`--conditions`).

Каждый тест закрывает найденный ОБХОД инварианта, а не счастливый путь
(дисциплина пакета R9): три дефекта ниже были найдены проверкой готового
решения одной из линий и исправлены классово.

1. Предел рабочего времени принадлежит ВХОДУ в множество смен: раньше
   проверка стояла только на расширении, и односегментная смена входила
   в множество не проверенной — дальнемагистральный рейс на 15 часов
   становился сменой при пределе в 12.
2. Счётчик сверхнормативных смен обязан мерить фактический предел:
   раньше он считал смены без яруса состава, а в режиме условий ярус
   проставляется всегда — счётчик структурно давал ноль (ложная зелень).
3. Правила, судящие по НОМЕРУ рейса (чартер), обязаны видеть номер:
   внутренний идентификатор участка несёт суффикс даты, и счёт цифр по
   нему не срабатывал никогда — запрет смешения был пустым.
4. Пол документа над буферами нарядов — одна реализация на построитель
   и выгрузку: двойная реализация уже дала выгрузку, не совпавшую с
   расчётом.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

_pb = pytest.importorskip("_pairing_builder")
_conditions = pytest.importorskip("_conditions")
_cos_rules = pytest.importorskip("_cos_rules")
_p2m = pytest.importorskip("_payload_to_meridian")

sys.path.insert(0, str(HERE / "tests"))
from test_pairing_builder import _args as _base_args  # noqa: E402
from test_pairing_builder import _leg, _payload  # noqa: E402

# Минимальная спецификация условий: предел дневной смены с 1-2 посадками
# 12 часов, брифинг 100/60, дебрифинг 60/30, чартер = 4 цифры не с 9.
SPEC = {
    "base": "BASE",
    "A": {
        "crew_min_by_fleet": {"rule": "П.1.6", "values": {"A320": 4}},
        "briefing_base_min": {"rule": "П.2.4", "value": 100},
        "briefing_offbase_min": {"rule": "П.2.5", "value": 60},
        "debrief_base_min": {"rule": "П.2.8", "value": 60},
        "debrief_offbase_min": {"rule": "П.2.9", "value": 30},
        "sit_offbase_min": {"rule": "П.2.7", "value": 45},
        "deadhead_credit_to_work_pct": {
            "rule": "П.2.10",
            "values": {"without_rest_12h_before": 100,
                       "with_rest_12h_before": 50}},
        "rest_offbase_by_work_h": {
            "rule": "П.3.7",
            "values": [{"work_upto_h": 12, "rest_h": 12},
                       {"work_upto_h": 16, "rest_h": 24}],
            "transmeridian": [{"work_upto_h": 16, "rest_h": 24}]},
        "rest_base_by_work_h": {
            "rule": "П.3.5",
            "values": [{"work_upto_h": 16, "rest_h": 36}],
            "transmeridian": [{"work_upto_h": 16, "rest_h": 48}]},
        "chain_max_days": {"rule": "П.2.14", "value": 5},
    },
    "B": {
        "b1_crew_std_by_fleet": {"values": {"A320": 5}},
        "b3_worktime_table": {
            "day_by_landings_h": {"1-2": 12, "3": 11.5, "4": 11, "5+": 10},
            "night_by_landings_h": {"1-2": 12, "3": 11, "4": 10,
                                    "5+": None}},
        "b4_standard_night": {
            "values": {"start": "22:00", "end": "06:00", "share": 0.5}},
        "b6_sit_base_min": {"value": 60},
        "b7_lowcost_vs_charter": {
            "values": {"lowcost_prefix": "9", "charter_digits": 4}},
        "b8_airport_group": {"values": {"c_class_required_from_h": 6}},
        "b12_rest_base_upto_12h": {
            "values": {"work_upto_h": 12, "rest_h_if_ambiguous": 24,
                       "rest_h_min": 12}},
        "b13_transmeridian_unknown": {"value": True},
        "b15_estafeta_rest_bonus_min": {"value": 40},
    },
}


def _conds() -> "_conditions.Conditions":
    return _conditions.Conditions(copy.deepcopy(SPEC))


def _args(**over) -> argparse.Namespace:
    a = _base_args(**over)
    a.conditions = _conds()
    # Правила источника — ЯВНАЯ синтетика фикстуры: умолчаний у CosRules
    # больше нет (пакет «умолчания-в-данные»), пустой from_parameters
    # оставляет None. Ночное окно и прочие не-числовые умолчания класса
    # остаются; девятку числовых задаём сами (значения прежние, чтобы
    # ассерты набора сохранились дословно).
    r = _cos_rules.from_parameters({})
    r.work_time_limit_by_tier = (720, 840, 960)
    r.layover_tiers = (
        _cos_rules.LayoverTier(720, 720, 760, 1440, 760, 1440, 1440),
        _cos_rules.LayoverTier(840, 2160, 1480, 2160, 1480, 2880, 1440),
        _cos_rules.LayoverTier(960, 2160, 1480, 2160, 1480, 2880, 1440),
    )
    r.min_sit_min = 50
    r.max_pairing_days = 6
    r.base_tz_offset_min = 300
    r.trans_tz_hours = 3.0
    r.trans_leg_min = 720
    r.dh_board_after_debrief_min = 20
    r.teleportation = {}
    a.cos_rules = r
    # Сцепка с вектором фикстуры (синтетика): при правилах порог
    # стыковки перегона построитель читает из args.sit_min — держим его
    # равным min_sit_min фикстуры правил (50).
    a.sit_min = over.get("sit_min", 50)
    return a


# ---------- 1. предел рабочего времени на входе в множество смен ----------

def test_single_leg_duty_over_limit_is_not_built():
    """Рейс на 15 часов при пределе 12 не должен становиться сменой —
    ни односегментной (прежний обход), ни какой-либо ещё; участок обязан
    честно попасть в непокрытые."""
    payload = _payload([
        _leg("F900-20260801-1", "BASE", "AAA", "2026-08-01T06:00:00",
             15 * 60)])
    catalog, report = _pb.build(payload, _args())
    covered = {fn for u in catalog["selected"]
               for fn in (u.get("active_fns") or ())}
    assert "F900-20260801-1" not in covered
    assert report["uncovered_legs"] == 1
    assert report["duties_over_work_time_limit"] == 0


def test_legal_single_leg_duty_still_builds():
    """Контроль симметрии: часовой рейс строится как прежде."""
    payload = _payload([
        _leg("F1-20260801-1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("F2-20260801-1", "AAA", "BASE", "2026-08-01T10:00:00", 120)])
    _catalog, report = _pb.build(payload, _args())
    assert report["uncovered_legs"] == 0


# ---------- 2. счётчик сверхнормативных смен мерит предел ----------

def test_over_limit_counter_measures_actual_limit():
    """Смена заказчика сверх предела (закреплённая, из каталога) обязана
    попасть в счётчик: признак «нет яруса» в режиме условий не может
    отличиться никогда — счётчик, который структурно ноль, есть ложная
    зелень."""
    long_leg = _leg("F901-20260801-1", "BASE", "AAA",
                    "2026-08-01T06:00:00", 15 * 60)
    back = _leg("F902-20260802-1", "AAA", "BASE",
                "2026-08-02T12:00:00", 15 * 60)
    payload = _payload([long_leg, back])
    base_catalog = {"selected": [{
        "fns": ["F901-20260801-1", "F902-20260802-1"],
        "active_fns": ["F901-20260801-1", "F902-20260802-1"],
        "duty_fns": [["F901-20260801-1"], ["F902-20260802-1"]],
        "kind": "pairing", "cost": 1}]}
    _catalog, report = _pb.build(payload, _args(level=2), base_catalog)
    assert report["duties_over_work_time_limit"] == 2, report.get(
        "duties_over_work_time_limit")


# ---------- 3. чартер судится по подлинному номеру рейса ----------

def test_true_fn_strips_date_suffix():
    assert _pb._true_fn_of("ZZ3223-20260812-1") == "ZZ3223"
    assert _pb._true_fn_of("ZZ606") == "ZZ606"
    # номер без суффикса даты не трогается, даже если содержит дефис
    assert _pb._true_fn_of("ZZ-CARGO") == "ZZ-CARGO"


def test_charter_mix_rejected_via_internal_ids():
    """Смешение чартера (ZZ3223) с регулярным (ZZ256) обязано ловиться
    на ВНУТРЕННИХ идентификаторах с суффиксом даты — прежде счёт цифр по
    всей строке не срабатывал никогда."""
    payload = _payload([
        _leg("ZZ256-20260801-1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("ZZ3223-20260801-1", "AAA", "BASE", "2026-08-01T10:00:00",
             120)])
    catalog, report = _pb.build(payload, _args())
    for u in catalog["selected"]:
        fns = set(u.get("active_fns") or ())
        assert not ({"ZZ256-20260801-1", "ZZ3223-20260801-1"} <= fns), \
            "чартер и регулярный рейс попали в одну цепочку"


def test_charter_with_charter_still_allowed():
    """Контроль симметрии: два чартера в одной цепочке законны."""
    payload = _payload([
        _leg("ZZ3223-20260801-1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("ZZ3224-20260801-1", "AAA", "BASE", "2026-08-01T10:00:00",
             120)])
    _catalog, report = _pb.build(payload, _args())
    assert report["uncovered_legs"] == 0


# ---------- 4. пол документа — одна реализация на оба исполнителя ----------

def test_briefing_floor_shared_by_builder_and_exporter():
    """Выгрузка обязана поднимать буферы нарядов тем же методом
    `Conditions.briefing_floor`, что и построитель: станционные 45 минут
    против пола 60 уже давали выгрузку, не совпавшую с расчётом."""
    c = _conds()
    # пол поверх меньшего значения; большее данных не срезается
    assert c.briefing_floor(45, on_base=False) == 60
    assert c.briefing_floor(120, on_base=False) == 120
    assert c.briefing_floor(None, on_base=True) == 100
    assert c.debriefing_floor(20, on_base=False) == 30

    flights = {
        "X1-20260801-1": {
            "flightNumber": "X1-20260801-1",
            "departureAirport": "OFF1", "arrivalAirport": "BASE",
            "departureUTCDateTime": "2026-08-01T06:00:00",
            "arrivalUTCDateTime": "2026-08-01T08:00:00",
            "briefingMin": 45, "debriefingMin": 20},
    }
    brief, debrief, b_fb, d_fb = _p2m._buffer_tables(flights)
    w0, w1 = _p2m._work_period(["X1-20260801-1"], flights, brief, debrief,
                               b_fb, d_fb, conditions=c)
    dep = datetime.fromisoformat("2026-08-01T06:00:00")
    arr = datetime.fromisoformat("2026-08-01T08:00:00")
    assert dep - w0 == timedelta(minutes=60), "пол брифинга вне базы 60"
    assert w1 - arr == timedelta(minutes=60), "дебрифинг в базе: пол 60"
    # без условий выгрузка ведёт себя по-старому — данные как есть
    w0n, w1n = _p2m._work_period(["X1-20260801-1"], flights, brief,
                                 debrief, b_fb, d_fb)
    assert dep - w0n == timedelta(minutes=45)
    assert w1n - arr == timedelta(minutes=20)


# ---------- 5. роль бизнес-класса — из данных, не из кода ----------
# П.1.9: место «бортпроводник с допуском в бизнес-класс» на длинном
# рейсе. Написание роли принадлежит НАБОРУ: зашитая латинская
# «CA C-cl» на наборе с кириллической С (гомоглиф) не совпадала с
# ролью набора — детектор «место уже есть» её не видел, построитель
# достраивал место ролью, которой нет ни у одного сотрудника, и этап 2
# не мог закрыть его в принципе (приёмка UniA, 2026-08-06).

_CYR_CCL = "CA \u0421-cl"      # кириллическая С — гомоглиф латинской C


def _payload_with_roles(flights, roles_of):
    """payload, где слоты рейса задаются списком ролей roles_of[fn]."""
    assigns = []
    for f in flights:
        for i, role in enumerate(roles_of[f["flightNumber"]]):
            assigns.append({"id": f"{f['flightNumber']}#{i}",
                            "flight": f["flightNumber"],
                            "indexInFlight": i, "requiredSkill": role})
    return {"airports": [], "employees": [
        {"id": "E1", "homeAirport": "BASE", "skills": ["Captain", "CA"]}],
        "flights": flights, "flightAssignments": assigns}


def _long_pair():
    """Пара длинных рейсов с ЗАКОННЫМ отдыхом между ними.

    Обратный рейс отнесён так, чтобы между сменами укладывался
    нормативный отдых этой спецификации (24 часа плюс дорога): иначе
    пара покрывается только полукрестом «работа туда, возврат
    пассажиром», а перегон рейсом, который сам никем не выполняется,
    запрещён (ограничение модели покрытия, 2026-08-25)."""
    return [_leg("F905-20260801-1", "BASE", "AAA",
                 "2026-08-01T06:00:00", 7 * 60),
            _leg("F906-20260802-1", "AAA", "BASE",
                 "2026-08-02T16:00:00", 7 * 60)]


def test_c_class_detector_sees_dataset_spelling():
    """Роль бизнес-класса уже есть (в написании набора) — место НЕ
    достраивается: ни зашитой латиницей, ни дублем."""
    flights = _long_pair()
    roles = {f["flightNumber"]: ["Captain", "CA", _CYR_CCL]
             for f in flights}
    catalog, _ = _pb.build(_payload_with_roles(flights, roles), _args())
    for u in catalog["selected"]:
        seats = u.get("seats") or []
        assert "CA C-cl" not in seats, seats
        assert seats.count(_CYR_CCL) <= 3, seats


def test_c_class_augment_uses_dataset_role():
    """Достройка берёт роль ИЗ ДАННЫХ набора: длинный рейс без места
    бизнес-класса получает роль в написании набора (кириллическом),
    хотя сами его участки такой роли не несут."""
    flights = _long_pair() + [
        _leg("F907-20260803-1", "BASE", "AAA", "2026-08-03T06:00:00", 120),
        _leg("F908-20260803-1", "AAA", "BASE", "2026-08-03T10:00:00", 120)]
    roles = {fn: ["Captain", "CA"] for fn in
             (f["flightNumber"] for f in flights)}
    roles["F907-20260803-1"] = ["Captain", "CA", _CYR_CCL]
    roles["F908-20260803-1"] = ["Captain", "CA", _CYR_CCL]
    catalog, _ = _pb.build(_payload_with_roles(flights, roles), _args())
    long_units = [u for u in catalog["selected"]
                  if "F905-20260801-1" in (u.get("active_fns") or ())]
    assert long_units, "длинная пара обязана построиться"
    for u in long_units:
        seats = u.get("seats") or []
        assert "CA C-cl" not in seats, seats
        assert _CYR_CCL in seats, seats


def test_c_class_role_свойство_приёмника():
    """Ключ role узла c_class_attendant_required — контракт с
    построителем: getattr(cond, "c_class_role", None). Ключа нет —
    None; кривое значение (пустая строка, не-строка) — отказ
    приёмника, а не молчаливая подстановка."""
    spec = copy.deepcopy(SPEC)
    spec["A"]["c_class_attendant_required"] = {
        "rule": "П.1.9", "role": "CA X-cl"}
    assert _conditions.Conditions(spec).c_class_role == "CA X-cl"
    assert _conds().c_class_role is None
    for bad in ("", "   ", 7, ["CA X-cl"]):
        s2 = copy.deepcopy(SPEC)
        s2["A"]["c_class_attendant_required"] = {
            "rule": "П.1.9", "role": bad}
        with pytest.raises(_conditions.ConditionsError):
            _conditions.Conditions(s2)


def test_c_class_роль_из_ключа_условий_когда_в_наборе_нет():
    """Набор вовсе без роли бизнес-класса: имя берётся из ключа role
    условий (написание конкретной а/к — в её файле условий), а не из
    зашитой латиницы. Написание нарочно вымышленное — при зашитом
    имени тест невозможен."""
    flights = _long_pair()
    roles = {f["flightNumber"]: ["Captain", "CA"] for f in flights}
    spec = copy.deepcopy(SPEC)
    spec["A"]["c_class_attendant_required"] = {
        "rule": "П.1.9", "role": "CA X-cl"}
    a = _args()
    a.conditions = _conditions.Conditions(spec)
    catalog, _ = _pb.build(_payload_with_roles(flights, roles), a)
    long_units = [u for u in catalog["selected"]
                  if "F905-20260801-1" in (u.get("active_fns") or ())]
    assert long_units, "длинная пара обязана построиться"
    for u in long_units:
        seats = u.get("seats") or []
        assert "CA X-cl" in seats, seats
        assert "CA C-cl" not in seats, seats


def test_c_class_без_роли_в_наборе_и_условиях_громкий_отказ():
    """П.1.9 активен (длинные рейсы), но роли нет ни в спросе набора
    (суффикс «-cl»), ни в ключе role условий — достраивать место
    именем, которого нет в данных, незаконно (условие заказчика
    активно, молчаливый сон тоже незаконен). Форма отказа — исключение
    с П.1.9 либо вердикт каталога: пара не выпускается."""
    flights = _long_pair()
    roles = {f["flightNumber"]: ["Captain", "CA"] for f in flights}
    try:
        catalog, _report = _pb.build(
            _payload_with_roles(flights, roles), _args())
    except Exception as e:  # громкая форма — исключение
        assert "1.9" in str(e) or "бизнес" in str(e), e
        return
    long_units = [u for u in catalog["selected"]
                  if "F905-20260801-1" in (u.get("active_fns") or ())]
    assert not long_units, (
        "П.1.9 без носителя роли: пара не должна выпускаться — ни "
        "зашитым именем, ни без места бизнес-класса")


# ---------- 6. П.1.1.5 видит перегоны при выпуске ----------

def test_charter_deadhead_does_not_mix_with_scheduled():
    """Правило «чартер и регулярное расписание не в одной связке»
    обязано видеть и перегоны: прежде проверка стояла только в chain_ok
    БЕЗ dh-плечей, и перегон, добавленный при выпуске, обходил её.
    Регулярный рейс туда и единственный чартерный возврат: связка
    «работа + чартерный перегон» (и зеркальная «чартерный подвоз +
    работа») не выпускается — оба участка честно непокрыты."""
    flights = [
        _leg("100-20260801-1", "BASE", "AAA", "2026-08-01T06:00:00", 120),
        _leg("3223-20260801-1", "AAA", "BASE", "2026-08-01T10:00:00", 120)]
    roles = {f["flightNumber"]: ["Captain", "CA"] for f in flights}
    catalog, report = _pb.build(_payload_with_roles(flights, roles),
                                _args())
    for u in catalog["selected"]:
        fns = set(u.get("fns") or ())
        assert not {"100-20260801-1", "3223-20260801-1"} <= fns, \
            "чартер и регулярный в одной связке (через перегон)"
    assert report["uncovered_legs"] == 2, report["uncovered_legs"]


# ---------- 7. «Дальний» признак по фактическим поясам ----------

def test_is_trans_honest_by_offsets_with_threshold():
    """Порог `trans_tz_from_h` (группа A) включает честный счёт признака
    по расхождению поясов станции и базы; без порога или без пояса
    остаётся прежняя наихудшая трактовка (PARAM-T-20)."""
    spec = copy.deepcopy(SPEC)
    spec["A"]["trans_tz_from_h"] = {"rule": "PARAM-T-20", "value": 4}
    c = _conditions.Conditions(spec)
    base = 180  # база сети UTC+3
    assert c.is_trans(base + 4 * 60, base)        # ровно четыре часа
    assert not c.is_trans(base + 3 * 60, base)    # три часа — не дальний
    assert not c.is_trans(base - 3 * 60, base)
    assert c.is_trans(base - 5 * 60, base)
    # пояс неизвестен — наихудшая трактовка сохраняется
    assert c.is_trans(None, base) is True
    # порога в спецификации нет — прежнее поведение (b13)
    c_old = _conditions.Conditions(copy.deepcopy(SPEC))
    assert c_old.is_trans(base + 12 * 60, base) is True


# ---------- 8. Точная ярусная форма предела смены ----------

def _spec_exact():
    spec = copy.deepcopy(SPEC)
    # Кабинная норма C-класса выключена недостижимым порогом: третье
    # место в цепочке ниже обязано появляться ТОЛЬКО от яруса состава.
    spec["A"]["c_class_attendant_required"] = {
        "rule": "не применяется", "c_class_required_from_h": 999}
    spec["A"]["worktime_exact"] = {"rule": "прил. 2/3/4/6", "values": {
        "acclimatized_by_period": [
            {"from": "06:00", "to": "14:59",
             "by_landings": {"1-2": 13.0, "3": 12.5, "4": 12.0, "5+": 9.0}},
            {"from": "15:00", "to": "16:59",
             "by_landings": {"1-2": 12.5, "3": 12.0, "4": 11.5, "5+": 9.0}},
            {"from": "17:00", "to": "23:59",
             "by_landings": {"1-2": 12.0, "3": 11.5, "4": 11.0, "5+": 9.0}},
            {"from": "00:00", "to": "05:59",
             "by_landings": {"1-2": 11.5, "3": 11.0, "4": 10.5, "5+": 9.0}},
        ],
        "unknown_by_landings": {"1-2": 11.0, "3": 10.5, "4": 10.0, "5+": 9.0},
        "acclimatization": {"elapsed_b_h": 48, "tz_from_h": 2},
        "extended_by_class_h": {"1": 18.0, "2": 17.0, "3": 16.0},
        "rest_class_by_fleet": {"738": 2, "A330-2": 3},
    }}
    return spec


def test_exact_worktime_limits_by_period_state_and_class():
    """Прил. 3 по периоду базового местного времени старта; прил. 4 при
    неопределённой акклиматизации; прил. 6 по классу места отдыха; тип
    без класса продления не имеет (fail-closed PARAM-T-13)."""
    c = _conditions.Conditions(_spec_exact())
    assert c.has_exact_worktime
    # Плоский продлённый предел без лестницы — запасной путь: он
    # единственная ВЕРХНЯЯ ступень ценой двух дополнительных членов
    # (реестр 2026-08-28: цена одного члена при пределе удвоенного
    # состава недокомплектовывала экипаж).
    assert c.exact_limits(10 * 60, 2, True, "738") == (780.0,
                                                       [(2, 1020.0)])
    assert c.exact_limits(1 * 60, 2, True, "738")[0] == 690.0
    assert c.exact_limits(10 * 60, 2, False, "738")[0] == 660.0
    assert c.exact_limits(10 * 60, 2, True, "A320") == (780.0, [])
    assert c.exact_limits(10 * 60, 7, True, "738")[0] == 540.0
    # период считается по модулю суток: 34:00 = 10:00 следующего дня
    assert c.exact_limits(34 * 60, 2, True, "738")[0] == 780.0
    # без формы прежнее поведение
    c_old = _conditions.Conditions(copy.deepcopy(SPEC))
    assert not c_old.has_exact_worktime


def _spec_ladder():
    spec = _spec_exact()
    spec["A"]["worktime_exact"]["values"]["extended_ladder"] = {
        "steps": [
            {"extra_crew": 1,
             "by_class_min": {"1": 960, "2": 900, "3": 840}},
            {"extra_crew": 2,
             "by_class_min": {"1": 1020, "2": 960, "3": 900}},
        ],
        "long_sector_bonus": {
            "add_min": 60,
            "condition": {"kind": "one_sector_over_h", "value": 9,
                          "max_sectors": 2}},
    }
    return spec


def test_лестница_продления_по_числу_дополнительных_членов():
    """Лестница ступеней (R-S2-п-23-1 / R-S5-т2-с6): ступени по
    возрастанию состава, наименьшую достаточную выбирает судья;
    ступень мягче базового предела или предыдущей отбрасывается;
    немонотонность по составу — отказ приёмника (fail-closed)."""
    c = _conditions.Conditions(_spec_ladder())
    base, steps = c.exact_limits(10 * 60, 2, True, "738")  # класс 2
    assert base == 780.0
    assert steps == [(1, 900.0), (2, 960.0)]
    assert c.long_sector_bonus == {"add_min": 60.0, "over_min": 540.0,
                                   "max_sectors": 2}
    # тип без класса места отдыха — продления нет и лестницей
    assert c.exact_limits(10 * 60, 2, True, "A320")[1] == []
    # наименьшая достаточная ступень — зеркало tier_for_work_time
    assert _pb.Builder._least_tier(700, base, steps) == 0
    assert _pb.Builder._least_tier(880, base, steps) == 1
    assert _pb.Builder._least_tier(950, base, steps) == 2
    assert _pb.Builder._least_tier(1000, base, steps) is None
    # ступень мягче базового предела отбрасывается
    s2 = _spec_ladder()
    s2["A"]["worktime_exact"]["values"]["extended_ladder"]["steps"][0][
        "by_class_min"]["2"] = 700
    assert _conditions.Conditions(s2).exact_limits(
        10 * 60, 2, True, "738")[1] == [(2, 960.0)]
    # ступень большего состава мягче предыдущей отбрасывается
    s3 = _spec_ladder()
    s3["A"]["worktime_exact"]["values"]["extended_ladder"]["steps"][1][
        "by_class_min"]["2"] = 850
    assert _conditions.Conditions(s3).exact_limits(
        10 * 60, 2, True, "738")[1] == [(1, 900.0)]
    # ступени не строго возрастают по составу — отказ приёмника
    s4 = _spec_ladder()
    s4["A"]["worktime_exact"]["values"]["extended_ladder"]["steps"][1][
        "extra_crew"] = 1
    with pytest.raises(_conditions.ConditionsError):
        _conditions.Conditions(s4)


def test_exact_acclimatization_matrix():
    """Прил. 2: разница поясов меньше порога — акклиматизирован всегда;
    иначе только первые 48 часов ротации; неизвестный пояс — нет."""
    c = _conditions.Conditions(_spec_exact())
    base = 180
    assert c.is_acclimatized(base + 60, base, 1000 * 60)
    assert c.is_acclimatized(base + 300, base, 47 * 60)
    assert not c.is_acclimatized(base + 300, base, 49 * 60)
    assert not c.is_acclimatized(None, base, 0)


# ---------- 9. Точная форма в построителе: ярусы и позиция в ротации ----------

def _exact_args(**over):
    a = _base_args(**over)
    a.conditions = _conditions.Conditions(_spec_exact())
    r = _cos_rules.CosRules()
    r.base_tz_offset_min = 0
    r.night_seq_max = 99
    r.night_next_duty_start_min = 0
    r.night_seq_layover_on_base_min = 0
    r.night_seq_layover_off_base_min = 0
    r.min_sit_min = 50
    r.max_pairing_days = 14
    r.max_landings_per_duty = 10
    # Явная синтетика остальной девятки: умолчаний у CosRules больше
    # нет, незаполненное поле — отказ при обращении.
    r.work_time_limit_by_tier = (720, 840, 960)
    r.layover_tiers = (
        _cos_rules.LayoverTier(720, 720, 760, 1440, 760, 1440, 1440),
        _cos_rules.LayoverTier(840, 2160, 1480, 2160, 1480, 2880, 1440),
        _cos_rules.LayoverTier(960, 2160, 1480, 2160, 1480, 2880, 1440),
    )
    r.trans_tz_hours = 3.0
    r.trans_leg_min = 720
    r.dh_board_after_debrief_min = 20
    r.teleportation = {}
    a.cos_rules = r
    a.pairing_span = 14 * 1440
    # Сцепка с вектором фикстуры (синтетика): порог стыковки при
    # правилах — args.sit_min; держим равным min_sit_min правил выше.
    a.sit_min = over.get("sit_min", 50)
    return a


def _exact_payload(fleet, leg_minutes, airports=()):
    flights = [
        _leg("G1-20260801-1", "BASE", "AAA", "2026-08-01T08:00:00",
             leg_minutes, fleet),
        _leg("G2-20260801-1", "AAA", "BASE", "2026-08-01T08:00:00", 1, fleet)]
    t0 = datetime.fromisoformat("2026-08-01T08:00:00")
    back = t0 + timedelta(minutes=leg_minutes + 60)
    flights[1]["departureUTCDateTime"] = back.isoformat()
    flights[1]["arrivalUTCDateTime"] = (
        back + timedelta(minutes=leg_minutes)).isoformat()
    p = _payload(flights)
    p["airports"] = list(airports)
    return p


def test_exact_extended_tier_builds_with_extra_seat():
    """Смена сверх базового предела прил. 3, но в продлённом пределе
    прил. 6 (класс 2) строится ступенью лестницы. Плоский предел без
    лестницы — ВЕРХНЯЯ ступень ценой ДВУХ дополнительных членов
    (реестр 2026-08-28: прежняя цена одного члена при пределе
    удвоенного состава недокомплектовывала экипаж)."""
    payload = _exact_payload("738", 400)  # 90+400+60+400+30 = 980 > 780
    catalog, report = _pb.build(payload, _exact_args())
    assert report["uncovered_legs"] == 0
    assert report["units"] == 1
    assert len(catalog["selected"][0]["seats"]) == 4


def test_лестница_цена_наименьшей_достаточной_ступени():
    """Двухступенчатая лестница: смена в пределах ступени одного
    дополнительного члена стоит ОДНО дополнительное место, в пределах
    ступени двух — два. Выбор ступени — наименьший достаточный состав
    (правила R-S2-п-23-1 / R-S5-т2-с6)."""
    a1 = _exact_args()
    a1.conditions = _conditions.Conditions(_spec_ladder())
    catalog, report = _pb.build(_exact_payload("738", 340), a1)
    # 100+340+60+340+60 = 900 = ступень 1 класса 2 (900 мин)
    assert report["uncovered_legs"] == 0
    assert len(catalog["selected"][0]["seats"]) == 3
    a2 = _exact_args()
    a2.conditions = _conditions.Conditions(_spec_ladder())
    catalog2, report2 = _pb.build(_exact_payload("738", 370), a2)
    # 100+370+60+370+60 = 960 — сверх ступени 1, в ступени 2 (960 мин)
    assert report2["uncovered_legs"] == 0
    assert len(catalog2["selected"][0]["seats"]) == 4


def test_исключённые_типы_только_перегоны_и_отдельная_категория():
    """Исключение по типам: леги из fleet_auto_plan_excluded — не обязательства
    покрытия (участвуют только перегонами), отчёт покрытия несёт их
    отдельной поимённой категорией, а не растворяет в транспорте."""
    spec = _spec_exact()
    spec["A"]["fleet_auto_plan_excluded"] = {"types": ["XJ1"],
                                             "source": "TEST-EXCL-1"}
    a = _exact_args()
    a.conditions = _conditions.Conditions(spec)
    payload = _exact_payload("738", 300)
    payload["flights"].append(
        _leg("X1-20260801-1", "BASE", "AAA", "2026-08-01T09:00:00",
             120, "XJ1"))
    catalog, report = _pb.build(payload, a)
    cov = catalog["coverage"]
    assert cov["auto_plan_excluded"] == ["X1-20260801-1"]
    assert cov["legs_total"] == 2, (
        "исключённый лег не входит в знаменатель обязательств")
    assert report["uncovered_legs"] == 0, (
        "исключённый лег непокрытым не считается")


def test_ранние_вылеты_из_базы_судятся():
    """Правило ранних вылетов: участок с вылетом из базы раньше порога
    UTC не достигается стыковкой в базе; пейринг с ранним вылетом из
    базы не содержит промежуточной посадки в базе. Пороги и метки
    источников здесь вымышленные; без ключей правила выключены."""
    spec = _spec_exact()
    spec["A"]["base_sit_dep_utc_before_min"] = {
        "value": 210, "source": "TEST-EARLY-SIT"}
    spec["A"]["base_turn_dep_utc_before_min"] = {
        "value": 210, "source": "TEST-EARLY-TURN"}
    a = _exact_args()
    a.conditions = _conditions.Conditions(spec)
    flights = [
        _leg("W1-20260801-1", "BASE", "AAA", "2026-08-01T22:00:00",
             120, "738"),
        _leg("W2-20260802-1", "AAA", "BASE", "2026-08-02T01:00:00",
             60, "738"),
        _leg("W3-20260802-1", "BASE", "BBB", "2026-08-02T03:00:00",
             120, "738"),
        _leg("W4-20260802-1", "BASE", "BBB", "2026-08-02T04:30:00",
             120, "738"),
    ]
    b = _pb.Builder(_payload(flights), a)
    trio_early = [b.legs["W1-20260801-1"], b.legs["W2-20260802-1"],
                  b.legs["W3-20260802-1"]]
    # вылет из базы 03:00 UTC через стыковку — запрещён
    assert not b.duty_limit_ok(trio_early)
    # тот же рисунок с вылетом 04:30 UTC — порог не задет
    trio_late = [b.legs["W1-20260801-1"], b.legs["W2-20260802-1"],
                 b.legs["W4-20260802-1"]]
    assert b.duty_limit_ok(trio_late)
    # цепочечная половина: ранний старт из базы, возврат в базу не
    # последней сменой — запрещено
    b.build_duties(None)
    d_early = next(d for d in b.duties
                   if [l["id"] for l in d["legs"]]
                   == ["W3-20260802-1"])
    d_back = next(d for d in b.duties
                  if [l["id"] for l in d["legs"]]
                  == ["W2-20260802-1"])
    assert not b.chain_ok([d_early, d_back, d_early])
    # перегон, достигаемый стыковкой в базе, судится той же точкой
    # (судья периодов: непервый участок периода достигнут стыковкой)
    d_in = next(d for d in b.duties
                if [l["id"] for l in d["legs"]] == ["W2-20260802-1"])
    assert b.periods_tiers([d_in], [b.legs["W3-20260802-1"]]) is None
    assert b.periods_tiers([d_in],
                           [b.legs["W4-20260802-1"]]) is not None
    # без ключей условий оба правила выключены
    a2 = _exact_args()
    a2.conditions = _conditions.Conditions(_spec_exact())
    b2 = _pb.Builder(_payload(flights), a2)
    assert b2.duty_limit_ok(trio_early)


def test_слитый_период_судится_строжайшей_формой():
    """Слитый период обязанностей со сменами разных типов ВС судится
    формой пределов КАЖДОЙ смены: тип без класса места отдыха внутри
    периода лишает продления весь период (прежний суд по классу
    последней смены пропускал строгий тип не последним)."""
    def payload(first_fleet):
        return _payload([
            _leg("W1-20260801-1", "BASE", "AAA", "2026-08-01T08:00:00",
                 400, first_fleet),
            _leg("W2-20260801-1", "AAA", "BASE", "2026-08-01T18:00:00",
                 200, "738"),
        ])
    def tiers(first_fleet):
        a = _exact_args()
        a.conditions = _conditions.Conditions(_spec_exact())
        b = _pb.Builder(payload(first_fleet), a)
        b.build_duties(None)
        d1 = next(d for d in b.duties
                  if [l["id"] for l in d["legs"]] == ["W1-20260801-1"])
        d2 = next(d for d in b.duties
                  if [l["id"] for l in d["legs"]] == ["W2-20260801-1"])
        return b.periods_tiers([d1, d2], [])
    # контроль: обе смены 738 (класс 2) — период идёт продлением
    assert tiers("738") is not None
    # тип без класса первым в периоде — продления нет, период
    # недопустим (fail-closed)
    assert tiers("E95") is None


def test_надбавка_длинного_сектора_по_секторам_периода():
    """Надбавка +60 при секторе сверх порога даётся только когда
    секторов у СУДИМОЙ ЕДИНИЦЫ не больше границы: слитый период с
    тремя секторами надбавки не получает."""
    a = _exact_args()
    a.conditions = _conditions.Conditions(_spec_ladder())
    b = _pb.Builder(_exact_payload("738", 580), a)
    b.build_duties(None)
    duo = next(d for d in b.duties
               if [l["id"] for l in d["legs"]] == ["G1-20260801-1"])
    w0 = duo.get("work0") or duo["t0"]
    with_bonus = b._exact_limits(duo["legs"], w0, 2, True)[1]
    without = b._exact_limits(duo["legs"], w0, 3, True)[1]
    assert [lim for _c, lim in with_bonus] == \
        [lim + 60 for _c, lim in without]


def test_засев_не_минует_фильтр_исключённых_типов():
    """Исключение по типам: группа засева (уровни 1-2, закрепления, добор) с
    транспортным либо исключённым участком отклоняется целиком и
    поимённо считается — рабочей сменой такой участок не бывает ни
    на одном пути (состязательная выверка 2026-08-28)."""
    spec = _spec_exact()
    spec["A"]["fleet_auto_plan_excluded"] = {"types": ["XJ1"],
                                             "source": "TEST-EXCL-1"}
    a = _exact_args()
    a.conditions = _conditions.Conditions(spec)
    payload = _exact_payload("738", 300)
    payload["flights"].append(
        _leg("X1-20260801-1", "BASE", "AAA", "2026-08-01T09:00:00",
             120, "XJ1"))
    b = _pb.Builder(payload, a)
    catalog = {"selected": [
        {"duty_fns": [["X1-20260801-1", "G1-20260801-1"]]}]}
    b.build_duties(catalog)
    assert b.report.get("seeded_groups_dropped_transport") == 1
    assert not any("X1-20260801-1" in [l["id"] for l in d["legs"]]
                   for d in b.duties)


def test_exact_no_class_refuses_extended_duty():
    """Тот же профиль на типе без класса места отдыха (Embraer):
    продления нет (PARAM-T-13 fail-closed). Не строится ни сдвоенная
    смена, ни «крест» (работа в одну сторону плюс возврат перегоном):
    перегон, не отделённый нормативным отдыхом, продолжает период
    обязанностей, и период целиком выходит за предел. Оба участка честно
    остаются непокрытыми."""
    payload = _exact_payload("E95", 400)
    catalog, report = _pb.build(payload, _exact_args())
    assert report["uncovered_legs"] == 2
    assert not catalog["selected"]


def test_deadhead_without_rest_extends_duty_period():
    """Перегон без нормативного отдыха продолжает период обязанностей, и
    предел меряется ДЛИТЕЛЬНОСТЬЮ периода, а не суммой времён перелётов.

    Класс дефекта (аудит передаваемого файла 2026-08-25): прежняя форма
    складывала рабочее время и время перегонов, не видя стоянок между
    ними, и выпускала периоды до 23 часов. Здесь рабочая смена коротка,
    перегон короток, но между ними шесть часов стоянки — сумма времён в
    предел укладывается, длительность периода нет.

    Тот же перегон, отнесённый за нормативный отдых, образует
    собственный период и цепочку не ломает — контрольный случай.
    Проверяется сам судья периодов, а не эвристика поиска заброски."""
    base = datetime.fromisoformat("2026-08-01T04:00:00")
    def payload(gap_h):
        dh = _leg("D1-20260801-1", "BASE", "AAA", base.isoformat(), 120,
                  "738")
        w0 = base + timedelta(minutes=120 + gap_h * 60)
        work = [
            _leg("W1-20260801-1", "AAA", "BBB", w0.isoformat(), 300, "738"),
            _leg("W2-20260801-1", "BBB", "BASE",
                 (w0 + timedelta(minutes=360)).isoformat(), 300, "738")]
        p = _payload([dh] + work)
        p["airports"] = [{"code": c, "timeZone": "UTC"}
                         for c in ("BASE", "AAA", "BBB")]
        return p

    def judge(gap_h):
        b = _pb.Builder(payload(gap_h), _exact_args())
        b.build_duties(None)
        chain = [d for d in b.duties
                 if [l["id"] for l in d["legs"]]
                 == ["W1-20260801-1", "W2-20260801-1"]]
        assert chain, "рабочая смена не построена — проверять нечего"
        dh = [b.legs["D1-20260801-1"]]
        return b.periods_tiers(chain, dh), b.periods_tiers(chain, [])

    with_dh, without = judge(6)
    assert without is not None, "рабочая смена сама по себе допустима"
    assert with_dh is None, (
        "перегон через шесть часов стоянки обязан продолжать период "
        "обязанностей и выводить его за предел")
    # Порог отделения — отдых после САМОЙ перегонной смены; в этой
    # спецификации пояса неизвестны, поэтому действует наихудшая
    # трактовка (24 часа плюс дорога), и контрольный разрыв берётся
    # заведомо больше неё (пол 1480 минут: 24 часа плюс дорога).
    with_dh_rested, _ = judge(28)
    assert with_dh_rested is not None, (
        "перегон за нормативным отдыхом образует свой период и цепочку "
        "не ломает")


def test_per_duty_semantics_allows_split_turnaround():
    """Трактовка «per_duty» (ключ условий, группа B): предел судится по
    каждой рабочей смене, перегоны и стоянки период не склеивают.
    Конструкция, запрещённая строгой трактовкой, при «per_duty»
    допустима; строгая остаётся умолчанием."""
    spec = _spec_exact()
    spec["A"]["worktime_exact"]["values"]["period_semantics"] = "per_duty"
    # sit_max явно (синтетика фикстуры): примыкание 60 мин обязано
    # склеивать «крест» (зазор не больше sit_max) — защита от дрейфа
    # вектора.
    a = _exact_args(sit_max=300)
    a.conditions = _conditions.Conditions(spec)
    b = _pb.Builder(_exact_payload("E95", 400), a)
    b.build_duties(None)
    duo = [d for d in b.duties
           if [l["id"] for l in d["legs"]]
           == ["G1-20260801-1", "G2-20260801-1"]]
    dh = [b.legs["G2-20260801-1"]]
    single = [d for d in b.duties
              if [l["id"] for l in d["legs"]] == ["G1-20260801-1"]]
    # строгая трактовка запрещает «крест» (работа + перегон впритык)
    strict = _pb.Builder(_exact_payload("E95", 400), _exact_args(sit_max=300))
    strict.build_duties(None)
    s_single = [d for d in strict.duties
                if [l["id"] for l in d["legs"]] == ["G1-20260801-1"]]
    s_dh = [strict.legs["G2-20260801-1"]]
    assert strict.periods_tiers(s_single, s_dh) is None
    # «per_duty» ту же конструкцию допускает
    assert b.periods_tiers(single, dh) is not None


def test_rest_after_extended_period_is_fourteen_hours():
    """Правило 0041: после периода, прошедшего продлённым пределом
    (усиленный состав, приложение 6), отдых не менее 14 часов.

    Класс дефекта (аудит передаваемого файла 2026-08-25): пол отдыха
    брался только из таблицы и яруса не видел — цепочка выпускалась с
    отдыхом 12 часов после усиленной смены."""
    a = _exact_args()
    b = _pb.Builder(_exact_payload("738", 400), a)
    b.build_duties(None)
    duo = [d for d in b.duties
           if [l["id"] for l in d["legs"]] == ["G1-20260801-1",
                                               "G2-20260801-1"]]
    assert duo, "сдвоенная смена не построена — проверять нечего"
    pers = b.periods_of(duo, [])
    # Плоский предел без лестницы — верхняя ступень ценой двух
    # дополнительных членов (реестр 2026-08-28), потому ярус 2.
    assert pers and pers[-1][2] == 2, "смена обязана идти продлённым пределом"
    assert b.rest_floor_after(pers[-1]) >= 14 * 60
    # контроль: у обычного периода пол остаётся табличным
    single = [d for d in b.duties
              if [l["id"] for l in d["legs"]] == ["G1-20260801-1"]]
    p0 = b.periods_of(single, [])
    assert p0 and p0[-1][2] == 0
    assert b.rest_floor_after(p0[-1]) == b.rest_after(single[0], "AAA")


def test_underrest_before_deadhead_forbidden_in_per_duty():
    """«Недоотдых» перед перегоном запрещён и в трактовке per_duty.

    Класс дефекта (аудит передаваемого файла 2026-08-25, 4 цепочки, и
    приёмка П12, 8 пар): перегон после рабочей смены с отдыхом больше
    примыкания (240 мин), но меньше требуемого max(FDP, пол). Судья
    периодов склеивал такой перегон в один период, а per_duty мерил
    пределом только рабочую часть — период рос безнаказанно.

    Три случая: примыкание (60 мин) допустимо — крест; недоотдых
    (6 часов) запрещён; полный отдых (28 часов, наихудшая трактовка
    неизвестных поясов) допустим — перегон отдельным нарядом."""
    spec = _spec_exact()
    spec["A"]["worktime_exact"]["values"]["period_semantics"] = "per_duty"

    def judge(gap_min):
        base = datetime.fromisoformat("2026-08-01T04:00:00")
        work = [
            _leg("W1-20260801-1", "BASE", "AAA", base.isoformat(), 300,
                 "738"),
            _leg("W2-20260801-1", "AAA", "BBB",
                 (base + timedelta(minutes=360)).isoformat(), 300, "738")]
        dh_t0 = base + timedelta(minutes=660 + gap_min)
        dh = _leg("D1-20260801-1", "BBB", "BASE", dh_t0.isoformat(), 120,
                  "738")
        p = _payload(work + [dh])
        p["airports"] = [{"code": c, "timeZone": "UTC"}
                         for c in ("BASE", "AAA", "BBB")]
        # sit_max явно (синтетика фикстуры): окно [60, 360) — зазор 60
        # примыкает (крест), зазор 360 больше sit_max и судится
        # недоотдыхом; защита от дрейфа вектора.
        a = _exact_args(sit_max=300)
        a.conditions = _conditions.Conditions(spec)
        b = _pb.Builder(p, a)
        b.build_duties(None)
        chain = [d for d in b.duties
                 if [l["id"] for l in d["legs"]]
                 == ["W1-20260801-1", "W2-20260801-1"]]
        assert chain, "рабочая смена не построена — проверять нечего"
        return b.periods_tiers(chain, [b.legs["D1-20260801-1"]])

    assert judge(60) is not None, "примыкающий перегон (крест) допустим"
    assert judge(360) is None, (
        "перегон через шесть часов после смены — недоотдых: больше "
        "примыкания, меньше требуемого отдыха")
    assert judge(28 * 60) is not None, (
        "перегон за полным нормативным отдыхом — отдельный наряд")


# ---------- 10. порог ветви b12 — из данных, не из кода ----------

def test_b12_порог_из_данных_судит_лестницей():
    """Порог «смена до N часов» приходит из values.work_upto_h файла
    условий: при пороге 10 ч смена 9 ч идёт неоднозначной ветвью, а
    смена 11 ч — лестницей П.3.5. При зашитой константе 12 этот тест
    невозможен — он и есть доказательство переноса порога в данные."""
    spec = copy.deepcopy(SPEC)
    spec["B"]["b12_rest_base_upto_12h"] = {
        "values": {"work_upto_h": 10, "rest_h_if_ambiguous": 20}}
    c = _conditions.Conditions(spec)
    assert c.required_layover(9 * 60, on_base=True, night=False,
                              trans=False) == 20 * 60
    assert c.required_layover(11 * 60, on_base=True, night=False,
                              trans=False) == 36 * 60  # лестница П.3.5


def test_b12_без_порога_отказ_словами():
    """Файл условий без work_upto_h — отказ на загрузке, не в
    судействе: молчаливое чтение вернуло бы зашитый порог, которого
    больше нет, либо молча ослабило бы лестницу."""
    spec = copy.deepcopy(SPEC)
    spec["B"]["b12_rest_base_upto_12h"] = {
        "values": {"rest_h_if_ambiguous": 24, "rest_h_min": 12}}
    with pytest.raises(_conditions.ConditionsError) as e:
        _conditions.Conditions(spec)
    assert "work_upto_h" in str(e.value)
    assert "порог" in str(e.value)


def test_b12_нечисловой_порог_тоже_отказ():
    spec = copy.deepcopy(SPEC)
    spec["B"]["b12_rest_base_upto_12h"] = {
        "values": {"work_upto_h": "12", "rest_h_if_ambiguous": 24}}
    with pytest.raises(_conditions.ConditionsError):
        _conditions.Conditions(spec)
