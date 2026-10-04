"""Усечения поиска как наблюдаемый факт (аудит R9, P0-01 и его соседи).

Корень класса: признак усечения выводился ИЗ ДЛИНЫ уже усечённого списка.
Такая проверка не отличает «ровно k существовало» от «k+1 был выброшен», а
в двух местах из шести её просто забыли написать. Каждый раунд аудита
закрывал один забытый вызов и находил соседний.

Тесты ниже проверяют не отдельные места, а ФОРМУ механизма: пока отбор сам
записывает усечение в момент наблюдения отброшенного варианта, «забыть»
записать его нельзя, и новый вызывающий наследует правильное поведение
автоматически.
"""
from __future__ import annotations

import pytest

import _pricing_certificate as pc


# --------------------------------------------------------------- механизм

def test_truncation_is_recorded_only_when_a_variant_was_discarded():
    """Корень класса: предел, равный числу вариантов, — НЕ усечение."""
    sink = pc.Truncations(pc.SCOPE_PRICING)
    got = pc.take([1, 2, 3], 3, sink=sink, name="dh_options")
    assert got == [1, 2, 3]
    assert sink.names() == [], (
        "предел сработал ровно по границе — отброшенного варианта не было, "
        "и объявлять перебор усечённым нельзя")
    assert not sink.suppresses_bound


def test_truncation_is_recorded_when_the_next_variant_exists():
    sink = pc.Truncations(pc.SCOPE_PRICING)
    got = pc.take([1, 2, 3, 4], 3, sink=sink, name="dh_options")
    assert got == [1, 2, 3]
    assert sink.names() == ["dh_options"]
    assert sink.suppresses_bound
    assert sink.as_rows() == [
        {"name": "dh_options", "limit": 3, "observed_at_least": 4,
         "scope": "pricing"}]


def test_take_preserves_order_and_selection():
    """Отбор обязан совпадать со срезом по элементам и по порядку: это
    защита ЧИСЕЛ заказчика — механизм наблюдения не должен менять решение.
    """
    src = list("abcdefg")
    for k in range(0, len(src) + 2):
        sink = pc.Truncations(pc.SCOPE_PRICING)
        assert pc.take(src, k, sink=sink, name="x") == src[:k]


def test_take_does_not_consume_more_than_one_extra_element():
    """Подсматривается ровно один вариант: перебор не должен дорожать."""
    consumed = []

    def counting():
        for i in range(100):
            consumed.append(i)
            yield i

    pc.take(counting(), 3, sink=pc.Truncations(pc.SCOPE_PRICING), name="x")
    assert consumed == [0, 1, 2, 3]


def test_sink_must_be_named_explicitly():
    """Приёмник — параметр без умолчания: «забыл приёмник» обязано быть
    ошибкой вызова, а не тихим пропуском усечения."""
    with pytest.raises(TypeError):
        pc.take([1, 2], 1, name="x")            # type: ignore[call-arg]


def test_probe_scope_may_pass_no_sink_deliberately():
    """Зонд существования вправе не иметь приёмника — но обязан сказать
    это явно, значением None, а не отсутствием аргумента."""
    assert pc.take([1, 2, 3], 1, sink=None, name="x") == [1]


def test_pool_truncation_does_not_suppress_the_bound():
    """Разогрев пула усечён почти всегда — в этом смысл эвристики. Границу
    доказывает последний проход ценообразования, а не пул. Слить приёмники
    значило бы не публиковать границу НИКОГДА."""
    pool = pc.Truncations(pc.SCOPE_POOL)
    pc.take([1, 2], 1, sink=pool, name="dh_options")
    assert pool.names() == ["dh_options"]
    assert not pool.suppresses_bound
    assert pc.suppressed_by(pool) == []


def test_duty_space_truncation_suppresses_the_bound():
    """Пространство смен урезается ДО ценообразования, поэтому «перебор
    исчерпан» доказывает исчерпание урезанного множества."""
    duty = pc.Truncations(pc.SCOPE_DUTY)
    pc.take([1, 2], 1, sink=duty, name="branch_duty")
    assert duty.suppresses_bound
    assert pc.suppressed_by(duty) == ["duty"]


def test_repeated_records_keep_the_strongest_witness():
    sink = pc.Truncations(pc.SCOPE_PRICING)
    sink.record("depth", 6, 7)
    sink.record("depth", 6, 12)
    sink.record("depth", 6, 9)
    assert sink.as_rows()[0]["observed_at_least"] == 12


@pytest.mark.parametrize("value,expected", [
    (10.0, 10), (10.4, 10), (10.5, 10), (10.9, 10), (-0.5, -1), (0.0, 0)])
def test_lower_bound_rounds_down_not_to_nearest(value, expected):
    """Граница СНИЗУ, округлённая к ближайшему, границей быть перестаёт:
    она способна оказаться выше истинного оптимума на полединицы."""
    assert pc.floor_bound(value) == expected


def test_negative_limit_is_refused():
    with pytest.raises(ValueError):
        pc.take([1], -1, sink=None, name="x")


# ------------------------------------------------- механизм в построителе

def test_deadhead_options_record_truncation_through_the_sink():
    """Отбор перегонов записывает усечение сам — вызывающему нечего
    забывать. Прежде это решал каждый из шести вызывающих отдельно.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    import test_pairing_builder as T
    import _pairing_builder as pb

    payload = T._payload([
        T._leg("D1", "BASE", "AAA", "2026-08-01T04:00:00", 60),
        T._leg("D2", "BASE", "AAA", "2026-08-01T05:00:00", 60),
        T._leg("D3", "BASE", "AAA", "2026-08-01T06:00:00", 60),
        T._leg("W1", "AAA", "BASE", "2026-08-01T10:00:00", 120)])
    b = pb.Builder(payload, T._args(dh_options=1))

    sink = pc.Truncations(pc.SCOPE_PRICING)
    from datetime import datetime
    opts = b.dh_options("BASE", "AAA",
                        datetime.fromisoformat("2026-08-01T00:00:00"),
                        datetime.fromisoformat("2026-08-01T09:00:00"),
                        1, latest=True, sink=sink)
    assert len(opts) == 1
    assert "dh_options" in sink.names(), (
        "три варианта перегона при пределе в один — усечение наблюдалось")
    assert sink.suppresses_bound


@pytest.mark.parametrize("knob,other", [
    ("tz_offset_min", 180),
    ("min_rest_sit", 45),
    ("rest_base", 900),
    ("rest_base_long", 2400),
    ("rest_off", 800),
    ("rest_off_long", 1600),
    ("source_cost", False),
    # деци-ставки критерия 0096 меняют ЦЕЛЬ — подпись обязана различать
    # постановки (пакет «умолчания-в-данные»; ставки из блока rates)
    ("cost_rank_deci_work", 21),
    ("cost_rank_deci_dh", 9),
    ("briefing_default_min", 45),
    ("debriefing_default_min", 35),
    ("workers", 1),
])
def test_signature_separates_runs_by_every_formulation_parameter(knob, other):
    """Подпись постановки обязана различать прогоны по КАЖДОМУ параметру,
    меняющему допустимость или цель (внешний аудит R9, P1-01).

    Прежде подпись перечисляла ручки ПОИСКА, но не эти девять. Значит
    исторические пулы, лучшие решения и результаты разных математических
    постановок можно было смешивать, считая их одной задачей.
    """
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    import test_pairing_builder as T
    import _pairing_builder as pb

    payload = T._simple_round_trip()
    base = pb.Builder(payload, T._args()).config_signature()
    changed = pb.Builder(payload, T._args(**{knob: other})).config_signature()
    assert base != changed, (
        f"параметр {knob} меняет постановку задачи, но не меняет её личность")


def test_signature_access_survives_a_minimal_namespace():
    """Подпись читают тесты и инструмент сверки, собирающие набор
    аргументов вручную. Прямое обращение к новому полю уронило бы их
    ошибкой атрибута — то есть скрыло бы дефект вместо того, чтобы его
    показать. Доступ обязан оставаться мягким."""
    import argparse
    import _pairing_builder as pb

    b = pb.Builder.__new__(pb.Builder)
    b.args = argparse.Namespace(
        level=3, method="columns", budget_s=1, solve_s=0, seed=1,
        bases="BASE", w_night=1, w_dh=1, duty_span=1, pairing_span=1,
        max_segs=1, sit_min=1, rescue_dh=1, rescue_per_leg=1)
    b.fixed_meta = None
    sig = b.config_signature()
    assert "tz_offset_min" in sig and sig["tz_offset_min"] is None


def test_deadhead_probe_without_sink_records_nothing():
    """Зонд существования не пишет усечений: вопрос «есть ли хоть один»
    усечением до одного не искажается."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))
    import test_pairing_builder as T
    import _pairing_builder as pb
    from datetime import datetime

    payload = T._payload([
        T._leg("D1", "BASE", "AAA", "2026-08-01T04:00:00", 60),
        T._leg("D2", "BASE", "AAA", "2026-08-01T05:00:00", 60),
        T._leg("W1", "AAA", "BASE", "2026-08-01T10:00:00", 120)])
    b = pb.Builder(payload, T._args(dh_options=1))
    opts = b.dh_options("BASE", "AAA",
                        datetime.fromisoformat("2026-08-01T00:00:00"),
                        datetime.fromisoformat("2026-08-01T09:00:00"),
                        1, latest=True, sink=None)
    assert len(opts) == 1          # вызов работает и без приёмника


# ---------- PricingResult: полнота вычисляется, не записывается ----------
# Аудит R10, пакет A: тройка (columns, exhausted, caps) запрещена — булево
# «исчерпано» без перечня усечений объявляло полноту без доказательства.

def test_pricing_result_certifies_only_without_suppressing_rows():
    from _pricing_certificate import (PricingResult, Truncations,
                                      SCOPE_PRICING, SCOPE_POOL)
    clean = PricingResult(columns=(), sinks=(), problem_fingerprint="fp")
    assert clean.certifies_bound()
    pool = Truncations(SCOPE_POOL)
    pool.record("dh_options", 1, 2)
    warm = PricingResult(columns=(), sinks=(pool,), problem_fingerprint="fp")
    assert warm.certifies_bound(), "пул — разогрев, границу не подавляет"
    pricing = Truncations(SCOPE_PRICING)
    pricing.record("branch_duty", 1, 2)
    cut = PricingResult(columns=(), sinks=(pricing,),
                        problem_fingerprint="fp")
    assert not cut.certifies_bound()
    assert {"name": "branch_duty", "limit": 1, "observed_at_least": 2,
            "scope": SCOPE_PRICING} in cut.truncation_rows()


def test_pricing_result_deadline_suppresses_and_is_listed():
    from _pricing_certificate import PricingResult
    late = PricingResult(columns=(), sinks=(), problem_fingerprint="fp",
                         deadline_hit=True)
    assert not late.certifies_bound()
    assert "deadline" in late.truncation_names()


def test_pricing_result_and_truncations_survive_pickle():
    """Worker-протокол duty-прайсера проносит результат через пул
    процессов — тип обязан сериализоваться без потерь."""
    import pickle
    from _pricing_certificate import (PricingResult, Truncations,
                                      SCOPE_DUTY)
    t = Truncations(SCOPE_DUTY)
    t.record("max_legs", 1, 2)
    r = PricingResult(columns=((560, ("A", "B")),), sinks=(t,),
                      problem_fingerprint="fp", duals_fingerprint="dd")
    r2 = pickle.loads(pickle.dumps(r))
    assert r2.truncation_rows() == r.truncation_rows()
    assert not r2.certifies_bound()
    assert r2.columns == r.columns
