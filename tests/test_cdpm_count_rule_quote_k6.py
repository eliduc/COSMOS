"""K-6: source-count witnesses do not widen company-answer grammar.

Uses the pinned, explicit draft only as read-only data. No exporter, solver,
model call or service is exercised. The integer oracle is literal test data,
not another invocation of the reader being tested.
"""
import copy
import os
from pathlib import Path

import pytest

import template_catalog as tc
from template_catalog import family_contract, units
from cdpm import catalog_bridge as cb, extract
from cdpm import family_params as fp, landing_migration, model as m, repair


FINGERPRINT = "f1fff61c6b8ba48dfe936c93d64fe23c60d38f9de027ba393685e1cd6c1ab0ad"
FAMILY, PARAM = "crew_composition", "crew_size"
ROUTES = ("candidate_legacy", "candidate_typed", "repair_legacy",
          "repair_typed", "migration", "extract_legacy", "extract_typed")
SOURCE_QUOTES = ("Экипаж смены — ровно 2 пилота", "2 экипажа")


@pytest.fixture
def draft(monkeypatch):
    # Missing or different input is an error, not a skipped regression.
    path = Path(os.environ["STAND_DRAFT_CATALOG"]).resolve(strict=True)
    monkeypatch.setenv(cb.ENV_CATALOG, str(path))
    cb.forget()
    try:
        cat = tc.current(path, validated=True)
        assert cat.version() == "0.7.0-draft.1"
        assert cat.fingerprint() == FINGERPRINT
        assert units.unit_words(path)["count"]["form"] == "integer_nonneg"
        yield cat
    finally:
        cb.forget()


def _case(cat, quote, value=2, *, statement=None, typed=False, missing=False):
    rule = {"id": "R-K6-COUNT", "title": "Состав экипажа",
            "statement": statement if statement is not None else quote,
            "zone": "BOTH", "kind": "HARD",
            "sources": [{"sid": "S1", "anchor": "п.12"}]}
    cell = {"value": value, "quote": quote,
            "source": {"kind": "rule", "rule_id": rule["id"]}}
    fit = {"category": "A", "family": FAMILY,
           "registry_rev": cat.fingerprint(), "params": {PARAM: cell}}
    if typed:
        fit.update(schema_version="landing/2",
                   family_contract=family_contract.snapshot(cat, FAMILY, [PARAM]))
        cell.update(unit="count", relation="eq", condition=None)
    if missing:
        fit["params"] = {}
    rule["template_fit"] = fit
    doc = {"meta": {"customer": "K6 synthetic", "mode": 1,
                    "cdpm_version": "0.1", "revision": 2},
           "sources": [{"sid": "S1", "file": "synthetic.md", "sha256": "0" * 64}],
           "rules": [rule], "questions": [], "objectives": []}
    return doc, rule


def _attempt(cat, route, quote, value=2, *, statement=None):
    typed = route.endswith("_typed")
    missing = route.startswith(("repair_", "extract_"))
    doc, rule = _case(cat, quote, value, statement=statement,
                      typed=typed, missing=missing)
    if route == "candidate_legacy":
        # The model's legacy proposal admits value/quote only. Code binds
        # its new typed source after checking the real carrier identity.
        rule["template_fit"]["params"][PARAM].pop("source")
    before = copy.deepcopy(doc)
    clause = rule["statement"]
    if route.startswith("candidate_"):
        fit, _, note = m.accept_fit_candidate(rule, clause, new_output=True)
        assert doc == before
        return fit.get("category") == "A", fit, note
    if route == "migration":
        result = landing_migration.prepare_rule(rule, cat, document=doc)
        assert doc == before
        return result["state"] == "confirmed", result["rule"]["template_fit"], result["words"]
    if route.startswith("repair_"):
        reply = {"value": value, "quote": quote}
        original_reply = copy.deepcopy(reply)
        problem = repair._apply(rule, False, "param:" + PARAM, reply, clause, doc)
        assert reply == original_reply
    else:
        addition = {"name": PARAM, "value": value, "relation": "eq", "as_written": quote}
        original_addition = copy.deepcopy(addition)
        problem = extract.merge_soft_params(rule, [addition])
        assert addition == original_addition
    if problem:
        assert doc == before, "rejected evidence must not partially write a cell"
    return problem is None, rule["template_fit"], problem


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("quote", SOURCE_QUOTES)
def test_complete_source_count_quote_reaches_every_writer(draft, route, quote):
    # These nouns are source prose, not additions to unit_words/subject_words.
    assert fp.parse_value(quote, "count") is None
    accepted, fit, why = _attempt(draft, route, quote)
    assert accepted, (route, quote, why)
    cell = fit["params"][PARAM]
    assert cell["value"] == 2 and not isinstance(cell["value"], bool)
    assert cell["quote"] == quote
    if route == "repair_legacy":
        # The legacy single-slot writer keeps the legacy value/quote shape;
        # this correction is not authority to fabricate migration metadata.
        assert cell == {"value": 2, "quote": quote}
    else:
        assert cell["source"] == {"kind": "rule", "rule_id": "R-K6-COUNT"}
    if "unit" in cell:
        assert cell["unit"] == "count" and cell["relation"] == "eq"
    if "family_contract" in fit:
        assert fit["family_contract"]["catalog_fingerprint"] == FINGERPRINT
    # A successful source witness cannot mutate the answer grammar/cache.
    assert fp.parse_value(quote, "count") is None


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("quote", ["2", "2 count", "2 суток"])
def test_bare_and_declared_count_units_remain_healthy(draft, route, quote):
    # Draft.1 explicitly declares суток as count. The pre-0.6 rejection of
    # that word is not an oracle for this catalogue.
    assert "суток" in units.unit_words(draft.path)["count"]["words"]
    assert fp.parse_value(quote, "count")[0] == 2
    accepted, fit, why = _attempt(draft, route, quote)
    assert accepted, (route, quote, why)
    assert fit["params"][PARAM]["value"] == 2
    assert fit["params"][PARAM]["quote"] == quote


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("quote", [
    "2 часа", "2 %", "2 недели", "2,5 пилота", "-2 пилота",
    "2 пилота и 3 экипажа", "2 пилота. Другой состав — 3 экипажа",
    "не 2 пилота", "около 2 пилотов", "2 пилота в месяц",
    "2 пилота только для базы SVO", "2 пилота. Не применяется",
])
def test_source_quote_does_not_erase_units_numbers_or_context(draft, route, quote):
    accepted, _fit, why = _attempt(draft, route, quote)
    assert not accepted, (route, quote)
    assert why


@pytest.mark.parametrize("route", ROUTES)
def test_source_count_must_equal_claimed_value(draft, route):
    accepted, _fit, why = _attempt(draft, route, SOURCE_QUOTES[0], value=3)
    assert not accepted and why


@pytest.mark.parametrize("route", [r for r in ROUTES if r != "migration"])
@pytest.mark.parametrize("statement,quote", [
    ("Экипаж — ровно 12 пилотов", "2"),
    ("Экипаж — ровно 2,5 пилота", "2"),
    ("Длительность — 2 часа", "2"),
    ("Экипаж — ровно 3 пилота", "2 пилота"),
])
def test_new_writer_cannot_clip_or_invent_a_source_quote(draft, route, statement, quote):
    # Migration verifies saved evidence, not a freshly supplied clause
    # anchor; its separate source/history gates are not mocked here.
    accepted, _fit, why = _attempt(draft, route, quote, statement=statement)
    assert not accepted and why


def test_explicit_origin_owns_count_word_not_selected_catalogue(draft, tmp_path, monkeypatch):
    from test_catalog_060_versioned_schema import modern_docs
    from test_template_catalog_validate import _broken

    def different_word_ownership(docs):
        modern_docs(docs)
        duration = docs["dictionaries.json"]["unit_words"]["duration_minutes"]
        duration["words"].append("суток")
        duration["scale"]["суток"] = 1440

    # A valid temporary catalogue is the mechanism substitution. No product
    # reader is mocked; the external draft remains byte-for-byte untouched.
    other = tc.current(_broken(tmp_path, different_word_ownership), validated=True)
    assert other.fingerprint() != draft.fingerprint()
    raw = "2 суток"
    assert units.to_canonical(2, "суток", "count", source=draft.path) == 2
    with pytest.raises(units.UnitsError):
        units.to_canonical(2, "суток", "count", source=other.path)
    monkeypatch.setenv(cb.ENV_CATALOG, str(other.path))
    cb.forget()
    assert fp.parse_value(raw, "count") is None
    assert fp.parse_rule_quote(raw, "count") is None
    assert fp.parse_rule_quote(raw, "count", source=draft.path) == (2, raw)
    monkeypatch.setenv(cb.ENV_CATALOG, str(draft.path))
    cb.forget()
    assert fp.parse_rule_quote(raw, "count") == (2, raw)
    assert fp.parse_rule_quote(raw, "count", source=other.path) is None


def test_direct_quote_preserves_raw_evidence_and_domain(draft):
    raw = "  Экипаж смены — ровно 2 пилота  "
    assert fp.parse_rule_quote(raw, "count", source=draft.path) == (2, raw)
    assert fp.parse_rule_quote(raw, "count", {"min": 3}, source=draft.path) is None
    assert fp.parse_rule_quote(raw, "count", {"max": 1}, source=draft.path) is None
    assert fp.parse_value(raw, "count") is None
    # Numeric witness only: this does not prove arbitrary noun semantics,
    # catalogue acceptance, provenance or a full-ID completion.


@pytest.mark.parametrize("raw,unit,want", [
    ("2 часа", "duration_minutes", 120),
    ("2 пилота", "duration_minutes", None),
    ("2 пилота", "credit_hours", None),
    ("12:30", "time_of_day_minutes", 750),
])
def test_direct_quote_does_not_widen_other_unit_grammars(draft, raw, unit, want):
    quote = fp.parse_rule_quote(raw, unit, source=draft.path)
    answer = fp.parse_value(raw, unit)
    assert quote == answer
    assert (quote[0] if quote is not None else None) == want
