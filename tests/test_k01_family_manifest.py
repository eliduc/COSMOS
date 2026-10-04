"""K01 receiver replays the model; a self-consistent claim is not evidence.

Real temporary catalogues and package bytes. No external gate process,
network, services or solver are used by these selected receive-side tests.
Legacy fixtures opt into archive reading at both ends; only the actual
export fixture below carries landing/2, without granting execution.
"""
import copy
import hashlib
import json
import zipfile

import pytest

from cdpm import catalog_bridge as cb
from template_catalog import canon
from test_k01_param_set_reference import FAMILY, pinned_doc, unchanged, expanded
from test_k01_semantic_transition import pair, clean_bridge
from webapp import task_package as receiver


def manifest(cat, *, landing_compat=None):
    # Independent oracle: this fixture carries eight days in the monthly
    # set, not a result returned by the implementation being tested.
    declared = {"spec_version": "1.18", "catalog": {
        "version": cat.version(), "fingerprint": cat.fingerprint()},
        "allowed_modes": {"pairing": [], "rostering": []},
        "families": {FAMILY: {
            "contract": {"family": FAMILY, "catalog_version": cat.version(),
                         "record_fingerprint": canon.record_fingerprint(cat.family(FAMILY)),
                         "param_set": "monthly"},
            "verdict": "полна", "given": ["min_days_off"],
            "declined": [], "pending": [], "missing": []}}}
    # Shared by typed landing/2 consumers: archive consent is never implied.
    if landing_compat is not None:
        declared["landing_compat"] = landing_compat
    return declared


def setup_case(tmp_path, monkeypatch, edit=unchanged):
    _unused, old, new = pair(tmp_path, monkeypatch, edit)
    return pinned_doc(old, new, monkeypatch), new


def receive(tmp_path, doc, declared, *, landing_compat=None):
    # Rehash the altered model. Failures must reach family recomputation,
    # not stop at a stale ZIP member checksum or a subprocess gate.
    bodies = {"model.json": json.dumps(doc, ensure_ascii=False).encode(),
              "objective.json": b"{}"}
    declared = copy.deepcopy(declared)
    declared["files"] = [{"name": name, "sha256": hashlib.sha256(data).hexdigest()}
                         for name, data in bodies.items()]
    path = tmp_path / "received.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name, data in bodies.items():
            z.writestr(name, data)
        z.writestr("manifest.json", json.dumps(declared, ensure_ascii=False))
    return receiver.load(path, run_gate=False, landing_compat=landing_compat)


def assert_archive_only(result):
    # This oracle belongs only to the explicitly opted-in legacy K01 cases,
    # not to the shared receive helper used by modern landing/2 consumers.
    assert result["landing_projection"]["archive_only"] is True
    # A successful family check of these legacy bytes is archive evidence,
    # never an execution permission (including the unaffected-task case).
    before = copy.deepcopy(result)
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receiver.to_payload(result)
    assert error.value.code == "landing-version"
    assert result == before


def test_healthy_projection_and_pin_are_preserved(tmp_path, monkeypatch):
    from cdpm import family_manifest
    doc, cat = setup_case(tmp_path, monkeypatch)
    before = copy.deepcopy(doc)
    declared = manifest(cat, landing_compat=1)
    assert family_manifest.project(doc) == declared["families"]
    result = receive(tmp_path, doc, declared, landing_compat=1)
    assert_archive_only(result)
    assert result["family_projection"]["judged"] is True
    assert result["model"] == before == doc
    assert result["model"]["rules"][-1]["template_fit"]["family_contract"]["catalog_version"] == "0.7.0"


@pytest.mark.parametrize("reader_compat,manifest_compat", [(None, 1), (1, None)],
                         ids=["manifest-only", "reader-only"])
def test_legacy_archive_requires_explicit_compat_at_both_ends(
        tmp_path, monkeypatch, reader_compat, manifest_compat):
    doc, cat = setup_case(tmp_path, monkeypatch)
    declared = manifest(cat, landing_compat=manifest_compat)
    before = copy.deepcopy((doc, declared))
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared, landing_compat=reader_compat)
    assert error.value.code == "landing-version"
    assert (doc, declared) == before


@pytest.mark.parametrize("field,value", [
    ("given", []), ("given", ["forged"]), ("declined", ["min_days_off"]),
    ("pending", ["min_days_off"]), ("missing", ["min_days_off"]),
    ("verdict", "неполна"), ("given", None), ("pending", False),
])
def test_changed_manifest_field_rejected(tmp_path, monkeypatch, field, value):
    doc, cat = setup_case(tmp_path, monkeypatch)
    declared = manifest(cat, landing_compat=1)
    declared["families"][FAMILY][field] = value
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared, landing_compat=1)
    assert error.value.code == "manifest-mismatch"
    assert field in str(error.value)


@pytest.mark.parametrize("value", [None, {}, [], False])
def test_present_broken_or_omitted_family_is_not_legacy(tmp_path, monkeypatch, value):
    doc, cat = setup_case(tmp_path, monkeypatch)
    declared = manifest(cat, landing_compat=1)
    declared["families"] = value
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared, landing_compat=1)
    assert error.value.code == "manifest-mismatch"


@pytest.mark.parametrize("field", ["catalog_version", "record_fingerprint", "param_set", "family"])
def test_wrong_contract_rejected(tmp_path, monkeypatch, field):
    doc, cat = setup_case(tmp_path, monkeypatch)
    declared = manifest(cat, landing_compat=1)
    declared["families"][FAMILY]["contract"][field] = "wrong"
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared, landing_compat=1)
    assert error.value.code == "manifest-mismatch"


def test_receiver_catalogue_must_match_the_declared_release(tmp_path, monkeypatch):
    from cdpm import family_manifest
    doc, cat = setup_case(tmp_path, monkeypatch)
    declared = manifest(cat, landing_compat=1)
    declared["catalog"]["fingerprint"] = "0" * 64
    # The format gate now rejects the wrong catalogue before the family
    # replay. Keep the stricter boundary and independently exercise K01.
    before = copy.deepcopy((doc, declared))
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared, landing_compat=1)
    assert error.value.code == "landing-contract"
    assert "manifest.catalog" in str(error.value)
    problems = family_manifest.check(doc, declared)
    assert len(problems) == 1
    assert problems[0]["code"] == "manifest-mismatch"
    assert "manifest.catalog" in problems[0]["words"]
    assert (doc, declared) == before


def test_rehashed_model_cannot_keep_old_full_manifest(tmp_path, monkeypatch):
    from cdpm import model as m
    doc, cat = setup_case(tmp_path, monkeypatch)
    assert m.validate_schema(doc) == []
    fit = doc["rules"][-1]["template_fit"]
    assert set(fit["params"]) == {"min_days_off"}
    # Null is not a legal value, and an empty legacy params object is not
    # schema-valid either. Remove the sole cell/carrier instead: the format
    # remains readable, but the old "full" family declaration is now false.
    fit.pop("params")
    schema_problems = m.validate_schema(doc)
    assert schema_problems == [], schema_problems
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receive(tmp_path, doc, manifest(cat, landing_compat=1), landing_compat=1)
    assert error.value.code == "manifest-mismatch", str(error.value)
    assert "given" in str(error.value) or "missing" in str(error.value), str(error.value)


def test_schema_valid_pending_revision_cannot_keep_old_full_manifest(tmp_path, monkeypatch):
    from cdpm import family_manifest, model as m
    doc, cat = setup_case(tmp_path, monkeypatch)
    declared = manifest(cat, landing_compat=1)
    cell = doc["rules"][-1]["template_fit"]["params"]["min_days_off"]
    cell["pending_revision"] = "Q-legal-R1"
    before = copy.deepcopy((doc, declared))
    # A legal string carrier reaches K01, unlike the malformed boolean case
    # below. This is a schema/completeness case, not a revision lifecycle.
    assert m.validate_schema(doc) == []
    state = family_manifest.project(doc)[FAMILY]
    assert state["given"] == ["min_days_off"]
    assert state["pending"] == ["min_days_off"]
    assert state["verdict"] == "неполна"
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared, landing_compat=1)
    assert error.value.code == "manifest-mismatch", str(error.value)
    assert f"families.{FAMILY}.pending" in str(error.value)
    assert f"families.{FAMILY}.verdict" in str(error.value)
    assert cell["value"] == 8 and (doc, declared) == before


@pytest.mark.parametrize("field,value", [("value", None), ("pending_revision", True)],
                         ids=["null-value", "boolean-pending-flag"])
def test_malformed_legacy_cell_refuses_format_and_old_full_manifest(
        tmp_path, monkeypatch, field, value):
    from cdpm import family_manifest, model as m
    doc, cat = setup_case(tmp_path, monkeypatch)
    assert m.validate_schema(doc) == []
    doc["rules"][-1]["template_fit"]["params"]["min_days_off"][field] = value
    declared = manifest(cat, landing_compat=1)
    before = copy.deepcopy((doc, declared))
    schema_problems = m.validate_schema(doc)
    assert schema_problems, {"field": field, "value": value}
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared, landing_compat=1)
    assert error.value.code == "landing-incomplete", str(error.value)
    assert all(problem in str(error.value) for problem in schema_problems), str(error.value)
    # Separate K01 oracle on malformed input, not proof of the legal open-
    # revision path: pending_revision must be an actual revision-question ID.
    problems = family_manifest.check(doc, declared)
    assert any(problem["code"] == "manifest-mismatch" for problem in problems), problems
    assert (doc, declared) == before


@pytest.mark.parametrize("carrier", ["PAIRING", "ROSTERING"])
def test_honest_missing_family_allows_only_unaffected_task(tmp_path, monkeypatch, carrier):
    doc, cat = setup_case(tmp_path, monkeypatch, expanded)
    doc["rules"][-1]["zone"] = carrier
    assert "kotlin_logic_form" in cb.executor_kinds(FAMILY)
    assert "builder_method" not in cb.executor_kinds(FAMILY)
    declared = manifest(cat, landing_compat=1)
    state = declared["families"][FAMILY]
    # The old monthly pin now requires the window; a newly introduced
    # easier alternative may be the automatic set, but cannot erase it.
    state["contract"]["param_set"] = "alternative"
    state.update(verdict="неполна", missing=["days_off_window_days"])
    # days_off_floor always consumes ROSTERING; a PAIRING carrier adds
    # that task, it cannot erase the declared Kotlin consumer. With a
    # ROSTERING carrier only, PAIRING really is unaffected and must stay
    # readable (archive-only, never an execution approval).
    declared["allowed_modes"] = {"pairing": [], "rostering": []}
    result = receive(tmp_path, doc, declared, landing_compat=1)
    assert result["family_projection"]["judged"]
    assert_archive_only(result)
    for task in ("pairing", "rostering"):
        declared["allowed_modes"] = {"pairing": [], "rostering": []}
        declared["allowed_modes"][task] = ["feasibility-hard"]
        if carrier == "ROSTERING" and task == "pairing":
            result = receive(tmp_path, doc, declared, landing_compat=1)
            assert result["family_projection"]["judged"]
            assert_archive_only(result)
        else:
            with pytest.raises(receiver.TaskPackageRefused) as error:
                receive(tmp_path, doc, declared, landing_compat=1)
            assert error.value.code == "family-incomplete"
            assert "days_off_window_days" in str(error.value)


def test_absent_legacy_field_is_explicitly_not_judged(tmp_path, monkeypatch):
    doc, cat = setup_case(tmp_path, monkeypatch)
    declared = manifest(cat, landing_compat=1)
    declared.pop("families")
    result = receive(tmp_path, doc, declared, landing_compat=1)
    assert_archive_only(result)
    assert result["family_projection"]["judged"] is False


def test_human_parameter_review_names_expansion_and_keeps_pin(tmp_path, monkeypatch):
    from cdpm import parameter_review
    doc, cat = setup_case(tmp_path, monkeypatch, expanded)
    before = copy.deepcopy(doc)
    review = parameter_review.from_model(doc)["family_review"]
    assert review["judged"] is True
    assert review["catalog"]["fingerprint"] == cat.fingerprint()
    family = next(row for row in review["families"] if row["family"] == FAMILY)
    assert family["missing"] == ["days_off_window_days"]
    assert any("расшир" in words for words in family["transitions"])
    assert any("days_off_window_days" in words for words in family["problems"])
    assert doc == before


def test_human_review_catalogue_failure_does_not_hide_model_values(tmp_path, monkeypatch):
    from cdpm import parameter_review
    doc, _cat = setup_case(tmp_path, monkeypatch)
    monkeypatch.setenv(cb.ENV_CATALOG, str(tmp_path / "absent-catalogue"))
    cb.forget()
    review = parameter_review.from_model(doc)
    assert review["family_review"]["judged"] is False
    assert review["family_review"]["words"]
    assert review["rules"][-1]["params"][0]["value"] == 8


def test_human_review_names_real_relaxation_without_rewriting_origin(tmp_path, monkeypatch):
    from cdpm import parameter_review
    from template_catalog import family_contract
    from test_k01_param_set_reference import relaxed
    _unused, old, new = pair(tmp_path, monkeypatch, relaxed)
    doc = pinned_doc(old, new, monkeypatch)
    fit = doc["rules"][-1]["template_fit"]
    fit["params"] = {"days_off_window_days": {"value": 28, "quote": "28"}}
    fit["family_contract"] = family_contract.snapshot(old, FAMILY, fit["params"])
    before = copy.deepcopy(doc)
    review = parameter_review.from_model(doc)["family_review"]
    family = next(row for row in review["families"] if row["family"] == FAMILY)
    assert family["verdict"] == "полна" and not family["missing"]
    assert any("rolling_window" in words and "сужен" in words for words in family["transitions"])
    assert doc == before


def test_human_endpoint_snapshot_exposes_actual_transition_and_keeps_model_bytes(tmp_path, monkeypatch):
    from cdpm import webui
    from test_cdpm_k01_family_barrier import _run_dir
    doc, _cat = setup_case(tmp_path, monkeypatch, expanded)
    run = _run_dir(tmp_path, doc, "run-0042")
    path = run / "extract" / "model.json"
    monkeypatch.setenv("CDPM_RUNS_DIR", str(tmp_path))
    before = path.read_bytes()
    # Invoke the actual endpoint with the actual file, but not HTTP transport:
    # Windows asyncio TestClient creates a local socketpair; the offline
    # guard deliberately does not allow socket.bind for this profile.
    response = webui.run_effective_parameters("run-0042")
    assert response.status_code == 200
    data = json.loads(response.body)
    assert data["model_sha256"] == hashlib.sha256(before).hexdigest()
    family = next(row for row in data["family_review"]["families"] if row["family"] == FAMILY)
    assert family["missing"] == ["days_off_window_days"]
    assert any("расшир" in words for words in family["transitions"])
    assert path.read_bytes() == before


def test_real_export_gate_and_receiver_agree(tmp_path, monkeypatch):
    from cdpm import export_task_package as producer, family_manifest, model as m, smt_verify
    from cdpm.gates import gate_task_package as gate
    from test_cdpm_landing_v2_export import real_soft_package
    # The old synthetic rest_floors source had no provable migration origin.
    # Reuse the pinned SOFT seed's catalogue/support and real migration. Its
    # family is intentionally incomplete, so the seed package is NOT the
    # healthy family-completeness control. Prepare a separate full source.
    (_seed_doc, cat, _native, _support, _seed_run, seed_path, seed_bytes,
     _seed_report, pin, seed_body, seed_package) = real_soft_package(
        tmp_path / "seed", monkeypatch, "1.20")
    doc = m.loads_model(seed_bytes)
    assert len(doc["rules"]) == 1
    rule = doc["rules"][0]
    family = "duty_extension_penalty"
    fit = rule["template_fit"]
    assert fit["schema_version"] == "landing/2"
    threshold = next(p for p in cat.family(family)["params"]
                     if p["name"] == "long_duty_threshold")
    assert (threshold["unit"], threshold["relation"]) == ("duration_minutes", "max")
    rule["statement"] += " Мягкий порог продлённой смены — не более 720 минут."
    fit["params"]["long_duty_threshold"] = {
        "value": 720, "unit": "duration_minutes", "relation": "max",
        "condition": None, "quote": "720 минут",
        "source": {"kind": "rule", "rule_id": rule["id"]}}
    assert fit["family_contract"] == pin
    schema_problems = m.validate_schema(doc)
    fit_problems = m.validate_template_fit(doc)
    assert schema_problems == fit_problems == [], (schema_problems, fit_problems)
    clauses = {(source["sid"], source["anchor"]): rule["statement"]
               for source in rule["sources"]}
    quote_problems = m.validate_fit_quotes(doc, clauses)
    assert quote_problems == [], quote_problems
    prepared = family_manifest.project(doc)[family]
    assert prepared["missing"] == [], prepared
    # K01 given lists catalogue parameters, not applicability conditions.
    # All four landing cells must survive, but only these two close the
    # family's parameter set. Keep this oracle independent of project().
    expected_given = {"duty_extension_cap", "long_duty_threshold"}
    expected_conditions = {"extension_bases", "extension_consent"}
    assert {p["name"] for p in cat.family(family)["params"]} == expected_given
    assert {p["name"] for p in cat.family(family)["conditions"]} == expected_conditions
    assert set(fit["params"]) == expected_given | expected_conditions
    assert set(prepared["given"]) == expected_given, prepared
    original_fit = copy.deepcopy(fit)

    run = tmp_path / "run-full-family"
    source_path = run / "extract" / "model.json"
    source_path.parent.mkdir(parents=True)
    source_bytes = json.dumps(doc, ensure_ascii=False).encode("utf-8")
    source_path.write_bytes(source_bytes)

    def forbidden_translation(*_args, **_kwargs):
        pytest.fail("SOFT-only family fixture must not call a translator or solver")

    # A changed source needs its own real report, not the seed's old hash.
    report = smt_verify.verify(doc, forbidden_translation,
        model_sha256=smt_verify.model_fingerprint(source_bytes))
    assert report["n_rules"] == report["n_translated"] == 0, report
    smt_verify.write_report(run, report)
    body = producer.export_zip(run)
    path = tmp_path / "full-family.zip"
    path.write_bytes(body)
    with zipfile.ZipFile(path) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    declared = json.loads(files["manifest.json"])
    released = json.loads(files["model.json"])
    fit = released["rules"][0]["template_fit"]
    assert fit["schema_version"] == "landing/2"
    assert fit["family"] == family and fit["family_contract"] == pin
    assert fit == original_fit
    assert declared["spec_version"] == "1.20"
    assert "landing_compat" not in declared
    assert declared["families"] == family_manifest.project(released)
    assert declared["families"][family]["missing"] == [], declared["families"][family]
    assert set(declared["families"][family]["given"]) == expected_given
    problems, _counters = gate.check(path)
    assert not problems, problems
    received = receiver.load(path, run_gate=False)
    assert received["family_projection"]["judged"] is True
    assert received["landing_projection"]["archive_only"] is False
    assert received["model"] == released
    declared["families"][family]["given"] = []
    files["manifest.json"] = json.dumps(declared).encode()
    forged_path = tmp_path / "forged-families.zip"
    forged_path.write_bytes(producer.write_zip(files))
    problems, _counters = gate.check(forged_path)
    assert any("manifest-mismatch" in problem for problem in problems), problems
    with pytest.raises(receiver.TaskPackageRefused) as error:
        receiver.load(forged_path, run_gate=False)
    assert error.value.code == "manifest-mismatch", str(error.value)
    assert source_path.read_bytes() == source_bytes
    assert path.read_bytes() == body
    assert seed_path.read_bytes() == seed_bytes
    assert seed_package.read_bytes() == seed_body
