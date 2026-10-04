"""CDPM-421: use the actual producer's objective bytes, not a fixture projector."""
import hashlib
import json
import zipfile

import pytest

from cdpm import catalog_bridge as cb, export_solver, export_task_package as tp, model
from cdpm.gates import gate_task_package as producer
from vmp import objective_projection as op
from webapp import task_package as consumer
from test_cdpm419_criterion_context import make_package
from test_cdpm_task_package import _ready_doc, _run_dir


@pytest.mark.parametrize("carrier", ["document", "answer", "prose", "absent"])
def test_native_producer_bytes_pass_both_boundaries(tmp_path, monkeypatch, carrier):
    path, expected = make_package(tmp_path, monkeypatch, carrier, native_producer=True)
    assert producer.check(path)[0] == []
    pkg = consumer.load(path, run_gate=False)
    assert pkg["objective_projection"]["judged"] is True
    if expected is not None:
        criterion = pkg["objective"]["pairing"]["criterion"]
        assert criterion["direction"] == "minimize"
        if carrier == "document":
            assert criterion["per"] == "день командировки"


@pytest.mark.parametrize("carrier", ["document", "answer", "prose"])
@pytest.mark.parametrize("field", ["as_written", "source_rule", "direction", "unexpected_field"])
@pytest.mark.parametrize("boundary", ["producer", "consumer"])
def test_native_healthy_control_precedes_an_addressed_corruption(
        tmp_path, monkeypatch, carrier, field, boundary):
    path, _ = make_package(tmp_path, monkeypatch, carrier, native_producer=True)
    # Do not confuse a pre-existing producer refusal with catching our mutation.
    assert producer.check(path)[0] == []
    assert consumer.load(path, run_gate=False)["objective_projection"]["judged"]
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    obj = json.loads(files["objective.json"])
    if field == "unexpected_field":
        assert field not in obj["pairing"]["criterion"]
        obj["pairing"]["criterion"][field] = "я тут лишнее"
    else:
        assert field in obj["pairing"]["criterion"]
        del obj["pairing"]["criterion"][field]
    files["objective.json"] = op.canonical_bytes(obj)
    manifest = json.loads(files["manifest.json"])
    for entry in manifest["files"]:
        if entry["name"] == "objective.json":
            entry["sha256"] = hashlib.sha256(files["objective.json"]).hexdigest()
            entry["bytes"] = len(files["objective.json"])
    files["manifest.json"] = json.dumps(manifest).encode()
    path.write_bytes(tp.write_zip(files))
    code = "criterion-direction" if field == "direction" else "objective-mismatch"
    if boundary == "producer":
        problems = producer.check(path)[0]
        assert any(code in p and "pairing.criterion." + field in p for p in problems), problems
        if field == "unexpected_field":
            assert any("лишнее поле в pairing.criterion" in p for p in problems)
    else:
        with pytest.raises(consumer.TaskPackageRefused) as exc:
            consumer.load(path, run_gate=False)
        assert exc.value.code == code
        assert any(p["where"] == "pairing.criterion." + field for p in exc.value.problems)
        if field == "unexpected_field":
            assert "лишнее поле в pairing.criterion" in str(exc.value)


def test_native_projection_is_the_common_canonical_output(tmp_path, monkeypatch):
    path, _ = make_package(tmp_path, monkeypatch, "document", native_producer=True)
    pkg = consumer.load(path, run_gate=False)
    doc = pkg["model"]
    ready = model.readiness(doc)
    context = {
        "criterion": model.derive_pairing_criterion(doc),
        "readiness": ready["state"], "task_modes": list(model.TASK_MODES),
        "matrix": model.readiness_matrix(doc),
        "ready": {t: bool(ready.get(t + "_ready")) for t in op.TASKS},
        "note": {t: "данных зоны недостаточно — компоненты показаны справочно, веса не подтверждены"
                 for t in op.TASKS if not ready.get(t + "_ready")}}
    with zipfile.ZipFile(path) as archive:
        written = archive.read("objective.json")
    assert written == op.canonical_bytes(op.project(doc, cb.catalog(), context=context))
    assert export_solver.build_objective_json(doc) == export_solver.build_objective_json(doc)


@pytest.mark.parametrize("criterion_first", [False, True])
def test_native_airline_answer_is_first_applied_question_in_model_order(
        tmp_path, monkeypatch, criterion_first):
    path, _ = make_package(
        tmp_path, monkeypatch, "answer", native_producer=True,
        criterion_question_first=criterion_first)
    assert producer.check(path)[0] == []
    pkg = consumer.load(path, run_gate=False)
    expected_id = model.CRITERION_QUESTION_ID if criterion_first else "Q-3a-GOAL"
    answer = pkg["objective"]["pairing"]["airline_answer"]
    assert answer["question_id"] == expected_id
    question = next(q for q in pkg["model"]["questions"] if q["id"] == expected_id)
    assert answer["raw"] == question["answer"]["raw"]
    criterion = pkg["objective"]["pairing"]["criterion"]
    assert criterion["rate_per_duty"] == 123
    assert criterion["source_answer"]["question"] == model.CRITERION_QUESTION_ID


def test_native_producer_takes_maximize_from_catalogue(tmp_path, monkeypatch):
    path, _ = make_package(
        tmp_path, monkeypatch, "document", native_producer=True,
        catalog_direction="maximize", document_direction="maximize")
    assert producer.check(path)[0] == []
    pkg = consumer.load(path, run_gate=False)
    pairing = pkg["objective"]["pairing"]
    assert pairing["criterion"]["direction"] == "maximize"
    assert pairing["direction"] == "maximize"
    assert pairing["objectives"][0]["direction"] == "maximize"


@pytest.mark.parametrize("catalogue,document", [
    ("maximize", "minimize"), ("minimize", "maximize")])
def test_native_producer_refuses_direction_conflict(tmp_path, monkeypatch, catalogue, document):
    with pytest.raises(tp.TaskPackageError) as exc:
        make_package(tmp_path, monkeypatch, "document", native_producer=True,
                     catalog_direction=catalogue, document_direction=document)
    assert exc.value.code == "criterion-direction"
    assert "pairing.objectives[O-1].direction" in str(exc.value)


def test_future_spec_without_catalogue_direction_refuses_instead_of_legacy(
        tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "SPEC_VERSION", "1.20")
    with pytest.raises(tp.TaskPackageError) as exc:
        tp.build_package(_run_dir(tmp_path, _ready_doc()))
    assert exc.value.code == "criterion-direction"


def test_current_legacy_catalogue_and_package_still_build(tmp_path):
    files = tp.build_package(_run_dir(tmp_path, _ready_doc()))
    assert json.loads(files["manifest.json"])["spec_version"] == "1.18"
    assert "projection_version" not in json.loads(files["objective.json"])
