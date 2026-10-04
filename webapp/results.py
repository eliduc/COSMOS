"""Results aggregation from the final-report artifacts (API contract v1).

Everything here is read-only over the pipeline's own artifacts — the web
app reports what the runs produced, it never recomputes or embellishes.
Missing artifacts are returned as null sections, not fabricated.

roster.csv reconstructs the flight-level roster from the stage-2
assignment (pairings: employee + fns/active_fns) joined with the payload
flight times. A leg that rides in a pairing's fns but not in its
active_fns is a TDH deadhead (covering catalogs overlap by design).
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date, datetime, timedelta

from . import registry
from .registry import FINAL_REPORT, REPO_ROOT

# Artifact download whitelist — literal names only (contract: "no path
# traversal (name must match whitelist literally)").
#
# Security classification (audit 2026-07-15): /api/artifacts is the PUBLIC
# REFERENCE LIBRARY, not the per-tenant workspace. It is deliberately NOT
# gated per-owner like /api/results — those two routes serve different
# surfaces:
#   * /api/results/{id}  — a tenant's OWN workspace (owner-or-admin; a plain
#     user's seed-data view is 404, empty until they upload).
#   * /api/artifacts and /api/demo/*  — the published GERAD/BTS benchmark
#     corpus (public academic reference data), which onboarding shows to
#     EVERY authenticated user by design (demo.py renders these very files
#     as download links, and displays their roster/KPIs inline).
# The boundary that matters is enforced structurally here: this allowlist is
# built ONLY from constant GERAD/BTS names — it can never contain a tenant
# upload (uploads live under upload-<user-slug>--<slug> labels and per-user
# dirs, never a whitelist key), so no tenant's private data is downloadable
# through this route regardless of the caller. A positive allowlist of fixed
# names is the gate; authentication (require_identity) is still required.
_FIXED_ARTIFACTS = {
    "gerad_stage1_results.json": FINAL_REPORT,
    "gerad_coverage_check.json": FINAL_REPORT,
    "gerad_trajectory_check.json": FINAL_REPORT,
    "gerad_phase2_two_stage.json": FINAL_REPORT,
    "gerad_phase2_direct.json": FINAL_REPORT,
    "experiment-manifest.json": REPO_ROOT,
}
ARTIFACT_WHITELIST = dict(_FIXED_ARTIFACTS)
for _n in range(1, 8):
    ARTIFACT_WHITELIST[f"gerad_colgen_selected_inst{_n}.json"] = FINAL_REPORT
    ARTIFACT_WHITELIST[f"gerad_stage2_assignment_inst{_n}.json"] = FINAL_REPORT


def artifact_path(name: str):
    """Resolve a whitelisted artifact name to its path; None if not
    whitelisted or absent. Exact string match — no globbing, no paths.

    The ARTIFACT_WHITELIST comment above records why this positive allowlist
    (public GERAD/BTS reference names only) is the whole access gate: it
    cannot name a tenant upload, so /api/artifacts leaks no private data."""
    base = ARTIFACT_WHITELIST.get(name)
    if base is None:
        return None
    path = base / name
    return path if path.is_file() else None


# ----------------------------------------------------------- aggregation

def _load(path):
    return registry._load_json_cached(path)


def _stage1(inst: dict) -> dict | None:
    label = inst["label"]
    meta = _load(registry.BEST_DIR / f"meta_{label}.json")
    selected = _load(FINAL_REPORT / f"gerad_colgen_selected_{label}.json")
    if not isinstance(meta, dict) and not isinstance(selected, dict):
        return None
    meta = meta if isinstance(meta, dict) else {}
    selected = selected if isinstance(selected, dict) else {}
    covered = None
    if isinstance(selected.get("selected"), list):
        legs = set()
        for rec in selected["selected"]:
            legs.update(rec.get("active_fns", rec.get("fns", [])))
        covered = len(legs)
    return {
        "objective": meta.get("ip_objective", selected.get("ip_objective")),
        "ip_status": selected.get("ip_status"),
        "covered": covered,
        "total_legs": inst["legs"],
        "proven_impossible": meta.get("proven_impossible_legs", []),
        "source": meta.get("run_id"),
        "fingerprint": meta.get("fingerprint"),
    }


def _stage2(n: int | None, label: str | None = None) -> dict | None:
    """Journal row for the instance: numeric GERAD rows carry inst=N,
    generic (upload/bts) rows carry label=<slug> with inst null."""
    journal = _load(registry.TWO_STAGE_JOURNAL)
    row = None
    if isinstance(journal, list):
        for r in journal:            # last matching row wins (append order)
            if not isinstance(r, dict):
                continue
            if (n is not None and r.get("inst") == n) or \
                    (label is not None and r.get("label") == label):
                row = r
    if row is None:
        return None
    return {
        "result_status": row.get("result_status"),
        "stage2_score": row.get("stage2_score"),
        "pref_hits": row.get("pref_hits"),
        "pref_attainable": row.get("pref_attainable_legs"),
        "vacation_violations": row.get("vacation_violations"),
        "unassigned": row.get("unassigned"),
        "trajectory_violations": row.get("trajectory_violations"),
        "missing_legs": row.get("missing_legs"),
        "budget_s": row.get("budget_s"),
        "seed": row.get("seed"),
        "wall_s": row.get("wall_s"),
    }


def _validators(n: int | None, label: str | None = None) -> dict:
    coverage = trajectory = None
    if n is not None:
        rows = _load(FINAL_REPORT / "gerad_coverage_check.json")
        if isinstance(rows, list):
            mine = [r for r in rows
                    if isinstance(r, dict) and r.get("inst") == n]
            # The app's pipeline is the two-stage config; fall back to
            # whatever row exists so an existing verdict is never hidden.
            two_stage = [r for r in mine if r.get("config") == "two_stage"]
            picked = (two_stage or mine)
            coverage = picked[-1] if picked else None
        rows = _load(FINAL_REPORT / "gerad_trajectory_check.json")
        if isinstance(rows, list):
            mine = [r for r in rows
                    if isinstance(r, dict) and r.get("inst") == n]
            trajectory = mine[-1] if mine else None
    elif label is not None:
        # Generic instances: the stage-2 run embeds its own trajectory
        # certificate report into the assignment artifact.
        assignment = _load(
            FINAL_REPORT / f"gerad_stage2_assignment_{label}.json")
        if isinstance(assignment, dict):
            trajectory = assignment.get("trajectory_check")
    return {"coverage": coverage, "trajectory": trajectory}


def _label_artifacts(label: str) -> list[dict]:
    """Instance-specific artifacts named by label. The label comes from
    the validated registry (upload slugs are [a-z0-9-]) — never from the
    request path — so these names cannot traverse.

    Tenancy: this is only ever reached through aggregate(inst) with an
    instance the route layer already resolved via
    registry.get_instance_for(id, request.state.user), so label-named
    artifacts of other users' uploads are never listed to a caller who
    may not see the instance itself."""
    out = []
    for name in (f"gerad_colgen_selected_{label}.json",
                 f"gerad_stage2_assignment_{label}.json"):
        path = FINAL_REPORT / name
        if path.is_file():
            out.append({
                "name": name,
                "path": registry.repo_rel(path),
                "bytes": path.stat().st_size,
            })
    return out


def _artifacts(n: int | None, label: str | None = None) -> list[dict]:
    out = []
    for name in sorted(ARTIFACT_WHITELIST):
        m = re.search(r"inst([1-7])\.json$", name)
        if m and (n is None or int(m.group(1)) != n):
            continue                     # instance-specific, not this one
        path = artifact_path(name)
        if path is None:
            continue
        out.append({
            "name": name,
            "path": registry.repo_rel(path),
            "bytes": path.stat().st_size,
        })
    if n is None and label is not None:
        out.extend(_label_artifacts(label))
    return out


def _journal_metrics(row) -> dict | None:
    """Сравнимые метрики одной строки журнала stage-2 (со-локация или lane
    мультироли) для таблицы сравнения на экране «Результаты»."""
    if not isinstance(row, dict):
        return None
    return {k: row.get(k) for k in
            ("result_status", "stage2_score", "pref_hits",
             "pref_attainable_legs", "vacation_violations", "unassigned",
             "wall_s")}


def _multirole(inst: dict) -> dict | None:
    """Сводка мультироли (фаза 2b) + сравнение с со-локацией: вердикт и
    слияние из gerad_multirole_<label>.json, пер-lane и со-локационные
    метрики из журнала stage-2. None, когда мультироль не запускалась."""
    n = registry.gerad_inst_number(inst)
    if n is not None:
        return None                      # GERAD-сиды одночленные
    label = inst["label"]
    art = _load(FINAL_REPORT / f"gerad_multirole_{label}.json")
    if not isinstance(art, dict):
        return None
    journal = _load(FINAL_REPORT / "gerad_phase2_two_stage.json")
    rows = {r.get("label"): r for r in (journal or [])
            if isinstance(r, dict) and r.get("label")}
    lanes = {}
    lanes_raw = art.get("lanes")
    if not isinstance(lanes_raw, dict):
        lanes_raw = {}                   # битый артефакт не роняет /api/results
    for role, rec in lanes_raw.items():
        if not isinstance(rec, dict):
            continue
        metrics = _journal_metrics(rows.get(rec.get("lane")))
        # Свежесть (ревью 2026-07-19, medium): упавший lane НЕ переписывает
        # свою журнальную строку — метрики предыдущего успешного прогона были
        # бы показаны под свежим INFEASIBLE-вердиктом как «OK». Расхождение
        # вердиктов = метрики устарели -> не отдаём их вовсе.
        if (metrics and metrics.get("result_status")
                != rec.get("result_status")):
            metrics = None
        lanes[role] = {
            "result_status": rec.get("result_status"),
            "metrics": metrics,
        }
    roles = art.get("roles")
    return {
        "result_status": art.get("result_status"),
        "roles": roles if isinstance(roles, list) else [],
        "lanes": lanes,
        "merged": art.get("merged") or {},
        "mode": art.get("mode", "independent"),
        "checked_at": art.get("checked_at"),
        # со-локация фазы 2 = обычный stage-2 этой же метки (если гонялся)
        "colocation": _journal_metrics(rows.get(label)),
    }


def aggregate(inst: dict) -> dict:
    """Aggregated results for an instance the caller is allowed to see
    (the route layer resolves it via registry.get_instance_for, so a caller
    who may not see the instance never reaches here)."""
    n = registry.gerad_inst_number(inst)
    label = inst["label"] if n is None else None
    from .roster_board import roster_source
    return {
        "stage1": _stage1(inst),
        "stage2": _stage2(n, label),
        "validators": _validators(n, label),
        "artifacts": _artifacts(n, label),
        "multirole": _multirole(inst),
        # Аудит-2 (P1-06): статус источника активного ростера — режим,
        # legacy/stale-пометки, непромотированный multirole-OK.
        "active_roster": roster_source(inst),
    }


# ----------------------------------------------------------- roster.csv

CSV_COLUMNS = ["employee", "pairing_id", "leg", "role", "dep_airport",
               "dep_utc", "arr_airport", "arr_utc", "is_deadhead",
               "crew_slot_id"]


def _flights_by_identity(payload: dict, assignment: dict | None = None) -> dict:
    """Flights joined under BOTH identity namespaces (phase 3): the pipeline
    artifact references legs by flightNumber (catalog space), while payloads
    that materialize flightLegId are ALSO resolvable by it. For payloads
    without flightLegId this is exactly the old flightNumber dict.

    Пакет B: артефакт может ссылаться на wire-имена re-key (F100@1) — его
    штамп wire_map доиндексирует их на те же рейсы payload'а."""
    flights: dict = {}
    for f in payload.get("flights", []):
        flights[f["flightNumber"]] = f
        lid = f.get("flightLegId")
        if lid:
            flights.setdefault(lid, f)
    for wfn, rec in ((assignment or {}).get("wire_map") or {}).items():
        if not isinstance(rec, dict):
            continue
        leg = flights.get(rec.get("flightLegId")) \
            or flights.get(rec.get("flightNumber"))
        if leg is not None:
            flights.setdefault(wfn, leg)
    return flights


def _pairing_seats(pairing: dict, role_by_emp: dict) -> list:
    """Crew seats of one stage-2 pairing record as (employee, role, ordinal):
    the phase-2 `crew` array when present (role = the seat's requiredSkill),
    else the legacy scalar employee (role = the employee's first skill) —
    byte-identical rows for pre-phase-2 artifacts."""
    crew = pairing.get("crew")
    if isinstance(crew, list) and crew:
        return [(c.get("employee"), c.get("requiredSkill") or "",
                 c.get("indexInFlight", i)) for i, c in enumerate(crew)]
    emp = pairing.get("employee")
    return [(emp, role_by_emp.get(emp, "") if emp else "", None)]

# CSV formula-injection guard (M14): a payload field beginning with = + - @
# (or tab/CR) is a live formula when the file is opened in Excel/Sheets. The
# endpoint prepends a UTF-8 BOM "for Excel", so neutralise such cells by
# prefixing an apostrophe — the value stays readable, the formula is inert.
_CSV_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value) -> str:
    s = "" if value is None else str(value)
    if s and s[0] in _CSV_DANGEROUS:
        return "'" + s
    return s


def build_roster_csv(inst: dict) -> str | None:
    """Flight-level roster CSV (UTF-8 BOM added by the endpoint). None when
    there is no stage-2 result for the instance (contract: only when
    result_status is present)."""
    # Через единую воронку active-roster (аудит-2 P1-06): указатель с
    # проверкой отпечатков / legacy-фолбэк / fail-closed stale.
    label = inst["label"]
    from . import active_roster
    assignment = active_roster.resolve(inst).get("assignment")
    if not isinstance(assignment, dict) or "result_status" not in assignment:
        return None
    payload = registry.load_payload(registry.instance_payload_path(inst))
    flights = _flights_by_identity(payload, assignment)
    role_by_emp = {e["id"]: (e.get("skills") or [""])[0]
                   for e in payload.get("employees", [])}

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for pairing in assignment.get("pairings", []):
        pid = pairing.get("pid", "")
        active = set(pairing.get("active_fns", pairing.get("fns", [])))
        # Phase 3: one row per crew seat per leg (co-located multi-crew);
        # single-crew artifacts keep exactly one row per leg as before.
        for emp, role, slot in _pairing_seats(pairing, role_by_emp):
            for fn in pairing.get("fns", []):
                leg = flights.get(fn)
                if leg is None:
                    # Referential break between artifact and payload — a bug
                    # upstream; surface it instead of dropping the row.
                    raise ValueError(
                        f"assignment {label}: leg '{fn}' not in payload "
                        f"flights")
                writer.writerow([_csv_safe(c) for c in (
                    emp or "",
                    pid,
                    fn,
                    role if emp else "",
                    leg["departureAirport"],
                    leg["departureUTCDateTime"],
                    leg["arrivalAirport"],
                    leg["arrivalUTCDateTime"],
                    "true" if fn not in active else "false",
                    f"{pid}#{slot}" if slot is not None else "",
                )])
    return buf.getvalue()


# --------------------------------------------------- roster visualization

_MAX_CREW_ROWS = 200
_MAX_DAYS = 62


def _dur_min(leg: dict) -> int:
    a = datetime.fromisoformat(leg["departureUTCDateTime"])
    b = datetime.fromisoformat(leg["arrivalUTCDateTime"])
    return int((b - a).total_seconds() // 60)


def _int_days(raw) -> set[int]:
    """Employee day-lists (preferredDaysOff / unavailableDays) as a set of
    day-of-month ints; tolerates ints or 'YYYY-MM-DD' strings."""
    out: set[int] = set()
    for v in (raw or []):
        try:
            out.add(int(v) if not isinstance(v, str) else
                    (int(v) if v.isdigit() else datetime.fromisoformat(v).day))
        except (ValueError, TypeError):
            continue
    return out


def _pick_pairing_samples(enriched: list[dict]) -> list[dict]:
    """Up to three representative pairings for the duty-timeline: a
    multi-day trip with deadheads, a clean multi-day trip, a short one."""
    if not enriched:
        return []
    by_span = sorted(enriched, key=lambda x: (-x["span"], -x["ndh"]))
    picks: list[dict] = []
    seen: set[str] = set()

    def take(pred):
        for p in by_span:
            if p["pid"] not in seen and pred(p):
                seen.add(p["pid"])
                picks.append(p)
                return
    take(lambda p: p["ndh"] >= 1 and p["span"] >= 2)
    take(lambda p: p["span"] >= 3 and p["ndh"] == 0)
    singles = [p for p in enriched if p["span"] == 1 and p["pid"] not in seen]
    if singles:
        s = max(singles, key=lambda p: p["nlegs"])
        seen.add(s["pid"])
        picks.append(s)
    for p in by_span:                    # backfill to 3 if categories missed
        if len(picks) >= 3:
            break
        if p["pid"] not in seen:
            seen.add(p["pid"])
            picks.append(p)
    return picks[:3]


def roster_view(inst: dict) -> dict | None:
    """Rich roster + pairing data for the «Результаты» visualization,
    built from the SAME artifacts+payload join as build_roster_csv (so it
    covers GERAD and uploads alike). None when there is no stage-2 result.

    Emits: a crew×day roster grid (state per calendar day, block hours,
    preference-off honored/violated), a coverage summary, stage-1/stage-2
    KPIs, and a few resolved pairing timelines. Purely derived from the
    artifacts — nothing recomputed or embellished."""
    label = inst["label"]
    from . import active_roster
    assignment = active_roster.resolve(inst).get("assignment")
    if not isinstance(assignment, dict) or "result_status" not in assignment:
        return None
    payload = registry.load_payload(registry.instance_payload_path(inst))
    flights = _flights_by_identity(payload, assignment)
    employees = payload.get("employees", [])
    emp_by_id = {e["id"]: e for e in employees}
    pairings = [p for p in assignment.get("pairings", [])
                if isinstance(p, dict)]

    # Calendar day columns from the actual flight dates (contiguous range).
    dates = sorted({f["departureUTCDateTime"][:10]
                    for f in flights.values()
                    if f.get("departureUTCDateTime")})
    days: list[str] = []
    if dates:
        d0 = date.fromisoformat(dates[0])
        d1 = date.fromisoformat(dates[-1])
        span_days = (d1 - d0).days + 1
        if span_days > _MAX_DAYS:
            span_days = _MAX_DAYS
        days = [(d0 + timedelta(days=i)).isoformat()
                for i in range(span_days)]

    # Per-employee assignment: pairings they hold, resolved to legs. Phase 3:
    # EVERY crew member of a co-located pairing (phase-2 crew[]) gets the
    # pairing in their row — the FO's duty days are no longer invisible.
    # Legacy scalar-employee artifacts behave exactly as before.
    by_emp: dict[str, list[dict]] = {}
    for pr in pairings:
        crew = pr.get("crew")
        ids = ([c.get("employee") for c in crew if c.get("employee")]
               if isinstance(crew, list) and crew
               else ([pr["employee"]] if pr.get("employee") else []))
        for e in dict.fromkeys(ids):        # dedup, keep order
            by_emp.setdefault(e, []).append(pr)

    n_missing = 0
    crew_rows = []
    # Roster shows the whole workforce; idle crew (no pairings) reads as an
    # all-off row, which is itself a fairness/utilization signal.
    order = sorted(employees, key=lambda e: (e.get("homeAirport") or "",
                                             -(e.get("seniority") or 0)))
    crew_truncated = len(order) > _MAX_CREW_ROWS
    for e in order[:_MAX_CREW_ROWS]:
        eid = e["id"]
        prs = by_emp.get(eid, [])
        state: dict[str, str] = {}
        day_pid: dict[str, str] = {}
        block = dead = 0
        duty_dates: set[str] = set()
        for pr in prs:
            active = set(pr.get("active_fns", pr.get("fns", [])))
            for fn in pr.get("fns", []):
                leg = flights.get(fn)
                if leg is None:
                    n_missing += 1
                    continue
                d = leg["departureUTCDateTime"][:10]
                is_active = fn in active
                if is_active:
                    state[d] = "duty"
                    duty_dates.add(d)
                    block += _dur_min(leg)
                    day_pid.setdefault(d, pr.get("pid", ""))
                else:
                    if state.get(d) != "duty":
                        state[d] = "deadhead"
                    dead += _dur_min(leg)
                    day_pid.setdefault(d, pr.get("pid", ""))
        pref_off = _int_days(e.get("preferredDaysOff"))
        unavailable = _int_days(e.get("unavailableDays"))
        cells = []
        pref_violated = 0
        for d in days:
            dd = date.fromisoformat(d).day
            s = state.get(d)
            if s is None:
                if dd in unavailable:
                    s = "vacation"
                elif dd in pref_off:
                    s = "off_pref"
                else:
                    s = "off"
            elif s == "duty" and dd in pref_off:
                pref_violated += 1
            cells.append({"d": d, "s": s, "p": day_pid.get(d)})
        crew_rows.append({
            "id": eid, "base": e.get("homeAirport"),
            "sen": e.get("seniority"), "role": (e.get("skills") or ["—"])[0],
            "reserve": bool(e.get("isReserve")),
            "block_min": block, "dead_min": dead,
            "duty_days": len(duty_dates), "trips": len(prs),
            "pref_off_total": len(pref_off),
            "pref_off_violated": pref_violated,
            "cells": cells,
        })

    # Enrich every assigned pairing (for the timeline sample picker).
    enriched = []
    for pr in pairings:
        if not pr.get("employee"):
            continue
        active = set(pr.get("active_fns", pr.get("fns", [])))
        legs = []
        for fn in pr.get("fns", []):
            leg = flights.get(fn)
            if leg is None:
                continue
            legs.append({
                "leg": fn, "dep": leg["departureAirport"],
                "dep_utc": leg["departureUTCDateTime"],
                "arr": leg["arrivalAirport"],
                "arr_utc": leg["arrivalUTCDateTime"],
                "ac": leg.get("aircraftType", ""),
                "dh": fn not in active, "min": _dur_min(leg),
            })
        if not legs:
            continue
        legs.sort(key=lambda x: x["dep_utc"])
        span = (date.fromisoformat(legs[-1]["arr_utc"][:10])
                - date.fromisoformat(legs[0]["dep_utc"][:10])).days + 1
        ndh = sum(1 for x in legs if x["dh"])
        rec = {
            "pid": pr.get("pid", ""), "emp": pr["employee"],
            "base": legs[0]["dep"], "span": span, "nlegs": len(legs),
            "ndh": ndh, "block_min": sum(x["min"] for x in legs if not x["dh"]),
            "legs": legs,
        }
        # Phase 3: surface the full co-located crew of the pairing (additive —
        # current UI ignores unknown keys; emp stays the primary for compat).
        crew = pr.get("crew")
        if isinstance(crew, list) and crew:
            rec["crew"] = [{"emp": c.get("employee"),
                            "role": c.get("requiredSkill"),
                            "slot": c.get("indexInFlight", i)}
                           for i, c in enumerate(crew)]
        enriched.append(rec)

    selected = _load(FINAL_REPORT / f"gerad_colgen_selected_{label}.json")
    selected = selected if isinstance(selected, dict) else {}
    n = registry.gerad_inst_number(inst)
    s2 = _stage2(n, None if n is not None else label) or {}
    hard = soft = None
    score = assignment.get("stage2_score") or ""
    ms = re.match(r"(-?\d+)hard/(-?\d+)soft", score)
    if ms:
        hard, soft = int(ms.group(1)), int(ms.group(2))
    total_legs = inst.get("legs")
    missing = s2.get("missing_legs")
    bases = selected.get("bases") or sorted(
        {e.get("homeAirport") for e in employees if e.get("homeAirport")})

    return {
        "inst": n, "label": label,
        "meta": {
            "legs": total_legs, "pairings": len(pairings),
            "crew": len(by_emp), "bases": bases,
            "days": days, "wall_s": s2.get("wall_s"),
            "crew_truncated": crew_truncated,
            "unresolved_legs": n_missing,
        },
        "stage1": {
            "ip_objective": selected.get("ip_objective"),
            "ip_status": selected.get("ip_status"),
            "overcovered_legs": selected.get("overcovered_legs"),
        },
        "stage2": {
            "hard": hard, "soft": soft,
            "result_status": assignment.get("result_status"),
            "unassigned": s2.get("unassigned"),
            "pref_hits": s2.get("pref_hits"),
            "pref_attainable": s2.get("pref_attainable"),
            "vacation_violations": s2.get("vacation_violations"),
            "trajectory_violations": s2.get("trajectory_violations"),
            "missing_legs": missing,
        },
        "coverage": {
            "total_legs": total_legs, "missing": missing,
            "over": selected.get("overcovered_legs"),
        },
        "crew": crew_rows,
        "pairings_sample": _pick_pairing_samples(enriched),
    }
