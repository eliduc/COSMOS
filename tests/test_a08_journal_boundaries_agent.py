"""A08 journal boundaries, using real package gates and the real receiver.

Oracle: P0-CONTRACTS-v1.13-20260913.md sections 3.4, 3.6, 12.9;
P0-COSMOS-SIDE-v1-20260911.md section 6.3. Archive reading is not
projection PASS or an execution permit; semantic corruption is refused.

The existing real_soft_package helper creates synthetic isolated catalog/package
data with a pinned landing/2, real migration and a zero-HARD consistency report.
Explicit offline seams: service health, coverage, payload conversion,
and interruption of the public progress callback after the real mode entry.
No producer/receiver judge or journal writer is patched. The transport-fault
case changes a temporary ZIP only AFTER the producer has checked it, then
calls the real receiver. No HTTP, executor, solver or stand is started.

Repeat means package_from reuses a prior button's bytes, NOT solver-job
resume. The long-message test measures persisted delivery detail/problems;
whether a UI links to that full text is deliberately not claimed here.
"""
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from cdpm import export_task_package as exporter
from cdpm.gates import gate_task_package as producer
from test_cdpm_landing_v2_export import real_soft_package
from test_k01_semantic_transition import clean_bridge  # noqa: F401 (fixture)
from vmp import catalog_requests, objective_projection as projection
from vmp.run import button
from webapp import task_package as receiver


INTERRUPTED = "offline interruption after mode was journalled"
TAIL_RULE = "zz_a08_tail_marker_after_the_three_hundred_character_preview"


class NoExecution:
    def __init__(self):
        self.health_calls = 0

    def health(self):
        self.health_calls += 1
        return True

    def __getattr__(self, name):
        raise AssertionError("No executor call is allowed: " + name)


@pytest.fixture(autouse=True)
def isolated_catalog_queue(monkeypatch, tmp_path):
    monkeypatch.setenv(catalog_requests.ENV_AUTO, "0")
    monkeypatch.setenv(catalog_requests.ENV_OUTBOX, str(tmp_path / "queue"))
    monkeypatch.delenv(catalog_requests.ENV_API_URL, raising=False)
    monkeypatch.delenv(catalog_requests.ENV_TOKEN, raising=False)


def _package(tmp_path, monkeypatch, spec):
    # Same healthy source as proba-a08-arhiv-v-zhurnale-COSMOS.py. Unlike
    # the older criterion fixture, this really exports modern pinned landings.
    parts = real_soft_package(tmp_path, monkeypatch, spec)
    path = parts[-1]
    with zipfile.ZipFile(path) as archive:
        model = json.loads(archive.read("model.json"))
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["spec_version"] == spec
    assert model["rules"][0]["template_fit"]["schema_version"] == "landing/2"
    assert model["rules"][0]["template_fit"]["family_contract"] == parts[8]
    return path


def _rewrite_objective(body, change):
    """Keep a corruption's ZIP/manifest hashes valid: test semantics, not CRC."""
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    objective = json.loads(files["objective.json"])
    change(objective)
    files["objective.json"] = projection.canonical_bytes(objective)
    manifest = json.loads(files["manifest.json"])
    for entry in manifest["files"]:
        if entry["name"] == "objective.json":
            entry["sha256"] = hashlib.sha256(files["objective.json"]).hexdigest()
    files["manifest.json"] = json.dumps(manifest).encode("utf-8")
    return exporter.write_zip(files)


def _add_long_mismatch(objective):
    components = objective["pairing"]["components"]
    assert components  # The positive package has a real SOFT component.
    for number in range(4):
        components.append({
            "rule_id": f"a08_untrusted_extra_{number}_must_not_be_silently_accepted",
            "weight": 1})
    components.append({"rule_id": TAIL_RULE, "weight": 1})


def _press_until_mode(run, path, *, out_dir, package_from=None, load_pkg=None):
    callbacks = []
    converted = []
    service = NoExecution()

    def convert(pkg):
        converted.append(pkg)
        # This journal-boundary test is not a data converter proof.
        return {}

    def progress(step, status, detail):
        callbacks.append((step, status, detail))
        if step == "Ш-1к":
            # A public progress-sink interruption after partial progress;
            # do not bypass a judge or manufacture a journal verdict.
            raise button.ButtonStop(INTERRUPTED, "No registry or executor reached")

    result = button.orchestrate(
        run, service, task="pairing", mode="feasibility-hard",
        package_path=path, package_from=package_from, out_dir=out_dir,
        coverage_fn=lambda *_: None,
        load_pkg=load_pkg or (lambda source: receiver.load(source, run_gate=False)),
        to_payload=convert, log=progress)
    assert service.health_calls == 1
    return result, callbacks, converted


def _saved(run, out_dir, result, callbacks):
    out = run / out_dir
    delivery = json.loads((out / "delivery.json").read_text(encoding="utf-8"))
    journal = json.loads((out / "log.json").read_text(encoding="utf-8"))
    assert delivery == result
    assert journal == result["journal"]
    assert callbacks == [(row["step"], row["status"], row["detail"])
                         for row in journal]
    return journal


def _assert_scope(journal, spec):
    rows = [row for row in journal if row["step"] == "Ш-1ц"]
    assert len(rows) == 1, journal
    entry = rows[0]
    if spec == "1.18":
        assert entry["status"] == "не судится"
        assert entry["detail"] == projection.archive_words(spec)
        assert "не есть разрешение исполнения" in entry["detail"]
    else:
        assert entry["status"] == "пройден"
        assert "судимой части" in entry["detail"]
        assert "не общий допуск" in entry["detail"]
    return entry


@pytest.mark.parametrize("spec", ["1.18", "1.20"])
def test_later_interruption_keeps_projection_scope_and_completed_progress(
        tmp_path, monkeypatch, spec):
    path = _package(tmp_path, monkeypatch, spec)
    assert producer.check(path)[0] == []
    result, callbacks, converted = _press_until_mode(tmp_path, path, out_dir="later")
    assert result["verdict"] == INTERRUPTED, result
    assert len(converted) == 1
    journal = _saved(tmp_path, "later", result, callbacks)
    entry = _assert_scope(journal, spec)
    steps = [row["step"] for row in journal]
    assert steps.index("Ш-1") < steps.index("Ш-1ц") < steps.index("Ш-1к")
    assert journal[-2]["step"] == "Ш-1к"
    assert journal[-1]["step"] == "остановка"
    assert entry in journal[:-2]
    assert not (tmp_path / "registry").exists()


@pytest.mark.parametrize("corrupt_in_transport", [False, True])
def test_real_receiver_long_refusal_preserves_full_delivery_beyond_preview(
        tmp_path, monkeypatch, corrupt_in_transport):
    path = _package(tmp_path, monkeypatch, "1.20")
    assert producer.check(path)[0] == []
    assert receiver.load(path, run_gate=False)["objective_projection"]["judged"] is True
    original = path.read_bytes()
    refusals = []
    loaded_paths = []

    def transport(source):
        source = Path(source)
        loaded_paths.append(source)
        assert source.read_bytes() == original
        if corrupt_in_transport:
            source.write_bytes(_rewrite_objective(original, _add_long_mismatch))
        try:
            return receiver.load(source, run_gate=False)
        except receiver.TaskPackageRefused as exc:
            refusals.append(exc)
            raise

    result, callbacks, converted = _press_until_mode(
        tmp_path, path, out_dir="transport", load_pkg=transport)
    assert loaded_paths == [tmp_path / "transport" / "package.zip"]
    assert path.read_bytes() == original
    journal = _saved(tmp_path, "transport", result, callbacks)
    assert any(row["step"] == "Ш-1" and row["status"] == "пройден"
               for row in journal)
    if not corrupt_in_transport:
        assert result["verdict"] == INTERRUPTED, result
        assert not refusals
        assert len(converted) == 1
        _assert_scope(journal, "1.20")
        return

    assert len(refusals) == 1
    refusal = refusals[0]
    assert refusal.code == "objective-mismatch"
    assert result["verdict"] == "остановка: пакет постановки не принят"
    assert result["code"] == refusal.code
    assert result["detail"] == str(refusal)
    assert result["problems"] == refusal.problems
    assert len(result["detail"]) > 300
    assert TAIL_RULE in result["detail"]
    assert TAIL_RULE not in result["detail"][:300]
    assert any(problem["where"] == "pairing.components[" + TAIL_RULE + "]"
               for problem in result["problems"])
    assert journal[-1]["detail"] == result["detail"][:299] + "…"
    assert not converted
    assert not any(row["step"] in {"Ш-1ц", "Ш-1к"} for row in journal)
    # The same rehashed corrupt bytes are also rejected by the producer.
    assert any("objective-mismatch" in word
               for word in producer.check(loaded_paths[0])[0])


@pytest.mark.parametrize("spec", ["1.18", "1.20"])
def test_repeat_reuses_exact_bytes_and_rechecks_scope_but_refuses_changed_input(
        tmp_path, monkeypatch, spec):
    path = _package(tmp_path, monkeypatch, spec)
    first, first_callbacks, _ = _press_until_mode(tmp_path, path, out_dir="first")
    assert first["verdict"] == INTERRUPTED, first
    _assert_scope(_saved(tmp_path, "first", first, first_callbacks), spec)
    first_files = {name: (tmp_path / "first" / name).read_bytes()
                   for name in ("package.zip", "log.json", "delivery.json")}

    repeated, callbacks, converted = _press_until_mode(
        tmp_path, path, out_dir="repeated", package_from="first")
    assert repeated["verdict"] == INTERRUPTED, repeated
    assert len(converted) == 1
    _assert_scope(_saved(tmp_path, "repeated", repeated, callbacks), spec)
    assert (tmp_path / "repeated" / "package.zip").read_bytes() == first_files["package.zip"]

    # Negative control: a request with changed content cannot silently resume
    # the old bytes, even when the old package's projection was archival.
    changed = tmp_path / "changed.zip"
    changed.write_bytes(_rewrite_objective(path.read_bytes(), _add_long_mismatch))
    refused, callbacks, converted = _press_until_mode(
        tmp_path, changed, out_dir="changed-repeat", package_from="first")
    assert "постановка изменилась" in refused["verdict"], refused
    assert "objective.json" in refused["detail"]
    journal = _saved(tmp_path, "changed-repeat", refused, callbacks)
    assert not converted
    assert not any(row["step"] == "Ш-1ц" for row in journal)
    assert not (tmp_path / "changed-repeat" / "package.zip").exists()
    for name, original in first_files.items():
        assert (tmp_path / "first" / name).read_bytes() == original
