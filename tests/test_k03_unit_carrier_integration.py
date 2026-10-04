"""Canonical carrier conversion uses the selected 0.6 unit map, not K02 tables."""
from decimal import Decimal, localcontext

import pytest

from cdpm import catalog_bridge as cb, family_params as fp
from template_catalog import units
from test_k03_unit_vocabulary import selected, vocabulary, attach
from test_catalog_060_versioned_schema import modern_docs
from test_template_catalog_validate import _broken, CATALOG


@pytest.fixture(autouse=True)
def clean():
    yield
    cb.forget()


@pytest.mark.parametrize("value,word,target,expected", [
    (3, "суток", "count", Decimal(3)),
    (3, "посадок", "count", Decimal(3)),
    ("1.5", "кредитных часов", "credit_hours", Decimal("1.5")),
    ("0.5", "кредитных часов на час", "credit_hours_per_hour", Decimal("0.5")),
    ("10,5", "Ч.", "duration_minutes", Decimal(630)),
    (600, None, "duration_minutes", Decimal(600)),
    ("23:59", None, "time_of_day_minutes", Decimal(1439)),
    (3, "   ", "count", Decimal(3)),
    ("23:59", "чч:мм", "time_of_day_minutes", Decimal(1439)),
    ("10 ч 30 мин", None, "duration_minutes", Decimal(630)),
    ("10 часов", None, "duration_minutes", Decimal(600)),
])
def test_modern_carrier_uses_catalogue_words(tmp_path, monkeypatch, value, word, target, expected):
    selected(tmp_path, monkeypatch)
    got, problem = fp.canonical_value(value, word, target)
    assert problem is None and got == expected


@pytest.mark.parametrize("value,word,target", [
    ("0." + "9" * 60, "час", "duration_minutes"),
    ("1.5", "count", "count"),
    (0, "hours_per_credit_hour", "hours_per_credit_hour"),
    (1440, "time_of_day_minutes", "time_of_day_minutes"),
    (True, "count", "count"),
    ({"SVO": 3}, "count", "count"),
    (1, "неделя", "duration_minutes"),
    (1, "час", "count"),
    ("50 минут", "час", "duration_minutes"),
    (3, "чч:мм", "duration_minutes"),
    ("10:30", "час", "duration_minutes"),
    ("−1", None, "duration_minutes"),
    ("Для базы SVO 10 часов", None, "duration_minutes"),
    ("1. 10 часов", None, "duration_minutes"),
])
def test_modern_carrier_refuses_wrong_form_or_unit(tmp_path, monkeypatch, value, word, target):
    selected(tmp_path, monkeypatch)
    got, problem = fp.canonical_value(value, word, target)
    assert got is None and problem


def test_carrier_calls_the_shared_converter_after_healthy_control(tmp_path, monkeypatch):
    selected(tmp_path, monkeypatch)
    assert fp.canonical_value(10, "час", "duration_minutes") == (Decimal(600), None)
    calls = []
    def refusing(value, word, target, **kwargs):
        calls.append((value, word, target))
        raise units.UnitsError("independent shared-converter sentinel")
    monkeypatch.setattr(units, "to_canonical", refusing)
    got, problem = fp.canonical_value(10, "час", "duration_minutes")
    assert got is None and "sentinel" in problem and calls


def test_modern_scale_and_carrier_follow_a_declared_word_not_local_constants(tmp_path, monkeypatch):
    def prepare(docs):
        modern_docs(docs)
        words = vocabulary()
        entry = words["unit_words"]["duration_minutes"]
        entry["words"].append("четверть часа")
        entry["scale"]["четверть часа"] = 15
        updated = attach(docs, words)
        docs.clear()
        docs.update(updated)
    folder = _broken(tmp_path, prepare)
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    cb.forget()
    assert fp.unit_scale("четверть часа", "duration_minutes") == Decimal(15)
    assert fp.canonical_value(2, "четверть часа", "duration_minutes") == (Decimal(30), None)


@pytest.mark.parametrize("precision", [6, 28, 50])
def test_exact_typed_decimal_is_not_rounded_at_shared_boundary(tmp_path, monkeypatch, precision):
    cat = selected(tmp_path, monkeypatch)
    raw = Decimal("1.123456789012345678901234567890123456789")
    with localcontext() as ctx:
        ctx.prec = precision
        assert units.to_canonical(raw, "credit_hours", "credit_hours", source=cat.path, exact=True) == raw
        assert fp.canonical_value(raw, "credit_hours", "credit_hours") == (raw, None)


def test_legacy_carrier_boundary_remains_unchanged(monkeypatch):
    monkeypatch.setenv(cb.ENV_CATALOG, str(CATALOG))
    cb.forget()
    assert fp.canonical_value(10, "час", "duration_minutes") == (Decimal(600), None)
    assert fp.canonical_value(3, "суток", "count")[1]


@pytest.mark.parametrize("value,word", [(3, "чч:мм"), ("10:30", "час")])
def test_clock_word_is_a_format_not_an_implicit_scale(tmp_path, monkeypatch, value, word):
    cat = selected(tmp_path, monkeypatch)
    with pytest.raises(units.UnitsError):
        units.to_canonical(value, word, "duration_minutes", source=cat.path)


@pytest.mark.parametrize("value,word,target,expected", [
    (True, None, "flag", True), (False, "flag", "flag", False),
    ("Boeing 737 MAX", None, "code", "Boeing 737 MAX"),
    (["B738", "A320/321"], "code_list", "code_list", "A320/321, B738"),
])
def test_modern_non_numeric_carriers_keep_their_typed_law(tmp_path, monkeypatch, value, word, target, expected):
    selected(tmp_path, monkeypatch)
    def forbidden(*args, **kwargs):
        raise AssertionError("typed code/flag must not enter numeric converter")
    monkeypatch.setattr(units, "to_canonical", forbidden)
    assert fp.canonical_value(value, word, target) == (expected, None)


def test_modern_unknown_target_has_no_legacy_identity_fallback(tmp_path, monkeypatch):
    selected(tmp_path, monkeypatch)
    assert fp.unit_scale(None, "invented") is None
    assert fp.canonical_value(1, None, "invented")[1]


def test_real_reconciliation_and_coverage_use_catalogue_carriers(tmp_path, monkeypatch):
    from cdpm import model
    from test_cdpm_a05_carriers_barrier import _landed, _legacy, _doc_with
    cat = selected(tmp_path, monkeypatch)
    rule = _landed(cells={"max_pairing_days": {"value": 3, "unit": "суток", "quote": "3 суток"}},
                   legacy=[_legacy("max_pairing_days", 3)], rev=cat.fingerprint())
    assert model.reconcile_carriers(rule) == []
    coverage = fp.family_coverage(_doc_with(rule))["duty_limits"]
    assert "max_pairing_days" in coverage["given"]
    assert "max_pairing_days" not in coverage["unreadable"]
    rule["params"][0]["value"] = 30
    assert model.reconcile_carriers(rule)
