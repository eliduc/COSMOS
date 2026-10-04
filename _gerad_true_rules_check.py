"""Validator of TRUE GERAD duty semantics for flight-level rosters.

The flight-level GERAD profile in the solver approximates a duty by the
CALENDAR DAY (documented proxy). The pairing generator implements the true
semantics: a rest >= 10 h RESETS the duty wherever it falls. This checker
applies the true semantics to any expanded roster, so both configs can be
judged by the actual rules instead of the proxy:

  per employee, legs sorted by departure:
    gap < 30 min                      -> violation "sit<30"
    duties = maximal runs split by gaps >= 10h
    per duty: landings > 5            -> violation "landings>5/duty"
              span > 14 h             -> violation "duty span>14h"
    monthly block > 85 h              -> violation "block>85h"
    overlapping legs                  -> violation "overlap"

Usage: python _gerad_true_rules_check.py file1.json [file2.json ...]
  (files = FlightCrewSchedule JSONs with flights + flightAssignments)
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

# Reference-aligned profile (2026-07-10): GENCOL solutions use 30-42 min
# active connections and have NO dead zone (32+ reference connections sit
# inside the former [6h,10h) ban in inst1 alone). REST (>=10h) still splits
# duties; a gap in [MIN_SIT, REST) is a legal sit.
# Tier-1 parameters from rules_config (env CREW_RULES_CONFIG; defaults are
# exactly the historical hardcoded values).
_RULES = rules_config.effective()
MIN_SIT = _RULES["min_sit_min"]
REST_MIN = _RULES["min_rest_min"]
MAX_LANDINGS = _RULES["max_landings_per_duty"]
MAX_DUTY_SPAN = _RULES["max_duty_span_min"]
MAX_BLOCK = _RULES["max_monthly_block_min"]


def _rid(v, key):
    return v.get(key) if isinstance(v, dict) else v


def check(path: Path) -> dict:
    sol = json.loads(path.read_text(encoding="utf-8"))
    flights = {f["flightNumber"]: f for f in sol["flights"]}
    by_emp = defaultdict(list)
    unassigned = 0
    for a in sol["flightAssignments"]:
        eid = _rid(a.get("employee"), "id")
        fn = _rid(a.get("flight"), "flightNumber")
        if eid is None:
            unassigned += 1
            continue
        f = flights[fn]
        dep = datetime.fromisoformat(f["departureUTCDateTime"])
        arr = datetime.fromisoformat(f["arrivalUTCDateTime"])
        dc = f["departureAirport"]
        ac = f["arrivalAirport"]
        by_emp[eid].append((dep, arr,
                            dc if not isinstance(dc, dict) else dc["code"],
                            ac if not isinstance(ac, dict) else ac["code"]))

    v = defaultdict(int)
    for eid, legs in by_emp.items():
        legs.sort()
        block = sum((a - d).total_seconds() / 60 for d, a, _, _ in legs)
        if block > MAX_BLOCK:
            v["block>85h"] += 1
        # Audit fix (2026-07-10): route continuity — the next leg must
        # depart from where the previous one arrived (no teleporting).
        for (_, _, _, ac1), (_, _, dc2, _) in zip(legs, legs[1:]):
            if ac1 != dc2:
                v["teleport"] += 1
        legs = [(d, a) for d, a, _, _ in legs]
        duty = [legs[0]]
        duties = [duty]
        for (d1, a1), (d2, a2) in zip(legs, legs[1:]):
            gap = (d2 - a1).total_seconds() / 60
            if gap < 0:
                v["overlap"] += 1
                continue
            if gap < MIN_SIT:
                v["sit<30"] += 1
            if gap >= REST_MIN:
                duty = [(d2, a2)]
                duties.append(duty)
            else:
                duty.append((d2, a2))
        for duty in duties:
            if len(duty) > MAX_LANDINGS:
                v["landings>5/duty"] += 1
            span = (duty[-1][1] - duty[0][0]).total_seconds() / 60
            if span > MAX_DUTY_SPAN:
                v["duty span>14h"] += 1
    total = sum(v.values())
    return {"file": path.name, "employees": len(by_emp),
            "unassigned": unassigned, "violations_total": total,
            "violations": dict(sorted(v.items()))}


def main():
    for arg in sys.argv[1:]:
        r = check(Path(arg))
        print(f"{r['file']}: {r['violations_total']} violations "
              f"{r['violations']}  (unassigned {r['unassigned']})",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
