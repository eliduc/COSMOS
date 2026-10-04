"""Offline L02 witness selection; no catalog, solver, model or service writes."""
from __future__ import annotations

import importlib.util
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MODERN = ROOT / "catalog-integration" / "0.8.0"
OLD = ROOT / "catalog-runtime" / "local-test-20260920"
FIXTURES = ROOT / "tests" / "fixtures"
FAMILY = "duty_extension_penalty"
OLD_TITLE = ("мягкое превышение порога длительности смены до жёсткого потолка: "
             "порог — базовый предел смены пакета, потолок — параметр "
             "max_duty_duration правила семьи, вес — весом правила пакета")
NEW_TITLE = ("мягкое превышение порога длительности смены до жёсткого потолка: "
             "порог — базовый предел смены пакета, потолок — параметр "
             "duty_extension_cap правила семьи, вес — весом правила пакета")
OLD_SHA = "c968a13dd06a272da00c591ed4a873369ba3de0c97d75f8dbee4a36f900850c7"
NEW_SHA = "8cc2971430e0a0c450aae7d15924b79aa1ceb128266d18cef76180a08ed68bcd"


def _json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha(path):
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _l02(verdict):
    return next(row for row in verdict.rules if row.id == "L02")


@pytest.fixture
def law(monkeypatch):
    # This profile runs with --noconftest. Never read another contour's doors.
    for name in tuple(os.environ):
        if name.startswith(("CREW_", "CDPM_", "CATALOG_")):
            monkeypatch.delenv(name, raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("Network is forbidden in the snapshot profile")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    path = ROOT / "catalog" / "tools" / "parity.py"
    spec = importlib.util.spec_from_file_location("parity_versioned_gpt", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_modern_catalog_uses_correct_cap_witness(law):
    """Before fix: real judge rejects only the independently corrected title."""
    verdict = law.judge(MODERN)
    l02 = _l02(verdict)
    assert l02.ok is True, l02.words


def test_healthy_070_retains_historical_witness(law):
    assert law.tc.load(OLD).version() == "0.7.0"
    result = _l02(law.judge(OLD))
    assert result.ok is True, result.words


def test_old_snapshot_still_exposes_the_modern_title_change(law):
    """Passing old explicit test witnesses must not silently upgrade them."""
    witnesses = law.load_witnesses()
    before = copy.deepcopy(witnesses["snapshot"])
    result = _l02(law.judge(MODERN, witnesses=witnesses))
    assert result.ok is False
    assert "duty_extension_penalty.title" in result.words
    assert OLD_TITLE in result.words and NEW_TITLE in result.words
    assert witnesses["snapshot"] == before


@pytest.mark.parametrize("version,filename", [
    (None, "registry_step1.json"),
    ("0.7.0", "registry_step1.json"),
    ("0.7.1-draft.1", "registry_step1.json"),
    ("0.7.99", "registry_step1.json"),
    ("0.8.0-draft.1", "registry_step2.json"),
    ("0.8.0", "registry_step2.json"),
    ("0.8.1-draft.2", "registry_step2.json"),
    ("0.10.0", "registry_step2.json"),
    ("1.0.0", "registry_step2.json"),
])
def test_selection_is_only_semantic_version(law, version, filename):
    assert law.snapshot_file_for_version(version) == FIXTURES / filename
    assert law.load_witnesses(catalog_version=version)["snapshot"] == _json(FIXTURES / filename)


@pytest.mark.parametrize("version", ["", "0.8", "0.80bad", "0.8.0-draft.0", "v0.8.0", 8])
def test_malformed_version_does_not_fall_back(law, version):
    with pytest.raises(law.ParityError, match="версия для выбора"):
        law.snapshot_file_for_version(version)


def test_new_snapshot_changes_exactly_one_predeclared_field():
    old, new = (_json(FIXTURES / name) for name in ("registry_step1.json", "registry_step2.json"))
    assert _sha(FIXTURES / "registry_step1.json") == OLD_SHA
    assert _sha(FIXTURES / "registry_step2.json") == NEW_SHA
    expected = copy.deepcopy(old)
    row = dict(expected["rule_mechanic_types"])[FAMILY]
    assert row["title"] == OLD_TITLE
    row["title"] = NEW_TITLE
    assert new == expected


def test_provenance_is_delegated_authoring_not_human_signature():
    review = _json(FIXTURES / "registry_step2.review.json")
    assert review["schema"] == "registry-snapshot-review/1"
    assert review["baseline"]["sha256_lf"] == OLD_SHA
    assert review["snapshot"]["sha256_lf"] == NEW_SHA
    assert review["applicability"] == {"minimum_version_triple": [0, 8, 0], "include_drafts": True}
    assert review["change"] == {"family": FAMILY, "field": "title", "before": OLD_TITLE, "after": NEW_TITLE}
    assert review["authorization"]["mode"] == "delegated-owner-authorization"
    assert review["authorization"]["human_signature"] is False
    assert review["review"]["status"] == "root-reviewed"
    assert review["review"]["reviewer"] == "parent /root"
    assert review["review"]["scope"].strip()
    assert review["review"]["at"] == "2026-09-20T19:41:23Z"
    assert review["review"]["product_acceptance"] is False


def _catalog_copy(law, tmp_path, source, *, version=None, field=None, value=None, family=FAMILY):
    """Only new test files; actual catalog sections remain byte-immutable."""
    folder = tmp_path / "catalog"
    folder.mkdir()
    for name in ("catalog.json", "families.json", "criteria.json", "dictionaries.json", "requests.json", "versions.json"):
        (folder / name).write_bytes((source / name).read_bytes())
    header = _json(folder / "catalog.json")
    if version is not None:
        header["version"] = version
    families = _json(folder / "families.json")
    if field is not None:
        next(row for row in families["families"] if row["id"] == family)[field] = value
        (folder / "families.json").write_text(json.dumps(families, ensure_ascii=False), encoding="utf-8")
    # Refresh only the temp header fingerprint: H02 must not hide the L02 control.
    (folder / "catalog.json").write_text(json.dumps(header, ensure_ascii=False), encoding="utf-8")
    header["fingerprint"] = law.tc.load(folder).fingerprint()
    (folder / "catalog.json").write_text(json.dumps(header, ensure_ascii=False), encoding="utf-8")
    return folder


@pytest.mark.parametrize("source,version,title", [
    (MODERN, "0.8.0", OLD_TITLE),
    (MODERN, "0.8.0-draft.3", NEW_TITLE + " — произвольная добавка"),
    (OLD, "0.7.0", NEW_TITLE),
    (MODERN, "0.7.9", NEW_TITLE),
    (OLD, "0.8.0", OLD_TITLE),
])
def test_judge_does_not_choose_witness_from_observed_title_or_fingerprint(law, tmp_path, source, version, title):
    folder = _catalog_copy(law, tmp_path, source, version=version, field="title", value=title)
    result = _l02(law.judge(folder))
    assert result.ok is False
    assert FAMILY + ".title" in result.words


@pytest.mark.parametrize("field,value", [
    ("class", "core"), ("kind", "HARD"), ("zone", "ROSTERING"),
    ("violation_unit", "hour"),
])
def test_other_frozen_fields_remain_strict(law, tmp_path, field, value):
    folder = _catalog_copy(law, tmp_path, MODERN, field=field, value=value)
    result = _l02(law.judge(folder))
    assert result.ok is False
    assert FAMILY + "." + field in result.words


def test_other_family_title_remains_strict(law, tmp_path):
    folder = _catalog_copy(law, tmp_path, MODERN, family="duty_limits", field="title", value=NEW_TITLE)
    result = _l02(law.judge(folder))
    assert result.ok is False
    assert "duty_limits.title" in result.words


@pytest.mark.parametrize("payload", [None, "{not-json"])
def test_missing_or_corrupt_modern_witness_never_falls_back(law, monkeypatch, tmp_path, payload):
    path = tmp_path / "missing-or-invalid.json"
    if payload is not None:
        path.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(law, "SNAPSHOT_FILE_080", path)
    with pytest.raises(law.ParityError, match="не читается"):
        law.judge(MODERN)


def test_selected_catalog_version_reaches_ordinary_witness_loader(law, monkeypatch):
    original = law.load_witnesses
    versions = []
    def capture(catalog_version=None):
        versions.append(catalog_version)
        return original(catalog_version=catalog_version)
    monkeypatch.setattr(law, "load_witnesses", capture)
    assert _l02(law.judge(MODERN)).ok is True
    assert versions == ["0.8.0"]
