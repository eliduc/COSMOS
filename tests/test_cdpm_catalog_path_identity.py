"""CDPM-426 / F-1.7: spelling one local catalogue must not change export identity.

Only explicit temporary catalogues/runs; the package probe replaces the LLM
translation seam, not declarations, hashes, readiness or the package builder.
"""
import copy
import json
import os
from pathlib import Path

import pytest

from cdpm import catalog_bridge as cb, run_record as rr
from test_template_catalog_validate import _broken


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("COSMOS_SKIP_CONFIG_MIGRATION", "1")
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    yield
    cb.forget()


def spellings(folder):
    raw = folder.as_posix()
    forms = [raw, raw, os.path.relpath(folder)]
    if folder.is_dir():
        forms += [raw + "/", raw + "/../" + folder.name]
    else:
        forms += [folder.parent.as_posix() + "/../" +
                  folder.parent.name + "/" + folder.name]
    if os.name == "nt":
        forms += [raw.replace("/", "\\"), raw[0].lower() + raw[1:]]
    assert all(Path(form).samefile(folder) for form in forms)
    return forms


def catalogue_sources(folder):
    # The reader accepts a header filename as the SAME catalogue directory.
    return spellings(folder) + [str(folder / "catalog.json"),
                               "  " + str(folder) + "  ",
                               "  " + str(folder / "catalog.json") + "  "]


def head(run, kind, environment):
    if kind == "legacy":
        return rr._legacy_declaration(run, {
            "created_at": "2026-09-15T07:00:00+00:00",
            "passport": {"environment": environment}})
    return rr.new_declaration(
        run, created_at="2026-09-15T07:00:00+00:00", created_by=rr.PANEL_ACTOR,
        purpose="регресс", airline={"name": "Т", "data_class": "synthetic", "llm_scope": "any"},
        rules_documents=[], planned_stages=[], stop_after=None,
        expectations={}, answer_policy="человек", environment=environment)


@pytest.mark.parametrize("kind", ["new", "legacy"])
def test_equivalent_existing_paths_give_one_declaration(tmp_path, monkeypatch, kind):
    folder = _broken(tmp_path, lambda docs: None)
    run = tmp_path / "run-0001"
    run.mkdir()
    results = []
    for form in catalogue_sources(folder):
        monkeypatch.setenv(cb.ENV_CATALOG, form)
        cb.forget()
        cat = cb.catalog()
        results.append(head(run, kind, {"catalog": {"version": cat.version(), "fingerprint": cat.fingerprint()}}))
    assert results[0] == results[1]  # healthy repeated spelling before comparison
    assert all(d == results[0] for d in results)
    assert len({rr.declaration_sha256(d) for d in results}) == 1


@pytest.mark.parametrize("kind", ["new", "legacy"])
def test_distinct_locations_and_absent_override_are_not_conflated(tmp_path, monkeypatch, kind):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    env = {"catalog": {"version": "0.5.0", "fingerprint": "a" * 64}}
    heads = []
    for folder in (first, second):
        monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
        heads.append(head(tmp_path, kind, env))
    assert rr.declaration_sha256(heads[0]) != rr.declaration_sha256(heads[1])
    monkeypatch.delenv(cb.ENV_CATALOG, raising=False)
    assert head(tmp_path, kind, env)["catalog"]["path"] is None


def test_old_declared_path_and_its_hash_are_not_rewritten(tmp_path, monkeypatch):
    original = head(tmp_path, "new", {})
    original["catalog"]["path"] = "C:/Legacy/../Original/catalog/"
    sha = rr.declaration_sha256(original)
    saved = {"schema": rr.RECORD_SCHEMA, "declaration": copy.deepcopy(original), "declaration_sha256": sha}
    (tmp_path / rr.DECLARATION_FILE).write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv(cb.ENV_CATALOG, str(tmp_path / "different"))
    assert rr.read_declaration(tmp_path) == saved
    restored = rr.build(tmp_path)
    assert restored["declaration"] == original
    assert restored["declaration_sha256"] == sha


def test_same_run_exports_identical_zip_for_each_catalogue_spelling(tmp_path, monkeypatch):
    from cdpm import export_task_package as tp
    from test_cdpm_a05_carriers_barrier import _ready_doc, _run_dir
    from _smt_gate_stub import channel_factory
    folder = _broken(tmp_path, lambda docs: None)
    monkeypatch.setattr(tp, "smt_channel_factory", channel_factory)
    run = _run_dir(tmp_path, _ready_doc())
    packages = []
    for form in catalogue_sources(folder):
        monkeypatch.setenv(cb.ENV_CATALOG, form)
        cb.forget()
        packages.append(tp.write_zip(tp.build_package(run)))
    assert packages[0] == packages[1]  # clocks/unrelated export noise are controlled
    assert all(package == packages[0] for package in packages)


@pytest.mark.parametrize("kind", ["new", "legacy"])
@pytest.mark.parametrize("blank", ["", "   ", "\t \n"])
def test_blank_override_matches_catalogue_bridge(tmp_path, monkeypatch, kind, blank):
    monkeypatch.setenv(cb.ENV_CATALOG, blank)
    assert cb.catalog_path() is None
    cat = cb.catalog()
    env = {"catalog": {"version": cat.version(), "fingerprint": cat.fingerprint()}}
    assert head(tmp_path, kind, env)["catalog"]["path"] is None


@pytest.mark.parametrize("kind", ["new", "legacy"])
def test_single_exports_stay_distinct_from_folder_and_each_other(tmp_path, monkeypatch, kind):
    from template_catalog import load
    from template_catalog.export import export_single
    folder = _broken(tmp_path, lambda docs: None)
    payload = export_single(load(folder))
    one, two = folder / "one.json", folder / "two.json"
    one.write_text(payload, encoding="utf-8")
    two.write_text(payload, encoding="utf-8")
    fingerprints, declarations = set(), []
    for source in (folder, folder / "catalog.json", one, two):
        monkeypatch.setenv(cb.ENV_CATALOG, str(source))
        cb.forget()
        cat = cb.catalog()
        fingerprints.add(cat.fingerprint())
        declarations.append(head(tmp_path, kind, {"catalog": {
            "version": cat.version(), "fingerprint": cat.fingerprint()}}))
    assert len(fingerprints) == 1
    assert declarations[0] == declarations[1]
    assert len({rr.declaration_sha256(d) for d in declarations}) == 3
    assert declarations[2]["catalog"]["path"].endswith("/one.json")
    assert declarations[3]["catalog"]["path"].endswith("/two.json")


@pytest.mark.parametrize("kind", ["new", "legacy"])
def test_version_selector_is_the_reader_selected_location(tmp_path, monkeypatch, kind):
    from template_catalog import load, reader
    folder = _broken(tmp_path, lambda docs: None)
    monkeypatch.setattr(reader, "DEFAULT_CATALOG_DIR", folder)
    version = load(folder).version()
    declarations = []
    for source in (str(folder), str(folder / "catalog.json"), version):
        monkeypatch.setenv(cb.ENV_CATALOG, source)
        cb.forget()
        cat = cb.catalog()
        assert cat.path.samefile(folder)
        declarations.append(head(tmp_path, kind, {"catalog": {
            "version": cat.version(), "fingerprint": cat.fingerprint()}}))
    assert declarations[0] == declarations[1] == declarations[2]


@pytest.mark.parametrize("product,env_name", [("COSMOS", rr.BINDINGS_ENV),
                                           ("CDPM", rr.CDPM_BINDINGS_ENV)])
@pytest.mark.parametrize("kind", ["new", "legacy"])
def test_bindings_source_spellings_keep_facts_and_head_identity(tmp_path, monkeypatch, product, env_name, kind):
    import hashlib
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps({"catalog_version": "0.5.0",
                               "catalog_fingerprint": "a" * 64}), encoding="utf-8")
    facts, heads = [], []
    for source in spellings(path):
        # A trailing slash is not a supported spelling of a regular file.
        if source.endswith("/"):
            continue
        monkeypatch.setenv(env_name, source)
        facts.append(rr.bindings_evidence()[product])
        heads.append(head(tmp_path, kind, {}))
    assert len({row["path"] for row in facts}) == 1
    assert all(row["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest() for row in facts)
    assert all(d == heads[0] for d in heads)
    other = tmp_path / "other.json"
    other.write_bytes(path.read_bytes())
    monkeypatch.setenv(env_name, str(other))
    assert rr.bindings_evidence()[product]["path"] != facts[0]["path"]


def test_saved_bindings_paths_and_head_hash_are_unchanged(tmp_path, monkeypatch):
    original = head(tmp_path, "new", {})
    original["bindings"]["path"] = "C:\\Legacy\\..\\Original\\bindings.json"
    sha = rr.declaration_sha256(original)
    saved = {"schema": rr.RECORD_SCHEMA, "declaration": copy.deepcopy(original), "declaration_sha256": sha}
    (tmp_path / rr.DECLARATION_FILE).write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv(rr.BINDINGS_ENV, str(tmp_path / "changed.json"))
    assert rr.read_declaration(tmp_path) == saved
    assert rr.build(tmp_path)["declaration_sha256"] == sha
