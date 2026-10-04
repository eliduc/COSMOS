# -*- coding: utf-8 -*-
"""Контракт Б — комплект случаев GPT, перенесённый по смыслу в форму main
(единая версия 06.09.2026, строки P-431 и P-432; исходники —
tests/test_h0_contract_b_price_gpt.py, blob 5998c88, и
tests/test_h0_pairing_certificate_note_gpt.py, blob df1d126, дерево
h0-legion-test-gpt).

Отличия от исходника — решения единой версии: режимов границы нет
(P-403 не включать: любая явная граница отвергается), параметра
home_base нет (P-405: известная приписка — зона ростеринга,
roster_certify._layover_nights); совпавшая известная часть при
неизвестном остатке — «сошлось» с пометкой «полная стоимость не
установлена», не отзыв сертификата (политика P-417, §3.10.3);
отсутствующая или чужая подпись цены — «несравнимо», не «расхождение»
(P-416/P-424). Чистые юнит-случаи: без решателя, сервиса и данных.
"""
from __future__ import annotations

import sys
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from vmp import chain_price as cp                      # noqa: E402
from vmp import penalty_reconcile as reconcile         # noqa: E402

HOMES = ("PLY", "SVG", "GRT")


def p(start, end, station, *fns):
    return cp.Period(datetime.fromisoformat(start), datetime.fromisoformat(end),
                     station, tuple(fns))


def rules(night=10, weekend=25, long=0):
    return cp.rules_from_weights(w_night=night, w_weekend_night=weekend,
                                 weekend_days=(4, 5), w_long_duty=long,
                                 long_duty_threshold=720 if long else None)


def priced(periods, *, homes=HOMES, complete=True, soft=None, crew=1,
           kind="weights"):
    return cp.price(0, periods, rules() if soft is None else soft, homes,
                    eligible_bases_complete=complete, crew=crew,
                    criterion_kind=kind)


def catalog_of(pr):
    scope = dict(pr["accounting_scope"])
    scope.pop("periods")
    scope.pop("criterion_kind")
    summary = cp.aggregate_certainty([pr])
    catalog = {"selected": [{"fns": [f for x in pr["accounting_scope"]["periods"]
                                     for f in x["fns"]],
                             "cost": pr["cost"], "price": pr}],
               "ip_objective": pr["cost"], "price_certainty": summary,
               "config_sig": {"price_model": cp.VERSION, "price_scope": scope,
                              "tz_offset_min": scope["tz_offset_min"],
                              "price_criterion": "weights"}}
    report = {"price_model": cp.VERSION, "total_cost": pr["cost"],
              "price_certainty": deepcopy(summary)}
    return catalog, report


# ---------- ночи и область учёта ------------------------------------------

def test_day_return_has_zero_nights_in_one_or_two_chains():
    first = p("2026-09-04T06:00", "2026-09-04T08:00", "SVG", "F1")
    second = p("2026-09-04T20:00", "2026-09-04T22:00", "PLY", "F2")
    whole, left, right = priced([first, second]), priced([first]), priced([second])
    assert whole["nights"] == left["nights"] == right["nights"] == 0
    assert whole["cost"] == left["cost"] + right["cost"] == 0
    # открытый конец у каждой записи — известная часть, не полная цена
    for x in (whole, left, right):
        assert x["certainty"]["complete"] is False and x["full_cost"] is None
        assert [e["reasons"] for e in x["unknown_events"]] == [["open_end"]]


def test_known_internal_night_outside_complete_home_universe():
    seq = [p("2026-09-04T06:00", "2026-09-04T08:00", "OUT", "F1"),
           p("2026-09-05T08:00", "2026-09-05T10:00", "PLY", "F2")]
    got = priced(seq, crew=2)
    assert got["nights"] == 1
    assert got["known_events"][0]["night"] == "2026-09-04"
    assert got["known_events"][0]["station"] == "OUT"
    assert got["cost"] == got["known_subtotal"] == (10 + 25) * 2   # пятница
    assert got["accounting_scope"]["night_definition"] == "calendar_date_transition"
    assert got["accounting_scope"]["eligible_bases"] == sorted(HOMES)
    assert got["accounting_scope"]["eligible_bases_complete"] is True
    # хвост на базе — единственный неизвестный вклад
    assert [e["reasons"] for e in got["unknown_events"]] == [["open_end"]]
    assert got["full_cost"] is None and got["cost_kind"] == "known_subtotal"


def test_internal_station_in_home_universe_is_ambiguous_not_home():
    seq = [p("2026-09-04T06:00", "2026-09-04T08:00", "SVG", "F1"),
           p("2026-09-05T08:00", "2026-09-05T10:00", "PLY", "F2")]
    got = priced(seq)
    assert got["nights"] == got["cost"] == 0
    assert got["full_cost"] is None
    assert got["certainty"] == {"status": "partial", "complete": False, "unknown_count": 2}
    first = got["unknown_events"][0]
    assert first["reasons"] == ["rest_at_base"] and first["night"] == "2026-09-04"
    assert first["families"] == ["calendar_windows", "layover_penalty"]
    assert got["unknown_events"][1]["reasons"] == ["open_end"]
    # форма main той же расшифровки
    assert [(e["kind"], e["station"]) for e in got["uncertain"]] == \
        [("rest_at_base", "SVG"), ("open_end", "PLY")]
    assert got["uncertain"][0]["nights_if_away"] == 1
    assert got["cost_certainty"] == "partial"


@pytest.mark.parametrize("station,complete,reason", [
    ("", True, "missing_location"),
    ("OUT", False, "incomplete_home_universe"),
])
def test_unknown_location_or_home_coverage_is_not_zero(station, complete, reason):
    seq = [p("2026-09-04T06:00", "2026-09-04T08:00", station, "F1"),
           p("2026-09-05T08:00", "2026-09-05T10:00", "PLY", "F2")]
    got = priced(seq, complete=complete)
    assert got["cost"] == 0 and got["full_cost"] is None and got["nights"] == 0
    assert reason in got["unknown_events"][0]["reasons"]
    assert got["uncertain"][0]["kind"] == reason


def test_incomplete_universe_never_proves_a_night_but_complete_does():
    seq = [p("2026-09-04T06:00", "2026-09-04T08:00", "OUT", "F1"),
           p("2026-09-05T08:00", "2026-09-05T10:00", "PLY", "F2")]
    assert cp.layover_nights(seq, HOMES) == []                       # умолчание — не полон
    assert len(cp.layover_nights(seq, HOMES, eligible_bases_complete=True)) == 1
    with pytest.raises(cp.ChainPriceError, match="полным"):
        cp.scope_template((), eligible_bases_complete=True)
    with pytest.raises(cp.ChainPriceError, match="не строка"):
        cp.scope_template("PLY,SVG")


def test_unknown_terminal_never_creates_an_automatic_night():
    seq = [p("2026-09-04T06:00", "2026-09-04T17:30", "SVG", "F1")]
    got = priced(seq)
    assert got["nights"] == got["known_subtotal"] == 0
    assert got["full_cost"] is None and got["cost_kind"] == "known_subtotal"
    assert got["is_lower_bound"] is False
    assert got["unknown_events"][0]["reasons"] == ["open_end"]
    assert got["unknown_events"][0]["fns"] == ["F1"]
    assert cp.layover_nights(seq, {"PLY"}, eligible_bases_complete=True) == []
    assert cp.layover_nights(seq, {"PLY"}, eligible_bases_complete=True, open_end=True) == []
    assert cp.nights_by_period_dates(date(2026, 9, 4), None) is None
    assert cp.nights_by_period_dates(date(2026, 9, 4), None, open_end=True) is None
    assert cp.nights_by_period_dates(date(2026, 9, 4), date(2026, 9, 6)) == 2


def test_explicit_boundary_contracts_are_refused_not_interpreted():
    """P-403 не включать: производителя доказательств продолжения нет —
    метка времени горизонта отдых и место не доказывает, граница
    раньше конца не принимается; любая явная граница — отказ словами."""
    seq = [p("2026-09-04T06:00", "2026-09-04T17:30", "SVG", "F1")]
    for boundary in ({"mode": "closed_scope", "at": "2026-09-05T08:00", "reason": "x"},
                     {"mode": "continuation", "next_start": "2026-09-05T08:00"},
                     {"mode": "closed_scope", "at": "2026-09-04T17:29", "reason": "x"},
                     {"mode": "unknown", "evidence": "скрытое поле"}):
        with pytest.raises(cp.ChainPriceError, match="граница"):
            cp.price(0, seq, rules(), HOMES, eligible_bases_complete=True,
                     boundary=boundary)
    assert cp.scope_template(HOMES, boundary={"mode": "unknown"})["boundary"] == \
        {"mode": "unknown"}


def test_real_night_has_one_owner_when_split_into_two_chains():
    """Одна календарная ночь — один event_id: индекс цепочки и порядок
    периодов в ключ не входят; разрезанная цепочка ночь не доказывает
    (уходит в неизвестное), а не начисляет дважды."""
    first = p("2026-09-04T06:00", "2026-09-04T08:00", "OUT", "F1")
    second = p("2026-09-05T08:00", "2026-09-05T10:00", "PLY", "F2")
    third = p("2026-09-06T08:00", "2026-09-06T10:00", "PLY", "F3")
    whole = priced([first, second])
    assert whole["nights"] == 1 and whole["cost"] == 35
    eid = whole["known_events"][0]["event_id"]
    assert cp.assess_nights([second, first], HOMES, eligible_bases_complete=True)[
        "known_events"][0]["event_id"] == eid
    assert cp.assess_nights([first, second, third], HOMES, eligible_bases_complete=True)[
        "known_events"][0]["event_id"] == eid
    left, right = priced([first]), priced([second])
    assert left["nights"] + right["nights"] == 0 and left["cost"] + right["cost"] == 0
    assert left["unknown_events"][0]["reasons"] == ["open_end"]
    assert right["known_events"] == []


def test_continuous_overnight_duty_does_not_create_rest_night():
    seq = [p("2026-09-04T23:30", "2026-09-05T03:50", "PLY", "F1", "F2")]
    got = priced(seq)
    assert got["nights"] == 0 and got["known_events"] == []
    assert [e["reasons"] for e in got["unknown_events"]] == [["open_end"]]


def test_overlapping_periods_do_not_manufacture_rest():
    seq = [p("2026-09-04T06:00", "2026-09-05T02:00", "OUT", "F1"),
           p("2026-09-04T22:00", "2026-09-05T10:00", "PLY", "F2")]
    got = priced(seq)
    assert got["nights"] == 0
    assert got["unknown_events"][0]["reasons"] == ["overlapping_periods"]
    assert got["uncertain"][0]["kind"] == "overlapping_periods"


def test_duty_only_cost_is_complete_despite_irrelevant_night_unknowns():
    seq = [p("2026-09-04T06:00", "2026-09-04T19:00", "SVG", "F1")]
    got = priced(seq, soft=rules(night=0, weekend=0, long=15))
    assert got["cost"] == got["full_cost"] == 15
    assert got["certainty"]["complete"] and got["unknown_events"] == []
    assert got["cost_certainty"] == "full" and got["uncertain"] == []
    # неразрешённые меры строк с нулевым весом видны в строках
    by = {r["family"]: r for r in got["soft"]}
    assert by["layover_penalty"]["measure_complete"] is False
    assert by["duty_extension_penalty"]["measure_complete"] is True
    assert cp.compare_rows(got, cp.comparison_record(got)) == []
    # P-407: при ставках источника / 0096 стоянки живут в критерии —
    # неопределённость хвоста остаётся у цены при любом весе
    for kind in ("source_rates", "0096"):
        src = priced(seq, soft=rules(night=0, weekend=0, long=15), kind=kind)
        assert src["cost"] == 15 and src["full_cost"] is None
        assert src["certainty"]["complete"] is False
        assert cp.compare_rows(src, cp.comparison_record(src)) == []


def test_normative_duty_identity_not_chain_labels_controls_extension():
    legs = [{"id": "left-chain-leg", "t0": datetime(2026, 9, 4, 6),
             "t1": datetime(2026, 9, 4, 8), "arr": "SVG"},
            {"id": "right-chain-leg", "t0": datetime(2026, 9, 4, 17),
             "t1": datetime(2026, 9, 4, 19), "arr": "PLY"}]
    seq = cp.periods_from_legs([legs])
    got = priced(seq, soft=rules(night=0, weekend=0, long=15))
    assert got["cost"] == 15 and got["soft"][-1]["measure"] == 1


def test_calendar_date_definition_and_timezone_are_part_of_scope():
    seq = [p("2026-09-04T20:00", "2026-09-04T22:30", "OUT", "F1"),
           p("2026-09-05T00:30", "2026-09-05T02:00", "PLY", "F2")]
    utc = cp.price(0, seq, rules(), HOMES, eligible_bases_complete=True)
    shifted = cp.price(0, seq, rules(), HOMES, tz=timedelta(hours=3),
                       eligible_bases_complete=True)
    assert utc["nights"] == 1 and shifted["nights"] == 0
    assert utc["accounting_scope"]["tz_offset_min"] == 0
    assert shifted["accounting_scope"]["tz_offset_min"] == 180
    assert cp.compare_rows(utc, cp.comparison_record(shifted))
    with pytest.raises(cp.ChainPriceError, match="целых минутах"):
        cp.scope_template(HOMES, timedelta(seconds=30))


# ---------- сверка по событиям и области ----------------------------------

def test_equal_numbers_with_different_home_scopes_do_not_reconcile():
    seq = [p("2026-09-04T06:00", "2026-09-04T08:00", "SVG", "F1")]
    a = priced(seq, homes=("PLY", "SVG", "GRT"))
    b = priced(seq, homes=("PLY", "SVG"))
    assert a["cost"] == b["cost"] == 0
    assert any("accounting_scope" in s for s in cp.compare_rows(a, cp.comparison_record(b)))


def test_equal_numbers_with_different_event_addresses_do_not_reconcile():
    a = priced([p("2026-09-04T06:00", "2026-09-04T08:00", "SVG", "F1")])
    b = priced([p("2026-09-04T06:00", "2026-09-04T08:00", "SVG", "F2")])
    assert a["cost"] == b["cost"] == 0
    assert cp.compare_rows(a, cp.comparison_record(b))


def test_hidden_unknown_cannot_be_relabelled_complete_zero():
    original = priced([p("2026-09-04T06:00", "2026-09-04T08:00", "SVG", "F1")])
    forged = deepcopy(original)
    forged.update(unknown_events=[], full_cost=0, cost_kind="complete_scope",
                  uncertain=[], cost_certainty="full",
                  certainty={"status": "complete", "complete": True, "unknown_count": 0})
    issues = cp.compare_rows(forged, cp.comparison_record(original))
    assert any("неопределённые" in s for s in issues)
    assert any("certainty" in s for s in issues)
    assert any("cost_certainty" in s for s in issues)
    # самопроверка ловит подделку и без пересчёта
    assert any("неопределённые" in s for s in cp.compare_rows(forged))
    assert cp.compare_rows(original) == []


def test_matching_partial_subtotal_is_not_confirmed_full_cost():
    """Политика P-417: известная часть сверена — «сошлось» с пометкой;
    полная стоимость не подтверждена; сертификат не отзывается."""
    value = priced([p("2026-09-04T06:00", "2026-09-04T08:00", "SVG", "F1")])
    cat, rep = catalog_of(value)
    result = reconcile.reconcile_pairing(cat, rep, recomputed=[cp.comparison_record(value)])
    assert result["known_subtotal_verdict"] == reconcile.OK
    assert result["verdict"] == reconcile.OK
    assert result["price_complete"] is False and result["full_cost_confirmed"] is False
    assert reconcile.is_clean(result)
    assert "известная часть стоимости пейринга сверена" in result["note"]
    assert "полная стоимость не установлена" in result["note"]
    assert "неизвестных событий/интервалов 1" in result["note"]
    assert result["measure_rows"][0]["verdict"] == reconcile.OK
    assert result["signature_verified"] is True
    assert result["price_certainty"] == cat["price_certainty"]


def test_matching_complete_scope_can_be_confirmed():
    value = priced([p("2026-09-04T06:00", "2026-09-04T19:00", "PLY", "F1")],
                   soft=rules(night=0, weekend=0, long=15))
    assert value["certainty"]["complete"]
    cat, rep = catalog_of(value)
    result = reconcile.reconcile_pairing(cat, rep, recomputed=[cp.comparison_record(value)])
    assert result["verdict"] == reconcile.OK and result["full_cost_confirmed"] is True
    assert result["price_complete"] is True and "note" not in result


def test_numeric_only_reconciliation_is_no_longer_sufficient():
    value = priced([p("2026-09-04T06:00", "2026-09-04T08:00", "SVG", "F1")])
    cat, rep = catalog_of(value)
    no_independent = reconcile.reconcile_pairing(cat, rep)
    assert no_independent["verdict"] == reconcile.INCOMPARABLE
    assert no_independent["known_subtotal_verdict"] == reconcile.INCOMPARABLE
    assert "независимого пересчёта нет" in no_independent["note"]
    numeric_only = {"nights": 0, "measures": {"layover_penalty": 0, "calendar_windows": 0}}
    assert cp.compare_rows(value, numeric_only) == []      # числа не отвергаются…
    only_numbers = reconcile.reconcile_pairing(cat, rep, recomputed=[numeric_only])
    assert only_numbers["verdict"] == reconcile.INCOMPARABLE  # …но области не доказывают
    assert "только числами" in only_numbers["note"]
    short = reconcile.reconcile_pairing(cat, rep, recomputed=[])
    assert short["verdict"] == reconcile.INCOMPARABLE and "адресует 0 цепочек из 1" in short["note"]


def test_stale_model_signature_is_rejected_even_when_totals_match():
    value = priced([p("2026-09-04T06:00", "2026-09-04T08:00", "PLY", "F1")])
    cat, rep = catalog_of(value)
    cat["config_sig"]["price_model"] = "chain-price/1"
    with pytest.raises(cp.ChainPriceError, match="stale"):
        cp.pricing_context_from_signature(cat["config_sig"])
    with pytest.raises(cp.ChainPriceError, match="missing"):
        cp.pricing_context_from_signature({"bases": "PLY"})
    # чужая подпись — «несравнимо» (нет свидетельства), не «расхождение»
    result = reconcile.reconcile_pairing(cat, rep, recomputed=[cp.comparison_record(value)])
    assert result["verdict"] == reconcile.INCOMPARABLE
    assert result["signature_verified"] is False and result["full_cost_confirmed"] is False
    assert "подпись цены" in result["note"] and "stale" in result["note"]


def test_stale_price_breakdown_is_incomparable_not_a_mismatch():
    value = priced([p("2026-09-04T06:00", "2026-09-04T08:00", "PLY", "F1")])
    cat, rep = catalog_of(value)
    old = {"criterion": 0, "crew": 1, "cost": 0, "nights": 0, "soft": [
        {"family": "layover_penalty", "measure": 0, "weight": 10, "contribution": 0}]}
    cat["selected"][0]["price"] = old
    cat["price_certainty"] = cp.aggregate_certainty([old])
    rep["price_certainty"] = deepcopy(cat["price_certainty"])
    assert cat["price_certainty"]["unknown_events"][0]["reasons"] == ["missing_or_stale_price_scope"]
    result = reconcile.reconcile_pairing(cat, rep, recomputed=[cp.comparison_record(value)])
    assert result["verdict"] == reconcile.INCOMPARABLE
    assert "другой модели" in result["note"]
    assert result["measure_rows"][0]["verdict"] == reconcile.INCOMPARABLE
    old["soft"][0]["contribution"] = 1                     # арифметика сломана
    assert reconcile.reconcile_pairing(cat, rep)["verdict"] == reconcile.DIVERGE


def test_signature_home_universe_is_not_inferred_from_arbitrary_cli_bases():
    sig = {"price_model": cp.VERSION, "bases": "PLY", "price_scope": cp.scope_template(
        {"PLY", "SVG"}, eligible_bases_complete=True)}
    ctx = cp.pricing_context_from_signature(sig)
    assert ctx["bases"] == ["PLY", "SVG"] and ctx["eligible_bases_complete"] is True
    assert ctx["tz"] == timedelta(0) and "home_base" not in ctx
    sig["tz_offset_min"] = 60
    with pytest.raises(cp.ChainPriceError, match="пояс"):
        cp.pricing_context_from_signature(sig)
    del sig["tz_offset_min"]
    sig["price_scope"]["extra"] = 1
    with pytest.raises(cp.ChainPriceError, match="скрытые"):
        cp.pricing_context_from_signature(sig)
    del sig["price_scope"]
    with pytest.raises(cp.ChainPriceError, match="scope"):
        cp.pricing_context_from_signature(sig)


def test_soft_rules_from_signature_is_strict_and_carries_rule_ids():
    sig = {"price_model": cp.VERSION, "tz_offset_min": 0, "w_night": 10,
           "price_criterion": "weights",
           "soft_rule_ids": {"layover_penalty": "P-7"},
           "price_scope": cp.scope_template(HOMES, eligible_bases_complete=True)}
    got = cp.soft_rules_from_signature(sig)
    assert got[0]["rule_id"] == "P-7" and got[0]["weight"] == 10
    with pytest.raises(cp.ChainPriceError):
        cp.soft_rules_from_signature({"w_night": 10, "bases": "PLY"})


def test_aggregate_unknowns_must_not_disappear_from_report():
    value = priced([p("2026-09-04T06:00", "2026-09-04T08:00", "SVG", "F1")])
    cat, rep = catalog_of(value)
    rep["price_certainty"]["unknown_events"] = []
    result = reconcile.reconcile_pairing(cat, rep, recomputed=[cp.comparison_record(value)])
    assert result["verdict"] == reconcile.DIVERGE
    assert "отчёт: свод определённости" in result["note"]
    # отсутствующий свод — «несравнимо», не расхождение
    cat2, rep2 = catalog_of(value)
    del rep2["price_certainty"]
    result2 = reconcile.reconcile_pairing(cat2, rep2, recomputed=[cp.comparison_record(value)])
    assert result2["verdict"] == reconcile.INCOMPARABLE and "нет свода" in result2["note"]


def test_aggregate_certainty_keeps_main_summary_and_adds_scope_fingerprint():
    a = priced([p("2026-09-04T06:00", "2026-09-04T08:00", "SVG", "F1")])
    b = priced([p("2026-09-04T06:00", "2026-09-04T19:00", "PLY", "F2")],
               soft=rules(night=0, weekend=0, long=15))
    agg = cp.aggregate_certainty([a, b])
    assert agg["certainty"] == "partial" and agg["complete"] is False
    assert agg["chains"] == 2 and agg["complete_chains"] == 1
    assert agg["chains_with_uncertain"] == 1 and agg["uncertain_events"] == 1
    assert agg["by_kind"] == {"open_end": 1}
    assert agg["unknown_count"] == 1 and agg["unknown_events"][0]["chain"] == 0
    assert agg["known_subtotal"] == 15 and agg["full_cost"] is None
    assert agg["cost_kind"] == "known_subtotal" and len(agg["scope_fingerprint"]) == 64
    stale = cp.aggregate_certainty([{"cost": 5, "price_model": "chain-price/1"}, b])
    assert stale["unknown_events"][0]["reasons"] == ["missing_or_stale_price_scope"]
    assert stale["known_subtotal"] is None and stale["complete"] is False
    assert cp.aggregate_certainty([b])["complete"] is True
    assert cp.aggregate_certainty([b])["full_cost"] == 15


def test_chain_scope_must_match_catalog_signature_scope():
    value = priced([p("2026-09-04T06:00", "2026-09-04T08:00", "SVG", "F1")])
    cat, rep = catalog_of(value)
    cat["config_sig"]["price_scope"] = cp.scope_template(("PLY", "SVG"),
                                                         eligible_bases_complete=True)
    result = reconcile.reconcile_pairing(cat, rep, recomputed=[cp.comparison_record(value)])
    assert result["verdict"] == reconcile.DIVERGE
    assert "область учёта цепочки отличается от подписи каталога" in result["note"]


def test_chain_without_price_breakdown_is_incomparable_not_skipped():
    value = priced([p("2026-09-04T06:00", "2026-09-04T08:00", "SVG", "F1")])
    cat, rep = catalog_of(value)
    cat["selected"].append({"fns": ["F9"], "cost": 0})
    cat["price_certainty"] = cp.aggregate_certainty(c.get("price") for c in cat["selected"])
    rep["price_certainty"] = deepcopy(cat["price_certainty"])
    result = reconcile.reconcile_pairing(cat, rep, recomputed=[cp.comparison_record(value), {}])
    assert result["verdict"] == reconcile.INCOMPARABLE
    assert result["measure_rows"][1]["verdict"] == reconcile.INCOMPARABLE
    assert "цепочка 1: нет расшифровки цены" in result["note"]
    assert result["priced_chains"] == 1


def test_report_with_foreign_price_model_diverges():
    value = priced([p("2026-09-04T06:00", "2026-09-04T08:00", "SVG", "F1")])
    cat, rep = catalog_of(value)
    rep["price_model"] = "chain-price/1"
    result = reconcile.reconcile_pairing(cat, rep, recomputed=[cp.comparison_record(value)])
    assert result["verdict"] == reconcile.DIVERGE and "чужой моделью" in result["note"]
    del rep["price_model"]
    result2 = reconcile.reconcile_pairing(cat, rep, recomputed=[cp.comparison_record(value)])
    assert result2["verdict"] == reconcile.INCOMPARABLE and "без модели цены" in result2["note"]


# ---------- четыре случая шапки сертификата (P-432) -----------------------

def _certificate_case(monkeypatch, *, complete=False, mismatch=False,
                      missing_objective=False):
    from vmp.run import roster_certify as rc
    start, end = datetime(2026, 9, 4, 6), datetime(2026, 9, 4, 8)
    if complete:
        # полная область учёта без режимов границы: цена только за
        # продлённую смену (ночёвка и окно с весом 0 — правила нет)
        end = datetime(2026, 9, 4, 19)
        soft = cp.rules_from_weights(w_long_duty=15, long_duty_threshold=720)
    else:
        soft = cp.rules_from_weights(w_night=10)
    price = cp.price(0, [cp.Period(start, end, "SVG", ("F1",))], soft, {"PLY", "SVG"},
                     eligible_bases_complete=True, criterion_kind="weights")
    scope = dict(price["accounting_scope"])
    scope.pop("periods")
    scope.pop("criterion_kind")
    certainty = cp.aggregate_certainty([price])
    catalog = {"selected": [{"fns": ["F1"], "cost": price["cost"], "price": price}],
               "ip_objective": price["cost"], "price_certainty": certainty,
               "config_sig": {"price_model": cp.VERSION, "price_scope": scope,
                              "tz_offset_min": scope["tz_offset_min"],
                              "price_criterion": "weights"}}
    builder = {"price_model": cp.VERSION,
               "total_cost": price["cost"] + (10 if mismatch else 0),
               "price_certainty": deepcopy(certainty)}
    if missing_objective:
        catalog.pop("ip_objective")
    monkeypatch.setattr(rc, "pairing_scope_svod", lambda _svod: ({"rules": [{"id": "P-HARD"}]}, []))
    monkeypatch.setattr(rc, "pairing_input_from_catalog", lambda *_a: {})
    monkeypatch.setattr(rc, "certify", lambda *_a, **_kw: {
        "certificate": {"checks_run": 1, "findings": 0,
                        "note": "исходная пометка допустимости"}})
    monkeypatch.setattr(rc, "recompute_chain_measures",
                        lambda *_a: [cp.comparison_record(price)])
    return rc.certify_pairing({}, catalog, {}, None, builder_report=builder)["certificate"]


def test_matching_partial_price_keeps_certificate_and_names_known_part(monkeypatch):
    """Политика P-417: сверенная известная часть при неизвестном
    остатке — стоимость «partial», сертификат не отзывается; шапка
    исходная, полнота названа отдельным полем."""
    from vmp import penalty_reconcile as pr_
    cert = _certificate_case(monkeypatch)
    rec = cert["pairing_objective_reconciliation"]
    assert rec["known_subtotal_verdict"] == pr_.OK and rec["verdict"] == pr_.OK
    assert rec["price_complete"] is False and rec["full_cost_confirmed"] is False
    assert cert["result"]["feasibility"] == "confirmed"
    assert cert["result"]["cost"] == "partial"
    assert cert["pairing_objective_clean"] is True and cert["certified"] is True
    assert cert["cost_certainty"] == "partial"
    assert "полная стоимость каталога не установлена" in cert["cost_certainty_note"]
    assert cert["note"] == "исходная пометка допустимости"
    assert "расхождение" not in rec["note"] and "сверена" in rec["note"]


def test_real_mismatch_is_still_explicit_in_pairing_note(monkeypatch):
    from vmp import penalty_reconcile as pr_
    cert = _certificate_case(monkeypatch, mismatch=True)
    assert cert["pairing_objective_reconciliation"]["verdict"] == pr_.DIVERGE
    assert cert["result"]["feasibility"] == "confirmed"
    assert cert["result"]["cost"] == "mismatch" and cert["certified"] is False
    assert "расхождение" in cert["note"] and "стоимость не подтверждена" in cert["note"]
    assert "сверена" not in cert["note"]


def test_missing_evidence_does_not_claim_the_known_subtotal_was_checked(monkeypatch):
    from vmp import penalty_reconcile as pr_
    cert = _certificate_case(monkeypatch, missing_objective=True)
    assert cert["pairing_objective_reconciliation"]["verdict"] == pr_.INCOMPARABLE
    assert cert["result"]["cost"] == "not_checked" and cert["certified"] is False
    assert "известная часть не подтверждена" in cert["note"]
    assert "сверена" not in cert["note"] and "расхождение" not in cert["note"]


def test_complete_matching_cost_preserves_the_original_note(monkeypatch):
    from vmp import penalty_reconcile as pr_
    cert = _certificate_case(monkeypatch, complete=True)
    rec = cert["pairing_objective_reconciliation"]
    assert rec["verdict"] == pr_.OK and rec["full_cost_confirmed"] is True
    assert cert["result"]["cost"] == "confirmed" and cert["certified"] is True
    assert cert["pairing_objective_clean"] is True
    assert cert["note"] == "исходная пометка допустимости"
