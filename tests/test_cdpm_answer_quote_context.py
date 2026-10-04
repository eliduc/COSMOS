"""A02/A03: a literal scalar quotation cannot erase its answer's context.

Synthetic documents, actual provenance/worker validators, no model channel
or external consumer. Numerical and source identities are deliberately valid:
only the unsupported applicability/period/decline distinguishes the pair.
"""
import copy
import hashlib

import pytest

from cdpm import answers as ans, family_params as fp, model as m
from cdpm import provenance as prov
from test_cdpm_a03m_provenance import (
    CLAUSES, FAMILY, PARAM, QID, _answered, _cell, _doc_with_open_question,
    _q, _rule, _worker_reply,
)


RESTRICTED = [
    "10 часов только для пилотов.",
    "Только для пилотов — 10 часов.",
    "10 часов. Только для пилотов.",
    "10 часов в неделю.",
    "Не применяется для SVO. В остальных базах — 10 часов.",
    "10 часов. Не применяется для SVO.",
]
UNQUALIFIED = [
    "10 часов.",
    "Для всех баз — 10 часов.",
    "10 часов. Дома — 12 часов.",
]

COSMOS_CASES = [
    ("11 часов", False),
    ("Отдых не регламентирован", True),
    ("Нет, порог 11 часов мы не применяем", True),
    ("11 часов, кроме ночных смен — там 14 часов", True),
    ("С 1 июня — 11 часов, до этого 10 часов", True),
    ("Для базы SVO — 11 часов, для прочих 12 часов", True),
]


@pytest.mark.parametrize("raw,refused", COSMOS_CASES)
def test_cosmos_independent_core_counterexamples(raw, refused):
    problems = prov.value_problems("rest_floors", "min_rest_home",
                                   {"value": 660, "quote": "11 часов"}, raw)
    assert bool(problems) is refused


@pytest.mark.parametrize("raw", [
    "С 1 июня — 10 часов.",
    "До 01.06.2027 — 10 часов.",
    "После 2027-06-01 — 10 часов.",
    "10 часов. Начиная с 1 июня.",
])
def test_dated_norm_is_not_a_timeless_scalar(raw):
    assert fp.parse_value(raw, "duration_minutes") is None
    problems = prov.value_problems(FAMILY, PARAM,
                                   {"value": 600, "quote": "10 часов"}, raw)
    assert problems
    assert "полный ответ" in " ".join(problems)


def _received_raw(q, raw):
    """Build a coherent synthetic receipt; these probes vary context, not identity."""
    q["answer"]["raw"] = raw
    q["answer"]["answer_version"]["fingerprint"] = hashlib.sha256(
        " ".join(raw.split()).encode()).hexdigest()[:12]


def _document(raw):
    doc, cell = _answered()
    q = _q(doc, QID)
    _received_raw(q, raw)
    cell.update(value=600, quote="10 часов",
                source=prov.answer_source(q, PARAM, raw))
    assert m.validate_schema(doc) == []
    assert prov.resolve_source(doc, cell["source"]) == (q, raw)
    assert cell["quote"] in raw
    assert fp.parse_value(cell["quote"], "duration_minutes")[0] == 600
    return doc, cell


def _read(reader, doc, cell):
    if reader == "value":
        return prov.value_problems(FAMILY, PARAM, cell, _q(doc, QID)["answer"]["raw"])
    if reader == "quotes":
        return m.validate_fit_quotes(doc, CLAUSES)
    return prov.package_problems(doc)


@pytest.mark.parametrize("reader", ["value", "quotes", "package"])
@pytest.mark.parametrize("raw", RESTRICTED)
def test_literal_quote_cannot_erase_full_answer_context(reader, raw):
    doc, cell = _document(raw)
    before = copy.deepcopy(doc)
    assert fp.parse_value(raw, "duration_minutes") is None
    problems = _read(reader, doc, cell)
    assert problems, "literal but out-of-context scalar was accepted"
    assert "полный ответ" in " ".join(problems)
    assert doc == before


@pytest.mark.parametrize("reader", ["value", "quotes", "package"])
@pytest.mark.parametrize("raw", UNQUALIFIED)
def test_unqualified_quote_and_neighbour_subject_keep_working(reader, raw):
    doc, cell = _document(raw)
    before = copy.deepcopy(doc)
    assert _read(reader, doc, cell) == []
    assert doc == before


@pytest.mark.parametrize("raw", RESTRICTED + UNQUALIFIED[:2])
def test_worker_obeys_full_context_without_a_real_model(raw):
    doc = _doc_with_open_question()
    qid = "Q-3b-уточнение"
    q = _q(doc, qid)
    _received_raw(q, raw)
    before = copy.deepcopy(_cell(doc))
    rule = copy.deepcopy(_rule(doc))
    rule["template_fit"]["params"][PARAM] = {
        "value": 600, "quote": "10 часов",
        "source": prov.answer_source(q, PARAM, raw),
    }
    rule.pop("blockers", None)
    result = ans.apply_worker_patches(doc, lambda _: _worker_reply(qid, [rule]))
    if raw in RESTRICTED:
        assert result["worker_applied"] == 0
        assert any("полный ответ" in reason
                   for item in result["worker_rejected"] for reason in item["reasons"])
        assert _cell(doc) == before
        assert q["answer"]["status"] == "ожидает обработчика"
        assert qid in _rule(doc)["blockers"]
    else:
        assert result["worker_rejected"] == []
        assert result["worker_applied"] == 1
        assert _cell(doc)["source"]["question"]["id"] == qid
        assert _q(doc, qid)["answer"]["status"] == "применён"
