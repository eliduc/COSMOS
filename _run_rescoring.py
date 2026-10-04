"""Re-scoring: the honest single-stage vs two-stage comparison (open Q #1).

Solves BOTH approaches cold on the pinned BTS day, expands the two-stage
(pairing) roster onto REAL flights, and evaluates both rosters with the
ORIGINAL constraint set via the new score-only endpoint. This removes the
per-slot normalization problem: one scale, one rule set.

Steps:
  1. Stage 1 pinned (num_workers=1) -> synthetic schedule + pairing map.
  2. Solve all_at_once  (LA, seed 11, budget)          -> solved_full.json
  3. Solve ensemble on synthetic (4 styles x seeds)    -> solved_synth.json
     (retry once with another seed base if it lands in the plateau mode)
  4. Expand crew->pairing into crew->real-flight and fill the ORIGINAL
     6 824-slot roster (skill-wise, ordered zip). Count coverage/drops.
  5. POST both rosters to /schedules/score:
       - solved_full re-score MUST equal its solve score (sanity)
       - expanded roster score = the headline number
  6. Python hard-violation breakdown of the expanded roster (attribute
     which ORIGINAL rules the pairing-style roster breaks, e.g. min-rest
     between legs of one duty day).

Usage:  python _run_rescoring.py [budget_s]    (default 1200)
"""
from __future__ import annotations

import copy
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx  # noqa: E402

from flight_crew_scheduling.benchmark import KOTLIN_SOLVER_JAR  # noqa: E402
from flight_crew_scheduling.pairing_generator import enumerate_legal_pairings  # noqa: E402
from flight_crew_scheduling.pairing_selector import (  # noqa: E402
    select_pairings_set_partitioning,
)
from flight_crew_scheduling.pairing_to_schedule import build_rostering_schedule  # noqa: E402
from _run_bts_two_stage import load_schedule, ts  # noqa: E402
from _compr_isolated import IsolatedJvmFleet, parse_score  # noqa: E402

SCHEDULE_JSON = HERE / "data" / "bts_schedules" / "DL_ATL_2024-08-15.json"
ACCEPTORS = ["LA", "DLA", "SA", "SCHC"]

# RULES v2 (duty-period-aware) — mirror of the Kotlin provider
MIN_TURNAROUND_MIN = 60
DAY_BREAK_THRESHOLD_MIN = 6 * 60
MIN_DUTY_REST_MIN = 8 * 60
MAX_DAILY_DUTY_MIN = 14 * 60
MAX_MONTHLY_MIN = 100 * 60
MAX_WEEKLY_MIN = 60 * 60


def _ref_id(v, key):
    """@JsonIdentityInfo field: full dict on first occurrence, id string after."""
    if v is None:
        return None
    return v[key] if isinstance(v, dict) else v


def count_nulls(schedule_dict) -> int:
    return sum(1 for a in schedule_dict["flightAssignments"]
               if a.get("employee") is None)


def solve_single(payload, seed, budget):
    with IsolatedJvmFleet([9091], KOTLIN_SOLVER_JAR, heap_xmx="8g") as fleet:
        res = fleet.solve_parallel(copy.deepcopy(payload), ["LA"], budget,
                                   seeds=[seed])
    return res[0]


def solve_ensemble(payload, base_seed, budget):
    """4-island ensemble with best-ever coordination — FULL solutions only.

    KEY LESSON from the smoke run: a score string like "-150hard/-396765soft"
    can describe a solution where 70% of slots are UNASSIGNED (null-employee
    entities are simply skipped by every constraint). Comparing scores of
    partially- vs fully-initialized solutions is meaningless — fewer filled
    slots = fewer penalties = "better". So the headline best is chosen only
    among FULLY-initialized (nulls == 0) results; partially-initialized ones
    are used solely as warm-starts (CH continues filling their nulls next
    interval). If no island ever completes, we extend with up to 4 extra
    intervals and otherwise abort loudly.
    """
    n_int = max(1, min(8, budget // 150))
    interval_s = budget // n_int
    max_int = n_int + 4          # extension allowance if CH needs longer
    best_p, best_dict = None, None       # FULL solutions only
    seed_dict, seed_nulls, seed_p = None, None, None  # warm-start candidate
    with IsolatedJvmFleet([9091, 9092, 9093, 9094], KOTLIN_SOLVER_JAR,
                          heap_xmx="6g") as fleet:
        current = copy.deepcopy(payload)
        k = 0
        while k < max_int:
            k += 1
            if k > n_int and best_dict is not None:
                break            # extension only needed while nothing is full
            seeds = [base_seed + 100 * k + i for i in range(len(ACCEPTORS))]
            try:
                results = fleet.solve_parallel(current, ACCEPTORS, interval_s,
                                               seeds=seeds)
            except Exception as e:  # noqa: BLE001
                print(f"    interval {k} FAILED ({str(e)[:100]})", flush=True)
                break
            for r in results:
                if not r:
                    continue
                p = parse_score(r.get("score"))
                if p is None:
                    continue
                nulls = count_nulls(r)
                if nulls == 0 and (best_p is None or p > best_p):
                    best_p, best_dict = p, r
                # warm-start: most-initialized first, then better score
                cand_key = (-nulls, p)
                if seed_dict is None or cand_key > (-seed_nulls, seed_p):
                    seed_dict, seed_nulls, seed_p = r, nulls, p
            full_str = (f"{best_p[0]}hard/{best_p[1]}soft"
                        if best_p else "none yet")
            print(f"    interval {k}/{n_int}: best FULL={full_str}; "
                  f"warm-seed nulls={seed_nulls}", flush=True)
            nxt = best_dict if best_dict is not None else seed_dict
            if nxt is not None:
                current = nxt
    return best_p, best_dict


def expand_onto_real(solved_synth: dict, pairing_flights: dict,
                     payload_full: dict):
    """Fill a copy of the ORIGINAL roster from the solved pairing roster.

    Match per real flight per requiredSkill: expanded crew (ordered by the
    synthetic indexInFlight) zipped onto real slots (ordered by
    indexInFlight). Extras dropped, unmatched real slots stay null.
    """
    # (flight_number, skill) -> ordered crew ids from the pairing roster
    per_flight_skill = defaultdict(list)
    for fa in solved_synth["flightAssignments"]:
        emp = _ref_id(fa.get("employee"), "id")
        if emp is None:
            continue
        pid = _ref_id(fa.get("flight"), "flightNumber")
        legs = pairing_flights.get(pid)
        if not legs:
            continue
        for fn in legs:
            per_flight_skill[(fn, fa["requiredSkill"])].append(
                (fa.get("indexInFlight", 0), emp))

    expanded = copy.deepcopy(payload_full)
    # real slots grouped the same way
    real_groups = defaultdict(list)
    for fa in expanded["flightAssignments"]:
        fn = _ref_id(fa.get("flight"), "flightNumber")
        real_groups[(fn, fa["requiredSkill"])].append(fa)

    filled = dropped = 0
    for key, crew_list in per_flight_skill.items():
        crew_list.sort(key=lambda t: t[0])
        slots = sorted(real_groups.get(key, []),
                       key=lambda a: a.get("indexInFlight", 0))
        for i, (_, emp_id) in enumerate(crew_list):
            if i < len(slots):
                slots[i]["employee"] = emp_id
                filled += 1
            else:
                dropped += 1
    total = len(expanded["flightAssignments"])
    return expanded, filled, dropped, total


def hard_breakdown(payload: dict):
    """Attribute hard violations of a (possibly partial) roster, mirroring
    the Kotlin provider (language approximated away; weights in names)."""
    emps = {e["id"]: e for e in payload["employees"]}
    flights = {f["flightNumber"]: f for f in payload["flights"]}
    res = defaultdict(lambda: {"n": 0, "hard": 0})
    by_emp = defaultdict(list)

    for a in payload["flightAssignments"]:
        eid = _ref_id(a.get("employee"), "id")
        if eid is None:
            continue
        e = emps[eid]
        fn = _ref_id(a.get("flight"), "flightNumber")
        f = flights.get(fn)
        if not f:
            continue
        dep = datetime.fromisoformat(f["departureUTCDateTime"])
        arr = datetime.fromisoformat(f["arrivalUTCDateTime"])
        if a["requiredSkill"] not in (e.get("skills") or []):
            res["Required skill (100)"]["n"] += 1
            res["Required skill (100)"]["hard"] += 100
        at = f.get("aircraftType")
        if at and at not in (e.get("typeRatings") or []):
            res["Type rating (50)"]["n"] += 1
            res["Type rating (50)"]["hard"] += 50
        for d in (e.get("unavailableDays") or []):
            if dep.date().isoformat() <= d <= arr.date().isoformat():
                res["Unavailable (10)"]["n"] += 1
                res["Unavailable (10)"]["hard"] += 10
                break
        by_emp[eid].append((dep, arr))

    for eid, lst in by_emp.items():
        lst.sort()
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                if lst[j][0] >= lst[i][1]:
                    break
                overlap_h = max(1, int(
                    (min(lst[i][1], lst[j][1]) - lst[j][0]).total_seconds()
                    // 3600))
                res["Flight conflict (1/h overlap)"]["n"] += 1
                res["Flight conflict (1/h overlap)"]["hard"] += overlap_h
        for i in range(len(lst) - 1):
            gap = int((lst[i + 1][0] - lst[i][1]).total_seconds() // 60)
            # RULES v2: <60min = illegal turnaround (incl. negative gaps,
            # mirroring Kotlin); 6h..8h = dead zone (must be full duty rest).
            if gap < MIN_TURNAROUND_MIN:
                res["Min turnaround (5x shortfall)"]["n"] += 1
                res["Min turnaround (5x shortfall)"]["hard"] += \
                    5 * max(1, MIN_TURNAROUND_MIN - gap)
            elif DAY_BREAK_THRESHOLD_MIN <= gap < MIN_DUTY_REST_MIN:
                res["Duty rest 6-8h dead zone (5x)"]["n"] += 1
                res["Duty rest 6-8h dead zone (5x)"]["hard"] += \
                    5 * (MIN_DUTY_REST_MIN - gap)
        byday = defaultdict(list)
        for dep, arr in lst:
            byday[dep.date()].append((dep, arr))
        for day, xs in byday.items():
            if len(xs) >= 2:
                span = int((max(x[1] for x in xs) - min(x[0] for x in xs))
                           .total_seconds() // 60)
                if span > MAX_DAILY_DUTY_MIN:
                    res["Max daily duty (5x excess)"]["n"] += 1
                    res["Max daily duty (5x excess)"]["hard"] += \
                        5 * (span - MAX_DAILY_DUTY_MIN)
        byweek = defaultdict(int)
        bymonth = defaultdict(int)
        for dep, arr in lst:
            mins = int((arr - dep).total_seconds() // 60)
            iso = dep.isocalendar()
            byweek[(iso[0], iso[1])] += mins
            bymonth[(dep.year, dep.month)] += mins
        for tot in byweek.values():
            if tot > MAX_WEEKLY_MIN:
                res["Max weekly hours (2x excess)"]["n"] += 1
                res["Max weekly hours (2x excess)"]["hard"] += 2 * (tot - MAX_WEEKLY_MIN)
        for tot in bymonth.values():
            if tot > MAX_MONTHLY_MIN:
                res["Max monthly hours (3x excess)"]["n"] += 1
                res["Max monthly hours (3x excess)"]["hard"] += 3 * (tot - MAX_MONTHLY_MIN)
    return res


def main():
    budget = int(sys.argv[1]) if len(sys.argv) > 1 else 1200

    print(f"[{ts()}] === RE-SCORING: honest single vs two-stage ===", flush=True)
    schedule = load_schedule(SCHEDULE_JSON)

    print(f"[{ts()}] Stage 1 (pinned) ...", flush=True)
    pairings = enumerate_legal_pairings(schedule.flights)
    fns = [f.flight_number for f in schedule.flights]
    selected, info = select_pairings_set_partitioning(
        fns, pairings, time_limit_seconds=120, num_workers=1)
    synthetic, pairing_map = build_rostering_schedule(
        selected, schedule.employees, schedule.airports,
        real_flights=schedule.flights)
    pairing_flights = {pid: p.flight_numbers for pid, p in pairing_map.items()}

    payload_full = schedule.model_dump(by_alias=True, mode="json")
    payload_full.pop("solverStatus", None)
    payload_synth = synthetic.model_dump(by_alias=True, mode="json")
    payload_synth.pop("solverStatus", None)
    print(f"  full={len(payload_full['flightAssignments'])} slots, "
          f"synthetic={len(payload_synth['flightAssignments'])} slots, "
          f"{len(selected)} pairings ({info['status']})", flush=True)

    print(f"\n[{ts()}] SOLVE all_at_once (LA, seed 11, {budget}s) ...", flush=True)
    solved_full = solve_single(payload_full, 11, budget)
    n_full_nulls = count_nulls(solved_full)
    print(f"  solve score: {solved_full.get('score')} "
          f"(unassigned slots: {n_full_nulls})", flush=True)
    if n_full_nulls > 0:
        print("  WARNING: single-stage solution is PARTIAL — budget too small "
              "for CH; the comparison is void at this budget.", flush=True)
    (HERE / "_rescoring_full_solved.json").write_text(
        json.dumps(solved_full), encoding="utf-8")

    print(f"\n[{ts()}] SOLVE two-stage ensemble (seeds base 1000, {budget}s, "
          f"FULL-solutions-only tracking) ...", flush=True)
    best_p, solved_synth = solve_ensemble(payload_synth, 1000, budget)
    if best_p is None or solved_synth is None:
        print("  ABORT: ensemble never produced a fully-initialized solution "
              "even with extension intervals; increase the budget.", flush=True)
        return 1
    print(f"  ensemble best FULL score (synthetic scale): "
          f"{best_p[0]}hard/{best_p[1]}soft "
          f"(unassigned: {count_nulls(solved_synth)})", flush=True)
    (HERE / "_rescoring_synth_solved.json").write_text(
        json.dumps(solved_synth), encoding="utf-8")

    print(f"\n[{ts()}] EXPAND pairing roster onto real flights ...", flush=True)
    expanded, filled, dropped, total = expand_onto_real(
        solved_synth, pairing_flights, payload_full)
    print(f"  coverage: {filled}/{total} real slots filled "
          f"({100*filled/total:.1f}%), {dropped} pairing crew dropped "
          f"(over-crewed multi-leg pairings)", flush=True)
    (HERE / "_rescoring_expanded.json").write_text(
        json.dumps(expanded), encoding="utf-8")

    print(f"\n[{ts()}] SCORE-ONLY both rosters with ORIGINAL rules ...", flush=True)
    with IsolatedJvmFleet([9099], KOTLIN_SOLVER_JAR, heap_xmx="8g") as fleet:
        with httpx.Client(timeout=600) as cli:
            r1 = cli.post("http://127.0.0.1:9099/schedules/score",
                          json=solved_full)
            r1.raise_for_status()
            rescored_full = r1.text.strip()
            r2 = cli.post("http://127.0.0.1:9099/schedules/score",
                          json=expanded)
            r2.raise_for_status()
            rescored_expanded = r2.text.strip()

    sanity = "OK" if rescored_full == str(solved_full.get("score")) \
        else f"MISMATCH ({rescored_full} vs {solved_full.get('score')})"
    print(f"  sanity all_at_once re-score: {rescored_full}  [{sanity}]",
          flush=True)
    print(f"  EXPANDED two-stage on ORIGINAL rules: {rescored_expanded}",
          flush=True)

    print(f"\n[{ts()}] HARD breakdown of expanded roster (Python mirror):",
          flush=True)
    res = hard_breakdown(expanded)
    tot = 0
    for name, d in sorted(res.items(), key=lambda kv: -kv[1]["hard"]):
        print(f"  {name:<34} matches={d['n']:>6}  hard={d['hard']:>10}",
              flush=True)
        tot += d["hard"]
    print(f"  {'TOTAL (approx, w/o language)':<34} {'':>14} hard={tot:>10}",
          flush=True)

    print(f"\n[{ts()}] === SUMMARY (single scale: ORIGINAL rules) ===", flush=True)
    print(f"  all_at_once   : {rescored_full}  (slots {total - n_full_nulls}/{total})",
          flush=True)
    print(f"  two-stage exp.: {rescored_expanded}  (slots {filled}/{total}, "
          f"dropped {dropped})", flush=True)
    print(f"[{ts()}] DONE.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
