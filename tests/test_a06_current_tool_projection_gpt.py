"""Current integration tool reproduces the actual catalogue without losing kinds.

Read-only source witnesses; no Git, release, service or runtime invocation.
Historical draft3 tools remain unchanged. Unknown expectation kinds stay red.
"""
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest
import template_catalog as tc

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog-integration/0.7.0-draft.6"


@pytest.fixture
def bfc(monkeypatch):
    path = CATALOG / "tools/build_from_code.py"
    spec = importlib.util.spec_from_file_location("a06_current_tool", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    assert module.REPO.resolve() == ROOT
    return module


def projected(bfc):
    cat = tc.load(CATALOG)
    seed = bfc.curated_of(cat, hard=bfc.seed_of(CATALOG), order=bfc.order_of(CATALOG, cat))
    requests, _ = bfc.read_requests(CATALOG)
    defaults = bfc.header_defaults(CATALOG)
    files = bfc.build(defaults["version"], defaults["released_at"], defaults["released_by"],
                      defaults["note"], seed=seed, requests_doc=requests)
    return cat, files, requests


def test_current_tool_reproduces_all_declared_stand_expectations(bfc):
    cat, files, requests = projected(bfc)
    assert not [p for p in bfc.validate_built(files, requests) if p.severity == "error"]
    body = {name: json.loads(files[name + ".json"]) for name in ("families", "criteria", "dictionaries")}
    body["families"] = body["families"]["families"]
    body["criteria"] = body["criteria"]["criteria"]
    body["dictionaries"].pop("form_version")
    assert tc.canonical_bytes(body) == tc.canonical_bytes(cat.body())
    assert json.loads(files["catalog.json"])["fingerprint"] == cat.fingerprint()
    from vmp.stand_tasks import PURE_KINDS, SOLVER_KINDS
    from vmp.stand_components import KINDS
    assert set(body["dictionaries"]["stand_task_kinds"]) == set(PURE_KINDS + SOLVER_KINDS + KINDS)


def test_unknown_stand_expectation_is_still_rejected(bfc):
    _, files, requests = projected(bfc)
    files = copy.deepcopy(files)
    families = json.loads(files["families.json"])
    families["families"][0]["stand_tasks"][0]["expected"]["invented_executor"] = {"value": 0}
    files["families.json"] = json.dumps(families, ensure_ascii=False)
    problems = [str(p) for p in bfc.validate_built(files, requests) if p.severity == "error"]
    assert any("invented_executor" in p for p in problems), problems
