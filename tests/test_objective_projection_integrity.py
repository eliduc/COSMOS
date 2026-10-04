"""Local A06/A08 integration: the copied projector must not erase evidence."""
import copy

import pytest

from vmp import objective_projection as op


class Catalog:
    def criteria(self):
        return {"min_person_days": {"direction": "minimize"},
                "total_pairing_cost": {"direction": "minimize"}}

    def version(self):
        return "0.6.0-synthetic"

    def fingerprint(self):
        return "synthetic-not-a-release"


def doc():
    return {"meta": {"customer": "Synthetic"}, "sources": [{"sid": "S1"}],
            "rules": [{"id": "R1", "kind": "SOFT", "zone": "PAIRING",
                       "weight": 1, "statement": "penalty", "sources": [{"sid": "S1"}]}],
            "objectives": [{"id": "O1", "zone": "PAIRING", "direction": "minimize",
                            "metric": "total_pairing_cost", "sources": [{"sid": "S1"}],
                            "criterion": {"kind": "min_person_days", "rate_per_duty": 1,
                                          "version": "v1", "effective_from": "2030-01-01",
                                          "as_written": "source-" * 100}}],
            "questions": []}


@pytest.mark.parametrize("field", ["components", "objectives"])
def test_duplicate_identity_in_presented_projection_is_refused(field):
    model = doc()
    got = op.project(model, Catalog())
    got["pairing"][field].append(copy.deepcopy(got["pairing"][field][0]))
    problems = op.compare(got, model, Catalog())
    assert any(field in str(p) for p in problems), problems


@pytest.mark.parametrize("field", ["component", "criterion"])
def test_boolean_does_not_equal_a_numeric_weight_or_rate(field):
    model = doc()
    got = op.project(model, Catalog())
    if field == "component":
        got["pairing"]["components"][0]["weight"] = True
    else:
        got["pairing"]["criterion"]["rate_per_duty"] = True
    assert op.compare(got, model, Catalog())


@pytest.mark.parametrize("corruption", ["absent", "null", "text"])
def test_criterion_version_is_required_even_when_whole_container_is_missing(corruption):
    model = doc()
    got = op.project(model, Catalog())
    criterion = got["pairing"]["criterion"]
    if corruption == "absent":
        del criterion["rates_version"]
    else:
        criterion["rates_version"] = None if corruption == "null" else "v1"
    assert op.compare(got, model, Catalog())


def test_complete_criterion_quote_is_preserved():
    model = doc()
    got = op.project(model, Catalog())
    assert got["pairing"]["criterion"]["as_written"] == model["objectives"][0]["criterion"]["as_written"]
    assert op.compare(got, model, Catalog()) == []


def test_explicit_context_criterion_is_actually_compared():
    model = doc()
    model["objectives"] = []
    context = {"criterion": {"kind": "min_person_days", "direction": "minimize",
                              "rate_per_duty": 4, "rates_version": {
                                  "version": "v1", "effective_from": "2030-01-01"}}}
    got = op.project(model, Catalog(), context=context)
    assert op.compare(got, model, Catalog(), context=context) == []
    got["pairing"]["criterion"]["rate_per_duty"] = 400
    assert op.compare(got, model, Catalog(), context=context)


def test_project_refuses_a_context_direction_that_disagrees_with_catalog():
    model = doc()
    model["objectives"] = []
    with pytest.raises(op.ObjectiveProjectionError):
        op.project(model, Catalog(), context={"criterion": {
            "kind": "min_person_days", "direction": "maximize", "rate_per_duty": 1}})


def test_numeric_json_forms_remain_equivalent_and_healthy():
    model = doc()
    got = op.project(model, Catalog())
    got["pairing"]["criterion"]["rate_per_duty"] = 1.0
    assert op.compare(got, model, Catalog()) == []

