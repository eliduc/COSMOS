"""Process-isolated comprehensive search.

Spawns ONE JVM per island on different HTTP ports (default 9091-9094) and
runs each island in its OWN JVM. Eliminates cross-island GC/memory/JIT
contention that plagued the single-JVM implementation.

Usage:
    python _compr_isolated.py <ensemble> <dataset_idx> <total_s> <intervals> [<trial_id>]

Where:
  ensemble:    one of:
               - classic  (LA + SA + TABU + GD, intervals=1 typically)
               - I        (LA + DLA + SA + SCHC, intervals=5 typically)
               - I_8      (same acceptors, intervals=8)
  dataset_idx: 0..5 (S1..S6)
  total_s:     total wall-clock budget per trial
  intervals:   number of migration intervals (1 = no migration)
  trial_id:    appended to engine name in CSV ("...iso_t1", etc.)

Runs ONE trial. Outer multi-trial loop is in _run_multi_trial.py.
"""
from __future__ import annotations

import csv
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import httpx  # noqa: E402

from flight_crew_scheduling.benchmark import (  # noqa: E402
    CSV_FIELDS, CSV_PATH, KOTLIN_SOLVER_JAR, _java_exe, _kill_port, _wait_http_ready,
)
from flight_crew_scheduling.demo_data import (  # noqa: E402
    BENCHMARK_DATASETS, generate_demo_data_scaled,
)


# ---------------------------------------------- ensemble registry
ENSEMBLES = {
    "classic":  ["LA", "SA", "TABU", "GD"],
    "I":        ["LA", "DLA", "SA", "SCHC"],
    "I_3":      ["LA", "DLA", "SA", "SCHC"],
    "I_5":      ["LA", "DLA", "SA", "SCHC"],
    "I_8":      ["LA", "DLA", "SA", "SCHC"],
    "I_10":     ["LA", "DLA", "SA", "SCHC"],
    # ---- 6-island wide ensemble: every distinct acceptor that the
    # Kotlin solveSingleSync endpoint understands. Spawns 6 separate
    # JVMs on ports 9091..9096; each JVM runs ONE acceptor end-to-end
    # per interval, then we pick the best. Goal: richer diversification
    # than the 4-island variants — different acceptor families have
    # different escape behaviour from local optima, so a wider portfolio
    # explores more basins per interval.
    "I_6wide":  ["LA", "DLA", "SA", "SCHC", "TABU", "GD"],
    "I_8wide":  ["LA", "DLA", "SA", "SCHC", "TABU", "GD",
                 # Two extra LA-tagged slots so the acceptor list has 8
                 # entries — they map to identical LA configs in the
                 # current JAR (no per-call seed exposed yet) so are
                 # functionally duplicates. Useful only after JAR
                 # rebuild adds size/seed query params; today they're
                 # equivalent to I_6wide + 2 redundant LA islands.
                 "LA", "LA"],
}

# Default ports for the 4-island ensembles. Wider ensembles dynamically
# extend this list (port_base..port_base+N-1).
DEFAULT_PORTS = [9091, 9092, 9093, 9094]
DEFAULT_PORT_BASE = 9091

# Floor for per-interval wall-time: shorter than this and Local-Search
# acceptors that "perturb-then-recover" (SA, GD, TABU after migration)
# can't complete the recovery, returning a degraded score that
# back-propagates to next interval's seed. Confirmed empirically on
# 6-island BTS smoke-test (intervals went LA -156k → SA -317k → SA -404k →
# GD -506k → ... see RESULTS §9.7 follow-up).
MIN_INTERVAL_S = 60


def ports_for_ensemble(ensemble_name: str, port_base: int = DEFAULT_PORT_BASE) -> list[int]:
    """Return ports for an ensemble's isolated JVMs.

    Parallel multi-trial runs use port_base 9091, 9101, 9111, ... so that
    concurrent trials don't collide on the same TCP ports.
    """
    n = len(ENSEMBLES[ensemble_name])
    return [port_base + i for i in range(n)]


def compute_intervals(total_s: int, num_intervals: int) -> tuple[int, int]:
    """Honest budget split.

    Old behaviour ``max(60, total_s // num_intervals)`` honoured the floor
    but **overshot the budget** — e.g. 120s/8 intervals → 60s × 8 = 480s
    actual. New behaviour: if total_s is too small to give MIN_INTERVAL_S
    per interval, reduce ``num_intervals``. Returns ``(num_intervals,
    interval_s)`` and prints a warning when the requested split was
    invalid.
    """
    if total_s < MIN_INTERVAL_S:
        return 1, total_s
    interval_s = total_s // num_intervals
    if interval_s >= MIN_INTERVAL_S:
        return num_intervals, interval_s
    new_intervals = max(1, total_s // MIN_INTERVAL_S)
    new_interval_s = total_s // new_intervals
    print(f"  WARNING: budget {total_s}s with {num_intervals} intervals "
          f"would give {interval_s}s/interval (< {MIN_INTERVAL_S}s floor); "
          f"reducing to {new_intervals} × {new_interval_s}s",
          flush=True)
    return new_intervals, new_interval_s


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def parse_score(score_str):
    if not score_str:
        return None
    m = re.match(r"(-?\d+)hard/(-?\d+)soft", str(score_str))
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


# ---------------------------------------------- JVM fleet
class IsolatedJvmFleet:
    def __init__(self, ports, jar, heap_xmx="2g"):
        self.ports = list(ports)
        self.jar = jar
        self.heap_xmx = heap_xmx
        self.procs = []

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stop()

    def start(self):
        try:
            self._start_inner()
        except Exception:
            # A JVM that failed to become ready would otherwise leak its
            # already-spawned siblings (they'd keep burning CPU/RAM and bias
            # the NEXT run's throughput). Tear everything down before
            # re-raising. (Audit finding: run-independence guard.)
            self.stop()
            raise

    def _start_inner(self):
        # Pre-clean stale listeners
        for port in self.ports:
            _kill_port(port)
        time.sleep(1)

        # Per-port log file capturing stderr — useful to diagnose JVM-side
        # 4xx/5xx responses (Jackson errors etc.) that the synchronous
        # caller can't see otherwise. Log path: _iso_jvm_<port>.log next to
        # this script.
        from pathlib import Path as _Path
        log_dir = _Path(__file__).resolve().parent
        self.log_files = []

        for port in self.ports:
            print(f"[{ts()}]   spawning JVM on port {port} ...", flush=True)
            log_path = log_dir / f"_iso_jvm_{port}.log"
            log_f = log_path.open("w", encoding="utf-8")
            self.log_files.append(log_f)
            p = subprocess.Popen(
                [
                    _java_exe(),
                    "-Xms512m", f"-Xmx{self.heap_xmx}",
                    "-XX:+UseG1GC",
                    f"-Dquarkus.http.port={port}",
                    # Mirror _run_bts_compare.py:_start_jar — surface
                    # Jackson/RESTEasy errors and bump the 10MB default
                    # so even bigger schedules round-trip cleanly.
                    '-Dquarkus.log.category."org.jboss.resteasy.reactive".level=DEBUG',
                    '-Dquarkus.log.category."com.fasterxml.jackson".level=DEBUG',
                    '-Dquarkus.log.category."io.quarkus.resteasy".level=DEBUG',
                    "-Dquarkus.http.limits.max-body-size=64M",
                    # Synchronous solves hold the HTTP connection silently
                    # for the whole budget; Quarkus's DEFAULT idle-timeout
                    # (30M) was killing every solve longer than 1800s
                    # ("server disconnected" right at the finish line).
                    "-Dquarkus.http.idle-timeout=12H",
                    # Force REPRODUCIBLE (vs Quarkus's default PHASE_ASSERT).
                    # PHASE_ASSERT is a debug mode that runs expensive
                    # assertions and slows CH+LS dramatically; we
                    # observed 84 min CH on a 150s budget on BTS until
                    # this flag was added.
                    "-Dquarkus.timefold.solver.environment-mode=no-assert",
                    "-jar", str(self.jar),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                cwd=str(self.jar.parent.parent),
            )
            self.procs.append(p)

        # Wait for all to be ready
        for port in self.ports:
            ok = _wait_http_ready(f"http://127.0.0.1:{port}/q/openapi", timeout_s=60.0)
            if not ok:
                raise RuntimeError(f"JVM on port {port} did not become ready in 60s")
            print(f"[{ts()}]   JVM on port {port} ready", flush=True)

    def stop(self):
        for proc, port in zip(self.procs, self.ports):
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
            except Exception:
                pass
            _kill_port(port)
        for log_f in getattr(self, "log_files", []):
            try:
                log_f.close()
            except Exception:
                pass
        self.procs = []
        self.log_files = []

    def solve_parallel(self, schedule_dict, acceptors, seconds, warm_start=False,
                       seeds=None):
        """Submit ONE POST /schedules/single per port; collect responses.

        Each request runs synchronously on its JVM for ``seconds``. We submit
        all 4 concurrently from a thread-pool so they execute in parallel
        (in their separate JVMs).

        ``warm_start=True`` tells each JVM's solveSingleSync to skip the
        Construction Heuristic phase. Use it on intervals 2..N where the
        input is the already-feasible global best from a prior interval —
        otherwise FIRST_FIT may "fill in" any null planning variables and
        perturb the existing optimum (see migration-degradation discussion
        in RESULTS §9.x).
        """
        assert len(acceptors) == len(self.ports)
        # Per-island RNG seeds. Default (None) keeps the legacy behaviour
        # (every island inherits application.properties random-seed=0 —
        # the seed-monoculture flaw found in the session audit). Pass a
        # list of distinct ints to give each island its own trajectory.
        if seeds is None:
            seeds = [None] * len(self.ports)
        assert len(seeds) == len(self.ports)
        timeout = httpx.Timeout(seconds * 2 + 90.0)
        limits = httpx.Limits(max_connections=10, max_keepalive_connections=5)

        def _post(port, acceptor, seed):
            with httpx.Client(timeout=timeout, limits=limits) as cli:
                params = {"acceptor": acceptor,
                          "spentLimitSeconds": int(seconds)}
                if warm_start:
                    params["warmStart"] = "true"
                if seed is not None:
                    params["randomSeed"] = int(seed)
                r = cli.post(
                    f"http://127.0.0.1:{port}/schedules/single",
                    json=schedule_dict, params=params,
                )
                r.raise_for_status()
                return r.json()

        results = [None] * len(self.ports)
        with ThreadPoolExecutor(max_workers=len(self.ports)) as pool:
            futures = {
                pool.submit(_post, port, acc, seed): i
                for i, (port, acc, seed) in enumerate(
                    zip(self.ports, acceptors, seeds))
            }
            for fut in futures:
                idx = futures[fut]
                results[idx] = fut.result()
        return results


# ---------------------------------------------- main
def main():
    if len(sys.argv) < 5:
        print("Usage: python _compr_isolated.py <ensemble> <dataset_idx> "
              "<total_s> <intervals> [<trial_id>]", flush=True)
        return 2

    ensemble_name = sys.argv[1]
    dataset_idx = int(sys.argv[2])
    total_s = int(sys.argv[3])
    num_intervals_req = int(sys.argv[4])
    trial_id = sys.argv[5] if len(sys.argv) > 5 else "1"
    port_base = int(sys.argv[6]) if len(sys.argv) > 6 else DEFAULT_PORT_BASE

    if ensemble_name not in ENSEMBLES:
        print(f"unknown ensemble {ensemble_name}; valid: {list(ENSEMBLES)}",
              flush=True)
        return 2

    acceptors = ENSEMBLES[ensemble_name]
    num_intervals, interval_s = compute_intervals(total_s, num_intervals_req)

    dataset = BENCHMARK_DATASETS[dataset_idx]
    engine = f"timefold_kotlin_iso_{ensemble_name}_t{trial_id}"
    label = f"{ensemble_name} ISO trial={trial_id} on {dataset['label']}"

    print(f"[{ts()}] === {label} ===", flush=True)
    print(f"  acceptors={acceptors}", flush=True)
    print(f"  intervals={num_intervals} × {interval_s}s = {num_intervals*interval_s}s", flush=True)
    print(f"  total_s={total_s}", flush=True)

    started_at = datetime.now()

    # Generate problem
    schedule = generate_demo_data_scaled(
        seed=dataset["seed"],
        count_flights=dataset["count_flights"],
        teams_per_airport=dataset["teams_per_airport"],
    )
    problem_dict = schedule.model_dump(by_alias=True, mode="json")
    problem_dict.pop("solverStatus", None)

    best = {"hard": None, "soft": None, "score": None}
    # Schedule that achieved the global best — fed as input to every
    # island in every subsequent interval. See _run_bts_compare.py for
    # the migration-degradation rationale: feeding the best result of
    # interval k-1 (rather than the global best) lets one bad interval
    # poison every subsequent interval.
    best_schedule_dict = problem_dict

    def update_best(score_str, res_dict):
        nonlocal best_schedule_dict
        parsed = parse_score(score_str)
        if parsed is None:
            return
        h, s = parsed
        cand = (h, s)
        cur = (best["hard"], best["soft"]) if best["hard"] is not None else None
        if cur is None or cand > cur:
            best["hard"] = h
            best["soft"] = s
            best["score"] = score_str
            best_schedule_dict = res_dict

    t0 = time.monotonic()

    ports = ports_for_ensemble(ensemble_name, port_base=port_base)
    print(f"  ports={ports} (one JVM per island, port_base={port_base})", flush=True)

    with IsolatedJvmFleet(ports, KOTLIN_SOLVER_JAR) as fleet:
        for k in range(1, num_intervals + 1):
            print(f"\n[{ts()}] interval {k}/{num_intervals} starting "
                  f"(currentBest={best['score']}) ...", flush=True)
            interval_t0 = time.monotonic()
            # Always include CH+LS phase chain. Timefold's FIRST_FIT
            # only processes uninitialized entities (REINITIALIZE_VARIABLE
            # filter is default), so the warm-start global best stays put
            # and CH only fills any remaining nulls. The earlier
            # `warmStart=true` shortcut (LS-only) requires a fully
            # initialized input, which we don't have because some
            # FlightAssignments legitimately have null employee when
            # there's no qualified crew.
            results = fleet.solve_parallel(
                best_schedule_dict, acceptors, interval_s,
            )
            interval_elapsed = time.monotonic() - interval_t0

            # Find best of N
            scored = []
            for acc, res in zip(acceptors, results):
                sc = res.get("score")
                parsed = parse_score(sc)
                scored.append((acc, sc, parsed))
                update_best(sc, res)
                print(f"    {acc:6} → {sc}", flush=True)

            interval_winner = max(scored, key=lambda x: x[2] or (-10**9, -10**9))
            print(f"  [{ts()}] interval {k}/{num_intervals} done in "
                  f"{interval_elapsed:.1f}s; interval_winner={interval_winner[0]} "
                  f"score={interval_winner[1]}; globalBest={best['score']}",
                  flush=True)

    elapsed = time.monotonic() - t0
    finished_at = datetime.now()

    # Append CSV row
    row = {
        "run_index":          _next_run_index(),
        "timeout_s":          total_s,
        "dataset_idx":        dataset_idx,
        "dataset_label":      dataset["label"],
        "count_flights":      dataset["count_flights"],
        "teams_per_airport":  dataset["teams_per_airport"],
        "engine":             engine,
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

    print(f"\n[{ts()}] FINAL: {best['score']} after {elapsed:.1f}s",
          flush=True)
    print(f"[{ts()}] CSV row {row['run_index']} appended (engine={engine})",
          flush=True)
    return 0


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


if __name__ == "__main__":
    sys.exit(main())
