"""Read-only A06 release-input preflight; NO stand run or release is performed.

Synthetic stand documents exercise freshness only (empty inputs plus one named
metadata-flow row). They are never claimed as execution evidence. Real on-disk
catalogues, bindings and stand files remain
unchanged. Release/refresh/CLI entry points and git processes are not called.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = ROOT / "catalog-integration" / "0.7.0-draft.3"
COMMIT = "189136ef40fe5ed9da696edc66f54311d0322d66"
AT = "2026-09-19T19:45:00Z"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    for key in ("CREW_CDPM_RECORD_API", "CREW_TASK_INBOX", "CREW_CONTOUR",
                "CDPM_CATALOG_API_URL", "CDPM_CATALOG_TOKEN",
                "CATALOG_MACHINE_TOKEN", "CREW_TASK_CHANNEL_URL",
                "CREW_TASK_CHANNEL_TOKEN"):
        monkeypatch.setenv(key, "")
    for key in ("CREW_BINDINGS_SEED", "COSMOS_TEST_CATALOG_GROWN"):
        monkeypatch.delenv(key, raising=False)
    paths = list((ROOT / "catalog").rglob("*.json"))
    paths += list(CANDIDATE.rglob("*.json"))
    paths += [ROOT / product / name for product in ("vmp", "cdpm")
              for name in ("bindings.json", "stand_results.json")]
    paths.append(ROOT / "vmp" / "bindings_seed.json")
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    yield
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths} == before


@pytest.fixture
def candidate():
    import template_catalog as tc
    cat = tc.load(CANDIDATE)
    assert cat.version() == "0.7.0-draft.3"
    assert cat.fingerprint() == "dd8687d5f8574f2f3573b5a908a15c7c7bb8b1142991bb1297c2e445cbfaa02e"
    return cat


@pytest.mark.parametrize("bad_version", ["9.9.9", "0.8.0-draft.1", "broken"])
@pytest.mark.parametrize("consumer", ["freshness", "bindings"])
def test_cdpm_wrong_numeric_catalog_version_is_not_stand_evidence(candidate, bad_version, consumer):
    from cdpm import stand_tables as stand, bindings_build as builder
    doc = stand.results_doc(candidate, [], tasks_source={}, commit=COMMIT, at=AT)
    # Healthy control: same exact source/canonical body/code, EMPTY evidence.
    assert stand.stale_reason(doc, candidate) is None
    healthy = builder.build(candidate, commit=COMMIT, generated_at=AT, stand_results=doc)
    assert all(not row["stand_verified"] for row in healthy["bindings"])
    doc["catalog_version"] = bad_version
    if consumer == "freshness":
        assert stand.stale_reason(doc, candidate) is not None, (
            "stand catalogue version was ignored despite a different or invalid numeric triple")
    else:
        with pytest.raises(builder.BindingsBuildError):
            builder.build(candidate, commit=COMMIT, generated_at=AT, stand_results=doc)


@pytest.mark.parametrize("version", ["0.7.0-draft.1", "0.7.0-draft.3", "0.7.0"])
def test_cdpm_same_numeric_triple_preserves_legal_suffix_transition(candidate, version):
    from cdpm import stand_tables as stand, bindings_build as builder
    doc = stand.results_doc(candidate, [], tasks_source={}, commit=COMMIT, at=AT)
    doc["catalog_version"] = version
    assert stand.stale_reason(doc, candidate) is None
    built = builder.build(candidate, commit=COMMIT, generated_at=AT, stand_results=doc)
    assert built["catalog_version"] == candidate.version()
    assert all(not row["stand_verified"] for row in built["bindings"])


@pytest.mark.parametrize("changed", ["fingerprint", "code"])
def test_cdpm_same_version_rejects_changed_canonical_body_or_executable(candidate, changed):
    from cdpm import stand_tables as stand, bindings_build as builder
    doc = stand.results_doc(candidate, [], tasks_source={}, commit=COMMIT, at=AT)
    assert stand.stale_reason(doc, candidate) is None
    if changed == "fingerprint":
        doc["catalog_fingerprint"] = "0" * 64
    else:
        doc["code"]["cdpm/data_checks.py"] = "0" * 64
    assert doc["catalog_version"] == candidate.version()
    assert stand.stale_reason(doc, candidate) is not None
    with pytest.raises(builder.BindingsBuildError):
        builder.build(candidate, commit=COMMIT, generated_at=AT, stand_results=doc)


def test_cdpm_wrong_version_cannot_be_relabelled_as_candidate_stand_row(candidate):
    from cdpm import stand_tables as stand, bindings_build as builder
    built = builder.build(candidate, commit=COMMIT, generated_at=AT)
    entry = next(row for row in built["bindings"]
                 if row["section"] == "families"
                 and candidate.family(row["id"]).get("stand_tasks")
                 and any(ex["state"] == "есть" for ex in row["executors"]))
    rec = candidate.family(entry["id"])
    row = {"record": "families/" + rec["id"],
           "task": rec["stand_tasks"][0]["id"], "executor_kind": "check_template",
           "result": "passed", "note": "SYNTHETIC metadata-path control; NOT a stand run"}
    doc = stand.results_doc(candidate, [row], tasks_source={}, commit=COMMIT, at=AT)
    healthy = builder.build(candidate, commit=COMMIT, generated_at=AT, stand_results=doc)
    passed = next(r for r in healthy["bindings"] if r["id"] == rec["id"])["stand_verified"]
    assert len(passed) == 1 and passed[0]["task"] == row["task"]
    doc["catalog_version"] = "9.9.9"
    with pytest.raises(builder.BindingsBuildError):
        builder.build(candidate, commit=COMMIT, generated_at=AT, stand_results=doc)


@pytest.mark.parametrize("product", ["cdpm", "vmp"])
def test_actual_old_stand_results_are_refused_on_candidate(candidate, product, record_property):
    if product == "cdpm":
        from cdpm import bindings_build as builder
    else:
        from vmp import bindings_build as builder
    doc = json.loads((ROOT / product / "stand_results.json").read_text(encoding="utf-8"))
    with pytest.raises(builder.BindingsBuildError) as caught:
        builder.build(candidate, commit=COMMIT, generated_at=AT, stand_results=doc)
    record_property(product + "_old_stand_refusal", str(caught.value))


@pytest.mark.parametrize("product", ["cdpm", "vmp"])
def test_prepared_binding_identity_does_not_invent_execution(candidate, product, record_property):
    if product == "cdpm":
        from cdpm import bindings_build as builder
    else:
        from vmp import bindings_build as builder
    final = copy.deepcopy(candidate)
    final.header["version"] = "0.7.0"
    assert final.fingerprint() == candidate.fingerprint()
    doc = builder.build(final, commit=COMMIT, generated_at=AT, stand_results=None)
    assert doc["catalog_version"] == "0.7.0"
    assert doc["catalog_fingerprint"] == candidate.fingerprint()
    assert doc["bindings"] and all(not row["stand_verified"] for row in doc["bindings"])
    assert builder.dump(doc) == builder.dump(builder.build(
        final, commit=COMMIT, generated_at=AT, stand_results=None))
    record_property(product + "_prepared_without_stand_rows", len(doc["bindings"]))


def test_current_bindings_stop_real_candidate_release(candidate, tmp_path, record_property):
    from catalog_service.settings import Settings
    from catalog_service import executors
    settings = Settings(catalog_dir=CANDIDATE, work_dir=tmp_path / "work",
        read_only=True, cosmos_bindings=ROOT / "vmp" / "bindings.json",
        cdpm_bindings=ROOT / "cdpm" / "bindings.json")
    stop = executors.bindings_stop(candidate, settings, "0.7.0", check_fingerprint=True)
    assert stop is not None and stop.code == "bindings-stop"
    record_property("current_binding_refusal", stop.words)
    support = executors.support_table(candidate, settings)
    assert support["cells"]
    assert not any(cell["approved"] for cell in support["cells"])
    assert not any(cell["state"] == "есть" for cell in support["cells"])
    assert not (CANDIDATE / "support.json").exists()
    assert not (CANDIDATE / "releases" / "0.7.0").exists()
