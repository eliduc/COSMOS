"""Measure the TRUE pairing-structure cost of any flight-level roster:
optimal DP segmentation of each employee's chronological sequence into
legal BASE-ANCHORED chains (reference-aligned profile: sit >= 30 min, no
dead zone, rest >= 10 h splits duties, duty span <= 14 h, continuity,
start/end at a crew base), priced with the same cost formula as the
pairing selector. Legs not decomposable into anchored chains are charged
ANCHOR_PENALTY each and reported separately.

Usage: python _gerad_structure_eval.py solution1.json [solution2.json ...]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import rules_config  # noqa: E402  (single tier-1 rules source)

# Reference-aligned profile + base anchoring (2026-07-10): sit >= 30 min,
# no dead zone (a gap in [30min, 10h) is a legal sit; >= 10h is a rest);
# chains must start AND end at a crew base (0 anchoring violations across
# all 6160 GENCOL reference pairings). Legs not decomposable into anchored
# chains are charged ANCHOR_PENALTY each and reported separately.
# Tier-1 parameters from rules_config (env CREW_RULES_CONFIG; defaults are
# exactly the historical hardcoded values).
_RULES = rules_config.effective()
MIN_SIT = _RULES["min_sit_min"]
REST_MIN = _RULES["min_rest_min"]
MAX_SPAN_MIN = _RULES["max_pairing_span_min"]
MAX_BLOCK_MIN = 30 * 60  # 30h PER-PAIRING block cap (generator default) —
                         # NOT one of the six tier-1 params; stays fixed
MAX_DUTY_SPAN = _RULES["max_duty_span_min"]
MAX_LEGS = 10            # uniform proof-tier cap (battery part 4): deeper
                         # caps match the reference (12-18) but their LP
                         # bounds are not certifiable by our DFS pricing
DISCOUNT = 200
BASES = {"BASE1", "BASE2", "BASE3"}
ANCHOR_PENALTY = 100_000
EPOCH = datetime(2000, 1, 1)


def chain_cost(legs):
    """Cost if legs form ONE legal base-anchored chain, else None.
    legs = [(dep_min, arr_min, dep_code, arr_code, dur), ...]"""
    n = len(legs)
    span = legs[-1][1] - legs[0][0]
    if span > MAX_SPAN_MIN:
        return None
    # Base anchoring (audit fix): the chain starts at a base and closes
    # back at it. Applies to single-leg chains too (no real leg closes on
    # itself => singles are only ever the ANCHOR_PENALTY fallback).
    if legs[0][2] not in BASES or legs[-1][3] != legs[0][2]:
        return None
    if n > 1:
        if sum(l[4] for l in legs) > MAX_BLOCK_MIN:
            return None
        duty_start = legs[0][0]
        for a, b in zip(legs, legs[1:]):
            # Audit fix (2026-07-10): consecutive legs of a chain must be
            # airport-continuous (was unchecked — teleports could merge).
            if a[3] != b[2]:
                return None
            gap = b[0] - a[1]
            if gap < MIN_SIT:
                return None
            duty_start = b[0] if gap >= REST_MIN else duty_start
            if b[1] - duty_start > MAX_DUTY_SPAN:
                return None
    return max(1, span - DISCOUNT * (n - 1))


def dp_cost(seq):
    """Returns (cost, unanchorable) — optimal segmentation cost where legs
    with no legal anchored chain fall back to ANCHOR_PENALTY singles."""
    n = len(seq)
    dp = [(0, 0)] * (n + 1)   # (cost, penalized legs)
    for i in range(n - 1, -1, -1):
        best = None
        for j in range(i, min(i + MAX_LEGS, n)):
            c = chain_cost(seq[i:j + 1])
            if c is not None:
                t = (c + dp[j + 1][0], dp[j + 1][1])
                if best is None or t[0] < best[0]:
                    best = t
        if best is None:
            best = (ANCHOR_PENALTY + dp[i + 1][0], 1 + dp[i + 1][1])
        dp[i] = best
    return dp[0]


def _rid(v, key):
    return v.get(key) if isinstance(v, dict) else v


def evaluate(path: Path) -> int:
    sol = json.loads(path.read_text(encoding="utf-8"))
    flights = {f["flightNumber"]: f for f in sol["flights"]}
    by_emp = defaultdict(list)
    for a in sol["flightAssignments"]:
        eid = _rid(a.get("employee"), "id")
        fn = _rid(a.get("flight"), "flightNumber")
        if eid is None:
            continue
        f = flights[fn]
        dep = datetime.fromisoformat(f["departureUTCDateTime"])
        arr = datetime.fromisoformat(f["arrivalUTCDateTime"])
        dc = f["departureAirport"]
        ac = f["arrivalAirport"]
        by_emp[eid].append((
            int((dep - EPOCH).total_seconds() // 60),
            int((arr - EPOCH).total_seconds() // 60),
            dc if not isinstance(dc, dict) else dc["code"],
            ac if not isinstance(ac, dict) else ac["code"],
        ))
    total = 0
    unanchorable = 0
    for eid, seq in by_emp.items():
        seq.sort()
        c, pen = dp_cost([(d, a, dc, ac, a - d) for d, a, dc, ac in seq])
        total += c
        unanchorable += pen
    return total, unanchorable


def main():
    for arg in sys.argv[1:]:
        p = Path(arg)
        cost, pen = evaluate(p)
        note = (f"  ({pen} legs not decomposable into base-anchored chains, "
                f"{ANCHOR_PENALTY:,} each)" if pen else "")
        print(f"{p.name}: structure cost {cost:,}{note}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
