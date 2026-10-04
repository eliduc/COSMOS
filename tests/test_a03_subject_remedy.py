"""A03 minor: an intact subject refusal must explain the recovery action."""
import copy
import json
import pytest

from cdpm import smt_verify as sv
from cdpm.gates import gate_task_package as gate


def subject_inputs():
    doc = {"rules": [{"id": "R-SUBJECT", "kind": "HARD", "statement": "x <= 1"}]}
    sha = sv.subject_fingerprint(doc)
    report = {"schema_version": sv.REPORT_SCHEMA, "verdict": "sat",
              "model_sha256": "a" * 64, "subject_sha256": sha,
              "translations": [{"rule": "R-SUBJECT", "status": "translated", "smt": "(<= x 1)"}]}
    files = {"smt-report.json": json.dumps(report).encode()}
    manifest = {"consistency": {
        "verdict": "sat", "model_sha256": "a" * 64,
        "n_rules": 1, "n_translated": 1,
        "translator": {"role": sv.TRANSLATOR_ROLE, "provider": None, "model": None},
        "report": sv.REPORT_REL, "subject_sha256": sha, "subject": "canonical"}}
    assert gate._check_consistency(manifest, {}, files, doc) == []
    return doc, report, manifest


def test_subject_refusal_keeps_both_fingerprints_and_names_remedy():
    doc, report, manifest = subject_inputs()
    sha = report["subject_sha256"]
    files = {"smt-report.json": json.dumps(report).encode()}
    changed = copy.deepcopy(doc)
    changed["rules"][0]["statement"] = "x <= 2"
    other = sv.subject_fingerprint(changed)
    problems = gate._check_consistency(manifest, {}, files, changed)
    refusal = next(p for p in problems if "manifest.consistency.subject_sha256 не совпадает" in p)
    assert sha[:12] in refusal and other[:12] in refusal
    assert "выпустите пакет заново" in refusal


@pytest.mark.parametrize("field,value,diagnosis", [
    ("subject", "foreign", "subject вне перечня"),
    ("report", "wrong.json", "report не указывает"),
    ("subject_sha256", "not-sha", "subject_sha256 не sha256"),
])
def test_subject_manifest_shape_refusals_name_remedy(field, value, diagnosis):
    doc, report, manifest = subject_inputs()
    manifest["consistency"][field] = value
    files = {"smt-report.json": json.dumps(report).encode()}
    refusal = next(p for p in gate._check_consistency(manifest, {}, files, doc)
                   if diagnosis in p)
    assert "пересоберите справку" in refusal
    assert "выпустите пакет заново" in refusal


@pytest.mark.parametrize("mutation", ["missing", "foreign", "outdated_report"])
def test_report_subject_refusal_names_same_remedy(mutation):
    doc, report, manifest = subject_inputs()
    if mutation == "missing":
        report.pop("subject_sha256")
    elif mutation == "foreign":
        report["subject_sha256"] = "f" * 64
    else:
        doc["rules"][0]["statement"] = "x <= 2"
        manifest["consistency"]["subject_sha256"] = sv.subject_fingerprint(doc)
    files = {"smt-report.json": json.dumps(report).encode()}
    problems = gate._check_consistency(manifest, {}, files, doc)
    refusal = next(p for p in problems if "отчёт не о том предмете проверки" in p)
    assert "subject_sha256 отчёта" in refusal
    assert "пересоберите справку" in refusal
    assert "выпустите пакет заново" in refusal
