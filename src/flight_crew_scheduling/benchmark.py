"""Benchmark orchestrator: 5 timeouts × 6 datasets × 4 engines = 120 sequential runs.

Public surface:
    start(timeouts=None, datasets=None, engines=None) → dict   start a fresh run
    resume()                                          → dict   continue from CSV
    stop()                                            → dict   abort gracefully
    status()                                          → dict   live snapshot

Each completed run is appended to ``benchmark_results.csv`` immediately so
progress survives a server restart. ``benchmark_progress.json`` records the
current cursor so resume() picks up where stop() left off.

Sequencing is strictly sequential — at most one solver job is active at a
time. The orchestrator runs in its own daemon thread; the API endpoints in
``rest_api.py`` only see the in-memory snapshot.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import httpx

from .demo_data import BENCHMARK_DATASETS, generate_demo_data_scaled
from .domain import FlightCrewSchedule
from . import ortools_engine as _ortools_engine
from . import timefold_kotlin_engine as _tf_kotlin

# JPype-based engines — only available with `timefold` Python wheel
# installed (Python <=3.13). On Python 3.14 we still want to import this
# module for its KOTLIN_SOLVER_JAR / _java_exe / _kill_port / _wait_http_ready
# helpers; falling back to stubs for the modules that need JPype.
try:
    from . import solver as _solver_module  # type: ignore[assignment]
except ImportError:
    _solver_module = None  # type: ignore[assignment]
try:
    from . import solverforge_engine as _sf  # type: ignore[assignment]
except ImportError:
    _sf = None  # type: ignore[assignment]

LOGGER = logging.getLogger(__name__)

# Five engines. Keep order stable so results align column-for-column.
# (Timefold-Java was dropped as a duplicate of Timefold-Kotlin: both ride the
# same Timefold 2.0 JVM core; differences live only in the Quarkus REST
# wrapper, not in solver behaviour.)
# `timefold_kotlin_comprehensive` shares the same Quarkus prod-JAR as
# `timefold_kotlin`; it hits a different endpoint that runs four acceptors
# (Late Acceptance, Simulated Annealing, Tabu Search, Great Deluge) in
# parallel and returns the best score across them (N=100% mode).
ENGINES = ["timefold_python", "ortools", "timefold_kotlin",
           "timefold_kotlin_comprehensive", "solverforge"]

# Default benchmark grid — overridable via start(timeouts=...).
DEFAULT_TIMEOUTS = [60, 150, 300, 600, 900]

# Where we persist progress and results so the user can resume.
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
CSV_PATH = ROOT_DIR / "benchmark_results.csv"
PROGRESS_PATH = ROOT_DIR / "benchmark_progress.json"
CELL_RUNNER = ROOT_DIR / "_cell_runner.py"
KOTLIN_SOLVER_JAR = ROOT_DIR / "kotlin-solver" / "target" / "quarkus-app" / "quarkus-run.jar"

CSV_FIELDS = [
    "run_index", "timeout_s", "dataset_idx", "dataset_label",
    "count_flights", "teams_per_airport",
    "engine",
    "started_at", "finished_at", "elapsed_s",
    "score", "hard", "soft", "finish_reason", "error",
]


# ---------------------------------------------------------------- data class

@dataclass
class RunResult:
    run_index: int
    timeout_s: int
    dataset_idx: int
    dataset_label: str
    count_flights: int
    teams_per_airport: int
    engine: str
    started_at: str
    finished_at: str
    elapsed_s: float
    score: Optional[str]
    hard: Optional[int]
    soft: Optional[int]
    finish_reason: str       # "timeout" | "manual" | "threshold" | "error" | "skipped"
    error: Optional[str] = None


# ------------------------------------------------------- in-memory orchestrator

@dataclass
class _State:
    running: bool = False
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    plan: List[Dict] = field(default_factory=list)        # ordered run descriptors
    cursor: int = 0                                        # next index to execute
    total: int = 0
    results: List[RunResult] = field(default_factory=list)
    current: Optional[Dict] = None                         # description of in-flight run
    last_error: Optional[str] = None
    stop_requested: bool = False
    health: Dict[str, str] = field(default_factory=dict)   # engine -> "ok" / failure reason
    # (engine, timeout_s) -> first dataset_idx that was infeasible at this
    # (engine, timeout). Once recorded, all subsequent cells with the same
    # (engine, timeout) and a strictly LARGER dataset_idx are skipped — bigger
    # datasets are strictly harder, so they cannot become feasible while the
    # smaller one isn't.
    infeasibility_blocks: Dict[tuple, int] = field(default_factory=dict)


_state = _State()
_state_lock = threading.Lock()
_worker_thread: Optional[threading.Thread] = None


# ---------------------------------------------------------------- helpers


def _build_plan(timeouts: List[int], engines: List[str] = None) -> List[Dict]:
    """Return the full list of run descriptors in execution order:
        outer = timeout, middle = dataset, inner = engine.
    Indexed left-to-right so resume() can match a cursor against the plan.

    `engines` can be a subset of `ENGINES`; defaults to all 5.
    """
    chosen = list(engines) if engines else list(ENGINES)
    # Validate
    bad = [e for e in chosen if e not in ENGINES]
    if bad:
        raise ValueError(f"unknown engines: {bad}; supported: {ENGINES}")
    plan = []
    idx = 0
    for tmo in timeouts:
        for d_idx, ds in enumerate(BENCHMARK_DATASETS):
            for engine in chosen:
                plan.append({
                    "run_index": idx,
                    "timeout_s": tmo,
                    "dataset_idx": d_idx,
                    "dataset_label": ds["label"],
                    "count_flights": ds["count_flights"],
                    "teams_per_airport": ds["teams_per_airport"],
                    "seed": ds["seed"],
                    "engine": engine,
                })
                idx += 1
    return plan


def _ensure_csv_header() -> None:
    """Create the CSV with header if it doesn't already exist."""
    if CSV_PATH.exists() and CSV_PATH.stat().st_size > 0:
        return
    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()


def _append_row(result: RunResult) -> None:
    _ensure_csv_header()
    with CSV_PATH.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        row = asdict(result)
        writer.writerow({k: row.get(k) for k in CSV_FIELDS})


def _save_progress() -> None:
    payload = {
        "cursor": _state.cursor,
        "total": _state.total,
        "started_at": _state.started_at,
        "finished_at": _state.finished_at,
        "stop_requested": _state.stop_requested,
        "running": _state.running,
        "plan": _state.plan,
    }
    PROGRESS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _load_results_csv() -> List[RunResult]:
    if not CSV_PATH.exists():
        return []
    out: List[RunResult] = []
    with CSV_PATH.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                out.append(RunResult(
                    run_index=int(row["run_index"]),
                    timeout_s=int(row["timeout_s"]),
                    dataset_idx=int(row["dataset_idx"]),
                    dataset_label=row["dataset_label"],
                    count_flights=int(row["count_flights"]),
                    teams_per_airport=int(row["teams_per_airport"]),
                    engine=row["engine"],
                    started_at=row["started_at"],
                    finished_at=row["finished_at"],
                    elapsed_s=float(row["elapsed_s"] or 0),
                    score=row.get("score") or None,
                    hard=_parse_int(row.get("hard")),
                    soft=_parse_int(row.get("soft")),
                    finish_reason=row.get("finish_reason") or "",
                    error=row.get("error") or None,
                ))
            except Exception:
                LOGGER.exception("skipping malformed CSV row: %s", row)
    return out


def _parse_int(s: Optional[str]) -> Optional[int]:
    if s is None or s == "":
        return None
    try:
        return int(s)
    except ValueError:
        return None


# ---------------------------------------------------------------- health checks


def _health_check_all() -> Dict[str, str]:
    """Probe each engine and return a dict {engine: 'ok' | 'reason for failure'}."""
    health: Dict[str, str] = {}

    # Timefold Python — needs the in-process solver_manager built at import.
    health["timefold_python"] = "ok" if _solver_module.solver_manager is not None \
        else "solver_manager not initialised"

    # OR-Tools — module flag set at import.
    health["ortools"] = "ok" if _ortools_engine.ORTOOLS_AVAILABLE \
        else "ortools package missing"

    # Timefold Kotlin — needs the prod JAR (we spawn `java -jar` per cell
    # from this for full subprocess isolation). The long-running Quarkus
    # dev server is no longer required.
    kotlin_status: str
    if KOTLIN_SOLVER_JAR.exists():
        kotlin_status = "ok"
    else:
        kotlin_status = (f"Kotlin prod JAR missing: {KOTLIN_SOLVER_JAR} "
                          f"(run `mvn -DskipTests package` in kotlin-solver/)")
    health["timefold_kotlin"] = kotlin_status
    # Comprehensive mode shares the same JAR — same health as the regular
    # kotlin engine.
    health["timefold_kotlin_comprehensive"] = kotlin_status

    # SolverForge — try a tiny import to verify package presence.
    try:
        import solverforge_legacy  # noqa: F401
        health["solverforge"] = "ok"
    except Exception as exc:  # noqa: BLE001
        health["solverforge"] = f"solverforge-legacy import failed: {exc}"

    return health


# ---------------------------------------------------------------- subprocess isolation


def _kill_port(port: int) -> None:
    """Kill any LISTEN process on the given port (Windows). Best-effort."""
    try:
        out = subprocess.check_output(["netstat", "-ano"], text=True, errors="ignore", timeout=5)
    except Exception:
        return
    for line in out.splitlines():
        if "LISTENING" in line and f":{port} " in line:
            parts = line.split()
            if not parts:
                continue
            pid = parts[-1]
            try:
                subprocess.run(["taskkill", "/F", "/PID", pid],
                                check=False, capture_output=True, timeout=5)
            except Exception:
                pass


def _wait_http_ready(url: str, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get(url, timeout=1.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1.0)
    return False


def _java_exe() -> str:
    """Locate java.exe — prefers JAVA_HOME, falls back to PATH."""
    jh = os.environ.get("JAVA_HOME")
    if jh:
        candidate = Path(jh) / "bin" / "java.exe"
        if candidate.exists():
            return str(candidate)
    return "java"


def _run_python_cell_subprocess(rd: Dict) -> tuple:
    """Spawn a fresh `_cell_runner.py` subprocess for TF-Py / OR-Tools / SolverForge.

    The subprocess gets its own Python interpreter and (for JPype-based engines)
    its own JVM, so JIT cache, identityHashCode allocations, GC state etc. are
    pristine. Each cell starts cleanly without contamination from prior cells.
    """
    timeout_s = int(rd["timeout_s"])
    deadline_s = max(120, timeout_s * 5 + 120)

    params = {
        "engine": rd["engine"],
        "seed": int(rd["seed"]),
        "count_flights": int(rd["count_flights"]),
        "teams_per_airport": int(rd["teams_per_airport"]),
        "time_limit_s": timeout_s,
        "soft_threshold": None,
    }

    try:
        proc = subprocess.run(
            [sys.executable, str(CELL_RUNNER)],
            input=json.dumps(params).encode(),
            capture_output=True,
            timeout=deadline_s,
            cwd=str(ROOT_DIR),
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        return None, None, None, "watchdog", f"subprocess exceeded {deadline_s}s"

    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", errors="replace")[-500:]
        return None, None, None, "error", f"rc={proc.returncode}: {tail}"

    try:
        out = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except Exception as exc:
        tail = proc.stderr.decode("utf-8", errors="replace")[-500:]
        return None, None, None, "error", f"bad JSON from cell-runner: {exc}; stderr: {tail}"

    return (out.get("score"), out.get("hard"), out.get("soft"),
            out.get("finish_reason", "timeout"), out.get("error"))


def _run_kotlin_cell(rd: Dict) -> tuple:
    """Restart Quarkus prod-JAR per cell so each Kotlin solve gets a fresh JVM.

    Cycle: kill anything on the port → spawn `java -jar quarkus-run.jar` →
    wait for /q/openapi to respond → submit solve via the Kotlin engine
    proxy → kill the JVM after we have the result.

    Handles both ``timefold_kotlin`` (single Local Search with default Late
    Acceptance) and ``timefold_kotlin_comprehensive`` (four acceptors in
    parallel, N=100%). Both use the same Quarkus JAR — only the endpoint
    differs.
    """
    port = 9091
    jar = KOTLIN_SOLVER_JAR
    engine = rd["engine"]
    comprehensive = (engine == "timefold_kotlin_comprehensive")

    if not jar.exists():
        return None, None, None, "error", f"prod JAR missing: {jar}"

    timeout_s = int(rd["timeout_s"])

    # Always start clean.
    _kill_port(port)
    time.sleep(1.0)

    proc = subprocess.Popen(
        [_java_exe(), "-Xms256m", "-Xmx2g", "-jar", str(jar)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(jar.parent.parent),
    )

    try:
        if not _wait_http_ready(f"http://127.0.0.1:{port}/q/openapi", timeout_s=45.0):
            return None, None, None, "error", "kotlin JAR did not become ready in 45s"

        # Now make the solve call via the existing engine proxy.
        from .demo_data import generate_demo_data_scaled
        schedule = generate_demo_data_scaled(
            seed=int(rd["seed"]),
            count_flights=int(rd["count_flights"]),
            teams_per_airport=int(rd["teams_per_airport"]),
        )

        tracker = _BestTracker()
        solved, _ = _tf_kotlin.solve_with_timefold_kotlin(
            schedule, timeout_s,
            on_improvement=tracker.update_from_schedule,
            comprehensive=comprehensive,
            phase_a_fraction=1.0,
        )
        tracker.update_from_schedule(solved)
        score, hard, soft = tracker.result()
        return score, hard, soft, "timeout", None
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("isolated Kotlin cell failed (comprehensive=%s)", comprehensive)
        return None, None, None, "error", f"{type(exc).__name__}: {exc}"
    finally:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass
        _kill_port(port)


# ---------------------------------------------------------------- run executor


def _execute_single_run(rd: Dict) -> RunResult:
    """Run one (timeout × dataset × engine) cell with full subprocess isolation.

    Each cell starts from scratch — fresh JVM (or fresh process for OR-Tools)
    so identityHashCode, JIT cache, GC state etc. don't leak between cells.
    To compensate for cold-JIT penalty we pass ``time_limit + JIT_WARMUP``
    seconds to the solver, so the *effective* warm-JIT search time per cell
    matches the user-visible budget.
    """
    started = datetime.now()
    timeout_s = int(rd["timeout_s"])
    engine = rd["engine"]

    finish_reason = "timeout"
    error: Optional[str] = None
    score: Optional[str] = None
    hard: Optional[int] = None
    soft: Optional[int] = None

    try:
        if engine in ("timefold_python", "ortools", "solverforge"):
            score, hard, soft, finish_reason, error = _run_python_cell_subprocess(rd)
        elif engine in ("timefold_kotlin", "timefold_kotlin_comprehensive"):
            score, hard, soft, finish_reason, error = _run_kotlin_cell(rd)
        else:
            finish_reason = "error"
            error = f"unknown engine: {engine}"
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("cell execution crashed: %s on %s",
                          engine, rd["dataset_label"])
        finish_reason = "error"
        error = f"{type(exc).__name__}: {exc}"

    if _state.stop_requested and finish_reason == "timeout":
        finish_reason = "manual"

    if finish_reason == "watchdog":
        score = "-9999hard/0soft"
        hard, soft = -9999, 0

    finished = datetime.now()
    elapsed = (finished - started).total_seconds()

    return RunResult(
        run_index=rd["run_index"],
        timeout_s=timeout_s,
        dataset_idx=rd["dataset_idx"],
        dataset_label=rd["dataset_label"],
        count_flights=rd["count_flights"],
        teams_per_airport=rd["teams_per_airport"],
        engine=engine,
        started_at=started.isoformat(timespec="seconds"),
        finished_at=finished.isoformat(timespec="seconds"),
        elapsed_s=round(elapsed, 1),
        score=score,
        hard=hard,
        soft=soft,
        finish_reason=finish_reason,
        error=error,
    )


def _parse_score(score_str: Optional[str]) -> tuple[Optional[int], Optional[int]]:
    if not score_str:
        return None, None
    import re
    m = re.match(r"(-?\d+)hard/(-?\d+)soft", str(score_str))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


class _BestTracker:
    """Records the best (highest tuple (hard, soft) since negatives) score
    across improvement events.

    Even though Timefold's `best-solution-consumer` is contractually monotonic,
    empirical evidence on Timefold 2.0 shows occasional regressions across
    consecutive solver runs (likely due to JVM-state contamination /
    identityHashCode-based HashMap iteration). This wrapper guards the
    benchmark from such regressions.
    """

    def __init__(self) -> None:
        self.best_tuple: Optional[tuple[int, int]] = None
        self.best_score_str: Optional[str] = None

    def update_from_str(self, score_str: Optional[str]) -> bool:
        if not score_str:
            return False
        h, s = _parse_score(score_str)
        if h is None:
            return False
        candidate = (h, s)
        if self.best_tuple is None or candidate > self.best_tuple:
            self.best_tuple = candidate
            self.best_score_str = score_str
            return True
        return False

    def update_from_schedule(self, schedule) -> bool:
        try:
            score = schedule.score
        except Exception:
            return False
        return self.update_from_str(str(score) if score is not None else None)

    def result(self) -> tuple[Optional[str], Optional[int], Optional[int]]:
        if self.best_tuple is None:
            return None, None, None
        return self.best_score_str, self.best_tuple[0], self.best_tuple[1]


# ---------------------------------------------------------------- engine drivers


def _run_timefold_python(schedule: FlightCrewSchedule, timeout_s: int,
                          should_stop) -> tuple[Optional[str], Optional[int], Optional[int]]:
    """Drive the in-process Timefold-Python solver_manager.

    Tracks the best score across all best-solution-events so a regression
    in the final consumer (rare but observed) cannot poison the benchmark
    cell. We return the best EVER seen during this run.
    """
    from .solver import update_settings
    update_settings(time_limit_s=timeout_s)  # rebuilds factory with new spent_limit

    job_id = f"bench-tfp-{int(time.time() * 1000)}"
    tracker = _BestTracker()
    done = threading.Event()

    def _on_best(s):
        tracker.update_from_schedule(s)

    def _on_final(s):
        tracker.update_from_schedule(s)
        done.set()

    (_solver_module.solver_manager.solve_builder()
        .with_problem_id(job_id)
        .with_problem(schedule)
        .with_best_solution_consumer(_on_best)
        .with_final_best_solution_consumer(_on_final)
        .run())

    deadline = time.monotonic() + timeout_s + 30
    terminated = False
    while not done.is_set() and time.monotonic() < deadline:
        if should_stop() and not terminated:
            _solver_module.solver_manager.terminate_early(job_id)
            terminated = True
        done.wait(timeout=0.5)

    return tracker.result()


def _run_ortools(schedule: FlightCrewSchedule, timeout_s: int, should_stop):
    tracker = _BestTracker()
    solved, _ = _ortools_engine.solve_with_ortools(
        schedule, timeout_s,
        on_improvement=tracker.update_from_schedule,
        should_stop=should_stop,
    )
    # Also fold in the final returned schedule, just in case OR-Tools didn't
    # fire the listener for the optimal it returned.
    tracker.update_from_schedule(solved)
    return tracker.result()


def _run_timefold_kotlin(schedule: FlightCrewSchedule, timeout_s: int, should_stop):
    if not _tf_kotlin.is_running():
        raise RuntimeError("Timefold Kotlin service not running on :9091")
    tracker = _BestTracker()
    solved, _ = _tf_kotlin.solve_with_timefold_kotlin(
        schedule, timeout_s,
        on_improvement=tracker.update_from_schedule,
        should_stop=should_stop,
    )
    tracker.update_from_schedule(solved)
    return tracker.result()


def _run_solverforge(schedule: FlightCrewSchedule, timeout_s: int, should_stop):
    tracker = _BestTracker()
    solved, _ = _sf.solve_with_solverforge(
        schedule, timeout_s,
        on_improvement=tracker.update_from_schedule,
        should_stop=should_stop,
    )
    tracker.update_from_schedule(solved)
    return tracker.result()


# ---------------------------------------------------------------- worker thread


def _worker(start_cursor: int) -> None:
    LOGGER.info("benchmark worker starting at cursor=%d total=%d",
                start_cursor, _state.total)

    with _state_lock:
        _state.cursor = start_cursor

    for i in range(start_cursor, _state.total):
        if _state.stop_requested:
            LOGGER.info("benchmark worker: stop requested at cursor=%d", i)
            break

        rd = _state.plan[i]
        with _state_lock:
            _state.cursor = i
            _state.current = rd

        # Skip-ahead: if a smaller dataset was infeasible at this (engine,
        # timeout), we don't bother with the larger ones — bigger means
        # strictly harder. Record the cell as `skipped` and move on.
        block_key = (rd["engine"], int(rd["timeout_s"]))
        block_idx = _state.infeasibility_blocks.get(block_key)
        if block_idx is not None and rd["dataset_idx"] > block_idx:
            now = datetime.now().isoformat(timespec="seconds")
            result = RunResult(
                run_index=rd["run_index"],
                timeout_s=int(rd["timeout_s"]),
                dataset_idx=int(rd["dataset_idx"]),
                dataset_label=rd["dataset_label"],
                count_flights=int(rd["count_flights"]),
                teams_per_airport=int(rd["teams_per_airport"]),
                engine=rd["engine"],
                started_at=now,
                finished_at=now,
                elapsed_s=0.0,
                score=None, hard=None, soft=None,
                finish_reason="skipped",
                error=f"infeasible at dataset_idx={block_idx}",
            )
            LOGGER.info("[%d/%d] SKIP (smaller dataset infeasible at idx=%d)",
                        i + 1, _state.total, block_idx)
        else:
            LOGGER.info("[%d/%d] timeout=%ds dataset=%s engine=%s -- starting",
                        i + 1, _state.total, rd["timeout_s"],
                        rd["dataset_label"], rd["engine"])
            result = _execute_single_run(rd)

        _append_row(result)

        # If this real run was infeasible, register the block so future
        # cells with the same (engine, timeout) and a larger dataset get skipped.
        if (result.finish_reason not in ("skipped",)
                and result.hard is not None and result.hard != 0):
            ds_idx = int(rd["dataset_idx"])
            existing = _state.infeasibility_blocks.get(block_key)
            if existing is None or ds_idx < existing:
                _state.infeasibility_blocks[block_key] = ds_idx

        with _state_lock:
            _state.results.append(result)
            _state.cursor = i + 1
            _state.current = None
            _save_progress()

        LOGGER.info("[%d/%d] -> score=%s elapsed=%ss reason=%s",
                    i + 1, _state.total, result.score,
                    result.elapsed_s, result.finish_reason)

    with _state_lock:
        _state.running = False
        _state.finished_at = datetime.now().isoformat(timespec="seconds")
        _state.current = None
        _save_progress()
    LOGGER.info("benchmark worker done.")


# ---------------------------------------------------------------- public API


def start(timeouts: Optional[List[int]] = None,
           engines: Optional[List[str]] = None,
           append: bool = False) -> Dict:
    """Begin a benchmark run.

    Args:
        timeouts: list of seconds; defaults to [60, 150, 300, 600].
        engines: subset of {timefold_python, ortools, timefold_kotlin,
                  timefold_kotlin_comprehensive, solverforge}; defaults to
                  all five.
        append:  if True, keep existing CSV rows and progress file in place;
                 useful for adding a new engine column to a previous full run.
                 If False (default), wipe CSV and start fresh.
    """
    global _worker_thread
    with _state_lock:
        if _state.running:
            return {"ok": False, "error": "Benchmark already running"}

        # Health-check only the engines we'll actually use.
        chosen_engines = engines or ENGINES
        bad_e = [e for e in chosen_engines if e not in ENGINES]
        if bad_e:
            return {"ok": False, "error": f"unknown engines: {bad_e}"}

        all_health = _health_check_all()
        relevant_health = {e: all_health[e] for e in chosen_engines}
        bad = {k: v for k, v in relevant_health.items() if v != "ok"}
        if bad:
            return {"ok": False, "error": "Engine health check failed",
                    "health": all_health}

        timeouts = timeouts or DEFAULT_TIMEOUTS
        plan = _build_plan(timeouts, chosen_engines)

        existing_results = _load_results_csv() if append else []
        if not append:
            if CSV_PATH.exists():
                CSV_PATH.unlink()
            _ensure_csv_header()

        _state.running = True
        _state.started_at = datetime.now().isoformat(timespec="seconds")
        _state.finished_at = None
        # Rebuild infeasibility blocks from existing CSV rows when appending,
        # otherwise start with a clean slate.
        blocks: Dict[tuple, int] = {}
        if append:
            for r in existing_results:
                if r.finish_reason == "skipped":
                    continue
                if r.hard is None or r.hard == 0:
                    continue
                key = (r.engine, int(r.timeout_s))
                ds_idx = int(r.dataset_idx)
                if key not in blocks or ds_idx < blocks[key]:
                    blocks[key] = ds_idx

        _state.plan = plan
        _state.cursor = 0
        _state.total = len(plan)
        _state.results = list(existing_results)
        _state.current = None
        _state.last_error = None
        _state.stop_requested = False
        _state.health = all_health
        _state.infeasibility_blocks = blocks
        _save_progress()

    _worker_thread = threading.Thread(target=_worker, args=(0,), name="benchmark", daemon=True)
    _worker_thread.start()
    return {"ok": True, "started": True, "total": _state.total,
            "health": all_health, "engines": chosen_engines, "appended": append}


def resume() -> Dict:
    """Continue an interrupted run by re-reading the CSV and progress file."""
    global _worker_thread
    with _state_lock:
        if _state.running:
            return {"ok": False, "error": "Benchmark already running"}

        if not PROGRESS_PATH.exists():
            return {"ok": False, "error": "No progress file to resume from"}

        progress = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        plan = progress.get("plan") or []
        if not plan:
            return {"ok": False, "error": "Progress file has no plan"}

        existing = _load_results_csv()
        cursor = max(progress.get("cursor", 0), len(existing))

        if cursor >= len(plan):
            return {"ok": False, "error": "Benchmark already complete; use start() to rerun"}

        health = _health_check_all()
        bad = {k: v for k, v in health.items() if v != "ok"}
        if bad:
            return {"ok": False, "error": "Engine health check failed",
                    "health": health}

        # Rebuild infeasibility_blocks from existing rows so the skip-larger
        # rule is honoured on resume.
        blocks: Dict[tuple, int] = {}
        for r in existing:
            if r.finish_reason == "skipped":
                continue
            if r.hard is None or r.hard == 0:
                continue
            key = (r.engine, int(r.timeout_s))
            ds_idx = int(r.dataset_idx)
            if key not in blocks or ds_idx < blocks[key]:
                blocks[key] = ds_idx

        _state.running = True
        _state.started_at = progress.get("started_at") or datetime.now().isoformat(timespec="seconds")
        _state.finished_at = None
        _state.plan = plan
        _state.cursor = cursor
        _state.total = len(plan)
        _state.results = existing
        _state.current = None
        _state.last_error = None
        _state.stop_requested = False
        _state.health = health
        _state.infeasibility_blocks = blocks

    _worker_thread = threading.Thread(target=_worker, args=(cursor,), name="benchmark", daemon=True)
    _worker_thread.start()
    return {"ok": True, "resumed": True, "cursor": cursor, "total": len(plan), "health": health}


def stop() -> Dict:
    with _state_lock:
        if not _state.running:
            return {"ok": False, "error": "Benchmark not running"}
        _state.stop_requested = True
    return {"ok": True, "stopping": True}


def status() -> Dict:
    """Live snapshot consumed by the UI."""
    with _state_lock:
        return {
            "running": _state.running,
            "cursor": _state.cursor,
            "total": _state.total,
            "current": _state.current,
            "started_at": _state.started_at,
            "finished_at": _state.finished_at,
            "stop_requested": _state.stop_requested,
            "health": _state.health,
            "results": [asdict(r) for r in _state.results],
        }
