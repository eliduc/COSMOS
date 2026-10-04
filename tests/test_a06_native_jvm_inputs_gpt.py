"""Prepare exact real producer inputs for a separately invoked JVM probe.

No JVM, solver, socket or process is started here. Unsigned support and the
PAIRING admission refusal remain intact. The diagnostic consumer must not
treat these files as a support certification or an execution authorization.
"""
import hashlib
import json

from test_a06_native_stand_proof_agent import prepared, prepare_world  # noqa: F401
from cdpm.gates import gate_task_package
from vmp import stand_adapters, stand_solver
from webapp import task_package


def test_export_actual_duty_world_for_separate_jvm_diagnostic(prepared, tmp_path, record_property):
    world, rules, doc, run, raw, proof = prepare_world(prepared, tmp_path / "source", "st-duty-1")
    package = stand_solver.package_bytes(world, rules, stamp=stand_solver.stamp_of(prepared),
                                        base=tmp_path / "source", catalog=prepared,
                                        family="duty_limits", spec_version="1.20")
    target = tmp_path / "jvm-inputs"
    target.mkdir()
    archive = target / "st-duty-1.zip"
    archive.write_bytes(package)
    assert gate_task_package.check(archive)[0] == []
    received = task_package.load(archive, run_gate=False)
    assert received["manifest"]["allowed_modes"]["pairing"] == []
    assert not received["manifest"].get("stand_only")
    declared = next(t for t in prepared.family("duty_limits")["stand_tasks"] if t["id"] == "st-duty-1")
    assert world == declared["world"]
    problem = stand_adapters.world_to_problem(world)
    expected = declared["expected"]["kotlin_constraint"]
    assert expected == {"constraint": "Max duty period (rest-segmented)", "matches": 1, "amount": 120}
    (target / "st-duty-1.problem.json").write_text(json.dumps(problem, ensure_ascii=False), encoding="utf-8")
    (target / "st-duty-1.world.json").write_text(json.dumps({"world": world, "expected": declared["expected"]}, ensure_ascii=False), encoding="utf-8")
    identity = {"catalog_version": prepared.version(), "catalog_fingerprint": prepared.fingerprint(),
                "task": "st-duty-1", "admission": "PAIRING refused; diagnostic only",
                "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir()}}
    (target / "identity.json").write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
    assert (run / "extract/model.json").read_bytes() == raw
    record_property("a06_native_inputs", str(target))
    record_property("identity_sha256", hashlib.sha256((target / "identity.json").read_bytes()).hexdigest())
