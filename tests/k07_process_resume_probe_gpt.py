"""Two explicitly invoked local processes, real request persistence, no network.

Usage: python -B tests/k07_process_resume_probe_gpt.py first|resume <empty-dir>
Only the supplied isolated directory is written. No pytest, server, keys,
product startup, subprocesses or claim of a catalogue release.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]


def forbidden(event, args):
    if event.startswith("socket.") or event in {"subprocess.Popen", "os.system", "os.posix_spawn"} or event.startswith("os.spawn"):
        raise RuntimeError("Forbidden external action: " + event)


sys.addaudithook(forbidden)

import io
import json
import os
import shutil
from urllib.error import URLError

from starlette.requests import Request
from catalog_service.app import create_app
from catalog_service.settings import Settings
from cdpm import catalog_requests as client
from template_catalog import requests as law


class Response:
    def __init__(self, response):
        self.status = response.status_code
        self.headers = {"Content-Type": response.headers["content-type"]}
        self.body = response.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


def main(phase, target):
    target = Path(target).resolve()
    assert phase in {"first", "resume"}
    if phase == "first":
        assert not target.exists(), "Never overwrite an existing probe"
        target.mkdir(parents=True)
        shutil.copytree(ROOT / "catalog-integration/0.7.0-draft.3", target / "catalog")
    else:
        assert (target / "first.json").is_file()
    token = "local-process-resume-fixture-token"
    os.environ[client.ENV_SERVICE_URL] = "http://process-resume.invalid"
    os.environ[client.ENV_SERVICE_TOKEN] = token
    app = create_app(Settings(catalog_dir=target / "catalog", work_dir=target / "work",
                              actor="analyst", machine_token=token,
                              markers_file=target / "absent-test-markers"))
    route, = [r for r in app.routes if getattr(r, "path", None) == "/api/requests"
              and "POST" in (getattr(r, "methods", None) or set())]
    outbox, run = target / "outbox", target / "run"
    if phase == "first":
        outbox.mkdir()
        cat = app.state.store.catalog()
        kind = cat.dictionaries()["executor_kinds"][0]
        key = law.kind_request_key("families", "duty_limits", [kind])
        sent = {"request_id": law.request_id_of(key), "state": "подана",
                "section": "families", "record_id": "duty_limits",
                "submitted_at": "2026-09-19T20:45:00+00:00", "submitted_by": "CDPM",
                "source": {"kind": "вручную", "document_class": None, "run_class": None, "ref": None},
                "draft": None, "executor_kinds_requested": [kind],
                "decision": {"by": None, "at": None, "words": None}, "cr_key": None}
        queued = outbox / (sent["request_id"] + ".json")
        queued.write_text(json.dumps(sent, ensure_ascii=False), encoding="utf-8")
    else:
        queued, = list(outbox.glob("req-*.json"))
        sent = json.loads(queued.read_text(encoding="utf-8"))
    original = queued.read_bytes()
    prior = client.read_receipt(run, sent["request_id"])
    journal_before = app.state.journal.read()
    calls = []

    def transport(req, *, timeout):
        assert req.full_url == "http://process-resume.invalid/api/requests"
        assert json.loads(req.data) == sent and timeout > 0
        request = Request({"type": "http", "method": "POST", "path": "/api/requests",
                           "headers": [(k.lower().encode(), v.encode()) for k, v in req.header_items()], "app": app})
        response = route.endpoint(json.loads(req.data), request)
        calls.append(response.status_code)
        if phase == "first":
            raise URLError("local simulated loss after real persistence")
        return Response(response)

    result = client.deliver_one(sent, run, outbox, _urlopen=transport, _sleep=lambda _: None)
    rows = app.state.journal.read()
    assert len(rows) == 1 and rows[0]["op"] == "request-submit"
    requests = app.state.store.catalog().requests()
    assert len([r for r in requests if r["request_id"] == sent["request_id"]]) == 1
    if phase == "first":
        assert prior is None and not journal_before
        assert calls == [201] + [200] * (len(client.DELIVERY_PAUSES_S) - 1)
        assert result["outcome"] == "неизвестен" and result["receipt"] is None
        assert queued.read_bytes() == original and result["queue"] == "outbox"
    else:
        first = json.loads((target / "first.json").read_text(encoding="utf-8"))
        assert first["pid"] != os.getpid(), "Must actually be another process"
        assert prior["outcome"] == "неизвестен" and journal_before == rows
        assert calls == [200] and result["outcome"] == "доставлена"
        assert result["receipt"]["repeat"] is True
        assert result["receipt"]["decision"]["version"] == 0
        assert len(result["attempts"]) == len(prior["attempts"]) + 1
        assert not queued.exists() and (outbox / "sent" / queued.name).read_bytes() == original
    assert token not in json.dumps(result, ensure_ascii=False)
    record = {"phase": phase, "pid": os.getpid(), "statuses": calls,
              "attempts": len(result["attempts"]), "outcome": result["outcome"],
              "request_id": sent["request_id"], "journal_rows": len(rows)}
    (target / (phase + ".json")).write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record))


if __name__ == "__main__":
    assert len(sys.argv) == 3
    main(sys.argv[1], sys.argv[2])
