"""Z29: display existing release diagnostics without inventing journal entries.

GET handlers are called in process over a temporary explicit catalogue copy;
no server, HTTP transport, release/checks, subprocess or model runs. The final
source-wiring check is explicitly not a browser/React rendering test.
"""

import json
from pathlib import Path
import shutil

import pytest

import template_catalog as tc
from catalog_service.app import create_app
from catalog_service.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog-integration" / "0.7.0-draft.2"
SCREEN = ROOT / "catalog-ui" / "src" / "pages" / "VersionsPage.tsx"


def _files(folder):
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in folder.rglob("*") if path.is_file()}


@pytest.fixture
def service(tmp_path):
    folder = tmp_path / "catalog"
    shutil.copytree(CATALOG, folder)
    work = tmp_path / "work-must-stay-absent"
    tc.forget_current()
    app = create_app(Settings(catalog_dir=folder, work_dir=work,
                              read_only=True,
                              markers_file=tmp_path / "no-private-markers.json"))

    def get(path):
        matches = [route for route in app.routes
                   if getattr(route, "path", None) == path
                   and "GET" in getattr(route, "methods", set())]
        assert len(matches) == 1, path
        return matches[0].endpoint()

    yield folder, work, get
    assert not work.exists(), "read-only GET must not create service workspace"
    tc.forget_current()


def _readonly_reports(service):
    folder, work, get = service
    before = _files(folder)
    versions = get("/api/versions")
    validation = get("/api/validate")
    overview = get("/api/catalog")
    assert _files(folder) == before
    assert not work.exists()
    assert set(versions) == {"current", "versions", "suggested", "drafts"}
    assert set(validation) == {"problems", "errors", "warnings", "releases"}
    return versions, validation, overview


def test_healthy_versions_and_validation_keep_the_existing_wire_contract(service):
    folder, _work, _get = service
    expected = tc.load(folder).versions()
    versions, validation, overview = _readonly_reports(service)
    assert len(expected) == 5
    assert [row["version"] for row in versions["versions"]] == [
        entry["version"] for entry in expected]
    for row, entry in zip(versions["versions"], expected):
        assert {key: row[key] for key in entry} == entry
        assert row["release_dir_ok"] and row["release_fingerprint_ok"]
    assert all(row["ok"] for row in validation["releases"])
    assert overview["releases_ok"] is True


@pytest.mark.parametrize("name,readable", [
    ("0.8.0", False), ("0.8.0", True), ("unrecorded-folder", False),
], ids=["empty-folder", "readable-snapshot", "non-version-folder"])
def test_existing_validation_exposes_orphan_without_fabricating_journal(service, name, readable):
    folder, _work, _get = service
    before_journal = (folder / "versions.json").read_bytes()
    target = folder / "releases" / name
    if readable:
        shutil.copytree(folder / "releases" / "0.6.0", target)
    else:
        target.mkdir()
    versions, validation, overview = _readonly_reports(service)
    assert name not in {row["version"] for row in versions["versions"]}
    problems = [row for row in validation["releases"] if row["version"] == name]
    assert problems == [{"version": name, "ok": False,
                         "words": "папка выпуска отсутствует в журнале каталога"}]
    assert overview["releases_ok"] is False
    assert (folder / "versions.json").read_bytes() == before_journal
    assert len(versions["versions"]) == 5


@pytest.mark.parametrize("damage", ["missing", "fingerprint"])
def test_real_journal_snapshot_failures_remain_visible(service, damage):
    folder, _work, _get = service
    release = folder / "releases" / "0.6.0"
    if damage == "missing":
        release = release.resolve()
        assert release.is_relative_to(folder.resolve())
        assert release != folder.resolve()
        shutil.rmtree(release)  # Only a pytest-owned copy, never a release source.
    else:
        path = release / "catalog.json"
        header = json.loads(path.read_text(encoding="utf-8"))
        header["fingerprint"] = "0" * 64
        path.write_text(json.dumps(header, ensure_ascii=False), encoding="utf-8")
    versions, validation, overview = _readonly_reports(service)
    row = next(row for row in versions["versions"] if row["version"] == "0.6.0")
    gate = next(row for row in validation["releases"] if row["version"] == "0.6.0")
    assert row["release_fingerprint_ok"] is gate["ok"] is False
    assert row["release_words"] == gate["words"]
    assert row["release_dir_ok"] is (damage != "missing")
    assert overview["releases_ok"] is False


def test_versions_screen_wires_existing_validation_to_separate_diagnostics():
    """Static reachability only; runtime rendering must be proved separately."""
    source = SCREEN.read_text(encoding="utf-8")
    main = source.split("function VersionsMain()", 1)[1]
    load = main.split("const load = useCallback", 1)[1].split("[reloadSession])", 1)[0]
    assert "validateCatalog()" in load, "the Versions screen never requests release diagnostics"
    assert "setValidation(got)" in load
    assert "setValidationFailed(" in load, "validation failure must not be hidden"
    assert "validation.releases.filter" in main
    assert "!row.ok" in main and "!known.has(row.version)" in main
    assert "releaseProblems.map" in main
    diagnostics = main.split("releaseProblems.map", 1)[1].split("</ul>", 1)[0]
    assert "row.version" in diagnostics and "row.words" in diagnostics
    assert "row.fingerprint" not in diagnostics and "row.released_by" not in diagnostics
    assert "validationFailed &&" in main
