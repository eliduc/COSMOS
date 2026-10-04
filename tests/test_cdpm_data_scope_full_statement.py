"""The entire source statement participates in the data-question edition."""
import copy

import pytest

from cdpm import model as m, question_editions as qe
from test_cdpm_data_scope_editions import initial, report


@pytest.mark.parametrize("surface", ["md", "xlsx"])
@pytest.mark.parametrize("size", [139, 140, 141, 300])
def test_change_after_long_prefix_is_issued_and_shown(tmp_path, surface, size):
    from test_cdpm_edition_scope_display import shown
    doc, q, root = initial()
    rule = doc["rules"][0]
    prefix = "Область применения " + "я" * size + ": "
    rule["statement"] = prefix + "База ∈ {OLD}"
    m.ensure_data_scope_questions(doc, report("AAA"))
    qe.freeze(q)
    q["answer"] = {"raw": "а", "key": "а", "status": "применён", "revision": 1}
    old = copy.deepcopy(q)
    assert m.ensure_data_scope_questions(doc, report("AAA")) == []
    rule["statement"] = prefix + "База ∈ {OLD, ZZZ}"
    assert m.ensure_data_scope_questions(doc, report("AAA")) == [root + "-E2"]
    new = qe.current_for_root(doc, root)
    assert "База ∈ {OLD, ZZZ}" in shown(doc, new, surface, tmp_path)
    assert q == old and "answer" not in new
    assert m.ensure_data_scope_questions(doc, report("AAA")) == []
    assert m.validate_schema(doc) + m.validate_model(doc) == []


@pytest.mark.parametrize("size", [0, 300])
@pytest.mark.parametrize("invisible", ["\u200b", "\ufeff", "\u2060"])
@pytest.mark.parametrize("extended", [False, True])
def test_invisible_suffix_is_not_effect_but_real_extension_still_is(size, invisible, extended):
    from test_cdpm_data_scope_residuals import patch
    from cdpm import answers
    doc, q, root = initial()
    prefix = "я" * size + " "
    doc["rules"][0]["statement"] = prefix + "База ∈ {OLD}"
    m.ensure_data_scope_questions(doc, report("AAA"))
    qe.freeze(q)
    answers.apply_answers(doc, {root: "а"})
    before = copy.deepcopy(doc["rules"])
    text = prefix + ("База ∈ {OLD, AAA}" if extended else "База ∈ {OLD}") + invisible
    result = patch(doc, q, statement=text)
    assert result["worker_applied"] == int(extended)
    if extended:
        assert doc["rules"][0]["statement"] == text  # Do not rewrite source text.
    else:
        assert doc["rules"] == before
        assert q["answer"]["status"] == "ожидает обработчика"
