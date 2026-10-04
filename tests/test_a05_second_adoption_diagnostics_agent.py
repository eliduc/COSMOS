"""A05 minor diagnostic: real extraction must retain an adoption refusal.

One synthetic MD clause and canned completion functions use the existing
extractor interface. Assembly, repair, adoption, carrier validation and the
second-reading.json writer are real. No new keyword API is called: the red
baseline must reach the completed artifact before asking for the lost reason.
No HTTP, live model, SMT/JVM, service, operational run or customer input.
"""
import copy
import json
import re

import pytest

from cdpm import extract, freeze, model as m, second_reading
from test_a05_extracted_relation_contract_agent import (
    CLAUSE, FAMILY, NAME, _candidate, _post_assembly_pair, catalog,
)


def _canned(candidate, calls):
    original = copy.deepcopy(candidate)

    def complete(prompt):
        if re.search(r"^\[п\.1\] ", prompt, flags=re.M):
            calls.append("candidates")
            return json.dumps({"candidates": [copy.deepcopy(original)]}, ensure_ascii=False)
        if "Непотреблённая величина:" in prompt and "Законные слоты этой строки" in prompt:
            # An explicit model refusal, not a fake repair verdict. Actual
            # repair can still use the second reader's real validated hint.
            calls.append("repair-declined")
            return json.dumps({"slot": None, "reason": "Тестовый ответ не предлагает нового слота"},
                              ensure_ascii=False)
        if "Строки свода" in prompt:
            calls.append("contradictions")
            return '{"contradictions": []}'
        raise AssertionError("Unexpected completion stage in the bounded extraction fixture")

    return complete


def _load_output(out):
    raw = (out / "model.json").read_bytes()
    doc = m.loads_strict(raw)
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    assert m.carrier_problems(doc) == []
    return doc


@pytest.mark.parametrize("relation", ["min", "max"], ids=["carrier-conflict", "healthy-equal"])
def test_real_extract_artifact_retains_adoption_refusal_without_mutating_row(
        tmp_path, monkeypatch, catalog, relation):
    monkeypatch.setenv("CDPM_PARALLEL", "1")
    assert next(p for p in catalog.family(FAMILY)["params"] if p["name"] == NAME)["relation"] == "max"

    # Existing actual assembler fixture proves the two legal input states and
    # the unchanged old public adoption return contract before the full run.
    first, second = _post_assembly_pair(relation)
    first_before, second_before = copy.deepcopy(first), copy.deepcopy(second)
    adopted = second_reading.adopt_stronger_fits(first, second, {("S1", "п.1"): CLAUSE})
    assert isinstance(adopted, list)
    assert second == second_before
    if relation == "min":
        assert adopted == [] and first == first_before
    else:
        assert len(adopted) == 1 and adopted[0]["family"] == FAMILY
        assert m.carrier_problems(first) == []

    source = tmp_path / "src" / "synthetic-adoption.md"
    source.parent.mkdir()
    source.write_text("1. " + CLAUSE + "\n", encoding="utf-8")
    kit = tmp_path / "kit"
    frozen = freeze.freeze([source], kit)
    assert frozen["files"][0]["clause_count"] == 1
    clauses = json.loads((kit / "clauses" / "S1.json").read_bytes())["clauses"]
    assert clauses == [{"anchor": "п.1", "kind": "line", "text": "1. " + CLAUSE}]
    source_before = source.read_bytes()
    kit_before = {p.relative_to(kit).as_posix(): p.read_bytes() for p in kit.rglob("*") if p.is_file()}
    first_candidate = _candidate(relation)
    del first_candidate["template_fit"]
    second_candidate = _candidate("max")
    candidates_before = copy.deepcopy((first_candidate, second_candidate))

    baseline_row = None
    if relation == "min":
        # A genuine first-only run is the whole-row control. It includes the
        # same ordinary question/balance stages; comparing only against an
        # early assembler row would mistake their normal edits for adoption.
        baseline_calls = []
        baseline_out = tmp_path / "first-only"
        baseline = extract.run_extract(kit, baseline_out,
            _canned(first_candidate, baseline_calls), "A05 synthetic adoption diagnostic")
        assert baseline["second_reading"] is None
        assert baseline_calls.count("candidates") == 1
        baseline_doc = _load_output(baseline_out)
        baseline_row, = baseline_doc["rules"]
        assert baseline_row["template_fit"]["category"] == "C"
        assert baseline_row["params"][0]["relation"] == "min"
        assert not (baseline_out / "second-reading.json").exists()

    first_calls, second_calls = [], []
    out = tmp_path / "with-second"
    summary = extract.run_extract(kit, out,
        _canned(first_candidate, first_calls), "A05 synthetic adoption diagnostic",
        second_llm=_canned(second_candidate, second_calls))
    assert first_calls.count("candidates") == second_calls.count("candidates") == 1
    assert (first_candidate, second_candidate) == candidates_before
    doc = _load_output(out)
    row, = doc["rules"]
    artifact = out / "second-reading.json"
    assert artifact.is_file(), "The real extraction must finish and persist its second-reading artifact"
    report = json.loads(artifact.read_bytes())
    assert report["anchors"] == 1 and report["adopted_missing"] == []
    assert summary["second_reading"]["adopted"] == len(report["adopted"])
    assert source.read_bytes() == source_before
    assert {p.relative_to(kit).as_posix(): p.read_bytes() for p in kit.rglob("*") if p.is_file()} == kit_before
    expected_rejections = 1 if relation == "min" else 0
    assert summary["second_reading"].get("adoption_rejections_count") == expected_rejections
    assert report.get("adoption_rejections_count") == expected_rejections

    if relation == "max":
        # Healthy repair may consume the second-reading hint before adoption.
        # Inspect the actual final state without attributing it to that stage.
        assert row["template_fit"]["category"] == "A", row
        assert row["template_fit"]["params"][NAME]["relation"] == "max"
        assert report.get("adoption_rejections", []) == []
        return

    assert row == baseline_row, "The rejected second fit must not change the first reader's whole row"
    assert report["adopted"] == [] and summary["second_reading"]["adopted"] == 0
    assert row["template_fit"]["category"] == "C"
    expected_reason = (f"{row['id']}: параметр {NAME} — два носителя расходятся — "
                       "отношение: посадка 'max' против params[] 'min'")
    rejected = report.get("adoption_rejections")
    assert isinstance(rejected, list) and len(rejected) == 1, {
        "missing_persisted_carrier_reason": expected_reason, "actual_second_reading": report}
    item, = rejected
    assert item["anchor"] == ["S1", "п.1"]
    assert item["row"] == item["candidate_row"] == row["id"]
    assert item["family"] == FAMILY and item["code"] == "param-carriers-diverge"
    assert item["reasons"] == [expected_reason]
