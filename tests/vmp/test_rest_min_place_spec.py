# -*- coding: utf-8 -*-
"""Проверка отдыха по ИДАК (rest_min), заданная ПО ОДНОМУ МЕСТУ
(живой контур Test 06.09.2026): читатель скомпилировал правило
минимального отдыха в две проверки — «отдых дома» с одним home_min и
«отдых вне базы» с одним outstation_min; исполнитель читал оба ключа
безусловно → KeyError → «кривая спецификация — не судится».

Ожидания — через НАСТОЯЩИЙ source_check.run_checks: проверка с одним
полом судит только интервалы отдыха своего места (другое место в
objects_seen не считается), единый min_min — оба места одним полом,
спецификация без полов — not_checkable словами, прежний случай с обоими
ключами — без изменений. Критерий нарезки смен — по показаниям прогона,
не по полам клаузы."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))

from vmp import source_check as sc  # noqa: E402

HOME = "AAA"
R_HOME, R_OUT = 720, 600


def _leg(num, dep, arr, t0: datetime, minutes=120):
    return {"flightNumber": num, "departureAirport": dep,
            "arrivalAirport": arr,
            "departureUTCDateTime": t0.isoformat(timespec="minutes"),
            "arrivalUTCDateTime": (t0 + timedelta(minutes=minutes))
            .isoformat(timespec="minutes")}


def _roster(out_gap: int, home_gap: int) -> dict:
    """Один сотрудник E1 (база AAA), одна линия времени с ДВУМЯ
    интервалами отдыха: F1 AAA→BBB 06:00–08:00, разрыв out_gap вне базы
    (BBB), F2 BBB→AAA, разрыв home_gap дома (AAA), F3 AAA→BBB."""
    t1 = datetime.fromisoformat("2026-08-01T06:00")
    t2 = t1 + timedelta(minutes=120 + out_gap)
    t3 = t2 + timedelta(minutes=120 + home_gap)
    legs = [_leg("F1", HOME, "BBB", t1), _leg("F2", "BBB", HOME, t2),
            _leg("F3", HOME, "BBB", t3)]
    return {"employees": [{"id": "E1", "homeAirport": HOME}],
            "flights": legs,
            "flightAssignments": [{"flight": f["flightNumber"],
                                   "employee": "E1"} for f in legs]}


def _spec(cid: str, params: dict) -> dict:
    return {"id": cid, "kind": "rest_min", "anchor": "VMP-H0-2",
            "params": params}


def _home_only():
    return _spec("vn023-rest-home", {"home_min": R_HOME})


def _out_only():
    return _spec("vn024-rest-outstation", {"outstation_min": R_OUT})


def _both():
    return _spec("vn023-rest", {"home_min": R_HOME,
                                "outstation_min": R_OUT})


def _threshold(t: int = 360) -> dict:
    return {"rest_boundary_min": t,
            "rest_boundary_source": f"стенд: T={t}"}


def _rest_floor() -> dict:
    return {"rest_boundary_min": min(R_HOME, R_OUT),
            "rest_boundary_source": "стенд: выведено из пола отдыха по месту",
            "rest_boundary_basis": "rest_floor",
            "rest_boundary_by_place": {"home": R_HOME, "outstation": R_OUT},
            "rest_boundary_assumption": "МОДЕЛЬНОЕ УПРОЩЕНИЕ: стенд"}


def _by_id(results):
    return {r.check_id: r for r in results}


def test_две_проверки_по_одному_месту_каждая_судит_только_своё():
    """(а) Оба интервала законные (вне базы 700 ≥ 600, дома 800 ≥ 720):
    обе проверки ok, у каждой objects_seen = 1 — только своё место;
    показание «проверка по месту» — в подробностях."""
    got = _by_id(sc.run_checks([_home_only(), _out_only()],
                               _roster(out_gap=700, home_gap=800),
                               rest_boundary=_threshold()))
    home, out = got["vn023-rest-home"], got["vn024-rest-outstation"]
    assert home.verdict == "ok" and out.verdict == "ok", (home.detail,
                                                          out.detail)
    assert home.objects_seen == 1 and out.objects_seen == 1
    assert home.violations == [] and out.violations == []
    assert ("проверка по месту: только дома (пол 720); отдых вне базы "
            "этой проверкой не судится") in home.detail
    assert ("проверка по месту: только вне базы (пол 600); отдых дома "
            "этой проверкой не судится") in out.detail
    assert "интервалов отдыха проверено: 1" in home.detail
    assert "интервалов отдыха проверено: 1" in out.detail
    assert "KeyError" not in home.detail and "KeyError" not in out.detail


def test_нарушение_только_вне_базы_ловит_проверка_вне_базы():
    """(б) Вне базы 590 < 600 при доме 800 ≥ 720: проверка «вне базы»
    violated поимённо (BBB), проверка «дома» ok и нарушения вне базы не
    видит."""
    got = _by_id(sc.run_checks([_home_only(), _out_only()],
                               _roster(out_gap=590, home_gap=800),
                               rest_boundary=_threshold()))
    home, out = got["vn023-rest-home"], got["vn024-rest-outstation"]
    assert out.verdict == "violated", out.detail
    assert len(out.violations) == 1 and "BBB" in out.violations[0]
    assert "9:50" in out.violations[0] and "10:00" in out.violations[0]
    assert out.objects_seen == 1
    assert home.verdict == "ok" and home.violations == [], home.detail
    assert home.objects_seen == 1


def test_нарушение_только_дома_ловит_проверка_дома():
    """Симметрия (б): дома 700 < 720 при вне базы 700 ≥ 600 — «дома»
    violated (AAA), «вне базы» ok."""
    got = _by_id(sc.run_checks([_home_only(), _out_only()],
                               _roster(out_gap=700, home_gap=700),
                               rest_boundary=_threshold()))
    home, out = got["vn023-rest-home"], got["vn024-rest-outstation"]
    assert home.verdict == "violated", home.detail
    assert len(home.violations) == 1 and "AAA" in home.violations[0]
    assert out.verdict == "ok" and out.violations == [], out.detail


def test_спецификация_без_полов_не_судится_словами_без_KeyError():
    """(в) Ни home_min, ни outstation_min, ни min_min — not_checkable с
    причиной словами; KeyError наружу не выходит; соседняя проверка
    досуживается."""
    specs = [_spec("vn025-rest-empty", {}),
             _spec("vn026-rest-reader-only", {"rest_split_min": 60}),
             _out_only()]
    got = _by_id(sc.run_checks(specs, _roster(out_gap=700, home_gap=800),
                               rest_boundary=_threshold()))
    for cid in ("vn025-rest-empty", "vn026-rest-reader-only"):
        r = got[cid]
        assert r.verdict == "not_checkable", r.detail
        assert ("в спецификации нет пола отдыха (home_min / "
                "outstation_min / min_min)") in r.detail
        assert "KeyError" not in r.detail and "кривая" not in r.detail
        assert r.violations == [] and r.objects_seen == 0
    assert got["vn024-rest-outstation"].verdict == "ok"


def test_оба_ключа_как_прежде_оба_места_в_одной_проверке():
    """(г) Прежний случай с обоими ключами — без изменений: оба
    интервала считаются (objects_seen = 2), нарушение вне базы ловится,
    показания «проверка по месту» нет, прежняя строка о полах клаузы
    при нарезке по полу сохранена."""
    res = sc.run_checks([_both()], _roster(out_gap=590, home_gap=800),
                        rest_boundary=_threshold())[0]
    assert res.verdict == "violated", res.detail
    assert res.objects_seen == 2
    assert len(res.violations) == 1 and "BBB" in res.violations[0]
    assert "проверка по месту" not in res.detail
    ok = sc.run_checks([_both()], _roster(out_gap=700, home_gap=800),
                       rest_boundary=_rest_floor())[0]
    assert ok.verdict == "ok" and ok.objects_seen == 2, ok.detail
    assert ("достаточность отдыха — пол клаузы по месту (720 / 600), "
            "обязательное правило") in ok.detail


def test_единый_пол_min_min_судит_оба_места_одним_полом():
    """Единый ключ min_min=650: вне базы 590 < 650 — нарушение, дома 800
    ≥ 650 — нет; оба интервала в счёте; показание о едином поле."""
    res = sc.run_checks([_spec("vn027-rest-single", {"min_min": 650})],
                        _roster(out_gap=590, home_gap=800),
                        rest_boundary=_threshold())[0]
    assert res.verdict == "violated", res.detail
    assert res.objects_seen == 2
    assert len(res.violations) == 1 and "BBB" in res.violations[0]
    assert "единый пол отдыха min_min=650" in res.detail


def test_нарезка_по_критерию_прогона_а_не_по_полам_клаузы():
    """Критерий нарезки — показания прогона (rest_floor по месту 720 /
    600), не полы клаузы: проверка только «дома» на прогоне без ручки
    видит ровно один интервал дома (разрыв вне базы 700 ≥ 600 — граница
    смены по критерию прогона, но этой проверкой не судится); при
    отсутствии показаний прогона — not_checkable, как прежде."""
    res = sc.run_checks([_home_only()], _roster(out_gap=700, home_gap=800),
                        rest_boundary=_rest_floor())[0]
    assert res.verdict == "ok" and res.objects_seen == 1, res.detail
    assert "критерий границы смены: пол минимального отдыха по месту" \
        in res.detail
    assert "пол клаузы по месту (720 / —)" in res.detail
    assert "проверка по месту: только дома" in res.detail
    none = sc.run_checks([_home_only()], _roster(out_gap=700, home_gap=800),
                         rest_boundary=None)[0]
    assert none.verdict == "not_checkable" and none.objects_seen == 0
    assert "неизвестна" in none.detail
