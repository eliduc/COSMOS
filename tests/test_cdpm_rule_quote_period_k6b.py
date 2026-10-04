"""K-6b: a source-rule period is not the scalar's unit or a company answer.

Literal expected magnitudes and real seven writer paths; no product judge
is replaced. Reuses only the exact read-only draft fixture from count K-6.
"""
import copy

import pytest

from template_catalog import family_contract
from cdpm import extract, family_params as fp, landing_migration, model, repair
from test_cdpm_count_rule_quote_k6 import draft, FINGERPRINT, ROUTES


FAMILY = "balanced_workload"
PARAM = "workload_spread_threshold"
QUOTE = "сверх порога существенности 10 кредитных часов за месяц"


def _attempt(cat, route, quote, value=10):
    typed = route.endswith("_typed")
    missing = route.startswith(("repair_", "extract_"))
    cell = {"value": value, "quote": quote,
            "source": {"kind": "rule", "rule_id": "R-K6B-CREDIT"}}
    fit = {"category": "A", "family": FAMILY, "registry_rev": cat.fingerprint(),
           "params": {} if missing else {PARAM: cell}}
    if typed:
        fit.update(schema_version="landing/2",
                   family_contract=family_contract.snapshot(cat, FAMILY, [PARAM]))
        cell.update(unit="credit_hours", relation="max", condition=None)
    if route == "candidate_legacy":
        cell.pop("source")
    rule = {"id": "R-K6B-CREDIT", "title": "Порог разброса",
            "statement": quote, "zone": "BOTH", "kind": "SOFT",
            "sources": [{"sid": "S1", "anchor": "строка 6"}],
            "template_fit": fit}
    doc = {"meta": {"customer": "K6b synthetic", "mode": 1,
                    "cdpm_version": "0.1", "revision": 2},
           "sources": [{"sid": "S1", "file": "synthetic.md", "sha256": "0" * 64}],
           "rules": [rule], "questions": [], "objectives": []}
    before = copy.deepcopy(doc)
    if route.startswith("candidate_"):
        got, _, why = model.accept_fit_candidate(rule, quote, new_output=True)
        assert doc == before
        return got.get("category") == "A", got, why
    if route == "migration":
        got = landing_migration.prepare_rule(rule, cat, document=doc)
        assert doc == before
        return got["state"] == "confirmed", got["rule"]["template_fit"], got["words"]
    if route.startswith("repair_"):
        reply = {"value": value, "quote": quote}
        saved = copy.deepcopy(reply)
        why = repair._apply(rule, False, "param:" + PARAM, reply, quote, doc)
        assert reply == saved
    else:
        addition = {"name": PARAM, "value": value, "relation": "max", "as_written": quote}
        saved = copy.deepcopy(addition)
        why = extract.merge_soft_params(rule, [addition])
        assert addition == saved
    if why:
        assert doc == before, "Rejected evidence must not partially mutate a rule"
    return why is None, rule["template_fit"], why


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("quote", [QUOTE, "За месяц порог — 10 кредитных часов"])
def test_actual_period_source_reaches_each_writer(draft, route, quote):
    assert fp.parse_value(quote, "credit_hours") is None
    accepted, fit, why = _attempt(draft, route, quote)
    assert accepted, (route, quote, why)
    cell = fit["params"][PARAM]
    assert cell["value"] == 10 and cell["quote"] == quote
    if "unit" in cell:
        assert cell["unit"] == "credit_hours" and cell["relation"] == "max"
    if "family_contract" in fit:
        assert fit["family_contract"]["catalog_fingerprint"] == FINGERPRINT
    assert fp.parse_value(quote, "credit_hours") is None


@pytest.mark.parametrize("route", ROUTES)
def test_unqualified_credit_remains_healthy(draft, route):
    accepted, fit, why = _attempt(draft, route, "10 кредитных часов")
    assert accepted, (route, why)
    assert fit["params"][PARAM]["value"] == 10


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("quote", [
    "10 месяцев", "10 минут", "10 %", "10 пилотов за месяц",
    "10 кредитных часов за 2 месяца", "10 кредитных часов за месяц, ещё 12 часов",
    "от 10 до 12 кредитных часов за месяц", "-10 кредитных часов за месяц",
    "не 10 кредитных часов за месяц", "около 10 кредитных часов за месяц",
    "10 кредитных часов за месяц только для базы SVO",
    "10 кредитных часов за месяц. Не применяется",
])
def test_foreign_extra_ambiguous_or_conditional_quote_stays_rejected(draft, route, quote):
    accepted, _fit, why = _attempt(draft, route, quote)
    assert not accepted and why, (route, quote)


@pytest.mark.parametrize("raw,unit,want", [
    ("720 минут за смену", "duration_minutes", 720),
    ("За смену — 12 часов", "duration_minutes", 720),
    ("10 ч 30 мин за смену", "duration_minutes", 630),
    ("2,5 кредитного часа за месяц", "credit_hours", 2.5),
    ("2 часа на кредитный час", "hours_per_credit_hour", 2),
    ("2 часа на кредитный час за месяц", "hours_per_credit_hour", 2),
    ("Начало смены в 12:30", "time_of_day_minutes", 750),
    (QUOTE, "credit_hours", 10),
])
def test_bounded_source_period_and_declared_ratio_are_not_foreign_units(draft, raw, unit, want):
    assert fp.parse_rule_quote(raw, unit, source=draft.path) == (want, raw)


@pytest.mark.parametrize("raw,unit", [
    ("720 за смену", "duration_minutes"),
    ("2,5 минуты за смену", "duration_minutes"),
    ("720 месяцев за смену", "duration_minutes"),
    ("10 минут за месяц", "credit_hours"),
    ("2 пилота за месяц", "credit_hours"),
    ("2 пилота в месяц", "count"),
    ("10 кредитных часов за месяц, 12", "credit_hours"),
    ("2 часа на 2 кредитных часа", "hours_per_credit_hour"),
    ("24:00 за смену", "time_of_day_minutes"),
])
def test_period_mask_cannot_hide_value_unit_digits_or_existing_count_guard(draft, raw, unit):
    assert fp.parse_rule_quote(raw, unit, source=draft.path) is None


def test_source_domain_and_raw_evidence_are_preserved(draft):
    raw = "  " + QUOTE + "  "
    assert fp.parse_rule_quote(raw, "credit_hours", source=draft.path) == (10, raw)
    assert fp.parse_rule_quote(raw, "credit_hours", {"min": 11}, source=draft.path) is None
    assert fp.parse_rule_quote(raw, "credit_hours", {"max": 9}, source=draft.path) is None
    assert fp.parse_value(raw, "credit_hours") is None


def test_noncount_unit_vocabulary_is_bound_to_explicit_origin(draft, tmp_path, monkeypatch):
    import template_catalog as tc
    from cdpm import catalog_bridge as cb
    from test_catalog_060_versioned_schema import modern_docs
    from test_template_catalog_validate import _broken

    def other_word(docs):
        modern_docs(docs)
        docs["dictionaries.json"]["unit_words"]["credit_hours"]["words"].append("жетон")

    other = tc.current(_broken(tmp_path, other_word), validated=True)
    raw = "10 жетон за месяц"
    assert other.fingerprint() != draft.fingerprint()
    assert fp.parse_rule_quote(raw, "credit_hours", source=draft.path) is None
    assert fp.parse_rule_quote(raw, "credit_hours", source=other.path) == (10, raw)
    monkeypatch.setenv(cb.ENV_CATALOG, str(other.path))
    cb.forget()
    assert fp.parse_rule_quote(raw, "credit_hours") == (10, raw)
    assert fp.parse_rule_quote(raw, "credit_hours", source=draft.path) is None
    assert fp.parse_value(raw, "credit_hours") is None
