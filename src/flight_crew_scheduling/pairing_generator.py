"""Enumerate all legal 1-day pairings for a given flight schedule.

Brute-force DFS: for each base airport, start a chain at every flight
departing from that base, recursively extend with subsequent flights
that respect ground time + duty period limits, and emit a pairing when
the chain returns to the base on the same calendar day.

Complexity: O(branching^depth) but bounded by:
  * 1-day window — naturally limits chain length to ~5 flights
  * Connection feasibility (rest + airport match) prunes aggressively

For S2-S4 (21–42 flights) we expect 50–500 pairings. For S5+ pairings
count grows quickly (~thousands). PoC focuses on S2/S4.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import timedelta
from typing import Dict, List, Set

from .domain import Flight
from .pairing import Pairing

# --- pairing legality parameters --------------------------------------------
# Our demo data is long-haul (9–21h flights crossing day boundaries), so a
# 14h single-day duty model doesn't fit. We use a relaxed multi-day pairing
# model with overnight rest at outstations.

# Minimum turn-around at hub between consecutive same-day-ish flights.
MIN_GROUND_TIME_MIN = 60

# Minimum overnight rest at an outstation (between calendar days).
MIN_OVERNIGHT_REST_MIN = 8 * 60

# After more than this gap, we treat it as overnight rest (resets duty).
DAY_BREAK_THRESHOLD_MIN = 6 * 60

# Maximum total flight time across all legs in a pairing.
MAX_FLIGHT_MINUTES = 30 * 60   # 30 hours of block time

# Maximum total elapsed time of a pairing (first dep to last arr).
MAX_PAIRING_HOURS = 96   # 4 days

# Maximum chain length (number of legs).
MAX_LEGS = 5

# Heartbeat for very large enumerations (GERAD monthly instances): one
# progress line per this many emitted pairings. Small runs never hit it.
PROGRESS_EVERY = 1_000_000


def enumerate_legal_pairings(flights: List[Flight]) -> List[Pairing]:
    """Return all legal 1-day pairings constructible from ``flights``.

    Pairings are uniquely-identified (P_0, P_1, ...). Single-flight
    pairings are emitted only when departure == arrival airport (rare).
    """
    if not flights:
        return []

    flights_sorted = sorted(flights,
                             key=lambda f: (f.departure_utc_date_time,
                                              f.flight_number))

    by_dep_airport: Dict[str, List[Flight]] = defaultdict(list)
    for f in flights_sorted:
        if f.departure_airport is None:
            continue
        by_dep_airport[f.departure_airport.code].append(f)

    pairings: List[Pairing] = []
    pairing_counter = [0]
    t_start = time.monotonic()
    start_pos = [0]
    n_starts = len(flights_sorted)

    def _emit(chain: List[Flight]):
        if not chain:
            return
        first = chain[0]
        last = chain[-1]
        if first.departure_airport is None or last.arrival_airport is None:
            return
        # Total elapsed within window
        elapsed_h = (last.arrival_utc_date_time - first.departure_utc_date_time).total_seconds() / 3600
        if elapsed_h > MAX_PAIRING_HOURS:
            return
        # Multi-flight pairings must close the loop (return to base).
        # Single-flight pairings are accepted as-is (a single duty period
        # at a specific airport — common for long-haul where crew is
        # replaced at destination or deadheads back later).
        if len(chain) > 1:
            if first.departure_airport.code != last.arrival_airport.code:
                return
        pid = f"P_{pairing_counter[0]:04d}"
        pairing_counter[0] += 1
        pairings.append(Pairing.from_flights(pid, list(chain)))
        if pairing_counter[0] % PROGRESS_EVERY == 0:
            elapsed = time.monotonic() - t_start
            rate = pairing_counter[0] / max(elapsed, 1e-9)
            print(f"  [pairing-gen] {pairing_counter[0]:,} pairings in "
                  f"{elapsed:,.0f}s ({rate:,.0f}/s), start flight "
                  f"{start_pos[0]:,}/{n_starts:,}", flush=True)

    def _extend(chain: List[Flight], visited: Set[str]):
        if 1 <= len(chain) <= MAX_LEGS:
            _emit(chain)

        if len(chain) >= MAX_LEGS:
            return
        last = chain[-1]
        if last.arrival_airport is None or last.arrival_utc_date_time is None:
            return

        first = chain[0]
        # Minimum next-departure: same-day turnaround OR overnight rest
        # (we accept either via the larger of the two).
        same_day_dep = last.arrival_utc_date_time + timedelta(minutes=MIN_GROUND_TIME_MIN)
        overnight_dep = last.arrival_utc_date_time + timedelta(minutes=MIN_OVERNIGHT_REST_MIN)

        candidates = by_dep_airport.get(last.arrival_airport.code, [])
        for nxt in candidates:
            if nxt.flight_number in visited:
                continue
            if nxt.departure_utc_date_time is None:
                continue
            gap_min = (nxt.departure_utc_date_time - last.arrival_utc_date_time).total_seconds() / 60
            # Accept either short turnaround OR overnight rest
            if gap_min < MIN_GROUND_TIME_MIN:
                continue
            # If gap implies overnight, must be at least MIN_OVERNIGHT_REST_MIN
            if MIN_GROUND_TIME_MIN <= gap_min < DAY_BREAK_THRESHOLD_MIN:
                pass   # short turnaround, accept
            elif gap_min < MIN_OVERNIGHT_REST_MIN:
                continue   # too short for overnight rest
            # Else: overnight rest, accept

            # Total elapsed time (first dep to next arr) must fit in pairing window
            elapsed_h = (nxt.arrival_utc_date_time - first.departure_utc_date_time).total_seconds() / 3600
            if elapsed_h > MAX_PAIRING_HOURS:
                continue
            # Flight time check
            new_flight_min = sum(f.get_duration_minutes() for f in chain) + nxt.get_duration_minutes()
            if new_flight_min > MAX_FLIGHT_MINUTES:
                continue
            # Aircraft type compatibility (single type rating)
            if nxt.aircraft_type != first.aircraft_type:
                continue

            chain.append(nxt)
            visited.add(nxt.flight_number)
            _extend(chain, visited)
            chain.pop()
            visited.remove(nxt.flight_number)

    for i, start in enumerate(flights_sorted, 1):
        start_pos[0] = i
        if start.departure_airport is None:
            continue
        _extend([start], {start.flight_number})

    return pairings


def summarize_pairings(pairings: List[Pairing]) -> Dict[str, int]:
    """Quick statistics about generated pairings."""
    if not pairings:
        return {"count": 0}
    by_legs = defaultdict(int)
    by_base = defaultdict(int)
    for p in pairings:
        by_legs[len(p.flight_numbers)] += 1
        by_base[p.base_airport] += 1
    return {
        "count": len(pairings),
        "by_legs": dict(by_legs),
        "by_base": dict(by_base),
        "min_duty_min": min(p.duty_minutes for p in pairings),
        "max_duty_min": max(p.duty_minutes for p in pairings),
        "min_flight_min": min(p.flight_minutes for p in pairings),
        "max_flight_min": max(p.flight_minutes for p in pairings),
    }
