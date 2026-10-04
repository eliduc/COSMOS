"""Phase B of the pay-and-credit gap (#1, P0): re-select the stage-1
master over a STORED column pool with CREDIT costs, and compare against
the canonical duty-proxy selection — both evaluated by the same exact
credit metric.

Honest formulation (the gap analysis warned that pay-and-credit is
piecewise and non-separable, so this is a documented approximation):

  - selection-time cost of a column = its credit AS IF every leg were
    flown (an upper bound: deadhead ownership is only decided after
    selection). CP-SAT solves min-credit SET COVER over the whole pool;
  - the SELECTED set is then post-processed exactly like the canonical
    selector: each leg gets ONE active owner, deterministic cheapest-
    column-first (here by credit), the other coverers ride TDH;
  - the EXACT credit of a selection = sum of pairing_credit over its
    columns with their assigned active legs. The canonical catalog is
    evaluated with its OWN stored active_fns — nothing is recomputed for
    it, nothing is mutated: this runner writes a comparison artifact
    (final-report/pay_reselect_<label>.json) and never touches the
    canonical catalog or the pipeline.

Pools exist for inst5-7 (final-report/best/gerad_best_pool_*.json.gz);
inst1-4 pools were not persisted, so Phase B covers the three largest
seeds.

Usage:  python _pay_reselect.py --label inst5 [--time-limit 180]
"""
from __future__ import annotations

import argparse
import gzip
import json
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
import _data_paths
FINAL = _data_paths.results_dir(create=False)
BEST = FINAL / "best"

PAYLOADS = {f"inst{n}": REPO_ROOT / "data" / "gerad" / "payloads"
            / f"inst{n}_p2.json" for n in range(1, 8)}


def _load_pool(label: str) -> dict:
    for p in (BEST / f"gerad_best_pool_{label}.json.gz",
              FINAL / f"gerad_pool_{label}.json.gz"):
        if p.exists():
            with gzip.open(p, "rt", encoding="utf-8") as f:
                return json.load(f)
    raise SystemExit(f"no stored pool for {label} — Phase B needs one")


def _load_catalog(label: str) -> dict:
    for p in (BEST / f"gerad_best_catalog_{label}.json",
              FINAL / f"gerad_colgen_selected_{label}.json"):
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise SystemExit(f"no catalog for {label}")


def _exact_credit(columns, by_fn, cfg, rest_min, pairing_credit):
    """Exact credit of a selection: columns = [{'fns': [...],
    'active_fns': [...]}]. Returns (credit_h, block_h, dh_h). A PRESENT
    but empty active list means a fully-deadhead column — it must NOT
    fall back to all-flown (audit 2026-07-15)."""
    credit = block = dh = 0.0
    for col in columns:
        legs = [by_fn[fn] for fn in col["fns"]]
        active = (col["fns"] if col.get("active_fns") is None
                  else col["active_fns"])
        c = pairing_credit(legs, cfg, rest_min, active_fns=active)
        credit += c["credit_h"]
        block += c["block_h"]
        dh += c["dh_h"]
    return credit, block, dh


def _assign_ownership(cols_fns, credit_key):
    """The ONE ownership rule used for BOTH sides of the comparison:
    deterministic cheapest-credit-first (audit 2026-07-15 — evaluating the
    canon with its stored duty-cost-first ownership while the credit side
    got credit-first ownership biased the headline savings by ~16%).
    cols_fns: list of leg lists; credit_key(i) -> sort key."""
    order = sorted(range(len(cols_fns)),
                   key=lambda i: (credit_key(i), tuple(cols_fns[i])))
    owner: dict[str, int] = {}
    for i in order:
        for fn in cols_fns[i]:
            owner.setdefault(fn, i)
    return [{"fns": list(cols_fns[i]),
             "active_fns": [fn for fn in cols_fns[i]
                            if owner.get(fn) == i]}
            for i in range(len(cols_fns))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--time-limit", type=float, default=180.0)
    args = ap.parse_args()
    label = args.label

    from webapp.pay_credit import (load_pay_config, min_rest_minutes,
                                   pairing_credit)
    from ortools.sat.python import cp_model

    payload = json.loads(PAYLOADS[label].read_text(encoding="utf-8"))
    by_fn = {f["flightNumber"]: f for f in payload["flights"]}
    cfg = load_pay_config()
    rest_min = min_rest_minutes()

    pool = _load_pool(label)
    catalog = _load_catalog(label)
    canon_cols = catalog["selected"]

    # Candidate set = stored pool UNION the canonical catalog's columns
    # (the promoted catalog may contain columns the last stored pool lacks);
    # dedup by leg tuple, duty-proxy cost carried where known.
    seen: dict[tuple, int] = {}
    columns: list[list[str]] = []
    duty_costs: list[int | None] = []
    for fns, cost in zip(pool["columns"], pool["costs"]):
        key = tuple(fns)
        if key not in seen:
            seen[key] = len(columns)
            columns.append(list(fns))
            duty_costs.append(cost)
    extra = 0
    for col in canon_cols:
        key = tuple(col["fns"])
        if key not in seen:
            seen[key] = len(columns)
            columns.append(list(col["fns"]))
            duty_costs.append(col.get("cost"))
            extra += 1
    print(f"{label}: pool {len(pool['columns'])} columns "
          f"+ {extra} catalog-only -> {len(columns)} candidates", flush=True)

    # --- selection-time credit (all legs flown: upper bound), centihours
    t0 = time.time()
    credit_cents = []
    for fns in columns:
        legs = [by_fn[fn] for fn in fns]
        c = pairing_credit(legs, cfg, rest_min)          # all active
        credit_cents.append(int(round(c["credit_h"] * 100)))
    print(f"  credit costing: {time.time() - t0:.1f}s", flush=True)

    # --- CP-SAT min-credit set cover
    model = cp_model.CpModel()
    x = [model.NewBoolVar(f"x{i}") for i in range(len(columns))]
    covers: dict[str, list] = {fn: [] for fn in by_fn}
    for i, fns in enumerate(columns):
        for fn in fns:
            covers[fn].append(x[i])
    # Legs no candidate column carries are uncoverable for BOTH selections
    # (the anchored space has a certified handful) — excluded from the
    # cover constraints and reported, so the comparison stays fair.
    uncoverable = sorted(fn for fn, vs in covers.items() if not vs)
    if uncoverable:
        print(f"  uncoverable legs (excluded on both sides): "
              f"{len(uncoverable)}", flush=True)
    for fn, vs in covers.items():
        if vs:
            model.Add(sum(vs) >= 1)
    model.Minimize(sum(c * v for c, v in zip(credit_cents, x)))
    # WARM START from the canonical selection (all its columns are in the
    # candidate set by construction): the credit selection then starts at
    # the canon's selection-time cost and can only improve — without the
    # hint a 3-minute run left a raw incumbent WORSE than canon.
    canon_idx = {seen[tuple(c["fns"])] for c in canon_cols}
    for i, v in enumerate(x):
        model.AddHint(v, 1 if i in canon_idx else 0)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = args.time_limit
    solver.parameters.num_search_workers = 8
    t1 = time.time()
    st = solver.Solve(model)
    status = solver.StatusName(st)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise SystemExit(f"selector returned {status}")
    chosen = [i for i in range(len(columns)) if solver.Value(x[i])]
    print(f"  CP-SAT {status} in {time.time() - t1:.1f}s: "
          f"{len(chosen)} columns, bound gap "
          f"{solver.ObjectiveValue() - solver.BestObjectiveBound():.0f}c",
          flush=True)

    # --- ownership: the SAME deterministic rule on BOTH sides
    chosen_fns = [columns[i] for i in chosen]
    credit_sel = _assign_ownership(
        chosen_fns, lambda k: credit_cents[chosen[k]])
    owned = [fn for col in credit_sel for fn in col["active_fns"]]
    coverable = sorted(set(by_fn) - set(uncoverable))
    assert sorted(owned) == coverable, "ownership must partition legs"

    canon_fns = [c["fns"] for c in canon_cols]
    canon_credit = {i: int(round(pairing_credit(
        [by_fn[fn] for fn in fns], cfg, rest_min)["credit_h"] * 100))
        for i, fns in enumerate(canon_fns)}
    canon_reowned = _assign_ownership(canon_fns,
                                      lambda k: canon_credit[k])

    # --- exact credit: three evaluations
    #   canon as deployed (stored active_fns), canon re-owned by the
    #   uniform rule (isolates the ownership effect), credit selection
    cr_h, bl_h, dh_h = _exact_credit(credit_sel, by_fn, cfg, rest_min,
                                     pairing_credit)
    dep_h, dep_bl, dep_dh = _exact_credit(canon_cols, by_fn, cfg, rest_min,
                                          pairing_credit)
    ca_h, ca_bl, ca_dh = _exact_credit(canon_reowned, by_fn, cfg, rest_min,
                                       pairing_credit)
    known = [duty_costs[i] for i in chosen if duty_costs[i] is not None]
    duty_obj_credit_sel = sum(known) if len(known) == len(chosen) else None

    out = {
        "label": label,
        "computed_at": datetime.now().isoformat(timespec="seconds"),
        "pool_columns": len(pool["columns"]),
        "candidates": len(columns),
        "uncoverable_legs": len(uncoverable),
        "time_limit_s": args.time_limit,
        "selector_status": status,
        "pay_config": cfg,
        "min_rest_min": rest_min,
        "note": ("selection cost = credit with every leg flown (upper "
                 "bound); ONE ownership rule (cheapest-credit-first) is "
                 "applied to BOTH selections before the exact-credit "
                 "evaluation; the canon's as-deployed credit (its stored "
                 "duty-cost-first ownership) is reported separately so the "
                 "ownership effect is attributed, not mixed into the "
                 "re-selection savings"),
        "current": {
            "n_pairings": len(canon_cols),
            "duty_proxy_objective": catalog.get("ip_objective"),
            "credit_h": round(ca_h, 1),                # uniform ownership
            "as_deployed_credit_h": round(dep_h, 1),   # stored active_fns
            "block_h": round(ca_bl, 1),
            "deadhead_h": round(ca_dh, 1),
            "ratio": round(ca_h / ca_bl, 3) if ca_bl else None,
        },
        "credit_selected": {
            "n_pairings": len(credit_sel),
            "duty_proxy_objective": duty_obj_credit_sel,
            "credit_h": round(cr_h, 1),
            "block_h": round(bl_h, 1),
            "deadhead_h": round(dh_h, 1),
            "ratio": round(cr_h / bl_h, 3) if bl_h else None,
        },
        # attribution: re-selection under a uniform rule / pure ownership
        "savings_credit_h": round(ca_h - cr_h, 1),
        "ownership_savings_h": round(dep_h - ca_h, 1),
        "total_vs_deployed_h": round(dep_h - cr_h, 1),
    }
    dest = FINAL / f"pay_reselect_{label}.json"
    dest.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"  canon as deployed : {dep_h:.1f} credit h "
          f"(duty obj {catalog.get('ip_objective')})", flush=True)
    print(f"  canon re-owned    : {ca_h:.1f} credit h "
          f"(ownership effect {dep_h - ca_h:.1f})", flush=True)
    print(f"  credit-selected   : {cr_h:.1f} credit h "
          f"(duty obj {duty_obj_credit_sel})", flush=True)
    print(f"  SAVINGS: selection {ca_h - cr_h:.1f} h + ownership "
          f"{dep_h - ca_h:.1f} h = {dep_h - cr_h:.1f} h -> {dest}",
          flush=True)


if __name__ == "__main__":
    main()
