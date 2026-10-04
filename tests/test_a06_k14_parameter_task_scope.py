"""K14: parameter questions follow actual catalogue consumer kinds as well as carriers.

Read-only catalogue, in-memory model and local button delivery. No real services.
"""
from pathlib import Path

import pytest

from cdpm import catalog_bridge as cb, model as m
from test_a06_k13_button_question_scope import run_question_gate
from test_a06_k13_question_scope_matrix import document


@pytest.fixture(autouse=True)
def catalogue(monkeypatch):
    monkeypatch.setenv(cb.ENV_CATALOG, str(Path(__file__).resolve().parents[1]
                                         / "catalog-integration/0.7.0-draft.3"))
    cb.forget()
    yield
    cb.forget()


def parameter_doc(family="rest_floors", parameter="min_rest_outstation", zone="ROSTERING"):
    doc = document(zone)
    doc["questions"][0]["id"] = f"Q-3b-параметр-{family}-{parameter}"
    return doc


@pytest.mark.parametrize("parameter", ["min_rest_home", "min_rest_outstation"])
def test_rest_parameter_blocks_both_consumers_even_with_only_rostering_carrier(parameter):
    doc = parameter_doc(parameter=parameter)
    assert {"builder_method", "kotlin_constraint"} <= set(cb.executor_kinds("rest_floors"))
    assert m.question_tasks(doc, doc["questions"][0]) == ("pairing", "rostering")
    for task in ("pairing", "rostering"):
        assert not m.readiness_matrix(doc)[task]["feasibility-hard"]["ready"]


def test_matrix_directly_rejects_cross_task_parameter_without_relying_on_scope_assertion():
    matrix = m.readiness_matrix(parameter_doc())
    assert not matrix["pairing"]["feasibility-hard"]["ready"]
    assert not matrix["pairing"]["feasibility-soft"]["ready"]


@pytest.mark.parametrize("source", ["extract", "package_path"])
@pytest.mark.parametrize("task", ["pairing", "rostering"])
def test_parameter_question_blocks_real_button_before_export(tmp_path, monkeypatch, source, task):
    doc = parameter_doc()
    run_question_gate(tmp_path, monkeypatch, source, doc, task, [doc["questions"][0]["id"]])


def test_rostering_only_parameter_does_not_block_healthy_pairing():
    doc = parameter_doc("reserve_floors", "reserve_crews")
    assert "builder_method" not in cb.executor_kinds("reserve_floors")
    assert m.question_tasks(doc, doc["questions"][0]) == ("rostering",)
    assert m.readiness_matrix(doc)["pairing"]["feasibility-hard"]["ready"]


def test_answered_shared_parameter_no_longer_blocks_either_task():
    doc = parameter_doc()
    doc["questions"][0]["answer"] = {"status": "применён"}
    assert not m.open_questions(doc)
    assert all(m.readiness_matrix(doc)[task]["feasibility-hard"]["ready"]
               for task in ("pairing", "rostering"))


@pytest.mark.parametrize("family,parameter", [("unknown", "missing"), ("rest_floors", "unknown")])
def test_unresolved_parameter_identity_cannot_be_scoped_away(family, parameter):
    doc = parameter_doc(family, parameter)
    assert m.question_tasks(doc, doc["questions"][0]) == ("pairing", "rostering")
    assert not m.readiness_matrix(doc)["pairing"]["feasibility-hard"]["ready"]


def test_shared_parameter_soft_carrier_does_not_remove_hard_feasibility():
    doc = parameter_doc()
    doc["rules"][0]["kind"] = "SOFT"
    matrix = m.readiness_matrix(doc)
    for task in ("pairing", "rostering"):
        assert matrix[task]["feasibility-hard"]["ready"]
        assert not matrix[task]["feasibility-soft"]["ready"]
