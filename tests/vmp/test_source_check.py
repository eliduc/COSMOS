# -*- coding: utf-8 -*-
"""Проверка решения от первоисточника (vmp/source_check): каждый вид
словаря ловит подстроенное нарушение ПОИМЁННО и молчит на чистом
решении; кривая спецификация — отказ словами; мягкое — факты, не
вердикт; протокол читается человеком.

Фикстуры синтетические (мир «Проба»): два сотрудника, два дня."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))

from vmp import source_check as sc  # noqa: E402


def _fl(fid, dep, t0, arr, t1):
    return {"flightLegId": fid, "flightNumber": fid,
            "departureAirport": dep, "departureUTCDateTime": t0,
            "arrivalAirport": arr, "arrivalUTCDateTime": t1,
            "aircraftType": "T1"}


def _fa(fid, emp, skill="Captain", chain="C1", deadhead=False):
    return {"flight": fid, "employee": emp, "requiredSkill": skill,
            "chainId": chain, "deadhead": deadhead}


def _solution():
    """E1: день 1 — две смены с коротким отдыхом дома нет (норм);
    вторая смена длинная (11:00). E2: чистый день."""
    flights = [
        _fl("F1", "AAA", "2026-09-07T06:00:00", "BBB",
            "2026-09-07T08:00:00"),
        _fl("F2", "BBB", "2026-09-07T09:00:00", "AAA",
            "2026-09-07T11:00:00"),
        # вторая смена E1 после 10 ч отдыха дома
        _fl("F3", "AAA", "2026-09-07T21:00:00", "BBB",
            "2026-09-07T23:00:00"),
        # ночёвка вне базы (пн->вт), затем рейс домой
        _fl("F4", "BBB", "2026-09-08T09:00:00", "AAA",
            "2026-09-08T11:00:00"),
        _fl("F5", "AAA", "2026-09-08T13:00:00", "BBB",
            "2026-09-08T15:00:00"),
        _fl("F6", "BBB", "2026-09-08T16:00:00", "AAA",
            "2026-09-08T18:00:00"),
    ]
    emps = [
        {"id": "E1", "homeAirport": "AAA", "skills": ["Captain"],
         "unavailableDays": [], "typeRatings": ["T1"]},
        {"id": "E2", "homeAirport": "AAA", "skills": ["Captain"],
         "unavailableDays": ["2026-09-09"], "typeRatings": ["T1"]},
        {"id": "E3", "homeAirport": "AAA", "skills": ["Captain"],
         "unavailableDays": [], "typeRatings": ["T1"]},
    ]
    fas = [
        _fa("F1", "E1"), _fa("F2", "E1"),
        _fa("F3", "E1"), _fa("F4", "E1"),
        _fa("F5", "E2", chain="C2"), _fa("F6", "E2", chain="C2"),
    ]
    return {"flights": flights, "employees": emps,
            "flightAssignments": fas}


def _one(kind, params, anchor="п.1", cid=None):
    return {"id": cid or ("chk-" + kind), "kind": kind,
            "params": params, "anchor": anchor}


def _boundary(t):
    """Показание прогона о границе «стыковка / отдых» T (стенд):
    исполнитель rest_min судит только по нему, не по константе."""
    return {"rest_boundary_min": t,
            "rest_boundary_source": f"стенд: T={t}"}


def test_чистое_решение_выполнено_по_всем_жёстким():
    specs = [
        _one("duty_span_max", {"limit_min": 720}),
        _one("rest_min", {"home_min": 600, "outstation_min": 600}),
        _one("turnaround_min", {"min_min": 45}),
        _one("landings_per_duty_max", {"limit": 4}),
        _one("crew_size_exact", {"n": 1}),
        _one("skill_required", {}),
        _one("unavailability", {}),
        _one("type_rating", {}),
        _one("pairing_span_max_days", {"limit_days": 3}),
    ]
    got = sc.run_checks(specs, _solution(), rest_boundary=_boundary(360))
    assert [r.verdict for r in got] == ["ok"] * len(specs)
    assert all(r.objects_seen > 0 for r in got
               if r.kind != "unavailability")


@pytest.mark.parametrize("kind,params,needle", [
    ("duty_span_max", {"limit_min": 240}, "E1"),
    ("rest_min", {"home_min": 720, "outstation_min": 600}, "AAA"),
    ("turnaround_min", {"min_min": 90}, "стыковка"),
    ("landings_per_duty_max", {"limit": 1}, "посадок"),
    ("crew_size_exact", {"n": 2}, "различных людей 1"),
    ("pairing_span_max_days", {"limit_days": 1}, "C1"),
])
def test_каждый_вид_ловит_подстроенное_нарушение(kind, params,
                                                 needle):
    got = sc.run_checks([_one(kind, params)], _solution(),
                        rest_boundary=_boundary(360))
    assert got[0].verdict == "violated"
    assert any(needle in v for v in got[0].violations), \
        got[0].violations


def test_базовый_предел_с_продлением_судится_счётом_а_потолок_жёстко():
    """Живой случай 09.09.2026 (пакет Н0-B): «базовый предел 12:00;
    превышение — только продлением по VN-011 (до 13:00 со штрафом)»
    читатель закодировал жёсткой duty_span_max(720) рядом с потолком 780
    и мягким счётом duty_over_threshold(720) — законные смены 12:40
    протокол считал нарушением. Правило выводится из самих спецификаций:
    база + потолок выше + мягкий счёт с порогом базы → база судится
    счётом; без потолка или без счёта база остаётся жёсткой."""
    base = _one("duty_span_max", {"limit_min": 240}, anchor="п.14", cid="VN-010-base")
    cap = _one("duty_span_max", {"limit_min": 360}, anchor="п.17", cid="VN-011-cap")
    soft = _one("soft_event_count", {"event": "duty_over_threshold",
                                     "threshold_min": 240},
                anchor="п.17", cid="VN-011-soft")
    assert sc.base_limits_with_extension([base, cap, soft]) == \
        {"VN-010-base": (360, "VN-011-cap")}
    got = {r.check_id: r for r in sc.run_checks(
        [base, cap, soft], _solution(), rest_boundary=_boundary(360))}
    assert got["VN-010-base"].verdict == "soft_info"
    assert got["VN-010-base"].kind == "duty_span_max"
    assert "с продлением до 6:00" in got["VN-010-base"].detail \
        and "VN-011-cap" in got["VN-010-base"].detail
    assert got["VN-010-base"].objects_seen >= 1        # превышения базы сочтены
    assert got["VN-011-cap"].verdict == "ok"
    assert got["VN-011-soft"].verdict == "soft_info"
    # fail-closed: без потолка либо без мягкого счёта база — жёсткий предел
    assert sc.base_limits_with_extension([base, soft]) == {}
    assert sc.base_limits_with_extension([base, cap]) == {}
    for specs in ([base, soft], [base, cap]):
        alone = {r.check_id: r for r in sc.run_checks(
            specs, _solution(), rest_boundary=_boundary(360))}
        assert alone["VN-010-base"].verdict == "violated"


def test_роль_недоступность_допуск_поимённо():
    sol = _solution()
    sol["flightAssignments"][0]["requiredSkill"] = "FirstOfficer"
    sol["employees"][1]["unavailableDays"] = ["2026-09-08"]
    sol["employees"][0]["typeRatings"] = ["T9"]
    got = {r.kind: r for r in sc.run_checks(
        [_one("skill_required", {}), _one("unavailability", {}),
         _one("type_rating", {})], sol)}
    assert got["skill_required"].verdict == "violated"
    assert "E1" in got["skill_required"].violations[0]
    assert got["unavailability"].verdict == "violated"
    assert "E2" in got["unavailability"].violations[0]
    assert got["type_rating"].verdict == "violated"
    assert "T1" in got["type_rating"].violations[0]


def test_резерв_по_базам_считает_свободных():
    # E3 свободен оба дня; норма 1 выполняется, норма 2 — нет.
    ok = sc.run_checks([_one("reserve_daily_min",
                             {"per_base": {"AAA": 1}})], _solution())
    assert ok[0].verdict == "ok"
    bad = sc.run_checks([_one("reserve_daily_min",
                              {"per_base": {"AAA": 2}})], _solution())
    assert bad[0].verdict == "violated"
    assert "своб" in bad[0].violations[0]


def test_мягкое_считает_факты_и_не_судит():
    # Смены для duty_over_threshold режутся критерием прогона (T=360 —
    # показание стенда), не числом читателя 480 (GPT-059).
    got = sc.run_checks(
        [_one("soft_event_count", {"event": "layover_nights"}),
         _one("soft_event_count", {"event": "duty_over_threshold",
                                   "threshold_min": 240})],
        _solution(), rest_boundary=_boundary(360))
    assert [r.verdict for r in got] == ["soft_info", "soft_info"]
    assert got[0].objects_seen == 1      # ночь BBB пн->вт (E1)
    assert got[1].objects_seen >= 1
    assert "штраф" in got[0].detail
    assert "T=360 мин (basis knob)" in got[1].detail
    # без показания прогона факт «смена сверх порога» честно не
    # считается (единица — смена), ночёвки от нарезки не зависят
    got0 = sc.run_checks(
        [_one("soft_event_count", {"event": "layover_nights"}),
         _one("soft_event_count", {"event": "duty_over_threshold",
                                   "threshold_min": 240})],
        _solution())
    assert [r.verdict for r in got0] == ["soft_info", "not_checkable"]
    assert "нарезка на смены не выносится" in got0[1].detail


def test_честный_отказ_и_кривая_спецификация():
    got = sc.run_checks(
        [{"id": "x", "kind": "not_machine_checkable",
          "anchor": "п.9", "reason": "нет данных о согласиях"}],
        _solution())
    assert got[0].verdict == "not_checkable"
    assert "согласи" in got[0].detail
    # Кривая спецификация держит отказ НА ПРОВЕРКЕ (вердикт с
    # причиной), не роняя комплект: соседние проверки досуживаются
    # (урок run-0003 2026-08-31 — читатель дважды дал кривые
    # параметры одной мягкой проверки и валил весь прогон).
    got2 = sc.run_checks(
        [{"kind": "нет_такого"},
         _one("duty_span_max", {}),
         _one("soft_event_count", {"event": "чужое"}),
         _one("crew_size_exact", {"n": 1})],
        _solution())
    assert [r.verdict for r in got2[:3]] == ["not_checkable"] * 3
    assert all("не судится" in r.detail for r in got2[:3])
    assert got2[3].verdict in ("ok", "violated")   # сосед досужен


def test_протокол_читается_и_несёт_числа():
    # duty_span_max режет смены показанием прогона (T=360), а не
    # умолчанием читателя 480 (GPT-059); без показания — «не проверяется»
    txt = sc.render_protocol(sc.run_checks(
        [_one("duty_span_max", {"limit_min": 240}),
         _one("crew_size_exact", {"n": 1})], _solution(),
        rest_boundary=_boundary(360)),
        header="решение: проба")
    assert "НАРУШЕНО" in txt and "ВЫПОЛНЕНО" in txt
    assert "Итог:" in txt and "решение: проба" in txt
    txt0 = sc.render_protocol(sc.run_checks(
        [_one("duty_span_max", {"limit_min": 240})], _solution()))
    assert "МАШИННО НЕ ПРОВЕРЯЕТСЯ" in txt0 and "НАРУШЕНО" not in txt0


def _rest_case(gap_min: int, *, at_home: bool) -> dict:
    """E1 (база AAA): F1 AAA→BBB 06:00–08:00, стыковка 120 мин, F2
    BBB→X 10:00–12:00, затем разрыв gap_min и F3 X→AAA. X = AAA
    (отдых в базе) при at_home, иначе CCC (вне базы)."""
    from datetime import datetime, timedelta
    x = "AAA" if at_home else "CCC"
    t3 = datetime.fromisoformat("2026-03-02T12:00") + timedelta(minutes=gap_min)
    return {
        "employees": [{"id": "E1", "homeAirport": "AAA"}],
        "flights": [
            {"flightNumber": "F1", "departureAirport": "AAA", "arrivalAirport": "BBB",
             "departureUTCDateTime": "2026-03-02T06:00", "arrivalUTCDateTime": "2026-03-02T08:00"},
            {"flightNumber": "F2", "departureAirport": "BBB", "arrivalAirport": x,
             "departureUTCDateTime": "2026-03-02T10:00", "arrivalUTCDateTime": "2026-03-02T12:00"},
            {"flightNumber": "F3", "departureAirport": x, "arrivalAirport": "AAA",
             "departureUTCDateTime": t3.isoformat(timespec="minutes"),
             "arrivalUTCDateTime": (t3 + timedelta(hours=1)).isoformat(timespec="minutes")},
        ],
        "flightAssignments": [
            {"flight": "F1", "employee": "E1"},
            {"flight": "F2", "employee": "E1"},
            {"flight": "F3", "employee": "E1"},
        ],
    }


def _rest_spec(home_min: int, out_min: int, reader_split=60) -> dict:
    return {"id": "R1", "kind": "rest_min", "anchor": "S2:2",
            "params": {"home_min": home_min, "outstation_min": out_min,
                       "rest_split_min": reader_split}}


def test_rest_min_judges_rest_by_solver_day_break_boundary():
    """Граница «стыковка / отдых» T — показание ПРОГОНА из линии времени
    (tl["rest_boundary_min"]), не константа (SOURCE-REST-R1): стыковка
    короче T — не отдых (пол отдыха к ней не применяется), разрыв ≥ T
    — отдых и судится по полу места; число читателя rest_split_min не
    подменяет T и остаётся показанием; источник T — в подробностях."""
    from vmp import source_check as sc
    sol = _rest_case(400, at_home=False)     # разрыв 400 ≥ T=360, вне базы R=600
    res = sc.run_checks([_rest_spec(600, 600)], sol,
                        rest_boundary={"rest_boundary_min": 360,
                                       "rest_boundary_source": "стенд: T=360"})[0]
    assert res.verdict == "violated", res.detail
    assert len(res.violations) == 1 and "CCC" in res.violations[0], res.violations
    assert res.objects_seen == 1, "стыковка 120 мин отдыхом не считается"
    assert "rest_split_min=60 не применяется" in res.detail
    assert "T=360" in res.detail and "стенд: T=360" in res.detail


def test_rest_min_T0_граница_не_объявлена_не_судится():
    """Контрпример GPT №1 (SOURCE-REST-R1): пакет без ручки max_sit_duration,
    T=0, разрыв 400 вне базы при R=600 — решатель мёртвую зону не судит
    (штраф только при T>0), значит и читатель ИДАК не выносит «нарушено»:
    not_checkable с причиной «граница не объявлена (T=0)»."""
    res = sc.run_checks([_rest_spec(720, 600)], _rest_case(400, at_home=False),
                        rest_boundary={"rest_boundary_min": 0,
                                       "rest_boundary_source":
                                           "показания решателя прогона r1: "
                                           "day_break_threshold_min=0"})[0]
    assert res.verdict == "not_checkable", res.detail
    assert res.violations == [] and res.objects_seen == 0
    assert "не объявлена" in res.detail and "T=0" in res.detail
    assert "day_break_threshold_min=0" in res.detail
    assert "rest_split_min=60" in res.detail      # показание, не граница


def test_rest_min_T480_разрыв_400_в_базе_стыковка_не_нарушение():
    """Контрпример GPT №2: явный T=480, разрыв 400 в базе при R=720 —
    у решателя 400 < T: стыковка внутри смены, штрафа нет; прежний
    литерал 360 давал ложное нарушение. Число читателя 60 тоже не
    режет."""
    res = sc.run_checks([_rest_spec(720, 600)], _rest_case(400, at_home=True),
                        rest_boundary={"rest_boundary_min": 480,
                                       "rest_boundary_source": "стенд: T=480"})[0]
    assert res.verdict == "ok", res.detail
    assert res.violations == []
    assert res.objects_seen == 0, "разрыв 400 < T=480 — стыковка, интервала отдыха нет"
    assert "T=480" in res.detail


def test_rest_min_T240_разрыв_300_вне_базы_нарушение():
    """Контрпример GPT №3: явный T=240, разрыв 300 вне базы при R=600 —
    у решателя T ≤ 300 < R: неполный отдых, нарушение; литерал 360
    считал это стыковкой (ложное «выполнено»)."""
    res = sc.run_checks([_rest_spec(720, 600)], _rest_case(300, at_home=False),
                        rest_boundary={"rest_boundary_min": 240,
                                       "rest_boundary_source": "стенд: T=240"})[0]
    assert res.verdict == "violated", res.detail
    assert len(res.violations) == 1 and "CCC" in res.violations[0]
    assert "5:00" in res.violations[0] and "10:00" in res.violations[0]
    assert res.objects_seen == 1


def test_rest_min_ровно_R_не_нарушение_и_ровно_T_отдых():
    """Края контракта решателя: gap = R — полный отдых (не нарушение);
    gap = T — уже интервал отдыха (не стыковка)."""
    r_eq = sc.run_checks([_rest_spec(720, 600)], _rest_case(600, at_home=False),
                         rest_boundary={"rest_boundary_min": 240,
                                        "rest_boundary_source": "стенд"})[0]
    assert r_eq.verdict == "ok" and r_eq.objects_seen == 1
    t_eq = sc.run_checks([_rest_spec(720, 600)], _rest_case(240, at_home=False),
                         rest_boundary={"rest_boundary_min": 240,
                                        "rest_boundary_source": "стенд"})[0]
    assert t_eq.verdict == "violated" and t_eq.objects_seen == 1


def test_rest_min_неизвестная_T_не_подставляет_литерал():
    """Показаний прогона нет (граница неизвестна) — not_checkable с
    причиной; ни константа, ни число читателя не подставляются."""
    for rb in (None, {"rest_boundary_min": None,
                      "rest_boundary_source": "показаний решателя нет: HTTP 503"}):
        res = sc.run_checks([_rest_spec(720, 600)],
                            _rest_case(400, at_home=False), rest_boundary=rb)[0]
        assert res.verdict == "not_checkable", res.detail
        assert "неизвестна" in res.detail and "литерал не подставляется" in res.detail
        assert res.violations == [] and res.objects_seen == 0
        if rb:
            assert "HTTP 503" in res.detail
    # соседние проверки при неизвестной T досуживаются как прежде
    got = sc.run_checks([_rest_spec(720, 600), _one("crew_size_exact", {"n": 1})],
                        _rest_case(400, at_home=False))
    assert [r.verdict for r in got] == ["not_checkable", "ok"]

