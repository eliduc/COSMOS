"""Eight native direction-only models, genuine partial TEST release.

Catalogue worlds are retained unchanged for separate fixed-score components;
these models do NOT certify all their HARD rules or authorize a JVM solve.
Only this file owns its temporary evidence. No support verdict is fabricated.
Run directly with --evidence-root NEW --commit FULL_SHA: the reviewed bootstrap
guard permits pinned local Git for the isolated TEST release, nothing else.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import test_a06_partial_release_bootstrap_gpt as bootstrap
from test_a06_partial_release_bootstrap_gpt import isolated
from test_cdpm_task_package import _ready_doc, _run_dir

METRICS = {
    "min_person_days": "st-mpd-1", "rank_normalized_cost": "st-rnc-1",
    "total_pairing_cost": "st-tpc-1", "credit_hours_spread": "st-chs-1",
    "layover_nights_total": "st-lnt-1", "positioning_cost": "st-pos-1",
    "preferred_days_off_violations": "st-pdo-1", "uncovered_seats": "st-unc-1",
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def direction_model(cat, record, world):
    from vmp import stand_solver
    from test_objective_projection_boundaries import modern_soft_rule
    doc = _ready_doc()
    doc["rules"], doc["questions"] = [], []
    objective = stand_solver.objective_for(record, world=world)
    objective["rate"] = {"value": 1, "per": "component unit",
                         "as_written": "Synthetic direction-only model: one per component unit"}
    if record["id"] in {"min_person_days", "rank_normalized_cost"}:
        # These are public Builder criteria, not Kotlin metric tokens.
        objective.pop("metric")
    if record["id"] == "rank_normalized_cost":
        objective["criterion"] = {
            "kind": "rank_normalized_cost", "rates": {
                "flight": world["rates_deci"]["flight"] / 10,
                "work": world["rates_deci"]["work"] / 10},
            "version": "1", "effective_from": "2026-10-01",
            "as_written": "Synthetic direction-only model: original cost-pair rates",
        }
    if record["id"] == "total_pairing_cost":
        rule = modern_soft_rule(cat)
        rule["weight"] = world["stand"]["w_night"]
        rule["statement"] = "Synthetic component: one unit per layover night"
        rule["template_fit"]["quote"] = rule["statement"]
        doc["rules"] = [rule]
    if "criterion" in objective:
        # Machine criteria carry their monetary rates in criterion, not rate.
        objective.pop("rate")
    doc["objectives"] = [objective]
    return doc


def package_model(base, doc, monkeypatch):
    from cdpm import model, smt_verify, export_task_package as tp
    assert model.validate_model(doc) == []
    run = _run_dir(base, doc)
    raw = (run / "extract/model.json").read_bytes()

    def forbidden(*_a, **_k):
        raise AssertionError("No translator, Z3, service or solve for direction-only models")

    monkeypatch.setattr(tp, "smt_channel_factory", forbidden)
    monkeypatch.setattr(smt_verify, "_run_z3", forbidden)
    report = smt_verify.verify(doc, forbidden, model_sha256=smt_verify.model_fingerprint(raw))
    assert report["n_rules"] == report["n_translated"] == 0
    smt_verify.write_report(run, report)
    files = tp.build_package(run, spec_version="1.20")
    assert (run / "extract/model.json").read_bytes() == raw
    return files


def semantic_mutant(path, files, zone):
    from cdpm import export_task_package as tp
    from vmp import objective_projection as op
    files = dict(files)
    objective = json.loads(files["objective.json"])
    objective[zone]["direction"] = "maximize"
    files["objective.json"] = op.canonical_bytes(objective)
    manifest = json.loads(files["manifest.json"])
    for row in manifest["files"]:
        if row["name"] == "objective.json":
            row.update(sha256=hashlib.sha256(files["objective.json"]).hexdigest(),
                       bytes=len(files["objective.json"]))
    files["manifest.json"] = json.dumps(manifest).encode()
    path.write_bytes(tp.write_zip(files))


def public_builder(received, world, metric, base):
    from vmp import criteria, stand_adapters as sa, stand_tasks as st
    import _pairing_builder as pb
    criterion = received["objective"]["pairing"]["criterion"]
    assert criterion["direction"] == "minimize"
    # SC-1 reaches the public applicator before ANY weight/provenance mutation.
    argv, provenance = ["unchanged"], {"sentinel": True}
    with pytest.raises(criteria.CriteriaError, match="направлен"):
        criteria.apply_criterion(dict(criterion, direction="maximize"), argv, provenance)
    assert argv == ["unchanged"] and provenance == {"sentinel": True}
    argv, provenance = [], {}
    if metric == "rank_normalized_cost":
        path = sa.cost_pair_to_control_pair(world, base, expected_total_deci=14400)
        with st._stand_contour(path):
            assert criteria.apply_criterion(criterion, argv, provenance) is None
        payload, args = sa.cost_pair_payload(world)
    else:
        assert criteria.apply_criterion(criterion, argv, provenance) is None
        payload, args = sa.world_to_builder(world)
    st._apply_cli(args, argv)
    if metric == "total_pairing_cost":
        components = received["objective"]["pairing"]["components"]
        assert len(components) == 1 and components[0]["weight"] == 1
        # Actual received coefficient, not the world's expected total.
        args.w_night = components[0]["weight"]
    builder = pb.Builder(payload, args)
    builder.build_duties(None)
    if metric == "rank_normalized_cost":
        work = [builder.legs["F1"]]
        dh = [builder.legs["D1"]] if "D1" in builder.legs else []
        chain = [{"i": 0, "legs": work, "dep": work[0]["dep"], "arr": work[-1]["arr"],
                  "t0": work[0]["t0"], "t1": work[-1]["t1"],
                  "span": (work[-1]["t1"] - work[0]["t0"]).total_seconds() / 60}]
        builder.annotate_duty(chain[0])
        value = builder.cost_0096(chain, dh, world["crew"])
        assert value == 3 * 240 * (12 + 8) == 14400
    else:
        chain, dh = sa.chain_of(builder, world)
        price = builder.price(chain, dh, builder.crew_size(chain))
        assert price["criterion"] == 2 * 7 == 14
        value = price["cost"] if metric == "total_pairing_cost" else price["criterion"]
        assert value == (15 if metric == "total_pairing_cost" else 14)
    return {"value": value, "argv": argv, "provenance": provenance,
            "maximize_refused_before_mutation": True}


def test_eight_native_directions(isolated, tmp_path, monkeypatch, record_property):
    from cdpm import export_task_package as tp, model
    from cdpm.gates import gate_task_package
    from webapp import task_package as receiver
    from vmp import stand_adapters
    # A genuine fresh release, not native_support()/publish() test doubles.
    bootstrap.test_fresh_partial_test_release_enables_only_supported_native_mode(
        isolated, tmp_path, monkeypatch, record_property)
    from a06_postrelease_checks_gpt import verify_postrelease
    postrelease = verify_postrelease(isolated, tmp_path, monkeypatch)
    assert postrelease["ok"]
    cat = isolated.store.catalog()
    inputs = tmp_path / "direction-inputs"
    inputs.mkdir()
    rows = []
    for metric, task_id in METRICS.items():
        record = cat.criteria()[metric]
        task = next(t for t in record["stand_tasks"] if t["id"] == task_id)
        world = copy.deepcopy(task["world"])
        doc = direction_model(cat, record, world)
        base = tmp_path / "direction-source" / metric
        base.mkdir(parents=True)
        files = package_model(base, doc, monkeypatch)
        folder = inputs / metric
        folder.mkdir()
        archive = folder / "task.zip"
        archive.write_bytes(tp.write_zip(files))
        assert gate_task_package.check(archive)[0] == [], metric
        received = receiver.load(archive, run_gate=False)
        assert received["objective_projection"]["judged"] is True
        zone = "pairing" if metric in set(list(METRICS)[:3]) else "rostering"
        projected = received["objective"][zone]
        assert projected["direction"] == "minimize", (metric, projected)
        assert received["manifest"]["catalog"] == {"version": cat.version(), "fingerprint": cat.fingerprint()}
        modes = received["manifest"]["allowed_modes"]
        assert "feasibility-hard" in modes[zone], (metric, modes)
        receiver.check_support_execution(received, task=zone, mode="feasibility-hard")
        if zone == "rostering":
            assert "optimization" not in modes[zone], (metric, modes)
            with pytest.raises(receiver.TaskPackageRefused) as refusal:
                receiver.check_support_execution(received, task=zone, mode="optimization")
            assert refusal.value.code == tp.SUPPORT_MISSING
        dump(folder / "world.json", {"world": world, "expected": task["expected"]})
        dump(folder / "problem.json", None if world.get("form") == "cost_pair"
             else stand_adapters.world_to_problem(world))
        dump(folder / "identity.json", {
            "schema": "a06-native-direction-input/1", "scope": "direction-only-model/component-only",
            "catalog": {"version": cat.version(), "fingerprint": cat.fingerprint()},
            "metric": metric, "task": task_id, "objective": doc["objectives"][0]["id"],
            "source_commit": os.environ["A06_BOOTSTRAP_SOURCE_COMMIT"],
            "allowed_modes": modes,
            "files": {p.name: sha(p) for p in folder.iterdir() if p.is_file()},
        })
        before = sha(archive)
        bad = base / "semantic-maximize.zip"
        semantic_mutant(bad, files, zone)
        problems = gate_task_package.check(bad)[0]
        assert any("criterion-direction" in p for p in problems), (metric, problems)
        assert not any("SHA-256" in p or "support-missing" in p for p in problems), problems
        with pytest.raises(receiver.TaskPackageRefused) as refused:
            receiver.load(bad, run_gate=False)
        assert refused.value.code == "criterion-direction"
        wrong = copy.deepcopy(doc)
        wrong["objectives"][0]["direction"] = "maximize"
        validation = model.validate_model(wrong)
        assert any("criterion-direction" in str(problem) for problem in validation), validation
        readiness = model.readiness_matrix(wrong, spec_version="1.20")[zone]
        assert readiness["optimization"]["ready"] is False
        assert readiness["feasibility-hard"]["ready"] is True
        wrong_run = _run_dir(base / "wrong-model", wrong)
        with pytest.raises(tp.TaskPackageError) as refused_model:
            tp.build_package(wrong_run, spec_version="1.20")
        assert refused_model.value.code == "criterion-direction"
        builder = public_builder(received, world, metric, base) if zone == "pairing" else None
        assert sha(archive) == before and world == task["world"]
        rows.append({"metric": metric, "task": task_id, "native_sha256": before,
                     "allowed_modes": modes, "semantic_refusal": "criterion-direction",
                     "producer_refusal": refused_model.value.code, "builder": builder,
                     "wrong_direction_model_validation": validation,
                     "wrong_direction_readiness": readiness})
        dump(tmp_path / "direction-summary.json", {"complete": False, "rows": rows})
    assert len(rows) == 8
    dump(tmp_path / "direction-summary.json", {
        "complete": True, "scope": "directions only; not full-world certification or JVM execution",
        "source_commit": os.environ["A06_BOOTSTRAP_SOURCE_COMMIT"],
        "inputs": str(inputs), "rows": rows,
    })
    record_property("direction_inputs", str(inputs))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    options, arguments = parser.parse_known_args()
    if re.fullmatch(r"[0-9a-f]{40}", options.commit) is None:
        raise SystemExit("Expected exact source SHA")
    os.environ["A06_BOOTSTRAP_SOURCE_COMMIT"] = options.commit
    base = options.evidence_root.resolve()
    if base.exists():
        raise SystemExit("Evidence root must be new")
    base.mkdir(parents=True)
    allowed, denied = [], []

    def audit(event, args):
        if event == "subprocess.Popen":
            import subprocess
            executable, command, cwd, _env = args
            parts = list(command) if isinstance(command, (list, tuple)) else []
            if isinstance(command, str):
                parts = next(([name, *rest] for name in ("git", str(bootstrap.rehearsal.GIT))
                              for rest in bootstrap.rehearsal.ALLOWED_ARGS
                              if command == subprocess.list2cmdline([name, *rest])), [])
            location = Path(cwd).resolve() if cwd else Path.cwd().resolve()
            exe = executable or (parts[0] if parts else "")
            resolved = Path(shutil.which(str(exe)) or str(exe)).resolve()
            if (resolved == bootstrap.rehearsal.GIT.resolve() and parts
                    and tuple(parts[1:]) in bootstrap.rehearsal.ALLOWED_ARGS
                    and location.is_relative_to(base)):
                allowed.append({"args": parts[1:], "cwd": str(location)})
                return
        elif not (event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.sendto", "socket.sendmsg",
                             "os.system", "os.posix_spawn", "os.exec", "os.spawn"} or event.startswith("os.spawn")):
            return
        denied.append(event)
        raise RuntimeError("direction guard denied " + event)

    sys.addaudithook(audit)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["COSMOS_SKIP_CONFIG_MIGRATION"] = "1"
    import socket
    from _pytest import junitxml
    junitxml.platform = SimpleNamespace(node=socket.gethostname)
    code = pytest.main(["--noconftest", "-p", "no:cacheprovider", "-o", "junit_family=xunit1",
                       "--basetemp=" + str(base / "pytest"), str(Path(__file__).resolve()), *arguments])
    dump(base / "guard.json", {"exit": int(code), "allowed_git": allowed, "denied": denied})
    print(json.dumps({"exit": int(code), "git_children": len(allowed), "denied": denied}))
    return 90 if denied else int(code)


if __name__ == "__main__":
    raise SystemExit(main())
