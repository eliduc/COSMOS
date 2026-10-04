# -*- coding: utf-8 -*-
"""Внешний аудит 04.09.2026, A6 — одна цена цепочки.

Стенды с известным ответом:
  1. определение ночи едино: тот же день = 0, следующая дата = 1,
     непрерывная смена через полночь ночи не создаёт;
  2. при критерии 0096 мягкие штрафы части (ночёвка, окно дней недели,
     продлённая смена) ВХОДЯТ в цену — прежде выпадали молча;
  3. дневной промежуток вне базы одной датой не даёт ночи окна —
     прежде max(1, …) начислял надбавку за несуществующую ночь;
  4. сверка по мерам ловит потерянный или подменённый штраф в
     расшифровке цены, а независимый пересчёт сертификатора сходится с
     каталогом на честных данных.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from vmp import chain_price as cp                      # noqa: E402
from vmp import penalty_reconcile as pr                # noqa: E402

_pb = pytest.importorskip("_pairing_builder")
from tests.test_pairing_builder import _args, _leg, _payload   # noqa: E402


def _p(start: str, end: str, rest_at: str) -> cp.Period:
    return cp.Period(datetime.fromisoformat(start),
                     datetime.fromisoformat(end), rest_at)


# ---------- 1. единое определение ночи -----------------------------------
def test_night_definition_same_day_zero_next_date_one():
    bases = {"BASE"}
    same_day = [_p("2026-08-07T06:00", "2026-08-07T08:00", "AAA"),
                _p("2026-08-07T20:00", "2026-08-07T22:00", "BASE")]
    assert cp.layover_nights(same_day, bases) == []
    next_day = [_p("2026-08-07T06:00", "2026-08-07T08:00", "AAA"),
                _p("2026-08-08T08:00", "2026-08-08T10:00", "BASE")]
    # контракт Б (единая версия 06.09.2026): ночь вне баз доказана только
    # при ПОЛНОМ универсуме приписок; без признака — неизвестно, не ноль
    n = cp.layover_nights(next_day, bases, eligible_bases_complete=True)
    assert [(x["station"], x["night"].isoformat()) for x in n] == \
        [("AAA", "2026-08-07")]
    assert cp.layover_nights(next_day, bases) == []
    assert cp.uncertain_events(next_day, bases)[0]["kind"] == "incomplete_home_universe"
    # непрерывная смена через полночь, окончена на базе: ночи нет
    overnight = [_p("2026-08-07T22:00", "2026-08-08T03:00", "BASE")]
    assert cp.layover_nights(overnight, bases) == []
    # последний период вне базы (контракт Б, 05.09.2026): продолжение
    # неизвестно — ночь не начисляется и не отрицается: доказанных ночей
    # ноль, неопределённый вклад назван адресно (открытый конец в AAA)
    tail = [_p("2026-08-07T06:00", "2026-08-07T08:00", "AAA")]
    assert cp.layover_nights(tail, bases) == []
    assert cp.layover_nights(tail, bases, open_end=True) == []
    assert [(e["kind"], e["station"]) for e in cp.uncertain_events(tail, bases)] \
        == [("open_end", "AAA")]


def test_price_breakdown_is_criterion_plus_measures_times_weights():
    periods = [_p("2026-08-07T06:00", "2026-08-07T08:00", "AAA"),   # пятница
               _p("2026-08-08T08:00", "2026-08-08T21:00", "BASE")]  # 780 мин
    rules = cp.rules_from_weights(w_night=10, w_weekend_night=25,
                                  weekend_days=(4, 5), w_long_duty=15,
                                  long_duty_threshold=720)
    got = cp.price(300, periods, rules, {"BASE"}, crew=2, criterion_kind="weights",
                   eligible_bases_complete=True)
    by = {r["family"]: r for r in got["soft"]}
    assert got["nights"] == 1
    assert by["layover_penalty"]["contribution"] == 10 * 1 * 2
    assert by["calendar_windows"]["contribution"] == 25 * 1 * 2
    assert by["duty_extension_penalty"]["contribution"] == 15 * 1 * 2
    assert got["soft_total"] == 100 and got["cost"] == 400
    assert cp.compare_rows(got, {"nights": 1, "measures": {
        "layover_penalty": 1, "calendar_windows": 1,
        "duty_extension_penalty": 1}}) == []
    # P-420: независимая переоценка — запись сверки (область, события,
    # определённость), не только числа; конец на BASE — неизвестен
    independently_repriced = cp.price(0, periods, rules, {"BASE"}, crew=2,
                                      criterion_kind="weights",
                                      eligible_bases_complete=True)
    assert cp.compare_rows(got, cp.comparison_record(independently_repriced)) == []
    assert got["full_cost"] is None and got["certainty"]["complete"] is False
    assert got["known_subtotal"] == 400 and got["cost_kind"] == "known_subtotal"
    assert got["price_model"] == cp.VERSION and got["is_lower_bound"] is False
    assert [e["reasons"] for e in got["unknown_events"]] == [["open_end"]]
    assert got["cost_certainty"] == "partial"


def test_unknown_family_refuses_instead_of_silent_zero():
    with pytest.raises(cp.ChainPriceError):
        cp.price(1, [_p("2026-08-07T06:00", "2026-08-07T08:00", "AAA")],
                 [{"rule_id": "X", "family": "unknown_family", "weight": 1}],
                 {"BASE"})


# ---------- 2. критерий 0096 несёт мягкие штрафы --------------------------
def _friday_world() -> dict:
    # 2026-08-07 — пятница: ночёвка в AAA начинается в пятницу.
    return _payload([
        _leg("F1", "BASE", "AAA", "2026-08-07T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-08T08:00:00", 120)])


def test_cost_0096_includes_soft_penalties_of_the_part():
    """Известный ответ: критерий 0096 = 10·(ставка полёта + работы) ×
    полётные минуты × состав = 10·(1+1)·240·2 = 9600; мягкие: ночь 10 +
    пятница 25 на человека, состав 2 → +70. Без A6 цена была бы 9600."""
    world = _friday_world()
    common = dict(sit_max=None, w_dh=0, w_span_day=0, rest_off=700,
                  rest_base=660, duty_span=780, cost_0096=True,
                  cost_rank_deci_work=20, cost_rank_deci_dh=10)
    cat0, rep0 = _pb.build(world, _args(**dict(common, w_night=0)))
    assert cat0["coverage"]["verdict"] == "OK" and len(cat0["selected"]) == 1
    assert cat0["selected"][0]["cost"] == 9600
    cat, rep = _pb.build(world, _args(**dict(
        common, w_night=10, w_weekend_night=25, weekend_days=(4, 5))))
    unit = cat["selected"][0]
    assert unit["cost"] == 9600 + (10 + 25) * 2
    assert unit["price"]["criterion"] == 9600
    assert unit["price"]["criterion_kind"] == "0096"
    assert unit["price"]["nights"] == 1
    assert cat["ip_objective"] == rep["total_cost"] == unit["cost"]
    assert rep["soft_penalty_total"] == 70
    assert rep["soft_penalty_by_family"] == {"layover_penalty": 20,
                                             "calendar_windows": 50}
    assert rep["price_model"] == cp.VERSION
    assert cat["config_sig"]["price_model"] == cp.VERSION
    # P-429/P-430: отчёт и каталог называют total_cost известной частью и
    # несут одну сводку определённости; подпись — область учёта цены
    assert rep["known_subtotal"] == rep["total_cost"] == cat["known_subtotal"]
    assert rep["full_cost"] is None and cat["full_cost"] is None    # хвост на BASE
    assert rep["cost_kind"] == "known_subtotal"
    assert rep["full_price_optimality"] == "not_proven"
    assert cat["ip_objective_scope"] == rep["solver_status_scope"] == "known_subtotal"
    assert cat["price_certainty"] == rep["price_certainty"] == \
        cp.aggregate_certainty(u["price"] for u in cat["selected"])
    assert cat["price_certainty"]["certainty"] == "partial"
    assert cat["config_sig"]["price_scope"] == cp.scope_template(
        {"BASE"}, eligible_bases_complete=True)
    assert unit["price"]["certainty"]["complete"] is False
    assert unit["price"]["known_subtotal"] == unit["cost"]


def test_builder_price_scope_is_home_universe_not_cli_bases():
    """P-425: универсум приписок цены — homeAirport всех сотрудников
    постановки, не ключ --bases; при сотруднике без приписки универсум
    неполон, и ночь вне баз не доказана (цена — известная часть с
    причиной), а базы ПОИСКА не меняются."""
    world = _friday_world()
    common = dict(sit_max=None, w_dh=0, w_span_day=0, rest_off=700,
                  rest_base=660, duty_span=780, w_night=10,
                  w_weekend_night=25, weekend_days=(4, 5))
    cat, rep = _pb.build(world, _args(bases="BASE,ZZZ", **common))
    sig = cat["config_sig"]
    assert sig["bases"] == "BASE,ZZZ"
    assert sig["price_scope"]["eligible_bases"] == ["BASE"]
    assert sig["price_scope"]["eligible_bases_complete"] is True
    assert sig["price_home_coverage"] == {
        "source": "all_payload_employees.homeAirport", "employees": 1,
        "with_home": 1, "complete": True}
    assert sig["soft_rule_ids"] == {}
    unit = cat["selected"][0]
    assert unit["price"]["accounting_scope"]["eligible_bases"] == ["BASE"]
    assert unit["price"]["nights"] == 1
    # сотрудник без приписки: универсум неполон → ночь в AAA не доказана
    world2 = _friday_world()
    world2["employees"].append({"id": "E2", "skills": ["Captain", "CA"]})
    cat2, rep2 = _pb.build(world2, _args(**common))
    assert cat2["config_sig"]["price_scope"]["eligible_bases_complete"] is False
    assert cat2["config_sig"]["price_home_coverage"]["with_home"] == 1
    unit2 = cat2["selected"][0]
    assert unit2["price"]["nights"] == 0
    assert "incomplete_home_universe" in {
        r for e in unit2["price"]["unknown_events"] for r in e["reasons"]}
    assert rep2["price_certainty"]["certainty"] == "partial"
    assert rep2["soft_penalty_total"] == 0


def test_builder_signature_carries_rule_ids_to_recompute():
    """P-411/P-430: id мягких правил ВМП едут в подписи, и пересчёт
    сертификатора получает те же id строк, что каталог."""
    from vmp.run import roster_certify as rc
    world = _friday_world()
    cat, rep = _pb.build(world, _args(
        sit_max=None, w_night=10, w_dh=0, w_span_day=0, rest_off=700,
        rest_base=660, duty_span=780, w_weekend_night=25, weekend_days=(4, 5),
        soft_rule_ids="layover_penalty=P-1,calendar_windows=P-2"))
    assert cat["config_sig"]["soft_rule_ids"] == {"layover_penalty": "P-1",
                                                  "calendar_windows": "P-2"}
    unit = cat["selected"][0]
    ids = {r["family"]: r["rule_id"] for r in unit["price"]["soft"]}
    assert ids == {"layover_penalty": "P-1", "calendar_windows": "P-2"}
    fmap = {f["flightNumber"]: {"t0": f["departureUTCDateTime"],
                                "t1": f["arrivalUTCDateTime"],
                                "dep": f["departureAirport"],
                                "arr": f["arrivalAirport"]}
            for f in world["flights"]}
    rec = rc.recompute_chain_measures(cat, fmap)
    assert rec[0]["rule_ids"] == ids
    assert pr.reconcile_pairing(cat, rep, recomputed=rec)["verdict"] == pr.OK
    cat["selected"][0]["price"]["soft"][0]["rule_id"] = "P-9"     # подмена id
    assert pr.reconcile_pairing(cat, rep, recomputed=rec)["verdict"] == pr.DIVERGE


def test_column_generation_names_the_search_objective_scope():
    """P-427: исчерпание поиска по числовой метрике — известная часть
    цены; certifies_bound и оценка снизу не обнуляются, их область
    названа явно (lower_bound_scope)."""
    from tests.test_pairing_builder import _simple_round_trip
    _cat, report = _pb.build(_simple_round_trip(), _args(method="columns"))
    assert report["pricing_objective_scope"] == "known_subtotal"   # конец открыт
    assert report["price_complete_for_pool"] is False
    assert report["lower_bound_scope"] == "known_subtotal"
    assert report["known_subtotal_search_complete"] == report["pricing_complete"]
    assert all(isinstance(r.get("certifies_bound"), bool) for r in report["cg_rounds"])
    if report["pricing_complete"]:
        assert report["lower_bound"] is not None


# ---------- 3. дневной промежуток одной датой — не ночь -------------------
def test_same_day_layover_gives_no_weekend_night():
    """Пятница: BASE→AAA 06:00–08:00, AAA→BASE 20:00–22:00 — две смены
    (16 ч > предела 780 мин), промежуток 12 ч ≥ отдыха вне базы 700 мин.
    Ночёвки нет: та же дата. Прежний max(1, …) начислял надбавку окна за
    несуществующую пятничную ночь. Известный ответ: 1 календарный день ×
    100 × состав 2 = 200, штрафов 0."""
    world = _payload([
        _leg("F1", "BASE", "AAA", "2026-08-07T06:00:00", 120),
        _leg("F2", "AAA", "BASE", "2026-08-07T20:00:00", 120)])
    cat, rep = _pb.build(world, _args(
        sit_max=None, w_night=10, w_dh=0, w_span_day=100, rest_off=700,
        rest_base=660, duty_span=780, w_weekend_night=25,
        weekend_days=(4, 5)))
    assert cat["coverage"]["verdict"] == "OK" and len(cat["selected"]) == 1
    unit = cat["selected"][0]
    assert len(unit["duty_fns"]) == 2, unit["duty_fns"]
    assert unit["price"]["nights"] == 0
    assert unit["cost"] == 100 * 2
    assert rep["soft_penalty_total"] == 0


# ---------- 4. сверка по мерам -------------------------------------------
def _catalog_with_fmap():
    world = _friday_world()
    cat, rep = _pb.build(world, _args(
        sit_max=None, w_night=10, w_dh=0, w_span_day=100, rest_off=700,
        rest_base=660, duty_span=780, w_weekend_night=25,
        weekend_days=(4, 5), w_long_duty=15, long_duty_threshold=720))
    assert len(cat["selected"]) == 1
    fmap = {}
    for f in world["flights"]:
        fmap[f["flightNumber"]] = {"t0": f["departureUTCDateTime"],
                                   "t1": f["arrivalUTCDateTime"],
                                   "dep": f["departureAirport"],
                                   "arr": f["arrivalAirport"]}
    return cat, rep, fmap


def test_reconcile_by_measures_matches_on_honest_catalog():
    from vmp.run import roster_certify as rc
    cat, rep, fmap = _catalog_with_fmap()
    rec = rc.recompute_chain_measures(cat, fmap)
    # uncertain = 1: открытый конец цепочки на базе (контракт Б) —
    # доказанная ночь в AAA одна, полнота цены не утверждается. Запись
    # пересчёта — общая форма comparison_record (P-412): числа main плюс
    # область учёта, события и определённость.
    assert len(rec) == 1 and rec[0]["nights"] == 1 and rec[0]["uncertain"] == 1
    assert rec[0]["measures"] == {"layover_penalty": 1, "calendar_windows": 1,
                                  "duty_extension_penalty": 0}
    assert rec[0]["weights"] == {"layover_penalty": 10, "calendar_windows": 25,
                                 "duty_extension_penalty": 15}
    assert rec[0]["price_model"] == cp.VERSION
    assert rec[0]["certainty"]["complete"] is False
    assert rec[0]["accounting_scope"]["eligible_bases"] == ["BASE"]
    assert rec[0] == cp.comparison_record(cat["selected"][0]["price"])
    out = pr.reconcile_pairing(cat, rep, recomputed=rec)
    # политика P-417: известная часть сверена — «сошлось», полная
    # стоимость не подтверждена и названа словами
    assert out["verdict"] == pr.OK, out
    assert out["known_subtotal_verdict"] == pr.OK
    assert out["price_complete"] is False and out["full_cost_confirmed"] is False
    assert "известная часть стоимости пейринга сверена" in out["note"]
    assert "полная стоимость не установлена" in out["note"]
    assert out["priced_chains"] == 1 and out["recomputed"] is True
    assert out["measure_divergences"] == 0 and out["signature_verified"] is True
    assert out["price_certainty"] == cat["price_certainty"]


def test_reconcile_by_measures_catches_lost_penalty_with_equal_sums():
    """Потерянный штраф, спрятанный в равных суммах: вклад окна обнулён,
    критерий поднят на ту же величину — суммы сходятся, меры нет."""
    cat, rep, fmap = _catalog_with_fmap()
    unit = cat["selected"][0]
    row = next(r for r in unit["price"]["soft"] if r["family"] == "calendar_windows")
    unit["price"]["criterion"] += row["contribution"]
    row["contribution"] = 0
    row["measure"] = 0
    # P-413: подделка без подчистки адресов ловится самопроверкой —
    # мера не совпадает с evidence
    self_check = pr.reconcile_pairing(cat, rep)
    assert self_check["verdict"] == pr.DIVERGE and "evidence" in self_check["note"]
    row["evidence"] = []
    out = pr.reconcile_pairing(cat, rep)          # без пересчёта
    # P-421: арифметика цела, но суммы без пересчёта — не сертификат
    assert out["verdict"] == pr.INCOMPARABLE
    assert out["known_subtotal_verdict"] == pr.INCOMPARABLE
    assert "независимого пересчёта нет" in out["note"]
    from vmp.run import roster_certify as rc
    out2 = pr.reconcile_pairing(cat, rep, recomputed=rc.recompute_chain_measures(cat, fmap))
    assert out2["verdict"] == pr.DIVERGE
    assert "calendar_windows" in out2["note"] and "пересчёт 1" in out2["note"]


def test_reconcile_catches_broken_breakdown_arithmetic():
    cat, rep, fmap = _catalog_with_fmap()
    unit = cat["selected"][0]
    unit["price"]["soft"][0]["contribution"] += 1      # вклад ≠ мера×вес×состав
    out = pr.reconcile_pairing(cat, rep)
    assert out["verdict"] == pr.DIVERGE and "вклад" in out["note"]


def test_pairing_input_carries_base_and_rest_station():
    """A6 (вердикт аудита): вход сертификатора из каталога без базы и
    места отдыха оставлял проверки «в базе / вне базы» слепыми."""
    from vmp.run import roster_certify as rc
    cat, rep, fmap = _catalog_with_fmap()
    sol = rc.pairing_input_from_catalog(cat, fmap)
    assert all(a["base"] == "BASE" for a in sol["assignments"])
    duties = {d["id"]: d for d in sol["duties"]}
    assert len(duties) == 2
    by_start = sorted(sol["duties"], key=lambda d: d["start"])
    assert by_start[0]["last_arr_airport"] == "AAA"      # ночёвка вне базы
    assert by_start[1]["last_arr_airport"] == "BASE"
    assert all(d["base"] == "BASE" for d in sol["duties"])


def test_recompute_reads_signature_not_builder_numbers():
    """Пересчёт берёт базы/пояс/окно/порог из подписи постановки: сдвиг
    пояса в подписи меняет дату ночи и, значит, меру окна."""
    from vmp.run import roster_certify as rc
    cat, rep, fmap = _catalog_with_fmap()
    cat["config_sig"]["weekend_days"] = [5]            # только суббота
    rec = rc.recompute_chain_measures(cat, fmap)
    assert rec[0]["measures"]["calendar_windows"] == 0
    out = pr.reconcile_pairing(cat, rep, recomputed=rec)
    assert out["verdict"] == pr.DIVERGE
    # P-423 (одно поведение, закреплено): без подписи постановки либо с
    # подписью чужой модели / без области учёта независимого пересчёта
    # нет — None; причину словами называет reconcile_pairing («подпись
    # цены: …», несравнимо), сертификат не падает исключением.
    assert rc.recompute_chain_measures({"selected": []}, fmap) is None
    cat3, rep3, _ = _catalog_with_fmap()
    cat3["config_sig"]["price_model"] = "chain-price/1"
    assert rc.recompute_chain_measures(cat3, fmap) is None
    out3 = pr.reconcile_pairing(cat3, rep3, recomputed=None)
    assert out3["verdict"] == pr.INCOMPARABLE and "подпись цены" in out3["note"]
    cat4, _rep4, _ = _catalog_with_fmap()
    del cat4["config_sig"]["price_scope"]
    assert rc.recompute_chain_measures(cat4, fmap) is None




# ---------------------------------------------------------------------------
# Контракт Б (план 05.09.2026, согласован владельцем, аудитором, COSMOS и
# CDPM): цена цепочки учитывает только ДОКАЗАННЫЕ внутренние ночёвки по
# определению ВМП; отдых на базе и открытый конец — неопределённый вклад,
# показывается отдельно (число и адреса), не ноль и не «нижняя оценка».
# Граница горизонта сама по себе — не основание ни начислить ночёвку, ни
# признать её отсутствующей. Ожидания ниже заданы сценарием и ВМП, а не
# согласием двух реализаций. Случаи приёмки 1–5, 7, 8 (6 — сквозной, на
# живом прогоне).
# ---------------------------------------------------------------------------

_BASES = {"PLY", "SVG", "GRT"}


def _duty(emp, did, start, end, rest_at, base="PLY", row=1):
    from datetime import datetime
    return {"employee": emp, "id": did, "row": row, "base": base,
            "rest_at": rest_at, "start": datetime.fromisoformat(start),
            "end": datetime.fromisoformat(end)}


def test_case1_day_return_one_chain_and_two_chains_zero_nights():
    """Случай 1: PLY → SVG → PLY одним днём — ноль ночёвок в любой записи:
    одной цепочкой и двумя. Открытый конец — неопределённость, не ночь."""
    one = [_p("2026-09-04T06:00", "2026-09-04T08:00", "SVG"),
           _p("2026-09-04T09:00", "2026-09-04T11:00", "PLY")]
    assert cp.layover_nights(one, _BASES) == []
    first = [_p("2026-09-04T06:00", "2026-09-04T08:00", "SVG")]
    second = [_p("2026-09-04T09:00", "2026-09-04T11:00", "PLY")]
    assert cp.layover_nights(first, _BASES) == []
    assert cp.layover_nights(second, _BASES) == []
    # неопределённый вклад назван адресно: у каждой цепочки открытый конец
    assert [e["kind"] for e in cp.uncertain_events(one, _BASES)] == ["open_end"]
    assert [e["kind"] for e in cp.uncertain_events(first, _BASES)] == ["open_end"]
    assert cp.uncertain_events(first, _BASES)[0]["station"] == "SVG"


def test_case2_real_layover_is_one_night():
    """Случай 2: отдых на небазовой станции до смены следующего дня —
    ровно одна ночёвка, адрес — станция и календарная ночь."""
    two = [_p("2026-09-04T06:00", "2026-09-04T17:30", "AAA"),
           _p("2026-09-05T08:00", "2026-09-05T11:00", "PLY")]
    n = cp.layover_nights(two, _BASES, eligible_bases_complete=True)
    assert [(x["station"], x["night"].isoformat()) for x in n] == [("AAA", "2026-09-04")]
    rules = cp.rules_from_weights(w_night=10)
    got = cp.price(0, two, rules, _BASES, crew=2, eligible_bases_complete=True)
    assert got["nights"] == 1 and got["cost"] == 20
    # цена не полная: конец цепочки на базе — приписка неизвестна
    assert got["cost_certainty"] == "partial"
    assert [e["kind"] for e in got["uncertain"]] == ["open_end"]


def test_case2b_rest_at_a_base_is_uncertain_not_zero_and_not_a_night():
    """Отдых на объявленной базе до смены другого дня (PLY → SVG, наутро
    SVG → PLY): для экипажа SVG дом, для экипажа PLY ночёвка — цепочка
    этого не знает. Не ночь и не ноль: неопределённый вклад с адресом.
    База старта цепочки базой приписки НЕ считается (аудит 05.09.2026)."""
    two = [_p("2026-09-04T06:00", "2026-09-04T17:30", "SVG"),
           _p("2026-09-05T08:00", "2026-09-05T11:00", "PLY")]
    assert cp.layover_nights(two, _BASES) == []
    unc = cp.uncertain_events(two, _BASES)
    assert [(e["kind"], e["station"]) for e in unc] == [("rest_at_base", "SVG"),
                                                        ("open_end", "PLY")]
    assert unc[0]["nights_if_away"] == 1
    got = cp.price(0, two, cp.rules_from_weights(w_night=10), _BASES, crew=1)
    assert got["cost"] == 0 and got["cost_certainty"] == "partial"
    assert "home" not in got


def test_case3_start_away_from_home_base_no_extra_charge():
    """Случай 3: сотрудник базы PLY, находящийся в SVG, начинает смену там
    и возвращается в PLY — приписка не меняется, лишних начислений нет."""
    from vmp.run import roster_certify as rc
    duties = {"E1": [_duty("E1", "D1", "2026-09-04T08:00", "2026-09-04T11:00", "PLY", row=1),
                     _duty("E1", "D2", "2026-09-05T08:00", "2026-09-05T11:00", "PLY", row=2)]}
    unc: list = []
    assert rc._layover_nights(duties, uncertain=unc) == []
    assert unc == []


def test_case4_partition_invariance_full_cost_is_rostering_and_pairing_never_claims_full():
    """Случай 4: полная стоимость расписания не зависит от разбиения на
    цепочки. Маршрут PLY → AAA (04.09) → PLY (05.09): у сотрудника базы
    PLY это одна ночёвка при любом разбиении (ростеринг судит по
    фактической приписке); цепочка, разрезанная в AAA, эту ночь доказать
    не может — она уходит в неопределённый вклад, а не в ноль, и цена
    каталога полной не объявляется."""
    from vmp.run import roster_certify as rc
    whole = [_p("2026-09-04T06:00", "2026-09-04T17:30", "AAA"),
             _p("2026-09-05T08:00", "2026-09-05T11:00", "PLY")]
    part1 = [_p("2026-09-04T06:00", "2026-09-04T17:30", "AAA")]
    part2 = [_p("2026-09-05T08:00", "2026-09-05T11:00", "PLY")]
    assert len(cp.layover_nights(whole, _BASES, eligible_bases_complete=True)) == 1
    assert cp.layover_nights(part1, _BASES, eligible_bases_complete=True) == []
    assert cp.layover_nights(part2, _BASES, eligible_bases_complete=True) == []
    assert [(e["kind"], e["station"]) for e in cp.uncertain_events(part1, _BASES)] == [("open_end", "AAA")]
    # ростеринг: та же ночь ровно одна независимо от разбиения
    duties = {"E1": [_duty("E1", "D1", "2026-09-04T06:00", "2026-09-04T17:30", "AAA", row=1),
                     _duty("E1", "D2", "2026-09-05T08:00", "2026-09-05T11:00", "PLY", row=2)]}
    unc: list = []
    nights = rc._layover_nights(duties, uncertain=unc)
    assert [(x["airport"], x["night"].isoformat()) for x in nights] == [("AAA", "2026-09-04")]
    assert unc == []


def test_case5_no_double_charge_same_night_once_per_accounting():
    """Случай 5: одна календарная ночь начисляется один раз — и в цене
    цепочки, и в мере ростеринга; две смены подряд вне базы через одну
    ночь дают одну ночь, а не две."""
    from vmp.run import roster_certify as rc
    periods = [_p("2026-09-04T06:00", "2026-09-04T17:30", "AAA"),
               _p("2026-09-05T08:00", "2026-09-05T12:00", "AAA"),
               _p("2026-09-05T13:00", "2026-09-05T15:00", "PLY")]
    assert [x["night"].isoformat() for x in cp.layover_nights(
        periods, _BASES, eligible_bases_complete=True)] == ["2026-09-04"]
    duties = {"E1": [_duty("E1", "D1", "2026-09-04T06:00", "2026-09-04T17:30", "AAA", row=1),
                     _duty("E1", "D2", "2026-09-05T08:00", "2026-09-05T12:00", "AAA", row=2),
                     _duty("E1", "D3", "2026-09-05T13:00", "2026-09-05T15:00", "PLY", row=3)]}
    assert [x["night"].isoformat() for x in rc._layover_nights(duties)] == ["2026-09-04"]


def test_case7_day_end_away_unknown_continuation_no_auto_night_uncertainty_named():
    """Случай 7: дневное окончание вне базы с неизвестным продолжением —
    автоматической ночёвки нет (прежде сертификатор дописывал одну ночь
    после последней смены, а решатель — ночь после последнего прилёта),
    неопределённость отражена явно, полная стоимость не подтверждается."""
    from vmp.run import roster_certify as rc
    from vmp.run import verdicts as vd
    from vmp import penalty_reconcile as pr_
    tail = [_p("2026-09-04T06:00", "2026-09-04T17:30", "AAA")]
    got = cp.price(0, tail, cp.rules_from_weights(w_night=10), _BASES, crew=2)
    assert got["nights"] == 0 and got["cost"] == 0
    assert got["cost_certainty"] == "partial"
    assert got["uncertain"][0]["kind"] == "open_end" and got["uncertain"][0]["station"] == "AAA"
    duties = {"E1": [_duty("E1", "D1", "2026-09-04T06:00", "2026-09-04T17:30", "AAA", row=1)]}
    unc: list = []
    assert rc._layover_nights(duties, uncertain=unc) == []
    assert [(u["kind"], u["airport"]) for u in unc] == [("boundary", "AAA")]
    # статус стоимости при сошедшейся сверке, но неопределённом вкладе
    cert = {"penalty_reconciliation": {"verdict": pr_.OK}, "cost_certainty": "partial"}
    assert vd._cost_of(cert) == "partial"
    assert "не установлена" in vd.WORDS["cost"]["partial"]
    cert["cost_certainty"] = "full"
    assert vd._cost_of(cert) == "confirmed"


def test_case8_known_continuation_same_day_return_no_charge():
    """Случай 8: известное продолжение с возвратом в тот же день —
    начисления нет ни у цены, ни у меры сертификата."""
    from vmp.run import roster_certify as rc
    periods = [_p("2026-09-04T06:00", "2026-09-04T08:00", "AAA"),
               _p("2026-09-04T09:00", "2026-09-04T11:00", "PLY")]
    assert cp.layover_nights(periods, _BASES) == []
    duties = {"E1": [_duty("E1", "D1", "2026-09-04T06:00", "2026-09-04T08:00", "AAA", row=1),
                     _duty("E1", "D2", "2026-09-04T09:00", "2026-09-04T11:00", "PLY", row=2)]}
    unc: list = []
    assert rc._layover_nights(duties, uncertain=unc) == [] and unc == []


def test_pairing_accounting_rest_at_base_is_uncertain_for_certifier_measure():
    """Сертификат пейринга: область учёта та же, что у цены — отдых на
    любой объявленной базе неопределён (у цепочки людей нет), граница
    цепочки неопределённа; доказанной остаётся только ночь вне всех баз."""
    from vmp.run import roster_certify as rc
    duties = {"C1": [_duty("C1", "D1", "2026-09-04T06:00", "2026-09-04T17:30", "SVG", row=1),
                     _duty("C1", "D2", "2026-09-05T08:00", "2026-09-05T17:30", "AAA", row=2),
                     _duty("C1", "D3", "2026-09-06T08:00", "2026-09-06T11:00", "GRT", row=3)]}
    unc: list = []
    nights = rc._layover_nights(duties, {"PLY", "SVG", "GRT"}, unc)
    assert [(x["airport"], x["night"].isoformat()) for x in nights] == [("AAA", "2026-09-05")]
    assert [(u["kind"], u["airport"]) for u in unc] == [("rest_at_base", "SVG"), ("boundary", "GRT")]
    # цепочка, оконченная на базе СТАРТА: у пейринга это не «дома», а та же
    # неопределённость (живой проход 05.09: 94 события против 125)
    duties2 = {"C2": [_duty("C2", "D1", "2026-09-04T06:00", "2026-09-04T11:00", "PLY", row=1)]}
    unc2: list = []
    assert rc._layover_nights(duties2, {"PLY", "SVG", "GRT"}, unc2) == []
    assert [(u["kind"], u["airport"]) for u in unc2] == [("boundary", "PLY")]
    # у ростеринга приписка известна: окончание дома — не событие
    unc3: list = []
    assert rc._layover_nights(duties2, None, unc3) == [] and unc3 == []


def test_certifier_recompute_same_accounting_as_price_start_base_is_not_home():
    """Пересчёт мер сертификата: цепочка PLY → GRT → SVG, оконченная на
    другой базе, — ноль доказанных ночёвок и один неопределённый вклад
    (открытый конец); «база = станция старта» не применяется."""
    from vmp.run import roster_certify as rc
    # P-402/P-410: контекст пересчёта — строго из области учёта подписи
    # (price_scope), не из строки --bases
    catalog = {"config_sig": {"price_model": cp.VERSION, "bases": "GRT",
                              "price_scope": cp.scope_template(
                                  {"GRT", "PLY", "SVG"}, eligible_bases_complete=True),
                              "tz_offset_min": 0,
                              "w_night": 10, "w_weekend_night": 0,
                              "price_criterion": "weights"},
               "selected": [{"fns": ["A", "B"], "active_fns": ["A", "B"],
                             "duty_fns": [["A", "B"]], "dh": []}]}
    flights = {"A": {"t0": "2026-09-04T06:00", "t1": "2026-09-04T08:00", "dep": "PLY", "arr": "GRT"},
               "B": {"t0": "2026-09-04T09:00", "t1": "2026-09-04T11:00", "dep": "GRT", "arr": "SVG"}}
    got = rc.recompute_chain_measures(catalog, flights)
    assert got and got[0]["nights"] == 0 and got[0]["uncertain"] == 1, got
    assert got[0]["accounting_scope"]["eligible_bases"] == ["GRT", "PLY", "SVG"]
    assert got[0]["unknown_events"][0]["reasons"] == ["open_end"]
    # область учёта едет во вход сертификатора из подписи постановки
    cat2, rep2, fmap2 = _catalog_with_fmap()
    sol2 = rc.pairing_input_from_catalog(cat2, fmap2)
    assert sol2["accounting"]["task"] == "pairing"
    assert sol2["accounting"]["uncertain_bases"] == \
        cat2["config_sig"]["price_scope"]["eligible_bases"] == ["BASE"]
    assert sol2["accounting"]["eligible_bases_complete"] is True
    # каталог без области учёта (прежняя форма подписи): строка --bases
    sol3 = rc.pairing_input_from_catalog(dict(cat2, config_sig={"bases": "SVG,PLY"}), fmap2)
    assert sol3["accounting"]["uncertain_bases"] == ["PLY", "SVG"]
    assert sol3["accounting"]["eligible_bases_complete"] is None
