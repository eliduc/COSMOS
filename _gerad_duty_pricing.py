"""Duty-network pricing for _gerad_colgen — exhaustive, proof-grade.

Why: the naive leg-level DFS cannot certify LP bounds on the big months
(inst4: a full refinement sweep exceeds 900s; the mid-chain branch bound
is too weak at depth 10-18). The GERAD rules impose a two-level
structure — legs form DUTIES (sits in [MIN_SIT, REST), span <= 14 h),
duties form PAIRINGS (rests >= 10 h between duties, total span <= 96 h,
block <= 30 h, closed at the base). Enumerate duties ONCE, then price
chains OVER DUTIES: recursion depth <= ~8 with duty-level pruning makes
every pass exhaustive — the CG bound proof becomes routine. This is the
same architecture as GENCOL/the thesis (pairings = sequences of duties).

Space equivalence with the leg DFS (when max_landings is None): any
leg-legal pairing decomposes uniquely into duties at its >= REST gaps;
inter-duty gaps here are >= REST by construction and within-duty sits are
< REST, so chains over duties are exactly the leg-legal pairings.
Cross-check: on inst1, bases mode, 14-leg cap, max_landings=None this
pricer must reproduce the leg-DFS proven LP bound 114372.0.

Rule 6 (max 5 landings per duty) — the old cap-5 leg spaces satisfied it
trivially; the corrected deep space must enforce it explicitly, which the
duty enumeration does naturally (battery default max_landings=5).
"""
from __future__ import annotations

import heapq
import itertools
import time
from bisect import bisect_left
from collections import defaultdict

import _gerad_colgen as gc
import _pricing_certificate as _pc


class Duty:
    __slots__ = ("idx", "leg_idxs", "fns", "dep", "arr", "dep_min",
                 "arr_min", "flight_min", "n_legs", "ac", "dual")

    def __init__(self, idx, legs):
        self.idx = idx
        self.leg_idxs = tuple(l.idx for l in legs)
        self.fns = tuple(l.fn for l in legs)
        self.dep = legs[0].dep
        self.arr = legs[-1].arr
        self.dep_min = legs[0].dep_min
        self.arr_min = legs[-1].arr_min
        self.flight_min = sum(l.dur for l in legs)
        self.n_legs = len(legs)
        self.ac = legs[0].ac
        self.dual = 0.0


class _TimeUp(Exception):
    pass


class _StartDone(Exception):
    pass


def enumerate_duties(legs, by_dep, max_landings=5):
    """All legal duties (sit-connected leg runs), including sub-runs: a
    pairing may end its duty before the aircraft's next connection."""
    duties = []
    # A sit keeps the duty going only below this; anything >= it is either
    # a rest (>= REST) or, under v2, an illegal dead-zone gap — in both
    # cases the duty ends, and since candidates are sorted by departure we
    # can stop scanning.
    sit_limit = (gc.DAY_BREAK_THRESHOLD_MIN if gc.DEAD_ZONE_ON
                 else gc.MIN_OVERNIGHT_REST_MIN)

    def extend(chain, flight_min):
        duties.append(Duty(len(duties), chain))
        if max_landings is not None and len(chain) >= max_landings:
            return
        first, last = chain[0], chain[-1]
        dep_times, cands = by_dep.get(last.arr, (None, None))
        if not cands:
            return
        lo = bisect_left(dep_times, last.arr_min + gc.MIN_GROUND_TIME_MIN)
        for i in range(lo, len(cands)):
            nxt = cands[i]
            gap = nxt.dep_min - last.arr_min
            if gap >= sit_limit:
                break
            if nxt.ac != first.ac:
                continue
            if nxt.arr_min - first.dep_min > gc.MAX_DUTY_SPAN_MIN:
                continue
            if flight_min + nxt.dur > gc.MAX_FLIGHT_MINUTES:
                continue
            chain.append(nxt)
            extend(chain, flight_min + nxt.dur)
            chain.pop()

    for leg in legs:
        extend([leg], leg.dur)
    return duties


def build_duty_index(duties):
    by_dep = {}
    grouped = defaultdict(list)
    for d in duties:
        grouped[d.dep].append(d)
    for code, ds in grouped.items():
        ds.sort(key=lambda d: d.dep_min)
        by_dep[code] = ([d.dep_min for d in ds], ds)
    return by_dep


def set_duals(duties, leg_duals):
    """leg_duals: list indexed by leg idx."""
    for d in duties:
        s = 0.0
        for li in d.leg_idxs:
            s += leg_duals[li]
        d.dual = s


def price_pass(duties, by_ddep, min_gain, seen, eps, top_k, deadline,
               starts, max_legs):
    """One exhaustive-or-deadline pricing pass over duty chains.

    min_gain = min over all duties of (span - 200*n_legs): appending any
    duty to a chain raises the unclipped cost by >= REST + min_gain and
    the dual sum by <= max_duty_dual => sound completion bound."""
    heap = []
    tiebreak = itertools.count()
    max_dual = max((d.dual for d in duties), default=0.0)
    # Per-pass tight step bound: appending duty v changes (unclipped cost -
    # dual sum) by >= REST + v.span - 200*v.n_legs - v.dual; taking the min
    # of the COUPLED expression is far tighter than mixing global worst
    # cases (REST + min_gain - max_dual never pruned: ~-130 at zero duals).
    rest_min = gc.MIN_OVERNIGHT_REST_MIN
    disc = gc.DISCOUNT_PER_EXTRA_LEG
    slope = min(0.0, min((rest_min + d.arr_min - d.dep_min
                          - disc * d.n_legs - d.dual for d in duties),
                         default=0.0))
    drain = max_dual >= 50_000.0
    emit_cap = 64 if drain else None
    nodes = [0]
    # Аудит R10 (опровержение №3): continue по max_legs шёл БЕЗ следа —
    # производственный прайсер смен объявлял полноту по усечённому
    # перебору. Все усечения — в именованный приёмник; полнота
    # вычисляется из перечня (PricingResult), не пишется флагом.
    sink = _pc.Truncations(_pc.SCOPE_PRICING)
    deadline_hit = [False]
    rest = gc.MIN_OVERNIGHT_REST_MIN
    span_cap = gc.MAX_PAIRING_MIN
    block_cap = gc.MAX_FLIGHT_MINUTES

    def threshold():
        return -heap[0][0] if len(heap) >= top_k else -eps

    def extend(chain, dual_sum, flight_min, legs_cnt, emitted):
        nodes[0] += 1
        if nodes[0] % 4096 == 0 and time.monotonic() > deadline:
            raise _TimeUp()
        first, last = chain[0], chain[-1]
        if legs_cnt >= 2 and last.arr == first.dep:
            span = last.arr_min - first.dep_min
            cost = gc.pairing_cost(span, legs_cnt)
            rc = cost - dual_sum
            if rc < threshold():
                fns = sum((d.fns for d in chain), ())
                if fns not in seen:
                    if emit_cap is None or emitted[0] < emit_cap:
                        emitted[0] += 1
                        key = (-rc, next(tiebreak), cost, fns)
                        if len(heap) < top_k:
                            heapq.heappush(heap, key)
                        else:
                            heapq.heappushpop(heap, key)
                    if emit_cap is not None and emitted[0] >= emit_cap:
                        raise _StartDone()
        # Completion bound over any number of appended duties.
        cur_unclipped = (last.arr_min - first.dep_min
                         - gc.DISCOUNT_PER_EXTRA_LEG * (legs_cnt - 1))
        span_left = span_cap - (last.arr_min - first.dep_min)
        if span_left < rest:
            return
        if cur_unclipped - dual_sum + slope * _rem(span_left) >= threshold():
            return
        dep_times, cands = by_ddep.get(last.arr, (None, None))
        if not cands:
            return
        lo = bisect_left(dep_times, last.arr_min + rest)
        latest_dep = first.dep_min + span_cap
        for i in range(lo, len(cands)):
            nxt = cands[i]
            if nxt.dep_min > latest_dep:
                break
            if nxt.ac != first.ac:
                continue
            if nxt.arr_min - first.dep_min > span_cap:
                continue
            if flight_min + nxt.flight_min > block_cap:
                continue
            if max_legs is not None and legs_cnt + nxt.n_legs > max_legs:
                # кандидат прошёл остальные фильтры и отброшен ИМЕННО
                # пределом — наблюдение честное (R10)
                sink.record("max_legs", max_legs, legs_cnt + nxt.n_legs)
                continue
            chain.append(nxt)
            extend(chain, dual_sum + nxt.dual, flight_min + nxt.flight_min,
                   legs_cnt + nxt.n_legs, emitted)
            chain.pop()

    min_duty_span = min((d.arr_min - d.dep_min for d in duties), default=0)
    step = rest + max(1, min_duty_span)

    def _rem(span_left):
        return max(0, span_left // step)

    try:
        for sd in starts:
            try:
                extend([sd], sd.dual, sd.flight_min, sd.n_legs, [0])
            except _StartDone:
                # предел эмиссии старта наблюдён (эмиссия сверх капа)
                sink.record("emit_cap", emit_cap or 0, (emit_cap or 0) + 1)
    except _TimeUp:
        deadline_hit[0] = True

    cols = [(cost, fns) for _, _, cost, fns in
            sorted(heap, key=lambda k: -k[0])]
    if drain:
        # Spread drain columns across start duties' first legs.
        per = defaultdict(int)
        div = []
        for cost, fns in cols:
            per[fns[0]] += 1
            if per[fns[0]] <= 8:
                div.append((cost, fns))
            else:
                # колонка реально отброшена диверсификацией — columns
                # усечены, и это видно в перечне (R10)
                sink.record("drain_diversity", 8, per[fns[0]])
        cols = div
    return _pc.PricingResult(
        columns=tuple(cols), sinks=(sink,), problem_fingerprint="",
        deadline_hit=deadline_hit[0])


# ---------------------------------------------------------------------------
# Multiprocess wrapper (same pattern as _gerad_pricing_mp).

_G = {}


def init_worker(inst, payload_path, profile, legacy_duty, max_landings,
                rule_overrides=None):
    gc.apply_profile(profile)
    if legacy_duty:
        gc.DUTY_RULE_ON = False
    # Пределы главного процесса (правила источника поверх профиля) —
    # иначе воркер прайсит по другой постановке (аудит R10, отсрочка №1).
    for k, v in (rule_overrides or {}).items():
        setattr(gc, k, v)
    legs, _, _wm = gc.load_legs(inst, payload_path)
    by_dep = {}
    grouped = defaultdict(list)
    for leg in legs:
        grouped[leg.dep].append(leg)
    for code, ls in grouped.items():
        ls.sort(key=lambda l: l.dep_min)
        by_dep[code] = ([l.dep_min for l in ls], ls)
    duties = enumerate_duties(legs, by_dep, max_landings)
    _G["duties"] = duties
    _G["by_ddep"] = build_duty_index(duties)
    _G["min_gain"] = min((d.arr_min - d.dep_min
                          - gc.DISCOUNT_PER_EXTRA_LEG * d.n_legs
                          for d in duties), default=0)


def run_chunk(task):
    leg_duals, start_idxs, eps, top_k, deadline, max_legs = task
    duties = _G["duties"]
    set_duals(duties, leg_duals)
    starts = [duties[i] for i in start_idxs]
    return price_pass(duties, _G["by_ddep"], _G["min_gain"], set(),
                      eps, top_k, deadline, starts, max_legs)


_PASS_NO = [0]


def price_pass_mp(pool, legs, start_idxs, seen, eps, top_k, deadline,
                  workers, max_legs):
    leg_duals = [0.0] * len(legs)
    for l in legs:
        leg_duals[l.idx] = l.dual
    idxs = list(start_idxs)
    _PASS_NO[0] += 1
    r = (_PASS_NO[0] * 7919) % len(idxs) if idxs else 0
    idxs = idxs[r:] + idxs[:r]
    chunks = [idxs[w::workers] for w in range(workers) if idxs[w::workers]]
    worker_k = max(4000, top_k // max(1, workers))
    tasks = [(leg_duals, ch, eps, worker_k, deadline, max_legs)
             for ch in chunks]
    merged = []
    sinks: list = []
    deadline_hit = False
    for res in pool.map(run_chunk, tasks):
        deadline_hit = deadline_hit or res.deadline_hit
        sinks.extend(res.sinks)
        merged.extend(res.columns)
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
