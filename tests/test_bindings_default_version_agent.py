"""Bindings identify the producer default, never a per-package SPEC choice.

Actual build/resolve_symbol on a validated temporary catalogue. Bindings stay
in memory with stand_results=None; no stand, package export, approval or release.
Only the producer's default attribute is temporarily changed, after the actual
bindings module has already been imported. Restoration runs even on failure.
"""
import json
from pathlib import Path

import pytest

from cdpm import bindings_build as builder
from cdpm import export_task_package as producer
import template_catalog as tc
from test_catalog_060_versioned_schema import modern_docs
from test_template_catalog_validate import _broken


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "db8e1e0eb560bce5a772994cd6087823ccfe45b3"
AT = "2026-09-18T04:10:00Z"


@pytest.fixture
def draft(tmp_path):
    folder = _broken(tmp_path, lambda docs: modern_docs(docs, "0.7.0-draft.1"))
    cat = tc.current(folder, validated=True)
    assert not [p for p in cat.validate() if p.severity == "error"]
    artifacts = [ROOT / "catalog" / "catalog.json",
                 ROOT / "cdpm" / "bindings.json", ROOT / "vmp" / "bindings.json"]
    artifacts += sorted(folder.rglob("*.json"))
    before = {p: p.read_bytes() if p.exists() else None for p in artifacts}
    default = producer.SPEC_VERSION
    try:
        yield cat
    finally:
        assert producer.SPEC_VERSION == default, "temporary producer default leaked"
        for path, raw in before.items():
            assert (path.read_bytes() if path.exists() else None) == raw, str(path)


def _built(cat):
    doc = builder.build(cat, commit=COMMIT, generated_at=AT, stand_results=None)
    assert doc["product"] == "CDPM"
    assert doc["catalog_version"] == "0.7.0-draft.1"
    assert doc["catalog_fingerprint"] == cat.fingerprint()
    assert doc["generated_from"] == COMMIT and doc["generated_at"] == AT
    assert doc["bindings"] and any(ex["state"] == "есть"
        for row in doc["bindings"] for ex in row["executors"])
    assert all(row["stand_verified"] == [] for row in doc["bindings"])
    assert json.loads(builder.dump(doc)) == doc
    return doc


def test_unchanged_producer_default_is_repeatable(draft):
    default = producer.SPEC_VERSION
    first, second = _built(draft), _built(draft)
    assert first["product_version"] == default
    assert first == second


@pytest.mark.parametrize("new_default", ["1.20", "1.21"])
def test_build_observes_default_changed_after_module_import(draft, monkeypatch, new_default):
    original = producer.SPEC_VERSION
    before = _built(draft)
    with monkeypatch.context() as change:
        change.setattr(producer, "SPEC_VERSION", new_default)
        changed = _built(draft)
    restored = _built(draft)
    assert producer.SPEC_VERSION == original
    assert restored == before
    assert changed["product_version"] == new_default, (
        "bindings_build retained an import-time producer version", changed["product_version"])
    assert {k: v for k, v in changed.items() if k != "product_version"} == {
        k: v for k, v in before.items() if k != "product_version"}


def test_modern_default_modern_restores_original_producer(draft, monkeypatch):
    original = producer.SPEC_VERSION
    expected = ["1.20", original, "1.21"]
    original_doc = _built(draft)
    observed = []
    with monkeypatch.context() as change:
        for version in expected:
            change.setattr(producer, "SPEC_VERSION", version)
            observed.append(_built(draft)["product_version"])
    assert producer.SPEC_VERSION == original
    assert _built(draft) == original_doc
    assert observed == expected, "each build must read the current producer default"
