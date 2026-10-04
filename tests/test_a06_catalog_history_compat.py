"""K-8/K-9: immutable history keeps its form; modern drafts stay strict.

All mutations are confined to pytest tmp_path.  These tests neither publish a
release nor change an owner act.  The actual 0.6.0 release has the legacy form;
the separate 0.7.0-draft.2 fixture has the modern direction/unit contract.
"""

import copy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

import template_catalog as tc
from catalog_service.releases import releases_gate
from cdpm import catalog_bridge as bridge, family_params as fp
from template_catalog import diff, units
from template_catalog.validate import schema_path_for_version
from vmp.objective_projection import ObjectiveProjectionError, criterion_direction


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "catalog-integration" / "0.7.0-draft.2"
VERSIONS = ("0.2.0", "0.3.0", "0.4.0", "0.5.0", "0.6.0")
FINGERPRINTS = {
    "0.2.0": "5c9d26c1dfcac9f9de12771f0ede271a27f65a95b817048c1c853961b7dd8f78",
    "0.3.0": "2dd8037b1fd6fd5c6cb17950f818cc5b4a94f5c7cb2fda1029a6bfdac1234c1d",
    "0.4.0": "ca6999759dc499261c652afc82a0f88a57f5024e6f2307b37072974a6d6a36bc",
    "0.5.0": "5534196f824aff80156e33cd5fa741c869c5fd8d23e48b39e295d15d73e8b7d9",
    "0.6.0": "7dc0626d4ce85eb0b7a26ca385953ac03a2dce68557bb2e040a614f1584ad549",
}
DRAFT_FP = "07d68c15c6fb8c66d44b5cd5bc466e632a59e20db807d78a91772145010f9357"
RELEASE_FILES = {
    "catalog.json", "families.json", "criteria.json", "dictionaries.json",
    "requests.json", "versions.json", "schema/catalog.schema.json",
    "transition/families_order.json",
}


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")


def _bytes_of_tree(path):
    return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(path.rglob("*")) if p.is_file()}


@pytest.fixture(autouse=True)
def immutable_inputs_and_clean_reader_cache():
    before = _bytes_of_tree(CATALOG)
    bridge.forget()
    yield
    bridge.forget()
    assert _bytes_of_tree(CATALOG) == before


@pytest.fixture
def history_copy(tmp_path):
    destination = tmp_path / "catalog"
    # Copy data and schema only, never executable parity/build tools.
    shutil.copytree(CATALOG, destination, ignore=shutil.ignore_patterns("tools"))
    return destination


def _triples(changes):
    return {(c["section"], c["id"], c["kind"]) for c in changes}


def _assert_identity_and_diff(folder):
    """Form-independent T18 oracle; no call to today's semantic validator."""
    catalog = tc.load(folder)
    journal = catalog.versions()
    assert tuple(row["version"] for row in journal) == VERSIONS
    assert sorted(p.name for p in (folder / "releases").iterdir() if p.is_dir()) == list(VERSIONS)
    previous = None
    for record in journal:
        version = record["version"]
        snapshot = tc.load(folder / "releases" / version)
        assert snapshot.version() == version
        assert snapshot.fingerprint() == snapshot.declared_fingerprint() == record["fingerprint"]
        assert snapshot.versions()[-1] == record
        actual = {(c.section, c.id, c.kind) for c in diff.diff_bodies(previous, snapshot.body())}
        assert actual == _triples(record["changes"]), version
        previous = snapshot.body()
    return catalog


def _gate_row(folder, version):
    rows = releases_gate(tc.load(folder))
    matches = [row for row in rows if row["version"] == version]
    assert len(matches) == 1, rows
    return matches[0]


def _rehash(folder):
    header = _read(folder / "catalog.json")
    header["fingerprint"] = tc.load(folder).fingerprint()
    _write(folder / "catalog.json", header)
    assert tc.load(folder).declared_fingerprint() == tc.load(folder).fingerprint()


@pytest.mark.parametrize("version", VERSIONS)
def test_actual_released_snapshot_identity_and_own_form_are_preserved(version):
    folder = CATALOG / "releases" / version
    assert set(_bytes_of_tree(folder)) == RELEASE_FILES
    snapshot = tc.load(folder)
    row = next(r for r in tc.load(CATALOG).versions() if r["version"] == version)
    assert snapshot.version() == version
    assert snapshot.header["released_by"] == "владелец"
    assert snapshot.fingerprint() == snapshot.declared_fingerprint() == FINGERPRINTS[version]
    assert snapshot.versions()[-1] == row
    # All these releases precede the new field contract, including real0.6.
    assert "directions" not in snapshot.dictionaries()
    assert "unit_words" not in snapshot.dictionaries()
    assert all("direction" not in criterion for criterion in snapshot.criteria().values())


def test_actual_release_chain_satisfies_identity_journal_diff_and_product_gate():
    catalog = _assert_identity_and_diff(CATALOG)
    rows = releases_gate(catalog)
    assert [r["version"] for r in rows] == list(VERSIONS)
    assert all(r["ok"] is True for r in rows), rows


def test_actual_060_is_valid_readable_legacy_without_invented_modern_capabilities():
    source = CATALOG / "releases" / "0.6.0"
    catalog = tc.current(source, validated=True)
    assert catalog.version() == "0.6.0"
    assert catalog.fingerprint() == FINGERPRINTS["0.6.0"]
    assert not [p for p in catalog.validate() if p.severity == "error"]
    assert catalog.family("crew_unavailability")["status"] == "есть"
    assert catalog.family("crew_unavailability")["params"] == []
    parameter = catalog.params()["reserve_crews"]
    assert (parameter["family"], parameter["unit"], parameter["qualifier"]) == (
        "reserve_floors", "count", "base_code")
    # Readability of old data must not fabricate a modern converter/direction.
    with pytest.raises(units.UnitsError, match="нет карты unit_words"):
        units.unit_words(source)
    with pytest.raises(ObjectiveProjectionError, match="не задано"):
        criterion_direction(catalog, "min_person_days")


@pytest.mark.parametrize("modern", [False, True], ids=["released060", "draft070"])
def test_numeric_consumer_selects_actual_catalogue_contract(monkeypatch, modern):
    source = CATALOG if modern else CATALOG / "releases" / "0.6.0"
    monkeypatch.setenv(bridge.ENV_CATALOG, str(source))
    bridge.forget()
    assert bridge.catalog().path == source
    assert fp._param_spec("reserve_floors", "reserve_crews")["unit"] == "count"
    assert fp.unit_scale("часа", "duration_minutes") == 60
    assert fp.parse_value("2 часа", "duration_minutes")[0] == 120
    assert fp.parse_value("2", "count")[0] == 2
    assert fp.parse_value("2.5", "count") is None
    assert fp.parse_value("2 месяца", "duration_minutes") is None
    assert fp.parse_value("2 часа", "count") is None
    # This word is explicitly declared for count only in the modern fixture.
    got = fp.parse_value("2 суток", "count")
    assert (got[0] if got else None) == (2 if modern else None)


def test_modern_draft_retains_strict_units_directions_and_no_release_claim():
    catalog = tc.current(CATALOG, validated=True)
    assert catalog.version() == "0.7.0-draft.2" and catalog.is_draft()
    assert catalog.fingerprint() == catalog.declared_fingerprint() == DRAFT_FP
    assert catalog.header["released_by"] == "аналитик"
    assert len(catalog.criteria()) == 8
    assert set(catalog.dictionaries()["directions"]) == {"minimize", "maximize"}
    assert all(criterion_direction(catalog, name) == "minimize" for name in catalog.criteria())
    assert units.to_canonical(2, "часа", "duration_minutes", source=CATALOG) == 120
    assert units.to_canonical(2, "суток", "count", source=CATALOG) == 2
    assert schema_path_for_version("0.6.0").read_bytes() == (
        CATALOG / "releases/0.6.0/schema/catalog.schema.json").read_bytes()
    assert schema_path_for_version(catalog.version()).read_bytes() == (
        CATALOG / "schema/catalog.schema.json").read_bytes()
    assert schema_path_for_version("0.7.0") == schema_path_for_version(catalog.version())
    assert not any(row["version"].startswith("0.7") for row in catalog.versions())


@pytest.mark.parametrize("version", ["0.2.0", "0.6.0"])
@pytest.mark.parametrize("damage", ["declared-fingerprint", "body", "header-version",
                                    "last-journal-row", "missing-section-file"])
def test_old_form_never_exempts_identity_or_integrity_checks(history_copy, version, damage):
    folder = history_copy / "releases" / version
    if damage in {"declared-fingerprint", "header-version", "missing-section-file"}:
        header = _read(folder / "catalog.json")
        if damage == "declared-fingerprint":
            header["fingerprint"] = "0" * 64
        elif damage == "header-version":
            header["version"] = "0.1.9"
        else:
            header["sections"]["families"] = "missing-k8-family-file.json"
        _write(folder / "catalog.json", header)
    elif damage == "body":
        families = _read(folder / "families.json")
        families["families"][0]["title"] += " (unrecorded tamper)"
        _write(folder / "families.json", families)
    else:
        journal = _read(folder / "versions.json")
        journal["versions"][-1]["fingerprint"] = "0" * 64
        _write(folder / "versions.json", journal)
    assert _gate_row(history_copy, version)["ok"] is False


def test_matching_fake_change_in_both_journals_is_still_rejected_by_actual_diff(history_copy):
    version = "0.2.0"
    fake = {"section": "families", "id": "k8_no_such_family",
            "kind": "добавлена", "note": "fabricated history on a temporary copy"}
    for path in (history_copy / "versions.json",
                 history_copy / "releases" / version / "versions.json"):
        journal = _read(path)
        next(r for r in journal["versions"] if r["version"] == version)["changes"].append(fake)
        _write(path, journal)
    # The bad row agrees with its own journal: only counted body changes expose it.
    own = tc.load(history_copy / "releases" / version)
    outer = tc.load(history_copy)
    assert own.versions()[-1] == outer.versions()[0]
    assert own.fingerprint() == own.declared_fingerprint() == FINGERPRINTS[version]
    assert _gate_row(history_copy, version)["ok"] is False


def test_missing_historical_snapshot_is_not_confused_with_legacy_form(history_copy, tmp_path):
    target = (history_copy / "releases" / "0.6.0").resolve()
    assert target.is_relative_to(tmp_path.resolve())
    shutil.rmtree(target)
    row = _gate_row(history_copy, "0.6.0")
    assert row["ok"] is False and "нет" in row["words"]


def _refresh_temporary_history(folder):
    """Repair ONLY temporary identity/diff witnesses to isolate semantics."""
    outer = _read(folder / "versions.json")
    previous, prefix = None, []
    for row in outer["versions"]:
        snapshot_folder = folder / "releases" / row["version"]
        _rehash(snapshot_folder)
        snapshot = tc.load(snapshot_folder)
        row["fingerprint"] = snapshot.fingerprint()
        row["changes"] = diff.as_journal(diff.diff_bodies(previous, snapshot.body()))
        prefix.append(copy.deepcopy(row))
        _write(snapshot_folder / "versions.json", {"form_version": 1, "versions": prefix})
        previous = snapshot.body()
    _write(folder / "versions.json", outer)
    _assert_identity_and_diff(folder)


def test_same_form_snapshot_still_gets_semantic_checks_after_valid_rehash(history_copy):
    folder = history_copy / "releases" / "0.5.0"
    assert (folder / "schema/catalog.schema.json").read_bytes() == (
        ROOT / "template_catalog/schemas/catalog.schema.json").read_bytes()
    families = _read(folder / "families.json")
    family = next(f for f in families["families"] if f["id"] == "reserve_floors")
    next(p for p in family["params"] if p["name"] == "reserve_crews")["unit"] = "k8_unknown_unit"
    _write(folder / "families.json", families)
    _refresh_temporary_history(history_copy)
    # Identity and journal/diff now agree; semantic invalidity must remain red.
    assert any(p.severity == "error" and p.code == "D01" and "k8_unknown_unit" in str(p)
               for p in tc.load(folder).validate())
    assert _gate_row(history_copy, "0.5.0")["ok"] is False


@pytest.mark.parametrize("damage,expected_code", [
    ("direction", "X20"), ("unit_words", "D02"), ("unit_entry", "X21"),
])
def test_modern_draft_cannot_escape_its_contract_by_rehashing(history_copy, damage, expected_code):
    if damage == "direction":
        path = history_copy / "criteria.json"
        body = _read(path)
        next(c for c in body["criteria"] if c["id"] == "min_person_days").pop("direction")
    else:
        path = history_copy / "dictionaries.json"
        body = _read(path)
        if damage == "unit_words":
            body.pop("unit_words")
        else:
            body["unit_words"].pop("duration_minutes")
    _write(path, body)
    _rehash(history_copy)
    errors = [p for p in tc.load(history_copy).validate() if p.severity == "error"]
    assert errors
    assert not any(p.code == "H02" for p in errors), errors
    assert any(p.code == expected_code for p in errors), errors
    with pytest.raises(tc.CatalogInvalid):
        tc.current(history_copy, validated=True)
