"""Exact, isolated component expectations; no inherited stand or admission."""
import copy
from pathlib import Path

import pytest
import template_catalog as tc
from vmp import stand_tasks

ROOT = Path(__file__).resolve().parents[1]


def test_draft6_only_adds_two_component_expectations_and_kind_declarations():
    old = tc.load(ROOT / "catalog-integration/0.7.0-draft.5")
    new = tc.load(ROOT / "catalog-integration/0.7.0-draft.6")
    assert new.fingerprint() == new.declared_fingerprint()
    assert not [p for p in new.validate() if p.severity == "error"]
    body = copy.deepcopy(new.body())
    expected = {("duty_limits", "st-duty-1", "kotlin_knob"),
                ("calendar_windows", "st-window-1", "kotlin_form")}
    removed = set()
    for family in body["families"]:
        for task in family["stand_tasks"]:
            for kind in ("kotlin_knob", "kotlin_form"):
                if kind in task["expected"]:
                    del task["expected"][kind]
                    removed.add((family["id"], task["id"], kind))
    assert removed == expected
    for kind in ("kotlin_knob", "kotlin_form"):
        body["dictionaries"]["stand_task_kinds"].remove(kind)
    assert body == old.body(), "No existing world or expectation may change"
    assert not (new.path / "support.json").exists()


def test_component_expectations_cannot_pass_without_actual_evidence():
    cat = tc.load(ROOT / "catalog-integration/0.7.0-draft.6")
    rows = stand_tasks.run_tasks(catalog=cat, kinds=["kotlin_knob", "kotlin_form"])
    assert len(rows) == 2
    assert all(row["result"] == "skipped" and row["got"] is None for row in rows)


@pytest.mark.parametrize("kind,field", [("kotlin_knob", "provider_value"),
                                       ("kotlin_form", "twins")])
def test_catalog_validator_rejects_incomplete_component_oracle(kind, field, tmp_path):
    import json
    import shutil
    folder = tmp_path / "invalid-component"
    shutil.copytree(ROOT / "catalog-integration/0.7.0-draft.6", folder)
    path = folder / "families.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for record in data["families"]:
        for task in record["stand_tasks"]:
            if kind in task["expected"]:
                del task["expected"][kind][field]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    # Ignore the intentionally changed fingerprint: semantic rejection must
    # happen as well, and cannot be replaced by checksum failure.
    errors = [p for p in tc.load(folder).validate() if p.severity == "error"]
    assert any("X05" in str(p) or kind in str(p) for p in errors), errors
