"""Orchestrator: run remaining 900s cells across all engines on S1/S2/S3/S6.

Sequence:
  1. For each dataset in [S1, S2, S3, S6]:
       a. Standalone TF-Kotlin (single LA via /schedules)        — only if not already in CSV
       b. Comprehensive classic / E / B / B1 / H                 — 5 ensembles
  2. For S6 only: also TF-Python / OR-Tools / SolverForge baselines
     (S1/S2/S3 already have these in the historical CSV).

Each cell:
  * spawns its own driver subprocess (kills 9091 first)
  * appends one row to ``benchmark_results.csv``
  * logs to ``_standalone_*_<dataset>_<timeout>s.log`` or ``_cell_runner.py``-style

Usage:
    python _run_grid.py [timeout_s]   # default 900s
"""
from __future__ import annotations

import csv
import json
import subprocess
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

from flight_crew_scheduling.benchmark import (  # noqa: E402
    CSV_FIELDS, CSV_PATH, KOTLIN_SOLVER_JAR, _kill_port,
)

TIMEOUT_S = int(sys.argv[1]) if len(sys.argv) > 1 else 900

# Datasets to test (skip S4 — already done). Order: smallest first so
# easy wins appear in CSV early; S6 (the genuinely-hard one) goes last.
DATASETS_TO_TEST = [0, 1, 2, 5]   # S1, S2, S3, S6

# All 5 comprehensive ensemble variants. Standalone single-LA is run via
# a different driver and added to the per-dataset queue in main().
ENSEMBLES = ["classic", "E", "B", "B1", "H"]

# For S6 we also need fresh baselines (none in historical CSV).
# These dispatch to ``_cell_runner.py`` (Python) for TF-Py / OR-Tools /
# SolverForge.
S6_BASELINES = ["timefold_python", "ortools", "solverforge"]


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def already_done(dataset_idx: int, engine: str) -> bool:
    """Return True if a row matching (timeout, dataset, engine) is in CSV."""
    if not CSV_PATH.exists():
        return False
    with CSV_PATH.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                if (int(row["timeout_s"]) == TIMEOUT_S
                    and int(row["dataset_idx"]) == dataset_idx
                    and row["engine"] == engine
                    and row["finish_reason"] not in ("error", "skipped", "watchdog")
                    and row.get("hard")):
                    return True
            except (KeyError, ValueError, TypeError):
                continue
    return False


def run_cmd(cmd, label):
    """Run one driver subprocess synchronously, log start/finish."""
    print(f"\n========= [{ts()}] {label} =========", flush=True)
    print(f"  CMD: {' '.join(str(x) for x in cmd)}", flush=True)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=str(HERE),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=TIMEOUT_S * 2 + 300)
        elapsed = time.monotonic() - t0
        print(proc.stdout, flush=True)
        print(f"  [{ts()}] DONE rc={proc.returncode} elapsed={elapsed:.1f}s", flush=True)
        return proc.returncode == 0
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        print(f"  [{ts()}] WATCHDOG TIMEOUT after {elapsed:.1f}s", flush=True)
        _kill_port(9091)
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"  [{ts()}] ERROR: {type(exc).__name__}: {exc}", flush=True)
        return False


def run_python_cell(engine: str, dataset_idx: int):
    """Run a single benchmark cell for TF-Py / OR-Tools / SForge via
    ``_cell_runner.py`` and append the row to CSV manually."""
    from flight_crew_scheduling.demo_data import BENCHMARK_DATASETS
    ds = BENCHMARK_DATASETS[dataset_idx]
    label = f"{engine} on {ds['label']} ({TIMEOUT_S}s)"

    print(f"\n========= [{ts()}] {label} =========", flush=True)

    started_at = datetime.now()
    params = {
        "engine":            engine,
        "seed":              int(ds["seed"]),
        "count_flights":     int(ds["count_flights"]),
        "teams_per_airport": int(ds["teams_per_airport"]),
        "time_limit_s":      TIMEOUT_S,
        "soft_threshold":    None,
    }
    runner = HERE / "_cell_runner.py"
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, str(runner)],
            input=json.dumps(params),
            cwd=str(HERE),
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_S * 2 + 300,
        )
    except subprocess.TimeoutExpired:
        print(f"  [{ts()}] WATCHDOG TIMEOUT", flush=True)
        return False
    elapsed = time.monotonic() - t0

    if proc.returncode != 0:
        print(f"  [{ts()}] cell-runner failed rc={proc.returncode}", flush=True)
        print(f"  stderr tail: {proc.stderr[-500:]}", flush=True)
        return False
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"  [{ts()}] bad JSON from cell-runner: {proc.stdout[:200]}", flush=True)
        return False

    finished_at = datetime.now()
    score = out.get("score")
    hard = out.get("hard")
    soft = out.get("soft")
    print(f"  [{ts()}] DONE engine={engine} score={score} elapsed={elapsed:.1f}s", flush=True)

    # Manually append to CSV (the cell-runner doesn't do this)
    next_idx = _next_run_index()
    row = {
        "run_index":          next_idx,
        "timeout_s":          TIMEOUT_S,
        "dataset_idx":        dataset_idx,
        "dataset_label":      ds["label"],
        "count_flights":      ds["count_flights"],
        "teams_per_airport":  ds["teams_per_airport"],
        "engine":             engine,
        "started_at":         started_at.isoformat(timespec="seconds"),
        "finished_at":        finished_at.isoformat(timespec="seconds"),
        "elapsed_s":          round(elapsed, 1),
        "score":              score,
        "hard":               hard,
        "soft":               soft,
        "finish_reason":      out.get("finish_reason", "timeout"),
        "error":              out.get("error", "") or "",
    }
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writerow(row)
    print(f"  appended CSV row run_index={next_idx}", flush=True)
    return True


def _next_run_index() -> int:
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
    print(f"[{ts()}] === GRID RUNNER STARTING ===", flush=True)
    print(f"  timeout: {TIMEOUT_S}s", flush=True)
    print(f"  datasets: {DATASETS_TO_TEST}", flush=True)
    print(f"  Kotlin JAR: {KOTLIN_SOLVER_JAR}", flush=True)
    if not KOTLIN_SOLVER_JAR.exists():
        print("  ERROR: Kotlin JAR not found; run mvn package first", flush=True)
        return 1

    overall_t0 = time.monotonic()
    total_runs = 0
    skipped = 0

    py = sys.executable

    for ds_idx in DATASETS_TO_TEST:
        # 1. Standalone TF-Kotlin (single LA)
        if already_done(ds_idx, "timefold_kotlin"):
            print(f"  skip standalone TF-Kotlin on idx={ds_idx} (already in CSV)", flush=True)
            skipped += 1
        else:
            run_cmd(
                [py, str(HERE / "_standalone_regular.py"), str(ds_idx), str(TIMEOUT_S)],
                f"Standalone TF-Kotlin on dataset_idx={ds_idx}",
            )
            total_runs += 1

        # 2. Each ensemble variant
        for ens in ENSEMBLES:
            engine_name = f"timefold_kotlin_compr_{ens}" if ens != "B" else "timefold_kotlin_compr_B"
            # NOTE: ensemble B's CSV engine is "timefold_kotlin_compr_B"
            #       (ENSEMBLE_TAG="B" in driver). Same for B1, E, H, classic.
            engine_name_csv = {
                "classic": "timefold_kotlin_compr_classic",
                "E":       "timefold_kotlin_compr_E",
                "B":       "timefold_kotlin_compr_B",
                "B1":      "timefold_kotlin_compr_B1",
                "H":       "timefold_kotlin_compr_H",
            }[ens]
            if already_done(ds_idx, engine_name_csv):
                print(f"  skip ensemble {ens} on idx={ds_idx} (already in CSV)", flush=True)
                skipped += 1
                continue
            run_cmd(
                [py, str(HERE / "_standalone_compr.py"), ens, str(ds_idx), str(TIMEOUT_S)],
                f"Comprehensive {ens} on dataset_idx={ds_idx}",
            )
            total_runs += 1

        # 3. For S6 (idx=5) only: also Python-side baselines
        if ds_idx == 5:
            for engine in S6_BASELINES:
                if already_done(ds_idx, engine):
                    print(f"  skip {engine} on idx={ds_idx} (already in CSV)", flush=True)
                    skipped += 1
                    continue
                run_python_cell(engine, ds_idx)
                total_runs += 1

    overall_elapsed = time.monotonic() - overall_t0
    print(f"\n[{ts()}] === GRID DONE ===", flush=True)
    print(f"  ran: {total_runs}, skipped: {skipped}, "
          f"total elapsed: {overall_elapsed/60:.1f} min", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
