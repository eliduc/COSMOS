"""Multiprocess pricing for _gerad_colgen: the DFS over start legs is
embarrassingly parallel, so chunk the starts across workers (32-core box;
depth-14..18 base-anchored passes are 150s+ single-threaded on inst1 and
would be hours on inst7).

Soundness notes:
  - each worker runs the same extend() DFS with its own local top-K heap;
    the parent merges heaps and takes the global top-K. A worker's pruning
    threshold is only ever LOOSER than the global one => no valid column
    is pruned that the single-threaded pass would keep;
  - workers do NOT receive `seen` (it grows to 100k+ tuples; shipping it
    every pass costs more than the duplicates do) — they dedup against an
    empty set and the parent filters merged columns against the real one;
  - exhausted = all workers exhausted (deadline hit in any chunk makes the
    pass "capped", exactly like the single-threaded semantics).

Windows spawn: workers re-import _gerad_colgen and rebuild legs/by_dep once
per pool via the initializer, then receive only (duals, chunk, seen, ...)
per task.
"""
from __future__ import annotations

import heapq
import itertools

import _pricing_certificate as _pc

_G = {}


def init_worker(inst, payload_path, profile, legacy_duty, max_legs,
                rule_overrides=None):
    import _gerad_colgen as gc
    gc.apply_profile(profile)
    if legacy_duty:
        gc.DUTY_RULE_ON = False
    if max_legs:
        gc.MAX_LEGS = max_legs
    # Пределы главного процесса (правила источника поверх профиля) —
    # иначе воркер прайсит по другой постановке (аудит R10, отсрочка №1).
    for k, v in (rule_overrides or {}).items():
        setattr(gc, k, v)
    legs, _, _wm = gc.load_legs(inst, payload_path)
    from collections import defaultdict
    by_dep = {}
    grouped = defaultdict(list)
    for leg in legs:
        grouped[leg.dep].append(leg)
    for code, ls in grouped.items():
        ls.sort(key=lambda l: l.dep_min)
        by_dep[code] = ([l.dep_min for l in ls], ls)
    _G["gc"] = gc
    _G["legs"] = legs
    _G["by_dep"] = by_dep
    _G["min_dur"] = min(l.dur for l in legs)


def run_chunk(task):
    duals, start_idxs, eps, top_k, deadline = task
    gc = _G["gc"]
    legs = _G["legs"]
    for l in legs:
        l.dual = duals[l.idx]
    starts = [legs[i] for i in start_idxs]
    return gc.price_pass(legs, _G["by_dep"], _G["min_dur"],
                         set(), eps, top_k, deadline, starts=starts)


_PASS_NO = [0]


def price_pass_mp(pool, legs, starts, seen, eps, top_k, deadline, workers):
    """Parent-side dispatch. `pool` is a live ProcessPoolExecutor whose
    workers were initialized with init_worker."""
    duals = [l.dual for l in legs]
    idxs = [l.idx for l in starts]
    # Rotate the start order between passes: capped passes cut every chunk
    # at its deadline, so a FIXED order starves the tail starts forever and
    # the master can stall on artificial slack for legs only reachable from
    # them (seen on inst3: LP frozen near 100k x slack for many passes).
    _PASS_NO[0] += 1
    r = (_PASS_NO[0] * 7919) % len(idxs) if idxs else 0
    idxs = idxs[r:] + idxs[:r]
    # Interleave starts across chunks so expensive regions spread out.
    chunks = [idxs[w::workers] for w in range(workers) if idxs[w::workers]]
    # Per-worker top-K cap: with each worker returning the full global K,
    # the parent merge (24 x 60k pickled tuples + sort) cost more than the
    # pricing itself near convergence (inst4: 669s pass at 300s cap).
    worker_k = max(4000, top_k // max(1, workers))
    tasks = [(duals, ch, eps, worker_k, deadline) for ch in chunks]
    merged = []
    sinks: list = []
    deadline_hit = False
    for res in pool.map(run_chunk, tasks):
        deadline_hit = deadline_hit or res.deadline_hit
        sinks.extend(res.sinks)
        merged.extend(res.columns)
    # Parent-side dedup vs the real `seen`, then global top-K by reduced
    # cost recomputed against current duals.
    fn_dual = {l.fn: l.dual for l in legs}
    tiebreak = itertools.count()
    scored = []
    local = set()
    for cost, fns in merged:
        if fns in seen or fns in local:
            continue
        local.add(fns)
        rc = cost - sum(fn_dual[fn] for fn in fns)
        scored.append((rc, next(tiebreak), cost, fns))
    scored.sort()
    if len(scored) > top_k:
        top_sink = _pc.Truncations(_pc.SCOPE_PRICING)
        top_sink.record("mp_top_k", top_k, len(scored))
        sinks.append(top_sink)
    return _pc.PricingResult(
        columns=tuple((cost, fns) for _, _, cost, fns in scored[:top_k]),
        sinks=tuple(sinks), problem_fingerprint="",
        deadline_hit=deadline_hit)
