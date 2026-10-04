"""A08 SC-3 on a retained, genuinely released, native TEST package.

Required environment: A08_RELEASE_INPUT_ROOT (the existing eight-directions
evidence directory) and A08_RELEASE_SOURCE_COMMIT (its full producer commit).
No release/export rerun, fabricated support, solver, service or network. Only
the HTTP transport is in memory and threadpool dispatch is inline. The real
producer gate, receiver, registry derivation and registered handler run.
The catalogue is the retained postrelease working deployment, checked against
the immutable release body. This is TEST acceptance, not a launch permission,
full stand, production deployment or expert approval.

Run with the reviewed run_k01_offline_20260915.py, without a translator stub.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace
import zipfile

import pytest
from starlette.requests import Request

import template_catalog as tc
from cdpm import catalog_bridge as cb, export_task_package as exporter, model
from cdpm.gates import gate_task_package as producer
from vmp import catalog_access as ca, catalog_requests, objective_projection as op
from webapp import task_package as receiver


TOKEN = "isolated-native-release-sc3-token"
ROUTE = "/api/task-package/receive"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _census(folder):
    assert folder.is_dir(), folder
    return {p.relative_to(folder).as_posix(): _sha(p)
            for p in sorted(folder.rglob("*")) if p.is_file()}


def _dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
                    encoding="utf-8")


def _forget():
    tc.forget_current()
    ca.forget()
    cb.forget()


@pytest.fixture
def released(tmp_path, monkeypatch, record_property):
    raw_root = os.environ.get("A08_RELEASE_INPUT_ROOT", "")
    commit = os.environ.get("A08_RELEASE_SOURCE_COMMIT", "")
    assert raw_root and Path(raw_root).is_absolute(), "Explicit absolute retained input root required"
    assert re.fullmatch(r"[0-9a-f]{40}", commit), "Explicit full producer commit required"
    root = Path(raw_root).resolve(strict=True)
    original = root / "repo/catalog"
    working = root / "postrelease-working/catalog"
    inputs = root / "direction-inputs/total_pairing_cost"
    bindings = {"COSMOS": root / "repo/cosmos-bindings.json",
                "CDPM": root / "repo/cdpm-bindings.json"}
    primary = [root / name for name in (
        "actual-test-act.json", "actual-prechecks.json", "actual-pure.json",
        "actual-tables.json", "summary.json", "direction-summary.json",
        "postrelease/summary.json")]

    def protected():
        return {"release": _census(original), "working_deployment": _census(working),
                "inputs": _census(inputs),
                "raw_bindings": {k: _sha(v) for k, v in bindings.items()},
                "primary": {p.relative_to(root).as_posix(): _sha(p) for p in primary}}

    before = protected()
    evidence = {"schema": "a08-native-release-sc3/1", "source_commit": commit,
                "source_root": str(root), "test_source_sha256": _sha(__file__),
                "scope": "in-process producer/receiver/registered handler; TEST only, no launch",
                "protected_before": before, "observations": []}
    _dump(tmp_path / "sc3-evidence.json", evidence)
    try:
        identity = json.loads((inputs / "identity.json").read_bytes())
        assert identity["schema"] == "a06-native-direction-input/1"
        assert identity["source_commit"] == commit
        assert identity["metric"] == "total_pairing_cost" and identity["task"] == "st-tpc-1"
        assert set(identity["files"]) == {"task.zip", "world.json", "problem.json"}
        assert identity["files"] == {name: _sha(inputs / name) for name in identity["files"]}
        for name in ("summary.json", "direction-summary.json"):
            assert json.loads((root / name).read_bytes())["source_commit"] == commit
        post = json.loads((root / "postrelease/summary.json").read_bytes())
        assert post["ok"] is True and post["parity"]["counts"]["red"] == 0
        source_cat, work_cat = tc.load(original), tc.load(working)
        assert tc.canonical_bytes(source_cat.body()) == tc.canonical_bytes(work_cat.body())
        stamp = {"version": source_cat.version(), "fingerprint": source_cat.fingerprint()}
        assert stamp == identity["catalog"] and stamp["version"] == "0.7.0"
        assert (working / "support.json").read_bytes() == (original / "support.json").read_bytes()
        evidence["catalog"] = stamp
        evidence["postrelease_parity"] = post["parity"]  # Skips/warnings remain visible.

        for key, value in {
            "CREW_TEMPLATE_CATALOG": working,
            "CREW_COSMOS_BINDINGS": bindings["COSMOS"],
            "CDPM_BINDINGS_FILE": bindings["CDPM"],
            "CREW_DATA_DIR": tmp_path / "data", "CREW_TASK_INBOX": tmp_path / "inbox",
            "CREW_VMP_REGISTRY": tmp_path / "registry.json", "CREW_COSMOS_RUNS_DIR": "",
            "CREW_TASK_PACKAGE": "", "CREW_CHANNEL_TOKEN": TOKEN,
            catalog_requests.ENV_AUTO: "0", catalog_requests.ENV_OUTBOX: tmp_path / "queue",
        }.items():
            monkeypatch.setenv(key, str(value))
        monkeypatch.delenv(catalog_requests.ENV_API_URL, raising=False)
        monkeypatch.delenv(catalog_requests.ENV_TOKEN, raising=False)
        _forget()
        assert {"version": ca.current().version(), "fingerprint": ca.current().fingerprint()} == stamp
        assert {"version": cb.catalog().version(), "fingerprint": cb.catalog().fingerprint()} == stamp

        from webapp import app as application, task_channel
        import starlette.concurrency
        dispatched = []

        async def inline(fn, *args, **kwargs):
            assert fn is task_channel.receive
            dispatched.append(True)
            return fn(*args, **kwargs)

        monkeypatch.setattr(starlette.concurrency, "run_in_threadpool", inline)
        routes = [r for r in application.app.routes if getattr(r, "path", None) == ROUTE
                  and "POST" in (getattr(r, "methods", None) or set())]
        assert len(routes) == 1 and routes[0].endpoint is application.api_task_package_receive
        record_property("a08_source_commit", commit)
        record_property("a08_primary_evidence", str(tmp_path / "sc3-evidence.json"))
        yield SimpleNamespace(path=inputs / "task.zip", cat=ca.current(), evidence=evidence,
                              app=application, channel=task_channel, dispatched=dispatched)
    finally:
        _forget()
        after = protected()
        evidence["protected_after"] = after
        evidence["source_bytes_unchanged"] = before == after
        _dump(tmp_path / "sc3-evidence.json", evidence)
        assert before == after, "Original release, bindings, native inputs or primary evidence changed"


def _request(context, body, manifest):
    headers = {"content-type": "application/zip", "content-length": str(len(body)),
               "x-package-sha256": hashlib.sha256(body).hexdigest(),
               "x-spec-version": manifest["spec_version"],
               "x-source-run": manifest["source_run"], "x-channel-token": TOKEN}
    reads = []

    async def receive_body():
        assert not reads
        reads.append(True)
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request({"type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": ROUTE, "raw_path": ROUTE.encode(), "query_string": b"",
        "headers": [(k.encode(), str(v).encode()) for k, v in headers.items()],
        "server": ("offline.invalid", 80), "client": ("offline", 1), "app": context.app.app},
        receive=receive_body)
    coroutine = context.app.api_task_package_receive(request)
    try:
        try:
            suspended = coroutine.send(None)
        except StopIteration as completed:
            response = completed.value
        else:
            pytest.fail("Unexpected asynchronous dependency: " + repr(suspended))
    finally:
        coroutine.close()
    assert reads == [True]
    return response.status_code, json.loads(response.body)


def _mutant(body, mutation):
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    original = dict(files)
    objective = json.loads(files["objective.json"])
    pairing = objective["pairing"]
    assert len(pairing["components"]) == 1
    assert pairing["components"][0]["rule_id"] == "R-S"
    assert pairing["components"][0]["weight"] == 1
    assert pairing["criterion"]["rate_per_duty"] == 7
    if mutation == "weight":
        pairing["components"][0]["weight"] = 2
    elif mutation == "rate":
        pairing["criterion"]["rate_per_duty"] = 8
    else:
        assert mutation == "component"
        pairing["components"] = []
    files["objective.json"] = op.canonical_bytes(objective)
    manifest = json.loads(files["manifest.json"])
    entries = [row for row in manifest["files"] if row["name"] == "objective.json"]
    assert len(entries) == 1
    entries[0]["sha256"] = hashlib.sha256(files["objective.json"]).hexdigest()
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    assert {k for k in files if files[k] != original[k]} == {"objective.json", "manifest.json"}
    return exporter.write_zip(files), objective


@pytest.mark.parametrize("mutation,where,expected,got", [
    ("weight", "pairing.components[R-S].weight", 1, 2),
    ("rate", "pairing.criterion.rate_per_duty", 7, 8),
    ("component", "pairing.components[R-S]", 1, None),
])
def test_native_released_sc3_preserves_full_refusal_and_accepted_state(
        released, tmp_path, mutation, where, expected, got):
    context = released
    body = context.path.read_bytes()
    native_sha = hashlib.sha256(body).hexdigest()
    assert producer.check(context.path)[0] == []
    package = receiver.load(context.path, run_gate=False)
    assert package["objective_projection"]["judged"] is True
    assert package["objective_projection"]["problems"] == []
    assert package["manifest"]["catalog"] == context.evidence["catalog"]
    compare_context = {"criterion": model.derive_pairing_criterion(package["model"])}
    assert op.compare(package["objective"], package["model"], context.cat, context=compare_context) == []

    first_stored = None
    for repeat in (False, True):
        status, receipt = _request(context, body, package["manifest"])
        context.evidence["observations"].append({"stage": "healthy-repeat" if repeat else "healthy",
                                                  "status": status, "receipt": receipt})
        assert status == 200 and receipt["verdict"] in {"OK", "MISMATCH"}, receipt
        assert receipt["sha"] == native_sha and receipt["already_accepted"] is repeat
        assert receipt["spec_version"] == package["manifest"]["spec_version"]
        assert receipt["source_run"] == package["manifest"]["source_run"]
        assert receipt["cosmos_run"] is None and receipt["run_note_code"] == "storage-off"
        assert set(receipt["mg"]) == {"pairing", "rostering"}
        assert "вывод реестра исполнителей не удался" not in receipt.get("detail", ""), receipt
        stored = _census(context.channel.inbox_dir() / native_sha)
        if repeat:
            assert stored == first_stored
        else:
            first_stored = stored
    inbox = context.channel.inbox_dir()
    accepted = inbox / native_sha
    accepted_before = _census(accepted)
    assert (accepted / context.channel.PACKAGE_NAME).read_bytes() == body
    current_before = (inbox / context.channel.CURRENT_NAME).read_bytes()
    assert json.loads(current_before)["repeat"] is True
    registry = context.channel.registry_path()
    assert registry.is_file(), "Healthy control must derive a real registry"
    registry_before = registry.read_bytes()

    changed, changed_objective = _mutant(body, mutation)
    changed_path = tmp_path / (mutation + ".zip")
    changed_path.write_bytes(changed)
    problems = op.compare(changed_objective, package["model"], context.cat, context=compare_context)
    assert len(problems) == 1, problems
    assert problems[0]["code"] == "objective-mismatch" and problems[0]["where"] == where
    assert problems[0]["expected"] == expected and problems[0]["got"] == got
    gate_problems = producer.check(changed_path)[0]
    # Equality disallows an unrelated refusal (including an invalid rehash or
    # stale support) being substituted for the intended semantic mechanism.
    assert gate_problems == [f"{p['code']}: {p['words']}" for p in problems]
    with pytest.raises(receiver.TaskPackageRefused) as refused:
        receiver.load(changed_path, run_gate=False)
    assert refused.value.code == "objective-mismatch"
    assert refused.value.problems == problems
    assert str(refused.value) == op.problem_words(problems)
    status, receipt = _request(context, changed, package["manifest"])
    context.evidence["observations"].append({"stage": mutation, "status": status,
        "mutant_sha256": _sha(changed_path), "projection_problems": problems,
        "producer_problems": gate_problems, "receiver_words": str(refused.value), "receipt": receipt})
    assert status == 422 and receipt["verdict"] == "REFUSED", receipt
    assert receipt["detail"] == str(refused.value)
    assert receipt["sha"] == _sha(changed_path)
    assert not (inbox / receipt["sha"]).exists() and not list(inbox.glob("incoming-*.zip"))
    assert _census(accepted) == accepted_before
    assert (inbox / context.channel.CURRENT_NAME).read_bytes() == current_before
    assert registry.read_bytes() == registry_before
    rows = [json.loads(line) for line in (inbox / context.channel.RECEIPTS_NAME)
            .read_text(encoding="utf-8").splitlines()]
    assert [(r["sha"], r["repeat"]) for r in rows] == [
        (native_sha, False), (native_sha, True), (receipt["sha"], False)]
    assert rows[-1]["verdict"] == "REFUSED" and len(context.dispatched) == 3
    context.evidence["accepted_state_unchanged"] = {
        "package_files": accepted_before, "current_sha256": hashlib.sha256(current_before).hexdigest(),
        "registry_sha256": hashlib.sha256(registry_before).hexdigest()}
