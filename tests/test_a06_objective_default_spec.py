"""An omitted writer revision follows the producer, never a silent legacy path.

Only real in-memory model/catalogue readers and temporary catalogue copies.
No package release, live translator, executor, HTTP or service is started.
"""
import copy
import json

import pytest

from cdpm import catalog_bridge as cb, export_solver, export_task_package as tp
from vmp import catalog_access as ca, objective_projection as op
from test_a06_model_catalogue import document, use_real_catalogue
from test_k06_scope_agent import native_support, publish
from test_w17b_objective_projection import CATALOG


@pytest.fixture(autouse=True)
def legacy_catalogue(monkeypatch):
    monkeypatch.setenv(cb.ENV_CATALOG, str(CATALOG))
    cb.forget()
    ca.forget()
    assert "directions" not in cb.catalog().dictionaries()
    yield
    cb.forget()
    ca.forget()


@pytest.mark.parametrize("revision", ["1.20", "1.21"])
@pytest.mark.parametrize("argument", ["omitted", "none"])
def test_current_projection_revision_cannot_silently_use_legacy_writer(
        monkeypatch, revision, argument):
    monkeypatch.setattr(tp, "SPEC_VERSION", revision)
    doc = document()
    # A historical input marker is not the revision of the current writer.
    doc.setdefault("meta", {})["spec_version"] = "1.18"
    before = copy.deepcopy(doc)
    with pytest.raises(op.ObjectiveProjectionError, match="направлен"):
        export_solver.build_objective_json(doc, spec_version=revision)
    kwargs = {} if argument == "omitted" else {"spec_version": None}
    with pytest.raises(op.ObjectiveProjectionError, match="направлен"):
        export_solver.build_objective_json(doc, **kwargs)
    assert doc == before


def test_current_118_keeps_the_exact_historical_bytes(monkeypatch):
    # The correction must not activate a new release or move the reader floor.
    assert tp.SPEC_VERSION == "1.18"
    assert op.PROJECTION_SPEC_FLOOR == "1.20"
    doc = document()
    before = copy.deepcopy(doc)
    expected = export_solver.build_objective_json(doc, spec_version="1.18")
    assert "projection_version" not in json.loads(expected)
    assert export_solver.build_objective_json(doc) == expected
    assert export_solver.build_objective_json(doc, spec_version=None) == expected
    assert doc == before


def test_explicit_archive_revision_is_not_replaced_by_current(monkeypatch):
    doc = document()
    expected = export_solver.build_objective_json(doc, spec_version="1.18")
    monkeypatch.setattr(tp, "SPEC_VERSION", "1.20")
    assert export_solver.build_objective_json(doc, spec_version="1.18") == expected


@pytest.mark.parametrize("revision", [None, "1.18", "1.20"])
def test_declared_directions_keep_the_shared_projection(tmp_path, monkeypatch, revision):
    cat = use_real_catalogue(tmp_path, monkeypatch)
    doc = document()
    # Shared healthy bytes require the future support prerequisite as well.
    # This is validated synthetic support/1, never a release/stand approval.
    records = {("criteria", "min_person_days")}
    support = native_support(tmp_path, monkeypatch, cat, records)
    publish(cat, doc, support, records)
    before = copy.deepcopy(doc)
    expected = export_solver.build_objective_json(doc, spec_version="1.20")
    assert "projection_version" in json.loads(expected)
    assert export_solver.build_objective_json(doc, spec_version=revision) == expected
    assert doc == before


def test_unknown_reader_revision_is_still_judged_fail_closed():
    # Resolving the producer default does not weaken unpinned receiver checks.
    assert op.judged_for_spec(None) is True
    assert op.judged_for_spec("") is True
    assert op.judged_for_spec("unreadable") is True
