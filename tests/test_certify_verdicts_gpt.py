# -*- coding: utf-8 -*-
"""Трёхчастный результат сертификата — гарантии единой версии 06.09.2026
(строки C-1, C-2, C-3; тесты GPT второго дерева, адаптированные к
словарю main со значением «partial»)."""
from __future__ import annotations

import pytest

from vmp import penalty_reconcile as pr
from vmp.run import verdicts as vd


def _cert(**extra):
    base = {"certified": True, "checks_run": 3, "findings": 0,
            "rules_uncovered": [], "checks_not_compiled": 0,
            "rules_partially_covered": [],
            "pairing_objective_reconciliation": {"verdict": pr.OK}}
    base.update(extra)
    return base


def test_matching_partial_subtotal_is_not_confirmed_full_cost():
    """C-1: сверка сошлась, но оценка области учёта не заявляет полноты
    (cost_assessment.complete ≠ True) — стоимость partial словами, не
    «подтверждена» и не «не проверена»; cost_certainty main — тот же итог."""
    partial = vd.result_of_certificate(_cert(cost_assessment={"complete": False, "known_nights": 1}))
    assert partial["cost"] == "partial"
    assert partial["feasibility"] == "confirmed"
    assert "полная стоимость не установлена" in vd.result_line(partial)
    assert vd.result_of_certificate(_cert(cost_certainty="partial"))["cost"] == "partial"
    # complete = True либо оценки нет — подтверждена
    assert vd.result_of_certificate(_cert(cost_assessment={"complete": True}))["cost"] == "confirmed"
    assert vd.result_of_certificate(_cert())["cost"] == "confirmed"
    # partial — не основание отозвать сертификат (политика P-417)
    assert vd.certified_of(_cert(cost_certainty="partial"), partial) is True
    assert vd.verdict_words(partial) == "допустимо"


def test_unknown_windows_make_feasibility_not_established():
    """C-2: неизвестные окна проверок (window_coverage.unknown > 0) —
    допустимость не установлена и покрытие неполное, даже если перечень
    частично покрытых правил пуст; нарушение сильнее пробела."""
    cert = _cert(window_coverage={"expected": 5, "evaluated": 3, "unknown": 2})
    res = vd.result_of_certificate(cert, optimality="feasible_found")
    assert res["feasibility"] == "not_established"
    assert res["coverage_complete"] is False
    assert res["optimality"] == "not_proven"
    assert vd.certified_of(cert, res) is False
    assert vd.verdict_words(res, cert) == "не удостоверено: сертификат допустимости не выдан"
    violated = vd.result_of_certificate(dict(cert, findings=1))
    assert violated["feasibility"] == "violated"
    # нулевое либо нечитаемое поле — не пробел
    assert vd.result_of_certificate(_cert(window_coverage={"unknown": 0}))["feasibility"] == "confirmed"
    assert vd.result_of_certificate(_cert(window_coverage={"unknown": "x"}))["feasibility"] == "confirmed"
    assert vd.window_unknown_of(_cert(window_coverage=None)) == 0


def test_catalog_optimum_is_not_downgraded_but_full_price_optimality_is_named():
    """C-3 (GPT-011): оптимум каталога по объявленной числовой цели
    доказывается независимо от неизвестного остатка цены — без blanket-
    downgrade; рядом full_price_optimality = not_proven и область
    доказанного словами."""
    partial = vd.result_of_certificate(
        _cert(cost_certainty="partial", objective_signature={"price_scope": "nights"}),
        optimality=vd.pairing_optimality("OPTIMAL"))
    assert partial["optimality"] == "catalog_optimum"
    assert partial["cost"] == "partial"
    assert partial["full_price_optimality"] == "not_proven"
    assert partial["optimality_scope"] == vd.OPTIMALITY_SCOPE_CATALOG
    assert partial["objective_signature"] == {"price_scope": "nights"}
    line = vd.result_line(partial)
    assert "оптимизация: оптимум каталога" in line
    assert "оптимальность по полной цене не доказана" in line
    full = vd.result_of_certificate(_cert(), optimality=vd.pairing_optimality("OPTIMAL"))
    assert full["full_price_optimality"] == "proven"
    assert "не доказана" not in vd.result_line(full)
    # у допустимого решения без доказательства качества признака нет
    plain = vd.result_of_certificate(_cert(), optimality="feasible_found")
    assert "full_price_optimality" not in plain
    with pytest.raises(ValueError):
        vd.validate_result(dict(full, full_price_optimality="maybe"))
