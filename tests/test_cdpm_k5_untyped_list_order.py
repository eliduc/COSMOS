"""Z-15: exercise the real fallback relation, not the typed code-list canon.

Synthetic in-memory A/B carriers only. A missing unit or a different unit
must preserve the existing set-like list relation without canonicalizing
the elements. These are reader controls, not claims of valid full packages.
"""
import pytest

from test_cdpm_k5_param_conflict_values import (
    _assert_conflict, _assert_no_conflict, _doc, _fit_rule, _source,
)


def _fallback_rule(rid, value, quote, category, unit):
    rule = _fit_rule(rid, value, quote, category, unit=unit)
    if unit is None:
        # Exercise an actually absent declaration, not only JSON null.
        del rule["template_fit"]["params"]["fleet_types"]["unit"]
    return rule


@pytest.mark.parametrize("category", ["A", "B"])
@pytest.mark.parametrize("unit", [None, "count"], ids=["unit-absent", "other-unit"])
@pytest.mark.parametrize("left,right", [
    (["A", "B"], ["B", "A"]),
    (["A", "B", "A"], ["A", "B"]),
], ids=["reorder", "duplicates"])
def test_fallback_list_reorder_and_duplicates_do_not_create_question(
        category, unit, left, right):
    doc = _doc(_fallback_rule("R-A", left, "first original quote", category, unit),
               _fallback_rule("R-B", right, "second original quote", category, unit))
    _assert_no_conflict(doc)


@pytest.mark.parametrize("category", ["A", "B"])
@pytest.mark.parametrize("unit", [None, "count"], ids=["unit-absent", "other-unit"])
@pytest.mark.parametrize("left,right", [
    (["A"], ["A", "B"]),
    (["A", "B"], ["A"]),
    ([], ["A"]),
    (["A"], ["C"]),
], ids=["proper-subset", "proper-superset", "empty-vs-nonempty", "different"])
def test_fallback_list_genuine_difference_preserves_question_and_sources(
        category, unit, left, right):
    rows = [_fallback_rule("R-A", left, "first original quote", category, unit),
            _fallback_rule("R-B", right, "second original quote", category, unit)]
    # Distinct raw values have a documented display sort, independent of
    # the set comparison: compute only that presentation, not the verdict.
    ordered = sorted(zip([left, right], rows), key=lambda item: str(item[0]))
    _assert_conflict(_doc(*rows), "fleet_types",
                     ", ".join(str(value) for value, _row in ordered), [
        {"key": letter,
         "text": f"действует {row['statement']} (строки: {row['id']})"}
        for letter, (_value, row) in zip("аб", ordered)
    ], ["R-A", "R-B"], [_source(row["id"]) for _value, row in ordered])


@pytest.mark.parametrize("category", ["A", "B"])
@pytest.mark.parametrize("unit", [None, "count"], ids=["unit-absent", "other-unit"])
def test_fallback_equal_carriers_keep_two_options_original_quotes_and_sources(
        category, unit):
    doc = _doc(_fallback_rule("R-A", ["A", "B"], "A, B", category, unit),
               _fallback_rule("R-B", ["C"], "C", category, unit),
               _fallback_rule("R-C", ["B", "A", "B"], "B, A, B", category, unit))
    _assert_conflict(doc, "fleet_types", "['A', 'B'], ['C']", [
        {"key": "а", "text": "действует A, B; B, A, B (строки: R-A, R-C)"},
        {"key": "б", "text": "действует C (строки: R-B)"},
    ], ["R-A", "R-B", "R-C"], [_source("R-A"), _source("R-B")])
