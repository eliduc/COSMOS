"""Registered GET/support -> file -> CDPM readiness -> execution, in process.

All catalogue/stand/approval data are explicit isolated synthetic fixtures.
No real release or catalogue readiness is asserted. No TestClient, startup,
socket, service, solver or child process is used; product judges are untouched.
"""
import copy
import inspect
import json
import os
from pathlib import Path

import pytest

from catalog_service.app import create_app
from catalog_service.settings import Settings
from cdpm import model as model
from cdpm import support_manifest
from template_catalog import bindings
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from test_k06_scope_agent import FAMILY, healthy_model, native_support, publish
from webapp import task_package as receiver


def tree_bytes(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in folder.rglob("*") if p.is_file()}


@pytest.mark.parametrize("mutation", ["healthy", "approval", "catalog_fingerprint", "live_sha"])
def test_registered_support_file_and_actual_consumers_share_the_same_evidence(
        tmp_path, monkeypatch, mutation, record_property):
    doc, cat = healthy_model(tmp_path, monkeypatch)
    records = {("families", FAMILY)}
    support = native_support(tmp_path, monkeypatch, cat, records)
    pkg = publish(cat, doc, support, records)
    support_path = cat.path / "support.json"
    cosmos_path = Path(os.environ["CREW_COSMOS_BINDINGS"])
    settings = Settings(catalog_dir=cat.path, work_dir=tmp_path / "unused-service-work",
                        read_only=True, cosmos_bindings=cosmos_path,
                        cdpm_bindings=Path(os.environ["CDPM_BINDINGS_FILE"]),
                        markers_file=tmp_path / "unused-markers.json")
    app = create_app(settings)
    routes = [r for r in app.routes if
              getattr(r, "path", None) == "/api/records/{section}/{record_id}/support"
              and "GET" in getattr(r, "methods", set())]
    assert len(routes) == 1 and routes[0].name == "record_support"
    endpoint = routes[0].endpoint
    assert not inspect.iscoroutinefunction(endpoint)
    assert not app.state.journal.enabled
    original_tree = tree_bytes(cat.path)
    original_doc = copy.deepcopy(doc)
    original_live = cosmos_path.read_bytes()
    original_pkg = copy.deepcopy(pkg)

    def snapshot_and_get(expected):
        before = tree_bytes(cat.path)
        shown = endpoint("families", FAMILY)
        on_disk = json.loads(support_path.read_bytes())
        assert on_disk == expected
        assert shown["cells"] == [c for c in on_disk["cells"] if
                                   (c["section"], c["record_id"]) == ("families", FAMILY)]
        assert shown["catalog"] == on_disk["catalog"]
        assert shown["computed"] is True
        assert shown["record"] == "families/" + FAMILY
        assert tree_bytes(cat.path) == before, "GET must not refresh/approve or rewrite evidence"
        assert not settings.work_dir.exists(), "No service startup or action journal"
        return shown

    def healthy_control():
        shown = snapshot_and_get(support)
        assert not shown["stale"] and not shown["why"]
        assert support_manifest.readiness_problems(doc) == []
        for task in ("pairing", "rostering"):
            assert model.readiness_matrix(doc, "1.20")[task]["feasibility-hard"]["ready"]
            receiver.check_support_execution(pkg, task=task, mode="feasibility-hard")

    try:
        healthy_control()
        changed = copy.deepcopy(support)
        if mutation == "approval":
            own = [c for c in changed["cells"] if
                   (c["section"], c["record_id"], c["kind"]) ==
                   ("families", FAMILY, "kotlin_knob")]
            assert any(c["state"] == "есть" and c["approved"] for c in own)
            for cell in own:
                cell["approved"] = None
        elif mutation == "catalog_fingerprint":
            changed["catalog"]["fingerprint"] = "0" * 64
        elif mutation == "live_sha":
            # Semantically identical bindings; content identity alone changes.
            cosmos_path.write_bytes(original_live + b"\n")
            assert json.loads(cosmos_path.read_bytes()) == json.loads(original_live)

        if mutation in ("approval", "catalog_fingerprint"):
            support_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
            # Keep package/source snapshots identical. A negative must reach
            # support validity/executability, not just forged snapshot mismatch.
            pkg["manifest"]["support"] = copy.deepcopy(changed)
            pkg["manifest"]["support"]["cells"] = [c for c in changed["cells"]
                if (c["section"], c["record_id"]) in records]

        shown = snapshot_and_get(changed)
        assert shown["stale"] is (mutation == "catalog_fingerprint")
        if mutation == "catalog_fingerprint":
            assert shown["why"]
            assert not bindings.support_of(cat, changed)["valid"]
        else:
            assert bindings.support_of(cat, changed)["valid"]

        problems = support_manifest.readiness_problems(doc)
        assert bool(problems) is (mutation in ("approval", "catalog_fingerprint"))
        if problems:
            assert all(p["code"] == "support-missing" for p in problems)
        ready = model.readiness_matrix(doc, "1.20")
        assert ready["rostering"]["feasibility-hard"]["ready"] is (not problems)
        if mutation == "approval":
            assert ready["pairing"]["feasibility-hard"]["ready"]
            assert any(p["kind"] == "kotlin_knob" for p in problems)
            assert receiver.check_support_projection(pkg) == []
        if mutation == "healthy":
            receiver.check_support_execution(pkg, task="rostering", mode="feasibility-hard")
        else:
            with pytest.raises(receiver.TaskPackageRefused) as error:
                receiver.check_support_execution(pkg, task="rostering", mode="feasibility-hard")
            assert error.value.code == "support-missing", str(error.value)
            expected_words = {"approval": "утверждения", "catalog_fingerprint": "свидетельство написано для каталога",
                              "live_sha": "отпечаток живых привязок изменился"}
            assert expected_words[mutation] in str(error.value), str(error.value)
            if mutation == "catalog_fingerprint":
                assert "000000000000" in str(error.value)
                assert cat.fingerprint()[:12] in str(error.value)
        # Restore exact fixture bytes, not a recomputed approval: same app and
        # consumers must recover, proving neither stale success nor failure cache.
        support_path.write_bytes(original_tree["support.json"])
        cosmos_path.write_bytes(original_live)
        pkg.clear()
        pkg.update(copy.deepcopy(original_pkg))
        healthy_control()
        assert doc == original_doc and tree_bytes(cat.path) == original_tree
        record_property("evidence", "synthetic stand/approval only; no release or execution")
        record_property("boundary", mutation)
    finally:
        app.state.store.release_catalog_lock()
