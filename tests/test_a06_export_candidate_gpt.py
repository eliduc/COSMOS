"""Real draft.4 component evidence -> bindings; never a support approval."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
import template_catalog as tc
from template_catalog import bindings
from vmp import bindings_build, stand_tasks

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "catalog-integration/0.7.0-draft.3"
NEW = ROOT / "catalog-integration/0.7.0-draft.4"


def test_candidate_delta_is_explicit_original_worlds_unchanged():
    old, new = tc.load(OLD), tc.load(NEW)
    assert not [p for p in new.validate() if p.severity == "error"]
    assert new.fingerprint() == new.declared_fingerprint()
    before, after = old.body(), copy.deepcopy(new.body())
    after["dictionaries"]["stand_task_kinds"].remove("export_form")
    family = next(r for r in after["families"] if r["id"] == "duty_limits")
    new_task = next(r for r in family["stand_tasks"] if r["id"] == "st-export-worktime-exact-1")
    family["stand_tasks"].remove(new_task)
    assert after == before
    assert new_task["world"]["export_form_input"]
    assert len(new_task["expected"]["export_form"]["probes"]) == 7
    assert not (NEW / "support.json").exists()


def test_real_component_results_build_unsigned_bindings(tmp_path, monkeypatch, record_property):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CREW_VMP_DETECTORS", raising=False)
    cat = tc.load(NEW)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for folder in (OLD, NEW)
              for p in folder.glob("*.json")}
    rows = stand_tasks.run_tasks(catalog=cat, kinds=["export_form"], records=["duty_limits"])
    assert len(rows) == 1 and rows[0]["result"] == "passed", rows
    assert rows[0]["task"] == "st-export-worktime-exact-1"
    assert rows[0]["got"] == rows[0]["expected"]
    # Test-only envelope identifies the actual base plus code fingerprints;
    # this is not an exact committed-head acceptance claim.
    commit = "c5c8ef3d1679bb8d60399e791cf10b7193b470d8"
    results = stand_tasks.results_document(rows, cat, at="2026-09-19T21:04:00Z", commit=commit)
    assert stand_tasks.stale_reasons(results, cat) == []
    for filename in ("vmp/conditions_export.py", "_conditions.py", "template_catalog/stand_contracts.py"):
        for missing in (False, True):
            damaged = copy.deepcopy(results)
            if missing:
                damaged["code"].pop(filename)
            else:
                damaged["code"][filename] = "0" * 64
            reasons = stand_tasks.stale_reasons(damaged, cat)
            assert any(filename in reason for reason in reasons), reasons
            with pytest.raises(bindings_build.BindingsBuildError):
                bindings_build.build(cat, commit=commit, generated_at="2026-09-19T21:04:00Z",
                                     stand_results=damaged)
    built = bindings_build.build(cat, commit=commit, generated_at="2026-09-19T21:04:00Z",
                                 stand_results=results)
    row = next(r for r in built["bindings"] if r["section"] == "families" and r["id"] == "duty_limits")
    evidence = [s for s in row["stand_verified"] if s["executor_kind"] == "export_form"]
    assert len(evidence) == 1 and evidence[0]["result"] == "passed", row
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps(built, ensure_ascii=False), encoding="utf-8")
    support = bindings.support_table(cat, {"COSMOS": path})
    cell = next(c for c in support["cells"] if c["record_id"] == "duty_limits"
                and c["kind"] == "export_form" and c["product"] == "COSMOS")
    assert cell["state"] == "есть", cell
    assert cell["approved"] is None
    assert all(c["approved"] is None for c in support["cells"])
    record_property("real_export_form_rows", json.dumps(rows, ensure_ascii=False))
    record_property("real_export_form_fingerprint", cat.fingerprint())
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before
