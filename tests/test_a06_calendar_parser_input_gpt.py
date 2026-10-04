"""Real calendar serialization for a NONEXECUTABLE parser-component fixture.

This is a directory, never an exported task ZIP or catalogue-support evidence.
The ordinary exporter must still refuse its unsigned-support source model.
"""
import copy
import hashlib
import json

import pytest

from cdpm import export_solver, export_task_package as tp, smt_verify as sv
from cdpm import threshold_forms
from vmp import stand_adapters, stand_solver, stand_tasks
from test_a06_component_inputs_gpt import component_catalog, calendar_local_report


def test_prepare_nonexecutable_calendar_parser_directory(component_catalog, tmp_path, monkeypatch, record_property):
    cat = component_catalog
    task = next(t for t in cat.family("calendar_windows")["stand_tasks"] if t["id"] == "st-window-1")
    world = copy.deepcopy(task["world"])
    meta = stand_tasks._family_meta(cat)
    families = stand_tasks.families_for(world, "calendar_windows", meta)
    rules = stand_solver._rules_for(world, families, meta)
    doc = stand_solver.model_doc(rules, world=world, family="calendar_windows", catalog=cat)
    run = stand_solver._write_run(tmp_path / "source", doc, stand_solver._sheets(world),
                                 source=stand_solver._stand_source(world, rules, None))
    raw = (run / "extract/model.json").read_bytes()
    report = calendar_local_report(doc, raw)
    sv.write_report(run, report)
    with pytest.raises(stand_solver.StandSolverError) as refused:
        stand_solver.package_bytes(world, rules, stamp=stand_solver.stamp_of(cat),
                                   base=tmp_path / "source", catalog=cat,
                                   family="calendar_windows", spec_version="1.20")
    assert isinstance(refused.value.__context__, tp.TaskPackageError)
    assert refused.value.__context__.code == tp.SUPPORT_MISSING
    assert (run / "extract/model.json").read_bytes() == raw

    root = tmp_path / "calendar-parser-input"
    directory = root / "diagnostic-directory"
    directory.mkdir(parents=True)
    forms = threshold_forms.forms_bytes(doc, "1.20")
    assert forms is not None
    calendar = json.loads(forms)["calendar_window_penalties"]
    assert len(calendar) == 1 and calendar[0]["days"] == ["thursday"]
    assert calendar[0]["weight"] == 1
    objective = export_solver.build_objective_json(doc, spec_version="1.20").encode("utf-8")
    # There is no customer rule-map carrier in this isolated data directory.
    # Still call the actual producer; never substitute a synthetic rule map.
    monkeypatch.delenv("CDPM_RULE_MAPS", raising=False)
    maps = tp._rule_maps_for(doc)
    assert maps is None
    members = {"model.json": raw, "threshold-forms.json": forms, "objective.json": objective}
    manifest = {"spec_version": "1.20", "customer": "diagnostic-parser-component",
                "source_run": "st-window-1-parser-only", "revision": 1,
                "diagnostic_scope": "parser-component-only; NOT native export or admission",
                "readiness": "а", "allowed_modes": {"pairing": [], "rostering": []},
                "objectives": {"pairing": [], "rostering": []}, "has_data": False,
                "files": [{"name": name, "sha256": hashlib.sha256(body).hexdigest()}
                          for name, body in members.items()]}
    if maps is not None:
        manifest["rule_maps"] = maps
    members["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    for name, body in members.items():
        (directory / name).write_bytes(body)
    (root / "problem.json").write_text(json.dumps(stand_adapters.world_to_problem(world), ensure_ascii=False), encoding="utf-8")
    (root / "world.json").write_text(json.dumps({"world": world, "expected": task["expected"]}, ensure_ascii=False), encoding="utf-8")
    identity = {"schema": "a06-calendar-parser-input/1", "scope": "parser-component-only",
                "catalog": {"version": cat.version(), "fingerprint": cat.fingerprint()},
                "native_export_refusal": tp.SUPPORT_MISSING,
                "smt": {"subject_sha256": report["subject_sha256"], "model_sha256": report["model_sha256"],
                        "n_rules": report["n_rules"], "n_translated": report["n_translated"], "verdict": report["verdict"]},
                "files": {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in root.rglob("*") if p.is_file()}}
    (root / "identity.json").write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
    assert world == task["world"]
    assert not list(root.rglob("*.zip"))
    record_property("a06_calendar_parser_inputs", str(root))
    record_property("scope", "parser-component-only; native export remains refused")
