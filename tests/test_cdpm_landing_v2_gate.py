"""Actual in-process gate plus receiver; no subprocess, translator or solver.

Fixtures assemble the existing 1.18 metadata with its producer functions.
This exercises the complete gate on package bytes, not build_package or a
commercial release; the expected landing failures are independent literals.
"""
import copy

import pytest

from cdpm import export_task_package as producer, model as m
from cdpm.gates import gate_task_package as gate
from test_cdpm_landing_v2_reader import package, receive
from test_k01_semantic_transition import clean_bridge
from webapp import task_package as reader


def gate_fixture(tmp_path, monkeypatch):
    doc, rule, declared = package(tmp_path, monkeypatch)
    rule["applicability"] = producer.applicability_of(rule)
    rule["required_data"] = producer.required_data_of(rule)
    declared["allowed_modes"], declared["objectives"] = producer.allowed_modes(
        doc, spec_version=declared["spec_version"])
    declared["crew_size"] = producer.crew_size_of(doc)
    declared["readiness_matrix"] = producer.readiness_ladder(
        doc, spec_version=declared["spec_version"])
    assert m.validate_schema(doc) == m.validate_model(doc) == []
    return doc, rule, declared


def test_actual_gate_and_reader_keep_valid_original_pin(tmp_path, monkeypatch):
    doc, rule, declared = gate_fixture(tmp_path, monkeypatch)
    original = copy.deepcopy(rule["template_fit"]["family_contract"])
    result = receive(tmp_path, doc, declared)
    path = tmp_path / "landing.zip"
    before = path.read_bytes()
    problems, counters = gate.check(path)
    assert problems == [], problems
    assert counters["landing_archive_only"] is False
    assert result["model"]["rules"][0]["template_fit"]["family_contract"] == original
    assert original["catalog_version"] != declared["catalog"]["version"]
    assert path.read_bytes() == before


def test_actual_gate_archive_opt_in_cannot_authorize_payload(tmp_path, monkeypatch, capsys):
    doc, rule, declared = gate_fixture(tmp_path, monkeypatch)
    del rule["template_fit"]["schema_version"]
    rule["template_fit"]["params"]["min_days_off"].pop("condition")
    assert m.validate_schema(doc) == []
    declared["landing_compat"] = 1
    result = receive(tmp_path, doc, declared, landing_compat=1)
    path = tmp_path / "landing.zip"
    before = path.read_bytes()
    problems, counters = gate.check(path, landing_compat=1)
    assert problems == [], problems
    assert counters["landing_archive_only"] is True
    assert gate.main([str(path), "--landing-compat", "1"]) == 0
    assert "архивное чтение landing/1; исполнение запрещено" in capsys.readouterr().out
    rejected, _ = gate.check(path)
    assert any("landing-version" in words for words in rejected), rejected
    result.pop("landing_projection")
    with pytest.raises(reader.TaskPackageRefused) as error:
        reader.to_payload(result)
    assert error.value.code == "landing-version"
    assert path.read_bytes() == before


@pytest.mark.parametrize("mutation,code", [
    ("unknown-version", "landing-version"), ("null-version", "landing-version"),
    ("unit", "landing-incomplete"), ("condition", "landing-incomplete"),
    ("source", "landing-incomplete"), ("record_fingerprint", "landing-contract"),
    ("param_set", "landing-contract"), ("catalog", "landing-contract"),
])
def test_actual_gate_and_reader_refuse_the_same_landing_defect(
        tmp_path, monkeypatch, mutation, code):
    doc, rule, declared = gate_fixture(tmp_path, monkeypatch)
    fit = rule["template_fit"]
    # Compatibility never means a fallback for malformed modern data.
    declared["landing_compat"] = 1
    if mutation in ("unknown-version", "null-version"):
        fit["schema_version"] = "landing/3" if mutation == "unknown-version" else None
    elif mutation in ("unit", "condition", "source"):
        del fit["params"]["min_days_off"][mutation]
    elif mutation == "catalog":
        del declared["catalog"]
    else:
        fit["family_contract"][mutation] = "forged"
    with pytest.raises(reader.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared, landing_compat=1)
    assert error.value.code == code
    path = tmp_path / "landing.zip"
    before = path.read_bytes()
    problems, _ = gate.check(path, landing_compat=1)
    assert any(code in problem for problem in problems), problems
    assert path.read_bytes() == before
