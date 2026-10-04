"""K01 consumer scope controls; local catalogue/protocol checks only.

No support approval, exporter, solver or service is fabricated or invoked.
Malformed consumer lists below are explicit protocol boundary fixtures, not
claimed catalogue records or execution evidence.
"""
import copy
from pathlib import Path

import pytest

from cdpm import catalog_bridge as cb, model as m


@pytest.fixture(autouse=True)
def catalogue(monkeypatch):
    monkeypatch.setenv(cb.ENV_CATALOG, str(Path(__file__).resolve().parents[1]
                                         / "catalog-integration/0.7.0-draft.6"))
    cb.forget()
    yield
    cb.forget()


def problem(family="reserve_floors", zone="ROSTERING", *, hard=True,
            code="family-incomplete"):
    return {"code": code, "family": family, "zones": [zone], "hard": hard}


@pytest.mark.parametrize("hard,modes", [
    (True, {"feasibility-hard", "feasibility-soft", "optimization"}),
    (False, {"feasibility-soft", "optimization"}),
])
def test_real_roster_only_family_keeps_pairing_healthy(hard, modes):
    assert "builder_method" not in cb.executor_kinds("reserve_floors")
    assert "kotlin_constraint" in cb.executor_kinds("reserve_floors")
    p = problem(hard=hard)
    before = copy.deepcopy(p)
    assert m.family_consumer_tasks("reserve_floors") == ("rostering",)
    assert m.barrier_tasks(p) == {"ROSTERING"}
    assert m.barrier_affects(p) == {("rostering", mode) for mode in modes}
    assert p == before


def test_carrier_is_not_lost_when_it_differs_from_declared_consumer():
    p = problem(zone="PAIRING")
    before = copy.deepcopy(p)
    assert m.barrier_tasks(p) == {"PAIRING", "ROSTERING"}
    assert p == before


def test_unknown_family_cannot_narrow_missing_value_scope():
    family = "not-a-real-family-k01-control"
    assert not cb.executor_kinds(family)
    p = problem(family)
    assert m.family_consumer_tasks(family) == ("pairing", "rostering")
    assert m.barrier_tasks(p) == {"PAIRING", "ROSTERING"}


def test_unavailable_catalogue_is_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv(cb.ENV_CATALOG, str(tmp_path / "absent-catalogue"))
    cb.forget()
    assert m.family_consumer_tasks("reserve_floors") == ("pairing", "rostering")
    assert m.barrier_tasks(problem()) == {"PAIRING", "ROSTERING"}


@pytest.mark.parametrize("kinds", [(), ("new_unknown_executor",),
                                  ("builder_method", "new_unknown_executor"),
                                  ("check_template", "export_form", "check_code")])
def test_unknown_or_check_only_consumer_identity_is_fail_closed(monkeypatch, kinds):
    monkeypatch.setattr(cb, "executor_kinds", lambda _family: kinds)
    assert m.family_consumer_tasks("protocol-only-fixture") == ("pairing", "rostering")
    assert m.barrier_tasks(problem("protocol-only-fixture")) == {"PAIRING", "ROSTERING"}


@pytest.mark.parametrize("zone,task", [("PAIRING", "pairing"), ("ROSTERING", "rostering")])
@pytest.mark.parametrize("code", ["support-missing", "catalog-unavailable"])
def test_non_k01_barriers_keep_their_explicit_scope_and_modes(zone, task, code):
    # rest_floors really has both consumers, but K06's product/mode scope is
    # its own decision. A K01 helper must not broaden or reclassify it.
    assert {"builder_method", "kotlin_constraint"} <= set(cb.executor_kinds("rest_floors"))
    p = problem("rest_floors", zone, code=code)
    p["modes"] = ["feasibility-soft"]
    before = copy.deepcopy(p)
    assert m.barrier_tasks(p) == {zone}
    assert m.barrier_affects(p) == {(task, "feasibility-soft")}
    assert p == before


def test_k06_does_not_consult_k01_consumer_resolver(monkeypatch):
    def forbidden(_family):
        pytest.fail("Non-K01 barrier must not call the family consumer resolver")
    monkeypatch.setattr(m, "family_consumer_tasks", forbidden)
    p = problem("rest_floors", code="support-missing")
    assert m.barrier_tasks(p) == {"ROSTERING"}


def test_ordinary_question_is_not_reclassified_as_a_parameter_question():
    assert m.parameter_question_tasks({"id": "Q-ordinary", "part": "2"}) is None
