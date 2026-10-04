"""Actual scalar serializers for NONEXECUTABLE sit/rest parser directories.

These diagnostic directories are not native ZIPs, admission or support proof.
No native exporter/SMT/service is invoked and no catalogue oracle is changed.
Kotlin independently checks the emitted typed fields and provider readings.
"""
import copy
import hashlib
import json

import pytest

from cdpm import export_solver, export_task_package as tp, model, threshold_forms
from vmp import stand_solver, stand_tasks
from test_a06_component_inputs_gpt import component_catalog


TASKS = (("connection_windows", "st-sit-1"), ("rest_floors", "st-rest-1"))
SCALARS = {"min_turnaround": 45, "min_rest_home": 660, "min_rest_outstation": 700}
FAMILIES = {"connection_windows", "duty_limits", "rest_floors", "time_scale"}


def _prepare(cat, family, task_id, root, source):
    task = next(t for t in cat.family(family)["stand_tasks"] if t["id"] == task_id)
    world = copy.deepcopy(task["world"])
    meta = stand_tasks._family_meta(cat)
    families = stand_tasks.families_for(world, family, meta)
    assert set(families) == FAMILIES
    assert {key: world["params"][key] for key in SCALARS} == SCALARS
    assert "max_sit_duration" not in world["params"]
    rules = stand_solver._rules_for(world, families, meta)
    doc = stand_solver.model_doc(rules, world=world, family=family, catalog=cat)
    assert model.validate_schema(doc) == model.validate_template_fit(doc) == []
    run = stand_solver._write_run(source, doc, stand_solver._sheets(world),
                                 source=stand_solver._stand_source(world, rules, None))
    raw = (run / "extract/model.json").read_bytes()
    serialized = json.loads(raw)
    cells = {name: cell for rule in serialized["rules"]
             for name, cell in rule.get("template_fit", {}).get("params", {}).items()}
    for name, value in SCALARS.items():
        cell = cells[name]
        assert cell["value"] == value and type(cell["value"]) is int
        assert cell["unit"] == "duration_minutes" and cell["relation"] == "min"
        assert cell["condition"] is None
        assert cell["source"]["kind"] == "code"
        assert "synthetic world.params." + name in cell["source"]["note"]
    directory = root / task_id / "diagnostic-directory"
    directory.mkdir(parents=True)
    members = {"model.json": raw, "objective.json":
               export_solver.build_objective_json(doc, spec_version="1.20").encode("utf-8")}
    forms = threshold_forms.forms_bytes(doc, "1.20")
    if forms is not None:
        members["threshold-forms.json"] = forms
    maps = tp._rule_maps_for(doc)
    assert maps is None
    # This explicitly constructed diagnostic manifest is NOT the native
    # exporter's manifest. All task modes remain unavailable by construction.
    manifest = {"spec_version": "1.20", "customer": "diagnostic-parser-component",
                "source_run": task_id + "-parser-only", "revision": 1,
                "diagnostic_scope": "parser-component-only; NOT native export or admission",
                "readiness": "а", "allowed_modes": {"pairing": [], "rostering": []},
                "objectives": {"pairing": [], "rostering": []}, "has_data": False,
                "files": [{"name": name, "sha256": hashlib.sha256(body).hexdigest()}
                          for name, body in members.items()]}
    members["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    for name, body in members.items():
        (directory / name).write_bytes(body)
    (root / task_id / "world.json").write_text(json.dumps(
        {"world": world, "expected": task["expected"]}, ensure_ascii=False), encoding="utf-8")
    assert world == task["world"]
    assert (run / "extract/model.json").read_bytes() == raw
    assert not list(root.rglob("*.zip"))
    return serialized


def test_prepare_sit_rest_nonexecutable_parser_inputs(component_catalog, tmp_path, monkeypatch, record_property):
    monkeypatch.delenv("CDPM_RULE_MAPS", raising=False)
    root = tmp_path / "sit-rest-parser-inputs"
    for family, task in TASKS:
        _prepare(component_catalog, family, task, root, tmp_path / (task + "-source"))
    identity = {"schema": "a06-sit-rest-parser-input/1", "scope": "parser-component-only",
                "catalog": {"version": component_catalog.version(),
                            "fingerprint": component_catalog.fingerprint()},
                "native_export": "not attempted; diagnostic directory is not a ZIP",
                "tasks": [task for _, task in TASKS],
                "files": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in root.rglob("*") if p.is_file()}}
    (root / "identity.json").write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
    assert set(identity["tasks"]) == {"st-sit-1", "st-rest-1"}
    assert all("support" not in json.loads((root / task / "diagnostic-directory/manifest.json").read_text(
        encoding="utf-8")) for _, task in TASKS)
    record_property("a06_sit_rest_parser_inputs", str(root))
    record_property("scope", "parser-component-only; no native ZIP, admission or support witness")


@pytest.mark.parametrize("family,task_id", TASKS)
def test_serialized_native_world_keeps_independent_scopes_and_companions(component_catalog, tmp_path, monkeypatch, family, task_id):
    monkeypatch.delenv("CDPM_RULE_MAPS", raising=False)
    doc = _prepare(component_catalog, family, task_id, tmp_path / "inputs", tmp_path / "source")
    rules = {r["template_fit"]["family"]: r for r in doc["rules"]}
    assert set(rules) == FAMILIES
    for family_id in FAMILIES:
        fit = rules[family_id]["template_fit"]
        assert fit["schema_version"] == "landing/2"
        assert fit["family_contract"]["family_id"] == family_id
        assert fit["family_contract"]["catalog_fingerprint"] == component_catalog.fingerprint()
    assert rules["connection_windows"]["template_fit"]["params"]["min_turnaround"]["condition"] is None
    rests = rules["rest_floors"]["template_fit"]["params"]
    assert rests["min_rest_home"]["value"] != rests["min_rest_outstation"]["value"]
    assert set(rests) == {"min_rest_home", "min_rest_outstation"}
