"""Explicit package SPEC without changing the process-wide producer default.

Real typed model, catalogue, unsigned support, exporter, ZIP gate and reader.
The existing temporary 0.6 fixture is not the prepared 0.7 draft or a release.
Only SOFT rules are supplied: the actual verifier records zero HARD rules;
translation/Z3 are forbidden, not replaced with a successful verdict.
Before the keyword API exists, TypeError is missing capability, not a measured
semantic regression. No test changes SPEC_VERSION or manufactures approval.
"""
import copy
import io
import json
import zipfile

import pytest

from cdpm import catalog_bridge as cb, family_manifest, model as m, smt_verify
from cdpm import export_task_package as producer
from cdpm import export_solver, threshold_forms
from cdpm.gates import gate_task_package as gate
from template_catalog import bindings
from test_cdpm_landing_v2_projection import typed_rule
from test_k01_semantic_transition import clean_bridge  # noqa: F401
from webapp import task_package as receiver


FAMILY = "duty_extension_penalty"


@pytest.fixture
def soft_run(tmp_path, monkeypatch):
    cells = {
        "duty_extension_cap": {
            "value": 960, "unit": "duration_minutes", "relation": "max",
            "condition": None, "quote": "960 минут"},
        "long_duty_threshold": {
            "value": 720, "unit": "duration_minutes", "relation": "max",
            "condition": None, "quote": "720 минут"},
        "extension_bases": {
            "value": ["ST1", "ST2"], "unit": "code_list", "relation": "in",
            "condition": None, "quote": "ST1, ST2"},
        "extension_consent": {
            "value": True, "unit": "flag", "relation": "yes",
            "condition": None, "quote": "с согласия"},
    }
    doc, rule = typed_rule(tmp_path / "typed", monkeypatch, FAMILY, cells)
    rule.update(kind="SOFT", zone="PAIRING", weight=3,
                statement="Мягкий порог смены — не более 720 минут; продление "
                          "не более 960 минут для ST1, ST2 только с согласия экипажа.")
    doc["rules"], doc["questions"], doc["objectives"] = [rule], [], []
    assert m.validate_schema(doc) == m.validate_template_fit(doc) == []
    clauses = {(s["sid"], s["anchor"]): rule["statement"] for s in rule["sources"]}
    assert m.validate_fit_quotes(doc, clauses) == []
    assert m.reconcile_carriers(rule) == []
    assert family_manifest.project(doc)[FAMILY]["missing"] == []

    cat = cb.catalog()
    native = bindings.support_table(cat, {}, stand=None, approved_by=None,
                                    computed_at="2026-09-18T00:00:00Z")
    assert native["cells"] and all(c["approved"] is None for c in native["cells"])
    support = cat.path / "support.json"
    support.write_text(json.dumps(native, ensure_ascii=False), encoding="utf-8")
    assert bindings.support_of(cat, native)["valid"]

    run = tmp_path / "run-explicit-spec"
    source = run / "extract" / "model.json"
    source.parent.mkdir(parents=True)
    raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    source.write_bytes(raw)

    def forbidden(*_args, **_kwargs):
        pytest.fail("SOFT-only package cannot call a translator, channel or Z3")

    monkeypatch.setattr(producer, "smt_channel_factory", forbidden)
    monkeypatch.setattr(smt_verify, "_run_z3", forbidden)
    report = smt_verify.verify(doc, forbidden,
                              model_sha256=smt_verify.model_fingerprint(raw))
    assert report["n_rules"] == report["n_translated"] == 0
    smt_verify.write_report(run, report)
    paths = [source, support] + [cat.path / name for name in (
        "catalog.json", "families.json", "criteria.json", "dictionaries.json")]
    return {"run": run, "source": source, "support": support, "native": native,
            "pin": copy.deepcopy(rule["template_fit"]["family_contract"]),
            "inputs": {p: p.read_bytes() for p in paths}}


def _unchanged(parts, *, except_support=False):
    assert producer.SPEC_VERSION == "1.18", "per-call SPEC must not change the default"
    for path, raw in parts["inputs"].items():
        if not (except_support and path == parts["support"]):
            assert path.read_bytes() == raw, str(path)


def _read_package(parts, body, tmp_path, revision):
    path = tmp_path / ("package-" + revision + ".zip")
    path.write_bytes(body)
    problems, counters = gate.check(path)
    assert problems == [], problems
    assert counters["spec_version"] == revision
    # The same real gate was called in-process above; do not spawn its CLI.
    received = receiver.load(path, run_gate=False)
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        objective = json.loads(archive.read("objective.json"))
        thresholds = (archive.read("threshold-forms.json")
                      if "threshold-forms.json" in archive.namelist() else None)
        assert json.loads(archive.read("manifest.json")) == received["manifest"]
    doc, manifest = received["model"], received["manifest"]
    assert manifest["spec_version"] == revision
    assert manifest["catalog"] == cb.stamp() == objective["catalog"]
    assert received["landing_projection"]["archive_only"] is False
    assert doc["rules"][0]["template_fit"]["family_contract"] == parts["pin"]
    assert manifest["families"][FAMILY]["missing"] == []
    assert manifest["allowed_modes"] == producer.allowed_modes(doc, revision)[0]
    assert manifest["readiness_matrix"] == producer.readiness_ladder(doc, None, revision)
    assert thresholds == threshold_forms.forms_bytes(doc, revision)
    ready = m.readiness(doc, revision)
    assert manifest["readiness"] == ready["state"]
    assert objective["matrix"] == m.readiness_matrix(doc, revision)
    assert objective["readiness"] == ready["state"]
    for task in ("pairing", "rostering"):
        assert objective[task]["ready"] is bool(ready[task + "_ready"])
    assert objective["pairing"]["direction"] == "minimize"
    components = objective["pairing"]["components"]
    assert len(components) == 1 and components[0]["weight"] == 3
    assert components[0]["rule_id"] == doc["rules"][0]["id"]
    assert "feasibility-hard" in manifest["allowed_modes"]["pairing"]
    assert "feasibility-soft" not in manifest["allowed_modes"]["pairing"]
    assert "optimization" not in manifest["allowed_modes"]["pairing"]
    if revision in ("1.20", "1.21"):
        expected = copy.deepcopy(parts["native"])
        expected["cells"] = [c for c in expected["cells"]
                             if c["section"] == "families" and c["record_id"] == FAMILY]
        assert expected["cells"] and manifest["support"] == expected
        assert all(c["approved"] is None and c["state"] == "нет"
                   for c in manifest["support"]["cells"])
        receiver.check_support_execution(received, task="pairing", mode="feasibility-hard")
        with pytest.raises(receiver.TaskPackageRefused) as error:
            receiver.check_support_execution(received, task="pairing", mode="feasibility-soft")
        assert error.value.code == "support-missing", str(error.value)
    else:
        assert "support" not in manifest
    assert path.read_bytes() == body
    _unchanged(parts)
    return manifest


def test_existing_objective_api_uses_explicit_readiness_not_global_default(soft_run):
    """Causal BASE on an existing API, independently of future ZIP keywords."""
    doc = m.loads_strict(soft_run["source"].read_bytes())
    selected = m.readiness_matrix(doc, "1.20")
    implicit = m.readiness_matrix(doc)
    assert selected != implicit, "fixture must distinguish unsigned modern support from 1.18"
    actual = json.loads(export_solver.build_objective_json(doc, spec_version="1.20"))
    assert actual["matrix"] == selected, "explicit objective context must use the selected SPEC"
    ready = m.readiness(doc, "1.20")
    assert actual["readiness"] == ready["state"]
    for task in ("pairing", "rostering"):
        assert actual[task]["ready"] is bool(ready[task + "_ready"])
    _unchanged(soft_run)


def test_omitted_and_none_spec_keep_the_current_default(soft_run, tmp_path):
    files = producer.build_package(soft_run["run"])
    body = producer.write_zip(files)
    _read_package(soft_run, body, tmp_path, "1.18")
    assert producer.export_zip(soft_run["run"]) == body
    assert producer.export_zip(soft_run["run"], spec_version=None) == body
    _unchanged(soft_run)


@pytest.mark.parametrize("revision", ["1.19", "1.22", "", 120])
def test_unknown_explicit_spec_is_rejected_before_reading_a_run(tmp_path, revision):
    missing_run = tmp_path / "no-run"
    with pytest.raises(producer.TaskPackageError, match="неподдерживаемая версия пакета") as error:
        producer.export_zip(missing_run, spec_version=revision)
    assert error.value.code is None
    assert not missing_run.exists()
    assert producer.SPEC_VERSION == "1.18"


@pytest.mark.parametrize("revision", ["1.20", "1.21"])
def test_explicit_spec_reaches_the_actual_zip_gate_and_reader(soft_run, tmp_path, revision):
    files = producer.build_package(soft_run["run"], spec_version=revision)
    body = producer.write_zip(files)
    _read_package(soft_run, body, tmp_path, revision)
    assert producer.export_zip(soft_run["run"], spec_version=revision) == body
    _unchanged(soft_run)


def test_modern_default_modern_do_not_leak_spec_or_change_inputs(soft_run, tmp_path):
    first = producer.export_zip(soft_run["run"], spec_version="1.20")
    _read_package(soft_run, first, tmp_path, "1.20")
    middle = producer.export_zip(soft_run["run"])
    _read_package(soft_run, middle, tmp_path, "1.18")
    last = producer.export_zip(soft_run["run"], spec_version="1.21")
    _read_package(soft_run, last, tmp_path, "1.21")
    assert producer.export_zip(soft_run["run"], spec_version="1.20") == first
    _unchanged(soft_run)


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_explicit_modern_refuses_bad_support_instead_of_legacy_fallback(soft_run, damage):
    if damage == "missing":
        soft_run["support"].unlink()
    else:
        soft_run["support"].write_bytes(b"{not valid JSON")
    cb.forget()
    with pytest.raises(producer.TaskPackageError) as error:
        producer.export_zip(soft_run["run"], spec_version="1.21")
    assert error.value.code == "support-missing", str(error.value)
    assert "support" in str(error.value).lower()
    assert soft_run["support"].exists() is (damage == "corrupt")
    if damage == "corrupt":
        assert soft_run["support"].read_bytes() == b"{not valid JSON"
    _unchanged(soft_run, except_support=True)
