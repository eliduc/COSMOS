"""Тесты правил построения цепочек в формулировке системы-источника
(`_cos_rules.py`).

Каждое правило проверяется поштучно на синтетике: на реальном наборе
часть из них не срабатывает ни разу («тёмная» смена), и прогон на нём
доказывал бы только то, что код не падает. Синтетика показывает, что
правило действительно РАБОТАЕТ — то есть отсекает то, что должно.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

_cr = pytest.importorskip("_cos_rules")


def t(day: int, hh: int, mm: int = 0) -> datetime:
    """Момент во всемирном времени. В тестах база условно принята в
    поясе +05:00, поэтому местное время базы равно всемирному плюс
    пять часов."""
    return datetime(2026, 8, day, hh, mm)


def base_local(day: int, hh: int, mm: int = 0) -> datetime:
    """Момент, заданный МЕСТНЫМ временем базы (+05:00)."""
    return datetime(2026, 8, day, hh, mm) - timedelta(minutes=300)


# --- окно местного времени ----------------------------------------------

def test_window_overlap_handles_midnight_crossing():
    """Окно 22:00-06:00 переходит через полночь: наивная проверка
    «начало <= t < конец» дала бы ноль всегда."""
    r = _cr.CosRules()
    # 20:00-02:00 местного базы: шесть часов, из них в окне четыре
    ov = _cr.window_overlap(base_local(5, 20), base_local(6, 2),
                            r.night_start_min, r.night_end_min, 300)
    assert ov == 4 * 60, ov          # 22:00-02:00


def test_window_overlap_counts_whole_nights_across_days():
    r = _cr.CosRules()
    ov = _cr.window_overlap(base_local(5, 12), base_local(7, 12),
                            r.night_start_min, r.night_end_min, 300)
    assert ov == 2 * 8 * 60, ov      # две полные ночи по восемь часов


def test_window_is_read_in_the_named_timezone_not_utc():
    """Пояс — не украшение: то же окно во всемирном времени даёт другой
    ответ, и на реальном наборе это меняет классификацию заметной
    доли смен."""
    r = _cr.CosRules()
    t0, t1 = base_local(5, 23), base_local(6, 1)
    assert _cr.window_overlap(t0, t1, r.night_start_min, r.night_end_min,
                              300) == 120
    assert _cr.window_overlap(t0, t1, r.night_start_min, r.night_end_min,
                              0) == 0


# --- ночная смена --------------------------------------------------------
# Пояс базы у класса умолчания не имеет (правило приходит из данных),
# поэтому тесты окон задают его явно — те же +05:00, что документирует
# хелпер `base_local`.

def test_night_duty_is_half_of_work_time_in_the_window():
    r = _cr.CosRules(base_tz_offset_min=300)
    # 20:00-04:00 базы: восемь часов, из них шесть в окне -> ночная
    assert r.is_night_duty(base_local(5, 20), base_local(6, 4))
    # 18:00-23:00: пять часов, из них один в окне -> не ночная
    assert not r.is_night_duty(base_local(5, 18), base_local(5, 23))


def test_night_duty_boundary_is_exactly_the_ratio():
    """Ровно половина — уже ночная: порог включающий."""
    r = _cr.CosRules(base_tz_offset_min=300)
    # 20:00-00:00 базы: четыре часа, из них два в окне -> ровно 0.5
    assert r.is_night_duty(base_local(5, 20), base_local(6, 0))
    # 19:59-00:00: чуть больше четырёх часов, в окне те же два -> меньше
    assert not r.is_night_duty(base_local(5, 19, 59), base_local(6, 0))


def test_night_ratio_is_configurable_not_hardcoded():
    r = _cr.CosRules(night_ratio=0.9, base_tz_offset_min=300)
    assert not r.is_night_duty(base_local(5, 20), base_local(6, 4))


# --- «тёмная» смена ------------------------------------------------------

def test_dark_duty_definitions_are_ordered_by_strictness():
    """Определение «тёмной» смены источником не установлено, поэтому оно
    параметр. Три варианта обязаны быть вложены: строгий уже долевого,
    долевой уже касания."""
    inside = (base_local(5, 23, 30), base_local(6, 6, 30))
    half = (base_local(5, 22), base_local(6, 4))
    touch = (base_local(5, 18), base_local(5, 23, 30))
    for mode, expect in (("strict", (True, False, False)),
                         ("ratio", (True, True, False)),
                         ("touch", (True, True, True))):
        r = _cr.CosRules(dark_definition=mode, base_tz_offset_min=300)
        got = tuple(r.is_dark_duty(*w) for w in (inside, half, touch))
        assert got == expect, (mode, got)


def test_dark_sequence_allows_two_in_three_but_not_three():
    r = _cr.CosRules()
    assert r.dark_sequence_ok([True, True, False, True])
    assert not r.dark_sequence_ok([True, True, True])
    assert not r.dark_sequence_ok([False, True, True, True, False])


# --- ночная серия --------------------------------------------------------

def test_night_sequence_allows_one_but_not_two_in_a_row():
    r = _cr.CosRules()
    assert r.night_sequence_ok([True, False, True, False, True])
    assert not r.night_sequence_ok([True, True])


def test_next_duty_after_night_starts_no_earlier_than_six_local():
    r = _cr.CosRules(base_tz_offset_min=300)
    assert not r.next_duty_start_allowed(True, base_local(6, 5, 59))
    assert r.next_duty_start_allowed(True, base_local(6, 6, 0))
    # без предшествующей ночной смены ограничения нет
    assert r.next_duty_start_allowed(False, base_local(6, 3, 0))


def test_layover_after_night_sequence_differs_on_and_off_base():
    r = _cr.CosRules()
    assert r.layover_after_night_sequence(True) == 24 * 60
    assert r.layover_after_night_sequence(False) == 12 * 60


# --- предел рабочего времени по составу ----------------------------------

def test_tier_is_the_smallest_crew_that_admits_the_duty():
    # Пределы — явная синтетика фикстуры: на остатке умолчания класса
    # такой тест пройти не может (умолчаний больше нет).
    r = _cr.CosRules(work_time_limit_by_tier=(600, 700, 800))
    assert r.tier_for_work_time(500) == 0
    assert r.tier_for_work_time(600) == 0        # ровно предел — ещё можно
    assert r.tier_for_work_time(601) == 1
    assert r.tier_for_work_time(700) == 1
    assert r.tier_for_work_time(750) == 2
    assert r.tier_for_work_time(800) == 2
    assert r.tier_for_work_time(801) is None     # нельзя никаким составом


def test_work_time_limit_comes_from_the_rules_not_from_code():
    r = _cr.CosRules(work_time_limit_by_tier=(600, 700, 800))
    assert r.tier_for_work_time(650) == 1
    assert r.max_work_time() == 800


# --- таблица отдыха ------------------------------------------------------

def _tiers() -> tuple:
    """Синтетическая таблица отдыха фикстуры.

    Структура — как у настоящей: три яруса по шесть случаев; у второго
    яруса ВНЕ базы дальний столбец МЕНЬШЕ обычного — краевой случай для
    правила «берётся наибольшее». Числа вымышлены и заданы явно:
    умолчаний у класса больше нет."""
    return (
        _cr.LayoverTier(600, 700, 750, 1400, 750, 1400, 1400),
        _cr.LayoverTier(800, 2000, 1450, 2000, 1450, 2800, 1300),
        _cr.LayoverTier(900, 2000, 1450, 2000, 1450, 2800, 1300),
    )


def test_layover_tier_is_chosen_by_the_previous_duty_length():
    r = _cr.CosRules(layover_tiers=_tiers())
    assert r.required_layover(500, on_base=True, night=False, trans=False) == 700
    assert r.required_layover(700, on_base=True, night=False, trans=False) == 2000


def test_layover_night_and_trans_take_the_larger_requirement():
    """Смена бывает и ночной, и дальней. Взять меньшее из двух требований
    значило бы подменить правило послаблением."""
    r = _cr.CosRules(layover_tiers=_tiers())
    assert r.required_layover(500, True, night=True, trans=False) == 1400
    assert r.required_layover(500, True, night=False, trans=True) == 1400
    assert r.required_layover(500, False, night=True, trans=False) == 750
    assert r.required_layover(500, False, night=False, trans=True) == 1400
    assert r.required_layover(500, False, night=True, trans=True) == 1400
    # у второго яруса вне базы дальний столбец МЕНЬШЕ обычного —
    # правило обязано оставить больший
    assert r.required_layover(700, False, night=False, trans=True) == 1450


def test_layover_above_the_last_tier_uses_the_last_tier():
    r = _cr.CosRules(layover_tiers=_tiers())
    assert r.required_layover(5000, True, False, False) == 2000


# --- дальний участок и равнозначные станции ------------------------------

def test_trans_layover_is_measured_from_the_base_timezone():
    """«Дальняя» стоянка — про расхождение часов со своей базой, а не про
    длину рейса и не про абсолютное значение пояса станции."""
    r = _cr.CosRules(base_tz_offset_min=300, trans_tz_hours=3.0)
    assert not r.is_trans_layover(300)          # своя база
    assert not r.is_trans_layover(419)          # 1 ч 59 мин — не дотягивает
    assert r.is_trans_layover(120)              # на три часа назад
    assert r.is_trans_layover(480)              # на три часа вперёд


def test_station_without_a_timezone_is_not_far():
    """Отсутствие пояса в данных не должно молча ужесточать правило."""
    assert not _cr.CosRules().is_trans_layover(None)


def test_trans_threshold_is_a_setting_not_a_constant():
    r = _cr.CosRules(base_tz_offset_min=0, trans_tz_hours=5.0)
    assert not r.is_trans_layover(240)
    assert r.is_trans_layover(300)


def test_equivalent_stations_work_both_ways():
    """Пары хранятся в обе стороны, включая помеченные несимметричными.
    Таблица — явная синтетика фикстуры."""
    r = _cr.CosRules(teleportation={("AAA", "BBB"): 900, ("BBB", "AAA"): 900,
                                    ("CCC", "DDD"): 900, ("DDD", "CCC"): 900})
    assert r.equivalent_station_gap("AAA", "BBB") == 900
    assert r.equivalent_station_gap("BBB", "AAA") == 900
    assert r.equivalent_station_gap("DDD", "CCC") == 900
    assert r.equivalent_station_gap("BSE", "LED") is None
    assert r.equivalent_station_gap("BSE", "BSE") == 0


def test_bare_rules_declare_no_equivalent_stations():
    """У голого объекта равнозначных пар НЕТ: незаявленная таблица —
    сон механизма (None), а не зашитые пары какой-либо авиакомпании."""
    r = _cr.CosRules()
    assert r.teleportation is None
    assert r.equivalent_station_gap("AAA", "BBB") is None
    # совпадение станций — ноль и без таблицы: это не равнозначность
    assert r.equivalent_station_gap("AAA", "AAA") == 0


def test_pairing_days_counted_in_base_local_calendar():
    r = _cr.CosRules(base_tz_offset_min=300)
    # 23:00 первого по базе -> 01:00 второго по базе: двое суток
    assert r.pairing_days(base_local(1, 23), base_local(2, 1)) == 2
    assert r.pairing_days(base_local(1, 6), base_local(1, 22)) == 1


# --- отказ на голом объекте ----------------------------------------------
# Умолчаний заказчика у класса больше нет: обращение потребителя к
# незаполненному правилу обязано быть отказом словами, а не расчётом по
# чужому числу. Отказ — при ОБРАЩЕНИИ, не при конструировании: голый
# объект законен (снимок, инкрементальное заполнение загрузчиками).

@pytest.mark.parametrize("call, field", [
    (lambda r: r.tier_for_work_time(600), "work_time_limit_by_tier"),
    (lambda r: r.max_work_time(), "work_time_limit_by_tier"),
    (lambda r: r.required_layover(500, True, False, False),
     "layover_tiers"),
    (lambda r: r.min_off_base_rest(), "layover_tiers"),
    (lambda r: r.is_night_duty(base_local(5, 20), base_local(6, 4)),
     "base_tz_offset_min"),
    (lambda r: r.is_dark_duty(base_local(5, 23), base_local(6, 6)),
     "base_tz_offset_min"),
    (lambda r: r.is_trans_layover(0), "base_tz_offset_min"),
    (lambda r: r.pairing_days(base_local(1, 6), base_local(1, 22)),
     "base_tz_offset_min"),
    (lambda r: r.next_duty_start_allowed(True, base_local(6, 6)),
     "base_tz_offset_min"),
])
def test_bare_rules_refuse_each_consumer(call, field):
    with pytest.raises(_cr.SourceRulesError, match=field):
        call(_cr.CosRules())


def test_bare_rules_refusal_names_the_missing_data():
    with pytest.raises(_cr.SourceRulesError, match="не задано данными"):
        _cr.CosRules().max_work_time()


def test_trans_layover_refuses_on_missing_threshold():
    """Пояс базы задан, порога дальности нет — отказ по порогу."""
    r = _cr.CosRules(base_tz_offset_min=300)
    with pytest.raises(_cr.SourceRulesError, match="trans_tz_hours"):
        r.is_trans_layover(0)


def test_duty_span_matrix_needs_base_tz_only_when_present():
    """Пустая матрица — «правила нет», пояс не нужен; непустая требует
    пояс базы для окна местного времени."""
    assert _cr.CosRules().duty_span_limit(t(1, 6), 1) is None
    r = _cr.CosRules(duty_span_matrix=((360, 840, 1, 2, 600),))
    with pytest.raises(_cr.SourceRulesError, match="base_tz_offset_min"):
        r.duty_span_limit(t(1, 6), 1)


def test_empty_layover_table_falls_back_to_declared_sit_only():
    """Порог ночёвки при ПУСТОЙ (заявленной пустой) таблице — явная
    минимальная стыковка; не заданная — отказ, а не чужое число."""
    assert _cr.CosRules(layover_tiers=(),
                        min_sit_min=41).min_off_base_rest() == 41
    with pytest.raises(_cr.SourceRulesError, match="min_sit_min"):
        _cr.CosRules(layover_tiers=()).min_off_base_rest()


# --- чтение правил из настроек источника ---------------------------------

def _params() -> dict:
    """Настройки, ЗАВЕДОМО ОТЛИЧНЫЕ от умолчаний класса.

    Прежде фикстура повторяла умолчания, и тест «правила читаются из
    настроек» проходил при полностью удалённом чтении настроек: обе
    стороны сравнения давали одно и то же по разным причинам. Значения
    ниже не совпадают с умолчаниями ни одним полем.
    """
    return {
        "nightStart": "21:00:00", "nightEnd": "05:00:00",
        "nightDutyRules": {"@type": "ratio", "thresholdRatio": 0.6},
        "darkDutyRules": {"@type": "startEnd", "darkStart": "22:30:00",
                          "darkEnd": "06:30:00", "darkDutyCheckWindow": 4,
                          "allowedDarkDutySequenceLength": 3},
        "transLegThreshold": "PT8H",
        "layoverRules": {"thresholds": [
            {"thresholdDuration": "PT10H", "onBaseMinimum": "PT11H",
             "offBaseMinimum": "PT11H30M", "onBaseNightMinimum": "PT20H",
             "offBaseNightMinimum": "PT13H",
             "onBaseTransMinimum": "PT22H", "offBaseTransMinimum": "PT21H"},
            {"thresholdDuration": "PT13H", "onBaseMinimum": "PT30H",
             "offBaseMinimum": "PT20H", "onBaseNightMinimum": "PT32H",
             "offBaseNightMinimum": "PT21H",
             "onBaseTransMinimum": "PT40H", "offBaseTransMinimum": "PT22H"},
            {"thresholdDuration": "PT15H", "onBaseMinimum": "PT34H",
             "offBaseMinimum": "PT23H", "onBaseNightMinimum": "PT35H",
             "offBaseNightMinimum": "PT23H30M",
             "onBaseTransMinimum": "PT44H", "offBaseTransMinimum": "PT23H"}]},
        "nightDutySequenceRules": {
            "allowedNightDutySequenceLength": 2,
            "localTimeNextDutyStart": "07:00:00",
            "minLayoverAfterNightDutySequenceOnBase": "PT26H",
            "minLayoverAfterNightDutySequenceOffBase": "PT14H"},
        "maximumPairingDays": 4,
        "allowedSplitDutySequenceLength": 1,
        "extendedDutyUpperLimit": 2,
        "transferDeadheadRules": {"@type": "allowTransfers"},
        "stationEquivalenceRules": {"teleportationCodesList": [
            {"departureStation": "AAA", "arrivalStation": "BBB",
             "minimumDuration": "PT9H", "symmetric": True},
            {"departureStation": "CCC", "arrivalStation": "DDD",
             "minimumDuration": "PT9H", "symmetric": False}]},
    }


def test_rules_are_read_from_the_source_settings():
    """Каждое значение фикстуры отличается от умолчания, поэтому тест
    падает, если хоть один ключ не прочитан."""
    r = _cr.from_parameters(_params())
    assert r.night_start_min == 21 * 60 and r.night_end_min == 5 * 60
    assert r.night_ratio == 0.6
    assert r.dark_start_min == 22 * 60 + 30 and r.dark_end_min == 6 * 60 + 30
    assert r.dark_check_window == 4 and r.dark_seq_max == 3
    assert r.trans_leg_min == 8 * 60
    assert r.work_time_limit_by_tier == (600, 780, 900)
    assert r.required_layover(500, True, False, False) == 660
    assert r.required_layover(500, True, True, False) == 1200
    assert r.required_layover(500, False, False, True) == 1260
    assert r.night_seq_max == 2
    assert r.night_next_duty_start_min == 7 * 60
    assert r.night_seq_layover_on_base_min == 26 * 60
    assert r.night_seq_layover_off_base_min == 14 * 60
    assert r.max_pairing_days == 4
    assert r.split_duty_allowed is True
    assert r.extended_duty_allowed is True
    assert r.transfer_deadheads is True
    # ни один ключ не остался умолчанием
    assert r.source_keys_missing == [], r.source_keys_missing


def test_teleportation_replaces_defaults_and_works_both_ways():
    """Прочитанная таблица ЗАМЕНЯЕТ умолчания, а не дополняет их: иначе
    зашитые пары разрешили бы экипажу закончить смену в одном аэропорту и
    начать следующую в другом у авиакомпании, где это не объявлено."""
    r = _cr.from_parameters(_params())
    assert r.equivalent_station_gap("AAA", "BBB") == 540
    assert r.equivalent_station_gap("BBB", "AAA") == 540
    # несимметричная пара всё равно хранится в обе стороны
    assert r.equivalent_station_gap("DDD", "CCC") == 540
    # пары из умолчаний исчезли
    assert r.equivalent_station_gap("LHR", "LGW") is None


def test_empty_equivalence_list_leaves_no_pairs():
    r = _cr.from_parameters(dict(_params(),
                                 stationEquivalenceRules={
                                     "teleportationCodesList": []}))
    assert r.teleportation == {}
    assert r.equivalent_station_gap("LHR", "LGW") is None


def test_unparseable_duration_is_refused_not_silently_zeroed():
    """Длительность вида P1D — обычная запись суток по ISO-8601. Прежде
    она давала ноль, то есть требование «отдых ноль минут», и отличить
    его от настоящего нуля было нельзя."""
    bad = _params()
    bad["layoverRules"]["thresholds"][0]["onBaseMinimum"] = "P1D"
    with pytest.raises(_cr.SourceRulesError) as exc:
        _cr.from_parameters(bad)
    assert "onBaseMinimum" in str(exc.value)


def test_ratio_outside_the_unit_range_is_refused():
    """Порог, заданный процентами, обнулил бы число ночных смен, и это
    выглядело бы свойством данных."""
    with pytest.raises(_cr.SourceRulesError):
        _cr.from_parameters(dict(_params(),
                                 nightDutyRules={"thresholdRatio": 50}))


def test_unparseable_clock_is_refused():
    with pytest.raises(_cr.SourceRulesError):
        _cr.from_parameters(dict(_params(), nightStart="1260"))


def test_missing_keys_are_named_not_hidden_behind_defaults():
    """Чего в настройках нет, остаётся умолчанием — и обязано быть
    названо, иначе умолчание неотличимо от данных заказчика."""
    r = _cr.from_parameters({})
    snap = r.as_dict()
    assert snap["night_start_min"] == 22 * 60
    assert "nightStart" in snap["source_keys_missing"]
    assert "layoverRules.thresholds" in snap["source_keys_missing"]
    assert snap["source_keys_read"] == []


def test_snapshot_separates_different_rule_sets():
    a = _cr.from_parameters(_params()).as_dict()
    b = _cr.from_parameters(dict(_params(), maximumPairingDays=5)).as_dict()
    assert a != b
