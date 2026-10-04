"""The modern objective writer and reader use the same scalar-weight boundary.

The temporary 0.7 catalogue is schema-validated synthetic test data, not a
release or approval. These are real projection/writer/comparison calls, not
a package/solver/LLM test. Expected weights are independent literals.
"""
import copy
import json

import pytest

import template_catalog as tc
from cdpm import catalog_bridge as cb, export_solver as es
from vmp import objective_projection as op
from test_a06_legacy_weight_tokens import _rule
from test_catalog_060_versioned_schema import modern_docs
from test_cdpm_export_solver import _doc
from test_template_catalog_validate import _broken


@pytest.fixture
def modern_catalog(tmp_path, monkeypatch):
    folder = _broken(tmp_path, modern_docs)
    monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(folder))
    cb.forget()
    tc.forget_current()
    cat = cb.catalog()
    assert cat.version() == "0.7.0"
    assert not [p for p in cat.validate() if p.severity == "error"]
    yield cat
    cb.forget()
    tc.forget_current()


@pytest.mark.parametrize("statement,expected", [
    ("вес 10·2", None), ("вес 10‰", None), ("вес 10٪", None),
    ("вес 1.5", None), ("weight 10 / 2", None), ("вес 1 — 3", None),
    ("вес 10 · 2", None), ("вес 10‱", None), ("вес 10°", None),
    ("вес 10 x 2", None), ("вес 10 х 2", None), ("вес 10…20", None),
    ("вес 10 ½", None), ("вес 10до20", None),
    ("вес 10 до 20", None), ("вес 10 до́ 20", None),
    ("вес 10", 10), ("вес 0", 0), ("вес 10, затем штрафы", 10),
    ("штраф W_night = 10", 10), ("штрафом в размере 7 баллов", 7),
])
def test_modern_projection_writer_and_reader_keep_the_whole_weight_token(
        modern_catalog, statement, expected):
    doc = _doc(blocked=False)
    doc["rules"].append(_rule(statement))
    before = copy.deepcopy(doc)
    projected = op.project(doc, modern_catalog)
    written = json.loads(es.build_objective_json(doc, spec_version="1.20"))
    for payload in (projected, written):
        assert payload["projection_version"] == "1"
        for task in ("pairing", "rostering"):
            row = next(c for c in payload[task]["components"] if c["rule_id"] == "S-W")
            assert row["weight"] == expected
            assert row["statement"] == statement
            assert row["sources"] == doc["rules"][-1]["sources"]
        assert op.compare(payload, doc, modern_catalog) == []
    if expected is None:
        presented = copy.deepcopy(written)
        presented["pairing"]["components"][0]["weight"] = 10
        assert op.compare(presented, doc, modern_catalog)
    assert doc == before


@pytest.mark.parametrize("weight", [0, 9])
def test_modern_machine_weight_stays_authoritative(modern_catalog, weight):
    doc = _doc(blocked=False)
    doc["rules"].append({**_rule("вес 10·2"), "weight": weight})
    before = copy.deepcopy(doc)
    written = json.loads(es.build_objective_json(doc, spec_version="1.20"))
    for task in ("pairing", "rostering"):
        assert written[task]["components"][0]["weight"] == weight
    assert op.compare(written, doc, modern_catalog) == []
    assert doc == before
