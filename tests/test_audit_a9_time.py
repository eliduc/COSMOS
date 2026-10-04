# -*- coding: utf-8 -*-
"""Внешний аудит 04.09.2026, A9 — система времени построителя.

Под одним ключом base_tz_offset_min жили два смысла: сдвиг шкалы меток
(пакет и решатель) и смещение пояса к UTC-меткам (построитель); пояс
листа «Аэропorty» молча перекрывал объявленный пакетом сдвиг, а пояса
станций замораживались «на первое касание» — перевод часов внутри
горизонта не учитывался.

Стенды с известным ответом:
  1. смещение станции считается на момент: Europe/Berlin — +60 в марте,
     +120 в апреле (перевод часов 29.03.2026); первое касание не правит;
  2. разница поясов станции и базы берётся по листу и не зависит от
     объявленного сдвига шкалы (H0: метки местные, сдвиг 0, лист
     Europe/Moscow): Екатеринбург относительно Москвы — +120;
  3. разрешение сдвига шкалы: явный ключ > контракт > лист (метки
     считаются UTC) > отказ словами; лист не перекрывает контракт.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

_pb = pytest.importorskip("_pairing_builder")
from tests.test_pairing_builder import _args, _leg, _payload   # noqa: E402


def _world(airports: list[dict], legs: list[dict]) -> dict:
    p = _payload(legs)
    p["airports"] = airports
    return p


def test_station_offset_follows_daylight_saving_within_horizon():
    p = _world([{"code": "BASE", "timeZone": "Europe/Berlin"},
                {"code": "BER", "timeZone": "Europe/Berlin"}],
               [_leg("F1", "BASE", "BER", "2026-03-20T10:00:00", 60),
                _leg("F2", "BER", "BASE", "2026-04-10T10:00:00", 60)])
    b = _pb.Builder(p, _args())
    assert b.tz_off_of["BER"] == 60                      # первое касание
    assert b.station_off("BER", datetime(2026, 3, 20, 12)) == 60
    assert b.station_off("BER", datetime(2026, 4, 10, 12)) == 120
    assert b.station_off("BER") == 60                    # без момента — как прежде
    assert b.station_off("XXX", datetime(2026, 4, 10, 12)) is None


def test_station_minus_base_difference_is_scale_independent():
    p = _world([{"code": "BASE", "timeZone": "Europe/Moscow"},
                {"code": "SVX", "timeZone": "Asia/Yekaterinburg"}],
               [_leg("F1", "BASE", "SVX", "2026-08-01T06:00:00", 120),
                _leg("F2", "SVX", "BASE", "2026-08-01T12:00:00", 120)])
    b = _pb.Builder(p, _args())
    when = datetime(2026, 8, 1, 6)
    # метки местные, сдвиг шкалы контракта 0 — как у H0
    b.rules = SimpleNamespace(base_tz_offset_min=0)
    assert b.base_zone_off(when) == 180                  # пояс базы по листу
    assert b.station_off("SVX", when) - b.base_zone_off(when) == 120
    # листа нет — пояс базы = сдвиг шкалы контракта (прежнее поведение)
    b2 = _pb.Builder(_world([], [
        _leg("F1", "BASE", "SVX", "2026-08-01T06:00:00", 120),
        _leg("F2", "SVX", "BASE", "2026-08-01T12:00:00", 120)]), _args())
    b2.rules = SimpleNamespace(base_tz_offset_min=300)
    assert b2.base_zone_off(when) == 300
    assert b2.station_off("SVX", when) is None


def test_resolve_time_scale_contract_beats_sheet_and_refuses_when_empty():
    r = _pb.resolve_time_scale
    # явный ключ
    assert r(0, 180, explicit=60)[0] == 60
    # контракт объявил 0 (метки местные), лист говорит +180: действует 0
    tz, note, refuse = r(0, 180)
    assert tz == 0 and refuse is None
    assert "+180" in note and "не является сдвигом шкалы" in note
    # контракт молчит: лист даёт сдвиг, метки считаются UTC
    tz, note, refuse = r(None, 180)
    assert tz == 180 and refuse is None and "UTC" in note
    # контракт молчит, листа нет, файл правил несёт необъявленное число —
    # прежнее поведение с предупреждением
    tz, note, refuse = r(None, None, fallback=300)
    assert tz == 300 and refuse is None and "ВНИМАНИЕ" in note
    # ни контракта, ни листа, ни файла — отказ словами
    tz, note, refuse = r(None, None)
    assert tz is None and refuse and "base-tz-offset-min" in refuse


def test_translator_marks_declared_scale_and_rules_snapshot_keeps_it():
    """Транслятор пакета помечает объявленный сдвиг; снимок правил
    печатает и читает признак без потерь (замкнутость «печатаем —
    читаем»)."""
    import _cos_rules as _cr
    from vmp import pairing_input as pin
    out = pin.source_rules_contract({}, {"base_tz_offset_min": 0})
    assert out["base_tz_offset_min"] == 0
    assert out["base_tz_offset_declared"] is True
    # Спецификация 1.16: manifest.time старше параметра applied
    out2 = pin.source_rules_contract({}, {"base_tz_offset_min": 0},
                                     manifest_time={"time_scale": "shifted",
                                                    "base_tz_offset_min": 180})
    assert out2["base_tz_offset_min"] == 180
    assert out2["base_tz_offset_declared"] is True
    r = _cr.from_dict({"base_tz_offset_min": 0,
                       "base_tz_offset_declared": True})
    assert r.base_tz_offset_declared is True
    assert _cr.from_dict(r.as_dict()).base_tz_offset_declared is True
    assert _cr.CosRules().base_tz_offset_declared is False


def test_h0_like_sheet_does_not_override_declared_zero_scale():
    """H0: метки в местном времени базы (сдвиг 0), лист «Аэропорты» несёт
    Europe/Moscow. Прежде построитель с контрактом брал +180 и судил
    ночные окна на три часа позже настоящих."""
    p = _world([{"code": "BASE", "timeZone": "Europe/Moscow"}],
               [_leg("F1", "BASE", "AAA", "2026-09-04T06:00:00", 120),
                _leg("F2", "AAA", "BASE", "2026-09-04T10:00:00", 120)])
    assert _pb._base_tz_offset(p, "BASE") == 180
    tz, note, refuse = _pb.resolve_time_scale(0, _pb._base_tz_offset(p, "BASE"))
    assert tz == 0 and refuse is None
