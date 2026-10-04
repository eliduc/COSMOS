"""A06/01 and A06/07: explicit integration draft, never the default release.

Read/validate only. Mutation stays in pytest tmp_path and has a recalculated
body fingerprint, so a missing direction must reach X20 rather than H02.
No parity subprocess, bindings generation, service, stand or executor runs.
"""
import json
from pathlib import Path
import shutil

import pytest

import template_catalog as tc
from template_catalog.canon import fingerprint_of


CATALOG = Path(__file__).resolve().parents[1] / "catalog-integration" / "0.7.0-draft.2"
FINGERPRINT = "07d68c15c6fb8c66d44b5cd5bc466e632a59e20db807d78a91772145010f9357"
CRITERIA = {
    "credit_hours_spread", "layover_nights_total", "min_person_days",
    "positioning_cost", "preferred_days_off_violations", "rank_normalized_cost",
    "total_pairing_cost", "uncovered_seats",
}
SNAPSHOT_FILES = (
    "catalog.json", "criteria.json", "dictionaries.json", "families.json",
    "requests.json", "versions.json", "schema/catalog.schema.json",
    "schema/contract.json", "transition/families_order.json",
)
RELEASE_VERSIONS = ("0.2.0", "0.3.0", "0.4.0", "0.5.0", "0.6.0")
RELEASE_FILES = tuple(name for name in SNAPSHOT_FILES if name != "schema/contract.json")


def test_integration_draft_has_exact_direction_contract_and_local_tools():
    cat = tc.current(CATALOG, validated=True)
    assert cat.version() == "0.7.0-draft.2"
    contract = json.loads((CATALOG / "schema/contract.json").read_text(encoding="utf-8"))
    assert contract == {"catalog_version": cat.version(),
                        "schema_contract": "catalog-0.7.schema.json",
                        "schema_file": "catalog.schema.json"}
    assert cat.fingerprint() == fingerprint_of(cat.body()) == FINGERPRINT
    assert not [p for p in cat.validate() if p.severity == "error"]
    records = cat.criteria(status=None)
    assert set(records) == CRITERIA
    assert all(row["status"] == "есть" and row["direction"] == "minimize"
               for row in records.values())
    assert sorted(cat.dictionaries()["directions"]) == ["maximize", "minimize"]
    for relative in (
            "tools/parity.py", "tools/build_from_code.py", "tools/catalog_seed.json",
            "transition/executors_legacy.json", "transition/step4_additions.json"):
        assert (CATALOG / relative).is_file(), relative
    # Presence is not a parity/precheck verdict or authority to release.
    assert not (CATALOG / "releases" / "0.7.0-draft.1").exists()


@pytest.mark.parametrize("missing_direction", [False, True])
def test_direction_validation_reaches_rehashed_body(tmp_path, missing_direction):
    originals = {name: (CATALOG / name).read_bytes() for name in SNAPSHOT_FILES}
    copied = tmp_path / "catalog"
    for name, body in originals.items():
        target = copied / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    criteria_path = copied / "criteria.json"
    criteria = json.loads(criteria_path.read_text(encoding="utf-8"))
    if missing_direction:
        row = next(row for row in criteria["criteria"] if row["id"] == "min_person_days")
        del row["direction"]
        criteria_path.write_text(json.dumps(criteria, ensure_ascii=False), encoding="utf-8")
    candidate = tc.load(copied)
    header = candidate.header.copy()
    header["fingerprint"] = fingerprint_of(candidate.body())
    (copied / "catalog.json").write_text(json.dumps(header, ensure_ascii=False), encoding="utf-8")
    judged = tc.load(copied)
    errors = [p for p in judged.validate() if p.severity == "error"]
    assert not [p for p in errors if p.code == "H02"], errors
    if missing_direction:
        assert judged.fingerprint() != FINGERPRINT
        assert any(p.code == "X20" and "min_person_days" in p.where
                   and "direction" in p.where for p in errors), errors
        with pytest.raises(tc.CatalogInvalid):
            tc.current(copied, validated=True)
    else:
        assert judged.fingerprint() == FINGERPRINT
        assert errors == []
        assert tc.current(copied, validated=True).fingerprint() == FINGERPRINT
    assert {name: (CATALOG / name).read_bytes() for name in SNAPSHOT_FILES} == originals


def test_historical_release_snapshots_match_journal_and_release_gate():
    from catalog_service.releases import releases_gate

    cat = tc.load(CATALOG)
    journal = {row["version"]: row for row in cat.versions()}
    assert set(journal) == set(RELEASE_VERSIONS)
    for version in RELEASE_VERSIONS:
        folder = CATALOG / "releases" / version
        assert {p.relative_to(folder).as_posix() for p in folder.rglob("*")
                if p.is_file()} == set(RELEASE_FILES)
        snapshot = tc.load(folder)
        assert snapshot.version() == version
        assert snapshot.declared_fingerprint() == snapshot.fingerprint() == journal[version]["fingerprint"]
        assert snapshot.versions()[-1] == journal[version]
    # The real pure gate must judge all five bodies, not just their presence.
    # Historical schema problems remain visible; immutable snapshots are not
    # rewritten to make the current reader accept them.
    rows = releases_gate(cat)
    assert len(rows) == len(RELEASE_VERSIONS)
    assert {row["version"]: row["ok"] for row in rows} == {
        version: True for version in RELEASE_VERSIONS}, rows


def test_release_gate_refuses_a_removed_snapshot_in_temporary_copy(tmp_path):
    from catalog_service.releases import releases_gate

    copied = tmp_path / "catalog"
    originals = {name: (CATALOG / name).read_bytes() for name in SNAPSHOT_FILES}
    for name, body in originals.items():
        target = copied / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
    shutil.copytree(CATALOG / "releases", copied / "releases")
    before = {row["version"]: row for row in releases_gate(tc.load(copied))}
    assert set(before) == set(RELEASE_VERSIONS)
    removed_version = "0.6.0"
    missing_words = "папки releases/0.6.0 нет"
    # Isolate the missing-folder mechanism. Full semantic acceptance is the
    # separate healthy test above; a schema refusal is not a missing snapshot.
    assert before[removed_version]["words"] != missing_words
    missing = copied / "releases" / removed_version
    assert missing.is_dir()
    assert missing.resolve().is_relative_to(tmp_path.resolve())
    shutil.rmtree(missing)
    after = {row["version"]: row for row in releases_gate(tc.load(copied))}
    assert after[removed_version] == {
        "version": removed_version, "ok": False, "words": missing_words}
    assert {v: row for v, row in after.items() if v != removed_version} == {
        v: row for v, row in before.items() if v != removed_version}
    assert (CATALOG / "releases" / removed_version).is_dir()
    assert {name: (CATALOG / name).read_bytes() for name in SNAPSHOT_FILES} == originals
