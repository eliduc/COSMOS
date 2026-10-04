"""Multi-base / multi-fleet balancing and base sizing (2026-07-15).

Strategic crew-base analytics over the stage-1 pairing catalog + the crew
roster (payload employees). For every crew base it compares SUPPLY (line-crew
headcount x monthly block capacity) against DEMAND (block hours of the
pairings anchored at that base), turns the gap into a utilization figure, and
from that derives base-sizing guidance: which base is over/under-crewed and
how many crew to transfer to level utilization across the network.

Industrial context: this is the "base sizing / manpower planning" question a
carrier asks when it considers opening or closing a crew base or shifting
headcount between bases. Full manpower planning (reserve depth, training
pipeline, attrition, bidline seniority) is out of scope; this layer implements
the honest, computable core the same way the cost layer does.

Approximations (surfaced in the UI):
  - a pairing is "anchored" at the departure station of its first leg (UTC
    chronological); crew are counted at their homeAirport.
  - required crew = demand_block_h / (cap_hours x target_util) rounded up -- a
    first-order manpower floor. It ignores continuity/rest packing losses (so
    the true requirement is somewhat higher) but is monotone and comparable
    across bases, which is what base sizing needs.
  - "inter-base links" count pairings anchored at base X that also operate a
    leg touching another base Y -- a proxy for the positioning/deadhead
    exposure an open/close decision would create (the seed carries no explicit
    deadhead legs).

Config (webapp/config/base_config.json): cap_hours, target_util. PUT is
admin-only, validated loudly.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime

from .registry import (BEST_DIR, FINAL_REPORT, REPO_ROOT, get_instance,
                       instance_payload_path, load_payload, _load_json_cached)

BASE_CONFIG_PATH = REPO_ROOT / "webapp" / "config" / "base_config.json"

# cap_hours: monthly block-hour ceiling per crew (mirrors the solver's
# max_monthly_block_min = 5100 min = 85 h). target_util: the planning
# utilization a base is sized to -- you never staff a base to 100 % because
# rest/continuity and irregular ops eat the top slice.
BASE_DEFAULTS: dict = {
    "cap_hours": 85.0,
    "target_util": 0.80,
}


# Sanity ranges enforced on BOTH paths: PUT validation and disk load. A
# hand-edited/corrupted file must degrade to defaults, not 500 every report
# (audit 2026-07-15: unguarded float() on a null/string value crashed, and a
# stored 0 caused ZeroDivisionError downstream).
_RANGES = {"cap_hours": (1.0, 400.0), "target_util": (0.1, 1.0)}


def load_base_config() -> dict:
    """Stored config merged over defaults; unknown keys dropped; a value that
    is not a finite number in range falls back to its default."""
    try:
        raw = json.loads(BASE_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    cfg = dict(BASE_DEFAULTS)
    if isinstance(raw, dict):
        for k in BASE_DEFAULTS:
            v = raw.get(k)
            lo, hi = _RANGES[k]
            if isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and lo <= float(v) <= hi:      # NaN compares False
                cfg[k] = float(v)
    cfg["cap_hours"] = float(cfg["cap_hours"])
    cfg["target_util"] = float(cfg["target_util"])
    return cfg


def validate_base_config(body: dict) -> list[str]:
    """Loud field validation for PUT; returns error strings."""
    errors: list[str] = []
    if not isinstance(body, dict):
        return ["config must be a JSON object"]
    for k, (lo, hi) in _RANGES.items():
        v = body.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) \
                or not (lo <= float(v) <= hi):      # NaN compares False
            errors.append(f"{k} must be a number in [{lo:g}, {hi:g}]")
    return errors


def save_base_config(body: dict) -> dict:
    """Persist a validated config (caller checks validate_base_config first)."""
    cfg = {"cap_hours": float(body["cap_hours"]),
           "target_util": float(body["target_util"])}
    BASE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = BASE_CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(BASE_CONFIG_PATH)
    return cfg


# ---------------------------------------------------------------- resolution

def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _catalog_pairings(inst: dict) -> list[dict] | None:
    """Selected pairings for the instance's label (best catalog wins), each
    {'fns': [...]}. None when no catalog exists yet."""
    label = inst["label"]
    for p in (BEST_DIR / f"gerad_best_catalog_{label}.json",
              FINAL_REPORT / f"gerad_colgen_selected_{label}.json"):
        doc = _load_json_cached(p)
        if isinstance(doc, dict) and isinstance(doc.get("selected"), list):
            return doc["selected"]
        if isinstance(doc, list):
            return doc
    return None


def _pairing_facts(legs: list[dict]) -> dict:
    """Anchor base, block hours, distinct calendar days, fleets and the set of
    airports touched, for one pairing (legs = payload flight dicts)."""
    legs = sorted(legs, key=lambda f: f["departureUTCDateTime"])
    block_h = 0.0
    days: set[str] = set()
    fleets: set[str] = set()
    airports: set[str] = set()
    for f in legs:
        dep = _dt(f["departureUTCDateTime"])
        arr = _dt(f["arrivalUTCDateTime"])
        block_h += (arr - dep).total_seconds() / 3600
        days.add(dep.date().isoformat())
        days.add(arr.date().isoformat())
        if f.get("aircraftType"):
            fleets.add(str(f["aircraftType"]))
        airports.add(f.get("departureAirport") or "")
        airports.add(f.get("arrivalAirport") or "")
    return {
        "anchor": legs[0].get("departureAirport") or "",
        "block_h": block_h,
        "days": len(days),
        "fleets": fleets,
        "airports": airports,
    }


# ------------------------------------------------------------------- report

def instance_base_balance(inst_id: str) -> dict | None:
    """Base-balancing + sizing report for an instance's current catalog.
    None when the instance is missing; {available: False} when no catalog."""
    inst = get_instance(inst_id)
    if inst is None:
        return None
    pairings = _catalog_pairings(inst)
    if not pairings:
        return {"available": False, "reason": "no_catalog"}
    cfg = load_base_config()
    cap = cfg["cap_hours"]
    target = cfg["target_util"]
    payload = load_payload(instance_payload_path(inst))
    from . import roster_board
    from .costing import _catalog_doc
    by_fn = roster_board.flights_by_fn(payload, _catalog_doc(inst))
    employees = payload.get("employees") or []

    # ---- supply: crew per base
    bases: dict[str, dict] = {}
    base_set: set[str] = set()
    for e in employees:
        b = e.get("homeAirport") or "(none)"
        base_set.add(b)
        s = bases.setdefault(b, {
            "base": b, "headcount": 0, "line": 0, "reserve": 0,
            "sen_sum": 0.0, "crew_fleets": Counter(),
            "demand_block_h": 0.0, "pairings": 0, "crew_days": 0,
            "demand_fleets": Counter(),
        })
        s["headcount"] += 1
        if e.get("isReserve"):
            s["reserve"] += 1
        else:
            s["line"] += 1
        s["sen_sum"] += float(e.get("seniority") or 0)
        for tr in (e.get("typeRatings") or []):
            s["crew_fleets"][str(tr)] += 1

    # ---- demand: pairings per base + inter-base links
    inter: dict[tuple[str, str], dict] = {}
    non_base_demand = {"pairings": 0, "block_h": 0.0}
    skipped = 0
    for p in pairings:
        fns = p.get("fns") or []
        legs = [by_fn[x] for x in fns if x in by_fn]
        if len(legs) < 1 or len(legs) != len(fns):
            skipped += 1
            continue
        pf = _pairing_facts(legs)
        anchor = pf["anchor"]
        if anchor not in bases:
            # a pairing anchored at a station where nobody is homed (not even
            # a reserve): it must still be crewed from somewhere -- report it
            # separately rather than silently dropping it. (An all-reserve
            # base DOES get its own row: the supply loop creates one for
            # every employee's homeAirport.)
            non_base_demand["pairings"] += 1
            non_base_demand["block_h"] += pf["block_h"]
            continue
        s = bases[anchor]
        s["demand_block_h"] += pf["block_h"]
        s["pairings"] += 1
        s["crew_days"] += pf["days"]
        for fl in pf["fleets"]:
            s["demand_fleets"][fl] += 1
        for other in (pf["airports"] & base_set) - {anchor}:
            key = (anchor, other)
            link = inter.setdefault(key, {"from": anchor, "to": other,
                                          "pairings": 0, "block_h": 0.0})
            link["pairings"] += 1
            link["block_h"] += pf["block_h"]

    # ---- per-base sizing
    rows = []
    for b, s in bases.items():
        demand = s["demand_block_h"]
        line = s["line"]
        capacity = line * cap
        # status is derived from the SAME rounded util the row reports, so a
        # base can never display util 1.0 with a contradictory status
        # (audit 2026-07-15).
        util = (round(demand / capacity, 3) if capacity > 0 else None)
        required = math.ceil(demand / (cap * target)) if demand > 0 else 0
        surplus = line - required
        if demand > 0 and line == 0:
            status = "no_crew"
        elif line > 0 and demand == 0:
            status = "idle"
        elif util is not None and util > 1.0:
            status = "over"          # cannot be flown by current crew
        elif surplus > 0:
            status = "surplus"
        elif surplus < 0:
            status = "deficit"
        else:
            status = "balanced"
        rows.append({
            "base": b,
            "headcount": s["headcount"], "line": line,
            "reserve": s["reserve"],
            "avg_seniority": round(s["sen_sum"] / s["headcount"], 1)
            if s["headcount"] else None,
            "crew_fleets": [f for f, _ in s["crew_fleets"].most_common()],
            "demand_block_h": round(demand, 1),
            "pairings": s["pairings"], "crew_days": s["crew_days"],
            "demand_fleets": [f for f, _ in s["demand_fleets"].most_common()],
            "capacity_h": round(capacity, 1),
            "util": util,
            "required": required, "surplus": surplus, "status": status,
        })
    rows.sort(key=lambda r: -r["demand_block_h"])

    # ---- fleet summary (multi-fleet view)
    fleet_demand: Counter = Counter()
    fleet_qual: Counter = Counter()
    fleet_bases: dict[str, set] = {}
    for s in bases.values():
        for fl, n in s["demand_fleets"].items():
            fleet_demand[fl] += n
            fleet_bases.setdefault(fl, set()).add(s["base"])
        for fl, n in s["crew_fleets"].items():
            fleet_qual[fl] += n
    fleet_summary = [{
        "fleet": fl, "pairings": fleet_demand.get(fl, 0),
        "qualified_crew": fleet_qual.get(fl, 0),
        "bases": sorted(fleet_bases.get(fl, set())),
    } for fl in sorted(set(fleet_demand) | set(fleet_qual))]

    # ---- recommendations: transfers surplus -> deficit
    # "over" bases are always in the deficit pool already: util > 1 forces
    # required > line, i.e. surplus < 0 (target_util <= 1.0 by validation).
    surplus_pool = sorted(
        [[r["base"], r["surplus"], r] for r in rows if r["surplus"] > 0],
        key=lambda x: -x[1])
    deficit_pool = sorted(
        [[r["base"], -r["surplus"], r] for r in rows if r["surplus"] < 0],
        key=lambda x: -x[1])

    transfers = []
    si = 0
    for dst_base, need, dst in deficit_pool:
        remaining = need
        dst_fleet = dst["demand_fleets"][0] if dst["demand_fleets"] else None
        while remaining > 0 and si < len(surplus_pool):
            src_base, avail, src = surplus_pool[si]
            if avail <= 0:
                si += 1
                continue
            move = min(remaining, avail)
            cross = bool(dst_fleet) and dst_fleet not in src["crew_fleets"]
            transfers.append({
                "from": src_base, "to": dst_base, "crew": move,
                "fleet": dst_fleet,
                "requires_cross_training": cross,
            })
            surplus_pool[si][1] -= move
            remaining -= move
            if surplus_pool[si][1] <= 0:
                si += 1

    flags = []
    for r in rows:
        if r["status"] == "no_crew":
            flags.append({"base": r["base"], "kind": "no_crew"})
        elif r["status"] == "idle":
            flags.append({"base": r["base"], "kind": "idle"})
        elif r["status"] == "over":
            flags.append({"base": r["base"], "kind": "over"})
    if non_base_demand["pairings"]:
        flags.append({"base": None, "kind": "non_base",
                      "pairings": non_base_demand["pairings"],
                      "block_h": round(non_base_demand["block_h"], 1)})

    total_line = sum(r["line"] for r in rows)
    total_required = sum(r["required"] for r in rows)

    # balance score: 100 = perfectly level utilization among worked bases.
    # With fewer than two worked bases there is no dispersion to measure --
    # report None rather than a flattering 100 (audit 2026-07-15).
    utils = [r["util"] for r in rows
             if r["util"] is not None and r["demand_block_h"] > 0]
    if len(utils) >= 2:
        mean = sum(utils) / len(utils)
        if mean > 0:
            var = sum((u - mean) ** 2 for u in utils) / len(utils)
            cv = math.sqrt(var) / mean
            balance_score = max(0, round(100 * (1 - cv)))
        else:
            balance_score = 100
    else:
        balance_score = None

    return {
        "available": True,
        "instance": inst_id,
        "label": inst["label"],
        "cap_hours": cap,
        "target_util": target,
        "bases": rows,
        "fleet_summary": fleet_summary,
        "inter_base": sorted(
            ({"from": v["from"], "to": v["to"], "pairings": v["pairings"],
              "block_h": round(v["block_h"], 1)} for v in inter.values()),
            key=lambda x: -x["pairings"]),
        "recommendations": {
            "understaffed": total_required > total_line,
            "total_line": total_line,
            "total_required": total_required,
            "transfers": transfers,
            "flags": flags,
            "balance_score": balance_score,
        },
        "skipped": skipped,
        "note_approx": True,
    }
