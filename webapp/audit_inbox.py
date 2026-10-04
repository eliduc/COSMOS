"""Audit inbox (2026-07-16): third-party audit reports as a CR source.

An external auditor periodically drops a Markdown report into a watched
directory (legion: C:\\Users\\lev\\cosmos-audits, overridable via
CREW_AUDITS_DIR). File name contract: hh_mm_dd_mm_yyyy_audit.md
(hours_minutes_day_month_year, e.g. 14_30_16_07_2026_audit.md) — anything
else in the directory is ignored and listed as such.

Lifecycle:

  - DETECTION: a 10-minute poller (Test contour only, gated by
    CREW_AUDIT_POLL=1) plus the admin's "check now" button both call
    scan(): every valid file is registered in webapp/config/
    audit_reports.json (gitignored) with its SHA-256 and status "new".
    The auditor's file itself is NEVER touched — no rename, no marker.
    New files are announced once via Telegram (flag `notified`).
  - ANALYSIS: the admin's "process" button reads the report (size-capped)
    and asks the LLM — cloud on the Test contour, forced local on
    prod/stage via change_requests._agent_backend, same fail-closed rule
    as every other agent — for a structured breakdown: an executive
    summary plus findings classified bug / flaw / remark / suggestion
    with severity and effort.
  - IMPLEMENTATION: every finding becomes a CR (agent "audit") in the
    ordinary Автоулучшение queue — implement on Test, look with your own
    eyes, then port to stage/prod by the existing admin sign-off. The
    audit file is marked "processed" (that is the user-facing contract:
    после анализа файл отмечается как обработанный); re-processing is
    refused, and a file whose content CHANGES after processing is
    flagged, never silently re-read.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import registry

AUDITS_DIR_DEFAULT = r"C:\Users\lev\cosmos-audits"
REGISTRY_PATH = registry.REPO_ROOT / "webapp" / "config" / "audit_reports.json"

FNAME_RE = re.compile(r"^(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{4})_audit\.md$")
MAX_AUDIT_BYTES = 512 * 1024          # a report, not a data dump
MAX_FINDINGS = 30                     # CR flood guard per audit
ANALYSIS_TIMEOUT_S = 300.0
POLL_INTERVAL_S = 600
PROCESSING_TTL_S = 900                # a "processing" claim older than this
                                      # is a crashed run and may be retaken

_lock = threading.Lock()


class AuditInboxError(ValueError):
    """Invalid audit-inbox input/state; message is safe to show."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def audits_dir() -> Path:
    return Path(os.environ.get("CREW_AUDITS_DIR") or AUDITS_DIR_DEFAULT)


def parse_fname(name: str) -> str | None:
    """ISO timestamp encoded in a valid audit file name, else None.
    The auditor's contract is strict: hh_mm_dd_mm_yyyy_audit.md with a
    real clock time and calendar date."""
    m = FNAME_RE.match(name or "")
    if not m:
        return None
    hh, mi, dd, mo, yyyy = (int(x) for x in m.groups())
    try:
        return datetime(yyyy, mo, dd, hh, mi).isoformat(timespec="minutes")
    except ValueError:
        return None


# ------------------------------------------------------------------- store

def _load() -> dict:
    """Читает реестр FAIL-CLOSED (внешний аудит R7, P1-18а).

    Прежнее «повреждённый байт вырождается в пустой реестр» стирало
    журнал обработанных аудитов: уже обработанный отчёт регистрировался
    заново и порождал ВТОРОЙ комплект заявок на изменение. Отсутствие
    файла — законный пустой реестр; повреждение — ошибка вслух, а
    транзиентный сбой чтения Windows — короткий повтор, не повод
    объявлять реестр пустым."""
    if not REGISTRY_PATH.exists():
        return {}
    last: Exception | None = None
    for _ in range(5):
        try:
            raw = REGISTRY_PATH.read_text(encoding="utf-8")
            break
        except OSError as exc:                 # transient Windows share/AV
            last = exc
            time.sleep(0.05)
    else:
        raise AuditInboxError(
            f"реестр аудитов временно недоступен ({last}); операция "
            f"отменена — перезаписывать реестр вслепую нельзя")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuditInboxError(
            "реестр аудитов повреждён (%s) — операция отменена; "
            "проверьте %s, иначе обработанные отчёты породили бы "
            "повторные заявки" % (exc, REGISTRY_PATH.name)) from exc
    if not isinstance(data, dict):
        raise AuditInboxError(
            "реестр аудитов повреждён (не объект) — операция отменена; "
            "проверьте " + REGISTRY_PATH.name)
    return data


def _save_locked(data: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(REGISTRY_PATH)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_entry(p: Path, d: Path) -> bool:
    """A directory entry is trustworthy only if it is a regular file that
    is NOT a symlink and whose resolved path stays inside the inbox. The
    inbox is an untrusted third-party drop folder — a symlink/junction
    named like an audit could otherwise point the reader at any file the
    service account can open (arbitrary read + exfiltration to the LLM);
    audit 2026-07-16."""
    try:
        if p.is_symlink():
            return False
        rp = p.resolve()
        return rp.is_file() and rp.is_relative_to(d.resolve())
    except OSError:
        return False


# -------------------------------------------------------------------- scan

def scan() -> dict:
    """Look at the inbox directory, register unseen valid files as "new",
    flag processed files whose bytes changed, and return the full picture.
    Cheap (one listdir + hashes of new files) — safe for the button AND
    the poller. Never raises on a missing directory: that is an honest
    empty inbox, not an error."""
    d = audits_dir()
    found: dict[str, Path] = {}
    ignored: list[str] = []
    try:
        for p in sorted(d.iterdir()):
            if parse_fname(p.name) is None:
                if p.is_file() or p.is_symlink():
                    ignored.append(p.name)     # named wrong: surface it
                continue
            if not _safe_entry(p, d):
                ignored.append(p.name)         # symlink / escapes inbox
                continue
            found[p.name] = p
    except OSError:
        pass                                   # missing dir == empty inbox

    newly: list[str] = []
    with _lock:
        data = _load()
        files = data.setdefault("files", {})
        changed = False
        for name, p in found.items():
            rec = files.get(name)
            try:
                digest = _sha256(p)
                size = p.stat().st_size
            except OSError:
                continue                       # vanished mid-scan
            if rec is None:
                files[name] = {
                    "status": "new",
                    "audited_at": parse_fname(name),
                    "sha256": digest,
                    "size": size,
                    "detected_at": _now(),
                    "notified": False,
                }
                newly.append(name)
                changed = True
            elif (rec.get("status") == "processed"
                    and rec.get("sha256") not in (None, digest)
                    and not rec.get("modified_after_processing")):
                # the auditor's report changed AFTER we analyzed it —
                # surface it loudly, never silently re-read
                rec["modified_after_processing"] = True
                changed = True
            elif (rec.get("status") == "new"
                    and rec.get("sha256") not in (None, digest)):
                # Ещё НЕ обработанный отчёт аудитор дописал: прежний хеш
                # навсегда запирал обработку («изменён между проверкой и
                # обработкой» на каждой попытке — внешний аудит R7,
                # P1-18б). Свежая версия принимается явно, с пометкой.
                rec.update({"sha256": digest, "size": size,
                            "detected_at": _now(),
                            "changed_before_processing": True})
                changed = True
        for name, rec in files.items():
            missing = name not in found
            if bool(rec.get("missing")) != missing:
                rec["missing"] = missing
                changed = True
        if changed:
            _save_locked(data)
        # newest audit first: the FILENAME sorts by hour-of-day (it starts
        # hh_mm), so sort on the ISO audited_at instead (audit 2026-07-16)
        listing = [{**rec, "file": name} for name, rec in files.items()]
        listing.sort(key=lambda r: (r.get("audited_at") or "", r["file"]),
                     reverse=True)
    return {"dir": str(d), "files": listing, "new": newly,
            "ignored": ignored}


def mark_notified(names: list[str]) -> None:
    with _lock:
        data = _load()
        files = data.get("files") or {}
        hit = False
        for n in names:
            if n in files and not files[n].get("notified"):
                files[n]["notified"] = True
                hit = True
        if hit:
            _save_locked(data)


# ----------------------------------------------------------------- analysis

_ANALYSIS_SYSTEM = """\
You analyze a third-party AUDIT REPORT of COSMOS, a crew pairing &
rostering application. Extract every actionable point. Reply with ONLY a
JSON object, no prose:
{"summary": "...", "findings": [{"kind": "bug|flaw|remark|suggestion",
  "title": "...", "detail": "...", "severity": 1-5, "effort": 1-10}]}
Rules: summary = an honest executive assessment of the whole audit in
Russian, <= 800 chars — what the auditor found, how serious it is, what
to do first. One finding per distinct point; do not merge unrelated
points and do not invent ones the auditor did not make. title <= 90
chars in Russian; detail = the point restated precisely in Russian with
enough context to implement, <= 600 chars, citing the auditor's wording
where it helps; severity: 5 = critical bug, 1 = cosmetic remark; effort
= implementation difficulty for one engineer (1 trivial .. 10 huge)."""

_KINDS = ("bug", "flaw", "remark", "suggestion")


def _extract_json_object(text: str):
    """First complete JSON object in an LLM reply, prose-tolerant. A greedy
    first-brace-to-last-brace regex breaks the moment the model appends any
    prose containing a '}' (common on the weaker local backend), so scan
    each '{' and let raw_decode consume exactly one object (audit
    2026-07-16)."""
    text = text or ""
    dec = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _end = dec.raw_decode(text, idx)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        idx = text.find("{", idx + 1)
    return None


def _claim_for_processing(name: str, by: str) -> dict:
    """Atomically take the processing slot for one NEW file, under the
    lock. Refuses a file already processed or being processed by a live
    claim; a stale claim (crashed run older than PROCESSING_TTL_S) may be
    retaken. Returns the record. This single guarded transition is what
    stops two concurrent /process calls (double-click, two tabs) from both
    running the LLM and both appending duplicate CRs (audit 2026-07-16)."""
    token = secrets.token_hex(8)
    with _lock:
        data = _load()
        rec = (data.get("files") or {}).get(name)
        if rec is None:
            raise AuditInboxError("file is not registered — run a check first")
        if rec.get("status") == "processed":
            raise AuditInboxError("this audit is already processed")
        if rec.get("status") == "processing":
            started = rec.get("processing_at") or ""
            try:
                age = (datetime.now(timezone.utc)
                       - datetime.fromisoformat(started)).total_seconds()
            except ValueError:
                age = PROCESSING_TTL_S + 1        # unparseable == stale
            if age < PROCESSING_TTL_S:
                raise AuditInboxError(
                    "this audit is already being processed — wait for it "
                    "to finish")
        rec["status"] = "processing"
        rec["processing_at"] = _now()
        rec["claim"] = token
        rec["processed_by"] = by
        _save_locked(data)
        return {**rec}


def _release_claim(name: str, token: str) -> None:
    """Return a claimed-but-unfinished file to 'new' so it can be retried.
    Only called BEFORE any CR is created — once CRs exist the claim is
    kept (a stuck 'processing' beats duplicate CRs on retry; the TTL lets
    it be retaken eventually)."""
    with _lock:
        data = _load()
        rec = (data.get("files") or {}).get(name)
        if rec and rec.get("claim") == token and rec.get("status") == \
                "processing":
            rec["status"] = "new"
            rec.pop("claim", None)
            rec.pop("processing_at", None)
            _save_locked(data)


def _norm_text(value) -> str:
    """Нормализация текста находки для идентификатора: регистр и разбивка
    пробелами не должны порождать «другую» находку."""
    return " ".join(str(value or "").split()).casefold()


def _finding_id(name: str, file_sha: str, finding: dict,
                seen: dict[str, int]) -> str:
    """Идентификатор заявки как функция СОДЕРЖАНИЯ находки.

    `seen` разводит буквально одинаковые находки внутри ОДНОГО отчёта:
    порядковый номер добавляется только внутри группы совпадающих, а не
    глобально, поэтому перестановка или отбраковка соседей идентификатор
    не сдвигает.
    """
    base = "|".join((name, file_sha, _norm_text(finding.get("kind")),
                     _norm_text(finding.get("title")),
                     _norm_text(finding.get("detail"))))
    n = seen.get(base, 0)
    seen[base] = n + 1
    if n:
        base = f"{base}|#{n}"
    return "cra" + hashlib.sha256(base.encode()).hexdigest()[:10]


def _prepared_analysis(name: str, token: str) -> dict | None:
    """Разбор, сохранённый ДО создания заявок, если он есть и слот наш."""
    with _lock:
        rec = (_load().get("files") or {}).get(name) or {}
        if rec.get("claim") != token:
            return None
        return rec.get("analysis_pending")


def _persist_analysis(name: str, token: str, analysis: dict,
                      rows: list) -> None:
    """Сделать разбор ДОЛГОВЕЧНЫМ до создания заявок.

    Это устраняет ПРИЧИНУ неидемпотентности, а не её следствие: после
    сбоя восстановление продолжает работу с сохранённого разбора и модель
    не вызывается повторно, поэтому её недетерминизм больше не способен
    подменить состав очереди.
    """
    with _lock:
        data = _load()
        rec = (data.get("files") or {}).get(name)
        if rec is None or rec.get("claim") != token:
            raise AuditInboxError(
                "слот обработки потерян до сохранения разбора — повторите "
                "проверку")
        rec["analysis_pending"] = {"analysis": analysis, "rows": rows}
        _save_locked(data)


def process(name: str, by: str) -> dict:
    """Analyze one NEW audit file: LLM breakdown -> one CR per finding
    (agent "audit", ordinary Автоулучшение queue) -> registry record
    flips to "processed". Refuses double-processing and files that
    changed on disk since detection (the auditor's report is an artifact,
    not a living document)."""
    from . import change_requests as crq

    if parse_fname(name) is None:
        raise AuditInboxError(f"not an audit file name: {name!r}")
    d = audits_dir()
    path = d / name
    claimed = _claim_for_processing(name, by)
    token = claimed["claim"]
    prepared = _prepared_analysis(name, token)
    if prepared is not None:
        # Восстановление после сбоя: разбор уже сохранён, модель НЕ
        # вызывается. Прежде здесь шёл повторный вызов, и его иной порядок
        # ответа подменял состав очереди (внешний аудит R9, P1-02).
        return _commit(name, by, token, prepared["analysis"],
                       prepared["rows"], crq)
    try:
        # ONE read of the bytes (audit 2026-07-16): a symlink target or a
        # swap between a separate stat/hash and read would otherwise let
        # unverified — or oversized — content reach the LLM (TOCTOU). Reject
        # symlinks, read once, then size/hash/decode all off the same bytes.
        if not _safe_entry(path, d):
            raise AuditInboxError("audit file is not a plain file in the "
                                  "inbox (symlink or moved)")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise AuditInboxError(f"audit file unreadable: {exc}") from exc
        if len(raw) > MAX_AUDIT_BYTES:
            raise AuditInboxError(
                f"audit file too large ({len(raw)} bytes > {MAX_AUDIT_BYTES})")
        if hashlib.sha256(raw).hexdigest() != claimed.get("sha256"):
            raise AuditInboxError(
                "file content changed since detection — re-run the check, "
                "then process the fresh version")
        text = raw.decode("utf-8", errors="replace")

        res = crq.llm_backend.complete(
            "AUDIT REPORT (" + name + "):\n\n" + text,
            backend=crq._agent_backend(), timeout_s=ANALYSIS_TIMEOUT_S,
            system=_ANALYSIS_SYSTEM, usage="agents")
        if not res.get("ok"):
            raise AuditInboxError(
                f"audit analysis LLM failed: {res.get('error')}")
        obj = _extract_json_object(res.get("text") or "")
        if obj is None:
            raise AuditInboxError("audit analysis returned no JSON object")
    except BaseException:
        # nothing durable was created yet — free the slot for a retry
        _release_claim(name, token)
        raise

    findings = []
    for f in (obj.get("findings") or [])[:MAX_FINDINGS]:
        if not isinstance(f, dict):
            continue
        kind = str(f.get("kind") or "").strip().lower()
        if kind not in _KINDS:
            kind = "remark"
        title = crq._clip(f.get("title"), 90)
        if not title:
            continue
        try:
            severity = max(1, min(5, int(f.get("severity", 3))))
        except (TypeError, ValueError):
            severity = 3
        try:
            effort = max(1, min(10, int(f.get("effort", 5))))
        except (TypeError, ValueError):
            effort = 5
        findings.append({"kind": kind, "title": title,
                         "detail": crq._clip(f.get("detail"), 600),
                         "severity": severity, "effort": effort})

    rows = []
    seen_ids: dict[str, int] = {}
    for index, f in enumerate(findings):
        rows.append({
            # Идентификатор от СОДЕРЖАНИЯ находки (внешний аудит R9, P1-02).
            #
            # Прежде он строился из (отпечаток файла, НОМЕР находки в ответе
            # модели). Детерминизм был синтаксическим: он держался на
            # допущении, что модель вернёт тот же список в том же порядке.
            # Достаточно было сбоя до записи реестра — и повтор с иным
            # порядком давал очередь из двух копий одной находки, терял
            # другую, а ссылки реестра указывали на семантически чужие
            # строки. Порядок и отбраковка пустых заголовков больше ни на
            # что не влияют.
            #
            # Имя файла входит в ключ: два отчёта с одинаковыми байтами под
            # разными именами прежде получали совпадающие идентификаторы, и
            # второй помечался обработанным, не создав ни одной заявки.
            "id": _finding_id(name, claimed["sha256"], f, seen_ids),
            "agent": "audit",
            "title": f"[{f['kind']}] {f['title']}"[:90],
            "summary": crq._clip(
                f["detail"] + f"\n\nИсточник: аудит {name}, "
                f"серьёзность {f['severity']}/5.", 600),
            "suggestion_ids": [],
            # severity maps straight onto the queue's priority column:
            # a critical audit bug outranks a cosmetic remark
            "priority": f["severity"],
            "effort": f["effort"],
            "status": "draft",
            "source": name,
            "country": None,
            "created_at": _now(), "updated_at": _now(),
        })
    analysis = {
        "summary": crq._clip(obj.get("summary"), 800),
        "findings": findings,
        "backend": res.get("backend"),
        "model": res.get("model"),
    }
    # Разбор становится долговечным ДО создания заявок: с этого момента
    # повторная обработка не обращается к модели и потому не зависит от
    # её порядка и состава (внешний аудит R9, P1-02).
    _persist_analysis(name, token, analysis, rows)
    return _commit(name, by, token, analysis, rows, crq)


def _commit(name: str, by: str, token: str, analysis: dict, rows: list,
            crq) -> dict:
    """Записать заявки и закрыть запись реестра.

    Чистая по отношению к модели: вызывается и при первой обработке, и при
    восстановлении. Уникальность идентификаторов обеспечивает САМА точка
    записи очереди, поэтому предварительное чтение «а нет ли уже таких»
    (прежде — вне лока, с окном между чтением и записью) больше не нужно.
    """
    added = crq._add_crs(rows, unique_by_id=True)
    if len(added) != len(rows):
        print("audit_inbox: %d из %d заявок уже существовали — "
              "восстановление после сбоя, дубли не созданы"
              % (len(rows) - len(added), len(rows)), flush=True)
    with _lock:
        data = _load()
        rec = (data.get("files") or {}).get(name)
        if rec is None or rec.get("claim") != token:
            # our slot was reclaimed (TTL) or the registry was reset while
            # the LLM ran — the CRs still exist and carry source=name, so
            # they are not lost; report the anomaly honestly
            raise AuditInboxError(
                "the processing slot was lost while analyzing — the change "
                "requests were created (source " + name + "), but the "
                "registry could not be updated; re-run the check")
        rec.update({
            "status": "processed",
            "processed_at": _now(),
            "processed_by": by,
            "analysis": analysis,
            "cr_ids": [r["id"] for r in rows],
        })
        rec.pop("claim", None)
        rec.pop("processing_at", None)
        rec.pop("analysis_pending", None)
        _save_locked(data)
        out = {**rec, "file": name}
    return out


# ------------------------------------------------------------------- poller

def _poll_enabled() -> bool:
    """Test contour only, explicit opt-in — the same shape as the CR
    scheduler gate (CREW_CR_SCHEDULE)."""
    return os.environ.get("CREW_AUDIT_POLL", "") == "1"


def start_poller_thread() -> bool:
    """Every POLL_INTERVAL_S: scan the inbox and announce not-yet-notified
    new files via Telegram (once per file). Silent on all errors — the
    poller must never take the app down."""
    if not _poll_enabled():
        return False

    def loop() -> None:
        from . import telegram_bot
        while True:
            try:
                result = scan()
                pending = [f["file"] for f in result["files"]
                           if f.get("status") == "new"
                           and not f.get("notified")
                           and not f.get("missing")]
                if pending:
                    telegram_bot.notify_audit_found(pending)
                    mark_notified(pending)
            except Exception:                    # noqa: BLE001 — keep alive
                pass
            time.sleep(POLL_INTERVAL_S)

    threading.Thread(target=loop, daemon=True,
                     name="audit-inbox-poller").start()
    return True
