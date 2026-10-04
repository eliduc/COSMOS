"""A06 model/readiness use the real validated catalogue, not a reader stub.

Only temporary synthetic catalogue copies and in-memory documents are used.
No release, bindings, executor or production package version is activated.
"""
import copy
import json

import pytest

from cdpm import catalog_bridge as cb, model
from vmp import catalog_access as ca
from test_catalog_060_versioned_schema import modern_docs
from test_template_catalog_validate import _broken
from test_cdpm_task_package import _ready_doc


def use_real_catalogue(tmp_path, monkeypatch, direction="minimize"):
    def prepare(docs):
        modern_docs(docs)
        for row in docs["criteria.json"]["criteria"]:
            row["direction"] = direction
    folder = _broken(tmp_path, prepare)
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    cb.forget()
    ca.forget()
    assert cb.catalog().version() == "0.7.0"
    assert not [p for p in cb.catalog().validate() if p.severity == "error"]
    return cb.catalog()


@pytest.fixture(autouse=True)
def clean_catalogue_cache():
    yield
    cb.forget()
    ca.forget()


def document(carrier="document", *, direction="minimize"):
    doc = _ready_doc()
    answer = copy.deepcopy(doc["questions"][0])
    doc["rules"] = []
    doc["questions"] = []
    objective = doc["objectives"][0]
    objective["direction"] = direction
    if carrier == "document":
        objective["criterion"] = {
            "kind": "min_person_days", "rate_per_duty": 77,
            "version": "v7", "effective_from": "2030-01-01",
            "per": "день командировки", "as_written": "Полная цитата ставки"}
    elif carrier == "answer":
        doc["objectives"] = []
        answer["id"] = model.CRITERION_QUESTION_ID
        answer["rule_refs"] = []
        answer["answer"]["raw"] = json.dumps({
            "criterion": "MIN_PERSON_DAYS", "rate_per_duty": 123,
            "rule": "O-1", "version": "v7", "effective_from": "2030-01-01"})
        doc["questions"] = [answer]
    return doc


@pytest.mark.parametrize("direction", ["minimize", "maximize"])
@pytest.mark.parametrize("carrier", ["document", "answer", "prose"])
def test_model_derivation_keeps_catalogue_direction(tmp_path, monkeypatch, direction, carrier):
    use_real_catalogue(tmp_path, monkeypatch, direction)
    doc = document(carrier, direction=direction)
    before = copy.deepcopy(doc)
    got = model.derive_pairing_criterion(doc)
    assert got is not None
    assert got["direction"] == direction
    if carrier == "document":
        assert got["per"] == "день командировки"
        assert model.criterion_from_objectives(doc)["direction"] == direction
    assert doc == before


@pytest.mark.parametrize("task,zone,metric", [
    ("pairing", "PAIRING", "total_pairing_cost"),
    ("rostering", "ROSTERING", "credit_hours_spread"),
])
def test_readiness_rejects_direction_conflict_without_blocking_feasibility(
        tmp_path, monkeypatch, task, zone, metric):
    use_real_catalogue(tmp_path, monkeypatch)
    doc = document()
    obj = doc["objectives"][0]
    obj["zone"], obj["metric"] = zone, metric
    if task == "rostering":
        del obj["criterion"]
    healthy = model.readiness_matrix(doc)[task]
    assert healthy["optimization"]["ready"] is True
    assert model.validate_model(doc) == []
    obj["direction"] = "maximize"
    bad = model.readiness_matrix(doc)[task]
    assert bad["optimization"]["ready"] is False
    assert bad["optimization"]["state"] == "нет"
    assert "направлен" in bad["optimization"]["note"]
    assert "O-1" in bad["optimization"]["note"]
    assert any("criterion-direction" in p for p in model.validate_model(doc))
    if task == "pairing":
        assert not model.criterion_block_complete(obj["criterion"], objective=obj)
        assert model.criterion_from_objectives(doc) is None
    assert bad["feasibility-hard"] == healthy["feasibility-hard"]
    assert bad["feasibility-soft"] == healthy["feasibility-soft"]


def test_readiness_does_not_invent_catalogue_direction_for_an_unknown_metric(tmp_path, monkeypatch):
    use_real_catalogue(tmp_path, monkeypatch)
    doc = document()
    assert model.readiness_matrix(doc)["pairing"]["optimization"]["ready"] is True
    doc["objectives"][0]["metric"] = "invented_metric"
    cell = model.readiness_matrix(doc)["pairing"]["optimization"]
    assert cell["ready"] is False
    assert "invented_metric" in cell["note"]
