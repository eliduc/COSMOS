"""Crew self-service portal (#18, 2026-07-15): the app turned toward the
crew member, not just the planner.

Industrial context: this is the "crew self-service / crew mobile" layer of
industrial suites (Jeppesen CrewAccess, NetLine/Crew mobile) — the office
optimization stays untouched, but every crew member gets a personal, mostly
phone-shaped cabinet:

  - "my schedule": ONLY their pairings from the stage-2 roster, with the
    planner's manual board moves applied (the cabinet must show what the
    board shows, so both reuse the same resolve+overlay path);
  - "my clearances": their qualification records against the planning month;
  - vacation self-service: the crew member files their OWN bid windows into
    the same store the seniority award solves over (upsert of one bid — the
    planner previously typed all bids in);
  - acknowledgement: the industry-standard "I have seen my (changed) roster"
    signature, kept per (instance, employee) with the roster signature it
    was given against, so a later reassignment honestly flips the cabinet
    back to "changed since acknowledgement".

There is deliberately NO new DB role: the users table stays admin|user (a
CHECK constraint — no migration). The capability is the LINK itself:
config/crew_links.json maps instance -> {email -> employee_id}, managed by
an admin or the instance owner. A linked user can read their cabinet for
that instance and nothing else about it (the regular owner-or-admin gates on
all other endpoints are unchanged; invisible stays 404).
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone

from . import qualifications, roster_board, vacation_bids
from . import registry

LINKS_PATH = registry.REPO_ROOT / "webapp" / "config" / "crew_links.json"
ACKS_PATH = registry.REPO_ROOT / "webapp" / "config" / "crew_acks.json"

_lock = threading.Lock()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CrewPortalError(ValueError):
    """Invalid crew-portal input; message is safe to show the user."""


# ----------------------------------------------------------------- storage

def _load(path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"crew-portal store unreadable: {exc}") from exc


def _save_locked(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------- links

def links_for_instance(inst_id: str) -> dict:
    """{email -> employee_id} for one instance."""
    m = _load(LINKS_PATH).get(inst_id)
    return dict(m) if isinstance(m, dict) else {}


def links_for_email(email: str) -> list[dict]:
    """Every live link of this user: [{instance, employee_id, label,
    source}]. Links to instances that no longer exist are skipped (they are
    pruned lazily on the next write)."""
    email = (email or "").strip().lower()
    out = []
    for inst_id, m in _load(LINKS_PATH).items():
        if not isinstance(m, dict) or email not in m:
            continue
        inst = registry.get_instance(inst_id)
        if inst is None:
            continue
        out.append({"instance": inst_id, "employee_id": str(m[email]),
                    "label": inst.get("label"),
                    "source": inst.get("source")})
    out.sort(key=lambda x: x["instance"])
    return out


def linked_employee(inst_id: str, email: str) -> str | None:
    v = links_for_instance(inst_id).get((email or "").strip().lower())
    return str(v) if v else None


def _known_employees(inst: dict) -> dict[str, dict]:
    payload = registry.load_payload(registry.instance_payload_path(inst))
    return {str(e.get("id")): e for e in (payload.get("employees") or [])
            if e.get("id")}


def set_link(inst_id: str, inst: dict, email: str, employee_id: str) -> dict:
    """Create/replace the link email -> employee for an instance. The
    employee must exist in the instance payload; one email maps to one
    employee per instance (re-linking replaces)."""
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise CrewPortalError(f"bad email: {email!r}")
    emp = str(employee_id or "").strip()
    known = _known_employees(inst)
    if emp not in known:
        raise CrewPortalError(f"unknown employee_id {emp!r}")
    with _lock:
        data = _load(LINKS_PATH)
        # lazy prune: drop links of instances that vanished
        data = {k: v for k, v in data.items()
                if registry.get_instance(k) is not None}
        m = data.setdefault(inst_id, {})
        m[email] = emp
        _save_locked(LINKS_PATH, data)
    return {"instance": inst_id, "email": email, "employee_id": emp}


def remove_link(inst_id: str, email: str) -> bool:
    email = (email or "").strip().lower()
    with _lock:
        data = _load(LINKS_PATH)
        m = data.get(inst_id)
        if not isinstance(m, dict) or email not in m:
            return False
        del m[email]
        if not m:
            del data[inst_id]
        _save_locked(LINKS_PATH, data)
    return True


# --------------------------------------------------------------- schedule

def _my_pairings(inst_id: str, inst: dict, emp_id: str):
    """This employee's pairings from the stage-2 roster WITH the planner's
    manual board moves applied — the same resolve+overlay the board uses.
    Returns (pairings|None, roster_available)."""
    pairings, _crew, _days = roster_board._resolve(inst)
    if pairings is None:
        return None, False
    all_pids = {p["pid"] for p in pairings}
    ed = roster_board._reconcile(inst_id, all_pids)
    moves = ed.get("moves") or {}
    mine = []
    for p in pairings:
        # P0-4 (аудит документации 2026-07-20): сотрудник видит пейринг с
        # ЛЮБОГО занимаемого места (второй пилот со-локационного экипажа
        # раньше не видел свой график); оверлей — по ключам мест: плоский
        # pid = первое место, 'pid#slot' — конкретное место.
        hit = False
        for i, s in enumerate(p.get("seats") or []):
            key = p["pid"] if i == 0 else f"{p['pid']}#{s.get('slot')}"
            eff = moves[key] if key in moves else s.get("solver_employee")
            if eff == emp_id:
                hit = True
                break
        if not (p.get("seats")):
            eff = (moves[p["pid"]] if p["pid"] in moves
                   else p["solver_employee"])
            hit = eff == emp_id
        if hit:
            mine.append(p)
    mine.sort(key=lambda p: p["dep_utc"])
    return mine, True


def _block_hours(pairings) -> float:
    total = 0.0
    for p in pairings:
        for leg in p["legs"]:
            total += (roster_board._dt(leg["arr_utc"])
                      - roster_board._dt(leg["dep_utc"])).total_seconds() / 3600
    return round(total, 1)


def roster_signature(pairings) -> str:
    """Signature of MY schedule: changes when a pairing is added to /
    removed from / re-timed for this employee — the 'changed since
    acknowledgement' test."""
    basis = sorted((p["pid"], p["dep_utc"], p["arr_utc"], p["nlegs"])
                   for p in pairings)
    return hashlib.sha1(
        json.dumps(basis, ensure_ascii=False).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------- acks

def ack_for(inst_id: str, emp_id: str) -> dict | None:
    m = _load(ACKS_PATH).get(inst_id)
    if isinstance(m, dict) and isinstance(m.get(emp_id), dict):
        return m[emp_id]
    return None


def acknowledge(inst_id: str, emp_id: str, email: str, sig: str) -> dict:
    # aware UTC: the cabinet is the multi-timezone surface, and a naive
    # server-local stamp displayed a wrong time on every crew phone whose
    # timezone differs from the server's (audit 2026-07-15)
    rec = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "email": (email or "").strip().lower(), "sig": sig}
    with _lock:
        data = _load(ACKS_PATH)
        data.setdefault(inst_id, {})[emp_id] = rec
        _save_locked(ACKS_PATH, data)
    return rec


# ---------------------------------------------------------------- vacation

def _meta_defaults(m) -> dict:
    """The same shape meta_for guarantees, applied to a raw store entry."""
    if not isinstance(m, dict):
        m = {}
    m.setdefault("quota", 1)
    m.setdefault("bids", [])
    m.setdefault("award", None)
    m.setdefault("derived_instance", None)
    return m


def submit_my_windows(inst_id: str, inst: dict, emp_id: str, windows) -> dict:
    """Upsert ONE employee's vacation bid (self-service): validates via the
    vacation module's own rules, replaces only this employee's bid, keeps
    everyone else's, and invalidates a stale award — exactly what
    submit_bids does for the full list.

    The whole read-modify-write runs under the vacation store lock: unlike
    the planner-only submit_bids (single writer, full-list replace), this is
    crew-facing and inherently concurrent near a bid deadline — an unlocked
    merge silently dropped an uninvolved third crew member's committed bid
    (audit 2026-07-15)."""
    known = set(vacation_bids._employee_seniority(inst))
    clean = vacation_bids.validate_bids(
        [{"employee_id": emp_id, "windows": windows}], known)
    bid = clean[0]
    sen_map = vacation_bids._employee_seniority(inst)
    if emp_id in sen_map:
        bid["seniority"] = int(sen_map[emp_id] or bid["seniority"])
    with vacation_bids._lock:
        data = vacation_bids._load()
        meta = _meta_defaults(data.get(inst_id))
        others = [b for b in meta["bids"]
                  if b.get("employee_id") != emp_id]
        # the MAX_BIDS solver-size guardrail must survive per-employee
        # upserts too, not just full-list submits (audit 2026-07-15)
        if len(others) + 1 > vacation_bids.MAX_BIDS:
            raise vacation_bids.VacationBidError(
                f"too many bids ({len(others) + 1} > "
                f"{vacation_bids.MAX_BIDS})")
        meta["bids"] = others + [bid]
        meta["award"] = None                 # bids changed -> stale award
        data[inst_id] = meta
        vacation_bids._save_locked(data)
    return bid


def withdraw_my_windows(inst_id: str, emp_id: str) -> bool:
    with vacation_bids._lock:                # atomic read-modify-write
        data = vacation_bids._load()
        meta = _meta_defaults(data.get(inst_id))
        kept = [b for b in meta["bids"] if b.get("employee_id") != emp_id]
        if len(kept) == len(meta["bids"]):
            return False
        meta["bids"] = kept
        meta["award"] = None
        data[inst_id] = meta
        vacation_bids._save_locked(data)
    return True


# ----------------------------------------------------------------- cabinet

def cabinet(inst_id: str, inst: dict, emp_id: str) -> dict:
    """The personal view: who I am, my schedule (+ block-hour totals), my
    clearances, my vacation bid/award state, my acknowledgement state."""
    known = _known_employees(inst)
    emp = known.get(emp_id)
    if emp is None:
        raise CrewPortalError(f"unknown employee_id {emp_id!r}")

    me = {
        "employee_id": emp_id,
        "name": emp.get("name") or emp_id,
        "base": emp.get("homeAirport"),
        "role": (emp.get("skills") or ["—"])[0],
        "seniority": emp.get("seniority"),
        "type_ratings": [str(x) for x in (emp.get("typeRatings") or [])],
    }

    mine, roster_available = _my_pairings(inst_id, inst, emp_id)
    schedule = None
    sig = None
    if roster_available:
        duty_days = sorted({d for p in mine for d in p["days"]})
        sig = roster_signature(mine)
        schedule = {
            "pairings": [{
                "pid": p["pid"], "base": p["base"],
                "dep_utc": p["dep_utc"], "arr_utc": p["arr_utc"],
                "nlegs": p["nlegs"], "aircraft": p["aircraft"],
                "days": p["days"], "legs": p["legs"],
            } for p in mine],
            "n_pairings": len(mine),
            "block_hours": _block_hours(mine),
            "duty_days": len(duty_days),
        }

    # my clearances (only mine — the full calendar stays planner-side)
    comp = qualifications.compliance(inst_id, inst)
    my_rows = [e for e in comp["employees"] if e["employee_id"] == emp_id]
    quals = {
        "month": comp["month"],
        "status": my_rows[0]["status"] if my_rows else None,
        "first_off": my_rows[0]["first_off"] if my_rows else None,
        "records": my_rows[0]["records"] if my_rows else [],
    }

    # my vacation bid + my slice of the award
    vmeta = vacation_bids.meta_for(inst_id)
    my_bid = next((b for b in (vmeta.get("bids") or [])
                   if b.get("employee_id") == emp_id), None)
    award = vmeta.get("award")
    my_award = None
    if isinstance(award, dict):
        g = next((x for x in award.get("granted", [])
                  if x.get("employee_id") == emp_id), None)
        d = next((x for x in award.get("denied", [])
                  if x.get("employee_id") == emp_id), None)
        if g:
            my_award = {"granted": True, "window": g.get("window"),
                        "choice_rank": g.get("choice_rank")}
        elif d:
            my_award = {"granted": False}
    lo, hi = vacation_bids._payload_date_range(inst)
    vacation = {
        "windows": (my_bid or {}).get("windows") or [],
        "award": my_award,
        "quota": vmeta.get("quota", 1),
        "month": {"from": lo.isoformat(), "to": hi.isoformat()},
        "max_windows": vacation_bids.MAX_WINDOWS_PER_BID,
        "max_window_days": vacation_bids.MAX_WINDOW_DAYS,
    }

    ack = ack_for(inst_id, emp_id)
    ack_out = {"acknowledged": False, "at": None, "stale": False}
    if ack:
        ack_out["acknowledged"] = True
        ack_out["at"] = ack.get("at")
        # roster changed since the signature the ack was given against?
        ack_out["stale"] = bool(sig) and ack.get("sig") != sig

    return {
        "available": True,
        "instance": inst_id,
        "label": inst.get("label"),
        "me": me,
        "roster_available": roster_available,
        "schedule": schedule,
        "roster_sig": sig,
        "quals": quals,
        "vacation": vacation,
        "ack": ack_out,
    }
