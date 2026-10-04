"""Offline regression of the real source-check roster branch, without app startup.

The AST seam executes the endpoint's unchanged branch (also on the parent
revision); HTTP is an injected double, not a server. No reader or solver runs.
"""
import ast
import gzip
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN = "3e49bed1-91f0-4277-9ab2-d9a9a2651cc3"
ATTEMPT = "20260921T152717-9d39bc"
COSMOS = "run-0021"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canon(doc):
    return json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")


def put(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")


def change(path, edit):
    doc = json.loads(path.read_text(encoding="utf-8"))
    edit(doc)
    put(path, doc)


@pytest.fixture
def saved(tmp_path):
    manifest = b'{"source_run":"run-0181","revision":3}'
    pkg = tmp_path / "package.zip"
    with zipfile.ZipFile(pkg, "w") as z:
        z.writestr("manifest.json", manifest)
    package_sha = sha(pkg.read_bytes())
    wd = tmp_path / package_sha
    rel = f"attempts/{ATTEMPT}/button-on-pairing"
    out = wd / rel
    out.mkdir(parents=True)
    (out / "package.zip").write_bytes(pkg.read_bytes())
    info = {"package_fingerprint": sha(manifest), "source_run": wd.name,
            "score": "0hard/-22soft"}
    solution = {"solution": f"кнопка {wd.name}/{RUN}", "runInfo": info,
                "assignments": [{"employee": "B01", "flight": "B42"}]}
    solved = {"flights": [{"flightNumber": "B42"}], "employees": [{"id": "B01"}],
              "flightAssignments": [{"employee": "B01", "flight": "B42"}]}
    applied = {"runId": RUN, "lastRun": {"packageFingerprint": sha(manifest),
               "profileReadings": {"day_break_threshold_min": 240},
               "thresholds": {"day_break_threshold_min": 240}}}
    delivery = {"certified": True, "certificate_gate": [],
                "result": {"feasibility": "confirmed", "cost": "partial"},
                "schedule": {"solver_run_id": RUN}}
    certificate = {"sources": [{"sid": "SOL", "file": solution["solution"],
                               "sha256": sha(canon(solution))}],
                   "certificate": {"certified": True,
                       "result": {"feasibility": "confirmed", "cost": "partial"}},
                   "run_trace": info}
    record = {"id": ATTEMPT, "task": "rostering", "done": True,
              "state": "завершено", "package_sha": package_sha, "run": COSMOS,
              "catalog": rel, "summary": {"solver_run_id": RUN,
                  "certified": True, "certificate_gate": 0},
              "journal": {"complete": True, "dir": rel + "/external-calls"}}
    for name, doc in {"delivery": delivery, "certificate": certificate,
                      "solution": solution, "solved": solved,
                      "applied-rules": applied,
                      "solver-status": {"runId": RUN, "state": "готов",
                                        "packageFingerprint": sha(manifest)}}.items():
        put(out / (name + ".json"), doc)
    put(wd / "state.json", {"package_sha": package_sha, "attempts": [record]})
    journal = out / "external-calls"
    put(journal / "manifest.json", {"schema": "cosmos-call-journal/v1",
                                    "task": "rostering", "complete": True})
    rows = []
    for n, method, body in [(1, "result", solved), (2, "applied", applied)]:
        raw = json.dumps({"response": body, "error": None}).encode()
        components = {"run_id": RUN}
        fingerprint = sha(json.dumps(components, sort_keys=True,
                                    separators=(",", ":")).encode())
        name = f"{n:03d}-solver.{method}.json" + (".gz" if n == 1 else "")
        (journal / name).write_bytes(gzip.compress(raw) if n == 1 else raw)
        rows.append({"kind": "solver", "method": method, "ok": True,
                     "input": {"components": components, "fingerprint": fingerprint},
                     "body": name, "body_sha256": sha(raw)})
    rows.append({"kind": "end", "complete": True, "count": 2})
    (journal / "index.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return SimpleNamespace(wd=wd, out=out, sha=package_sha, solved=solved,
                           applied=applied, journal=journal)


class HttpFailure(RuntimeError):
    pass


class Response:
    def __init__(self, status, doc=None):
        self.status_code, self.doc = status, doc

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HttpFailure(f"native HTTP {self.status_code}")

    def json(self):
        return self.doc


def branch(monkeypatch, saved, *, status=404, applied_status=200, app_path=None):
    """Execute only `if solver_run_id` from api_source_check_run._work."""
    source = (app_path or ROOT / "webapp/app.py").read_text(encoding="utf-8")
    endpoint = next(n for n in ast.parse(source).body
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == "api_source_check_run")
    work = next(n for n in ast.walk(endpoint)
                if isinstance(n, ast.FunctionDef) and n.name == "_work")
    node = next(n for n in ast.walk(work) if isinstance(n, ast.If)
                and isinstance(n.test, ast.Name) and n.test.id == "solver_run_id")
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return (Response(status, saved.solved) if url.endswith("/result")
                else Response(applied_status, saved.applied))

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(get=get))
    import webapp
    pr = SimpleNamespace(solver_url=lambda: "http://offline.invalid",
                         PackageRunError=RuntimeError)
    monkeypatch.setitem(sys.modules, "webapp.package_runs", pr)
    monkeypatch.setattr(webapp, "package_runs", pr, raising=False)
    from vmp import source_check_run as scr
    scope = {"__package__": "webapp", "solver_run_id": RUN, "solutions": [],
             "press_wd": saved.wd, "package_sha": saved.sha, "cosmos_run": COSMOS,
             "scr": scr, "HTTPException": RuntimeError, "roster_source": None}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(app_path or "app.py"),
                 "exec"), scope)
    return scope, calls


def test_native_healthy_needs_no_persisted_attempt(monkeypatch, saved):
    (saved.wd / "state.json").unlink()
    scope, calls = branch(monkeypatch, saved, status=200)
    assert scope["solutions"][0][1] == saved.solved
    assert len(calls) == 2


def test_native_missing_applied_stays_honestly_uncheckable(monkeypatch, saved):
    scope, calls = branch(monkeypatch, saved, status=200, applied_status=503)
    assert scope["solutions"][0][1] == saved.solved
    assert "503" in str(scope["solutions"][0][4])
    assert len(calls) == 2


def test_post_restart_404_uses_exact_certified_attempt(monkeypatch, saved):
    before = {p: p.read_bytes() for p in saved.wd.rglob("*") if p.is_file()}
    scope, calls = branch(monkeypatch, saved)
    assert scope["solutions"][0][1] == saved.solved
    source = scope["roster_source"]
    assert source["origin"] == "persisted-certified-attempt"
    assert source["attempt_id"] == ATTEMPT
    assert source["solver_run_id"] == RUN
    assert source["package_sha"] == saved.sha
    assert source["cosmos_run"] == COSMOS
    assert source["certificate_sha256"] == sha((saved.out / "certificate.json").read_bytes())
    assert scope["solutions"][0][4]["rest_boundary_min"] == 240
    assert len(calls) == 1  # no live applied query after missing live result
    assert before == {p: p.read_bytes() for p in saved.wd.rglob("*") if p.is_file()}


@pytest.mark.parametrize("status", [401, 403, 409, 500, 503])
def test_other_http_failures_do_not_use_disk(monkeypatch, saved, status):
    with pytest.raises(HttpFailure, match=str(status)):
        branch(monkeypatch, saved, status=status)


@pytest.mark.parametrize("case", [
    "package", "record-package", "cosmos", "uuid", "ambiguous", "unfinished",
    "path", "zip", "delivery-uuid", "uncertified", "gate", "certificate",
    "solution-hash", "solution-identity", "manifest", "applied-uuid",
    "applied-package", "status-uuid", "solved-tamper", "applied-tamper",
    "journal-missing", "journal-incomplete", "journal-hash", "journal-uuid",
    "journal-path", "current-only",
])
def test_recovery_refuses_unproven_or_mismatched_artifacts(monkeypatch, saved, case):
    state = saved.wd / "state.json"
    def rec(**kw):
        change(state, lambda d: d["attempts"][0].update(kw))
    if case == "package": change(state, lambda d: d.update(package_sha="0" * 64))
    elif case == "record-package": rec(package_sha="0" * 64)
    elif case == "cosmos": rec(run="run-H0")
    elif case == "uuid": rec(summary={"solver_run_id": "old-H0"})
    elif case == "ambiguous": change(state, lambda d: d["attempts"].append(d["attempts"][0]))
    elif case == "unfinished": rec(done=False)
    elif case == "path": rec(catalog="../../outside")
    elif case == "zip": (saved.out / "package.zip").write_bytes(b"wrong package")
    elif case == "delivery-uuid": change(saved.out / "delivery.json", lambda d: d["schedule"].update(solver_run_id="old-H0"))
    elif case == "uncertified": change(saved.out / "delivery.json", lambda d: d.update(certified=False))
    elif case == "gate": change(saved.out / "delivery.json", lambda d: d.update(certificate_gate=["blocked"]))
    elif case == "certificate": change(saved.out / "certificate.json", lambda d: d["certificate"].update(certified=False))
    elif case == "solution-hash": change(saved.out / "solution.json", lambda d: d.update(assignments=[]))
    elif case == "solution-identity": change(saved.out / "solution.json", lambda d: d.update(solution="old-H0"))
    elif case == "manifest": change(saved.out / "certificate.json", lambda d: d["run_trace"].update(package_fingerprint="0" * 64))
    elif case == "applied-uuid": change(saved.out / "applied-rules.json", lambda d: d.update(runId="old-H0"))
    elif case == "applied-package": change(saved.out / "applied-rules.json", lambda d: d["lastRun"].update(packageFingerprint="0" * 64))
    elif case == "status-uuid": change(saved.out / "solver-status.json", lambda d: d.update(runId="old-H0"))
    elif case == "solved-tamper": change(saved.out / "solved.json", lambda d: d.update(flightAssignments=[]))
    elif case == "applied-tamper": change(saved.out / "applied-rules.json", lambda d: d["lastRun"]["thresholds"].update(day_break_threshold_min=1))
    elif case == "journal-missing": (saved.journal / "index.jsonl").unlink()
    elif case == "journal-incomplete": change(saved.journal / "manifest.json", lambda d: d.update(complete=False))
    elif case in ("journal-hash", "journal-uuid", "journal-path"):
        p = saved.journal / "index.jsonl"
        rows = [json.loads(line) for line in p.read_text().splitlines()]
        if case == "journal-hash": rows[0]["body_sha256"] = "0" * 64
        elif case == "journal-uuid": rows[0]["input"]["components"]["run_id"] = "old-H0"
        else: rows[0]["body"] = "../../outside.json"
        p.write_text("\n".join(json.dumps(r) for r in rows))
    elif case == "current-only":
        # A mutable current delivery is not a registered immutable attempt.
        change(state, lambda d: d.update(attempts=[]))
    with pytest.raises(ValueError, match="сохранён"):
        branch(monkeypatch, saved)


def test_endpoint_persists_origin_in_existing_binding():
    source = (ROOT / "webapp/app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    endpoint = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "api_source_check_run")
    binding = next(n.value for n in ast.walk(endpoint) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "binding" for t in n.targets))
    provenance = {"origin": "persisted-certified-attempt", "roster_path": "exact/solved.json"}
    scope = {"package_sha": "x", "pkg": {}, "solver_run_id": RUN,
             "catalog_path": None, "roster_source": provenance,
             "cosmos_run": COSMOS, "workdir_rel": "owned"}
    value = eval(compile(ast.Expression(body=binding), "app.py", "eval"), scope)
    assert value["roster_source"] == provenance
    assert value["roster_path"] == "exact/solved.json"


def test_timeout_does_not_trigger_recovery(saved):
    from webapp.source_check_result import roster_result
    def offline_timeout(*args, **kwargs):
        raise TimeoutError("native timed out")
    with pytest.raises(TimeoutError):
        roster_result(get=offline_timeout, base="offline", wd=saved.wd,
                      package_sha=saved.sha, cosmos_run=COSMOS, solver_run_id=RUN)


def test_unrelated_current_delivery_is_never_selected(monkeypatch, saved):
    put(saved.wd / "button" / "delivery.json", {"certified": True,
                                               "schedule": {"solver_run_id": "H0"}})
    put(saved.wd / "button" / "solved.json", {"flightAssignments": ["wrong H0"]})
    scope, _ = branch(monkeypatch, saved)
    assert scope["solutions"][0][1] == saved.solved
    assert scope["roster_source"]["attempt_id"] == ATTEMPT
