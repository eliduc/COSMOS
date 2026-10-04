"""Vacation bidding (2026-07-15, gap item #14).

The industrial "vacation bid award" flow: crew submit ranked candidate
vacation windows; a per-day quota caps how many may be off at once; the
award solves a CP-SAT assignment that maximizes seniority-weighted granted
bids subject to that quota; the winning windows are baked into each
employee's unavailableDays (a HARD roster constraint — "G2P Unavailable",
verified 2026-07-15) in a DERIVED instance the regular stage-2 roster then
runs on unchanged.

Model note: the award MAXIMIZES total granted vacation weighted by RAW
seniority priority (1001 - seniority, distinct per seniority; lower seniority
number = more senior), with the bid's own window rank as a strictly-dominated
tie-break — one seniority unit outranks any window-preference gap, so on a
contested day the most senior bidder wins. It is a seniority-weighted optimum
under the daily quota (which also counts pre-existing unavailableDays), not a
strict lexicographic walk across days. On the GERAD seeds every employee
carries the default seniority 500 (flat), so the demo generator assigns
distinct seniority to make the priority effect visible.

Storage: webapp/config/vacation_bids.json  {base_inst_id: meta}
Derived roster-ready instances are ordinary uploads (upload_target), so the
Data/Compute/Results tabs and costing/KPI all work on them unchanged.
"""
from __future__ import annotations

import json
import secrets
import threading
from datetime import date, datetime, timedelta

from . import registry

BIDS_PATH = registry.REPO_ROOT / "webapp" / "config" / "vacation_bids.json"

_lock = threading.Lock()

# Guardrails on user-supplied bid data (bound filenames, solver size, abuse).
MAX_BIDS = 400            # >= largest seed headcount (inst7 = 305)
MAX_WINDOWS_PER_BID = 5
MAX_WINDOW_DAYS = 31      # a vacation window is at most a month
DEFAULT_SENIORITY = 500


class VacationBidError(ValueError):
    """User-facing bidding failure."""


def _priority(raw) -> int:
    """Award priority from raw seniority (lower seniority number = more
    senior). Returns 1..1000, DISTINCT per distinct seniority — the award
    must honor real seniority order, not a coarse 10-band weight (a band
    would collapse many crew to one weight and lose priority; audit
    2026-07-15). seniority 1 -> 1000 (top), 1000 -> 1."""
    try:
        s = max(1, min(1000, int(raw)))
    except (TypeError, ValueError):
        s = DEFAULT_SENIORITY
    return 1001 - s


# --------------------------------------------------------------- storage

def _load() -> dict:
    try:
        raw = json.loads(BIDS_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"vacation_bids store unreadable: {exc}") from exc


def _save_locked(data: dict) -> None:
    BIDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = BIDS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(BIDS_PATH)


def meta_for(inst_id: str) -> dict:
    """Bidding record for an instance (quota + bids + last award), or a
    fresh empty record."""
    m = _load().get(inst_id)
    if not isinstance(m, dict):
        return {"quota": 1, "bids": [], "award": None,
                "derived_instance": None}
    m.setdefault("quota", 1)
    m.setdefault("bids", [])
    m.setdefault("award", None)
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


def _employee_seniority(inst: dict) -> dict[str, int]:
    return {str(e.get("id")): e.get("seniority", DEFAULT_SENIORITY)
            for e in _payload_employees(inst) if e.get("id")}


def _parse_day(s) -> date:
    if not isinstance(s, str):
        raise VacationBidError(f"date must be an ISO string, got {s!r}")
    try:
        return date.fromisoformat(s)
    except ValueError as exc:
        raise VacationBidError(f"bad ISO date {s!r}: {exc}") from exc


def _window_days(win) -> list[date]:
    """Expand an inclusive [start, end] window into a list of days."""
    if not (isinstance(win, (list, tuple)) and len(win) == 2):
        raise VacationBidError("each window must be [startISO, endISO]")
    start, end = _parse_day(win[0]), _parse_day(win[1])
    if end < start:
        raise VacationBidError(f"window end {end} precedes start {start}")
    span = (end - start).days + 1
    if span > MAX_WINDOW_DAYS:
        raise VacationBidError(
            f"window {start}..{end} spans {span} days (max {MAX_WINDOW_DAYS})")
    return [start + timedelta(days=i) for i in range(span)]


def validate_bids(bids, known_ids: set[str]) -> list[dict]:
    """Normalize + validate a bid list. Raises VacationBidError. Returns the
    cleaned bids (employee_id, seniority, windows as [startISO, endISO])."""
    if not isinstance(bids, list):
        raise VacationBidError("'bids' must be a list")
    if len(bids) > MAX_BIDS:
        raise VacationBidError(f"too many bids ({len(bids)} > {MAX_BIDS})")
    seen: set[str] = set()
    out: list[dict] = []
    for b in bids:
        if not isinstance(b, dict):
            raise VacationBidError("each bid must be an object")
        emp = str(b.get("employee_id") or "").strip()
        if not emp:
            raise VacationBidError("bid missing employee_id")
        if known_ids and emp not in known_ids:
            raise VacationBidError(f"unknown employee_id {emp!r}")
        if emp in seen:
            raise VacationBidError(f"duplicate bid for {emp!r}")
        seen.add(emp)
        wins = b.get("windows") or []
        if not isinstance(wins, list) or not wins:
            raise VacationBidError(f"bid {emp}: needs >= 1 window")
        if len(wins) > MAX_WINDOWS_PER_BID:
            raise VacationBidError(
                f"bid {emp}: too many windows (max {MAX_WINDOWS_PER_BID})")
        norm_wins = []
        for w in wins:
            _window_days(w)                    # validation only
            norm_wins.append([str(w[0]), str(w[1])])
        sen = b.get("seniority", DEFAULT_SENIORITY)
        try:
            sen = int(sen)
        except (TypeError, ValueError):
            raise VacationBidError(f"bid {emp}: seniority must be an integer")
        out.append({"employee_id": emp, "seniority": sen,
                    "windows": norm_wins})
    return out


# --------------------------------------------------------------- commands

def set_quota(inst_id: str, quota, email: str | None = None) -> dict:
    try:
        q = int(quota)
    except (TypeError, ValueError):
        raise VacationBidError("quota must be an integer")
    if q < 0:
        raise VacationBidError("quota must be >= 0")
    meta = meta_for(inst_id)
    meta["quota"] = q
    meta["award"] = None          # quota change invalidates a prior award
    if email:
        meta.setdefault("owner", email)
    _store(inst_id, meta)
    return meta


def submit_bids(inst_id: str, inst: dict, bids, email: str | None = None) -> dict:
    known = set(_employee_seniority(inst))
    clean = validate_bids(bids, known)
    # Fill missing seniority from the payload so the award reflects real HR
    # seniority when the bid did not carry one.
    sen_map = _employee_seniority(inst)
    for b in clean:
        if b["seniority"] == DEFAULT_SENIORITY and \
                b["employee_id"] in sen_map:
            b["seniority"] = int(sen_map[b["employee_id"]] or DEFAULT_SENIORITY)
    meta = meta_for(inst_id)
    meta["bids"] = clean
    meta["award"] = None          # bids changed -> stale award
    if email:
        meta.setdefault("owner", email)
    _store(inst_id, meta)
    return meta


def award(inst_id: str, inst: dict) -> dict:
    """Solve the seniority-weighted vacation award under the daily quota.
    Returns (and caches) {status, objective, quota, granted, denied,
    per_day_load}."""
    meta = meta_for(inst_id)
    bids = meta.get("bids") or []
    quota = int(meta.get("quota", 1))
    if not bids:
        raise VacationBidError("no bids to award — submit bids first")

    try:
        from ortools.sat.python import cp_model
    except Exception as exc:                      # noqa: BLE001
        raise VacationBidError(
            f"CP-SAT solver (ortools) unavailable: {exc}") from exc

    model = cp_model.CpModel()
    # x[(bi, wi)] = window wi of bid bi is granted.
    x: dict[tuple[int, int], object] = {}
    bid_days: dict[tuple[int, int], list[date]] = {}
    for bi, b in enumerate(bids):
        for wi, w in enumerate(b["windows"]):
            x[(bi, wi)] = model.NewBoolVar(f"x_{bi}_{wi}")
            bid_days[(bi, wi)] = _window_days(w)

    # At most one window granted per bid.
    for bi, b in enumerate(bids):
        model.Add(sum(x[(bi, wi)] for wi in range(len(b["windows"]))) <= 1)

    # Baseline: employees already off (existing unavailableDays) count
    # against the daily quota — otherwise "max off per day" is silently
    # exceeded on real crew with standing leave, or when re-bidding on a
    # derived instance that already baked in a prior award (audit 2026-07-15).
    baseline: dict[str, int] = {}
    for e in _payload_employees(inst):
        for d in (e.get("unavailableDays") or []):
            baseline[str(d)] = baseline.get(str(d), 0) + 1

    # Daily quota: at most `quota` employees off on any single day, counting
    # the baseline. A day already at/over quota admits no new grants.
    by_day: dict[date, list[object]] = {}
    for key, days in bid_days.items():
        for d in days:
            by_day.setdefault(d, []).append(x[key])
    for d, vars_ in by_day.items():
        cap = quota - baseline.get(d.isoformat(), 0)
        if cap <= 0:
            for v in vars_:
                model.Add(v == 0)
        else:
            model.Add(sum(vars_) <= cap)

    # Objective: seniority priority (raw, distinct per seniority) STRICTLY
    # dominates the window-rank tie-break — one seniority unit outranks any
    # window-preference gap. The rank term is (MAX_WINDOWS_PER_BID - wi),
    # independent of how many windows a bid listed, so padding alternatives
    # cannot inflate a bid's first-choice claim (audit 2026-07-15).
    rank_scale = MAX_WINDOWS_PER_BID + 1
    terms = []
    for bi, b in enumerate(bids):
        p = _priority(b["seniority"])
        for wi in range(len(b["windows"])):
            terms.append(x[(bi, wi)]
                         * (p * rank_scale + (MAX_WINDOWS_PER_BID - wi)))
    model.Maximize(sum(terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10.0
    solver.parameters.num_search_workers = 1      # reproducible award
    solver.parameters.random_seed = 0
    status = solver.Solve(model)
    status_name = solver.StatusName(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # A pure assignment with a <= quota is always feasible (grant
        # nothing), so this only happens on solver error — surface honestly.
        raise VacationBidError(f"award solver returned {status_name}")

    granted, denied = [], []
    day_load: dict[str, int] = {}
    for bi, b in enumerate(bids):
        chosen = None
        for wi in range(len(b["windows"])):
            if solver.Value(x[(bi, wi)]):
                chosen = wi
                break
        if chosen is None:
            denied.append({"employee_id": b["employee_id"],
                           "seniority": b["seniority"]})
            continue
        days = [d.isoformat() for d in bid_days[(bi, chosen)]]
        for d in days:
            day_load[d] = day_load.get(d, 0) + 1
        granted.append({
            "employee_id": b["employee_id"],
            "seniority": b["seniority"],
            "window": list(b["windows"][chosen]),
            "choice_rank": chosen + 1,
            "days": days,
        })

    # Per-day load includes the baseline so the "over quota" signal is
    # honest: a day shows every employee off that day, bidders or not.
    total_load = dict(day_load)
    for d, c in baseline.items():
        if d in total_load:
            total_load[d] += c
    result = {
        "status": status_name,
        "objective": int(solver.ObjectiveValue()),
        "quota": quota,
        "bids": len(bids),
        "baseline_off_days": len(baseline),
        "granted": granted,
        "denied": denied,
        "per_day_load": dict(sorted(total_load.items())),
        "computed_at": datetime.now().isoformat(timespec="seconds"),
    }
    meta["award"] = result
    _store(inst_id, meta)
    return result


def _remove_derived(derived_id: str) -> None:
    """Delete a derived instance's payload + ownership + scope (best effort)."""
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


def apply_award(inst_id: str, inst: dict, email: str) -> dict:
    """Bake the awarded vacations into unavailableDays of a DERIVED instance
    (a fresh upload the roster runs on unchanged). Returns the new instance
    dict. Requires a computed award. Re-applying replaces the prior derived
    instance (no orphan leak; audit 2026-07-15)."""
    meta = meta_for(inst_id)
    result = meta.get("award")
    if not result or not result.get("granted"):
        raise VacationBidError("no award to apply — run the award first")

    # awarded ISO days per employee
    awarded: dict[str, set[str]] = {}
    for g in result["granted"]:
        awarded.setdefault(g["employee_id"], set()).update(g["days"])

    # Deep clone the base payload (load_payload returns a SHARED cached dict —
    # never mutate it in place, or the base instance's cache is corrupted).
    import copy
    payload = copy.deepcopy(
        registry.load_payload(registry.instance_payload_path(inst)))
    for e in payload.get("employees") or []:
        eid = str(e.get("id"))
        if eid in awarded:
            existing = set(e.get("unavailableDays") or [])
            e["unavailableDays"] = sorted(existing | awarded[eid])

    base_slug = registry.slugify_or_hash(inst.get("label") or inst["id"])
    slug = f"vacation-{base_slug}-{secrets.token_hex(3)}"
    new_id, _label, target = registry.upload_target(email, slug)
    registry.write_payload(target, payload, "vacation_award")
    registry.set_owner(new_id, email)
    registry.set_llm_scope(new_id, inst.get("llm_scope", "local-only"))

    # Replace (not orphan) any previously derived instance.
    prior = meta.get("derived_instance")
    if prior and prior != new_id:
        _remove_derived(prior)
    meta["derived_instance"] = new_id
    _store(inst_id, meta)
    return {"id": new_id, "granted_employees": len(awarded),
            "granted_days": sum(len(v) for v in awarded.values())}


def demo_bids(inst: dict, want: int = 12) -> list[dict]:
    """Generate a readable demo bidding round: the first `want` employees get
    DISTINCT seniority (1..N, so priority is visible on the flat GERAD seeds)
    and two overlapping candidate windows drawn around the payload's own date
    range, so the quota actually binds. Deterministic (indexed, not random)."""
    emps = _payload_employees(inst)
    if not emps:
        raise VacationBidError("instance has no employees")
    lo, hi = _payload_date_range(inst)
    total_days = (hi - lo).days + 1
    dur = min(7, max(1, total_days // 4))
    n = min(want, len(emps))
    bids = []
    for i in range(n):
        emp = emps[i]
        # popular window everyone contends for (start near the first third)
        a0 = lo + timedelta(days=min(total_days - dur,
                                     total_days // 3))
        # alternate second choice staggered by employee so denials have a
        # fallback the solver can actually place
        b0 = lo + timedelta(days=min(total_days - dur,
                                     (i * 2) % max(1, total_days - dur)))
        w1 = [a0.isoformat(), (a0 + timedelta(days=dur - 1)).isoformat()]
        w2 = [b0.isoformat(), (b0 + timedelta(days=dur - 1)).isoformat()]
        bids.append({"employee_id": str(emp.get("id")),
                     "seniority": i + 1,          # 1 = most senior
                     "windows": [w1, w2]})
    return bids


def _payload_date_range(inst: dict) -> tuple[date, date]:
    """Min/max calendar date across the payload's flights (the planning
    month). Falls back to January 2000 if flights lack timestamps."""
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


def clear(inst_id: str, email: str, is_admin: bool) -> bool:
    """Owner-or-admin removal of a bidding round + its derived instance's
    payload. False when nothing there / not yours."""
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
