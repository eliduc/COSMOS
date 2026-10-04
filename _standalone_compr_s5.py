"""Standalone 1200s comprehensive-search run on S5 (medium-lg).

Usage:
    python _standalone_compr_s5.py [ensemble]

Where ``ensemble`` is one of:
  * ``classic``     — LA + SA + Tabu(7) + GD (the original 4)
  * ``E`` / ``multi-la``     — LA(200) + LA(400) + LA(800) + LA(1600)
  * ``B`` / ``gold``         — LA + DLA + SA + SCHC
  * ``H`` / ``multi-phase``  — 4 islands, each does CH→LA→SA→Tabu→LA, seeds 0-3

Output:
  * per-minute per-island state to stdout (read from /islands endpoint)
  * Kotlin server log to ``_standalone_compr_s5_<ensemble>.log``
  * one row appended to ``benchmark_results.csv``
    with engine="timefold_kotlin_compr_<ensemble>"
"""
from __future__ import annotations

import csv
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

# Force UTF-8 on stdout/stderr so any non-ASCII character (em-dash, arrows,
# etc.) doesn't crash on Windows cp1252 default. We learned this the hard
# way during the first comprehensive run.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from flight_crew_scheduling import timefold_kotlin_engine as _tf_kotlin  # noqa: E402
from flight_crew_scheduling.benchmark import (  # noqa: E402
    CSV_FIELDS, CSV_PATH, KOTLIN_SOLVER_JAR, _java_exe, _kill_port, _wait_http_ready,
)
from flight_crew_scheduling.demo_data import generate_demo_data_scaled  # noqa: E402

TIMEOUT_S = 1200
DATASET = {
    "idx": 4,
    "label": "S5 (medium-lg)",
    "seed": 5,
    "count_flights": 56,
    "teams_per_airport": 3,
}

# Resolve ensemble from CLI arg. Default = "classic" preserves prior
# behaviour (LA + SA + Tabu + GD).
_ALIAS = {
    "classic":      "classic",
    "e":            "multi-la",
    "multi-la":     "multi-la",
    "b":            "gold",
    "gold":         "gold",
    "h":            "multi-phase",
    "multi-phase":  "multi-phase",
}
_LABEL = {
    "classic":      "classic",
    "multi-la":     "E",
    "gold":         "B",
    "multi-phase":  "H",
}
_RAW_ENSEMBLE = (sys.argv[1] if len(sys.argv) > 1 else "classic").lower()
ENSEMBLE = _ALIAS.get(_RAW_ENSEMBLE, "classic")
ENSEMBLE_TAG = _LABEL[ENSEMBLE]
ENGINE = f"timefold_kotlin_compr_{ENSEMBLE_TAG}"


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def next_run_index() -> int:
    if not CSV_PATH.exists():
        return 0
    max_idx = -1
    with CSV_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                max_idx = max(max_idx, int(row.get("run_index", "-1")))
            except (ValueError, TypeError):
                pass
    return max_idx + 1


def main() -> int:
    print(f"[{ts()}] starting Kotlin prod-JAR (4 islands, comprehensive, "
          f"ensemble={ENSEMBLE_TAG}={ENSEMBLE}) ...", flush=True)
    _kill_port(9091)
    time.sleep(1)

    log_path = HERE / f"_standalone_compr_s5_{ENSEMBLE_TAG}.log"
    log_f = log_path.open("w", encoding="utf-8")
    # Bump heap to -Xmx4g: comprehensive runs four ScoreDirector instances in
    # parallel, so working memory is ~4x the regular kotlin engine.
    proc = subprocess.Popen(
        [
            _java_exe(),
            "-Xms2g", "-Xmx8g",
            "-XX:+UseG1GC",
            "-XX:ParallelGCThreads=4",
            "-XX:ConcGCThreads=2",
            "-XX:MaxGCPauseMillis=50",
            "-jar", str(KOTLIN_SOLVER_JAR),
        ],
        stdin=subprocess.DEVNULL,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        cwd=str(KOTLIN_SOLVER_JAR.parent.parent),
    )

    started_at = datetime.now()
    best = {"score": None, "hard": None, "soft": None}
    t0 = 0.0
    # Each ensemble emits a different set of island labels (see
    # FlightCrewSchedulingResource.runAllToEnd dispatch).
    _ACCEPTORS_BY_ENSEMBLE = {
        "classic":     ["LA", "SA", "TABU", "GD"],
        "multi-la":    ["LA-200", "LA-400", "LA-800", "LA-1600"],
        "gold":        ["LA", "DLA", "SA", "SCHC"],
        "multi-phase": ["MP-S0", "MP-S1", "MP-S2", "MP-S3"],
    }
    ACCEPTORS = _ACCEPTORS_BY_ENSEMBLE[ENSEMBLE]

    def parse_score(score_str):
        m = re.match(r"(-?\d+)hard/(-?\d+)soft", str(score_str))
        if not m:
            return None
        return int(m.group(1)), int(m.group(2))

    def on_improvement(s):
        # Just track the global best — minute-by-minute printing now
        # happens in on_islands_update (which sees ALL four acceptors).
        if s.score is None:
            return
        parsed = parse_score(str(s.score))
        if parsed is None:
            return
        h, soft = parsed
        cand = (h, soft)
        cur = (best["hard"], best["soft"]) if best["hard"] is not None else None
        if cur is None or cand > cur:
            best["hard"] = h
            best["soft"] = soft
            best["score"] = str(s.score)

    def on_islands_update(islands_dict):
        elapsed = int(time.monotonic() - t0)
        print(f"\n[+{elapsed:5d}s] per-island state:", flush=True)
        for acc in ACCEPTORS:
            sc = islands_dict.get(acc, "-")
            marker = "  <- best" if sc == best["score"] else ""
            print(f"    {acc:5} {sc:>30}{marker}", flush=True)

    try:
        if not _wait_http_ready("http://127.0.0.1:9091/q/openapi", timeout_s=45.0):
            print(f"[{ts()}] ERROR: Kotlin JAR did not become ready in 45s", flush=True)
            return 1

        print(f"[{ts()}] ready; building S5 schedule (seed={DATASET['seed']}, "
              f"flights={DATASET['count_flights']}, teams={DATASET['teams_per_airport']}) ...",
              flush=True)
        schedule = generate_demo_data_scaled(
            seed=DATASET["seed"],
            count_flights=DATASET["count_flights"],
            teams_per_airport=DATASET["teams_per_airport"],
        )

        t0 = time.monotonic()
        print(f"[{ts()}] firing comprehensive (N=100%) solve, "
              f"time_limit_s={TIMEOUT_S} ...", flush=True)

        solved, status = _tf_kotlin.solve_with_timefold_kotlin(
            schedule, TIMEOUT_S,
            on_improvement=on_improvement,
            comprehensive=True,
            phase_a_fraction=1.0,
            on_islands_update=on_islands_update,
            islands_poll_interval_s=60.0,
            ensemble=ENSEMBLE,
        )
        elapsed = time.monotonic() - t0

        if solved is not None:
            on_improvement(solved)

        finished_at = datetime.now()

        # Append row to CSV
        row = {
            "run_index":          next_run_index(),
            "timeout_s":          TIMEOUT_S,
            "dataset_idx":        DATASET["idx"],
            "dataset_label":      DATASET["label"],
            "count_flights":      DATASET["count_flights"],
            "teams_per_airport":  DATASET["teams_per_airport"],
            "engine":             ENGINE,
            "started_at":         started_at.isoformat(timespec="seconds"),
            "finished_at":        finished_at.isoformat(timespec="seconds"),
            "elapsed_s":          round(elapsed, 1),
            "score":              best["score"],
            "hard":               best["hard"],
            "soft":               best["soft"],
            "finish_reason":      "timeout",
            "error":              "",
        }
        with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
            writer.writerow(row)

        print(f"\n[{ts()}] FINAL: {best['score']} after {elapsed:.1f}s "
              f"status={status}", flush=True)
        print(f"[{ts()}] appended to CSV (run_index={row['run_index']})", flush=True)
        return 0
    finally:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:  # noqa: BLE001
            pass
        _kill_port(9091)
        try:
            log_f.close()
        except Exception:  # noqa: BLE001
            pass
        print(f"[{ts()}] Kotlin JVM stopped. Log: {log_path}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
