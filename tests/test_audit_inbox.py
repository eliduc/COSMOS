"""Audit inbox: file-name contract, scan/registry lifecycle, LLM analysis
to CRs (agent "audit"), tamper protection, API tenancy, poller gating."""
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from webapp import audit_inbox as ai
from webapp import change_requests as crq
from _stand_identity import ADMIN_EMAIL

ADMIN = ADMIN_EMAIL
USER = "someone@example.com"

GOOD = "14_30_16_07_2026_audit.md"


@pytest.fixture()
def inbox(tmp_path, monkeypatch):
    d = tmp_path / "audits"
    d.mkdir()
    monkeypatch.setenv("CREW_AUDITS_DIR", str(d))
    monkeypatch.setattr(ai, "REGISTRY_PATH", tmp_path / "audit_reports.json")
    monkeypatch.setattr(crq, "CR_PATH", tmp_path / "crs.json")
    yield d


def _reply(text):
    def fake(prompt, *, backend, timeout_s=0, system=None, cwd=None,
             usage=None, model=None):
        return {"ok": True, "text": text, "backend": backend, "model": "m"}
    return fake


ANALYSIS = json.dumps({
    "summary": "Аудит серьёзный: два бага и одно предложение.",
    "findings": [
        {"kind": "bug", "title": "Гонка в сохранении",
         "detail": "Подробности бага.", "severity": 5, "effort": 3},
        {"kind": "suggestion", "title": "Добавить экспорт",
         "detail": "Подробности предложения.", "severity": 2, "effort": 6},
        {"kind": "nonsense", "title": "Неизвестный вид",
         "detail": "Должен стать замечанием.", "severity": 99,
         "effort": -1},
    ],
})


# ------------------------------------------------------------ name contract

def test_parse_fname_contract():
    assert ai.parse_fname(GOOD) == "2026-07-16T14:30"
    for bad in ("audit.md", "14_30_16_07_2026_audit.txt",
                "25_30_16_07_2026_audit.md",     # 25 hours
                "14_61_16_07_2026_audit.md",     # 61 minutes
                "14_30_32_07_2026_audit.md",     # day 32
                "14_30_16_13_2026_audit.md",     # month 13
                "14_30_16_07_26_audit.md",       # 2-digit year
                "", None):
        assert ai.parse_fname(bad) is None


# --------------------------------------------------------------------- scan

def test_scan_registers_and_flags(inbox):
    (inbox / GOOD).write_text("# audit", encoding="utf-8")
    (inbox / "notes.md").write_text("junk", encoding="utf-8")
    res = ai.scan()
    assert res["new"] == [GOOD]
    assert "notes.md" in res["ignored"]
    rec = next(f for f in res["files"] if f["file"] == GOOD)
    assert rec["status"] == "new" and rec["audited_at"] == "2026-07-16T14:30"
    # idempotent: the second scan registers nothing new
    assert ai.scan()["new"] == []
    # a vanished file is flagged, not dropped
    (inbox / GOOD).unlink()
    rec = next(f for f in ai.scan()["files"] if f["file"] == GOOD)
    assert rec["missing"] is True


def test_scan_missing_dir_is_empty_inbox(inbox, monkeypatch):
    monkeypatch.setenv("CREW_AUDITS_DIR", str(inbox / "nope"))
    res = ai.scan()
    assert res["new"] == [] and res["ignored"] == []


# ------------------------------------------------------------------ process

def test_process_creates_audit_crs(inbox, monkeypatch):
    (inbox / GOOD).write_text("# audit\nfindings...", encoding="utf-8")
    ai.scan()
    monkeypatch.setattr(crq.llm_backend, "complete", _reply(ANALYSIS))
    rec = ai.process(GOOD, ADMIN)
    assert rec["status"] == "processed" and rec["processed_by"] == ADMIN
    assert len(rec["cr_ids"]) == 3
    a = rec["analysis"]
    assert a["summary"].startswith("Аудит серьёзный")
    kinds = [f["kind"] for f in a["findings"]]
    assert kinds == ["bug", "suggestion", "remark"]   # nonsense -> remark
    assert [f["severity"] for f in a["findings"]] == [5, 2, 5]  # 99 -> 5
    assert [f["effort"] for f in a["findings"]] == [3, 6, 1]    # -1 -> 1
    crs = crq.list_crs()
    assert len(crs) == 3
    assert all(c["agent"] == "audit" and c["status"] == "draft"
               and c["source"] == GOOD for c in crs)
    bug = next(c for c in crs if "[bug]" in c["title"])
    assert bug["priority"] == 5 and GOOD in bug["summary"]
    # double processing is refused
    with pytest.raises(ai.AuditInboxError, match="already processed"):
        ai.process(GOOD, ADMIN)


def test_process_guards(inbox, monkeypatch):
    (inbox / GOOD).write_text("# audit", encoding="utf-8")
    with pytest.raises(ai.AuditInboxError, match="not registered"):
        ai.process(GOOD, ADMIN)                     # scan first
    ai.scan()
    # tamper: content changed after detection
    (inbox / GOOD).write_text("# audit CHANGED", encoding="utf-8")
    with pytest.raises(ai.AuditInboxError, match="changed since detection"):
        ai.process(GOOD, ADMIN)
    # Повторная проверка ПРИНИМАЕТ свежую версию ещё не обработанного
    # отчёта (внешний аудит R7, P1-18б): прежний хеш первой находки
    # запирал обработку навсегда. Принятие явное — с пометкой в реестре;
    # tamper-защита обработанных и захваченных записей не ослабляется.
    ai.scan()
    rec = ai._load()["files"][GOOD]
    assert rec["changed_before_processing"] is True
    import hashlib as _h
    assert rec["sha256"] == _h.sha256(b"# audit CHANGED").hexdigest()
    assert rec["status"] == "new"


def test_process_llm_failures(inbox, monkeypatch):
    (inbox / GOOD).write_text("# audit", encoding="utf-8")
    ai.scan()
    monkeypatch.setattr(crq.llm_backend, "complete",
                        lambda *a, **k: {"ok": False, "error": "boom"})
    with pytest.raises(ai.AuditInboxError, match="LLM failed"):
        ai.process(GOOD, ADMIN)
    monkeypatch.setattr(crq.llm_backend, "complete", _reply("no json here"))
    with pytest.raises(ai.AuditInboxError, match="no JSON object"):
        ai.process(GOOD, ADMIN)
    monkeypatch.setattr(ai, "MAX_AUDIT_BYTES", 3)
    with pytest.raises(ai.AuditInboxError, match="too large"):
        ai.process(GOOD, ADMIN)


def test_symlink_is_rejected(inbox, tmp_path):
    """A symlink named like an audit must never be read (arbitrary-file
    read + exfiltration guard)."""
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    link = inbox / GOOD
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not creatable in this environment")
    res = ai.scan()
    assert res["new"] == [] and GOOD in res["ignored"]


def test_json_extraction_tolerates_trailing_prose():
    obj = ai._extract_json_object(
        'Here is the analysis:\n{"summary": "ok", "findings": []}\n'
        'Note: the {1-5} scale applies. Hope this helps!}')
    assert obj == {"summary": "ok", "findings": []}
    assert ai._extract_json_object("no json at all") is None


def test_concurrent_process_claim_blocks_second(inbox, monkeypatch):
    """The processing claim stops a second call from re-running the LLM
    and duplicating CRs (double-click / two tabs)."""
    (inbox / GOOD).write_text("# audit", encoding="utf-8")
    ai.scan()
    # simulate: first call has claimed the slot but not finished
    claimed = ai._claim_for_processing(GOOD, ADMIN)
    assert claimed["status"] == "processing"
    monkeypatch.setattr(crq.llm_backend, "complete", _reply(ANALYSIS))
    with pytest.raises(ai.AuditInboxError, match="already being processed"):
        ai.process(GOOD, ADMIN)
    # a stale claim (older than the TTL) may be retaken
    monkeypatch.setattr(ai, "PROCESSING_TTL_S", -1)
    rec = ai.process(GOOD, ADMIN)
    assert rec["status"] == "processed" and len(rec["cr_ids"]) == 3


def test_process_releases_claim_on_llm_failure(inbox, monkeypatch):
    """A failure BEFORE any CR is created returns the file to 'new' so it
    can be retried."""
    (inbox / GOOD).write_text("# audit", encoding="utf-8")
    ai.scan()
    monkeypatch.setattr(crq.llm_backend, "complete",
                        lambda *a, **k: {"ok": False, "error": "boom"})
    with pytest.raises(ai.AuditInboxError, match="LLM failed"):
        ai.process(GOOD, ADMIN)
    rec = next(f for f in ai.scan()["files"] if f["file"] == GOOD)
    assert rec["status"] == "new"           # slot released, retriable
    assert crq.list_crs() == []             # no CRs created


def test_processed_file_change_is_flagged(inbox, monkeypatch):
    (inbox / GOOD).write_text("# audit", encoding="utf-8")
    ai.scan()
    monkeypatch.setattr(crq.llm_backend, "complete", _reply(ANALYSIS))
    ai.process(GOOD, ADMIN)
    (inbox / GOOD).write_text("# audit TAMPERED", encoding="utf-8")
    rec = next(f for f in ai.scan()["files"] if f["file"] == GOOD)
    assert rec["modified_after_processing"] is True
    assert rec["status"] == "processed"             # never silently re-read


# ------------------------------------------------------------------- poller

def test_poller_gated_off_by_default(monkeypatch):
    monkeypatch.delenv("CREW_AUDIT_POLL", raising=False)
    assert ai.start_poller_thread() is False


def test_notify_audit_found_without_config(capsys, monkeypatch):
    from webapp import telegram_bot as tg
    monkeypatch.delenv("CREW_TG_BOT_TOKEN", raising=False)
    monkeypatch.delenv("CREW_TG_CHAT_ID", raising=False)
    tg.notify_audit_found([GOOD])                   # must not raise
    assert "not configured" in capsys.readouterr().out


# ---------------------------------------------------------------------- API

@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CREW_AUTH_MODE", "dev")
    monkeypatch.setenv("CREW_DEV_USER", ADMIN)
    d = tmp_path / "audits"
    d.mkdir()
    monkeypatch.setenv("CREW_AUDITS_DIR", str(d))
    from webapp import users
    monkeypatch.setattr(users, "USERS_DB_PATH", tmp_path / "users.db")
    monkeypatch.setattr(ai, "REGISTRY_PATH", tmp_path / "audit_reports.json")
    monkeypatch.setattr(crq, "CR_PATH", tmp_path / "crs.json")
    users.ensure_db()
    users.add(USER, "user", by=ADMIN)
    from webapp import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        yield c, d


def test_api_flow_and_admin_gate(client, monkeypatch):
    c, d = client
    (d / GOOD).write_text("# audit", encoding="utf-8")
    res = c.get("/api/audits").json()
    assert res["new"] == [GOOD]
    monkeypatch.setattr(crq.llm_backend, "complete", _reply(ANALYSIS))
    rec = c.post(f"/api/audits/{GOOD}/process").json()
    assert rec["status"] == "processed" and len(rec["cr_ids"]) == 3
    assert c.post(f"/api/audits/{GOOD}/process").status_code == 422
    # the audit CRs ride the ordinary queue endpoint
    items = c.get("/api/cr").json()["items"]
    assert sum(1 for r in items if r["agent"] == "audit") == 3
    # non-admin: 403 on both endpoints
    monkeypatch.setenv("CREW_DEV_USER", USER)
    assert c.get("/api/audits").status_code == 403
    assert c.post(f"/api/audits/{GOOD}/process").status_code == 403
