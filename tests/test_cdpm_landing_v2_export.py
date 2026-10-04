"""New-export format boundary; no live LLM/solver/JVM or stand is run.

The actual build path is stopped at healing or the later modes boundary.
Pure migration and the shared producer/receiver format decision are not mocked.
The SOFT-only complete package uses a real zero-hard-rule report, not a
synthetic consistency verdict, and proves no executor or operational readiness.
"""
import copy
import json
import io
import zipfile

import pytest

from cdpm import export_task_package as producer, model as m
from test_cdpm_landing_migration import legacy
from test_k01_semantic_transition import clean_bridge


def test_new_export_migrates_without_changing_source_bytes(tmp_path, monkeypatch):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    original = copy.deepcopy(doc)
    raw = json.dumps(doc, ensure_ascii=False).encode()
    proposed = producer._load_modern_package_model(raw)
    fit = proposed["rules"][0]["template_fit"]
    assert fit["schema_version"] == "landing/2"
    assert fit["family_contract"]["catalog_version"] == "0.7.0"
    assert fit["params"]["min_days_off"]["value"] == 8
    assert proposed["meta"]["landing_migrations"][-1]["items"][0]["state"] == "confirmed"
    assert producer._load_modern_package_model(raw) == proposed
    assert json.loads(raw) == original == doc


@pytest.mark.parametrize("change", ["unknown-version", "missing-unit", "wrong-pin", "unknown-origin"])
def test_export_does_not_repair_invalid_modern_or_guess_a_legacy_origin(tmp_path, monkeypatch, change):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    if change == "unknown-origin":
        rule["template_fit"]["registry_rev"] = "unknown"
    else:
        doc = producer._load_modern_package_model(json.dumps(doc).encode())
        fit = doc["rules"][0]["template_fit"]
        if change == "unknown-version": fit["schema_version"] = "landing/3"
        elif change == "missing-unit": del fit["params"]["min_days_off"]["unit"]
        else: fit["family_contract"]["record_fingerprint"] = "0" * 64
    raw = json.dumps(doc).encode()
    with pytest.raises(producer.TaskPackageError) as error:
        producer._load_modern_package_model(raw)
    assert error.value.code in ("landing-version", "landing-incomplete", "landing-contract")
    assert json.dumps(doc).encode() == raw


def test_actual_builder_reaches_healing_with_typed_model_and_keeps_input(tmp_path, monkeypatch):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    run = tmp_path / "run-landing"
    path = run / "extract" / "model.json"
    path.parent.mkdir(parents=True)
    raw = json.dumps(doc, ensure_ascii=False).encode()
    path.write_bytes(raw)
    seen = []

    class ReachedHealing(Exception):
        pass

    def stop_after_real_format_gate(proposed):
        seen.append(copy.deepcopy(proposed))
        raise ReachedHealing

    monkeypatch.setattr(producer, "heal_for_package", stop_after_real_format_gate)
    with pytest.raises(ReachedHealing):
        producer.build_package(run)
    assert len(seen) == 1
    assert seen[0]["rules"][0]["template_fit"]["schema_version"] == "landing/2"
    assert seen[0]["meta"]["landing_migrations"]
    assert path.read_bytes() == raw


def test_actual_builder_judges_original_carrier_conflict_before_migration(tmp_path, monkeypatch):
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    rule["params"] = [{"name": "min_days_off", "value": 80, "unit": "count",
                       "relation": "min", "as_written": "80"}]
    run = tmp_path / "run-conflict"
    path = run / "extract" / "model.json"
    path.parent.mkdir(parents=True)
    raw = json.dumps(doc).encode()
    path.write_bytes(raw)

    def forbidden(_raw):
        raise AssertionError("the conflicting original must be refused before migration")

    monkeypatch.setattr(producer, "_load_modern_package_model", forbidden)
    with pytest.raises(producer.TaskPackageError) as error:
        producer.build_package(run)
    assert error.value.code == m.PARAM_CARRIERS_DIVERGE
    assert path.read_bytes() == raw
    words = str(error.value)
    assert "полной согласованной заплатой цикла" in words
    assert "посадку и соответствующие params[]" in words
    assert "снимите устаревшую запись" not in words


@pytest.mark.parametrize("change", [None, "unknown-version", "wrong-pin", "missing-unit"])
def test_builder_rechecks_actual_landing_after_healing(tmp_path, monkeypatch, change):
    """Fault injection after REAL healing, not a mocked format verdict.

    The healthy control reaches the later modes boundary; damaged landing
    must be refused before it. No solver/consistency result is fabricated.
    """
    doc, rule, cat = legacy(tmp_path, monkeypatch)
    run = tmp_path / "run-post-heal"
    path = run / "extract" / "model.json"
    path.parent.mkdir(parents=True)
    raw = json.dumps(doc, ensure_ascii=False).encode()
    path.write_bytes(raw)
    actual_heal = producer.heal_for_package
    observed = []

    def heal_then_inject(proposed):
        changes = actual_heal(proposed)
        fit = proposed["rules"][0]["template_fit"]
        if change == "unknown-version":
            fit["schema_version"] = "landing/3"
        elif change == "wrong-pin":
            fit["family_contract"]["record_fingerprint"] = "0" * 64
        elif change == "missing-unit":
            del fit["params"]["min_days_off"]["unit"]
        observed.append(copy.deepcopy(proposed))
        return changes

    class ReachedModes(Exception):
        pass

    def stop_at_modes(proposed, spec_version=None):
        assert spec_version == producer.SPEC_VERSION
        from cdpm import catalog_bridge, landing_contract
        assert not landing_contract.inspect(
            proposed, {"catalog": catalog_bridge.stamp()}, for_execution=True)["problems"]
        raise ReachedModes

    monkeypatch.setattr(producer, "heal_for_package", heal_then_inject)
    monkeypatch.setattr(producer, "allowed_modes", stop_at_modes)
    if change is None:
        with pytest.raises(ReachedModes):
            producer.build_package(run)
    else:
        with pytest.raises(producer.TaskPackageError) as error:
            producer.build_package(run)
        assert error.value.code == ("landing-version" if change == "unknown-version"
                                    else "landing-contract" if change == "wrong-pin"
                                    else "landing-incomplete")
    assert len(observed) == 1
    assert path.read_bytes() == raw


def real_soft_package(tmp_path, monkeypatch, revision, *, with_data=False):
    """Complete offline package, not operational executor readiness.

    Default: the real verifier records no hard rules, with no translation/Z3.
    with_data: a synthetic book and one DATA rule with exact unit translation
    checked by real local Z3. Package/readiness/format judges stay real.
    """
    from cdpm import smt_verify, catalog_bridge
    from cdpm.gates import gate_task_package
    from webapp import task_package as receiver
    from test_cdpm_landing_v2_projection import typed_rule
    from template_catalog import bindings

    # Future protocol is scoped to this test; no release/version activation.
    monkeypatch.setattr(producer, "SPEC_VERSION", revision)

    cells = {
        "duty_extension_cap": {"value": 960, "unit": "duration_minutes", "relation": "max",
                               "condition": None, "quote": "960 минут"},
        "extension_bases": {"value": ["ST1", "ST2"], "unit": "code_list", "relation": "in",
                            "condition": None, "quote": "ST1, ST2"},
        "extension_consent": {"value": True, "unit": "flag", "relation": "yes",
                              "condition": None, "quote": "с согласия"},
    }
    doc, rule = typed_rule(tmp_path, monkeypatch, "duty_extension_penalty", cells)
    cat = catalog_bridge.catalog()
    support_path = cat.path / "support.json"
    native = bindings.support_table(cat, {}, computed_at="2026-09-16T00:00:00Z")
    support_path.write_text(json.dumps(native, ensure_ascii=False), encoding="utf-8")
    rule.update(kind="SOFT", zone="PAIRING", weight=3,
                statement="Продление смены не более 960 минут для ST1, ST2 только с согласия экипажа.")
    doc["rules"], doc["questions"], doc["objectives"] = [rule], [], []
    if with_data:
        doc["rules"].append({"id": "D-CREW", "title": "Состав экипажа",
            "statement": "Экипаж состоит из 1 пилота", "kind": "DATA", "zone": "BOTH",
            "sources": copy.deepcopy(rule["sources"]),
            "params": [{"name": "crew_size", "value": 1, "relation": "eq",
                        "unit": "count", "as_written": "1 пилот"}]})
    fit = rule["template_fit"]
    del fit["schema_version"]
    for cell in fit["params"].values():
        for key in ("unit", "relation", "condition", "source"):
            del cell[key]
    pin = copy.deepcopy(fit["family_contract"])
    run = tmp_path / "run-full-soft"
    path = run / "extract" / "model.json"
    path.parent.mkdir(parents=True)
    raw = json.dumps(doc, ensure_ascii=False).encode()
    path.write_bytes(raw)

    if with_data:
        from test_task_package import _pkg_A7D
        # Explicit synthetic book; real clean-data writer, not a payload stub.
        data = _pkg_A7D()["clean_data"]
        for folder, filename, value in (
                ("data-freeze", "dataset.json", data),
                ("data-report", "data-report.json", {"findings": [], "checks": []})):
            target = run / folder / filename
            target.parent.mkdir()
            target.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def forbidden(*_args, **_kwargs):
        pytest.fail("No translator/solver is allowed in this SOFT-only fixture")

    monkeypatch.setattr(producer, "smt_channel_factory", forbidden)
    if with_data:
        # This separate fixture adds one DATA rule, which is a genuine SMT
        # obligation. Supply its exact deterministic translation, not a fake
        # verdict. The real in-process Z3 checks one integer equality; no
        # network/model/roster solver or service is invoked.
        def unit_translation(prompt):
            assert "D-CREW" in prompt and "Экипаж состоит из 1 пилота" in prompt
            return json.dumps({"decls": ["(declare-const crew_size Int)"],
                "asserts": [{"rule": "D-CREW", "smt": "(= crew_size 1)"}]})

        report = smt_verify.verify(doc, unit_translation,
            model_sha256=smt_verify.model_fingerprint(raw))
        assert report["n_rules"] == report["n_translated"] == 1
        assert report["verdict"] == "sat"
    else:
        monkeypatch.setattr(smt_verify, "_run_z3", forbidden)
        report = smt_verify.verify(doc, forbidden, model_sha256=smt_verify.model_fingerprint(raw))
        assert report["n_rules"] == report["n_translated"] == 0
    smt_verify.write_report(run, report)
    migration = m.loads_model(raw)["meta"]["landing_migrations"][-1]
    assert migration["items"][0]["state"] == "confirmed", json.dumps(migration["items"], ensure_ascii=False)
    body = producer.export_zip(run)
    package_path = tmp_path / "real-soft.zip"
    package_path.write_bytes(body)
    return doc, cat, native, support_path, run, path, raw, report, pin, body, package_path


@pytest.mark.parametrize("revision", ["1.18", "1.20"])
def test_real_package_preserves_migration_through_gate_and_receiver(tmp_path, monkeypatch, revision):
    from cdpm import catalog_bridge
    from cdpm.gates import gate_task_package
    from webapp import task_package as receiver

    doc, cat, native, support_path, run, path, raw, report, pin, body, package_path = (
        real_soft_package(tmp_path, monkeypatch, revision))
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        actual = json.loads(archive.read("model.json"))
        declared = json.loads(archive.read("manifest.json"))
    actual_fit = actual["rules"][0]["template_fit"]
    assert actual_fit["schema_version"] == "landing/2"
    assert actual_fit["family_contract"] == pin
    assert actual_fit["params"]["duty_extension_cap"]["value"] == 960
    assert actual_fit["params"]["extension_bases"]["value"] == ["ST1", "ST2"]
    assert actual_fit["params"]["extension_consent"]["value"] is True
    assert actual["meta"]["landing_migrations"][-1]["items"][0]["state"] == "confirmed"
    assert declared["catalog"] == catalog_bridge.stamp()
    assert declared["spec_version"] == revision
    if revision == "1.20":
        expected_support = copy.deepcopy(native)
        expected_support["cells"] = [c for c in native["cells"] if
            c["section"] == "families" and c["record_id"] == "duty_extension_penalty"]
        assert declared["support"] == expected_support
        assert all(c["state"] == "нет" and c["approved"] is None
                   for c in declared["support"]["cells"])
    else:
        assert "support" not in declared
    assert declared["consistency"]["subject_sha256"] == report["subject_sha256"]
    problems, _ = gate_task_package.check(package_path)
    assert problems == [], problems
    received = receiver.load(package_path, run_gate=False)
    assert received["model"] == actual
    assert received["landing_projection"]["archive_only"] is False
    assert producer.export_zip(run) == body
    with pytest.raises(receiver.TaskPackageRefused, match="нет очищенных данных"):
        receiver.to_payload(received)
    assert path.read_bytes() == raw and package_path.read_bytes() == body
    if revision == "1.20":
        support_path.unlink()
        with pytest.raises(producer.TaskPackageError) as error:
            producer.export_zip(run)
        assert error.value.code == "support-missing"
