"""Separate draft5 real export witnesses, never release/admission approval."""
import copy
import hashlib
import json
from pathlib import Path

import template_catalog as tc
from template_catalog import bindings
from vmp import bindings_build, stand_tasks

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / "catalog-integration/0.7.0-draft.4"
NEW = ROOT / "catalog-integration/0.7.0-draft.5"


def test_draft5_has_only_two_explicit_export_task_additions():
    before, after = tc.load(OLD), tc.load(NEW)
    assert not [p for p in after.validate() if p.severity == "error"]
    assert after.fingerprint() == after.declared_fingerprint()
    body = copy.deepcopy(after.body())
    additions = []
    old_tasks = {row["id"]: {task["id"] for task in row.get("stand_tasks", [])}
                 for row in before.body()["families"]}
    for row in body["families"]:
        extra = [t for t in row.get("stand_tasks", []) if t["id"] not in old_tasks[row["id"]]]
        for task in extra:
            additions.append((row["id"], task))
            row["stand_tasks"].remove(task)
    assert {family for family, task in additions} == {"rest_floors", "fleet_exclusions"}
    assert len(additions) == 2
    assert body == before.body(), "No previous world, dictionary or contract may be silently changed"
    for family, task in additions:
        assert set(task["expected"]) == {"export_form"}
        assert set(task["world"]["export_form_input"]) == {"model", "forms", "manifest"}
    assert not (NEW / "support.json").exists()


def test_three_real_exports_reach_separate_unsigned_support_cells(tmp_path, monkeypatch, record_property):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("CREW_VMP_DETECTORS", raising=False)
    protected = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                 for folder in (OLD, NEW) for p in folder.glob("*.json")}
    catalog = tc.load(NEW)
    rows = stand_tasks.run_tasks(catalog=catalog, kinds=["export_form"])
    assert len(rows) == 3, rows
    assert {row["record"] for row in rows} == {
        "families/duty_limits", "families/rest_floors", "families/fleet_exclusions"}
    assert all(row["result"] == "passed" and row["got"] == row["expected"] for row in rows), rows
    # Unit envelope only; actual CLI evidence uses its committed head separately.
    stamp = "2026-09-19T21:23:00Z"
    commit = "9c528585d1a7cbd94ace8d57fbfb980a4ddcb673"
    evidence = stand_tasks.results_document(rows, catalog, at=stamp, commit=commit)
    assert not stand_tasks.stale_reasons(evidence, catalog)
    built = bindings_build.build(catalog, commit=commit, generated_at=stamp, stand_results=evidence)
    path = tmp_path / "bindings.json"
    path.write_text(json.dumps(built, ensure_ascii=False), encoding="utf-8")
    support = bindings.support_table(catalog, {"COSMOS": path})
    cells = [c for c in support["cells"] if c["product"] == "COSMOS" and c["kind"] == "export_form"]
    assert len(cells) == 3
    assert all(c["state"] == "есть" and c["approved"] is None for c in cells), cells
    assert all(c["approved"] is None for c in support["cells"])
    assert protected == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    record_property("actual_export_rows", json.dumps(rows, ensure_ascii=False))
    record_property("catalog_fingerprint", catalog.fingerprint())
