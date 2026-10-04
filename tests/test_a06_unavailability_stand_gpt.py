"""Declared unavailability reaches the real Python executor and native input.

Offline adapter/dispatcher tests only: no JVM, solver, model or HTTP call, and
no support approval. The independent arrival-day pair is declared before use.
"""
from __future__ import annotations

from copy import deepcopy
import socket
import subprocess

import pytest

from vmp import source_check as sc
from vmp import stand_adapters as sa, stand_solver as ss, stand_tasks as st


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("offline unavailability profile forbids external execution")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    monkeypatch.setenv("COSMOS_SKIP_CONFIG_MIGRATION", "1")


def world(*, healthy=False):
    return {
        "form": "schedule",
        "crews": [
            {"id": "P1", "base": "ST1", "rank": "CP", "ratings": ["T1"],
             "unavailable_days": ["2026-10-02"]},
            {"id": "P2", "base": "ST1", "rank": "CP", "ratings": ["T1"],
             "unavailable_days": []},
        ],
        "flights": [{"id": "F1", "from": "ST1", "to": "ST2", "fleet_type": "T1",
                     "dep": "2026-10-01T23:00", "arr": "2026-10-02T01:00"}],
        "assignments": [{"crew": "P2" if healthy else "P1", "flight": "F1", "seat": 0}],
        "horizon": {"from": "2026-10-01", "to": "2026-10-03"},
        "params": {"min_rest_home": 660, "min_rest_outstation": 700},
        "history": None,
        "stand": {"nights_per_layover": 2, "w_night": 1},
    }


def ctx(source, findings):
    return {"world": source, "family": "crew_unavailability",
            "families": ["crew_unavailability"],
            "meta": {"crew_unavailability": {"params": [], "kind": "HARD",
                      "zone": "ROSTERING", "registry_rev": "synthetic-test"}},
            "expected": {"symbol": "_run_unavailability", "findings": findings}}


def test_declared_days_reach_native_problem_without_mutating_world():
    source = world()
    before = deepcopy(source)
    problem = sa.world_to_problem(source)
    assert [e["unavailableDays"] for e in problem["employees"]] == [["2026-10-02"], []]
    assert problem["flightAssignments"][0]["employee"] == "P1"
    problem["employees"][0]["unavailableDays"].append("2026-10-03")
    assert source == before


def test_both_native_and_certificate_source_tables_retain_declared_days():
    source = world()
    before = deepcopy(source)
    expected = {"columns": ["Табельный номер", "С", "По"],
                "rows": [{"Табельный номер": "P1", "С": "2026-10-02", "По": "2026-10-02"}]}
    assert ss._sheets(source)["Отпуска"] == expected
    assert sa._reference_tables(source)["S1"]["Отпуска"] == expected
    assert source == before


@pytest.mark.parametrize("healthy,findings", [(False, 1), (True, 0)])
def test_real_source_executor_judges_declared_arrival_pair(monkeypatch, healthy, findings):
    source = world(healthy=healthy)
    before = deepcopy(source)
    actual = sc._run_unavailability
    calls = []

    def capture(spec, timeline):
        calls.append((deepcopy(spec), deepcopy(timeline)))
        return actual(spec, timeline)

    monkeypatch.setattr(sc, "_run_unavailability", capture)
    outcome = st._kind_outcome("check_code", ctx(source, findings))
    assert outcome.result == "passed", outcome.note
    assert outcome.got == findings
    assert len(calls) == 1
    assert calls[0][0]["kind"] == "unavailability"
    assert calls[0][1]["employees"]["P1"]["unavailableDays"] == ["2026-10-02"]
    assert source == before


@pytest.mark.parametrize("day,dep,arr,expected", [
    ("2026-10-01", "2026-10-01T23:00", "2026-10-02T01:00", 1),
    ("2026-10-02", "2026-10-01T23:00", "2026-10-02T00:00", 1),
    ("2026-10-02", "2026-10-02T00:00", "2026-10-02T02:00", 1),
    ("2026-10-03", "2026-10-01T23:00", "2026-10-02T01:00", 0),
])
def test_endpoint_boundaries_and_neighbor_day(day, dep, arr, expected):
    source = world()
    source["crews"][0]["unavailable_days"] = [day]
    source["flights"][0].update(dep=dep, arr=arr)
    outcome = st._kind_outcome("check_code", ctx(source, expected))
    assert outcome.result == "passed", outcome.note
    assert outcome.got == expected


def test_both_endpoint_days_are_distinct_python_findings_not_copied_kotlin_matches():
    source = world()
    source["crews"][0]["unavailable_days"] = ["2026-10-01", "2026-10-02"]
    outcome = st._kind_outcome("check_code", ctx(source, 2))
    assert outcome.result == "passed", outcome.note
    assert outcome.got == 2  # Native constraint has one assignment, not two.


def test_empty_explicit_lists_and_deadhead_are_not_reinterpreted():
    source = world()
    source["flights"][0]["deadhead"] = True
    assert st._kind_outcome("check_code", ctx(source, 1)).result == "passed"
    source["crews"][0]["unavailable_days"] = []
    assert st._kind_outcome("check_code", ctx(source, 0)).result == "passed"
    assert ss._sheets(source)["Отпуска"] == {
        "columns": ["Табельный номер", "С", "По"], "rows": []}


@pytest.mark.parametrize("bad", [None, True, "2026-10-02", [True], [None],
                                  [""], ["2026-02-30"], ["2026-10-2"],
                                  ["2026-10-02T00:00"], [" 2026-10-02"],
                                  ["2026-10-02", "2026-10-02"]])
def test_malformed_explicit_day_lists_fail_in_every_carrier(bad):
    source = world()
    source["crews"][0]["unavailable_days"] = bad
    before = deepcopy(source)
    for adapter in (sa.world_to_problem, sa._reference_tables, ss._sheets):
        with pytest.raises(sa.StandWorldError, match="unavailable_days"):
            adapter(source)
    outcome = st._kind_outcome("check_code", ctx(source, 0))
    assert outcome.result == "failed"
    assert source == before


@pytest.mark.parametrize("mutation", ["missing-one-list", "duplicate-employee", "missing-employee"])
def test_partial_or_ambiguous_explicit_crew_carrier_is_not_empty(mutation):
    source = world()
    if mutation == "missing-one-list":
        del source["crews"][1]["unavailable_days"]
    elif mutation == "duplicate-employee":
        source["crews"][1]["id"] = "P1"
    else:
        source["crews"][0]["id"] = ""
    for adapter in (sa.world_to_problem, sa._reference_tables, ss._sheets):
        with pytest.raises(sa.StandWorldError, match="unavailable_days"):
            adapter(source)


def test_legacy_world_keeps_existing_carriers_but_cannot_claim_new_check_code():
    source = world()
    for crew in source["crews"]:
        del crew["unavailable_days"]
    assert all("unavailableDays" not in e for e in sa.world_to_problem(source)["employees"])
    assert "Отпуска" not in sa._reference_tables(source)["S1"]
    assert ss._sheets(source)["Отпуска"] == {"columns": ["Табельный номер", "Дата"], "rows": []}
    outcome = st._kind_outcome("check_code", ctx(source, 0))
    assert outcome.result == "failed" and "unavailable_days" in outcome.note


@pytest.mark.parametrize("findings", [True, -1, 1.0, "1", None])
def test_unavailability_expected_count_is_exact_nonnegative_integer(findings):
    assert st._kind_outcome("check_code", ctx(world(), findings)).result == "failed"


def test_wrong_oracle_fails_and_executor_mutation_reaches_real_judge(monkeypatch):
    assert st._kind_outcome("check_code", ctx(world(), 0)).result == "failed"
    actual = sc._run_unavailability

    def drop_actual_findings(spec, timeline):
        result = actual(spec, timeline)
        result.violations.clear()
        result.verdict = "ok"
        return result

    monkeypatch.setattr(sc, "_run_unavailability", drop_actual_findings)
    assert st._kind_outcome("check_code", ctx(world(), 1)).result == "failed"


@pytest.mark.parametrize("mutation", ["crew", "flight", "duplicate", "boolean-seat",
                                      "missing-seat", "empty", "omitted"])
def test_lost_or_ambiguous_assignments_never_make_a_healthy_zero(mutation):
    source = world()
    if mutation == "crew":
        source["assignments"][0]["crew"] = "P999"
    elif mutation == "flight":
        source["assignments"][0]["flight"] = "F999"
    elif mutation == "duplicate":
        source["assignments"].append({"crew": "P2", "flight": "F1", "seat": 0})
    elif mutation == "boolean-seat":
        source["assignments"][0]["seat"] = False
    elif mutation == "missing-seat":
        source["assignments"][0]["seat"] = 1
    elif mutation == "empty":
        source["assignments"] = []
    else:
        del source["assignments"]
    outcome = st._kind_outcome("check_code", ctx(source, 0))
    assert outcome.result == "failed", outcome.note


@pytest.mark.parametrize("mutation", ["not-checked", "bool-count", "zero-count", "contradiction"])
def test_broken_actual_executor_result_is_not_a_count_only_pass(monkeypatch, mutation):
    actual = sc._run_unavailability

    def broken(spec, timeline):
        result = actual(spec, timeline)
        if mutation == "not-checked":
            result.verdict = "not_checkable"
        elif mutation == "bool-count":
            result.objects_seen = True
        elif mutation == "zero-count":
            result.objects_seen = 0
        else:
            result.verdict = "ok"
        return result

    monkeypatch.setattr(sc, "_run_unavailability", broken)
    assert st._kind_outcome("check_code", ctx(world(), 1)).result == "failed"


@pytest.mark.parametrize("healthy,findings", [(False, 1), (True, 0)])
def test_neighbor_certificate_executor_reads_the_same_declared_source(healthy, findings):
    from vmp.run import fit_checks as fc
    source = world(healthy=healthy)
    context = ctx(source, findings)
    svod, dataset = sa.world_to_certificate(source, context["families"], meta=context["meta"])
    rule = svod["rules"][0]
    check, found = fc._crew_unavailability(rule["id"], rule, dataset, fc._Counter())
    assert check["status"] == "прогнана", check.get("reason")
    assert check["rows_checked"] == 1
    assert len(found) == findings
    if findings:
        assert "S1/Отпуска!2" in found[0]["evidence"]
        assert found[0]["target"] == {"sid": "SOL", "sheet": "Назначения", "row": 2}


def test_python_check_never_grants_kotlin_approval():
    outcome = st._kind_outcome("kotlin_constraint", {
        **ctx(world(), 1),
        "expected": {"constraint": "Employee unavailable", "amount": 1, "matches": 1}})
    assert outcome.result == "skipped"
    assert outcome.got is None


def test_real_source_executor_is_in_freshness_map_and_change_invalidates(tmp_path):
    class CatalogIdentity:
        def version(self):
            return "test-only"

        def fingerprint(self):
            return "1" * 64

    assert "vmp/source_check.py" in st.CODE_FILES
    for name in st.CODE_FILES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((st.REPO / name).read_bytes())
    cat = CatalogIdentity()
    doc = {"catalog_version": cat.version(), "catalog_fingerprint": cat.fingerprint(),
           "code": st.code_fingerprints(tmp_path), "results": []}
    assert st.stale_reasons(doc, cat, repo=tmp_path) == []
    executor = tmp_path / "vmp/source_check.py"
    executor.write_bytes(executor.read_bytes() + b"\n# isolated freshness mutation\n")
    reasons = st.stale_reasons(doc, cat, repo=tmp_path)
    assert reasons == ["отпечаток vmp/source_check.py изменился с записи результатов"]
