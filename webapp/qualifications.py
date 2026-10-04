"""Crew qualification / currency & recency management (2026-07-15, gap #5).

Industrial crew systems (Lufthansa CQT, IBS iFlight, AIMS, Jeppesen, CAE)
track the TEMPORAL validity of each crew member's clearances — licence,
medical, visa/passport, type rating — plus recency currency (e.g. N recent
landings within a window), and forecast which crew fall out of compliance
BEFORE a flight date so the roster never assigns an illegal-because-expired
crew member.

This layer adds exactly that on top of the existing STATIC skill/type-rating
hard constraints:
  * per-instance qualification records with expiry dates + recency counters,
  * a compliance calendar against the instance's planning month (expired /
    expires-during / expires-soon / not-current), with severity alerts,
  * apply(): bake the non-compliant span into unavailableDays (a HARD roster
    constraint — "G2P Unavailable") of a DERIVED instance, so the regular
    roster hard-avoids assigning a crew member from the moment a blocking
    clearance lapses — no solver change.

Record sources (канон v2, 2026-07-17): explicit submissions live in
config/qualifications.json keyed by inst_id and always win; when the store
is empty, the compliance calendar reads the canonical payload's optional
v2 `qualifications` entities directly (records_from_payload) — an imported
dataset that carries validity dates needs no re-entry. A demo generator
seeds plausible records (some expiring mid-month) so the calendar is
visible.
"""
from __future__ import annotations

import copy
import json
import threading
from datetime import date, datetime, timedelta

from . import registry

QUALS_PATH = registry.REPO_ROOT / "webapp" / "config" / "qualifications.json"

_lock = threading.Lock()

# A qualification KIND is either a dated clearance (expires) or a recency
# currency (recent_events vs required within a window). "recency" is the only
# kind that carries recency fields; the rest carry an expiry date.
DATED_KINDS = ("licence", "medical", "visa", "passport", "type_rating")
KINDS = DATED_KINDS + ("recency",)

MAX_RECORDS_PER_EMP = 12
MAX_EMPLOYEES = 400
DEFAULT_WARN_DAYS = 30

# Severity ranks for sorting alerts (higher = worse).
_SEV = {"expired": 3, "expires_during": 3, "not_current": 3,
        "expires_soon": 2, "ok": 0}


class QualificationError(ValueError):
    """User-facing qualification failure."""


# --------------------------------------------------------------- storage

def _load() -> dict:
    try:
        raw = json.loads(QUALS_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"qualifications store unreadable: {exc}") from exc


def _save_locked(data: dict) -> None:
    QUALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUALS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),  # census-ok: журнал модуля, не набор
                   encoding="utf-8")
    tmp.replace(QUALS_PATH)


def meta_for(inst_id: str) -> dict:
    m = _load().get(inst_id)
    if not isinstance(m, dict):
        return {"warn_days": DEFAULT_WARN_DAYS, "records": {},
                "derived_instance": None}
    m.setdefault("warn_days", DEFAULT_WARN_DAYS)
    m.setdefault("records", {})
    m.setdefault("derived_instance", None)
    return m


def _store(inst_id: str, meta: dict) -> None:
    with _lock:
        data = _load()
        data[inst_id] = meta
        _save_locked(data)


# --------------------------------------------------------------- helpers

def _payload_employees(inst: dict) -> list[dict]:
    payload = registry.load_payload(registry.instance_payload_path(inst))
    return list(payload.get("employees") or [])


def _payload_date_range(inst: dict) -> tuple[date, date]:
    payload = registry.load_payload(registry.instance_payload_path(inst))
    days: list[date] = []
    for f in payload.get("flights") or []:
        for key in ("departureUTCDateTime", "arrivalUTCDateTime"):
            v = f.get(key)
            if isinstance(v, str) and len(v) >= 10:
                try:
                    days.append(date.fromisoformat(v[:10]))
                except ValueError:
                    pass
    if not days:
        return date(2000, 1, 1), date(2000, 1, 31)
    return min(days), max(days)


def _parse_day(s) -> date:
    if not isinstance(s, str):
        raise QualificationError(f"date must be an ISO string, got {s!r}")
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise QualificationError(f"bad ISO date {s!r}: {exc}") from exc


def validate_records(records, known_ids: set[str]) -> dict:
    """Normalize + validate {employee_id: [record]}. Raises
    QualificationError. Returns the cleaned map."""
    if not isinstance(records, dict):
        raise QualificationError("'records' must be an object keyed by "
                                 "employee_id")
    if len(records) > MAX_EMPLOYEES:
        raise QualificationError(f"too many employees ({len(records)})")
    out: dict[str, list] = {}
    for emp, recs in records.items():
        emp = str(emp).strip()
        if known_ids and emp not in known_ids:
            raise QualificationError(f"unknown employee_id {emp!r}")
        if not isinstance(recs, list):
            raise QualificationError(f"{emp}: records must be a list")
        if len(recs) > MAX_RECORDS_PER_EMP:
            raise QualificationError(
                f"{emp}: too many records (max {MAX_RECORDS_PER_EMP})")
        clean = []
        for r in recs:
            if not isinstance(r, dict):
                raise QualificationError(f"{emp}: each record is an object")
            kind = str(r.get("kind") or "").strip()
            if kind not in KINDS:
                raise QualificationError(
                    f"{emp}: unknown kind {kind!r} (known: {list(KINDS)})")
            code = str(r.get("code") or "").strip()
            if not code or len(code) > 40:
                raise QualificationError(f"{emp}: record needs a short code")
            rec = {"kind": kind, "code": code}
            if kind == "recency":
                for f in ("required_events", "recent_events", "within_days"):
                    v = r.get(f)
                    try:
                        rec[f] = int(v)
                    except (TypeError, ValueError):
                        raise QualificationError(
                            f"{emp}/{code}: recency '{f}' must be an integer")
                    if rec[f] < 0:
                        raise QualificationError(
                            f"{emp}/{code}: recency '{f}' must be >= 0")
            else:
                _parse_day(r.get("expires"))       # validate
                rec["expires"] = str(r.get("expires"))
            clean.append(rec)
        out[emp] = clean
    return out


# --------------------------------------------------------------- commands

def set_warn_days(inst_id: str, warn_days, email: str | None = None) -> dict:
    try:
        w = int(warn_days)
    except (TypeError, ValueError):
        raise QualificationError("warn_days must be an integer")
    if w < 0 or w > 365:
        raise QualificationError("warn_days must be between 0 and 365")
    meta = meta_for(inst_id)
    meta["warn_days"] = w
    if email:
        meta.setdefault("owner", email)
    _store(inst_id, meta)
    return meta


def submit_records(inst_id: str, inst: dict, records,
                   email: str | None = None) -> dict:
    known = {str(e.get("id")) for e in _payload_employees(inst) if e.get("id")}
    clean = validate_records(records, known)
    meta = meta_for(inst_id)
    meta["records"] = clean
    if email:
        meta.setdefault("owner", email)
    _store(inst_id, meta)
    return meta


def records_from_payload(payload: dict) -> dict:
    """Canonical v2 `qualifications` -> the feature's record map (канон v2:
    квалификации со сроками живут в самом payload). The payload passed
    schema + referential validation upstream; entries that still miss the
    feature's shape are skipped defensively — this is a read path, and the
    loud gate is validate_payload."""
    out: dict[str, list] = {}
    for q in payload.get("qualifications") or []:
        if not isinstance(q, dict):
            continue
        emp = str(q.get("employee") or "").strip()
        kind = str(q.get("kind") or "").strip()
        code = str(q.get("code") or "").strip()
        if not emp or kind not in KINDS or not code:
            continue
        rec: dict = {"kind": kind, "code": code[:40]}
        if kind == "recency":
            try:
                rec["required_events"] = int(q.get("requiredEvents", 0))
                rec["recent_events"] = int(q.get("recentEvents", 0))
                rec["within_days"] = int(q.get("withinDays", 0))
            except (TypeError, ValueError):
                continue
        else:
            v = q.get("validTo")
            if not isinstance(v, str) or not v:
                continue
            # jsonschema's format:date is advisory (not enforced by default),
            # so a syntactically-bad validTo can reach here; parse it now and
            # SKIP the unparseable record instead of letting _record_status
            # raise deep inside compliance() (which would 500 the calendar).
            try:
                date.fromisoformat(v)
            except ValueError:
                continue
            rec["expires"] = v
        out.setdefault(emp, []).append(rec)
    # Keep the temporally most urgent records if an employee somehow carries
    # more than the cap — never silently drop a sooner-expiring clearance for
    # a later one (sort dated records by expiry; recency first, always kept).
    for emp in list(out):
        recs = out[emp]
        if len(recs) > MAX_RECORDS_PER_EMP:
            recs.sort(key=lambda r: (r["kind"] != "recency",
                                     r.get("expires") or "9999"))
            out[emp] = recs[:MAX_RECORDS_PER_EMP]
    return out


def effective_records(inst_id: str, inst: dict) -> tuple[dict, str]:
    """(records, source): the store wins (explicit submissions/edits);
    otherwise the canonical v2 payload; otherwise empty."""
    meta = meta_for(inst_id)
    records = meta.get("records") or {}
    if records:
        return records, "store"
    payload = registry.load_payload(registry.instance_payload_path(inst))
    records = records_from_payload(payload)
    return (records, "canonical") if records else ({}, "none")


def _record_status(rec: dict, lo: date, hi: date, warn_days: int) -> dict:
    """Compliance status of one record against the planning month [lo, hi].
    Returns {status, first_off (ISO|None), detail}."""
    if rec["kind"] == "recency":
        need = rec.get("required_events", 0)
        have = rec.get("recent_events", 0)
        if have < need:
            return {"status": "not_current",
                    "first_off": lo.isoformat(),
                    "detail": f"{have}/{need} in {rec.get('within_days')}d"}
        return {"status": "ok", "first_off": None,
                "detail": f"{have}/{need}"}
    exp = _parse_day(rec["expires"])
    if exp < lo:
        return {"status": "expired", "first_off": lo.isoformat(),
                "detail": rec["expires"]}
    # exp == hi means valid THROUGH the last flight day and no flight runs
    # after it, so the crew is compliant for the whole window — it must NOT
    # be blocking (audit 2026-07-15: exp==hi was flagged expires_during but
    # grounded zero days, so blocking_employees disagreed with apply). Only
    # exp strictly inside the window grounds a real span (exp+1 .. hi).
    if exp < hi:
        # valid through exp inclusive; grounded from the day after
        return {"status": "expires_during",
                "first_off": (exp + timedelta(days=1)).isoformat(),
                "detail": rec["expires"]}
    if exp <= hi + timedelta(days=warn_days):
        return {"status": "expires_soon", "first_off": None,
                "detail": rec["expires"]}
    return {"status": "ok", "first_off": None, "detail": rec["expires"]}


def compliance(inst_id: str, inst: dict) -> dict:
    """Compliance calendar for the instance's planning month: per-employee
    worst status + first non-compliant day, plus a flat alert list sorted by
    severity. Read-only."""
    meta = meta_for(inst_id)
    records, records_source = effective_records(inst_id, inst)
    warn = int(meta.get("warn_days", DEFAULT_WARN_DAYS))
    lo, hi = _payload_date_range(inst)
    emp_names = {str(e.get("id")): e.get("name") or e.get("id")
                 for e in _payload_employees(inst)}

    per_emp = []
    alerts = []
    counts = {"expired": 0, "expires_during": 0, "not_current": 0,
              "expires_soon": 0, "ok": 0}
    for emp, recs in records.items():
        worst = "ok"
        first_off = None
        rec_statuses = []
        for r in recs:
            st = _record_status(r, lo, hi, warn)
            rec_statuses.append({"kind": r["kind"], "code": r["code"],
                                 **st})
            if _SEV[st["status"]] > _SEV[worst]:
                worst = st["status"]
            if st["first_off"] is not None:
                first_off = (st["first_off"] if first_off is None
                             else min(first_off, st["first_off"]))
            if st["status"] != "ok":
                alerts.append({"employee_id": emp,
                               "name": emp_names.get(emp, emp),
                               "kind": r["kind"], "code": r["code"],
                               "status": st["status"],
                               "detail": st["detail"]})
        counts[worst] = counts.get(worst, 0) + 1
        per_emp.append({"employee_id": emp, "name": emp_names.get(emp, emp),
                        "status": worst, "first_off": first_off,
                        "records": rec_statuses})

    alerts.sort(key=lambda a: (-_SEV[a["status"]], a["employee_id"]))
    per_emp.sort(key=lambda e: (-_SEV[e["status"]], e["employee_id"]))
    blocking = sum(1 for e in per_emp if e["first_off"] is not None)
    return {
        "month": {"from": lo.isoformat(), "to": hi.isoformat()},
        "warn_days": warn,
        "records_source": records_source,
        "employees_with_records": len(records),
        "counts": counts,
        "blocking_employees": blocking,
        "alerts": alerts,
        "employees": per_emp,
        "computed_at": datetime.now().isoformat(timespec="seconds"),
    }


def _remove_derived(derived_id: str) -> None:
    if not derived_id:
        return
    di = registry.get_instance(derived_id)
    if di is not None:
        try:
            registry.instance_payload_path(di).unlink(missing_ok=True)
        except OSError:
            pass
    registry.remove_owner(derived_id)
    registry.remove_llm_scope(derived_id)


def apply_compliance(inst_id: str, inst: dict, email: str) -> dict:
    """Bake each non-compliant employee's grounded span (first_off .. month
    end) into unavailableDays of a DERIVED instance the roster runs on
    unchanged, so an expired/not-current crew member is never assigned on or
    after the day their clearance lapses. Re-applying replaces the prior
    derived instance."""
    comp = compliance(inst_id, inst)
    lo = _parse_day(comp["month"]["from"])
    hi = _parse_day(comp["month"]["to"])
    grounded: dict[str, list[str]] = {}
    for e in comp["employees"]:
        if e["first_off"] is None:
            continue
        start = max(lo, _parse_day(e["first_off"]))
        days = [(start + timedelta(days=i)).isoformat()
                for i in range((hi - start).days + 1)]
        if days:
            grounded[e["employee_id"]] = days
    if not grounded:
        raise QualificationError(
            "no non-compliant crew to ground — nothing to apply")

    payload = copy.deepcopy(
        registry.load_payload(registry.instance_payload_path(inst)))
    for e in payload.get("employees") or []:
        eid = str(e.get("id"))
        if eid in grounded:
            existing = set(e.get("unavailableDays") or [])
            e["unavailableDays"] = sorted(existing | set(grounded[eid]))

    base_slug = registry.slugify_or_hash(inst.get("label") or inst["id"])
    slug = f"currency-{base_slug}-{_token()}"
    new_id, _label, target = registry.upload_target(email, slug)
    registry.write_payload(target, payload, "qualification_grounding")
    registry.set_owner(new_id, email)
    registry.set_llm_scope(new_id, inst.get("llm_scope", "local-only"))

    meta = meta_for(inst_id)
    prior = meta.get("derived_instance")
    if prior and prior != new_id:
        _remove_derived(prior)
    meta["derived_instance"] = new_id
    _store(inst_id, meta)
    return {"id": new_id, "grounded_employees": len(grounded),
            "grounded_days": sum(len(v) for v in grounded.values())}


def _token() -> str:
    import secrets
    return secrets.token_hex(3)


def demo_records(inst: dict, want: int = 16) -> dict:
    """Seed plausible qualification records: a licence + medical + type
    rating + recency per employee, with a deterministic minority expiring
    mid-month (red), some soon after (amber), and a couple not current — so
    the compliance calendar and alerts are visible on the flat GERAD seeds."""
    emps = _payload_employees(inst)
    if not emps:
        raise QualificationError("instance has no employees")
    lo, hi = _payload_date_range(inst)
    n = min(want, len(emps))
    out: dict[str, list] = {}
    for i in range(n):
        emp = emps[i]
        eid = str(emp.get("id"))
        ac = (emp.get("typeRatings") or ["B727"])[0]
        recs = [
            {"kind": "licence", "code": "ATPL",
             "expires": (hi + timedelta(days=365)).isoformat()},
        ]
        # medical: every 5th expires mid-month (red), every 5th+2 soon (amber)
        if i % 5 == 0:
            recs.append({"kind": "medical", "code": "Class-1",
                         "expires": (lo + timedelta(
                             days=min((hi - lo).days,
                                      7 + (i % 10)))).isoformat()})
        elif i % 5 == 2:
            recs.append({"kind": "medical", "code": "Class-1",
                         "expires": (hi + timedelta(
                             days=10 + (i % 15))).isoformat()})
        else:
            recs.append({"kind": "medical", "code": "Class-1",
                         "expires": (hi + timedelta(days=200)).isoformat()})
        recs.append({"kind": "type_rating", "code": str(ac),
                     "expires": (hi + timedelta(days=120)).isoformat()})
        # recency: a couple are not current
        if i % 7 == 3:
            recs.append({"kind": "recency", "code": "3-landings-90d",
                         "required_events": 3, "recent_events": 1,
                         "within_days": 90})
        else:
            recs.append({"kind": "recency", "code": "3-landings-90d",
                         "required_events": 3, "recent_events": 3,
                         "within_days": 90})
        out[eid] = recs
    return out


def clear(inst_id: str, email: str, is_admin: bool) -> bool:
    data = _load()
    meta = data.get(inst_id)
    if not isinstance(meta, dict):
        return False
    if not is_admin and meta.get("owner") not in (None, email):
        return False
    _remove_derived(meta.get("derived_instance"))
    with _lock:
        data = _load()
        data.pop(inst_id, None)
        _save_locked(data)
    return True
