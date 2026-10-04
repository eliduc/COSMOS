"""Read-only demo/onboarding API (API contract v1.2).

Two endpoints power the first-run experience:

  GET /api/demo/pipeline    -- replay of the full pipeline on the showcase
                               month (GERAD inst1) assembled from canonical
                               artifacts; nothing is computed on request.
  GET /api/demo/comparison  -- the "solver set vs single solver" evidence
                               and the GENCOL/Jeppesen positioning numbers.

Design rules. Any authenticated user may read these — that is the point of
onboarding, a fresh user's workspace is empty and these views must still
work. Everything is read-only aggregation over final-report artifacts; the
published reference numbers (GENCOL/Kasirzadeh, Saddoune) are constants
with their source next to them. Missing artifacts surface as nulls, never
as fabricated numbers.
"""
from __future__ import annotations

import csv
import io
import math
import statistics

from fastapi import APIRouter, HTTPException

from . import registry, results
from .registry import EVIDENCE_DIR, FINAL_REPORT

router = APIRouter(prefix="/api/demo", tags=["demo"])

# The showcase month: proven stage-1 optimum, OK stage-2, full coverage.
SHOWCASE_INSTANCE_ID = "gerad-1"

ROSTER_PREVIEW_ROWS = 8
SAMPLE_FLIGHTS = 5
SAMPLE_EMPLOYEES = 4

# Published reference numbers, quoted verbatim from the approved report
# chapter (final-report/kasirzadeh_spec.json -> published_reference_results).
# gap_pct / cpu_min / uncovered_pct: Kasirzadeh, Saddoune, Soumis 2017
# (GENCOL 4.5 + CPLEX 12.4, Intel 3.40 GHz) — solution-process and quality
# tables. pairing_cpu_min: Saddoune et al. 2013 (pairing phase CPU).
GENCOL_REFERENCE = {
    1: {"gap_pct": 0.00, "cpu_min": 0.16, "uncovered_pct": 0.00,
        "pairing_cpu_min": 2.50},
    2: {"gap_pct": 0.18, "cpu_min": 0.45, "uncovered_pct": 0.00,
        "pairing_cpu_min": 4.34},
    3: {"gap_pct": 0.02, "cpu_min": 1.81, "uncovered_pct": 0.00,
        "pairing_cpu_min": 9.14},
    4: {"gap_pct": 0.38, "cpu_min": 47.59, "uncovered_pct": 0.04,
        "pairing_cpu_min": 393.58},
    5: {"gap_pct": 2.91, "cpu_min": 149.58, "uncovered_pct": 1.81,
        "pairing_cpu_min": 67.80},
    6: {"gap_pct": 0.49, "cpu_min": 75.31, "uncovered_pct": 0.14,
        "pairing_cpu_min": 154.75},
    7: {"gap_pct": 0.37, "cpu_min": 184.76, "uncovered_pct": 0.86,
        "pairing_cpu_min": 289.22},
}

# Catalog sizes of the two public scale instances (COR@L), as reported in
# the approved summary ("каталоги в 8,1 и 12,6 миллиона маршрутов").
SCALE_CATALOG_COLS = {"a320": 8_100_000, "b727": 12_600_000}


def _load(path):
    return registry._load_json_cached(path)


# ---------------------------------------------------------------- pipeline

def _stage1_canonical(label: str) -> dict | None:
    """Canonical stage-1 numbers for a label: the completed catalog when the
    instance was extended (gerad-5..7), else the initial budget run."""
    all_recs = _load(FINAL_REPORT / "gerad_stage1_results.json")
    rec = all_recs.get(label) if isinstance(all_recs, dict) else None
    if not isinstance(rec, dict):
        return None
    best = rec.get("completed_result") or rec.get("initial_budget_result")
    if not isinstance(best, dict):
        return None
    return {
        "pool_size": best.get("pool_size"),
        "selected": best.get("selected"),
        "ip_objective": best.get("ip_objective"),
        "ip_status": best.get("ip_status"),
        # Fail-closed (аудит R10): исторические JSON могли нести число
        # при invalid — потребитель не показывает его границей.
        "lp_bound": (best.get("lp_bound")
                     if best.get("lp_bound_valid") else None),
        "lp_bound_valid": best.get("lp_bound_valid"),
        "gap_vs_lp_pct": best.get("gap_vs_lp_pct"),
        "overcovered_legs": best.get("overcovered_legs"),
        "wall_s": best.get("total_s"),
    }


def _roster_preview(inst: dict) -> dict | None:
    csv_text = results.build_roster_csv(inst)
    if csv_text is None:
        return None
    reader = csv.reader(io.StringIO(csv_text))
    rows = list(reader)
    if not rows:
        return None
    return {
        "columns": rows[0],
        "rows": rows[1:1 + ROSTER_PREVIEW_ROWS],
        "total_rows": len(rows) - 1,
    }


@router.get("/pipeline")
def demo_pipeline():
    """The full pipeline replay on the showcase instance. Read-only: every
    number comes from the same canonical artifacts the results tab reads."""
    inst = registry.get_instance(SHOWCASE_INSTANCE_ID)
    if inst is None:                       # showcase data missing on disk
        raise HTTPException(503, "showcase instance is not available: "
                                 f"{SHOWCASE_INSTANCE_ID}")
    payload = registry.load_payload(registry.instance_payload_path(inst))
    validation = registry.validate_payload(payload)

    flights = payload.get("flights") or []
    employees = payload.get("employees") or []
    flights_sample = [{
        "flightNumber": f.get("flightNumber"),
        "departureAirport": f.get("departureAirport"),
        "departureUTCDateTime": f.get("departureUTCDateTime"),
        "arrivalAirport": f.get("arrivalAirport"),
        "arrivalUTCDateTime": f.get("arrivalUTCDateTime"),
    } for f in flights[:SAMPLE_FLIGHTS]]
    employees_sample = [{
        "id": e.get("id"),
        "homeAirport": e.get("homeAirport"),
        "skills": e.get("skills"),
    } for e in employees[:SAMPLE_EMPLOYEES]]

    n = registry.gerad_inst_number(inst)
    stage2 = results._stage2(n, None if n is not None else inst["label"])
    aggregated = results.aggregate(inst)

    return {
        "instance": {
            "id": inst["id"],
            "label": inst["label"],
            "source": inst.get("source"),
            "legs": inst.get("legs"),
            "employees": inst.get("employees"),
        },
        "input": {
            "flights_total": len(flights),
            "employees_total": len(employees),
            "airports_total": len(payload.get("airports") or []),
            "flights_sample": flights_sample,
            "employees_sample": employees_sample,
        },
        "validation": validation,
        "stage1": _stage1_canonical(inst["label"]),
        "gate": None if stage2 is None else {
            "result_status": stage2.get("result_status"),
            "trajectory_violations": stage2.get("trajectory_violations"),
            "missing_legs": stage2.get("missing_legs"),
        },
        "stage2": stage2,
        "results": {
            "roster": _roster_preview(inst),
            "artifacts": aggregated.get("artifacts") or [],
        },
    }


# -------------------------------------------------------------- comparison

def _phase2_rows(name: str) -> dict[int, dict]:
    rows = _load(FINAL_REPORT / name)
    out: dict[int, dict] = {}
    if isinstance(rows, list):
        for r in rows:                  # append order: last row per inst wins
            if isinstance(r, dict) and isinstance(r.get("inst"), int):
                out[r["inst"]] = r
    return out


def _rostering_summary(row: dict | None, legs) -> dict | None:
    if row is None:
        return None
    unassigned = row.get("unassigned", row.get("missing_legs"))
    pref_hits = row.get("pref_hits")
    pref_att = row.get("pref_attainable_legs")
    return {
        "result_status": row.get("result_status"),
        "unassigned": unassigned,
        "unassigned_pct": (round(100.0 * unassigned / legs, 2)
                           if isinstance(unassigned, int)
                           and isinstance(legs, int) and legs else None),
        "pref_hits": pref_hits,
        "pref_attainable": pref_att,
        "pref_pct": (round(100.0 * pref_hits / pref_att, 1)
                     if isinstance(pref_hits, int)
                     and isinstance(pref_att, int) and pref_att else None),
        "vacation_violations": row.get("vacation_violations"),
        "wall_s": row.get("wall_s"),
    }


def _spp_summary() -> dict | None:
    """Task 1 (route selection, 29 OR-Library instances): exact solver vs
    the single generic solver, computed live from the result CSVs."""
    try:
        with open(EVIDENCE_DIR / "orlib_spp_results.csv",
                  encoding="utf-8", newline="") as f:
            ort = list(csv.DictReader(f))
        with open(EVIDENCE_DIR / "orlib_spp_timefold.csv",
                  encoding="utf-8", newline="") as f:
            tf = list(csv.DictReader(f))
    except OSError:
        return None
    optima = {r["instance"]: float(r["objective"]) for r in ort
              if r.get("status") == "OPTIMAL"}
    excesses = []
    feasible = 0
    for r in tf:
        if r.get("status") == "FEASIBLE" and r.get("hard") == "0":
            feasible += 1
            ref = optima.get(r["instance"])
            if ref:
                excesses.append(100.0 * (float(r["cost"]) - ref) / ref)
    return {
        "instances": len(ort),
        "ort": {
            "optimal": sum(r.get("status") == "OPTIMAL" for r in ort),
            "median_s": round(statistics.median(
                float(r["seconds"]) for r in ort), 1) if ort else None,
        },
        "tf": {
            "feasible": feasible,
            "median_excess_pct": (round(statistics.median(excesses), 1)
                                  if excesses else None),
        },
    }


def _scale_summary() -> dict:
    """The two public COR@L scale instances (catalogs 1-2 orders of
    magnitude beyond a GERAD month)."""
    out: dict = {}
    a320 = _load(EVIDENCE_DIR / "coral_a320_engines.json")
    sift = _load(EVIDENCE_DIR / "coral_a320_sifting.json")
    cert = _load(EVIDENCE_DIR / "coral_a320_bp_certificate.json")
    if isinstance(a320, list) and isinstance(sift, dict):
        # LP baselines that actually reached optimality: HiGHS-LP
        # ("HighsModelStatus.kOptimal") and OR-Tools-GLOP ("OPTIMAL").
        # No engine-name filtering — that would drop GLOP (362.6 s, the
        # fastest solved baseline) and inflate the speedup claim beyond
        # the approved report's 60-85x range.
        solved = [e for e in a320
                  if isinstance(e.get("obj"), (int, float))
                  and math.isfinite(e["obj"]) and e["obj"] > 0
                  and ("Optimal" in str(e.get("status"))
                       or e.get("status") == "OPTIMAL")]
        baseline_s = [e["solve_s"] for e in solved
                      if isinstance(e.get("solve_s"), (int, float))]
        out["a320"] = {
            "catalog_cols": SCALE_CATALOG_COLS["a320"],
            "sifting_s": sift.get("total_s"),
            "sifting_working_cols": sift.get("working_cols"),
            "lp_matches_published": sift.get("match"),
            "baseline_lp_s_min": min(baseline_s) if baseline_s else None,
            "baseline_lp_s_max": max(baseline_s) if baseline_s else None,
            "bp": None if not isinstance(cert, dict) else {
                "status": cert.get("status"),
                "objective": cert.get("objective"),
                "gap_pct": cert.get("gap_pct"),
                "partition_ok": cert.get("partition_ok"),
            },
        }
    b727 = _load(EVIDENCE_DIR / "coral727_engines.json")
    sift727 = _load(EVIDENCE_DIR / "coral727_sifting.json")
    if isinstance(b727, list):
        failed = sum(1 for e in b727
                     if not isinstance(e.get("obj"), (int, float))
                     or not e.get("obj"))
        out["b727"] = {
            "catalog_cols": SCALE_CATALOG_COLS["b727"],
            "baselines_total": len(b727),
            "baselines_failed": failed,
            "sifting_s": (sift727.get("total_s")
                          if isinstance(sift727, dict) else None),
            "sifting_working_cols": (sift727.get("working_cols")
                                     if isinstance(sift727, dict) else None),
        }
    return out


@router.get("/comparison")
def demo_comparison():
    """Solver-set-vs-single-solver evidence + GENCOL positioning, from the
    same artifacts the approved report is built on."""
    stage1_all = _load(FINAL_REPORT / "gerad_stage1_results.json")
    two_stage = _phase2_rows("gerad_phase2_two_stage.json")
    direct = _phase2_rows("gerad_phase2_direct.json")

    gerad = []
    for n in range(1, 8):
        label = f"inst{n}"
        # legs comes from the phase-2 journal — independent of the
        # stage-1 artifact, so a missing stage-1 file must not null it.
        legs = (two_stage.get(n) or {}).get("legs")
        gerad.append({
            "inst": n,
            "legs": legs,
            "stage1": _stage1_canonical(label),
            "gencol": GENCOL_REFERENCE[n],
            "rostering": {
                "two_stage": _rostering_summary(two_stage.get(n), legs),
                "direct": _rostering_summary(direct.get(n), legs),
            },
        })

    return {
        "gerad": gerad,
        "spp": _spp_summary(),
        "scale": _scale_summary(),
        "reference_note": {
            "gencol": "Kasirzadeh/Saddoune/Soumis 2017 (GENCOL 4.5, CPLEX "
                      "12.4, Intel 3.40 GHz); pairing_cpu_min: Saddoune et "
                      "al. 2013",
        },
    }
