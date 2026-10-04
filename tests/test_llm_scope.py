"""Phase-1 unit tests for per-dataset LLM eligibility (registry.llm_scope)."""
import json

import pytest

from webapp import registry


@pytest.fixture()
def scope_file(tmp_path, monkeypatch):
    p = tmp_path / "llm_scope.json"
    monkeypatch.setattr(registry, "LLM_SCOPE_PATH", p)
    return p


def test_absent_map_is_empty(scope_file):
    assert registry.load_llm_scopes() == {}


def test_default_scope_by_source():
    assert registry._default_scope("upload") == "local-only"
    assert registry._default_scope("gerad") == "any"
    assert registry._default_scope("bts") == "any"


def test_set_and_load_roundtrip(scope_file):
    registry.set_llm_scope("upload-alice-x", "any")
    assert registry.load_llm_scopes() == {"upload-alice-x": "any"}
    registry.set_llm_scope("upload-alice-x", "local-only")
    assert registry.load_llm_scopes()["upload-alice-x"] == "local-only"


def test_set_invalid_scope_rejected(scope_file):
    with pytest.raises(ValueError):
        registry.set_llm_scope("upload-x", "public")


def test_remove_scope(scope_file):
    registry.set_llm_scope("upload-x", "any")
    registry.remove_llm_scope("upload-x")
    assert registry.load_llm_scopes() == {}


def test_malformed_map_raises(scope_file):
    scope_file.write_text('{"upload-x": "banana"}', encoding="utf-8")
    with pytest.raises(RuntimeError):
        registry.load_llm_scopes()


def test_instance_carries_scope(scope_file, tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "REPO_ROOT", tmp_path)   # payload_path rel
    payload = tmp_path / "p.json"
    payload.write_text(json.dumps(
        {"airports": [], "employees": [], "flights": [],
         "flightAssignments": []}), encoding="utf-8")
    # default for an upload with no override -> local-only
    inst = registry._instance("upload-x", "x", "upload", payload,
                              gate={}, owner="a@b.c", scopes={})
    assert inst["llm_scope"] == "local-only"
    # explicit override wins
    inst2 = registry._instance("upload-x", "x", "upload", payload,
                               gate={}, owner="a@b.c",
                               scopes={"upload-x": "any"})
    assert inst2["llm_scope"] == "any"
