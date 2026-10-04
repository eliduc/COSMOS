"""Offline regression for the A06 card's synthetic future-package setup.

Calls package(), the real producer gate and the real receiver only. Importing
the card module does not call its TestClient/DOM helpers. No HTTP, subprocess,
model, solver or release is started. Local support evidence is synthetic test
data built by the real support/1 writer, not a production readiness verdict.
These tests do NOT establish the eight HTTP/card/DOM cells reported by COSMOS,
nor repair the two pre-existing cache/answer failures.
"""
import json
import zipfile

import pytest

from cdpm import catalog_bridge as cb, export_task_package as writer
from cdpm.gates import gate_task_package as gate
from vmp import catalog_access as ca
from webapp import task_package as receiver
from test_a06_direction_card import package


@pytest.fixture(autouse=True)
def isolated_fixture(monkeypatch, tmp_path):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CREW_TASK_INBOX", "")
    monkeypatch.setenv("CREW_CDPM_RECORD_API", "")
    cb.forget()
    ca.forget()
    yield
    cb.forget()
    ca.forget()


def _files(path):
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


@pytest.mark.parametrize("direction", ["minimize", "maximize"])
@pytest.mark.parametrize("carrier", ["document", "prose"])
def test_card_fixture_has_real_modern_support_and_keeps_optimization(
        tmp_path, monkeypatch, direction, carrier):
    path, cat = package(tmp_path, monkeypatch, direction=direction, carrier=carrier)
    problems, _ = gate.check(path)
    assert problems == [], problems
    received = receiver.load(path, run_gate=False)
    manifest = received["manifest"]
    assert writer.SPEC_VERSION == "1.18", "the fixture must not activate a release"
    assert manifest["spec_version"] == "1.20"
    assert manifest["allowed_modes"]["pairing"] == [
        "optimization", "feasibility-soft", "feasibility-hard"]
    assert received["objective_projection"]["judged"] is True
    assert received["support_projection"]["judged"] is True
    assert received["objective"]["pairing"]["direction"] == direction
    # Independent scope selection from the native file, not project() as oracle.
    native = json.loads((cat.path / "support.json").read_text(encoding="utf-8"))
    expected = [c for c in native["cells"] if
                (c["section"], c["record_id"]) == ("criteria", "min_person_days")]
    actual = manifest["support"]["cells"]
    key = lambda c: (c["section"], c["record_id"], c["kind"], c["product"])
    assert sorted(actual, key=key) == sorted(expected, key=key)
    required = next(c for c in actual if
                    (c["kind"], c["product"]) == ("builder_method", "COSMOS"))
    assert required["state"] == "есть" and required["approved"]


def test_card_fixture_preserves_archived_118_without_modern_evidence(
        tmp_path, monkeypatch):
    path, _ = package(tmp_path, monkeypatch, spec="1.18")
    problems, _ = gate.check(path)
    assert problems == [], problems
    received = receiver.load(path, run_gate=False)
    assert "support" not in received["manifest"]
    assert received["support_projection"]["judged"] is False
    assert received["objective_projection"]["judged"] is False


def test_rehashed_direction_conflict_is_still_rejected_not_hidden_by_support(
        tmp_path, monkeypatch):
    path, _ = package(tmp_path, monkeypatch,
                      mutate=lambda obj: obj["pairing"].update(direction="maximize"))
    problems, _ = gate.check(path)
    assert any("criterion-direction" in p for p in problems), problems
    assert not any("support-missing" in p or "allowed_modes" in p for p in problems), problems
    with pytest.raises(receiver.TaskPackageRefused, match="direction"):
        receiver.load(path, run_gate=False)


@pytest.mark.parametrize("damage", ["missing_support", "false_modes"])
def test_modern_card_fixture_cannot_hide_missing_evidence_or_false_modes(
        tmp_path, monkeypatch, damage):
    path, _ = package(tmp_path, monkeypatch)
    assert gate.check(path)[0] == [], "prove the unchanged package before damage"
    files = _files(path)
    manifest = json.loads(files["manifest.json"])
    if damage == "missing_support":
        del manifest["support"]
        expected = "support-missing"
    else:
        manifest["allowed_modes"]["pairing"].remove("optimization")
        expected = "allowed_modes"
    files["manifest.json"] = json.dumps(manifest).encode("utf-8")
    path.write_bytes(writer.write_zip(files))
    problems, _ = gate.check(path)
    assert any(expected in p for p in problems), problems
