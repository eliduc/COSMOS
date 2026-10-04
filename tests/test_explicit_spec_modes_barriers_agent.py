"""Version-sensitive package modes/barriers, without approval or execution.

The temporary 0.6 catalogue uses copies of existing executor declarations,
with all stand observations removed and no approval. This legitimately
distinguishes the legacy declaration-based gate from modern support/1.
The SOFT-only package uses the real zero-HARD verifier and actual ZIP readers;
the HARD case stops at the real support barrier, before any SMT operation.

EXPLICIT_SPEC_MUTATION_AGENT optionally loads one mechanically mutated copy
of the exporter from pytest's own temporary directory. It never edits the
product or changes a validator's verdict. Run each mutant only on its named
target test; the unmutated profile remains the ordinary regression suite.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

from cdpm import catalog_bridge as cb, model as m
from cdpm import export_task_package as producer, smt_verify
from cdpm.gates import gate_task_package as gate
from template_catalog import bindings
from webapp import task_package as receiver
from test_explicit_package_spec_agent import soft_run  # noqa: F401
from test_k06_scope_agent import healthy_model
from test_k01_semantic_transition import clean_bridge  # noqa: F401


ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = {
    "allowed_modes": (
        "modes, objective_ids = allowed_modes(doc, spec_version)",
        "modes, objective_ids = allowed_modes(doc)"),
    "refuse_by_barriers": (
        "refuse_by_barriers(doc, modes, spec_version)",
        "refuse_by_barriers(doc, modes)"),
}


def _forbidden(*_args, **_kwargs):
    pytest.fail("This profile must not translate, call a channel, or execute Z3")


@pytest.fixture
def exporter(tmp_path, monkeypatch):
    mutation = os.environ.get("EXPLICIT_SPEC_MUTATION_AGENT", "")
    module = producer
    source = Path(producer.__file__)
    original = source.read_bytes()
    if mutation:
        assert mutation in MUTATIONS, "only the two reviewed call-site mutations are allowed"
        old, new = MUTATIONS[mutation]
        assert original.count(old.encode()) == 1
        changed = original.replace(old.encode(), new.encode(), 1)
        target = tmp_path / ("export_task_package_" + mutation + ".py")
        target.write_bytes(changed)
        name = "cdpm._explicit_spec_mutant_agent"
        spec = importlib.util.spec_from_file_location(name, target)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        print(json.dumps({"mutation": mutation, "copy": str(target),
                          "source_sha256": hashlib.sha256(original).hexdigest(),
                          "copy_sha256": hashlib.sha256(changed).hexdigest()}))
    monkeypatch.setattr(module, "smt_channel_factory", _forbidden)
    monkeypatch.setattr(smt_verify, "_run_z3", _forbidden)
    yield module
    assert source.read_bytes() == original
    assert producer.SPEC_VERSION == module.SPEC_VERSION == "1.18"


def _unsigned_declarations(tmp_path, monkeypatch, cat):
    """Retain real declared symbols, not historical stand/approval claims."""
    docs, paths, inputs = [], {}, {}
    for product, relative, variable in (
        ("COSMOS", "vmp/bindings.json", "CREW_COSMOS_BINDINGS"),
        ("CDPM", "cdpm/bindings.json", "CDPM_BINDINGS_FILE"),
    ):
        source = ROOT / relative
        inputs[source] = source.read_bytes()
        doc = bindings.load_bindings(source)
        doc.update(catalog_version=cat.version(), catalog_fingerprint=cat.fingerprint())
        for row in doc["bindings"]:
            row["stand_verified"] = []
        path = tmp_path / (product.lower() + "-unsigned-declarations.json")
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setenv(variable, str(path))
        paths[product] = path
        docs.append(bindings.load_bindings(path))
    problems = bindings.check_bindings(cat, *docs)
    assert not [p for p in problems if p.severity == "error"], problems
    assert not [p for p in problems if p.code == "S06"], problems
    native = bindings.support_table(cat, paths, stand=None, approved_by=None,
                                    computed_at="2026-09-18T00:00:00Z")
    assert bindings.support_of(cat, native)["valid"]
    assert all(c["approved"] is None and c["state"] != "есть" for c in native["cells"])
    support = cat.path / "support.json"
    support.write_text(json.dumps(native, ensure_ascii=False), encoding="utf-8")
    cb.forget()
    inputs.update({p: p.read_bytes() for p in [*paths.values(), support]})
    inputs.update({p: p.read_bytes() for p in cat.path.glob("*.json")})
    return native, inputs


def _unchanged(inputs):
    for path, raw in inputs.items():
        assert path.read_bytes() == raw, str(path)


@pytest.mark.parametrize("revision", ["1.18", "1.20"])
def test_modes_use_selected_spec_in_actual_zip(soft_run, tmp_path, monkeypatch,
                                               exporter, revision):
    native, inputs = _unsigned_declarations(tmp_path, monkeypatch, cb.catalog())
    source = soft_run["source"]
    inputs[source] = source.read_bytes()
    doc = m.loads_strict(inputs[source])
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    assert m.support_problems(doc, "1.18") == []
    assert m.support_problems(doc, "1.20"), "unsigned modern support must still refuse SOFT"
    assert producer.allowed_modes(doc, "1.18")[0]["pairing"] == [
        "feasibility-soft", "feasibility-hard"]
    assert producer.allowed_modes(doc, "1.20")[0]["pairing"] == ["feasibility-hard"]

    body = exporter.export_zip(soft_run["run"], spec_version=revision)
    path = tmp_path / ("actual-" + revision + ".zip")
    path.write_bytes(body)
    problems, counters = gate.check(path)
    assert problems == [], problems
    assert counters["spec_version"] == revision
    package = receiver.load(path, run_gate=False)  # the actual gate ran above, no CLI
    manifest = package["manifest"]
    assert manifest["spec_version"] == revision
    expected = ["feasibility-soft", "feasibility-hard"] if revision == "1.18" else ["feasibility-hard"]
    assert manifest["allowed_modes"]["pairing"] == expected
    assert package["landing_projection"]["archive_only"] is False
    if revision == "1.20":
        expected_cells = [c for c in native["cells"] if
                          c["section"] == "families" and c["record_id"] == "duty_extension_penalty"]
        assert manifest["support"]["cells"] == expected_cells
        assert all(c["approved"] is None for c in expected_cells)
        receiver.check_support_execution(package, task="pairing", mode="feasibility-hard")
        with pytest.raises(receiver.TaskPackageRefused) as failure:
            receiver.check_support_execution(package, task="pairing", mode="feasibility-soft")
        assert failure.value.code == "support-missing", str(failure.value)
    else:
        assert "support" not in manifest
    assert path.read_bytes() == body
    _unchanged(inputs)


def test_barrier_preserves_modern_support_address_before_smt(tmp_path, monkeypatch, exporter):
    doc, cat = healthy_model(tmp_path / "hard", monkeypatch)
    _native, inputs = _unsigned_declarations(tmp_path, monkeypatch, cat)
    assert m.validate_schema(doc) == m.validate_model(doc) == m.validate_template_fit(doc) == []
    assert m.support_problems(doc, "1.18") == []
    legacy_modes = producer.allowed_modes(doc, "1.18")[0]
    assert all("feasibility-hard" in legacy_modes[task] for task in ("pairing", "rostering"))
    # Healthy declaration-based barrier control; not a HARD SMT/export claim.
    assert producer.refuse_by_barriers(doc, legacy_modes, "1.18") is None
    modern_modes = producer.allowed_modes(doc, "1.20")[0]
    assert modern_modes == {"pairing": [], "rostering": []}, modern_modes
    barriers = m.readiness_barriers(doc, "1.20")
    assert barriers and all(p["code"] == "support-missing" for p in barriers), barriers
    assert {p["item"] for p in barriers} == {"families/time_scale"}
    run = tmp_path / "hard-run"
    source = run / "extract" / "model.json"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    inputs[source] = source.read_bytes()
    with pytest.raises(exporter.TaskPackageError) as failure:
        exporter.build_package(run, spec_version="1.20")
    assert failure.value.code == "support-missing", str(failure.value)
    assert set(failure.value.items) == {"families/time_scale"}, failure.value.items
    assert any(p["words"] in str(failure.value) for p in barriers), str(failure.value)
    assert not (run / "smt-check").exists(), "the addressed refusal must precede SMT"
    _unchanged(inputs)
