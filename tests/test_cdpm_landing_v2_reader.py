"""A05: receive exact package bytes; compatibility is not execution consent."""
import copy
import hashlib
import json
import zipfile
from types import SimpleNamespace

import pytest

from cdpm import model as m
from test_cdpm_landing_v2_candidate import case
from test_k01_family_manifest import manifest
from test_k01_semantic_transition import clean_bridge
from webapp import task_package as reader


def package(tmp_path, monkeypatch):
    from cdpm import catalog_bridge
    doc, rule = case(tmp_path, monkeypatch)
    # Keep this test about one actual landing, not unrelated sample rows.
    doc["rules"] = [rule]
    # The sample's goal questions reference the removed sample rules.
    doc["questions"] = []
    assert m.validate_schema(doc) == m.validate_model(doc) == []
    declared = manifest(catalog_bridge.catalog())
    return doc, rule, declared


def receive(tmp_path, doc, declared, *, run_gate=False, model_bytes=None,
            duplicate_manifest_compat=False, **kwargs):
    bodies = {"model.json": (model_bytes if model_bytes is not None else
                            json.dumps(doc, ensure_ascii=False).encode()),
              "objective.json": b"{}"}
    declared = copy.deepcopy(declared)
    declared["files"] = [{"name": n, "sha256": hashlib.sha256(b).hexdigest()}
                         for n, b in bodies.items()]
    path = tmp_path / "landing.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in bodies.items():
            archive.writestr(name, body)
        manifest_json = json.dumps(declared)
        if duplicate_manifest_compat:
            manifest_json = '{"landing_compat": 0, ' + manifest_json[1:]
        archive.writestr("manifest.json", manifest_json)
    before = path.read_bytes()
    try:
        return reader.load(path, run_gate=run_gate, **kwargs)
    finally:
        assert path.read_bytes() == before  # loading does not migrate evidence


def test_typed_reader_keeps_original_pin_with_current_manifest(tmp_path, monkeypatch):
    doc, rule, declared = package(tmp_path, monkeypatch)
    pin = rule["template_fit"]["family_contract"]
    assert pin["catalog_version"] != declared["catalog"]["version"]
    result = receive(tmp_path, doc, declared)
    assert result["model"] == doc
    assert result["landing_projection"]["archive_only"] is False
    assert rule["template_fit"]["params"]["min_days_off"]["value"] == 8


@pytest.mark.parametrize("version", ["missing", "landing/1", "landing/3", None, ""])
def test_reader_refuses_unconfirmed_or_unknown_version(tmp_path, monkeypatch, version):
    doc, rule, declared = package(tmp_path, monkeypatch)
    if version == "missing":
        del rule["template_fit"]["schema_version"]
    else:
        rule["template_fit"]["schema_version"] = version
    with pytest.raises(reader.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared)
    assert error.value.code == "landing-version"


@pytest.mark.parametrize("field", ["value", "unit", "relation", "condition", "quote", "source"])
def test_reader_rejects_incomplete_typed_cell_without_legacy_fallback(tmp_path, monkeypatch, field):
    doc, rule, declared = package(tmp_path, monkeypatch)
    rule["params"] = [{"name": "min_days_off", "value": 8, "unit": "count",
                       "relation": "min", "as_written": "8"}]
    del rule["template_fit"]["params"]["min_days_off"][field]
    with pytest.raises(reader.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared)
    assert error.value.code == "landing-incomplete"


@pytest.mark.parametrize("field", ["family_id", "catalog_fingerprint"])
def test_reader_rejects_false_pin_even_without_families_manifest(tmp_path, monkeypatch, field):
    doc, rule, declared = package(tmp_path, monkeypatch)
    declared.pop("families")
    rule["template_fit"]["family_contract"][field] = "wrong"
    with pytest.raises(reader.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared)
    assert error.value.code == "landing-contract"


def test_reader_requires_own_catalog_identity_without_families_claim(tmp_path, monkeypatch):
    doc, _rule, declared = package(tmp_path, monkeypatch)
    declared.pop("families")
    declared.pop("catalog")
    with pytest.raises(reader.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared)
    assert error.value.code == "landing-contract"


@pytest.mark.parametrize("version", ["missing", "landing/1"])
def test_legacy_archive_requires_two_explicit_compat_flags_and_cannot_execute(tmp_path, monkeypatch, version):
    doc, rule, declared = package(tmp_path, monkeypatch)
    if version == "missing": del rule["template_fit"]["schema_version"]
    else: rule["template_fit"]["schema_version"] = version
    # Legacy cells have an optional string condition, not the typed null.
    rule["template_fit"]["params"]["min_days_off"].pop("condition")
    assert m.validate_schema(doc) == []
    declared["landing_compat"] = 1
    with pytest.raises(reader.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared)
    assert error.value.code == "landing-version"
    result = receive(tmp_path, doc, declared, landing_compat=1)
    assert result["landing_projection"]["archive_only"] is True
    assert result["model"] == doc
    # Even a caller removing the derived display flag cannot authorize it.
    result.pop("landing_projection")
    with pytest.raises(reader.TaskPackageRefused) as error:
        reader.to_payload(result)
    assert error.value.code == "landing-version"


def test_reader_refuses_diverging_carriers_before_use(tmp_path, monkeypatch):
    doc, rule, declared = package(tmp_path, monkeypatch)
    rule["params"] = [{"name": "min_days_off", "value": 80, "unit": "count",
                       "relation": "min", "as_written": "80"}]
    with pytest.raises(reader.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared)
    assert error.value.code == "param-carriers-diverge"


@pytest.mark.parametrize("caller,claimed", [
    (1, None), (True, 1), (1, True), ("1", 1), (1, "1"),
    (0, 1), (1, 0), (2, 1), (1, 2),
])
def test_archive_flags_cannot_be_implied_or_coerced(tmp_path, monkeypatch, caller, claimed):
    doc, rule, declared = package(tmp_path, monkeypatch)
    del rule["template_fit"]["schema_version"]
    rule["template_fit"]["params"]["min_days_off"].pop("condition")
    if claimed is not None:
        declared["landing_compat"] = claimed
    with pytest.raises(reader.TaskPackageRefused) as error:
        receive(tmp_path, doc, declared, landing_compat=caller)
    assert error.value.code == "landing-version"


@pytest.mark.parametrize("compat", [None, 1])
def test_load_passes_explicit_archive_mode_to_gate_adapter(tmp_path, monkeypatch, compat):
    """Tests adapter arguments, not the verdict of a real gate subprocess."""
    doc, rule, declared = package(tmp_path, monkeypatch)
    if compat == 1:
        del rule["template_fit"]["schema_version"]
        rule["template_fit"]["params"]["min_days_off"].pop("condition")
        declared["landing_compat"] = 1
    commands = []

    def fake_subprocess(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(stdout=b"adapter-only", stderr=b"", returncode=0)

    monkeypatch.setattr(reader.subprocess, "run", fake_subprocess)
    result = receive(tmp_path, doc, declared, run_gate=True, landing_compat=compat)
    assert len(commands) == 1
    command, kwargs = commands[0]
    assert command[1:3] == ["-m", reader.GATE_MODULE]
    assert command[3] == str(tmp_path / "landing.zip")
    assert command[4:] == (["--landing-compat", "1"] if compat else [])
    assert kwargs["timeout"] == 300 and kwargs["capture_output"] is True
    assert result["landing_projection"]["archive_only"] is (compat == 1)


def test_bad_archive_adapter_flag_refused_before_process(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid compatibility must not launch the producer gate")
    monkeypatch.setattr(reader.subprocess, "run", forbidden)
    with pytest.raises(reader.TaskPackageRefused) as error:
        reader.run_producer_gate(tmp_path / "absent.zip", landing_compat=True)
    assert error.value.code == "landing-version"


def test_archive_run_labels_its_non_execution_verdict(monkeypatch, capsys):
    from cdpm.gates import gate_task_package as gate
    seen = []

    def fake_check(path, *, landing_compat=None):
        seen.append(landing_compat)
        return [], {"files": 3, "rules": 1, "objectives": 0, "modes": 0,
                    "spec_version": "1.18", "landing_archive_only": True}

    monkeypatch.setattr(gate, "check", fake_check)
    assert gate.run("adapter-only.zip", landing_compat=1) == 0
    assert seen == [1]
    words = capsys.readouterr().out
    assert "архивное чтение landing/1; исполнение запрещено" in words
    assert "пакет постановки соответствует спецификации" not in words


@pytest.mark.parametrize("args,expected", [
    (["fixture.zip"], None), (["fixture.zip", "--landing-compat", "1"], 1),
])
def test_gate_cli_preserves_explicit_archive_choice(monkeypatch, args, expected):
    from cdpm.gates import gate_task_package as gate
    seen = []

    def fake_run(path, *, landing_compat=None):
        seen.append((path, landing_compat))
        return 0

    monkeypatch.setattr(gate, "run", fake_run)
    assert gate.main(args) == 0
    assert seen == [("fixture.zip", expected)]


@pytest.mark.parametrize("value", ["0", "2", "True"])
def test_gate_cli_refuses_other_archive_choices(monkeypatch, value):
    from cdpm.gates import gate_task_package as gate
    monkeypatch.setattr(gate, "run", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(SystemExit) as error:
        gate.main(["fixture.zip", "--landing-compat", value])
    assert error.value.code == 2


def test_receiver_does_not_silently_replace_duplicate_value(tmp_path, monkeypatch):
    doc, _rule, declared = package(tmp_path, monkeypatch)
    raw = json.dumps(doc, ensure_ascii=False).encode()
    assert raw.count(b'"value": 8') == 1
    raw = raw.replace(b'"value": 8', b'"value": 80, "value": 8', 1)
    # A last-key-wins decoder would recreate the healthy model exactly.
    assert json.loads(raw) == doc
    with pytest.raises(reader.TaskPackageRefused, match="дубль ключа"):
        receive(tmp_path, doc, declared, model_bytes=raw)


def test_receiver_refuses_duplicate_archive_consent(tmp_path, monkeypatch):
    doc, _rule, declared = package(tmp_path, monkeypatch)
    declared["landing_compat"] = 1
    with pytest.raises(reader.TaskPackageRefused, match="дубль ключа"):
        receive(tmp_path, doc, declared, duplicate_manifest_compat=True, landing_compat=1)
