# -*- coding: utf-8 -*-
"""Формулировки шапки сертификата пейринга различают неизвестную полную
стоимость и расхождение (тесты GPT второго дерева, перенос по смыслу в
единую версию 06.09.2026, строка C-20; политика H11/P-417: совпавшая
известная часть при неизвестном остатке — partial, не расхождение и не
certified = False; настоящее расхождение — DIVERGE, красный).

Сертификатор жёстких правил изолирован; сверка целевой и вердикты —
настоящие (vmp.penalty_reconcile.reconcile_pairing, vmp.run.verdicts).
Каталог — по контракту Б (группа P, P-424): подпись с областью учёта
цены, расшифровка каждой цепочки, свод определённости; суммы без
области учёта сверка честно зовёт «несравнимо». Носитель «известная
часть сверена» — known_subtotal_verdict сверки (P-424), а не совпавшие
ночёвки меры сертификата: при отсутствующем обязательном свидетельстве
(ip_objective) известная часть не подтверждена, даже если доказанные
ночёвки сертификата и пересчёта сошлись.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

import pytest

from vmp import chain_price as cp
from vmp import penalty_reconcile as pr
from vmp.run import roster_certify as rc


def _report(cert):
    """Минимальный отчёт контракта 3.3 вокруг шапки — для рендера."""
    return {"customer": "стенд", "sources": [], "checks": [], "findings": [],
            "counters": {"checks": 0, "checks_run": 0, "checks_not_run": 0, "findings": 0},
            "certificate": cert}


@pytest.fixture
def certificate_case(monkeypatch):
    def case(*, complete=False, mismatch=False, missing_objective=False,
             nights_mismatch=False):
        # Одна цепочка из одной смены на SVG; универсум приписок полон.
        # complete: цена только за продлённую смену (семей ночёвок нет —
        # открытый конец цену неполной не делает); иначе — ночёвка с
        # весом, открытый конец цепочки — неопределённый вклад (1).
        start, end = datetime(2026, 9, 4, 6), datetime(2026, 9, 4, 8)
        if complete:
            end = datetime(2026, 9, 4, 19)
            soft = cp.rules_from_weights(w_long_duty=15, long_duty_threshold=720)
        else:
            soft = cp.rules_from_weights(w_night=10)
        price = cp.price(0, [cp.Period(start, end, "SVG", ("F1",))], soft,
                         {"PLY", "SVG"}, eligible_bases_complete=True,
                         criterion_kind="weights")
        scope = dict(price["accounting_scope"])
        scope.pop("periods")
        scope.pop("criterion_kind")
        certainty = cp.aggregate_certainty([price])
        catalog = {"selected": [{"fns": ["F1"], "cost": price["cost"], "price": price,
                                 "duty_fns": [["F1"]]}],
                   "ip_objective": price["cost"], "price_certainty": certainty,
                   "config_sig": {"price_model": cp.VERSION, "price_scope": scope,
                                  "tz_offset_min": scope["tz_offset_min"],
                                  "price_criterion": "weights"}}
        builder = {"price_model": cp.VERSION,
                   "total_cost": price["cost"] + (10 if mismatch else 0),
                   "price_certainty": deepcopy(certainty)}
        if missing_objective:
            catalog.pop("ip_objective")
        scoped_rules = [{"id": "P-HARD"}]
        if nights_mismatch:
            # Мягкое правило ночёвок в срезе — мера сертификата (0 ночей
            # по пустым находкам) сверяется с пересчётом (1 ночь).
            scoped_rules.append({"id": "R-NIGHT", "kind": "SOFT",
                                 "template_fit": {"category": "A",
                                                  "family": "layover_penalty"}})
        monkeypatch.setattr(rc, "pairing_scope_svod", lambda _svod: (
            {"rules": scoped_rules}, []))
        monkeypatch.setattr(rc, "pairing_input_from_catalog", lambda *_a: {})
        monkeypatch.setattr(rc, "certify", lambda *_a, **_kw: {
            "certificate": {"checks_run": 1, "findings": 0,
                            "note": "исходная пометка допустимости"},
            "checks": [], "findings": []})
        # Независимый пересчёт (запись сверки): доказанных ночёвок нет, а
        # неопределённый вклад (открытый конец цепочки) — есть либо нет.
        record = cp.comparison_record(price)
        if nights_mismatch:
            record = dict(record, nights=1)
        monkeypatch.setattr(rc, "recompute_chain_measures", lambda *_a: [record])
        return rc.certify_pairing({}, catalog, {}, None,
                                  builder_report=builder)["certificate"]

    return case


def test_matching_partial_price_note_does_not_claim_a_mismatch(certificate_case):
    cert = certificate_case()
    rec = cert["pairing_objective_reconciliation"]
    assert rec["verdict"] == pr.OK
    assert rec["known_subtotal_verdict"] == pr.OK
    assert rec["price_complete"] is False and rec["full_cost_confirmed"] is False
    assert cert["cost_certainty"] == "partial"
    assert cert["result"]["feasibility"] == "confirmed"
    # Политика P-417: известная часть сверена, полная не установлена —
    # partial словами, сертификат не отзывается.
    assert cert["result"]["cost"] == "partial"
    assert cert["pairing_objective_clean"] is True and cert["certified"] is True
    assert "полная стоимость каталога не установлена" in cert["cost_certainty_note"]
    assert "расхождение" not in cert["note"] and "расходится" not in cert["note"]
    assert "не сошлась" not in cert["note"]
    assert "известная часть стоимости пейринга сверена" in rec["note"]
    from vmp.run import verdicts as vd
    assert "полная стоимость не установлена" in vd.result_line(cert["result"])


def test_real_mismatch_is_still_explicit_in_pairing_note(certificate_case):
    cert = certificate_case(mismatch=True)
    assert cert["pairing_objective_reconciliation"]["verdict"] == pr.DIVERGE
    assert cert["result"]["feasibility"] == "confirmed"
    assert cert["result"]["cost"] == "mismatch" and cert["certified"] is False
    # Журнал кнопки читает первые 200 знаков — оба факта обязаны уцелеть.
    excerpt = cert["note"][:200]
    assert "расхождение" in excerpt and "стоимость не подтверждена" in excerpt
    assert "сверена" not in cert["note"]
    md = rc.render_certificate(_report(cert))
    assert "ДОПУСТИМОСТЬ ПОДТВЕРЖДЕНА" in md and "сертификат не выдан: стоимость: расхождение" in md


def test_missing_evidence_does_not_claim_the_known_subtotal_was_checked(certificate_case):
    cert = certificate_case(missing_objective=True, complete=True)
    assert cert["pairing_objective_reconciliation"]["verdict"] == pr.INCOMPARABLE
    assert cert["result"]["cost"] == "not_checked" and cert["certified"] is False
    assert "известная часть не подтверждена" in cert["note"][:200]
    assert "сверена" not in cert["note"] and "расхождение" not in cert["note"]
    md = rc.render_certificate(_report(cert))
    assert "сертификат не выдан: стоимость: не проверена" in md


def test_known_part_matched_but_total_incomparable_is_named_as_such(certificate_case):
    """Совпавшие доказанные ночёвки меры и пересчёта при отсутствующем
    ip_objective: полная стоимость не установлена (partial словами), но
    известная часть НЕ сверена — обязательного свидетельства нет
    (P-424); совпадение ночёвок расхождением не называется."""
    cert = certificate_case(missing_objective=True)
    rec = cert["pairing_objective_reconciliation"]
    assert rec["verdict"] == pr.INCOMPARABLE
    assert rec["known_subtotal_verdict"] == pr.INCOMPARABLE
    assert cert["cost_certainty"] == "partial"
    assert rec["certificate_nights"] == rec["recomputed_nights"] == 0
    excerpt = cert["note"][:200]
    assert "полная стоимость пейринга не установлена" in excerpt
    assert "известная часть не подтверждена" in excerpt
    assert "сверена" not in cert["note"]
    assert "расхождение" not in cert["note"]
    assert cert["result"]["cost"] == "not_checked" and cert["certified"] is False


def test_complete_matching_cost_preserves_the_original_note(certificate_case):
    cert = certificate_case(complete=True)
    rec = cert["pairing_objective_reconciliation"]
    assert rec["verdict"] == pr.OK and rec["full_cost_confirmed"] is True
    assert cert["cost_certainty"] == "full"
    assert cert["result"]["cost"] == "confirmed" and cert["certified"] is True
    assert cert["pairing_objective_clean"] is True
    assert cert["note"] == "исходная пометка допустимости"


def test_night_count_divergence_marks_known_part_and_full_cost_consistently(certificate_case):
    """P-432 (ревью группы P): расхождение числа доказанных ночёвок меры
    сертификата и пересчёта — расхождение известной части, а не только
    итога: verdict, known_subtotal_verdict и full_cost_confirmed
    выставляются согласованно, шапка — красная."""
    cert = certificate_case(nights_mismatch=True)
    rec = cert["pairing_objective_reconciliation"]
    assert rec["certificate_nights"] == 0 and rec["recomputed_nights"] == 1
    assert rec["verdict"] == pr.DIVERGE
    assert rec["known_subtotal_verdict"] == pr.DIVERGE
    assert rec["full_cost_confirmed"] is False
    assert any("доказанных ночёвок" in p for p in rec["problems"])
    assert cert["pairing_objective_clean"] is False and cert["certified"] is False
    assert cert["result"]["cost"] == "mismatch"
    excerpt = cert["note"][:200]
    assert "расхождение" in excerpt and "стоимость не подтверждена" in excerpt
    assert "сверена" not in cert["note"]
