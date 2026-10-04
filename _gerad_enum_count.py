"""Control point for the report: COUNT-ONLY full enumeration of legal
pairings under the GERAD profile WITH the true duty rules (the fidelity
fix: duty = run of legs split by rests >= 10 h; span of a duty <= 14 h).
No pairing objects are stored — just counters — so memory stays flat and
the count is comparable with the duty-fixed CG battery.

Usage: python _gerad_enum_count.py <inst> [--legacy]
  --legacy : disable the duty-span rule (reproduces the old, slightly
             too-permissive space for an apples-to-apples pair of counts)
"""
from __future__ import annotations

import json
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

SCRATCH = Path(__file__).resolve().parent / "data" / "gerad"

MIN_SIT = 60
DEAD_LO, DEAD_HI = 6 * 60, 10 * 60
MAX_SPAN_MIN = 96 * 60
MAX_FLIGHT_MINUTES = 30 * 60
MAX_DUTY_SPAN = 14 * 60
MAX_LEGS = 5
PROGRESS_EVERY = 5_000_000
EPOCH = datetime(2000, 1, 1)


def main():
    inst = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    legacy = "--legacy" in sys.argv
    payload = json.loads(
        (SCRATCH / "payloads" / f"inst{inst}.json").read_text("utf-8"))

    legs = []
    for f in sorted(payload["flights"],
                    key=lambda f: (f["departureUTCDateTime"],
                                   f["flightNumber"])):
        dep = datetime.fromisoformat(f["departureUTCDateTime"])
        arr = datetime.fromisoformat(f["arrivalUTCDateTime"])
        legs.append((
            int((dep - EPOCH).total_seconds() // 60),
            int((arr - EPOCH).total_seconds() // 60),
            f["departureAirport"], f["arrivalAirport"],
            f["aircraftType"], len(legs)))
    n = len(legs)
    by_dep = defaultdict(list)
    for l in legs:
        by_dep[l[2]].append(l)
    for code in by_dep:
        by_dep[code].sort()
    dep_times = {c: [l[0] for l in ls] for c, ls in by_dep.items()}

    mode = "LEGACY (no duty-span rule)" if legacy else "duty-fixed"
    print(f"=== enum-count inst{inst} ({n} legs), {mode} ===", flush=True)
    t0 = time.monotonic()
    count = 0
    by_len = Counter()

    def extend(chain, visited, flight_min, duty_start):
        nonlocal count
        first = chain[0]
        last = chain[-1]
        depth = len(chain)
        if depth >= 2 and first[2] == last[3]:
            count += 1
            by_len[depth] += 1
            if count % PROGRESS_EVERY == 0:
                el = time.monotonic() - t0
                print(f"  [count] {count:,} in {el:,.0f}s "
                      f"({count/el:,.0f}/s), start {first[5]+1:,}/{n:,}",
                      flush=True)
        if depth >= MAX_LEGS:
            return
        arr_t = last[1]
        cands = by_dep.get(last[3])
        if not cands:
            return
        times = dep_times[last[3]]
        latest = first[0] + MAX_SPAN_MIN
        for i in range(bisect_left(times, arr_t + MIN_SIT), len(cands)):
            nxt = cands[i]
            if nxt[0] > latest:
                break
            gap = nxt[0] - arr_t
            if DEAD_LO <= gap < DEAD_HI:
                continue
            if nxt[5] in visited:
                continue
            if nxt[4] != first[4]:
                continue
            if nxt[1] - first[0] > MAX_SPAN_MIN:
                continue
            d = nxt[1] - nxt[0]
            if flight_min + d > MAX_FLIGHT_MINUTES:
                continue
            nds = nxt[0] if gap >= DEAD_HI else duty_start
            if not legacy and nxt[1] - nds > MAX_DUTY_SPAN:
                continue
            chain.append(nxt)
            visited.add(nxt[5])
            extend(chain, visited, flight_min + d, nds)
            chain.pop()
            visited.remove(nxt[5])

    for start in legs:
        count += 1                      # single-leg pairing
        by_len[1] += 1
        extend([start], {start[5]}, start[1] - start[0], start[0])

    el = time.monotonic() - t0
    print(f"  TOTAL: {count:,} legal pairings in {el:,.0f}s  "
          f"by_len={dict(sorted(by_len.items()))}", flush=True)
    out = {"inst": inst, "legs": n, "mode": mode, "count": count,
           "by_len": dict(sorted(by_len.items())), "seconds": round(el, 1)}
    p = HERE / "final-report" / "gerad_enum_counts.json"
    try:
        prior = json.loads(p.read_text())
    except Exception:
        prior = []
    prior = [r for r in prior
             if not (r.get("inst") == inst and r.get("mode") == mode)] + [out]
    tmp = p.with_suffix(f".tmp{inst}")
    tmp.write_text(json.dumps(prior, indent=1))
    tmp.replace(p)
    print(f"  saved -> {p.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
