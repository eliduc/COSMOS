"""Offline regression: generator must retain every supported stand kind.

The expected nine-name contract is independent of the generator and keeps
condition_fn out: declaring an executor is not proof of a stand adapter.
No provider, JVM, HTTP, service or historical catalogue write occurs here.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest
import template_catalog as tc

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "catalog/tools"
LEGACY = {
    "kotlin_constraint", "kotlin_logic_form", "kotlin_metric",
    "builder_method", "check_template", "check_code",
}
MODERN = LEGACY | {"export_form", "kotlin_knob", "kotlin_form"}


@pytest.fixture(scope="module")
def tool():
    sys.path.insert(0, str(TOOLS))
    spec = importlib.util.spec_from_file_location(
        "stand_kind_generator_regression", TOOLS / "build_from_code.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    sys.path.remove(str(TOOLS))


def build(tool, version, *, legacy=False):
    folder = ROOT / ("catalog/releases/0.5.0" if legacy else
                     "catalog-integration/0.8.0")
    cat = tc.load(folder)
    seed = tool.curated_of(cat, hard=tool.seed_of(folder),
                          order=tool.order_of(folder, cat))
    requests = {"form_version": 1, "requests": cat.requests()}
    files = tool.build(version, cat.header["released_at"],
                       cat.header["released_by"], cat.header["note"],
                       seed=seed, requests_doc=requests)
    return files, requests


@pytest.mark.parametrize("version", ["0.7.0", "0.7.1-draft.1", "0.8.0"])
def test_modern_generator_preserves_all_nine_stand_kinds(tool, version):
    files, requests = build(tool, version)
    actual = json.loads(files["dictionaries.json"])["stand_task_kinds"]
    assert set(actual) == MODERN
    assert len(actual) == len(MODERN)
    assert "condition_fn" not in actual
    assert not [p for p in tool.validate_built(files, requests)
                if p.severity == "error" and p.code == "X05"]


@pytest.mark.parametrize("version", ["0.5.0", "0.6.0"])
def test_historical_schema_keeps_original_six_kind_contract(tool, version):
    files, _ = build(tool, version, legacy=True)
    assert set(json.loads(files["dictionaries.json"])["stand_task_kinds"]) == LEGACY


@pytest.mark.parametrize("removed", ["export_form", "kotlin_knob", "kotlin_form"])
def test_missing_supported_kind_is_still_rejected_by_real_validator(tool, removed):
    files, requests = build(tool, "0.8.0")
    dictionaries = json.loads(files["dictionaries.json"])
    assert removed in dictionaries["stand_task_kinds"]
    dictionaries["stand_task_kinds"].remove(removed)
    mutated = {**files, "dictionaries.json": json.dumps(dictionaries, ensure_ascii=False)}
    problems = tool.validate_built(mutated, requests)
    assert any(p.severity == "error" and p.code == "X05" and removed in str(p)
               for p in problems)


def test_invalid_typed_component_expectation_is_not_silenced(tool):
    files, requests = build(tool, "0.8.0")
    families = json.loads(files["families.json"])
    task = next(task for family in families["families"]
                for task in family["stand_tasks"]
                if "kotlin_knob" in task.get("expected", {}))
    task["expected"]["kotlin_knob"] = {"not_a_component_contract": True}
    mutated = {**files, "families.json": json.dumps(families, ensure_ascii=False)}
    assert any(p.severity == "error" and "kotlin_knob" in str(p)
               for p in tool.validate_built(mutated, requests))
