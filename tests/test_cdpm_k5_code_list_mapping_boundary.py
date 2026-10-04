"""Keep the raw mapping contract even inside declared code-list carriers.

The oracle here is mapping equality: key order is immaterial, but string
values retain case. It deliberately differs from stringifying a dictionary
inside contradiction._canon_value. All inputs are synthetic reader controls.
"""
import copy

import pytest

from test_cdpm_k5_param_conflict_values import (
    _assert_conflict, _assert_no_conflict, _doc, _fit_rule, _source,
)


def _rule(rid, value, quote, carrier):
    if carrier != "legacy":
        return _fit_rule(rid, value, quote, carrier)
    return {
        "id": rid, "kind": "HARD", "zone": "BOTH", "statement": quote,
        "sources": [_source(rid), {"sid": "S2", "anchor": "secondary-" + rid}],
        "params": [{"name": "fleet_types", "value": copy.deepcopy(value),
                    "unit": "code_list", "as_written": quote}],
    }


def _nested(value, depth):
    for _ in range(depth):
        value = [value]
    return value


def _assert_distinct(rows, values):
    # Only display ordering is derived; all supplied values are explicitly
    # distinct under the raw mapping contract stated by each test.
    ordered = sorted(zip(values, rows), key=lambda item: str(item[0]))
    _assert_conflict(_doc(*rows), "fleet_types",
                     ", ".join(str(value) for value, _row in ordered), [
        {"key": letter,
         "text": f"действует {row['statement']} (строки: {row['id']})"}
        for letter, (_value, row) in zip("абв", ordered)
    ], [row["id"] for row in rows],
        [_source(row["id"]) for _value, row in ordered])


@pytest.mark.parametrize("carrier", ["A", "B", "legacy"])
@pytest.mark.parametrize("depth", [1, 3], ids=["direct-element", "nested-element"])
def test_mapping_key_order_does_not_create_a_question(carrier, depth):
    left = _nested({"a": 1, "b": 2}, depth)
    right = _nested({"b": 2, "a": 1}, depth)
    _assert_no_conflict(_doc(_rule("R-A", left, "first original", carrier),
                            _rule("R-B", right, "second original", carrier)))


@pytest.mark.parametrize("carrier", ["A", "B", "legacy"])
@pytest.mark.parametrize("depth", [1, 3], ids=["direct-element", "nested-element"])
def test_mapping_value_case_remains_a_real_difference(carrier, depth):
    values = [_nested({"k": "A"}, depth), _nested({"k": "a"}, depth)]
    _assert_distinct([_rule("R-A", values[0], "upper original", carrier),
                      _rule("R-B", values[1], "lower original", carrier)], values)


@pytest.mark.parametrize("carrier,mapping_position", [
    ("A", 0), ("B", 1), ("legacy", 2),
])
def test_one_mapping_switches_the_whole_group_to_raw_equality(
        carrier, mapping_position):
    values = [["A"], ["a"]]
    values.insert(mapping_position, [[{"k": "A"}]])
    rows = [_rule("R-" + str(i), value, "original-" + str(i), carrier)
            for i, value in enumerate(values)]
    # The two scalar-only lists must remain different too: the mapping
    # chooses one relation for the entire group, not only its own pair.
    _assert_distinct(rows, values)


@pytest.mark.parametrize("carrier", ["A", "B", "legacy"])
def test_mapping_groups_preserve_original_quotes_and_first_sources(carrier):
    first = [{"a": "A", "b": 2}]
    different = [{"a": "a", "b": 2}]
    reordered = [{"b": 2, "a": "A"}]
    doc = _doc(_rule("R-A", first, "map-one", carrier),
               _rule("R-B", different, "map-lower", carrier),
               _rule("R-C", reordered, "map-same", carrier))
    _assert_conflict(doc, "fleet_types", f"{first}, {different}", [
        {"key": "а", "text": "действует map-one; map-same (строки: R-A, R-C)"},
        {"key": "б", "text": "действует map-lower (строки: R-B)"},
    ], ["R-A", "R-B", "R-C"], [_source("R-A"), _source("R-B")])


@pytest.mark.parametrize("left,right", [
    ([["A", "B"]], [[" b ", "a"]]),
    ([["1e3"]], [["1000"]]),
    (["2 ч"], ["120"]),
], ids=["nested-code-canon", "nested-numeric-string", "duration-string"])
def test_mapping_free_lists_keep_existing_canonical_paths(left, right):
    _assert_no_conflict(_doc(_rule("R-A", left, "first original", "A"),
                            _rule("R-B", right, "second original", "A")))
