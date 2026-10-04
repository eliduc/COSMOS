"""Generalized standalone comprehensive-search driver.

Usage:
    python _standalone_compr.py <ensemble> <dataset_idx> <timeout_s>

Where:
  ``ensemble``     — one of:
                       classic | E (multi-la) | B (gold) | H (multi-phase)
  ``dataset_idx``  — 0..5 corresponding to S1..S6 in BENCHMARK_DATASETS
  ``timeout_s``    — solve budget (seconds)

Output:
  * per-minute per-island state to stdout (read from /islands endpoint)
  * Kotlin server log to ``_standalone_compr_<ensemble>_<dataset>_<timeout>s.log``
  * one row appended to ``benchmark_results.csv``
    with engine="timefold_kotlin_compr_<ensemble-tag>"
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

# Force UTF-8 on stdout/stderr so any non-ASCII character doesn't crash
# on Windows cp1252 default.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from flight_crew_scheduling import timefold_kotlin_engine as _tf_kotlin  # noqa: E402
from flight_crew_scheduling.benchmark import (  # noqa: E402
    CSV_FIELDS, CSV_PATH, KOTLIN_SOLVER_JAR, _java_exe, _kill_port, _wait_http_ready,
)
from flight_crew_scheduling.demo_data import (  # noqa: E402
    BENCHMARK_DATASETS, generate_demo_data_scaled,
)


# ---------------------------------------------- argument parsing
_ALIAS = {
    "classic":            "classic",
    "e":                  "multi-la",
    "multi-la":           "multi-la",
    "b":                  "gold",
    "b2":                 "gold",
    "gold":               "gold",
    "b1":                 "gold-3way",
    "gold-3way":          "gold-3way",
    "h":                  "multi-phase",
    "multi-phase":        "multi-phase",
    "i":                  "island-migration",
    "island-migration":   "island-migration",
    "i_3":                "i_3",
    "i_6":                "i_6",
    "i_7":                "i_7",
    "i_8":                "i_8",
    "i_10":               "i_10",
    "ip":                 "ip",
    "island-perturbed":   "ip",
    "ia":                 "ia",
    "island-asymmetric":  "ia",
    "hi":                 "hi",
    "hybrid-hi":          "hi",
}
_LABEL = {
    "classic":          "classic",
    "multi-la":         "E",
    "gold":             "B",
    "gold-3way":        "B1",
    "multi-phase":      "H",
    "island-migration": "I",
    "i_3":              "I_3",
    "i_6":              "I_6",
    "i_7":              "I_7",
    "i_8":              "I_8",
    "i_10":             "I_10",
    "ip":               "Ip",
    "ia":               "Ia",
    "hi":               "HI",
}

_RAW_ENSEMBLE = (sys.argv[1] if len(sys.argv) > 1 else "classic").lower()
ENSEMBLE = _ALIAS.get(_RAW_ENSEMBLE, "classic")
ENSEMBLE_TAG = _LABEL[ENSEMBLE]

DATASET_IDX = int(sys.argv[2]) if len(sys.argv) > 2 else 4  # default S5
TIMEOUT_S = int(sys.argv[3]) if len(sys.argv) > 3 else 1200

assert 0 <= DATASET_IDX < len(BENCHMARK_DATASETS), \
    f"dataset_idx must be 0..{len(BENCHMARK_DATASETS)-1}; got {DATASET_IDX}"

DATASET = BENCHMARK_DATASETS[DATASET_IDX]
ENGINE = f"timefold_kotlin_compr_{ENSEMBLE_TAG}"

_DS_TAG = DATASET["label"].split()[0]   # "S4 (medium)" -> "S4"
LOG_BASENAME = f"_standalone_compr_{ENSEMBLE_TAG}_{_DS_TAG}_{TIMEOUT_S}s"


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


# Per-ensemble island labels (must match Kotlin runAllToEnd dispatch)
_ACCEPTORS_BY_ENSEMBLE = {
    "classic":          ["LA", "SA", "TABU", "GD"],
    "multi-la":         ["LA-200", "LA-400", "LA-800", "LA-1600"],
    "gold":             ["LA", "DLA", "SA", "SCHC"],
    "gold-3way":        ["LA", "SA", "SCHC"],
    "multi-phase":      ["MP-S0", "MP-S1", "MP-S2", "MP-S3"],
    # All island-migration variants use the same 4 base acceptors.
    # Per-island map is cleared at every interval boundary, so labels
    # represent the CURRENT interval's island state.
    "island-migration": ["LA", "DLA", "SA", "SCHC"],
    "i_3":              ["LA", "DLA", "SA", "SCHC"],
    "i_6":              ["LA", "DLA", "SA", "SCHC"],
    "i_7":              ["LA", "DLA", "SA", "SCHC"],
    "i_8":              ["LA", "DLA", "SA", "SCHC"],
    "i_10":             ["LA", "DLA", "SA", "SCHC"],
    "ip":               ["LA", "DLA", "SA", "SCHC"],
    "ia":               ["LA", "DLA", "SA", "SCHC"],
    "hi":               ["LA", "DLA", "SA", "SCHC"],
}


def main() -> int:
    print(f"[{ts()}] starting Kotlin prod-JAR (ensemble={ENSEMBLE_TAG}={ENSEMBLE}, "
          f"dataset={DATASET['label']}, timeout={TIMEOUT_S}s) ...", flush=True)
    _kill_port(9091)
    time.sleep(1)

    log_path = HERE / f"{LOG_BASENAME}.log"
    log_f = log_path.open("w", encoding="utf-8")
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
    ACCEPTORS = _ACCEPTORS_BY_ENSEMBLE[ENSEMBLE]

    def parse_score(score_str):
        m = re.match(r"(-?\d+)hard/(-?\d+)soft", str(score_str))
        if not m:
            return None
        return int(m.group(1)), int(m.group(2))

    def on_improvement(s):
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
            print(f"    {acc:8} {sc:>30}{marker}", flush=True)

    try:
        if not _wait_http_ready("http://127.0.0.1:9091/q/openapi", timeout_s=45.0):
            print(f"[{ts()}] ERROR: Kotlin JAR did not become ready in 45s", flush=True)
            return 1

        print(f"[{ts()}] ready; building schedule ({DATASET['label']}, "
              f"seed={DATASET['seed']}, flights={DATASET['count_flights']}, "
              f"teams={DATASET['teams_per_airport']}) ...", flush=True)
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

        row = {
            "run_index":          next_run_index(),
            "timeout_s":          TIMEOUT_S,
            "dataset_idx":        DATASET_IDX,
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
        except Exception:
            pass
        _kill_port(9091)
        try:
            log_f.close()
        except Exception:
            pass
        print(f"[{ts()}] Kotlin JVM stopped. Log: {log_path}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
