"""Z-14: canonical code lists, without changing legacy/raw value contracts.

Only synthetic in-memory carriers; no extractor, network or live catalogue.
The pre-existing contradiction evidence is an independent expectation source.
"""
import copy

import pytest

from cdpm import contradiction
from cdpm import model as m
from test_cdpm_k5_param_conflict_values import (
    _assert_conflict, _assert_no_conflict, _doc, _fit_rule, _source,
)


@pytest.mark.parametrize("category", ["A", "B"])
@pytest.mark.parametrize("left,right", [
    (["Полярис-Р200"], ["полярис-р200"]),
    ([" Полярис-Р200  "], ["Полярис-Р200"]),
    ([" TYPE   A "], ["type a"]),
    (["A", "B", "A"], [" b ", "a"]),
    (["01"], ["1"]),
])
def test_code_list_canon_agrees_with_existing_evidence(category, left, right):
    doc = _doc(_fit_rule("R-A", left, "first original quote", category),
               _fit_rule("R-B", right, "second original quote", category))
    evidence, _reason = contradiction.evidence(doc["rules"])
    assert evidence is None
    _assert_no_conflict(doc)


@pytest.mark.parametrize("category", ["A", "B"])
def test_canonical_carriers_keep_original_quotes_and_source_groups(category):
    doc = _doc(_fit_rule("R-A", ["A", "B"], "A, B", category),
               _fit_rule("R-B", ["C"], "C", category),
               _fit_rule("R-C", [" b ", "a"], "b / a", category))
    _assert_conflict(doc, "fleet_types", "['A', 'B'], ['C']", [
        {"key": "а", "text": "действует A, B; b / a (строки: R-A, R-C)"},
        {"key": "б", "text": "действует C (строки: R-B)"},
    ], ["R-A", "R-B", "R-C"], [_source("R-A"), _source("R-B")])


@pytest.mark.parametrize("left,right", [
    (["a"], ["A", "B"]), (["A", "B"], ["a"]), ([], ["a"]),
    (["A"], ["B"]), (["1"], ["2"]), (["A,B"], ["A", "B"]),
])
def test_real_code_list_difference_still_blocks(left, right):
    doc = _doc(_fit_rule("R-A", left, "first"),
               _fit_rule("R-B", right, "second"))
    evidence, _reason = contradiction.evidence(doc["rules"])
    assert evidence is not None
    assert m.ensure_param_conflict_questions(doc) == ["Q-1-парам-fleet_types"]
    assert len(doc["questions"][0]["answer_format"]["options"]) == 2
    once = copy.deepcopy(doc)
    assert m.ensure_param_conflict_questions(doc) == []
    assert doc == once


@pytest.mark.parametrize("left,right", [
    ({"key": "A"}, {"key": "a"}),
    ({"key": ["A"]}, {"key": ["a"]}),
    (["A"], ["a"]),
])
def test_non_code_list_containers_retain_raw_element_semantics(left, right):
    doc = _doc(_fit_rule("R-A", left, "first", unit="count"),
               _fit_rule("R-B", right, "second", unit="count"))
    assert m.ensure_param_conflict_questions(doc) == ["Q-1-парам-fleet_types"]


def test_mixed_untyped_group_is_not_silently_canonicalized():
    doc = _doc(_fit_rule("R-A", ["A"], "first"),
               _fit_rule("R-B", ["a"], "second", unit=None))
    assert m.ensure_param_conflict_questions(doc) == ["Q-1-парам-fleet_types"]


def test_typed_legacy_and_fit_code_lists_share_the_canonical_relation():
    first = _fit_rule("R-A", ["A"], "first")
    second = _fit_rule("R-B", [" a "], "second")
    second["params"] = [{"name": "fleet_types", "value": [" a "],
                         "unit": "code_list", "as_written": "second"}]
    evidence, _reason = contradiction.evidence([first, second])
    assert evidence is None
    _assert_no_conflict(_doc(first, second))
