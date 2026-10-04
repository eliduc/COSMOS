"""Exact unit arithmetic: P0 3.3/11.3 forbids rounding fractional minutes.

The small explicit map isolates the converter, not catalogue publication or
answer-path integration. All numbers are independent mathematical oracles.
Run without project conftest; no network, services or customer files.
"""
from decimal import Decimal, localcontext

import pytest

from template_catalog import units


@pytest.fixture(autouse=True)
def declared_units(monkeypatch):
    words = {
        "duration_minutes": {"canon": "минута", "form": "integer_nonneg",
                             "words": ["минута", "час", "чч:мм"],
                             "scale": {"минута": 1, "час": 60}},
        "credit_hours": {"canon": "кредитный час", "form": "decimal_nonneg",
                         "words": ["кредитный час"], "scale": {}},
        "time_of_day_minutes": {"canon": "минута", "form": "integer_0_1439_tz",
                                "words": ["минута", "чч:мм"], "scale": {}},
        "count": {"canon": "штука", "form": "integer_nonneg",
                  "words": ["штука"], "scale": {}},
    }
    monkeypatch.setattr(units, "unit_words", lambda source=None: words)


@pytest.mark.parametrize("precision", [6, 28, 50])
@pytest.mark.parametrize("value,word", [
    ("0." + "9" * 60, "час"),
    ("0.1" + "0" * 60 + "1", "час"),
    ("600." + "0" * 60 + "1", "минута"),
    ("600." + "0" * 60 + "1", "duration_minutes"),
])
def test_fractional_minutes_cannot_round_to_an_integer(precision, value, word):
    with localcontext() as context:
        context.prec = precision
        with pytest.raises(units.UnitsError, match="целый"):
            units.to_canonical(value, word, "duration_minutes")


@pytest.mark.parametrize("precision", [6, 28, 50])
@pytest.mark.parametrize("value,word,expected", [
    ("10,5", "час", 630),
    ("0.1", "час", 6),
    ("10:30", "чч:мм", 630),
    ("123456789012345678901234567890", "минута", 123456789012345678901234567890),
])
def test_whole_canonical_values_do_not_depend_on_decimal_context(precision, value, word, expected):
    with localcontext() as context:
        context.prec = precision
        assert units.to_canonical(value, word, "duration_minutes") == expected


@pytest.mark.parametrize("value", [Decimal("1e-1000"), Decimal("1" + "0" * 400 + ".5")])
def test_decimal_output_cannot_silently_underflow_or_overflow(value):
    with pytest.raises(units.UnitsError):
        units.to_canonical(value, "credit_hours", "credit_hours")


@pytest.mark.parametrize("value,word,target", [
    (True, "минута", "duration_minutes"),
    (-1, "минута", "duration_minutes"),
    ("10:90", "чч:мм", "duration_minutes"),
    ("24:00", "чч:мм", "time_of_day_minutes"),
    ("1.5", "штука", "count"),
    ("10 часов (600 минут)", "час", "duration_minutes"),
    (float("inf"), "минута", "duration_minutes"),
    (Decimal("sNaN"), "минута", "duration_minutes"),
])
def test_existing_numeric_refusals_survive_exact_arithmetic(value, word, target):
    with pytest.raises(units.UnitsError):
        units.to_canonical(value, word, target)


@pytest.mark.parametrize("value,word,target,expected", [
    ("23:59", "чч:мм", "time_of_day_minutes", 1439),
    ("10.5", "credit_hours", "credit_hours", 10.5),
    ("0.125", "credit_hours", "credit_hours", 0.125),
    (0, "штука", "count", 0),
])
def test_other_numeric_forms_keep_their_public_values(value, word, target, expected):
    assert units.to_canonical(value, word, target) == expected
