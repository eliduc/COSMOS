"""K-5: structural values reach the real deterministic conflict builder.

Synthetic in-memory carriers only; no extractor, model channel, schema bypass,
catalog publication or claim that these focused inputs are complete packages.
Dict order is immaterial; code-list order follows the existing contradiction
contract (sets), while 1 == True == 1.0 keeps legacy scalar equality.
"""
import copy

import pytest

from cdpm import model as m


def _source(rid):
    return {"sid": "S1", "anchor": rid}


def _fit_rule(rid, value, quote, category="A", *, name="fleet_types",
              family="fleet_exclusions", unit="code_list"):
    return {
        "id": rid, "kind": "HARD", "zone": "BOTH",
        "title": "Синтетический носитель", "statement": quote,
        "sources": [_source(rid), {"sid": "S2", "anchor": "secondary-" + rid}],
        "template_fit": {"category": category, "family": family,
                         "params": {name: {"value": copy.deepcopy(value),
                                            "unit": unit, "quote": quote}}},
    }


def _legacy_rule(rid, value, quote, *, name="min_rest_home", **changes):
    rule = {"id": rid, "kind": "HARD", "zone": "BOTH",
            "title": "Синтетический носитель", "statement": quote,
            "sources": [_source(rid)],
            "params": [{"name": name, "value": value,
                        "unit": "duration_minutes", "relation": "min",
                        "as_written": quote}]}
    rule.update(changes)
    return rule


def _doc(*rules):
    return {"rules": list(rules), "questions": []}


def _assert_no_conflict(doc):
    before = copy.deepcopy(doc)
    assert m.ensure_param_conflict_questions(doc) == []
    assert doc == before
    assert m.ensure_param_conflict_questions(doc) == []
    assert doc == before


def _assert_conflict(doc, name, value_words, options, refs, sources):
    before = copy.deepcopy(doc)
    qid = "Q-1-парам-" + name
    assert m.ensure_param_conflict_questions(doc) == [qid]
    expected = {
        "id": qid, "part": "1", "title": "Разные значения параметра " + name,
        "text": ("Правила свода несут параметр " + name
                 + " с разными значениями: " + value_words
                 + ". Какое значение действует?"),
        "answer_format": {"type": "choice", "options": options},
        "rule_refs": refs, "source_refs": sources,
    }
    assert doc["questions"] == [expected]
    for old, actual in zip(before["rules"], doc["rules"], strict=True):
        wanted = copy.deepcopy(old)
        if old["id"] in refs:
            wanted["blockers"] = [qid]
        assert actual == wanted  # values, quotes, sources and unrelated state intact
    once = copy.deepcopy(doc)
    assert m.ensure_param_conflict_questions(doc) == []
    assert doc == once


@pytest.mark.parametrize("category", ["A", "B"])
@pytest.mark.parametrize("copies", [1, 2], ids=["single", "equal"])
def test_code_list_single_or_equal_does_not_crash_or_ask(category, copies):
    doc = _doc(*[_fit_rule("R-" + str(i), ["Полярис-Р200"], "Полярис-Р200",
                          category) for i in range(copies)])
    _assert_no_conflict(doc)


@pytest.mark.parametrize("category", ["A", "B"])
def test_different_code_lists_address_all_carriers_and_only_first_source(category):
    doc = _doc(_fit_rule("R-A", ["Полярис-Р200"], "Полярис-Р200", category),
               _fit_rule("R-B", ["Север-300"], "Север-300", category),
               _fit_rule("R-C", ["Полярис-Р200"], "Полярис-Р200", category))
    _assert_conflict(doc, "fleet_types", "['Полярис-Р200'], ['Север-300']", [
        {"key": "а", "text": "действует Полярис-Р200 (строки: R-A, R-C)"},
        {"key": "б", "text": "действует Север-300 (строки: R-B)"},
    ], ["R-A", "R-B", "R-C"], [_source("R-A"), _source("R-B")])


@pytest.mark.parametrize("category", ["A", "B"])
def test_code_list_order_agrees_with_existing_contradiction_contract(category):
    doc = _doc(_fit_rule("R-A", ["A", "B"], "A, B", category),
               _fit_rule("R-B", ["B", "A"], "B, A", category))
    from cdpm import contradiction
    evidence, _reason = contradiction.evidence(doc["rules"])
    assert evidence is None
    _assert_no_conflict(doc)


@pytest.mark.parametrize("category", ["A", "B"])
def test_dict_key_order_does_not_create_a_conflict(category):
    doc = _doc(_fit_rule("R-A", {"PLY": 2, "SVG": 1}, "PLY=2, SVG=1", category,
                        name="reserve_crews", family="reserve_floors", unit="count"),
               _fit_rule("R-B", {"SVG": 1, "PLY": 2}, "SVG=1, PLY=2", category,
                         name="reserve_crews", family="reserve_floors", unit="count"))
    _assert_no_conflict(doc)


@pytest.mark.parametrize("category", ["A", "B"])
def test_reordered_list_carrier_joins_the_same_option_and_source_group(category):
    doc = _doc(_fit_rule("R-A", ["A", "B"], "A, B", category),
               _fit_rule("R-B", ["C"], "C", category),
               _fit_rule("R-C", ["B", "A"], "B, A", category))
    _assert_conflict(doc, "fleet_types", "['A', 'B'], ['C']", [
        {"key": "а", "text": "действует A, B; B, A (строки: R-A, R-C)"},
        {"key": "б", "text": "действует C (строки: R-B)"},
    ], ["R-A", "R-B", "R-C"], [_source("R-A"), _source("R-B")])


@pytest.mark.parametrize("values", [(["A"], ["A", "B"]),
                                   (["A", "B"], ["A"]),
                                   ([], ["A"])])
def test_a_proper_subset_still_requires_a_conflict_question(values):
    doc = _doc(_fit_rule("R-A", values[0], "first"),
               _fit_rule("R-B", values[1], "second"))
    assert m.ensure_param_conflict_questions(doc) == ["Q-1-парам-fleet_types"]
    question, = doc["questions"]
    assert len(question["answer_format"]["options"]) == 2
    assert question["rule_refs"] == ["R-A", "R-B"]
    assert all(r["blockers"] == [question["id"]] for r in doc["rules"])


@pytest.mark.parametrize("category", ["A", "B"])
def test_different_dict_values_keep_exact_question_and_equal_group(category):
    rows = [
        _fit_rule("R-A", {"PLY": 2, "SVG": 1}, "PLY=2, SVG=1", category,
                  name="reserve_crews", family="reserve_floors", unit="count"),
        _fit_rule("R-B", {"PLY": 3, "SVG": 1}, "PLY=3, SVG=1", category,
                  name="reserve_crews", family="reserve_floors", unit="count"),
        _fit_rule("R-C", {"SVG": 1, "PLY": 2}, "PLY=2, SVG=1", category,
                  name="reserve_crews", family="reserve_floors", unit="count"),
    ]
    _assert_conflict(_doc(*rows), "reserve_crews",
                     "{'PLY': 2, 'SVG': 1}, {'PLY': 3, 'SVG': 1}", [
        {"key": "а", "text": "действует PLY=2, SVG=1 (строки: R-A, R-C)"},
        {"key": "б", "text": "действует PLY=3, SVG=1 (строки: R-B)"},
    ], ["R-A", "R-B", "R-C"], [_source("R-A"), _source("R-B")])


@pytest.mark.parametrize("values", [[1, True, 1.0], [0, False, 0.0], [None, None]],
                         ids=["one-bool-float", "zero-bool-float", "none"])
def test_legacy_equal_scalar_types_keep_existing_equality(values):
    _assert_no_conflict(_doc(*[
        _legacy_rule("R-" + str(i), value, "исходный скаляр")
        for i, value in enumerate(values)]))


def test_legacy_number_and_text_are_distinct_without_imposing_tie_order():
    doc = _doc(_legacy_rule("R-A", 1, "число один"),
               _legacy_rule("R-B", True, "логическая единица"),
               _legacy_rule("R-C", 1.0, "вещественная единица"),
               _legacy_rule("R-D", "1", "текст один"))
    before = copy.deepcopy(doc)
    qid = "Q-1-парам-min_rest_home"
    assert m.ensure_param_conflict_questions(doc) == [qid]
    question, = doc["questions"]
    assert question["id"] == qid and question["part"] == "1"
    assert question["rule_refs"] == ["R-A", "R-B", "R-C", "R-D"]
    options = question["answer_format"]
    assert options["type"] == "choice" and len(options["options"]) == 2
    assert {o["key"] for o in options["options"]} == {"а", "б"}
    assert {o["text"] for o in options["options"]} == {
        "действует вещественная единица; логическая единица; число один "
        "(строки: R-A, R-B, R-C)", "действует текст один (строки: R-D)"}
    assert question["source_refs"] in (
        [_source("R-A"), _source("R-D")], [_source("R-D"), _source("R-A")])
    for old, actual in zip(before["rules"], doc["rules"], strict=True):
        assert actual == {**old, "blockers": [qid]}
    once = copy.deepcopy(doc)
    assert m.ensure_param_conflict_questions(doc) == []
    assert doc == once


def test_legacy_numeric_conflict_preserves_active_scope_and_blockers():
    doc = _doc(_legacy_rule("R-A", 720, "12:00"),
               _legacy_rule("R-B", 750, "12:30", kind="DATA"),
               _legacy_rule("R-C", 720, "12:00", kind="DEF"),
               _legacy_rule("R-OUT", 999, "16:39", kind="OUT"),
               _legacy_rule("R-SOFT", 998, "16:38", kind="SOFT"),
               _legacy_rule("R-BLOCKED", 777, "12:57", blockers=["Q-OLD"]))
    _assert_conflict(doc, "min_rest_home", "720, 750", [
        {"key": "а", "text": "действует 12:00 (строки: R-A, R-C)"},
        {"key": "б", "text": "действует 12:30 (строки: R-B)"},
    ], ["R-A", "R-B", "R-C"], [_source("R-A"), _source("R-B")])


def test_shared_anchor_retains_two_source_references_for_two_values():
    first = _fit_rule("R-A", ["Полярис-Р200"], "Полярис-Р200")
    second = _fit_rule("R-B", ["Север-300"], "Север-300", "B")
    first["sources"] = second["sources"] = [{"sid": "S1", "anchor": "п.1"}]
    # Existing fallback retains the already selected source and adds another
    # reference from the same clause: two positions, not invented anchors.
    _assert_conflict(_doc(first, second), "fleet_types",
                     "['Полярис-Р200'], ['Север-300']", [
        {"key": "а", "text": "действует Полярис-Р200 (строки: R-A)"},
        {"key": "б", "text": "действует Север-300 (строки: R-B)"},
    ], ["R-A", "R-B"], [{"sid": "S1", "anchor": "п.1"}] * 2)
