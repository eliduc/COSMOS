"""K-8-only edge regressions; all mutations stay in a temporary history copy."""

import hashlib
import json
from pathlib import Path
import shutil

import pytest

import template_catalog as tc
from catalog_service.releases import releases_gate
from template_catalog.history import same_schema_form


SOURCE = Path(__file__).resolve().parents[1] / "catalog-integration/0.7.0-draft.2"


def _hashes(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob("*") if p.is_file()}


@pytest.fixture
def history(tmp_path):
    before = _hashes(SOURCE)
    folder = tmp_path / "history"
    shutil.copytree(SOURCE, folder, ignore=shutil.ignore_patterns("tools"))
    yield folder
    assert _hashes(SOURCE) == before


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _row(folder, version):
    matches = [r for r in releases_gate(tc.load(folder)) if r["version"] == version]
    assert len(matches) == 1
    return matches[0]


def test_schema_comparison_uses_declared_version_and_ignores_only_line_endings(history):
    assert same_schema_form(tc.load(history / "releases/0.2.0")) is False
    assert same_schema_form(tc.load(history / "releases/0.6.0")) is True
    assert same_schema_form(tc.load(history)) is True
    schema = history / "releases/0.6.0/schema/catalog.schema.json"
    text = schema.read_text(encoding="utf-8")
    schema.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    assert same_schema_form(tc.load(history / "releases/0.6.0")) is True


@pytest.mark.parametrize("damage", ["missing", "invalid-json", "empty-schema"])
def test_unreadable_embedded_schema_is_not_an_exemption(history, damage):
    path = history / "releases/0.2.0/schema/catalog.schema.json"
    if damage == "missing":
        path.unlink()
    else:
        path.write_text("not JSON" if damage == "invalid-json" else "{}", encoding="utf-8")
    row = _row(history, "0.2.0")
    assert row["ok"] is False and "схема" in row["words"]


@pytest.mark.parametrize("changes", [None, {}, [None], [{"section": "families"}]])
def test_malformed_changes_in_both_journals_fail_without_crashing(history, changes):
    for path in (history / "versions.json", history / "releases/0.2.0/versions.json"):
        document = _read(path)
        next(r for r in document["versions"] if r["version"] == "0.2.0")["changes"] = changes
        _write(path, document)
    row = _row(history, "0.2.0")
    assert row["ok"] is False and "разница выпусков не проверена" in row["words"]


def test_missing_previous_body_is_not_replaced_by_first_release_diff(history):
    (history / "releases/0.3.0/families.json").unlink()
    assert _row(history, "0.3.0")["ok"] is False
    following = _row(history, "0.4.0")
    assert following["ok"] is False
    assert "предыдущий снимок недоступен" in following["words"]


def test_unjournalled_release_directory_stays_visible(history):
    (history / "releases/0.6.1").mkdir()
    row = _row(history, "0.6.1")
    assert row["ok"] is False and "отсутствует в журнале" in row["words"]


def test_invalid_journal_version_is_refused_before_it_becomes_a_path(history):
    path = history / "versions.json"
    document = _read(path)
    document["versions"][0]["version"] = "../../outside-history"
    _write(path, document)
    row = _row(history, "../../outside-history")
    assert row["ok"] is False and "semver" in row["words"]


def test_modern_release_cannot_substitute_old_schema_to_bypass_semantics(history):
    from template_catalog import diff
    from template_catalog.history import release_history
    from types import SimpleNamespace
    folder = history / "releases/0.7.0"
    shutil.copytree(history, folder, ignore=shutil.ignore_patterns("releases"))
    header = _read(folder / "catalog.json")
    header["version"] = "0.7.0"
    _write(folder / "catalog.json", header)
    body = tc.load(folder).body()
    row = {"version": "0.7.0", "fingerprint": tc.load(folder).fingerprint(),
           "code": "a" * 40, "changes": diff.as_journal(diff.diff_bodies(None, body))}
    header["fingerprint"] = row["fingerprint"]
    _write(folder / "catalog.json", header)
    _write(folder / "versions.json", {"form_version": 1, "versions": [row]})
    shutil.copyfile(history / "releases/0.6.0/schema/catalog.schema.json",
                    folder / "schema/catalog.schema.json")
    # All ordinary identity witnesses agree: refusal is the schema substitution.
    assert tc.load(folder).fingerprint() == tc.load(folder).declared_fingerprint()
    result = release_history(SimpleNamespace(path=history, versions=lambda: [row]))
    modern = next(r for r in result if r["version"] == "0.7.0")
    assert modern["ok"] is False
    assert "схема нового выпуска" in modern["words"]
