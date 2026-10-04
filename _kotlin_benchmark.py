"""Standalone runner: Timefold Kotlin only on the same 6 datasets × 4 timeouts.
Appends rows to the existing benchmark_results.csv (engine=timefold_kotlin)
so the report generator picks them up alongside the original 4 engines."""

import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from flight_crew_scheduling.demo_data import (
    BENCHMARK_DATASETS,
    generate_demo_data_scaled,
)
from flight_crew_scheduling import timefold_kotlin_engine as tfk

CSV_PATH = ROOT / "benchmark_results.csv"
TIMEOUTS = [60, 150, 300, 600]

CSV_FIELDS = [
    "run_index", "timeout_s", "dataset_idx", "dataset_label",
    "count_flights", "teams_per_airport",
    "engine",
    "started_at", "finished_at", "elapsed_s",
    "score", "hard", "soft", "finish_reason", "error",
]


def parse_score(s):
    if not s:
        return None, None
    m = re.match(r"(-?\d+)hard/(-?\d+)soft", s)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def main() -> None:
    if not tfk.is_running():
        print("ERROR: Kotlin service not reachable on", tfk.KOTLIN_BASE_URL)
        sys.exit(1)

    # Determine starting run_index (continue numbering past existing rows)
    existing = 0
    if CSV_PATH.exists():
        with CSV_PATH.open(encoding="utf-8") as f:
            existing = sum(1 for _ in f) - 1  # minus header
    print(f"Existing rows in CSV: {existing}")

    deadline_per_cell = {t: t * 3 + 60 for t in TIMEOUTS}
    plan = []
    idx = max(existing, 96)
    for t in TIMEOUTS:
        for d_idx, ds in enumerate(BENCHMARK_DATASETS):
            plan.append({
                "run_index": idx,
                "timeout_s": t,
                "dataset_idx": d_idx,
                "dataset_label": ds["label"],
                "count_flights": ds["count_flights"],
                "teams_per_airport": ds["teams_per_airport"],
                "seed": ds["seed"],
            })
            idx += 1

    print(f"Plan: {len(plan)} Kotlin-only runs across {len(TIMEOUTS)} timeouts × "
          f"{len(BENCHMARK_DATASETS)} datasets")
    print()

    # Open CSV in append mode (assumes header already exists from prior run)
    write_header = not CSV_PATH.exists() or CSV_PATH.stat().st_size == 0
    f = CSV_PATH.open("a", newline="", encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
    if write_header:
        writer.writeheader()
        f.flush()

    try:
        for n, rd in enumerate(plan, 1):
            timeout_s = rd["timeout_s"]
            print(f"[{n}/{len(plan)}] timeout={timeout_s}s "
                  f"{rd['dataset_label']:<14} (flights={rd['count_flights']}, "
                  f"teams={rd['teams_per_airport']}) -- starting", flush=True)

            schedule = generate_demo_data_scaled(
                seed=rd["seed"],
                count_flights=rd["count_flights"],
                teams_per_airport=rd["teams_per_airport"],
            )

            started = datetime.now()
            t_start = time.monotonic()

            score = None
            finish_reason = "timeout"
            error = None
            try:
                # Engine-side timeout is enforced by the Java/Kotlin solver via
                # spentLimitSeconds. We don't need a Python-side watchdog here
                # because Kotlin honored its budget across all earlier tests.
                solved, status = tfk.solve_with_timefold_kotlin(
                    schedule, time_limit_s=timeout_s,
                )
                score = str(solved.score) if solved.score is not None else None
            except Exception as exc:  # noqa: BLE001
                finish_reason = "error"
                error = str(exc)
                print(f"  ERROR: {exc}", flush=True)

            finished = datetime.now()
            elapsed = round(time.monotonic() - t_start, 1)
            hard, soft = parse_score(score)

            writer.writerow({
                "run_index": rd["run_index"],
                "timeout_s": timeout_s,
                "dataset_idx": rd["dataset_idx"],
                "dataset_label": rd["dataset_label"],
                "count_flights": rd["count_flights"],
                "teams_per_airport": rd["teams_per_airport"],
                "engine": "timefold_kotlin",
                "started_at": started.isoformat(timespec="seconds"),
                "finished_at": finished.isoformat(timespec="seconds"),
                "elapsed_s": elapsed,
                "score": score,
                "hard": hard if hard is not None else "",
                "soft": soft if soft is not None else "",
                "finish_reason": finish_reason,
                "error": error or "",
            })
            f.flush()

            print(f"  -> score={score} hard={hard} soft={soft} elapsed={elapsed}s reason={finish_reason}",
                  flush=True)
    finally:
        f.close()

    print()
    print("Kotlin benchmark complete. Rerun _gen_report.py to refresh the MD.")


if __name__ == "__main__":
    main()
