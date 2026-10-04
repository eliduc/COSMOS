# -*- coding: utf-8 -*-
"""Один критерий нарезки смен для ВСЕХ проверок по ИДАК с единицей
«смена» (находка GPT-059 / P1 P3-DUTY-BOUNDARY-WIRE, 06.09.2026):
проверка длительности смены (и стыковки внутри смены, посадок за смену,
мягкого счёта смен сверх порога) режет линию времени тем же критерием
прогона, что и отдых между сменами (vmp.sit_boundary): ручка
max_sit_duration → порог T; без ручки → пол минимального отдыха ПО
МЕСТУ (модельное упрощение); ни того ни другого → not_checkable с
причиной. Число читателя rest_split_min — только показание.

Все ожидания — через НАСТОЯЩИЙ source_check.run_checks (не через
зеркало), на сценарии ревьюера: один сотрудник, участки F1..F4, разрывы
120 вне базы / 650 дома / 130 вне базы, полы 720 дома / 600 вне базы,
предел смены 600."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))

from vmp import sit_boundary as sb  # noqa: E402
from vmp import source_check as sc  # noqa: E402

HOME = "BASE"
R_HOME, R_OUT = 720, 600
BY_PLACE = {"home": R_HOME, "outstation": R_OUT}
LIMIT = 600


def _leg(num, dep, arr, start, minutes):
    t0 = datetime.fromisoformat(start)
    return {"flightNumber": num, "departureAirport": dep,
            "arrivalAirport": arr, "departureUTCDateTime": t0.isoformat(),
            "arrivalUTCDateTime": (t0 + timedelta(minutes=minutes)).isoformat()}


def _roster(home_gap: int = 650) -> dict:
    """F1 BASE→AAA 06:00–08:00; +120 вне базы; F2 AAA→BASE 10:00–12:00;
    +home_gap дома; F3 BASE→AAA; +130 вне базы; F4 AAA→BASE."""
    t3 = datetime.fromisoformat("2026-08-01T12:00") + timedelta(minutes=home_gap)
    t4 = t3 + timedelta(minutes=120 + 130)
    legs = [
        _leg("F1", HOME, "AAA", "2026-08-01T06:00", 120),
        _leg("F2", "AAA", HOME, "2026-08-01T10:00", 120),
        _leg("F3", HOME, "AAA", t3.isoformat(), 120),
        _leg("F4", "AAA", HOME, t4.isoformat(), 120),
    ]
    return {"employees": [{"id": "E1", "homeAirport": HOME}],
            "flights": legs,
            "flightAssignments": [{"flight": f["flightNumber"],
                                   "employee": "E1"} for f in legs]}


def _spec(kind, params, cid=None):
    return {"id": cid or kind, "kind": kind, "anchor": "п.1",
            "params": params}


def _specs(reader_split=None) -> list[dict]:
    extra = {} if reader_split is None else {"rest_split_min": reader_split}
    return [
        _spec("duty_span_max", dict(limit_min=LIMIT, **extra)),
        _spec("rest_min", dict(home_min=R_HOME, outstation_min=R_OUT, **extra)),
        _spec("turnaround_min", dict(min_min=45, **extra)),
        _spec("landings_per_duty_max", dict(limit=3, **extra)),
        _spec("soft_event_count", dict(event="duty_over_threshold",
                                       threshold_min=LIMIT, **extra)),
        _spec("crew_size_exact", {"n": 1}),
    ]


def _rest_floor() -> dict:
    return {"rest_boundary_min": min(BY_PLACE.values()),
            "rest_boundary_source": "стенд: выведено из пола отдыха по месту",
            "rest_boundary_basis": "rest_floor",
            "rest_boundary_by_place": dict(BY_PLACE),
            "rest_boundary_assumption": sb.ASSUMPTION}


def _knob(t: int) -> dict:
    return {"rest_boundary_min": t,
            "rest_boundary_source": f"стенд: ручка max_sit_duration, T={t}",
            "rest_boundary_basis": "knob",
            "rest_boundary_by_place": dict(BY_PLACE),
            "rest_boundary_assumption": None}


def _by_kind(results) -> dict:
    return {r.check_id: r for r in results}


# ------------------------------------------- отрицательный пример GPT-059

@pytest.mark.parametrize("reader_split", [None, 600])
def test_rest_floor_разрыв_650_дома_одна_смена_1380_нарушение(reader_split):
    """Basis rest_floor (720/600): разрыв 650 дома короче пола дома 720 —
    стыковка, вся линия — ОДНА смена 1380 мин > предела 600 (превышение
    780). Прежде duty_span_max резал числом читателя (480 либо явное 600)
    и давал ложное «ok, 2 смены» — теперь violation через настоящий
    run_checks; rest_min по-прежнему ok с 0 интервалов; число читателя
    упоминается только как «не применяется»."""
    got = _by_kind(sc.run_checks(_specs(reader_split), _roster(650),
                                 rest_boundary=_rest_floor()))
    span = got["duty_span_max"]
    assert span.verdict == "violated", span.detail
    assert span.objects_seen == 1 and len(span.violations) == 1
    assert "23:00" in span.violations[0] and "превышение 13:00" in span.violations[0]
    assert "basis rest_floor" in span.detail and "дома 720" in span.detail \
        and "вне базы 600" in span.detail
    if reader_split is None:
        assert "rest_split_min" not in span.detail
    else:
        assert f"rest_split_min={reader_split} не применяется" in span.detail
    assert "480" not in span.detail
    rest = got["rest_min"]
    assert rest.verdict == "ok" and rest.objects_seen == 0, rest.detail
    # те же смены у остальных проверок с единицей «смена»
    turn = got["turnaround_min"]
    assert turn.verdict == "ok" and turn.objects_seen == 3, turn.detail
    land = got["landings_per_duty_max"]
    assert land.verdict == "violated" and land.objects_seen == 1, land.detail
    assert "посадок за смену 4 > 3" in land.violations[0]
    soft = got["soft_event_count"]
    assert soft.verdict == "soft_info" and soft.objects_seen == 1, soft.detail
    assert "basis rest_floor" in soft.detail
    assert got["crew_size_exact"].verdict == "ok"


# ------------------------------------------------ ручка: basis knob, T=600

def test_knob_T600_разрыв_650_дома_две_смены_и_нарушение_отдыха():
    """С ручкой (basis knob, T=600): разрыв 650 ≥ T — граница смены → две
    смены (360 и 370 мин, duty_span_max ok), а отдых 650 дома < пола 720
    — нарушение rest_min. Оба вердикта через настоящий run_checks."""
    got = _by_kind(sc.run_checks(_specs(480), _roster(650),
                                 rest_boundary=_knob(600)))
    span = got["duty_span_max"]
    assert span.verdict == "ok" and span.objects_seen == 2, span.detail
    assert "T=600 мин (basis knob)" in span.detail
    assert "rest_split_min=480 не применяется" in span.detail
    rest = got["rest_min"]
    assert rest.verdict == "violated" and rest.objects_seen == 1, rest.detail
    assert HOME in rest.violations[0] and "10:50" in rest.violations[0]
    assert got["turnaround_min"].objects_seen == 2
    assert got["landings_per_duty_max"].verdict == "ok" \
        and got["landings_per_duty_max"].objects_seen == 2
    assert got["soft_event_count"].objects_seen == 0


# ---------------------------------------- rest_floor: разрыв 720 дома — отдых

def test_rest_floor_разрыв_720_дома_две_смены_ok_по_обеим():
    """Разрыв 720 дома при rest_floor — не короче пола дома: граница
    смены → две смены (duty_span_max ok) и один полный отдых (rest_min
    ok)."""
    got = _by_kind(sc.run_checks(_specs(), _roster(720),
                                 rest_boundary=_rest_floor()))
    span = got["duty_span_max"]
    assert span.verdict == "ok" and span.objects_seen == 2, span.detail
    rest = got["rest_min"]
    assert rest.verdict == "ok" and rest.objects_seen == 1, rest.detail
    assert got["landings_per_duty_max"].verdict == "ok"
    # одна нарезка у обеих: смен = интервалов отдыха + 1
    assert span.objects_seen == rest.objects_seen + 1


# --------------------------------------------- граница не определена

@pytest.mark.parametrize("rb", [
    None,
    {"rest_boundary_min": None,
     "rest_boundary_source": "показаний решателя нет: HTTP 503"},
    {"rest_boundary_min": 0,
     "rest_boundary_source": f"{sb.UNDEFINED}: day_break_threshold_min=0",
     "rest_boundary_basis": None, "rest_boundary_by_place": None},
])
def test_граница_не_определена_проверки_смен_не_выносятся(rb):
    """Показаний нет либо T=0 без полов: все проверки с единицей «смена»
    — not_checkable с причиной теми же словами, что у rest_min; число
    читателя rest_split_min=600 не применяется, 480 не подставляется;
    соседняя проверка без единицы «смена» досуживается."""
    got = _by_kind(sc.run_checks(_specs(600), _roster(650), rest_boundary=rb))
    for cid in ("duty_span_max", "turnaround_min", "landings_per_duty_max",
                "soft_event_count", "rest_min"):
        r = got[cid]
        assert r.verdict == "not_checkable", (cid, r.detail)
        assert r.violations == [] and r.objects_seen == 0, cid
        assert "rest_split_min=600" in r.detail, (cid, r.detail)
        assert "480" not in r.detail, (cid, r.detail)
    for cid in ("duty_span_max", "turnaround_min", "landings_per_duty_max",
                "soft_event_count"):
        r = got[cid]
        assert "не объявлена и из пола отдыха не выведена" in r.detail, cid
        assert "нарезка на смены не выносится" in r.detail, cid
        assert "rest_split_min=600 не применяется" in r.detail, cid
        if rb and rb.get("rest_boundary_source"):
            assert rb["rest_boundary_source"] in r.detail, cid
    assert "факт события duty_over_threshold не считается" \
        in got["soft_event_count"].detail
    assert got["crew_size_exact"].verdict == "ok"


def test_критерий_прогона_один_у_rest_min_и_проверок_смен():
    """duty_criterion — единственный источник нарезки: по всем трём
    режимам число смен у duty_span_max равно числу интервалов отдыха
    rest_min + 1, а сами смены совпадают с split_duties_by_place /
    split_duties."""
    sol = _roster(650)
    rows = sc.build_timelines(sol)["by_employee"]["E1"]
    for rb, want in ((_rest_floor(), [["F1", "F2", "F3", "F4"]]),
                     (_knob(600), [["F1", "F2"], ["F3", "F4"]]),
                     (_knob(130), [["F1", "F2"], ["F3"], ["F4"]])):
        tl = sc.build_timelines(sol, rb)
        crit = sc.duty_criterion(tl)
        duties = [[r["flight"] for r in d]
                  for d in sc.duties_of_run(tl, rows, HOME, crit)]
        assert duties == want, (rb["rest_boundary_basis"], duties)
        got = _by_kind(sc.run_checks(_specs(), sol, rest_boundary=rb))
        assert got["duty_span_max"].objects_seen == len(want)
        assert got["rest_min"].objects_seen == len(want) - 1
    # неопределённая граница — отказ словами и у нарезки
    tl0 = sc.build_timelines(sol, None)
    assert sc.duty_criterion(tl0)["mode"] is None
    with pytest.raises(sc.SourceCheckError, match="нарезка на смены не выносится"):
        sc.duties_of_run(tl0, rows, HOME)
