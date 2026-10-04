"""Pay-and-credit cost model (#1, P0, 2026-07-15) — Phase A: the honest
calculator layer.

Industrial context: North American (and increasingly global) crew pay is
by CREDIT HOURS, not raw block hours. A pairing's credit is a piecewise
maximum of competing floors, negotiated per labor contract:

  duty credit  = max( block in the duty period,
                      duty span / duty_rig,        # e.g. 1:2 - 1h credit
                                                   #   per 2h on duty
                      minimum duty credit )        # e.g. 5h per duty day
  trip credit  = max( sum of duty credits,
                      TAFB / trip_rig )            # e.g. 1:3.5 away rig
  pairing pay  = trip credit x block-hour rate

and at ROSTER level every lineholder is paid at least the monthly
guarantee: paid = max(assigned credit, guarantee). The industry efficiency
metric is the credit-to-block ratio (1.00 = perfect: every paid hour is a
flown hour); the excess is "soft cost" — pay burn the optimizer should be
minimizing. This module computes all of it EXACTLY for concrete pairings
(catalog columns are concrete leg sequences, so no approximation is
needed at the reporting layer).

Duty periods are split at gaps >= the airline's min_rest_min (tier-1 rule
from webapp/config/airline_default.json) — the same boundary the legality
engine treats as a rest. Times are UTC (the FTL/acclimatization engine is
gap #2).

The roster view reuses the board's resolve+overlay path, so per-crew
credit reflects the planner's manual moves — consistent with the board
and the crew cabinet.

Phase B (separate runner): re-select the stage-1 master over the STORED
column pools with credit costs and compare against the duty-proxy
selection. Phase C (with fair-share #9): guarantee-aware spreading in
stage 2.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from datetime import datetime

from . import registry, roster_board
from .costing import _catalog_pairings, load_cost_config

# Пути установки объявлены ОДИН раз — в config_scope (P1-10, 2026-07-26);
# и договор оплаты, и правила яруса 1 этот модуль берёт через него, с
# учётом уровня экземпляра.

_lock = threading.Lock()

# Contract knobs. Rigs are stated the industry way: HOURS PER ONE CREDIT
# HOUR ("1:2 duty rig" -> duty_rig = 2.0). Ranges guard both the PUT and
# the on-disk file (fail to defaults, never 500 — the base_config lesson).
PAY_DEFAULTS: dict = {
    "duty_rig": 2.0,             # duty hours per credit hour
    "trip_rig": 3.5,             # TAFB hours per credit hour
    "min_duty_credit_h": 5.0,    # credit floor per duty period
    "monthly_guarantee_h": 75.0, # lineholder monthly pay floor
}
_RANGES = {
    "duty_rig": (1.0, 10.0),
    "trip_rig": (1.0, 10.0),
    "min_duty_credit_h": (0.0, 12.0),
    "monthly_guarantee_h": (0.0, 200.0),
}


def load_pay_config(inst=None) -> dict:
    """Договор оплаты, действующий для экземпляра (P1-10, 2026-07-26).

    Без экземпляра — договор установки, как и было."""
    from . import config_scope
    return load_pay_config_from(
        config_scope.path_for(config_scope.DOMAIN_PAY, inst))


def load_pay_config_from(path) -> dict:
    """Тот же разбор по ЯВНОМУ пути (симметрично `rules_config.load_from`).
    Значение, которое не является конечным числом в допустимых границах,
    откатывается к своему умолчанию — денежный отчёт не имеет права
    падать пятисоткой."""
    if path is None:
        raw = {}
    else:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
    cfg = dict(PAY_DEFAULTS)
    if isinstance(raw, dict):
        for k in PAY_DEFAULTS:
            v = raw.get(k)
            lo, hi = _RANGES[k]
            if isinstance(v, (int, float)) and not isinstance(v, bool) \
                    and lo <= float(v) <= hi:      # NaN compares False
                cfg[k] = float(v)
    return cfg


def validate_pay_config(body: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(body, dict):
        return ["config must be a JSON object"]
    for k, (lo, hi) in _RANGES.items():
        v = body.get(k)
        if not isinstance(v, (int, float)) or isinstance(v, bool) \
                or not (lo <= float(v) <= hi):
            errors.append(f"{k} must be a number in [{lo:g}, {hi:g}]")
    return errors


def save_pay_config(body: dict) -> dict:
    from . import config_scope
    cfg = {k: float(body[k]) for k in PAY_DEFAULTS}
    target = config_scope.install_path(config_scope.DOMAIN_PAY)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(target)
    return cfg


def min_rest_minutes(inst=None) -> int:
    """Граница отдыха яруса 1, разделяющая наряды.

    Тот же источник, который конвейер передаёт каждому этапу через
    `CREW_RULES_CONFIG`, — теперь с учётом правил ЭКЗЕМПЛЯРА (P1-10):
    кредитные часы обязаны считаться по тем же нормам, по которым построен
    ростер, иначе оплата и расписание разъезжаются молча."""
    from . import config_scope
    try:
        v = int(config_scope.effective(config_scope.DOMAIN_RULES,
                                       inst)["min_rest_min"])
        return v if v > 0 else 600
    except (OSError, ValueError, TypeError, KeyError):
        # Битый конфиг не имеет права ронять денежный отчёт пятисоткой
        # (аудит 2026-07-15): откат к умолчанию.
        return 600


# ------------------------------------------------------------- the formula

def reselect_report(inst_id: str) -> dict | None:
    """Phase B comparison artifact (written by _pay_reselect.py): the
    canonical duty-proxy selection vs the credit-cost re-selection over the
    stored pool, both by exact credit. None when the instance is missing;
    {available: False} when the experiment has not been run for its label
    (pools exist only for inst5-7)."""
    inst = registry.get_instance(inst_id)
    if inst is None:
        return None
    p = (registry.FINAL_REPORT / f"pay_reselect_{inst['label']}.json")
    doc = registry._load_json_cached(p)
    if not isinstance(doc, dict) or "credit_selected" not in doc:
        return {"available": False, "reason": "not_computed"}
    # the artifact was computed under ITS config; flag divergence from the
    # live one so the UI can mark the table stale (audit 2026-07-15)
    stale = (doc.get("pay_config") != load_pay_config(inst)
             or doc.get("min_rest_min") != min_rest_minutes(inst))
    return {"available": True, "config_stale": stale, **doc}


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _hours(a: str, b: str) -> float:
    return (_dt(b) - _dt(a)).total_seconds() / 3600.0


def split_duties(legs: list[dict], rest_min: int) -> list[list[dict]]:
    """Chronological legs -> duty periods; a gap >= rest_min starts a new
    duty. Legs are payload flight dicts (departureUTCDateTime/...)."""
    legs = sorted(legs, key=lambda f: f["departureUTCDateTime"])
    duties: list[list[dict]] = []
    cur: list[dict] = []
    for leg in legs:
        if cur:
            gap_min = (_dt(leg["departureUTCDateTime"])
                       - _dt(cur[-1]["arrivalUTCDateTime"])
                       ).total_seconds() / 60.0
            if gap_min >= rest_min:
                duties.append(cur)
                cur = []
        cur.append(leg)
    if cur:
        duties.append(cur)
    return duties


def pairing_credit(legs: list[dict], pay_cfg: dict,
                   rest_min: int, active_fns=None) -> dict:
    """Exact pay-and-credit of one concrete pairing. Returns block/tafb,
    per-duty rows, the trip credit and WHICH floor bound it — the binding
    reason is what a planner argues about, so it is first-class.

    Catalog columns overlap by design: a leg is FLOWN by one owning pairing
    (`active_fns`) and ridden as a TDH deadhead in the others. BLOCK counts
    ACTIVE legs only — a paid deadhead hour is credit, never flown block;
    duty span and TAFB count ALL legs (the crew is on duty while riding).
    Without the distinction the module's own headline metric lied ~3x on
    the seed (audit 2026-07-15). active_fns=None means every leg is flown.
    """
    active = set(active_fns) if active_fns is not None else None

    def _blk(f):
        if active is not None and f["flightNumber"] not in active:
            return 0.0
        return _hours(f["departureUTCDateTime"], f["arrivalUTCDateTime"])

    duties = split_duties(legs, rest_min)
    duty_rows = []
    duty_credit_sum = 0.0
    block_total = 0.0
    dh_total = 0.0
    for dl in duties:
        block = sum(_blk(f) for f in dl)
        dh_total += sum(_hours(f["departureUTCDateTime"],
                               f["arrivalUTCDateTime"]) for f in dl) - block
        span = _hours(dl[0]["departureUTCDateTime"],
                      dl[-1]["arrivalUTCDateTime"])
        rig = span / pay_cfg["duty_rig"] if pay_cfg["duty_rig"] > 0 else 0.0
        floor = pay_cfg["min_duty_credit_h"]
        credit = max(block, rig, floor)
        binding = ("block" if credit == block
                   else "duty_rig" if credit == rig else "min_duty")
        duty_rows.append({
            "legs": len(dl),
            "block_h": round(block, 2), "span_h": round(span, 2),
            "credit_h": round(credit, 2), "binding": binding,
        })
        duty_credit_sum += credit
        block_total += block
    all_sorted = sorted(legs, key=lambda f: f["departureUTCDateTime"])
    tafb = _hours(all_sorted[0]["departureUTCDateTime"],
                  all_sorted[-1]["arrivalUTCDateTime"])
    trip_rig_credit = (tafb / pay_cfg["trip_rig"]
                       if pay_cfg["trip_rig"] > 0 else 0.0)
    trip_credit = max(duty_credit_sum, trip_rig_credit)
    binding = ("duties" if trip_credit == duty_credit_sum else "trip_rig")
    return {
        "duties": duty_rows,
        "n_duties": len(duty_rows),
        "block_h": round(block_total, 2),
        "dh_h": round(dh_total, 2),
        "tafb_h": round(tafb, 2),
        "duty_credit_h": round(duty_credit_sum, 2),
        "trip_rig_credit_h": round(trip_rig_credit, 2),
        "credit_h": round(trip_credit, 2),
        "binding": binding,
    }


# ------------------------------------------------------------------ report

def instance_pay(inst_id: str) -> dict | None:
    """Pay-and-credit report over the instance's pairing catalog, plus the
    per-crew payroll view when a stage-2 roster exists. None when the
    instance is missing; {available: False} without a catalog."""
    inst = registry.get_instance(inst_id)
    if inst is None:
        return None
    pairings = _catalog_pairings(inst)
    if not pairings:
        return {"available": False, "reason": "no_catalog"}
    pay_cfg = load_pay_config(inst)
    cost_cfg = load_cost_config(inst)
    rate = float(cost_cfg["block_hour_rate"])
    currency = cost_cfg.get("currency") or "USD"
    rest_min = min_rest_minutes(inst)
    payload = registry.load_payload(registry.instance_payload_path(inst))
    from . import roster_board
    from .costing import _catalog_doc
    by_fn = roster_board.flights_by_fn(payload, _catalog_doc(inst))

    rows = []
    tot_block = tot_credit = tot_dh = 0.0
    bind_counts: dict[str, int] = {}
    skipped = 0
    # cache keyed by (all legs, active legs): the roster path re-costs the
    # same columns
    credit_by_key: dict[tuple, float] = {}
    for i, p in enumerate(pairings):
        fns = p.get("fns") or []
        # a PRESENT but empty active list = fully-deadhead column; only a
        # MISSING field means "all flown" (audit 2026-07-15)
        active = fns if p.get("active_fns") is None else p["active_fns"]
        legs = [by_fn[x] for x in fns if x in by_fn]
        if len(legs) < 1 or len(legs) != len(fns):
            skipped += 1
            continue
        c = pairing_credit(legs, pay_cfg, rest_min, active_fns=active)
        credit_by_key[(tuple(sorted(fns)),
                       tuple(sorted(active)))] = c["credit_h"]
        tot_block += c["block_h"]
        tot_credit += c["credit_h"]
        tot_dh += c["dh_h"]
        bind_counts[c["binding"]] = bind_counts.get(c["binding"], 0) + 1
        rows.append({
            "pairing": i + 1, "legs": len(legs),
            "n_duties": c["n_duties"],
            "block_h": c["block_h"], "dh_h": c["dh_h"],
            "tafb_h": c["tafb_h"],
            "credit_h": c["credit_h"],
            "ratio": (round(c["credit_h"] / c["block_h"], 3)
                      if c["block_h"] > 0 else None),
            "binding": c["binding"],
        })
    rows.sort(key=lambda r: -(r["credit_h"] - r["block_h"]))

    # ---- roster payroll (stage-2 assignment + board moves, if present)
    roster = None
    resolved, crew_by_id, _days = roster_board._resolve(inst)
    if resolved is not None:
        # the assignment artifact carries each pairing's ACTIVE (flown) legs
        assign_doc = roster_board._assignment(inst) or {}
        active_by_pid = {
            str(pr.get("pid") or ""): (
                (pr.get("fns") or []) if pr.get("active_fns") is None
                else pr["active_fns"])
            for pr in assign_doc.get("pairings", [])
            if isinstance(pr, dict)}
        ed = roster_board._reconcile(inst_id, {p["pid"] for p in resolved})
        moves = ed.get("moves") or {}
        per_crew: dict[str, dict] = {}
        unassigned_credit = 0.0
        for p in resolved:
            emp = (moves[p["pid"]] if p["pid"] in moves
                   else p["solver_employee"])
            all_fns = [leg["fn"] for leg in p["legs"]]
            a = active_by_pid.get(p["pid"])
            active = all_fns if a is None else a
            key = (tuple(sorted(all_fns)), tuple(sorted(active)))
            credit = credit_by_key.get(key)
            if credit is None:
                # roster pairing not in the current catalog (e.g. manual
                # artifacts drift) — cost it directly, honestly
                legs = [by_fn[fn] for fn in all_fns if fn in by_fn]
                if not legs:
                    continue
                credit = pairing_credit(legs, pay_cfg, rest_min,
                                        active_fns=active)["credit_h"]
            if not emp:
                unassigned_credit += credit
                continue
            e = per_crew.setdefault(emp, {"employee_id": emp,
                                          "credit_h": 0.0, "pairings": 0})
            e["credit_h"] += credit
            e["pairings"] += 1
        guarantee = pay_cfg["monthly_guarantee_h"]
        crew_rows = []
        payroll = burn = 0.0
        # every LINE crew member is on guarantee, flying or not — idle
        # lineholders are exactly where the guarantee burns
        for cid, c in crew_by_id.items():
            got = per_crew.get(cid, {}).get("credit_h", 0.0)
            n = per_crew.get(cid, {}).get("pairings", 0)
            paid = max(got, guarantee)
            payroll += paid
            burn += paid - got
            crew_rows.append({
                "employee_id": cid, "base": c.get("base"),
                "pairings": n,
                "credit_h": round(got, 1),
                "paid_h": round(paid, 1),
                "guarantee_burn_h": round(paid - got, 1),
            })
        crew_rows.sort(key=lambda r: -r["guarantee_burn_h"])
        roster = {
            "crew": crew_rows,
            "total_crew": len(crew_rows),
            "payroll_h": round(payroll, 1),
            "payroll_cost": round(payroll * rate, 2),
            "guarantee_burn_h": round(burn, 1),
            "guarantee_burn_cost": round(burn * rate, 2),
            "unassigned_credit_h": round(unassigned_credit, 1),
            "edited": bool(moves),
        }

    ratio = round(tot_credit / tot_block, 3) if tot_block > 0 else None
    return {
        "available": True,
        "instance": inst_id,
        "label": inst["label"],
        "currency": currency,
        "config": {**pay_cfg, "block_hour_rate": rate},
        "min_rest_min": rest_min,
        "pairings": len(rows),
        "skipped": skipped,
        "totals": {
            "block_h": round(tot_block, 1),
            "deadhead_h": round(tot_dh, 1),
            "credit_h": round(tot_credit, 1),
            "credit_to_block": ratio,
            "pay_cost": round(tot_credit * rate, 2),
            "soft_cost": round((tot_credit - tot_block) * rate, 2),
        },
        "binding_counts": bind_counts,
        "top_pairings": rows[:15],
        "roster": roster,
        "note_utc": True,
    }
