"""Read-only «Прогоны» data layer for the rich (Panel) view.

Derives normalized run records and convergence series from the EXISTING job
store (webapp/jobs/<id>.json + .log) — no new storage, no re-instrumentation
of the solver code. Metrics and per-iteration curves are parsed from the run
log with tolerant regexes; everything is best-effort (missing → None) so a
log in any shape never breaks the view.
"""
from __future__ import annotations

import re
from datetime import datetime

# Stage-1 column-generation iteration line, e.g.
#   "  it   5: LP    361963.9  +   466 cols (best rc -98536.0)  pool 5,219"
# Counters are printed WITH thousands separators (found on prod 2026-07-15:
# "pool 5,219" parsed as 5) — every numeric group must accept commas.
_IT = re.compile(
    r"^\s*it\s+(\d+):\s+LP\s+([\d,.eE+-]+)\s+\+\s+([\d,]+)\s+cols"
    r".*?pool\s+([\d,]+)",
    re.MULTILINE)
_OBJ = re.compile(r"\bobj\s*=\s*([\d,.eE+-]+)")
# Только ЯВНО валидная граница (аудит R10, P1-01): строка без «(valid)»
# — величина ограниченного пула, границей не является; немаркированная
# граница читается как несертифицированная.
_LP_BOUND = re.compile(r"LP bound\s+([\d,.eE+-]+)\s*\(valid\)")
_IP_STATUS = re.compile(r"CP-SAT:.*?\((OPTIMAL|FEASIBLE|INFEASIBLE|UNKNOWN)\)")
# Stage-2 summary lines
_RESULT = re.compile(r"result_status=([A-Z_]+)")
_UNASSIGNED = re.compile(r"\bunassigned=(\d+)")
_TRAJ = re.compile(r"trajectory_violations=(\d+)")
_MISSING = re.compile(r"missing=(\d+)")
_WALL_PAREN = re.compile(r"\((\d+)s\)\s*$", re.MULTILINE)


def _cast(raw, cast=float):
    """Parse a logged number, tolerating thousands separators."""
    try:
        return cast(str(raw).replace(",", ""))
    except (ValueError, TypeError):
        return None


def _num(m, cast=float):
    if not m:
        return None
    return _cast(m.group(1), cast)


def _last_num(pattern, log, cast=float):
    """LAST match wins: logs print running values first and the final
    summary last — the summary is the truth."""
    vals = pattern.findall(log or "")
    return _cast(vals[-1], cast) if vals else None


def _it_points(log: str) -> list[dict]:
    """Iteration points, deduplicated by iteration number (the live feed and
    the final table both print 'it N' lines — the last print wins)."""
    by_it: dict[int, dict] = {}
    for m in _IT.finditer(log or ""):
        it = _cast(m.group(1), int)
        if it is None:
            continue
        by_it[it] = {"it": it, "lp": _cast(m.group(2)),
                     "cols": _cast(m.group(3), int),
                     "pool": _cast(m.group(4), int)}
    return [by_it[k] for k in sorted(by_it)]


def _wall_from_times(started: str | None, ended: str | None):
    if not started or not ended:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return (datetime.strptime(ended, fmt)
                    - datetime.strptime(started, fmt)).total_seconds()
        except ValueError:
            continue
    return None


def parse_metrics(log: str, jtype: str | None = None) -> dict:
    """Final-metric summary parsed from a run log (best-effort)."""
    obj = _last_num(_OBJ, log)
    lp = _last_num(_LP_BOUND, log)
    gap = None
    if obj is not None and lp not in (None, 0):
        gap = round((obj - lp) / lp * 100.0, 3)
        # Base-anchored runs price artificial slack into mid-run LP values,
        # so a bound ABOVE the objective is uncertified — a negative "gap"
        # is meaningless and gets nulled (same convention as the stage-1
        # journal, audit round 5).
        if gap < 0:
            gap = None
    pts = _it_points(log or "")
    verdict = None
    m = _RESULT.search(log or "")
    if m:
        verdict = m.group(1)
    elif jtype == "stage1":
        s = _IP_STATUS.search(log or "")
        verdict = s.group(1) if s else None
    walls = _WALL_PAREN.findall(log or "")
    return {
        "objective": obj,
        "lp_bound": lp,
        "gap_pct": gap,
        "iters": (max(p["it"] for p in pts) if pts else None),
        "pool": pts[-1]["pool"] if pts else None,
        "unassigned": _num(_UNASSIGNED.search(log or ""), int),
        "trajectory_violations": _num(_TRAJ.search(log or ""), int),
        "missing_legs": _num(_MISSING.search(log or ""), int),
        "verdict": verdict,
        "wall_s": float(walls[-1]) if walls else None,
    }


def run_series(job_id: str) -> dict:
    """Convergence points for a run (currently stage-1 column generation:
    LP bound and pool size per iteration). Empty for runs without a curve."""
    from . import jobs
    pts = _it_points(jobs.read_log(job_id) or "")
    return {"kind": "stage1-cg" if pts else "none", "points": pts}


def _norm(job: dict) -> dict:
    from . import jobs
    jtype = job.get("type")
    log = jobs.read_log(job["id"]) or ""
    m = parse_metrics(log, jtype)
    wall = m["wall_s"] or _wall_from_times(job.get("started_at"),
                                           job.get("ended_at"))
    return {
        "id": job.get("id"),
        "type": jtype,
        "instance": job.get("instance_id"),
        "owner": job.get("owner"),
        "status": job.get("status"),
        "exit_code": job.get("exit_code"),
        "verdict": m["verdict"],
        "objective": m["objective"],
        "lp_bound": m["lp_bound"],
        "gap_pct": m["gap_pct"],
        "unassigned": m["unassigned"],
        "wall_s": round(wall, 1) if isinstance(wall, (int, float)) else None,
        "iters": m["iters"],
        "started_at": job.get("started_at"),
        "ended_at": job.get("ended_at"),
    }


def list_runs(owner: str | None = None) -> list[dict]:
    """Normalized run records, newest first. `owner` filters to that email
    (None = all — the caller must have already authorized the wider scope)."""
    from . import jobs
    out = []
    for job in jobs.list_jobs():
        if owner is not None and job.get("owner") != owner:
            continue
        out.append(_norm(job))
    return out
