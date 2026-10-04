"""A10: a valid counterexample to a different predicate proves nothing."""
import copy
import hashlib
import json

import pytest

from cdpm import export_task_package as tp, logic_proof as lp, model
from cdpm.gates import gate_task_package as gate
from test_cdpm_logic_proof import _rule_duration
from test_cdpm_task_package import _ready_doc, _run_dir


def _minimum(subject="duty"):
    r = _rule_duration("min", subject)
    r["statement"] = "Продолжительность каждой смены не менее 12:00."
    r["logic"]["demand"]["as_written"] = "не менее 12:00"
    return r


def _files(tmp_path, rule):
    doc = _ready_doc()
    doc["rules"].append(rule)
    assert model.validate_model(doc) == []
    return tp.build_package(_run_dir(tmp_path, doc))


def _put_examples(files, examples):
    files["logic-examples.json"] = json.dumps(
        examples, ensure_ascii=False).encode("utf-8")
    manifest = json.loads(files["manifest.json"])
    manifest["logic_unproven"] = [u for u in manifest.get("logic_unproven", [])
                                  if u["rule"] not in examples]
    manifest["files"] = [f for f in manifest["files"]
                         if f["name"] != "logic-examples.json"]
    manifest["files"].append({
        "name": "logic-examples.json",
        "sha256": hashlib.sha256(files["logic-examples.json"]).hexdigest()})
    files["manifest.json"] = json.dumps(manifest).encode("utf-8")


def _gate(tmp_path, files):
    path = tmp_path / "proof.zip"
    path.write_bytes(tp.write_zip(files))
    return gate.check(path)[0]


@pytest.mark.parametrize("subject", ["duty", "assignment"])
def test_minimum_span_is_not_proven_by_an_adjacent_gap(subject):
    rule = _minimum(subject)
    spec, why = lp.logic_to_check_spec(rule)
    assert spec is None and "span" in why and "min" in why
    pair, why = lp.generate_pair(rule)
    assert pair is None and why


def test_unsupported_minimum_is_named_unproven_in_the_real_package(tmp_path):
    files = _files(tmp_path, _minimum())
    manifest = json.loads(files["manifest.json"])
    assert any(u["rule"] == "R-D" and u["reason"]
               for u in manifest.get("logic_unproven", []))
    assert "R-D" not in json.loads(files.get("logic-examples.json", b"{}"))
    assert _gate(tmp_path, files) == []


def _legacy_gap_pair():
    def dataset(second_start):
        return {"sources": [{"sid": "SOL", "file": "example.json",
                             "sha256": "0" * 64, "rows_seen": 2}],
                "tables": {"SOL": {"Смены": {
                    "columns": ["Табельный номер", "Начало", "Конец"],
                    "rows": [{"Табельный номер": "EX-1",
                              "Начало": "2030-01-01T06:00",
                              "Конец": "2030-01-01T07:00"},
                             {"Табельный номер": "EX-1",
                              "Начало": second_start,
                              "Конец": "2030-01-02T00:00"}]}}}}
    return {"spec": {"id": "L-R-D", "basis": {"rule": "R-D"},
                     "template": "минимальный-разрыв-часов",
                     "params": {"sheet": "SOL/Смены",
                                "group_column": "Табельный номер",
                                "start_column": "Начало", "end_column": "Конец",
                                "min_hours": 12}},
            "dataset_bad": dataset("2030-01-01T18:00"),
            "dataset_good": dataset("2030-01-01T20:00")}


def test_old_gap_proof_cannot_be_attached_to_minimum_span(tmp_path):
    pair = _legacy_gap_pair()
    # The pair is internally consistent for GAP, despite its one-hour duty.
    assert lp.check_pair(pair, "R-D") == []
    files = _files(tmp_path, _minimum())
    _put_examples(files, {"R-D": pair})
    problems = _gate(tmp_path, files)
    assert any("пара R-D" in p and "не доказ" in p for p in problems), problems


@pytest.mark.parametrize("corruption", ["threshold", "expected_units"])
def test_internally_green_pair_must_still_match_the_actual_rule(tmp_path, corruption):
    rule = _rule_duration()
    files = _files(tmp_path, rule)
    if corruption == "threshold":
        other = copy.deepcopy(rule)
        other["params"][0]["value"] = 600
        pair, why = lp.generate_pair(other)
        assert pair is not None and why == ""
    else:
        pair, _ = lp.generate_pair(rule)
        pair["dataset_bad"] = copy.deepcopy(pair["dataset_good"])
        pair["spec"]["expected_units"] = {"bad": 0, "good": 0}
    assert lp.check_pair(pair, "R-D") == []
    _put_examples(files, {"R-D": pair})
    problems = _gate(tmp_path, files)
    assert any("пара R-D" in p and "спецификац" in p for p in problems), problems


def test_supported_maximum_retains_a_real_positive_package(tmp_path):
    files = _files(tmp_path, _rule_duration())
    assert "R-D" in json.loads(files["logic-examples.json"])
    assert _gate(tmp_path, files) == []
