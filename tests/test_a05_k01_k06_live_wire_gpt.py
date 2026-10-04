"""Explicit opt-in, owned TEST Kotlin wire probe; never starts a service.

Required A05_LIVE_OPT_IN=owned-test-only, URL=http://127.0.0.1:<port>=8130,
COMMIT (40 hex), SERVER_PID, SERVER_ATTESTATION, INPUT_ROOT (st-window-1),
CATALOG, COSMOS_BINDINGS, CDPM_BINDINGS, OUTPUT_DIRECTORY (empty absolute
directory), all prefixed A05_LIVE_. Bindings are the retained TEST release's
real inputs, never default workspace bindings or fabricated support.
The root-written server attestation has commit/pid/url/command/launched_at,
sources (all Kotlin main .kt, resources and pom relative to repository), and
artifacts (all target/quarkus-app files relative to repository). It binds the
owned launch to a build, not a self-reported HTTP server identity.

One sequential healthy native run, two rehashed carrier refusals, same-run
healthy reread. Builder evidence is a separate local component observation,
not invented HTTP output or proof of full Builder generation. This does not
by itself close C.I/C.IC: exact K01 lifecycle, K06 service/adopt/late and
migration profiles remain separate prerequisites. No full catalogue stand,
optimization, external service, release, main catalogue write or PROD use.

Do NOT use the all-network-denied offline runner: this explicit profile has
its own endpoint-only audit fence and no subprocess allowance.
"""
from __future__ import annotations

import base64
import copy
import ctypes
from datetime import datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
from urllib.parse import urlsplit
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
MEASURED = {"min_sit_home_min": 45, "min_sit_outstation_min": 45,
            "max_duty_span_min": 780, "min_rest_home_min": 660,
            "min_rest_outstation_min": 700, "base_tz_offset_min": 0}
EXTERNAL = "max_pairing_days"


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _census(folder, base=None):
    assert folder.is_dir(), folder
    base = folder if base is None else base
    return {p.relative_to(base).as_posix(): _sha(p.read_bytes())
            for p in sorted(folder.rglob("*")) if p.is_file()}


def _sources():
    out = {p.relative_to(ROOT).as_posix(): _sha(p.read_bytes())
           for p in sorted((ROOT / "kotlin-solver/src/main/kotlin").rglob("*.kt"))}
    out.update(_census(ROOT / "kotlin-solver/src/main/resources", ROOT))
    out["kotlin-solver/pom.xml"] = _sha((ROOT / "kotlin-solver/pom.xml").read_bytes())
    return out


def _python_sources():
    paths = [p for name in ("cdpm", "webapp", "vmp", "template_catalog")
             for p in (ROOT / name).rglob("*.py")]
    paths.extend(ROOT / name for name in ("_cos_rules.py", "_pairing_builder.py"))
    return {p.relative_to(ROOT).as_posix(): _sha(p.read_bytes()) for p in sorted(paths)}


def _write(folder, name, value):
    # Preserve even incomplete first results; primary observations are separate
    # files, never overwritten by the final summary.
    with (folder / name).open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)


def _listen_pid(port):
    """Read Windows IPv4 listener ownership without a shell or subprocess."""
    assert os.name == "nt", "This owned-listener check is Windows-specific"
    from ctypes import wintypes as w

    class Row(ctypes.Structure):
        _fields_ = [(name, w.DWORD) for name in (
            "state", "local_addr", "local_port", "remote_addr", "remote_port", "pid")]

    api = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
    api.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.DWORD), w.BOOL,
                    w.ULONG, w.ULONG, w.ULONG]
    api.restype = w.DWORD
    size = w.DWORD(0)
    assert api(None, ctypes.byref(size), False, socket.AF_INET, 3, 0) in (0, 122)
    buf = ctypes.create_string_buffer(size.value)
    assert api(buf, ctypes.byref(size), False, socket.AF_INET, 3, 0) == 0
    count = w.DWORD.from_buffer(buf).value
    rows = (Row * count).from_buffer(buf, ctypes.sizeof(w.DWORD))
    hits = [r for r in rows if r.state == 2
            and socket.ntohs(r.local_port & 0xffff) == port]
    assert len(hits) == 1, "Need exactly one owned IPv4 listener on the explicit port"
    row = hits[0]
    assert row.local_addr.to_bytes(4, "little") == socket.inet_aton("127.0.0.1"), \
        "Server must bind loopback only, not 0.0.0.0"
    return int(row.pid)


def _unzip(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert len(archive.namelist()) == len(set(archive.namelist()))
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files["manifest.json"])
    for item in manifest["files"]:
        assert item["sha256"] == _sha(files[item["name"]]), item["name"]
        if "bytes" in item:
            assert item["bytes"] == len(files[item["name"]]), item["name"]
    return files, manifest


def _sc2(raw, typed, legacy):
    """A labelled mutation, not a new export or a re-approved SMT report."""
    from cdpm import export_task_package as exporter
    files, manifest = _unzip(raw)
    original = dict(files)
    document = json.loads(files["model.json"])
    rules = [r for r in document["rules"]
             if EXTERNAL in (r.get("template_fit") or {}).get("params", {})]
    assert len(rules) == 1
    rule = rules[0]
    cell = rule["template_fit"]["params"][EXTERNAL]
    old, = [p for p in rule["params"] if p["name"] == EXTERNAL]
    assert cell["value"] == old["value"] == 7
    cell["value"], old["value"] = typed, legacy
    files["model.json"] = json.dumps(document, ensure_ascii=False).encode("utf-8")
    item, = [r for r in manifest["files"] if r["name"] == "model.json"]
    item["sha256"] = _sha(files["model.json"])
    if "bytes" in item:
        item["bytes"] = len(files["model.json"])
    files["manifest.json"] = json.dumps(manifest, ensure_ascii=False).encode("utf-8")
    assert {k for k in files if files[k] != original[k]} == {"model.json", "manifest.json"}
    body = exporter.write_zip(files)
    _unzip(body)  # Descriptor-valid; SMT subject is deliberately NOT re-approved.
    return body, rule["id"]


def _builder(pkg, members, snapshot):
    import _cos_rules
    from _pairing_builder import Builder
    from vmp import pairing_input
    from webapp import threshold_gate as tg

    values = {a["threshold"]: a["value"] for a in snapshot["applied"]}
    contract = pairing_input.source_rules_contract(
        json.loads(members["threshold-forms.json"]), values,
        manifest_time=pkg["manifest"].get("time"))
    raw = json.dumps(contract, ensure_ascii=False, sort_keys=True).encode("utf-8")
    rules = _cos_rules.load_bytes(raw, where="owned TEST native wire component", strict=True)
    assert rules.max_pairing_days == 7 and rules.base_tz_offset_min == 0
    builder = object.__new__(Builder)  # Predicate-only, no search or generation.
    builder.rules = rules
    outcomes = []
    start = datetime(2026, 10, 1, 6)
    for days in (7, 8):
        end = start + timedelta(days=days - 1, hours=1)
        chain = [{"t0": start, "t1": end, "work0": start, "work1": end}]
        got = builder.pairing_days_ok(chain, [])
        assert rules.pairing_days(start, end) == days and got is (days == 7)
        assert builder.pairing_span_ok(chain) is (days == 7)
        outcomes.append({"calendar_days": days, "allowed": got})
    evidence = {"schema": "pairing-builder-thresholds/1",
        "package_identity": tg.builder_package_identity(pkg), "contract_bytes": raw,
        "contract_sha256": _sha(raw), "source_keys_read": list(rules.source_keys_read),
        "readings": {EXTERNAL: {"value": rules.max_pairing_days, "unit": "count",
            "relation": "max", "semantics": "inclusive-local-calendar-days",
            "base_tz_offset_min": rules.base_tz_offset_min}}}
    serial = {**evidence, "contract_bytes": raw.decode("utf-8"),
              "scope": "local real contract reader and predicates; not JVM or full Builder run",
              "outcomes": outcomes}
    return evidence, serial


def _compare(pkg, run, builder):
    from webapp import threshold_gate as tg
    snap = run["thresholds"]
    return tg.compare(pkg, run["profileReadings"], mapping=snap["paramToThreshold"],
        qualified=snap["qualifiedParamToThreshold"], applied=snap["applied"],
        ignored=snap["ignored"], off_literals=snap["offLiterals"], builder_readings=builder)


def test_owned_native_wire_sc2_and_actual_threshold_consumers(tmp_path, monkeypatch):
    if os.environ.get("A05_LIVE_OPT_IN") != "owned-test-only":
        pytest.skip("Explicit owned TEST service opt-in is absent; no connection attempted")
    from cdpm import catalog_bridge as cb
    from cdpm.gates import gate_task_package as producer
    from vmp import catalog_access as ca, execution_manifest as em
    from vmp.run.button import SolverHttp
    from webapp import task_package as receiver, certify_input, threshold_gate as tg
    import httpx
    import template_catalog as tc

    def required(key):
        value = os.environ["A05_LIVE_" + key]
        assert value.strip(), key
        return value

    url, commit = required("URL"), required("COMMIT")
    parsed = urlsplit(url)
    assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
    assert parsed.username is None and parsed.password is None
    assert parsed.path in ("", "/") and not parsed.query and not parsed.fragment
    port = parsed.port
    assert port is not None and 8130 <= port <= 65535
    assert re.fullmatch(r"[0-9a-f]{40}", commit)
    pid = int(required("SERVER_PID"))
    assert pid > 0 and pid != os.getpid() and _listen_pid(port) == pid
    paths = {name: Path(required(name)) for name in (
        "INPUT_ROOT", "CATALOG", "SERVER_ATTESTATION", "OUTPUT_DIRECTORY")}
    assert all(p.is_absolute() for p in paths.values())
    folder, catalog, attpath, output = [paths[n].resolve() for n in paths]
    bindings = {name: Path(required(name + "_BINDINGS")).resolve(strict=True)
                for name in ("COSMOS", "CDPM")}
    assert folder.is_dir() and catalog.is_dir() and attpath.is_file()
    assert not output.exists() or (output.is_dir() and not list(output.iterdir()))
    assert not any(output == p or p in output.parents for p in (folder, catalog, ROOT))
    output.mkdir(parents=True, exist_ok=True)
    attestation = json.loads(attpath.read_bytes())
    assert attestation["commit"] == commit and attestation["pid"] == pid
    assert attestation["url"].rstrip("/") == url.rstrip("/")
    assert isinstance(attestation["command"], list) and attestation["command"]
    assert isinstance(attestation["launched_at"], str) and attestation["launched_at"]
    builddir = ROOT / "kotlin-solver/target/quarkus-app"
    assert attestation["sources"] == _sources()
    assert attestation["artifacts"] == _census(builddir, ROOT)

    def protected():
        return {"inputs": _census(folder), "catalog": _census(catalog),
                "sources": _sources(), "artifacts": _census(builddir, ROOT),
                "python_sources": _python_sources(),
                "raw_bindings": {k: _sha(v.read_bytes()) for k, v in bindings.items()},
                "attestation": _sha(attpath.read_bytes()), "test": _sha(Path(__file__).read_bytes())}

    before = protected()
    events, active = [], [True]

    def audit(event, args):
        if not active[0]:
            return
        if event == "socket.connect":
            address = args[1]
            assert address == ("127.0.0.1", port), (event, address)
            events.append({"event": event, "address": list(address)})
        elif event == "socket.getaddrinfo":
            assert args[0] == "127.0.0.1" and args[1] == port, (event, args)
        elif event in {"socket.bind", "socket.sendto", "subprocess.Popen", "os.system", "os.posix_spawn"}:
            raise AssertionError(f"Forbidden action in owned wire probe: {event}")

    sys.addaudithook(audit)
    client = None
    run_id = None
    observation = {"schema": "a05-k01-k06-owned-wire/1", "commit": commit,
        "scope": "one native TEST run plus local real consumers; not full C.I/C.IC acceptance",
        "server_pid": pid, "url": url, "protected_before": before,
        "remaining": ["exact K01 lifecycle and compatibility profile",
            "exact K06 service/support/adopt/late and migration profiles",
            "Builder predicates are not a full candidate-generation run"]}
    try:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(cb.ENV_CATALOG, str(catalog))
        monkeypatch.setenv("CREW_COSMOS_BINDINGS", str(bindings["COSMOS"]))
        monkeypatch.setenv("CDPM_BINDINGS_FILE", str(bindings["CDPM"]))
        monkeypatch.setenv("CREW_TASK_PACKAGE", "")
        cb.forget(); ca.forget(); tc.forget_current()
        identity = json.loads((folder / "identity.json").read_bytes())
        assert identity["task"] == "st-window-1"
        assert identity["files"] == {n: _sha((folder / n).read_bytes()) for n in (
            "st-window-1.zip", "st-window-1.problem.json", "st-window-1.world.json")}
        native = (folder / "st-window-1.zip").read_bytes()
        members, manifest = _unzip(native)
        assert manifest["spec_version"] == "1.20"
        assert manifest["allowed_modes"] == {"pairing": ["feasibility-hard"], "rostering": []}
        cat = tc.load(catalog)
        assert manifest["catalog"] == {"version": cat.version(), "fingerprint": cat.fingerprint()}
        assert identity["catalog_version"] == cat.version()
        assert identity["catalog_fingerprint"] == cat.fingerprint()
        observation["native_zip_sha256"] = _sha(native)
        observation["manifest_sha256"] = _sha(members["manifest.json"])
        observation["zip_members"] = {k: _sha(v) for k, v in members.items()}
        observation["catalog"] = manifest["catalog"]
        assert producer.check(folder / "st-window-1.zip")[0] == []
        pkg = receiver.load(folder / "st-window-1.zip", run_gate=False)
        receiver.check_support_execution(pkg, task="pairing", mode="feasibility-hard")
        with pytest.raises(receiver.TaskPackageRefused) as wrong_mode:
            receiver.check_support_execution(pkg, task="rostering", mode="optimization")
        observation["unsupported_mode_refusal"] = str(wrong_mode.value)
        payload = json.loads((folder / "st-window-1.problem.json").read_bytes())
        client = SolverHttp(url, timeout_s=5)
        client._c.close()
        client._c = httpx.Client(timeout=5, trust_env=False, follow_redirects=False)
        assert _listen_pid(port) == pid
        started = client.start(payload, base64.b64encode(native).decode("ascii"),
            "feasibility-hard", None, 1, task="pairing", deterministic={"seed": 0, "step_limit": 1})
        _write(output, "start.json", started)
        candidate_id = started.get("runId")
        assert isinstance(candidate_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", candidate_id)
        run_id = candidate_id
        assert started["packageFingerprint"] == _sha(members["manifest.json"])
        deadline = time.monotonic() + 30
        statuses = []
        while True:
            left = deadline - time.monotonic()
            assert left > 0, "Owned run did not finish within 30 seconds"
            client.set_timeout(min(5, left))
            status = client.status(run_id)
            statuses.append(status)
            if status.get("state") == "готов":
                break
            assert status.get("state") != "упал", status
            time.sleep(min(.2, max(0, deadline - time.monotonic())))
        _write(output, "statuses.json", statuses)
        client.set_timeout(5)
        actual = client.applied(run_id)
        solved = client.result(run_id)
        _write(output, "http-applied.json", actual)
        _write(output, "http-result.json", solved)
        assert actual["runId"] == run_id
        run = actual["lastRun"]
        assert run["packageFingerprint"] == _sha(members["manifest.json"])
        snapshot = run["thresholds"]
        assert {a["threshold"]: a["value"] for a in snapshot["applied"]} == {**MEASURED, EXTERNAL: 7}
        assert {k: run["profileReadings"][k] for k in MEASURED} == MEASURED
        assert EXTERNAL not in run["profileReadings"]
        preserved = copy.deepcopy((actual, solved))
        certifier, coverage = certify_input.build(pkg, payload, solved, actual,
                                                "owned A05/K01/K06 TEST wire")
        _write(output, "certify-input.json", {"input": certifier, "coverage": coverage})
        builder, serial_builder = _builder(pkg, members, snapshot)
        _write(output, "builder-local-component.json", serial_builder)
        rows = _compare(pkg, run, builder)
        assert rows and all(r["verdict"] == tg.MATCH for r in rows), rows
        assert not tg.unjudged_claimed_params(rows, snapshot["applied"])
        gaps = {t: em.thresholds_gaps(pkg["model"]["rules"],
            {a["threshold"]: a["value"] for a in snapshot["applied"]}, task=t)
            for t in ("pairing", "rostering")}
        assert gaps == {"pairing": [], "rostering": []}
        _write(output, "healthy-consumers.json", {"rows": rows, "gaps": gaps})
        red = []
        for name in MEASURED:
            changed = copy.deepcopy(run)
            changed["profileReadings"][name] += 1
            if name == "base_tz_offset_min":
                # Calendar evidence is bound to the actual JVM offset. A
                # provider-copy mutation invalidates that binding before
                # individual rows, rather than returning an ordinary row.
                with pytest.raises(tg.ThresholdGateError, match="calendar applied/provider binding missing or inconsistent") as refusal:
                    _compare(pkg, changed, builder)
                red.append({"copy_mutation": name, "refusal": str(refusal.value)})
                continue
            judged = _compare(pkg, changed, builder)
            assert any(r["reading"] == name and r["verdict"] == tg.DIVERGE for r in judged)
            red.append({"copy_mutation": name, "rows": judged})
        _write(output, "report-copy-refusals.json", red)
        for typed, legacy in ((3, 30), (30, 3)):
            changed, rule_id = _sc2(native, typed, legacy)
            path = tmp_path / f"sc2-{typed}-{legacy}.zip"
            path.write_bytes(changed)
            problems = producer.check(path)[0]
            assert any("param-carriers-diverge" in str(p) and rule_id in str(p) for p in problems), problems
            with pytest.raises(receiver.TaskPackageRefused) as rejected:
                receiver.load(path, run_gate=False)
            assert "param-carriers-diverge" in (str(getattr(rejected.value, "code", "")) + str(rejected.value))
            assert _listen_pid(port) == pid
            response = client._c.post(url.rstrip("/") + "/schedules/run",
                params={"task": "pairing", "mode": "feasibility-hard", "spentLimitSeconds": 1},
                json={"problem": payload, "packageZipBase64": base64.b64encode(changed).decode("ascii")})
            _write(output, f"sc2-{typed}-{legacy}.json", {"zip_sha256": _sha(changed),
                "producer": problems, "receiver": str(rejected.value),
                "http_status": response.status_code, "http_body": response.text,
                "note": "Descriptor-valid deliberate carrier conflict; no fabricated SMT approval"})
            assert response.status_code == 400 and "param-carriers-diverge" in response.text
            assert rule_id in response.text and EXTERNAL in response.text
            assert not re.search(r'"runId"\s*:', response.text), "Refusal must precede run registration"
        # One healthy run only: re-read it after both refused requests, proving
        # no mutation of that run's package/readings. No second solve is hidden.
        assert client.applied(run_id) == actual
        assert client.result(run_id) == solved
        assert (actual, solved) == preserved
        assert producer.check(folder / "st-window-1.zip")[0] == []
        receiver.check_support_execution(receiver.load(folder / "st-window-1.zip", run_gate=False),
                                         task="pairing", mode="feasibility-hard")
        observation["result"] = "scoped wire checks passed; not a full-ID acceptance"
    finally:
        try:
            if client is not None and run_id is not None:
                assert _listen_pid(port) == pid
                client.set_timeout(5)
                stopped = client._c.delete(url.rstrip("/") + "/schedules/run/" + run_id)
                _write(output, "cancel-owned-run.json", {"runId": run_id,
                    "status": stopped.status_code, "body": stopped.text})
                assert stopped.status_code == 200
        finally:
            if client is not None:
                client._c.close()
            active[0] = False
            cb.forget(); ca.forget(); tc.forget_current()
            observation["runId"] = run_id
            observation["network_events"] = events
            observation["protected_after"] = protected()
            _write(output, "summary.json", observation)
            assert observation["protected_after"] == before
