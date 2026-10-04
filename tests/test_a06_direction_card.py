"""A06: real ZIP/catalogue -> actual GET handler -> actual card DOM renderer.

No app startup, HTTP listener, services, operational solver or LLM. The GET function is compiled
unchanged from app.py into an isolated FastAPI router; identity and unrelated
flow-readiness are isolated. Node runs the production card renderer in a minimal
DOM, not a live browser. Real package reading and catalogue selection are NOT
mocked. Synthetic future spec does not activate the production writer version.
Its support/1 is built and validated from temporary bindings with explicitly
synthetic stand evidence; it is not a release or execution approval.
The answer fixture has one HARD rule: a deterministic SMT translation is the
only model substitute; the real local Z3 checker, exporter and gates still run.
"""
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from cdpm import catalog_bridge as cb, export_task_package as tp, model
from cdpm import support_manifest
from cdpm.gates import gate_task_package as producer
from vmp import catalog_access as ca, objective_projection as op
from webapp import task_channel as channel, task_package as consumer
from test_a06_model_catalogue import document, use_real_catalogue
from test_cdpm_task_package import _run_dir, _ready_doc
from test_k06_scope_agent import native_support, publish

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated_card(monkeypatch, tmp_path):
    monkeypatch.setenv("CREW_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CREW_TASK_INBOX", "")
    monkeypatch.setenv("CREW_CDPM_RECORD_API", "")
    from webapp import flow, build_identity
    monkeypatch.setattr(flow, "readiness_state", lambda: {})
    # Host/build discovery is unrelated to projection/card semantics. Report
    # it explicitly unavailable, without git subprocesses or invented approval.
    monkeypatch.setattr(build_identity, "build_identity", lambda **_: {
        "schema": "cosmos-build-identity/v1", "source": {}, "solver": {}})
    channel._CARD_CACHE.update(key=None, value=None)
    yield
    channel._CARD_CACHE.update(key=None, value=None)
    cb.forget()
    ca.forget()


def package(tmp_path, monkeypatch, spec="1.20", direction="minimize", mutate=None,
            private=False, carrier="document", legacy_answer_rule=False):
    cat = use_real_catalogue(tmp_path, monkeypatch, direction)
    doc = document(carrier, direction=direction)
    translation_calls = []
    if carrier == "answer":
        # Unlike the derivation-only helper, a complete package needs a real
        # target for the question; keep the healthy model-validation oracle.
        doc["objectives"] = document("prose", direction=direction)["objectives"]
        doc["rules"] = _ready_doc()["rules"][:1]
        if not legacy_answer_rule:
            # A complete modern execution fixture must carry a real typed
            # landing. Do not relabel or grant execution to the old x<=1 row.
            from template_catalog import family_contract
            row = doc["rules"][0]
            row["statement"] = "В экипаже 2 пилота."
            cells = {"crew_size": {"value": 2, "unit": "count", "relation": "eq", "condition": None,
                                  "quote": "2", "source": {
                                      "kind": "code", "note": "explicit synthetic card input"}}}
            row["template_fit"] = {
                "schema_version": "landing/2", "category": "A",
                "family": "crew_composition", "registry_rev": cat.fingerprint(),
                "family_contract": family_contract.snapshot(cat, "crew_composition", cells),
                "params": cells}
            assert model.validate_template_fit(doc) == []
            def translate(prompt):
                translation_calls.append(prompt)
                return json.dumps({"decls": ["(declare-fun crew_size () Int)"],
                                   "asserts": [{"rule": "R-H", "smt": "(= crew_size 2)"}]})
            monkeypatch.setattr(tp, "smt_channel_factory", lambda run_dir: translate)
        doc["questions"][0]["rule_refs"] = ["R-H"]
        raw = json.loads(doc["questions"][0]["answer"]["raw"])
        raw["rule"] = "R-H"
        doc["questions"][0]["answer"]["raw"] = json.dumps(raw)
    assert model.validate_model(doc) == []
    if support_manifest.required(spec):
        # A future package needs genuine support/1 construction, not just a
        # changed revision string. This display fixture uses one real criterion.
        records = {("criteria", "min_person_days")}
        if carrier == "answer" and not legacy_answer_rule:
            records.add(("families", "crew_composition"))
        support = native_support(tmp_path, monkeypatch, cat, records)
        publish(cat, doc, support, records)
    files = tp.build_package(_run_dir(tmp_path, doc))
    if carrier == "answer" and not legacy_answer_rule:
        assert len(translation_calls) == 1
        consistency = json.loads(files["manifest.json"])["consistency"]
        assert consistency["verdict"] == "sat"
        assert consistency["n_rules"] == consistency["n_translated"] == 1
    obj = json.loads(files["objective.json"])
    assert obj["pairing"]["direction"] == direction
    if mutate:
        mutate(obj)
    files["objective.json"] = op.canonical_bytes(obj)
    man = json.loads(files["manifest.json"])
    man["spec_version"] = spec  # Synthetic future package, not a release.
    if support_manifest.required(spec):
        released_doc = json.loads(files["model.json"])
        man["support"] = support_manifest.project(released_doc)
        man["allowed_modes"], man["objectives"] = tp.allowed_modes(
            released_doc, spec_version=spec)
        problems = model.support_problems(released_doc, spec)
        assert problems == [], problems
        # Do not make the display probe green by silently dropping optimization.
        assert "optimization" in man["allowed_modes"]["pairing"], man["allowed_modes"]
    if private:
        man.pop("record_id", None)
        man.pop("declaration_sha256", None)
        man.pop("record_api", None)
    for entry in man["files"]:
        if entry["name"] == "objective.json":
            entry["sha256"] = hashlib.sha256(files["objective.json"]).hexdigest()
    files["manifest.json"] = json.dumps(man).encode()
    path = tmp_path / "card.zip"
    path.write_bytes(tp.write_zip(files))
    monkeypatch.setenv("CREW_TASK_PACKAGE", str(path))
    return path, cat


def get_card(handler="api_task_package_current", url="/api/task-package/current"):
    # Execute the actual decorated GET handler without the full application's
    # startup side effects. Authentication itself is outside this display test.
    source = ROOT / "webapp" / "app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == handler)
    router = FastAPI()
    from webapp import cosmos_runs
    from fastapi.responses import JSONResponse
    namespace = {"__package__": "webapp", "Request": Request,
                 "app": router, "_user": lambda request: None,
                 "_user_or_channel": lambda request: None,
                 "_run_actor": lambda request, user: "display-test",
                 "_run_id_or_404": cosmos_runs.check_run_id,
                 "_cosmos_runs": cosmos_runs, "JSONResponse": JSONResponse}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), "exec"), namespace)
    with TestClient(router) as client:
        response = client.get(url)
    assert response.status_code == 200
    return response.json()


def visible_card(card, *, record=False):
    node = shutil.which("node")
    assert node, "Node is required for the actual DOM-renderer check"
    proc = subprocess.run(
        [node, str(ROOT / "tests" / "a06_card_dom.cjs"), "record" if record else "card"],
        input=json.dumps(card), text=True, encoding="utf-8",
        capture_output=True, timeout=20, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


@pytest.mark.parametrize("direction", ["minimize", "maximize"])
def test_validated_direction_and_catalogue_source_reach_http_and_dom(
        tmp_path, monkeypatch, direction):
    path, cat = package(tmp_path, monkeypatch, direction=direction)
    assert producer.check(path)[0] == []
    assert consumer.load(path, run_gate=False)["objective_projection"]["judged"] is True
    card = get_card()
    assert not card.get("error"), card
    text = visible_card(card)
    assert "Источник направления" in text
    assert f"min_person_days → {direction}" in text
    assert "0.7.0" in text and cat.fingerprint() in text
    assert "не является разрешением исполнения" in text
    assert direction in text


def test_archive_warning_reaches_actual_card_without_invented_verification(
        tmp_path, monkeypatch):
    path, _ = package(tmp_path, monkeypatch, spec="1.18")
    assert producer.check(path)[0] == []
    text = visible_card(get_card())
    assert op.archive_words("1.18") in text
    assert "Источник направления" not in text
    assert "Проекция сверена" not in text


def test_rehashed_forged_basis_is_not_a_display_authority(tmp_path, monkeypatch):
    marker = "FAKE CATALOGUE SOURCE <script>bad()</script>"
    path, cat = package(tmp_path, monkeypatch, mutate=lambda o:
                        o["pairing"].update(direction_basis=marker))
    # Basis is presentation, not a checked scalar: recompute it from the model,
    # never turn unchecked package prose into an authoritative human explanation.
    assert producer.check(path)[0] == []
    card = get_card()
    assert not card.get("error"), card
    text = visible_card(card)
    assert marker not in text
    assert f"min_person_days → minimize" in text
    assert cat.fingerprint() in text


def test_conflict_is_visible_and_not_presented_as_a_verified_direction(
        tmp_path, monkeypatch):
    path, _ = package(tmp_path, monkeypatch, mutate=lambda o:
                      o["pairing"].update(direction="maximize"))
    assert any("criterion-direction" in p for p in producer.check(path)[0])
    card = get_card()
    assert card.get("error") and "direction" in card["error"]
    text = visible_card(card)
    assert card["error"] in text
    assert "Проекция сверена" not in text
    assert "Источник направления" not in text


def test_card_cache_does_not_reuse_verdict_after_catalogue_changes(
        tmp_path, monkeypatch):
    path, _ = package(tmp_path, monkeypatch)
    original_catalog = cb.catalog().path
    original_zip = path.read_bytes()
    healthy = get_card()
    assert not healthy.get("error")
    other = tmp_path / "other"
    other.mkdir()
    use_real_catalogue(other, monkeypatch, "maximize")
    # Same package path and bytes; catalogue is the dependency that changed.
    assert channel.current_package()["path"] == str(path)
    changed = get_card()
    # A different catalogue fingerprint is rejected before direction judging;
    # a stale cached green card would evade this real identity barrier.
    assert changed.get("error") == (
        "landing-contract: manifest.catalog не совпадает с фактическим каталогом читателя"), changed
    assert path.read_bytes() == original_zip
    assert "Проекция сверена" not in visible_card(changed)
    monkeypatch.setenv(cb.ENV_CATALOG, str(original_catalog))
    cb.forget()
    ca.forget()
    restored = get_card()
    assert not restored.get("error"), restored
    assert restored["objective_projection"] == healthy["objective_projection"]


def test_missing_projection_status_is_not_a_green_card():
    text = visible_card({"present": True, "manifest": {}, "criterion": None})
    assert "Статус сверки проекции не установлен" in text
    assert "Проекция сверена" not in text


@pytest.mark.parametrize("case", ["healthy", "archive", "conflict", "missing"])
def test_record_route_reads_package_and_renders_its_own_projection(
        tmp_path, monkeypatch, case):
    from vmp import run_record as rr
    from test_run_record import declaration
    path, _ = package(tmp_path, monkeypatch,
                      private=True,
                      spec="1.18" if case == "archive" else "1.20",
                      mutate=(lambda o: o["pairing"].update(direction="maximize"))
                      if case == "conflict" else None)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    root = tmp_path / "runs"
    run = root / "run-0001"
    run.mkdir(parents=True)
    monkeypatch.setenv("CREW_COSMOS_RUNS_DIR", str(root))
    wd = tmp_path / "data" / "package-runs" / sha
    wd.mkdir(parents=True)
    record = rr.new_record(declaration())
    (wd / rr.RECORD_FILE).write_text(json.dumps(record), encoding="utf-8")
    (run / "run.json").write_text(json.dumps({
        "run": "run-0001", "current_package": sha,
        "packages": [{"sha": sha, "path": str(path)}]}), encoding="utf-8")
    if case == "missing":
        path.unlink()  # Only this test's temporary ZIP; record survives.
    out = get_card("api_cosmos_runs_record", "/api/cosmos-runs/run-0001/record")
    assert out["record"] == record  # A 200 empty fixture is not positive control.
    text = visible_card(out, record=True)
    if case == "healthy":
        assert out["declared"] is True
        assert "Источник направления" in text
        assert "min_person_days → minimize" in text
    elif case == "archive":
        assert op.archive_words("1.18") in text
    elif case == "conflict":
        assert "direction" in text and "maximize" in text and "minimize" in text
    else:
        assert "файл пакета недоступен" in text
    if case != "healthy":
        assert "Проекция сверена" not in text
        assert "Источник направления" not in text
    if case != "missing":
        current = get_card("api_run_current", "/api/run/current")
        assert current["record"] == record
        assert current["objective_projection"] == out["objective_projection"]
        assert visible_card(current, record=True) == text


@pytest.mark.parametrize("carrier", ["document", "answer", "prose"])
def test_card_criterion_uses_the_common_three_carrier_reader(tmp_path, monkeypatch, carrier):
    path, _ = package(tmp_path, monkeypatch, carrier=carrier)
    assert producer.check(path)[0] == []
    expected = model.derive_pairing_criterion(consumer.load(path, run_gate=False)["model"])
    assert expected and expected["kind"] == "min_person_days"
    out = get_card()
    assert out["criterion"] is not None
    assert out["criterion"]["source_rule"] == expected.get("source_rule")
    assert out["criterion"]["rates_version"] == expected.get("rates_version")
    assert (expected.get("label") or expected["source_rule"]) in visible_card(out)


def test_legacy_answer_target_still_refuses_modern_export(tmp_path, monkeypatch):
    with pytest.raises(tp.TaskPackageError) as refusal:
        package(tmp_path, monkeypatch, carrier="answer", legacy_answer_rule=True)
    assert refusal.value.code == "landing-version"
