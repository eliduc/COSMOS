"""K06 actual TEST release support -> service view -> CDPM -> execution.

Retained A06 release is read-only. All destructive negative cases are confined
to a new local COPY; no new approval, service startup, network or solver.
The imported fixture protects the original release/inputs/raw bindings.
"""
import copy
import json
import shutil

import pytest
import template_catalog as tc
from catalog_service.app import create_app
from catalog_service.settings import Settings
from cdpm import catalog_bridge as cb, model, support_manifest as sm
from template_catalog import bindings
from webapp import task_package as receiver
from test_a08_actual_release_sc3_gpt import released, _census, _dump, _forget

FAMILY = "layover_penalty"


@pytest.mark.parametrize("mutation", [
    "approval", "foreign-product", "product-version", "live-bytes",
    "catalog-fingerprint", "unsigned", "foreign-record", "criterion-approval",
])
def test_actual_support_is_record_product_version_and_mode_specific(
        released, tmp_path, monkeypatch, mutation):
    original_root = released.cat.path
    local = tmp_path / "negative-copy"
    shutil.copytree(original_root, local / "catalog")
    paths = {}
    for product, variable in (("COSMOS", "CREW_COSMOS_BINDINGS"),
                              ("CDPM", "CDPM_BINDINGS_FILE")):
        import os
        paths[product] = local / (product + ".json")
        shutil.copyfile(os.environ[variable], paths[product])
        monkeypatch.setenv(variable, str(paths[product]))
    folder = local / "catalog"
    monkeypatch.setenv(cb.ENV_CATALOG, str(folder))
    _forget()
    cat = tc.current(folder, validated=True)
    original_support = (folder / "support.json").read_bytes()
    support = json.loads(original_support)
    before_raw = {key: path.read_bytes() for key, path in paths.items()}
    package = receiver.load(released.path, run_gate=False)
    doc = package["model"]
    original_doc = copy.deepcopy(doc)
    assert sm.readiness_problems(doc) == []
    receiver.check_support_execution(package, task="pairing", mode="optimization")
    original_package = copy.deepcopy(package)
    settings = Settings(catalog_dir=folder, work_dir=local / "unused-service-work",
                        read_only=True, cosmos_bindings=paths["COSMOS"],
                        cdpm_bindings=paths["CDPM"], markers_file=local / "absent-markers.json")
    app = create_app(settings)
    endpoint = next(r.endpoint for r in app.routes
                    if getattr(r, "path", "") == "/api/records/{section}/{record_id}/support")
    assert not app.state.journal.enabled
    evidence = {"scope": "actual TEST source, negative copy only; no approval or launch",
                "mutation": mutation, "source_commit": released.evidence["source_commit"],
                "catalog": released.evidence["catalog"]}

    def view():
        before = _census(folder)
        got = endpoint("families", FAMILY)
        raw = json.loads((folder / "support.json").read_bytes())
        assert got["cells"] == [cell for cell in raw["cells"]
                                if (cell["section"], cell["record_id"]) == ("families", FAMILY)]
        assert got["catalog"] == raw["catalog"] and got["computed"]
        assert _census(folder) == before and not settings.work_dir.exists()
        return got

    try:
        healthy = view()
        assert not healthy["stale"] and not healthy["why"]
        own = next(c for c in support["cells"] if
                   (c["section"], c["record_id"], c["kind"], c["product"]) ==
                   ("families", FAMILY, "builder_method", "COSMOS"))
        assert own["state"] == "есть" and own["approved"]
        other = next(c for c in support["cells"] if
                     (c["section"], c["record_id"], c["kind"], c["product"]) ==
                     ("families", FAMILY, "builder_method", "CDPM"))
        assert other["state"] != "есть"
        if mutation == "approval":
            own["approved"] = None
        elif mutation == "foreign-product":
            # Deliberately invalid claim only in a negative copy. A different
            # product's cell must never authorize this COSMOS binary.
            other.update(state="есть", approved=copy.deepcopy(own["approved"]))
            own.update(state="нет", approved=None)
        elif mutation == "product-version":
            own["bindings_file"]["product_version"] = "0.0.0"
        elif mutation == "live-bytes":
            paths["COSMOS"].write_bytes(before_raw["COSMOS"] + b"\n")
        elif mutation == "catalog-fingerprint":
            support["catalog"]["fingerprint"] = "0" * 64
        elif mutation == "unsigned":
            for c in support["cells"]:
                c["approved"] = None
        elif mutation == "criterion-approval":
            criterion = next(c for c in support["cells"] if
                (c["section"], c["record_id"], c["kind"], c["product"]) ==
                ("criteria", "total_pairing_cost", "builder_method", "COSMOS"))
            assert criterion["state"] == "есть" and criterion["approved"]
            criterion["approved"] = None
        elif mutation == "foreign-record":
            # The unrelated actual builder proof remains; withdrawing this
            # one record must not be filled from another family's evidence.
            assert any(c["section"] == "families" and c["record_id"] != FAMILY
                       and c["kind"] == "builder_method" and c["state"] == "есть"
                       and c["approved"] for c in support["cells"])
            own.update(state="не проверено", approved=None)

        (folder / "support.json").write_text(json.dumps(support, ensure_ascii=False), encoding="utf-8")
        _forget()
        app.state.store.forget()
        shown = view()
        assert shown["stale"] is (mutation == "catalog-fingerprint")
        source_verdict = bindings.support_of(cb.catalog(), support)
        assert source_verdict["valid"] is (mutation != "catalog-fingerprint")
        problems = sm.readiness_problems(doc)
        # Readiness uses any-product evidence. Execution additionally requires
        # the selected binary's live SHA/product version; do not conflate them.
        expected_readiness_problem = mutation in {"approval", "catalog-fingerprint", "unsigned", "foreign-record", "criterion-approval"}
        assert bool(problems) is expected_readiness_problem, problems
        if problems:
            assert all(p["code"] == "support-missing" for p in problems)
            assert not model.readiness_matrix(doc, "1.20")["pairing"]["optimization"]["ready"]

        # Match the local negative snapshot to reach executability, rather
        # than accept an earlier incidental manifest inequality as proof.
        if mutation != "catalog-fingerprint":
            package["manifest"]["support"] = sm.project(doc)
            assert receiver.check_support_projection(package) == []
        with pytest.raises(receiver.TaskPackageRefused) as refused:
            receiver.check_support_execution(package, task="pairing", mode="optimization")
        assert refused.value.code == "support-missing"
        if mutation not in {"catalog-fingerprint", "criterion-approval"}:
            assert "families/" + FAMILY in str(refused.value)
        if mutation == "criterion-approval":
            assert "criteria/total_pairing_cost" in str(refused.value)
            receiver.check_support_execution(package, task="pairing", mode="feasibility-soft")
        if mutation == "product-version":
            assert "версия продукта" in str(refused.value)
        if mutation == "live-bytes":
            assert "отпечаток живых привязок" in str(refused.value)
        # The SOFT rule and criterion are not HARD feasibility requirements.
        if mutation != "catalog-fingerprint":
            receiver.check_support_execution(package, task="pairing", mode="feasibility-hard")

        evidence.update(service=shown, readiness=problems,
                        execution={"code": refused.value.code, "words": str(refused.value)})
        (folder / "support.json").write_bytes(original_support)
        for key, raw in before_raw.items():
            paths[key].write_bytes(raw)
        _forget()
        app.state.store.forget()
        package = copy.deepcopy(original_package)
        assert not view()["stale"]
        assert sm.readiness_problems(doc) == []
        receiver.check_support_execution(package, task="pairing", mode="optimization")
        assert doc == original_doc
        evidence["healthy_restored"] = True
    finally:
        app.state.store.release_catalog_lock()
        _dump(tmp_path / "k06-actual-support.json", evidence)
