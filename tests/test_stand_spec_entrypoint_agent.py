"""Explicit stand SPEC reaches a genuine ZIP before any solver submission.

The CLI composition uses one NEW synthetic SOFT task, not a filtered declared
catalogue task. The shared fixture supplies the real pinned draft and fresh
unsigned bindings; its real verifier handles zero HARD rules. Health is an
in-process transport double, NOT a successful stand. A BaseException sentinel
stops before start_run, so there is no solved result, passed row or stand proof.
The metric cases are only a forwarding seam, with no package or metric verdict.
"""
import copy
import io
import json
import zipfile

import pytest

from cdpm import export_task_package as producer
from cdpm.gates import gate_task_package as gate
from vmp import stand_solver as ss, stand_tasks as st

from test_stand_draft_package_composition_agent import (
    FAMILY, _prepare, _soft_world, draft_composition,
)


class _BoundaryReached(BaseException):
    """Deliberate stop, not caught as a product/solver failure or success."""


def _forbidden(*_args, **_kwargs):
    pytest.fail("This entrypoint check must not submit, solve or write stand results")


class _HealthOnly:
    def __init__(self):
        self.healths = 0
        self.closed = False
        self._c = self

    def health(self):
        self.healths += 1
        return True

    def close(self):
        self.closed = True

    def __getattr__(self, _name):
        _forbidden()


@pytest.mark.parametrize("revision", ["1.20", "1.21", None])
def test_cli_selected_spec_reaches_real_zip_before_solver(
        draft_composition, tmp_path, monkeypatch, capsys, revision):
    cat, documents, binding_paths, support = draft_composition
    tmp = tmp_path / "cli"
    task_id = "synthetic-entrypoint-boundary-only"
    base = ss.run_base(tmp, task_id)
    world, rules, _doc, run, raw = _prepare(cat, base, monkeypatch)
    task = {"id": task_id, "world": world,
            "expected": {"kotlin_constraint": {
                "constraint": "Duty extension over threshold",
                "matches": 0, "amount": 0}},
            "note": "Synthetic package boundary only; expected score is not judged"}
    before = copy.deepcopy((world, rules, task))
    frozen = {p: p.read_bytes() for p in [*binding_paths.values(), support]}
    # Enumerate only a newly constructed test task. Pass the genuine Catalog
    # through all production semantics; a duck-typed proxy is not a Catalog.
    record = copy.deepcopy(cat.family(FAMILY))
    record["stand_tasks"] = [copy.deepcopy(task)]
    client = _HealthOnly()
    monkeypatch.setattr(st, "_catalog", lambda _path=None: cat)
    monkeypatch.setattr(st, "_records", lambda _cat: [("families", record)])
    monkeypatch.setattr(ss, "solver_client", lambda _url: client)
    monkeypatch.setattr(ss, "start_run", _forbidden)
    monkeypatch.setattr(st, "results_document", _forbidden)
    monkeypatch.setattr(st, "_table", _forbidden)
    real_package = ss.package_bytes
    captured = []

    def stop_after_real_package(*args, **kwargs):
        captured.append(real_package(*args, **kwargs))
        raise _BoundaryReached()

    monkeypatch.setattr(ss, "package_bytes", stop_after_real_package)
    argv = ["--catalog", str(cat.path), "--kinds", "kotlin_constraint",
            "--records", FAMILY, "--with-solver", "http://in-process-double.invalid",
            "--tmp-dir", str(tmp)]
    if revision is not None:
        argv += ["--spec-version", revision]
    with pytest.raises(_BoundaryReached):
        st.main(argv)
    assert len(captured) == 1
    assert client.healths == 1 and client.closed
    assert "итог стенда:" not in capsys.readouterr().out
    assert (world, rules, task) == before
    assert (run / "extract" / "model.json").read_bytes() == raw
    assert producer.SPEC_VERSION == "1.18"
    want = revision or "1.18"
    zipped = tmp_path / ("entrypoint-" + want + ".zip")
    zipped.write_bytes(captured[0])
    problems, counters = gate.check(zipped)
    assert problems == [], problems
    assert counters["spec_version"] == want
    with zipfile.ZipFile(io.BytesIO(captured[0])) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["spec_version"] == want
    assert manifest["catalog"] == ss.stamp_of(cat)
    if revision is not None:
        assert manifest["support"]["catalog"] == ss.stamp_of(cat)
        cells = manifest["support"]["cells"]
        assert cells and {c["product"] for c in cells} == {"CDPM", "COSMOS"}
        assert all(c["approved"] is None and c["state"] != "есть" for c in cells)
    for doc in documents.values():
        assert all(not row["stand_verified"] for row in doc["bindings"])
    for path, original in frozen.items():
        assert path.read_bytes() == original


@pytest.mark.parametrize("revision", ["1.20", "1.21", None])
def test_metric_spec_forwarding_seam_without_execution(
        draft_composition, tmp_path, monkeypatch, revision):
    cat, _, _, _ = draft_composition
    world = _soft_world()
    record = copy.deepcopy(cat.criteria()["min_person_days"])
    assert record["zone"] == "PAIRING" and record["direction"] == "minimize"
    before = copy.deepcopy((world, record))
    seen = []

    def stop_at_run_world(actual_world, **kwargs):
        assert actual_world is world
        assert kwargs.get("spec_version") == revision
        assert kwargs["catalog"] is cat
        assert kwargs["families"] == [FAMILY]
        assert kwargs["task"] == "pairing" and kwargs["mode"] == "optimization"
        assert kwargs["objective"]["metric"] == "min_person_days"
        assert kwargs["objective"]["id"] == "O-STAND-min_person_days"
        seen.append(True)
        raise _BoundaryReached()

    monkeypatch.setattr(ss, "run_world", stop_at_run_world)
    monkeypatch.setattr(ss, "package_bytes", _forbidden)
    monkeypatch.setattr(ss, "start_run", _forbidden)
    with pytest.raises(_BoundaryReached):
        ss.measure("kotlin_metric", world=world, expected={"refusal": False},
                   families=[FAMILY], meta=st._family_meta(cat),
                   stamp=ss.stamp_of(cat), base=tmp_path, client=object(),
                   record=record, family=FAMILY, catalog=cat,
                   spec_version=revision)
    assert seen == [True]
    assert (world, record) == before
    assert producer.SPEC_VERSION == "1.18"


@pytest.mark.parametrize("revision", ["1.20", "1.21", ""])
def test_cli_explicit_spec_without_solver_refuses_before_context(
        tmp_path, monkeypatch, capsys, revision):
    monkeypatch.setattr(st, "_run", _forbidden)
    monkeypatch.setattr(st, "_catalog", _forbidden)
    monkeypatch.setattr(ss, "solver_client", _forbidden)
    untouched = tmp_path / "must-not-be-created"
    assert st.main(["--spec-version", revision, "--tmp-dir", str(untouched)]) == 2
    words = capsys.readouterr().out
    assert "--spec-version" in words and "--with-solver" in words
    assert not untouched.exists()


@pytest.mark.parametrize("revision", ["1.20", "1.21", ""])
def test_api_explicit_spec_without_solver_refuses_before_context(monkeypatch, revision):
    monkeypatch.setattr(st, "_catalog", _forbidden)
    monkeypatch.setattr(ss, "solver_client", _forbidden)
    with pytest.raises(ValueError) as error:
        st.run_tasks(spec_version=revision)
    assert "spec_version" in str(error.value) and "solver_url" in str(error.value)


def test_api_none_spec_without_solver_keeps_context_path(monkeypatch):
    seen = []

    def stop_at_catalog(path=None):
        seen.append(path)
        raise _BoundaryReached()

    monkeypatch.setattr(st, "_catalog", stop_at_catalog)
    monkeypatch.setattr(ss, "solver_client", _forbidden)
    with pytest.raises(_BoundaryReached):
        st.run_tasks(spec_version=None)
    assert seen == [None]
