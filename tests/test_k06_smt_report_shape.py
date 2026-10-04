"""Malformed cached-report containers must produce a refusal, not a crash/PASS.

Uses explicit synthetic saved reports and the real consumers. No live model,
SMT translation, operational solve, full package build or transport is run.
"""
import copy
import json

import pytest

from cdpm import export_task_package as tp, smt_verify as sv
from cdpm.gates import gate_task_package as gate
from tests.test_k06_smt_report_json_identity import subject


CASES = [
    ("core", 1), ("core", 0), ("core", True), ("core", False),
    ("core", "R-A"), ("core", {"R-A": True}), ("core", [1]),
    ("dropped", 1), ("dropped", 0), ("dropped", True),
    ("dropped", ""), ("dropped", {}), ("dropped", [None]),
    ("malformed", 1), ("malformed", False), ("malformed", {}),
    ("malformed", [None]), ("malformed", [1]),
    ("translations", 1), ("translations", {}),
    ("translations-extra", None), ("translations-extra", 1),
]


def _bad_report(subject, field, value):
    report = copy.deepcopy(subject[3])
    if field == "translations-extra":
        report["translations"].append(value)
    else:
        report[field] = value
    if field == "core":
        report["verdict"] = "unsat"
    return report


@pytest.mark.parametrize("field,value", CASES)
def test_completeness_names_bad_report_shape(subject, field, value):
    report = _bad_report(subject, field, value)
    details = sv.report_completeness(report, subject[2])
    name = "translations" if field == "translations-extra" else field
    assert any(name in p for p in details["malformed"]), details
    assert any(name in p for p in details["malformed_words"]), details


@pytest.mark.parametrize("field,value", CASES)
def test_release_refuses_bad_report_shape_with_named_error(subject, field, value, monkeypatch):
    run, canon, released, _, _, which = subject
    report = _bad_report(subject, field, value)
    raw = json.dumps(report).encode("utf-8")
    path = sv.report_path(run, which)
    path.write_bytes(raw)

    def no_recheck(*args, **kwargs):
        pytest.fail("The saved report's shape is judged without calling a new solver")

    monkeypatch.setattr(tp, "_run_smt_check", no_recheck)
    with pytest.raises(tp.TaskPackageError) as error:
        tp.consistency_of(run, canon, released)
    name = "translations" if field == "translations-extra" else field
    assert name in str(error.value), str(error.value)
    assert error.value.code in (tp.SMT_INCOMPLETE, tp.SMT_UNAVAILABLE)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("field,value", CASES)
def test_package_consistency_names_bad_report_shape(subject, field, value):
    _, _, released, _, cons, _ = subject
    report = _bad_report(subject, field, value)
    problems = gate._check_consistency(
        {"consistency": cons}, {},
        {tp.SMT_REPORT_NAME: json.dumps(report).encode("utf-8")}, released)
    name = "translations" if field == "translations-extra" else field
    assert problems and any(name in p for p in problems), problems


@pytest.mark.parametrize("empty", [None, []])
def test_optional_empty_containers_retain_healthy_report(subject, empty, monkeypatch):
    run, canon, released, report, cons, which = subject
    for key in ("core", "dropped", "malformed"):
        report[key] = copy.deepcopy(empty)
    raw = json.dumps(report).encode("utf-8")
    sv.report_path(run, which).write_bytes(raw)
    monkeypatch.setattr(tp, "_run_smt_check",
                        lambda *a, **kw: pytest.fail("Healthy cache must stay usable"))
    assert sv.report_completeness(report, released)["malformed"] == []
    assert tp.consistency_of(run, canon, released) == cons
    assert gate._check_consistency(
        {"consistency": cons}, {}, {tp.SMT_REPORT_NAME: raw}, released) == []


def test_healthy_unsat_core_retains_named_contradiction(subject):
    run, canon, released, report, _, which = subject
    report.update(verdict="unsat", core=["R-A"])
    raw = json.dumps(report).encode("utf-8")
    path = sv.report_path(run, which)
    path.write_bytes(raw)
    with pytest.raises(tp.TaskPackageError) as error:
        tp.consistency_of(run, canon, released)
    assert error.value.code == tp.SMT_CONTRADICTION
    assert "R-A" in str(error.value)
    assert sv.report_completeness(report, released)["malformed"] == []
    assert path.read_bytes() == raw


def test_real_report_malformed_entries_are_preserved(subject):
    report = subject[3]
    report["malformed"] = [{"field": "asserts", "index": 2,
                            "kind": "null", "text": "null"}]
    details = sv.report_completeness(report, subject[2])
    assert details["malformed"] == ["asserts[2]"]
    assert details["malformed_words"] == ["asserts[2]: не объект (null)"]


def test_valid_diagnostics_survive_an_adjacent_bad_container_element(subject):
    report = subject[3]
    report["malformed"] = [{"field": "asserts", "index": 2,
                            "kind": "null", "text": "null"}, None]
    report["dropped"] = ["R-FOREIGN", None]
    details = sv.report_completeness(report, subject[2])
    assert details["n_translated"] == 1
    assert details["foreign"] == ["R-FOREIGN"]
    assert details["malformed"] == ["asserts[2]", "dropped[1]", "malformed[1]"]
    assert details["malformed_words"][0] == "asserts[2]: не объект (null)"
