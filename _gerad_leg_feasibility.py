"""Exact per-leg feasibility certificates (audit P0, 2026-07-12; v2).

For every leg without a covering column in the budgeted CG pool, decide
whether ANY legal base-anchored pairing of the aligned space contains it.

v2 design (v1 burned 7 CPU-hours on one impossibility attempt): a naked
exhaustive DFS cannot certify impossibility on dense months, so the
decision is staged, each stage sound in the direction it reports:

  S1  boolean reachability over the duty graph, backward from the leg's
      duties to bases and forward to bases, inside a loose +-96h window.
      No common base  => PROVEN_IMPOSSIBLE (the loose relaxation admits
      every real pairing, so emptiness here is a strict proof).
  S2  witness DFS with a wall budget: first legal anchored pairing found
      through the leg => FEASIBLE (certificate: the leg list).
  S3  per-base tightest-times DP: latest possible chain start from base b
      into the leg's duty, earliest possible return to b after it (block
      cap and landings relaxed — both only widen the space). If for every
      (duty, base) earliest_end - latest_start > 96h
      => PROVEN_IMPOSSIBLE. Node-capped: if the cap is hit the stage
      abstains rather than claims.
  Otherwise => UNRESOLVED_BUDGET (honest: neither witness nor proof).

Usage: python _gerad_leg_feasibility.py [inst ...] [--budget S]
Output: final-report/gerad_leg_feasibility.json
"""
from __future__ import annotations

import json
import sys
import time
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import _gerad_colgen as gc  # noqa: E402
import _gerad_duty_pricing as dpx  # noqa: E402
import rules_config  # noqa: E402  (single tier-1 rules source)

BASES = {"BASE1", "BASE2", "BASE3"}
# Tier-1 limits come from rules_config via the colgen module constants
# (env CREW_RULES_CONFIG; defaults = historical values).
REST = gc.MIN_OVERNIGHT_REST_MIN
SPAN = gc.MAX_PAIRING_MIN
BLOCK = gc.MAX_FLIGHT_MINUTES  # 30h PER-PAIRING cap, not a tier-1 param
MAX_LANDINGS = rules_config.effective()["max_landings_per_duty"]
NODE_CAP = 2_000_000


class _Found(Exception):
    def __init__(self, chain):
        self.chain = chain


class _Budget(Exception):
    pass


def build_by_arr(duties):
    by_arr = {}
    grouped = defaultdict(list)
    for d in duties:
        grouped[d.arr].append(d)
    for code, ds in grouped.items():
        ds.sort(key=lambda d: d.arr_min)
        by_arr[code] = ([d.arr_min for d in ds], ds)
    return by_arr


def backward_bases(d0, by_arr, ac, lo_dep):
    """S1/S3 backward: {base: latest chain-start dep_min} over duty chains
    ending at d0's departure (predecessor p: p.arr == cur.dep,
    p.arr_min <= cur.dep_min - REST, p.dep_min >= lo_dep, same fleet).
    Iterative DFS with memo; returns (result, complete_flag)."""
    memo = {}
    nodes = 0

    def preds(cur_dep, cur_dep_min):
        arr_times, cands = by_arr.get(cur_dep, (None, None))
        if not cands:
            return []
        hi = bisect_right(arr_times, cur_dep_min - REST)
        return [p for p in cands[:hi]
                if p.dep_min >= lo_dep and p.ac == ac]

    def solve(dep, dep_min):
        nonlocal nodes
        stack = [(dep, dep_min, False)]
        while stack:
            cdep, cdep_min, expanded = stack.pop()
            key = (cdep, cdep_min)
            if key in memo and memo[key] is not None:
                continue
            ps = preds(cdep, cdep_min)
            if not expanded:
                stack.append((cdep, cdep_min, True))
                for p in ps:
                    pk = (p.dep, p.dep_min)
                    if pk not in memo:
                        memo[pk] = None
                        stack.append((p.dep, p.dep_min, False))
                        nodes += 1
                        if nodes > NODE_CAP:
                            raise _Budget()
            else:
                best = {}
                if cdep in BASES:
                    best[cdep] = cdep_min
                for p in ps:
                    sub = memo.get((p.dep, p.dep_min))
                    if sub:
                        for b, t in sub.items():
                            if t > best.get(b, -1 << 60):
                                best[b] = t
                memo[key] = best
        return memo[(dep, dep_min)]

    try:
        res = dict(solve(d0.dep, d0.dep_min))
        if d0.dep in BASES:
            res[d0.dep] = max(res.get(d0.dep, -1 << 60), d0.dep_min)
        return res, True
    except _Budget:
        return {b: 1 << 60 for b in BASES}, False   # unknown: assume best


def forward_bases(d0, by_ddep, ac, hi_arr):
    """S1/S3 forward: {base: earliest chain-end arr_min} for chains
    starting after d0 (successor v: v.dep == cur.arr,
    v.dep_min >= cur.arr_min + REST, v.arr_min <= hi_arr, same fleet)."""
    memo = {}
    nodes = 0

    def succs(cur_arr, cur_arr_min):
        dep_times, cands = by_ddep.get(cur_arr, (None, None))
        if not cands:
            return []
        lo = bisect_left(dep_times, cur_arr_min + REST)
        return [v for v in cands[lo:]
                if v.arr_min <= hi_arr and v.ac == ac]

    def solve(arr, arr_min):
        nonlocal nodes
        stack = [(arr, arr_min, False)]
        while stack:
            carr, carr_min, expanded = stack.pop()
            key = (carr, carr_min)
            if key in memo and memo[key] is not None:
                continue
            vs = succs(carr, carr_min)
            if not expanded:
                stack.append((carr, carr_min, True))
                for v in vs:
                    vk = (v.arr, v.arr_min)
                    if vk not in memo:
                        memo[vk] = None
                        stack.append((v.arr, v.arr_min, False))
                        nodes += 1
                        if nodes > NODE_CAP:
                            raise _Budget()
            else:
                best = {}
                if carr in BASES:
                    best[carr] = carr_min
                for v in vs:
                    sub = memo.get((v.arr, v.arr_min))
                    if sub:
                        for b, t in sub.items():
                            if t < best.get(b, 1 << 60):
                                best[b] = t
                memo[key] = best
        return memo[(arr, arr_min)]

    try:
        res = dict(solve(d0.arr, d0.arr_min))
        if d0.arr in BASES:
            res[d0.arr] = min(res.get(d0.arr, 1 << 60), d0.arr_min)
        return res, True
    except _Budget:
        return {b: -1 << 60 for b in BASES}, False  # unknown: assume best


def witness_dfs(leg, dl, l_duty_idx, duties, by_ddep, deadline):
    """S2: budgeted first-witness search (same semantics as the pricer)."""
    starts = sorted((d for d in duties
                     if d.dep in BASES
                     and leg.arr_min - SPAN <= d.dep_min <= leg.dep_min),
                    key=lambda d: -d.dep_min)   # closest-to-leg first
    nodes = [0]

    def extend(chain, flight_min, legs_cnt, has_l):
        nodes[0] += 1
        if nodes[0] % 4096 == 0 and time.monotonic() > deadline:
            raise _Budget()
        first, last = chain[0], chain[-1]
        if has_l and legs_cnt >= 2 and last.arr == first.dep:
            raise _Found(chain)
        span_left = SPAN - (last.arr_min - first.dep_min)
        if span_left < REST:
            return
        if not has_l and last.arr_min + REST > max(d.dep_min for d in dl):
            return
        dep_times, cands = by_ddep.get(last.arr, (None, None))
        if not cands:
            return
        lo = bisect_left(dep_times, last.arr_min + REST)
        latest = first.dep_min + SPAN
        for i in range(lo, len(cands)):
            nxt = cands[i]
            if nxt.dep_min > latest:
                break
            if nxt.ac != first.ac:
                continue
            if nxt.arr_min - first.dep_min > SPAN:
                continue
            if flight_min + nxt.flight_min > BLOCK:
                continue
            chain.append(nxt)
            extend(chain, flight_min + nxt.flight_min,
                   legs_cnt + nxt.n_legs, has_l or nxt.idx in l_duty_idx)
            chain.pop()

    try:
        for sd in starts:
            extend([sd], sd.flight_min, sd.n_legs, sd.idx in l_duty_idx)
    except _Found as f:
        return [fn for d in f.chain for fn in d.fns]
    except _Budget:
        return "BUDGET"
    return None


def certify_leg(leg, duties, by_ddep, by_arr, duty_sets, budget_s):
    dl = [d for d in duties if leg.idx in duty_sets[d.idx]]
    if not dl:
        return {"status": "PROVEN_IMPOSSIBLE", "method": "no_legal_duty"}
    l_duty_idx = {d.idx for d in dl}
    # S1 + S3 data: per containing duty, per base, tightest chain times.
    any_common, any_unknown = False, False
    windows = []
    for d in dl:
        back, cb = backward_bases(d, by_arr, d.ac, leg.arr_min - SPAN)
        fwd, cf = forward_bases(d, by_ddep, d.ac, leg.dep_min + SPAN)
        if not (cb and cf):
            any_unknown = True
        for b in set(back) & set(fwd):
            any_common = True
            windows.append(fwd[b] - back[b])   # tightest span for (d, b)
    if not any_common and not any_unknown:
        return {"status": "PROVEN_IMPOSSIBLE", "method": "no_common_base"}
    # S2: witness with budget.
    w = witness_dfs(leg, dl, l_duty_idx, duties, by_ddep,
                    time.monotonic() + budget_s)
    if isinstance(w, list):
        return {"status": "FEASIBLE_PAIRING_EXISTS", "witness_pairing": w}
    # S3 verdict: relaxed tightest spans all exceed the cap => impossible.
    if not any_unknown and windows and min(windows) > SPAN:
        return {"status": "PROVEN_IMPOSSIBLE", "method": "span_window",
                "min_relaxed_span_min": min(windows)}
    return {"status": "UNRESOLVED_BUDGET",
            "note": ("witness not found within budget; relaxed reachability "
                     "does not exclude a pairing")}


def main():
    args = [a for a in sys.argv[1:]]
    budget_s = 120.0
    if "--budget" in args:
        i = args.index("--budget")
        budget_s = float(args[i + 1])
        del args[i:i + 2]
    insts = [int(a) for a in args] or [5, 6, 7]
    # Idempotency (audit 2026-07-12): read the IMMUTABLE stage field
    # budget_missing_legs from the canonical record — never a field that a
    # later stage rewrites. Fallback to the legacy results file.
    canon_p = HERE / "final-report" / "gerad_stage1_results.json"
    canon = json.loads(canon_p.read_text()) if canon_p.exists() else {}
    legacy = json.loads(
        (HERE / "final-report" / "gerad_colgen_results_duty.json")
        .read_text())
    out = {}
    for inst in insts:
        if f"inst{inst}" in canon:
            missing = (canon[f"inst{inst}"]["initial_budget_result"]
                       ["budget_missing_legs"])
        else:
            rec = next(r for r in legacy if r["inst"] == inst)
            missing = rec.get("uncoverable_legs") or []
        if not missing:
            print(f"inst{inst}: nothing to certify", flush=True)
            continue
        t0 = time.monotonic()
        legs, _, _wm = gc.load_legs(inst)
        by_dep = {}
        grouped = defaultdict(list)
        for leg in legs:
            grouped[leg.dep].append(leg)
        for code, ls in grouped.items():
            ls.sort(key=lambda l: l.dep_min)
            by_dep[code] = ([l.dep_min for l in ls], ls)
        duties = dpx.enumerate_duties(legs, by_dep,
                                      max_landings=MAX_LANDINGS)
        by_ddep = dpx.build_duty_index(duties)
        by_arr = build_by_arr(duties)
        duty_sets = {d.idx: set(d.leg_idxs) for d in duties}
        by_fn = {l.fn: l for l in legs}
        print(f"inst{inst}: {len(duties):,} duties, {len(missing)} legs, "
              f"budget {budget_s:.0f}s/leg (setup "
              f"{time.monotonic()-t0:.1f}s)", flush=True)
        rows = {}
        for fn in missing:
            t1 = time.monotonic()
            r = certify_leg(by_fn[fn], duties, by_ddep, by_arr,
                            duty_sets, budget_s)
            r["search_s"] = round(time.monotonic() - t1, 2)
            rows[fn] = r
            tag = {"FEASIBLE_PAIRING_EXISTS": "ПОКРЫВАЕМ",
                   "PROVEN_IMPOSSIBLE": "ДОКАЗАНО НЕДОСТИЖИМ",
                   "UNRESOLVED_BUDGET": "НЕ РАЗРЕШЕНО ЗА БЮДЖЕТ"}[r["status"]]
            extra = (f" ({len(r['witness_pairing'])} плеч)"
                     if "witness_pairing" in r else
                     f" [{r.get('method', '')}]")
            print(f"  {fn}: {tag}{extra} ({r['search_s']}s)", flush=True)
        out[f"inst{inst}"] = rows
    p = HERE / "final-report" / "gerad_leg_feasibility.json"
    p.write_text(json.dumps(out, indent=1))
    cnt = defaultdict(int)
    for r in out.values():
        for v in r.values():
            cnt[v["status"]] += 1
    print(f"saved -> {p.name}: {dict(cnt)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
