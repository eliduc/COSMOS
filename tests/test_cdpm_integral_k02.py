"""K02 integration: numeric carriers and the shared decline grammar.

These tests exercise the merged tree, not either isolated component branch.
"""
from decimal import Decimal, localcontext

import pytest

from cdpm import family_params as fp


@pytest.mark.parametrize("raw,unit,carrier,want", [
    ("10,5", "duration_minutes", "ч", Decimal("630")),
    ("−10.5", "duration_minutes", "ч", Decimal("-630")),
    ("12345.67890123456789", "duration_minutes", "ч",
     Decimal("740740.73407407407340")),
    (Decimal("12345.67890123456789"), "duration_minutes", "ч",
     Decimal("740740.73407407407340")),
])
def test_canonical_number_uses_exact_arithmetic_independent_of_context(
        raw, unit, carrier, want):
    with localcontext() as context:
        context.prec = 3
        got, reason = fp.canonical_value(
            raw, word_or_code=carrier, target_unit=unit)
    assert reason is None
    assert isinstance(got, Decimal)
    assert got == want


@pytest.mark.parametrize("raw", ["9" * 25, "NaN", "Infinity", True, None])
def test_unreadable_canonical_number_is_refused(raw):
    got, reason = fp.canonical_value(
        raw, word_or_code="ч", target_unit="duration_minutes")
    assert got is None and reason


@pytest.mark.parametrize("raw,declined,conditional", [
    ("«Не применяется».", True, False),
    ("«Не применяется». Неприменимо!", True, False),
    ("Не применяется для базы SVO. В остальных 600 минут.", False, True),
    ("600 минут. Не применяется для базы SVO.", False, True),
    ("600 минут", False, False),
])
def test_coverage_uses_the_same_decline_grammar_as_application(
        raw, declined, conditional):
    question = {"id": "Q-test", "answer": {
        "raw": raw, "status": "применён"}}
    assert fp._is_declined(question) is declined
    assert fp._is_conditional_decline(question) is conditional
    assert fp.is_declined(raw) is declined


@pytest.mark.parametrize("reverse", [False, True])
def test_self_healing_follows_the_explicit_chain_not_list_order(reverse):
    from test_cdpm_k02_number_grammar import _stored_decline_with_k04_revision
    doc, rule, root, revision = _stored_decline_with_k04_revision()
    if reverse:
        doc["questions"].reverse()
    coverage = fp.family_coverage(doc)
    assert fp._reapply_values(doc, coverage) == [rule["id"]]
    cell = rule["template_fit"]["params"]["min_rest_outstation"]
    assert cell["value"] == 600
    assert cell["source"]["question"]["id"] == revision["id"]
    assert cell["source"]["root"] == root["id"]


def test_unlinked_applied_revision_is_not_used_as_a_fallback():
    from test_cdpm_k02_number_grammar import _stored_decline_with_k04_revision
    doc, rule, root, revision = _stored_decline_with_k04_revision()
    root["answer"]["status"] = "применён"
    root["answer"].pop("revised_by")
    coverage = fp.family_coverage(doc)
    assert fp._reapply_values(doc, coverage) == []
    assert "min_rest_outstation" not in rule["template_fit"]["params"]
    assert "min_rest_outstation" in coverage["rest_floors"]["missing"]
