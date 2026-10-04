"""The criterion projection retains the complete original answer as evidence."""
import copy

import pytest

from cdpm import model as m, export_solver
from test_cdpm_conditions_wave import _doc_with_goal, _ANS_0096, _ANS_MPD


@pytest.mark.parametrize("answer", [_ANS_MPD, _ANS_0096,
    _ANS_0096.replace("NORMALIZED_COST_0096", "NORMALIZED_COST")])
@pytest.mark.parametrize("long", [False, True])
def test_criterion_keeps_full_answer_without_changing_machine_fields(answer, long):
    raw = answer + ("\nПояснение авиакомпании. " * 40 + "КОНЕЦ ИСХОДНОГО ОТВЕТА" if long else "")
    doc = _doc_with_goal(raw)
    before = copy.deepcopy(doc)
    baseline = m._criterion_from_answer(_doc_with_goal(answer))
    result = m._criterion_from_answer(doc)
    assert result is not None and result["as_written"] == raw
    assert {k: v for k, v in result.items() if k != "as_written"} == {
        k: v for k, v in baseline.items() if k != "as_written"}
    assert export_solver.derive_pairing_criterion(doc)["as_written"] == raw
    assert m.readiness_matrix(doc) == m.readiness_matrix(_doc_with_goal(answer))
    assert doc == before


@pytest.mark.parametrize("kind", ["min_person_days", "rank_normalized_cost"])
@pytest.mark.parametrize("long", [False, True])
def test_objective_carrier_keeps_full_quote_too(kind, long):
    raw = ("Полная цитата критерия. " * 40 if long else "Критерий. ") + "КОНЕЦ ЦИТАТЫ"
    criterion = {"kind": kind, "version": "v1", "effective_from": "2026-09-01",
                 "as_written": raw}
    criterion.update({"rate_per_duty": 100} if kind == "min_person_days" else
                     {"rates": {"flight": 1.0, "work": 0.7}})
    doc = {"objectives": [{"id": "O-CRITERION", "zone": "PAIRING", "criterion": criterion}]}
    before = copy.deepcopy(doc)
    result = m.criterion_from_objectives(doc)
    assert result["as_written"] == raw
    assert result["source_objective"] == "O-CRITERION"
    assert result["kind"] == kind and result["rates_version"]["version"] == "v1"
    assert export_solver.derive_pairing_criterion(doc) == result
    assert doc == before
