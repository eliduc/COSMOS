"""Minor COSMOS follow-up: human-readable formats and executable examples."""
import re

import pytest

from cdpm import answer_parsing as ap, catalog_bridge as cb, family_params as fp
from template_catalog import units
from test_cdpm_a03m_provenance import _doc
from test_k03_catalogue_numeric_reader import selected


@pytest.fixture(autouse=True)
def cleanup():
    yield
    cb.forget()


@pytest.mark.parametrize("unit", [
    "duration_minutes", "count", "time_of_day_minutes", "credit_hours",
    "hours_per_credit_hour", "credit_hours_per_hour", "code", "code_list", "flag",
])
def test_all_formats_are_human_and_have_an_example(tmp_path, monkeypatch, unit):
    cat = selected(tmp_path, monkeypatch)
    hint = fp.unit_words(unit)
    assert not any(re.search(r"\b" + form + r"\b", hint) for form in units.UNIT_FORMS)
    sample = re.search(r"например «([^»]+)»", hint)
    assert sample is not None, hint
    entry = cat.dictionaries()["unit_words"][unit]
    # Preserve the actual complete vocabulary, not a shortened local list.
    assert hint.split("слова записи:", 1)[1].strip() == ", ".join(entry["words"])
    if entry["form"] not in units.NON_NUMERIC_FORMS:
        want = 360 if unit == "time_of_day_minutes" else 1
        assert units.to_canonical(sample[1], entry["words"][0], unit,
                                  source=cat.path, exact=True) == want
        # A condition-only unit is not a CDPM scalar-answer implementation.
        # Do not manufacture support for it to satisfy a hint test.
        if unit in fp._NUMERIC_UNITS:
            got = fp.parse_value(sample[1], unit)
            assert got is not None and got[0] == want, (unit, sample[1])


def test_question_format_and_refusal_share_the_repaired_hint(tmp_path, monkeypatch):
    selected(tmp_path, monkeypatch)
    doc = _doc()
    rule = next(r for r in doc["rules"] if r["id"] == "R-R")
    rule["template_fit"]["params"].pop("min_rest_outstation", None)
    fp.ensure_family_param_questions(doc)
    q = next(q for q in doc["questions"]
             if fp.question_target(q) == ("rest_floors", "min_rest_outstation"))
    bad = ap.parameter(q, "min_rest_outstation — 3 неопознанных единиц")
    assert bad["value"] is None
    for text in (q["text"], q["answer_format"]["note"], " ".join(bad["problems"])):
        assert "integer_nonneg" not in text and "например «1 минута»" in text


def test_legacy_hint_is_unchanged(monkeypatch):
    monkeypatch.delenv(cb.ENV_CATALOG, raising=False)
    cb.forget()
    assert fp.unit_words("duration_minutes") == fp._UNIT_WORDS["duration_minutes"]
