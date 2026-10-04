"""Catalog completion v3 — MONOTONIC (audit 2026-07-12, round 5).

v2's flaw: a fresh CG pool is not a superset of previous pools, CP-SAT
started cold, and any FEASIBLE unconditionally overwrote the previous
(better) catalog — a quality regression of +1.4..+5.4%.

v3 rules:
  - the column universe is the UNION of: previous published catalog(s),
    the full persisted pool(s), and certified witness columns;
  - the best previous incumbent is passed to CP-SAT via AddHint;
  - the result NEVER overwrites a better previous incumbent (monotonic
    gate) — if the new solve is worse, the previous catalog stands and
    only provenance is updated;
  - provenance (sources, incumbent history) is recorded in the canonical
    record.
"""
from __future__ import annotations

import gzip
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import _gerad_colgen as gc  # noqa: E402
from ortools.sat.python import cp_model  # noqa: E402


def load_catalog_columns(path: Path, pool: dict, by_fn) -> list:
    """Merge a selection dump's columns into the pool; return its column
    list (for hinting)."""
    if not path.exists():
        return []
    d = json.loads(path.read_text())
    cols = []
    for rec in d["selected"]:
        fns = tuple(rec["fns"])
        cost = rec.get("cost")
        if cost is None:
            span = by_fn[fns[-1]].arr_min - by_fn[fns[0]].dep_min
            cost = gc.pairing_cost(span, len(fns))
        pool.setdefault(fns, int(cost))
        cols.append(fns)
    return cols


def complete(inst: int, ip_budget: float = 900.0) -> None:
    fr = HERE / "final-report"
    canon_p = fr / "gerad_stage1_results.json"
    canon = json.loads(canon_p.read_text())
    rec = canon.get(f"inst{inst}") or {}
    init = rec.get("initial_budget_result") or {}
    cert = rec.get("feasibility_certification") or {}

    legs, _, _wm = gc.load_legs(inst)
    by_fn = {l.fn: l for l in legs}
    pool: dict = {}
    sources = []

    # 1. Full persisted pool(s): the canonical pool, the durable best pool,
    # and every immutable run pool (audit round 6: historical pools are
    # merged, not discarded — the union only grows).
    def merge_pool_gz(path: Path):
        if not path.exists():
            return
        with gzip.open(path, "rt", encoding="utf-8") as gz:
            pd = json.load(gz)
        n_new = 0
        for c, cost in zip(pd["columns"], pd["costs"]):
            k = tuple(c)
            if k not in pool:
                pool[k] = int(cost)
                n_new += 1
        sources.append(f"{path.name} ({len(pd['columns']):,} cols, "
                       f"{n_new:,} new)")

    pool_name = init.get("pool_dump")
    if pool_name:
        merge_pool_gz(fr / pool_name)
    merge_pool_gz(fr / "best" / f"gerad_best_pool_inst{inst}.json.gz")
    for run_pool in sorted(
            (fr / "runs" / "stage1").glob(f"*/gerad_pool_inst{inst}.json.gz")):
        merge_pool_gz(run_pool)

    # 2. Previous published catalogs: Package B restore, the durable best
    # and the current canonical.
    prev_hints = {}
    for tag, path in [
            ("packageB", fr / f"catalog_b_inst{inst}.json"),
            ("best", fr / "best" / f"gerad_best_catalog_inst{inst}.json"),
            ("current", fr / f"gerad_colgen_selected_inst{inst}.json")]:
        cols = load_catalog_columns(path, pool, by_fn)
        if cols:
            obj = sum(pool[c] for c in cols)
            prev_hints[tag] = (obj, cols)
            sources.append(f"{path.name} (incumbent {obj})")

    # 3. Certified witnesses.
    impossible, unresolved = [], []
    for fn, v in cert.items():
        if v["status"] == "FEASIBLE_PAIRING_EXISTS":
            w = tuple(v["witness_pairing"])
            if w not in pool:
                span = by_fn[w[-1]].arr_min - by_fn[w[0]].dep_min
                pool[w] = gc.pairing_cost(span, len(w))
        elif v["status"] == "PROVEN_IMPOSSIBLE":
            impossible.append(fn)
        else:
            unresolved.append(fn)

    excluded = set(impossible) | set(unresolved)
    must = [l.fn for l in legs if l.fn not in excluded]

    # Verify previous incumbents remain feasible covers of `must`; the
    # best feasible one becomes the hint and the monotonic baseline.
    best_prev_obj, best_prev_cols, best_prev_tag = None, None, None
    for tag, (obj, cols) in prev_hints.items():
        covered = set()
        for c in cols:
            covered.update(c)
        if all(fn in covered for fn in must):
            if best_prev_obj is None or obj < best_prev_obj:
                best_prev_obj, best_prev_cols, best_prev_tag = obj, cols, tag

    plist = list(pool.items())
    idx_of = {fns: i for i, (fns, _) in enumerate(plist)}
    model = cp_model.CpModel()
    xs = [model.NewBoolVar(f"x{i}") for i in range(len(plist))]
    model.Minimize(sum(c * x for (_, c), x in zip(plist, xs)))
    rows = defaultdict(list)
    for i, (fns, _) in enumerate(plist):
        for fn in fns:
            rows[fn].append(i)
    for fn in must:
        idxs = rows.get(fn)
        assert idxs, f"leg {fn} has no column in the merged universe"
        model.Add(sum(xs[i] for i in idxs) >= 1)
    if best_prev_cols is not None:
        chosen = {idx_of[c] for c in best_prev_cols}
        for i in range(len(plist)):
            model.AddHint(xs[i], 1 if i in chosen else 0)
    sat = cp_model.CpSolver()
    sat.parameters.max_time_in_seconds = float(ip_budget)
    sat.parameters.num_search_workers = 8
    sat.parameters.random_seed = 0
    t0 = time.monotonic()
    st = sat.Solve(model)
    status = sat.StatusName(st)
    new_obj = (int(sat.ObjectiveValue())
               if status in ("OPTIMAL", "FEASIBLE") else None)

    # Monotonic gate.
    if (new_obj is not None
            and (best_prev_obj is None or new_obj <= best_prev_obj)):
        sel = [plist[i][0] for i in range(len(plist)) if sat.Value(xs[i])]
        obj, chosen_src = new_obj, f"solver ({status})"
    else:
        assert best_prev_cols is not None, "no feasible incumbent at all"
        sel, obj = best_prev_cols, best_prev_obj
        chosen_src = f"previous incumbent kept ({best_prev_tag}); solver " \
                     f"got {new_obj}"
    cover = Counter(fn for fns in sel for fn in fns)
    assert all(cover.get(fn, 0) >= 1 for fn in must)
    over = {fn: c for fn, c in cover.items() if c > 1}

    owner = {}
    for fns in sorted(sel, key=lambda f: (pool[f], f)):
        for fn in fns:
            owner.setdefault(fn, fns)
    dump_p = fr / f"gerad_colgen_selected_inst{inst}.json"
    fingerprint = gc.space_fingerprint(
        legs, {"bases": (rec.get("space") or {}).get("bases"),
               "pricer": (rec.get("space") or {}).get("pricer"),
               "max_legs": "natural",
               "ip_model": (rec.get("space") or {}).get("ip_model")})
    dump = {"inst": inst, "label": f"inst{inst}", "profile": "gerad",
            "bases": (rec.get("space") or {}).get("bases"),
            "source": f"monotonic completion v3: {chosen_src}",
            "provenance_sources": sources,
            "fingerprint": fingerprint,
            "ip_status": f"{status}+MONOTONIC",
            "ip_objective": obj,
            "overcovered_legs": len(over),
            "proven_impossible_legs": impossible,
            "unresolved_legs": unresolved,
            "selected": [{"fns": list(fns),
                          "active_fns": [fn for fn in fns
                                         if owner[fn] == fns],
                          "cost": pool[fns]} for fns in sel]}
    dump_p.write_text(json.dumps(dump))
    # Durable best promotion (audit round 6): a completed catalog covers
    # everything achievable and is monotonically no worse than every
    # previous incumbent (they are all in its universe + no-worse gate),
    # so it always qualifies.
    best_dir = fr / "best"
    best_dir.mkdir(exist_ok=True)
    (best_dir / f"gerad_best_catalog_inst{inst}.json").write_text(
        json.dumps(dump))
    (best_dir / f"meta_inst{inst}.json").write_text(json.dumps({
        "label": f"inst{inst}", "ip_objective": obj,
        "fingerprint": fingerprint,
        "covered_legs_semantics": "all except proven-impossible",
        "proven_impossible_legs": impossible,
        "run_id": f"completion-{time.strftime('%Y%m%d-%H%M%S')}",
        "promoted_at": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=1))

    rec["completed_result"] = {
        "ip_objective": obj,
        "ip_status": f"{status}+MONOTONIC",
        "chosen": chosen_src,
        "previous_incumbent": best_prev_obj,
        "solver_objective": new_obj,
        "universe_columns": len(plist),
        "provenance_sources": sources,
        "selected": len(sel),
        "overcovered_legs": len(over),
        "covered_legs": len(must), "total_legs": len(legs),
        "proven_impossible_legs": impossible,
        "unresolved_legs": unresolved,
        "ip_seconds": round(time.monotonic() - t0, 1)}
    canon_all = json.loads(canon_p.read_text())
    canon_all[f"inst{inst}"] = rec
    tmp = canon_p.with_suffix(f".tmpc{inst}")
    tmp.write_text(json.dumps(canon_all, indent=1))
    tmp.replace(canon_p)
    # Per-instance canonical source of truth (audit 2026-07-12, round 5):
    # stage1/instN.json is what aggregate_stage1() rebuilds the merged
    # file from — keep it in sync with every completion write.
    inst_dir = canon_p.parent / "stage1"
    inst_dir.mkdir(exist_ok=True)
    tmp2 = inst_dir / f"inst{inst}.tmp"
    tmp2.write_text(json.dumps(canon_all[f"inst{inst}"], indent=1))
    tmp2.replace(inst_dir / f"inst{inst}.json")
    print(f"inst{inst}: universe {len(plist):,} колонок из "
          f"{len(sources)} источников; прежний инкумбент {best_prev_obj} "
          f"({best_prev_tag}); солвер {new_obj} ({status}); ИТОГ {obj} "
          f"[{chosen_src}]; покрыто {len(must)}/{len(legs)}, "
          f"перекрытий {len(over)}, {time.monotonic()-t0:.0f}s", flush=True)


def main():
    insts = [int(a) for a in sys.argv[1:]] or [5, 6, 7]
    for inst in insts:
        complete(inst)
    return 0


if __name__ == "__main__":
    sys.exit(main())
