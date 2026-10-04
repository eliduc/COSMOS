"""Document and derived criteria retain the same full-provenance protection.

Real package builder/readers, synthetic inputs/catalogue, no live execution.
The future spec flag is local to fixtures; this does not activate spec 1.20.
"""
import pytest

from cdpm.gates import gate_task_package as producer
from webapp import task_package as consumer
from test_cdpm419_criterion_context import make_package


FIELDS = ["as_written", "source_rule", "source_objective", "label", "per",
          "direction", "rates_version.source", "rates_version.version"]


@pytest.mark.parametrize("boundary", ["producer", "consumer"])
@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("action", ["delete", "replace"])
def test_document_criterion_cannot_drop_or_replace_expected_fields(
        tmp_path, monkeypatch, boundary, field, action):
    def corrupt(criterion):
        parts = field.split(".")
        target = criterion
        for part in parts[:-1]:
            target = target[part]
        key = parts[-1]
        assert target[key]  # Prove the field existed before corruption.
        if action == "delete":
            del target[key]
        else:
            target[key] = "maximize" if field == "direction" else "different-source"

    path, _ = make_package(tmp_path, monkeypatch, "document", corrupt)
    code = "criterion-direction" if field == "direction" else "objective-mismatch"
    if boundary == "producer":
        problems, _ = producer.check(path)
        assert any(code in p and "pairing.criterion" in p for p in problems), problems
    else:
        with pytest.raises(consumer.TaskPackageRefused) as exc:
            consumer.load(path, run_gate=False)
        assert exc.value.code == code


@pytest.mark.parametrize("carrier", ["document", "answer", "prose"])
def test_complete_provenance_keeps_healthy_package_accepted(
        tmp_path, monkeypatch, carrier):
    path, _ = make_package(tmp_path, monkeypatch, carrier)
    assert producer.check(path)[0] == []
    assert consumer.load(path, run_gate=False)["objective_projection"]["judged"] is True


@pytest.mark.parametrize("boundary", ["producer", "consumer"])
@pytest.mark.parametrize("carrier,origin", [
    ("document", "блока criterion целевой O-1"),
    ("answer", "ответа Q-3a-CRITERION"),
    ("prose", "формулировки правила O-1")])
def test_criterion_refusal_names_the_trusted_source_carrier(
        tmp_path, monkeypatch, boundary, carrier, origin):
    path, expected = make_package(tmp_path, monkeypatch, carrier, "rate")
    # The diagnostic must name the MODEL source, not any presented replacement.
    if carrier == "answer":
        origin = "ответа " + expected["source_answer"]["question"]
    if boundary == "producer":
        problems, _ = producer.check(path)
        words = "; ".join(problems)
    else:
        with pytest.raises(consumer.TaskPackageRefused) as exc:
            consumer.load(path, run_gate=False)
        words = str(exc.value)
    assert origin in words, words
