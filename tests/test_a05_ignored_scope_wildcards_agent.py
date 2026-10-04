"""Report-format controls for per-entry ignored-scope wildcards.

Literal diagnostic inputs only, not captured Kotlin or modern button evidence.
"""
import pytest

from test_a05_ignored_scope_groups_agent import PARAM, _compare, _ignored, _model, _not_applied
from test_a05_landing_numeric_neighbors_agent import (
    clean_bridge,  # noqa: F401
    isolated_consumer_catalog,  # noqa: F401
    landed,  # noqa: F401
)
from webapp import threshold_gate as tg


@pytest.mark.parametrize("rows,rules,qualifiers,refused_values", [
    ([('R1', 420, 'ST1'), ('R2', 450, 'ST1'), ('R3', 600, 'ST2')],
     [], ['ST1'], [420, 450]),
    ([('R1', 420, 'ST1'), ('R1', 450, 'ST2'), ('R2', 600, 'ST2')],
     ['R1'], [], [420, 450]),
    ([('R1', 420, None), ('R1', 600, 'ST1')],
     ['R1'], [''], [420]),
], ids=['any-rule-specific-scope', 'specific-rule-any-scope', 'empty-string-is-not-wildcard'])
def test_partial_wildcards_keep_unmatched_records(landed, rows, rules, qualifiers, refused_values):
    pkg = _model(landed, [(rid, PARAM, value, 'min', q) for rid, value, q in rows])
    result = _compare(pkg, [_ignored(rules, qualifiers, 'Scope refused')])
    refused, = _not_applied(result)
    assert refused['package_values'] == refused_values, result
    matched, = [v for v in result if v['verdict'] == tg.MATCH]
    assert matched['package_values'] == [600], result
    assert len(result) == 2, result


def test_global_entry_is_not_narrowed_by_another_scoped_entry(landed):
    pkg = _model(landed, [('R1', PARAM, 420, 'min', 'ST1'),
                          ('R2', PARAM, 600, 'min', 'ST2')])
    result = _compare(pkg, [_ignored(['R1'], ['ST1'], 'Scope refused'),
                            {'param': PARAM, 'reason': 'All scopes refused'}])
    refused, = result
    assert refused['verdict'] == tg.INCOMPARABLE, result
    assert refused['package_values'] == [420, 600], result
    assert 'Scope refused' in refused['note'] and 'All scopes refused' in refused['note']
