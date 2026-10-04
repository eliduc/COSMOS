"""Run a process-isolated ensemble on the PINNED S4 instance, n trials.

Checks whether search diversification (esp. SA) breaks S4's single-LA
plateau (~-230h on the pinned instance: type rating 150 + flight conflict).
Same instance as _run_conflict_test.py / the #5 diagnosis, so results are
directly comparable to the single-LA baseline.

Usage:
    python _run_s4_ensemble.py <ensemble> <budget_s> <num_intervals> <n_trials>
    e.g.  python _run_s4_ensemble.py classic 300 1 5
          python _run_s4_ensemble.py I 300 5 5
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from flight_crew_scheduling.benchmark import KOTLIN_SOLVER_JAR  # noqa: E402
from _compr_isolated import (  # noqa: E402
    IsolatedJvmFleet, ENSEMBLES, ports_for_ensemble, compute_intervals,
    parse_score,
)
from _run_conflict_test import count_conflicts  # noqa: E402

PIN = HERE / "_s4_pinned.json"


def main():
    ensemble = sys.argv[1] if len(sys.argv) > 1 else "classic"
    budget = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    req_intervals = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    n = int(sys.argv[4]) if len(sys.argv) > 4 else 5
    inst_path = Path(sys.argv[5]) if len(sys.argv) > 5 else PIN

    if ensemble not in ENSEMBLES:
        print(f"unknown ensemble {ensemble}; valid: {list(ENSEMBLES)}", flush=True)
        return 2
    if not inst_path.exists():
        print(f"instance {inst_path.name} missing", flush=True)
        return 1

    pinned = json.loads(inst_path.read_text(encoding="utf-8"))
    print(f"  instance: {inst_path.name}", flush=True)
    acceptors = ENSEMBLES[ensemble]
    ports = ports_for_ensemble(ensemble)
    num_intervals, interval_s = compute_intervals(budget, req_intervals)

    print(f"=== S4 ENSEMBLE: {ensemble} ===", flush=True)
    print(f"  acceptors={acceptors} ports={ports}", flush=True)
    print(f"  {num_intervals} × {interval_s}s (budget {budget}s) × {n} trials",
          flush=True)
    print(f"  baseline single-LA plateau on this instance: ~-230h "
          f"(type rating 150 + flight conflict 70)\n", flush=True)

    results = []
    for trial in range(1, n + 1):
        t0 = time.monotonic()
        best = {"hard": None, "soft": None, "score": None, "dict": None}

        def better(sc):
            p = parse_score(sc)
            if not p:
                return False
            cur = (best["hard"], best["soft"]) if best["hard"] is not None else None
            return cur is None or p > cur

        with IsolatedJvmFleet(ports, KOTLIN_SOLVER_JAR, heap_xmx="2g") as fleet:
            current = pinned
            for k in range(1, num_intervals + 1):
                res = fleet.solve_parallel(current, acceptors, interval_s)
                # pick best island this interval
                scored = [(r.get("score"), r) for r in res]
                for sc, r in scored:
                    if better(sc):
                        p = parse_score(sc)
                        best.update(hard=p[0], soft=p[1], score=sc, dict=r)
                # warm-start next interval from best-ever (CH reinit nulls only)
                current = best["dict"] or current
        elapsed = time.monotonic() - t0
        nconf = count_conflicts(best["dict"]) if best["dict"] else None
        results.append((best["score"], nconf))
        feas = "FEASIBLE ✓" if best["hard"] == 0 else ""
        print(f"  trial {trial}: {best['score']}  conflicts={nconf}  "
              f"({elapsed:.0f}s) {feas}", flush=True)

    hards = [parse_score(sc)[0] for sc, _ in results if parse_score(sc)]
    if hards:
        feas = sum(1 for h in hards if h == 0)
        print(f"\n{ensemble}: feasible {feas}/{len(hards)} | "
              f"hard best={max(hards)} worst={min(hards)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
