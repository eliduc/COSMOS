"""CDPM-419: both actual readers recompute context from model, never objective."""
import copy
import hashlib
import json

import pytest

from cdpm import catalog_bridge as cb, export_task_package as tp, model
from cdpm.gates import gate_task_package as producer
from vmp import catalog_access as ca, objective_projection as op
from webapp import task_package as consumer
from test_cdpm_task_package import _ready_doc, _run_dir
from test_w17b_objective_projection import _catalog
from test_objective_projection_integrity import Catalog, doc as projection_doc


def make_package(tmp_path, monkeypatch, carrier, mutation=None, *,
                 spec_version="1.20", native_producer=False,
                 catalog_direction="minimize", document_direction="minimize",
                 criterion_question_first=False):
    cat = _catalog(tmp_path, directions={"min_person_days": catalog_direction})
    monkeypatch.setattr(cb, "catalog", lambda: cat)
    monkeypatch.setattr(ca, "current", lambda: cat)
    doc = _ready_doc()
    doc["objectives"][0]["direction"] = document_direction
    assert "criterion" not in doc["objectives"][0]
    if carrier == "answer":
        q = copy.deepcopy(doc["questions"][0])
        q["id"] = model.CRITERION_QUESTION_ID
        q["title"] = "Критерий и версия ставки"
        q["text"] = "Укажите вид критерия пейринга, ставку и версию ставки."
        q["answer"]["raw"] = json.dumps({
            "criterion": "MIN_PERSON_DAYS", "rate_per_duty": 123,
            "rule": "R-S", "version": "v7", "effective_from": "2030-01-01"})
        if criterion_question_first:
            doc["questions"].insert(0, q)
        else:
            doc["questions"].append(q)
    elif carrier == "document":
        doc["objectives"][0]["statement"] = "Критерий задан машинным блоком целевой."
        doc["objectives"][0]["criterion"] = {
            "kind": "min_person_days", "rate_per_duty": 77,
            "version": "v7", "effective_from": "2030-01-01",
            "per": "день командировки", "as_written": "Полная цитата " * 60}
    elif carrier == "absent":
        doc["objectives"] = []
    model_problems = model.validate_model(doc)
    if catalog_direction != document_direction:
        assert model_problems and all("criterion-direction" in p for p in model_problems)
    else:
        assert model_problems == []
    files = tp.build_package(_run_dir(tmp_path, doc))
    actual = json.loads(files["model.json"])
    expected = model.derive_pairing_criterion(actual)
    if carrier == "absent":
        assert expected is None
    else:
        assert expected["rate_per_duty"] == {
            "answer": 123, "document": 77, "prose": 100}[carrier]
    context = {"criterion": expected}
    obj = (json.loads(files["objective.json"]) if native_producer
           else op.project(actual, cat, context=context))
    if not native_producer:
        assert op.compare(obj, actual, cat, context=context) == []
    c = obj["pairing"].get("criterion")
    if mutation == "rate":
        c["rate_per_duty"] = 999
    elif mutation == "source":
        c["source_rule"] = "R-H"
    elif mutation == "missing_quote":
        del c["as_written"]
    elif mutation == "rates_source":
        c["rates_version"]["source"] = "R-H"
    elif mutation == "answer_source":
        c["source_answer"]["question"] = "Q-3b-GOAL"
    elif mutation == "missing":
        obj["pairing"]["criterion"] = None
    elif mutation == "invented":
        obj["pairing"]["criterion"] = {
            "kind": "min_person_days", "direction": "minimize", "rate_per_duty": 999}
    elif callable(mutation):
        mutation(c)
    if not native_producer or mutation is not None:
        files["objective.json"] = op.canonical_bytes(obj)
    manifest = json.loads(files["manifest.json"])
    # Synthetic future package: the production version is NOT raised by this test.
    manifest["spec_version"] = spec_version
    for entry in manifest["files"]:
        if entry["name"] == "objective.json":
            entry["sha256"] = hashlib.sha256(files["objective.json"]).hexdigest()
    files["manifest.json"] = json.dumps(manifest).encode("utf-8")
    path = tmp_path / "context.zip"
    path.write_bytes(tp.write_zip(files))
    return path, expected


BAD = [("prose", x) for x in ("rate", "source", "missing_quote", "missing")]
BAD += [("answer", x) for x in
        ("rate", "source", "missing_quote", "rates_source", "answer_source", "missing")]
BAD += [("absent", "invented")]


@pytest.mark.parametrize("boundary", ["producer", "consumer"])
@pytest.mark.parametrize("carrier,mutation", BAD)
def test_rehashed_package_cannot_supply_its_own_expected_criterion(
        tmp_path, monkeypatch, boundary, carrier, mutation):
    path, _ = make_package(tmp_path, monkeypatch, carrier, mutation)
    if boundary == "producer":
        problems, _ = producer.check(path)
        assert any("objective-mismatch" in p and "criterion" in p for p in problems), problems
    else:
        with pytest.raises(consumer.TaskPackageRefused) as exc:
            consumer.load(path, run_gate=False)
        assert exc.value.code == "objective-mismatch"


@pytest.mark.parametrize("carrier", ["prose", "answer", "absent"])
def test_genuine_package_keeps_both_reader_results(tmp_path, monkeypatch, carrier):
    path, expected = make_package(tmp_path, monkeypatch, carrier)
    assert producer.check(path)[0] == []
    got = consumer.load(path, run_gate=False)
    assert got["objective_projection"]["judged"] is True
    if expected is not None:
        assert got["objective"]["pairing"]["criterion"]["rate_per_duty"] == expected["rate_per_duty"]


@pytest.mark.parametrize("bad", [False, 0, "criterion", []])
def test_invalid_context_cannot_be_treated_as_missing(bad):
    doc = projection_doc()
    doc["objectives"] = []
    with pytest.raises(op.ObjectiveProjectionError):
        op.project(doc, Catalog(), context={"criterion": bad})
