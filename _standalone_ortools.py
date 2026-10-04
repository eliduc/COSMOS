"""Standalone OR-Tools (CP-SAT) solve with configurable num_search_workers.

Usage:
    python _standalone_ortools.py <num_workers> <dataset_idx> <timeout_s>

Sets ``ORTOOLS_NUM_WORKERS`` env var which ``ortools_engine.py`` reads to
override its default of 8.

Output:
  * minute-by-minute best score to stdout
  * one row appended to ``benchmark_results.csv`` with
    engine="ortools_w<N>" so different worker counts don't collide.
"""
from __future__ import annotations

import csv
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

NUM_WORKERS = int(sys.argv[1]) if len(sys.argv) > 1 else 8
DATASET_IDX = int(sys.argv[2]) if len(sys.argv) > 2 else 4   # default S5
TIMEOUT_S = int(sys.argv[3]) if len(sys.argv) > 3 else 1200

# Set BEFORE importing the engine so it picks up the override
os.environ["ORTOOLS_NUM_WORKERS"] = str(NUM_WORKERS)

from flight_crew_scheduling import ortools_engine  # noqa: E402
from flight_crew_scheduling.benchmark import CSV_FIELDS, CSV_PATH  # noqa: E402
from flight_crew_scheduling.demo_data import (  # noqa: E402
    BENCHMARK_DATASETS, generate_demo_data_scaled,
)


def ts():
    return datetime.now().strftime("%H:%M:%S")


def parse_score(score_str):
    if not score_str:
        return None
    m = re.match(r"(-?\d+)hard/(-?\d+)soft", str(score_str))
    return (int(m.group(1)), int(m.group(2))) if m else None


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


def main():
    dataset = BENCHMARK_DATASETS[DATASET_IDX]
    engine_name = f"ortools_w{NUM_WORKERS}"

    print(f"[{ts()}] === OR-Tools workers={NUM_WORKERS} on {dataset['label']} "
          f"({TIMEOUT_S}s) ===", flush=True)

    schedule = generate_demo_data_scaled(
        seed=dataset["seed"],
        count_flights=dataset["count_flights"],
        teams_per_airport=dataset["teams_per_airport"],
    )

    started_at = datetime.now()
    best = {"score": None, "hard": None, "soft": None}
    last_print_ts = 0.0
    t0_ref = [0.0]

    def on_improvement(s):
        nonlocal last_print_ts
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
        now = time.monotonic()
        if now - last_print_ts >= 60.0:
            print(f"[+{int(now - t0_ref[0]):5d}s] best={best['score']}", flush=True)
            last_print_ts = now

    t0_ref[0] = time.monotonic()
    last_print_ts = t0_ref[0]
    print(f"[{ts()}] firing OR-Tools CP-SAT solve, workers={NUM_WORKERS}, "
          f"time_limit_s={TIMEOUT_S} ...", flush=True)

    solved, status_text = ortools_engine.solve_with_ortools(
        schedule, TIMEOUT_S,
        on_improvement=on_improvement,
    )
    elapsed = time.monotonic() - t0_ref[0]
    if solved is not None:
        on_improvement(solved)

    finished_at = datetime.now()

    row = {
        "run_index":          next_run_index(),
        "timeout_s":          TIMEOUT_S,
        "dataset_idx":        DATASET_IDX,
        "dataset_label":      dataset["label"],
        "count_flights":      dataset["count_flights"],
        "teams_per_airport":  dataset["teams_per_airport"],
        "engine":             engine_name,
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
          f"status={status_text}", flush=True)
    print(f"[{ts()}] CSV row {row['run_index']} appended (engine={engine_name})",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
