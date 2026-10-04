"""Compare claimed versus read numbers without truncation or type coercion.

Synthetic report fragments exercise the actual comparison, not a live solver
or a package acceptance. Missing readings keep their existing refusal.
"""
import copy

import pytest

from webapp import threshold_gate as tg


def compare_reading(actual, claimed):
    pkg = {"model": {"rules": []}}
    readings = {"threshold": actual}
    applied = [{"threshold": "threshold", "value": claimed,
                "fromParams": ["parameter"], "rules": ["R-1"]}]
    before = copy.deepcopy((pkg, readings, applied))
    result = tg.compare(pkg, readings, mapping={"parameter": "threshold"},
                        applied=applied)
    assert (pkg, readings, applied) == before
    return result


@pytest.mark.parametrize(("actual", "claimed"), [(5.75, 5.25), (5.5, 5), (5, 5.5)])
def test_different_numbers_with_same_integer_part_do_not_agree(actual, claimed):
    row, = compare_reading(actual, claimed)
    assert row["verdict"] == tg.DIVERGE
    assert row["solver_value"] == actual and row["package_values"] == [claimed]
    assert row["rules"] == ["R-1"]


@pytest.mark.parametrize(("actual", "claimed"), [(5, 5), (0, 0), (5.25, 5.25)])
def test_equal_finite_numbers_preserve_the_claim_reading_check(actual, claimed):
    assert compare_reading(actual, claimed) == []


@pytest.mark.parametrize("value", [True, "5", float("nan"), float("inf"), [], {}])
@pytest.mark.parametrize("field", ["actual", "claimed"])
def test_non_numeric_or_nonfinite_evidence_is_an_addressed_refusal(value, field):
    values = {"actual": 5, "claimed": 5}
    values[field] = value
    with pytest.raises(tg.ThresholdGateError, match="threshold"):
        compare_reading(**values)


def test_missing_reading_does_not_turn_an_applied_claim_into_evidence():
    row, = compare_reading(None, 5)
    assert row["verdict"] == tg.DIVERGE and row["solver_value"] is None


def compare_group_reading(actual, *, relation="max", want=720, off=None):
    """Exercise the package-versus-provider path without an applied claim."""
    pkg = {"model": {"rules": [{"id": "R-GROUP", "kind": "HARD", "params": [
        {"name": "max_duty_duration", "value": want,
         "unit": "duration_minutes", "relation": relation}]}]}}
    readings = {"max_duty_span_min": actual}
    before = copy.deepcopy((pkg, readings))
    result = tg.compare(pkg, readings,
                        mapping={"max_duty_duration": "max_duty_span_min"},
                        off_literals={"max_duty_span_min": off})
    assert (pkg, readings) == before
    return result


@pytest.mark.parametrize("relation", ["max", "min", "eq"])
def test_group_reading_does_not_truncate_a_difference(relation):
    row, = compare_group_reading(720.75, relation=relation)
    assert row["verdict"] == tg.DIVERGE
    assert row["solver_value"] == 720.75 and row["package_values"] == [720]
    assert "0.75" in row["note"]


def test_group_fractional_reading_is_not_the_zero_off_literal():
    row, = compare_group_reading(0.75, want=1, off=0)
    assert row["verdict"] == tg.DIVERGE and "ВЫКЛЮЧЕНО" not in row["note"]
    assert "0.25" in row["note"]


def test_group_numerically_equal_float_preserves_exact_agreement():
    row, = compare_group_reading(720.0)
    assert row["verdict"] == tg.MATCH


@pytest.mark.parametrize("value", [True, "720", float("nan"), float("inf"), [], {}])
def test_group_malformed_reading_without_applied_claim_is_addressed(value):
    with pytest.raises(tg.ThresholdGateError, match="max_duty_span_min"):
        compare_group_reading(value)


@pytest.mark.parametrize("value", [True, "0", float("nan"), float("inf"), [], {}])
def test_group_malformed_off_literal_is_addressed(value):
    with pytest.raises(tg.ThresholdGateError, match="max_duty_span_min"):
        compare_group_reading(720, off=value)
