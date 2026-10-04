# -*- coding: utf-8 -*-
"""Сравнение способов решения (директива владельца 2026-08-24).

Правило: сквозной побеждает, только если его ростеринг лучше
классического И его пейринг приемлем (порог — настройка, умолчание 5%).
"""

import pytest

from webapp import way_compare as wc


def test_сквозной_побеждает_когда_лучше_и_пейринг_в_пороге():
    v = wc.compare("0hard/-100soft", "0hard/-80soft", 1000.0, 1040.0)
    assert v["winner"] == wc.WAY_THROUGH
    assert v["pairing_acceptable"] is True
    assert v["rostering_through_better"] is True


def test_пейринг_хуже_порога_отдаёт_победу_классике():
    v = wc.compare("0hard/-100soft", "0hard/-80soft", 1000.0, 1051.0)
    assert v["winner"] == wc.WAY_CLASSICAL
    assert v["pairing_acceptable"] is False


def test_ростеринг_не_лучше_отдаёт_победу_классике():
    v = wc.compare("0hard/-80soft", "0hard/-100soft", 1000.0, 1000.0)
    assert v["winner"] == wc.WAY_CLASSICAL
    assert v["rostering_through_better"] is False


def test_порог_настройка_а_не_константа():
    # При пороге 10% та же пара стоимостей становится приемлемой.
    v = wc.compare("0hard/-100soft", "0hard/-80soft", 1000.0, 1051.0,
                   margin_pct=10.0)
    assert v["winner"] == wc.WAY_THROUGH
    assert v["margin_pct"] == 10.0


def test_жёсткие_решают_раньше_мягких():
    # Сквозной допустим, классика нет — сквозной побеждает при приемлемом
    # пейринге даже с худшими мягкими.
    v = wc.compare("-5hard/-10soft", "0hard/-999soft", 1000.0, 1000.0)
    assert v["winner"] == wc.WAY_THROUGH
    # А при неприемлемом пейринге победителя нет — вопрос человеку.
    v2 = wc.compare("-5hard/-10soft", "0hard/-999soft", 1000.0, 2000.0)
    assert v2["winner"] == wc.WAY_NONE


def test_оба_недопустимы_победителя_нет():
    v = wc.compare("-5hard/-10soft", "-3hard/-1soft", 1.0, 1.0)
    assert v["winner"] == wc.WAY_NONE


def test_нулевая_классическая_стоимость_требует_ровно_нуля():
    assert wc.compare("0hard/-1soft", "0hard/0soft", 0.0, 0.0)["winner"] == wc.WAY_THROUGH
    v = wc.compare("0hard/-1soft", "0hard/0soft", 0.0, 0.1)
    assert v["winner"] == wc.WAY_CLASSICAL
    assert v["pairing_acceptable"] is False


def test_вердикт_несёт_числа_и_причины():
    v = wc.compare("0hard/-100soft", "0hard/-80soft", 1000.0, 1040.0)
    assert v["inputs"]["classical_pairing_cost"] == 1000.0
    assert any("порог" in r for r in v["reasons"])


def test_мусорные_входы_отказ_а_не_догадка():
    with pytest.raises(wc.WayCompareError):
        wc.compare("мусор", "0hard/0soft", 1.0, 1.0)
    with pytest.raises(wc.WayCompareError):
        wc.compare("0hard/0soft", "0hard/0soft", -1.0, 1.0)
    with pytest.raises(wc.WayCompareError):
        wc.compare("0hard/0soft", "0hard/0soft", 1.0, 1.0, margin_pct=-5)


def test_стоимость_из_счёта():
    assert wc.cost_from_score("0hard/-2054soft") == 2054.0
    assert wc.cost_from_score("-3hard/0soft") == 0.0


def test_zone_cost_считает_по_карте_пакета():
    """Ревизия 1.14: словарь исполнителей зоны приходит подсекцией
    rule_maps.zone_executors манифеста и подаётся параметром executors
    — форма чисел та же, что у согласованного словаря (вклад / литерал
    × вес компоненты). Карта пакета сильнее зашитой таблицы: строка
    легаси-словаря, которой в карте нет, — честное «исполнителя нет»,
    а не тихое чтение зашитой координаты."""
    from webapp import rule_maps as rm
    manifest = {"rule_maps": {"zone_executors": {
        "R-СИНТ-зона": {"constraint": "Synthetic nights", "literal": 40}}}}
    execs = rm.zone_executors(manifest)
    by_name = {"Synthetic nights": {"soft": -80},
               "Layover nights away from home": {"soft": -120}}
    comps = [
        {"rule_id": "R-СИНТ-зона", "title": "синтетика", "weight": 10},
        # Живая запись легаси-словаря ZONE_EXECUTORS — в карте пакета
        # её нет, и считать её по зашитой таблице нельзя.
        {"rule_id": "R-S1-табл-3-строка-6-кол-3",
         "title": "легаси-строка вне карты", "weight": 5},
    ]
    z = wc.zone_cost(by_name, comps, executors=execs)
    assert z["cost"] == 20.0            # 80/40 = 2 единицы × вес 10
    assert z["counted"][0]["units"] == 2
    assert [u["rule_id"] for u in z["uncounted"]] \
        == ["R-S1-табл-3-строка-6-кол-3"]
    # Контроль легаси-ветки: без карты (манифест без секции) читатель
    # даёт None, и умолчание zone_cost — прежний согласованный словарь.
    assert rm.zone_executors({}) is None
    z2 = wc.zone_cost(by_name, comps[1:], executors=None)
    assert z2["cost"] == 15.0           # 120/40 = 3 единицы × вес 5
    assert not z2["uncounted"]
