"""GERAD benchmark ①, config (а) — Stage-1 via COLUMN GENERATION
(price-and-branch), pattern from Flórez et al. 2009 (Uniandes/AGIFORS).

Architecture (see final-report/PROFESSIONAL-REPORT-RU.md §5.2):
  master   — set-COVERING LP relaxation (GLOP, gives duals) over a
             restricted, growing pool of pairing columns;
  pricing  — the same DFS legality rules as pairing_generator under the
             GERAD profile, but emitting ONLY columns with reduced cost
             < -eps into a bounded top-K heap, with a sound dual-based
             branch-pruning bound (kills both the 20 GB pairing list and
             the 3h+ enumeration seen on inst4);
  integer  — the EXISTING CP-SAT set-partitioning selector over the final
             pool (price-and-branch: no pricing inside the tree). Result
             is reported as IP objective + LP lower bound + gap, NOT as
             a proven optimum.

Deliberately self-contained: does NOT import pairing_generator and does
not modify any module used by the running full-enumeration batch. Cost
MUST stay in exact parity with pairing_selector.select_pairings_set_partitioning:
    cost(p) = int(max(1, duty_minutes - 200 * (legs - 1)))
(the run asserts this parity on the final selection).

Usage: python _gerad_colgen.py <inst 1..7> [--iters N] [--k K] [--eps E]
         [--price-cap S] [--final-tl S] [--total-cap S]
"""
from __future__ import annotations

import argparse
import heapq
import _pricing_certificate as _pc
import itertools
import hashlib
import json
import os
import sys
import time
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from flight_crew_scheduling.domain import Airport, Flight  # noqa: E402
from flight_crew_scheduling.pairing import Pairing  # noqa: E402
from flight_crew_scheduling.pairing_selector import (  # noqa: E402
    DISCOUNT_PER_EXTRA_LEG,
    select_pairings_set_partitioning,
)

import rules_config  # noqa: E402  (repo root: single tier-1 rules source)
import _pricing_certificate as _pc  # noqa: E402

SCRATCH = Path(__file__).resolve().parent / "data" / "gerad"

# --- GERAD rule profile (mirror of the monkey-patch in _gerad_two_stage.py) --
# Connection profile correction (2026-07-10, audit follow-up): the GENCOL
# reference solutions have active-active connections down to 30-42 min in
# every month and 32+ connections inside our former [6h,10h) "dead zone"
# (inst1 alone) — both the 60-min sit and the dead zone were OUR artifacts,
# stricter than the dataset semantics. Aligned: sit >= 30 min, no dead zone
# (a gap < 10 h is a sit inside the duty; >= 10 h is a rest). The v2 (BTS
# day) profile keeps its original semantics.
# Tier-1 parameters come from rules_config (env CREW_RULES_CONFIG; defaults
# are exactly the historical values). apply_profile('v2') — the legacy/BTS
# profile — still explicitly overrides sit/rest/dead-zone AFTER this
# config load, keeping its historical semantics.
_RULES = rules_config.effective()
MIN_GROUND_TIME_MIN = _RULES["min_sit_min"]        # rule 4: min sit
MIN_OVERNIGHT_REST_MIN = _RULES["min_rest_min"]    # rule 5: min rest
DAY_BREAK_THRESHOLD_MIN = 6 * 60  # v2 only: gap in [6h, rest) is illegal
DEAD_ZONE_ON = False              # gerad: no dead zone (reference-aligned)
MAX_PAIRING_MIN = _RULES["max_pairing_span_min"]   # rule 2: elapsed cap
MAX_FLIGHT_MINUTES = 30 * 60      # PER-PAIRING block cap (generator
                                  # default) — NOT the monthly tier-1 cap
MAX_LEGS = 5                      # fidelity note: per-pairing cap
DEFAULT_MAX_LANDINGS = _RULES["max_landings_per_duty"]  # rule 6 default
# Rule 7 fidelity fix (found by the true-rules validator): within a duty —
# a run of legs not split by a rest >= MIN_OVERNIGHT_REST_MIN — the elapsed
# span must not exceed 14 h. The legacy space lacked this check.
MAX_DUTY_SPAN_MIN = _RULES["max_duty_span_min"]
DUTY_RULE_ON = True   # --legacy-duty reverts to the historical space
# Big-M for artificial slack in base-anchored mode; any real pairing costs
# <= MAX_PAIRING_MIN = 5760, so this can never displace a real cover.
ARTIFICIAL_COST = 100_000.0


def apply_profile(name: str) -> None:
    """'gerad' (module defaults, reference-aligned connections) or 'v2'
    (RULES v2, the BTS-day profile: sit >= 60 min, rest >= 8 h, dead zone
    [6h, 8h)) — v2 keeps its historical semantics.

    CREW_RULES_STRICT=1 (set by the webapp for what-if scenario runs): the
    explicitly configured tier-1 sit/rest from CREW_RULES_CONFIG win over
    the v2 presets — otherwise the preset would silently swallow the very
    rule change the scenario exists to test. The dead zone is profile
    semantics, not a tier-1 parameter, and stays on. Without the strict
    flag v2 is byte-identical to its historical behavior."""
    global MIN_OVERNIGHT_REST_MIN, MIN_GROUND_TIME_MIN, DEAD_ZONE_ON
    if name == "v2":
        if os.environ.get("CREW_RULES_STRICT") != "1":
            MIN_OVERNIGHT_REST_MIN = 8 * 60
            MIN_GROUND_TIME_MIN = 60
        DEAD_ZONE_ON = True
    elif name != "gerad":
        raise SystemExit(f"unknown rules profile: {name}")

# Reference objectives from the full-enumeration path (proven OPTIMAL over
# the same pairing space) — used as self-check where available.
FULL_ENUM_OPT = {1: 98191, 2: 97453, 3: 130650}

EPOCH = datetime(2000, 1, 1)


class Leg:
    """Flat, fast record for the pricing DFS (pydantic access is slow)."""
    __slots__ = ("idx", "fn", "dep_min", "arr_min", "dep", "arr", "dur",
                 "ac", "dual")

    def __init__(self, idx, fn, dep_min, arr_min, dep, arr, dur, ac):
        self.idx = idx
        self.fn = fn
        self.dep_min = dep_min
        self.arr_min = arr_min
        self.dep = dep
        self.arr = arr
        self.dur = dur
        self.ac = ac
        self.dual = 0.0


class _TimeUp(Exception):
    pass


class _StartDone(Exception):
    """Drain phase: this start hit its emission cap — abort its DFS and
    spend the pass budget on the next start instead."""


def load_legs(inst: int, payload_path: str | None = None):
    from _leg_identity import assert_representable
    src = (Path(payload_path) if payload_path
           else SCRATCH / "payloads" / f"inst{inst}.json")
    payload = json.loads(src.read_text(encoding="utf-8"))
    # C01/C02 phase 1: refuse a payload the flat single-crew pipeline cannot
    # faithfully model (multi-seat/multi-role legs; repeated leg identity that
    # would collapse). Raised here, caught in main() -> clean exit 3.
    assert_representable(payload, "colgen.load_legs")
    # Пакет B (аудит-2): повторные wire-номера переименовываются в
    # уникальные синтетические ДО построения структур, ключуемых по номеру
    # (Leg/by_fn/master); payload без повторов проходит identity-путём —
    # канонические артефакты не меняются ни на байт.
    from _leg_rekey import rekey_or_passthrough
    payload, wire_map = rekey_or_passthrough(payload)
    airports = {a["code"]: Airport(code=a["code"], name=a["code"],
                                   languages=["en"])
                for a in payload["airports"]}
    flights = [Flight(
        flight_number=f["flightNumber"],
        departure_airport=airports[f["departureAirport"]],
        departure_utc_date_time=f["departureUTCDateTime"],
        arrival_airport=airports[f["arrivalAirport"]],
        arrival_utc_date_time=f["arrivalUTCDateTime"],
        aircraft_type=f.get("aircraftType", "UNKNOWN"),
    ) for f in payload["flights"]]

    legs = []
    for f in sorted(flights, key=lambda f: (f.departure_utc_date_time,
                                            f.flight_number)):
        dep_t, arr_t = f.departure_utc_date_time, f.arrival_utc_date_time
        # Cost parity relies on minute-aligned times (GERAD data is h:mm).
        assert dep_t.second == 0 and arr_t.second == 0, f.flight_number
        legs.append(Leg(
            idx=len(legs), fn=f.flight_number,
            dep_min=int((dep_t - EPOCH).total_seconds() // 60),
            arr_min=int((arr_t - EPOCH).total_seconds() // 60),
            dep=f.departure_airport.code, arr=f.arrival_airport.code,
            dur=f.get_duration_minutes(), ac=f.aircraft_type))
    by_fn = {f.flight_number: f for f in flights}
    return legs, by_fn, wire_map


def pairing_cost(duty_min: int, n_legs: int) -> int:
    c = duty_min - DISCOUNT_PER_EXTRA_LEG * (n_legs - 1)
    return c if c > 1 else 1


class RestrictedMaster:
    """Set-covering LP over the column pool. GLOP; columns added
    incrementally so the basis warm-starts between iterations.

    ``artificial_cost``: when set (base-anchored mode has no universal
    single-leg seeds), every row gets an artificial slack column at that
    cost, keeping the LP feasible while real columns are priced in.
    ``artificial_usage()`` reports how much slack the optimum still uses —
    rows stuck on artificials are uncoverable by the current pricing rules."""

    def __init__(self, legs, artificial_cost: float | None = None):
        from ortools.linear_solver import pywraplp
        self._pw = pywraplp
        self.solver = pywraplp.Solver.CreateSolver("GLOP")
        self.obj = self.solver.Objective()
        self.obj.SetMinimization()
        self.cons = {}
        inf = self.solver.infinity()
        self._arts = []
        for leg in legs:
            self.cons[leg.fn] = self.solver.Constraint(1.0, inf, f"c_{leg.fn}")
            if artificial_cost is not None:
                a = self.solver.NumVar(0.0, inf, f"art_{leg.fn}")
                self.obj.SetCoefficient(a, float(artificial_cost))
                self.cons[leg.fn].SetCoefficient(a, 1.0)
                self._arts.append((leg.fn, a))
        self._n = 0

    def artificial_usage(self):
        return [(fn, a.solution_value()) for fn, a in self._arts
                if a.solution_value() > 1e-6]

    def relax_row(self, fn):
        """Drop a row from the covering requirement (>=1 -> >=0): its
        big-M artificial leaves the basis and stops poisoning the pricing
        pruning bound. The LP bound then applies to the SUBproblem without
        this leg — matching the partial-cover IP."""
        self.cons[fn].SetBounds(0.0, self.solver.infinity())

    def add_column(self, cost: int, fns) -> None:
        v = self.solver.NumVar(0.0, self.solver.infinity(), f"x{self._n}")
        self._n += 1
        self.obj.SetCoefficient(v, float(cost))
        for fn in fns:
            self.cons[fn].SetCoefficient(v, 1.0)

    def solve(self):
        status = self.solver.Solve()
        if status != self._pw.Solver.OPTIMAL:
            raise RuntimeError(f"GLOP status {status}")
        duals = {fn: c.dual_value() for fn, c in self.cons.items()}
        return self.obj.Value(), duals


def price_pass(legs, by_dep, min_dur, seen, eps, top_k, deadline,
               starts=None):
    """One pricing pass. Returns (columns, exhaustive) where columns is a
    list of (cost, fns) with reduced cost < -eps (at most top_k, most
    negative first) and exhaustive=False iff the deadline cut the pass."""
    if top_k <= 0:
        # P1-07 (аудит R10): та же граница параметров, что у построителя.
        raise ValueError(f"top_k must be positive: {top_k}")
    heap = []  # (key=-rc, tiebreak, cost, fns); min-heap keeps K largest keys
    tiebreak = itertools.count()
    max_dual = max((l.dual for l in legs), default=0.0)
    # Drain phase (base-anchored master still on artificial big-M duals):
    # cap emissions per start, otherwise the global top-K concentrates on
    # one hot uncovered region and a 60k-column pass drains ~300 legs of
    # 5000+ (seen on inst4). Diversity >> volume while draining.
    drain = max_dual >= 50_000.0
    emit_cap = 64 if drain else None
    emit_count = Counter()
    # Adding one leg changes unclipped cost by >= delta_min and dual sum by
    # <= max_dual  =>  rc of ANY completion with j extra legs is >=
    # cur_unclipped - dual_sum + j*(delta_min - max_dual). slope<=0 => worst
    # at j = remaining; slope>0 => cur itself is a valid lower bound.
    delta_min = MIN_GROUND_TIME_MIN + min_dur - DISCOUNT_PER_EXTRA_LEG
    slope = min(0.0, delta_min - max_dual)
    nodes = [0]
    deadline_hit = [False]
    # Наблюдённые структурные усечения прохода. Множество (а не флаг),
    # чтобы перечень попадал в отчёт по именам: «граница недостоверна»
    # без указания причины заставляет искать её заново каждый раунд.
    # (аудит R10): единый приёмник вместо множества имён — полнота
    # вычисляется из перечня с limit/observed (PricingResult).
    sink = _pc.Truncations(_pc.SCOPE_PRICING)

    def threshold():
        return -heap[0][0] if len(heap) >= top_k else -eps

    def extend(chain, visited, dual_sum, flight_min, duty_start):
        nodes[0] += 1
        if nodes[0] % 8192 == 0 and time.monotonic() > deadline:
            raise _TimeUp()
        first, last = chain[0], chain[-1]
        n = len(chain)
        if n >= 2 and first.dep == last.arr:
            duty = last.arr_min - first.dep_min
            cost = pairing_cost(duty, n)
            rc = cost - dual_sum
            if rc < threshold():
                fns = tuple(l.fn for l in chain)
                if fns not in seen:
                    if emit_cap is None or emit_count[first.fn] < emit_cap:
                        emit_count[first.fn] += 1
                        key = (-rc, next(tiebreak), cost, fns)
                        if len(heap) < top_k:
                            heapq.heappush(heap, key)
                        else:
                            heapq.heappushpop(heap, key)
                    if (emit_cap is not None
                            and emit_count[first.fn] >= emit_cap):
                        raise _StartDone()
        if n >= MAX_LEGS:
            # Предел числа участков в командировке — ЭВРИСТИКА (настраивается
            # ключом --max-legs), а не правило источника. Прежде обход по
            # нему обрывался молча, и «проход исчерпан» означало «исчерпан в
            # пределах пяти участков» — то есть достоверность границы LP
            # объявлялась по усечённому перебору (внешний аудит R9: тот же
            # класс, что P0-01 в построителе, только во втором движке).
            #
            # Свидетельство приближённое и названо так: точная проверка
            # «существовало ли допустимое продолжение» потребовала бы
            # построить преемников ровно там, где обход прерывается ради
            # скорости. Направление ошибки безопасное — лишняя запись
            # ОТНИМАЕТ достоверность у границы, а не выдаёт её.
            if by_dep.get(last.arr, (None, None))[1]:
                sink.record("max_legs~", MAX_LEGS, MAX_LEGS + 1)
            return
        # Sound lower bound on rc of any strict extension of this chain.
        cur_unclipped = (last.arr_min - first.dep_min
                         - DISCOUNT_PER_EXTRA_LEG * (n - 1))
        if cur_unclipped - dual_sum + (MAX_LEGS - n) * slope >= threshold():
            return
        dep_times, cands = by_dep.get(last.arr, (None, None))
        if not cands:
            return
        lo = bisect_left(dep_times, last.arr_min + MIN_GROUND_TIME_MIN)
        latest_dep = first.dep_min + MAX_PAIRING_MIN
        for i in range(lo, len(cands)):
            nxt = cands[i]
            if nxt.dep_min > latest_dep:
                break
            gap = nxt.dep_min - last.arr_min
            if (DEAD_ZONE_ON
                    and DAY_BREAK_THRESHOLD_MIN <= gap
                    < MIN_OVERNIGHT_REST_MIN):
                continue  # dead zone: too long for a sit, too short for rest
            if nxt.idx in visited:
                continue
            if nxt.ac != first.ac:
                continue
            if nxt.arr_min - first.dep_min > MAX_PAIRING_MIN:
                continue
            if flight_min + nxt.dur > MAX_FLIGHT_MINUTES:
                continue
            # Rule 7: a rest (gap >= MIN_OVERNIGHT_REST_MIN) starts a new
            # duty; otherwise the duty continues and its span is capped.
            nds = nxt.dep_min if gap >= MIN_OVERNIGHT_REST_MIN else duty_start
            if DUTY_RULE_ON and nxt.arr_min - nds > MAX_DUTY_SPAN_MIN:
                continue
            chain.append(nxt)
            visited.add(nxt.idx)
            extend(chain, visited, dual_sum + nxt.dual,
                   flight_min + nxt.dur, nds)
            chain.pop()
            visited.remove(nxt.idx)

    try:
        for start in (starts if starts is not None else legs):
            try:
                extend([start], {start.idx}, start.dual, start.dur,
                       start.dep_min)
            except _StartDone:
                # Emission-capped start counts as NOT fully explored: a
                # drain pass must never certify LP optimality.
                sink.record("emit_cap", emit_cap or 0, (emit_cap or 0) + 1)
    except _TimeUp:
        deadline_hit[0] = True
    cols = [(cost, fns) for _, _, cost, fns in
            sorted(heap, key=lambda k: -k[0])]
    if drain:
        # Keep at most 8 columns per start — spread the pass across regions.
        per = Counter()
        div = []
        for cost, fns in cols:
            per[fns[0]] += 1
            if per[fns[0]] <= 8:
                div.append((cost, fns))
            else:
                # колонка реально отброшена диверсификацией (R10)
                sink.record("drain_diversity", 8, per[fns[0]])
        cols = div
    return _pc.PricingResult(
        columns=tuple(cols), sinks=(sink,), problem_fingerprint="",
        deadline_hit=deadline_hit[0])


def backward_focus(legs, slack_fns, max_hops):
    """Indices of legs that can PRECEDE a slack leg in a legal chain within
    max_hops (connection-rule over-approximation: duty/span ignored). Used
    to focus tail-drain pricing passes on relevant starts only — with any
    artificial dual at big-M the branch bound prunes nothing, so unfocused
    passes waste their budget on starts nowhere near the uncovered legs."""
    by_arr = defaultdict(list)
    for l in legs:
        by_arr[l.arr].append(l)
    want = set(slack_fns)
    frontier = [l for l in legs if l.fn in want]
    seen_idx = {l.idx for l in frontier}
    for _ in range(max_hops - 1):
        nxt = []
        for leg in frontier:
            for p in by_arr.get(leg.dep, ()):
                if p.idx in seen_idx or p.ac != leg.ac:
                    continue
                gap = leg.dep_min - p.arr_min
                if gap < MIN_GROUND_TIME_MIN:
                    continue
                if (DEAD_ZONE_ON and DAY_BREAK_THRESHOLD_MIN <= gap
                        < MIN_OVERNIGHT_REST_MIN):
                    continue
                seen_idx.add(p.idx)
                nxt.append(p)
        if not nxt:
            break
        frontier = nxt
    return seen_idx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("inst", type=int)
    ap.add_argument("--iters", type=int, default=200)
    # Default raised 20000 -> 60000 (2026-07-10): base-anchored masters
    # start on artificial slack and early passes are pure coverage-drain;
    # a bigger top-K drains them in ~3x fewer expensive passes.
    ap.add_argument("--k", type=int, default=60000)
    ap.add_argument("--eps", type=float, default=1e-4)
    ap.add_argument("--price-cap", type=float, default=180.0)
    ap.add_argument("--final-tl", type=int, default=600)
    ap.add_argument("--total-cap", type=float, default=14400.0)
    ap.add_argument("--pool-cap", type=int, default=600_000)
    ap.add_argument("--out", default=str(HERE / "final-report" /
                                         "gerad_colgen_results.json"))
    ap.add_argument("--dump-selected", action="store_true",
                    help="write selected pairings (fns lists) next to --out")
    ap.add_argument("--profile", default="gerad", choices=["gerad", "v2"],
                    help="rules profile: gerad (monthly) or v2 (BTS day)")
    ap.add_argument("--payload", default=None,
                    help="path to a FlightCrewSchedule JSON (overrides inst)")
    ap.add_argument("--name", default=None,
                    help="label for prints/dump file (default: inst<N>)")
    ap.add_argument("--legacy-duty", action="store_true",
                    help="disable the duty-span rule (historical space)")
    # Audit fix (2026-07-10), base anchoring: GENCOL reference pairings all
    # start/end at a crew base (verified: inst1 172/172, inst4 1076/1079);
    # our catalog started chains at ANY airport (inst1: 22 start airports).
    ap.add_argument("--bases", default=None,
                    help="comma-separated base codes; restricts pairings to "
                         "start (and, via closure, end) at a base")
    ap.add_argument("--max-legs", type=int, default=None,
                    help="per-pairing leg cap (default: module MAX_LEGS); "
                         "base mode needs more than 5 to cover distant legs")
    ap.add_argument("--mp", type=int, default=0,
                    help="parallel pricing workers (0/1 = single-threaded)")
    ap.add_argument("--dump-pool", action="store_true",
                    help="persist the FULL final column pool (gzip json) — "
                         "required for honest catalog completion (audit "
                         "2026-07-12: completion must optimize the full "
                         "pool, not selection+witnesses)")
    ap.add_argument("--seed-columns", default=None,
                    help="path to gerad_leg_feasibility.json: witness "
                         "pairings for this instance are seeded into the "
                         "initial pool (coverage guaranteed from pass 1)")
    ap.add_argument("--cert-budget", type=float, default=60.0,
                    help="per-leg budget of the built-in exact feasibility "
                         "certification of budget-missing legs")
    ap.add_argument("--pricer", default="legs", choices=["legs", "duty"],
                    help="legs: naive leg DFS; duty: duty-network pricing "
                         "(exhaustive passes, proof-grade on big months)")
    # Правила системы-источника (аудит R10, отсрочка №1): раньше эта линия
    # этапа 1 строила постановку без них — половины прогона судили по
    # разным моделям. Числовые пределы применяются к формулировке; то, что
    # формулировка НЕ выражает (равнозначные станции), — громкий отказ, а
    # не тихое игнорирование: считать такой набор надо построителем.
    ap.add_argument("--source-rules", type=Path, default=None,
                    help="файл правил системы-источника (см. построитель)")
    ap.add_argument("--source-rules-fp", default=None,
                    help="sha256 файла правил — обязателен вместе с путём")
    ap.add_argument("--source-rules-strict", action="store_true",
                    help="неполный файл источника — отказ")
    ap.add_argument("--max-landings", default=str(DEFAULT_MAX_LANDINGS),
                    help="duty pricer: landings per duty cap (rule 6, "
                         "default from rules_config); 'none' disables it "
                         "(leg-space cross-check mode)")
    args = ap.parse_args()
    apply_profile(args.profile)
    if args.legacy_duty:
        global DUTY_RULE_ON
        DUTY_RULE_ON = False
    if args.max_legs:
        global MAX_LEGS
        MAX_LEGS = args.max_legs
    # ---- правила системы-источника (аудит R10, отсрочка №1) ------------
    args.source_rules_overrides = None
    if args.source_rules:
        if not args.source_rules_fp:
            print("ОТКАЗ: --source-rules требует --source-rules-fp "
                  "(sha256 файла правил)", flush=True)
            return 2
        raw = Path(args.source_rules).read_bytes()
        got = hashlib.sha256(raw).hexdigest()
        if got != args.source_rules_fp:
            print("ОТКАЗ: файл правил источника %s не совпал с отпечатком "
                  "команды (%s != %s) — файл подменён между стартом задачи "
                  "и чтением." % (Path(args.source_rules).name, got[:16],
                                  args.source_rules_fp[:16]), flush=True)
            return 2
        import _cos_rules
        # Единая точка загрузки с опознанием вида (см.
        # `_cos_rules.load_bytes`): принимается и служебный JSON, и
        # документ постановки целиком. Документ вида `as_dict()` прежде
        # разбирался в ноль ключей, и порождение колонок молча уходило
        # на умолчания чужой авиакомпании.
        try:
            cr = _cos_rules.load_bytes(
                raw, where=Path(args.source_rules).name)
        except _cos_rules.SourceRulesError as exc:
            print("ОТКАЗ: %s" % exc, flush=True)
            return 2
        if args.source_rules_strict and cr.source_keys_missing:
            print("ОТКАЗ: файл правил источника неполон — отсутствуют "
                  "ключи: %s." % ", ".join(cr.source_keys_missing),
                  flush=True)
            return 2
        se_declared = ("stationEquivalenceRules"
                       not in cr.source_keys_missing)
        if se_declared and getattr(cr, "teleportation", None):
            # Равнозначные станции меняют ПРОСТРАНСТВО этапа 1, а эта
            # формулировка стыкует смены строго по одному аэропорту.
            # Тихое игнорирование дало бы каталог другой постановки под
            # правильным отпечатком — отказ всегда, не только в strict.
            print("ОТКАЗ: правила источника содержат равнозначные станции "
                  "(stationEquivalenceRules), а формулировка colgen их не "
                  "выражает — используйте построитель цепочек "
                  "(_pairing_builder, уровень 1/2).", flush=True)
            return 2
        if not se_declared:
            # Ключа в файле нет: зашитые пары умолчания — НЕ данные
            # источника, они не применяются; сказать вслух (в strict сюда
            # не дойти — неполный файл отвергнут выше).
            print("этап: равнозначные станции источником не заданы — "
                  "стыковка строго по одному аэропорту", flush=True)
        global MAX_DUTY_SPAN_MIN, MIN_GROUND_TIME_MIN, MAX_PAIRING_MIN
        # ГЕЙТ формулировки (пакет «умолчания-в-данные»): правила,
        # задающие пространство колонок, обязаны прийти из файла правил
        # (или пакета постановки). Поле None — отказ словами, а не тихая
        # подстановка профиля: чужое число под правильным отпечатком
        # выдало бы себя за правило источника.
        lacking = [name for name, val in (
            ("work_time_limit_by_tier", cr.work_time_limit_by_tier),
            ("min_sit_min", cr.min_sit_min),
            ("max_pairing_days", cr.max_pairing_days))
            if val is None]
        if lacking:
            print("ОТКАЗ: файл правил источника не несёт %s — дополните "
                  "файл правил (снимок as_dict) или пакет постановки; "
                  "расчёт по умолчанию запрещён"
                  % ", ".join(lacking), flush=True)
            return 2
        overrides = []
        for name, value in (("MAX_DUTY_SPAN_MIN", cr.max_work_time()),
                            ("MIN_GROUND_TIME_MIN", cr.min_sit_min),
                            ("MAX_PAIRING_MIN", cr.max_pairing_days * 1440)):
            old = globals()[name]
            if int(old) != int(value):
                overrides.append(f"{name}: профиль {old} -> "
                                 f"источник {value}")
            globals()[name] = int(value)
        if str(args.max_landings) != str(cr.max_landings_per_duty):
            overrides.append(f"max_landings: {args.max_landings} -> "
                             f"источник {cr.max_landings_per_duty}")
            args.max_landings = str(cr.max_landings_per_duty)
        args.source_rules_overrides = overrides
        if overrides:
            print("этап: правила источника перекрывают профиль:",
                  flush=True)
            for o in overrides:
                print(f"  {o}", flush=True)
        else:
            print("этап: правила источника совпадают с профилем",
                  flush=True)
    bases = (set(args.bases.split(",")) if args.bases else None)
    label = args.name or f"inst{args.inst}"

    t_all = time.monotonic()
    from _leg_identity import GeradPipelineUnsupported
    try:
        legs, flight_by_fn, wire_map = load_legs(args.inst, args.payload)
    except GeradPipelineUnsupported as exc:
        print("!! входной payload не поддерживается плоским одночленным "
              f"конвейером — расчёт отменён (fail-closed): {exc}", flush=True)
        return 3
    n_legs = len(legs)
    duty_note = "duty-fixed" if DUTY_RULE_ON else "LEGACY duty"
    base_note = (f", base-anchored {sorted(bases)}, max {MAX_LEGS} legs"
                 if bases else "")
    print(f"=== Stage-1 via COLUMN GENERATION [{args.profile}, {duty_note}"
          f"{base_note}]: {label} ({n_legs} legs) ===", flush=True)

    by_dep = {}
    grouped = defaultdict(list)
    for leg in legs:
        grouped[leg.dep].append(leg)
    for code, ls in grouped.items():
        ls.sort(key=lambda l: l.dep_min)
        by_dep[code] = ([l.dep_min for l in ls], ls)
    min_dur = min(l.dur for l in legs)

    pool = {}   # fns tuple -> cost
    if bases:
        # Base-anchored: single-leg pairings cannot close at a base (dep !=
        # arr for real legs), so there are no universal seeds; the master
        # stays feasible via artificial slack columns instead. Slack left
        # in the final LP marks legs uncoverable from the bases.
        starts = [l for l in legs if l.dep in bases]
        print(f"  starts: {len(starts)}/{n_legs} legs depart from a base",
              flush=True)
        rmp = RestrictedMaster(legs, artificial_cost=ARTIFICIAL_COST)
    else:
        # Seed: single-leg columns => the master is always feasible and the
        # final ==1 partitioning always has a fallback cover.
        starts = None
        for leg in legs:
            pool[(leg.fn,)] = pairing_cost(leg.arr_min - leg.dep_min, 1)
        rmp = RestrictedMaster(legs)
    seen = set(pool)
    for fns, cost in pool.items():
        rmp.add_column(cost, fns)

    if args.seed_columns:
        feas = json.loads(Path(args.seed_columns).read_text())
        by_fn_min = {l.fn: l for l in legs}
        added = 0
        for v in feas.get(f"inst{args.inst}", {}).values():
            w = tuple(v.get("witness_pairing") or ())
            if not w or w in pool:
                continue
            span = by_fn_min[w[-1]].arr_min - by_fn_min[w[0]].dep_min
            c = pairing_cost(span, len(w))
            pool[w] = c
            seen.add(w)
            rmp.add_column(c, w)
            added += 1
        print(f"  seeded {added} witness columns from "
              f"{Path(args.seed_columns).name}", flush=True)

    duty_mod = duties = duty_by_dep = duty_starts = None
    duty_min_gain = 0
    max_landings = (None if args.max_landings == "none"
                    else int(args.max_landings))
    if args.pricer == "duty":
        import _gerad_duty_pricing as dpx
        duty_mod = dpx
        t0 = time.monotonic()
        duties = dpx.enumerate_duties(legs, by_dep, max_landings)
        duty_by_dep = dpx.build_duty_index(duties)
        duty_min_gain = min((d.arr_min - d.dep_min
                             - DISCOUNT_PER_EXTRA_LEG * d.n_legs
                             for d in duties), default=0)
        duty_starts = [d.idx for d in duties
                       if bases is None or d.dep in bases]
        # Drain-phase start set: ONE (longest) duty per base-departing
        # first leg. Full-start drain passes on the big months are 900s+
        # (253k starts on inst4, no pruning under big-M duals) while the
        # drain only needs coverage, not exactness — restricted passes are
        # marked non-certifying below.
        best_by_first = {}
        for d in duties:
            if bases is not None and d.dep not in bases:
                continue
            f0 = d.leg_idxs[0]
            b = best_by_first.get(f0)
            if b is None or d.n_legs > duties[b].n_legs:
                best_by_first[f0] = d.idx
        duty_drain_starts = list(best_by_first.values())
        print(f"  duties: {len(duties):,} enumerated in "
              f"{time.monotonic()-t0:.1f}s; {len(duty_starts):,} start at "
              f"a base ({len(duty_drain_starts):,} drain starts); "
              f"landings cap {max_landings}", flush=True)

    executor = None
    if args.mp and args.mp > 1:
        from concurrent.futures import ProcessPoolExecutor
        # Действующие пределы едут в воркеры ЯВНО (аудит R10, отсрочка
        # №1): init_worker сам вызывает apply_profile, и перекрытия правил
        # источника в главном процессе иначе потерялись бы в дочерних —
        # прайсинг шёл бы по другой постановке, чем объявлено.
        rule_overrides = {"MAX_DUTY_SPAN_MIN": MAX_DUTY_SPAN_MIN,
                          "MIN_GROUND_TIME_MIN": MIN_GROUND_TIME_MIN,
                          "MAX_PAIRING_MIN": MAX_PAIRING_MIN}
        if duty_mod is not None:
            executor = ProcessPoolExecutor(
                max_workers=args.mp, initializer=duty_mod.init_worker,
                initargs=(args.inst, args.payload, args.profile,
                          args.legacy_duty, max_landings, rule_overrides))
        else:
            import _gerad_pricing_mp as pmp
            executor = ProcessPoolExecutor(
                max_workers=args.mp, initializer=pmp.init_worker,
                initargs=(args.inst, args.payload, args.profile,
                          args.legacy_duty, args.max_legs, rule_overrides))
        print(f"  pricing: {args.mp} parallel workers", flush=True)

    iterations = []
    lp_obj, lp_valid = None, False
    t_price = t_lp = 0.0
    retried_uncapped = False
    hopeless = set()   # slack legs a focused pass failed to reach
    last_focused = False   # never two focused passes in a row (livelock)
    # Neame dual smoothing (validated on our A320 branch-and-price): price
    # at 0.5*center + 0.5*exact to damp the dual oscillation that keeps
    # 60k new columns arriving per pass (inst4 refinement stalled at
    # -37k LP/pass, 900s/pass). A zero-column smoothed pass is a MISPRICE:
    # the pass is rerun at exact duals; only exact-dual exhausted passes
    # certify the bound.
    smooth_center = None
    force_exact = False
    it = 0
    while it < args.iters:
        it += 1
        t0 = time.monotonic()
        lp_obj, duals = rmp.solve()
        t_lp += time.monotonic() - t0
        if smooth_center is None:
            smooth_center = dict(duals)
        if force_exact:
            priced_duals = duals
        else:
            priced_duals = {fn: 0.5 * smooth_center[fn] + 0.5 * duals[fn]
                            for fn in duals}
        for leg in legs:
            leg.dual = priced_duals[leg.fn]

        # Tail-drain focus: with few legs left on artificial slack, point
        # the whole pass at starts that can actually reach them.
        use_starts, focused = starts, False
        use_dstarts = duty_starts
        drain_restricted = False
        if duty_mod is not None and bases:
            slack_all = rmp.artificial_usage()
            if slack_all:
                # While ANY slack remains, big-M duals poison the pruning
                # bound and full-start passes cap out (253k starts, 900s).
                # Coverage passes over one-longest-duty-per-first-leg
                # starts; they cannot certify the LP — the proof pass runs
                # on the full start set once slack is zero.
                use_dstarts = duty_drain_starts
                drain_restricted = True
        if bases:
            slack_now = [fn for fn, _ in rmp.artificial_usage()
                         if fn not in hopeless]
            if 0 < len(slack_now) <= 200:
                # Hop limit 8: at full chain depth the backward cone covers
                # ~everything (inst4: 97% of starts) and focusing is moot;
                # a focused pass only pays off on a TIGHT cone.
                fidx = backward_focus(legs, slack_now, 8)
                if duty_mod is not None:
                    # Last chance before hopeless: the cone must be searched
                    # over the FULL start set — a slack leg may only be
                    # coverable by a chain starting with a SUB-duty.
                    fset = set(fidx)
                    fs = [i for i in duty_starts
                          if not fset.isdisjoint(duties[i].leg_idxs)]
                    if fs and len(fs) < 0.5 * len(duty_starts):
                        use_dstarts, focused = fs, True
                else:
                    fs = [l for l in starts if l.idx in fidx]
                    if fs and len(fs) < 0.5 * len(starts):
                        use_starts, focused = fs, True
                if focused:
                    print(f"  [focus] {len(slack_now)} slack legs -> "
                          f"{len(fs)} candidate starts", flush=True)

        cap = args.price_cap if not retried_uncapped else args.price_cap * 10
        t0 = time.monotonic()
        if duty_mod is not None:
            if executor is not None:
                pres = duty_mod.price_pass_mp(
                    executor, legs, use_dstarts, seen, args.eps, args.k,
                    deadline=time.monotonic() + cap, workers=args.mp,
                    max_legs=args.max_legs)
            else:
                leg_duals = [0.0] * len(legs)
                for l in legs:
                    leg_duals[l.idx] = l.dual
                duty_mod.set_duals(duties, leg_duals)
                pres = duty_mod.price_pass(
                    duties, duty_by_dep, duty_min_gain, seen,
                    args.eps, args.k, time.monotonic() + cap,
                    [duties[i] for i in use_dstarts], args.max_legs)
        elif executor is not None:
            import _gerad_pricing_mp as pmp
            pres = pmp.price_pass_mp(
                executor, legs,
                use_starts if use_starts is not None else legs,
                seen, args.eps, args.k,
                deadline=time.monotonic() + cap, workers=args.mp)
        else:
            pres = price_pass(legs, by_dep, min_dur, seen,
                              args.eps, args.k,
                              deadline=time.monotonic() + cap,
                              starts=use_starts)
        # Единый тип результата (аудит R10): полнота прохода вычисляется
        # из перечня усечений; ограничения оркестратора (drain/focus) —
        # такие же записанные усечения, а не голый флаг.
        cols = list(pres.columns)
        orch_sink = _pc.Truncations(_pc.SCOPE_PRICING)
        last_focused = focused
        if drain_restricted:
            # restricted pass can never certify the LP
            orch_sink.record("drain_restricted_starts", 0, 1)
        if focused:
            # A focused pass must COVER slack legs or send them to the
            # relax list — merely returning refinement columns from cone
            # starts is a slow-motion livelock (inst4: -43k LP/pass at
            # 900s/pass with slack frozen).
            slack_set = set(slack_now)
            covering = any(not slack_set.isdisjoint(fns)
                           for _, fns in cols)
            if not covering:
                # Unreachable even from their own full-start focus cone:
                # relax the rows (>=1 -> >=0). Keeping them at big-M
                # poisons the pruning bound for the WHOLE run.
                hopeless.update(slack_now)
                for fn in slack_now:
                    rmp.relax_row(fn)
                print(f"  [focus] no slack-covering columns within budget — "
                      f"{len(slack_now)} legs marked "
                      f"MISSING_FROM_BUDGETED_POOL (rows relaxed; exact "
                      f"certification runs at the end)", flush=True)
                if not cols:
                    it -= 1
                    continue
            orch_sink.record("focused_pass", 0, 1)
        exhausted = (not pres.deadline_hit
                     and pres.certifies_bound() and not orch_sink)
        dt = time.monotonic() - t0
        t_price += dt
        best_rc = (cols[0][0] - sum(duals[fn] for fn in cols[0][1])
                   if cols else 0.0)
        iterations.append({"it": it, "lp_obj": round(lp_obj, 1),
                           "new_cols": len(cols),
                           "best_rc": round(best_rc, 1),
                           "pricing_s": round(dt, 1),
                           "exhausted": exhausted,
                           "truncations": (pres.truncation_rows()
                                           + orch_sink.as_rows()),
                           "pool": len(pool)})
        print(f"  it {it:3d}: LP {lp_obj:12.1f}  +{len(cols):6d} cols "
              f"(best rc {best_rc:9.1f})  pricing {dt:6.1f}s"
              f"{'' if exhausted else ' [capped]'}  pool {len(pool):,}",
              flush=True)

        if not cols:
            if not force_exact:
                # Misprice at smoothed duals — recenter, rerun at exact.
                smooth_center = dict(duals)
                force_exact = True
                it -= 1
                continue
            if exhausted:
                lp_valid = True   # exact-dual complete pass, no negative rc
                break
            if not retried_uncapped:
                retried_uncapped = True   # one generous retry to prove it
                it -= 1
                continue
            break
        retried_uncapped = False
        force_exact = False
        smooth_center = dict(priced_duals)   # Neame recenter on success
        for cost, fns in cols:
            pool[fns] = cost
            seen.add(fns)
            rmp.add_column(cost, fns)
        if len(pool) > args.pool_cap:
            print(f"  pool cap {args.pool_cap:,} reached", flush=True)
            break
        if time.monotonic() - t_all > args.total_cap:
            print("  total time cap reached", flush=True)
            break

    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)

    # Два РАЗНЫХ машинных имени (аудит R10): валидная граница и величина
    # на ограниченном пуле не должны быть различимы только скобкой.
    if lp_valid:
        print(f"  CG done: {it} iters, pool {len(pool):,}, "
              f"LP bound {lp_obj:.1f} (valid), "
              f"pricing {t_price:.0f}s + LP {t_lp:.0f}s", flush=True)
    else:
        print(f"  CG done: {it} iters, pool {len(pool):,}, "
              f"RMP LP value {lp_obj:.1f} (RESTRICTED — not a proven "
              f"global bound), "
              f"pricing {t_price:.0f}s + LP {t_lp:.0f}s", flush=True)

    if bases:
        # Re-solve before the final artificial read: columns added after
        # the last solve leave GLOP's solution stale and artificial_usage
        # can read false zeros (seen on inst7: 3 rows without columns
        # reported as "fully driven out" => certification skipped).
        try:
            rmp.solve()
        except Exception:
            pass
    art_used = rmp.artificial_usage() if bases else []
    unc = (sorted({fn for fn, _ in art_used} | hopeless)) if bases else []
    if bases:
        if art_used:
            # Legs the converged LP still covers with slack: no base-anchored
            # pairing within MAX_LEGS reaches them => honest uncoverability.
            print(f"  !! {len(art_used)} legs still on artificial slack "
                  f"(MISSING_FROM_BUDGETED_POOL, max {MAX_LEGS} legs): "
                  f"{[fn for fn, _ in art_used[:10]]}"
                  f"{'...' if len(art_used) > 10 else ''}", flush=True)
            lp_valid = False
        elif hopeless:
            # Bound stays valid — for the subproblem without these rows.
            print(f"  cover complete EXCEPT {len(hopeless)} legs "
                  f"(MISSING_FROM_BUDGETED_POOL — focus found no chain "
                  f"within budget; exact certification below): "
                  f"{sorted(hopeless)[:10]}",
                  flush=True)
        else:
            print("  artificial slack fully driven out: every leg reachable "
                  "from a base", flush=True)

    # --- integerization ----------------------------------------------------
    t0 = time.monotonic()
    if bases:
        # Set-COVERING IP: base-anchored columns have no single-leg
        # fallback, so exact partitioning is (almost surely) infeasible.
        # The reference semantics is covering: pairings may overlap and the
        # extra crew rides as a TDH deadhead (40 TDH legs inside the inst1
        # reference alone). Overlap is reported; each leg gets one ACTIVE
        # owner for stage-2 / flight-level expansion.
        from ortools.sat.python import cp_model
        plist = list(pool.items())
        model = cp_model.CpModel()
        xs = [model.NewBoolVar(f"x{i}") for i in range(len(plist))]
        model.Minimize(sum(int(c) * x for (_, c), x in zip(plist, xs)))
        rows = defaultdict(list)
        for i, (fns, _) in enumerate(plist):
            for fn in fns:
                rows[fn].append(i)
        missing = [l.fn for l in legs if not rows.get(l.fn)]
        if missing:
            # Honest partial cover: legs with no covering column in the
            # converged pool are unreachable under OUR rules (the month's
            # reference itself violates duty/landing rules around such
            # legs) — cover the rest, report the orphans by name.
            print(f"  !! {len(missing)} legs have no covering column "
                  f"(MISSING_FROM_BUDGETED_POOL; exact certification below): "
                  f"{missing[:10]}"
                  f"{'...' if len(missing) > 10 else ''}", flush=True)
        for l in legs:
            if rows.get(l.fn):
                model.Add(sum(xs[i] for i in rows[l.fn]) >= 1)
        sat = cp_model.CpSolver()
        sat.parameters.max_time_in_seconds = float(args.final_tl)
        sat.parameters.num_search_workers = 8
        sat.parameters.random_seed = 0
        st = sat.Solve(model)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            selected = []
            info = {"status": sat.StatusName(st), "objective": None}
        else:
            sel_fns = [plist[i][0] for i in range(len(plist))
                       if sat.Value(xs[i])]
            selected = [Pairing.from_flights(
                f"C_{i:06d}", [flight_by_fn[fn] for fn in fns])
                for i, fns in enumerate(sel_fns)]
            status = sat.StatusName(st)
            if missing:
                status += f"+PARTIAL({len(missing)} uncoverable)"
            info = {"status": status,
                    "objective": int(sat.ObjectiveValue()),
                    "ip_model": "set_covering",
                    "uncovered_legs": missing}
    else:
        # Historical any-start space: the existing CP-SAT set-partitioning
        # selector (single-leg seeds keep ==1 feasible).
        pairings = [Pairing.from_flights(f"C_{i:06d}",
                                         [flight_by_fn[fn] for fn in fns])
                    for i, fns in enumerate(pool)]
        selected, info = select_pairings_set_partitioning(
            [l.fn for l in legs], pairings,
            time_limit_seconds=args.final_tl, num_workers=1)
    t_ip = time.monotonic() - t0
    print(f"  CP-SAT: {len(selected)} selected ({info['status']}) "
          f"in {t_ip:.1f}s  obj={info.get('objective')}", flush=True)

    # --- self-checks -------------------------------------------------------
    over = {}
    if selected:
        cover = Counter(fn for p in selected for fn in p.flight_numbers)
        my_obj = sum(pool[tuple(p.flight_numbers)] for p in selected)
        cost_parity = (info.get("objective") == my_obj)
        if bases:
            # Covering: every COVERABLE leg >= 1 (uncoverable ones are
            # reported by name); overlap = TDH deadhead riders.
            must = {l.fn for l in legs} - set(info.get("uncovered_legs", []))
            cover_ok = set(cover) >= must
            over = {fn: c for fn, c in cover.items() if c > 1}
            if over:
                print(f"  overlap (TDH riders): {len(over)} legs covered "
                      f">1x (max {max(over.values())})", flush=True)
        else:
            cover_ok = (len(cover) == n_legs and set(cover.values()) == {1})
        if not cover_ok:
            print("  !! COVER CHECK FAILED", flush=True)
        if not cost_parity:
            print(f"  !! COST PARITY FAILED: selector {info.get('objective')} "
                  f"vs colgen {my_obj}", flush=True)
    else:
        # Honest INFEASIBLE from the selector (audit fix 2) — no cover to
        # check; keep flags None so the JSON does not claim a failed check.
        cover_ok = cost_parity = None

    # --- immutable run storage (audit 2026-07-12, round 6) -----------------
    # A fresh run must NEVER clobber the durable best catalog/pool: run
    # artifacts go to runs/stage1/<run_id>/ unconditionally; the canonical
    # names (gerad_colgen_selected_*.json, gerad_pool_*.json.gz) are only
    # refreshed by PROMOTION further below, after certification.
    run_id = f"{label}-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir = Path(args.out).parent / "runs" / "stage1" / run_id
    dump = None
    if args.dump_selected and selected:
        # Each leg gets ONE active owner (deterministic: cheapest pairing
        # first); the other coverers carry it as a TDH deadhead. Stage-2 /
        # flight-level expansion must use active_fns.
        run_dir.mkdir(parents=True, exist_ok=True)
        owner = {}
        for p in sorted(selected,
                        key=lambda p: (pool[tuple(p.flight_numbers)],
                                       tuple(p.flight_numbers))):
            for fn in p.flight_numbers:
                owner.setdefault(fn, id(p))
        # config_sig — ТА ЖЕ схема, что у продуктового построителя
        # (третий аудит 2026-07-26, §5.7): без неё сверка правил этапа 2
        # принимала каталог этой ветки как наследие без отпечатка, и
        # каталог, посчитанный под другими правилами, проходил молча.
        # Формула отпечатка — единая (sha256 сорт-ключей действующих
        # правил, первые 16 знаков). АДДИТИВНО: прежние поля не меняются.
        _rules_now = rules_config.effective()
        _sig = {"rules_fingerprint": hashlib.sha256(
                    json.dumps(_rules_now, sort_keys=True).encode())
                .hexdigest()[:16],
                "rules_effective": _rules_now,
                "run_id": os.environ.get("CREW_RUN_ID") or None,
                "profile": args.profile, "pricer": args.pricer}
        # Правила источника — часть постановки (аудит R10, отсрочка №1);
        # ключи пишутся только при использовании: прежние отпечатки не
        # меняются.
        if getattr(args, "source_rules", None):
            _sig["source_rules_path"] = Path(args.source_rules).name
            _sig["source_rules_fp"] = args.source_rules_fp
            _sig["source_rules_overrides"] = (
                args.source_rules_overrides or [])
        # Личность ПОСТАНОВКИ (аудит R10, P0-03): идентификатор единого
        # манифеста прогона — этап 2 сверяет цепочку, а не собирает
        # постановку заново.
        _pm_id = os.environ.get("CREW_PROBLEM_MANIFEST_ID")
        if _pm_id:
            _sig["problem_manifest_id"] = _pm_id
        dump = {"inst": args.inst, "label": label,
                "profile": args.profile,
                "run_id": run_id,
                "config_sig": _sig,
                "bases": sorted(bases) if bases else None,
                "pricer": args.pricer,
                "max_legs": (args.max_legs if args.max_legs
                             else ("natural" if duty_mod else MAX_LEGS)),
                "ip_status": info["status"],
                "ip_model": info.get("ip_model", "set_partitioning"),
                "ip_objective": info.get("objective"),
                "overcovered_legs": len(over),
                "selected": [{"fns": list(p.flight_numbers),
                              "active_fns": [fn for fn in p.flight_numbers
                                             if owner.get(fn) == id(p)],
                              "cost": pool[tuple(p.flight_numbers)]}
                             for p in selected]}
        # Пакет B: штамп целостности биекции wire-имён — АДДИТИВНО (только
        # при повторных номерах; канонические артефакты байт-идентичны).
        if wire_map:
            dump["wire_map"] = wire_map
        (run_dir / f"gerad_colgen_selected_{label}.json").write_text(
            json.dumps(dump))
        print(f"  selected pairings dumped -> runs/stage1/{run_id}/",
              flush=True)

    if args.dump_pool:
        import gzip
        run_dir.mkdir(parents=True, exist_ok=True)
        pp = run_dir / f"gerad_pool_{label}.json.gz"
        with gzip.open(pp, "wt", encoding="utf-8") as gz:
            json.dump({"inst": args.inst, "label": label,
                       "run_id": run_id,
                       "columns": [list(f) for f in pool.keys()],
                       "costs": list(pool.values())}, gz)
        print(f"  full pool dumped -> runs/stage1/{run_id}/ "
              f"({len(pool):,} columns)", flush=True)

    # --- built-in exact certification of budget-missing legs ---------------
    # (audit 2026-07-12: a fresh run must never publish "uncoverable"
    # without a certificate; every missing leg gets FEASIBLE with a witness,
    # PROVEN_IMPOSSIBLE, or honest UNRESOLVED_BUDGET.)
    # The IP-stage missing list (rows with no covering column) is computed
    # from pool contents and is the most reliable source — union it in.
    if bases:
        unc = sorted(set(unc) | set(info.get("uncovered_legs", [])))
    certification = None
    if bases and unc:
        import _gerad_leg_feasibility as glf
        t0c = time.monotonic()
        if duties is not None:
            duties_c, by_ddep_c = duties, duty_by_dep
        else:
            import _gerad_duty_pricing as dpx2
            duties_c = dpx2.enumerate_duties(legs, by_dep, 5)
            by_ddep_c = dpx2.build_duty_index(duties_c)
        by_arr_c = glf.build_by_arr(duties_c)
        duty_sets_c = {d.idx: set(d.leg_idxs) for d in duties_c}
        by_fn_c = {l.fn: l for l in legs}
        certification = {}
        for fn in unc:
            certification[fn] = glf.certify_leg(
                by_fn_c[fn], duties_c, by_ddep_c, by_arr_c, duty_sets_c,
                args.cert_budget)
        n_f = sum(1 for r in certification.values()
                  if r["status"].startswith("FEASIBLE"))
        n_i = sum(1 for r in certification.values()
                  if r["status"] == "PROVEN_IMPOSSIBLE")
        print(f"  certification: {n_f} feasible (witnesses attached), "
              f"{n_i} proven impossible, {len(unc)-n_f-n_i} unresolved "
              f"({time.monotonic()-t0c:.0f}s)", flush=True)

    # Enrich the run dump with in-run certification verdicts, so every
    # consumer of the catalog knows its coverage semantics.
    if dump is not None and certification:
        dump["proven_impossible_legs"] = sorted(
            fn for fn, r in certification.items()
            if r["status"] == "PROVEN_IMPOSSIBLE")
        dump["unresolved_legs"] = sorted(
            fn for fn, r in certification.items()
            if r["status"].startswith("UNRESOLVED"))
        (run_dir / f"gerad_colgen_selected_{label}.json").write_text(
            json.dumps(dump))

    # --- durable best + promotion (audit 2026-07-12, round 6) --------------
    # The canonical catalog/pool names are refreshed ONLY here: a candidate
    # must be coverage-complete (covers every leg except individually
    # PROVEN-impossible ones) and strictly better than the durable best for
    # the same problem fingerprint. A weak or partial fresh run stays in
    # runs/stage1/<run_id>/ and cannot clobber the best.
    promoted = False
    if dump is not None:
        fp = space_fingerprint(legs, dump)
        dump["fingerprint"] = fp
        (run_dir / f"gerad_colgen_selected_{label}.json").write_text(
            json.dumps(dump))
        promoted, why = promote_run(
            Path(args.out).parent, label, dump, run_dir, fp,
            certification or {}, cover_ok)
        print(f"  promotion: {'PROMOTED to best' if promoted else 'best kept'}"
              f" — {why}", flush=True)

    ip_obj = info.get("objective")
    gap_pct = (round(100.0 * (ip_obj - lp_obj) / lp_obj, 3)
               if ip_obj is not None and lp_obj else None)
    ref = (FULL_ENUM_OPT.get(args.inst)
           if args.profile == "gerad" and not args.payload and not bases
           else None)
    if ref is not None and ip_obj is not None:
        note = ("matches it" if ip_obj == ref
                else f"differs {ip_obj - ref:+d} (>=0 expected: space shrank)")
        print(f"  legacy-space full-enum optimum was {ref}: "
              f"duty-fixed result {note}", flush=True)

    out = {
        "inst": args.inst, "label": label, "legs": n_legs,
        "method": "column_generation",
        "profile": args.profile, "duty_span_rule": DUTY_RULE_ON,
        "bases": sorted(bases) if bases else None,
        "pricer": args.pricer,
        "max_landings": max_landings if duty_mod else None,
        "max_legs": (args.max_legs if args.max_legs
                     else ("natural" if duty_mod else MAX_LEGS)),
        "duties": len(duties) if duty_mod else None,
        # Union of LP-stage relaxed rows and IP-stage rows with no covering
        # column (audit fix 2026-07-12: inst7 had PARTIAL(38) in the status
        # while this field stayed empty — the two lists were kept apart).
        "uncoverable_legs": (sorted(set(unc)
                                    | set(info.get("uncovered_legs", [])))
                             if bases else None),
        "ip_model": info.get("ip_model", "set_partitioning"),
        "overcovered_legs": len(over) if bases else None,
        "params": {"k": args.k, "eps": args.eps, "iters_cap": args.iters,
                   "price_cap_s": args.price_cap,
                   "final_tl_s": args.final_tl,
                   "min_sit_min": MIN_GROUND_TIME_MIN,
                   "dead_zone": DEAD_ZONE_ON},
        "cg_iterations": it, "pool_size": len(pool),
        # Округление ВНИЗ (внешний аудит R9), см. пояснение ниже по файлу.
        # Fail-closed (аудит R10, P1-01): невалидная граница НЕ публикуется
        # числом под именем lp_bound — потребители (runs.py) тянули её
        # регуляркой и показывали «Зазор, %» по величине ограниченного
        # пула. Число остаётся под честным именем.
        "lp_bound": (_pc.floor_bound(lp_obj)
                     if lp_obj is not None and lp_valid else None),
        "rmp_lp_value_on_restricted_pool": (
            _pc.floor_bound(lp_obj)
            if lp_obj is not None and not lp_valid else None),
        "lp_bound_valid": lp_valid,
        "pricing_s": round(t_price, 1), "lp_s": round(t_lp, 1),
        "ip_status": info["status"], "ip_objective": ip_obj,
        "ip_s": round(t_ip, 1), "selected": len(selected),
        "legs_distribution": info.get("legs_distribution"),
        # Audit fix (2026-07-12, round 5): a gap against an uncertified
        # bound is meaningless (inst7 even had a negative one) — null it.
        "gap_vs_lp_pct": gap_pct if lp_valid else None,
        "full_enum_reference": ref,
        "cover_ok": cover_ok, "cost_parity": cost_parity,
        "total_s": round(time.monotonic() - t_all, 1),
        "iterations": iterations,
    }
    p = Path(args.out)
    # Atomic, crash/concurrency-tolerant save: never truncate in place, and
    # never let a corrupt/contended file cost us the finished computation.
    for attempt in range(5):
        try:
            prior = json.loads(p.read_text()) if p.exists() else []
        except (json.JSONDecodeError, OSError):
            prior = []
        prior = [r for r in prior if r.get("inst") != args.inst] + [out]
        tmp = p.with_suffix(f".tmp{args.inst}")
        tmp.write_text(json.dumps(prior, indent=1))
        try:
            tmp.replace(p)
            break
        except OSError:
            time.sleep(0.5 * (attempt + 1))
    else:
        fallback = p.with_suffix(f".inst{args.inst}.json")
        fallback.write_text(json.dumps([out], indent=1))
        print(f"  !! main save contended; wrote {fallback.name}", flush=True)
    print(f"  saved -> {p.name}  (total {out['total_s']}s)", flush=True)

    # --- canonical result record (audit 2026-07-12: single source of truth,
    # staged and idempotent; a re-run of stage 1 resets completed_result
    # because a new pool invalidates any prior completion) -----------------
    canon_p = Path(args.out).with_name("gerad_stage1_results.json")
    canon_rec = {
        "schema": "stage1/v2",
        "label": label,
        "space": {"bases": sorted(bases) if bases else None,
                  "pricer": args.pricer,
                  "max_landings": max_landings if duty_mod else None,
                  "min_sit_min": MIN_GROUND_TIME_MIN,
                  "dead_zone": DEAD_ZONE_ON,
                  "ip_model": info.get("ip_model", "set_partitioning")},
        "initial_budget_result": {
            # Округление ВНИЗ (внешний аудит R9): величина публикуется как
            # граница СНИЗУ, и округление к ближайшему способно поднять её
            # выше истинного оптимума.
            "lp_bound": (_pc.floor_bound(lp_obj)
                         if lp_obj is not None and lp_valid else None),
            "rmp_lp_value_on_restricted_pool": (
                _pc.floor_bound(lp_obj)
                if lp_obj is not None and not lp_valid else None),
            "lp_bound_valid": lp_valid,
            "gap_vs_lp_pct": gap_pct if lp_valid else None,
            "ip_objective": ip_obj,
            "ip_status": info["status"],
            "selected": len(selected),
            "overcovered_legs": len(over) if bases else None,
            "budget_missing_legs": unc if bases else [],
            "seeded_witness_columns": bool(args.seed_columns),
            "pool_size": len(pool),
            "pool_dump": (f"gerad_pool_{label}.json.gz"
                          if args.dump_pool else None),
            "pricing_workers": args.mp or 1,
            "total_s": out["total_s"]},
        "feasibility_certification": certification,
        "completed_result": None,
    }
    # Per-instance file + deterministic aggregation (audit 2026-07-12,
    # round 5: read-modify-replace of ONE shared file can lose a parallel
    # lane's update; per-instance files cannot). Round 6: the canonical
    # record follows the same promotion rule as the catalog — a weak or
    # partial fresh run keeps its record in runs/stage1/<run_id>/ and does
    # not replace the canonical record of the promoted best.
    best_meta = (canon_p.parent / "best" / f"meta_{label}.json")
    if promoted or not best_meta.exists():
        inst_dir = canon_p.parent / "stage1"
        inst_dir.mkdir(exist_ok=True)
        # Keyed by LABEL, not by inst number: generic --payload runs all
        # share args.inst == 0, and keying by number let an upload run
        # clobber the bts-day record (found by the first end-to-end SSIM
        # run, 2026-07-13). Numeric GERAD labels are "instN" — unchanged.
        inst_p = inst_dir / f"{label}.json"
        tmp = inst_p.with_suffix(".tmp")
        tmp.write_text(json.dumps(canon_rec, indent=1))
        tmp.replace(inst_p)
        aggregate_stage1(canon_p)
        print(f"  canonical -> stage1/{label}.json (+ aggregated "
              f"{canon_p.name})", flush=True)
    else:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "stage1-record.json").write_text(
            json.dumps(canon_rec, indent=1))
        print(f"  run not promoted: stage-1 record kept in "
              f"runs/stage1/{run_id}/ only", flush=True)
    return 0


# Version tag of the column cost function; part of the problem fingerprint
# (audit round 6: best-catalog comparisons are only meaningful for the same
# input, space and costing).
COST_VERSION = "tdh-cover-v1"

# Правила, входящие в ИДЕНТИЧНОСТЬ задачи пейринга: те же легы под другими
# значениями этих шести — другая задача, и продвинутый каталог от неё уже
# не годится. Список задан ЯВНО (см. space_fingerprint): добавление ключа
# сюда обязано быть отдельным осознанным решением, потому что оно
# обесценивает все продвинутые каталоги и все опубликованные ростеры.
TIER1_SPACE_KEYS = frozenset({
    "min_sit_min",
    "max_landings_per_duty",
    "max_duty_span_min",
    "min_rest_min",
    "max_monthly_block_min",
    "max_pairing_span_min",
})


def space_fingerprint(legs, dump: dict) -> str:
    """SHA-256 over the actual leg set, the space parameters, the tier-1
    rule set and the cost version — the identity of the optimization
    problem (the rule set is part of the identity: the same legs under
    different rules are a different problem)."""
    import hashlib
    legs_sig = sorted((l.fn, l.dep_min, l.arr_min, l.dep, l.arr, l.ac)
                      for l in legs)
    blob = json.dumps({
        "legs": legs_sig,
        "space": {"bases": dump.get("bases"),
                  "pricer": dump.get("pricer"),
                  "max_legs": dump.get("max_legs"),
                  # Дыра отпечатка (аудит R10): предел посадок за смену
                  # и предел налёта командировки меняют пространство,
                  # но в личность задачи не входили — прогоны с разными
                  # значениями были неотличимы по отпечатку. 'none' —
                  # тоже значение, а не отсутствие.
                  "max_landings": dump.get("max_landings"),
                  "max_flight_minutes": MAX_FLIGHT_MINUTES,
                  "ip_model": dump.get("ip_model"),
                  "min_sit_min": MIN_GROUND_TIME_MIN,
                  "dead_zone": DEAD_ZONE_ON},
        # Правила, ОПРЕДЕЛЯЮЩИЕ пространство пейрингов — перечислены явно.
        #
        # Раньше здесь стоял ЗАПРЕЩАЮЩИЙ фильтр («всё, кроме comp_*»), и
        # это опасно наоборот: любой новый ключ правил попадал в отпечаток
        # МОЛЧА — без строчки кода, без падающего теста и без чьего-либо
        # решения. А смена отпечатка обесценивает все продвинутые каталоги
        # и (через хеш правил в манифесте) делает устаревшими все
        # опубликованные ростеры. Разрешающий список превращает включение
        # ключа в пространство задачи в осознанное действие.
        #
        # Состав экипажа (comp_*) сюда не входит по существу: он про
        # рассадку на этапе 2, а не про то, какие пейринги вообще
        # существуют.
        "rules": {k: v for k, v in rules_config.effective().items()
                  if k in TIER1_SPACE_KEYS},
        "cost": COST_VERSION}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def promote_run(fr: Path, label: str, dump: dict, run_dir: Path,
                fingerprint: str, certification: dict,
                cover_ok) -> tuple[bool, str]:
    """Promotion gate for the durable best catalog (audit round 6).

    Promotable iff the candidate covers every leg of the instance except
    those individually PROVEN impossible, its self-checks passed, and it
    is strictly better than the stored best for the same fingerprint (a
    changed fingerprint means a different problem — it supersedes with an
    explicit note). On promotion the canonical catalog/pool names are
    refreshed too, so downstream (stage 2, completion, validators) always
    reads the best known catalog."""
    import gzip
    import shutil
    obj = dump.get("ip_objective")
    if obj is None or not dump.get("selected"):
        return False, "no incumbent to promote"
    if cover_ok is False:
        return False, "cover self-check failed"
    not_proven = [fn for fn, r in certification.items()
                  if r["status"] != "PROVEN_IMPOSSIBLE"]
    if not_proven:
        return False, (f"{len(not_proven)} missing legs lack an "
                       f"impossibility proof (witnessed/unresolved) — "
                       f"complete the catalog first")
    best_dir = fr / "best"
    best_dir.mkdir(exist_ok=True)
    meta_p = best_dir / f"meta_{label}.json"
    note = "no previous best"
    if meta_p.exists():
        meta = json.loads(meta_p.read_text())
        if meta.get("fingerprint") == fingerprint:
            if meta.get("ip_objective") is not None \
                    and obj >= meta["ip_objective"]:
                return False, (f"candidate {obj} is not better than best "
                               f"{meta['ip_objective']} "
                               f"(run kept in {run_dir.name})")
            note = f"improves best {meta['ip_objective']} -> {obj}"
        else:
            note = ("fingerprint changed — new problem definition "
                    "supersedes the old best")
    sel_src = run_dir / f"gerad_colgen_selected_{label}.json"
    pool_src = run_dir / f"gerad_pool_{label}.json.gz"
    # Продвигаемый каталог получает блок coverage из уже доказанных здесь
    # фактов (cover_ok + сертификация невозможных): линия колгена перестаёт
    # быть исключением гейта публикации, и люк «каталог без coverage
    # публикуем как есть» сужается до настоящей старины (внешний аудит R6,
    # P1-15).
    if "coverage" not in dump:
        proven = list(dump.get("proven_impossible_legs") or [])
        dump["coverage"] = {
            "legs_total": None, "uncovered": len(proven),
            "no_pair_in_horizon": len(proven), "not_built": 0,
            "complete": not proven,
            "verdict": "OK" if not proven else "INFEASIBLE_INPUT",
            "source": "colgen-promotion"}
        sel_src.write_text(json.dumps(dump))
    shutil.copyfile(sel_src, best_dir / f"gerad_best_catalog_{label}.json")
    if pool_src.exists():
        shutil.copyfile(pool_src, best_dir / f"gerad_best_pool_{label}.json.gz")
    meta_p.write_text(json.dumps({
        "label": label, "ip_objective": obj,
        "fingerprint": fingerprint,
        "covered_legs_semantics": "all except proven-impossible",
        "proven_impossible_legs": dump.get("proven_impossible_legs", []),
        "run_id": dump.get("run_id"),
        "promoted_at": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=1))
    # Refresh the canonical names (canonical == best, always).
    shutil.copyfile(sel_src, fr / f"gerad_colgen_selected_{label}.json")
    if pool_src.exists():
        shutil.copyfile(pool_src, fr / f"gerad_pool_{label}.json.gz")
    return True, note


def aggregate_stage1(canon_p: Path) -> None:
    """Rebuild the merged canonical file from the per-instance sources."""
    inst_dir = canon_p.parent / "stage1"
    merged = {}
    for f in sorted(inst_dir.glob("*.json")):
        try:
            merged[f.stem] = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            continue
    # Unique temp name per process (audit round 6: a shared .tmpagg is
    # race-prone when parallel battery lanes aggregate concurrently).
    import os
    tmp = canon_p.with_suffix(f".tmpagg{os.getpid()}")
    tmp.write_text(json.dumps(merged, indent=1))
    for attempt in range(5):
        try:
            tmp.replace(canon_p)
            return
        except OSError:
            time.sleep(0.5 * (attempt + 1))


if __name__ == "__main__":
    sys.exit(main())
