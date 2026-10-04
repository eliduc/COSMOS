"""Release/bindings byte-order regression, entirely on an isolated Store.

Real release(), writers, bindings builders, support projection and execution
judge. Only git identity/cleanliness is a fixture; no git process is allowed.
There is deliberately NO parity tool: the real unjudged warning is explicitly
confirmed by the TEST owner, never replaced by a green parity verdict.
Stand rows are labelled synthetic input fixtures, NOT executed stand results.
Only release() writes approved. No real catalogue, service, solver or activation.
"""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "a" * 40  # tmp-store git metadata, not a claimed product revision
BEFORE = "2026-09-17T00:00:00Z"
AFTER = "2026-09-17T00:01:00Z"
FAMILY = "duty_limits"
VERSION = "0.5.0"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    # Imports happen after data/channel isolation. No global conftest or webui.
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path / "runs"))
    for key in ("CREW_CDPM_RECORD_API", "CREW_TASK_INBOX", "CREW_CONTOUR",
                "CDPM_CATALOG_API_URL", "CDPM_CATALOG_TOKEN",
                "CATALOG_MACHINE_TOKEN", "CREW_TASK_CHANNEL_URL",
                "CREW_TASK_CHANNEL_TOKEN"):
        monkeypatch.setenv(key, "")
    for key in ("COSMOS_TEST_CATALOG_GROWN", "CREW_VMP_TEMPLATE_BASE"):
        monkeypatch.delenv(key, raising=False)

    import template_catalog as tc
    from catalog_service import releases
    from catalog_service.journal import Journal
    from catalog_service.settings import Settings
    from catalog_service.store import Store
    from cdpm import catalog_bridge as bridge
    from tests.catalog_copies import copy_catalog
    from vmp import catalog_access

    live_paths = [ROOT / "catalog", ROOT / "cdpm" / "bindings.json",
                  ROOT / "vmp" / "bindings.json"]
    live = {p: _sha(p) for root in live_paths
            for p in (root.rglob("*.json") if root.is_dir() else [root])}
    made = []

    def prepare(*, draft):
        folder = copy_catalog(tmp_path, name="catalog-draft" if draft else "catalog-final",
                              version=VERSION + "-draft.1" if draft else VERSION,
                              requests=False, candidates=False, bindings=False)
        assert not (folder / "tools" / "parity.py").exists()
        work = tmp_path / (folder.name + "-work")
        marker = tmp_path / "markers.json"
        marker.write_text(json.dumps({"checks": [{"name": "unit fixture",
            "kind": "token", "values": ["ФРЕГЕНОЛЁТ"]}]}), encoding="utf-8")
        settings = Settings(catalog_dir=folder, work_dir=work, actor="owner",
            markers_file=marker, cosmos_bindings=tmp_path / (folder.name + "-cosmos.json"),
            cdpm_bindings=tmp_path / (folder.name + "-cdpm.json"), machine_token=None)
        store = Store(settings)
        made.append(store)
        journal = Journal(settings.journal_path)
        # Fixture-only git transport. Catalogue validity, B01, approval and
        # hash judges are untouched. This test does not certify git prechecks.
        monkeypatch.setattr(releases, "head_commit", lambda path: COMMIT)
        monkeypatch.setattr(releases, "dirty_files", lambda path: [])
        monkeypatch.setenv("CREW_TEMPLATE_CATALOG", str(folder))
        monkeypatch.setenv("CREW_COSMOS_BINDINGS", str(settings.cosmos_bindings))
        monkeypatch.setenv("CDPM_BINDINGS_FILE", str(settings.cdpm_bindings))
        monkeypatch.setenv("CREW_CDPM_BINDINGS", str(settings.cdpm_bindings))
        bridge.forget()
        catalog_access.forget()
        cat = store.catalog()
        assert not [p for p in cat.validate() if p.severity == "error"]
        return SimpleNamespace(cat=cat, folder=folder, store=store,
                               journal=journal, settings=settings)

    yield prepare
    for store in made:
        store.release_catalog_lock()
    bridge.forget()
    catalog_access.forget()
    tc.forget_current()
    assert {p: _sha(p) for p in live} == live


def _build(state, *, at=BEFORE):
    from cdpm import bindings_build as cb
    from vmp import bindings_build as vb, stand_tasks
    # A synthetic stand INPUT for this ordering test; no stand function runs.
    rows = [{"record": "families/" + FAMILY, "task": "st-duty-1",
             "executor_kind": "builder_method", "result": "passed",
             "note": "synthetic ordering fixture; no stand was executed"}]
    evidence = stand_tasks.results_document(rows, state.cat, at=at, commit=COMMIT)
    cosmos = vb.build(state.cat, commit=COMMIT, generated_at=at, stand_results=evidence)
    cdpm = cb.build(state.cat, commit=COMMIT, generated_at=at)
    state.settings.cosmos_bindings.write_bytes(vb.dump(cosmos).encode("utf-8"))
    state.settings.cdpm_bindings.write_bytes(cb.dump(cdpm).encode("utf-8"))
    return evidence, cosmos, cdpm


def _release(state):
    from catalog_service import releases
    result = releases.release(state.store, state.journal, version=VERSION,
        note="isolated byte-order fixture, not an owner product release",
        confirm_parity=True)
    assert result["parity"]["ok"] is None
    assert result["confirmed_parity"] is True
    assert state.journal.last(op="release")["outcome"] == "warning"
    assert result["support"]["approved_by"] == "владелец"
    state.cat = state.store.catalog()
    saved = json.loads((state.folder / "support.json").read_text(encoding="utf-8"))
    frozen = state.folder / "releases" / VERSION / "support.json"
    assert frozen.read_bytes() == (state.folder / "support.json").read_bytes()
    cell = next(c for c in saved["cells"] if
                (c["section"], c["record_id"], c["product"], c["kind"]) ==
                ("families", FAMILY, "COSMOS", "builder_method"))
    assert cell["state"] == "есть" and cell["approved"]["by"] == "владелец"
    assert cell["bindings_file"]["sha256"] == _sha(state.settings.cosmos_bindings)
    return result, saved, frozen


def _model(state):
    from cdpm import model
    doc = {"meta": {"customer": "synthetic", "mode": 1,
                    "cdpm_version": "0.1", "revision": 1},
           "sources": [{"sid": "S1", "file": "unit.md", "sha256": "0" * 64}],
           "questions": [], "objectives": [], "rules": [{
               "id": "R-DUTY", "title": "Предел цепочки",
               "statement": "Длительность командировки не более 3 суток.",
               "kind": "HARD", "zone": "PAIRING",
               "sources": [{"sid": "S1", "anchor": "п.1"}],
               "template_fit": {"category": "A", "family": FAMILY,
                   "registry_rev": state.cat.fingerprint(),
                   "params": {"max_pairing_days": {"value": 3, "quote": "3 суток"}}}}]}
    assert model.validate_schema(doc) == model.validate_template_fit(doc) == []
    return doc


def _execution(state):
    from cdpm import support_manifest
    from vmp.version import PRODUCT_VERSION
    doc = _model(state)
    manifest = {"spec_version": "1.20", "catalog": {
        "version": state.cat.version(), "fingerprint": state.cat.fingerprint()},
        "support": support_manifest.project(doc)}
    assert support_manifest.check(doc, manifest) == []
    return support_manifest.execution_problems(doc, manifest, task="pairing",
        mode="feasibility-hard", product="COSMOS", product_version=PRODUCT_VERSION)


def test_unapproved_post_release_rebuild_is_rejected(isolated, record_property):
    """The former unsafe instruction stays a negative gate control.

    The old probe expected this rebuild to remain usable and exposed a real
    SHA refusal. The fixed instruction no longer prescribes the rebuild;
    performing it manually must still be rejected, not silently reapproved.
    """
    from vmp import bindings_build as vb
    state = isolated(draft=True)
    old_evidence, _, _ = _build(state)
    result, saved, frozen = _release(state)
    assert _execution(state) == []  # prove the healthy selected gate first
    before_hash = _sha(state.settings.cosmos_bindings)
    assert not any("vmp.bindings_build" in step for step in result["next"])
    frozen_bytes = frozen.read_bytes()
    # COSMOS does not silently carry stand rows across the draft suffix.
    with pytest.raises(vb.BindingsBuildError, match="версия каталога"):
        vb.build(state.cat, commit=COMMIT, generated_at=AFTER,
                 stand_results=old_evidence)
    _build(state, at=AFTER)  # fresh synthetic input for the final version
    assert frozen.read_bytes() == frozen_bytes
    assert json.loads((state.folder / "support.json").read_text(encoding="utf-8")) == saved
    problems = _execution(state)
    record_property("approved_bindings_sha256", before_hash)
    record_property("rebuilt_bindings_sha256", _sha(state.settings.cosmos_bindings))
    record_property("immutable_support_sha256", _sha(frozen))
    record_property("selected_execution_problems", json.dumps(problems, ensure_ascii=False))
    # Localize the baseline: snapshot equality and product version still pass;
    # the only refusal is the changed raw SHA, not a missing executor/approval.
    assert _sha(state.settings.cosmos_bindings) != before_hash
    assert len(problems) == 1 and problems[0]["code"] == "support-missing"
    assert problems[0]["words"].endswith("отпечаток живых привязок изменился")


def test_final_bindings_before_release_keep_identity_and_refresh_is_not_reapproval(isolated, record_property):
    """Working order for hash coupling, not permission to skip real stands."""
    from catalog_service import executors
    state = isolated(draft=True)
    draft = state.cat
    draft_header_bytes = (state.folder / "catalog.json").read_bytes()
    # Prepare a final-version view IN MEMORY, without activating/changing the
    # draft store. The body fingerprint is identical; only the release name
    # differs. Real stand execution would use this exact candidate, not these
    # synthetic input rows. The fixture is not a new production release API.
    state.cat = copy.deepcopy(draft)
    state.cat.header["version"] = VERSION
    assert state.cat.fingerprint() == draft.fingerprint()
    assert not [p for p in state.cat.validate() if p.severity == "error"]
    _build(state)
    assert (state.folder / "catalog.json").read_bytes() == draft_header_bytes
    state.cat = draft
    before = {p: p.read_bytes() for p in
              (state.settings.cosmos_bindings, state.settings.cdpm_bindings)}
    _, _, frozen = _release(state)
    assert _execution(state) == []
    record_property("prepared_final_bindings_sha256", _sha(state.settings.cosmos_bindings))
    record_property("immutable_support_sha256", _sha(frozen))
    assert {p: p.read_bytes() for p in before} == before
    # A deterministic check, with identical metadata, need not rewrite bytes.
    _build(state)
    assert {p: p.read_bytes() for p in before} == before
    assert _execution(state) == []
    frozen_bytes = frozen.read_bytes()
    refreshed = executors.refresh_support(state.store, state.journal)
    assert all(cell["approved"] is None for cell in refreshed["cells"])
    assert frozen.read_bytes() == frozen_bytes
    problems = _execution(state)
    assert len(problems) == 1 and problems[0]["code"] == "support-missing"
    assert "нет утверждения выпуска" in problems[0]["words"]
