# -*- coding: utf-8 -*-
"""Период обязанностей — ОДНА реализация на все стороны.

Периодом обязанностей называется непрерывная работа экипажа: рабочие
секторы вместе с перегонами (перелётами пассажиром), не отделёнными от
них нормативным отдыхом. Границы периода определяют сразу три вещи:
предел служебного полётного времени, отсчёт отдыха и вид смены в
передаваемом решении.

Понятие жило тремя отдельными реализациями — в построителе цепочек, в
выгрузке формата Meridian и в аудите — и они расходились: одна и та же
цепочка получала разные границы, а вместе с ними разные пределы и
разный отдых. Расхождение проявлялось не ошибкой, а тихим несогласием
результата с проверкой (аудит передаваемого файла 2026-08-25). Поэтому
реализация здесь одна, а стороны только подают ей свои данные.

Модуль намеренно не знает ни про правила свода, ни про структуру
каталога: он получает активности и функцию пола отдыха, возвращает
периоды. Все числа приходят снаружи.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class Activity:
    """Активность экипажа: рабочий сектор либо перелёт пассажиром."""
    key: str
    t0: datetime
    t1: datetime
    dep: str
    arr: str
    working: bool
    payload: object = None


@dataclass
class Period:
    """Период обязанностей: границы с подготовкой и завершением."""
    start: datetime
    end: datetime
    activities: list = field(default_factory=list)

    @property
    def working(self):
        return [a for a in self.activities if a.working]

    @property
    def sectors(self) -> int:
        """Рабочие секторы: перегоны в счёт не входят (правило 0047)."""
        return len(self.working)

    @property
    def dep_station(self) -> str:
        return self.activities[0].dep

    @property
    def end_station(self) -> str:
        return self.activities[-1].arr

    @property
    def span_min(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    def fdp_end(self, debrief_min: float) -> datetime:
        """Конец служебного полётного времени: последний РАБОЧИЙ сектор
        плюс завершающие работы. Хвостовой перегон в служебное полётное
        время не входит, но период продолжает (правила 0047/0051)."""
        w = self.working
        if not w:
            return self.start
        return w[-1].t1 + timedelta(minutes=debrief_min)

    def work_span_min(self, brief_min: float, debrief_min: float) -> float:
        """Длительность рабочей части: от подготовки перед первым рабочим
        сектором до завершающих работ после последнего."""
        w = self.working
        if not w:
            return 0.0
        start = w[0].t0 - timedelta(minutes=brief_min)
        return (w[-1].t1 + timedelta(minutes=debrief_min)
                - start).total_seconds() / 60


# RULE: 0019 0020 0021 0037 -- сборка периодов обязанностей с брифингом и дебрифингом
def build_periods(activities, rest_floor, brief_min, debrief_min):
    """Активности -> периоды обязанностей.

    `rest_floor(prev_period, station)` — минуты отдыха, после которых
    период прерывается. Функция приходит снаружи: пол зависит от правил
    заказчика (станция, пояса, ярус состава), а не от этого модуля.

    Разрыв меньше пола означает, что экипаж всё это время в
    обязанностях: перерыв на стоянке периодом не прерывается.
    """
    acts = sorted(activities, key=lambda a: (a.t0, a.t1))
    if not acts:
        return []
    periods, cur = [], [acts[0]]
    for prev, nxt in zip(acts, acts[1:]):
        gap = (nxt.t0 - prev.t1).total_seconds() / 60
        probe = _period_of(cur, brief_min, debrief_min)
        if gap >= rest_floor(probe, prev.arr):
            periods.append(probe)
            cur = [nxt]
        else:
            cur.append(nxt)
    periods.append(_period_of(cur, brief_min, debrief_min))
    return periods


def _period_of(acts, brief_min, debrief_min) -> Period:
    return Period(start=acts[0].t0 - timedelta(minutes=brief_min),
                  end=acts[-1].t1 + timedelta(minutes=debrief_min),
                  activities=list(acts))
