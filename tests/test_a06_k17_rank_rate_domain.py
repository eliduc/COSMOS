"""Neighbor of K16: invalid normalized-cost numbers must not become runnable."""
import copy
import json

import pytest
from cdpm import model
from vmp import objective_projection as op, criteria
from test_a06_k16_criterion_scalar import catalogue, unchanged


def document(rate, carrier):
    block = {"kind": "rank_normalized_cost", "rates": {"flight": rate, "work": 2.5},
             "version": "v7", "effective_from": "2030-01-01"}
    doc = {"sources": [], "rules": [], "questions": [], "objectives": []}
    if carrier == "document":
        doc["objectives"] = [{"id": "O-RANK", "zone": "PAIRING",
                              "direction": "minimize", "statement": "Нормированная стоимость",
                              "criterion": block}]
    else:
        raw = {"criterion": "NORMALIZED_COST", "rule": "R-RANK",
               "rates_param": "rates", "version": "v7", "effective_from": "2030-01-01",
               "rates": {"CP": block["rates"], "FO": block["rates"]}}
        doc["questions"] = [{"id": model.CRITERION_QUESTION_ID, "part": "3a",
                             "answer": {"status": "применён", "revision": 3,
                                        "raw": json.dumps(raw)}}]
    return doc, block


@pytest.mark.parametrize("carrier", ["document", "answer"])
@pytest.mark.parametrize("rate", [True, False, 0, -1, float("inf"), float("-inf"), float("nan")])
def test_invalid_rank_rate_is_not_an_executable_criterion(rate, carrier):
    doc, block = document(rate, carrier)
    if carrier == "document":
        assert not model.criterion_block_complete(block)
        assert not op._criterion_block_complete(block)
    assert model.derive_pairing_criterion(doc) is None


@pytest.mark.parametrize("carrier", ["document", "answer"])
@pytest.mark.parametrize("rate", [1, 1.5, 123.4])
def test_valid_rank_rate_and_provenance_remain_exact(rate, carrier):
    doc, block = document(rate, carrier)
    before = copy.deepcopy(doc)
    result = model.derive_pairing_criterion(doc)
    assert result["rates"] == {"flight": rate, "work": 2.5}
    assert result["direction"] == "minimize"
    assert result["rates_version"]["version"] == "v7"
    if carrier == "answer":
        assert result["as_written"] == doc["questions"][0]["answer"]["raw"]
        assert result["source_answer"]["revision"] == 3
    else:
        assert result["source_objective"] == "O-RANK"
    assert doc == before
    assert model.criterion_block_complete(block) and op._criterion_block_complete(block)


@pytest.mark.parametrize("rate", [float("inf"), float("-inf"), float("nan"), 1e308])
def test_nonfinite_or_overflowing_deci_is_a_named_refusal_not_runtime_error(rate):
    with pytest.raises(criteria.CriteriaError):
        criteria._deci(rate, "flight")
